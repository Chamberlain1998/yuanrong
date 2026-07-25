#!/usr/bin/env python3
# coding=UTF-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
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

"""setup for openyuanrong Python packages."""

from enum import Enum
import hashlib
import os
import re as _re
import shutil
import subprocess
import sysconfig
import tempfile
import warnings
import zipfile

import setuptools
from setuptools.command.build_ext import build_ext
from setuptools.command.build_py import build_py
from setuptools.command.develop import develop
from setuptools import Extension
from wheel.bdist_wheel import bdist_wheel as _bdist_wheel
from wheel_platform import get_wheel_platform_tag

try:
    from packaging import tags
    from packaging.version import Version
except ModuleNotFoundError as err:  # pragma: no cover
    try:
        from wheel.vendored.packaging import tags
        from wheel.vendored.packaging.version import Version
    except ImportError:  # pragma: no cover
        tags = None

        class Version(str):
            """Small fallback used when packaging is unavailable."""

ROOT_DIR = os.path.dirname(__file__)

PYTHON_RUNTIME_METRICS_EXPORTERS = (
    "libobservability-metrics-file-exporter.so",
    "libobservability-prometheus-push-exporter.so",
    "libobservability-prometheus-pull-exporter.so",
)

PYTHON_TOP_LEVEL_NATIVE_EXCLUDE_PREFIXES = (
    "libyr-api.so",
    "libfunctionsdk.so",
)


def is_python_top_level_native_excluded(filename):
    return filename.startswith(PYTHON_TOP_LEVEL_NATIVE_EXCLUDE_PREFIXES)


def get_version():
    """get version"""
    version = os.getenv("BUILD_VERSION", None)
    if version is None or len(version) == 0:
        with open(os.path.join(ROOT_DIR, "../../VERSION"), encoding="utf-8") as version_file:
            return version_file.read().strip()
    return version


def get_component_version(component, fallback):
    """get split component package version"""
    version_path = os.path.join(ROOT_DIR, "..", "..", component, "VERSION")
    if not os.path.exists(version_path):
        return fallback
    with open(version_path, "r", encoding="utf-8") as version_file:
        version = version_file.read().strip()
    return version or fallback


class SetupType(Enum):
    """setup type enum"""

    OPENYUANRONG = 1
    OPENYUANRONG_SDK = 2
    OPENYUANRONG_CPP_SDK = 3
    OPENYUANRONG_ALL = 4
    OPENYUANRONG_RUNTIME = 5
    OPENYUANRONG_DASHBOARD = 6
    OPENYUANRONG_FAAS = 7
    OPENYUANRONG_FULL = 8


class SetupSpec:
    """setup spec"""

    def __init__(self, setup_type: SetupType, name: str, description: str):
        self.setup_type = setup_type
        self.name = name
        self.description = description
        self.version = get_version()
        self.install_requires = []
        self.extras = {}
        self.entry_points = {}

    def get_packages(self):
        if self.setup_type in (SetupType.OPENYUANRONG, SetupType.OPENYUANRONG_FULL):
            return setuptools.find_packages(
                exclude=(
                    "yr.tests",
                    "yr.tests.*",
                    "yr.runtime",
                    "yr.runtime.*",
                    "yr.faas",
                    "yr.faas.*",
                )
            )
        if self.setup_type == SetupType.OPENYUANRONG_SDK:
            return setuptools.find_packages(
                exclude=("yr.tests", "yr.tests.*", "yr.inner", "yr.inner.*")
            )
        if self.setup_type == SetupType.OPENYUANRONG_RUNTIME:
            return setuptools.find_packages(
                include=("yr.runtime", "yr.runtime.*", "yr.faas", "yr.faas.*")
            )
        return []


setup_type_env = os.getenv("SETUP_TYPE", "")
base_name = os.getenv("YR_PACKAGE_NAME", "openyuanrong")

if setup_type_env == "sdk":
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG_SDK,
        f"{base_name}_sdk",
        "openyuanrong python sdk",
    )
    setup_spec.install_requires = [
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
        "aiohttp>=3.9.0",  # tunnel_server Port B HTTP/WS server
        "httpx>=0.27.0",   # tunnel_client async HTTP forwarding
    ]
    setup_spec.entry_points = {
        "console_scripts": [
            "yrcli=yr.cli.scripts:main",
            "yrcluster=yr.cli.cluster:main",
        ]
    }
