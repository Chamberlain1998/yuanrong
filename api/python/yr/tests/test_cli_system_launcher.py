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

from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest import mock

from yr.cli.component.base import ComponentConfig
from yr.cli.component.registry import get_depends_on_overrides
from yr.cli.const import StartMode
import yr.cli.system_launcher as system_launcher_module
from yr.cli.system_launcher import SystemLauncher


class TestableSystemLauncher(SystemLauncher):
    def apply_component_overrides_for_test(self, comp_name, launcher):
        self._apply_component_overrides(comp_name, launcher)

    def get_start_order_for_test(self):
        return self._get_start_order()


class TestCliSystemLauncher(unittest.TestCase):
    def make_launcher(self, name: str):
        return SimpleNamespace(component_config=ComponentConfig(name=name))

    def test_disabled_ds_worker_dependency_is_not_silently_removed(self):
        system_launcher = TestableSystemLauncher.__new__(TestableSystemLauncher)
        system_launcher.mode = StartMode.MASTER
        system_launcher.prepend_char_overrides = {}
        system_launcher.depends_on_overrides = {
            "function_proxy": ["ds_worker", "runtime_launcher"],
        }
        system_launcher.resolver = SimpleNamespace(
            rendered_config={
                "mode": {
                    StartMode.MASTER.value: {
                        "function_proxy": True,
                        "runtime_launcher": True,
                        "ds_worker": False,
                    }
                }
            }
        )

        function_proxy = self.make_launcher("function_proxy")
        runtime_launcher = self.make_launcher("runtime_launcher")
        system_launcher.apply_component_overrides_for_test("function_proxy", function_proxy)
        system_launcher.components = {
            "function_proxy": function_proxy,
            "runtime_launcher": runtime_launcher,
        }

        with self.assertRaisesRegex(ValueError, "depends on unknown component 'ds_worker'"):
            system_launcher.get_start_order_for_test()

    def test_disabled_etcd_dependency_can_be_provided_externally(self):
        system_launcher = TestableSystemLauncher.__new__(TestableSystemLauncher)
        system_launcher.mode = StartMode.MASTER
        system_launcher.prepend_char_overrides = {}
        system_launcher.depends_on_overrides = {
            "function_master": ["etcd"],
        }
        system_launcher.resolver = SimpleNamespace(
            rendered_config={
                "mode": {
                    StartMode.MASTER.value: {
                        "function_master": True,
                        "etcd": False,
                    }
                }
            }
        )

        function_master = self.make_launcher("function_master")
        system_launcher.apply_component_overrides_for_test("function_master", function_master)
        system_launcher.components = {
            "function_master": function_master,
        }

        self.assertEqual(system_launcher.get_start_order_for_test(), ["function_master"])

    def test_function_proxy_does_not_require_runtime_launcher(self):
        self.assertNotIn(
            "runtime_launcher",
            get_depends_on_overrides(StartMode.MASTER)["function_proxy"],
        )
        self.assertNotIn(
            "runtime_launcher",
            get_depends_on_overrides(StartMode.AGENT)["function_proxy"],
        )

    def test_constructor_passes_port_policy_to_config_resolver(self):
        resolver = SimpleNamespace(
            runtime_context={
                "time": "20260801_000000",
                "deploy_path": Path("/tmp/yr-test-session"),
            }
        )
        with (
            mock.patch.object(
                system_launcher_module, "ConfigResolver", return_value=resolver
            ) as config_resolver,
            mock.patch.object(system_launcher_module, "SessionManager"),
            mock.patch.object(SystemLauncher, "_register_component_launchers"),
        ):
            SystemLauncher(
                Path("/tmp/config.toml"),
                Path("/tmp/yr/cli"),
                StartMode.MASTER,
                port_policy="FIX",
            )

        self.assertEqual(config_resolver.call_args.kwargs["port_policy"], "FIX")


if __name__ == "__main__":
    unittest.main()
