#!/usr/bin/env python3
# coding=UTF-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
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
import binascii
import builtins
import json
import logging
import os
import shutil
import subprocess
import tempfile
import traceback
import uuid
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional
import urllib.error
import urllib.request
from urllib.parse import urlencode

import click
import requests
from requests.exceptions import RequestException

import yr
from yr.cli.exec import (
    CopyRequest,
    ExecConnection,
    ExecInvocation,
    choose_cp_mode,
    copy_from_remote,
    copy_from_remote_streaming,
    copy_to_remote,
    copy_to_remote_streaming,
    run_client,
)

QUERY_INSTANCES_MAX_PAGE = 10000
QUERY_INSTANCES_MAX_PAGE_SIZE = 1000
SANDBOX_CREATE_HTTP_TIMEOUT = 180
SANDBOX_CREATE_TIMEOUT_ENV_NAME = "YR_SANDBOX_CREATE_TIMEOUT"
DEFAULT_SANDBOX_CREATE_TIMEOUT_SECONDS = 60
DEFAULT_SANDBOX_NAMESPACE = "default"
DEFAULT_SANDBOX_RUNTIME = "python3.10"
LOGGER = logging.getLogger(__name__)

# Frontend-exposed instance kill (delete) interface. The frontend accepts a JSON
# body ({"instanceID": ..., "signal": ...}) and replies with JSON
# ({"code": <int>, "message": <str>}); the frontend transcodes JSON to/from the
# core_service.KillRequest/KillResponse protobuf internally, so the CLI never has
# to deal with protobuf encoding.
FRONTEND_INSTANCE_KILL_PATH = "/frontend/v1/instance/kill"
# libruntime Signal.KillInstance == SHUT_DOWN_SIGNAL, terminates a single instance.
KILL_SIGNAL_INSTANCE = 1
# KillResponse.code value that maps to common.ErrorCode.ERR_NONE (success).
KILL_RESPONSE_CODE_OK = 0


__server_address = None
__ds_address = None
__client_cert = None
__client_key = None
__ca_cert = None
__insecure = False
__user = None
__client_auth_type = "mutual"  # "mutual" or "one-way"
__jwt_token = None


@dataclass
class SandboxCreateOptions:
    namespace: str
    name: str
    runtime: str
    image: Any = None
    ports: Any = None
    upstream: Any = None
    proxy_port: int = 8766
    create_timeout_seconds: Any = None

    @classmethod
    def from_call(cls, *args, **kwargs):
        fields = ("namespace", "name", "runtime", "image", "ports", "upstream", "proxy_port", "create_timeout_seconds")
        values = dict(zip(fields, args))
        values.update(kwargs)
        return cls(
            values["namespace"], values["name"], values["runtime"], values.get("image"),
            values.get("ports"), values.get("upstream"), values.get("proxy_port", 8766),
            values.get("create_timeout_seconds"),
        )


def use_tls_for_server_address(server_address):
    return bool(server_address and server_address.rsplit(":", 1)[-1] == "443")


class FunctionName:
    """Function name components"""

    def __init__(self, raw_name: str, version: str = "latest"):
        self.raw_name = raw_name
        self.type = "0"
        self.service = None
        self.name = None
        self.simple_name = None
        self.version = version
        self.parse()

    def __str__(self):
        if self.simple_name:
            return f"{self.simple_name}:{self.version}"
        return f"{self.service}@{self.name}:{self.version}"

    def parse(self):
        """Parse function name into components"""
        name_part = self.raw_name
        if ":" in self.raw_name:
            name_part, version_part = self.raw_name.split(":", 1)
            self.version = version_part
        if "@" in name_part:
            parts = name_part.split("@")
            if len(parts) == 3:
                self.type = parts[0]
                self.service = parts[1]
                self.name = parts[2]
            elif len(parts) == 2:
                self.service = parts[0]
                self.name = parts[1]
            else:
                raise ValueError(f"Invalid function name format: {self.raw_name}")
        else:
            self.simple_name = name_part

    def full_name(self):
        """Get full function name"""
        if self.simple_name:
            return f"{self.simple_name}:{self.version}"
        return f"{self.type}@{self.service}@{self.name}:{self.version}"

    def full_name_no_version(self):
        """Get full function name without version"""
        if self.simple_name:
            return self.simple_name
        return f"{self.type}@{self.service}@{self.name}"

    def invocation_path(self):
        """Get URL path components used by the FaaS invocation endpoint."""
        if self.simple_name:
            return f"{self.simple_name}:{self.version}"
        return "/".join([self.service, f"{self.name}:{self.version}"])


def _decode_sse_data(data_lines):
    payload = "".join(data_lines).strip()
    if not payload:
        return {}
    try:
        parsed = json.loads(payload)
        return parsed if isinstance(parsed, dict) else {"data": parsed}
    except (TypeError, ValueError):
        return {"message": payload}


def _parse_event_stream(response):
    event_name = ""
    data_lines = []
    for raw_line in response.iter_lines(decode_unicode=True):
        if raw_line is None:
            line = ""
        elif isinstance(raw_line, bytes):
            line = raw_line.decode("utf-8").strip()
        else:
            line = raw_line.strip()
        if not line:
            if event_name == "final" and data_lines:
                return _decode_sse_data(data_lines)
            event_name = ""
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
            data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())

    if event_name == "final" and data_lines:
        return _decode_sse_data(data_lines)
    return None


class HTTPClient:
    """HTTP client with TLS authentication support (mutual or one-way) and JWT token"""

    def __init__(
        self,
        timeout: int = 30,
        client_cert: Optional[str] = None,
        client_key: Optional[str] = None,
        ca_cert: Optional[str] = None,
        insecure: bool = False,
        client_auth_type: str = "mutual",  # "mutual" or "one-way"
        jwt_token: Optional[str] = None,
        accept_status: tuple = (200,),  # Status codes to consider as success
    ):
        self.timeout = timeout
        self.session = requests.Session()
        self.client_cert = client_cert
        self.client_key = client_key
        self.ca_cert = ca_cert
        self.insecure = insecure
        self.client_auth_type = client_auth_type
        self.jwt_token = jwt_token
        self.accept_status = accept_status
        self.verify = False

    _parse_event_stream = staticmethod(_parse_event_stream)
    _decode_sse_data = staticmethod(_decode_sse_data)

    def request(
        self,
        url: str,
        data: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        method: str = "POST",
    ) -> Dict[str, Any]:
        """
        Send POST JSON request

        Args:
            url: Target URL
            data: JSON data
            headers: Request headers

        Returns:
            Response data
        """

        default_headers = {
            "Content-Type": "application/json",
            "User-Agent": "DeploymentScript/1.0",
        }
        # Add JWT token if provided
        if self.jwt_token:
            default_headers["X-Auth"] = self.jwt_token
        if headers:
            default_headers.update(headers)

        logging.debug("%s to %s", method.lower(), url)
        logging.debug("headers: %s", json.dumps(default_headers, indent=2))
        logging.debug("body: %s", json.dumps(data, indent=2, ensure_ascii=False))

        accept_event_stream = False
        for value in default_headers.get("Accept", "").split(","):
            normalized = value.strip().lower()
            normalized_media_type = normalized.split(";", 1)[0].strip()
            if normalized_media_type == "text/event-stream":
                accept_event_stream = True
                break

        # Configure certificates based on client_auth_type
        cert = None
        if self.client_auth_type == "mutual":
            # Mutual TLS: Use client certificate
            if self.client_cert and self.client_key:
                cert = (self.client_cert, self.client_key)
            elif self.client_cert:
                cert = self.client_cert
        # For "one-way" TLS, cert remains None (only verify server)
        if self.insecure:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            verify = False
            if url.startswith("http://"):
                url = url.replace("http://", "https://", 1)
        else:
            verify = self.ca_cert if self.ca_cert else False
            if url.startswith("http://") and verify:
                url = url.replace("http://", "https://", 1)

        response = self.session.request(
            method.upper(),
            url,
            json=data,
            headers=default_headers,
            timeout=self.timeout,
            cert=cert,
            verify=verify,
            stream=accept_event_stream,
        )

        try:
            content_type = (response.headers.get("Content-Type") or "").lower()
            if accept_event_stream and "text/event-stream" in content_type:
                result = self._parse_event_stream(response)
            else:
                try:
                    result = response.json() if response.content else {}
                except ValueError:
                    result = response.content

            logging.debug("response: %s\n%s", response.headers, result)

            return {
                "success": response.status_code in self.accept_status,
                "error": result,
                "status_code": response.status_code,
                "data": result,
                "headers": dict(response.headers),
            }

        except RequestException as e:
            logging.debug("HTTP failed: %s", str(e))
            traceback.print_exc()
            return {
                "success": False,
                "error": str(e),
                "status_code": (
                    getattr(e.response, "status_code", None)
                    if hasattr(e, "response")
                    else None
                ),
            }


