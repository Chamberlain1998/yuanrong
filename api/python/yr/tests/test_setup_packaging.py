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

"""Packaging behavior tests for split Python wheels."""

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[4]
SETUP_PATH = REPO_ROOT / "api" / "python" / "setup.py"


def load_setup_module(setup_type=""):
    """Load setup.py with setuptools.setup patched out."""
    module_name = f"_yr_setup_test_{setup_type or 'main'}"
    sys.modules.pop(module_name, None)
    old_setup_type = os.environ.get("SETUP_TYPE")
    python_root = str(SETUP_PATH.parent)
    added_path = False
    if python_root not in sys.path:
        sys.path.append(python_root)
        added_path = True
    if setup_type:
        os.environ["SETUP_TYPE"] = setup_type
    else:
        os.environ.pop("SETUP_TYPE", None)

    spec = importlib.util.spec_from_file_location(module_name, SETUP_PATH)
    module = importlib.util.module_from_spec(spec)
    try:
        with mock.patch("setuptools.setup"):
            spec.loader.exec_module(module)
    finally:
        if added_path:
            sys.path.remove(python_root)
        if old_setup_type is None:
            os.environ.pop("SETUP_TYPE", None)
        else:
            os.environ["SETUP_TYPE"] = old_setup_type
    return module


