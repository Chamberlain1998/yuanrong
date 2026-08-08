#!/usr/bin/env python3

"""Docker-based local build pipeline with a shared, worktree-safe cache."""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import fcntl
import hashlib
import json
import os
import pathlib
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import socket
import tarfile
import csv
from urllib.parse import urlsplit
from collections import defaultdict
from typing import Any, Iterable

from local_build_components import COMPONENTS, UT_COMPONENTS, component_command, prime_command, release_command, ut_command


DEFAULT_IMAGE = "swr.cn-southwest-2.myhuaweicloud.com/yuanrong-dev/compile_x86:2.1"
CONTAINER_WORKSPACE = "/workspace"
CONTAINER_CACHE = "/cache"
PROFILES = ("release", "ut")
LOCAL_BUILD_PROTOCOL = "2"
# WSL can terminate the whole VM under memory pressure, so keep a cold UT
# action graph at two compiler jobs unless the developer explicitly overrides it.
DEFAULT_UT_JOBS = 2
LOCAL_BUILD_ADAPTER_FILES = (
    "Makefile",
    "build.sh",
    "scripts/package_yuanrong.sh",
    "scripts/with_local_build_cache.sh",
    "scripts/local_build_components.py",
    "api/python/setup.py",
    "functionsystem/scripts/executor/builder/build_bazel.py",
    "functionsystem/scripts/executor/make_functionsystem.py",
    "functionsystem/scripts/executor/tasks/build_task.py",
    "functionsystem/scripts/executor/tasks/test_task.py",
)
SERVER_ARCHIVE_BASE = (
    "https://build-logs.openeuler.openatom.cn:38080/"
    "temp-archived/openeuler/openYuanrong/yr_cache/x86_64"
)
SERVER_SEED_ARCHIVES = {
    "datasystem": "yr-datasystem.tar.gz",
    "metrics": "metrics.tar.gz",
    "ds_tmp": "ds_tmp.tar.gz",
    "vendor": "vendor.tar.gz",
}
# Keep the approved package baseline in one policy knob.  Developers normally
# consume the prebuilt 9.9.9 archive; an explicit source build opts out.
DATASYSTEM_BASELINE_VERSION = os.environ.get("LOCAL_DATASYSTEM_BASELINE_VERSION", "9.9.9").strip() or "9.9.9"
AIO_BUILD_SCRIPT = pathlib.Path("deploy/sandbox/docker/build-images.sh")


def datasystem_baseline_filename() -> str:
    return f"yr-datasystem-v{DATASYSTEM_BASELINE_VERSION}.tar.gz"


def _validate_datasystem_baseline(archive_path: pathlib.Path) -> None:
    """Ensure the generic archive URL really contains the configured version."""
    version_pattern = re.compile(rf"(?<![0-9.]){re.escape(DATASYSTEM_BASELINE_VERSION)}(?![0-9.])")
    try:
        with tarfile.open(archive_path, "r:*") as archive:
            names = (member.name for member in archive)
            if not any(version_pattern.search(name) for name in names):
                raise LocalBuildError(
                    f"DataSystem baseline {archive_path} does not contain version {DATASYSTEM_BASELINE_VERSION}"
                )
    except tarfile.TarError as error:
        raise LocalBuildError(f"invalid DataSystem baseline {archive_path}: {error}") from error


class LocalBuildError(RuntimeError):
    """A user-actionable local pipeline error."""


def aio_build_command(repo: pathlib.Path, image_tag: str) -> list[str]:
    """Resolve the source-controlled AIO image entry point.

    The legacy ``example/aio`` Dockerfile names fixed, obsolete wheel files and
    references files that are absent from current checkouts.  It must not turn
    a successful release build into a misleading AIO claim.  Modern branches
    own the supported staged-image pipeline under ``deploy/sandbox/docker``.
    """
    script = repo / AIO_BUILD_SCRIPT
    if script.is_file():
        return ["env", f"YR_AIO_IMAGE={image_tag}", "bash", str(AIO_BUILD_SCRIPT)]
    raise LocalBuildError(
        "AIO validation is unavailable for this worktree: missing "
        f"{AIO_BUILD_SCRIPT}. The checked-in example/aio Dockerfile is a legacy "
        "layout and is not compatible with current split-wheel release artifacts. "
        "Rebase to a branch containing the modern AIO builder before using --aio."
    )


def _remote_cache_reachable(value: str, timeout: float = 1.5) -> bool:
    """Return whether a remote-cache endpoint accepts a TCP connection.

    The CI endpoint is a cluster-internal DNS name.  Probing it on the host
    lets local builds fall back immediately to the local disk cache instead
    of waiting for Bazel's remote-cache retry timeout when outside that
    network.
    """
    raw = value.strip()
    if not raw:
        return False
    parsed = urlsplit(raw if "://" in raw else f"grpc://{raw}")
    host = parsed.hostname
    if not host:
        return False
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "grpc":
        port = 9092
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _normalize_machine(machine: str) -> str:
    return {"x86_64": "amd64", "aarch64": "arm64"}.get(machine.lower(), machine.lower())


def default_cache_root(repo: pathlib.Path) -> pathlib.Path:
    """Find one cache root for all worktrees sharing a Git common directory."""
    fallback = repo.parent / ".yr-cache" / "yuanrong-local-build-profile"
    common_dir = _run_text(["git", "rev-parse", "--git-common-dir"], repo, check=False)
    if not common_dir:
        return fallback
    common_path = pathlib.Path(common_dir)
    if not common_path.is_absolute():
        common_path = repo / common_path
    common_path = common_path.resolve()
    # Git's common directory is normally <primary-worktree>/.git.  Its parent
    # identifies the worktree family even when the selected worktree lives in
    # a nested .worktrees directory or an external path.
    if common_path.name != ".git":
        return fallback
    return common_path.parent.parent / ".yr-cache" / "yuanrong-local-build-profile"


