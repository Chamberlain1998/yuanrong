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
"""HTTP representation and compatibility tests for the Python tunnel."""

import asyncio
import gzip
import http.server
import json
import socket
import threading
import unittest
from typing import ClassVar

import httpx

from yr.sandbox.tunnel_client import TunnelClient
from yr.sandbox.tunnel_protocol import HttpReqFrame, HttpRespFrame, parse_frame
from yr.sandbox.tunnel_server import TunnelServer


def _unused_tcp_ports(count: int) -> tuple[int, ...]:
    sockets = []
    try:
        for _ in range(count):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return tuple(sock.getsockname()[1] for sock in sockets)
    finally:
        for sock in sockets:
            sock.close()


def _header_values(headers: list[tuple[str, str]], name: str) -> list[str]:
    return [value for key, value in headers if key.lower() == name.lower()]


def _raw_request(port: int, request: bytes) -> tuple[int, list[tuple[str, str]], bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as stream:
        stream.sendall(request)
        response = bytearray()
        while True:
            chunk = stream.recv(65536)
            if not chunk:
                break
            response.extend(chunk)
    head, separator, body = bytes(response).partition(b"\r\n\r\n")
    if not separator:
        raise AssertionError(f"incomplete HTTP response: {response!r}")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = []
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers.append((key, value.strip()))
    return status, headers, body


class _FidelityUpstream(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    observations: ClassVar[list] = []

    def log_message(self, _format, *_args):
        return

    def _body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or "0")
        return self.rfile.read(length)

    def _record(self, body: bytes) -> None:
        type(self).observations.append((self.path, self.headers, body))

    def _send(self, status: int, body: bytes, headers=()) -> None:
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        if self.command != "HEAD" and status not in (204, 304):
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD" and status not in (204, 304):
            self.wfile.write(body)
        self.close_connection = True

    def do_POST(self):
        body = self._body()
        self._record(body)
        self._send(200, b"ok")

    def do_GET(self):
        self._record(b"")
        if self.path == "/gzip-response":
            body = gzip.compress(b"gzip-response-body")
            self._send(
                200,
                body,
                (
                    ("Content-Encoding", "gzip"),
                    ("Set-Cookie", "session=one; Path=/"),
                    ("Set-Cookie", "theme=dark; Path=/"),
                    ("Connection", "close, X-Remove"),
                    ("X-Remove", "first-hop-only"),
                ),
            )
            return
        if self.path == "/set-cookie":
            self._send(200, b"", (("Set-Cookie", "session=leaked; Path=/"),))
            return
        self._send(200, b"ok")

    def do_HEAD(self):
        self._record(b"")
        self._send(200, b"", (("Content-Length", "123"),))


class TestPythonTunnelHttpFidelity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws_port, cls.http_port = _unused_tcp_ports(2)
        cls.server_loop = asyncio.new_event_loop()
        cls.tunnel_server = TunnelServer(
            ws_port=cls.ws_port,
            http_port=cls.http_port,
        )
        ready = threading.Event()
        errors = []

        def run_tunnel_server():
            asyncio.set_event_loop(cls.server_loop)
            try:
                cls.server_loop.run_until_complete(cls.tunnel_server.start())
                ready.set()
                cls.server_loop.run_forever()
            except Exception as error:  # noqa: BLE001 - forward thread failure
                errors.append(error)
                ready.set()

        cls.tunnel_thread = threading.Thread(
            target=run_tunnel_server,
            daemon=True,
        )
        cls.tunnel_thread.start()
        if not ready.wait(timeout=5):
            raise RuntimeError("TunnelServer did not start")
        if errors:
            raise errors[0]

        _FidelityUpstream.observations = []
        cls.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _FidelityUpstream,
        )
        cls.upstream_thread = threading.Thread(
            target=cls.upstream.serve_forever,
            daemon=True,
        )
        cls.upstream_thread.start()
        cls.client = TunnelClient(
            upstream=f"http://127.0.0.1:{cls.upstream.server_port}",
        )
        if not cls.client.start(
            f"ws://127.0.0.1:{cls.ws_port}",
            timeout=5,
        ):
            raise RuntimeError("TunnelClient did not connect")

    @classmethod
    def tearDownClass(cls):
        cls.client.stop()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.upstream_thread.join(timeout=5)
        stop = asyncio.run_coroutine_threadsafe(
            cls.tunnel_server.stop(),
            cls.server_loop,
        )
        stop.result(timeout=5)
        cls.server_loop.call_soon_threadsafe(cls.server_loop.stop)
        cls.tunnel_thread.join(timeout=5)
        cls.server_loop.close()

    def test_gzip_request_and_duplicate_headers_remain_byte_exact(self):
        payload = gzip.compress(b"gzip-request-body")
        request = (
            b"POST /gzip-request HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{self.http_port}\r\n".encode()
            + b"Content-Encoding: gzip\r\n"
            + b"Content-Type: application/octet-stream\r\n"
            + b"X-Dup: first\r\n"
            + b"X-Dup: second\r\n"
            + b"Connection: close, X-First-Hop\r\n"
            + b"X-First-Hop: remove-me\r\n"
            + f"Content-Length: {len(payload)}\r\n\r\n".encode()
            + payload
        )

        status, _, _ = _raw_request(self.http_port, request)

        self.assertEqual(status, 200)
        _, headers, body = _FidelityUpstream.observations[-1]
        self.assertEqual(body, payload)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertEqual(headers.get_all("X-Dup"), ["first", "second"])
        self.assertIsNone(headers.get("X-First-Hop"))
        self.assertEqual(headers.get("Content-Length"), str(len(payload)))

    def test_gzip_response_and_repeated_set_cookie_remain_byte_exact(self):
        status, headers, body = _raw_request(
            self.http_port,
            (
                b"GET /gzip-response HTTP/1.1\r\n"
                + f"Host: 127.0.0.1:{self.http_port}\r\n".encode()
                + b"Connection: close\r\n\r\n"
            ),
        )

        self.assertEqual(status, 200)
        self.assertEqual(_header_values(headers, "Content-Encoding"), ["gzip"])
        self.assertEqual(gzip.decompress(body), b"gzip-response-body")
        self.assertEqual(
            _header_values(headers, "Set-Cookie"),
            ["session=one; Path=/", "theme=dark; Path=/"],
        )
        self.assertEqual(_header_values(headers, "X-Remove"), [])
        self.assertEqual(_header_values(headers, "Content-Length"), [str(len(body))])

    def test_shared_pool_does_not_replay_response_cookie(self):
        for path in ("/set-cookie", "/inspect-cookie"):
            status, _, _ = _raw_request(
                self.http_port,
                (
                    f"GET {path} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.http_port}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode(),
            )
            self.assertEqual(status, 200)

        path, headers, _ = _FidelityUpstream.observations[-1]
        self.assertEqual(path, "/inspect-cookie")
        self.assertIsNone(headers.get("Cookie"))

    def test_explicit_request_cookie_is_still_forwarded(self):
        status, _, _ = _raw_request(
            self.http_port,
            (
                b"GET /explicit-cookie HTTP/1.1\r\n"
                + f"Host: 127.0.0.1:{self.http_port}\r\n".encode()
                + b"Cookie: caller=session\r\n"
                + b"Connection: close\r\n\r\n"
            ),
        )

        self.assertEqual(status, 200)
        path, headers, _ = _FidelityUpstream.observations[-1]
        self.assertEqual(path, "/explicit-cookie")
        self.assertEqual(headers.get("Cookie"), "caller=session")

    def test_head_preserves_representation_content_length(self):
        status, headers, body = _raw_request(
            self.http_port,
            (
                b"HEAD /head HTTP/1.1\r\n"
                + f"Host: 127.0.0.1:{self.http_port}\r\n".encode()
                + b"Connection: close\r\n\r\n"
            ),
        )

        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(_header_values(headers, "Content-Length"), ["123"])


class TestCompatibleHeaderProtocol(unittest.TestCase):
    def test_new_frame_sends_pair_list_and_legacy_map(self):
        frame = HttpRespFrame(
            id="response-1",
            status=200,
            headers={},
            header_items=[
                ("Set-Cookie", "session=one"),
                ("Set-Cookie", "theme=dark"),
            ],
            body=b"ok",
        )

        raw = json.loads(frame.to_json())
        self.assertEqual(
            raw["header_items"],
            [
                ["Set-Cookie", "session=one"],
                ["Set-Cookie", "theme=dark"],
            ],
        )
        self.assertEqual(raw["headers"], {"Set-Cookie": "theme=dark"})
        self.assertEqual(
            parse_frame(json.dumps(raw)).header_items,
            [
                ("Set-Cookie", "session=one"),
                ("Set-Cookie", "theme=dark"),
            ],
        )

    def test_new_parser_accepts_legacy_map_only_frame(self):
        parsed = parse_frame(
            json.dumps(
                {
                    "type": "http_req",
                    "id": "legacy-request",
                    "method": "GET",
                    "path": "/legacy",
                    "headers": {"X-Legacy": "value"},
                    "body": "",
                }
            )
        )

        self.assertEqual(parsed.headers, {"X-Legacy": "value"})
        self.assertEqual(parsed.header_items, [("X-Legacy", "value")])

    def test_absolute_request_target_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_frame(
                json.dumps(
                    {
                        "type": "http_req",
                        "id": "absolute-target",
                        "method": "GET",
                        "path": "http://unexpected.example/path",
                        "headers": {},
                        "body": "",
                    }
                )
            )


class _QueueWebSocket:
    def __init__(self):
        self.incoming = asyncio.Queue()
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, value):
        self.sent.append(value)


