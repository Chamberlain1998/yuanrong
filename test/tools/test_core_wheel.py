#!/usr/bin/env python3
# coding=UTF-8

"""Regression tests for the Buildkite-only core wheel assembler."""

import base64
import csv
import hashlib
import io
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
import zipfile


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE_CORE_WHEEL = REPO_ROOT / ".buildkite" / "package_core_wheel.py"
DATA_SYSTEM_INSTALL = REPO_ROOT / "deploy" / "data_system" / "install.sh"
VERSION = "0.7.0+test"
PLATFORM = "manylinux_2_31_x86_64"


class CoreWheelTest(unittest.TestCase):
    def write_source_wheel(
        self,
        output_dir,
        distribution,
        tag,
        files,
        *,
        version=VERSION,
        entry_points="",
        extra_metadata="",
    ):
        normalized = re.sub(r"[-_.]+", "_", distribution)
        dist_info = f"{normalized}-{version}.dist-info"
        wheel_path = output_dir / f"{normalized}-{version}-{tag}.whl"
        metadata = (
            "Metadata-Version: 2.2\n"
            f"Name: {distribution}\n"
            f"Version: {version}\n"
            "Requires-Python: >=3.9,<3.15\n"
            f"{extra_metadata}"
            "\n"
        ).encode("utf-8")
        wheel = (
            "Wheel-Version: 1.0\n"
            "Generator: core-wheel-test\n"
            "Root-Is-Purelib: false\n"
            f"Tag: {tag}\n"
        ).encode("utf-8")

        with zipfile.ZipFile(
            wheel_path, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for member, payload in files.items():
                info = zipfile.ZipInfo(member)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                mode = 0o100755 if member.endswith(
                    (
                        "runtime-launcher",
                        "runtime_manager",
                        "function_proxy",
                    )
                ) else 0o100644
                if member.endswith(
                    ("datasystem_worker", "datasystem_coordinator")
                ):
                    mode = 0o100600
                elif member.endswith("docker_entryfile/daemonset/install.sh"):
                    mode = 0o100400
                info.external_attr = mode << 16
                archive.writestr(info, payload)
            archive.writestr(f"{dist_info}/METADATA", metadata)
            archive.writestr(f"{dist_info}/WHEEL", wheel)
            archive.writestr(f"{dist_info}/top_level.txt", b"yr\n")
            if entry_points:
                archive.writestr(
                    f"{dist_info}/entry_points.txt", entry_points.encode("utf-8")
                )
            archive.writestr(f"{dist_info}/RECORD", b"")
        return wheel_path

    def make_source_wheels(self, input_dir, *, datasystem_version=VERSION):
        self.write_source_wheel(
            input_dir,
            "openyuanrong",
            f"py3-none-{PLATFORM}",
            {
                "yr/__init__.py": b"",
                "yr/cli/main.py": b"def main(): pass\n",
                "yr/cli/scripts.py": b"def main(): pass\n",
                "yr/third_party/etcd/etcd": b"etcd",
            },
            entry_points=(
                "[console_scripts]\n"
                "yr = yr.cli.main:main\n"
                "yrcli = yr.cli.scripts:main\n"
            ),
            extra_metadata=(
                "Requires-Dist: click>=8\n"
                "Provides-Extra: default\n"
                f'Requires-Dist: openyuanrong_runtime=={VERSION}; extra == "default"\n'
                "Provides-Extra: faas\n"
                f'Requires-Dist: openyuanrong_faas=={VERSION}; extra == "faas"\n'
            ),
        )
        self.write_source_wheel(
            input_dir,
            "openyuanrong-functionsystem",
            f"py3-none-{PLATFORM}",
            {
                "yr/functionsystem/bin/runtime-launcher": b"launcher",
                "yr/functionsystem/bin/runtime_manager": b"manager",
                "yr/functionsystem/bin/function_proxy": b"proxy",
                "yr/functionsystem/lib/libgrpc.so.42": b"grpc",
                "yr/functionsystem/sym/function_proxy.sym": b"symbols",
            },
        )
        self.write_source_wheel(
            input_dir,
            "openyuanrong-datasystem",
            f"cp311-cp311-{PLATFORM}",
            {
                "yr/datasystem/__init__.py": b"",
                "yr/datasystem/object_client.py": b"client",
                "yr/datasystem/cli/command.py": b"cli",
                "yr/datasystem/include/datasystem/datasystem.h": b"sdk",
                "yr/datasystem/cpp_template/example.cpp": b"sdk",
                "yr/datasystem/dsbench_cpp": b"benchmark",
                "yr/datasystem/sdk_lib_list": b"sdk",
                "yr/datasystem/datasystem_worker": b"worker",
                "yr/datasystem/datasystem_coordinator": b"coordinator",
                "yr/datasystem/worker_config.json": b"{}",
                "yr/datasystem/lib/libdatasystem_worker.so": b"worker-lib",
                "yr/datasystem/lib/libds_client_py.so": b"PyInit_libds_client_py",
                "yr/datasystem/helm_chart/datasystem/Chart.yaml": b"name: ds",
                "yr/datasystem/docker_entryfile/daemonset/install.sh": b"#!/bin/sh\n",
            },
            version=datasystem_version,
        )
        self.write_source_wheel(
            input_dir,
            "openyuanrong-faas",
            f"cp311-cp311-{PLATFORM}",
            {
                "yr/faas/faasfrontend/faasfrontend.so": b"frontend",
                "yr/faas/faasscheduler/faasscheduler.so": b"scheduler",
                "yr/faas/templates/system-function-config.yaml": b"kind: function",
            },
        )
        self.write_source_wheel(
            input_dir,
            "openyuanrong-runtime",
            f"cp311-cp311-{PLATFORM}",
            {
                "yr/runtime/service/go/bin/goruntime": b"go-runtime",
                "yr/runtime/service/go/bin/libcpplibruntime.so": b"go-lib",
                "yr/runtime/service/go/bin/libgrpc.so.42": b"duplicate-lib",
                "yr/runtime/python/bin/python": b"python-runtime",
                "yr/runtime/java/bin/java": b"java-runtime",
                "yr/runtime/cpp/bin/cppruntime": b"cpp-runtime",
                "yr/runtime/sym/goruntime.sym": b"symbols",
            },
        )

    def run_packager(self, input_dir, output_dir, *, check=True):
        return subprocess.run(
            [
                sys.executable,
                str(PACKAGE_CORE_WHEEL),
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
            ],
            check=check,
            capture_output=True,
            text=True,
        )

    def test_builds_language_runtime_free_py3_core_wheel(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "output"
            input_dir.mkdir()
            self.make_source_wheels(input_dir)

            result = self.run_packager(input_dir, output_dir)
            wheel_path = (
                output_dir
                / f"openyuanrong_core-{VERSION}-py3-none-{PLATFORM}.whl"
            )
            self.assertTrue(wheel_path.is_file())
            self.assertIn(wheel_path.name, result.stdout)

            with zipfile.ZipFile(wheel_path) as archive:
                members = set(archive.namelist())
                self.assertIn("yr/cli/main.py", members)
                self.assertIn(
                    "yr/functionsystem/bin/runtime_manager", members
                )
                self.assertIn("yr/datasystem/datasystem_worker", members)
                self.assertIn(
                    "yr/datasystem/datasystem_coordinator", members
                )
                self.assertIn(
                    "yr/faas/faasfrontend/faasfrontend.so", members
                )
                self.assertIn(
                    "yr/faas/faasscheduler/faasscheduler.so", members
                )
                self.assertIn(
                    "yr/runtime/service/go/bin/goruntime", members
                )
                self.assertIn(
                    "yr/runtime/service/go/bin/libcpplibruntime.so", members
                )
                self.assertIn(
                    "yr/datasystem/helm_chart/datasystem/Chart.yaml", members
                )
                deployment_members = {
                    "yr/functionsystem/deploy/install.sh",
                    "yr/functionsystem/deploy/health_check.sh",
                    "yr/datasystem/deploy/install.sh",
                    "yr/datasystem/deploy/health_check.sh",
                }
                self.assertTrue(deployment_members.issubset(members))
                self.assertIn("yr/checkpoints/.keep", members)

                self.assertNotIn(
                    "yr/functionsystem/bin/runtime-launcher", members
                )
                self.assertNotIn(
                    "yr/datasystem/lib/libds_client_py.so", members
                )
                self.assertNotIn("yr/datasystem/object_client.py", members)
                self.assertNotIn("yr/datasystem/cli/command.py", members)
                self.assertNotIn(
                    "yr/datasystem/include/datasystem/datasystem.h", members
                )
                self.assertNotIn(
                    "yr/functionsystem/sym/function_proxy.sym", members
                )
                self.assertNotIn("yr/runtime/python/bin/python", members)
                self.assertNotIn(
                    "yr/runtime/service/go/bin/libgrpc.so.42", members
                )
                self.assertNotIn("yr/runtime/java/bin/java", members)
                self.assertNotIn(
                    "yr/runtime/cpp/bin/cppruntime", members
                )
                self.assertNotIn("yr/runtime/sym/goruntime.sym", members)

                dist_info = f"openyuanrong_core-{VERSION}.dist-info"
                metadata = archive.read(
                    f"{dist_info}/METADATA"
                ).decode("utf-8")
                self.assertIn("Name: openyuanrong-core\n", metadata)
                self.assertIn("Requires-Dist: click>=8\n", metadata)
                self.assertNotIn("Provides-Extra:", metadata)
                self.assertNotIn("Dynamic: provides-extra", metadata)
                self.assertNotIn("Dynamic: requires-dist", metadata)
                self.assertNotIn("openyuanrong_runtime", metadata)
                self.assertNotIn("openyuanrong_faas", metadata)

                wheel = archive.read(f"{dist_info}/WHEEL").decode("utf-8")
                self.assertIn("Root-Is-Purelib: false", wheel)
                self.assertIn(
                    f"Tag: py3-none-{PLATFORM}", wheel
                )
                entry_points = archive.read(
                    f"{dist_info}/entry_points.txt"
                ).decode("utf-8")
                self.assertIn("yr = yr.cli.main:main", entry_points)

                manager_info = archive.getinfo(
                    "yr/functionsystem/bin/runtime_manager"
                )
                self.assertEqual((manager_info.external_attr >> 16) & 0o777, 0o755)
                goruntime_info = archive.getinfo(
                    "yr/runtime/service/go/bin/goruntime"
                )
                self.assertEqual(
                    (goruntime_info.external_attr >> 16) & 0o777,
                    0o755,
                )
                for executable in (
                    "yr/datasystem/datasystem_worker",
                    "yr/datasystem/datasystem_coordinator",
                    "yr/datasystem/docker_entryfile/daemonset/install.sh",
                    *deployment_members,
                ):
                    executable_info = archive.getinfo(executable)
                    self.assertEqual(
                        (executable_info.external_attr >> 16) & 0o777,
                        0o755,
                    )
                faas_info = archive.getinfo(
                    "yr/faas/faasfrontend/faasfrontend.so"
                )
                self.assertEqual(
                    (faas_info.external_attr >> 16) & 0o777,
                    0o644,
                )

                record_member = f"{dist_info}/RECORD"
                rows = list(
                    csv.reader(
                        io.StringIO(
                            archive.read(record_member).decode("utf-8"),
                            newline="",
                        )
                    )
                )
                record_entries = {
                    member: (digest, size)
                    for member, digest, size in rows
                }
                self.assertEqual(len(record_entries), len(rows))
                self.assertEqual(set(record_entries), members)
                for member in members:
                    digest, size = record_entries[member]
                    if member == record_member:
                        self.assertEqual((digest, size), ("", ""))
                        continue
                    payload = archive.read(member)
                    expected = base64.urlsafe_b64encode(
                        hashlib.sha256(payload).digest()
                    ).rstrip(b"=").decode("ascii")
                    self.assertEqual(digest, f"sha256={expected}")
                    self.assertEqual(size, str(len(payload)))

    def test_datasystem_install_selects_flat_and_full_binary_layouts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            process_dir = temp_path / "yr" / "deploy" / "process"
            datasystem_dir = temp_path / "yr" / "datasystem"
            bin_dir = temp_path / "bin"
            process_dir.mkdir(parents=True)
            (datasystem_dir / "deploy").mkdir(parents=True)
            bin_dir.mkdir()
            readlink = bin_dir / "readlink"
            readlink.write_text(
                "#!/bin/sh\n"
                "shift\n"
                f'exec "{sys.executable}" -c '
                '\'import os, sys; print(os.path.realpath(sys.argv[1]))\' "$1"\n',
                encoding="utf-8",
            )
            readlink.chmod(0o755)

            def resolve_bin_dir():
                return subprocess.run(
                    [
                        "bash",
                        "-c",
                        '. "$1"; printf "%s" "$DATA_SYSTEM_BIN_DIR"',
                        "bash",
                        str(DATA_SYSTEM_INSTALL),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    env={
                        "BASE_DIR": str(process_dir),
                        "PATH": f"{bin_dir}:/usr/bin:/bin",
                    },
                ).stdout

            flat_worker = datasystem_dir / "datasystem_worker"
            flat_worker.touch()
            flat_worker.chmod(0o755)
            self.assertEqual(resolve_bin_dir(), str(datasystem_dir.resolve()))

            service_dir = datasystem_dir / "service"
            service_dir.mkdir()
            service_worker = service_dir / "datasystem_worker"
            service_worker.touch()
            service_worker.chmod(0o755)
            self.assertEqual(resolve_bin_dir(), str(service_dir.resolve()))

    def test_rejects_component_version_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = pathlib.Path(temp_dir)
            input_dir = temp_path / "input"
            output_dir = temp_path / "output"
            input_dir.mkdir()
            self.make_source_wheels(
                input_dir, datasystem_version="0.7.0+different"
            )

            result = self.run_packager(input_dir, output_dir, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertIn("source wheel versions do not match", result.stderr)
            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
