#!/usr/bin/env python3
# coding=UTF-8
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

from collections import deque
import ast
import json
from pathlib import Path
import ssl
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, patch

import pytest

from yr.config import Config, UserTLSConfig
from yr.config_manager import ConfigManager
from yr.datasystem_capability import (
    DataSystemCapability,
    require_data_system_for_object_refs,
    resolve_data_system_capability,
    resolve_data_system_endpoint,
)
from yr.err_type import ErrorCode
from yr.exception import YRRuntimeError
from yr.object_ref import ObjectRef


@pytest.fixture(autouse=True)
def clear_capability_environment(monkeypatch):
    monkeypatch.delenv("YR_DATASYSTEM_DEPLOYED", raising=False)
    monkeypatch.delenv("DATA_SYSTEM_ENABLE", raising=False)
    monkeypatch.delenv("YR_BYPASS_DATASYSTEM", raising=False)


def test_complete_environment_does_not_query_frontend(monkeypatch):
    monkeypatch.setenv("YR_DATASYSTEM_DEPLOYED", " false ")
    monkeypatch.setenv("YR_BYPASS_DATASYSTEM", "YES")

    with patch("yr.datasystem_capability.urlopen") as get:
        capability = resolve_data_system_capability(Config(server_address="frontend:31222"))

    get.assert_not_called()
    assert capability.data_system_deployed is False
    assert capability.bypass_data_system is True
    assert capability.source == "environment"


def test_partial_environment_uses_frontend_only_for_missing_value(monkeypatch):
    monkeypatch.setenv("YR_DATASYSTEM_DEPLOYED", "false")
    response = Mock()
    response.getcode.return_value = 200
    response.read.return_value = json.dumps({
        "dataSystem": {"dataSystemDeployed": True, "bypassDataSystem": True}
    }).encode()

    with patch("yr.datasystem_capability.urlopen", return_value=response) as get:
        capability = resolve_data_system_capability(Config(server_address="frontend:31222"))

    get.assert_called_once()
    assert capability.data_system_deployed is False
    assert capability.bypass_data_system is True
    assert capability.source == "environment+frontend"


def test_explicit_bypass_avoids_frontend_when_deployment_capability_is_in_environment(monkeypatch):
    monkeypatch.setenv("YR_DATASYSTEM_DEPLOYED", "false")

    with patch("yr.datasystem_capability.urlopen") as get:
        capability = resolve_data_system_capability(
            Config(server_address="frontend:31222", bypass_datasystem=True))

    get.assert_not_called()
    assert capability.data_system_deployed is False
    assert capability.bypass_data_system is True
    assert capability.source == "environment+config"


def test_old_frontend_without_capability_endpoint_uses_compatible_defaults():
    failure = HTTPError("http://frontend", 404, "not found", None, None)
    with patch("yr.datasystem_capability.urlopen", side_effect=failure):
        capability = resolve_data_system_capability(Config(server_address="frontend:31222"))

    assert capability.data_system_deployed is True
    assert capability.bypass_data_system is False
    assert capability.source == "default"


@pytest.mark.parametrize("failure", [
    URLError("network failure"),
    TimeoutError("timed out"),
    ValueError("invalid json"),
])
def test_frontend_discovery_failure_is_explicit(failure):
    with patch("yr.datasystem_capability.urlopen", side_effect=failure):
        with pytest.raises(RuntimeError, match="Failed to discover DataSystem capability"):
            resolve_data_system_capability(Config(server_address="frontend:31222"))


@pytest.mark.parametrize("data_system, expected_error", [
    ({"dataSystemDeployed": "false", "bypassDataSystem": 1}, "invalid boolean fields"),
    (None, "dataSystem field must be an object"),
])
def test_invalid_frontend_fields_are_rejected(data_system, expected_error):
    response = Mock()
    response.getcode.return_value = 200
    response.read.return_value = json.dumps({"dataSystem": data_system}).encode()

    with patch("yr.datasystem_capability.urlopen", return_value=response), \
            pytest.raises(RuntimeError, match=expected_error):
        resolve_data_system_capability(Config(server_address="frontend:31222"))


