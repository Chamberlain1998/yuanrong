#!/usr/bin/env python3

"""Container command construction for the local build pipeline."""

from __future__ import annotations

import os
import shlex


# The public x86 archive is the developer-approved DataSystem baseline.  It is
# mounted from the shared cache and consumed as a package; source DataSystem
# compilation remains available with LOCAL_DATASYSTEM_SOURCE_BUILD=1.
DATASYSTEM_BASELINE_VERSION = os.environ.get("LOCAL_DATASYSTEM_BASELINE_VERSION", "9.9.9")
DATASYSTEM_BASELINE_ARCHIVE = os.environ.get(
    "LOCAL_DATASYSTEM_BASELINE_ARCHIVE",
    f"/cache/server-seeds/yr_cache-x86_64/yr-datasystem-v{DATASYSTEM_BASELINE_VERSION}.tar.gz",
)


COMPONENTS = (
    "datasystem",
    "functionsystem",
    "frontend",
    "runtime_launcher",
    "dashboard",
    "yuanrong",
)
UT_COMPONENTS = ("all", "functionsystem", "yuanrong", "datasystem", "frontend", "dashboard", "python")


def _make(target: str, jobs: int) -> str:
    command = f"make {shlex.quote(target)} JOBS={jobs} FUNCTIONSYSTEM_JOBS={jobs}"
    if target == "datasystem":
        command += " DATASYSTEM_NINJA=on DATASYSTEM_BUILD_DIR=/cache/profiles/release/datasystem-build-ninja"
        if not os.environ.get("LOCAL_DATASYSTEM_SOURCE_BUILD"):
            command += (
                f" LOCAL_DATASYSTEM_BASELINE_ARCHIVE={shlex.quote(DATASYSTEM_BASELINE_ARCHIVE)}"
                f" LOCAL_DATASYSTEM_BASELINE_VERSION={DATASYSTEM_BASELINE_VERSION}"
            )
    return command


def component_command(component: str, *, jobs: int, profile: str = "release") -> str:
    del profile
    if component not in COMPONENTS:
        raise ValueError(f"unsupported component: {component}")
    if component == "yuanrong":
        # Dependency artifacts are prepared by `prime yuanrong` (or a
        # release).  A normal component build must only rebuild YuanRong so a
        # small runtime change does not force DataSystem/FunctionSystem.
        return _make("yuanrong", jobs)
    return _make(component, jobs)


def release_command(*, jobs: int) -> str:
    commands = [
        _make("datasystem", jobs),
        _make("functionsystem", jobs),
        _make("frontend", jobs),
        _make("dashboard", jobs),
        _make("yuanrong", jobs),
    ]
    return "set -euo pipefail; " + "; ".join(commands)


def prime_command(component: str, *, jobs: int) -> str:
    if component == "all":
        return release_command(jobs=jobs)
    if component == "functionsystem":
        return "set -euo pipefail; " + "; ".join((_make("datasystem", jobs), _make("functionsystem", jobs)))
    if component == "yuanrong":
        return "set -euo pipefail; " + "; ".join(
            (_make("datasystem", jobs), _make("functionsystem", jobs), _make("yuanrong", jobs))
        )
    return component_command(component, jobs=jobs)


def ut_command(
    component: str,
    *,
    jobs: int,
    suite: str = "",
    case: str = "",
    targets: list[str] | None = None,
    package: str = "",
    test: str = "",
    nodeid: str = "",
) -> str:
    if component not in UT_COMPONENTS:
        raise ValueError(f"unsupported UT component: {component}")
    targets = targets or []

    if component == "functionsystem":
        parts = [
            "cd functionsystem && bash run.sh test",
            f"-j {jobs}",
            "--builder bazel",
            # The executor appends profiles/<release|ut> itself.  Keep UT on
            # the same root as release; only the profile is different.
            "--bazel_local_cache_root /cache",
            "--bazel_cache_profile ut",
        ]
        if suite:
            parts.extend(("-s", shlex.quote(suite)))
        if case:
            parts.extend(("-c", shlex.quote(case)))
        return " ".join(parts)

    if component == "yuanrong":
        target_value = " ".join(targets)
        selected_non_python = bool(targets) and not any(target.startswith("//api/python") for target in targets)
        prefix = "YR_SKIP_PYTHON_REQUIREMENTS=1 " if selected_non_python else ""
        prefix += f"BAZEL_TARGETS_OVERRIDE={shlex.quote(target_value)} " if target_value else ""
        # UT requires the dependency packages to have been prepared once by
        # `prime yuanrong`; this command itself only runs YuanRong's selected
        # Bazel tests.  `-t run` is invalid for YuanRong (its -t is a flag).
        return (
            f"{prefix}bash scripts/with_local_build_cache.sh --root /cache --profile ut -- "
            f"bash build.sh -t -j {jobs}"
        )

    if component == "datasystem":
        return (
            "bash scripts/with_local_build_cache.sh --root /cache --profile ut -- "
            f"bash datasystem/build.sh -b bazel -t run -j {jobs} -u {jobs}"
        )

    if component == "frontend":
        return "bash scripts/with_local_build_cache.sh --root /cache --profile ut -- bash -c 'cd frontend && bash test/test.sh'"

    if component == "dashboard":
        return "bash scripts/with_local_build_cache.sh --root /cache --profile ut -- bash -c 'cd go && bash test/test.sh'"

    if component == "python":
        selected = nodeid or "api/python/yr/tests"
        return (
            "bash scripts/with_local_build_cache.sh --root /cache --profile ut -- "
            f"python3 -m pytest {shlex.quote(selected)}"
        )

    commands = []
    if component == "all":
        # Produce the shared upstream packages once before YuanRong UT. The
        # individual component commands intentionally remain hot and require
        # callers to prime their dependencies explicitly.
        commands.extend((_make("datasystem", jobs), _make("functionsystem", jobs)))
    commands.extend([
        ut_command("functionsystem", jobs=jobs, suite=suite, case=case),
        ut_command("yuanrong", jobs=jobs, targets=targets),
        ut_command("datasystem", jobs=jobs),
        ut_command("frontend", jobs=jobs),
        ut_command("dashboard", jobs=jobs),
    ])
    return "set -euo pipefail; " + "; ".join(commands)
