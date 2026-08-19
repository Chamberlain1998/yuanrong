# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# Licensed under the Apache License, Version 2.0.
"""Independent V2 protocol and lifecycle tests for Python TunnelClient."""

import asyncio
import base64
import http.server
import json
import threading
import unittest
from typing import ClassVar

from websockets.asyncio.server import serve
from yr.sandbox.tunnel_client import TunnelClient
from yr.sandbox.tunnel_protocol import (
    BinaryEnvelope,
    BinaryKind,
    ProtocolError,
    hello_frame,
)


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    requests: ClassVar[list] = []
    block_started: ClassVar[threading.Event] = threading.Event()
    block_release: ClassVar[threading.Event] = threading.Event()

    def do_GET(self):
        type(self).requests.append((self.path, self.headers, b""))
        if self.path == "/block":
            type(self).block_started.set()
            type(self).block_release.wait(timeout=3)
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).requests.append((self.path, self.headers, body))
        response = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args):
        return


class _FrameWebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = asyncio.Queue()

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.incoming.get()
        if message is None:
            raise StopAsyncIteration
        return message if isinstance(message, bytes) else json.dumps(message)

    async def send(self, message):
        await self.sent.put(
            message if isinstance(message, bytes) else json.loads(message)
        )

    def feed(self, frame):
        self.incoming.put_nowait(frame)

    def close_input(self):
        self.incoming.put_nowait(None)