class YRContext:
    def __init__(self, server_address, ds_address, user=None):
        self.__server_address = server_address
        self.__ds_address = ds_address
        self.__user = user

    def __enter__(self):
        if os.environ.get("YR_RUNTIME_ID"):
            os.environ.pop("YR_WORKING_DIR", None)
            cfg = yr.Config()
            cfg.in_cluster = True
            return yr.init(cfg)
        cfg = yr.Config()
        cfg.log_dir = os.path.join(tempfile.gettempdir(), "yr_sessions", "driver")
        if self.__user:
            cfg.tenant_id = self.__user
        if self.__server_address and self.__ds_address:
            cfg.server_address = self.__server_address
            cfg.ds_address = self.__ds_address
            return yr.init(cfg)
        if self.__server_address:
            cfg.server_address = self.__server_address
            cfg.in_cluster = False
            return yr.init(cfg)
        return yr.init(cfg)

    def __exit__(self, exc_type, exc_value, exc_traceback):
        yr.finalize()
        return False


def get_name_from_info(function_info):
    name = function_info.get("name")
    version = function_info.get("versionNumber")
    return FunctionName(name, version)


def deploy_function(function_json, user):
    http_client = HTTPClient(
        timeout=30,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    url = f"http://{__server_address}/admin/v1/functions"
    headler = {}
    if user:
        headler = {"X-Tenant-Id": user}
    resp = http_client.request(url, function_json, headers=headler, method="POST")
    if resp["success"]:
        return True, resp["data"]["function"]
    else:
        return False, resp


def update_function(function_json, user):
    name = function_json.get("name")
    if not name:
        raise RuntimeError("function name is required to update function.")
    http_client = HTTPClient(
        timeout=30,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    url = f"http://{__server_address}/admin/v1/functions/{name}"
    headler = {}
    if user:
        headler = {"X-Tenant-Id": user}
    resp = http_client.request(url, function_json, headers=headler, method="PUT")
    if resp["success"]:
        return True, resp["data"]["result"]
    else:
        return False, resp


def delete_function(function_name, user):
    http_client = HTTPClient(
        timeout=30,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    function_path = function_name.full_name_no_version()
    url = f"http://{__server_address}/admin/v1/functions/{function_path}?versionNumber={function_name.version}"
    headler = {}
    if user:
        headler = {"X-Tenant-Id": user}
    resp = http_client.request(url, {}, headers=headler, method="DELETE")
    if resp["success"]:
        return True, None
    else:
        return False, resp


def query_function(function_name, user=None):
    http_client = HTTPClient(
        timeout=30,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    if function_name is None:
        url = f"http://{__server_address}/admin/v1/functions"
    else:
        function_path = function_name.full_name_no_version()
        url = f"http://{__server_address}/admin/v1/functions/{function_path}?versionNumber={function_name.version}"
    headler = {}
    if user:
        headler = {"X-Tenant-Id": user}
    resp = http_client.request(url, {}, headers=headler, method="GET")
    if resp["success"]:
        if function_name is None:
            return True, resp["data"]["result"]["functions"]
        else:
            return True, resp["data"]["function"]
    else:
        return False, resp


def query_instances(user=None, page=None, page_size=None, instance_id=None, fields=None):
    """Query instance list for a specific tenant"""
    http_client = HTTPClient(
        timeout=30,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    tenant_id = user if user else "default"
    query_params = {"tenant_id": tenant_id}
    if instance_id is not None:
        query_params["instance_id"] = instance_id
    if page is not None:
        query_params["page"] = page
    if page_size is not None:
        query_params["page_size"] = page_size
    if fields is not None:
        query_params["fields"] = fields
    url = f"http://{__server_address}/api/instances?{urlencode(query_params)}"
    resp = http_client.request(url, {}, method="GET")
    if resp["success"]:
        return True, resp["data"]
    else:
        return False, resp


def get_instance_list(resp):
    """Extract the instance list from paginated or legacy instance responses."""
    if isinstance(resp, dict):
        if "instances" not in resp:
            return None, {"error": "invalid instances response: missing instances"}
        instances = resp["instances"]
    elif isinstance(resp, builtins.list):
        instances = resp
    else:
        return None, {"error": "invalid instances response: expected list or object"}
    if not isinstance(instances, builtins.list):
        return None, {"error": "invalid instances response: instances must be a list"}
    return [instance for instance in instances if isinstance(instance, dict)], None


def query_instance(instance_id, user=None):
    """Query single instance detail by instance ID"""
    tenant_id = user if user else "default"
    ret, resp = query_instances(tenant_id, instance_id=instance_id)
    if not ret:
        return False, resp

    instances, error = get_instance_list(resp)
    if error is not None:
        return False, error

    for instance in instances:
        if instance.get("id") == instance_id:
            return True, instance
    return False, {"error": "instance not found"}


def publish_function(function_name, publish_json, user=None):
    http_client = HTTPClient(
        timeout=30,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    url = f"http://{__server_address}/admin/v1/functions/{function_name.full_name_no_version()}/versions"
    headler = {}
    if user:
        headler = {"X-Tenant-Id": user}
    resp = http_client.request(url, publish_json, headers=headler, method="POST")
    if resp["success"]:
        return True, resp["data"]["function"]
    else:
        return False, resp


def install_requirements(requirements_file, target_dir):
    """
    Install Python dependencies from requirements file to target directory.

    Args:
        requirements_file: Path to requirements.txt file
        target_dir: Target directory to install dependencies

    Returns:
        True if successful, False otherwise
    """
    if not os.path.exists(requirements_file):
        print(f"Requirements file not found: {requirements_file}")
        return False

    print(f"Installing dependencies from {requirements_file} to {target_dir}...")

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-r",
                requirements_file,
                "-t",
                target_dir,
                "--no-warn-script-location",
            ],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"Failed to install dependencies: {result.stderr}")
            return False

        print(f"Successfully installed dependencies to {target_dir}")
        return True

    except Exception as e:
        print(f"Error installing dependencies: {str(e)}")
        return False


def package(backend, code_path, package_format, user=None):
    real_code_path = os.path.realpath(code_path)
    file_name = f"code-{uuid.uuid4().hex}"
    archive_file = os.path.join(tempfile.gettempdir(), file_name)
    if package_format == "zip":
        shutil.make_archive(archive_file, package_format, real_code_path)
    elif package_format == "img":
        result = subprocess.run(
            [
                "mkfs.erofs",
                "-E",
                "noinline_data",
                f"{archive_file}.{package_format}",
                real_code_path,
            ]
        )
        if result.returncode != 0:
            raise click.ClickException(f"mkfs.erofs failed with code {result.returncode}")
    else:
        raise click.ClickException(f"unkown format: {package_format}")
    if backend == "ds":
        with YRContext(__server_address, __ds_address, user):
            with open(f"{archive_file}.{package_format}", "rb") as f:
                yr.kv_write(file_name, f.read())

        package_key = f"ds://{file_name}.{package_format}"
    else:
        raise click.ClickException(f"not support backend: {backend}")
    return real_code_path, package_key


def invoke_function(function_name, payload, headers=None, user=None, timeout=30):
    if headers is None:
        headers = {}
    http_client = HTTPClient(
        timeout=timeout,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
        accept_status=(200, 202),  # Accept 202 for async invoke
    )
    url = f"http://{__server_address}/invocations/{user}/{function_name.invocation_path()}/"
    resp = http_client.request(url, payload, headers=headers, method="POST")
    if resp["success"]:
        return True, resp["data"]
    else:
        return False, resp


def parse_cp_targets(src: str, dst: str) -> Dict[str, Any]:
    """Resolve cp operands where exactly one side is remote."""

    def parse_remote(spec: str) -> Optional[Dict[str, str]]:
        if ":" not in spec:
            return None
        instance, remote_path = spec.split(":", 1)
        if not instance or not remote_path:
            raise ValueError("remote path must use INSTANCE:/path format")
        return {"instance": instance, "path": remote_path}

    src_remote = parse_remote(src)
    dst_remote = parse_remote(dst)
    if (src_remote is None) == (dst_remote is None):
        raise ValueError("exactly one side must be remote, format INSTANCE:/path")

    if dst_remote is not None:
        return {
            "upload": True,
            "instance": dst_remote["instance"],
            "local_path": src,
            "remote_path": dst_remote["path"],
        }

    return {
        "upload": False,
        "instance": src_remote["instance"],
        "local_path": dst,
        "remote_path": src_remote["path"],
    }


def sandbox_runtime_function_id(runtime, tenant_id):
    if runtime.startswith("python3."):
        suffix = f"py{runtime.replace('python', '').replace('.', '')}"
    else:
        suffix = runtime
    return f"sn:cn:yrk:{tenant_id}:function:0-defaultservice-{suffix}:$latest"


def decode_frontend_sandbox_instance_id(data):
    if not isinstance(data, dict):
        return ""
    if isinstance(data.get("instance_id"), str):
        return data["instance_id"]
    sandbox_id = data.get("sandboxId")
    if isinstance(sandbox_id, str):
        return sandbox_id
    inner = data.get("data", "")
    if isinstance(inner, dict):
        instance_id = inner.get("instance_id", "")
        return instance_id if isinstance(instance_id, str) else ""
    if not isinstance(inner, str) or not inner:
        return ""
    try:
        decoded = json.loads(base64.b64decode(inner).decode())
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    instance_id = decoded.get("instance_id", "")
    return instance_id if isinstance(instance_id, str) else ""


SANDBOX_CREATE_STATUS_RUNNING = "running"
SANDBOX_CREATE_STATUS_TIMEOUT = "timeout"
SANDBOX_CREATE_STATUS_FAILED = "failed"


def get_sandbox_create_timeout_seconds(explicit_create_timeout_seconds=None):
    if explicit_create_timeout_seconds:
        return explicit_create_timeout_seconds

    env_timeout = os.environ.get(SANDBOX_CREATE_TIMEOUT_ENV_NAME)
    if not env_timeout:
        return DEFAULT_SANDBOX_CREATE_TIMEOUT_SECONDS

    try:
        value = int(env_timeout)
    except ValueError:
        return DEFAULT_SANDBOX_CREATE_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_SANDBOX_CREATE_TIMEOUT_SECONDS


def should_fallback_to_sdk_for_sandbox_create(resp):
    status_code = resp.get("status_code")
    if status_code in (404, 405, 501):
        return True
    error_text = json.dumps(resp.get("error", resp), ensure_ascii=False).lower()
    fallback_markers = (
        "404 page not found",
        "no route",
        "not support",
        "unsupported",
        "executable path of python3.9",
        "0-defaultservice-py39",
    )
    return any(marker in error_text for marker in fallback_markers)


def should_fallback_to_frontend_for_sandbox_create(error):
    error_text = str(error).lower()
    if "same instance id" in error_text or "already exists" in error_text:
        return False
    fallback_markers = (
        "function not found",
        "not found",
        "not support",
        "unsupported",
        "invalid function",
        "failed to create sandbox",
        "0-defaultservice-py310",
    )
    return any(marker in error_text for marker in fallback_markers)


def parse_sandbox_port_forwardings(ports):
    port_forwardings = []
    for port_forward in ports or ():
        parts = port_forward.split(":")
        if len(parts) == 1:
            protocol = "TCP"
            port_str = parts[0]
        elif len(parts) == 2:
            protocol, port_str = parts
            protocol = protocol.upper()
        else:
            raise ValueError(
                f"invalid port forwarding format: {port_forward}. Expected PORT or PROTOCOL:PORT"
            )

        try:
            port = int(port_str)
        except ValueError as e:
            raise ValueError(f"invalid port number: {port_str}") from e
        if port < 1 or port > 65535:
            raise ValueError(f"port must be in [1, 65535], got {port}")
        if protocol not in ("TCP", "UDP"):
            raise ValueError(f"protocol must be TCP or UDP, got {protocol}")
        port_forwardings.append(yr.PortForwarding(port=port, protocol=protocol))
    return port_forwardings


def build_sandbox_rootfs_option(image):
    return json.dumps(
        {
            "runtime": "runsc",
            "type": "image",
            "imageurl": image,
        }
    )


def build_sandbox_network_option(port_forwardings):
    return json.dumps(
        {
            "portForwardings": [
                {"port": pf.port, "protocol": pf.protocol} for pf in port_forwardings
            ]
        }
    )


def print_sandbox_port_forwarding_urls(instance_id, port_forwardings):
    if not port_forwardings:
        return
    from yr.sandbox.sandbox import _print_gateway_urls

    _print_gateway_urls(instance_id, port_forwardings)


def wait_sandbox_gateway_route(url, timeout=30):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).close()
            return True
        except urllib.error.HTTPError as err:
            if err.code != 404:
                return True
            last_error = err
        except Exception as err:
            last_error = err
        time.sleep(1)
    logging.debug("sandbox gateway route is not ready: url=%s, error=%s", url, last_error)
    return False


def setup_sandbox_tunnel(sandbox_instance, instance_id, upstream, proxy_port):
    tunnel_port = proxy_port - 1
    yr.get(sandbox_instance.start_tunnel_server.invoke(tunnel_port, proxy_port))

    from yr.sandbox.sandbox import _build_gateway_url, _get_gateway_host
    from yr.sandbox.tunnel_client import TunnelClient

    gateway_host = _get_gateway_host()
    tunnel_url = _build_gateway_url(instance_id, tunnel_port, gateway_host)
    tunnel_ws_url = tunnel_url.replace("https://", "wss://").replace("http://", "ws://")
    if not wait_sandbox_gateway_route(tunnel_url, timeout=30):
        logging.warning("sandbox gateway route is not ready, continuing tunnel startup: %s", tunnel_url)
    tunnel_client = TunnelClient(upstream)
    connected = tunnel_client.start(tunnel_ws_url, timeout=30.0)
    return {
        "tunnel_client": tunnel_client,
        "tunnel_ws_url": tunnel_ws_url,
        "proxy_url": f"http://127.0.0.1:{proxy_port}",
        "connected": connected,
    }


def create_sandbox_via_frontend(*args, **kwargs):
    options = SandboxCreateOptions.from_call(*args, **kwargs)
    namespace = options.namespace
    name = options.name
    runtime = options.runtime
    image = options.image
    ports = options.ports
    upstream = options.upstream
    create_timeout_seconds = options.create_timeout_seconds
    if upstream:
        return False, "", {"error": "frontend sandbox create fallback does not support --upstream tunnel"}
    http_client = HTTPClient(
        timeout=SANDBOX_CREATE_HTTP_TIMEOUT,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    url = f"http://{__server_address}/api/sandbox/v1/sandboxes"
    headers = {}
    if __user:
        headers["X-Tenant-ID"] = __user
    headers["Accept"] = "text/event-stream"
    payload = {"name": name, "namespace": namespace, "runtime": runtime}
    create_timeout_seconds = get_sandbox_create_timeout_seconds(create_timeout_seconds)
    payload["createTimeoutSeconds"] = create_timeout_seconds
    if image:
        payload["rootfs"] = image
    if ports:
        payload["ports"] = builtins.list(ports)
    resp = http_client.request(url, payload, headers=headers, method="POST")
    if resp["success"]:
        data = resp["data"]
        if isinstance(data, dict):
            status = data.get("status", "")
            if status in (SANDBOX_CREATE_STATUS_RUNNING, SANDBOX_CREATE_STATUS_TIMEOUT, SANDBOX_CREATE_STATUS_FAILED):
                if status == SANDBOX_CREATE_STATUS_RUNNING:
                    return True, decode_frontend_sandbox_instance_id(data), data
                return False, "", data
            instance_id = decode_frontend_sandbox_instance_id(data)
            if not instance_id:
                return False, "", {"error": "frontend create response missing final result"}
            return True, instance_id, data
        if data is None:
            return False, "", {"error": "frontend create response missing final result"}
        instance_id = decode_frontend_sandbox_instance_id(data)
        return True, instance_id, data
    if should_fallback_to_sdk_for_sandbox_create(resp):
        return False, "", resp
    raise RuntimeError(resp.get("error", resp))


def build_sandbox_sdk_config(runtime):
    tenant_id = __user or "default"
    cfg = yr.Config(
        server_address=__server_address,
        function_id=sandbox_runtime_function_id(runtime, tenant_id),
        in_cluster=False,
    )
    cfg.auth_token = __jwt_token or ""
    cfg.tenant_id = tenant_id
    if use_tls_for_server_address(__server_address):
        cfg.enable_tls = True
    return cfg


def create_sandbox_via_sdk(*args, **kwargs):
    options = SandboxCreateOptions.from_call(*args, **kwargs)
    namespace = options.namespace
    name = options.name
    runtime = options.runtime
    image = options.image
    ports = options.ports
    upstream = options.upstream
    proxy_port = options.proxy_port
    cfg = build_sandbox_sdk_config(runtime)
    yr.init(cfg)
    try:
        opt = yr.InvokeOptions()
        opt.custom_extensions["lifecycle"] = "detached"
        opt.idle_timeout = 60 * 60 * 24 * 7
        opt.cpu = 1000
        opt.memory = 2048
        opt.name = name
        opt.namespace = namespace
        opt.skip_serialize = True
        if image:
            opt.custom_extensions["rootfs"] = build_sandbox_rootfs_option(image)
        port_forwardings = parse_sandbox_port_forwardings(ports)
        if upstream:
            tunnel_port = proxy_port - 1
            port_forwardings.append(yr.PortForwarding(port=tunnel_port, protocol="TCP"))
        opt.port_forwardings = port_forwardings
        if port_forwardings:
            opt.custom_extensions["network"] = build_sandbox_network_option(port_forwardings)

        sandbox_instance = yr.sandbox.SandboxInstance.options(opt).invoke()
        instance_id = resolve_created_sandbox_instance_id(namespace, name, sandbox_instance.real_id)
        if port_forwardings:
            print_sandbox_port_forwarding_urls(instance_id, port_forwardings)
        tunnel_info = None
        if upstream:
            tunnel_info = setup_sandbox_tunnel(sandbox_instance, instance_id, upstream, proxy_port)
        return instance_id, tunnel_info
    finally:
        yr.finalize()


def resolve_created_sandbox_instance_id(namespace, name, instance_id, timeout=30):
    candidates = []
    if instance_id:
        candidates.append(instance_id)
    if namespace and name:
        candidates.append(f"{namespace}-{name}")
    deadline = time.time() + timeout
    while True:
        for candidate in dict.fromkeys(candidates):
            ret, resp = query_instance(candidate, __user)
            if ret:
                return resp.get("id", candidate)
        if time.time() >= deadline:
            break
        time.sleep(1)
    return instance_id


def create_sandbox_auto(*args, **kwargs):
    options = SandboxCreateOptions.from_call(*args, **kwargs)
    namespace = options.namespace
    name = options.name
    runtime = options.runtime
    image = options.image
    ports = options.ports
    upstream = options.upstream
    proxy_port = options.proxy_port
    create_timeout_seconds = options.create_timeout_seconds
    sdk_error = None
    try:
        instance_id, tunnel_info = create_sandbox_via_sdk(
            namespace,
            name,
            runtime,
            image=image,
            ports=ports,
            upstream=upstream,
            proxy_port=proxy_port,
        )
        resolved_id = resolve_created_sandbox_instance_id(namespace, name, instance_id)
        ret, instance = query_instance(resolved_id, __user)
        if ret and sandbox_instance_matches_runtime(instance, runtime) and sandbox_instance_is_usable(instance):
            return resolved_id, tunnel_info
        sdk_error = RuntimeError(
            f"SDK sandbox create returned an instance that is not visible or usable: {resolved_id}"
        )
        if upstream:
            raise sdk_error
    except Exception as sdk_error:
        if not should_fallback_to_frontend_for_sandbox_create(sdk_error):
            raise
        if upstream:
            raise RuntimeError("SDK sandbox create is required for --upstream tunnel") from sdk_error

    supported, instance_id, data = create_sandbox_via_frontend(
        namespace,
        name,
        runtime,
        image=image,
        ports=ports,
        upstream=upstream,
        proxy_port=proxy_port,
        create_timeout_seconds=create_timeout_seconds,
    )
    if supported:
        resolved_id = resolve_created_sandbox_instance_id(namespace, name, instance_id)
        ret, instance = query_instance(resolved_id, __user)
        if ret and sandbox_instance_matches_runtime(instance, runtime) and sandbox_instance_is_usable(instance):
            return resolved_id, None
        instance_detail = json.dumps(instance if ret else data, ensure_ascii=False)
        raise RuntimeError(f"frontend sandbox create produced incompatible instance: {instance_detail}")
    raise RuntimeError(data.get("error", data) if isinstance(data, dict) else data)


def delete_sandbox_via_sdk(sandbox_id, runtime=DEFAULT_SANDBOX_RUNTIME):
    cfg = build_sandbox_sdk_config(runtime)
    yr.init(cfg)
    try:
        yr.runtime_holder.global_runtime.get_runtime().terminate_instance(sandbox_id)
    finally:
        yr.finalize()


def kill_instance_via_frontend(instance_id):
    """Delete an instance through the frontend-exposed instance kill interface.

    Sends a route-less kill request (no proxyID/routeAddress) as JSON to
    ``POST /frontend/v1/instance/kill``. The frontend transcodes the JSON body to
    a core_service.KillRequest protobuf and replies with a JSON KillResponse, so
    the CLI does not deal with protobuf at all. Returns a dict:
        {"success": bool, "code": int, "message": str, "status_code": int}
    ``success`` is True only when the HTTP call succeeded and KillResponse.code
    is ERR_NONE (0). Any non-zero code (including ERR_INSTANCE_NOT_FOUND returned
    when neither the function_proxy nor function_master can locate the instance)
    is treated as a deletion failure.
    """
    http_client = HTTPClient(
        timeout=30,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        insecure=__insecure,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    url = f"http://{__server_address}{FRONTEND_INSTANCE_KILL_PATH}"
    payload = {"instanceID": instance_id, "signal": KILL_SIGNAL_INSTANCE}
    resp = http_client.request(url, payload)
    if not resp.get("success"):
        return {
            "success": False,
            "code": None,
            "message": resp.get("error", "frontend kill request failed"),
            "status_code": resp.get("status_code"),
        }
    data = resp.get("data") or {}
    if not isinstance(data, dict):
        data = {}
    code = data.get("code", KILL_RESPONSE_CODE_OK)
    return {
        "success": code == KILL_RESPONSE_CODE_OK,
        "code": code,
        "message": data.get("message", ""),
        "status_code": resp.get("status_code"),
    }


def get_sandbox_status(instance):
    return (
        instance.get("status")
        or instance.get("state")
        or instance.get("phase")
        or instance.get("instanceStatus")
        or instance.get("statusMessage")
        or "N/A"
    )


def format_resource_quota(value):
    if value is None or value == "":
        return "N/A"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def format_runtime_seconds(value):
    if value is None or value == "":
        return "N/A"
    try:
        seconds = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{seconds}s"


def sandbox_instance_matches_runtime(instance, runtime):
    function = str(instance.get("function", "")).lower()
    if runtime == "python3.10":
        return "0-defaultservice-py310" in function
    return True


def sandbox_instance_is_usable(instance):
    status = get_sandbox_status(instance).lower()
    return status not in ("fatal", "failed", "error")


def sandbox_exists(sandbox_id):
    ret, _ = query_instance(sandbox_id, __user)
    return ret


def wait_until_sandbox_deleted(sandbox_id, timeout=90):
    deadline = time.time() + timeout
    while True:
        if not sandbox_exists(sandbox_id):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


@click.group(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option(
    "--server-address",
    required=False,
    type=str,
    envvar="YR_SERVER_ADDRESS",
    help="YuanRong Server address",
)
@click.option(
    "--ds-address",
    required=False,
    type=str,
    envvar="YR_DS_ADDRESS",
    help="YuanRong DataSystem address",
)
@click.option(
    "--client-cert",
    required=False,
    type=str,
    envvar="YR_CERT_FILE",
    help="Client certificate file path",
)
@click.option(
    "--client-key",
    required=False,
    type=str,
    envvar="YR_PRIVATE_KEY_FILE",
    help="Client private key file path",
)
@click.option(
    "--ca-cert",
    required=False,
    type=str,
    envvar="YR_VERIFY_FILE",
    help="CA certificate file path",
)
@click.option(
    "--insecure",
    is_flag=True,
    default=False,
    envvar="YR_INSECURE",
    help="Skip server TLS certificate verification (connects via HTTPS without cert check)",
)
@click.option(
    "--client-auth-type",
    required=False,
    type=click.Choice(["mutual", "one-way"], case_sensitive=False),
    default="mutual",
    envvar="YR_CLIENT_AUTH_TYPE",
    help="TLS client authentication type: 'mutual' for mTLS (default), 'one-way' for server-only verification",
)
@click.option(
    "--jwt-token",
    required=False,
    type=str,
    envvar="YR_JWT_TOKEN",
    help="JWT token for API authentication (sent in X-Auth header)",
)
@click.option("--log-level", required=False, type=str, default="INFO")
@click.option("--user", required=False, type=str, default="default")
@click.version_option(package_name="openyuanrong-sdk")
def cli(**kwargs):
    """
    run command
    """
    server_address = kwargs["server_address"]
    ds_address = kwargs["ds_address"]
    client_cert = kwargs["client_cert"]
    client_key = kwargs["client_key"]
    ca_cert = kwargs["ca_cert"]
    insecure = kwargs["insecure"]
    client_auth_type = kwargs["client_auth_type"]
    jwt_token = kwargs["jwt_token"]
    log_level = kwargs["log_level"]
    user = kwargs["user"]
    if server_address:
        global __server_address
        __server_address = server_address
    if ds_address:
        global __ds_address
        __ds_address = ds_address
    if client_cert:
        global __client_cert
        __client_cert = client_cert
    if client_key:
        global __client_key
        __client_key = client_key
    if ca_cert:
        global __ca_cert
        __ca_cert = ca_cert
    if insecure:
        global __insecure
        __insecure = insecure
    if client_auth_type:
        global __client_auth_type
        __client_auth_type = client_auth_type.lower()
    if jwt_token:
        global __jwt_token
        __jwt_token = jwt_token
    if user:
        global __user
        __user = user
    logging.basicConfig(level=getattr(logging, log_level.upper(), None))


@cli.command(name="help")
@click.argument("command_name", required=False)
@click.pass_context
def help_command(ctx, command_name):
    """Show help for commands

    Examples:
        yrcli help               # Show general help
        yrcli help deploy        # Show help for deploy command
        yrcli help token-auth    # Show help for token-auth command
    """
    if command_name is None:
        # Show general help
        click.echo(ctx.parent.get_help())
    else:
        # Show help for specific command
        cmd = cli.commands.get(command_name)
        if cmd is None:
            click.echo(f"Error: No such command '{command_name}'.")
            click.echo("\nAvailable commands:")
            for name in sorted(cli.commands.keys()):
                click.echo(f"  {name}")
            raise click.ClickException(f"No such command '{command_name}'.")
        else:
            click.echo(cmd.get_help(ctx))


@cli.command()
@click.option("--backend", required=False, type=str, default="ds")
@click.option("--code-path", required=False, type=str, default=".")
@click.option("--format", "package_format", required=False, type=str, default="zip")
@click.option("--function-json", required=False, type=str, default=None)
@click.option("--skip-package", required=False, type=bool, default=False)
@click.option("--update", required=False, is_flag=True, default=False)
@click.option(
    "-r",
    "--requirements",
    required=False,
    type=str,
    default=None,
    help="Path to requirements.txt file for installing dependencies",
)
def deploy(**kwargs):
    backend = kwargs["backend"]
    code_path = kwargs["code_path"]
    package_format = kwargs["package_format"]
    function_json = kwargs["function_json"]
    skip_package = kwargs["skip_package"]
    update = kwargs["update"]
    requirements = kwargs["requirements"]
    if function_json:
        with open(function_json, "r") as f:
            function_json = json.load(f)
    # Install dependencies if requirements file is provided
    if requirements and not skip_package:
        real_code_path = os.path.realpath(code_path)
        if not install_requirements(requirements, real_code_path):
            raise click.ClickException("Failed to install dependencies. Deployment aborted.")
    if not skip_package:
        real_code_path, package_key = package(backend, code_path, package_format, __user)
        if function_json:
            function_json["storageType"] = "working_dir"
            function_json["codePath"] = package_key
        else:
            print(
                f"""already upload {real_code_path} to {backend}.
export YR_WORKING_DIR={package_key} to set this package.
yrcli clear {package_key} to delete this package.
yrcli download {package_key} to download this package."""
            )
            return
    if function_json:
        name = function_json.get("name")
        if name is None:
            raise click.ClickException(
                "function name is required to deploy function. use <func_name>, "
                "0-<service_name>-<func_name>, or 0@<service_name>@<func_name> "
                "(1-128 chars: letters, digits, '.', and '-')."
            )
        version = "latest" if function_json.get("kind", "faas") == "faas" else "$latest"
        name = FunctionName(name, version)
        query_ret, function_info = query_function(name, __user)
        if query_ret and not update:
            raise click.ClickException(f"function {name} already exists, use --update to update it.")
        if query_ret:
            function_json["revisionId"] = function_info.get("revisionId")
            ret = update_function(function_json, __user)
            if ret[0]:
                name = get_name_from_info(ret[1])
                print(f"succeed to update function: {name}")
            else:
                print(f"failed to update function: {ret[1]['error']}")
        else:
            ret = deploy_function(function_json, __user)
            if ret[0]:
                name = get_name_from_info(ret[1])
                print(f"succeed to deploy function: {name}")
            else:
                print(f"failed to deploy function: {ret[1]}")
    else:
        raise click.ClickException("function json is required to deploy function.")


@cli.command()
@click.option("-f", "--function-name", required=False, type=str, default=None)
@click.option("-v", "--version", required=False, type=str, default=None)
def publish(function_name, version):
    if ":" not in function_name and version is None:
        raise click.ClickException("version is required if function name has no version.")
    if ":" in function_name and version is not None:
        raise click.ClickException("version should not be specified if function name has version.")
    if ":" in function_name:
        version = function_name.split(":")[1]
    function_name = FunctionName(function_name, version)
    publish_json = {}
    query_ret, function_info = query_function(function_name, __user)
    if not query_ret:
        raise click.ClickException(f"function not found: {function_name}")
    publish_json["revisionId"] = function_info.get("revisionId")
    publish_json["kind"] = function_info.get("kind", "faas")
    if version:
        publish_json["versionNumber"] = version
    ret = publish_function(function_name, publish_json, __user)
    if ret[0]:
        print(f"succeed to publish function: {ret[1]}")
    else:
        print(f"failed to publish function: {ret[1]}")


@cli.command()
@click.option(
    "-f",
    "--function-name",
    required=False,
    type=str,
    default=None,
    help="Function name to query",
)
@click.option(
    "-i",
    "--instance-id",
    required=False,
    type=str,
    default=None,
    help="Instance ID to query",
)
def query(function_name, instance_id):
    """Query function or instance details

    Examples:
        yrcli query -f myservice@myfunction:latest       # Query function
        yrcli query -i db6126e0-0000-4000-8000-00faf8d1692b  # Query instance
    """
    if function_name and instance_id:
        raise click.ClickException("Cannot specify both function-name and instance-id")

    if not function_name and not instance_id:
        raise click.ClickException("Must specify either --function-name or --instance-id")

    if function_name:
        # Query function
        function_name = FunctionName(function_name)
        ret, resp = query_function(function_name, __user)
        if ret:
            print(json.dumps(resp, indent=2, ensure_ascii=False))
        else:
            print(f"function not found: {function_name}")

    if instance_id:
        # Query instance
        ret, resp = query_instance(instance_id, __user)
        if ret:
            print(json.dumps(resp, indent=2, ensure_ascii=False))
        else:
            if isinstance(resp, dict) and resp.get("error"):
                print(f"Error: {resp['error']}")
            else:
                print(f"instance not found: {instance_id}")


@cli.command("list")
@click.option("--page", required=False, type=int, default=None, help="Page number for instance listing")
@click.option("--page-size", required=False, type=int, default=None, help="Page size for instance listing")
@click.argument(
    "resource_type",
    type=click.Choice(
        [
            "function",
            "functions",
            "func",
            "fun",
            "instance",
            "instances",
            "inst",
            "ins",
        ],
        case_sensitive=False,
    ),
    default="function",
    required=False,
)
def list_resources(page, page_size, resource_type):
    """List functions or instances

    Examples:
        yrcli list                  # List functions (default)
        yrcli list function         # List functions
        yrcli list instance         # List instances
    """
    if resource_type in ("instance", "instances", "inst", "ins"):
        if page is not None and page <= 0:
            raise click.ClickException("--page must be a positive integer")
        if page is not None and page > QUERY_INSTANCES_MAX_PAGE:
            raise click.ClickException(f"--page must be less than or equal to {QUERY_INSTANCES_MAX_PAGE}")
        if page_size is not None and page_size <= 0:
            raise click.ClickException("--page-size must be a positive integer")
        if page_size is not None and page_size > QUERY_INSTANCES_MAX_PAGE_SIZE:
            raise click.ClickException(f"--page-size must be less than or equal to {QUERY_INSTANCES_MAX_PAGE_SIZE}")
        # List instances
        ret, resp = query_instances(__user, page=page, page_size=page_size, fields="summary")
        instances, error = get_instance_list(resp) if ret else ([], None)
        if ret and error is not None:
            raise click.ClickException(error["error"])
        if ret and len(instances) > 0:
            for instance in instances:
                instance_id = instance.get("id", "N/A")
                tenant_id = instance.get("tenantID", "N/A")
                print(f"{instance_id}\t{tenant_id}")
        else:
            print(f"user {__user} has no instance.")
    elif resource_type in ("function", "functions", "func", "fun"):
        # List functions (default)
        ret, resp = query_function(None, __user)
        if ret and len(resp) > 0:
            for function in resp:
                print(f"{function['name'][2:]}:{function['versionNumber']}")
        else:
            print(f"user {__user} has no function.")


@cli.group("sandbox")
def sandbox():
    """Manage detached sandbox instances.

    Examples:
        yrcli sandbox list
        yrcli sandbox create --namespace aaa --name bbb
        yrcli sandbox query aaa-bbb
        yrcli sandbox delete aaa-bbb
    """


@sandbox.command("create")
@click.option(
    "--namespace",
    required=False,
    type=str,
    help="Namespace for sandbox instance",
)
@click.option(
    "--name",
    required=False,
    type=str,
    help="Name for sandbox instance",
)
@click.option(
    "--runtime",
    required=False,
    type=click.Choice(["python3.10"], case_sensitive=False),
    default=DEFAULT_SANDBOX_RUNTIME,
    show_default=True,
    help="Runtime for sandbox instance",
)
@click.option(
    "--image",
    "--rootfs",
    "image",
    required=False,
    type=str,
    help="Custom sandbox rootfs/container image",
)
@click.option(
    "--port",
    "--port-forward",
    "ports",
    multiple=True,
    type=str,
    help="Forward sandbox port through gateway, format PORT or PROTOCOL:PORT",
)
@click.option(
    "--upstream",
    required=False,
    type=str,
    default=None,
    help="Local upstream address for reverse tunnel, for example 127.0.0.1:8000",
)
@click.option(
    "--proxy-port",
    required=False,
    type=int,
    default=8766,
    show_default=True,
    help="HTTP proxy port inside sandbox when --upstream is set",
)
@click.option(
    "--create-timeout-seconds",
    required=False,
    type=int,
    default=None,
    show_default=False,
    help="Override sandbox create timeout in seconds (overrides YR_SANDBOX_CREATE_TIMEOUT)",
)
def sandbox_create(*args, **kwargs):
    """Create a detached sandbox instance."""
    if args:
        fields = (
            "namespace",
            "name",
            "runtime",
            "image",
            "ports",
            "upstream",
            "proxy_port",
            "create_timeout_seconds",
        )
        values = dict(zip(fields, args))
        values.update(kwargs)
    else:
        values = kwargs
    namespace = values["namespace"]
    name = values["name"]
    runtime = values["runtime"]
    image = values.get("image")
    ports = values.get("ports", ())
    upstream = values.get("upstream")
    proxy_port = values.get("proxy_port", 8766)
    create_timeout_seconds = values.get("create_timeout_seconds")
    if not __server_address:
        raise click.ClickException("server address is required. Use --server-address or set YR_SERVER_ADDRESS.")
    if upstream and (proxy_port < 2 or proxy_port > 65535):
        raise click.ClickException("--proxy-port must be in [2, 65535].")
    namespace = namespace or DEFAULT_SANDBOX_NAMESPACE
    name = name or uuid.uuid4().hex

    try:
        instance_id, data = create_sandbox_auto(
            namespace,
            name,
            runtime,
            image=image,
            ports=ports,
            upstream=upstream,
            proxy_port=proxy_port,
            create_timeout_seconds=create_timeout_seconds,
        )
    except Exception as err:
        raise click.ClickException(
            f"sandbox create failed, name={name}, namespace={namespace}, runtime={runtime}, error={err}"
        ) from err
    if instance_id:
        print(f"sandbox created, instance_id={instance_id}")
    else:
        print(f"sandbox created, response={json.dumps(data, ensure_ascii=False)}")
    if data and data.get("tunnel_client"):
        print(f"tunnel websocket: {data['tunnel_ws_url']}")
        print(f"sandbox upstream proxy: {data['proxy_url']}")
        if data.get("connected"):
            print("tunnel connected, press Ctrl+C to stop the local tunnel client")
        else:
            print("tunnel connecting in background, press Ctrl+C to stop the local tunnel client")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            tunnel_client = data.get("tunnel_client")
            if tunnel_client:
                try:
                    tunnel_client.stop()
                except Exception as e:
                    print(f"failed to stop tunnel client: {e}")
            print("tunnel disconnected")


@sandbox.command("list")
@click.option(
    "--namespace",
    required=False,
    type=str,
    default=None,
    help="Filter by namespace prefix",
)
def sandbox_list(namespace):
    """List sandbox instances."""
    ret, resp = query_instances(__user, fields="summary")
    if not ret:
        raise click.ClickException(f"failed to list instances: {resp.get('error', resp)}")

    instances, error = get_instance_list(resp) if ret else ([], None)
    if error is not None:
        raise click.ClickException(f"failed to list instances: {error['error']}")

    sandboxes = []
    for instance in instances:
        instance_id = instance.get("id", "")
        if not instance_id:
            continue
        if instance_id.startswith("app-"):
            continue
        if "-" not in instance_id:
            continue
        if namespace and not instance_id.startswith(f"{namespace}-"):
            continue
        tenant_id = instance.get("tenantID", "N/A")
        sandboxes.append((
            instance_id,
            tenant_id,
            instance.get("node_id") or "N/A",
            get_sandbox_status(instance),
            instance.get("image") or "N/A",
            format_resource_quota(instance.get("required_cpu")),
            format_resource_quota(instance.get("required_mem")),
            format_resource_quota(instance.get("limit_cpu")),
            format_resource_quota(instance.get("limit_mem")),
            format_runtime_seconds(instance.get("runtime_seconds")),
        ))

    if not sandboxes:
        print("no sandbox instance found")
        return

    headers = (
        "INSTANCE_ID",
        "TENANT_ID",
        "NODE_ID",
        "STATUS",
        "IMAGE",
        "CPU_REQ",
        "MEMORY_REQ",
        "CPU_LIMIT",
        "MEMORY_LIMIT",
        "RUNTIME",
    )
    widths = [
        max(len(header), *(len(str(row[index])) for row in sandboxes))
        for index, header in enumerate(headers)
    ]
    formatter = "  ".join(f"{{:<{width}}}" for width in widths)

    print(formatter.format(*headers).rstrip())
    for sandbox in sandboxes:
        print(formatter.format(*sandbox).rstrip())


@sandbox.command("query")
@click.argument("sandbox_id", type=str)
def sandbox_query(sandbox_id):
    """Query sandbox instance detail by instance id."""
    if not __server_address:
        raise click.ClickException("server address is required. Use --server-address or set YR_SERVER_ADDRESS.")

    ret, resp = query_instance(sandbox_id, __user)
    if ret:
        print(json.dumps(resp, indent=2, ensure_ascii=False))
    else:
        raise click.ClickException(f"sandbox not found: {sandbox_id}")


@sandbox.command("delete")
@click.argument("sandbox_id", type=str)
def sandbox_delete(sandbox_id):
    """Delete (terminate) a sandbox instance via the frontend instance kill interface."""
    if not __server_address:
        raise click.ClickException("server address is required. Use --server-address or set YR_SERVER_ADDRESS.")

    # Delete directly through the frontend-exposed instance kill interface
    # (POST /frontend/v1/instance/kill). The request carries no routing info, so
    # function_proxy resolves the owner locally or forwards to function_master.
    result = kill_instance_via_frontend(sandbox_id)
    if result["success"] and wait_until_sandbox_deleted(sandbox_id):
        print(f"succeed to delete sandbox: {sandbox_id}")
        return

    if not result["success"]:
        error_message = (
            f"failed to delete sandbox {sandbox_id}: "
            f"code={result.get('code')}, {result.get('message')}"
        )
    else:
        error_message = f"failed to delete sandbox {sandbox_id}: instance still exists after delete"
    raise click.ClickException(error_message)


@cli.command()
@click.option("-f", "--function-name", required=True, type=str, default=None)
@click.option("--no-clear-package", is_flag=True, default=False)
@click.option("-v", "--version", required=False, type=str, default="latest")
def delete(function_name, no_clear_package, version):
    function_name = FunctionName(function_name, version)
    if not no_clear_package:
        ret, function_info = query_function(function_name, __user)
        if not ret:
            print(f"function not found.")
            return
        code_path = function_info.get("codePath")
        if code_path and code_path.startswith("ds://"):
            key = code_path.strip("ds://").split(".")[0]
            with YRContext(__server_address, __ds_address, __user):
                yr.kv_del(key)
            print(f"succeed to del package {code_path}")
    ret, _ = delete_function(function_name, __user)
    if not ret:
        print(f"function not found.")
    else:
        print(f"succeed to delete function: {function_name}")


@cli.command
@click.argument("package", type=str)
def clear(package_uri):
    if package_uri.startswith("ds://"):
        key = package_uri.strip("ds://").split(".")[0]
        with YRContext(__server_address, __ds_address, __user):
            yr.kv_del(key)
        print(f"succeed to del {package_uri}")


@cli.command
@click.argument("package", type=str)
def download(package_uri):
    if package_uri.startswith("ds://"):
        key = package_uri.strip("ds://").split(".")[0]
        file_name = package_uri.strip("ds://")
        with YRContext(__server_address, __ds_address, __user):
            with open(file_name, "wb") as f:
                value = yr.kv_get(key)
                f.write(value)
        print(f"save {package_uri} to {file_name}")


@cli.command
@click.option("-f", "--function-name", required=True, type=str, default=None)
@click.option("--payload", required=False, type=str, default=None)
@click.option("--timeout", required=False, type=int, default=30)
@click.option("--header", required=False, type=str, multiple=True)
@click.option("--async", "async_mode", is_flag=True, default=False, help="Invoke function asynchronously")
def invoke(function_name, payload, timeout, header, async_mode):
    headers = {}
    for header_item in header:
        if ":" in header_item:
            key, value = header_item.split(":", 1)
            headers[key.strip()] = value.strip()
    if async_mode:
        headers["X-Invoke-Type"] = "async"
    function_name = FunctionName(function_name)
    if payload:
        try:
            payload_dict = json.loads(payload)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON payload: {e}") from e
    else:
        payload_dict = {}
    ret, resp = invoke_function(
        function_name, payload_dict, headers=headers, user=__user, timeout=timeout
    )
    if ret:
        print(json.dumps(resp, indent=2, ensure_ascii=False))
        if async_mode and "requestId" in resp:
            print(f"\nAsync invocation started. Use 'yrcli result {resp['requestId']}' to check status.")
    else:
        raise click.ClickException(f"failed to invoke function: {resp['error']}")


@cli.command
@click.option("-f", "--function-name", required=True, type=str, default=None)
@click.option("--payload", required=False, type=str, default=None)
@click.option("--timeout", required=False, type=int, default=30)
@click.option("--header", required=False, type=str, multiple=True)
@click.option("--webhook", required=False, type=str, default=None, help="Webhook URL for async callback")
def async_invoke(function_name, payload, timeout, header, webhook):
    """Asynchronously invoke a function and return immediately with a request ID"""
    headers = {}
    for header_item in header:
        if ":" in header_item:
            key, value = header_item.split(":", 1)
            headers[key.strip()] = value.strip()
    # Add async invoke header
    headers["X-Invoke-Type"] = "async"
    if webhook:
        headers["X-Webhook-Url"] = webhook

    function_name = FunctionName(function_name)
    if payload:
        try:
            payload_dict = json.loads(payload)
        except json.JSONDecodeError as e:
            raise click.ClickException(f"Invalid JSON payload: {e}") from e
    else:
        payload_dict = {}

    http_client = HTTPClient(
        timeout=timeout,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        server_name=__server_name,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
        accept_status=(200, 202),  # Accept 202 for async invoke
    )
    # Parse function name for short URL format: [tenant-id@]namespace@function-name[:version]
    func_str = str(function_name)
    parts = func_str.split('@')

    if len(parts) >= 3:
        tenant_id = parts[0]
        namespace = parts[1]
        function_name_only = parts[2].split(':')[0]
    elif len(parts) == 2:
        tenant_id = __user  # Use the user as tenant
        namespace = parts[0]
        function_name_only = parts[1].split(':')[0]
    else:
        # Fallback to old format
        tenant_id = __user
        namespace = func_str.split(':')[0] if ':' in func_str else func_str
        function_name_only = func_str.split(':')[0] if ':' in func_str else func_str

    # Build URL with trailing slash as required by the route
    url = f"http://{__server_address}/invocations/{tenant_id}/{namespace}/{function_name_only}/"
    resp = http_client.request(url, payload_dict, headers=headers, method="POST")
    if resp.get("success"):
        data = resp.get("data", {})
        print(json.dumps({
            "requestId": data.get("requestId", ""),
            "status": "pending",
            "message": "Async invocation started. Use 'async-result' command to get the result."
        }, indent=2, ensure_ascii=False))
    else:
        raise click.ClickException(f"failed to invoke function: {resp.get('error', resp)}")


@cli.command
@click.option("-r", "--request-id", required=True, type=str, help="Request ID from async-invoke")
@click.option("--timeout", required=False, type=int, default=30)
def async_result(request_id, timeout):
    """Get the result of an asynchronous function invocation"""
    http_client = HTTPClient(
        timeout=timeout,
        client_cert=__client_cert,
        client_key=__client_key,
        ca_cert=__ca_cert,
        server_name=__server_name,
        client_auth_type=__client_auth_type,
        jwt_token=__jwt_token,
    )
    url = f"http://{__server_address}/serverless/v1/functions/async-results/{request_id}"
    resp = http_client.request(url, None, headers={}, method="GET")
    if resp.get("success"):
        data = resp.get("data", {})
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        status_code = resp.get("status_code")
        if status_code == 404:
            raise click.ClickException(f"Async result not found for request ID: {request_id}")
        else:
            error_msg = resp.get("error", "Unknown error")
            raise click.ClickException(f"failed to get async result: {error_msg}")


@cli.command(
    "deploy-language-rt",
    context_settings=dict(
        ignore_unknown_options=True,
        allow_extra_args=True,
        allow_interspersed_args=False,
    ),
)
@click.option(
    "--runtime",
    required=False,
    type=click.Choice(["python3.11", "python3.9", "python3.10", "python3.12", "python3.13", "python3.14"]),
    help="Runtime language version",
)
@click.option(
    "--sdk",
    is_flag=True,
    default=False,
    help="sdk language",
)
@click.option("--no-rootfs", is_flag=True, default=False, help="Deploy without rootfs")
@click.option(
    "--function-json",
    required=False,
    type=str,
    default=None,
    help="Path to function JSON file",
)
@click.pass_context
def deploy_language_rt(ctx, runtime, sdk, function_json, no_rootfs):
    """Deploy language runtime executor function

    You can override any JSON field using dot notation, for example:
    yrcli deploy-language-rt --runtime python3.11 --cpu=1000 --memory=1024 --rootfs.storageInfo.accessKey=mykey
    yrcli deploy-language-rt --runtime python3.11 --rootfs.storageInfo.object=rootfs_python3.11.img

    Naming rules:
    - sdk + python3.x -> 0-defaultservice-py3xx (e.g. python3.11 -> py311, python3.9 -> py39)
    - non-sdk -> 0-system-faasExecutorPython3.x

    Hook handler defaults:
    - sdk -> yrlib_handler.*
    - non-sdk -> faas_executor.*
    Or provide a function JSON file directly with --function-json
    """

    # Get extra arguments from context
    overrides = ctx.args

    # Determine which user to use
    current_user = "default"

    # If function_json is provided, load it as base configuration
    if function_json:
        with open(function_json, "r") as f:
            function_json_data = json.load(f)
    else:
        # Validate runtime is provided when not using function_json
        if not runtime:
            raise click.ClickException("--runtime is required when not using --function-json")

        # Generate function name based on runtime and sdk flag
        # sdk + python3.x -> 0-defaultservice-py3xx (e.g. python3.11 -> py311, python3.9 -> py39)
        if sdk and runtime.startswith("python3."):
            runtime_suffix = f"py{runtime.replace('python', '').replace('.', '')}"
            function_name = f"0-defaultservice-{runtime_suffix}"
        else:
            # python3.11 -> 0-system-faasExecutorPython3.11
            runtime_name = runtime[0].upper() + runtime[1:]  # Capitalize first letter
            function_name = f"0-system-faasExecutor{runtime_name}"

        # Build default function configuration based on the template
        if sdk:
            hook_handler = {
                "call": "yrlib_handler.call",
                "checkpoint": "yrlib_handler.checkpoint",
                "init": "yrlib_handler.init",
                "recover": "yrlib_handler.recover",
                "shutdown": "yrlib_handler.shutdown",
                "signal": "yrlib_handler.signal",
            }
        else:
            hook_handler = {
                "call": "faas_executor.faasCallHandler",
                "checkpoint": "faas_executor.faasCheckPointHandler",
                "init": "faas_executor.faasInitHandler",
                "recover": "faas_executor.faasRecoverHandler",
                "shutdown": "faas_executor.faasShutDownHandler",
                "signal": "faas_executor.faasSignalHandler",
            }
        function_json_data = {
            "name": function_name,
            "runtime": runtime,
            "kind": "yrlib",
            "cpu": 600,
            "memory": 512,
            "timeout": 600,
            "storageType": "local",
            "codePath": "/var/task",
            "hookHandler": hook_handler,
            "warmup": "seed",
            "rootfs": {
                "runtime": "runsc",
                "type": "s3",
                "imageurl": f"registry.cn-hangzhou.com",
                "readonly": True,
                "mountpoint": "/var/task/code",
                "storageInfo": {
                    "endpoint": "cn-hangzhou.alipay.aliyun-inc.com",
                    "bucket": "crfs-dev",
                    "object": f"rootfs.img",
                },
            },
        }

    for override in overrides:
        if override.startswith("--"):
            override = override[2:]  # Remove leading --

        if "=" in override:
            key_path, value = override.split("=", 1)
            keys = key_path.split(".")

            # Navigate to the nested dict and set the value
            current = function_json_data
            for key in keys[:-1]:
                if key not in current:
                    current[key] = {}
                current = current[key]

            # Try to parse value as int, bool, or keep as string
            final_key = keys[-1]
            if value.lower() in ("true", "false"):
                current[final_key] = value.lower() == "true"
            elif value.isdigit():
                current[final_key] = int(value)
            else:
                current[final_key] = value

    if no_rootfs:
        if "rootfs" in function_json_data:
            del function_json_data["rootfs"]
        if "warmup" in function_json_data:
            del function_json_data["warmup"]

    # Get function name from the json data
    function_name = function_json_data.get("name")
    if not function_name:
        raise click.ClickException("function name is required in configuration")

    # Check if function already exists
    version = "$latest"
    query_ret, function_info = query_function(FunctionName(function_name, version), current_user)

    if query_ret:
        # Update existing function
        function_json_data["revisionId"] = function_info.get("revisionId")
        ret = update_function(function_json_data, current_user)
        if ret[0]:
            name = get_name_from_info(ret[1])
            print(f"Successfully updated FaaS language runtime function: {name}")
            if runtime:
                print(f"Runtime: {runtime}")
        else:
            raise click.ClickException(f"Failed to update FaaS language runtime function: {ret[1]['error']}")
    else:
        # Deploy new function
        ret = deploy_function(function_json_data, current_user)
        if ret[0]:
            name = get_name_from_info(ret[1])
            print(f"Successfully deployed FaaS language runtime function: {name}")
            if runtime:
                print(f"Runtime: {runtime}")
        else:
            raise click.ClickException(f"Failed to deploy FaaS language runtime function: {ret[1]}")


@cli.command("run-spark")
@click.option(
    "--script",
    required=True,
    type=str,
    help="Path to the Python script to run with Spark",
)
@click.option(
    "--args",
    required=False,
    type=str,
    default="",
    help="Arguments to pass to the Spark job",
)
def run_spark(script, args):
    """Run a Python script with Spark job

    Example:
        yrcli run-spark --script /path/to/script.py
        yrcli run-spark --script /path/to/script.py --args "arg1 arg2 arg3"
    """
    # Validate script path
    if not os.path.exists(script):
        raise click.ClickException(f"Script file not found: {script}")

    # Get absolute path
    script_path = os.path.abspath(script)
    # Build Java command
    cmd = [
        "java",
        "-cp",
        "/home/yuanrong/sparkjob/spark-job.jar",
        f"-Dentry.point.path={script_path}",
    ]

    if args:
        cmd.append(f"-Dargs={args}")

    cmd.append("com.SparkJobExample")

    print(f"Running Spark job with script: {script_path}")
    print(f"Command: {' '.join(cmd)}")

    try:
        # Execute the Java command with stdout/stderr redirected to terminal
        result = subprocess.run(cmd)

        # Check return code
        if result.returncode != 0:
            raise click.ClickException(f"Spark job failed with exit code: {result.returncode}")

    except FileNotFoundError as err:
        raise click.ClickException("Java command not found. Make sure Java is installed and in PATH.") from err
    except Exception as err:
        if isinstance(err, click.ClickException):
            raise
        raise click.ClickException(f"Error executing Spark job: {err}") from err


@cli.command("exec")
@click.option(
    "-i",
    "--stdin",
    required=False,
    type=bool,
    is_flag=True,
    default=False,
    help="Whether to allocate stdin for the instance",
)
@click.option(
    "-t",
    "--tty",
    required=False,
    type=bool,
    is_flag=True,
    default=False,
    help="Whether to allocate a TTY for the instance",
)
@click.option(
    "--verify-server",
    required=False,
    type=bool,
    is_flag=True,
    default=True,
    help="Verify server certificate (default: True)",
)
@click.argument("instance", type=str)
@click.argument("command", type=str)
def exec_command(stdin, tty, verify_server, instance, command):
    use_ssl = (
        use_tls_for_server_address(__server_address)
        or __insecure
        or __ca_cert is not None
        or (__client_cert is not None and __client_key is not None)
    )
    try:
        host, port = __server_address.split(":")
        connection = ExecConnection(
            host=host,
            port=port,
            user=__user,
            use_ssl=use_ssl,
            cert_file=__client_cert,
            key_file=__client_key,
            ca_file=__ca_cert,
            verify_server=verify_server and not __insecure,
            token=__jwt_token,
            quiet=not tty,
        )
        invocation = ExecInvocation(
            instance=instance,
            command=command,
            allocate_tty=tty,
            stdin=stdin,
        )
        asyncio.run(
            run_client(
                connection,
                invocation,
            )
        )
    except KeyboardInterrupt:
        if tty:
            print("\nDisconnected", file=sys.stderr)


@cli.command("cp")
@click.argument("src")
@click.argument("dst")
@click.option(
    "--streaming/--no-streaming",
    "-s/-S",
    default=None,
    help=(
        "Force streaming (gzip-compressed, full-pipeline) or non-streaming mode. "
        "When neither flag is given, the mode is chosen automatically based on "
        "file size and compressibility (Shannon entropy of the first 64 KB)."
    ),
)
def cp(src, dst, streaming):
    """Copy a file to or from an instance via the exec websocket channel.

    Transfer mode is selected automatically by default:

    \b
      * Small files (<256 KB)          → non-streaming (overhead dominates)
      * Incompressible data            → non-streaming (gzip provides no benefit)
      * Large compressible files       → streaming     (gzip reduces channel load)

    Use --streaming / --no-streaming to override the automatic selection.
    """
    if not __server_address:
        raise click.ClickException("--server-address is required")

    try:
        target = parse_cp_targets(src, dst)
    except ValueError as err:
        raise click.ClickException(str(err)) from err

    if target["upload"] and not os.path.exists(target["local_path"]):
        raise click.ClickException(f"local file not found: {target['local_path']}")

    use_ssl = __client_cert is not None and __client_key is not None
    host, port = __server_address.split(":")
    connection = ExecConnection(
        host=host,
        port=port,
        user=__user,
        use_ssl=use_ssl,
        cert_file=__client_cert,
        key_file=__client_key,
        ca_file=__ca_cert,
        verify_server=not __insecure,
        token=__jwt_token,
    )

    if target["upload"]:
        use_streaming = (
            streaming
            if streaming is not None
            else choose_cp_mode(target["local_path"], target["remote_path"], upload=True)
        )
        fn = copy_to_remote_streaming if use_streaming else copy_to_remote
        asyncio.run(
            fn(
                connection,
                CopyRequest(target["instance"], target["local_path"], target["remote_path"]),
            )
        )
        return

    use_streaming = (
        streaming
        if streaming is not None
        else choose_cp_mode(target["local_path"], target["remote_path"], upload=False)
    )
    fn = copy_from_remote_streaming if use_streaming else copy_from_remote
    asyncio.run(
        fn(
            connection,
            CopyRequest(target["instance"], target["local_path"], target["remote_path"]),
        )
    )


@cli.command(
    "runtime_main",
    context_settings=dict(ignore_unknown_options=True, allow_extra_args=True),
)
def runtime_main():
    """Start the runtime main process

    All unknown options will be ignored, allowing the command to run without errors.
    """
    from yr.main.yr_runtime_main import main as yr_runtime_main

    yr_runtime_main()


@cli.command("token-auth")
@click.option("--token", required=True, type=str, help="JWT token to authenticate")
@click.option(
    "--iam-address",
    required=True,
    type=str,
    envvar="YR_IAM_ADDRESS",
    help="YuanRong IAM Server address",
)
def token_auth(token, iam_address):
    """Authenticate/verify a JWT token

    Example:
        yrcli token-auth --iam-address 127.0.0.1:31112 --token "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
    """
    http_client = HTTPClient(timeout=30)
    url = f"http://{iam_address}/iam-server/v1/token/auth"
    headers = {"X-Auth": token}
    resp = http_client.request(url, {}, headers=headers, method="GET")

    if resp["success"]:
        print("Token is valid. Authentication successful.")
    else:
        raise click.ClickException(f"Token authentication failed: {resp.get('error', 'Unknown error')}")


@cli.command("token-require")
@click.option(
    "--tenant-id", required=True, type=str, help="Tenant ID for token generation"
)
@click.option("--ttl", required=False, type=int, help="Token time-to-live in seconds")
@click.option("--role", required=False, type=str, default="developer", help="Role for the token")
@click.option(
    "--frontend-address",
    required=True,
    type=str,
    envvar="YR_SERVER_ADDRESS",
    help="YuanRong frontend address",
)
@click.option(
    "--operator-token",
    required=True,
    type=str,
    help="System tenant 0 developer token used to authorize the request",
)
def token_require(tenant_id, ttl, role, frontend_address, operator_token):
    """Request/generate a new developer JWT token through frontend

    Example:
        yrcli token-require --frontend-address 127.0.0.1:8888 --operator-token "$TOKEN0" --tenant-id user
        yrcli token-require --operator-token "$TOKEN0" --tenant-id user --ttl 3600
    """
    if not operator_token:
        raise click.ClickException("operator token is required")
    if role != "developer":
        raise click.ClickException("role must be developer")
    http_client = HTTPClient(timeout=30)
    url = f"http://{frontend_address}/auth/token/require"
    headers = {"X-Auth": operator_token, "X-Tenant-ID": tenant_id, "X-Role": role}
    if ttl:
        headers["X-TTL"] = str(ttl)

    resp = http_client.request(url, {}, headers=headers, method="GET")

    if resp["success"]:
        # Print token separately for easy copy
        if "X-Auth" in resp["headers"]:
            click.echo(f"Token: {resp['headers']['X-Auth']}")
    else:
        raise click.ClickException(f"Token generation failed: {resp.get('error', 'Unknown error')}")


@cli.command("token-abandon")
@click.option("--tenant-id", required=True, type=str, help="Tenant ID; abandon is currently unsupported")
@click.option(
    "--frontend-address",
    required=True,
    type=str,
    envvar="YR_SERVER_ADDRESS",
    help="YuanRong frontend address",
)
@click.option(
    "--operator-token",
    required=True,
    type=str,
    help="System tenant 0 developer token used to authorize the request",
)
def token_abandon(tenant_id, frontend_address, operator_token):
    """Declare that tenant developer token abandon is not supported.

    Example:
        yrcli token-abandon --frontend-address 127.0.0.1:8888 --operator-token "$TOKEN0" --tenant-id user
    """
    raise click.ClickException("developer token abandon is not supported; token expiration depends on TTL")


def main():
    return cli()


if __name__ == "__main__":
    raise SystemExit(main())
