#!/usr/bin/env python3
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

"""Assemble the Buildkite-only openYuanRong core wheel."""

import argparse
import base64
import csv
import hashlib
import io
import logging
import os
import pathlib
import re
import stat
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser


REQUIRED_DISTRIBUTIONS = (
    "openyuanrong",
    "openyuanrong_functionsystem",
    "openyuanrong_datasystem",
    "openyuanrong_faas",
    "openyuanrong_runtime",
)
CORE_DISTRIBUTION = "openyuanrong-core"
CORE_FILENAME_DISTRIBUTION = "openyuanrong_core"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GO_RUNTIME_PREFIX = "yr/runtime/service/go/bin/"
GO_RUNTIME_MEMBERS = {
    GO_RUNTIME_PREFIX + "goruntime",
    GO_RUNTIME_PREFIX + "libcpplibruntime.so",
}
CORE_OVERLAY_SOURCES = {
    "yr/functionsystem/deploy/install.sh": (
        REPO_ROOT / "functionsystem/scripts/deploy/function_system/install.sh"
    ),
    "yr/functionsystem/deploy/health_check.sh": (
        REPO_ROOT / "functionsystem/scripts/deploy/function_system/health_check.sh"
    ),
    "yr/datasystem/deploy/install.sh": (
        REPO_ROOT / "deploy/data_system/install.sh"
    ),
    "yr/datasystem/deploy/health_check.sh": (
        REPO_ROOT / "deploy/data_system/health_check.sh"
    ),
}
CORE_GENERATED_MEMBERS = {
    "yr/checkpoints/.keep": b"",
}


@dataclass
class AssemblyState:
    """Track generated wheel members and RECORD rows."""

    records: list
    seen: set


def log_to_stream(message, stream, level=logging.INFO):
    """Write one plain log message to the requested command-line stream."""
    logger = logging.getLogger(f"{__name__}.{level}")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    logger.log(level, message)


def normalize_distribution(name):
    """Normalize a distribution name for comparisons."""
    return re.sub(r"[-_.]+", "_", name).lower()


def find_dist_info_member(archive, filename):
    """Return the single dist-info member ending with filename."""
    matches = []
    for member in archive.namelist():
        parts = member.split("/")
        if len(parts) != 2:
            continue
        dist_info, member_name = parts
        if dist_info.endswith(".dist-info") and member_name == filename:
            matches.append(member)
    if len(matches) != 1:
        raise ValueError(
            f"{archive.filename}: expected one .dist-info/{filename}, found {len(matches)}"
        )
    return matches[0]


def read_source_wheel(path):
    """Read the identity and platform tag of a source wheel."""
    with zipfile.ZipFile(path) as archive:
        metadata_member = find_dist_info_member(archive, "METADATA")
        metadata_bytes = archive.read(metadata_member)
        metadata = BytesParser(policy=policy.compat32).parsebytes(metadata_bytes)
        name = metadata.get("Name")
        version = metadata.get("Version")
        if not name or not version:
            raise ValueError(f"{path}: METADATA must contain Name and Version")

        wheel_member = find_dist_info_member(archive, "WHEEL")
        wheel_text = archive.read(wheel_member).decode("utf-8")
        platforms = {
            line.split(":", 1)[1].strip().split("-", 2)[2]
            for line in wheel_text.splitlines()
            if line.startswith("Tag:") and line.count("-") >= 2
        }
        if len(platforms) != 1:
            raise ValueError(
                f"{path}: expected one wheel platform, found {sorted(platforms)}"
            )

    return {
        "path": path,
        "name": normalize_distribution(name),
        "version": version,
        "platform": platforms.pop(),
        "metadata_member": metadata_member,
        "metadata_bytes": metadata_bytes,
    }


