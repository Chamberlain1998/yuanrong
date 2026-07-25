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

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


class TestFrontendInitArgsPatch(unittest.TestCase):
    def load_launcher_cls(self, relative_path, class_name):
        repo_root = Path(__file__).resolve().parents[4]
        module_path = repo_root / relative_path

        fake_base = types.ModuleType("yr.cli.component.base")

        class ComponentLauncher:
            def __init__(self, name, resolver, config=None):
                self.name = name
                self.resolver = resolver
                self.component_config = config or SimpleNamespace(name=name)

        fake_base.ComponentLauncher = ComponentLauncher

        with mock.patch.dict(
            sys.modules,
            {
                "yr": types.ModuleType("yr"),
                "yr.cli": types.ModuleType("yr.cli"),
                "yr.cli.component": types.ModuleType("yr.cli.component"),
                "yr.cli.component.base": fake_base,
            },
        ):
            spec = importlib.util.spec_from_file_location(class_name, module_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return getattr(module, class_name)

    def make_launcher(self, launcher_cls, name):
        rendered_config = {
            "values": {
                name: {"ip": "127.0.0.1", "port": 8888, "scc_enable": "false"},
                "fs": {"tls": {"enable": "false", "base_path": ""}},
                "etcd": {
                    "auth_type": "Noauth",
                    "table_prefix": "",
                    "auth": {"base_path": ""},
                },
            },
            name: {"args": {}},
            "function_proxy": {"args": {"etcd_address": "127.0.0.1:2379"}},
        }
        resolver = SimpleNamespace(rendered_config=rendered_config)
        return launcher_cls(name, resolver, SimpleNamespace(name=name))

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
        repo_root = Path(__file__).resolve().parents[4]
        template = repo_root / "frontend/build/init_frontend_args.json"
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "init_frontend_args_temp.json"

            self.make_launcher(launcher_cls, name).patch_init_frontend_args(
                template, dest
            )

            config = json.loads(dest.read_text())
            self.assertEqual(config["functionInvokeBackend"], 0)

    def test_frontend_launcher_sets_default_lease_bypass(self):
        frontend_launcher_cls = self.load_launcher_cls(
            "api/python/yr/cli/component/frontend.py", "FrontendLauncher"
        )
        self.assert_patches_frontend_lease_bypass(frontend_launcher_cls, "frontend")

    def test_faas_frontend_launcher_sets_default_lease_bypass(self):
        faas_frontend_launcher_cls = self.load_launcher_cls(
            "api/python/yr/cli/component/faas_frontend.py", "FaaSFrontendLauncher"
        )
        self.assert_patches_frontend_lease_bypass(
            faas_frontend_launcher_cls, "faas_frontend"
        )

    def test_frontend_launcher_renders_complete_template(self):
        frontend_launcher_cls = self.load_launcher_cls(
            "api/python/yr/cli/component/frontend.py", "FrontendLauncher"
        )
        self.assert_patches_complete_frontend_template(
            frontend_launcher_cls, "frontend"
        )

    def test_faas_frontend_launcher_renders_complete_template(self):
        faas_frontend_launcher_cls = self.load_launcher_cls(
            "api/python/yr/cli/component/faas_frontend.py", "FaaSFrontendLauncher"
        )
        self.assert_patches_complete_frontend_template(
            faas_frontend_launcher_cls, "faas_frontend"
        )


if __name__ == "__main__":
    unittest.main()
