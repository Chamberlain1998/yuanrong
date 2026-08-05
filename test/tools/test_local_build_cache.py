#!/usr/bin/env python3

"""Regression tests for the opt-in local release/UT build caches."""

import json
import pathlib
import subprocess
import tempfile
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CACHE_WRAPPER = REPO_ROOT / "scripts/with_local_build_cache.sh"


class LocalBuildCacheTest(unittest.TestCase):
    def cached_environment(self, root: pathlib.Path, profile: str) -> dict[str, str]:
        command = [
            "bash",
            str(CACHE_WRAPPER),
            "--root",
            str(root),
            "--profile",
            profile,
            "--",
            "python3",
            "-c",
            "import json, os; print(json.dumps(dict(os.environ)))",
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_release_and_ut_share_downloads_but_not_compiled_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "cache"
            release = self.cached_environment(root, "release")
            unit_test = self.cached_environment(root, "ut")

        for variable in (
            "BAZEL_REPOSITORY_CACHE",
            "CARGO_HOME",
            "GOMODCACHE",
            "PIP_CACHE_DIR",
            "npm_config_cache",
            "GRADLE_USER_HOME",
            "CCACHE_DIR",
            "SCCACHE_DIR",
        ):
            self.assertEqual(release[variable], unit_test[variable], variable)

        for variable in (
            "BAZEL_OUTPUT_USER_ROOT",
            "BAZEL_OUTPUT_BASE",
            "BAZEL_DISK_CACHE",
            "CARGO_TARGET_DIR",
            "GOCACHE",
        ):
            self.assertNotEqual(release[variable], unit_test[variable], variable)
            self.assertIn("/profiles/release/", release[variable])
            self.assertIn("/profiles/ut/", unit_test[variable])

    def test_wrapper_requires_explicit_valid_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for arguments in (
                ["--root", temp_dir, "--", "true"],
                ["--root", temp_dir, "--profile", "debug", "--", "true"],
                ["--profile", "release", "--", "true"],
            ):
                result = subprocess.run(
                    ["bash", str(CACHE_WRAPPER), *arguments],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(result.returncode, 2, arguments)

    def test_makefile_routes_only_explicit_local_profiles(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("LOCAL_CACHE_ROOT ?=", makefile)
        self.assertIn("LOCAL_CACHE_PROFILE ?=", makefile)
        self.assertIn("FUNCTIONSYSTEM_BUILDER ?= cmake", makefile)
        self.assertIn("FUNCTIONSYSTEM_BUILDER_ARGS := --builder bazel", makefile)
        self.assertIn('--root "$(abspath $(LOCAL_CACHE_ROOT))"', makefile)
        self.assertIn("--bazel_local_cache_root", makefile)
        self.assertIn("--bazel_cache_profile", makefile)
        self.assertIn("$(LOCAL_CACHE_RUN) bash build.sh -P", makefile)
        self.assertIn("$(LOCAL_CACHE_RUN) bash scripts/build_rrt_runtime.sh", makefile)

    def test_build_sh_accepts_profile_disk_cache_without_changing_default(self):
        build_script = (REPO_ROOT / "build.sh").read_text(encoding="utf-8")

        self.assertIn('LOCAL_DISK_CACHE="${BAZEL_DISK_CACHE:-}"', build_script)
        self.assertIn('--disk_cache=${LOCAL_DISK_CACHE}', build_script)
        self.assertNotIn('BAZEL_DISK_CACHE="', build_script)

    def test_rrt_build_uses_workspace_target_directory(self):
        rust_script = (REPO_ROOT / "scripts/build_rrt_runtime.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn('RUST_WORKSPACE="${BASE_DIR}/api/rust"', rust_script)
        self.assertIn(
            'TARGET_DIR="${CARGO_TARGET_DIR:-${RUST_WORKSPACE}/target}"', rust_script
        )
        self.assertIn("cargo build --release -p rrt-daemon --bin rrt-runtime", rust_script)
        self.assertNotIn("api/rust/rrt-daemon/target", rust_script)


if __name__ == "__main__":
    unittest.main()