def discover_source_wheels(input_dir):
    """Discover exactly one wheel for each core component."""
    discovered = {}
    for path in sorted(input_dir.glob("*.whl")):
        source = read_source_wheel(path)
        if source["name"] not in REQUIRED_DISTRIBUTIONS:
            continue
        if source["name"] in discovered:
            raise ValueError(
                f"multiple wheels found for {source['name']}: "
                f"{discovered[source['name']]['path']} and {path}"
            )
        discovered[source["name"]] = source

    missing = [
        distribution
        for distribution in REQUIRED_DISTRIBUTIONS
        if distribution not in discovered
    ]
    if missing:
        raise ValueError(
            f"missing required source wheel(s) in {input_dir}: {', '.join(missing)}"
        )

    versions = {source["version"] for source in discovered.values()}
    if len(versions) != 1:
        details = ", ".join(
            f"{name}={source['version']}" for name, source in sorted(discovered.items())
        )
        raise ValueError(f"source wheel versions do not match: {details}")

    platforms = {source["platform"] for source in discovered.values()}
    if len(platforms) != 1:
        details = ", ".join(
            f"{name}={source['platform']}"
            for name, source in sorted(discovered.items())
        )
        raise ValueError(f"source wheel platforms do not match: {details}")

    return discovered, versions.pop(), platforms.pop()


def is_symbol_payload(member):
    """Return whether member is a detached symbol payload."""
    parts = pathlib.PurePosixPath(member).parts
    return (
        member.endswith(".sym")
        or "sym" in parts
        or any(part.endswith("_SYM") for part in parts)
    )


def include_member(distribution, member):
    """Select the fixed core profile from the source component wheels."""
    if member.endswith("/"):
        return False
    path = pathlib.PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe wheel member: {member}")
    if any(part.endswith(".dist-info") for part in path.parts):
        return False
    if is_symbol_payload(member):
        return False

    if distribution == "openyuanrong":
        return True

    if distribution == "openyuanrong_functionsystem":
        if not member.startswith("yr/functionsystem/"):
            return False
        return member != "yr/functionsystem/bin/runtime-launcher"

    if distribution == "openyuanrong_faas":
        return member.startswith("yr/faas/")

    if distribution == "openyuanrong_runtime":
        return member in GO_RUNTIME_MEMBERS

    if distribution == "openyuanrong_datasystem":
        if not member.startswith("yr/datasystem/"):
            return False
        relative = pathlib.PurePosixPath(member).relative_to("yr/datasystem")
        if not relative.parts:
            return False
        if relative.parts[0] in ("cli", "cpp_template", "include"):
            return False
        if relative.name in ("dsbench_cpp", "sdk_lib_list", "libds_client_py.so"):
            return False
        if relative.suffix in (".py", ".pyc", ".pyo"):
            return False
        if "__pycache__" in relative.parts:
            return False
        return True

    raise ValueError(f"unsupported core source distribution: {distribution}")


def core_metadata(base_metadata):
    """Create core METADATA from the base wheel without split-wheel extras."""
    metadata = BytesParser(policy=policy.compat32).parsebytes(base_metadata)
    metadata.replace_header("Name", CORE_DISTRIBUTION)
    if metadata.get("Summary"):
        metadata.replace_header(
            "Summary",
            "openYuanRong language-runtime-free core control-plane package",
        )
    else:
        metadata["Summary"] = (
            "openYuanRong language-runtime-free core control-plane package"
        )

    requirements = [
        value
        for value in metadata.get_all("Requires-Dist", [])
        if "extra ==" not in value.lower()
    ]
    if metadata.get_all("Requires-Dist"):
        del metadata["Requires-Dist"]
    if metadata.get_all("Provides-Extra"):
        del metadata["Provides-Extra"]
    dynamic_fields = [
        value
        for value in metadata.get_all("Dynamic", [])
        if value.lower() not in ("provides-extra", "requires-dist")
    ]
    if metadata.get_all("Dynamic"):
        del metadata["Dynamic"]
    for requirement in requirements:
        metadata["Requires-Dist"] = requirement
    for field in dynamic_fields:
        metadata["Dynamic"] = field
    return metadata.as_bytes(policy=policy.compat32)


