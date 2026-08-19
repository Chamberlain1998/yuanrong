#!/usr/bin/env python3
import ast
import importlib.util
import io
from pathlib import Path
import sys
import types
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
import unittest


class TestCliScripts(unittest.TestCase):
    def load_cli_scripts_with_stubbed_deps(self):
        scripts_path = Path(__file__).resolve().parents[1] / "cli" / "scripts.py"
        spec = importlib.util.spec_from_file_location("yr_cli_scripts_for_test", scripts_path)
        scripts = importlib.util.module_from_spec(spec)

        fake_click = types.ModuleType("click")
        fake_click.option = lambda *args, **kwargs: lambda func: func
        fake_click.argument = lambda *args, **kwargs: lambda func: func
        fake_click.version_option = lambda *args, **kwargs: lambda func: func
        fake_click.pass_context = lambda func: func
        fake_click.Choice = lambda *args, **kwargs: str
        fake_click.ClickException = RuntimeError
        fake_click.echo = print

        def group_decorator(*args, **kwargs):
            def decorate(func):
                func.command = lambda *a, **kw: lambda command_func: command_func
                func.group = group_decorator
                return func

            return decorate

        fake_click.group = group_decorator

        fake_requests = types.ModuleType("requests")
        fake_requests.Session = object
        fake_requests_exceptions = types.ModuleType("requests.exceptions")
        fake_requests_exceptions.RequestException = Exception
        fake_requests.exceptions = fake_requests_exceptions

        fake_yr = types.ModuleType("yr")
        fake_yr_cli = types.ModuleType("yr.cli")
        fake_yr_cli_exec = types.ModuleType("yr.cli.exec")
        fake_yr_cli_exec.CopyRequest = (
            lambda instance, local_path, remote_path:
            types.SimpleNamespace(instance=instance, local_path=local_path, remote_path=remote_path)
        )
        fake_yr_cli_exec.ExecConnection = types.SimpleNamespace
        fake_yr_cli_exec.ExecInvocation = types.SimpleNamespace
        fake_yr_cli_exec.choose_cp_mode = lambda *args, **kwargs: False
        fake_yr_cli_exec.copy_from_remote = lambda *args, **kwargs: None
        fake_yr_cli_exec.copy_from_remote_streaming = lambda *args, **kwargs: None
        fake_yr_cli_exec.copy_to_remote = lambda *args, **kwargs: None
        fake_yr_cli_exec.copy_to_remote_streaming = lambda *args, **kwargs: None
        fake_yr_cli_exec.run_client = lambda *args, **kwargs: None

        with mock.patch.dict(
            sys.modules,
            {
                "click": fake_click,
                "requests": fake_requests,
                "requests.exceptions": fake_requests_exceptions,
                "yr": fake_yr,
                "yr.cli": fake_yr_cli,
                "yr.cli.exec": fake_yr_cli_exec,
            },
        ):
            spec.loader.exec_module(scripts)
        return scripts

    def test_cli_defines_insecure_option_once(self):
        scripts_path = Path(__file__).resolve().parents[1] / "cli" / "scripts.py"
        module = ast.parse(scripts_path.read_text())

        cli_func = next(
            node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "cli"
        )

        insecure_option_count = 0
        for decorator in cli_func.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr != "option":
                continue
            if any(isinstance(arg, ast.Constant) and arg.value == "--insecure" for arg in decorator.args):
                insecure_option_count += 1

        self.assertEqual(insecure_option_count, 1)

    def test_http_client_defaults_to_plain_http_without_ca_or_insecure(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        captured = {}

        class FakeResponse:
            status_code = 200
            content = b"{}"
            headers = {}

            def json(self):
                return {}

        class FakeSession:
            def request(self, method, url, **kwargs):
                captured["method"] = method
                captured["url"] = url
                captured["kwargs"] = kwargs
                return FakeResponse()

        scripts.requests.Session = FakeSession
        client = scripts.HTTPClient()

        result = client.request("http://127.0.0.1:18888/api/instances", {}, method="GET")

        self.assertTrue(result["success"])
        self.assertEqual(captured["method"], "GET")
        self.assertEqual(captured["url"], "http://127.0.0.1:18888/api/instances")
        self.assertFalse(captured["kwargs"]["verify"])

    def test_sandbox_create_options_are_optional_and_default_to_py310(self):
        scripts_path = Path(__file__).resolve().parents[1] / "cli" / "scripts.py"
        module = ast.parse(scripts_path.read_text())

        sandbox_create_func = next(
            node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "sandbox_create"
        )

        option_required_values = {}
        runtime_default = None
        for decorator in sandbox_create_func.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr != "option" or not decorator.args:
                continue
            option_name = decorator.args[0]
            if not isinstance(option_name, ast.Constant) or option_name.value not in {"--namespace", "--name", "--runtime"}:
                continue
            required = next((keyword for keyword in decorator.keywords if keyword.arg == "required"), None)
            option_required_values[option_name.value] = required.value.value
            default = next((keyword for keyword in decorator.keywords if keyword.arg == "default"), None)
            if option_name.value == "--runtime":
                runtime_default = default.value.id

        self.assertEqual(option_required_values, {"--namespace": False, "--name": False, "--runtime": False})
        self.assertEqual(runtime_default, "DEFAULT_SANDBOX_RUNTIME")

    def test_query_instances_appends_pagination_params(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                self.__class__.url = url
                self.__class__.method = method
                return {"success": True, "data": []}

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            ret, resp = scripts.query_instances("tenant-a", page=2, page_size=50)

        self.assertTrue(ret)
        self.assertEqual(resp, [])
        self.assertEqual(FakeHTTPClient.method, "GET")
        self.assertIn("tenant_id=tenant-a", FakeHTTPClient.url)
        self.assertIn("page=2", FakeHTTPClient.url)
        self.assertIn("page_size=50", FakeHTTPClient.url)

    def test_query_instance_uses_frontend_instances_query_endpoint(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                self.__class__.url = url
                self.__class__.method = method
                return {
                    "success": True,
                    "data": [{"id": "instance-a", "tenantID": "tenant-a"}],
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            ret, resp = scripts.query_instance("instance-a", "tenant-a")

        self.assertTrue(ret)
        self.assertEqual(resp["id"], "instance-a")
        self.assertEqual(FakeHTTPClient.method, "GET")
        self.assertIn("/api/instances?", FakeHTTPClient.url)
        self.assertIn("tenant_id=tenant-a", FakeHTTPClient.url)
        self.assertIn("instance_id=instance-a", FakeHTTPClient.url)

    def test_query_instance_ignores_malformed_instance_entries(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                return {
                    "success": True,
                    "data": {"instances": [None, "bad-entry", {"id": "instance-a", "tenantID": "tenant-a"}]},
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            ret, resp = scripts.query_instance("instance-a", "tenant-a")

        self.assertTrue(ret)
        self.assertEqual(resp["id"], "instance-a")

    def test_query_instance_rejects_malformed_response(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                return {
                    "success": True,
                    "data": {"error": "unexpected response"},
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            ret, resp = scripts.query_instance("instance-a", "tenant-a")

        self.assertFalse(ret)
        self.assertEqual(resp["error"], "invalid instances response: missing instances")

    def test_function_name_preserves_single_segment_system_function(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        function_name = scripts.FunctionName("0-system-faasExecutorPython3.10", "$latest")

        self.assertEqual(function_name.full_name_no_version(), "0-system-faasExecutorPython3.10")
        self.assertEqual(function_name.full_name(), "0-system-faasExecutorPython3.10:$latest")
        self.assertEqual(str(function_name), "0-system-faasExecutorPython3.10:$latest")

    def test_invoke_function_uses_trailing_slash(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                self.__class__.url = url
                self.__class__.method = method
                return {"success": True, "data": {"result": "ok"}}

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            ret, resp = scripts.invoke_function(
                scripts.FunctionName("default@hello:$latest"), {"value": 1}, user="tenant-a"
            )

        self.assertTrue(ret)
        self.assertEqual(resp, {"result": "ok"})
        self.assertEqual(FakeHTTPClient.method, "POST")
        self.assertEqual(
            FakeHTTPClient.url,
            "http://frontend.example/invocations/tenant-a/default/hello:$latest/",
        )

    def test_list_instances_passes_pagination_params(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__user", "tenant-a")
        calls = []

        def fake_query_instances(user=None, page=None, page_size=None, fields=None):
            calls.append((user, page, page_size, fields))
            return True, {"instances": [{"id": "instance-a", "tenantID": "tenant-a"}]}

        with mock.patch.object(scripts, "query_instances", fake_query_instances), redirect_stdout(io.StringIO()):
            scripts.list(3, 25, "instance")

        self.assertEqual(calls, [("tenant-a", 3, 25, "summary")])

    def test_list_instances_rejects_oversized_page_size(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()) as output:
            scripts.list(None, scripts.QUERY_INSTANCES_MAX_PAGE_SIZE + 1, "instance")

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("less than or equal to 1000", output.getvalue())

    def test_list_instances_rejects_oversized_page(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        with self.assertRaises(SystemExit) as ctx, redirect_stdout(io.StringIO()) as output:
            scripts.list(scripts.QUERY_INSTANCES_MAX_PAGE + 1, None, "instance")

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("less than or equal to 10000", output.getvalue())

    def test_list_instances_rejects_malformed_response(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__user", "tenant-a")

        def fake_query_instances(user=None, page=None, page_size=None, fields=None):
            return True, {"error": "unexpected response"}

        with (
            mock.patch.object(scripts, "query_instances", fake_query_instances),
            self.assertRaises(SystemExit) as ctx,
            redirect_stdout(io.StringIO()) as output,
        ):
            scripts.list(None, None, "instance")

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("invalid instances response: missing instances", output.getvalue())

    def test_token_require_calls_frontend_proxy_with_operator_token(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                self.__class__.url = url
                self.__class__.method = method
                self.__class__.headers = headers
                return {"success": True, "headers": {"X-Auth": "tenant-token"}}

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient), redirect_stdout(io.StringIO()) as output:
            scripts.token_require("tenant-a", 3600, "developer", "frontend.example", "operator-token")

        self.assertEqual(FakeHTTPClient.url, "http://frontend.example/auth/token/require")
        self.assertEqual(FakeHTTPClient.method, "GET")
        self.assertEqual(
            FakeHTTPClient.headers,
            {
                "X-Auth": "operator-token",
                "X-Tenant-ID": "tenant-a",
                "X-TTL": "3600",
                "X-Role": "developer",
            },
        )
        self.assertIn("Token: tenant-token", output.getvalue())

    def test_token_require_rejects_missing_operator_token(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        with self.assertRaisesRegex(RuntimeError, "operator token is required"):
            scripts.token_require("tenant-a", None, "developer", "frontend.example", None)

    def test_token_require_rejects_non_developer_role_before_request(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                raise AssertionError("token_require should reject non-developer role locally")

        with self.assertRaisesRegex(RuntimeError, "role must be developer"), \
                mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            scripts.token_require("tenant-a", 3600, "admin", "frontend.example", "operator-token")

    def test_token_abandon_declares_unsupported(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                raise AssertionError("token_abandon should not call frontend")

        with self.assertRaisesRegex(RuntimeError, "developer token abandon is not supported"), \
                mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            scripts.token_abandon("tenant-a", "frontend.example", "operator-token")

    def test_token_commands_default_frontend_address_from_server_address_env(self):
        scripts_path = Path(__file__).resolve().parents[1] / "cli" / "scripts.py"
        module = ast.parse(scripts_path.read_text())

        token_require_func = next(
            node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "token_require"
        )

        frontend_option = None
        for decorator in token_require_func.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            if not isinstance(decorator.func, ast.Attribute) or decorator.func.attr != "option":
                continue
            if any(isinstance(arg, ast.Constant) and arg.value == "--frontend-address" for arg in decorator.args):
                frontend_option = decorator
                break

        self.assertIsNotNone(frontend_option)
        envvar = next((keyword for keyword in frontend_option.keywords if keyword.arg == "envvar"), None)
        self.assertEqual(envvar.value.value, "YR_SERVER_ADDRESS")

    def test_sandbox_create_uses_default_namespace_and_generates_missing_name(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        setattr(scripts, "__user", "tenant-a")

        class FakeUUID:
            def __init__(self, value):
                self.hex = value

        create_calls = []

        def fake_create_sandbox_auto(
            namespace,
            name,
            runtime,
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
            create_timeout_seconds=None,
        ):
            create_calls.append((namespace, name, runtime, image, ports, upstream, proxy_port, create_timeout_seconds))
            return f"{namespace}-{name}", None

        with (
            mock.patch.object(scripts, "create_sandbox_auto", fake_create_sandbox_auto),
            mock.patch.object(scripts.uuid, "uuid4", return_value=FakeUUID("name-id")) as uuid4_mock,
            redirect_stdout(io.StringIO()) as output,
        ):
            scripts.sandbox_create(None, None, scripts.DEFAULT_SANDBOX_RUNTIME)

        self.assertEqual(
            create_calls,
            [(scripts.DEFAULT_SANDBOX_NAMESPACE, "name-id", scripts.DEFAULT_SANDBOX_RUNTIME, None, (), None, 8766, None)],
        )
        self.assertIn("sandbox created, instance_id=default-name-id", output.getvalue())
        uuid4_mock.assert_called_once_with()

    def test_get_sandbox_create_timeout_seconds_prefers_explicit_over_env_and_default(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        old_timeout_env = scripts.os.environ.get("YR_SANDBOX_CREATE_TIMEOUT")
        scripts.os.environ.pop("YR_SANDBOX_CREATE_TIMEOUT", None)
        self.assertEqual(
            scripts.get_sandbox_create_timeout_seconds(),
            scripts.DEFAULT_SANDBOX_CREATE_TIMEOUT_SECONDS,
        )
        self.assertEqual(scripts.get_sandbox_create_timeout_seconds(30), 30)

        scripts.os.environ["YR_SANDBOX_CREATE_TIMEOUT"] = "90"
        self.assertEqual(scripts.get_sandbox_create_timeout_seconds(), 90)
        self.assertEqual(scripts.get_sandbox_create_timeout_seconds(120), 120)

        scripts.os.environ["YR_SANDBOX_CREATE_TIMEOUT"] = "-1"
        self.assertEqual(
            scripts.get_sandbox_create_timeout_seconds(),
            scripts.DEFAULT_SANDBOX_CREATE_TIMEOUT_SECONDS,
        )

        scripts.os.environ["YR_SANDBOX_CREATE_TIMEOUT"] = "0"
        self.assertEqual(scripts.get_sandbox_create_timeout_seconds(), scripts.DEFAULT_SANDBOX_CREATE_TIMEOUT_SECONDS)

        if old_timeout_env is None:
            scripts.os.environ.pop("YR_SANDBOX_CREATE_TIMEOUT", None)
        else:
            scripts.os.environ["YR_SANDBOX_CREATE_TIMEOUT"] = old_timeout_env

    def test_parse_event_stream_decodes_final_payload(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        class FakeResponse:
            def __init__(self, lines):
                self._lines = lines

            def iter_lines(self, decode_unicode=True):
                for line in self._lines:
                    yield line

        response = FakeResponse(
            [
                "event: heartbeat",
                "data: {}",
                "",
                "event: final",
                'data: {"sandboxId":"abc","status":"running"}',
                "",
            ]
        )
        self.assertEqual(
            scripts.HTTPClient._parse_event_stream(response),
            {"sandboxId": "abc", "status": "running"},
        )

    def test_decode_sse_data_returns_dict_or_message(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        self.assertEqual(
            scripts.HTTPClient._decode_sse_data(['{"sandboxId":"abc","status":"running"}']),
            {"sandboxId": "abc", "status": "running"},
        )
        self.assertEqual(
            scripts.HTTPClient._decode_sse_data(["not-json"]),
            {"message": "not-json"},
        )

    def test_create_sandbox_via_frontend_treats_running_status_as_success_and_parses_id(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        setattr(scripts, "__user", "tenant-a")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                return {
                    "success": True,
                    "data": {"sandboxId": "tenant-a-box", "status": scripts.SANDBOX_CREATE_STATUS_RUNNING},
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            supported, instance_id, data = scripts.create_sandbox_via_frontend(
                "tenant-a",
                "box",
                scripts.DEFAULT_SANDBOX_RUNTIME,
            )

        self.assertTrue(supported)
        self.assertEqual(instance_id, "tenant-a-box")
        self.assertEqual(data["status"], scripts.SANDBOX_CREATE_STATUS_RUNNING)

    def test_create_sandbox_via_frontend_rejects_timeout_or_failed_status(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                return {
                    "success": True,
                    "data": {"status": scripts.SANDBOX_CREATE_STATUS_TIMEOUT},
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            supported, instance_id, data = scripts.create_sandbox_via_frontend(
                "tenant-a",
                "box",
                scripts.DEFAULT_SANDBOX_RUNTIME,
            )

        self.assertFalse(supported)
        self.assertEqual(instance_id, "")
        self.assertEqual(data["status"], scripts.SANDBOX_CREATE_STATUS_TIMEOUT)

    def test_create_sandbox_via_frontend_requires_final_payload(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                return {
                    "success": True,
                    "data": None,
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            supported, instance_id, data = scripts.create_sandbox_via_frontend(
                "tenant-a",
                "box",
                scripts.DEFAULT_SANDBOX_RUNTIME,
            )

        self.assertFalse(supported)
        self.assertEqual(instance_id, "")
        self.assertIn("missing final result", data["error"])

    def test_create_sandbox_via_frontend_requires_instance_id_when_status_missing(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                return {
                    "success": True,
                    "data": {"status": "creating"},
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            supported, instance_id, data = scripts.create_sandbox_via_frontend(
                "tenant-a",
                "box",
                scripts.DEFAULT_SANDBOX_RUNTIME,
            )

        self.assertFalse(supported)
        self.assertEqual(instance_id, "")
        self.assertIn("missing final result", data["error"])

    def test_sandbox_create_preserves_explicit_namespace_and_name(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        create_calls = []

        def fake_create_sandbox_auto(
            namespace,
            name,
            runtime,
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
            create_timeout_seconds=None,
        ):
            create_calls.append((namespace, name, runtime, image, ports, upstream, proxy_port, create_timeout_seconds))
            return f"{namespace}-{name}", None

        with (
            mock.patch.object(scripts, "create_sandbox_auto", fake_create_sandbox_auto),
            mock.patch.object(scripts.uuid, "uuid4") as uuid4_mock,
            redirect_stdout(io.StringIO()),
        ):
            scripts.sandbox_create("custom-ns", "custom-name", scripts.DEFAULT_SANDBOX_RUNTIME)

        self.assertEqual(create_calls, [("custom-ns", "custom-name", scripts.DEFAULT_SANDBOX_RUNTIME, None, (), None, 8766, None)])
        uuid4_mock.assert_not_called()

    def test_sandbox_create_passes_custom_image_to_create_flow(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        create_calls = []

        def fake_create_sandbox_auto(
            namespace,
            name,
            runtime,
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
            create_timeout_seconds=None,
        ):
            create_calls.append((namespace, name, runtime, image, ports, upstream, proxy_port, create_timeout_seconds))
            return f"{namespace}-{name}", None

        with (
            mock.patch.object(scripts, "create_sandbox_auto", fake_create_sandbox_auto),
            redirect_stdout(io.StringIO()),
        ):
            scripts.sandbox_create("custom-ns", "custom-name", scripts.DEFAULT_SANDBOX_RUNTIME, "python:3.12-slim")

        self.assertEqual(
            create_calls,
            [("custom-ns", "custom-name", scripts.DEFAULT_SANDBOX_RUNTIME, "python:3.12-slim", (), None, 8766, None)],
        )

    def test_sandbox_create_passes_ports_and_tunnel_to_create_flow(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        create_calls = []

        def fake_create_sandbox_auto(
            namespace,
            name,
            runtime,
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
            create_timeout_seconds=None,
        ):
            create_calls.append((namespace, name, runtime, image, ports, upstream, proxy_port, create_timeout_seconds))
            return f"{namespace}-{name}", None

        with (
            mock.patch.object(scripts, "create_sandbox_auto", fake_create_sandbox_auto),
            redirect_stdout(io.StringIO()),
        ):
            scripts.sandbox_create(
                "custom-ns",
                "custom-name",
                scripts.DEFAULT_SANDBOX_RUNTIME,
                None,
                ("8080", "udp:9090"),
                "127.0.0.1:8000",
                9000,
            )

        self.assertEqual(
            create_calls,
            [
                (
                    "custom-ns",
                    "custom-name",
                    scripts.DEFAULT_SANDBOX_RUNTIME,
                    None,
                    ("8080", "udp:9090"),
                    "127.0.0.1:8000",
                    9000,
                    None,
                )
            ],
        )

    def test_sandbox_create_passes_create_timeout_seconds_to_create_flow(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        create_calls = []

        class FakeUUID:
            def __init__(self, value):
                self.hex = value

        def fake_create_sandbox_auto(
            namespace,
            name,
            runtime,
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
            create_timeout_seconds=None,
        ):
            create_calls.append((namespace, name, runtime, image, ports, upstream, proxy_port, create_timeout_seconds))
            return f"{namespace}-{name}", None

        with (
            mock.patch.object(scripts, "create_sandbox_auto", fake_create_sandbox_auto),
            mock.patch.object(scripts.uuid, "uuid4", return_value=FakeUUID("name-id")),
            redirect_stdout(io.StringIO()),
        ):
            scripts.sandbox_create(
                None,
                None,
                scripts.DEFAULT_SANDBOX_RUNTIME,
                create_timeout_seconds=90,
            )

        self.assertEqual(
            create_calls,
            [
                (
                    scripts.DEFAULT_SANDBOX_NAMESPACE,
                    "name-id",
                    scripts.DEFAULT_SANDBOX_RUNTIME,
                    None,
                    (),
                    None,
                    8766,
                    90,
                )
            ],
        )

    def test_sandbox_create_rejects_invalid_proxy_port_upper_bound(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        with (
            mock.patch.object(scripts, "create_sandbox_auto") as create_sandbox_auto,
            self.assertRaises(SystemExit) as ctx,
            redirect_stdout(io.StringIO()) as output,
        ):
            scripts.sandbox_create(
                "custom-ns",
                "custom-name",
                scripts.DEFAULT_SANDBOX_RUNTIME,
                None,
                (),
                "127.0.0.1:8000",
                65536,
            )

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("--proxy-port must be in [2, 65535]", output.getvalue())
        create_sandbox_auto.assert_not_called()

    def test_sandbox_runtime_function_id_uses_py310_suffix(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()

        self.assertEqual(
            scripts.sandbox_runtime_function_id("python3.10", "default"),
            "sn:cn:yrk:default:function:0-defaultservice-py310:$latest",
        )

    def test_create_sandbox_via_sdk_returns_create_instance_id_without_get(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example:443")
        setattr(scripts, "__jwt_token", "token")

        class FakeConfig:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
                self.auth_token = ""
                self.tenant_id = ""
                self.enable_tls = None

        class FakeInvokeOptions:
            def __init__(self):
                self.custom_extensions = {}

        class FakePortForwarding:
            def __init__(self, port, protocol="TCP"):
                self.port = port
                self.protocol = protocol

        fake_sandbox_instance = mock.Mock()
        fake_sandbox_instance.instance_id = "default-name-id"
        fake_sandbox_instance.real_id = "real-instance-id"
        fake_creator = mock.Mock()
        fake_creator.invoke.return_value = fake_sandbox_instance
        fake_options = mock.Mock(return_value=fake_creator)
        fake_sandbox_instance_class = mock.Mock()
        fake_sandbox_instance_class.options = fake_options
        fake_yr = mock.Mock()
        fake_yr.Config = FakeConfig
        fake_yr.InvokeOptions = FakeInvokeOptions
        fake_yr.PortForwarding = FakePortForwarding
        fake_yr.sandbox.SandboxInstance = fake_sandbox_instance_class

        with (
            mock.patch.object(scripts, "yr", fake_yr),
            mock.patch.object(scripts, "print_sandbox_port_forwarding_urls") as print_urls,
            mock.patch.object(scripts, "resolve_created_sandbox_instance_id", return_value="real-instance-id") as resolve_id,
        ):
            instance_id, tunnel_info = scripts.create_sandbox_via_sdk(
                "default",
                "name-id",
                scripts.DEFAULT_SANDBOX_RUNTIME,
                image="python:3.12-slim",
                ports=("8080", "udp:9090"),
            )

        self.assertEqual(instance_id, "real-instance-id")
        self.assertIsNone(tunnel_info)
        invoke_opt = fake_options.call_args.args[0]
        self.assertEqual(
            scripts.json.loads(invoke_opt.custom_extensions["rootfs"]),
            {"runtime": "runsc", "type": "image", "imageurl": "python:3.12-slim"},
        )
        self.assertEqual(
            [(pf.port, pf.protocol) for pf in invoke_opt.port_forwardings],
            [(8080, "TCP"), (9090, "UDP")],
        )
        self.assertEqual(
            scripts.json.loads(invoke_opt.custom_extensions["network"]),
            {
                "portForwardings": [
                    {"port": 8080, "protocol": "TCP"},
                    {"port": 9090, "protocol": "UDP"},
                ]
            },
        )
        print_urls.assert_called_once_with("real-instance-id", invoke_opt.port_forwardings)
        fake_yr.get.assert_not_called()
        resolve_id.assert_called_once_with("default", "name-id", "real-instance-id")
        fake_yr.init.assert_called_once()
        fake_yr.finalize.assert_called_once()

    def test_create_sandbox_auto_prefers_sdk_create(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        with (
            mock.patch.object(scripts, "create_sandbox_via_sdk", return_value=("sdk-real-id", None)) as sdk_create,
            mock.patch.object(scripts, "create_sandbox_via_frontend") as frontend_create,
            mock.patch.object(
                scripts,
                "query_instance",
                return_value=(True, {"id": "actual-sdk-id", "function": "0-defaultservice-py310", "status": "running"}),
            ),
        ):
            instance_id, data = scripts.create_sandbox_auto("default", "box", scripts.DEFAULT_SANDBOX_RUNTIME)

        self.assertEqual(instance_id, "actual-sdk-id")
        self.assertIsNone(data)
        sdk_create.assert_called_once_with(
            "default",
            "box",
            "python3.10",
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
        )
        frontend_create.assert_not_called()

    def test_create_sandbox_auto_uses_frontend_api_when_sdk_is_unsupported(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        setattr(scripts, "__user", "tenant-a")
        encoded = scripts.base64.b64encode(
            scripts.json.dumps({"instance_id": "tenant-a-box"}).encode()
        ).decode()

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                self.__class__.kwargs = kwargs

            def request(self, url, data, method="POST", headers=None):
                self.__class__.url = url
                self.__class__.data = data
                self.__class__.headers = headers
                self.__class__.method = method
                return {"success": True, "data": {"data": encoded}}

        with (
            mock.patch.object(scripts, "HTTPClient", FakeHTTPClient),
            mock.patch.object(scripts, "create_sandbox_via_sdk", side_effect=RuntimeError("function not found: 0-defaultservice-py310")) as sdk_create,
            mock.patch.object(
                scripts,
                "query_instance",
                return_value=(
                    True,
                    {"id": "actual-tenant-a-box", "function": "default/0-defaultservice-py310/$latest", "status": "running"},
                ),
            ),
        ):
            instance_id, data = scripts.create_sandbox_auto(
                "tenant-a",
                "box",
                scripts.DEFAULT_SANDBOX_RUNTIME,
                image="python:3.12-slim",
            )

        self.assertEqual(instance_id, "actual-tenant-a-box")
        self.assertIsNone(data)
        self.assertEqual(FakeHTTPClient.url, "http://frontend.example/api/sandbox/v1/sandboxes")
        self.assertEqual(
            FakeHTTPClient.data,
            {
                "name": "box",
                "namespace": "tenant-a",
                "runtime": "python3.10",
                "rootfs": "python:3.12-slim",
                "createTimeoutSeconds": 60,
            },
        )
        self.assertEqual(
            FakeHTTPClient.headers,
            {"X-Tenant-ID": "tenant-a", "Accept": "text/event-stream"},
        )
        self.assertEqual(FakeHTTPClient.method, "POST")
        sdk_create.assert_called_once_with(
            "tenant-a",
            "box",
            "python3.10",
            image="python:3.12-slim",
            ports=None,
            upstream=None,
            proxy_port=8766,
        )

    def test_create_sandbox_auto_falls_back_when_sdk_reports_invalid_function(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        with (
            mock.patch.object(
                scripts,
                "create_sandbox_via_sdk",
                side_effect=RuntimeError("failed to create sandbox: invalid function"),
            ) as sdk_create,
            mock.patch.object(
                scripts,
                "create_sandbox_via_frontend",
                return_value=(True, "frontend-id", {"instance_id": "frontend-id"}),
            ) as frontend_create,
            mock.patch.object(scripts, "resolve_created_sandbox_instance_id", return_value="frontend-real-id"),
            mock.patch.object(
                scripts,
                "query_instance",
                return_value=(True, {"id": "frontend-real-id", "function": "0-defaultservice-py310", "status": "running"}),
            ),
        ):
            instance_id, data = scripts.create_sandbox_auto("default", "box", scripts.DEFAULT_SANDBOX_RUNTIME)

        self.assertEqual(instance_id, "frontend-real-id")
        self.assertIsNone(data)
        sdk_create.assert_called_once()
        frontend_create.assert_called_once()

    def test_create_sandbox_auto_falls_back_when_sdk_result_is_not_visible(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        with (
            mock.patch.object(scripts, "create_sandbox_via_sdk", return_value=("sdk-missing-id", None)) as sdk_create,
            mock.patch.object(
                scripts,
                "create_sandbox_via_frontend",
                return_value=(True, "frontend-id", {"instance_id": "frontend-id"}),
            ) as frontend_create,
            mock.patch.object(
                scripts,
                "resolve_created_sandbox_instance_id",
                side_effect=["sdk-missing-id", "frontend-real-id"],
            ),
            mock.patch.object(
                scripts,
                "query_instance",
                side_effect=[
                    (False, {"error": "not found"}),
                    (True, {"id": "frontend-real-id", "function": "0-defaultservice-py310", "status": "running"}),
                ],
            ),
        ):
            instance_id, data = scripts.create_sandbox_auto("default", "box", scripts.DEFAULT_SANDBOX_RUNTIME)

        self.assertEqual(instance_id, "frontend-real-id")
        self.assertIsNone(data)
        sdk_create.assert_called_once()
        frontend_create.assert_called_once_with(
            "default",
            "box",
            "python3.10",
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
            create_timeout_seconds=None,
        )

    def test_create_sandbox_via_frontend_passes_ports(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        setattr(scripts, "__user", "tenant-a")
        encoded = scripts.base64.b64encode(
            scripts.json.dumps({"instance_id": "tenant-a-box"}).encode()
        ).decode()

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                self.__class__.data = data
                self.__class__.headers = headers
                return {"success": True, "data": {"data": encoded}}

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            supported, instance_id, data = scripts.create_sandbox_via_frontend(
                "tenant-a",
                "box",
                scripts.DEFAULT_SANDBOX_RUNTIME,
                ports=("8080", "udp:9090"),
            )

        self.assertTrue(supported)
        self.assertEqual(instance_id, "tenant-a-box")
        self.assertEqual(data, {"data": encoded})
        self.assertEqual(
            FakeHTTPClient.data,
            {
                "name": "box",
                "namespace": "tenant-a",
                "runtime": "python3.10",
                "ports": ["8080", "udp:9090"],
                "createTimeoutSeconds": 60,
            },
        )
        self.assertEqual(
            FakeHTTPClient.headers,
            {"X-Tenant-ID": "tenant-a", "Accept": "text/event-stream"},
        )

    def test_create_sandbox_auto_does_not_fallback_for_duplicate_sdk_error(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        with (
            mock.patch.object(scripts, "create_sandbox_via_sdk", side_effect=RuntimeError("same instance id already exists")),
            mock.patch.object(scripts, "create_sandbox_via_frontend") as frontend_create,
            self.assertRaisesRegex(RuntimeError, "same instance id"),
        ):
            scripts.create_sandbox_auto("default", "box", scripts.DEFAULT_SANDBOX_RUNTIME)

        frontend_create.assert_not_called()

    def test_create_sandbox_auto_rejects_old_py39_frontend_fallback(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, method="POST", headers=None):
                return {
                    "success": False,
                    "status_code": 500,
                    "error": "Executable path of python3.9 is not found",
                }

        with (
            mock.patch.object(scripts, "HTTPClient", FakeHTTPClient),
            mock.patch.object(scripts, "create_sandbox_via_sdk", side_effect=RuntimeError("function not found: 0-defaultservice-py310")) as sdk_create,
            mock.patch.object(scripts, "query_instance", return_value=(True, {"id": "default-box"})),
            self.assertRaisesRegex(RuntimeError, "Executable path of python3.9"),
        ):
            scripts.create_sandbox_auto("default", "box", scripts.DEFAULT_SANDBOX_RUNTIME)

        sdk_create.assert_called_once_with(
            "default",
            "box",
            "python3.10",
            image=None,
            ports=None,
            upstream=None,
            proxy_port=8766,
        )

    def test_sandbox_list_prints_header_status_and_resource_quota(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__user", "tenant-a")

        def fake_query_instances(user=None, fields=None):
            self.assertEqual(fields, "summary")
            return True, {
                "instances": [
                    {
                        "id": "tenant-a-box",
                        "tenantID": "tenant-a",
                        "node_id": "node-a",
                        "status": "running",
                        "required_cpu": 500,
                        "required_mem": 1024,
                        "limit_cpu": 1000,
                        "limit_mem": 2048,
                        "runtime_seconds": 125,
                        "image": "s3://crfs-dev/rootfs.img",
                    },
                    {"id": "app-not-sandbox", "tenantID": "tenant-a", "status": "running"},
                ]
            }

        with mock.patch.object(scripts, "query_instances", fake_query_instances), redirect_stdout(io.StringIO()) as output:
            scripts.sandbox_list(None)

        self.assertEqual(
            output.getvalue().splitlines(),
            [
                "INSTANCE_ID   TENANT_ID  NODE_ID  STATUS   IMAGE                     CPU_REQ  MEMORY_REQ  CPU_LIMIT  MEMORY_LIMIT  RUNTIME",
                "tenant-a-box  tenant-a   node-a   running  s3://crfs-dev/rootfs.img  500      1024        1000       2048          125s",
            ],
        )

    def test_delete_sandbox_via_sdk_uses_async_terminate(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        runtime = mock.Mock()
        scripts.yr.init = mock.Mock()
        scripts.yr.finalize = mock.Mock()
        scripts.yr.kill_instance = mock.Mock()
        scripts.yr.runtime_holder = types.SimpleNamespace(
            global_runtime=types.SimpleNamespace(get_runtime=lambda: runtime)
        )

        with (
            mock.patch.object(scripts, "build_sandbox_sdk_config", return_value=object()) as build_config,
            mock.patch.object(scripts.yr, "init") as yr_init,
            mock.patch.object(scripts.yr, "finalize") as yr_finalize,
        ):
            scripts.delete_sandbox_via_sdk("sandbox-id")

        build_config.assert_called_once_with(scripts.DEFAULT_SANDBOX_RUNTIME)
        yr_init.assert_called_once()
        runtime.terminate_instance.assert_called_once_with("sandbox-id")
        scripts.yr.kill_instance.assert_not_called()
        yr_finalize.assert_called_once()

    def test_sandbox_delete_uses_frontend_kill_interface(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        captured = {}

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                self.__class__.kwargs = kwargs

            def request(self, url, data, headers=None, method="POST"):
                captured["url"] = url
                captured["data"] = data
                captured["method"] = method
                return {
                    "success": True,
                    "status_code": 200,
                    "data": {"code": 0, "message": ""},
                }

        with (
            mock.patch.object(scripts, "HTTPClient", FakeHTTPClient),
            mock.patch.object(scripts, "wait_until_sandbox_deleted", return_value=True),
            redirect_stdout(io.StringIO()) as output,
        ):
            scripts.sandbox_delete("sandbox-id")

        self.assertEqual(captured["url"], "http://frontend.example/frontend/v1/instance/kill")
        self.assertEqual(captured["method"], "POST")
        # The body is a plain JSON object carrying the instance id and kill signal,
        # and intentionally carries no routing info (no proxyID/routeAddress), so the
        # function_proxy resolves the owner locally or forwards to function_master.
        self.assertEqual(captured["data"], {"instanceID": "sandbox-id", "signal": 1})
        self.assertNotIn("proxyID", captured["data"])
        self.assertNotIn("routeAddress", captured["data"])
        self.assertIn("succeed to delete sandbox: sandbox-id", output.getvalue())

    def test_sandbox_delete_passes_tls_and_jwt_options(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")
        setattr(scripts, "__insecure", True)
        setattr(scripts, "__jwt_token", "token")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                self.__class__.kwargs = kwargs

            def request(self, url, data, headers=None, method="POST"):
                return {
                    "success": True,
                    "status_code": 200,
                    "data": {"code": 0, "message": ""},
                }

        with (
            mock.patch.object(scripts, "HTTPClient", FakeHTTPClient),
            mock.patch.object(scripts, "wait_until_sandbox_deleted", return_value=True),
            redirect_stdout(io.StringIO()),
        ):
            scripts.sandbox_delete("sandbox-id")

        self.assertTrue(FakeHTTPClient.kwargs["insecure"])
        self.assertEqual(FakeHTTPClient.kwargs["jwt_token"], "token")

    def test_sandbox_delete_fails_when_kill_response_has_error(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, headers=None, method="POST"):
                return {
                    "success": True,
                    "status_code": 200,
                    "data": {"code": 14, "message": "instance not found"},
                }

        with (
            mock.patch.object(scripts, "HTTPClient", FakeHTTPClient),
            mock.patch.object(scripts, "wait_until_sandbox_deleted", return_value=False),
            redirect_stdout(io.StringIO()) as output,
            self.assertRaises(SystemExit) as ctx,
        ):
            scripts.sandbox_delete("sandbox-id")

        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("failed to delete sandbox sandbox-id", output.getvalue())

    def test_kill_instance_via_frontend_builds_json_and_parses_code(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "frontend.example")

        captured = {}

        class FakeHTTPClient:
            def __init__(self, **kwargs):
                pass

            def request(self, url, data, headers=None, method="POST"):
                captured["data"] = data
                return {
                    "success": True,
                    "status_code": 200,
                    "data": {"code": 0, "message": ""},
                }

        with mock.patch.object(scripts, "HTTPClient", FakeHTTPClient):
            ok = scripts.kill_instance_via_frontend("abc-123")
        # route-less JSON kill request: only instanceID + signal, no routing fields
        self.assertEqual(captured["data"], {"instanceID": "abc-123", "signal": 1})
        self.assertTrue(ok["success"])
        self.assertEqual(ok["code"], 0)

        # a non-zero KillResponse.code (e.g. ERR_INSTANCE_NOT_FOUND) is a failure
        class FailingHTTPClient(FakeHTTPClient):
            def request(self, url, data, headers=None, method="POST"):
                return {
                    "success": True,
                    "status_code": 200,
                    "data": {"code": 14, "message": "not found"},
                }

        with mock.patch.object(scripts, "HTTPClient", FailingHTTPClient):
            failed = scripts.kill_instance_via_frontend("missing")
        self.assertFalse(failed["success"])
        self.assertEqual(failed["code"], 14)
        self.assertEqual(failed["message"], "not found")

    def test_wait_until_sandbox_deleted_polls_until_missing(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        clock = mock.Mock()
        clock.time.side_effect = [0, 1, 1]

        with (
            mock.patch.object(scripts, "sandbox_exists", side_effect=[True, False]),
            mock.patch.object(scripts, "time", clock),
        ):
            self.assertTrue(scripts.wait_until_sandbox_deleted("sandbox-id", timeout=30))

        clock.sleep.assert_called_once_with(1)

    def test_wait_until_sandbox_deleted_default_covers_slow_async_delete(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        clock = mock.Mock()
        clock.time.side_effect = [0, 59, 59]

        with (
            mock.patch.object(scripts, "sandbox_exists", side_effect=[True, False]),
            mock.patch.object(scripts, "time", clock),
        ):
            self.assertTrue(scripts.wait_until_sandbox_deleted("sandbox-id"))

        clock.sleep.assert_called_once_with(1)

    def test_exec_uses_wss_for_443_token_only_connection(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "124.70.166.142:443")
        setattr(scripts, "__client_cert", None)
        setattr(scripts, "__client_key", None)
        setattr(scripts, "__ca_cert", None)
        setattr(scripts, "__insecure", True)
        setattr(scripts, "__jwt_token", "token")
        captured = {}

        async def fake_run_client(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        with mock.patch.object(scripts, "run_client", fake_run_client):
            scripts.exec(False, False, True, "instance-id", "bash")

        connection, invocation = captured["args"]
        self.assertEqual((connection.host, connection.port), ("124.70.166.142", "443"))
        self.assertEqual(invocation.instance, "instance-id")
        self.assertEqual(invocation.command, "bash")
        self.assertTrue(connection.use_ssl)
        self.assertFalse(connection.verify_server)
        self.assertIsNone(connection.cert_file)
        self.assertIsNone(connection.key_file)
        self.assertEqual(connection.token, "token")
        self.assertTrue(connection.quiet)

    def test_exec_keeps_tty_mode_verbose(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "124.70.166.142:443")
        setattr(scripts, "__client_cert", None)
        setattr(scripts, "__client_key", None)
        setattr(scripts, "__ca_cert", None)
        setattr(scripts, "__insecure", True)
        captured = {}

        async def fake_run_client(*args, **kwargs):
            captured["connection"] = args[0]

        with mock.patch.object(scripts, "run_client", fake_run_client):
            scripts.exec(False, True, True, "instance-id", "bash")

        self.assertFalse(captured["connection"].quiet)

    def test_exec_without_tty_suppresses_keyboard_interrupt_message(self):
        scripts = self.load_cli_scripts_with_stubbed_deps()
        setattr(scripts, "__server_address", "124.70.166.142:443")
        setattr(scripts, "__client_cert", None)
        setattr(scripts, "__client_key", None)
        setattr(scripts, "__ca_cert", None)
        setattr(scripts, "__insecure", True)
        stderr = io.StringIO()

        async def fake_run_client(*args, **kwargs):
            raise KeyboardInterrupt()

        with mock.patch.object(scripts, "run_client", fake_run_client), redirect_stderr(stderr):
            scripts.exec(False, False, True, "instance-id", "bash")

        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