class _FakeResponse:
    status_code = 200
    headers = httpx.Headers({"Content-Length": "2"})
    content = b"ok"

    async def aiter_raw(self):
        yield b"ok"


class _FakeStream:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        self.client.active += 1
        self.client.max_active = max(self.client.max_active, self.client.active)
        await asyncio.sleep(0.05)
        return _FakeResponse()

    async def __aexit__(self, *_args):
        self.client.active -= 1


class _FakeHttpClient:
    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def request(self, **_kwargs):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        return _FakeResponse()

    def stream(self, *_args, **_kwargs):
        return _FakeStream(self)


class TestTunnelClientConcurrency(unittest.IsolatedAsyncioTestCase):
    async def test_http_concurrency_is_bounded_and_tasks_are_drained(self):
        websocket = _QueueWebSocket()
        http = _FakeHttpClient()
        client = TunnelClient(upstream="http://127.0.0.1:1")
        client._max_http_concurrency = 1
        for index in range(3):
            websocket.incoming.put_nowait(
                HttpReqFrame(
                    id=f"request-{index}",
                    method="GET",
                    path="/",
                    headers={},
                    body=b"",
                ).to_json()
            )

        receive_task = asyncio.create_task(client._recv_frames(websocket, http))
        async def wait_for_responses():
            while len(websocket.sent) < 3:
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait_for_responses(), timeout=2)
        websocket.incoming.put_nowait(None)
        await receive_task

        self.assertEqual(http.max_active, 1)


if __name__ == "__main__":
    unittest.main()
