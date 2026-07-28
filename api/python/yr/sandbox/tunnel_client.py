# api/python/yr/sandbox/tunnel_client.py
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Tunnel client: runs locally in a background thread.

Connects to Port A (WS) of TunnelServer, receives request frames,
forwards them to the local upstream service, and sends response frames back.
"""
import asyncio
import base64
import dataclasses
import http.cookiejar
import logging
import os
import random
import ssl
import threading
import time
import urllib.parse
from typing import Optional

import httpx
import websockets

from yr.sandbox.tunnel_protocol import (
    MAX_TUNNEL_FRAME_SIZE,
    ErrorFrame,
    HttpReqFrame,
    HttpRespFrame,
    PingFrame,
    PongFrame,
    WsCloseFrame,
    WsConnectedFrame,
    WsConnectFrame,
    WsMessageFrame,
    header_items_to_legacy_headers,
    make_id,
    parse_frame,
    rebuilt_request_header_items,
    rebuilt_response_header_items,
)

logger = logging.getLogger(__name__)

_WS_CHANNEL_QUEUE_MAX = 100
_PENDING_RESPONSE_TTL = 120.0  # seconds
_CONNECT_FAILURE_WARNING_THRESHOLD = 5
_DEFAULT_HTTP_CONCURRENCY = 10


def _headers_for_rebuilt_request(
    headers: dict[str, str],
    body: bytes,
) -> dict[str, str]:
    """Build headers for a new upstream HTTP connection.

    TunnelServer has already decoded the first hop's body framing. Connection
    metadata from that hop must therefore not be reused when httpx creates the
    upstream request.
    """
    return header_items_to_legacy_headers(
        rebuilt_request_header_items(
            list(headers.items()),
            len(body),
        )
    )


class _NoCookieJar(http.cookiejar.CookieJar):
    """Keep pooled tunnel requests free of ambient HTTP cookie state."""

    def extract_cookies(self, _response, _request):
        return

    def add_cookie_header(self, _request):
        return


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _ssl_verify_enabled() -> bool:
    """Check whether SSL verification is enabled (default: True).

    Set TUNNEL_SSL_VERIFY=0 to disable.
    """
    return os.environ.get("TUNNEL_SSL_VERIFY", "1") not in ("0", "false", "")


def _http_timeout() -> float:
    """Read YR_TUNNEL_HTTP_TIMEOUT env var (seconds, default 600)."""
    try:
        return float(os.environ.get("YR_TUNNEL_HTTP_TIMEOUT", "600"))
    except ValueError:
        return 600.0


def _http_concurrency() -> int:
    try:
        value = int(
            os.environ.get(
                "YR_TUNNEL_HTTP_CONCURRENCY",
                str(_DEFAULT_HTTP_CONCURRENCY),
            )
        )
    except ValueError:
        return _DEFAULT_HTTP_CONCURRENCY
    return value if value > 0 else _DEFAULT_HTTP_CONCURRENCY


def _patch_websockets_proxy_auth_unquote() -> None:
    """Decode URL-escaped proxy credentials before websockets builds Basic auth.

    websockets 15.x parses proxy.username / proxy.password from the URL but
    doesn't unquote them before building the Proxy-Authorization header. That
    breaks enterprise proxies when a password contains escaped characters such
    as %40 for '@'.
    """
    try:
        import websockets.asyncio.client as ws_client
    except (AttributeError, ImportError):
        return

    prepare_connect_request = getattr(ws_client, "prepare_connect_request", None)
    if prepare_connect_request is None:
        return
    if getattr(prepare_connect_request, "_yr_proxy_auth_unquote", False):
        return

    def prepare_connect_request_unquote(proxy, ws_uri, user_agent_header=None):
        if proxy.username is not None or proxy.password is not None:
            proxy = dataclasses.replace(
                proxy,
                username=(
                    urllib.parse.unquote(proxy.username)
                    if proxy.username is not None
                    else None
                ),
                password=(
                    urllib.parse.unquote(proxy.password)
                    if proxy.password is not None
                    else None
                ),
            )
        return prepare_connect_request(proxy, ws_uri, user_agent_header)

    setattr(prepare_connect_request_unquote, "_yr_proxy_auth_unquote", True)
    ws_client.prepare_connect_request = prepare_connect_request_unquote


class TunnelClient:
    def __init__(
        self,
        upstream: str,
        ping_interval: float = 30.0,
        ping_timeout: float = 30.0,
        reconnect_base_delay: float = 1.0,
        reconnect_max_delay: float = 60.0,
        max_http_concurrency: Optional[int] = None,
    ):
        """
        Args:
            upstream: upstream service address, e.g. "192.0.2.1:8000" or
                      "https://192.0.2.1:8000" or "https://...".
            ping_interval: seconds between heartbeat PingFrames.
            ping_timeout: seconds to wait for PongFrame before closing.
            reconnect_base_delay: base delay for exponential backoff (Task 4).
            reconnect_max_delay: max delay for exponential backoff (Task 4).
            max_http_concurrency: maximum concurrent upstream HTTP requests.
        """
        if "://" not in upstream:
            upstream = f"http://{upstream}"
        self._upstream = upstream.rstrip("/")
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._reconnect_base_delay = reconnect_base_delay
        self._reconnect_max_delay = reconnect_max_delay
        self._max_http_concurrency = (
            max_http_concurrency
            if max_http_concurrency is not None and max_http_concurrency > 0
            else _http_concurrency()
        )
        self._current_ping_id: Optional[str] = None
        self._pong_event: Optional[asyncio.Event] = None
        self._tunnel_url: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._main_task: Optional[asyncio.Task] = None
        self._current_ws = None
        self._stop_event = threading.Event()
        self._ws_channels: dict = {}  # channel_id -> asyncio.Queue
        self._ssl_verify = _ssl_verify_enabled()
        self._connected_event = threading.Event()  # Signal when connected
        self._pending_responses: dict = {}  # fid -> (resp_frame, timestamp)
        self._sent_request_ids: set = set()  # request IDs already sent to upstream
        self._connect_failure_count = 0
        self._last_connect_error: Optional[Exception] = None

    def start(self, tunnel_url: str, timeout: float = 10.0) -> bool:
        """Start the client in a background daemon thread.

        Args:
            tunnel_url: The WebSocket URL to connect to.
            timeout: Maximum time to wait for connection (seconds).

        Returns:
            True if connected successfully within timeout, False otherwise.
        """
        self._tunnel_url = tunnel_url
        self._thread = threading.Thread(target=self._run, name="tunnel-client", daemon=True)
        self._thread.start()
        # Wait for connection signal
        return self._connected_event.wait(timeout=timeout)

    def is_connected(self) -> bool:
        """Check if the client is currently connected."""
        return self._connected_event.is_set()

    def stop(self) -> None:
        """Stop the client and wait for the thread to finish."""
        self._stop_event.set()
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._request_shutdown)
        if self._thread:
            self._thread.join(timeout=5)

    def _request_shutdown(self) -> None:
        if self._current_ws is not None:
            asyncio.create_task(self._current_ws.close())
        self._cancel_main_task()

    def _cancel_main_task(self) -> None:
        if self._main_task and not self._main_task.done():
            self._main_task.cancel()

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._main_task = self._loop.create_task(self._connect_loop())
            self._loop.run_until_complete(self._main_task)
        except asyncio.CancelledError:
            pass
        finally:
            pending = [task for task in asyncio.all_tasks(self._loop) if not task.done()]
            for task in pending:
                task.cancel()
            if pending:
                cleanup = asyncio.gather(*pending, return_exceptions=True)
                try:
                    self._loop.run_until_complete(asyncio.wait_for(cleanup, timeout=3.0))
                except asyncio.TimeoutError:
                    cleanup.cancel()
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    async def _connect_loop(self) -> None:
        attempt = 0
        while not self._stop_event.is_set():
            try:
                ws_kwargs = self._build_ws_kwargs()
                async with websockets.connect(self._tunnel_url, **ws_kwargs) as ws:
                    self._current_ws = ws
                    logger.info("Connected to tunnel: %s", self._tunnel_url)
                    self._connected_event.set()  # Signal connected
                    attempt = 0  # reset on successful connect

                    # Clean expired cached responses on reconnect
                    self._cleanup_expired_responses()

                    try:
                        async with httpx.AsyncClient(
                            base_url=self._upstream,
                            verify=self._ssl_verify,
                            timeout=httpx.Timeout(_http_timeout()),
                            trust_env=False,
                            cookies=_NoCookieJar(),
                        ) as http:
                            await self._recv_loop(ws, http)
                    finally:
                        if self._current_ws is ws:
                            self._current_ws = None
            except Exception as e:
                self._connected_event.clear()  # Clear on disconnect
                self._connect_failure_count += 1
                self._last_connect_error = e
                if self._stop_event.is_set():
                    break
                if self._connect_failure_count == _CONNECT_FAILURE_WARNING_THRESHOLD:
                    logger.warning(
                        "Tunnel connection failed %d consecutive times: %s; reconnecting in background",
                        self._connect_failure_count,
                        e,
                    )
                else:
                    logger.debug("Tunnel disconnected (%s), reconnecting...", e)

            # Increment attempt after disconnect (before backoff calculation)
            attempt += 1

            # Cleanup stale WS channels on disconnect
            self._cleanup_ws_channels()

            if not self._stop_event.is_set():
                delay = min(
                    self._reconnect_base_delay * (2 ** attempt),
                    self._reconnect_max_delay,
                ) + random.random() * min(self._reconnect_base_delay, 1.0)
                logger.info("Reconnecting in %.1fs (attempt %d)", delay, attempt + 1)
                await asyncio.sleep(delay)

    def _cleanup_ws_channels(self) -> None:
        """Cancel in-flight WS proxy tasks and clear channels."""
        self._ws_channels.clear()

    def _cleanup_expired_responses(self) -> None:
        """Remove expired cached responses."""
        now = time.time()
        expired = [
            fid
            for fid, (_, ts) in self._pending_responses.items()
            if now - ts > _PENDING_RESPONSE_TTL
        ]
        for fid in expired:
            self._pending_responses.pop(fid, None)
            logger.debug("Expired cached response for request %s", fid)

    def _make_ssl_context(self) -> Optional[ssl.SSLContext]:
        if self._tunnel_url and self._tunnel_url.startswith("wss://"):
            if self._ssl_verify:
                return None  # use websockets default SSL verification
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            logger.warning(
                "TunnelClient: SSL certificate verification disabled "
                "via TUNNEL_SSL_VERIFY=0."
            )
            return ctx
        return None

    def _build_ws_kwargs(self) -> dict:
        ws_kwargs = {
            "ping_interval": None,
            "ping_timeout": None,
            "max_size": MAX_TUNNEL_FRAME_SIZE,
        }
        ssl_ctx = self._make_ssl_context()
        if ssl_ctx is not None:
            ws_kwargs["ssl"] = ssl_ctx
        # Same switch as yr.init; websockets>=15 auto-detects proxy env vars
        # unless proxy is set to None explicitly.
        if _env_truthy("YR_ENABLE_HTTP_PROXY"):
            _patch_websockets_proxy_auth_unquote()
            ws_kwargs["proxy"] = True
        else:
            ws_kwargs["proxy"] = None
        return ws_kwargs

    async def _recv_loop(self, ws, http: httpx.AsyncClient) -> None:
        """Orchestrate recv and heartbeat tasks. First to exit triggers cancel of the other."""
        self._connect_failure_count = 0
        self._last_connect_error = None
        self._pong_event = asyncio.Event()
        recv_task = asyncio.create_task(self._recv_frames(ws, http))
        hb_task = asyncio.create_task(self._heartbeat_loop(ws))
        tasks = [recv_task, hb_task]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                if not t.cancelled() and t.exception() is not None:
                    logger.debug("Recv loop task finished with error: %s", t.exception())
        finally:
            pending = [t for t in tasks if not t.done()]
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    async def _recv_frames(self, ws, http: httpx.AsyncClient) -> None:
        """Receive frames and dispatch them."""
        http_tasks = set()
        http_semaphore = asyncio.Semaphore(self._max_http_concurrency)

        async def handle_http(frame):
            async with http_semaphore:
                await self._handle_http(ws, http, frame)

        try:
            async for message in ws:
                try:
                    frame = parse_frame(message)
                except Exception as e:
                    logger.warning("Dropping malformed tunnel frame: %s", e)
                    continue
                if isinstance(frame, PongFrame) and frame.id == self._current_ping_id:
                    self._pong_event.set()
                elif isinstance(frame, HttpReqFrame):
                    task = asyncio.create_task(handle_http(frame))
                    http_tasks.add(task)
                    task.add_done_callback(http_tasks.discard)
                elif isinstance(frame, WsConnectFrame):
                    asyncio.create_task(self._handle_ws_connect(ws, frame))
                elif isinstance(frame, (WsMessageFrame, WsCloseFrame)):
                    q = self._ws_channels.get(frame.id)
                    if q is not None:
                        await q.put(frame)
        except websockets.ConnectionClosedError as e:
            logger.warning("Connection closed unexpectedly during recv: %s", e)
        except websockets.ConnectionClosedOK:
            logger.debug("Connection closed normally during recv")
        finally:
            for task in http_tasks:
                task.cancel()
            if http_tasks:
                await asyncio.gather(*http_tasks, return_exceptions=True)

    async def _heartbeat_loop(self, ws) -> None:
        """Send PingFrame periodically. Close ws on pong timeout."""
        while True:
            await asyncio.sleep(self._ping_interval)
            if self._stop_event.is_set():
                return
            self._current_ping_id = make_id()
            self._pong_event.clear()
            ping = PingFrame(id=self._current_ping_id, timestamp=time.time())
            try:
                await ws.send(ping.to_json())
                await asyncio.wait_for(self._pong_event.wait(), timeout=self._ping_timeout)
            except asyncio.TimeoutError:
                logger.warning("Heartbeat timeout, closing connection")
                try:
                    await ws.close()
                except Exception as _e:
                    logger.debug("Failed to close ws cleanly: %s", _e)
                return
            except websockets.ConnectionClosedOK:
                logger.debug("Connection closed normally during heartbeat")
                return
            except websockets.ConnectionClosedError as e:
                logger.warning("Connection closed unexpectedly during heartbeat: %s", e)
                return
            except Exception as e:
                logger.warning("Unexpected error during heartbeat: %s", e)
                try:
                    await ws.close()
                except Exception as _e:
                    logger.debug("Failed to close ws cleanly: %s", _e)
                return

    async def _handle_http(self, ws, http: httpx.AsyncClient, frame: HttpReqFrame) -> None:
        fid = frame.id

        # Check if we have a cached response for this request
        if fid in self._pending_responses:
            resp_frame, _ = self._pending_responses[fid]
            logger.debug("Using cached response for request %s", fid)
        elif fid in self._sent_request_ids:
            # Request already sent to upstream but no response yet, skip
            logger.warning("Request %s already in flight, skipping duplicate", fid)
            return
        else:
            # New request, send to upstream
            self._sent_request_ids.add(fid)
            try:
                request_headers = rebuilt_request_header_items(
                    frame.header_items,
                    len(frame.body),
                )
                async with http.stream(
                    method=frame.method,
                    url=frame.path,
                    headers=request_headers,
                    content=frame.body,
                ) as resp:
                    response_body = b"".join([
                        chunk async for chunk in resp.aiter_raw()
                    ])
                    response_header_items = [
                        (
                            name.decode("ascii"),
                            value.decode("latin-1"),
                        )
                        for name, value in resp.headers.raw
                    ]
                response_header_items = rebuilt_response_header_items(
                    response_header_items,
                    frame.method,
                    resp.status_code,
                    len(response_body),
                )
                resp_frame = HttpRespFrame(
                    id=fid, status=resp.status_code,
                    headers={},
                    header_items=response_header_items,
                    body=response_body,
                )
            except Exception as e:
                resp_frame = ErrorFrame(id=fid, message=str(e))
            finally:
                self._sent_request_ids.discard(fid)

        try:
            await ws.send(resp_frame.to_json())
            # Clear cached response if successfully sent
            self._pending_responses.pop(fid, None)
        except websockets.ConnectionClosedOK:
            # Normal closure, cache response for reconnect
            logger.debug("WebSocket connection closed normally, caching response for request %s", fid)
            self._pending_responses[fid] = (resp_frame, time.time())
        except websockets.ConnectionClosedError as e:
            # Abnormal closure, cache response for reconnect
            logger.warning("WebSocket connection closed unexpectedly, caching response for request %s: %s", fid, e)
            self._pending_responses[fid] = (resp_frame, time.time())
        except Exception as e:
            logger.warning("Unexpected error sending response for request %s: %s", fid, e)
            self._pending_responses[fid] = (resp_frame, time.time())

    async def _handle_ws_connect(self, ws, frame: WsConnectFrame) -> None:
        upstream_ws_url = (
            self._upstream
            .replace("http://", "ws://")
            .replace("https://", "wss://")
        ) + frame.path
        queue: asyncio.Queue = asyncio.Queue(maxsize=_WS_CHANNEL_QUEUE_MAX)
        self._ws_channels[frame.id] = queue
        try:
            async with websockets.connect(
                upstream_ws_url,
                max_size=MAX_TUNNEL_FRAME_SIZE,
            ) as upstream_ws:
                try:
                    await ws.send(WsConnectedFrame(id=frame.id).to_json())
                except websockets.ConnectionClosedOK:
                    logger.debug(
                        "WS channel %s: connection closed normally before sending connected frame",
                        frame.id,
                    )
                    return
                except websockets.ConnectionClosedError as e:
                    logger.warning(
                        "WS channel %s: connection closed unexpectedly before sending connected frame: %s",
                        frame.id,
                        e,
                    )
                    return
                except Exception as e:
                    logger.warning("WS channel %s: unexpected error sending connected frame: %s", frame.id, e)
                    return

                async def from_upstream():
                    try:
                        async for msg in upstream_ws:
                            if isinstance(msg, str):
                                await ws.send(
                                    WsMessageFrame(id=frame.id, data=msg, binary=False).to_json()
                                )
                            else:
                                await ws.send(
                                    WsMessageFrame(
                                        id=frame.id,
                                        data=base64.b64encode(msg).decode(),
                                        binary=True,
                                    ).to_json()
                                )
                    except websockets.ConnectionClosedOK:
                        pass  # Normal closure
                    except websockets.ConnectionClosedError as e:
                        logger.warning(
                            "WS channel %s: connection closed unexpectedly in from_upstream: %s",
                            frame.id,
                            e,
                        )
                    except Exception as e:
                        logger.warning("WS channel %s: unexpected error in from_upstream: %s", frame.id, e)
                    finally:
                        try:
                            await ws.send(WsCloseFrame(id=frame.id).to_json())
                        except websockets.ConnectionClosed:
                            pass
                        except Exception as e:
                            logger.warning("WS channel %s: unexpected error sending close frame: %s", frame.id, e)

                async def from_sandbox():
                    try:
                        while True:
                            f = await queue.get()
                            if isinstance(f, WsMessageFrame):
                                if f.binary:
                                    await upstream_ws.send(base64.b64decode(f.data))
                                else:
                                    await upstream_ws.send(f.data)
                            elif isinstance(f, WsCloseFrame):
                                break
                    except websockets.ConnectionClosedOK:
                        pass  # Normal closure
                    except websockets.ConnectionClosedError as e:
                        logger.warning("WS channel %s: connection closed unexpectedly in from_sandbox: %s", frame.id, e)
                    except Exception as e:
                        logger.warning("WS channel %s: unexpected error in from_sandbox: %s", frame.id, e)

                t1 = asyncio.create_task(from_upstream())
                t2 = asyncio.create_task(from_sandbox())
                done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass
        except Exception as e:
            logger.warning("WS channel %s error: %s", frame.id, e)
            try:
                await ws.send(ErrorFrame(id=frame.id, message=str(e)).to_json())
            except websockets.ConnectionClosed:
                pass
            except Exception as send_err:
                logger.warning("WS channel %s: failed to send error frame: %s", frame.id, send_err)
        finally:
            self._ws_channels.pop(frame.id, None)
