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

import unittest
from types import SimpleNamespace

from yr.cli.component.function_proxy import FunctionProxyLauncher


class TestFunctionProxyEnvironment(unittest.TestCase):
    @staticmethod
    def make_launcher(enable_merge_process: bool) -> FunctionProxyLauncher:
        resolver = SimpleNamespace(
            rendered_config={
                "function_proxy": {
                    "args": {"enable_merge_process": enable_merge_process},
                    "env": {
                        "HOST_IP": "proxy-host",
                        "LD_LIBRARY_PATH": "/proxy/lib",
                    },
                },
                "function_agent": {
                    "env": {
                        "HOST_IP": "agent-host",
                        "NODE_ID": "node-1",
                        "GOOGLE_LOG_DIR": "/logs/function-system",
                        "RUNTIME_METRICS_CONFIG_FILE": "/metrics/config.json",
                    }
                },
            }
        )
        return FunctionProxyLauncher("function_proxy", resolver)

    def test_does_not_inherit_function_agent_env_without_merge_process(self):
        env = self.make_launcher(False).prepare_environment({"PATH": "/bin"})

        self.assertEqual(env["HOST_IP"], "proxy-host")
        self.assertNotIn("NODE_ID", env)
        self.assertNotIn("RUNTIME_METRICS_CONFIG_FILE", env)

    def test_inherits_agent_env_and_keeps_proxy_env_on_conflict(self):
        env = self.make_launcher(True).prepare_environment(
            {"PATH": "/bin", "NODE_ID": "ambient-node"}
        )

        self.assertEqual(env["HOST_IP"], "proxy-host")
        self.assertEqual(env["NODE_ID"], "node-1")
        self.assertEqual(env["GOOGLE_LOG_DIR"], "/logs/function-system")
        self.assertEqual(
            env["RUNTIME_METRICS_CONFIG_FILE"], "/metrics/config.json"
        )


if __name__ == "__main__":
    unittest.main()