class TunnelClientV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _RecordingHandler.requests = []
        _RecordingHandler.block_started = threading.Event()
        _RecordingHandler.block_release = threading.Event()
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _RecordingHandler,
        )
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever,
            daemon=True,
        )
        self.upstream_thread.start()

    async def asyncTearDown(self):
        _RecordingHandler.block_release.set()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=2)

    async def _start_v2(self, websocket):
        websocket.feed(hello_frame())
        client = TunnelClient(
            upstream=f"127.0.0.1:{self.upstream.server_port}",
        )
        task = asyncio.create_task(client._proxy_loop(websocket))
        response_hello = await asyncio.wait_for(websocket.sent.get(), timeout=2)
        self.assertEqual(response_hello["type"], "hello")
        self.assertEqual(response_hello["max_body_size"], 512 * 1024 * 1024)
        return client, task

    async def test_streams_request_and_response_with_credit_backpressure(self):
        request_id = "00112233-4455-6677-8899-aabbccddeeff"
        payload = b"a" * 100_000
        websocket = _FrameWebSocket()
        client, proxy_task = await self._start_v2(websocket)
        del client
        websocket.feed(
            {
                "type": "http_req_begin",
                "id": request_id,
                "method": "POST",
                "path": "/stream",
                "headers": [["Content-Type", "application/octet-stream"]],
                "content_length": len(payload),
            }
        )
        initial_window = await asyncio.wait_for(websocket.sent.get(), timeout=2)
        self.assertEqual(
            initial_window,
            {
                "type": "window",
                "id": request_id,
                "credits": 16,
            },
        )
        websocket.feed(
            BinaryEnvelope(
                request_id=request_id,
                kind=BinaryKind.HTTP_REQUEST_DATA,
                payload=payload[:65536],
            ).encode()
        )
        websocket.feed(
            BinaryEnvelope(
                request_id=request_id,
                kind=BinaryKind.HTTP_REQUEST_DATA,
                payload=payload[65536:],
            ).encode()
        )
        websocket.feed({"type": "http_req_end", "id": request_id})

        returned_credits = 0
        while True:
            frame = await asyncio.wait_for(websocket.sent.get(), timeout=2)
            if frame["type"] == "http_resp_begin":
                response_begin = frame
                break
            self.assertEqual(frame["type"], "window")
            returned_credits += frame["credits"]
        self.assertEqual(returned_credits, 2)
        self.assertEqual(response_begin["status"], 200)
        websocket.feed({"type": "window", "id": request_id, "credits": 16})
        response_data = await asyncio.wait_for(websocket.sent.get(), timeout=2)
        envelope = BinaryEnvelope.decode(response_data)
        self.assertEqual(envelope.kind, BinaryKind.HTTP_RESPONSE_DATA)
        self.assertEqual(envelope.payload, b"ok")
        self.assertEqual(
            await asyncio.wait_for(websocket.sent.get(), timeout=2),
            {"type": "http_resp_end", "id": request_id},
        )
        websocket.close_input()
        await asyncio.wait_for(proxy_task, timeout=2)
        self.assertEqual(_RecordingHandler.requests[-1][2], payload)

    async def test_peer_error_cancels_only_target_stream(self):
        request_id = "00112233-4455-6677-8899-aabbccddeeff"
        websocket = _FrameWebSocket()
        _, proxy_task = await self._start_v2(websocket)
        websocket.feed(
            {
                "type": "http_req_begin",
                "id": request_id,
                "method": "POST",
                "path": "/stream",
                "headers": [],
                "content_length": 1,
            }
        )
        self.assertEqual(
            (await asyncio.wait_for(websocket.sent.get(), timeout=2))["type"],
            "window",
        )
        websocket.feed(
            {
                "type": "error",
                "id": request_id,
                "message": "downstream closed",
            }
        )
        websocket.feed({"type": "ping", "id": "healthy", "timestamp": 1})
        pong = await asyncio.wait_for(websocket.sent.get(), timeout=2)
        self.assertEqual(pong["type"], "pong")
        self.assertEqual(pong["id"], "healthy")
        websocket.close_input()
        await asyncio.wait_for(proxy_task, timeout=2)

    async def test_binary_websocket_message_uses_bounded_raw_chunks(self):
        request_id = "00112233-4455-6677-8899-aabbccddeeff"
        payload = b"z" * 100_000

        async def echo(upstream_websocket):
            message = await upstream_websocket.recv()
            self.assertEqual(message, payload)
            await upstream_websocket.send(message)

        async with serve(echo, "127.0.0.1", 0) as upstream_server:
            upstream_port = upstream_server.sockets[0].getsockname()[1]
            websocket = _FrameWebSocket()
            websocket.feed(hello_frame())
            client = TunnelClient(upstream=f"127.0.0.1:{upstream_port}")
            proxy_task = asyncio.create_task(client._proxy_loop(websocket))
            self.assertEqual(
                (await asyncio.wait_for(websocket.sent.get(), timeout=2))["type"],
                "hello",
            )
            websocket.feed(
                {
                    "type": "ws_connect",
                    "id": request_id,
                    "path": "/binary",
                    "headers": {},
                }
            )
            self.assertEqual(
                await asyncio.wait_for(websocket.sent.get(), timeout=2),
                {"type": "ws_connected", "id": request_id},
            )
            websocket.feed(
                BinaryEnvelope(
                    request_id=request_id,
                    kind=BinaryKind.WS_BINARY_DATA,
                    payload=payload[:65536],
                ).encode()
            )
            websocket.feed(
                BinaryEnvelope(
                    request_id=request_id,
                    kind=BinaryKind.WS_BINARY_DATA,
                    payload=payload[65536:],
                    end_of_body=True,
                ).encode()
            )
            echoed = bytearray()
            while True:
                raw = await asyncio.wait_for(websocket.sent.get(), timeout=2)
                envelope = BinaryEnvelope.decode(raw)
                echoed.extend(envelope.payload)
                if envelope.end_of_body:
                    break
            self.assertEqual(echoed, payload)
            websocket.close_input()
            await asyncio.wait_for(proxy_task, timeout=2)

    async def test_malformed_binary_frame_terminates_and_cleans_tasks(self):
        websocket = _FrameWebSocket()
        _, proxy_task = await self._start_v2(websocket)
        websocket.feed(b"bad")
        with self.assertRaises(ProtocolError):
            await asyncio.wait_for(proxy_task, timeout=2)

    async def test_negotiated_max_inflight_rejects_burst(self):
        request_one = "00112233-4455-6677-8899-aabbccddeeff"
        request_two = "10213243-5465-7687-98a9-bacbdcedfe0f"
        websocket = _FrameWebSocket()
        websocket.feed(hello_frame(max_inflight=1))
        client = TunnelClient(
            upstream=f"127.0.0.1:{self.upstream.server_port}",
        )
        proxy_task = asyncio.create_task(client._proxy_loop(websocket))
        self.assertEqual(
            (await asyncio.wait_for(websocket.sent.get(), timeout=2))["type"],
            "hello",
        )
        websocket.feed(
            {
                "type": "http_req",
                "id": request_one,
                "method": "GET",
                "path": "/block",
                "headers": [],
                "body": "",
            }
        )
        started = await asyncio.to_thread(_RecordingHandler.block_started.wait, 2)
        self.assertTrue(started)
        websocket.feed(
            {
                "type": "http_req",
                "id": request_two,
                "method": "GET",
                "path": "/block",
                "headers": [],
                "body": "",
            }
        )
        rejected = await asyncio.wait_for(websocket.sent.get(), timeout=2)
        self.assertEqual(rejected["type"], "error")
        self.assertEqual(rejected["id"], request_two)
        self.assertIn("max_inflight", rejected["message"])
        _RecordingHandler.block_release.set()
        response = await asyncio.wait_for(websocket.sent.get(), timeout=2)
        self.assertEqual(response["type"], "http_resp")
        self.assertEqual(response["id"], request_one)
        websocket.close_input()
        await asyncio.wait_for(proxy_task, timeout=2)

    async def test_v1_map_headers_remain_backward_compatible(self):
        websocket = _FrameWebSocket()
        websocket.feed(
            {
                "type": "http_req",
                "id": "legacy",
                "method": "GET",
                "path": "/legacy",
                "headers": {"X-Test": "value"},
                "body": "",
            }
        )
        client = TunnelClient(
            upstream=f"127.0.0.1:{self.upstream.server_port}",
        )
        proxy_task = asyncio.create_task(client._proxy_loop(websocket))
        response = await asyncio.wait_for(websocket.sent.get(), timeout=2)
        self.assertEqual(response["type"], "http_resp")
        self.assertIsInstance(response["headers"], dict)
        self.assertEqual(base64.b64decode(response["body"]), b"ok")
        websocket.close_input()
        await asyncio.wait_for(proxy_task, timeout=2)


if __name__ == "__main__":
    unittest.main()
