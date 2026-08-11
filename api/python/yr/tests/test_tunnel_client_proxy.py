#!/usr/bin/env python3
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
import asyncio
import base64
import importlib.util
import pathlib
import ssl
import sys
import types
import unittest
from unittest.mock import patch

from websockets.uri import parse_proxy, parse_uri
import websockets.asyncio.client as ws_client


_TUNNEL_MODULES = (
    "yr",
    "yr.sandbox",
    "yr.sandbox.tunnel_protocol",
    "yr.sandbox.tunnel_client",
)


def _load_tunnel_client_module():
    root = pathlib.Path(__file__).resolve().parents[1] / "sandbox"
    previous_modules = {name: sys.modules.get(name) for name in _TUNNEL_MODULES}
    missing_modules = {name for name in _TUNNEL_MODULES if name not in sys.modules}

    try:
        sys.modules["yr"] = types.ModuleType("yr")
        sys.modules["yr.sandbox"] = types.ModuleType("yr.sandbox")

        for name in ["tunnel_protocol", "tunnel_client"]:
            path = root / f"{name}.py"
            module_name = f"yr.sandbox.{name}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = mod
            spec.loader.exec_module(mod)

        return sys.modules["yr.sandbox.tunnel_client"]
    finally:
        for name in missing_modules:
            sys.modules.pop(name, None)
        for name, module in previous_modules.items():
            if module is not None:
                sys.modules[name] = module


