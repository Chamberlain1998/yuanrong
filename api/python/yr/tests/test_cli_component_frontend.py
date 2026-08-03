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

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from yr.cli.component.base import ComponentLauncher
from yr.cli.component.faas_frontend import FaaSFrontendLauncher
from yr.cli.component.frontend import FrontendLauncher


class ComponentLauncherProbe(ComponentLauncher):
    def get_stdout_log_file(self):
        return self._get_stdout_log_file()


class TestFrontendInitArgsPatch(unittest.TestCase):
    def make_launcher(
        self,
        launcher_cls,
        name,
        *,
        fs_tls_enable=False,
        **frontend_overrides,
    ):
        frontend_values = {
            "ip": "127.0.0.1",
            "port": 8888,
            "scc_enable": "false",
            **frontend_overrides,
        }

        rendered_config = {
            "values": {
                name: frontend_values,
                "fs": {"tls": {"enable": fs_tls_enable, "base_path": ""}},
                "etcd": {
                    "auth_type": "Noauth",
                    "table_prefix": "",
                    "auth": {"base_path": ""},
                },
            },
            name: {
                "args": {},
                "src_init_config_path": "/package/faas/init_frontend_args.json",
                "env": {"INIT_ARGS_FILE_PATH": "/deploy/frontend_init_args.json"},
            },
            "function_proxy": {"args": {"etcd_address": "127.0.0.1:2379"}},
        }
        resolver = SimpleNamespace(rendered_config=rendered_config)
        return launcher_cls(name, resolver, SimpleNamespace(name=name))

    def launcher_cases(self):
        return (
            (FrontendLauncher, "frontend"),
            (FaaSFrontendLauncher, "faas_frontend"),
        )

    def assert_patches_frontend_lease_bypass(self, launcher_cls, name):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = Path(tmpdir) / "init_frontend_args.json"
            dest = Path(tmpdir) / "init_frontend_args_temp.json"
            template.write_text(
                '{"leaseBypass": {frontend_lease_bypass}, "listen": "{faas_frontend_http_ip}"}'
            )

            self.make_launcher(launcher_cls, name).patch_init_frontend_args(
                template, dest
            )

            text = dest.read_text()
            self.assertNotIn("{frontend_lease_bypass}", text)
            config = json.loads(text)
            self.assertFalse(config["leaseBypass"])

    def assert_patches_complete_frontend_template(self, launcher_cls, name):
        # Use a Bazel-owned fixture because the optional frontend submodule is not
        # checked out in every runtime build. Keep this path inside runfiles.
        template = Path(__file__).parent / "testdata/frontend_init_args_template.json"
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "init_frontend_args_temp.json"

            self.make_launcher(launcher_cls, name).patch_init_frontend_args(
                template, dest
            )

            rendered = dest.read_text()
            self.assertNotRegex(rendered, r"\{[A-Za-z_][A-Za-z0-9_]*\}")
            config = json.loads(rendered)
            self.assertEqual(config["functionInvokeBackend"], 0)

    def test_frontend_launcher_sets_default_lease_bypass(self):
        self.assert_patches_frontend_lease_bypass(FrontendLauncher, "frontend")

    def test_faas_frontend_launcher_sets_default_lease_bypass(self):
        self.assert_patches_frontend_lease_bypass(
            FaaSFrontendLauncher, "faas_frontend"
        )

    def test_frontend_tls_and_client_auth_configuration(self):
        cases = (
            (False, {"ssl_enable": True, "client_auth_type": "NoClientCert"}),
            (True, {}),
        )
        for launcher_cls, name in self.launcher_cases():
            for fs_tls_enable, overrides in cases:
                for template_value in (
                    "RequireAndVerifyClientCert",
                    "{frontendClientAuthType}",
                ):
                    with self.subTest(
                        name=name,
                        fs_tls_enable=fs_tls_enable,
                        template_value=template_value,
                    ):
                        self.assert_frontend_security_config(
                            launcher_cls,
                            name,
                            fs_tls_enable,
                            overrides,
                            template_value,
                        )

    def test_frontend_launcher_renders_complete_template(self):
        self.assert_patches_complete_frontend_template(
            FrontendLauncher, "frontend"
        )

    def test_frontend_launcher_reads_component_init_config_path(self):
        launcher = self.make_launcher(FrontendLauncher, "frontend")

        with mock.patch.object(launcher, "patch_init_frontend_args") as patch:
            launcher.prestart_hook()

        patch.assert_called_once_with(
            "/package/faas/init_frontend_args.json",
            Path("/deploy/frontend_init_args.json"),
        )

    def test_faas_frontend_launcher_renders_complete_template(self):
        self.assert_patches_complete_frontend_template(
            FaaSFrontendLauncher, "faas_frontend"
        )

    def assert_frontend_security_config(
        self, launcher_cls, name, fs_tls_enable, overrides, template_value
    ):
        with tempfile.TemporaryDirectory() as tmpdir:
            template = Path(tmpdir) / "init_frontend_args.json"
            dest = Path(tmpdir) / "rendered.json"
            template.write_text(
                '{"httpsConfig": {"httpsEnable": {frontendSslEnable}, '
                f'"clientAuthType": {json.dumps(template_value)}}}'
                "}"
            )
            self.make_launcher(
                launcher_cls,
                name,
                fs_tls_enable=fs_tls_enable,
                **overrides,
            ).patch_init_frontend_args(template, dest)

            config = json.loads(dest.read_text())
            self.assertTrue(config["httpsConfig"]["httpsEnable"])
            self.assertEqual(
                config["httpsConfig"]["clientAuthType"],
                overrides.get(
                    "client_auth_type", "RequireAndVerifyClientCert"
                ),
            )

    def test_component_stdout_log_uses_configured_log_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_root = Path(tmpdir) / "custom-logs"
            resolver = SimpleNamespace(
                rendered_config={
                    "values": {"fs": {"log": {"path": str(log_root)}}}
                }
            )
            launcher = ComponentLauncherProbe("function_proxy", resolver)

            log_file = launcher.get_stdout_log_file()

            self.assertEqual(log_file, log_root / "function_proxy_stdout.log")
            self.assertTrue(log_root.is_dir())

    def test_frontend_values_keep_required_defaults_and_optional_tls_override(self):
        repo_root = Path(__file__).resolve().parents[4]
        section = (
            (repo_root / "api/python/yr/cli/values.toml")
            .read_text()
            .split("[values.frontend]", 1)[1]
            .split("\n[", 1)[0]
        )
        for expected in (
            'bin_path = "{{ values.yr_package_path }}/runtime/service/go/bin/goruntime"',
            'ip = "{{ values.host_ip }}"',
            'port = "{{ 8888|check_port() }}"',
        ):
            self.assertIn(expected, section)
        self.assertNotIn("\nssl_enable =", section)


if __name__ == "__main__":
    unittest.main()
