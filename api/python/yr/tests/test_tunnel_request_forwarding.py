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
"""Regression tests for rebuilding HTTP requests across the reverse tunnel."""

import asyncio
import socket
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

from yr.sandbox.tunnel_client import (
    TunnelClient,
    _headers_for_rebuilt_request,
)
from yr.sandbox.tunnel_server import TunnelServer

_PAYLOAD = b'{"message":"akernel-sdk-body-check"}'


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


def _wait_for_tunnel_forwarding(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        request = (
            b"GET /tunnel-ready HTTP/1.1\r\n"
            + f"Host: 127.0.0.1:{port}\r\n".encode()
            + b"Connection: close\r\n\r\n"
        )
        with socket.create_connection(("127.0.0.1", port), timeout=5) as conn:
            conn.sendall(request)
            with conn.makefile("rb") as response_file:
                status_line = response_file.readline()
        if b" 200 " in status_line:
            return
        if b" 503 " not in status_line:
            raise RuntimeError(
                f"unexpected tunnel readiness response: {status_line!r}"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError("TunnelClient forwarding did not become ready")
        time.sleep(0.01)


class _StrictContentLengthHandler(BaseHTTPRequestHandler):
    """Read only Content-Length bytes, as common upstream proxies do."""

    protocol_version = "HTTP/1.1"
    received: ClassVar[dict] = {}

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(content_length)
        type(self).received = {
            "body": body,
            "content_length": self.headers.get("Content-Length"),
            "transfer_encoding": self.headers.get("Transfer-Encoding"),
            "connection": self.headers.get("Connection"),
            "host": self.headers.get("Host"),
            "internal_only": self.headers.get("X-Internal-Only"),
            "authorization": self.headers.get("Authorization"),
            "content_type": self.headers.get("Content-Type"),
            "content_encoding": self.headers.get("Content-Encoding"),
        }
        status = 200 if body == _PAYLOAD else 422
        response = b'{"ok":true}' if status == 200 else b'{"ok":false}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response)
        self.close_connection = True


class TestRebuiltRequestHeaders(unittest.TestCase):
    def test_removes_hop_by_hop_and_connection_named_headers(self):
        headers = {
            "hOsT": "first-hop.example",
            "cOnNeCtIoN": " close, X-Internal-Only ",
            "X-Internal-Only": "do-not-forward",
            "Keep-Alive": "timeout=5",
            "Proxy-Authenticate": "Basic",
            "Proxy-Authorization": "Basic secret",
            "Proxy-Connection": "keep-alive",
            "TE": "trailers",
            "Trailer": "Digest",
            "Transfer-Encoding": "chunked",
            "Upgrade": "websocket",
            "content-length": "999",
            "Authorization": "Bearer tunnel-test",
            "Content-Type": "application/json",
            "Content-Encoding": "identity",
        }

        result = _headers_for_rebuilt_request(headers, _PAYLOAD)
        result_by_lower_name = {name.lower(): value for name, value in result.items()}

        for name in (
            "host",
            "connection",
            "x-internal-only",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "proxy-connection",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
        ):
            self.assertNotIn(name, result_by_lower_name)
        self.assertEqual(
            result_by_lower_name["content-length"],
            str(len(_PAYLOAD)),
        )
        self.assertEqual(
            result_by_lower_name["authorization"],
            "Bearer tunnel-test",
        )
        self.assertEqual(
            result_by_lower_name["content-type"],
            "application/json",
        )
        self.assertEqual(
            result_by_lower_name["content-encoding"],
            "identity",
        )

    def test_sets_zero_content_length_for_empty_body(self):
        result = _headers_for_rebuilt_request(
            {"Content-Length": "123", "Transfer-Encoding": "chunked"},
            b"",
        )

        self.assertEqual(result, {"Content-Length": "0"})


class TestChunkedRequestForwarding(unittest.TestCase):
    def test_chunked_post_is_rebuilt_for_strict_upstream(self):
        ws_port, http_port = _unused_tcp_ports(2)
        server_loop = asyncio.new_event_loop()
        tunnel_server = TunnelServer(ws_port=ws_port, http_port=http_port)
        tunnel_ready = threading.Event()
        tunnel_error = []

        def run_tunnel_server():
            asyncio.set_event_loop(server_loop)
            try:
                server_loop.run_until_complete(tunnel_server.start())
                tunnel_ready.set()
                server_loop.run_forever()
            except Exception as error:  # noqa: BLE001 - forward thread errors
                tunnel_error.append(error)
                tunnel_ready.set()

        tunnel_thread = threading.Thread(
            target=run_tunnel_server,
            daemon=True,
        )
        tunnel_thread.start()
        self.assertTrue(tunnel_ready.wait(timeout=5))
        if tunnel_error:
            raise tunnel_error[0]

        _StrictContentLengthHandler.received = {}
        upstream = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            _StrictContentLengthHandler,
        )
        upstream_thread = threading.Thread(
            target=upstream.serve_forever,
            daemon=True,
        )
        upstream_thread.start()

        client = TunnelClient(
            upstream=f"http://127.0.0.1:{upstream.server_port}",
        )
        try:
            self.assertTrue(
                client.start(
                    f"ws://127.0.0.1:{ws_port}",
                    timeout=5,
                )
            )
            _wait_for_tunnel_forwarding(http_port)
            request = (
                b"POST /aux/v1/messages HTTP/1.1\r\n"
                + f"Host: 127.0.0.1:{http_port}\r\n".encode()
                + b"Content-Type: application/json\r\n"
                + b"Content-Encoding: identity\r\n"
                + b"Authorization: Bearer tunnel-test\r\n"
                + b"Connection: close, X-Internal-Only\r\n"
                + b"X-Internal-Only: do-not-forward\r\n"
                + b"Transfer-Encoding: chunked\r\n"
                + b"\r\n"
                + f"{len(_PAYLOAD):X}\r\n".encode()
                + _PAYLOAD
                + b"\r\n0\r\n\r\n"
            )
            with socket.create_connection(
                ("127.0.0.1", http_port),
                timeout=5,
            ) as conn:
                conn.sendall(request)
                with conn.makefile("rb") as response_file:
                    response = response_file.read()

            self.assertIn(b" 200 ", response.split(b"\r\n", 1)[0])
            received = _StrictContentLengthHandler.received
            self.assertEqual(received["body"], _PAYLOAD)
            self.assertEqual(
                received["content_length"],
                str(len(_PAYLOAD)),
            )
            self.assertIsNone(received["transfer_encoding"])
            self.assertNotEqual(
                received["connection"],
                "close, X-Internal-Only",
            )
            self.assertEqual(
                received["host"],
                f"127.0.0.1:{upstream.server_port}",
            )
            self.assertIsNone(received["internal_only"])
            self.assertEqual(
                received["authorization"],
                "Bearer tunnel-test",
            )
            self.assertEqual(
                received["content_type"],
                "application/json",
            )
            self.assertEqual(
                received["content_encoding"],
                "identity",
            )
        finally:
            client.stop()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join(timeout=5)
            stop_future = asyncio.run_coroutine_threadsafe(
                tunnel_server.stop(),
                server_loop,
            )
            stop_future.result(timeout=5)
            server_loop.call_soon_threadsafe(server_loop.stop)
            tunnel_thread.join(timeout=5)
            server_loop.close()


if __name__ == "__main__":
    unittest.main()
