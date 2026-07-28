#!/usr/bin/env python3

"""Regression checks for native shared-library symbol export boundaries."""

import pathlib
import re
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PYTHON_BUILD = REPO_ROOT / "api" / "python" / "BUILD.bazel"
PYTHON_EXPORTS = PYTHON_BUILD.with_name("fnruntime_exports.lds")
CPP_BUILD = REPO_ROOT / "api" / "cpp" / "BUILD.bazel"
CPP_EXPORTS = CPP_BUILD.with_name("yr_api_exports.lds")
GO_BUILD = REPO_ROOT / "api" / "go" / "libruntime" / "cpplibruntime" / "BUILD.bazel"
GO_HEADER = GO_BUILD.with_name("clibruntime.h")
GO_EXPORTS = GO_BUILD.with_name("cpplibruntime_exports.lds")
JNI_BUILD = REPO_ROOT / "api" / "java" / "function-common" / "src" / "main" / "cpp" / "BUILD.bazel"
JNI_EXPORTS = JNI_BUILD.with_name("jni_exports.lds")
METRICS_BUILD = REPO_ROOT / "src" / "utility" / "metrics" / "BUILD.bazel"
METRICS_EXPORTS = METRICS_BUILD.with_name("plugin_exports.lds")


class NativeSymbolExportsTest(unittest.TestCase):
    @staticmethod
    def _version_script_global_symbols(exports):
        global_block = re.search(r"\bglobal:\s*(.*?)\s*\blocal:", exports, re.DOTALL)
        if global_block is None:
            return set()
        return set(
            re.findall(
                r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*;\s*$",
                global_block.group(1),
                re.MULTILINE,
            )
        )

    def test_python_extension_exports_only_module_entry_point(self):
        self.assertTrue(PYTHON_EXPORTS.is_file(), "Python linker version script is missing")
        if not PYTHON_EXPORTS.is_file():
            return
        exports = PYTHON_EXPORTS.read_text(encoding="utf-8")
        build = PYTHON_BUILD.read_text(encoding="utf-8")

        self.assertIn("PyInit_fnruntime;", exports)
        self.assertEqual({"PyInit_fnruntime"}, self._version_script_global_symbols(exports))
        self.assertIn("local:", exports)
        self.assertIn("*;", exports)
        self.assertIn('additional_linker_inputs = [":fnruntime_exports.lds"]', build)
        self.assertIn("-Wl,--version-script=$(location :fnruntime_exports.lds)", build)
        self.assertIn('"//:is_linux": [', build)

    def test_cpp_api_isolates_embedded_curl_without_breaking_split_sdk_abi(self):
        self.assertTrue(CPP_EXPORTS.is_file(), "C++ linker version script is missing")
        if not CPP_EXPORTS.is_file():
            return
        exports = CPP_EXPORTS.read_text(encoding="utf-8")
        build = CPP_BUILD.read_text(encoding="utf-8")

        self.assertIn("global:", exports)
        self.assertIn("*;", exports)
        self.assertIn("local:", exports)
        self.assertIn("Curl_*;", exports)
        self.assertIn("curl_*;", exports)
        self.assertNotIn("*2YR*;", exports)
        self.assertIn('additional_linker_inputs = [":yr_api_exports.lds"]', build)
        self.assertIn("-Wl,--version-script=$(location :yr_api_exports.lds)", build)
        self.assertIn('"//:is_linux": [', build)

    def test_go_bridge_exports_only_declared_c_api(self):
        self.assertTrue(GO_EXPORTS.is_file(), "Go linker version script is missing")
        if not GO_EXPORTS.is_file():
            return
        exports = GO_EXPORTS.read_text(encoding="utf-8")
        build = GO_BUILD.read_text(encoding="utf-8")
        header = GO_HEADER.read_text(encoding="utf-8")
        declared_bridge_functions = set(re.findall(r"\b(C[A-Z][A-Za-z0-9_]*)\s*\(", header))
        exported_bridge_functions = self._version_script_global_symbols(exports)

        self.assertTrue(declared_bridge_functions, "No Go C bridge functions were found")
        self.assertEqual(declared_bridge_functions, exported_bridge_functions)
        for forbidden_export in ("Go*", "Curl_*", "curl_*", "CRYPTO_*", "pthread_*"):
            self.assertNotIn(forbidden_export, exports)
        self.assertIn("local:", exports)
        self.assertIn("*;", exports)
        self.assertIn('additional_linker_inputs = [":cpplibruntime_exports.lds"]', build)
        self.assertIn("-Wl,--version-script=$(location :cpplibruntime_exports.lds)", build)
        self.assertIn('"//:is_linux": [', build)

    def test_jni_library_exports_only_jni_entry_points(self):
        self.assertTrue(JNI_EXPORTS.is_file(), "JNI linker version script is missing")
        exports = JNI_EXPORTS.read_text(encoding="utf-8")
        build = JNI_BUILD.read_text(encoding="utf-8")

        self.assertIn("JNI_OnLoad;", exports)
        self.assertIn("JNI_OnUnload;", exports)
        self.assertIn("Java_*;", exports)
        self.assertIn("local:", exports)
        self.assertIn("*;", exports)
        self.assertIn('additional_linker_inputs = [":jni_exports.lds"]', build)
        self.assertIn("-Wl,--version-script=$(location :jni_exports.lds)", build)
        self.assertIn('"//:is_linux": [', build)

    def test_metrics_plugins_export_only_factory_hook(self):
        self.assertTrue(METRICS_EXPORTS.is_file(), "metrics plugin linker version script is missing")
        exports = METRICS_EXPORTS.read_text(encoding="utf-8")
        build = METRICS_BUILD.read_text(encoding="utf-8")

        self.assertIn("ObservabilityMakeFactoryImpl;", exports)
        self.assertIn("local:", exports)
        self.assertIn("*;", exports)
        self.assertEqual(3, build.count("additional_linker_inputs = METRICS_PLUGIN_LINKER_INPUTS"))
        self.assertEqual(3, build.count("linkopts = METRICS_PLUGIN_LINKOPTS"))
        self.assertIn("-Wl,--version-script=$(location :plugin_exports.lds)", build)
        self.assertIn('"//:is_linux": [', build)


if __name__ == "__main__":
    unittest.main()
