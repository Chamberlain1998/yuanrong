#!/usr/bin/env python3

"""Regression contracts for the musl-only RRT release build."""

import os
import pathlib
import stat
import subprocess
import tempfile
import textwrap
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD_SCRIPT = REPO_ROOT / "scripts" / "build_rrt_runtime.sh"
PIPELINE = REPO_ROOT / ".buildkite" / "pipeline.dynamic.yml"
RUST_BUILD = REPO_ROOT / "api" / "rust" / "BUILD.bazel"
RRT_BUILDER = REPO_ROOT / "ci" / "rrt" / "Dockerfile.aarch64.rust"
RRT_BUILDER_OVERLAY = REPO_ROOT / "ci" / "rrt" / "Dockerfile.musl-overlay"


class RrtStaticBuildTest(unittest.TestCase):
    def _write_executable(self, path, content):
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def _run_build(
        self, arch="amd64", *, target="", elf_kind="static", rustup_add=0
    ):
        tempdir = tempfile.TemporaryDirectory()
        root = pathlib.Path(tempdir.name)
        fake_bin = root / "bin"
        workspace = root / "rust"
        target_dir = root / "target"
        output = root / "out" / "rrt-runtime"
        command_log = root / "cargo-args"
        installed_marker = root / "target-installed"
        fake_bin.mkdir()
        workspace.mkdir()

        self._write_executable(
            fake_bin / "rustup",
            f"""
            #!/usr/bin/env bash
            set -e
            if [[ "$1 $2 $3" == "target list --installed" ]]; then
                [[ -f "{installed_marker}" ]] && cat "{installed_marker}"
                exit 0
            fi
            if [[ "$1 $2" == "target add" ]]; then
                [[ "{rustup_add}" == "0" ]] || exit "{rustup_add}"
                printf '%s\\n' "$3" > "{installed_marker}"
                exit 0
            fi
            exit 64
            """,
        )
        self._write_executable(
            fake_bin / "cargo",
            f"""
            #!/usr/bin/env bash
            set -e
            printf '%s\\n' "$@" > "{command_log}"
            target=""
            while [[ $# -gt 0 ]]; do
                if [[ "$1" == "--target" ]]; then target="$2"; shift 2; else shift; fi
            done
            mkdir -p "{target_dir}/$target/release"
            printf '%s\\n' "{elf_kind}" > "{target_dir}/$target/release/rrt-runtime"
            chmod +x "{target_dir}/$target/release/rrt-runtime"
            """,
        )
        self._write_executable(
            fake_bin / "readelf",
            """
            #!/usr/bin/env bash
            set -e
            kind=$(cat "$2")
            case "$1" in
                -hW)
                    case "${RRT_BUILD_ARCH}" in
                        amd64)
                            echo '  Machine:       Advanced Micro Devices X86-64'
                            ;;
                        arm64) echo '  Machine:                           AArch64' ;;
                    esac
                    ;;
                -lW) [[ "$kind" == "dynamic" ]] && echo '  INTERP' || true ;;
                -dW)
                    [[ "$kind" == "dynamic" || "$kind" == "shared" ]] \
                        && echo ' 0x0000000000000001 (NEEDED)' || true
                    ;;
                *) exit 65 ;;
            esac
            """,
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{fake_bin}:{env['PATH']}",
                "CARGO_TARGET_DIR": str(target_dir),
                "RRT_BUILD_ARCH": arch,
                "RRT_OUTPUT": str(output),
                "RRT_WORKSPACE": str(workspace),
            }
        )
        if target:
            env["RRT_TARGET"] = target
        result = subprocess.run(
            ["bash", str(BUILD_SCRIPT)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        return tempdir, result, output, command_log

    def test_each_architecture_uses_its_musl_target(self):
        for arch, target in (
            ("amd64", "x86_64-unknown-linux-musl"),
            ("arm64", "aarch64-unknown-linux-musl"),
        ):
            with self.subTest(arch=arch):
                tempdir, result, output, command_log = self._run_build(arch)
                with tempdir:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertTrue(output.is_file())
                    args = command_log.read_text(encoding="utf-8").splitlines()
                    self.assertIn("--locked", args)
                    self.assertEqual(args[args.index("--target") + 1], target)

    def test_non_musl_target_is_rejected_before_cargo(self):
        tempdir, result, output, command_log = self._run_build(
            target="x86_64-unknown-linux-gnu"
        )
        with tempdir:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(command_log.exists())
            self.assertFalse(output.exists())

    def test_target_provisioning_failure_stops_the_build(self):
        tempdir, result, output, command_log = self._run_build(rustup_add=42)
        with tempdir:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(command_log.exists())
            self.assertFalse(output.exists())

    def test_dynamic_elf_is_rejected_before_packaging(self):
        tempdir, result, output, _ = self._run_build(elf_kind="dynamic")
        with tempdir:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_shared_library_dependency_is_rejected_before_packaging(self):
        tempdir, result, output, _ = self._run_build(elf_kind="shared")
        with tempdir:
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())

    def test_all_release_paths_use_the_shared_build_script(self):
        pipeline = PIPELINE.read_text(encoding="utf-8")
        rust_build = RUST_BUILD.read_text(encoding="utf-8")

        self.assertEqual(pipeline.count("bash scripts/build_rrt_runtime.sh"), 2)
        self.assertIn("RRT_BUILD_ARCH=amd64", pipeline)
        self.assertIn("RRT_BUILD_ARCH=arm64", pipeline)
        self.assertNotIn(
            "cargo build --release --bin rrt-runtime",
            pipeline,
        )
        self.assertNotIn("RRT_STATIC", rust_build)
        self.assertIn('"//:scripts/build_rrt_runtime.sh"', rust_build)
        self.assertIn(
            'bash "$$(realpath $$BASE_DIR/scripts/build_rrt_runtime.sh)"',
            rust_build,
        )
        builder = RRT_BUILDER.read_text(encoding="utf-8")
        self.assertIn("aarch64-unknown-linux-musl", builder)
        self.assertIn("x86_64-unknown-linux-musl", builder)
        self.assertIn('rustup target add "${rrt_target}"', builder)

    def test_builder_images_preinstall_the_musl_targets(self):
        pipeline = PIPELINE.read_text(encoding="utf-8")
        overlay = RRT_BUILDER_OVERLAY.read_text(encoding="utf-8")

        self.assertIn("v20260826_rust1950_musl_x86_64", pipeline)
        self.assertIn("v20260826_rust1851_sccache082_musl_arm64", pipeline)
        self.assertIn("aarch64-unknown-linux-musl", overlay)
        self.assertIn('rustup target add "${RRT_TARGET}"', overlay)
        self.assertIn("RUSTUP_DIST_SERVER=https://rsproxy.cn", overlay)


if __name__ == "__main__":
    unittest.main()