CORE_EXECUTABLE_MEMBERS = {
    "yr/datasystem/datasystem_coordinator",
    "yr/datasystem/datasystem_worker",
    GO_RUNTIME_PREFIX + "goruntime",
}


def normalized_member_mode(source_info, member):
    """Return a portable wheel mode while preserving executable intent."""
    source_mode = (source_info.external_attr >> 16) & 0xFFFF
    if stat.S_IFMT(source_mode) == stat.S_IFLNK:
        return source_mode
    if (
        source_mode & 0o111
        or member in CORE_EXECUTABLE_MEMBERS
        or member.endswith(".sh")
    ):
        return stat.S_IFREG | 0o755
    return stat.S_IFREG | 0o644


def clone_zip_info(source_info, member):
    """Clone timestamps while normalizing permissions for wheel installation."""
    info = zipfile.ZipInfo(member, date_time=source_info.date_time)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = normalized_member_mode(source_info, member) << 16
    info.internal_attr = source_info.internal_attr
    info.comment = source_info.comment
    return info


def generated_zip_info(member, mode=0o100644):
    """Create metadata ZipInfo with stable permissions."""
    info = zipfile.ZipInfo(member)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = mode << 16
    return info


def record_digest(data):
    """Return a wheel RECORD sha256 digest."""
    digest = hashlib.sha256(data).digest()
    encoded = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"sha256={encoded}"


def add_member(output, state, member, data, info):
    """Write one unique member and remember its RECORD entry."""
    if member in state.seen:
        raise ValueError(f"source wheels contain duplicate path: {member}")
    state.seen.add(member)
    output.writestr(info, data)
    state.records.append((member, record_digest(data), str(len(data))))


def copy_base_dist_info_files(base_source, output, state, dist_info):
    """Copy base entry points and license metadata into the core dist-info."""
    with zipfile.ZipFile(base_source["path"]) as archive:
        source_dist_info = base_source["metadata_member"].split("/", 1)[0]
        for source_info in archive.infolist():
            prefix = source_dist_info + "/"
            if not source_info.filename.startswith(prefix):
                continue
            relative = source_info.filename.removeprefix(prefix)
            if not relative or relative in ("METADATA", "WHEEL", "RECORD"):
                continue
            member = f"{dist_info}/{relative}"
            data = archive.read(source_info)
            add_member(
                output,
                state,
                member,
                data,
                clone_zip_info(source_info, member),
            )


def add_core_overlay_members(output, state):
    """Add legacy deployment helpers that are not part of split wheels."""
    for member, source_path in CORE_OVERLAY_SOURCES.items():
        if not source_path.is_file():
            raise ValueError(f"core overlay source is missing: {source_path}")
        add_member(
            output,
            state,
            member,
            source_path.read_bytes(),
            generated_zip_info(member, stat.S_IFREG | 0o755),
        )
    for member, data in CORE_GENERATED_MEMBERS.items():
        add_member(
            output,
            state,
            member,
            data,
            generated_zip_info(member),
        )


def is_forbidden_core_member(member):
    """Return whether a member falls outside the fixed core profile."""
    return (
        member == "yr/functionsystem/bin/runtime-launcher"
        or member == "yr/datasystem/lib/libds_client_py.so"
        or (
            member.startswith("yr/runtime/")
            and member not in GO_RUNTIME_MEMBERS
        )
        or member.startswith("yr/datasystem/cli/")
        or member.startswith("yr/datasystem/include/")
        or member.startswith("yr/datasystem/cpp_template/")
        or is_symbol_payload(member)
    )


