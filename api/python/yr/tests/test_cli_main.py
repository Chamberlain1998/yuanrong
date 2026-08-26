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

import importlib.util
import io
import logging
from importlib.metadata import PackageNotFoundError
from pathlib import Path
import sys
import types
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock
import unittest

from click.testing import CliRunner


class TestCliMain(unittest.TestCase):
    def load_cli_main_with_stubbed_deps(self):
        main_path = Path(__file__).resolve().parents[1] / "cli" / "main.py"
        spec = importlib.util.spec_from_file_location("yr_cli_main_for_test", main_path)
        module = importlib.util.module_from_spec(spec)

        fake_yr = types.ModuleType("yr")
        fake_yr_cli = types.ModuleType("yr.cli")
        fake_config = types.ModuleType("yr.cli.config")
        fake_config.ConfigResolver = object
        fake_config.render_user_config_template = mock.Mock(
            return_value="[user]\nvalue = 1\n"
        )
        fake_discovery = types.ModuleType("yr.cli.discovery")
        fake_discovery.resolve_overrides_from_function_master = mock.Mock(return_value=("x=y",))

        fake_const = types.ModuleType("yr.cli.const")
        fake_const.DEFAULT_CONFIG_PATH = "/tmp/config.toml"
        fake_const.DEFAULT_CONFIG_TEMPLATE_PATH = "config.toml.jinja"
        fake_const.DEFAULT_VALUES_TOML = "values.toml"
        fake_const.DEFAULT_SESSIONS_DIR = "/tmp/yr_sessions"
        fake_const.SESSION_JSON_PATH = "/tmp/session.json"
        fake_const.StartMode = types.SimpleNamespace(
            MASTER=types.SimpleNamespace(value="master"),
            AGENT=types.SimpleNamespace(value="agent"),
        )

        fake_launcher = types.ModuleType("yr.cli.system_launcher")

        class FakeSystemLauncher:
            calls = []
            wait_for_shutdown_calls = 0

            def __init__(self, *args, **kwargs):
                self.args = args
                self.kwargs = kwargs
                FakeSystemLauncher.calls.append((args, kwargs))

            def start_all(self):
                return True

            def load_components(self):
                pass

            def wait_for_shutdown(self):
                FakeSystemLauncher.wait_for_shutdown_calls += 1

            def health(self):
                return True

            def status(self):
                return True

            def stop_daemon_from_session(self, force=False):
                return True

            def stop_components_from_session(self, force=False):
                return True

        fake_launcher.SystemLauncher = FakeSystemLauncher

        fake_checkpoint = types.ModuleType("yr.cli.checkpoint")
        fake_checkpoint.CheckpointClient = object

        def get_frontend_address_from_session(session_path):
            return None

        fake_checkpoint.get_frontend_address_from_session = get_frontend_address_from_session

        # The module creates a named logger at import time. Keep tests isolated
        # from handlers left by previous imports.
        print_logger = logging.getLogger("print")
        print_logger.handlers.clear()

        with mock.patch.dict(
            sys.modules,
            {
                "yr": fake_yr,
                "yr.cli": fake_yr_cli,
                "yr.cli.config": fake_config,
                "yr.cli.discovery": fake_discovery,
                "yr.cli.const": fake_const,
                "yr.cli.system_launcher": fake_launcher,
                "yr.cli.checkpoint": fake_checkpoint,
            },
        ):
            spec.loader.exec_module(module)
        module.fake_discovery = fake_discovery
        module.fake_config = fake_config
        module.FakeSystemLauncher = FakeSystemLauncher
        return module

    def test_logging_configuration_replaces_existing_handlers(self):
        with mock.patch.object(logging, "basicConfig") as basic_config:
            module = self.load_cli_main_with_stubbed_deps()

        self.assertIsNotNone(module.cli)
        self.assertTrue(basic_config.call_args.kwargs["force"])

    def test_main_uses_yr_program_name(self):
        main = self.load_cli_main_with_stubbed_deps()

        with mock.patch.object(main.cli, "main") as click_main:
            main.main(["-h"])

        click_main.assert_called_once_with(args=["-h"], prog_name="yr", standalone_mode=True)

    def test_version_falls_back_to_core_distribution(self):
        main = self.load_cli_main_with_stubbed_deps()
        runner = CliRunner()

        with (
            mock.patch(
                "importlib.metadata.version",
                side_effect=[
                    PackageNotFoundError("openyuanrong"),
                    "0.7.0+core",
                ],
            ) as package_version,
            mock.patch.object(main.print_logger, "info") as print_version,
        ):
            result = runner.invoke(main.cli, ["--version"])

        self.assertEqual(result.exit_code, 0, result.output)
        print_version.assert_called_once_with("yr version: 0.7.0+core")
        self.assertEqual(
            [call.args[0] for call in package_version.call_args_list],
            ["openyuanrong", "openyuanrong-core"],
        )

    def test_start_master_address_uses_service_discovery(self):
        main = self.load_cli_main_with_stubbed_deps()
        runner = CliRunner()

        result = runner.invoke(
            main.cli,
            ["start", "--master_address", "http://127.0.0.1:8080"],
            obj={},
        )

        self.assertEqual(result.exit_code, 0, result.output)
        main.fake_discovery.resolve_overrides_from_function_master.assert_called_once()
        call_kwargs = main.fake_discovery.resolve_overrides_from_function_master.call_args.kwargs
        self.assertEqual(call_kwargs["function_master_addr"], "http://127.0.0.1:8080")
        self.assertEqual(main.FakeSystemLauncher.calls[-1][1]["overrides"], ("x=y",))

    def test_block_start_waits_for_shutdown(self):
        main = self.load_cli_main_with_stubbed_deps()
        runner = CliRunner()

        result = runner.invoke(main.cli, ["start", "--block", "true"], obj={})

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(main.FakeSystemLauncher.wait_for_shutdown_calls, 1)

    def test_start_port_policy_options(self):
        cases = [
            ([], "RANDOM", 0),
            (["--port-policy", "FIX"], "FIX", 0),
            (["--port_policy", "fix"], "FIX", 0),
            (["--port-policy", "invalid"], None, 2),
        ]
        for args, expected_policy, expected_exit_code in cases:
            with self.subTest(args=args):
                main = self.load_cli_main_with_stubbed_deps()
                result = CliRunner().invoke(main.cli, ["start", *args], obj={})

                self.assertEqual(result.exit_code, expected_exit_code, result.output)
                if expected_policy is not None:
                    self.assertEqual(
                        main.FakeSystemLauncher.calls[-1][1]["port_policy"],
                        expected_policy,
                    )

    def test_start_log_dir_prefix_option(self):
        cases = [
            ([], "/tmp/yr_sessions"),
            (
                ["--log-dir-prefix", "/data/yr_logs"],
                "/data/yr_logs",
            ),
        ]
        for args, expected_sessions_dir in cases:
            with self.subTest(args=args):
                main = self.load_cli_main_with_stubbed_deps()
                result = CliRunner().invoke(main.cli, ["start", *args], obj={})

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(
                    main.FakeSystemLauncher.calls[-1][1]["sessions_dir"],
                    expected_sessions_dir,
                )

    def test_stop_status_health_log_dir_prefix_option(self):
        # stop/status/health must honor --log-dir-prefix so they can locate the
        # session file when start used a non-default prefix.
        cases = [
            ("stop", [], "/tmp/yr_sessions"),
            ("stop", ["--log-dir-prefix", "/data/yr_logs"], "/data/yr_logs"),
            ("status", ["--log-dir-prefix", "/data/yr_logs"], "/data/yr_logs"),
            ("health", ["--log-dir-prefix", "/data/yr_logs"], "/data/yr_logs"),
        ]
        for cmd, args, expected_sessions_dir in cases:
            with self.subTest(cmd=cmd, args=args):
                main = self.load_cli_main_with_stubbed_deps()
                result = CliRunner().invoke(main.cli, [cmd, *args], obj={})

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(
                    main.FakeSystemLauncher.calls[-1][1]["sessions_dir"],
                    expected_sessions_dir,
                )

    def test_start_data_system_enable_option(self):
        cases = [
            ([], ()),
            (
                ["--data-system-enable", "false"],
                ("values.function_agent.data_system_enable=false",),
            ),
            (
                ["--data_system_enable", "true"],
                ("values.function_agent.data_system_enable=true",),
            ),
        ]
        for args, expected_overrides in cases:
            with self.subTest(args=args):
                main = self.load_cli_main_with_stubbed_deps()
                result = CliRunner().invoke(main.cli, ["start", *args], obj={})

                self.assertEqual(result.exit_code, 0, result.output)
                self.assertEqual(
                    main.FakeSystemLauncher.calls[-1][1]["overrides"],
                    expected_overrides,
                )

    def test_named_data_system_enable_option_wins_over_set_override(self):
        main = self.load_cli_main_with_stubbed_deps()

        result = CliRunner().invoke(
            main.cli,
            [
                "start",
                "-s",
                "values.function_agent.data_system_enable=true",
                "--data-system-enable",
                "false",
            ],
            obj={},
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            main.FakeSystemLauncher.calls[-1][1]["overrides"],
            ("values.function_agent.data_system_enable=false",),
        )

    def test_config_render_outputs_stdout_or_file(self):
        main = self.load_cli_main_with_stubbed_deps()
        runner = CliRunner()
        with runner.isolated_filesystem():
            template_path = Path("user.toml.jinja")
            output_path = Path("rendered.toml")
            template_path.write_text("[user]\nvalue = 1\n")

            stdout_result = runner.invoke(
                main.cli,
                ["config", "render", "--template", str(template_path)],
                obj={},
            )
            file_result = runner.invoke(
                main.cli,
                [
                    "config",
                    "render",
                    "--template",
                    str(template_path),
                    "--output",
                    str(output_path),
                ],
                obj={},
            )

            self.assertEqual(stdout_result.output, "[user]\nvalue = 1\n")
            self.assertEqual(file_result.exit_code, 0, file_result.output)
            self.assertEqual(output_path.read_text(), "[user]\nvalue = 1\n")

    def test_user_visible_print_logger_writes_to_stdout(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        print_logger = logging.getLogger("print")
        old_handlers = list(print_logger.handlers)
        old_propagate = print_logger.propagate

        try:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                main = self.load_cli_main_with_stubbed_deps()
                main.print_logger.info("visible output")
        finally:
            for handler in print_logger.handlers:
                if handler not in old_handlers:
                    handler.close()
            print_logger.handlers = old_handlers
            print_logger.propagate = old_propagate

        self.assertEqual(stdout.getvalue(), "visible output\n")
        self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
