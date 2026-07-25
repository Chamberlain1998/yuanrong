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

import logging
from pathlib import Path

from yr.cli.component.base import ComponentLauncher

logger = logging.getLogger(__name__)


class FrontendLauncher(ComponentLauncher):
    def prestart_hook(self) -> None:
        logger.info(f"{self.name}: prestart hook executing")
        src = self.resolver.rendered_config["values"][self.name]["config_path"]
        dest = Path(
            self.resolver.rendered_config[self.name]["env"]["INIT_ARGS_FILE_PATH"]
        ).resolve()
        self.patch_init_frontend_args(src, dest)

    def patch_init_frontend_args(self, src: Path, dest: Path) -> None:
        src = Path(src).resolve()
        text = src.read_text()

        config = self.resolver.rendered_config
        values = config["values"]
        faas_args = config[self.component_config.name]["args"]
        faas_values = config["values"]["frontend"]
        # attention: etcd address getting from function_proxy component
        etcd_addrs = config["function_proxy"]["args"]["etcd_address"]
        etcd_addr = etcd_addrs.split(",")
        etcd_addrs = '","'.join(etcd_addr)
        ip_address = faas_values["ip"]
        port = faas_values["port"]
        etcd_auth_type = values["etcd"].get("auth_type", "Noauth")
        etcd_table_prefix = values["etcd"].get("table_prefix", "")

        ssl_enable = str(values["fs"]["tls"].get("enable", "false")).lower()
        scc_enable = str(faas_values.get("scc_enable", "false")).lower()
        ssl_base_path = values["fs"]["tls"].get("base_path", "")
        scc_base_path = faas_args.get("scc_base_path", "")
        etcd_ssl_base_path = values["etcd"]["auth"].get("base_path", "")

        if etcd_auth_type == "TLS":
            etcd_ca = (
                f"{etcd_ssl_base_path}/{values['etcd']['auth'].get('ca_file', '')}"
            )
            etcd_cert = f"{etcd_ssl_base_path}/{values['etcd']['auth'].get('client_cert_file', '')}"
            etcd_key = f"{etcd_ssl_base_path}/{values['etcd']['auth'].get('client_key_file', '')}"
            user_pass_phrase = values["etcd"]["auth"].get("pass_phrase", "")
            pass_phrase = (
                f"{etcd_ssl_base_path}/{user_pass_phrase}" if user_pass_phrase else ""
            )
        else:
            etcd_ca = ""
            etcd_cert = ""
            etcd_key = ""
            pass_phrase = ""

        # sandboxRouter (rrt direct L7 reverse proxy); off by default, opt in via values
        sandbox_router_enable = str(
            faas_values.get("sandbox_router_enable", False)
        ).lower()
        sandbox_router_listen_port = str(
            faas_values.get("sandbox_router_listen_port", 8080)
        )
        sandbox_router_rrt_port = str(faas_values.get("sandbox_router_rrt_port", 50090))
        sandbox_router_enable_jwt = str(
            faas_values.get("sandbox_router_enable_jwt", False)
        ).lower()
        sandbox_router_validate_iam = str(
            faas_values.get("sandbox_router_validate_iam", False)
        ).lower()

        # frontend auth/meta placeholders. The template carries these (added by the
        # Keycloak/Casdoor/metaservice frontend features); process deploy fills them
        # via functionsystem install.sh. The k8s path must fill them too, otherwise
        # they stay literal and the rendered init_frontend_args.json is invalid JSON
        # and the frontend fails to start. k8s default: auth off, IAM/meta address
        # taken from config when present, else empty (still valid JSON).
        frontend_https_enable = ssl_enable
        enable_func_token_auth = str(
            faas_values.get("enable_func_token_auth", False)
        ).lower()
        frontend_lease_bypass = str(
            faas_values.get("frontend_lease_bypass", False)
        ).lower()
        function_invoke_backend = str(faas_values.get("function_invoke_backend", 0))
        auth_enabled = str(faas_values.get("auth_enabled", False)).lower()
        meta_service_address = str(
            faas_args.get("meta_service_address", "")
            or faas_values.get("meta_service_address", "")
        )
        iam_server_address = str(faas_values.get("iam_server_address", ""))
        auth_public_url = str(faas_values.get("auth_public_url", ""))
        auth_internal_url = str(faas_values.get("auth_internal_url", ""))
        auth_realm = str(faas_values.get("auth_realm", ""))
        auth_client_id = str(faas_values.get("auth_client_id", ""))
        auth_client_secret = str(faas_values.get("auth_client_secret", ""))

        replacements = {
            "{sandboxRouterEnable}": sandbox_router_enable,
            "{sandboxRouterListenPort}": sandbox_router_listen_port,
            "{sandboxRouterRrtPort}": sandbox_router_rrt_port,
            "{sandboxRouterEnableJwt}": sandbox_router_enable_jwt,
            "{sandboxRouterValidateIam}": sandbox_router_validate_iam,
            "{etcdAddr}": etcd_addrs,
            "{faas_frontend_http_ip}": ip_address,
            "{faas_frontend_http_port}": str(port),
            "{sslEnable}": ssl_enable,
            "{sccEnable}": scc_enable,
            "{etcdAuthType}": etcd_auth_type,
            "{azPrefix}": etcd_table_prefix,
            "{sslBasePath}": ssl_base_path,
            "{sccBasePath}": scc_base_path,
            "{etcdCAFile}": etcd_ca,
            "{etcdCertFile}": etcd_cert,
            "{etcdKeyFile}": etcd_key,
            "{passphraseFile}": pass_phrase,
            "{iam_server_address}": iam_server_address,
            "{enable_func_token_auth}": enable_func_token_auth,
            "{auth_public_url}": auth_public_url,
            "{auth_internal_url}": auth_internal_url,
            "{auth_realm}": auth_realm,
            "{auth_client_id}": auth_client_id,
            "{auth_client_secret}": auth_client_secret,
            "{auth_enabled}": auth_enabled,
            "{meta_service_address}": meta_service_address,
            "{frontend_lease_bypass}": frontend_lease_bypass,
            "{function_invoke_backend}": function_invoke_backend,
            "{frontendSslEnable}": frontend_https_enable,
        }

        for placeholder, value in replacements.items():
            text = text.replace(placeholder, value)

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