def test_frontend_url_normalizes_trailing_slash():
    response = Mock()
    response.getcode.return_value = 200
    response.read.return_value = json.dumps({
        "dataSystem": {"dataSystemDeployed": False, "bypassDataSystem": True}
    }).encode()

    with patch("yr.datasystem_capability.urlopen", return_value=response) as get:
        capability = resolve_data_system_capability(Config(server_address="frontend:31222/"))

    assert capability.data_system_deployed is False
    assert capability.bypass_data_system is True
    assert get.call_args.args[0].full_url == "http://frontend:31222/serverless/v1/capabilities"


def test_frontend_request_reuses_tls_and_has_bounded_timeout_without_auth():
    response = Mock()
    response.getcode.return_value = 200
    response.read.return_value = json.dumps({
        "dataSystem": {"dataSystemDeployed": False, "bypassDataSystem": True}
    }).encode()
    config = Config(
        server_address="frontend:443",
        enable_tls=True,
        auth_token="jwt-token",
        tls_config=UserTLSConfig(
            root_cert_path="/certs/ca.pem",
            module_cert_path="/certs/client.pem",
            module_key_path="/certs/client-key.pem",
        ),
    )

    context = Mock(spec=ssl.SSLContext)
    with patch("yr.datasystem_capability.ssl.create_default_context", return_value=context) as create_context, \
            patch("yr.datasystem_capability.urlopen", return_value=response) as get:
        capability = resolve_data_system_capability(config)

    assert capability.data_system_deployed is False
    assert capability.bypass_data_system is True
    request = get.call_args.args[0]
    _, kwargs = get.call_args
    assert not dict(request.header_items())
    assert kwargs["timeout"] <= 1
    assert request.full_url == "https://frontend:443/serverless/v1/capabilities"
    assert kwargs["context"] is context
    create_context.assert_called_once_with(cafile="/certs/ca.pem")
    context.load_cert_chain.assert_called_once_with(
        certfile="/certs/client.pem", keyfile="/certs/client-key.pem")


def test_legacy_tls_frontend_request_verifies_server_by_default():
    response = Mock()
    response.getcode.return_value = 200
    response.read.return_value = json.dumps({
        "dataSystem": {"dataSystemDeployed": True, "bypassDataSystem": False}
    }).encode()
    config = Config(server_address="frontend:443", enable_tls=True)

    context = Mock(spec=ssl.SSLContext)
    with patch("yr.datasystem_capability.ssl.create_default_context", return_value=context) as create_context, \
            patch("yr.datasystem_capability.urlopen", return_value=response) as get:
        resolve_data_system_capability(config)

    create_context.assert_called_once_with()
    assert get.call_args.kwargs["context"] is context


@pytest.mark.parametrize("config", [
    Config(
        server_address="frontend:443",
        enable_tls=True,
        tls_config=UserTLSConfig(
            root_cert_path="",
            module_cert_path="/certs/client.pem",
            module_key_path="",
        ),
    ),
    Config(
        server_address="frontend:443",
        enable_tls=True,
        enable_mtls=True,
        certificate_file_path="/certs/client.pem",
    ),
])
def test_incomplete_mtls_client_certificate_pair_is_rejected(config):
    with patch("yr.datasystem_capability.urlopen") as get, \
            pytest.raises(ValueError, match="certificate and private key"):
        resolve_data_system_capability(config)

    get.assert_not_called()


def test_object_ref_scan_handles_cycles_without_object_refs():
    cyclic = []
    cyclic.append(cyclic)
    ConfigManager().data_system_capability = DataSystemCapability(False, True, "test")
    try:
        require_data_system_for_object_refs("invoke arguments", cyclic)
    finally:
        ConfigManager().data_system_capability = DataSystemCapability()


