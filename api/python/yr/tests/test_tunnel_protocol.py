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

# api/python/yr/tests/test_tunnel_protocol.py
import base64
import json
import unittest

from yr.sandbox.tunnel_protocol import (
    MAX_TUNNEL_FRAME_SIZE,
    BinaryEnvelope,
    BinaryKind,
    ErrorFrame,
    HttpReqFrame,
    HttpRespFrame,
    PingFrame,
    PongFrame,
    ProtocolError,
    WsCloseFrame,
    WsConnectedFrame,
    WsConnectFrame,
    WsMessageFrame,
    hello_frame,
    make_id,
    parse_frame,
)


class TestHttpFrames(unittest.TestCase):
    def test_http_req_roundtrip(self):
        frame = HttpReqFrame(
            id="id-1",
            method="POST",
            path="/api/data",
            headers={"Content-Type": "application/json"},
            body=b'{"key": "val"}',
        )
        raw = frame.to_json()
        parsed = parse_frame(raw)
        self.assertIsInstance(parsed, HttpReqFrame)
        self.assertEqual(parsed.id, "id-1")
        self.assertEqual(parsed.method, "POST")
        self.assertEqual(parsed.path, "/api/data")
        self.assertEqual(parsed.headers["Content-Type"], "application/json")
        self.assertEqual(parsed.body, b'{"key": "val"}')

    def test_http_resp_roundtrip(self):
        frame = HttpRespFrame(
            id="id-2", status=200, headers={"X-Foo": "bar"}, body=b"hello"
        )
        parsed = parse_frame(frame.to_json())
        self.assertIsInstance(parsed, HttpRespFrame)
        self.assertEqual(parsed.status, 200)
        self.assertEqual(parsed.body, b"hello")

    def test_http_resp_body_larger_than_one_mib_roundtrip(self):
        body = b"x" * (2 << 20)
        frame = HttpRespFrame(id="id-large", status=200, headers={}, body=body)
        raw = frame.to_json()
        self.assertGreater(len(raw), 1 << 20)
        self.assertLess(len(raw), MAX_TUNNEL_FRAME_SIZE)
        parsed = parse_frame(raw)
        self.assertIsInstance(parsed, HttpRespFrame)
        self.assertEqual(parsed.body, body)

    def test_http_req_empty_body(self):
        frame = HttpReqFrame(id="id-3", method="GET", path="/", headers={}, body=b"")
        parsed = parse_frame(frame.to_json())
        self.assertEqual(parsed.body, b"")

    def test_body_is_base64_in_json(self):
        frame = HttpReqFrame(
            id="x", method="GET", path="/", headers={}, body=b"\x00\x01\x02"
        )
        data = json.loads(frame.to_json())
        self.assertEqual(base64.b64decode(data["body"]), b"\x00\x01\x02")

    def test_http_req_null_body_parses_as_empty_bytes(self):
        parsed = parse_frame(
            json.dumps(
                {
                    "type": "http_req",
                    "id": "id-4",
                    "method": "GET",
                    "path": "/",
                    "headers": {},
                    "body": None,
                }
            )
        )
        self.assertEqual(parsed.body, b"")

    def test_http_resp_null_body_parses_as_empty_bytes(self):
        parsed = parse_frame(
            json.dumps(
                {
                    "type": "http_resp",
                    "id": "id-5",
                    "status": 204,
                    "headers": {},
                    "body": None,
                }
            )
        )
        self.assertEqual(parsed.body, b"")

    def test_http_req_invalid_method_raises(self):
        with self.assertRaises(ValueError):
            parse_frame(
                json.dumps(
                    {
                        "type": "http_req",
                        "id": "id-6",
                        "method": "GET /bad",
                        "path": "/",
                        "headers": {},
                        "body": "",
                    }
                )
            )

    def test_http_resp_invalid_status_raises(self):
        with self.assertRaises(ValueError):
            parse_frame(
                json.dumps(
                    {
                        "type": "http_resp",
                        "id": "id-7",
                        "status": 99,
                        "headers": {},
                        "body": "",
                    }
                )
            )


