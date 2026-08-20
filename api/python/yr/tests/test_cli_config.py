#!/usr/bin/env python3
# coding=UTF-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import tomllib
except ImportError:
    import tomli as tomllib

from yr.cli.config import ConfigResolver, render_user_config_template


class FixedRuntimeConfigResolver(ConfigResolver):
    def _build_runtime_context(self):
        return _runtime_context(self.yr_package_path)


def _runtime_context(package_path):
    return {
        "yr_package_path": package_path,
        "hostname": "test-host",
        "pid": 123,
        "node_id": "test-node",
        "cpu_millicores": 1000,
        "memory_num_mb": 1024,
        "ip": "127.0.0.1",
        "timestamp": 1.0,
        "time": "20260801_000000",
        "deploy_path": Path("/tmp/yr-test-session"),
        "cwd": Path.cwd(),
        "ld_library_path": "",
        "python_path": "",
    }


class TestCliConfig(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.cli_dir = self.root / "yr" / "cli"
        self.cli_dir.mkdir(parents=True)
        self.config_path = self.root / "config.toml"
        self.template_path = self.root / "config.toml.jinja"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_user_template_renders_env_and_runtime_without_defaults(self):
        self.template_path.write_text(
            "[user]\n"
            'name = {{ env["NAME"] | tojson }}\n'
            'enabled = {{ env.get("ENABLED", "false") | lower }}\n'
            'port = {{ env.get("PORT", "2379") }}\n'
            "hostname = {{ hostname | tojson }}\n"
            "ip = {{ ip | tojson }}\n"
        )
        with (
            mock.patch.dict(os.environ, {"NAME": "akernel"}, clear=True),
            mock.patch(
                "yr.cli.config.build_runtime_context",
                return_value=_runtime_context(self.cli_dir.parent),
            ),
        ):
            rendered = render_user_config_template(self.template_path, self.cli_dir)

        self.assertEqual(
            tomllib.loads(rendered),
            {
                "user": {
                    "name": "akernel",
                    "enabled": False,
                    "port": 2379,
                    "hostname": "test-host",
                    "ip": "127.0.0.1",
                }
            },
        )
        self.assertNotIn("[values", rendered)

    def test_user_template_reports_missing_env_and_invalid_toml(self):
        cases = {
            "missing env": '[user]\nvalue = {{ env["REQUIRED"] | tojson }}\n',
            "invalid TOML": "[user]\nvalue =\n",
        }
        for name, template in cases.items():
            with self.subTest(name=name):
                self.template_path.write_text(template)
                with mock.patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(ValueError, str(self.template_path)):
                        render_user_config_template(self.template_path, self.cli_dir)

    def test_port_policy_and_explicit_ports(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("", 0))
        occupied_port = listener.getsockname()[1]
        try:
            self._write_default_templates(occupied_port)
            random_port = self._resolve("RANDOM")["service"]["port"]
            fixed_port = self._resolve("FIX")["service"]["port"]
            self.config_path.write_text(f"[values.service]\nport = {occupied_port}\n")
            explicit_toml = self._resolve("RANDOM")["service"]["port"]
            explicit_set = self._resolve(
                "RANDOM", (f"values.service.port={occupied_port}",)
            )["service"]["port"]
        finally:
            listener.close()

        self.assertNotEqual(random_port, occupied_port)
        self.assertEqual(fixed_port, occupied_port)
        self.assertEqual(explicit_toml, occupied_port)
        self.assertEqual(explicit_set, occupied_port)

    def test_frontend_ssl_enable_falls_back_to_fs_tls(self):
        cli_dir = Path(__file__).resolve().parents[1] / "cli"
        cases = (
            ("[values.fs.tls]\nenable = false\n", False),
            ("[values.fs.tls]\nenable = true\n", True),
            (
                "[values.fs.tls]\nenable = true\n"
                "[values.frontend]\nssl_enable = false\n",
                False,
            ),
        )
        for config_text, expected in cases:
            with self.subTest(config_text=config_text):
                self.config_path.write_text(config_text)
                config = FixedRuntimeConfigResolver(
                    self.config_path, cli_dir, port_policy="FIX"
                ).rendered_config
                self.assertEqual(config["frontend"]["ssl_enable"], expected)

    def test_lite_scheduler_switch_is_injected_into_frontend_and_scheduler(self):
        cli_dir = Path(__file__).resolve().parents[1] / "cli"
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                value = str(enabled).lower()
                self.config_path.write_text(
                    f"[values.lite_scheduler]\nenable = {value}\n"
                )

                config = FixedRuntimeConfigResolver(
                    self.config_path, cli_dir, port_policy="FIX"
                ).rendered_config

                self.assertEqual(
                    config["frontend"]["env"]["YR_LITE_SCHEDULER_ENABLE"],
                    value,
                )
                self.assertEqual(
                    config["function_scheduler"]["env"][
                        "YR_LITE_SCHEDULER_ENABLE"
                    ],
                    value,
                )

    def test_function_agent_data_system_enable_defaults_to_false(self):
        config = self._resolve_real_config("")

        self.assertIs(config["function_proxy"]["args"]["data_system_enable"], False)
        self.assertIs(config["function_agent"]["args"]["data_system_enable"], False)
        self.assertEqual(
            config["function_proxy"]["env"]["YR_DATASYSTEM_DEPLOYED"], "true"
        )
        self.assertEqual(
            config["function_agent"]["env"]["YR_DATASYSTEM_DEPLOYED"], "true"
        )
        self.assertEqual(
            config["function_agent"]["env"]["YR_BYPASS_DATASYSTEM"], "false"
        )

    def test_function_agent_data_system_enable_override_reaches_both_modes(self):
        for enabled in (False, True):
            with self.subTest(enabled=enabled):
                value = str(enabled).lower()
                config = self._resolve_real_config(
                    f"[values.function_agent]\ndata_system_enable = {value}\n"
                )

                self.assertIs(
                    config["function_proxy"]["args"]["data_system_enable"], enabled
                )
                self.assertIs(
                    config["function_agent"]["args"]["data_system_enable"], enabled
                )
                self.assertEqual(
                    config["function_agent"]["env"]["YR_DATASYSTEM_DEPLOYED"],
                    "true",
                )
                self.assertEqual(
                    config["function_agent"]["env"]["YR_BYPASS_DATASYSTEM"],
                    "false",
                )

    def test_function_agent_data_system_enable_rejects_non_boolean(self):
        with self.assertRaisesRegex(
            ValueError, "values.function_agent.data_system_enable must be a boolean"
        ):
            self._resolve_real_config(
                '[values.function_agent]\ndata_system_enable = "false"\n'
            )

    def test_sandbox_capability_override_is_independent_from_agent_client(self):
        config = self._resolve_real_config(
            "[function_agent.env]\n"
            'YR_DATASYSTEM_DEPLOYED = "false"\n'
            'YR_BYPASS_DATASYSTEM = "true"\n'
        )

        self.assertIs(config["function_agent"]["args"]["data_system_enable"], False)
        self.assertEqual(
            config["function_agent"]["env"]["YR_DATASYSTEM_DEPLOYED"], "false"
        )
        self.assertEqual(
            config["function_agent"]["env"]["YR_BYPASS_DATASYSTEM"], "true"
        )

    def test_local_ip_defaults_to_host_ip_for_backward_compatibility(self):
        config = self._resolve_real_config('[values]\nhost_ip = "192.0.2.10"\n')

        self._assert_dual_address_config(
            config,
            host_ip="192.0.2.10",
            local_ip="192.0.2.10",
        )

    def test_local_ip_routes_runtime_traffic_over_local_bridge(self):
        config = self._resolve_real_config(
            '[values]\nhost_ip = "192.0.2.10"\n',
            ('values.local_ip="172.17.0.1"',),
        )

        self._assert_dual_address_config(
            config,
            host_ip="192.0.2.10",
            local_ip="172.17.0.1",
        )

    def _resolve_real_config(self, config_text, overrides=None):
        self.config_path.write_text(config_text)
        cli_dir = Path(__file__).resolve().parents[1] / "cli"
        return FixedRuntimeConfigResolver(
            self.config_path,
            cli_dir,
            overrides=overrides,
            port_policy="FIX",
        ).rendered_config

    def _assert_dual_address_config(self, config, host_ip, local_ip):
        values = config["values"]
        proxy = config["function_proxy"]
        agent = config["function_agent"]
        proxy_port = values["function_proxy"]["port"]
        proxy_grpc_port = values["function_proxy"]["grpc_listen_port"]
        agent_port = values["function_agent"]["port"]

        self.assertEqual(values["local_ip"], local_ip)
        self.assertEqual(proxy["args"]["address"], f"{host_ip}:{proxy_port}")
        self.assertEqual(proxy["args"]["ip"], local_ip)
        self.assertEqual(
            proxy["args"]["local_scheduler_address"],
            f"{host_ip}:{proxy_port}",
        )
        self.assertEqual(proxy["args"]["proxy_ip"], local_ip)
        self.assertEqual(proxy["args"]["host_ip"], host_ip)
        self.assertEqual(proxy["args"]["agent_address"], f"{host_ip}:{proxy_port}")
        self.assertEqual(proxy["env"]["LOCAL_IP"], local_ip)
        self.assertEqual(proxy["env"]["HOST_IP"], host_ip)

        self.assertEqual(agent["args"]["ip"], host_ip)
        self.assertEqual(
            agent["args"]["local_scheduler_address"],
            f"{host_ip}:{proxy_port}",
        )
        self.assertEqual(agent["args"]["proxy_ip"], local_ip)
        self.assertEqual(agent["args"]["host_ip"], host_ip)
        self.assertEqual(agent["args"]["agent_address"], f"{host_ip}:{agent_port}")
        self.assertEqual(agent["env"]["LOCAL_IP"], local_ip)
        self.assertEqual(agent["env"]["HOST_IP"], host_ip)

        function_system_address = f"{local_ip}:{proxy_grpc_port}"
        self.assertEqual(
            config["function_scheduler"]["args"]["functionSystemAddress"],
            function_system_address,
        )
        self.assertEqual(
            config["frontend"]["args"]["functionSystemAddress"],
            function_system_address,
        )

    def _write_default_templates(self, port):
        (self.cli_dir / "values.toml").write_text(
            f'[values]\ndeploy_path = "/tmp/yr-test-session"\n'
            f'[values.service]\nport = "{{{{ {port}|check_port() }}}}"\n'
        )
        (self.cli_dir / "config.toml.jinja").write_text(
            "[service]\nport = {{ values.service.port }}\n"
        )

    def _resolve(self, policy, overrides=None):
        return FixedRuntimeConfigResolver(
            self.config_path,
            self.cli_dir,
            overrides=overrides,
            port_policy=policy,
        ).rendered_config


if __name__ == "__main__":
    unittest.main()
