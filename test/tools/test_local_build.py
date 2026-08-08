#!/usr/bin/env python3

"""Regression tests for the worktree-independent local build pipeline."""

import json
import multiprocessing
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from local_build import (  # noqa: E402
    BuildQueue,
    CacheLayout,
    InputIdentity,
    LocalBuildError,
    LocalBuildRunner,
    collect_artifacts,
    publish_component_outputs,
    create_parser,
    create_mount_plan,
    validate_component_artifacts,
    artifact_key_for,
    parse_build_metrics,
    validate_functionsystem_inputs,
    validate_runtime_inputs,
    _remote_cache_reachable,
    SERVER_SEED_ARCHIVES,
    produced_output_names,
    datasystem_baseline_filename,
    _validate_datasystem_baseline,
    aio_build_command,
    default_cache_root,
    local_build_adapter_sha256,
    LOCAL_BUILD_ADAPTER_FILES,
)
from local_build_components import component_command, release_command, ut_command  # noqa: E402


def _hold_queue(cache_root: str, ready: multiprocessing.Event) -> None:
    with BuildQueue(pathlib.Path(cache_root), "first", pathlib.Path("/tmp/worktree-a")):
        ready.set()
        time.sleep(0.5)


class CacheLayoutTest(unittest.TestCase):
    def test_default_cache_root_uses_git_common_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = pathlib.Path(temp_dir) / "workspace"
            repo = workspace / "yuanrong/.worktrees/topic"
            repo.mkdir(parents=True)
            common_dir = workspace / "yuanrong/.git"
            with mock.patch("local_build._run_text", return_value=str(common_dir)):
                resolved = default_cache_root(repo)

        self.assertEqual(resolved, workspace / ".yr-cache/yuanrong-local-build-profile")

    def test_default_cache_root_falls_back_outside_non_git_worktree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir) / "checkout"
            repo.mkdir()
            with mock.patch("local_build._run_text", return_value=""):
                resolved = default_cache_root(repo)

        self.assertEqual(resolved, repo.parent / ".yr-cache/yuanrong-local-build-profile")

    def test_unreachable_remote_cache_is_not_enabled(self):
        with mock.patch("local_build.socket.create_connection", side_effect=OSError):
            self.assertFalse(_remote_cache_reachable("grpc://bazel-remote.invalid:9092"))

    def test_remote_cache_probe_accepts_reachable_endpoint(self):
        with mock.patch("local_build.socket.create_connection") as connect:
            self.assertTrue(_remote_cache_reachable("grpc://cache.example:9092"))
            connect.assert_called_once_with(("cache.example", 9092), timeout=1.5)

    def test_release_and_ut_share_downloads_but_isolate_outputs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = CacheLayout(
                pathlib.Path(temp_dir),
                platform_name="linux",
                machine="x86_64",
                bazel_major="6",
                rust_version="1.85.1",
                go_version="1.24.1",
            )
            release = layout.environment("release")
            unit_test = layout.environment("ut")

        for variable in (
            "BAZEL_REPOSITORY_CACHE",
            "CARGO_HOME",
            "GOMODCACHE",
            "PIP_CACHE_DIR",
            "npm_config_cache",
            "DS_OPENSOURCE_DIR",
            "FS_VENDOR_CACHE_DIR",
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

    def test_go_plugin_cache_is_shared(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            layout = CacheLayout(pathlib.Path(temp_dir))
            release = layout.environment("release")
            unit_test = layout.environment("ut")
        self.assertEqual(release["GOBIN"], unit_test["GOBIN"])
        self.assertIn("/common/go-bin", release["GOBIN"])

    def test_input_identity_includes_dependency_files_and_toolchain(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir)
            (repo / "functionsystem/vendor").mkdir(parents=True)
            (repo / "tools").mkdir()
            (repo / "functionsystem/WORKSPACE").write_text("workspace-a", encoding="utf-8")
            (repo / "functionsystem/vendor/VendorList.csv").write_text("vendor-a", encoding="utf-8")
            (repo / "tools/openSource.txt").write_text("runtime-a", encoding="utf-8")

            identity = InputIdentity.from_repo(
                repo,
                image_id="sha256:1234567890abcdef",
                platform_name="linux",
                machine="amd64",
                bazel_major="6",
            )
            first_fs = identity.functionsystem
            first_runtime = identity.runtime
            (repo / "functionsystem/vendor/VendorList.csv").write_text("vendor-b", encoding="utf-8")
            changed = InputIdentity.from_repo(
                repo,
                image_id="sha256:1234567890abcdef",
                platform_name="linux",
                machine="amd64",
                bazel_major="6",
            )

        self.assertNotEqual(first_fs, changed.functionsystem)
        self.assertEqual(first_runtime, changed.runtime)
        self.assertIn("linux-amd64-bazel6-img12345678", first_fs)

    def test_input_identity_changes_with_submodule_gitlinks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir)
            (repo / "functionsystem/vendor").mkdir(parents=True)
            (repo / "tools").mkdir()
            (repo / "functionsystem/WORKSPACE").write_text("workspace-a", encoding="utf-8")
            (repo / "functionsystem/vendor/VendorList.csv").write_text("vendor-a", encoding="utf-8")
            (repo / "tools/openSource.txt").write_text("runtime-a", encoding="utf-8")

            with mock.patch.dict(os.environ, {"LOCAL_DATASYSTEM_SOURCE_BUILD": "1"}), mock.patch("local_build._run_text", return_value=" 1111111 datasystem\n 2222222 functionsystem"):
                first = InputIdentity.from_repo(
                    repo,
                    image_id="sha256:1234567890abcdef",
                    platform_name="linux",
                    machine="amd64",
                    bazel_major="6",
                )
            with mock.patch.dict(os.environ, {"LOCAL_DATASYSTEM_SOURCE_BUILD": "1"}), mock.patch("local_build._run_text", return_value=" 3333333 datasystem\n 2222222 functionsystem"):
                changed = InputIdentity.from_repo(
                    repo,
                    image_id="sha256:1234567890abcdef",
                    platform_name="linux",
                    machine="amd64",
                    bazel_major="6",
                )

        self.assertNotEqual(first.functionsystem, changed.functionsystem)
        self.assertNotEqual(first.runtime, changed.runtime)
        self.assertNotEqual(first.submodules_sha256, changed.submodules_sha256)

    def test_default_datasystem_baseline_ignores_datasystem_gitlink(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir)
            (repo / "functionsystem/vendor").mkdir(parents=True)
            (repo / "tools").mkdir()
            (repo / "functionsystem/WORKSPACE").write_text("workspace-a", encoding="utf-8")
            (repo / "functionsystem/vendor/VendorList.csv").write_text("vendor-a", encoding="utf-8")
            (repo / "tools/openSource.txt").write_text("runtime-a", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LOCAL_DATASYSTEM_SOURCE_BUILD", None)
                with mock.patch("local_build._run_text", side_effect=[
                    " 1111111 datasystem\n 2222222 functionsystem",
                    "paths\n",
                    " 3333333 datasystem\n 2222222 functionsystem",
                    "paths\n",
                ]):
                    first = InputIdentity.from_repo(repo, image_id="sha256:1234567890abcdef", platform_name="linux", machine="amd64", bazel_major="6")
                    changed = InputIdentity.from_repo(repo, image_id="sha256:1234567890abcdef", platform_name="linux", machine="amd64", bazel_major="6")
            self.assertEqual(first.functionsystem, changed.functionsystem)
            self.assertEqual(first.runtime, changed.runtime)


class MountPlanTest(unittest.TestCase):
    def test_mount_plan_restores_nested_inputs_at_fixed_container_paths(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = pathlib.Path(temp_dir)
            repo = base / "worktree"
            cache = base / "cache"
            repo.mkdir()
            fs_input = cache / "functionsystem-inputs/fs-id"
            runtime_input = cache / "runtime-inputs/runtime-id/thirdparty"
            for directory in (
                "vendor-src",
                "vendor-output",
                "litebus-output",
                "logs-output",
                "metrics-output",
            ):
                (fs_input / directory).mkdir(parents=True)
            runtime_input.mkdir(parents=True)

            mounts = create_mount_plan(repo, cache, "fs-id", "runtime-id")

        mapping = {mount.container: mount.host for mount in mounts}
        self.assertEqual(mapping["/workspace"], repo.resolve())
        self.assertEqual(mapping["/cache"], cache.resolve())
        self.assertEqual(
            mapping["/workspace/datasystem/output"],
            (cache / "artifacts/components/unkeyed/datasystem-output").resolve(),
        )
        self.assertEqual(
            mapping["/workspace/metrics"],
            (cache / "artifacts/components/unkeyed/metrics").resolve(),
        )
        self.assertEqual(
            mapping["/workspace/datasystem/build/_deps"],
            (cache / "common/datasystem-fetchcontent").resolve(),
        )
        self.assertEqual(mapping["/workspace/functionsystem/vendor/src"], (fs_input / "vendor-src").resolve())
        self.assertEqual(mapping["/workspace/functionsystem/vendor/output"], (fs_input / "vendor-output").resolve())
        self.assertEqual(mapping["/workspace/functionsystem/common/litebus/output"], (fs_input / "litebus-output").resolve())
        self.assertEqual(mapping["/workspace/functionsystem/common/logs/output"], (fs_input / "logs-output").resolve())
        self.assertEqual(mapping["/workspace/functionsystem/common/metrics/output"], (fs_input / "metrics-output").resolve())
        self.assertEqual(mapping["/workspace/functionsystem/thirdparty/runtime_deps"], (cache / "common/functionsystem-bazel-distdir").resolve())
        self.assertEqual(mapping["/workspace/thirdparty"], runtime_input.resolve())

    def test_producer_output_is_not_bind_mounted_over_packager_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir) / "worktree"
            cache = pathlib.Path(temp_dir) / "cache"
            repo.mkdir()
            mounts = create_mount_plan(
                repo, cache, "fs-id", "runtime-id", "artifact-key",
                produced_outputs={"functionsystem-output"},
            )
        containers = {mount.container for mount in mounts}
        self.assertNotIn("/workspace/functionsystem/output", containers)
        self.assertIn("/workspace/datasystem/output", containers)

    def test_functionsystem_input_validation_rejects_empty_seed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            for directory in (
                "vendor-src",
                "vendor-output/Install/curl",
                "litebus-output/lib",
                "logs-output",
                "metrics-output",
            ):
                (root / directory).mkdir(parents=True)
            ready, missing = validate_functionsystem_inputs(root)
            self.assertFalse(ready)
            self.assertIn("litebus-output/lib/liblitebus.so", missing)
            (root / "litebus-output/lib/liblitebus.so").write_text("library", encoding="utf-8")
            ready, missing = validate_functionsystem_inputs(root)
            self.assertTrue(ready)
            self.assertEqual(missing, [])

    def test_runtime_input_validation_checks_manifest_modules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir) / "thirdparty"
            (root / "runtime_deps").mkdir(parents=True)
            (root / "boost").mkdir()
            manifest = pathlib.Path(temp_dir) / "openSource.txt"
            manifest.write_text("boost,1,runtime,x,sha\ngloo,main,runtime,x,sha\n", encoding="utf-8")
            ready, missing = validate_runtime_inputs(root, manifest)
            self.assertFalse(ready)
            self.assertIn("gloo", missing)
            (root / "gloo").mkdir()
            (root / "gloo" / "CMakeLists.txt").write_text("", encoding="utf-8")
            (root / "boost" / "boost.txt").write_text("", encoding="utf-8")
            (root / "runtime_deps" / "seed").write_text("", encoding="utf-8")
            ready, missing = validate_runtime_inputs(root, manifest)
            self.assertTrue(ready)
            self.assertEqual(missing, [])

    def test_runtime_cold_build_publishes_inputs_after_compile(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base = pathlib.Path(temp_dir)
            cache = base / "cache"
            repo = base / "repo"
            repo.mkdir()
            (repo / "thirdparty/runtime_deps").mkdir(parents=True)
            (repo / "thirdparty/runtime_deps/archive.tar.gz").write_text("archive", encoding="utf-8")
            (repo / "thirdparty/boost").mkdir()
            (repo / "thirdparty/boost/header.hpp").write_text("header", encoding="utf-8")
            runner = LocalBuildRunner.__new__(LocalBuildRunner)
            runner.repo = repo
            runner.args = mock.Mock(stamp=False)
            runner.remote_cache_enabled = False
            command = runner._docker_command(
                mock.Mock(docker_platform="linux/amd64", image="builder"),
                [mock.Mock(docker_args=lambda: [])],
                "release",
                "make yuanrong",
                "run-id",
                runtime_identity="runtime-id",
                runtime_cache_ready=False,
            )
            rendered = " ".join(command)

        self.assertIn("source /etc/profile.d/buildtools.sh", rendered)
        self.assertIn("cp -a -n /workspace/thirdparty/. /cache/runtime-inputs/runtime-id/thirdparty/", rendered)
        self.assertIn("chown -R", rendered)

    def test_component_artifacts_are_keyed_by_submodule_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            identity = InputIdentity(
                "fs-id", "runtime-id", "w", "v", "r", "submodu123456789", "adapter"
            )
            ready, missing = validate_component_artifacts(root, identity)
            self.assertFalse(ready)
            self.assertIn("datasystem-output", missing)
            artifact_root = root / f"artifacts/components/{artifact_key_for(identity)}/datasystem-output"
            artifact_root.mkdir(parents=True)
            (artifact_root / "ds.tar.gz").write_bytes(b"ds")
            fs_root = root / f"artifacts/components/{artifact_key_for(identity)}/functionsystem-output"
            fs_root.mkdir(parents=True)
            (fs_root / "fs.tar.gz").write_bytes(b"fs")
            ready, missing = validate_component_artifacts(root, identity)
            self.assertTrue(ready)
            self.assertEqual(missing, [])

    def test_component_artifact_key_includes_all_identity_fields(self):
        base = InputIdentity("fs-id", "runtime-id", "workspace", "vendor", "runtime", "submodule-a", "adapter-a")
        changed_submodule = InputIdentity("fs-id", "runtime-id", "workspace", "vendor", "runtime", "submodule-b", "adapter-a")
        self.assertNotEqual(artifact_key_for(base), artifact_key_for(changed_submodule))
        self.assertEqual(len(artifact_key_for(base)), 64)

    def test_component_artifact_key_includes_adapter_revision(self):
        base = InputIdentity("fs-id", "runtime-id", "workspace", "vendor", "runtime", "submodule", "adapter-a")
        changed_adapter = InputIdentity("fs-id", "runtime-id", "workspace", "vendor", "runtime", "submodule", "adapter-b")
        self.assertNotEqual(artifact_key_for(base), artifact_key_for(changed_adapter))
        self.assertEqual(base.functionsystem, changed_adapter.functionsystem)
        self.assertEqual(base.runtime, changed_adapter.runtime)

    def test_adapter_digest_changes_when_an_adapter_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = pathlib.Path(directory)
            for relative in LOCAL_BUILD_ADAPTER_FILES:
                path = repo / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            initial = local_build_adapter_sha256(repo)
            (repo / "build.sh").write_text("changed", encoding="utf-8")
            self.assertNotEqual(initial, local_build_adapter_sha256(repo))


class QueueTest(unittest.TestCase):
    def test_global_queue_serializes_different_components(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            ready = multiprocessing.Event()
            process = multiprocessing.Process(target=_hold_queue, args=(temp_dir, ready))
            process.start()
            self.assertTrue(ready.wait(timeout=3))
            start = time.monotonic()
            with BuildQueue(pathlib.Path(temp_dir), "functionsystem", pathlib.Path("/tmp/worktree-b")) as queue:
                waited = queue.wait_seconds
            elapsed = time.monotonic() - start
            process.join(timeout=3)

        self.assertFalse(process.is_alive())
        self.assertGreaterEqual(elapsed, 0.35)
        self.assertGreaterEqual(waited, 0.35)

    def test_acquiring_queue_recovers_metadata_left_by_dead_process(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            locks = root / "locks"
            (locks / "requests").mkdir(parents=True)
            stale = {
                "id": "dead-request",
                "operation": "prime:all:release",
                "pid": 99999999,
                "worktree": "/tmp/dead-worktree",
            }
            (locks / "requests/dead-request.json").write_text(json.dumps(stale), encoding="utf-8")
            (locks / "holder.json").write_text(json.dumps(stale), encoding="utf-8")

            before = BuildQueue.status(root)
            self.assertEqual(before["queued"], [])
            self.assertEqual(before["stale_holder"]["id"], "dead-request")
            self.assertEqual(before["stale_requests"][0]["id"], "dead-request")
            with BuildQueue(root, "yuanrong", pathlib.Path("/tmp/live-worktree")):
                self.assertFalse((locks / "requests/dead-request.json").exists())
                holder = json.loads((locks / "holder.json").read_text(encoding="utf-8"))
                self.assertNotEqual(holder["id"], "dead-request")

        self.assertFalse((locks / "holder.json").exists())


class CommandSelectionTest(unittest.TestCase):
    def test_prime_yuanrong_isolates_all_producer_outputs(self):
        self.assertEqual(
            produced_output_names("prime", "yuanrong"),
            {"datasystem-output", "functionsystem-output", "runtime-output", "metrics"},
        )

    def test_component_builds_only_requested_module(self):
        command = component_command("functionsystem", jobs=6, profile="release")
        self.assertIn("make functionsystem", command)
        self.assertNotIn("make yuanrong", command)

    def test_yuanrong_build_does_not_rebuild_upstream_modules(self):
        command = component_command("yuanrong", jobs=6)
        self.assertIn("make yuanrong", command)
        self.assertNotIn("make datasystem", command)
        self.assertNotIn("make functionsystem", command)

    def test_release_orders_datasystem_before_functionsystem_and_runtime(self):
        command = release_command(jobs=6)
        self.assertLess(command.index("make datasystem"), command.index("make functionsystem"))
        self.assertLess(command.index("make functionsystem"), command.index("make yuanrong"))

    def test_functionsystem_filters_execution_without_splitting_test_target(self):
        command = ut_command(
            "functionsystem",
            jobs=4,
            suite="InstanceProxyTest",
            case="Create",
        )
        self.assertIn("run.sh test", command)
        self.assertIn("--builder bazel", command)
        self.assertIn("-s InstanceProxyTest", command)
        self.assertIn("-c Create", command)

    def test_yuanrong_accepts_explicit_bazel_targets(self):
        command = ut_command(
            "yuanrong",
            jobs=4,
            targets=["//test/foo:foo_test", "//api/go:yr_go_test"],
        )
        self.assertIn("BAZEL_TARGETS_OVERRIDE=", command)
        self.assertIn("//test/foo:foo_test", command)
        self.assertIn("//api/go:yr_go_test", command)
        self.assertIn("YR_SKIP_PYTHON_REQUIREMENTS=1", command)

    def test_yuanrong_python_ut_keeps_python_requirements(self):
        command = ut_command("yuanrong", jobs=4, targets=["//api/python/yr/tests:unit_test"])
        self.assertNotIn("YR_SKIP_PYTHON_REQUIREMENTS=1", command)

    def test_python_nodeid_runs_only_selected_test(self):
        command = ut_command("python", jobs=2, nodeid="api/python/yr/tests/test_x.py::TestX::test_y")
        self.assertIn("python3 -m pytest", command)
        self.assertIn("test_x.py::TestX::test_y", command)

    def test_yuanrong_ut_uses_flag_form_of_test_option(self):
        command = ut_command("yuanrong", jobs=4, targets=["//test/foo:foo_test"])
        self.assertIn("bash build.sh -t -j 4", command)
        self.assertNotIn("-t run", command)
        self.assertNotIn("make datasystem", command)

    def test_yuanrong_component_uses_shared_output_mounts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir) / "repo"
            cache = pathlib.Path(temp_dir) / "cache"
            repo.mkdir()
            mounts = create_mount_plan(repo, cache, "fs-key", "runtime-key", "artifact-key")
        mapping = {mount.container: mount.host for mount in mounts}
        self.assertEqual(mapping["/workspace/datasystem/output"], (cache / "artifacts/components/artifact-key/datasystem-output").resolve())
        self.assertEqual(mapping["/workspace/functionsystem/output"], (cache / "artifacts/components/artifact-key/functionsystem-output").resolve())
        self.assertEqual(mapping["/workspace/output"], (cache / "artifacts/components/artifact-key/runtime-output").resolve())

    def test_yuanrong_ut_does_not_require_functionsystem_vendor_inputs(self):
        self.assertEqual(LocalBuildRunner._required_inputs("ut", "yuanrong"), (False, True, True))
        self.assertEqual(LocalBuildRunner._required_inputs("ut", "functionsystem"), (True, False, False))


class MetricsAndArtifactsTest(unittest.TestCase):
    def test_aio_build_requires_modern_source_controlled_entrypoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir)
            with self.assertRaisesRegex(LocalBuildError, "missing deploy/sandbox/docker/build-images.sh"):
                aio_build_command(repo, "yr-local-aio:test")

    def test_aio_build_uses_modern_entrypoint_and_image_tag(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir)
            script = repo / "deploy/sandbox/docker/build-images.sh"
            script.parent.mkdir(parents=True)
            script.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            command = aio_build_command(repo, "yr-local-aio:test")

        self.assertEqual(
            command,
            ["env", "YR_AIO_IMAGE=yr-local-aio:test", "bash", "deploy/sandbox/docker/build-images.sh"],
        )

    def test_log_capture_replaces_non_utf8_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = create_parser().parse_args(["stats"])
            runner = LocalBuildRunner(args)
            code, output, _ = runner._run_logged(
                ["bash", "-c", "printf '\\377'"], pathlib.Path(temp_dir) / "build.log"
            )
        self.assertEqual(code, 0)
        self.assertIn("\ufffd", output)

    def test_parses_bazel_vendor_and_cargo_cache_signals(self):
        metrics = parse_build_metrics(
            """
            INFO: 812 processes: 790 disk cache hit, 12 internal, 10 processwrapper-sandbox.
            Functionsystem vendor cache hit: curl
            Functionsystem vendor cache miss: openssl
               Compiling serde v1.0.0
               Compiling tokio v1.0.0
            [7/10] Building CXX object foo.o
            -- absl found in /cache/common/datasystem-opensource/absl_x...
            """
        )
        self.assertEqual(metrics["bazel_disk_hits"], 790)
        self.assertEqual(metrics["bazel_local_actions"], 10)
        self.assertEqual(metrics["vendor_hits"], 1)
        self.assertEqual(metrics["vendor_misses"], 1)
        self.assertEqual(metrics["cargo_compiling"], 2)
        self.assertEqual(metrics["ninja_actions_completed"], 7)
        self.assertEqual(metrics["ninja_actions_total"], 10)
        self.assertEqual(metrics["shared_dependency_hits"], 1)
        self.assertEqual(metrics["bazel_cached_tests"], 0)

    def test_artifact_manifest_uses_content_checksums(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir)
            (repo / "output").mkdir()
            wheel = repo / "output/openyuanrong-1.0.0-py3-none-any.whl"
            wheel.write_bytes(b"wheel-content")
            artifacts = collect_artifacts(repo)

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["source"], "output/openyuanrong-1.0.0-py3-none-any.whl")
        self.assertEqual(len(artifacts[0]["sha256"]), 64)
        self.assertEqual(artifacts[0]["size"], len(b"wheel-content"))

    def test_artifact_manifest_excludes_stale_and_nested_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = pathlib.Path(temp_dir)
            output = repo / "output"
            legacy = output / "0.7.6"
            legacy.mkdir(parents=True)
            (legacy / "openyuanrong-0.7.6.whl").write_bytes(b"legacy")
            stale = output / "yr-datasystem-v0.8.2.tar.gz"
            stale.write_bytes(b"old")
            boundary = time.time()
            time.sleep(0.01)
            current = output / "openyuanrong-v0.0.1.tar.gz"
            current.write_bytes(b"current")
            artifacts = collect_artifacts(repo, changed_after=boundary)

        self.assertEqual([artifact["source"] for artifact in artifacts], ["output/openyuanrong-v0.0.1.tar.gz"])

    def test_runtime_component_cache_excludes_stale_and_expanded_output(self):
        identity = InputIdentity("functionsystem", "runtime", "workspace", "vendor", "runtime", "submodules", "adapter")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            output = root / "output"
            (output / "openyuanrong").mkdir(parents=True)
            (output / "openyuanrong" / "large-runtime-file").write_bytes(b"expanded")
            old = output / "openyuanrong-0.7.0.whl"
            old.write_bytes(b"old")
            boundary = time.time()
            time.sleep(0.01)
            current = output / "openyuanrong-v0.0.1.tar.gz"
            current.write_bytes(b"current")
            publish_component_outputs(
                root,
                root / "cache",
                identity,
                {"runtime-output"},
                produced_after=boundary,
            )
            cached = root / "cache/artifacts/components" / artifact_key_for(identity) / "runtime-output"
            self.assertEqual([path.name for path in cached.iterdir()], ["openyuanrong-v0.0.1.tar.gz"])


class RepositoryIntegrationTest(unittest.TestCase):
    def test_makefile_keeps_ci_recipe_without_local_cache(self):
        environment = os.environ.copy()
        environment.pop("LOCAL_CACHE_ROOT", None)
        environment.pop("LOCAL_CACHE_PROFILE", None)
        result = subprocess.run(
            [
                "make",
                "-n",
                "functionsystem",
                "JOBS=16",
                "FUNCTIONSYSTEM_JOBS=8",
                "FUNCTIONSYSTEM_BUILDER=cmake",
            ],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("bash run.sh build -j 8", result.stdout)
        self.assertNotIn("with_local_build_cache.sh", result.stdout)
        self.assertNotIn("--builder bazel", result.stdout)

    def test_makefile_keeps_historical_defaults_without_ci_overrides(self):
        environment = os.environ.copy()
        environment.pop("LOCAL_CACHE_ROOT", None)
        environment.pop("LOCAL_CACHE_PROFILE", None)
        environment.pop("FUNCTIONSYSTEM_JOBS", None)
        environment.pop("FUNCTIONSYSTEM_BUILDER", None)
        result = subprocess.run(
            ["make", "-n", "functionsystem", "JOBS=16"],
            cwd=REPO_ROOT,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("bash run.sh build -j 8", result.stdout)
        self.assertNotIn("--builder cmake", result.stdout)

    def test_makefile_enables_local_cache_recipe_explicitly(self):
        result = subprocess.run(
            [
                "make",
                "-n",
                "functionsystem",
                "LOCAL_CACHE_ROOT=/tmp/yr-local-build-test-cache",
                "LOCAL_CACHE_PROFILE=release",
                "JOBS=4",
                "FUNCTIONSYSTEM_JOBS=2",
            ],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("with_local_build_cache.sh", result.stdout)
        self.assertIn("bash run.sh build -j 2 --builder bazel", result.stdout)
        self.assertIn("--bazel_cache_profile \"release\"", result.stdout)

    def test_ut_defaults_to_two_jobs_but_explicit_jobs_wins(self):
        with mock.patch.dict(os.environ, {"LOCAL_BUILD_UT_JOBS": "2"}, clear=False):
            default_args = create_parser().parse_args(["ut", "functionsystem"])
            default_runner = LocalBuildRunner(default_args)
        explicit_args = create_parser().parse_args(["ut", "functionsystem", "--jobs", "3"])
        explicit_runner = LocalBuildRunner(explicit_args)

        self.assertEqual(default_runner.jobs, min(max(1, (os.cpu_count() or 4) // 2), 2))
        self.assertEqual(explicit_runner.jobs, 3)

    def test_ut_rejects_invalid_job_environment(self):
        with mock.patch.dict(os.environ, {"LOCAL_BUILD_UT_JOBS": "zero"}, clear=False):
            args = create_parser().parse_args(["ut", "functionsystem"])
            with self.assertRaisesRegex(LocalBuildError, "LOCAL_BUILD_UT_JOBS"):
                LocalBuildRunner(args)

    def test_runner_accepts_an_explicit_worktree(self):
        args = create_parser().parse_args(["status", "--repo", str(REPO_ROOT)])
        runner = LocalBuildRunner(args)
        self.assertEqual(runner.repo, REPO_ROOT)

    def test_runner_rejects_worktree_without_cache_integration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            worktree = pathlib.Path(temp_dir)
            (worktree / "build.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (worktree / "Makefile").write_text("all:\n\t@true\n", encoding="utf-8")
            args = create_parser().parse_args(["build", "functionsystem", "--repo", str(worktree)])
            runner = LocalBuildRunner(args)
            with self.assertRaisesRegex(LocalBuildError, "does not contain the local-build cache integration"):
                runner._validate_worktree_integration()

    def test_runner_accepts_current_worktree_cache_integration(self):
        args = create_parser().parse_args(["build", "functionsystem", "--repo", str(REPO_ROOT)])
        runner = LocalBuildRunner(args)
        runner._validate_worktree_integration()

    def test_launcher_and_documented_entrypoints_exist(self):
        launcher = REPO_ROOT / "scripts/local-build.sh"
        docs = REPO_ROOT / "docs/development/local-build.md"
        self.assertTrue(launcher.is_file())
        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertTrue(docs.is_file())

    def test_build_sh_allows_profile_cache_and_target_override(self):
        build_script = (REPO_ROOT / "build.sh").read_text(encoding="utf-8")
        self.assertIn('BUILD_BASE="${BAZEL_OUTPUT_USER_ROOT:-${BASE_DIR}/build}"', build_script)
        self.assertIn('LOCAL_DISK_CACHE="${BAZEL_DISK_CACHE:-}"', build_script)
        self.assertIn('BAZEL_TARGETS_OVERRIDE', build_script)
        self.assertIn('--test_env=LD_LIBRARY_PATH=', build_script)
        self.assertIn('functionsystem/output/functionsystem/lib', build_script)
        self.assertIn('YR_SKIP_PYTHON_REQUIREMENTS', build_script)

    def test_makefile_defaults_functionsystem_to_bazel_when_cache_is_enabled(self):
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("LOCAL_BUILD_PROTOCOL ?= 2", makefile)
        self.assertIn("LOCAL_CACHE_ROOT ?=", makefile)
        self.assertIn("LOCAL_CACHE_PROFILE ?=", makefile)
        self.assertIn("FUNCTIONSYSTEM_BUILDER := bazel", makefile)
        self.assertIn("--bazel_local_cache_root", makefile)
        self.assertIn("--bazel_cache_profile", makefile)

    def test_local_cache_wrapper_defaults_to_unstamped(self):
        wrapper = (REPO_ROOT / "scripts/with_local_build_cache.sh").read_text(encoding="utf-8")
        self.assertIn('export FUNCTIONSYSTEM_BUILD_STAMP="${FUNCTIONSYSTEM_BUILD_STAMP:-0}"', wrapper)

    def test_functionsystem_ut_uses_shared_cache_root_and_ut_profile(self):
        command = ut_command("functionsystem", jobs=2)
        self.assertIn("--bazel_local_cache_root /cache", command)
        self.assertNotIn("/cache/functionsystem", command)
        self.assertIn("--bazel_cache_profile ut", command)

    def test_server_seed_resources_are_explicit_and_not_release_artifacts(self):
        self.assertEqual(SERVER_SEED_ARCHIVES["vendor"], "vendor.tar.gz")
        self.assertEqual(SERVER_SEED_ARCHIVES["ds_tmp"], "ds_tmp.tar.gz")
        self.assertNotIn("openyuanrong.tar.gz", SERVER_SEED_ARCHIVES.values())

    def test_datasystem_baseline_uses_versioned_local_filename(self):
        self.assertEqual(datasystem_baseline_filename(), "yr-datasystem-v9.9.9.tar.gz")
        self.assertIn("yr-datasystem-v9.9.9.tar.gz", component_command("datasystem", jobs=2))

    def test_datasystem_baseline_validates_package_version(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = pathlib.Path(directory) / "datasystem.tar.gz"
            payload = pathlib.Path(directory) / "datasystem-9.9.9_x86_64.jar"
            payload.write_bytes(b"baseline")
            with tarfile.open(archive, "w:gz") as stream:
                stream.add(payload, arcname="datasystem/sdk/datasystem-9.9.9_x86_64.jar")
            _validate_datasystem_baseline(archive)


if __name__ == "__main__":
    unittest.main()
