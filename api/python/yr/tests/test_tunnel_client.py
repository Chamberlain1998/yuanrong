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

# api/python/yr/tests/test_tunnel_client.py
import asyncio
import time
import unittest

import websockets
from aiohttp import web
from yr.sandbox.tunnel_client import TunnelClient
from yr.sandbox.tunnel_protocol import (
    ErrorFrame,
    HttpReqFrame,
    HttpRespFrame,
    PingFrame,
    PongFrame,
    parse_frame,
)

MOCK_WS_PORT = 28765  # mock Port A (pretend to be TunnelServer)
MOCK_UPSTREAM_PORT = 28800  # mock local service C


class TestTunnelClientHttp(unittest.TestCase):
    def test_http_req_frame_forwarded_to_upstream(self):
        """Forward an http_req upstream and return its http_resp frame."""
        results = {}

        async def _run():
            # Start mock upstream (service C)
            async def upstream_handler(request):
                results["upstream_path"] = request.path
                return web.Response(
                    status=200,
                    body=b"from-upstream",
                    headers={"Content-Type": "text/plain"},
                )

            upstream_app = web.Application()
            upstream_app.router.add_route("*", "/{path_info:.*}", upstream_handler)
            upstream_runner = web.AppRunner(upstream_app)
            await upstream_runner.setup()
            await web.TCPSite(upstream_runner, "127.0.0.1", MOCK_UPSTREAM_PORT).start()

            # Start mock Port A (WS server)
            received_frames = []

            async def mock_port_a(websocket):
                # Send an http_req frame to TunnelClient
                req = HttpReqFrame(
                    id="r1", method="GET", path="/test", headers={}, body=b""
                )
                await websocket.send(req.to_json())
                # Receive the http_resp frame back
                raw = await asyncio.wait_for(websocket.recv(), timeout=5)
                received_frames.append(parse_frame(raw))

            ws_server = await websockets.serve(mock_port_a, "127.0.0.1", MOCK_WS_PORT)

            # Start TunnelClient
            client = TunnelClient(upstream=f"http://127.0.0.1:{MOCK_UPSTREAM_PORT}")
            await asyncio.to_thread(client.start, f"ws://127.0.0.1:{MOCK_WS_PORT}", 2)
            await asyncio.sleep(1)  # let client connect and process

            ws_server.close()
            await ws_server.wait_closed()
            await upstream_runner.cleanup()
            client.stop()
            return received_frames

        frames = asyncio.run(_run())
        self.assertEqual(len(frames), 1)
        self.assertIsInstance(frames[0], HttpRespFrame)
        self.assertEqual(frames[0].id, "r1")
        self.assertEqual(frames[0].status, 200)
        self.assertEqual(frames[0].body, b"from-upstream")

    def test_upstream_error_sends_error_frame(self):
        """If upstream is unreachable, TunnelClient sends error frame."""
        received_frames = []

        async def _run():
            async def mock_port_a(websocket):
                req = HttpReqFrame(
                    id="r2", method="GET", path="/x", headers={}, body=b""
                )
                await websocket.send(req.to_json())
                raw = await asyncio.wait_for(websocket.recv(), timeout=5)
                received_frames.append(parse_frame(raw))

            ws_server = await websockets.serve(mock_port_a, "127.0.0.1", MOCK_WS_PORT)
            # Point to a port with nothing listening
            client = TunnelClient(upstream="http://127.0.0.1:19999")
            await asyncio.to_thread(client.start, f"ws://127.0.0.1:{MOCK_WS_PORT}", 2)
            await asyncio.sleep(1)
            ws_server.close()
            await ws_server.wait_closed()
            client.stop()

        asyncio.run(_run())
        self.assertEqual(len(received_frames), 1)
        self.assertIsInstance(received_frames[0], ErrorFrame)
        self.assertEqual(received_frames[0].id, "r2")

    def test_client_reconnects_after_disconnect(self):
        """TunnelClient should reconnect when server disconnects."""
        connect_count = {"n": 0}

        async def _run():
            async def mock_port_a(websocket):
                connect_count["n"] += 1
                # Immediately close — force reconnect
                await websocket.close()

            ws_server = await websockets.serve(mock_port_a, "127.0.0.1", MOCK_WS_PORT)
            client = TunnelClient(upstream="http://127.0.0.1:19999")
            await asyncio.to_thread(client.start, f"ws://127.0.0.1:{MOCK_WS_PORT}", 2)
            await asyncio.sleep(4)  # reconnect delay is 3s
            ws_server.close()
            await ws_server.wait_closed()
            client.stop()

        asyncio.run(_run())
        self.assertGreaterEqual(connect_count["n"], 2)


