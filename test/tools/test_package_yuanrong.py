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

"""Regression tests for package_yuanrong.sh layout contracts."""

import os
import pathlib
import shutil
import subprocess
import tempfile
import textwrap
import unittest
import zipfile


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_SCRIPT = REPO_ROOT / "scripts" / "package_yuanrong.sh"
PARENT_CHART = (
    REPO_ROOT / "deploy" / "k8s" / "charts" / "openyuanrong" / "Chart.yaml"
)
BUILD_SCRIPT = REPO_ROOT / "build.sh"
PREPARE_ST_SCRIPT = REPO_ROOT / "test" / "st" / "prepare_and_start_yr.sh"
ST_TEST_SCRIPT = REPO_ROOT / "test" / "st" / "test.sh"
PYTHON_SETUP = REPO_ROOT / "api" / "python" / "setup.py"
CPP_BUILD = REPO_ROOT / "api" / "cpp" / "BUILD.bazel"
ROOT_BUILD = REPO_ROOT / "build.sh"
SANDBOX_RELEASE_SCRIPT = REPO_ROOT / ".buildkite" / "package_sandbox_release.sh"
CLI_VALUES = REPO_ROOT / "api" / "python" / "yr" / "cli" / "values.toml"
SCRIPT_UTILS = REPO_ROOT / "scripts" / "utils.sh"


def load_package_function_definitions():
    content = PACKAGE_SCRIPT.read_text(encoding="utf-8")
    start = content.index("function resolve_first_match")
    end = content.index("function parse_args")
    return content[start:end]