class TestWsFrames(unittest.TestCase):
    def test_ws_connect_roundtrip(self):
        frame = WsConnectFrame(id="c1", path="/ws", headers={"Origin": "test"})
        parsed = parse_frame(frame.to_json())
        self.assertIsInstance(parsed, WsConnectFrame)
        self.assertEqual(parsed.path, "/ws")

    def test_ws_connected_roundtrip(self):
        frame = WsConnectedFrame(id="c1")
        parsed = parse_frame(frame.to_json())
        self.assertIsInstance(parsed, WsConnectedFrame)
        self.assertEqual(parsed.id, "c1")

    def test_ws_message_roundtrip(self):
        frame = WsMessageFrame(id="c1", data="hello world", binary=False)
        parsed = parse_frame(frame.to_json())
        self.assertIsInstance(parsed, WsMessageFrame)
        self.assertEqual(parsed.data, "hello world")
        self.assertFalse(parsed.binary)

    def test_ws_close_roundtrip(self):
        frame = WsCloseFrame(id="c1", code=1001, reason="bye")
        parsed = parse_frame(frame.to_json())
        self.assertIsInstance(parsed, WsCloseFrame)
        self.assertEqual(parsed.code, 1001)
        self.assertEqual(parsed.reason, "bye")

    def test_ws_close_invalid_code_raises(self):
        with self.assertRaises(ValueError):
            parse_frame(
                json.dumps(
                    {
                        "type": "ws_close",
                        "id": "c2",
                        "code": 999,
                        "reason": "bad",
                    }
                )
            )

    def test_error_roundtrip(self):
        frame = ErrorFrame(id="e1", message="connection refused")
        parsed = parse_frame(frame.to_json())
        self.assertIsInstance(parsed, ErrorFrame)
        self.assertEqual(parsed.message, "connection refused")

    def test_unknown_type_raises(self):
        with self.assertRaises(ValueError):
            parse_frame(json.dumps({"type": "bogus", "id": "x"}))


class TestPingPongFrames(unittest.TestCase):
    def test_ping_frame_round_trip(self):
        ping = PingFrame(id="ping-1", timestamp=1234567890.123)
        raw = ping.to_json()
        parsed = parse_frame(raw)
        self.assertIsInstance(parsed, PingFrame)
        self.assertEqual(parsed.id, "ping-1")
        self.assertAlmostEqual(parsed.timestamp, 1234567890.123)

    def test_pong_frame_round_trip(self):
        pong = PongFrame(id="ping-1", timestamp=1234567890.123)
        raw = pong.to_json()
        parsed = parse_frame(raw)
        self.assertIsInstance(parsed, PongFrame)
        self.assertEqual(parsed.id, "ping-1")
        self.assertAlmostEqual(parsed.timestamp, 1234567890.123)


class TestMakeId(unittest.TestCase):
    def test_make_id_returns_unique_strings(self):
        ids = {make_id() for _ in range(100)}
        self.assertEqual(len(ids), 100)

    def test_make_id_is_string(self):
        self.assertIsInstance(make_id(), str)


class TestStreamingProtocol(unittest.TestCase):
    def test_binary_envelope_roundtrip(self):
        request_id = "00112233-4455-6677-8899-aabbccddeeff"
        encoded = BinaryEnvelope(
            request_id=request_id,
            kind=BinaryKind.HTTP_REQUEST_DATA,
            payload=b"chunk",
            end_of_body=True,
        ).encode()

        decoded = BinaryEnvelope.decode(encoded)
        self.assertEqual(decoded.request_id, request_id)
        self.assertEqual(decoded.kind, BinaryKind.HTTP_REQUEST_DATA)
        self.assertEqual(decoded.payload, b"chunk")
        self.assertTrue(decoded.end_of_body)

    def test_binary_envelope_rejects_malformed_and_oversized_data(self):
        request_id = "00112233-4455-6677-8899-aabbccddeeff"
        with self.assertRaises(ProtocolError):
            BinaryEnvelope.decode(b"bad")
        with self.assertRaises(ProtocolError):
            BinaryEnvelope(
                request_id=request_id,
                kind=BinaryKind.HTTP_RESPONSE_DATA,
                payload=b"too large",
            ).encode(max_payload=1)

    def test_default_hello_advertises_independent_http_and_ws_limits(self):
        hello = hello_frame()
        self.assertEqual(hello["protocol_version"], 2)
        self.assertEqual(hello["max_stream_chunk"], 64 * 1024)
        self.assertEqual(hello["stream_window_frames"], 16)
        self.assertEqual(hello["max_body_size"], 512 * 1024 * 1024)
        self.assertEqual(hello["max_ws_message_size"], 8 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