class TestTunnelClientHeartbeat(unittest.TestCase):
    def test_client_sends_ping_and_expects_pong(self):
        """Client sends PingFrame periodically and Server echoes PongFrame."""
        received_pings = []

        async def _run():
            async def mock_server(ws):
                async for msg in ws:
                    frame = parse_frame(msg)
                    if isinstance(frame, PingFrame):
                        received_pings.append(frame)
                        await ws.send(
                            PongFrame(id=frame.id, timestamp=frame.timestamp).to_json()
                        )

            server = await websockets.serve(mock_server, "127.0.0.1", MOCK_WS_PORT)
            client = TunnelClient(
                upstream=f"http://127.0.0.1:{MOCK_UPSTREAM_PORT}",
                ping_interval=0.5,
            )
            await asyncio.to_thread(client.start, f"ws://127.0.0.1:{MOCK_WS_PORT}", 2)
            await asyncio.sleep(2)
            client.stop()
            server.close()
            await server.wait_closed()

        asyncio.run(_run())
        assert len(received_pings) >= 3

    def test_client_reconnects_on_pong_timeout(self):
        """Client detects dead connection (no pong) and reconnects."""
        connect_count = 0

        async def _run():
            nonlocal connect_count

            async def mock_server(ws):
                nonlocal connect_count
                connect_count += 1
                if connect_count == 1:
                    async for msg in ws:
                        pass  # don't respond to pings
                else:
                    async for msg in ws:
                        frame = parse_frame(msg)
                        if isinstance(frame, PingFrame):
                            await ws.send(
                                PongFrame(
                                    id=frame.id, timestamp=frame.timestamp
                                ).to_json()
                            )

            server = await websockets.serve(mock_server, "127.0.0.1", MOCK_WS_PORT)
            client = TunnelClient(
                upstream=f"http://127.0.0.1:{MOCK_UPSTREAM_PORT}",
                ping_interval=0.3,
                ping_timeout=0.5,
                reconnect_base_delay=0.2,
                reconnect_max_delay=1.0,
            )
            await asyncio.to_thread(client.start, f"ws://127.0.0.1:{MOCK_WS_PORT}", 2)
            await asyncio.sleep(5)
            client.stop()
            server.close()
            await server.wait_closed()

        asyncio.run(_run())
        assert connect_count >= 2


class TestTunnelClientBackoff(unittest.TestCase):
    def test_exponential_backoff_on_reconnect(self):
        """Client eventually connects when server becomes available after failures."""
        connect_times = []

        async def _run():
            # Start client first (server not running) -- it will fail and back off
            client = TunnelClient(
                upstream=f"http://127.0.0.1:{MOCK_UPSTREAM_PORT}",
                ping_interval=30.0,
                reconnect_base_delay=0.2,
                reconnect_max_delay=1.0,
            )
            await asyncio.to_thread(client.start, f"ws://127.0.0.1:{MOCK_WS_PORT}", 2)
            # Let client fail a few times with no server
            await asyncio.sleep(0.5)

            # Now start server -- client should connect on next retry
            async def mock_server(ws):
                connect_times.append(time.monotonic())
                async for msg in ws:
                    frame = parse_frame(msg)
                    if isinstance(frame, PingFrame):
                        await ws.send(
                            PongFrame(id=frame.id, timestamp=frame.timestamp).to_json()
                        )

            server = await websockets.serve(mock_server, "127.0.0.1", MOCK_WS_PORT)
            await asyncio.sleep(2)  # wait for client to connect
            client.stop()
            server.close()
            await server.wait_closed()

        asyncio.run(_run())
        # Client should have connected once server became available
        self.assertGreaterEqual(len(connect_times), 1)

    def test_exponential_backoff_delays_increase(self):
        """Failed connect attempts show increasing delays (exponential backoff)."""
        connect_attempts = []

        async def _run():
            # Record timestamps of each connect attempt
            # by intercepting websockets.connect
            original_connect = websockets.connect
            attempt_times = []

            class TrackingConnect:
                """WS that always fails to simulate server-down."""

                def __init__(self, *args, **kwargs):
                    attempt_times.append(time.monotonic())

                async def __aenter__(self):
                    raise ConnectionRefusedError("simulated server down")

                async def __aexit__(self, *args):
                    return False

            websockets.connect = TrackingConnect

            try:
                client = TunnelClient(
                    upstream=f"http://127.0.0.1:{MOCK_UPSTREAM_PORT}",
                    ping_interval=30.0,
                    reconnect_base_delay=0.5,
                    reconnect_max_delay=4.0,
                )
                await asyncio.to_thread(
                    client.start, f"ws://127.0.0.1:{MOCK_WS_PORT}", 0.2
                )
                await asyncio.sleep(8)
                client.stop()
            finally:
                websockets.connect = original_connect

            connect_attempts.extend(attempt_times)

        asyncio.run(_run())
        self.assertGreaterEqual(
            len(connect_attempts), 3, "Expected at least 3 connect attempts"
        )
        delay1 = connect_attempts[1] - connect_attempts[0]
        delay2 = connect_attempts[2] - connect_attempts[1]
        # delay1 should be ~1.0+s (attempt 1: base*2^1)
        # delay2 should be ~2.0+s (attempt 2: base*2^2)
        # delay2 should be clearly larger than delay1
        self.assertGreater(
            delay2,
            delay1 * 1.1,
            "Expected exponential growth: "
            f"delay2={delay2:.3f} > delay1*1.1={delay1*1.1:.3f}",
        )


if __name__ == "__main__":
    unittest.main()