class SetupPackagingTest(unittest.TestCase):
    def test_python_dependency_ranges_preserve_existing_lower_bounds(self):
        expected_requirements = [
            "cloudpickle==3.1.2",
            "msgpack>=1.0.5",
            'protobuf>=4.25.5,<8; python_version < "3.14"',
            'protobuf>=7.35.1,<8; python_version >= "3.14"',
            "pyyaml>=6.0.0",
            "click>=8.0.0,<9",
            'tomli>=2; python_version < "3.11"',
            "tomli-w>=1.2.0",
            "Jinja2>=3.1.6",
            "requests>=2.32.5",
            "websockets>=15.0",
            "aiohttp>=3.9.0",
            "httpx>=0.27.0",
        ]
        requirements = (REPO_ROOT / "api" / "python" / "requirements.txt").read_text(
            encoding="utf-8"
        )
        self.assertEqual(requirements.splitlines(), expected_requirements)

        expected_by_setup_type = {
            "sdk": [
                "cloudpickle==3.1.2",
                "msgpack>=1.0.5",
                'protobuf>=4.25.5,<8; python_version < "3.14"',
                'protobuf>=7.35.1,<8; python_version >= "3.14"',
                "pyyaml>=6.0.0",
                "click>=8.0.0,<9",
                'tomli>=2; python_version < "3.11"',
                "tomli-w>=1.2.0",
                "Jinja2>=3.1.6",
                "requests>=2.32.5",
                "websockets>=15.0",
                "aiohttp>=3.9.0",
                "httpx>=0.27.0",
            ],
            "full": [
                "cloudpickle==3.1.2",
                "msgpack>=1.0.5",
                'protobuf>=4.25.5,<8; python_version < "3.14"',
                'protobuf>=7.35.1,<8; python_version >= "3.14"',
                "pyyaml>=6.0.2",
                "click>=8.1.8",
                "requests>=2.32.5",
                "websockets>=15.0.1",
                "aiohttp>=3.9.0",
                "httpx>=0.27.0",
                'tomli>=2; python_version < "3.11"',
                "tomli-w>=1.2.0",
                "Jinja2>=3.1.6",
            ],
            "": [
                "cloudpickle==3.1.2",
                "msgpack>=1.0.5",
                'protobuf>=4.25.5,<8; python_version < "3.14"',
                'protobuf>=7.35.1,<8; python_version >= "3.14"',
                "pyyaml>=6.0.0",
                "click>=8.0.0,<9",
                "requests>=2.32.5",
                "websockets>=13.0",
                "aiohttp>=3.9.0",
                "httpx>=0.27.0",
                'tomli>=2; python_version < "3.11"',
                "tomli-w>=1.2.0",
                "Jinja2>=3.1.6",
            ],
        }
        for setup_type, expected in expected_by_setup_type.items():
            with self.subTest(setup_type=setup_type or "default"):
                setup_mod = load_setup_module(setup_type)
                self.assertEqual(setup_mod.setup_spec.install_requires, expected)

    def test_runtime_image_uses_sdk_cloudpickle_version(self):
        sdk_cloudpickle = next(
            requirement
            for requirement in load_setup_module("sdk").setup_spec.install_requires
            if requirement.startswith("cloudpickle==")
        )
        dockerfile = (
            REPO_ROOT
            / "deploy"
            / "k8s"
            / "build"
            / "functionsystem"
            / "dockerfile"
            / "Dockerfile-runtime-manager"
        ).read_text(encoding="utf-8")

        self.assertIn(sdk_cloudpickle, dockerfile)

    def test_cli_config_uses_declared_tomli_fallback(self):
        config_source = (
            REPO_ROOT / "api" / "python" / "yr" / "cli" / "config.py"
        ).read_text(encoding="utf-8")
        self.assertIn("import tomli as tomllib", config_source)
        self.assertNotIn("pip._vendor", config_source)

    def test_runtime_bazel_package_includes_metrics_exporters(self):
        build_rule = (REPO_ROOT / "api" / "python" / "BUILD.bazel").read_text()
        package_rule_start = build_rule.index('name = "yr_python_pkg"')
        package_inputs_end = build_rule.index('outs = ["yr_python_pkg.out"]', package_rule_start)
        package_inputs = build_rule[package_rule_start:package_inputs_end]

        self.assertIn("//src/utility/metrics:shared_exporters", package_inputs)
        self.assertIn("libobservability-*.so|libobservability-*.dylib", build_rule)

    def test_package_import_does_not_require_fnruntime(self):
        code = f"""
import ctypes
import sys
import types
calls = []
ctypes.CDLL = lambda path, mode=0: calls.append((path, mode))
sys.path.insert(0, {str(REPO_ROOT / "api" / "python")!r})
import yr
assert "yr.apis" not in sys.modules
assert "yr.fnruntime" not in sys.modules
assert calls == [], calls
yr._preload_native_libraries()
assert calls, "native preload should still be available when explicitly requested"
assert all(mode == getattr(ctypes, "RTLD_GLOBAL", 0) for _, mode in calls)
assert any(path.endswith("/yr/libcurl.so.4") for path, _ in calls), calls
libcrypto_calls = [path for path, _ in calls if path.endswith("libcrypto.so.1.1")]
system_crypto = next(path for path in libcrypto_calls if path == "/usr/lib64/libcrypto.so.1.1")
assert system_crypto
assert not any("/yr/libcrypto.so.1.1" in path for path in libcrypto_calls), libcrypto_calls
fake_runtime_holder = types.ModuleType("yr.runtime_holder")
fake_runtime_holder.marker = object()
sys.modules["yr.runtime_holder"] = fake_runtime_holder
assert yr.runtime_holder is fake_runtime_holder
fake_fcc = types.ModuleType("yr.fcc")
fake_fcc.create_function_group = object()
sys.modules["yr.fcc"] = fake_fcc
assert yr.fcc is fake_fcc
assert yr.create_function_group is fake_fcc.create_function_group
fake_cluster_runtime = types.ModuleType("yr.cluster_mode_runtime")
fake_cluster_runtime.marker = object()
sys.modules["yr.cluster_mode_runtime"] = fake_cluster_runtime
assert yr.cluster_mode_runtime is fake_cluster_runtime
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("ok", result.stdout)

    def test_fnruntime_support_modules_do_not_load_runtime_holder(self):
        code = f"""
import importlib.abc
import sys
import types

sys.path.insert(0, {str(REPO_ROOT / "api" / "python")!r})
import yr

cloudpickle = types.ModuleType("cloudpickle")
cloudpickle.dumps = lambda value: b""
sys.modules["cloudpickle"] = cloudpickle

libruntime_pb2 = types.ModuleType("yr.libruntime_pb2")
libruntime_pb2.FunctionMeta = type("FunctionMeta", (), {{}})
libruntime_pb2.LanguageType = type("LanguageType", (), {{"Python": 0}})
sys.modules["yr.libruntime_pb2"] = libruntime_pb2

class BlockRuntimeHolder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "yr.runtime_holder":
            raise AssertionError("fnruntime support modules must not import yr.runtime_holder at module load")
        return None