def _proxy_authorization_from_request(request: bytes) -> str:
    for line in request.decode("latin1").split("\r\n"):
        if line.lower().startswith("proxy-authorization:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("Proxy-Authorization header not found")


class TestTunnelClientProxy(unittest.TestCase):
    def test_wss_with_default_ssl_verify_passes_verified_context(self):
        tunnel_client = _load_tunnel_client_module()
        client = tunnel_client.TunnelClient(upstream="http://127.0.0.1:28800")
        setattr(client, "_tunnel_url", "wss://127.0.0.1:28765")

        kwargs = getattr(client, "_build_ws_kwargs")()

        self.assertIsInstance(kwargs["ssl"], ssl.SSLContext)
        self.assertTrue(kwargs["ssl"].check_hostname)
        self.assertEqual(kwargs["ssl"].verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(kwargs["max_size"], tunnel_client.MAX_TUNNEL_FRAME_SIZE)

    def test_wss_with_ssl_verify_disabled_passes_ssl_context(self):
        tunnel_client = _load_tunnel_client_module()
        with patch.dict("os.environ", {"TUNNEL_SSL_VERIFY": "0"}):
            client = tunnel_client.TunnelClient(upstream="http://127.0.0.1:28800")
        setattr(client, "_tunnel_url", "wss://127.0.0.1:28765")

        kwargs = getattr(client, "_build_ws_kwargs")()

        self.assertIsInstance(kwargs["ssl"], ssl.SSLContext)
        self.assertEqual(kwargs["ssl"].verify_mode, ssl.CERT_NONE)

    def test_reconnect_reuses_ssl_contexts_across_http_clients(self):
        tunnel_client = _load_tunnel_client_module()
        client = tunnel_client.TunnelClient(
            upstream="http://127.0.0.1:28800",
            reconnect_base_delay=0.0,
            reconnect_max_delay=0.0,
        )
        setattr(client, "_tunnel_url", "wss://127.0.0.1:28765")

        ssl_contexts = []
        connection_count = 0
        http_client_count = 0
        http_client_close_count = 0
        http_verify_values = []

        class FakeWebSocketContext:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, _exc_type, _exc, _traceback):
                return False

        def fake_connect(_url, **kwargs):
            nonlocal connection_count
            connection_count += 1
            ssl_contexts.append(kwargs["ssl"])
            return FakeWebSocketContext()

        class FakeHttpClient:
            def __init__(self, **kwargs):
                nonlocal http_client_count
                http_client_count += 1
                http_verify_values.append(kwargs["verify"])

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _traceback):
                nonlocal http_client_close_count
                http_client_close_count += 1
                return False

        async def fake_recv_loop(_ws, _http):
            if connection_count == 3:
                getattr(client, "_stop_event").set()

        setattr(client, "_recv_loop", fake_recv_loop)
        original_create_default_context = ssl.create_default_context
        expected_http_context = object()
        with patch.object(
            tunnel_client.ssl,
            "create_default_context",
            wraps=original_create_default_context,
        ) as create_default_context:
            with patch.object(
                tunnel_client.websockets,
                "connect",
                side_effect=fake_connect,
            ), patch.object(
                tunnel_client.httpx,
                "create_ssl_context",
                return_value=expected_http_context,
            ) as create_http_context, patch.object(
                tunnel_client.httpx,
                "AsyncClient",
                FakeHttpClient,
            ):
                asyncio.run(getattr(client, "_connect_loop")())

        self.assertEqual(connection_count, 3)
        self.assertEqual(create_default_context.call_count, 1)
        create_http_context.assert_called_once_with(verify=True, trust_env=False)
        self.assertEqual(http_client_count, 3)
        self.assertEqual(http_client_close_count, 3)
        self.assertTrue(
            all(context is expected_http_context for context in http_verify_values)
        )
        self.assertTrue(all(context is ssl_contexts[0] for context in ssl_contexts))
        self.assertFalse(client.is_connected())

    def test_disconnect_cancels_websocket_proxy_tasks(self):
        tunnel_client = _load_tunnel_client_module()
        client = tunnel_client.TunnelClient(upstream="http://127.0.0.1:28800")
        frame = tunnel_client.WsConnectFrame(id="ws-1", path="/stream", headers={})

        async def run_scenario():
            started = asyncio.Event()
            cancelled = asyncio.Event()
            proxy_task = None

            async def blocked_ws_proxy(_ws, _frame):
                nonlocal proxy_task
                proxy_task = asyncio.current_task()
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    cancelled.set()

            class DisconnectingWebSocket:
                def __aiter__(self):
                    async def messages():
                        yield frame.to_json()
                        await started.wait()

                    return messages()

            setattr(client, "_handle_ws_connect", blocked_ws_proxy)
            await getattr(client, "_recv_frames")(
                DisconnectingWebSocket(), object()
            )
            return proxy_task, cancelled.is_set()

        proxy_task, was_cancelled = asyncio.run(run_scenario())

        self.assertIsNotNone(proxy_task)
        self.assertTrue(proxy_task.done())
        self.assertTrue(proxy_task.cancelled())
        self.assertTrue(was_cancelled)
        self.assertEqual(getattr(client, "_ws_channels"), {})

    def test_proxy_auth_unquotes_url_encoded_credentials(self):
        tunnel_client = _load_tunnel_client_module()
        original_prepare = ws_client.prepare_connect_request
        try:
            getattr(tunnel_client, "_patch_websockets_proxy_auth_unquote")()
            request = ws_client.prepare_connect_request(
                parse_proxy("http://z00826700:huawei%40123@proxy.example:8080"),
                parse_uri("wss://124.70.166.142:443/tunnel"),
            )
        finally:
            ws_client.prepare_connect_request = original_prepare

        auth = _proxy_authorization_from_request(request)
        decoded = base64.b64decode(auth.split(None, 1)[1]).decode()
        self.assertEqual(decoded, "z00826700:huawei@123")

    def test_proxy_enabled_patches_auth_and_sets_proxy_true(self):
        tunnel_client = _load_tunnel_client_module()
        original_prepare = ws_client.prepare_connect_request
        try:
            with patch.dict("os.environ", {"YR_ENABLE_HTTP_PROXY": "true"}):
                client = tunnel_client.TunnelClient(upstream="http://127.0.0.1:28800")
                setattr(client, "_tunnel_url", "ws://127.0.0.1:28765")
                kwargs = getattr(client, "_build_ws_kwargs")()

            self.assertIs(kwargs["proxy"], True)
            self.assertTrue(
                getattr(ws_client.prepare_connect_request, "_yr_proxy_auth_unquote", False)
            )
        finally:
            ws_client.prepare_connect_request = original_prepare

    def test_proxy_disabled_ignores_proxy_environment(self):
        tunnel_client = _load_tunnel_client_module()
        original_prepare = ws_client.prepare_connect_request
        if getattr(original_prepare, "_yr_proxy_auth_unquote", False):
            self.skipTest("websockets proxy auth was already patched globally")
        try:
            with patch.dict(
                "os.environ",
                {
                    "https_proxy": "http://user:pass@proxy.example:8080",
                    "wss_proxy": "http://user:pass@proxy.example:8080",
                },
                clear=True,
            ):
                client = tunnel_client.TunnelClient(upstream="http://127.0.0.1:28800")
                setattr(client, "_tunnel_url", "wss://127.0.0.1:28765")
                kwargs = getattr(client, "_build_ws_kwargs")()

            self.assertIsNone(kwargs["proxy"])
            self.assertFalse(
                getattr(ws_client.prepare_connect_request, "_yr_proxy_auth_unquote", False)
            )
        finally:
            ws_client.prepare_connect_request = original_prepare


if __name__ == "__main__":
    unittest.main()