class PackageYuanrongLayoutTest(unittest.TestCase):
    def test_packaged_datasystem_chart_is_standalone_and_parent_condition_controls_it(self):
        """The parent dependency controls DS without modifying the standalone chart."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            source_chart = temp_path / "source" / "k8s" / "helm_chart" / "datasystem"
            templates = source_chart / "templates"
            templates.mkdir(parents=True)
            (source_chart / "Chart.yaml").write_text(
                "apiVersion: v2\nname: datasystem\nversion: 2.2.0\n",
                encoding="utf-8",
            )
            (source_chart / "values.yaml").write_text(
                "global:\n  namespace: default\n",
                encoding="utf-8",
            )
            (templates / "worker.yaml").write_text(
                "apiVersion: apps/v1\nkind: DaemonSet\nmetadata:\n  name: ds-worker\n",
                encoding="utf-8",
            )

            output_dir = temp_path / "output"
            function_file = temp_path / "package-functions.sh"
            function_file.write_text(load_package_function_definitions(), encoding="utf-8")
            subprocess.run(
                [
                    "bash",
                    "-c",
                    textwrap.dedent(
                        f"""\
                        source "{function_file}"
                        DATASYSTEM_SOURCE_DIR="{temp_path / 'source'}"
                        LEGACY_DATASYSTEM_SOURCE_DIR="{temp_path / 'missing'}"
                        OUTPUT_DIR="{output_dir}"
                        mkdir -p "$OUTPUT_DIR/openyuanrong/deploy/k8s/charts"
                        copy_datasystem_k8s_assets
                        """
                    ),
                ],
                check=True,
            )

            copied_chart = output_dir / "openyuanrong/deploy/k8s/charts/datasystem"
            copied_template = copied_chart / "templates/worker.yaml"
            template_content = copied_template.read_text(encoding="utf-8")
            source_template = (templates / "worker.yaml").read_text(encoding="utf-8")
            self.assertEqual(template_content, source_template)
            self.assertEqual(
                (copied_chart / "values.yaml").read_text(encoding="utf-8"),
                (source_chart / "values.yaml").read_text(encoding="utf-8"),
            )

            rendered = subprocess.run(
                [
                    "helm",
                    "template",
                    "datasystem",
                    str(copied_chart),
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("kind: DaemonSet", rendered)
            self.assertIn("name: ds-worker", rendered)

            parent_chart = temp_path / "parent"
            (parent_chart / "charts").mkdir(parents=True)
            shutil.copytree(copied_chart, parent_chart / "charts/datasystem")
            shutil.copy2(PARENT_CHART, parent_chart / "Chart.yaml")
            (parent_chart / "values.yaml").write_text(
                "global:\n  dataSystem:\n    enabled: true\n    bypass: false\n",
                encoding="utf-8",
            )
            parent_enabled = subprocess.run(
                ["helm", "template", "parent", str(parent_chart)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            parent_disabled = subprocess.run(
                [
                    "helm",
                    "template",
                    "parent",
                    str(parent_chart),
                    "--set",
                    "global.dataSystem.enabled=false",
                    "--set",
                    "global.dataSystem.bypass=true",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("name: ds-worker", parent_enabled)
            self.assertNotIn("name: ds-worker", parent_disabled)

    def test_release_archive_uses_parallel_gzip_with_compatible_fallback(self):
        """Final release archives should preserve layout with or without parallel gzip."""
        package_script = PACKAGE_SCRIPT.read_text(encoding="utf-8")
        utils_script = SCRIPT_UTILS.read_text(encoding="utf-8")

        self.assertIn("create_tar_gz", utils_script)
        self.assertIn(
            'create_tar_gz "openyuanrong-${BUILD_VERSION}.tar.gz" openyuanrong',
            package_script,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            source_dir = temp_path / "openyuanrong"
            source_dir.mkdir()
            executable = source_dir / "bin"
            executable.write_text("release-binary", encoding="utf-8")
            executable.chmod(0o750)
            (source_dir / "binary-link").symlink_to("bin")
            os.link(executable, source_dir / "binary-hardlink")

            fake_bin = temp_path / "fake-bin"
            fake_bin.mkdir()
            invocation_log = temp_path / "parallel-gzip.log"
            fake_pigz = fake_bin / "pigz"
            fake_pigz.write_text(
                "#!/bin/bash\nprintf '%s\\n' \"$*\" > \"$PARALLEL_GZIP_LOG\"\nexec gzip\n",
                encoding="utf-8",
            )
            fake_pigz.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:/usr/bin:/bin",
                    "PACKAGE_COMPRESSION_THREADS": "3",
                    "PARALLEL_GZIP_LOG": str(invocation_log),
                }
            )
            parallel_archive = temp_path / "parallel.tar.gz"
            subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{SCRIPT_UTILS}"; cd "{temp_path}"; '
                    f'create_tar_gz "{parallel_archive}" openyuanrong',
                ],
                check=True,
                env=env,
            )
            self.assertEqual(invocation_log.read_text(encoding="utf-8").strip(), "-p 3")

            fallback_archive = temp_path / "fallback.tar.gz"
            fallback_env = os.environ.copy()
            fallback_env["PATH"] = "/usr/bin:/bin"
            subprocess.run(
                [
                    "bash",
                    "-c",
                    f'source "{SCRIPT_UTILS}"; cd "{temp_path}"; '
                    f'create_tar_gz "{fallback_archive}" openyuanrong',
                ],
                check=True,
                env=fallback_env,
            )

            for archive_name in (parallel_archive, fallback_archive):
                extract_dir = temp_path / f"extract-{archive_name.stem}"
                extract_dir.mkdir()
                subprocess.run(
                    ["tar", "-xzf", str(archive_name), "-C", str(extract_dir)],
                    check=True,
                )
                extracted = extract_dir / "openyuanrong"
                self.assertEqual((extracted / "bin").read_text(encoding="utf-8"), "release-binary")
                self.assertEqual((extracted / "bin").stat().st_mode & 0o777, 0o750)
                self.assertEqual(os.readlink(extracted / "binary-link"), "bin")
                self.assertEqual(
                    (extracted / "bin").stat().st_ino,
                    (extracted / "binary-hardlink").stat().st_ino,
                )

    def test_package_io_diagnostics_cover_unexplained_build_phases(self):
        """Packaging diagnostics must split SDK assembly, runtime tar, and tree finalization."""
        build_script = BUILD_SCRIPT.read_text(encoding="utf-8")
        package_script = PACKAGE_SCRIPT.read_text(encoding="utf-8")

        for label in (
            "bazel-build",
            "python-sdk-pip-bootstrap",
            "python-sdk-wheel-build",
            "python-sdk-wheel-publish",
            "python-runtime-service-assembly",
            "python-runtime-abi-extensions",
            "python-java-runtime-launchers",
            "python-sdk-assembly-total",
            "runtime-tar",
        ):
            self.assertIn(f"[PACKAGE_TIMER] {label}", build_script)

        for label in (
            "runtime-native-restore",
            "java-sdk-native-jar-refresh",
            "package-permission-normalization",
            "package-layout-finalization",
            "openyuanrong-tree",
        ):
            self.assertIn(f"[PACKAGE_TIMER] {label}", package_script)

        self.assertIn("[PACKAGE_SIZE] openyuanrong-tree", package_script)
        self.assertNotIn("pigz", build_script)
        self.assertNotIn("pigz", package_script)

    def test_cpp_sdk_package_creates_openssl_linker_symlinks(self):
        """C++ SDK packaging must expose linker names when versioned OpenSSL libs exist."""
        cpp_build = CPP_BUILD.read_text(encoding="utf-8")

        self.assertIn("for lib_name in ssl crypto; do", cpp_build)
        self.assertIn('link_path="$$CPP_SDK_DIR/lib/lib$${lib_name}.so"', cpp_build)
        self.assertIn('"$$CPP_SDK_DIR/lib/lib$${lib_name}.so.1.1"', cpp_build)
        self.assertIn('"$$CPP_SDK_DIR/lib/lib$${lib_name}.so.3"', cpp_build)
        self.assertIn('rm -f "$$link_path"', cpp_build)
        self.assertIn('ln -s "$$target_name" "$$link_path"', cpp_build)
        self.assertIn('libcurl.so*|libssl.so|libcrypto.so', cpp_build)
        self.assertNotIn("cp -rf $$DATASYSTEM_DIR/lib/* $$CPP_SDK_DIR/lib/", cpp_build)
        self.assertLess(
            cpp_build.index('"$$CPP_SDK_DIR/lib/lib$${lib_name}.so.3"'),
            cpp_build.index('"$$CPP_SDK_DIR/lib/lib$${lib_name}.so.1.1"'),
        )
        self.assertNotIn("external/boringssl/*.so*", cpp_build)
        self.assertNotIn("external/boringssl/*.dylib*", cpp_build)

    def test_dashboard_package_layout_matches_split_wheel_source(self):
        """Dashboard package assembly must populate the directory used by its split wheel."""
        package_script = PACKAGE_SCRIPT.read_text(encoding="utf-8")
        python_setup = PYTHON_SETUP.read_text(encoding="utf-8")

        self.assertIn('dashboard_dir = os.path.join(root_dir, "dashboard")', python_setup)
        self.assertIn('"${OUTPUT_DIR}/openyuanrong/dashboard/"', package_script)
        self.assertNotIn(
            'copy_dashboard_stage_or_extract_tar "${DASHBOARD_STAGE_DIR}" '
            '"${dashboard_filename}" "${OUTPUT_DIR}/openyuanrong/functionsystem/"',
            package_script,
        )

    def test_st_failure_dumps_deploy_diagnostics(self):
        """ST failures should include component logs, not only driver output."""
        test_script = ST_TEST_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("dump_st_failure_diagnostics", test_script)
        self.assertIn("dump_failed_runtime_logs", test_script)
        self.assertIn("grep -hoE 'runtime-[[:alnum:]_.:-]+'", test_script)
        self.assertIn("[gate-st] tail failed runtime", test_script)
        self.assertIn("[gate-st] ST failed, dump deploy diagnostics", test_script)
        self.assertIn("function_master|function_proxy|function_agent", test_script)

        failure_msg = 'echo "----------------------Failed to run ${language} st----------------------"'
        first_failure = test_script.index(failure_msg)
        first_dump = test_script.index("dump_st_failure_diagnostics", first_failure)
        self.assertLess(first_failure, first_dump)

    def test_process_st_installs_runtime_python_requirements_before_deploy(self):
        """Process-mode runtime services must see Python requirements such as protobuf."""
        test_script = ST_TEST_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("install_python_runtime_service_deps", test_script)
        self.assertIn('${YUANRONG_DIR}/runtime/service/python/requirements.txt', test_script)
        self.assertIn('${BASE_DIR}/../../api/python/requirements.txt', test_script)
        self.assertIn('${DEPLOY_PATH}/python_runtime_deps', test_script)
        self.assertIn('python3.9 -m pip install numpy -t "${runtime_deps_dir}"', test_script)
        self.assertIn('export PYTHONPATH="${runtime_deps_dir}', test_script)

        install_call = "\n        install_python_runtime_service_deps\n"
        deploy_call = "\n        bash prepare_and_start_yr.sh"
        install_pos = test_script.index(install_call)
        deploy_pos = test_script.index(deploy_call, install_pos)
        self.assertLess(install_pos, deploy_pos)

    def test_process_st_installs_driver_sdk_before_runtime_python_deps(self):
        """Driver SDK install must not remove the runtime dependency target dir."""
        test_script = ST_TEST_SCRIPT.read_text(encoding="utf-8")

        process_branch = test_script.index('if [[ "$DEPLOY_MODE" == "process" ]]')
        driver_install_pos = test_script.index("\n            install_python_pkg\n", process_branch)
        runtime_install_pos = test_script.index("\n        install_python_runtime_service_deps\n", process_branch)
        deploy_pos = test_script.index("\n        bash prepare_and_start_yr.sh", runtime_install_pos)

        self.assertLess(driver_install_pos, runtime_install_pos)
        self.assertLess(runtime_install_pos, deploy_pos)

    def test_runtime_wheel_keeps_python_metrics_exporters(self):
        """Runtime split wheel must include Python runtime metrics exporter plugins."""
        python_setup = PYTHON_SETUP.read_text(encoding="utf-8")

        self.assertIn("PYTHON_RUNTIME_METRICS_EXPORTERS", python_setup)
        self.assertIn('"libobservability-metrics-file-exporter.so"', python_setup)
        self.assertIn('"libobservability-prometheus-push-exporter.so"', python_setup)
        self.assertIn('"libobservability-prometheus-pull-exporter.so"', python_setup)
        self.assertNotIn('"libobservability-aom-alarm-exporter.so"', python_setup)
        self.assertIn("copy_python_runtime_metrics_exporters(build_lib, runtime_dir)", python_setup)
        self.assertIn(
            'os.path.join(build_lib, "yr/runtime/service/python/yr")',
            python_setup,
        )

    def test_build_stages_python_runtime_abi_extensions(self):
        """Runtime staging must collect ABI extensions from SDK and component outputs."""
        build_script = ROOT_BUILD.read_text(encoding="utf-8")

        self.assertIn("function package_python_runtime_abi_extensions()", build_script)
        self.assertIn("'yr/fnruntime*.so'", build_script)
        self.assertIn("'yr/cpp/lib/libobservability-*-exporter.so'", build_script)
        self.assertIn('"${BASE_DIR}/functionsystem/output/metrics/lib"', build_script)
        for exporter in (
            "libobservability-metrics-file-exporter.so",
            "libobservability-prometheus-push-exporter.so",
            "libobservability-prometheus-pull-exporter.so",
        ):
            self.assertIn(exporter, build_script)
        self.assertIn(
            'package_python_runtime_abi_extensions "$OUTPUT_BASE/runtime/service/python"',
            build_script,
        )

    def test_sandbox_image_downloads_complete_split_wheel_set(self):
        """Control-plane image assembly must download every required split wheel."""
        package_script = SANDBOX_RELEASE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("CONTROLPLANE_WHEEL_PATTERNS=", package_script)
        for wheel_pattern in (
            "openyuanrong-*.whl",
            "openyuanrong_runtime-*.whl",
            "openyuanrong_faas-*.whl",
            "openyuanrong_dashboard-*.whl",
            "openyuanrong_cpp_sdk-*.whl",
            "openyuanrong_functionsystem-*.whl",
            "openyuanrong_datasystem-*.whl",
        ):
            self.assertIn(wheel_pattern, package_script)
        self.assertIn('"${CONTROLPLANE_WHEEL_PATTERN_LIST[@]}"', package_script)
        self.assertIn("download_obs_patterns", package_script)
        self.assertIn("copy_artifacts", package_script)

    def test_cli_values_expose_layout_specific_paths(self):
        """CLI templates must receive paths resolved for split and full layouts."""
        values = CLI_VALUES.read_text(encoding="utf-8")

        self.assertIn('ds_bin = "{{ ds_bin }}"', values)
        self.assertIn('faas_root = "{{ faas_root }}"', values)
        self.assertIn('ld_library_path = "{{ ld_library_path }}"', values)
        self.assertIn('python_path = "{{ python_path }}"', values)
        self.assertIn("[values.fs.metrics]", values)
        self.assertIn('component_grpc_port = "{{ 22774|check_port() }}"', values)
        self.assertIn("[values.runtime_launcher]", values)
        self.assertIn("[values.auth.casdoor]", values)
        self.assertIn("[values.auth.keycloak]", values)

    def test_runtime_datasystem_openssl_linker_symlinks_are_created(self):
        """Runtime datasystem libs must support consumers that link with -lssl/-lcrypto."""
        with tempfile.TemporaryDirectory() as temp_dir:
            lib_dir = pathlib.Path(temp_dir) / "yr" / "datasystem" / "lib"
            lib_dir.mkdir(parents=True)
            (lib_dir / "libssl.so.1.1").write_text("ssl", encoding="utf-8")
            (lib_dir / "libcrypto.so.1.1").write_text("crypto", encoding="utf-8")

            functions_file = pathlib.Path(temp_dir) / "package_functions.sh"
            functions_file.write_text(load_package_function_definitions(), encoding="utf-8")
            test_script = pathlib.Path(temp_dir) / "run.sh"
            test_script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    set -euo pipefail
                    source "{functions_file}"
                    ensure_openssl_linker_symlinks "{lib_dir}"
                    test "$(readlink "{lib_dir / "libssl.so"}")" = "libssl.so.1.1"
                    test "$(readlink "{lib_dir / "libcrypto.so"}")" = "libcrypto.so.1.1"
                    """
                ),
                encoding="utf-8",
            )

            subprocess.run(["bash", str(test_script)], check=True)

    def test_cpp_sdk_package_rebuilds_openssl_links_with_openssl3_first(self):
        """C++ SDK generation should undo stale linker names and prefer OpenSSL 3."""
        cpp_build = CPP_BUILD.read_text(encoding="utf-8")

        self.assertIn('rm -f "$$link_path"', cpp_build)
        self.assertLess(
            cpp_build.index('"$$CPP_SDK_DIR/lib/lib$${lib_name}.so.3"'),
            cpp_build.index('"$$CPP_SDK_DIR/lib/lib$${lib_name}.so.1.1"'),
        )

    def test_package_openssl_links_fall_back_to_openssl11_when_only_11_exists(self):
        """Package-level helper should not require OpenSSL 3 in 1.1-only builds."""
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = pathlib.Path(temp_dir) / "openyuanrong"
            runtime_lib_dir = package_root / "runtime" / "sdk" / "cpp" / "lib"
            runtime_lib_dir.mkdir(parents=True)
            (runtime_lib_dir / "libssl.so.1.1").write_text("runtime-ssl11", encoding="utf-8")
            (runtime_lib_dir / "libcrypto.so.1.1").write_text("runtime-crypto11", encoding="utf-8")

            functions_file = pathlib.Path(temp_dir) / "package_functions.sh"
            functions_file.write_text(load_package_function_definitions(), encoding="utf-8")
            test_script = pathlib.Path(temp_dir) / "run.sh"
            test_script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    set -euo pipefail
                    source "{functions_file}"
                    ensure_package_openssl_linker_symlinks "{package_root}"
                    test "$(readlink "{runtime_lib_dir / "libssl.so"}")" = "libssl.so.1.1"
                    test "$(readlink "{runtime_lib_dir / "libcrypto.so"}")" = "libcrypto.so.1.1"
                    """
                ),
                encoding="utf-8",
            )

            subprocess.run(["bash", str(test_script)], check=True)

    def test_runtime_cpp_sdk_native_dependency_closure_is_restored(self):
        """Runtime C++ SDK must carry native libs needed by libyr-api.so."""
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = pathlib.Path(temp_dir) / "openyuanrong"
            runtime_lib_dir = package_root / "runtime" / "sdk" / "cpp" / "lib"
            functionsystem_lib_dir = package_root / "functionsystem" / "lib"
            datasystem_lib_dir = package_root / "datasystem" / "sdk" / "cpp" / "lib"
            runtime_lib_dir.mkdir(parents=True)
            functionsystem_lib_dir.mkdir(parents=True)
            datasystem_lib_dir.mkdir(parents=True)

            (runtime_lib_dir / "libyr-api.so").write_text("yr", encoding="utf-8")
            (runtime_lib_dir / "libyr-api.so.1").write_text("yr-versioned", encoding="utf-8")
            (runtime_lib_dir / "libfunctionsdk.so").write_text("functionsdk", encoding="utf-8")
            (runtime_lib_dir / "libfunctionsdk.so.1.0.0").write_text("functionsdk-versioned", encoding="utf-8")
            (runtime_lib_dir / "libcurl.so").symlink_to("datasystem-libcurl.so")
            (runtime_lib_dir / "datasystem-libcurl.so").write_text("ds-curl", encoding="utf-8")
            (runtime_lib_dir / "libssl.so").symlink_to("libssl.so.1.1")
            (runtime_lib_dir / "libssl.so.1.1").write_text("ssl11", encoding="utf-8")
            (functionsystem_lib_dir / "libcurl.so.4.8.0").write_text("curl", encoding="utf-8")
            (functionsystem_lib_dir / "libcurl.so.4").symlink_to("libcurl.so.4.8.0")
            (functionsystem_lib_dir / "libcurl.so").symlink_to("libcurl.so.4")
            (functionsystem_lib_dir / "libssl.so.1.1").write_text("ssl11-from-fs", encoding="utf-8")
            (functionsystem_lib_dir / "libssl.so").symlink_to("libssl.so.1.1")
            (functionsystem_lib_dir / "libfunctionsdk.so").write_text("wrong", encoding="utf-8")
            (functionsystem_lib_dir / "libfunctionsdk.so.1.0.0").write_text("wrong-versioned", encoding="utf-8")
            (functionsystem_lib_dir / "libnot_needed.so").write_text("unused", encoding="utf-8")
            (datasystem_lib_dir / "libdatasystem.so").write_text("ds", encoding="utf-8")
            (datasystem_lib_dir / "libgrpc.so.42.0.0").write_text("grpc", encoding="utf-8")
            (datasystem_lib_dir / "libgrpc.so.42").symlink_to("libgrpc.so.42.0.0")
            (datasystem_lib_dir / "libgrpc.so").symlink_to("libgrpc.so.42")
            (datasystem_lib_dir / "libyr-api.so.1").write_text("wrong-yr-versioned", encoding="utf-8")
            datasystem_cmake_dir = datasystem_lib_dir / "cmake" / "Datasystem"
            datasystem_cmake_dir.mkdir(parents=True)
            (datasystem_cmake_dir / "DatasystemConfig.cmake").write_text(
                "datasystem-cmake", encoding="utf-8"
            )
            functions_file = pathlib.Path(temp_dir) / "package_functions.sh"
            functions_file.write_text(load_package_function_definitions(), encoding="utf-8")
            test_script = pathlib.Path(temp_dir) / "run.sh"
            test_script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    set -euo pipefail
                    source "{functions_file}"
                    restore_runtime_cpp_sdk_native_libs "{package_root}"
                    test -e "{runtime_lib_dir / "libcurl.so.4.8.0"}"
                    test "$(readlink "{runtime_lib_dir / "libcurl.so"}")" = "libcurl.so.4"
                    test "$(readlink "{runtime_lib_dir / "libcurl.so.4"}")" = "libcurl.so.4.8.0"
                    test "$(readlink "{runtime_lib_dir / "libssl.so"}")" = "libssl.so.1.1"
                    test "$(cat "{runtime_lib_dir / "libssl.so.1.1"}")" = "ssl11-from-fs"
                    test -e "{runtime_lib_dir / "libdatasystem.so"}"
                    test -e "{runtime_lib_dir / "libgrpc.so.42.0.0"}"
                    test "$(readlink "{runtime_lib_dir / "libgrpc.so"}")" = "libgrpc.so.42"
                    test "$(cat "{runtime_lib_dir / "cmake" / "Datasystem" / "DatasystemConfig.cmake"}")" = "datasystem-cmake"
                    test "$(cat "{runtime_lib_dir / "libnot_needed.so"}")" = "unused"
                    test "$(cat "{runtime_lib_dir / "libyr-api.so"}")" = "yr"
                    test "$(cat "{runtime_lib_dir / "libyr-api.so.1"}")" = "yr-versioned"
                    test "$(cat "{runtime_lib_dir / "libfunctionsdk.so"}")" = "functionsdk"
                    test "$(cat "{runtime_lib_dir / "libfunctionsdk.so.1.0.0"}")" = "functionsdk-versioned"
                    """
                ),
                encoding="utf-8",
            )

            subprocess.run(["bash", str(test_script)], check=True)

    def test_runtime_java_native_dependency_closure_is_restored(self):
        """Runtime Java service and SDK jar must carry JNI native deps."""
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = pathlib.Path(temp_dir) / "openyuanrong"
            java_lib_dir = package_root / "runtime" / "service" / "java" / "lib"
            java_sdk_dir = package_root / "runtime" / "sdk" / "java"
            functionsystem_lib_dir = package_root / "functionsystem" / "lib"
            datasystem_lib_dir = package_root / "datasystem" / "sdk" / "cpp" / "lib"
            native_dir_in_jar = "native/aarch64"
            java_lib_dir.mkdir(parents=True)
            java_sdk_dir.mkdir(parents=True)
            functionsystem_lib_dir.mkdir(parents=True)
            datasystem_lib_dir.mkdir(parents=True)

            (java_lib_dir / "libruntime_lib_jni.so").write_text("jni", encoding="utf-8")
            (java_lib_dir / "libcurl.so").symlink_to("system-libcurl.so")
            (java_lib_dir / "system-libcurl.so").write_text("system-curl", encoding="utf-8")
            (functionsystem_lib_dir / "libcurl.so.4.8.0").write_text("curl", encoding="utf-8")
            (functionsystem_lib_dir / "libcurl.so.4").symlink_to("libcurl.so.4.8.0")
            (functionsystem_lib_dir / "libcurl.so").symlink_to("libcurl.so.4")
            (functionsystem_lib_dir / "libssl.so.1.1").write_text("ssl", encoding="utf-8")
            (functionsystem_lib_dir / "libcrypto.so.1.1").write_text("crypto", encoding="utf-8")
            (datasystem_lib_dir / "libdatasystem.so").write_text("ds", encoding="utf-8")
            (datasystem_lib_dir / "libbrpc.so").write_text("brpc", encoding="utf-8")
            (datasystem_lib_dir / "libaddress_sorting.so.42.0.0").write_text(
                "address", encoding="utf-8"
            )
            (datasystem_lib_dir / "libaddress_sorting.so.42").symlink_to(
                "libaddress_sorting.so.42.0.0"
            )
            (datasystem_lib_dir / "libnot_needed.so").write_text("unused", encoding="utf-8")
            datasystem_cmake_dir = datasystem_lib_dir / "cmake" / "Datasystem"
            datasystem_cmake_dir.mkdir(parents=True)
            (datasystem_cmake_dir / "DatasystemConfig.cmake").write_text(
                "datasystem-cmake", encoding="utf-8"
            )
            jar_path = java_sdk_dir / "yr-api-sdk-9.9.9.jar"
            with zipfile.ZipFile(jar_path, "w") as jar:
                jar.writestr(f"{native_dir_in_jar}/libruntime_lib_jni.so", "jni")
                jar.writestr(f"{native_dir_in_jar}/so.properties", "libruntime_lib_jni.so=old\n")

            functions_file = pathlib.Path(temp_dir) / "package_functions.sh"
            functions_file.write_text(load_package_function_definitions(), encoding="utf-8")
            test_script = pathlib.Path(temp_dir) / "run.sh"
            test_script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    set -euo pipefail
                    source "{functions_file}"
                    BASE_DIR="{REPO_ROOT / "scripts"}"
                    restore_runtime_java_service_native_libs "{package_root}"
                    ensure_package_openssl_linker_symlinks "{package_root}"
                    update_runtime_java_sdk_native_jar "{package_root}" 2>"{pathlib.Path(temp_dir) / "java_sdk_warnings.log"}"
                    test "$(readlink "{java_lib_dir / "libcurl.so"}")" = "libcurl.so.4"
                    test "$(readlink "{java_lib_dir / "libcurl.so.4"}")" = "libcurl.so.4.8.0"
                    test "$(cat "{java_lib_dir / "libcurl.so.4.8.0"}")" = "curl"
                    [[ "$(readlink "{java_lib_dir / "libssl.so"}")" == *"libssl.so.1.1" ]]
                    [[ "$(readlink "{java_lib_dir / "libcrypto.so"}")" == *"libcrypto.so.1.1" ]]
                    test "$(cat "{java_lib_dir / "libdatasystem.so"}")" = "ds"
                    test "$(cat "{java_lib_dir / "cmake" / "Datasystem" / "DatasystemConfig.cmake"}")" = "datasystem-cmake"
                    test "$(cat "{java_lib_dir / "libnot_needed.so"}")" = "unused"
                    """
                ),
                encoding="utf-8",
            )

            subprocess.run(["bash", str(test_script)], check=True)

            with zipfile.ZipFile(jar_path) as jar:
                names = set(jar.namelist())
                self.assertIn(f"{native_dir_in_jar}/libruntime_lib_jni.so", names)
                self.assertIn(f"{native_dir_in_jar}/libcurl.so.4.8.0", names)
                self.assertIn(f"{native_dir_in_jar}/libcurl.so.4", names)
                self.assertIn(f"{native_dir_in_jar}/libssl.so.1.1", names)
                self.assertIn(f"{native_dir_in_jar}/libcrypto.so.1.1", names)
                self.assertIn(f"{native_dir_in_jar}/libdatasystem.so", names)
                self.assertIn(f"{native_dir_in_jar}/libbrpc.so", names)
                self.assertIn(f"{native_dir_in_jar}/libaddress_sorting.so.42.0.0", names)
                self.assertNotIn(f"{native_dir_in_jar}/libaddress_sorting.so.42", names)
                self.assertNotIn(f"{native_dir_in_jar}/libnot_needed.so", names)
                self.assertEqual(jar.read(f"{native_dir_in_jar}/libcurl.so.4.8.0"), b"curl")
                self.assertEqual(jar.read(f"{native_dir_in_jar}/libcurl.so.4"), b"curl")
                self.assertEqual(jar.read(f"{native_dir_in_jar}/libaddress_sorting.so.42.0.0"), b"address")
                properties = jar.read(f"{native_dir_in_jar}/so.properties").decode()
                self.assertIn("libruntime_lib_jni.so=", properties)
                self.assertIn("libcurl.so.4.8.0=", properties)
                self.assertIn("libssl.so.1.1=", properties)
                self.assertNotIn("old", properties)

    def test_runtime_service_language_native_dirs_share_native_restore(self):
        """All runtime service native dirs should use the same native source restore."""
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = pathlib.Path(temp_dir) / "openyuanrong"
            cpp_lib_dir = package_root / "runtime" / "service" / "cpp" / "lib"
            go_bin_dir = package_root / "runtime" / "service" / "go" / "bin"
            python_service_dir = package_root / "runtime" / "service" / "python" / "yr"
            python_ds_lib_dir = package_root / "runtime" / "service" / "python" / "yr" / "datasystem" / "lib"
            functionsystem_lib_dir = package_root / "functionsystem" / "lib"
            datasystem_lib_dir = package_root / "datasystem" / "sdk" / "cpp" / "lib"
            for target_dir in (cpp_lib_dir, go_bin_dir, python_service_dir, python_ds_lib_dir):
                target_dir.mkdir(parents=True)
            functionsystem_lib_dir.mkdir(parents=True)
            datasystem_lib_dir.mkdir(parents=True)

            (cpp_lib_dir / "libcpp_runtime.so").write_text("cpp", encoding="utf-8")
            (go_bin_dir / "libgo_runtime.so").write_text("go", encoding="utf-8")
            (python_ds_lib_dir / "libpython_runtime.so").write_text("python", encoding="utf-8")
            (functionsystem_lib_dir / "libshared_dep.so.1.0.0").write_text("shared", encoding="utf-8")
            (functionsystem_lib_dir / "libshared_dep.so.1").symlink_to("libshared_dep.so.1.0.0")
            (functionsystem_lib_dir / "libshared_dep.so").symlink_to("libshared_dep.so.1")
            (datasystem_lib_dir / "libnested_dep.so").write_text("nested", encoding="utf-8")
            (datasystem_lib_dir / "libnot_needed.so").write_text("unused", encoding="utf-8")

            functions_file = pathlib.Path(temp_dir) / "package_functions.sh"
            functions_file.write_text(load_package_function_definitions(), encoding="utf-8")
            test_script = pathlib.Path(temp_dir) / "run.sh"
            test_script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    set -euo pipefail
                    source "{functions_file}"
                    restore_runtime_service_native_libs "{package_root}"
                    for target_dir in "{cpp_lib_dir}" "{go_bin_dir}" "{python_service_dir}" "{python_ds_lib_dir}"; do
                        test "$(readlink "$target_dir/libshared_dep.so")" = "libshared_dep.so.1"
                        test "$(readlink "$target_dir/libshared_dep.so.1")" = "libshared_dep.so.1.0.0"
                        test "$(cat "$target_dir/libshared_dep.so.1.0.0")" = "shared"
                        test "$(cat "$target_dir/libnested_dep.so")" = "nested"
                        test "$(cat "$target_dir/libnot_needed.so")" = "unused"
                    done
                    """
                ),
                encoding="utf-8",
            )

            subprocess.run(["bash", str(test_script)], check=True)

    def test_java_native_loader_knows_packaged_libcurl(self):
        """Java JNI loader must load packaged libcurl before libruntime_lib_jni."""
        load_util = (
            REPO_ROOT / "api/java/function-common/src/main/java/org/yuanrong/jni/LoadUtil.java"
        ).read_text(encoding="utf-8")
        package_script = PACKAGE_SCRIPT.read_text(encoding="utf-8")

        self.assertIn('{"libcurl.so.4.8.0", "libcurl.so.4"}', load_util)
        self.assertLess(
            load_util.index('{"libcurl.so.4.8.0", "libcurl.so.4"}'),
            load_util.index('{"libruntime_lib_jni.so", "libruntime_lib_jni.dylib"}'),
        )
        self.assertIn(
            'copy_java_sdk_loader_libs_to_native_dir "${native_dir}"',
            package_script,
        )
        self.assertNotIn("java_sdk_native_libs=(", package_script)

    def test_openssl_linker_symlinks_warn_when_source_libs_are_missing(self):
        """Missing OpenSSL sources should be visible in package logs."""
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_lib_dir = pathlib.Path(temp_dir) / "runtime" / "sdk" / "cpp" / "lib"
            source_lib_dir = pathlib.Path(temp_dir) / "functionsystem" / "lib"
            runtime_lib_dir.mkdir(parents=True)
            source_lib_dir.mkdir(parents=True)

            functions_file = pathlib.Path(temp_dir) / "package_functions.sh"
            functions_file.write_text(load_package_function_definitions(), encoding="utf-8")
            stderr_file = pathlib.Path(temp_dir) / "stderr.log"
            test_script = pathlib.Path(temp_dir) / "run.sh"
            test_script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    set -euo pipefail
                    source "{functions_file}"
                    ensure_openssl_linker_symlinks_from_sources \\
                        "{runtime_lib_dir}" \\
                        "{source_lib_dir}" 2>"{stderr_file}"
                    grep -Fq "Warning: skip {runtime_lib_dir / "libssl.so"}, no libssl.so.* found in candidate dirs: {source_lib_dir}" "{stderr_file}"
                    grep -Fq "Warning: skip {runtime_lib_dir / "libcrypto.so"}, no libcrypto.so.* found in candidate dirs: {source_lib_dir}" "{stderr_file}"
                    test ! -e "{runtime_lib_dir / "libssl.so"}"
                    test ! -e "{runtime_lib_dir / "libcrypto.so"}"
                    """
                ),
                encoding="utf-8",
            )

            subprocess.run(["bash", str(test_script)], check=True)

    def test_python_service_restore_skips_metrics_exporters(self):
        """Restore of the Python service must not copy metrics exporter plugins.

        The Python runtime places its exporter plugins during the build stage
        (package_python_runtime_abi_extensions / wheel assembly), so the package
        restore must neither overwrite nor backfill exporters from
        functionsystem/lib, which is a different build chain. Other services
        (cpp/go) keep receiving exporters from functionsystem/lib.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            package_root = pathlib.Path(temp_dir) / "openyuanrong"
            cpp_lib_dir = package_root / "runtime" / "service" / "cpp" / "lib"
            go_bin_dir = package_root / "runtime" / "service" / "go" / "bin"
            python_service_dir = package_root / "runtime" / "service" / "python" / "yr"
            python_ds_lib_dir = package_root / "runtime" / "service" / "python" / "yr" / "datasystem" / "lib"
            functionsystem_lib_dir = package_root / "functionsystem" / "lib"
            datasystem_lib_dir = package_root / "datasystem" / "sdk" / "cpp" / "lib"
            for target_dir in (cpp_lib_dir, go_bin_dir, python_service_dir, python_ds_lib_dir):
                target_dir.mkdir(parents=True)
            functionsystem_lib_dir.mkdir(parents=True)
            datasystem_lib_dir.mkdir(parents=True)

            push_exporter = "libobservability-prometheus-push-exporter.so"
            missing_exporter = "libobservability-metrics-opentelemetry-exporter.so"
            (cpp_lib_dir / "libcpp_runtime.so").write_text("cpp", encoding="utf-8")
            (go_bin_dir / "libgo_runtime.so").write_text("go", encoding="utf-8")
            (python_ds_lib_dir / "libpython_runtime.so").write_text("python", encoding="utf-8")
            (python_service_dir / push_exporter).write_text("runtime-exporter", encoding="utf-8")
            (functionsystem_lib_dir / "libshared_dep.so.1.0.0").write_text("shared", encoding="utf-8")
            (functionsystem_lib_dir / "libshared_dep.so.1").symlink_to("libshared_dep.so.1.0.0")
            (functionsystem_lib_dir / "libshared_dep.so").symlink_to("libshared_dep.so.1")
            (functionsystem_lib_dir / push_exporter).write_text("functionsystem-exporter", encoding="utf-8")
            (functionsystem_lib_dir / missing_exporter).write_text("missing-exporter", encoding="utf-8")
            (datasystem_lib_dir / "libnested_dep.so").write_text("nested", encoding="utf-8")

            functions_file = pathlib.Path(temp_dir) / "package_functions.sh"
            functions_file.write_text(load_package_function_definitions(), encoding="utf-8")
            test_script = pathlib.Path(temp_dir) / "run.sh"
            test_script.write_text(
                textwrap.dedent(
                    f"""\
                    #!/bin/bash
                    set -euo pipefail
                    source "{functions_file}"
                    restore_runtime_service_native_libs "{package_root}"
                    test "$(cat "{python_service_dir}/{push_exporter}")" = "runtime-exporter"
                    test ! -e "{python_service_dir}/{missing_exporter}"
                    test "$(readlink "{python_service_dir}/libshared_dep.so")" = "libshared_dep.so.1"
                    test "$(cat "{cpp_lib_dir}/{push_exporter}")" = "functionsystem-exporter"
                    test "$(cat "{go_bin_dir}/{push_exporter}")" = "functionsystem-exporter"
                    test "$(readlink "{cpp_lib_dir}/libshared_dep.so")" = "libshared_dep.so.1"
                    test "$(cat "{python_ds_lib_dir}/libnested_dep.so")" = "nested"
                    """
                ),
                encoding="utf-8",
            )

            subprocess.run(["bash", str(test_script)], check=True)


if __name__ == "__main__":
    unittest.main()
