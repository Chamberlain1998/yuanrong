# api/python/yr/sandbox/tunnel_server.py
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
"""Tunnel server: runs inside the sandbox.

Port A (ws_port, 0.0.0.0): WebSocket endpoint for TunnelClient to connect.
Port B (http_port, 127.0.0.1): HTTP/WS proxy for sandbox code.
"""
import asyncio
import base64
import contextlib
import logging
import os
from typing import Optional

import aiohttp
import websockets
from aiohttp import web
from multidict import CIMultiDict

from yr.sandbox.tunnel_protocol import (
    MAX_TUNNEL_BODY_SIZE,
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
    make_id,
    parse_frame,
    rebuilt_request_header_items,
    rebuilt_response_header_items,
)

logger = logging.getLogger(__name__)

_PENDING_REQUEST_TTL = 120.0  # seconds


def _http_timeout() -> float:
    """Read YR_TUNNEL_HTTP_TIMEOUT env var (seconds, default 600)."""
    try:
        return float(os.environ.get("YR_TUNNEL_HTTP_TIMEOUT", "600"))
    except ValueError:
        return 600.0


class TunnelServer:
    def __init__(self, ws_port: int, http_port: int):
        self._ws_port = ws_port
        self._http_port = http_port
        self._sdk_ws = None          # active WebSocket from TunnelClient
        self._pending: dict = {}     # id -> Future (HTTP) or Queue (WS)
        self._pending_requests: dict = {}  # id -> (HttpReqFrame, timestamp) for reconnect retry
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ws_server = None
        self._http_runner = None

    async def start(self):
        self._loop = asyncio.get_event_loop()
        self._ws_server = await websockets.serve(
            self._handle_tunnel_conn, "0.0.0.0", self._ws_port,
            reuse_address=True,
            ping_interval=None,
            ping_timeout=None,
            max_size=MAX_TUNNEL_FRAME_SIZE,
        )
        app = web.Application(client_max_size=MAX_TUNNEL_BODY_SIZE)
        app.router.add_route("*", "/{path_info:.*}", self._handle_request)
        self._http_runner = web.AppRunner(app, auto_decompress=False)
        await self._http_runner.setup()
        site = web.TCPSite(self._http_runner, "127.0.0.1", self._http_port)
        await site.start()
        logger.info("TunnelServer started ws=0.0.0.0:%d http=127.0.0.1:%d",
                    self._ws_port, self._http_port)

    async def stop(self):
        if self._ws_server:
            self._ws_server.close()
            try:
                await asyncio.wait_for(self._ws_server.wait_closed(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
        if self._http_runner:
            await self._http_runner.cleanup()

    async def _handle_tunnel_conn(self, websocket):
        if self._sdk_ws is not None:
            logger.info("TunnelClient reconnected, replacing previous connection")
        self._sdk_ws = websocket
        logger.info("TunnelClient connected")

        # Resend pending HTTP requests on reconnect
        await self._resend_pending_requests()

        try:
            async for message in websocket:
                frame = parse_frame(message)
                await self._dispatch_frame(frame)
        except websockets.ConnectionClosed:
            logger.info("TunnelClient disconnected")
        finally:
            # Only clean up state if this connection is still the active one.
            # A reconnect may have already replaced self._sdk_ws; in that case
            # the stale finally block must not tear down the new healthy session.
            if websocket is self._sdk_ws:
                # Clean up expired pending requests
                self._cleanup_expired_pending_requests()
                # For WS channels, still notify disconnection
                for fid, item in list(self._pending.items()):
                    if isinstance(item, asyncio.Queue):
                        await item.put(WsCloseFrame(id="", code=1001, reason="SDK disconnected"))
                        self._pending.pop(fid, None)
                self._sdk_ws = None

    async def _dispatch_frame(self, frame):
        # Ping/Pong is handled immediately, not via pending map
        if isinstance(frame, PingFrame):
            try:
                await self._send_frame(PongFrame(id=frame.id, timestamp=frame.timestamp))
            except RuntimeError:
                pass  # Connection closed, ignore
            return
        fid = frame.id
        if fid not in self._pending:
            return
        target = self._pending[fid]
        if isinstance(frame, HttpRespFrame):
            if isinstance(target, asyncio.Future) and not target.done():
                target.set_result(frame)
        elif isinstance(frame, ErrorFrame):
            # Deliver errors to both HTTP futures and WS queues.
            if isinstance(target, asyncio.Future) and not target.done():
                target.set_result(frame)
            elif isinstance(target, asyncio.Queue):
                await target.put(frame)
        elif isinstance(frame, (WsConnectedFrame, WsMessageFrame, WsCloseFrame)):
            if isinstance(target, asyncio.Queue):
                await target.put(frame)

    async def _send_frame(self, frame):
        if self._sdk_ws is None:
            raise RuntimeError("No TunnelClient connected")
        try:
            await self._sdk_ws.send(frame.to_json())
        except websockets.ConnectionClosedOK:
            raise RuntimeError("TunnelClient disconnected normally") from None
        except websockets.ConnectionClosedError as e:
            logger.warning("TunnelClient connection closed unexpectedly: %s", e)
            raise RuntimeError("TunnelClient disconnected") from e
        except Exception as e:
            logger.warning("Unexpected error sending frame: %s", e)
            raise RuntimeError(f"TunnelClient send error: {e}") from e

    def _cleanup_expired_pending_requests(self) -> None:
        """Remove expired pending requests."""
        import time
        now = time.time()
        expired = [
            fid
            for fid, (_, ts) in self._pending_requests.items()
            if now - ts > _PENDING_REQUEST_TTL
        ]
        for fid in expired:
            self._pending_requests.pop(fid, None)
            # Also remove from pending futures
            fut = self._pending.pop(fid, None)
            if fut and isinstance(fut, asyncio.Future) and not fut.done():
                fut.set_exception(RuntimeError("Request expired"))
            logger.debug("Expired pending request %s", fid)

    async def _resend_pending_requests(self) -> None:
        """Resend pending HTTP requests on reconnect."""
        if not self._pending_requests:
            return

        # Clean expired requests first
        self._cleanup_expired_pending_requests()

        # Resend remaining pending requests
        pending = list(self._pending_requests.items())
        logger.info("Resending %d pending requests on reconnect", len(pending))
        for fid, (frame, _) in pending:
            if self._sdk_ws is None:
                break
            try:
                await self._sdk_ws.send(frame.to_json())
                logger.debug("Resent pending request %s", fid)
            except websockets.ConnectionClosedOK:
                logger.debug("Connection closed normally while resending pending request %s", fid)
                break
            except websockets.ConnectionClosedError as e:
                logger.warning("Connection closed unexpectedly while resending pending request %s: %s", fid, e)
                break
            except Exception as e:
                logger.warning("Unexpected error while resending pending request %s: %s", fid, e)
                break

    async def _handle_request(self, request: web.Request):
        if request.headers.get("Upgrade", "").lower() == "websocket":
            return await self._handle_ws(request)
        return await self._handle_http(request)

    async def _handle_http(self, request: web.Request) -> web.StreamResponse:
        import time
        body = await request.read()
        header_items = [
            (
                name.decode("ascii"),
                value.decode("latin-1"),
            )
            for name, value in request.raw_headers
        ]
        header_items = rebuilt_request_header_items(
            header_items,
            len(body),
        )
        fid = make_id()
        frame = HttpReqFrame(
            id=fid, method=request.method,
            path=str(request.rel_url),
            headers={},
            header_items=header_items,
            body=body,
        )
        fut = self._loop.create_future()
        self._pending[fid] = fut
        self._pending_requests[fid] = (frame, time.time())  # Cache request for reconnect
        try:
            try:
                await self._send_frame(frame)
            except RuntimeError:
                logger.info("Tunnel down for request %s, waiting for reconnect", fid)
            resp_frame = await asyncio.wait_for(fut, timeout=_http_timeout())
        except asyncio.TimeoutError:
            return web.Response(status=504, text="Tunnel timeout")
        finally:
            self._pending.pop(fid, None)
            self._pending_requests.pop(fid, None)  # Clear cached request
        if isinstance(resp_frame, ErrorFrame):
            # Close TCP without sending HTTP response so curl gets a
            # transport-level error (returncode!=0), matching the behavior
            # of connecting directly to an unreachable service.
            request.transport.close()
            return web.Response()  # not sent; transport already closed
        header_items = rebuilt_response_header_items(
            resp_frame.header_items,
            request.method,
            resp_frame.status,
            len(resp_frame.body),
        )
        headers = CIMultiDict()
        for name, value in header_items:
            headers.add(name, value)
        resp = web.StreamResponse(status=resp_frame.status, headers=headers)
        await resp.prepare(request)
        body_allowed = (
            request.method != "HEAD"
            and not 100 <= resp_frame.status < 200
            and resp_frame.status not in (204, 304)
        )
        if body_allowed and resp_frame.body:
            await resp.write(resp_frame.body)
        await resp.write_eof()
        return resp

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws_resp = web.WebSocketResponse(max_msg_size=MAX_TUNNEL_BODY_SIZE)
        await ws_resp.prepare(request)
        fid = make_id()
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        queue: asyncio.Queue = asyncio.Queue()
        self._pending[fid] = queue
        try:
            try:
                await self._send_frame(WsConnectFrame(id=fid, path=str(request.rel_url), headers=headers))
            except RuntimeError as e:
                await ws_resp.close(code=1011, message=str(e).encode())
                return ws_resp
            ack = await asyncio.wait_for(queue.get(), timeout=10)
            if not isinstance(ack, WsConnectedFrame):
                msg = ack.message if isinstance(ack, ErrorFrame) else "tunnel error"
                await ws_resp.close(code=1011, message=msg.encode())
                return ws_resp

            async def from_portb_to_sdk():
                try:
                    async for msg in ws_resp:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await self._send_frame(WsMessageFrame(id=fid, data=msg.data, binary=False))
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await self._send_frame(
                                WsMessageFrame(id=fid, data=base64.b64encode(msg.data).decode(), binary=True)
                            )
                        elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
                except RuntimeError:
                    pass  # Tunnel disconnected
                except Exception as e:
                    logger.warning("WS channel %s: unexpected error in from_portb_to_sdk: %s", fid, e)
                finally:
                    try:
                        await self._send_frame(WsCloseFrame(id=fid))
                    except RuntimeError:
                        pass
                    except Exception as e:
                        logger.warning("WS channel %s: unexpected error sending close frame: %s", fid, e)

            async def from_sdk_to_portb():
                try:
                    while True:
                        f = await queue.get()
                        if isinstance(f, WsMessageFrame):
                            if f.binary:
                                await ws_resp.send_bytes(base64.b64decode(f.data))
                            else:
                                await ws_resp.send_str(f.data)
                        elif isinstance(f, WsCloseFrame):
                            await ws_resp.close(code=f.code)
                            break
                        elif isinstance(f, ErrorFrame):
                            await ws_resp.close(code=1011, message=f.message.encode())
                            break
                except Exception as e:
                    logger.warning("WS channel %s: unexpected error in from_sdk_to_portb: %s", fid, e)

            t1 = asyncio.create_task(from_portb_to_sdk())
            t2 = asyncio.create_task(from_sdk_to_portb())
            done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
                with contextlib.suppress(Exception):
                    await task
            # Propagate RuntimeError from completed tasks as a graceful close
            for task in done:
                exc = task.exception()
                if isinstance(exc, RuntimeError):
                    if not ws_resp.closed:
                        await ws_resp.close(code=1011, message=str(exc).encode())
                    break
        finally:
            self._pending.pop(fid, None)
        return ws_resp


async def _main(ws_port: int, http_port: int):
    server = TunnelServer(ws_port, http_port)
    await server.start()
    await asyncio.Future()  # run forever


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--ws-port", type=int, default=8765)
    p.add_argument("--http-port", type=int, default=8766)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_main(args.ws_port, args.http_port))
