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

"""Resolve DataSystem deployment capability for SDK initialization."""

from collections import deque
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
import json
import logging
import os
import ssl
from typing import Optional, Tuple
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from yr.config import Config


_CAPABILITIES_PATH = "/serverless/v1/capabilities"
_CAPABILITIES_TIMEOUT_SECONDS = 1
_DATA_SYSTEM_DEPLOYED_ENV = "YR_DATASYSTEM_DEPLOYED"
_BYPASS_DATA_SYSTEM_ENV = "YR_BYPASS_DATASYSTEM"
_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataSystemCapability:
    """Effective DataSystem capability used by one initialized SDK process."""

    data_system_deployed: bool = True
    bypass_data_system: bool = False
    source: str = "default"


def resolve_data_system_endpoint(
        address: str, in_cluster: bool, data_system_deployed: bool) -> Tuple[str, int]:
    """Return the native DataSystem endpoint only when the runtime can use it."""
    if not in_cluster or not data_system_deployed:
        return "", 0
    try:
        parsed = urlsplit(address if "://" in address else f"//{address}")
        host = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"Invalid DataSystem address: {address!r}") from error
    if not host or port is None:
        raise ValueError(f"Invalid DataSystem address: {address!r}; expected host:port")
    return host, port


def require_data_system(operation: str) -> None:
    """Reject a DataSystem-backed operation before it reaches native clients."""
    from yr.config_manager import ConfigManager
    from yr.err_type import ErrorCode, ModuleCode
    from yr.exception import raise_yr_runtime_error

    if ConfigManager().data_system_capability.data_system_deployed:
        return
    raise_yr_runtime_error(
        f"DataSystem is disabled in this cluster; {operation} is unavailable",
        code=ErrorCode.ERR_DATASYSTEM_FAILED,
        module_code=ModuleCode.DATASYSTEM,
    )


def require_data_system_for_object_refs(operation: str, *values) -> None:
    """Reject arguments whose value transfer requires DataSystem object IDs."""
    from yr.config_manager import ConfigManager
    from yr.object_ref import ObjectRef

    if ConfigManager().data_system_capability.data_system_deployed:
        return

    visited = set()

    def contains_object_ref(value) -> bool:
        if isinstance(value, ObjectRef):
            return True
        if isinstance(value, Mapping):
            if id(value) in visited:
                return False
            visited.add(id(value))
            return any(contains_object_ref(key) or contains_object_ref(item) for key, item in value.items())
        if isinstance(value, (str, bytes, bytearray, memoryview)):
            return False
        if isinstance(value, (Sequence, Set, deque)):
            if id(value) in visited:
                return False
            visited.add(id(value))
            return any(contains_object_ref(item) for item in value)
        return False

    if any(contains_object_ref(value) for value in values):
        require_data_system(operation)


def _parse_optional_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    return None


def _ssl_context(config: Config, url: str) -> Optional[ssl.SSLContext]:
    ca_file = None
    cert = None
    if config.tls_config is not None:
        ca_file = config.tls_config.root_cert_path or None
        if bool(config.tls_config.module_cert_path) != bool(config.tls_config.module_key_path):
            raise ValueError("Both client certificate and private key are required for mTLS capability discovery")
        if config.tls_config.module_cert_path and config.tls_config.module_key_path:
            cert = (config.tls_config.module_cert_path, config.tls_config.module_key_path)
    elif config.enable_tls:
        ca_file = config.verify_file_path or None
        if config.enable_mtls and bool(config.certificate_file_path) != bool(config.private_key_path):
            raise ValueError("Both client certificate and private key are required for mTLS capability discovery")
        if config.enable_mtls and config.certificate_file_path and config.private_key_path:
            cert = (config.certificate_file_path, config.private_key_path)
    if not url.startswith("https://"):
        return None
    context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
    if cert is not None:
        context.load_cert_chain(certfile=cert[0], keyfile=cert[1])
    return context


def _frontend_url(config: Config) -> str:
    if config.server_address.startswith(("http://", "https://")):
        base_url = config.server_address.rstrip("/")
    else:
        scheme = "https" if config.enable_tls else "http"
        base_url = f"{scheme}://{config.server_address}"
    return base_url.rstrip("/") + _CAPABILITIES_PATH


def _query_frontend(config: Config) -> Tuple[Optional[bool], Optional[bool]]:
    if not config.server_address:
        return None, None
    url = _frontend_url(config)
    context = _ssl_context(config, url)
    request_options = {"timeout": _CAPABILITIES_TIMEOUT_SECONDS}
    if context is not None:
        request_options["context"] = context
    try:
        response = urlopen(Request(url, method="GET"), **request_options)
        try:
            status = response.getcode()
            if status is not None and not 200 <= status < 300:
                raise HTTPError(url, status, f"HTTP status {status}", None, None)
            payload = response.read().decode("utf-8")
        finally:
            response.close()
        data_system = json.loads(payload)["dataSystem"]
        if not isinstance(data_system, Mapping):
            raise ValueError("capability response dataSystem field must be an object")
        enabled = data_system.get("dataSystemDeployed")
        bypass = data_system.get("bypassDataSystem")
        if not isinstance(enabled, bool) or not isinstance(bypass, bool):
            raise ValueError("capability response contains invalid boolean fields")
        return enabled, bypass
    except HTTPError as error:
        if error.code == 404:
            _logger.info("Frontend does not expose DataSystem capability; using compatible defaults")
            return None, None
        raise RuntimeError(f"Failed to discover DataSystem capability: {error}") from error
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise RuntimeError(f"Failed to discover DataSystem capability: {error}") from error


def resolve_data_system_capability(config: Config) -> DataSystemCapability:
    """Resolve env-first capability, querying frontend only for driver-side gaps."""
    if config.local_mode:
        return DataSystemCapability(source="local_mode")

    env_enabled = _parse_optional_bool(os.environ.get(_DATA_SYSTEM_DEPLOYED_ENV))
    env_bypass = _parse_optional_bool(os.environ.get(_BYPASS_DATA_SYSTEM_ENV))
    configured_bypass = config.bypass_datasystem
    effective_bypass = configured_bypass if configured_bypass is not None else env_bypass
    if env_enabled is not None and effective_bypass is not None:
        source = "environment+config" if configured_bypass is not None else "environment"
        return DataSystemCapability(env_enabled, effective_bypass, source)

    frontend_enabled = None
    frontend_bypass = None
    if config.is_driver and config.in_cluster is not True:
        frontend_enabled, frontend_bypass = _query_frontend(config)

    enabled = env_enabled if env_enabled is not None else frontend_enabled
    bypass = effective_bypass if effective_bypass is not None else frontend_bypass
    enabled_source = "environment" if env_enabled is not None else (
        "frontend" if frontend_enabled is not None else "default")
    bypass_source = "config" if configured_bypass is not None else (
        "environment" if env_bypass is not None else (
            "frontend" if frontend_bypass is not None else "default"))
    enabled = True if enabled is None else enabled
    bypass = False if bypass is None else bypass

    source = "+".join(name for name in ("environment", "frontend", "config", "default")
                      if name in (enabled_source, bypass_source))
    return DataSystemCapability(enabled, bypass, source)
