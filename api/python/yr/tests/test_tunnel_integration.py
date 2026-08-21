# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
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

# api/python/yr/tests/test_tunnel_integration.py
"""End-to-end: TunnelServer (in sandbox) + TunnelClient (local) + mock upstream."""

import asyncio
import threading
import unittest
from unittest import mock

import websockets
from aiohttp import web
from yr.sandbox.tunnel_client import TunnelClient
from yr.sandbox.tunnel_server import TunnelServer

SRV_WS_PORT = 38765
SRV_HTTP_PORT = 38766
UPSTREAM_PORT = 38800


class TestIntegration(unittest.TestCase):
    def setUp(self):
        """Start TunnelServer in a background asyncio loop."""
        self._server_loop = asyncio.new_event_loop()
        self._server = TunnelServer(ws_port=SRV_WS_PORT, http_port=SRV_HTTP_PORT)
        ready = threading.Event()

        def _run_server():
            asyncio.set_event_loop(self._server_loop)
            self._server_loop.run_until_complete(self._server.start())
            ready.set()
            self._server_loop.run_forever()

        self._server_thread = threading.Thread(target=_run_server, daemon=True)
        self._server_thread.start()
        ready.wait(timeout=5)

    def tearDown(self):
        # Properly close server sockets before stopping the loop so ports are
        # released immediately and the next test can bind to the same ports.
        stop_fut = asyncio.run_coroutine_threadsafe(
            self._server.stop(), self._server_loop
        )
        stop_fut.result(timeout=5)
        self._server_loop.call_soon_threadsafe(self._server_loop.stop)
        self._server_thread.join(timeout=5)
        self._server_loop.close()

    def test_http_get_roundtrip_through_tunnel(self):
        """Round-trip Port B HTTP through TunnelClient to the mock upstream."""
        import time

        async def handler(request):
            return web.Response(
                status=200, body=b"hello-from-c", headers={"Content-Type": "text/plain"}
            )

        app = web.Application()
        app.router.add_route("GET", "/ping", handler)
        upstream_runner = self._start_upstream(app)

        async def _fetch_via_portb():
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{SRV_HTTP_PORT}/ping"
                ) as resp:
                    return resp.status, await resp.read()

        try:
            client = TunnelClient(upstream=f"http://127.0.0.1:{UPSTREAM_PORT}")
            client.start(f"ws://127.0.0.1:{SRV_WS_PORT}")
            time.sleep(0.5)  # let client connect

            status, body = asyncio.run(_fetch_via_portb())
            self.assertEqual(status, 200)
            self.assertEqual(body, b"hello-from-c")
        finally:
            client.stop()
            self._stop_upstream(upstream_runner)

    def test_large_http_response_roundtrip_through_tunnel(self):
        """Pass HTTP responses larger than the default 1 MiB WS frame."""
        import time

        large_body = b"x" * (2 << 20)

        async def handler(request):
            return web.Response(
                status=200,
                body=large_body,
                headers={"Content-Type": "application/octet-stream"},
            )

        app = web.Application()
        app.router.add_route("GET", "/large", handler)
        upstream_runner = self._start_upstream(app)

        async def _fetch_via_portb():
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{SRV_HTTP_PORT}/large"
                ) as resp:
                    return resp.status, await resp.read()

        try:
            client = TunnelClient(upstream=f"http://127.0.0.1:{UPSTREAM_PORT}")
            client.start(f"ws://127.0.0.1:{SRV_WS_PORT}")
            time.sleep(0.5)

            status, body = asyncio.run(_fetch_via_portb())
            self.assertEqual(status, 200)
            self.assertEqual(body, large_body)
        finally:
            client.stop()
            self._stop_upstream(upstream_runner)

    def test_http_post_with_body_roundtrip(self):
        """POST with body is forwarded and body reaches upstream."""
        import time

        received_body = {}

        async def handler(request):
            received_body["data"] = await request.read()
            return web.Response(status=201, body=b"created")

        app = web.Application()
        app.router.add_route("POST", "/items", handler)
        upstream_runner = self._start_upstream(app)

        async def _post_via_portb():
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"http://127.0.0.1:{SRV_HTTP_PORT}/items",
                    data=b'{"name":"test"}',
                    headers={"Content-Type": "application/json"},
                ) as resp:
                    return resp.status

        try:
            client = TunnelClient(upstream=f"http://127.0.0.1:{UPSTREAM_PORT}")
            client.start(f"ws://127.0.0.1:{SRV_WS_PORT}")
            time.sleep(0.5)

            status = asyncio.run(_post_via_portb())
            self.assertEqual(status, 201)
            self.assertEqual(received_body["data"], b'{"name":"test"}')
        finally:
            client.stop()
            self._stop_upstream(upstream_runner)

    def test_actor_v2_http_survives_network_flaps_in_all_stream_phases(self):
        """Keep one upstream call across upload, response wait, and SSE flaps."""

        upload_started = threading.Event()
        wait_started = threading.Event()
        stream_started = threading.Event()
        calls = {"upload": 0, "wait": 0, "stream": 0}
        uploaded = bytearray()

        async def upload_handler(request):
            calls["upload"] += 1
            async for chunk in request.content.iter_chunked(32 * 1024):
                uploaded.extend(chunk)
                upload_started.set()
                await asyncio.sleep(0.005)
            return web.Response(body=b"upload-ok")

        async def wait_handler(request):
            calls["wait"] += 1
            wait_started.set()
            await asyncio.sleep(0.35)
            return web.Response(body=b"wait-ok")

        async def stream_handler(request):
            calls["stream"] += 1
            response = web.StreamResponse(
                status=200,
                headers={"Content-Type": "text/event-stream"},
            )
            await response.prepare(request)
            await response.write(b"data: first\n\n")
            stream_started.set()
            await asyncio.sleep(0.35)
            await response.write(b"data: second\n\n")
            await response.write_eof()
            return response

        app = web.Application()
        app.router.add_post("/upload-flap", upload_handler)
        app.router.add_get("/wait-flap", wait_handler)
        app.router.add_get("/stream-flap", stream_handler)
        upstream_runner = self._start_upstream(app)
        client = TunnelClient(
            upstream=f"http://127.0.0.1:{UPSTREAM_PORT}",
            reconnect_base_delay=0.02,
            reconnect_max_delay=0.05,
        )

        def close_control_websocket():
            websocket = client._ws
            self.assertIsNotNone(websocket)
            future = asyncio.run_coroutine_threadsafe(
                websocket.close(code=1012, reason="test network flap"),
                client._loop,
            )
            future.result(timeout=2)

        async def fetch_with_flap(method, path, started, **kwargs):
            import aiohttp

            async with aiohttp.ClientSession() as session:
                request = asyncio.create_task(
                    session.request(
                        method,
                        f"http://127.0.0.1:{SRV_HTTP_PORT}{path}",
                        **kwargs,
                    )
                )
                started_ok = await asyncio.to_thread(started.wait, 2)
                self.assertTrue(started_ok)
                await asyncio.to_thread(close_control_websocket)
                response = await asyncio.wait_for(request, timeout=5)
                async with response:
                    return response.status, await response.read()

        upload_body = b"upload-resume-" * 180_000
        try:
            self.assertTrue(client.start(f"ws://127.0.0.1:{SRV_WS_PORT}"))
            upload_status, upload_response = asyncio.run(
                fetch_with_flap(
                    "POST",
                    "/upload-flap",
                    upload_started,
                    data=upload_body,
                )
            )
            wait_status, wait_response = asyncio.run(
                fetch_with_flap("GET", "/wait-flap", wait_started)
            )
            stream_status, stream_response = asyncio.run(
                fetch_with_flap("GET", "/stream-flap", stream_started)
            )
            self.assertEqual((upload_status, upload_response), (200, b"upload-ok"))
            self.assertEqual(bytes(uploaded), upload_body)
            self.assertEqual((wait_status, wait_response), (200, b"wait-ok"))
            self.assertEqual(
                (stream_status, stream_response),
                (200, b"data: first\n\ndata: second\n\n"),
            )
            self.assertEqual(calls, {"upload": 1, "wait": 1, "stream": 1})
        finally:
            client.stop()
            self._stop_upstream(upstream_runner)

    def test_ws_message_relay_through_tunnel(self):
        """Relay WebSocket messages between Port B and the upstream server."""
        import time

        async def ws_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    await ws.send_str(f"echo:{msg.data}")
            return ws

        app = web.Application()
        app.router.add_route("GET", "/ws", ws_handler)
        upstream_runner = self._start_upstream(app)

        async def _ws_via_portb():
            async with websockets.connect(f"ws://127.0.0.1:{SRV_HTTP_PORT}/ws") as ws:
                await ws.send("hello")
                reply = await asyncio.wait_for(ws.recv(), timeout=5)
                return reply

        try:
            client = TunnelClient(upstream=f"http://127.0.0.1:{UPSTREAM_PORT}")
            client.start(f"ws://127.0.0.1:{SRV_WS_PORT}")
            time.sleep(0.5)

            reply = asyncio.run(_ws_via_portb())
            self.assertEqual(reply, "echo:hello")
        finally:
            client.stop()
            self._stop_upstream(upstream_runner)

    def test_concurrent_http_requests(self):
        """Multiple HTTP requests in parallel are all handled correctly."""
        import time

        async def handler(request):
            idx = request.match_info["idx"]
            return web.Response(status=200, body=f"response-{idx}".encode())

        app = web.Application()
        app.router.add_route("GET", "/item/{idx}", handler)
        upstream_runner = self._start_upstream(app)

        async def _fetch_all():
            import aiohttp

            async with aiohttp.ClientSession() as session:
                tasks = [
                    session.get(f"http://127.0.0.1:{SRV_HTTP_PORT}/item/{i}")
                    for i in range(10)
                ]
                responses = await asyncio.gather(*tasks)
                results = []
                for resp in responses:
                    body = await resp.read()
                    results.append((resp.status, body))
                    resp.close()
                return results

        try:
            client = TunnelClient(upstream=f"http://127.0.0.1:{UPSTREAM_PORT}")
            client.start(f"ws://127.0.0.1:{SRV_WS_PORT}")
            time.sleep(0.5)

            results = asyncio.run(_fetch_all())
            self.assertEqual(len(results), 10)
            for status, _ in results:
                self.assertEqual(status, 200)
            bodies = {body for _, body in results}
            self.assertEqual(len(bodies), 10)  # all distinct responses
        finally:
            client.stop()
            self._stop_upstream(upstream_runner)

    def test_tunnel_reconnects_after_server_restart(self):
        """TunnelClient reconnects after TunnelServer restart.

        Full keepalive + reconnect flow:
        1. Client connects with fast heartbeat
        2. Verify tunnel works (HTTP roundtrip)
        3. Kill server (simulate crash)
        4. Client detects failure via heartbeat timeout
        5. Restart server on same ports
        6. Client reconnects with exponential backoff
        7. Verify tunnel recovers (HTTP roundtrip succeeds again)
        """
        # Stop the server started by setUp so we can manage our own lifecycle.
        stop_fut = asyncio.run_coroutine_threadsafe(
            self._server.stop(), self._server_loop
        )
        stop_fut.result(timeout=5)

        async def _run():
            # Start mock upstream HTTP service
            async def upstream_handler(request):
                return web.Response(status=200, body=b"ok")

            upstream_app = web.Application()
            upstream_app.router.add_route("*", "/{path_info:.*}", upstream_handler)
            upstream_runner = web.AppRunner(upstream_app)
            await upstream_runner.setup()
            await web.TCPSite(upstream_runner, "127.0.0.1", UPSTREAM_PORT).start()
            server = None
            server2 = None
            client = None
            try:
                # Start tunnel server
                server = TunnelServer(ws_port=SRV_WS_PORT, http_port=SRV_HTTP_PORT)
                await server.start()

                # Start client with fast heartbeat for quick failure detection
                client = TunnelClient(
                    upstream=f"http://127.0.0.1:{UPSTREAM_PORT}",
                    ping_interval=0.5,
                    ping_timeout=0.5,
                    reconnect_base_delay=0.2,
                    reconnect_max_delay=1.0,
                )
                client.start(f"ws://127.0.0.1:{SRV_WS_PORT}")
                await asyncio.sleep(1)

                # Verify tunnel works: send HTTP request through tunnel
                import aiohttp

                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{SRV_HTTP_PORT}/test"
                    ) as resp:
                        self.assertEqual(resp.status, 200)
                        body = await resp.read()
                        self.assertEqual(body, b"ok")

                # Kill server (simulate server crash)
                await server.stop()
                server = None
                await asyncio.sleep(2)

                # Restart server on same ports
                server2 = TunnelServer(ws_port=SRV_WS_PORT, http_port=SRV_HTTP_PORT)
                await server2.start()
                await asyncio.sleep(2)

                # Verify tunnel recovers: HTTP request should work again
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"http://127.0.0.1:{SRV_HTTP_PORT}/test2"
                    ) as resp:
                        self.assertEqual(resp.status, 200)
                        body = await resp.read()
                        self.assertEqual(body, b"ok")
            finally:
                if client is not None:
                    client.stop()
                if server is not None:
                    await server.stop()
                if server2 is not None:
                    await server2.stop()
                await upstream_runner.cleanup()

        asyncio.run(_run())

    def test_tunnel_reconnect_restart_cleans_up_on_restart_failure(self):
        """Restart failures still stop the client and clean up the upstream app."""
        cleanup = {
            "client_stopped": False,
            "upstream_cleaned": False,
        }

        class _DoneFuture:
            @staticmethod
            def result(timeout=None):
                return None

        def _run_coroutine_threadsafe(coro, loop):
            asyncio.run(coro)
            return _DoneFuture()

        class _FakeExistingServer:
            async def stop(self):
                return None

        class _FakeTunnelServer:
            start_calls = 0

            def __init__(self, ws_port, http_port):
                self.ws_port = ws_port
                self.http_port = http_port

            async def start(self):
                type(self).start_calls += 1
                if type(self).start_calls == 2:
                    raise RuntimeError("restart failed")

            async def stop(self):
                return None

        class _FakeTunnelClient:
            def __init__(self, upstream, **kwargs):
                self.upstream = upstream
                self.kwargs = kwargs

            @staticmethod
            def start(url):
                return None

            @staticmethod
            def stop():
                cleanup["client_stopped"] = True

        class _FakeRunner:
            async def setup(self):
                return None

            async def cleanup(self):
                cleanup["upstream_cleaned"] = True

        class _FakeTCPSite:
            def __init__(self, runner, host, port):
                self.runner = runner
                self.host = host
                self.port = port

            async def start(self):
                return None

        class _FakeResponse:
            status = 200

            async def read(self):
                return b"ok"

        class _FakeRequestContext:
            async def __aenter__(self):
                return _FakeResponse()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class _FakeClientSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            @staticmethod
            def get(url):
                return _FakeRequestContext()

        real_server = self._server
        real_server_loop = self._server_loop
        self._server = _FakeExistingServer()
        self._server_loop = object()
        try:
            with mock.patch(__name__ + ".TunnelServer", _FakeTunnelServer), mock.patch(
                __name__ + ".TunnelClient", _FakeTunnelClient
            ), mock.patch(
                __name__ + ".web.AppRunner", side_effect=lambda app: _FakeRunner()
            ), mock.patch(
                __name__ + ".web.TCPSite", _FakeTCPSite
            ), mock.patch(
                __name__ + ".asyncio.run_coroutine_threadsafe",
                side_effect=_run_coroutine_threadsafe,
            ), mock.patch(
                __name__ + ".asyncio.sleep", new=mock.AsyncMock()
            ), mock.patch(
                "aiohttp.ClientSession", _FakeClientSession
            ):
                with self.assertRaisesRegex(RuntimeError, "restart failed"):
                    self.test_tunnel_reconnects_after_server_restart()
        finally:
            self._server = real_server
            self._server_loop = real_server_loop

        self.assertTrue(cleanup["client_stopped"])
        self.assertTrue(cleanup["upstream_cleaned"])

    def test_ws_channel_cleanup_on_client_disconnect(self):
        """WS channel is removed from _ws_channels when client disconnects."""
        import time

        async def ws_handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            async for _ in ws:
                continue
            return ws

        app = web.Application()
        app.router.add_route("GET", "/ws", ws_handler)
        upstream_runner = self._start_upstream(app)

        async def _connect_and_disconnect():
            async with websockets.connect(f"ws://127.0.0.1:{SRV_HTTP_PORT}/ws"):
                await asyncio.sleep(0.1)
                # connection opens and closes
            await asyncio.sleep(0.2)

        try:
            client = TunnelClient(upstream=f"http://127.0.0.1:{UPSTREAM_PORT}")
            client.start(f"ws://127.0.0.1:{SRV_WS_PORT}")
            time.sleep(0.5)

            asyncio.run(_connect_and_disconnect())

            # After disconnect, no lingering ws channels on client side
            self.assertEqual(len(getattr(client, "_ws_channels")), 0)
        finally:
            client.stop()
            self._stop_upstream(upstream_runner)

    def _start_upstream(self, app: web.Application) -> web.AppRunner:
        """Start an aiohttp app on UPSTREAM_PORT inside the shared server loop."""

        async def _start():
            runner = web.AppRunner(app)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", UPSTREAM_PORT).start()
            return runner

        return asyncio.run_coroutine_threadsafe(_start(), self._server_loop).result(
            timeout=5
        )

    def _stop_upstream(self, runner: web.AppRunner) -> None:
        asyncio.run_coroutine_threadsafe(runner.cleanup(), self._server_loop).result(
            timeout=5
        )


if __name__ == "__main__":
    unittest.main()