def _submodule_identity_status(repo: pathlib.Path, *, include_datasystem: bool = True) -> str:
    """Return deterministic gitlink *and* worktree state for cache identity.

    ``git submodule status`` records the checked-out gitlink but does not
    distinguish a dirty submodule at that same commit.  A dirty DataSystem or
    FunctionSystem worktree can change generated headers and package inputs,
    so borrowing an identity created from a clean checkout is unsafe.
    """
    raw_status = _run_text(["git", "submodule", "status", "--recursive"], repo, check=False)
    if not include_datasystem:
        # The normal local policy consumes the approved DataSystem package
        # baseline, so a checked-out DataSystem gitlink is not an input to the
        # downstream FunctionSystem/runtime identities.  Keep other
        # submodules as safety boundaries.  A source DataSystem build opts
        # back in below.
        kept = []
        for line in raw_status.splitlines():
            fields = line.lstrip(" +-\t").split()
            path = fields[1] if len(fields) > 1 else ""
            if path == "datasystem" or path.startswith("datasystem/"):
                continue
            kept.append(line)
        raw_status = "\n".join(kept)
    lines = [raw_status]
    submodules = _run_text(["git", "config", "--file", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"], repo, check=False)
    for line in submodules.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        path = parts[1].strip()
        if not include_datasystem and (path == "datasystem" or path.startswith("datasystem/")):
            continue
        # Untracked files are commonly generated protobuf/Go sources.  They
        # are direct source inputs to Bazel and therefore already participate
        # in action keys; including them here would make the identity change
        # during a normal build and strand the just-produced cache.  Tracked
        # edits remain part of the dependency identity as a safety boundary.
        status_lines = _run_text(
            ["git", "-C", path, "status", "--porcelain=v1", "--untracked-files=no"],
            repo,
            check=False,
        ).splitlines()
        status = "\n".join(status_lines)
        lines.append(f"{path}\n{status}")
    return "\n".join(lines)


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def local_build_adapter_sha256(repo: pathlib.Path) -> str:
    """Hash source-controlled adapters that define local-build semantics.

    The shared cache is intentionally outside a worktree, but its generated
    inputs and package outputs must not cross a partial protocol upgrade. A
    numeric protocol version catches deliberate breaking revisions; this
    digest additionally isolates a worktree with locally modified or
    incompletely cherry-picked adapters until that revision is committed.
    """
    digest = hashlib.sha256()
    digest.update(f"local-build-protocol:{LOCAL_BUILD_PROTOCOL}\n".encode("utf-8"))
    for relative in LOCAL_BUILD_ADAPTER_FILES:
        path = repo / relative
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        # InputIdentity is also used by lightweight test fixtures. The build
        # entry point separately rejects a real worktree that lacks an
        # adapter, while this deterministic marker keeps identity calculation
        # total and prevents a missing file from colliding with any real one.
        digest.update((_sha256(path) if path.is_file() else "<missing>").encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _datasystem_expected_version(repo: pathlib.Path, cache_root: pathlib.Path | None = None) -> str:
    """Resolve the approved local DataSystem package version.

    Local development intentionally follows the server-provided 9.9.9
    baseline.  Setting LOCAL_DATASYSTEM_SOURCE_BUILD opts back into the
    checked-out submodule version for an explicit DataSystem update.
    """
    if os.environ.get("LOCAL_DATASYSTEM_SOURCE_BUILD"):
        version_file = repo / "datasystem/VERSION"
        return version_file.read_text(encoding="utf-8").strip() if version_file.is_file() else ""
    return DATASYSTEM_BASELINE_VERSION


def _atomic_json(path: pathlib.Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _download_server_seed(cache_root: pathlib.Path, resource: str) -> tuple[pathlib.Path, dict[str, Any]]:
    """Download one public archive into an immutable, auditable staging area."""
    if resource not in SERVER_SEED_ARCHIVES:
        raise LocalBuildError(f"unsupported server seed: {resource}")
    filename = SERVER_SEED_ARCHIVES[resource]
    seed_root = cache_root / "server-seeds" / "yr_cache-x86_64"
    seed_root.mkdir(parents=True, exist_ok=True)
    destination = seed_root / filename
    url = f"{SERVER_ARCHIVE_BASE}/{filename}"
    if not destination.is_file():
        import urllib.request

        temporary = destination.with_name(f".{filename}.{os.getpid()}.part")
        try:
            with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as stream:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    stream.write(chunk)
            os.replace(temporary, destination)
        except Exception as error:
            temporary.unlink(missing_ok=True)
            raise LocalBuildError(f"unable to download server seed {url}: {error}") from error
    if resource == "datasystem":
        _validate_datasystem_baseline(destination)
        # The archive service keeps the historical generic URL, while local
        # builds consume an immutable, versioned filename.  A hard link avoids
        # storing the 262 MB package twice and makes the selected baseline
        # unambiguous to Docker/Make.
        versioned = seed_root / datasystem_baseline_filename()
        if not versioned.exists():
            try:
                os.link(destination, versioned)
            except OSError:
                shutil.copy2(destination, versioned)
    size = destination.stat().st_size
    digest = _sha256(destination)
    try:
        with tarfile.open(destination, "r:*") as archive:
            first_member = next(iter(archive), None)
            archive_valid = first_member is not None
    except (OSError, tarfile.TarError) as error:
        raise LocalBuildError(f"server seed is not a valid non-empty archive: {url}: {error}") from error
    metadata = {
        "resource": resource,
        "filename": filename,
        "url": url,
        "size": size,
        "sha256": digest,
        "archive_valid": archive_valid,
        "downloaded_at": _now(),
        "baseline_filename": datasystem_baseline_filename() if resource == "datasystem" else None,
    }
    _atomic_json(seed_root / f"{filename}.json", metadata)
    return destination, metadata


def _safe_seed_member(root: pathlib.Path, member: tarfile.TarInfo, prefix: str) -> pathlib.Path | None:
    """Return a safe destination for a regular archive member."""
    if not member.name.startswith(prefix) or not member.isfile():
        return None
    relative = pathlib.PurePosixPath(member.name[len(prefix):].lstrip("/"))
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise LocalBuildError(f"unsafe path in server seed: {member.name}")
    destination = (root / pathlib.Path(*relative.parts)).resolve()
    if root.resolve() not in destination.parents:
        raise LocalBuildError(f"server seed escapes destination: {member.name}")
    return destination


def _import_ds_tmp(cache_root: pathlib.Path, archive: pathlib.Path) -> dict[str, int]:
    """Import only absent DataSystem install-cache files; never overwrite local state."""
    destination_root = cache_root / "common" / "datasystem-opensource"
    destination_root.mkdir(parents=True, exist_ok=True)
    imported = skipped = 0
    with tarfile.open(archive, "r:*") as stream:
        for member in stream:
            destination = _safe_seed_member(destination_root, member, "ds_tmp/")
            if destination is None:
                continue
            if destination.exists():
                skipped += 1
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = stream.extractfile(member)
            if source is None:
                continue
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.seed")
            try:
                with temporary.open("wb") as output:
                    shutil.copyfileobj(source, output)
                os.replace(temporary, destination)
                imported += 1
            finally:
                temporary.unlink(missing_ok=True)
    return {"imported": imported, "skipped_existing": skipped}


def _run_text(command: list[str], cwd: pathlib.Path, *, check: bool = True) -> str:
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise LocalBuildError(f"command failed ({shlex.join(command)}): {detail}")
    return result.stdout.strip()


@dataclasses.dataclass(frozen=True)
class CacheLayout:
    root: pathlib.Path
    platform_name: str = dataclasses.field(default_factory=lambda: platform.system().lower())
    machine: str = dataclasses.field(default_factory=lambda: platform.machine().lower())
    bazel_major: str = "6"
    rust_version: str = "unknown"
    go_version: str = "unknown"

    @property
    def bazel_namespace(self) -> str:
        return f"{self.platform_name}-{self.machine}-bazel{self.bazel_major}"

    @property
    def rust_namespace(self) -> str:
        return f"{self.platform_name}-{self.machine}-rust{self.rust_version}"

    @property
    def go_namespace(self) -> str:
        return f"{self.platform_name}-{self.machine}-go{self.go_version}"

    def environment(self, profile_name: str) -> dict[str, str]:
        if profile_name not in PROFILES:
            raise ValueError(f"unsupported profile: {profile_name}")
        root = self.root.resolve()
        common = root / "common"
        profile_root = root / "profiles" / profile_name
        bazel_output = profile_root / "bazel-output" / self.bazel_namespace
        return {
            "YR_LOCAL_CACHE_ROOT": str(root),
            "YR_LOCAL_CACHE_PROFILE": profile_name,
            "BAZEL_REPOSITORY_CACHE": str(common / "bazel-repository" / self.bazel_namespace),
            "CARGO_HOME": str(common / "cargo"),
            "GOPATH": str(common / "go"),
            "GOMODCACHE": str(common / "go-mod"),
            "GOBIN": str(common / "go-bin"),
            "PIP_CACHE_DIR": str(common / "pip"),
            "npm_config_cache": str(common / "npm"),
            "GRADLE_USER_HOME": str(common / "gradle"),
            "CCACHE_DIR": str(common / "ccache"),
            "SCCACHE_DIR": str(common / "sccache"),
            "CCACHE_MAXSIZE": os.environ.get("CCACHE_MAXSIZE", "100G"),
            "SCCACHE_CACHE_SIZE": os.environ.get("SCCACHE_CACHE_SIZE", "20G"),
            "DS_OPENSOURCE_DIR": str(common / "datasystem-opensource"),
            "FS_VENDOR_CACHE_DIR": str(common / "functionsystem-vendor-cache"),
            "BAZEL_OUTPUT_USER_ROOT": str(bazel_output),
            "BAZEL_OUTPUT_BASE": str(bazel_output / "output-base"),
            "BAZEL_DISK_CACHE": str(profile_root / "bazel-action" / self.bazel_namespace),
            "CARGO_TARGET_DIR": str(profile_root / "cargo-target" / self.rust_namespace),
            "GOCACHE": str(profile_root / "go-build" / self.go_namespace),
            "CARGO_INCREMENTAL": os.environ.get("CARGO_INCREMENTAL", "0"),
        }

    def initialize(self) -> None:
        # Tool-specific namespaces are created inside the compile container,
        # which may own existing profile directories as root. Host init only
        # creates stable topology and host-written metadata directories.
        for relative in (
            "common",
            # CMake FetchContent keeps downloaded sources and its populate
            # stamps below <build>/_deps.  Keep that input/download layer in
            # the shared cache as well; the worktree's generated build tree
            # remains private, while the host compile queue prevents two
            # CMake processes from mutating this directory concurrently.
            "common/datasystem-fetchcontent",
            "profiles",
            "functionsystem",
            "functionsystem-inputs",
            "runtime-inputs",
            "runs",
            "logs",
            "locks/requests",
            "artifacts/releases",
            "artifacts/components",
        ):
            (self.root / relative).mkdir(parents=True, exist_ok=True)


@dataclasses.dataclass(frozen=True)
class InputIdentity:
    functionsystem: str
    runtime: str
    workspace_sha256: str
    vendor_sha256: str
    runtime_sha256: str
    submodules_sha256: str
    adapter_sha256: str

    @classmethod
    def from_repo(
        cls,
        repo: pathlib.Path,
        *,
        image_id: str,
        platform_name: str,
        machine: str,
        bazel_major: str,
    ) -> "InputIdentity":
        files = {
            "workspace": repo / "functionsystem/WORKSPACE",
            "vendor": repo / "functionsystem/vendor/VendorList.csv",
            "runtime": repo / "tools/openSource.txt",
        }
        missing = [str(path) for path in files.values() if not path.is_file()]
        if missing:
            raise LocalBuildError(f"dependency identity files missing: {', '.join(missing)}")
        digests = {key: _sha256(value) for key, value in files.items()}
        # A root worktree only records submodule gitlinks.  Include their
        # recursive status in the cache identity so a datasystem,
        # functionsystem, or frontend pin change cannot borrow an old input
        # tree.  Temporary fixture repos used by unit tests may not be Git
        # repositories, in which case the empty status is still deterministic.
        source_datasystem = bool(os.environ.get("LOCAL_DATASYSTEM_SOURCE_BUILD"))
        submodule_status = _submodule_identity_status(repo, include_datasystem=source_datasystem)
        if not source_datasystem:
            submodule_status = f"datasystem-baseline:{DATASYSTEM_BASELINE_VERSION}\n{submodule_status}"
        submodules_sha256 = hashlib.sha256(submodule_status.encode("utf-8")).hexdigest()
        adapter_sha256 = local_build_adapter_sha256(repo)
        machine = _normalize_machine(machine)
        image_short = image_id.removeprefix("sha256:")[:8]
        suffix = f"{platform_name}-{machine}-bazel{bazel_major}-img{image_short}"
        # These names select only immutable source/download input trees. Their
        # actual inputs are the manifests, submodule state and toolchain above;
        # keeping adapter revisions out preserves vendor/runtime reuse when a
        # packaging or queue adapter changes. artifact_key_for still hashes
        # adapter_sha256, so final component packages never cross an adapter
        # revision.
        fs_identity = f"{digests['workspace'][:8]}-{digests['vendor'][:8]}-{submodules_sha256[:8]}-{suffix}"
        runtime_identity = f"{digests['runtime'][:8]}-{submodules_sha256[:8]}-{platform_name}-{machine}-img{image_short}"
        return cls(
            fs_identity,
            runtime_identity,
            digests["workspace"],
            digests["vendor"],
            digests["runtime"],
            submodules_sha256,
            adapter_sha256,
        )


def artifact_key_for(identity: InputIdentity) -> str:
    """Key downstream package outputs by the complete dependency identity.

    The human-readable identity prefixes intentionally start with the
    workspace/vendor hashes.  Truncating them again for artifact paths drops
    the submodule-status, image, and platform suffixes, allowing incompatible
    worktrees to select one package-output directory.  Use one stable digest
    of all fields instead.
    """
    encoded = json.dumps(dataclasses.asdict(identity), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def produced_output_names(command: str, component: str) -> set[str]:
    """Outputs that the command itself is allowed to replace.

    Producers must keep their output directories worktree-local: project
    packagers commonly delete/recreate them, which is incompatible with a
    bind-mounted cache directory.  They are published atomically after a
    successful container run.  All other output mounts remain shared inputs.
    """
    names = {
        "datasystem": "datasystem-output",
        "functionsystem": "functionsystem-output",
        "frontend": "frontend-output",
        "dashboard": "dashboard-output",
        "yuanrong": "runtime-output",
    }
    # Unit tests consume prepared packages but do not publish a package output.
    # Treating them as producers makes the Docker teardown chown an optional
    # worktree-local output directory that the test command never creates.
    if command == "ut":
        return set()
    if command == "release":
        return set(names.values()) | {"metrics"}
    if command == "prime":
        if component == "functionsystem":
            return {names["datasystem"], names["functionsystem"], "metrics"}
        if component == "yuanrong":
            return {names["datasystem"], names["functionsystem"], names["yuanrong"], "metrics"}
        if component == "all":
            return set(names.values()) | {"metrics"}
    if command in {"prime", "build", "ut"} and component in names:
        return {names[component]}
    return set()


@dataclasses.dataclass(frozen=True)
class Mount:
    host: pathlib.Path
    container: str
    read_only: bool = False

    def docker_args(self) -> list[str]:
        suffix = ":ro" if self.read_only else ""
        return ["-v", f"{self.host}:{self.container}{suffix}"]


def create_mount_plan(
    repo: pathlib.Path,
    cache_root: pathlib.Path,
    functionsystem_identity: str | None,
    runtime_identity: str | None,
    artifact_key: str | None = None,
    runtime_cache_ready: bool = True,
    runtime_seed_dir: pathlib.Path | None = None,
    produced_outputs: set[str] | None = None,
) -> list[Mount]:
    repo = repo.resolve()
    cache_root = cache_root.resolve()
    mounts = [Mount(repo, CONTAINER_WORKSPACE), Mount(cache_root, CONTAINER_CACHE)]
    # Upstream package outputs are shared inputs for YuanRong/AIO/3VM. They
    # live outside worktrees and are restored at the paths existing scripts
    # already consume.
    artifact_key = artifact_key or "unkeyed"
    output_mounts = {
        "datasystem-output": Mount(
            cache_root / "artifacts" / "components" / artifact_key / "datasystem-output",
            "/workspace/datasystem/output",
        ),
        "functionsystem-output": Mount(
            cache_root / "artifacts" / "components" / artifact_key / "functionsystem-output",
            "/workspace/functionsystem/output",
        ),
        "frontend-output": Mount(
            cache_root / "artifacts" / "components" / artifact_key / "frontend-output",
            "/workspace/frontend/output",
        ),
        "dashboard-output": Mount(
            cache_root / "artifacts" / "components" / artifact_key / "dashboard-output",
            "/workspace/go/output",
        ),
        "runtime-output": Mount(
            cache_root / "artifacts" / "components" / artifact_key / "runtime-output",
            "/workspace/output",
        ),
        "metrics": Mount(
            cache_root / "artifacts" / "components" / artifact_key / "metrics",
            "/workspace/metrics",
        ),
    }
    produced_outputs = produced_outputs or set()
    mounts.extend(
        mount for name, mount in output_mounts.items() if name not in produced_outputs
    )
    # DataSystem's CMake dependency sources otherwise disappear with a new
    # worktree.  Only _deps is shared; CMake's generated top-level build state
    # and install output stay in the worktree (and are still selected by the
    # release/UT profile).  The fixed /workspace path keeps CMake cache keys
    # stable across worktrees.
    mounts.append(Mount(cache_root / "common/datasystem-fetchcontent", "/workspace/datasystem/build/_deps"))
    if functionsystem_identity:
        fs_root = cache_root / "functionsystem-inputs" / functionsystem_identity
        mounts.extend(
            (
                Mount(fs_root / "vendor-src", "/workspace/functionsystem/vendor/src"),
                Mount(fs_root / "vendor-output", "/workspace/functionsystem/vendor/output"),
                Mount(fs_root / "litebus-output", "/workspace/functionsystem/common/litebus/output"),
                Mount(fs_root / "logs-output", "/workspace/functionsystem/common/logs/output"),
                Mount(fs_root / "metrics-output", "/workspace/functionsystem/common/metrics/output"),
                Mount(cache_root / "common/functionsystem-bazel-distdir", "/workspace/functionsystem/thirdparty/runtime_deps"),
            )
        )
    # Do not mount an empty identity directory over the worktree.  On a cold
    # cache the worktree's checked-out thirdparty tree is the source used by
    # `prime`; once validated, the identity-scoped tree is mounted for stable
    # cross-worktree reuse.
    if runtime_identity and runtime_cache_ready:
        mounts.append(
            Mount(cache_root / "runtime-inputs" / runtime_identity / "thirdparty", "/workspace/thirdparty")
        )
    elif runtime_identity and runtime_seed_dir and runtime_seed_dir.is_dir():
        # A previous identity may provide Bazel download archives.  Mount
        # only its runtime_deps subtree read-only as a bootstrap seed; source
        # directories remain in the current worktree and the current identity
        # is not considered ready until its own manifest is populated.
        mounts.append(
            Mount(runtime_seed_dir.resolve(), "/workspace/thirdparty/runtime_deps", read_only=True)
        )
    return mounts


def prepare_input_directories(cache_root: pathlib.Path, identity: InputIdentity) -> None:
    fs_root = cache_root / "functionsystem-inputs" / identity.functionsystem
    for relative in ("vendor-src", "vendor-output", "litebus-output", "logs-output", "metrics-output"):
        (fs_root / relative).mkdir(parents=True, exist_ok=True)
    (cache_root / "runtime-inputs" / identity.runtime / "thirdparty").mkdir(parents=True, exist_ok=True)
    (cache_root / "common/functionsystem-bazel-distdir").mkdir(parents=True, exist_ok=True)
    component_root = cache_root / "artifacts" / "components"
    artifact_key = artifact_key_for(identity)
    for relative in (
        f"{artifact_key}/datasystem-output",
        f"{artifact_key}/frontend-output",
        f"{artifact_key}/dashboard-output",
        f"{artifact_key}/functionsystem-output",
        f"{artifact_key}/runtime-output",
        f"{artifact_key}/metrics",
    ):
        (component_root / relative).mkdir(parents=True, exist_ok=True)


def validate_functionsystem_inputs(root: pathlib.Path) -> tuple[bool, list[str]]:
    required = (
        "vendor-src",
        "vendor-output/Install/curl",
        "litebus-output/lib/liblitebus.so",
        "logs-output",
        "metrics-output",
    )
    # Some generated vendor entries are absolute links to /cache paths.  They
    # are intentionally valid inside Docker even though the host cannot
    # resolve the container-only prefix; lexists preserves that input.
    missing = [relative for relative in required if not os.path.lexists(root / relative)]
    return not missing, missing


def validate_component_artifacts(
    cache_root: pathlib.Path,
    identity: InputIdentity,
    expected_datasystem_version: str | None = None,
) -> tuple[bool, list[str]]:
    """Check upstream package outputs before a downstream hot build."""
    artifact_root = cache_root / "artifacts" / "components" / artifact_key_for(identity)
    # DataSystem packages are versioned inputs.  Merely finding any tarball
    # here is unsafe because an older server seed (or a previous branch) may
    # coexist with the current package.  Require the exact version checked
    # out by this worktree; callers then mount a directory that cannot make a
    # downstream script select an unrelated DataSystem release.
    expected_version = (expected_datasystem_version or "").strip()
    datasystem_root = artifact_root / "datasystem-output"
    expected_datasystem = (
        datasystem_root / f"yr-datasystem-v{expected_version}.tar.gz"
        if expected_version
        else datasystem_root
    )
    required = {
        "datasystem-output": expected_datasystem,
        "functionsystem-output": cache_root / "artifacts" / "components" / artifact_key_for(identity) / "functionsystem-output",
    }
    missing: list[str] = []
    for name, path in required.items():
        if path is None:
            missing.append(name)
        elif name == "datasystem-output" and (
            (expected_version and not path.is_file())
            or (not expected_version and (not path.is_dir() or not any(path.glob("*.tar.gz"))))
        ):
            missing.append(name)
        elif name != "datasystem-output" and (not path.is_dir() or not any(path.glob("*.tar.gz"))):
            missing.append(name)
    return not missing, missing


def _runtime_dependency_names(manifest: pathlib.Path | None) -> list[str]:
    if manifest is None or not manifest.is_file():
        return []
    names: list[str] = []
    try:
        with manifest.open(newline="", encoding="utf-8") as stream:
            for row in csv.reader(stream):
                if len(row) >= 3 and "runtime" in row[2] and row[0].strip():
                    names.append(row[0].strip())
    except (OSError, UnicodeError, csv.Error):
        return []
    return sorted(set(names))


def validate_runtime_inputs(
    root: pathlib.Path, manifest: pathlib.Path | None = None
) -> tuple[bool, list[str]]:
    missing: list[str] = []
    runtime_deps = root / "runtime_deps"
    if not runtime_deps.is_dir() or not any(runtime_deps.iterdir()):
        missing.append("runtime_deps (non-empty)")
    for name in _runtime_dependency_names(manifest):
        dependency = root / name
        if not dependency.is_dir() or not any(dependency.iterdir()):
            missing.append(name)
    if not missing and not next(root.rglob("*"), None):
        missing.append("non-empty thirdparty tree")
    return not missing, missing


def seed_runtime_dependency_archives(
    repo: pathlib.Path, cache_root: pathlib.Path, identity: InputIdentity
) -> dict[str, int]:
    """Copy only hash-matching runtime archive files from older identities.

    Runtime dependency archives are content-addressed inputs.  Matching the
    SHA-256 from the current ``openSource.txt`` makes this safe across old
    identities, while generated source trees and compiler outputs remain
    identity-local.
    """
    expected: set[str] = set()
    manifest = repo / "tools/openSource.txt"
    try:
        with manifest.open(newline="", encoding="utf-8") as stream:
            for row in csv.reader(stream):
                if len(row) >= 5 and "runtime" in row[2] and row[4].strip():
                    expected.add(row[4].strip().lower())
    except (OSError, UnicodeError, csv.Error):
        return {}
    destination = cache_root / "runtime-inputs" / identity.runtime / "thirdparty/runtime_deps"
    destination.mkdir(parents=True, exist_ok=True)
    copied = 0
    seen: set[str] = set()
    root = cache_root / "runtime-inputs"
    if not root.is_dir():
        return {}
    for identity_root in root.iterdir():
        if not identity_root.is_dir() or identity_root.name == identity.runtime:
            continue
        candidate = identity_root / "thirdparty/runtime_deps"
        if not candidate.is_dir():
            continue
        try:
            entries = candidate.iterdir()
            for source in entries:
                if not source.is_file() or source.name in seen:
                    continue
                seen.add(source.name)
                try:
                    digest = _sha256(source)
                except OSError:
                    continue
                if digest not in expected:
                    continue
                target = destination / source.name
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
                copied += 1
        except OSError:
            continue
    return {"runtime_deps": copied} if copied else {}


def seed_inputs_from_worktree(
    repo: pathlib.Path, cache_root: pathlib.Path, identity: InputIdentity
) -> dict[str, int]:
    """Safely fill identity-scoped dependency inputs from this worktree.

    This is deliberately missing-file-only: a different worktree can never
    overwrite an existing identity cache.  It lets a freshly-created worktree
    adopt already downloaded vendor/runtime inputs without treating a server
    archive or an old identity as a current build result.
    """
    copied: dict[str, int] = {}
    fs_root = cache_root / "functionsystem-inputs" / identity.functionsystem
    fs_mapping = {
        "vendor-src": repo / "functionsystem/vendor/src",
        "vendor-output": repo / "functionsystem/vendor/output",
        "litebus-output": repo / "functionsystem/common/litebus/output",
        "logs-output": repo / "functionsystem/common/logs/output",
        "metrics-output": repo / "functionsystem/common/metrics/output",
    }
    runtime_root = cache_root / "runtime-inputs" / identity.runtime / "thirdparty"
    runtime_mapping = {
        name: repo / "thirdparty" / name
        for name in ("boost", "gloo", "grpc", "libboundscheck", "openssl", "spdlog", "runtime_deps")
    }
    for name, source in {**fs_mapping, **runtime_mapping}.items():
        destination = (fs_root if name in fs_mapping else runtime_root) / name
        if not source.is_dir():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        count = 0
        try:
            for item in source.rglob("*"):
                if not item.is_file():
                    continue
                target = destination / item.relative_to(source)
                if target.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, target, follow_symlinks=False)
                count += 1
        except (OSError, PermissionError):
            # Legacy worktree directories may be owned by the compile
            # container. Optional seeds must not make the build abort.
            continue
        if count:
            copied[name] = count
    return copied


@dataclasses.dataclass(frozen=True)
class Toolchain:
    image: str
    image_id: str
    os: str
    architecture: str

    @property
    def docker_platform(self) -> str:
        return f"{self.os}/{self.architecture}"


def inspect_toolchain(image: str) -> Toolchain:
    result = subprocess.run(["docker", "image", "inspect", image], text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise LocalBuildError(f"builder image is unavailable: {image}; pull or build it before using local-build")
    try:
        data = json.loads(result.stdout)[0]
    except (json.JSONDecodeError, IndexError, KeyError) as error:
        raise LocalBuildError(f"unable to inspect builder image {image}: {error}") from error
    return Toolchain(image, data["Id"], data.get("Os", "linux"), data.get("Architecture", "amd64"))


class BuildQueue:
    """One host-wide compile queue shared by all worktrees and components."""

    def __init__(self, cache_root: pathlib.Path, operation: str, worktree: pathlib.Path):
        self.cache_root = cache_root.resolve()
        self.operation = operation
        self.worktree = worktree.resolve()
        self.request_id = uuid.uuid4().hex
        self.wait_seconds = 0.0
        self._stream: Any = None

    @property
    def lock_root(self) -> pathlib.Path:
        return self.cache_root / "locks"

    def __enter__(self) -> "BuildQueue":
        request = {
            "id": self.request_id,
            "operation": self.operation,
            "pid": os.getpid(),
            "worktree": str(self.worktree),
            "queued_at": _now(),
        }
        request_path = self.lock_root / "requests" / f"{self.request_id}.json"
        _atomic_json(request_path, request)
        self.lock_root.mkdir(parents=True, exist_ok=True)
        self._stream = (self.lock_root / "build.lock").open("a+", encoding="utf-8")
        started = time.monotonic()
        fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX)
        self.wait_seconds = time.monotonic() - started
        request_path.unlink(missing_ok=True)
        # The advisory file lock is the source of truth. A process killed
        # while compiling releases this lock automatically, but it cannot run
        # __exit__ to remove its status files. Once this process owns the
        # lock, safely reclaim only records whose owner PID no longer exists.
        self._reclaim_stale_metadata()
        request.update({"acquired_at": _now(), "wait_seconds": round(self.wait_seconds, 3)})
        _atomic_json(self.lock_root / "holder.json", request)
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        holder_path = self.lock_root / "holder.json"
        try:
            holder = json.loads(holder_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            holder = {}
        if holder.get("id") == self.request_id:
            holder_path.unlink(missing_ok=True)
        if self._stream is not None:
            fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()

    @staticmethod
    def _pid_is_alive(pid: Any) -> bool:
        try:
            if not pid:
                return False
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Another local user owns the process. Treat it as live rather
            # than risking removal of an active queue record.
            return True
        except (TypeError, ValueError):
            return False

    def _reclaim_stale_metadata(self) -> None:
        """Remove status files left by killed processes while holding the lock."""
        holder_path = self.lock_root / "holder.json"
        try:
            holder = json.loads(holder_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            holder = None
        if holder is not None and not self._pid_is_alive(holder.get("pid")):
            holder_path.unlink(missing_ok=True)
        requests = self.lock_root / "requests"
        for request_path in requests.glob("*.json"):
            try:
                request = json.loads(request_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # A partially-written request cannot represent a live queued
                # process because requests are published atomically.
                request_path.unlink(missing_ok=True)
                continue
            if not self._pid_is_alive(request.get("pid")):
                request_path.unlink(missing_ok=True)

    @staticmethod
    def status(cache_root: pathlib.Path) -> dict[str, Any]:
        lock_root = cache_root / "locks"
        try:
            holder = json.loads((lock_root / "holder.json").read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            holder = None
        stale_holder = None
        if holder and not BuildQueue._pid_is_alive(holder.get("pid")):
            stale_holder = holder
            holder = None
        queued = []
        stale_requests = []
        for path in sorted((lock_root / "requests").glob("*.json")):
            try:
                request = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                stale_requests.append({"path": str(path), "reason": "invalid-json"})
                continue
            if BuildQueue._pid_is_alive(request.get("pid")):
                queued.append(request)
            else:
                stale_requests.append(request)
        result: dict[str, Any] = {"holder": holder, "queued": queued}
        if stale_holder is not None:
            result["stale_holder"] = stale_holder
        if stale_requests:
            result["stale_requests"] = stale_requests
        return result


def parse_build_metrics(log_text: str) -> dict[str, int]:
    def total(pattern: str) -> int:
        return sum(int(match.group(1)) for match in re.finditer(pattern, log_text, re.IGNORECASE))

    disk_hits = total(r"(\d+)\s+disk cache hit")
    remote_hits = total(r"(\d+)\s+remote cache hit")
    local_actions = total(r"(\d+)\s+(?:processwrapper-sandbox|local)(?:[,.\s]|$)")
    internal_actions = total(r"(\d+)\s+internal(?:[,.\s]|$)")
    action_total = total(r"INFO:\s*(\d+)\s+processes?:")
    ninja_steps = [
        (int(match.group(1)), int(match.group(2)))
        for match in re.finditer(r"\[(\d+)/(\d+)\]", log_text)
    ]
    ninja_completed = max((step[0] for step in ninja_steps), default=0)
    ninja_total = max((step[1] for step in ninja_steps), default=0)
    cached_tests = len(re.findall(r"^//[^\n]*\(cached\)\s+PASSED", log_text, re.MULTILINE))
    return {
        "bazel_disk_hits": disk_hits,
        "bazel_remote_hits": remote_hits,
        "bazel_local_actions": local_actions,
        "bazel_internal_actions": internal_actions,
        "bazel_actions_total": action_total or disk_hits + remote_hits + local_actions + internal_actions,
        "bazel_cacheable_actions": disk_hits + remote_hits + local_actions,
        "bazel_cache_hits": disk_hits + remote_hits,
        "vendor_hits": len(re.findall(r"(?:functionsystem\s+)?vendor cache hit", log_text, re.IGNORECASE)),
        "vendor_misses": len(re.findall(r"(?:functionsystem\s+)?vendor cache miss", log_text, re.IGNORECASE)),
        "cargo_compiling": len(re.findall(r"^\s*Compiling\s+", log_text, re.MULTILINE)),
        "ninja_actions_completed": ninja_completed,
        "ninja_actions_total": ninja_total,
        "ninja_no_work": int(bool(re.search(r"ninja:\s+no work to do", log_text, re.IGNORECASE))),
        "bazel_cached_tests": cached_tests,
        "shared_dependency_hits": len(
            re.findall(r"(?:found in /cache/common|already exists, skipping download|already exists, skipping extraction)", log_text, re.IGNORECASE)
        ),
        "shared_dependency_misses": len(
            re.findall(r"(?:Downloading .*?sha256:|Extracting .*? to|download failed)", log_text, re.IGNORECASE)
        ),
    }


def collect_artifacts(
    repo: pathlib.Path, *, changed_after: float | None = None
) -> list[dict[str, Any]]:
    """Collect the top-level packages produced by this release.

    ``output/`` is intentionally not cleaned by the local pipeline: it is a
    user-owned directory and can contain packages from earlier releases.  A
    recursive scan would incorrectly publish those files in a new immutable
    manifest.  The release targets write their distributable archives and
    wheels at the top level, so select only those files and, when provided,
    require them to have been refreshed by the current release invocation.
    """
    candidates: dict[str, pathlib.Path] = {}
    for relative_root in ("output", "datasystem/output", "functionsystem/output", "frontend/output", "go/output"):
        root = repo / relative_root
        if not root.is_dir():
            continue
        for path in root.iterdir():
            if not path.is_file() or not path.name.endswith((".whl", ".tar.gz", ".zip")):
                continue
            if changed_after is not None and path.stat().st_mtime < changed_after:
                continue
            relative = path.relative_to(repo).as_posix()
            candidates[relative] = path
    runtime_launcher = repo / "functionsystem/runtime-launcher/bin/runtime/runtime-launcher"
    if runtime_launcher.is_file() and (
        changed_after is None or runtime_launcher.stat().st_mtime >= changed_after
    ):
        candidates[runtime_launcher.relative_to(repo).as_posix()] = runtime_launcher
    return [
        {"source": relative, "sha256": _sha256(path), "size": path.stat().st_size}
        for relative, path in sorted(candidates.items())
    ]


def restore_component_outputs(
    repo: pathlib.Path, cache_root: pathlib.Path, identity: InputIdentity
) -> dict[str, int]:
    """Restore keyed outputs before host-side packaging such as ``make aio``."""
    key_root = cache_root / "artifacts" / "components" / artifact_key_for(identity)
    mapping = {
        "datasystem-output": repo / "datasystem/output",
        "functionsystem-output": repo / "functionsystem/output",
        "frontend-output": repo / "frontend/output",
        "dashboard-output": repo / "go/output",
        "runtime-output": repo / "output",
        "metrics": repo / "metrics",
    }
    restored: dict[str, int] = {}
    for name, destination in mapping.items():
        source = key_root / name
        if not source.is_dir():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        count = 0
        for item in source.iterdir():
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target, follow_symlinks=False)
            count += 1
        restored[name] = count
    return restored


def publish_component_outputs(
    repo: pathlib.Path,
    cache_root: pathlib.Path,
    identity: InputIdentity,
    names: set[str] | None = None,
    *,
    produced_after: float | None = None,
) -> dict[str, int]:
    """Copy only current component outputs into the identity-keyed cache.

    Runtime packaging writes into the long-lived top-level ``output/``
    directory.  It can contain several gigabytes of older releases and an
    expanded ``openyuanrong/`` tree, neither of which is an input for a new
    worktree.  Keep that directory user-owned, but cache only top-level
    packages refreshed by the command that just completed.
    """
    key_root = cache_root / "artifacts" / "components" / artifact_key_for(identity)
    mapping = {
        "datasystem-output": repo / "datasystem/output",
        "functionsystem-output": repo / "functionsystem/output",
        "frontend-output": repo / "frontend/output",
        "dashboard-output": repo / "go/output",
        "runtime-output": repo / "output",
        "metrics": repo / "metrics",
    }
    copied: dict[str, int] = {}
    key_root.mkdir(parents=True, exist_ok=True)
    for name, source in mapping.items():
        if names is not None and name not in names:
            continue
        if not source.is_dir():
            continue
        destination = key_root / name
        staging = pathlib.Path(tempfile.mkdtemp(prefix=f".{name}.", dir=key_root))
        count = 0
        try:
            for item in source.iterdir():
                if name == "runtime-output":
                    if not item.is_file() or not item.name.endswith((".whl", ".tar.gz")):
                        continue
                    if produced_after is not None and item.stat().st_mtime < produced_after:
                        continue
                # Do not publish stale DataSystem packages left in a dirty
                # worktree.  They can otherwise sit beside the current
                # package and be selected by downstream wildcard globs.
                if name == "datasystem-output" and item.is_file() and item.name.endswith(".tar.gz"):
                    expected = _datasystem_expected_version(repo, cache_root)
                    if expected and item.name != f"yr-datasystem-v{expected}.tar.gz":
                        continue
                target = staging / item.name
                if item.is_dir():
                    shutil.copytree(item, target, symlinks=True)
                else:
                    shutil.copy2(item, target, follow_symlinks=False)
                count += 1
            if destination.exists():
                # Renaming a directory is independent of permissions on its
                # children.  This lets a previous root-owned container output
                # be replaced safely without destructive recursive deletion.
                # Remove the old tree only when the invoking user owns it;
                # otherwise retain a clearly-marked recoverable backup.
                backup = key_root / f".{name}.stale-{uuid.uuid4().hex[:8]}"
                os.replace(destination, backup)
                try:
                    shutil.rmtree(backup)
                except PermissionError:
                    copied.setdefault("stale_backups", 0)
                    copied["stale_backups"] += 1
            os.replace(staging, destination)
            copied[name] = count
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
    return copied


def source_identity(repo: pathlib.Path) -> dict[str, Any]:
    head = _run_text(["git", "rev-parse", "HEAD"], repo)
    branch = _run_text(["git", "symbolic-ref", "--short", "-q", "HEAD"], repo, check=False) or "detached"
    dirty_text = _run_text(["git", "status", "--porcelain", "--untracked-files=no"], repo, check=False)
    submodules = []
    for line in _run_text(["git", "submodule", "status", "--recursive"], repo, check=False).splitlines():
        if not line.strip():
            continue
        fields = line.lstrip(" +-U").split()
        if len(fields) >= 2:
            submodules.append({"path": fields[1], "commit": fields[0], "state": line[0]})
    return {"commit": head, "branch": branch, "dirty": bool(dirty_text), "submodules": submodules}


def publish_release(
    repo: pathlib.Path,
    cache_root: pathlib.Path,
    build_id: str,
    manifest: dict[str, Any],
) -> pathlib.Path:
    releases = cache_root / "artifacts" / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    final = releases / build_id
    if final.exists():
        raise LocalBuildError(f"immutable artifact build already exists: {final}")
    staging = pathlib.Path(tempfile.mkdtemp(prefix=f".{build_id}.", dir=releases))
    try:
        for artifact in manifest["artifacts"]:
            source = repo / artifact["source"]
            destination = staging / "files" / artifact["source"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if _sha256(destination) != artifact["sha256"]:
                raise LocalBuildError(f"artifact changed while publishing: {artifact['source']}")
        _atomic_json(staging / "manifest.json", manifest)
        os.replace(staging, final)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    current = releases / "current.json"
    _atomic_json(current, {"build_id": build_id, "manifest": str(final / "manifest.json")})
    return final / "manifest.json"


def _directory_size(path: pathlib.Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except (FileNotFoundError, PermissionError):
            continue
    return total


def _is_wsl2() -> bool:
    try:
        return "microsoft" in pathlib.Path("/proc/version").read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


class LocalBuildRunner:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        script_repo = pathlib.Path(__file__).resolve().parents[1]
        self.repo = pathlib.Path(args.repo).expanduser().resolve() if args.repo else script_repo
        if not (self.repo / "build.sh").is_file():
            raise LocalBuildError(f"--repo is not a YuanRong worktree: {self.repo}")
        default_root = default_cache_root(self.repo)
        self.cache_root = pathlib.Path(args.cache_root or os.environ.get("LOCAL_BUILD_CACHE_ROOT", default_root)).expanduser().resolve()
        self.image = args.image or os.environ.get("LOCAL_BUILD_IMAGE", DEFAULT_IMAGE)
        host_default_jobs = max(1, (os.cpu_count() or 4) // 2)
        if args.jobs is not None:
            self.jobs = args.jobs
        elif args.command == "ut":
            try:
                requested_ut_jobs = int(os.environ.get("LOCAL_BUILD_UT_JOBS", str(DEFAULT_UT_JOBS)))
            except ValueError as error:
                raise LocalBuildError("LOCAL_BUILD_UT_JOBS must be a positive integer") from error
            if requested_ut_jobs < 1:
                raise LocalBuildError("LOCAL_BUILD_UT_JOBS must be a positive integer")
            # UT profiles compile a separate debug action graph. Keep their
            # cold start intentionally quiet so a developer can edit or run
            # other local workloads while this host-wide build queue drains.
            self.jobs = min(host_default_jobs, requested_ut_jobs)
        else:
            self.jobs = host_default_jobs
        # A remote cache is opt-in.  Do not silently point local builds at the
        # CI cluster: the endpoint is usually only resolvable inside Jenkins /
        # Buildkite networking and Bazel's retry timeout is expensive offline.
        self.remote_cache = args.remote_cache or os.environ.get("LOCAL_BUILD_REMOTE_CACHE", "")
        self.remote_cache_enabled = bool(self.remote_cache and _remote_cache_reachable(self.remote_cache))
        if self.remote_cache and not self.remote_cache_enabled:
            print(
                f"[local-build] remote cache unavailable; using local cache only: {self.remote_cache}",
                file=sys.stderr,
            )

    def _validate_worktree_integration(self) -> None:
        """Require the checked-out cache integration before mutating a cache.

        ``--repo`` can point at a different worktree, but the Docker command
        runs that worktree's Makefile and helper scripts.  A pre-integration
        branch would otherwise silently choose its old builder/cache behavior
        while sharing this cache root.
        """
        makefile = self.repo / "Makefile"
        cache_wrapper = self.repo / "scripts" / "with_local_build_cache.sh"
        runtime_build = self.repo / "build.sh"
        fs_bazel_builder = self.repo / "functionsystem" / "scripts" / "executor" / "builder" / "build_bazel.py"
        text = makefile.read_text(encoding="utf-8", errors="replace") if makefile.is_file() else ""
        runtime_text = runtime_build.read_text(encoding="utf-8", errors="replace") if runtime_build.is_file() else ""
        fs_bazel_text = fs_bazel_builder.read_text(encoding="utf-8", errors="replace") if fs_bazel_builder.is_file() else ""
        missing = []
        if not cache_wrapper.is_file():
            missing.append("scripts/with_local_build_cache.sh")
        protocol_pattern = re.compile(
            rf"^\s*LOCAL_BUILD_PROTOCOL\s*(?:\?=|:=|=)\s*{re.escape(LOCAL_BUILD_PROTOCOL)}\s*$",
            re.MULTILINE,
        )
        if not protocol_pattern.search(text):
            missing.append(f"Makefile:LOCAL_BUILD_PROTOCOL={LOCAL_BUILD_PROTOCOL}")
        for marker in (
            "LOCAL_CACHE_ROOT ?=",
            "LOCAL_CACHE_PROFILE ?=",
            "LOCAL_DATASYSTEM_BASELINE_ARCHIVE",
            "--bazel_local_cache_root",
        ):
            if marker not in text:
                missing.append(f"Makefile:{marker}")
        for marker in ("BAZEL_OUTPUT_USER_ROOT", "BAZEL_DISK_CACHE"):
            if marker not in runtime_text:
                missing.append(f"build.sh:{marker}")
        if "bazel_local_cache_root" not in fs_bazel_text:
            missing.append("functionsystem build_bazel.py:bazel_local_cache_root")
        if missing:
            raise LocalBuildError(
                "worktree does not contain the local-build cache integration: "
                + ", ".join(missing)
                + ". Rebase/cherry-pick local-build protocol "
                + LOCAL_BUILD_PROTOCOL
                + " before sharing this cache root."
            )

    def execute(self) -> int:
        if self.args.command == "stats":
            return self.show_stats()
        if self.args.command == "seed":
            return self.seed_server(self.args.resource)
        toolchain = inspect_toolchain(self.image)
        identity = InputIdentity.from_repo(
            self.repo,
            image_id=toolchain.image_id,
            platform_name=toolchain.os,
            machine=toolchain.architecture,
            bazel_major="6",
        )
        if self.args.command == "init":
            CacheLayout(self.cache_root).initialize()
            return self.show_status(toolchain, identity)
        if self.args.command == "status":
            return self.show_status(toolchain, identity)
        if self.args.command == "release" and self.args.aio:
            # Validate before acquiring the build lock or compiling a release.
            aio_build_command(self.repo, "openyuanrong-aio:preflight")
        self._validate_worktree_integration()
        return self.run_build(toolchain, identity)

    def seed_server(self, resource: str) -> int:
        CacheLayout(self.cache_root).initialize()
        archive, metadata = _download_server_seed(self.cache_root, resource)
        result: dict[str, Any] = {"archive": str(archive), "metadata": metadata}
        if resource == "ds_tmp":
            with BuildQueue(self.cache_root, f"seed:{resource}", self.repo) as queue:
                result["queue_wait_seconds"] = round(queue.wait_seconds, 3)
                result["import"] = _import_ds_tmp(self.cache_root, archive)
        elif resource == "vendor":
            # Vendor output is tied to VendorList.csv and compiler inputs.  Keep
            # it staged until an explicit identity comparison approves import.
            result["import"] = "staged_only"
            result["reason"] = "server VendorList.csv differs from current repository; no overwrite"
        else:
            result["import"] = "staged_only"
        if resource == "datasystem":
            result["approved_local_baseline"] = resource == "datasystem" and DATASYSTEM_BASELINE_VERSION == "9.9.9"
            result["baseline_version"] = DATASYSTEM_BASELINE_VERSION
        self._print(result)
        return 0

    def _readiness(self, identity: InputIdentity) -> dict[str, Any]:
        fs_path = self.cache_root / "functionsystem-inputs" / identity.functionsystem
        runtime_path = self.cache_root / "runtime-inputs" / identity.runtime / "thirdparty"
        fs_ready, fs_missing = validate_functionsystem_inputs(fs_path)
        runtime_ready, runtime_missing = validate_runtime_inputs(
            runtime_path, self.repo / "tools/openSource.txt"
        )
        return {
            "functionsystem": {"identity": identity.functionsystem, "path": str(fs_path), "ready": fs_ready, "missing": fs_missing},
            "runtime": {"identity": identity.runtime, "path": str(runtime_path), "ready": runtime_ready, "missing": runtime_missing},
            "artifacts": {
                "identity": artifact_key_for(identity),
                "ready": validate_component_artifacts(
                    self.cache_root,
                    identity,
                    _datasystem_expected_version(self.repo, self.cache_root),
                )[0],
                "missing": validate_component_artifacts(
                    self.cache_root,
                    identity,
                    _datasystem_expected_version(self.repo, self.cache_root),
                )[1],
            },
        }

    def show_status(self, toolchain: Toolchain, identity: InputIdentity) -> int:
        seed_root = self.cache_root / "server-seeds" / "yr_cache-x86_64"
        seeds = []
        for resource, filename in SERVER_SEED_ARCHIVES.items():
            archive = seed_root / filename
            metadata_path = seed_root / f"{filename}.json"
            metadata: dict[str, Any] = {}
            if metadata_path.is_file():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    metadata = {}
            if not metadata.get("sha256"):
                checksum_path = seed_root / f"{filename}.sha256"
                if checksum_path.is_file():
                    try:
                        metadata["sha256"] = checksum_path.read_text(encoding="utf-8").split()[0]
                    except (OSError, IndexError):
                        pass
            seeds.append(
                {
                    "resource": resource,
                    "path": str(archive),
                    "staged": archive.is_file(),
                    "size": archive.stat().st_size if archive.is_file() else 0,
                    "sha256": metadata.get("sha256"),
                    "import_policy": "missing-only" if resource == "ds_tmp" else "staged-only",
                    "used_as_action_cache": False,
                    "used_as_component_baseline": resource == "datasystem" and DATASYSTEM_BASELINE_VERSION == "9.9.9",
                    "note": (
                        "approved DataSystem package baseline; not a Bazel action cache"
                        if resource == "datasystem" and DATASYSTEM_BASELINE_VERSION == "9.9.9"
                        else "download/source seed only; not a Bazel action cache"
                        if resource != "ds_tmp"
                        else "missing DataSystem download-cache files only"
                    ),
                }
            )
        result = {
            "cache_root": str(self.cache_root),
            "cache_size_bytes": _directory_size(self.cache_root),
            "image": dataclasses.asdict(toolchain),
            "remote_cache": {
                "requested": self.remote_cache or None,
                "enabled": self.remote_cache if self.remote_cache_enabled else None,
            },
            "server_seeds": seeds,
            "inputs": self._readiness(identity),
            "queue": BuildQueue.status(self.cache_root),
        }
        self._print(result)
        return 0

    def _operation(self) -> tuple[str, str, str]:
        command = self.args.command
        if command == "release":
            return release_command(jobs=self.jobs), "release", "all"
        if command == "prime":
            component = self.args.component
            return prime_command(component, jobs=self.jobs), "release", component
        if command == "build":
            component = self.args.component
            return component_command(component, jobs=self.jobs), "release", component
        component = self.args.component
        return (
            ut_command(
                component,
                jobs=self.jobs,
                suite=self.args.suite,
                case=self.args.case,
                targets=self.args.target,
                package=self.args.package,
                test=self.args.test,
                nodeid=self.args.nodeid,
            ),
            "ut",
            component,
        )

    @staticmethod
    def _required_inputs(command: str, component: str) -> tuple[bool, bool, bool]:
        if command in {"release", "prime"}:
            need_fs = component in {"all", "functionsystem", "yuanrong"}
            need_runtime = component in {"all", "yuanrong"}
            return need_fs, need_runtime, False
        if command == "build":
            return component == "functionsystem", component == "yuanrong", component == "yuanrong"
        # YuanRong UT consumes upstream packages; it does not need to prepare
        # FunctionSystem's vendor tree again. FunctionSystem UT still does.
        return component == "functionsystem", component in {"all", "yuanrong"}, component in {"all", "yuanrong"}

    def _docker_command(
        self,
        toolchain: Toolchain,
        mounts: Iterable[Mount],
        profile_name: str,
        inner_command: str,
        run_id: str,
        *,
        runtime_identity: str | None = None,
        runtime_cache_ready: bool = True,
        produced_outputs: set[str] | None = None,
        artifact_key: str | None = None,
    ) -> list[str]:
        command = [
            "docker", "run", "--rm", "--name", f"yr-local-build-{run_id}",
            "--platform", toolchain.docker_platform, "--network", "host",
        ]
        for mount in mounts:
            command.extend(mount.docker_args())
        environment = {
            "LOCAL_CACHE_ROOT": CONTAINER_CACHE,
            "LOCAL_CACHE_PROFILE": profile_name,
            "FUNCTIONSYSTEM_BUILDER": "bazel",
            "FUNCTIONSYSTEM_BUILD_STAMP": "1" if getattr(self.args, "stamp", False) else "0",
        }
        # CMake's dependency scanner is unstable on the current WSL2 kernel
        # for the Unix Makefiles generator (SIGABRT in cmake_depends).  The
        # DataSystem build script supports Ninja; selecting it here avoids the
        # scanner crash while retaining the shared FetchContent cache.
        if _is_wsl2():
            environment["DATASYSTEM_USE_NINJA"] = "on"
            environment["DATASYSTEM_NINJA"] = "on"
            environment["DATASYSTEM_BUILD_DIR"] = f"/cache/profiles/{profile_name}/datasystem-build-ninja"
        if self.remote_cache_enabled:
            environment["REMOTE_CACHE"] = self.remote_cache
        for key, value in environment.items():
            command.extend(("-e", f"{key}={value}"))
        # A cold runtime identity must build from the checked-out worktree,
        # then publish its inputs into the identity namespace.  The source
        # tree may contain directories created by a root-owned compile
        # container, so perform this missing-file-only promotion inside the
        # Docker container and return ownership to the invoking user.
        if runtime_identity and not runtime_cache_ready:
            destination = f"/cache/runtime-inputs/{runtime_identity}/thirdparty"
            owner = f"{os.getuid()}:{os.getgid()}"
            publish = (
                f"mkdir -p {shlex.quote(destination)}; "
                f"cp -a -n {CONTAINER_WORKSPACE}/thirdparty/. {shlex.quote(destination)}/; "
                f"chown -R {owner} {shlex.quote(destination)}"
            )
            inner_command = f"set -euo pipefail; {inner_command}; {publish}"
        # Producer output is deliberately worktree-local so packagers may
        # delete/recreate it.  Restore ownership of only those generated
        # paths (and their keyed cache destinations) before Docker exits.
        owner = f"{os.getuid()}:{os.getgid()}"
        producer_paths = {
            "datasystem-output": "/workspace/datasystem/output",
            "functionsystem-output": "/workspace/functionsystem/output",
            "frontend-output": "/workspace/frontend/output",
            "dashboard-output": "/workspace/go/output",
            "runtime-output": "/workspace/output",
            "metrics": "/workspace/metrics",
        }
        cleanup_paths = [producer_paths[name] for name in (produced_outputs or set()) if name in producer_paths]
        if artifact_key:
            cache_paths = {
                name: f"/cache/artifacts/components/{artifact_key}/{name}"
                for name in (produced_outputs or set())
            }
            cleanup_paths.extend(cache_paths.values())
        cleanup = ""
        if cleanup_paths:
            cleanup = " || true; chown -R " + shlex.quote(owner) + " " + " ".join(
                shlex.quote(path) for path in cleanup_paths
            )
        # The project build scripts expect the compile image's toolchain
        # environment (Go, Rust, protoc, etc.) to be loaded explicitly.  Do
        # this once at the Docker boundary so every component and packaging
        # step sees the same toolchain, including DataSystem's Go SDK stage.
        if cleanup:
            container_command = (
                "set +e; source /etc/profile.d/buildtools.sh; rc=$?; "
                "if [ $rc -eq 0 ]; then "
                f"{inner_command}; rc=$?; "
                f"chown -R {shlex.quote(owner)} " + " ".join(shlex.quote(path) for path in cleanup_paths) + " || true; "
                "fi; exit $rc"
            )
        else:
            container_command = f"source /etc/profile.d/buildtools.sh && {inner_command}"
        command.extend(("-w", CONTAINER_WORKSPACE, toolchain.image, "bash", "-c", container_command))
        return command

    def _run_logged(self, command: list[str], log_path: pathlib.Path) -> tuple[int, str, float]:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.monotonic()
        chunks = []
        process = None
        try:
            with log_path.open("w", encoding="utf-8") as log_stream:
                process = subprocess.Popen(
                    command,
                    cwd=self.repo,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                )
                assert process.stdout is not None
                for line in process.stdout:
                    sys.stdout.write(line)
                    log_stream.write(line)
                    chunks.append(line)
                process.stdout.close()
                return_code = process.wait()
        except BaseException:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            raise
        return return_code, "".join(chunks), time.monotonic() - started

    def _previous_duration(self, command: str, profile_name: str, component: str) -> float | None:
        matches = []
        for path in (self.cache_root / "runs").glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                value.get("exit_code") == 0
                and value.get("command") == command
                and value.get("profile") == profile_name
                and value.get("component") == component
            ):
                matches.append(value)
        if not matches:
            return None
        matches.sort(key=lambda item: item.get("started_at", ""))
        return float(matches[-1]["duration_seconds"])

    def run_build(self, toolchain: Toolchain, identity: InputIdentity) -> int:
        inner_command, profile_name, component = self._operation()
        command_name = self.args.command
        need_fs, need_runtime, need_artifacts = self._required_inputs(command_name, component)
        layout = CacheLayout(self.cache_root)
        if not self.args.dry_run:
            layout.initialize()
            (self.cache_root / "common/functionsystem-bazel-distdir").mkdir(parents=True, exist_ok=True)
        if not self.args.dry_run:
            prepare_input_directories(self.cache_root, identity)
            # A worktree may already contain valid downloaded/generated
            # inputs (for example after the first non-local build).  Promote
            # only missing files into this exact identity namespace before
            # readiness checks; never overwrite shared cache state.
            seed_inputs_from_worktree(self.repo, self.cache_root, identity)
            seed_runtime_dependency_archives(self.repo, self.cache_root, identity)
        readiness = self._readiness(identity)
        if not self.args.dry_run and command_name != "prime":
            failures = []
            if need_fs and not readiness["functionsystem"]["ready"]:
                failures.append(f"FunctionSystem inputs are not ready: {readiness['functionsystem']['missing']}")
            if need_runtime and not readiness["runtime"]["ready"]:
                failures.append(f"runtime inputs are not ready: {readiness['runtime']['missing']}")
            if need_artifacts and not readiness["artifacts"]["ready"]:
                failures.append(f"upstream package artifacts are not ready: {readiness['artifacts']['missing']}")
            if failures:
                raise LocalBuildError("; ".join(failures) + "; run scripts/local-build.sh prime first")

        fs_identity = identity.functionsystem if need_fs else None
        runtime_identity = identity.runtime if need_runtime else None
        mounts = create_mount_plan(
            self.repo,
            self.cache_root,
            fs_identity,
            runtime_identity,
            artifact_key_for(identity),
            readiness["runtime"]["ready"],
            None,
            produced_output_names(command_name, component),
        )
        run_id = f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        docker_command = self._docker_command(
            toolchain,
            mounts,
            profile_name,
            inner_command,
            run_id,
            runtime_identity=runtime_identity,
            runtime_cache_ready=readiness["runtime"]["ready"],
            produced_outputs=produced_output_names(command_name, component),
            artifact_key=artifact_key_for(identity),
        )
        if self.args.dry_run:
            self._print(
                {
                    "run_id": run_id,
                    "profile": profile_name,
                    "component": component,
                    "input_readiness": readiness,
                    "mounts": [dataclasses.asdict(mount) | {"host": str(mount.host)} for mount in mounts],
                    "command": shlex.join(docker_command),
                }
            )
            return 0

        log_path = self.cache_root / "logs" / f"{run_id}.log"
        started_at = _now()
        previous = self._previous_duration(command_name, profile_name, component)
        source = source_identity(self.repo)
        with BuildQueue(self.cache_root, f"{command_name}:{component}:{profile_name}", self.repo) as queue:
            print(f"[local-build] queue acquired after {queue.wait_seconds:.2f}s")
            current_identity = InputIdentity.from_repo(
                self.repo,
                image_id=toolchain.image_id,
                platform_name=toolchain.os,
                machine=toolchain.architecture,
                bazel_major="6",
            )
            if dataclasses.asdict(current_identity) != dataclasses.asdict(identity):
                raise LocalBuildError(
                    "dependency inputs changed while waiting for the compile queue; "
                    "rerun the command so its mount identity matches the current worktree"
                )
            source = source_identity(self.repo)
            command_started = time.time()
            exit_code, log_text, duration = self._run_logged(docker_command, log_path)
            # Producers keep output directories local so project packagers can
            # delete/recreate them. Publish only after the complete command
            # succeeds; consumers continue to use direct cache mounts.
            component_outputs = {
                "artifact_key": artifact_key_for(current_identity),
                "direct_bind_mount": False,
            }
            if exit_code == 0:
                component_outputs["published"] = publish_component_outputs(
                    self.repo,
                    self.cache_root,
                    current_identity,
                    produced_output_names(command_name, component),
                    produced_after=command_started,
                )
            artifact_manifest = None
            build_id = None
            aio_image = None
            if exit_code == 0 and command_name == "release":
                build_id = f"{dt.datetime.now().strftime('%Y%m%d%H%M%S')}-{source['commit'][:12]}"
                consumers = ["local"]
                if self.args.aio:
                    # Producers write into keyed cache mounts.  AIO is a
                    # host-side Make target, so restore exactly this identity
                    # into the normal paths before it copies package wheels.
                    restore_component_outputs(self.repo, self.cache_root, current_identity)
                    aio_image = f"openyuanrongaio:local-{build_id}"
                    aio_command = aio_build_command(self.repo, aio_image)
                    aio_code, aio_log, aio_duration = self._run_logged(aio_command, log_path.with_name(f"{run_id}-aio.log"))
                    log_text += aio_log
                    duration += aio_duration
                    exit_code = aio_code
                    consumers.append("aio")
                if self.args.three_vm_ready:
                    consumers.append("3vm")
                if exit_code == 0:
                    artifacts = collect_artifacts(self.repo, changed_after=command_started)
                    if not artifacts:
                        raise LocalBuildError("release succeeded but no wheel/tar artifacts were found")
                    manifest = {
                        "schema_version": 1,
                        "build_id": build_id,
                        "created_at": _now(),
                        "source": source,
                        "toolchain": dataclasses.asdict(toolchain),
                        "profile": profile_name,
                        "stamped": bool(self.args.stamp),
                        "consumers": consumers,
                        "aio_image": aio_image,
                        "artifacts": artifacts,
                    }
                    artifact_manifest = str(publish_release(self.repo, self.cache_root, build_id, manifest))

        metrics = parse_build_metrics(log_text)
        record = {
            "schema_version": 1,
            "run_id": run_id,
            "command": command_name,
            "component": component,
            "profile": profile_name,
            "started_at": started_at,
            "finished_at": _now(),
            "worktree": str(self.repo),
            "source": source,
            "toolchain": dataclasses.asdict(toolchain),
            "remote_cache": self.remote_cache if self.remote_cache_enabled else None,
            "input_identity": dataclasses.asdict(identity),
            "mounts": [dataclasses.asdict(mount) | {"host": str(mount.host)} for mount in mounts],
            "queue_wait_seconds": round(queue.wait_seconds, 3),
            "duration_seconds": round(duration, 3),
            "exit_code": exit_code,
            "metrics": metrics,
            "previous_duration_seconds": previous,
            "speedup_ratio": round(previous / duration, 3) if previous and duration else None,
            "log_path": str(log_path),
            "artifact_build_id": build_id,
            "artifact_manifest": artifact_manifest,
            "component_outputs": component_outputs,
        }
        _atomic_json(self.cache_root / "runs" / f"{run_id}.json", record)
        self._print(record)
        return exit_code

    def show_stats(self) -> int:
        groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for path in sorted((self.cache_root / "runs").glob("*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            key = (value.get("command", ""), value.get("component", ""), value.get("profile", ""))
            groups[key].append(value)
        summary = []
        for key, runs in sorted(groups.items()):
            successful = [run for run in runs if run.get("exit_code") == 0]
            latest = runs[-1]
            durations = [float(run["duration_seconds"]) for run in successful]
            metrics = latest.get("metrics", {})
            hits = int(metrics.get("bazel_cache_hits", metrics.get("bazel_disk_hits", 0)))
            local = int(metrics.get("bazel_local_actions", 0))
            cacheable = int(metrics.get("bazel_cacheable_actions", hits + local))
            cached_tests = int(metrics.get("bazel_cached_tests", 0))
            summary.append(
                {
                    "command": key[0], "component": key[1], "profile": key[2],
                    "runs": len(runs), "successful": len(successful),
                    "average_duration_seconds": round(sum(durations) / len(durations), 3) if durations else None,
                    "latest_duration_seconds": latest.get("duration_seconds"),
                    "latest_speedup_ratio": latest.get("speedup_ratio"),
                    "latest_bazel_hit_rate": round(hits / cacheable, 4) if cacheable else None,
                    "latest_bazel_cached_tests": cached_tests,
                    "latest_metrics": metrics,
                }
            )
        self._print({"cache_root": str(self.cache_root), "groups": summary})
        return 0

    def _print(self, value: Any) -> None:
        if self.args.json:
            print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))
            return
        if isinstance(value, dict) and "command" in value and len(value) == 1:
            print(value["command"])
            return
        print(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True))


def _add_common_options(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    defaults = {"default": argparse.SUPPRESS} if suppress_defaults else {}
    parser.add_argument("--cache-root", help="shared cache root outside all worktrees", **defaults)
    parser.add_argument("--repo", help="YuanRong worktree to build (defaults to the runner's repository)", **defaults)
    parser.add_argument("--image", help=f"compile image (default: {DEFAULT_IMAGE})", **defaults)
    parser.add_argument("--jobs", type=int, help="compile parallelism (default: half of host CPUs)", **defaults)
    parser.add_argument(
        "--remote-cache",
        help="optional Bazel remote-cache endpoint; enabled only when reachable (also LOCAL_BUILD_REMOTE_CACHE)",
        **defaults,
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the resolved Docker command without running it", **defaults
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON", **defaults)


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    _add_common_options(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "status", "stats"):
        subparser = subparsers.add_parser(name)
        _add_common_options(subparser, suppress_defaults=True)

    seed = subparsers.add_parser("seed", help="download and safely stage a public server cache seed")
    _add_common_options(seed, suppress_defaults=True)
    seed.add_argument("resource", choices=tuple(SERVER_SEED_ARCHIVES))

    prime = subparsers.add_parser("prime", help="populate dependency inputs and release caches")
    _add_common_options(prime, suppress_defaults=True)
    prime.add_argument("component", nargs="?", default="all", choices=("all", *COMPONENTS))

    build = subparsers.add_parser("build", help="build one component with the release cache")
    _add_common_options(build, suppress_defaults=True)
    build.add_argument("component", choices=COMPONENTS)

    release = subparsers.add_parser("release", help="build and publish one coherent local release")
    _add_common_options(release, suppress_defaults=True)
    release.add_argument("--aio", action="store_true", help="also build a local AIO image")
    release.add_argument("--3vm-ready", dest="three_vm_ready", action="store_true", help="mark the artifact manifest for local 3VM deployment")
    release.add_argument("--stamp", action="store_true", help="embed Git branch/commit metadata")

    unit_test = subparsers.add_parser("ut", help="compile and run selected unit tests with the UT cache")
    _add_common_options(unit_test, suppress_defaults=True)
    unit_test.add_argument("component", nargs="?", default="all", choices=UT_COMPONENTS)
    unit_test.add_argument("--suite", default="", help="FunctionSystem gtest suite filter")
    unit_test.add_argument("--case", default="", help="FunctionSystem gtest case filter")
    unit_test.add_argument("--target", action="append", default=[], help="explicit YuanRong Bazel test target; repeatable")
    unit_test.add_argument("--package", default="", help="Rust Cargo package")
    unit_test.add_argument("--test", default="", help="Rust integration-test binary")
    unit_test.add_argument("--nodeid", default="", help="Python pytest node id")
    return parser


def main() -> int:
    args = create_parser().parse_args()
    if args.jobs is not None and args.jobs < 1:
        raise LocalBuildError("--jobs must be greater than zero")
    return LocalBuildRunner(args).execute()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except LocalBuildError as error:
        print(f"local-build: error: {error}", file=sys.stderr)
        raise SystemExit(2)
