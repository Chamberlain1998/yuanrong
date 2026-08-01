#!/usr/bin/env python3

"""Regression tests for Buildkite main-build artifact reuse."""

import os
import pathlib
import subprocess
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PIPELINE = REPO_ROOT / ".buildkite" / "pipeline.dynamic.yml"
CACHE_CONFIG_SCRIPT = REPO_ROOT / ".buildkite" / "configure_bazel_remote_cache.sh"
BUILD_SCRIPT = REPO_ROOT / "build.sh"
COMPILE_IMAGE = REPO_ROOT / "ci" / "ubuntu" / "Dockerfile.ubuntu2004"


class BuildkiteMainPackagingTest(unittest.TestCase):
    def test_k8s_runtime_uses_selected_sdk_python_suffix(self):
        env = os.environ.copy()
        env.update(
            {
                "ENABLE_LINUX_ARM": "false",
                "ENABLE_MACOS_SDK": "false",
                "ENABLE_TEST_PYPI_PUBLISH": "false",
                "SDK_PYTHON_VERSIONS": "python3.11",
            }
        )
        result = subprocess.run(
            ["bash", str(PIPELINE)],
            cwd=REPO_ROOT,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn('YR_K8S_DEFAULT_RUNTIME_SDK_SUFFIX: "cp311"', result.stdout)
        self.assertIn("YR_K8S_DEFAULT_RUNTIME_SDK_SUFFIX", PIPELINE.read_text())
        self.assertIn(
            'os.environ.get("YR_K8S_DEFAULT_RUNTIME_SDK_SUFFIX", "cp310")',
            (REPO_ROOT / ".buildkite" / "test_sandbox_k8s.sh").read_text(),
        )

    def test_core_wheel_is_only_wired_into_buildkite(self):
        def tracked_references(needle):
            result = subprocess.run(
                [
                    "git",
                    "grep",
                    "-l",
                    "-F",
                    needle,
                    "--",
                    ".",
                    ":(exclude)test/**",
                    ":(exclude).buildkite/package_core_wheel.py",
                ],
                cwd=REPO_ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode not in (0, 1):
                result.check_returncode()
            return result.stdout.splitlines()

        self.assertEqual(
            tracked_references(".buildkite/package_core_wheel.py"),
            [".buildkite/pipeline.dynamic.yml"],
        )
        self.assertEqual(
            tracked_references("scripts/trim.sh"),
            [],
        )

    def test_linux_bazel_remote_cache_is_enabled_by_default(self):
        pipeline = PIPELINE.read_text(encoding="utf-8")
        cache_config = CACHE_CONFIG_SCRIPT.read_text(encoding="utf-8")

        self.assertGreaterEqual(
            pipeline.count(". .buildkite/configure_bazel_remote_cache.sh"),
            4,
        )
        self.assertIn(
            "YR_BUILDKITE_ENABLE_BAZEL_REMOTE_CACHE:-true",
            cache_config,
        )
        self.assertNotIn(
            "YR_BUILDKITE_ENABLE_BAZEL_REMOTE_CACHE:-false",
            cache_config,
        )

    def test_python314_builder_installs_obs_sdk_for_every_python(self):
        pipeline = PIPELINE.read_text(encoding="utf-8")
        dockerfile = COMPILE_IMAGE.read_text(encoding="utf-8")

        self.assertIn("for v in 3.9 3.10 3.11 3.12 3.13 3.14", dockerfile)
        self.assertIn(
            "--break-system-packages setuptools wheel packaging esdk-obs-python",
            dockerfile,
        )
        self.assertIn(
            "compile-ubuntu2004:v20260717_py3146_obs",
            pipeline,
        )

    def test_main_builds_reuse_make_all_release_artifacts(self):
        pipeline = PIPELINE.read_text(encoding="utf-8")

        self.assertEqual(pipeline.count('make all BUILD_VERSION='), 2)
        self.assertNotIn("Repackage release artifacts", pipeline)
        self.assertNotIn("bash scripts/package_yuanrong.sh", pipeline)
        self.assertNotIn(
            "SETUP_TYPE= PYTHON_RUNTIME_VERSION=python3.11 "
            "python3 setup.py bdist_wheel",
            pipeline,
        )
        self.assertEqual(
            pipeline.count("api/python-rrt && RRT_RUNTIME_SRC="),
            pipeline.count("python3 setup.py bdist_wheel"),
        )

        self.assertNotIn("cp datasystem/output/*.whl output/", pipeline)
        self.assertNotIn("cp functionsystem/output/*.whl output/", pipeline)
        self.assertEqual(
            pipeline.count("find output -maxdepth 1 -name 'openyuanrong-*.tar.gz'"),
            2,
        )
        self.assertEqual(
            pipeline.count("find output -maxdepth 1 -name 'openyuanrong-*.whl'"),
            2,
        )
        self.assertEqual(
            pipeline.count("python3 .buildkite/package_core_wheel.py"),
            2,
        )
        self.assertEqual(
            pipeline.count(
                "find artifacts/release -maxdepth 1 "
                "-name 'openyuanrong_core-*.whl'"
            ),
            2,
        )
        self.assertNotIn(
            "package_core_release.sh",
            pipeline,
        )
        self.assertNotIn("*-core.tar.gz", pipeline)
        self.assertEqual(pipeline.count("Verify Go plugin ABI"), 2)

    def test_validation_package_version_does_not_invalidate_all_bazel_actions(self):
        pipeline = PIPELINE.read_text(encoding="utf-8")
        build_script = BUILD_SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            'BAZEL_BUILD_VERSION="${BAZEL_BUILD_VERSION:-${BUILD_VERSION}}"',
            build_script,
        )
        self.assertIn(
            "--action_env=BUILD_VERSION=${BAZEL_BUILD_VERSION}",
            build_script,
        )
        self.assertNotIn(
            "--action_env=BUILD_VERSION=${BUILD_VERSION}",
            build_script,
        )

        stable_version = (
            r'BAZEL_BUILD_VERSION="\$\${BAZEL_BUILD_VERSION:-'
            r'\$\${TAG_BUILD_VERSION:-\$\$(cat VERSION)}}"'
        )
        self.assertEqual(
            pipeline.count(stable_version) + 1,
            pipeline.count('YR_BUILD_VERSION="'),
        )


if __name__ == "__main__":
    unittest.main()