sys.meta_path.insert(0, BlockRuntimeHolder())
import yr.object_ref
import yr.device
assert "yr.runtime_holder" not in sys.modules
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("ok", result.stdout)

    def test_runtime_holder_does_not_load_runtime_implementations_on_import(self):
        code = f"""
import importlib.abc
import sys

sys.path.insert(0, {str(REPO_ROOT / "api" / "python")!r})

class BlockRuntimeImplementation(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = {
            "yr.base_runtime",
            "yr.cluster_mode_runtime",
            "yr.config_manager",
            "yr.local_mode.local_mode_runtime",
        }
        if fullname in blocked:
            raise AssertionError("yr.runtime_holder must not import init-only modules at module load")
        return None

sys.meta_path.insert(0, BlockRuntimeImplementation())
import yr.runtime_holder
assert "yr.cluster_mode_runtime" not in sys.modules
assert "yr.local_mode.local_mode_runtime" not in sys.modules
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout}\nstderr={result.stderr}",
        )
        self.assertIn("ok", result.stdout)

    def test_openyuanrong_main_prunes_top_level_native_libraries(self):
        setup_mod = load_setup_module()
        with tempfile.TemporaryDirectory() as tmp:
            yr_dir = Path(tmp) / "yr"
            yr_dir.mkdir()
            (yr_dir / "fnruntime.cpython-39-x86_64-linux-gnu.so").write_text("native")
            (yr_dir / "libdatasystem.so").write_text("native")
            (yr_dir / "cli").mkdir()
            (yr_dir / "cli" / "config.toml").write_text("pure")

            setup_mod.prune_openyuanrong_native_libraries(tmp)

            self.assertFalse((yr_dir / "fnruntime.cpython-39-x86_64-linux-gnu.so").exists())
            self.assertFalse((yr_dir / "libdatasystem.so").exists())
            self.assertTrue((yr_dir / "cli" / "config.toml").exists())

    def test_runtime_package_copies_current_fnruntime_to_top_level_yr(self):
        setup_mod = load_setup_module("runtime")
        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api_python_dir = root / "api" / "python"
            runtime_dir = root / "output" / "openyuanrong" / "runtime"
            service_bin_dir = runtime_dir / "service" / "go" / "bin"
            service_python_yr_dir = runtime_dir / "service" / "python" / "yr"
            build_lib = root / "build_lib"

            api_python_dir.mkdir(parents=True)
            service_bin_dir.mkdir(parents=True)
            service_python_yr_dir.mkdir(parents=True)
            (service_bin_dir / "goruntime").write_text("runtime")
            (service_python_yr_dir / f"fnruntime{ext_suffix}").write_bytes(b"native")
            (service_python_yr_dir / "fnruntime.cpython-39-x86_64-linux-gnu.so").write_bytes(
                b"other-native"
            )
            for exporter in (
                "libobservability-metrics-file-exporter.so",
                "libobservability-prometheus-push-exporter.so",
                "libobservability-prometheus-pull-exporter.so",
            ):
                (service_python_yr_dir / exporter).write_bytes(b"metrics")

            old_root_dir = setup_mod.ROOT_DIR
            setup_mod.ROOT_DIR = str(api_python_dir)
            try:
                setup_mod.copy_openyuanrong_runtime(str(build_lib))
            finally:
                setup_mod.ROOT_DIR = old_root_dir

            self.assertTrue((build_lib / "yr" / "runtime" / "service" / "go" / "bin" / "goruntime").exists())
            self.assertTrue((build_lib / "yr" / f"fnruntime{ext_suffix}").exists())
            self.assertEqual((build_lib / "yr" / f"fnruntime{ext_suffix}").read_bytes(), b"native")
            self.assertFalse(
                (build_lib / "yr" / "fnruntime.cpython-39-x86_64-linux-gnu.so").exists()
            )

    def test_runtime_package_flattens_python_native_dependencies_to_top_level_yr(self):
        setup_mod = load_setup_module("runtime")
        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api_python_dir = root / "api" / "python"
            runtime_dir = root / "output" / "openyuanrong" / "runtime"
            service_bin_dir = runtime_dir / "service" / "go" / "bin"
            service_python_yr_dir = runtime_dir / "service" / "python" / "yr"
            datasystem_lib_dir = service_python_yr_dir / "datasystem" / "lib"
            build_lib = root / "build_lib"

            api_python_dir.mkdir(parents=True)
            service_bin_dir.mkdir(parents=True)
            datasystem_lib_dir.mkdir(parents=True)
            (service_bin_dir / "goruntime").write_text("runtime")
            (service_python_yr_dir / f"fnruntime{ext_suffix}").write_bytes(b"native")
            (datasystem_lib_dir / "libcurl.so.4").write_bytes(b"curl")
            (datasystem_lib_dir / "libssl.so.1.1").write_bytes(b"ssl")
            (datasystem_lib_dir / "libdatasystem.so").write_bytes(b"datasystem")
            (datasystem_lib_dir / "libyr-api.so.1").write_bytes(b"yr-api-versioned")
            (datasystem_lib_dir / "libfunctionsdk.so.1.0.0").write_bytes(
                b"functionsdk-versioned"
            )
            for exporter in (
                "libobservability-metrics-file-exporter.so",
                "libobservability-prometheus-push-exporter.so",
                "libobservability-prometheus-pull-exporter.so",
            ):
                (service_python_yr_dir / exporter).write_bytes(b"metrics")

            old_root_dir = setup_mod.ROOT_DIR
            setup_mod.ROOT_DIR = str(api_python_dir)
            try:
                setup_mod.copy_openyuanrong_runtime(str(build_lib))
            finally:
                setup_mod.ROOT_DIR = old_root_dir

            self.assertEqual((build_lib / "yr" / "libcurl.so.4").read_bytes(), b"curl")
            self.assertEqual((build_lib / "yr" / "libssl.so.1.1").read_bytes(), b"ssl")
            self.assertEqual((build_lib / "yr" / "libdatasystem.so").read_bytes(), b"datasystem")
            self.assertFalse((build_lib / "yr" / "libyr-api.so.1").exists())
            self.assertFalse((build_lib / "yr" / "libfunctionsdk.so.1.0.0").exists())
            self.assertFalse((build_lib / "yr" / "datasystem" / "lib" / "libcurl.so.4").exists())

    def test_sdk_package_flattens_native_dependencies_to_top_level_yr(self):
        setup_mod = load_setup_module("sdk")
        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api_python_dir = root / "api" / "python"
            source_yr_dir = api_python_dir / "yr"
            cpp_sdk_lib_dir = root / "build" / "output" / "runtime" / "sdk" / "cpp" / "lib"
            output_python_ds_lib_dir = (
                root
                / "output"
                / "openyuanrong"
                / "runtime"
                / "service"
                / "python"
                / "yr"
                / "datasystem"
                / "lib"
            )
            build_lib = root / "build_lib"

            source_yr_dir.mkdir(parents=True)
            cpp_sdk_lib_dir.mkdir(parents=True)
            output_python_ds_lib_dir.mkdir(parents=True)
            (source_yr_dir / f"fnruntime{ext_suffix}").write_bytes(b"native")
            (cpp_sdk_lib_dir / "libcurl.so.4").write_bytes(b"curl")
            (cpp_sdk_lib_dir / "libyr-api.so").write_bytes(b"yr-api")
            (cpp_sdk_lib_dir / "libyr-api.so.1").write_bytes(b"yr-api-versioned")
            (output_python_ds_lib_dir / "libdatasystem.so").write_bytes(b"datasystem")
            (output_python_ds_lib_dir / "libcrypto.so.1.1").write_bytes(b"crypto")
            (output_python_ds_lib_dir / "libfunctionsdk.so.1.0.0").write_bytes(
                b"functionsdk-versioned"
            )

            old_root_dir = setup_mod.ROOT_DIR
            setup_mod.ROOT_DIR = str(api_python_dir)
            try:
                setup_mod.copy_openyuanrong_sdk(str(build_lib))
            finally:
                setup_mod.ROOT_DIR = old_root_dir

            self.assertEqual((build_lib / "yr" / f"fnruntime{ext_suffix}").read_bytes(), b"native")
            self.assertEqual((build_lib / "yr" / "libcurl.so.4").read_bytes(), b"curl")
            self.assertEqual((build_lib / "yr" / "libdatasystem.so").read_bytes(), b"datasystem")
            self.assertEqual((build_lib / "yr" / "libcrypto.so.1.1").read_bytes(), b"crypto")
            self.assertFalse((build_lib / "yr" / "libyr-api.so").exists())
            self.assertFalse((build_lib / "yr" / "libyr-api.so.1").exists())
            self.assertFalse((build_lib / "yr" / "libfunctionsdk.so.1.0.0").exists())
            self.assertTrue((build_lib / "yr" / "cpp" / "lib" / "libyr-api.so").exists())


if __name__ == "__main__":
    unittest.main()