elif setup_type_env == "runtime":
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG_RUNTIME,
        f"{base_name}_runtime",
        "openyuanrong runtime package",
    )
elif setup_type_env == "sdk_cpp":
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG_CPP_SDK,
        f"{base_name}_cpp_sdk",
        "openyuanrong cpp sdk",
    )
elif setup_type_env == "dashboard":
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG_DASHBOARD,
        f"{base_name}_dashboard",
        "openyuanrong dashboard",
    )
elif setup_type_env == "faas":
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG_FAAS,
        f"{base_name}_faas",
        "openyuanrong faas",
    )
elif setup_type_env == "all":
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG_ALL,
        f"{base_name}_all",
        "openyuanrong all package",
    )
    setup_spec.entry_points = {
        "console_scripts": [
            "yr=yr.cli.main:main",
            "yrcli=yr.cli.scripts:main",
        ]
    }
elif setup_type_env == "full":
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG_FULL,
        f"{base_name}-full",
        "openyuanrong full package (all-in-one)",
    )
    setup_spec.install_requires = [
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
    ]
    setup_spec.entry_points = {
        "console_scripts": [
            "yr=yr.cli.main:main",
            "yrcli=yr.cli.scripts:main",
        ]
    }
else:
    setup_spec = SetupSpec(
        SetupType.OPENYUANRONG,
        base_name,
        "openyuanrong package",
    )
    setup_spec.install_requires = [
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
    ]
    setup_spec.extras["cpp"] = [f"{base_name}_cpp_sdk==" + setup_spec.version]
    setup_spec.extras["dashboard"] = [f"{base_name}_dashboard==" + setup_spec.version]
    setup_spec.extras["faas"] = [f"{base_name}_faas==" + setup_spec.version]
    datasystem_version = get_component_version("datasystem", setup_spec.version)
    setup_spec.extras["default"] = [
        f"{base_name}_runtime==" + setup_spec.version,
        f"{base_name}_functionsystem==" + setup_spec.version,
        f"{base_name}_datasystem==" + datasystem_version,
    ]
    setup_spec.extras["all"] = (
        setup_spec.extras["default"]
        + setup_spec.extras["cpp"]
        + setup_spec.extras["faas"]
        + setup_spec.extras["dashboard"]
    )
    setup_spec.entry_points = {
        "console_scripts": [
            "yrcli=yr.cli.scripts:main",
            "yr=yr.cli.main:main",
        ]
    }