def test_object_ref_scan_rejects_object_ref_inside_cycle_when_datasystem_disabled():
    with patch.object(ObjectRef, "__del__", return_value=None):
        cyclic = []
        cyclic.append(cyclic)
        cyclic.append(ObjectRef.__new__(ObjectRef))
        ConfigManager().data_system_capability = DataSystemCapability(False, True, "test")
        try:
            with pytest.raises(YRRuntimeError) as raised:
                require_data_system_for_object_refs("invoke arguments", cyclic)
        finally:
            ConfigManager().data_system_capability = DataSystemCapability()
        cyclic.clear()

    assert raised.value.code == ErrorCode.ERR_DATASYSTEM_FAILED


def test_object_ref_scan_rejects_deque_container():
    with patch.object(ObjectRef, "__del__", return_value=None):
        ref = ObjectRef.__new__(ObjectRef)
        values = deque([ref])
        ConfigManager().data_system_capability = DataSystemCapability(False, True, "test")
        try:
            with pytest.raises(YRRuntimeError):
                require_data_system_for_object_refs("invoke arguments", values)
        finally:
            ConfigManager().data_system_capability = DataSystemCapability()
        values.clear()
        del ref


def test_object_ref_scan_is_skipped_when_datasystem_enabled():
    cyclic = []
    cyclic.append(cyclic)

    require_data_system_for_object_refs("invoke arguments", cyclic)


def test_runtime_without_complete_environment_never_queries_frontend(monkeypatch):
    monkeypatch.setenv("YR_DATASYSTEM_DEPLOYED", "false")
    config = Config(is_driver=False, server_address="")

    with patch("yr.datasystem_capability.urlopen") as get:
        capability = resolve_data_system_capability(config)

    get.assert_not_called()
    assert capability.data_system_deployed is False
    assert capability.bypass_data_system is False
    assert capability.source == "environment+default"


def test_in_cluster_driver_never_queries_function_proxy_as_frontend():
    config = Config(is_driver=True, in_cluster=True, server_address="function-proxy:29363")

    with patch("yr.datasystem_capability.urlopen") as get:
        capability = resolve_data_system_capability(config)

    get.assert_not_called()
    assert capability.data_system_deployed is True
    assert capability.bypass_data_system is False
    assert capability.source == "default"


def test_local_mode_never_queries_frontend():
    config = Config(local_mode=True, server_address="frontend:31222")

    with patch("yr.datasystem_capability.urlopen") as get:
        capability = resolve_data_system_capability(config)

    get.assert_not_called()
    assert capability.data_system_deployed is True
    assert capability.bypass_data_system is False
    assert capability.source == "local_mode"


def test_capability_module_has_no_requests_runtime_dependency():
    source = Path(__file__).parents[1] / "datasystem_capability.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            imports.update(alias.name for alias in node.names)
    assert "requests" not in imports


def test_disabled_runtime_does_not_require_datasystem_address():
    assert resolve_data_system_endpoint("", in_cluster=True, data_system_deployed=False) == ("", 0)


def test_datasystem_endpoint_supports_bracketed_ipv6():
    assert resolve_data_system_endpoint("[::1]:31501", in_cluster=True, data_system_deployed=True) == ("::1", 31501)


@pytest.mark.parametrize("address", ["worker", "worker:not-a-port", "::1:31501"])
def test_invalid_datasystem_endpoint_is_rejected(address):
    with pytest.raises(ValueError, match="DataSystem address"):
        resolve_data_system_endpoint(address, in_cluster=True, data_system_deployed=True)


def test_function_agent_client_switch_does_not_change_deployment_capability(monkeypatch):
    monkeypatch.setenv("DATA_SYSTEM_ENABLE", "false")
    monkeypatch.setenv("YR_DATASYSTEM_DEPLOYED", "true")
    monkeypatch.setenv("YR_BYPASS_DATASYSTEM", "false")

    capability = resolve_data_system_capability(Config(is_driver=False))

    assert capability.data_system_deployed is True
    assert capability.bypass_data_system is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