def validate_core_members(members):
    """Validate the fixed core scope before publishing."""
    required = {
        "yr/cli/main.py",
        "yr/functionsystem/bin/runtime_manager",
        "yr/datasystem/datasystem_worker",
        "yr/datasystem/datasystem_coordinator",
        "yr/faas/faasfrontend/faasfrontend.so",
        "yr/faas/faasscheduler/faasscheduler.so",
        *CORE_OVERLAY_SOURCES,
        *CORE_GENERATED_MEMBERS,
        *GO_RUNTIME_MEMBERS,
    }
    missing = sorted(required - members)
    if missing:
        raise ValueError(f"core wheel is missing required payloads: {', '.join(missing)}")

    forbidden = [member for member in members if is_forbidden_core_member(member)]
    if forbidden:
        raise ValueError(
            "core wheel contains forbidden payloads: " + ", ".join(sorted(forbidden))
        )


def assemble_core_wheel(input_dir, output_dir):
    """Assemble and return the generated core wheel path."""
    sources, version, platform_tag = discover_source_wheels(input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    wheel_version = version.replace("-", "_")
    dist_info = f"{CORE_FILENAME_DISTRIBUTION}-{wheel_version}.dist-info"
    wheel_name = (
        f"{CORE_FILENAME_DISTRIBUTION}-{wheel_version}-"
        f"py3-none-{platform_tag}.whl"
    )
    final_path = output_dir / wheel_name

    state = AssemblyState(records=[], seen=set())
    with tempfile.NamedTemporaryFile(
        prefix=wheel_name + ".", suffix=".tmp", dir=output_dir, delete=False
    ) as temp_file:
        temp_path = pathlib.Path(temp_file.name)

    try:
        with zipfile.ZipFile(
            temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as output:
            for distribution in REQUIRED_DISTRIBUTIONS:
                source = sources[distribution]
                with zipfile.ZipFile(source["path"]) as archive:
                    for source_info in archive.infolist():
                        member = source_info.filename
                        if not include_member(distribution, member):
                            continue
                        data = archive.read(source_info)
                        add_member(
                            output,
                            state,
                            member,
                            data,
                            clone_zip_info(source_info, member),
                        )

            add_core_overlay_members(output, state)

            base_source = sources["openyuanrong"]
            copy_base_dist_info_files(
                base_source, output, state, dist_info
            )

            metadata_member = f"{dist_info}/METADATA"
            metadata_bytes = core_metadata(base_source["metadata_bytes"])
            add_member(
                output,
                state,
                metadata_member,
                metadata_bytes,
                generated_zip_info(metadata_member),
            )

            wheel_member = f"{dist_info}/WHEEL"
            wheel_bytes = (
                "Wheel-Version: 1.0\n"
                "Generator: openYuanRong Buildkite core wheel assembler\n"
                "Root-Is-Purelib: false\n"
                f"Tag: py3-none-{platform_tag}\n"
            ).encode("utf-8")
            add_member(
                output,
                state,
                wheel_member,
                wheel_bytes,
                generated_zip_info(wheel_member),
            )

            validate_core_members(state.seen)
            record_member = f"{dist_info}/RECORD"
            record_buffer = io.StringIO(newline="")
            writer = csv.writer(record_buffer, lineterminator="\n")
            writer.writerows(state.records)
            writer.writerow((record_member, "", ""))
            output.writestr(
                generated_zip_info(record_member),
                record_buffer.getvalue().encode("utf-8"),
            )

        os.replace(temp_path, final_path)
        final_path.chmod(0o644)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    with zipfile.ZipFile(final_path) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise ValueError(f"generated wheel has a corrupt member: {bad_member}")

    return final_path


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Assemble the Buildkite-only openYuanRong core wheel."
    )
    parser.add_argument(
        "--input-dir",
        type=pathlib.Path,
        required=True,
        help="Directory containing the five source component wheels.",
    )
    parser.add_argument(
        "--output-dir",
        type=pathlib.Path,
        required=True,
        help="Directory for the generated core wheel.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        wheel_path = assemble_core_wheel(args.input_dir, args.output_dir)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        log_to_stream(f"ERROR: {error}", sys.stderr, logging.ERROR)
        return 1
    size_mib = wheel_path.stat().st_size / (1024 * 1024)
    log_to_stream(f"Created {wheel_path} ({size_mib:.2f} MiB)", sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
