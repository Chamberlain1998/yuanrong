# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# Licensed under the Apache License, Version 2.0.
"""Independent V2 protocol and lifecycle tests for Python TunnelServer."""

import asyncio
import contextlib
import json
import socket
import unittest

import aiohttp
import websockets
from yr.sandbox.tunnel_protocol import (
    BinaryEnvelope,
    BinaryKind,
    HttpReqFrame,
    HttpRespFrame,
    hello_frame,
    parse_frame,
)
from yr.sandbox.tunnel_server import TunnelServer


def _unused_ports(count):
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


async def _recv_json(websocket):
    raw = await asyncio.wait_for(websocket.recv(), timeout=3)
    if not isinstance(raw, str):
        raise AssertionError("expected a JSON tunnel frame")
    return json.loads(raw)


class TunnelServerV2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ws_port, self.http_port = _unused_ports(2)
        self.server = TunnelServer(self.ws_port, self.http_port)
        await self.server.start()

    async def asyncTearDown(self):
        await self.server.stop()

    async def _connect_v2_peer(self):
        websocket = await websockets.connect(
            f"ws://127.0.0.1:{self.ws_port}",
            max_size=8 * 1024 * 1024,
        )
        await websocket.send(json.dumps(hello_frame()))
        peer_hello = await _recv_json(websocket)
        self.assertEqual(peer_hello["type"], "hello")
        self.assertEqual(peer_hello["max_body_size"], 512 * 1024 * 1024)
        self.assertEqual(peer_hello["max_ws_message_size"], 8 * 1024 * 1024)
        return websocket

    async def test_streams_multi_megabyte_request_with_credit_backpressure(self):
        payload = b"upload" * 400_000
        websocket = await self._connect_v2_peer()
        try:
            async with aiohttp.ClientSession() as session:
                request_task = asyncio.create_task(
                    session.post(
                        f"http://127.0.0.1:{self.http_port}/upload",
                        data=payload,
                    )
                )
                begin = await _recv_json(websocket)
                self.assertEqual(begin["type"], "http_req_begin")
                self.assertEqual(begin["content_length"], len(payload))
                request_id = begin["id"]
                await websocket.send(
                    json.dumps(
                        {
                            "type": "window",
                            "id": request_id,
                            "credits": 16,
                        }
                    )
                )

                received = bytearray()
                while True:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=3)
                    if isinstance(raw, str):
                        frame = json.loads(raw)
                        self.assertEqual(
                            frame,
                            {
                                "type": "http_req_end",
                                "id": request_id,
                            },
                        )
                        break
                    envelope = BinaryEnvelope.decode(raw)
                    self.assertEqual(envelope.kind, BinaryKind.HTTP_REQUEST_DATA)
                    received.extend(envelope.payload)
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "window",
                                "id": request_id,
                                "credits": 1,
                            }
                        )
                    )
                self.assertEqual(received, payload)
                await websocket.send(
                    json.dumps(
                        {
                            "type": "http_resp",
                            "id": request_id,
                            "status": 200,
                            "headers": [["Content-Type", "text/plain"]],
                            "body": "b2s=",
                        }
                    )
                )
                response = await asyncio.wait_for(request_task, timeout=3)
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.read(), b"ok")
        finally:
            await websocket.close()

    async def test_streams_multi_megabyte_response_with_credit_backpressure(self):
        payload = b"response" * 300_000
        websocket = await self._connect_v2_peer()
        try:
            async with aiohttp.ClientSession() as session:
                request_task = asyncio.create_task(
                    session.get(f"http://127.0.0.1:{self.http_port}/download")
                )
                begin = await _recv_json(websocket)
                self.assertIn(begin["type"], ("http_req", "http_req_begin"))
                request_id = begin["id"]
                if begin["type"] == "http_req_begin":
                    await websocket.send(
                        json.dumps(
                            {
                                "type": "window",
                                "id": request_id,
                                "credits": 16,
                            }
                        )
                    )
                    self.assertEqual(
                        await _recv_json(websocket),
                        {"type": "http_req_end", "id": request_id},
                    )
                await websocket.send(
                    json.dumps(
                        {
                            "type": "http_resp_begin",
                            "id": request_id,
                            "status": 200,
                            "headers": [["Content-Length", str(len(payload))]],
                            "content_length": len(payload),
                        }
                    )
                )
                initial_window = await _recv_json(websocket)
                self.assertEqual(initial_window["type"], "window")
                credits = initial_window["credits"]
                for offset in range(0, len(payload), 64 * 1024):
                    if credits == 0:
                        credits += (await _recv_json(websocket))["credits"]
                    chunk = payload[offset : offset + 64 * 1024]
                    await websocket.send(
                        BinaryEnvelope(
                            request_id=request_id,
                            kind=BinaryKind.HTTP_RESPONSE_DATA,
                            payload=chunk,
                        ).encode()
                    )
                    credits -= 1
                await websocket.send(
                    json.dumps(
                        {
                            "type": "http_resp_end",
                            "id": request_id,
                        }
                    )
                )
                response = await asyncio.wait_for(request_task, timeout=3)
                self.assertEqual(await response.read(), payload)
        finally:
            await websocket.close()

    async def test_downstream_disconnect_propagates_stream_error_and_cleans_state(self):
        websocket = await self._connect_v2_peer()

        async def body():
            while True:
                yield b"x" * (64 * 1024)
                await asyncio.sleep(0.01)

        try:
            session = aiohttp.ClientSession()
            request_task = asyncio.create_task(
                session.post(
                    f"http://127.0.0.1:{self.http_port}/cancel",
                    data=body(),
                )
            )
            begin = await _recv_json(websocket)
            request_id = begin["id"]
            await websocket.send(
                json.dumps(
                    {
                        "type": "window",
                        "id": request_id,
                        "credits": 1,
                    }
                )
            )
            raw = await asyncio.wait_for(websocket.recv(), timeout=3)
            self.assertIsInstance(raw, bytes)
            request_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await request_task
            error = await _recv_json(websocket)
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["id"], request_id)
            for _ in range(40):
                if request_id not in self.server._pending_http:
                    break
                await asyncio.sleep(0.05)
            self.assertNotIn(request_id, self.server._pending_http)
            await session.close()
        finally:
            await websocket.close()

    async def test_malformed_binary_frame_closes_only_current_generation(self):
        websocket = await self._connect_v2_peer()
        await websocket.send(b"bad")
        await asyncio.wait_for(websocket.wait_closed(), timeout=3)
        self.assertEqual(websocket.close_code, 1002)
        for _ in range(20):
            if self.server._active is None:
                break
            await asyncio.sleep(0.05)
        self.assertIsNone(self.server._active)
        self.assertEqual(self.server._pending_http, {})

    async def test_reconnect_routes_new_requests_only_to_latest_generation(self):
        previous = await self._connect_v2_peer()
        current = await self._connect_v2_peer()
        try:
            await asyncio.wait_for(previous.wait_closed(), timeout=3)
            self.assertEqual(previous.close_code, 1012)
            async with aiohttp.ClientSession() as session:
                request_task = asyncio.create_task(
                    session.get(f"http://127.0.0.1:{self.http_port}/current")
                )
                request = await _recv_json(current)
                self.assertEqual(request["type"], "http_req")
                self.assertEqual(request["path"], "/current")
                await current.send(
                    json.dumps(
                        {
                            "type": "http_resp",
                            "id": request["id"],
                            "status": 200,
                            "headers": [],
                            "body": "b2s=",
                        }
                    )
                )
                response = await asyncio.wait_for(request_task, timeout=3)
                self.assertEqual(await response.read(), b"ok")
        finally:
            await current.close()

    async def test_v1_chunked_body_larger_than_prefetch_is_not_truncated(self):
        payload = b"legacy-chunk" * 12_000

        async def body():
            for offset in range(0, len(payload), 4096):
                yield payload[offset : offset + 4096]

        websocket = await websockets.connect(f"ws://127.0.0.1:{self.ws_port}")
        try:
            async with aiohttp.ClientSession() as session:
                request_task = asyncio.create_task(
                    session.post(
                        f"http://127.0.0.1:{self.http_port}/legacy",
                        data=body(),
                    )
                )
                request = parse_frame(
                    await asyncio.wait_for(websocket.recv(), timeout=3)
                )
                self.assertIsInstance(request, HttpReqFrame)
                self.assertEqual(request.body, payload)
                await websocket.send(
                    HttpRespFrame(
                        id=request.id,
                        status=200,
                        headers={},
                        body=b"ok",
                    ).to_json()
                )
                response = await asyncio.wait_for(request_task, timeout=3)
                self.assertEqual(response.status, 200)
                self.assertEqual(await response.read(), b"ok")
        finally:
            await websocket.close()


if __name__ == "__main__":
    unittest.main()
