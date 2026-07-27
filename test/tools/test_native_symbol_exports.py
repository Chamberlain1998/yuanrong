#!/usr/bin/env python3

"""Regression checks for native shared-library symbol export boundaries."""

import pathlib
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
JNI_BUILD = REPO_ROOT / "api" / "java" / "function-common" / "src" / "main" / "cpp" / "BUILD.bazel"
JNI_EXPORTS = JNI_BUILD.with_name("jni_exports.lds")
METRICS_BUILD = REPO_ROOT / "src" / "utility" / "metrics" / "BUILD.bazel"
METRICS_EXPORTS = METRICS_BUILD.with_name("plugin_exports.lds")


class NativeSymbolExportsTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