def _get_soname(filepath):
    """Read SONAME of a shared library via readelf. Returns None if not found."""
    try:
        result = subprocess.run(
            ["readelf", "-d", filepath], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if "(SONAME)" in line:
                match = _re.search(r"\[([^\]]+)\]", line)
                if match:
                    return match.group(1)
    except Exception:
        pass
    return None


def _get_needed(filepath):
    """Return the set of NEEDED sonames declared in a binary/library."""
    needed = set()
    try:
        result = subprocess.run(
            ["readelf", "-d", filepath], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            if "(NEEDED)" in line:
                match = _re.search(r"\[([^\]]+)\]", line)
                if match:
                    needed.add(match.group(1))
    except Exception:
        pass
    return needed


def _md5_file(filepath):
    """Compute MD5 digest of a file."""
    digest = hashlib.md5()
    with open(filepath, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _so_specificity(filepath):
    name = os.path.basename(filepath)
    if ".so." in name:
        version = name.split(".so.", 1)[1]
        parts = version.split(".")
        return (len(parts), [int(part) if part.isdigit() else 0 for part in parts])
    return (0, [])


def _collect_so_files(directory):
    files = []
    if not os.path.isdir(directory):
        return files
    for name in os.listdir(directory):
        if ".so" not in name:
            continue
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            files.append(path)
    return files


def _dedup_so_in_dir(directory):
    groups = {}
    for filepath in _collect_so_files(directory):
        soname = _get_soname(filepath)
        if soname is None:
            continue
        groups.setdefault((soname, _md5_file(filepath)), []).append(filepath)

    removed = 0
    for (soname, _), files in groups.items():
        if len(files) <= 1:
            continue
        to_keep = next((f for f in files if os.path.basename(f) == soname), None)
        if to_keep is None:
            files.sort(key=_so_specificity)
            to_keep = files[-1]
        for filepath in files:
            if filepath == to_keep:
                continue
            print(f"  [dedup-indir] {os.path.basename(filepath)} -> kept {os.path.basename(to_keep)}")
            os.remove(filepath)
            removed += 1
    return removed


def _dedup_so_cross_dirs(primary_dir, secondary_dir):
    primary_index = set()
    for filepath in _collect_so_files(primary_dir):
        soname = _get_soname(filepath)
        if soname is None:
            continue
        primary_index.add((soname, _md5_file(filepath)))

    secondary_meta = {}
    for filepath in _collect_so_files(secondary_dir):
        soname = _get_soname(filepath)
        if soname is None:
            continue
        secondary_meta[filepath] = (soname, _md5_file(filepath))

    candidates = {
        filepath
        for filepath, (soname, md5) in secondary_meta.items()
        if (soname, md5) in primary_index
    }
    all_secondary_files = [
        os.path.join(secondary_dir, name)
        for name in os.listdir(secondary_dir)
        if os.path.isfile(os.path.join(secondary_dir, name))
    ] if os.path.isdir(secondary_dir) else []
    required_sonames = set()
    for filepath in all_secondary_files:
        if filepath not in candidates:
            required_sonames.update(_get_needed(filepath))

    changed = True
    while changed:
        changed = False
        for filepath in list(candidates):
            soname, _ = secondary_meta[filepath]
            if soname in required_sonames:
                candidates.discard(filepath)
                required_sonames.update(_get_needed(filepath))
                changed = True

    removed = 0
    for filepath in candidates:
        print(
            f"  [dedup-cross] removing {os.path.basename(filepath)} "
            f"from {os.path.relpath(secondary_dir)}"
        )
        os.remove(filepath)
        removed += 1
    return removed


def _remove_build_artifacts(root_dir):
    removed = 0
    if not os.path.isdir(root_dir):
        return removed
    for dirpath, dirnames, filenames in os.walk(root_dir, topdown=True):
        for dirname in list(dirnames):
            if dirname not in ("cmake", "pkgconfig"):
                continue
            path = os.path.join(dirpath, dirname)
            shutil.rmtree(path)
            dirnames.remove(dirname)
            print(f"  [artifacts] removed dir {os.path.relpath(path)}")
            removed += 1
        for filename in filenames:
            if not filename.endswith(".a"):
                continue
            path = os.path.join(dirpath, filename)
            os.remove(path)
            print(f"  [artifacts] removed {os.path.relpath(path)}")
            removed += 1
    return removed


def optimize_wheel_files(build_lib, setup_type):
    if setup_type == SetupType.OPENYUANRONG_RUNTIME:
        java_lib = os.path.join(build_lib, "yr/runtime/service/java/lib")
        go_bin = os.path.join(build_lib, "yr/runtime/service/go/bin")
        print("Optimizing openyuanrong_runtime wheel files...")
        removed = _dedup_so_in_dir(java_lib)
        print(f"  java/lib intra-dedup: removed {removed} files")
        removed = _dedup_so_in_dir(go_bin)
        print(f"  go/bin intra-dedup: removed {removed} files")
        removed = _dedup_so_cross_dirs(java_lib, go_bin)
        print(f"  go/bin vs java/lib cross-dedup: removed {removed} files")
        removed = _remove_build_artifacts(os.path.join(build_lib, "yr/runtime"))
        print(f"  build artifacts removed: {removed} items")
    elif setup_type == SetupType.OPENYUANRONG_CPP_SDK:
        cpp_lib = os.path.join(build_lib, "yr/cpp/lib")
        cpp_svc_lib = os.path.join(build_lib, "yr/runtime/service/cpp/lib")
        print("Optimizing openyuanrong_cpp_sdk wheel files...")
        removed = _dedup_so_in_dir(cpp_lib)
        print(f"  cpp/lib intra-dedup: removed {removed} files")
        removed = _dedup_so_in_dir(cpp_svc_lib)
        print(f"  service/cpp/lib intra-dedup: removed {removed} files")
        removed = _remove_build_artifacts(os.path.join(build_lib, "yr/cpp"))
        removed += _remove_build_artifacts(os.path.join(build_lib, "yr/runtime/service/cpp"))
        print(f"  build artifacts removed: {removed} items")
    elif setup_type == SetupType.OPENYUANRONG_FULL:
        print("Optimizing openyuanrong-full wheel files...")
        inner = os.path.join(build_lib, "yr/inner")
        total_removed = 0
        for dirpath, _, _ in os.walk(inner):
            dirname = os.path.basename(dirpath)
            if dirname not in ("lib", "bin"):
                continue
            removed = _dedup_so_in_dir(dirpath)
            if removed:
                print(f"  intra-dedup {os.path.relpath(dirpath, inner)}: removed {removed} files")
            total_removed += removed
        removed = _remove_build_artifacts(os.path.join(inner, "runtime"))
        removed += _remove_build_artifacts(os.path.join(inner, "datasystem"))
        total_removed += removed
        print(f"  build artifacts removed: {removed} items")
        print(f"  total files removed: {total_removed}")


def copy_file(target, filename, root):
    """copy file"""
    if not os.path.exists(filename):
        return
    source = os.path.relpath(filename, root)
    dst = os.path.join(target, source)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if is_same_file_content(filename, dst):
        return
    link_or_copy_file(filename, dst)


def link_or_copy_file(source, target):
    """stage a file by hardlinking when possible, falling back to copy."""
    link_source = os.path.realpath(source) if os.path.islink(source) else source
    try:
        if os.path.exists(target):
            os.remove(target)
        os.link(link_source, target, follow_symlinks=True)
        return
    except OSError:
        pass
    try:
        shutil.copy(source, target, follow_symlinks=True)
    except Exception as e:
        print(f"Warning: Failed to copy {source}: {e}")


def is_same_file_content(source, target):
    """return whether target already contains the same file payload"""
    if not os.path.exists(target):
        return False
    try:
        if os.path.samefile(source, target):
            return True
    except OSError:
        pass
    try:
        if os.path.getsize(source) != os.path.getsize(target):
            return False
        return _md5_file(source) == _md5_file(target)
    except OSError:
        return False


def copy_file_flat(target, filename):
    """copy a file into target without preserving its source directory"""
    if not os.path.exists(filename):
        return
    os.makedirs(target, exist_ok=True)
    dst = os.path.join(target, os.path.basename(filename))
    if is_same_file_content(filename, dst):
        return
    link_or_copy_file(filename, dst)


def contains_keyword(text, keywords):
    return any(kw in text for kw in keywords)


def copy_python_runtime_metrics_exporters(build_lib, runtime_dir):
    """copy metrics exporter plugins needed by the Python runtime service"""
    runtime_python_yr_dir = os.path.join(runtime_dir, "service", "python", "yr")
    if not os.path.isdir(runtime_python_yr_dir):
        return

    target_dir = os.path.join(build_lib, "yr/runtime/service/python/yr")
    for exporter in PYTHON_RUNTIME_METRICS_EXPORTERS:
        source = os.path.join(runtime_python_yr_dir, exporter)
        if not os.path.exists(source):
            raise FileNotFoundError(f"required Python runtime metrics exporter is missing: {source}")
        copy_file(target_dir, source, runtime_python_yr_dir)


def is_shared_library(filename):
    return (
        filename.endswith((
            ".so",
            ".so.1",
            ".so.2",
            ".so.3",
            ".so.4",
            ".so.5",
            ".so.6",
            ".so.7",
            ".so.8",
            ".dylib",
        ))
        or ".so." in filename
        or ".dylib." in filename
    )


def python_native_dependency_roots():
    """native source dirs used to rebuild the Python wheel top-level closure"""
    return [
        os.path.join(ROOT_DIR, "../../output/openyuanrong/runtime/service/python/yr"),
        os.path.join(ROOT_DIR, "../../output/openyuanrong/runtime/sdk/cpp/lib"),
        os.path.join(ROOT_DIR, "../../output/openyuanrong/functionsystem/lib"),
        os.path.join(ROOT_DIR, "../../output/openyuanrong/datasystem/sdk/cpp/lib"),
        os.path.join(ROOT_DIR, "../../output/openyuanrong/datasystem/sdk/go/lib"),
        os.path.join(ROOT_DIR, "../../output/openyuanrong/datasystem/service/lib"),
        os.path.join(ROOT_DIR, "../../build/output/runtime/sdk/cpp/lib"),
        os.path.join(ROOT_DIR, "../../build/output/runtime/service/python/yr"),
    ]


def copy_python_native_dependencies_to_top_level_yr(build_lib):
    """copy fnruntime dependencies to yr/ so preload can avoid system libs"""
    target_dir = os.path.join(build_lib, "yr")
    for source_root in python_native_dependency_roots():
        if not os.path.isdir(source_root):
            continue
        for root, _, filenames in os.walk(source_root):
            for filename in filenames:
                if filename.startswith("fnruntime"):
                    continue
                if is_python_top_level_native_excluded(filename):
                    continue
                if not is_shared_library(filename):
                    continue
                copy_file_flat(target_dir, os.path.join(root, filename))


def select_fnruntime_binaries(candidates):
    ext_suffix = sysconfig.get_config_var("EXT_SUFFIX")
    if ext_suffix:
        expected_name = f"fnruntime{ext_suffix}"
        matched = [path for path in candidates if os.path.basename(path) == expected_name]
        if matched:
            return matched
    fallback_names = {"fnruntime.so", "fnruntime.dylib"}
    fallback = [path for path in candidates if os.path.basename(path) in fallback_names]
    if fallback:
        return fallback[:1]
    if len(candidates) == 1:
        return candidates
    return []


def strip_nonmatching_fnruntime(target_dir):
    """remove fnruntime binaries that do not match current Python ABI"""
    yr_dir = os.path.join(target_dir, "yr")
    if not os.path.isdir(yr_dir):
        return
    candidates = []
    for name in os.listdir(yr_dir):
        if not name.startswith("fnruntime"):
            continue
        file_path = os.path.join(yr_dir, name)
        if os.path.isfile(file_path) and is_shared_library(name):
            candidates.append(file_path)
    keep = set(select_fnruntime_binaries(candidates))
    for filename in candidates:
        if filename not in keep:
            os.remove(filename)


def prune_openyuanrong_native_libraries(build_lib):
    """Keep the main openyuanrong wheel free of native runtime libraries."""
    yr_dir = os.path.join(build_lib, "yr")
    if not os.path.isdir(yr_dir):
        return
    for name in os.listdir(yr_dir):
        path = os.path.join(yr_dir, name)
        if os.path.isfile(path) and is_shared_library(name):
            os.remove(path)


def copy_openyuanrong(build_lib):
    """copy the lightweight openyuanrong package metadata and deploy helpers"""
    python_runtime_version = os.getenv("PYTHON_RUNTIME_VERSION", "python3.11")

    import re
    import sys

    python_tag = f"py{sys.version_info.major}{sys.version_info.minor}"

    third_party_source_dir = os.path.join(ROOT_DIR, "yr", "third_party")
    if os.path.exists(third_party_source_dir):
        for root, _, files in os.walk(third_party_source_dir):
            for filename in files:
                copy_file(
                    os.path.join(build_lib, "yr/third_party"),
                    os.path.join(root, filename),
                    third_party_source_dir,
                )

    output_root_dir = os.path.join(ROOT_DIR, "../../output/openyuanrong")
    deploy_dir = os.path.join(output_root_dir, "deploy/process")
    if os.path.exists(deploy_dir):
        for root, _, files in os.walk(deploy_dir):
            for filename in files:
                copy_file(
                    os.path.join(build_lib, "yr/deploy/process"),
                    os.path.join(root, filename),
                    deploy_dir,
                )

    cli_services_src = os.path.join(ROOT_DIR, "yr", "cli", "services.yaml")
    cli_services_dst = os.path.join(build_lib, "yr", "cli", "services.yaml")
    if os.path.exists(cli_services_src):
        os.makedirs(os.path.dirname(cli_services_dst), exist_ok=True)
        shutil.copy(cli_services_src, cli_services_dst)

    if os.path.exists(cli_services_dst):
        with open(cli_services_dst, "r", encoding="utf-8") as file_obj:
            content = file_obj.read()
        content = re.sub(r"runtime: python3\.\d+", f"runtime: {python_runtime_version}", content)
        content = re.sub(r"^(\s*)py:", f"\\1{python_tag}:", content, flags=re.MULTILINE)
        with open(cli_services_dst, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)


def copy_openyuanrong_sdk(build_lib):
    """copy C++ SDK .so files"""
    cpp_sdk_root = os.path.abspath(os.path.join(ROOT_DIR, "../../build/output/runtime/sdk/cpp"))
    files_to_include = []
    for root, _, fs in os.walk(cpp_sdk_root):
        for i in fs:
            files_to_include.append(os.path.join(root, i))
    for filename in files_to_include:
        copy_file(os.path.join(build_lib, "yr/cpp"), filename, cpp_sdk_root)

    yr_root = os.path.join(ROOT_DIR, "yr")
    files_to_include = []
    fnruntime_candidates = []
    for root, _, fs in os.walk(yr_root):
        for i in fs:
            if not is_shared_library(i):
                continue
            file_path = os.path.join(root, i)
            if i.startswith("fnruntime"):
                fnruntime_candidates.append(file_path)
                continue
            files_to_include.append(file_path)
    files_to_include.extend(select_fnruntime_binaries(fnruntime_candidates))
    for filename in files_to_include:
        copy_file(build_lib, filename, ROOT_DIR)
    copy_python_native_dependencies_to_top_level_yr(build_lib)


def copy_openyuanrong_runtime(build_lib):
    """copy runtime files for the split runtime wheel"""
    root_dir = os.path.join(ROOT_DIR, "../../output/openyuanrong")
    runtime_dir = os.path.join(root_dir, "runtime")
    files_to_include = []
    for root, _, fs in os.walk(runtime_dir):
        if contains_keyword(root, ["runtime/sdk", "runtime/service/python", "runtime/service/cpp"]):
            continue
        for i in fs:
            files_to_include.append(os.path.join(root, i))
    for filename in files_to_include:
        copy_file(os.path.join(build_lib, "yr/runtime"), filename, runtime_dir)

    runtime_python_yr_dir = os.path.join(runtime_dir, "service", "python", "yr")
    if not os.path.isdir(runtime_python_yr_dir):
        return
    copy_python_runtime_metrics_exporters(build_lib, runtime_dir)

    fnruntime_candidates = []
    for filename in os.listdir(runtime_python_yr_dir):
        if filename.startswith("fnruntime") and is_shared_library(filename):
            fnruntime_candidates.append(os.path.join(runtime_python_yr_dir, filename))
    for filename in select_fnruntime_binaries(fnruntime_candidates):
        copy_file(os.path.join(build_lib, "yr"), filename, runtime_python_yr_dir)
    copy_python_native_dependencies_to_top_level_yr(build_lib)


def copy_openyuanrong_faas(build_lib):
    """copy faas files for the split faas wheel"""
    file_to_exclude = {
        "faasfrontend",
        "faasfrontend.zip",
        "faasscheduler",
        "faasscheduler.zip",
    }
    root_dir = os.path.join(ROOT_DIR, "../../output/openyuanrong")
    faas_dir = os.path.join(root_dir, "pattern/pattern_faas")
    files_to_include = []
    for root, _, fs in os.walk(faas_dir):
        if contains_keyword(root, ["faasmanager"]):
            continue
        for i in fs:
            if i in file_to_exclude:
                continue
            files_to_include.append(os.path.join(root, i))
    for filename in files_to_include:
        copy_file(os.path.join(build_lib, "yr/faas"), filename, faas_dir)


def copy_openyuanrong_dashboard(build_lib):
    """copy dashboard files for the split dashboard wheel"""
    root_dir = os.path.join(ROOT_DIR, "../../output/openyuanrong")
    dashboard_dir = os.path.join(root_dir, "dashboard")
    files_to_include = []
    for root, _, fs in os.walk(dashboard_dir):
        for i in fs:
            files_to_include.append(os.path.join(root, i))
    for filename in files_to_include:
        copy_file(os.path.join(build_lib, "yr/dashboard"), filename, dashboard_dir)


def copy_openyuanrong_cpp_sdk(build_lib):
    """copy C++ SDK and C++ runtime service files for the split C++ wheel"""
    root_dir = os.path.join(ROOT_DIR, "../../output/openyuanrong")
    cpp_sdk_dir = os.path.join(root_dir, "runtime/sdk/cpp")
    files_to_include = []
    for root, _, fs in os.walk(cpp_sdk_dir):
        for i in fs:
            files_to_include.append(os.path.join(root, i))
    for filename in files_to_include:
        copy_file(os.path.join(build_lib, "yr/cpp"), filename, cpp_sdk_dir)

    runtime_dir = os.path.join(root_dir, "runtime")
    files_to_include = []
    for root, _, fs in os.walk(runtime_dir):
        if contains_keyword(root, ["runtime/service/cpp"]):
            for i in fs:
                files_to_include.append(os.path.join(root, i))
    for filename in files_to_include:
        copy_file(os.path.join(build_lib, "yr/runtime"), filename, runtime_dir)


def copy_openyuanrong_full(build_lib):
    """copy all deployable components into yr.inner for the full wheel"""
    root_dir = os.path.join(ROOT_DIR, "../../output/openyuanrong")
    if not os.path.isdir(root_dir):
        return

    exclude_components = {"dashboard", "VERSION"}
    exclude_subdirs = {
        "datasystem/sdk",
        "runtime/service/python/yr/third_party",
    }
    exclude_files = {
        "runtime/service/python/yr/libdatasystem_worker.so",
        "runtime/service/java/lib/libdatasystem_worker.so",
        "runtime/service/cpp/lib/libdatasystem_worker.so",
        "pattern/pattern_faas/faasfrontend/faasfrontend.zip",
        "pattern/pattern_faas/faasfrontend/faasfrontend",
        "pattern/pattern_faas/faasscheduler/faasscheduler",
        "pattern/pattern_faas/faasscheduler/faasscheduler.zip",
        "pattern/pattern_faas/faasmanager/faasmanager.zip",
    }

    for component in os.listdir(root_dir):
        if component in exclude_components:
            continue
        component_dir = os.path.join(root_dir, component)
        if not os.path.isdir(component_dir):
            continue
        for root, _, fs in os.walk(component_dir):
            rel_root = os.path.relpath(root, root_dir)
            if any(rel_root == excl or rel_root.startswith(excl + os.sep) for excl in exclude_subdirs):
                continue
            for filename in fs:
                source = os.path.join(root, filename)
                rel_file = os.path.relpath(source, root_dir)
                if rel_file in exclude_files:
                    continue
                copy_file(os.path.join(build_lib, "yr/inner"), source, root_dir)


def run_ext(build_lib):
    """run ext"""
    if setup_spec.setup_type == SetupType.OPENYUANRONG:
        copy_openyuanrong(build_lib)
    elif setup_spec.setup_type == SetupType.OPENYUANRONG_SDK:
        copy_openyuanrong_sdk(build_lib)
    elif setup_spec.setup_type == SetupType.OPENYUANRONG_RUNTIME:
        copy_openyuanrong_runtime(build_lib)
    elif setup_spec.setup_type == SetupType.OPENYUANRONG_FAAS:
        copy_openyuanrong_faas(build_lib)
    elif setup_spec.setup_type == SetupType.OPENYUANRONG_DASHBOARD:
        copy_openyuanrong_dashboard(build_lib)
    elif setup_spec.setup_type == SetupType.OPENYUANRONG_CPP_SDK:
        copy_openyuanrong_cpp_sdk(build_lib)
    elif setup_spec.setup_type == SetupType.OPENYUANRONG_FULL:
        copy_openyuanrong_full(build_lib)


class BuildExtImpl(build_ext):
    """build ext impl"""

    def run(self):
        run_ext(self.build_lib)
        if setup_spec.setup_type == SetupType.OPENYUANRONG:
            prune_openyuanrong_native_libraries(self.build_lib)
        if setup_spec.setup_type == SetupType.OPENYUANRONG_FULL:
            for filename in ("yr/libdatasystem_worker.so",):
                path = os.path.join(self.build_lib, filename)
                if os.path.exists(path):
                    print(f"  [full] removing redundant base file: {filename}")
                    os.remove(path)
        optimize_wheel_files(self.build_lib, setup_spec.setup_type)


class BuildPyImpl(build_py):
    """build py impl"""

    def run(self):
        super().run()
        strip_nonmatching_fnruntime(self.build_lib)
        if setup_spec.setup_type == SetupType.OPENYUANRONG:
            prune_openyuanrong_native_libraries(self.build_lib)
        shutil.rmtree(os.path.join(self.build_lib, "yr", "tests"), ignore_errors=True)


class BdistWheelImpl(_bdist_wheel):
    """bdist wheel impl"""

    def get_tag(self):
        """Build wheels with a supported platform tag."""
        tag = next(tags.sys_tags())
        platform_tag = get_wheel_platform_tag() or tag.platform
        if setup_spec.setup_type == SetupType.OPENYUANRONG:
            return "py3", "none", platform_tag
        return tag.interpreter, tag.abi, platform_tag

    def run(self):
        super().run()
        for wheel_name in os.listdir(self.dist_dir):
            if not wheel_name.endswith(".whl"):
                continue
            wheel_path = os.path.join(self.dist_dir, wheel_name)
            strip_wheel_tests(wheel_path)


class DevelopImpl(develop):
    """develop impl for editable install"""

    def run(self):
        super().run()
        run_ext(ROOT_DIR)


class BinaryDistribution(setuptools.Distribution):
    """binary distribution"""

    def __init__(self, attrs=None):
        super().__init__(attrs)
        # Keep generated metadata compatible with older upload validators.
        self.metadata.metadata_version = Version("2.2")

    def has_ext_modules(self):
        """has ext modules"""
        return True


def strip_wheel_tests(wheel_path):
    """remove yr/tests from built wheel"""
    with zipfile.ZipFile(wheel_path, "r") as src:
        members = src.infolist()
        if not any(member.filename.startswith("yr/tests/") for member in members):
            return

        temp_fd, temp_path = tempfile.mkstemp(suffix=".whl", dir=os.path.dirname(wheel_path))
        os.close(temp_fd)
        try:
            with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as dst:
                for member in members:
                    if member.filename.startswith("yr/tests/"):
                        continue
                    dst.writestr(member, src.read(member.filename))
            os.replace(temp_path, wheel_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)


warnings.filterwarnings("ignore", category=setuptools.SetuptoolsDeprecationWarning)

ext_modules = []
if setup_spec.setup_type in (
    SetupType.OPENYUANRONG,
    SetupType.OPENYUANRONG_SDK,
    SetupType.OPENYUANRONG_ALL,
    SetupType.OPENYUANRONG_RUNTIME,
    SetupType.OPENYUANRONG_DASHBOARD,
    SetupType.OPENYUANRONG_FAAS,
    SetupType.OPENYUANRONG_CPP_SDK,
    SetupType.OPENYUANRONG_FULL,
):
    ext_modules = [Extension("yr._dummy", sources=[])]

setuptools.setup(
    name=setup_spec.name,
    version=setup_spec.version,
    author="openyuanrong",
    python_requires=">=3.9,<3.15",
    classifiers=[
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
    ],
    cmdclass={
        "bdist_wheel": BdistWheelImpl,
        "build_ext": BuildExtImpl,
        "build_py": BuildPyImpl,
        "develop": DevelopImpl,
    },
    distclass=BinaryDistribution,
    ext_modules=ext_modules,
    packages=setup_spec.get_packages(),
    install_requires=setup_spec.install_requires,
    include_package_data=True,
    package_data={
        "yr": [
            "includes/*.pxd",
            "includes/*.pxi",
            "*.so.*",
            "*.so",
            "*.dylib.*",
            "*.dylib",
            "cli/*.toml",
            "cli/*.yaml",
            "cli/*.jinja",
        ],
    },
    exclude_package_data={
        "": ["BUILD", "BUILD.bazel"],
        "yr": ["tests/*", "tests/**/*"],
    },
    extras_require=setup_spec.extras,
    entry_points=setup_spec.entry_points,
)
