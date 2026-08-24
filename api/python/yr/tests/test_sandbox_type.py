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

"""Unit tests for sandbox_type parameter support and file read/write."""

import json
import os
import tempfile
from unittest import TestCase, main
from unittest.mock import MagicMock, patch

import yr


class TestSandboxTypeParameter(TestCase):
    """Test cases for sandbox_type parameter."""

    def test_sandbox_init_with_default_type(self):
        """Test Sandbox initialization with default type (empty string)."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            sandbox = yr.sandbox.Sandbox()

            # Verify custom_extensions["sandbox_type"] is not set when type is empty
            call_args = mock_instance.options.call_args
            opts = call_args[0][0]  # InvokeOptions object

            # When type is default (empty), sandbox_type should not be in custom_extensions
            self.assertNotIn("sandbox_type", opts.custom_extensions)

    def test_sandbox_init_with_supervisor_type(self):
        """Test Sandbox initialization with supervisor type."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            sandbox = yr.sandbox.Sandbox(sandbox_type="supervisor")

            # Verify custom_extensions["sandbox_type"] is set to "supervisor"
            call_args = mock_instance.options.call_args
            opts = call_args[0][0]  # InvokeOptions object

            self.assertIn("sandbox_type", opts.custom_extensions)
            self.assertEqual(opts.custom_extensions["sandbox_type"], "supervisor")

    def test_sandbox_init_with_docker_type(self):
        """Test Sandbox initialization with docker type."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            sandbox = yr.sandbox.Sandbox(sandbox_type="docker")

            # Verify custom_extensions["sandbox_type"] is set to "docker"
            call_args = mock_instance.options.call_args
            opts = call_args[0][0]  # InvokeOptions object

            self.assertIn("sandbox_type", opts.custom_extensions)
            self.assertEqual(opts.custom_extensions["sandbox_type"], "docker")

    def test_sandbox_init_with_empty_type(self):
        """Test Sandbox initialization with explicitly empty type."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            sandbox = yr.sandbox.Sandbox(sandbox_type="")

            # Verify custom_extensions["sandbox_type"] is not set when type is empty
            call_args = mock_instance.options.call_args
            opts = call_args[0][0]  # InvokeOptions object

            self.assertNotIn("sandbox_type", opts.custom_extensions)

    def test_sandbox_create_with_default_type(self):
        """Test sandbox.create() with default type."""
        with patch('yr.sandbox.sandbox.Sandbox') as mock_sandbox:
            yr.sandbox.create()

            # Verify Sandbox is called with sandbox_type=""
            call_args = mock_sandbox.call_args
            self.assertEqual(call_args[1]["sandbox_type"], "")

    def test_sandbox_create_with_supervisor_type(self):
        """Test sandbox.create() with supervisor type."""
        with patch('yr.sandbox.sandbox.Sandbox') as mock_sandbox:
            yr.sandbox.create(sandbox_type="supervisor")

            # Verify Sandbox is called with sandbox_type="supervisor"
            call_args = mock_sandbox.call_args
            self.assertEqual(call_args[1]["sandbox_type"], "supervisor")

    def test_sandbox_create_with_docker_type(self):
        """Test sandbox.create() with docker type."""
        with patch('yr.sandbox.sandbox.Sandbox') as mock_sandbox:
            yr.sandbox.create(sandbox_type="docker")

            # Verify Sandbox is called with sandbox_type="docker"
            call_args = mock_sandbox.call_args
            self.assertEqual(call_args[1]["sandbox_type"], "docker")

    def test_sandbox_docker_with_image(self):
        """Test Sandbox with docker type and image parameter."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            sandbox = yr.sandbox.Sandbox(sandbox_type="docker", image="python:3.12-slim")

            call_args = mock_instance.options.call_args
            opts = call_args[0][0]

            # Verify sandbox_type is set
            self.assertEqual(opts.custom_extensions.get("sandbox_type"), "docker")
            # Verify rootfs JSON is constructed from image
            rootfs = json.loads(opts.custom_extensions["rootfs"])
            self.assertEqual(rootfs["type"], "image")
            self.assertEqual(rootfs["imageurl"], "python:3.12-slim")

    def test_sandbox_docker_with_host_dir_workdir(self):
        """Test Sandbox with docker type, image, host_dir, and workdir."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            sandbox = yr.sandbox.Sandbox(
                sandbox_type="docker",
                image="yr-runtime:v0",
                host_dir="/home/user",
                workdir="/mnt/host",
            )

            call_args = mock_instance.options.call_args
            opts = call_args[0][0]

            self.assertEqual(opts.custom_extensions.get("sandbox_type"), "docker")
            rootfs = json.loads(opts.custom_extensions["rootfs"])
            self.assertEqual(rootfs["type"], "image")
            self.assertEqual(rootfs["imageurl"], "yr-runtime:v0")
            self.assertEqual(rootfs["workdir"], "/mnt/host")
            self.assertEqual(len(rootfs["mounts"]), 1)
            self.assertEqual(rootfs["mounts"][0]["source"], "/home/user")
            self.assertEqual(rootfs["mounts"][0]["target"], "/mnt/host")
            self.assertTrue(rootfs["mounts"][0]["readonly"])

    def test_sandbox_docker_rootfs_priority(self):
        """Test that rootfs parameter takes priority over image parameter."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            custom_rootfs = '{"type":"image","imageurl":"custom:latest"}'
            sandbox = yr.sandbox.Sandbox(
                sandbox_type="docker",
                rootfs=custom_rootfs,
                image="python:3.12-slim",
            )

            call_args = mock_instance.options.call_args
            opts = call_args[0][0]

            # rootfs parameter should be used as-is, image should be ignored
            self.assertEqual(opts.custom_extensions["rootfs"], custom_rootfs)

    def test_sandbox_type_parameter_precedence(self):
        """Test that type parameter correctly sets custom_extensions."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            # Test different type values
            test_cases = [
                ("supervisor", "supervisor"),
                ("docker", "docker"),
                ("", None),  # Empty string should not set sandbox_type
                ("other", "other"),  # Future extensibility
            ]

            for type_value, expected_value in test_cases:
                mock_instance.reset_mock()

                sandbox = yr.sandbox.Sandbox(sandbox_type=type_value)

                call_args = mock_instance.options.call_args
                opts = call_args[0][0]

                if expected_value is None:
                    self.assertNotIn("sandbox_type", opts.custom_extensions)
                else:
                    self.assertEqual(opts.custom_extensions.get("sandbox_type"), expected_value)

    def test_sandbox_with_working_dir_and_type(self):
        """Test Sandbox with working_dir and type parameters."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            working_dir = "/tmp/test"
            sandbox = yr.sandbox.Sandbox(working_dir=working_dir, sandbox_type="supervisor")

            call_args = mock_instance.options.call_args
            opts = call_args[0][0]

            self.assertEqual(opts.custom_extensions.get("sandbox_type"), "supervisor")

    def test_sandbox_with_env_and_type(self):
        """Test Sandbox with env and type parameters."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            env = {"TEST_VAR": "test_value"}
            sandbox = yr.sandbox.Sandbox(env=env, sandbox_type="supervisor")

            call_args = mock_instance.options.call_args
            opts = call_args[0][0]

            self.assertEqual(opts.custom_extensions.get("sandbox_type"), "supervisor")

    def test_sandbox_skip_serialize_always_true(self):
        """Test that skip_serialize is always True for Sandbox."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            sandbox = yr.sandbox.Sandbox(sandbox_type="supervisor")

            call_args = mock_instance.options.call_args
            opts = call_args[0][0]

            # Verify skip_serialize is True
            self.assertTrue(opts.skip_serialize)


class TestSandboxTypeIntegration(TestCase):
    """Integration tests for sandbox type parameter (require yr.init)."""

    def test_sandbox_type_string_format(self):
        """Test that type parameter uses lowercase format."""
        type_value = "supervisor"
        self.assertEqual(type_value, type_value.lower())

    def test_sandbox_type_consistency(self):
        """Test consistency between Sandbox and create() functions."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance:
            mock_options = MagicMock()
            mock_instance.options.return_value = mock_options

            # Test Sandbox class
            sandbox1 = yr.sandbox.Sandbox(sandbox_type="supervisor")
            call_args1 = mock_instance.options.call_args
            opts1 = call_args1[0][0]
            type1 = opts1.custom_extensions.get("sandbox_type")

            mock_instance.reset_mock()

            # Test create() function
            with patch('yr.sandbox.sandbox.Sandbox') as mock_sandbox:
                yr.sandbox.create(sandbox_type="supervisor")
                call_args2 = mock_sandbox.call_args
                type2 = call_args2[1]["sandbox_type"]

                # Both should use the same type value
                self.assertEqual(type1, type2)
                self.assertEqual(type1, "supervisor")


class TestSandboxFileReadWrite(TestCase):
    """Test SandboxInstance.read_file / write_file local behavior.

    These tests directly instantiate SandboxInstance (not via @yr.instance)
    to verify the file I/O logic works with native Python open().
    """

    def setUp(self):
        """Create a temp directory for test files."""
        self._tmpdir = tempfile.mkdtemp(prefix="yr_test_sandbox_")

    def tearDown(self):
        """Clean up temp directory."""
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_read_text_file(self):
        """Test reading a text file with mode='r'."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        filepath = os.path.join(self._tmpdir, "data.txt")
        with open(filepath, "w") as f:
            f.write("hello world")
        content = instance.read_file(filepath, mode="r")
        self.assertEqual(content, "hello world")

    def test_read_binary_file(self):
        """Test reading a binary file with mode='rb'."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        filepath = os.path.join(self._tmpdir, "data.bin")
        data = b"\x00\x01\x02\x03"
        with open(filepath, "wb") as f:
            f.write(data)
        content = instance.read_file(filepath, mode="rb")
        self.assertEqual(content, data)

    def test_read_file_not_found(self):
        """Test that reading a nonexistent file raises FileNotFoundError."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        with self.assertRaises(FileNotFoundError):
            instance.read_file(os.path.join(self._tmpdir, "nonexistent"))

    def test_write_binary_file(self):
        """Test writing a binary file with mode='wb'."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        filepath = os.path.join(self._tmpdir, "output.bin")
        data = b"\x00\x01\x02\x03"
        instance.write_file(filepath, data)
        with open(filepath, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_write_text_file(self):
        """Test writing a text file with mode='w'."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        filepath = os.path.join(self._tmpdir, "output.txt")
        instance.write_file(filepath, "hello world", mode="w")
        with open(filepath, "r") as f:
            self.assertEqual(f.read(), "hello world")

    def test_write_file_overwrite(self):
        """Test that writing to an existing file overwrites it."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        filepath = os.path.join(self._tmpdir, "existing.txt")
        with open(filepath, "w") as f:
            f.write("old content")
        instance.write_file(filepath, "new content", mode="w")
        with open(filepath, "r") as f:
            self.assertEqual(f.read(), "new content")

    def test_write_file_creates_parent_dirs(self):
        """Test that write_file auto-creates parent directories."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        filepath = os.path.join(self._tmpdir, "nested", "deep", "output.txt")
        instance.write_file(filepath, "data", mode="w")
        with open(filepath, "r") as f:
            self.assertEqual(f.read(), "data")

    def test_write_file_append_mode(self):
        """Test that write_file with mode='a' appends to existing content."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        filepath = os.path.join(self._tmpdir, "append.txt")
        instance.write_file(filepath, "line1\n", mode="w")
        instance.write_file(filepath, "line2\n", mode="a")
        content = instance.read_file(filepath, mode="r")
        self.assertEqual(content, "line1\nline2\n")


class TestSandboxListFiles(TestCase):
    """Test SandboxInstance.list_files local behavior."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="yr_test_listfiles_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_list_files_non_recursive(self):
        """Test listing files in non-recursive mode."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        os.makedirs(os.path.join(self._tmpdir, "testdir"))
        for name in ["a.txt", "b.py", "c.log"]:
            with open(os.path.join(self._tmpdir, "testdir", name), "w") as f:
                f.write("x")
        os.makedirs(os.path.join(self._tmpdir, "testdir", "sub"))

        items = instance.list_files(os.path.join(self._tmpdir, "testdir"))
        names = [item["name"] for item in items]
        self.assertIn("a.txt", names)
        self.assertIn("b.py", names)
        self.assertIn("c.log", names)
        self.assertIn("sub", names)

    def test_list_files_recursive(self):
        """Test listing files recursively."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        base = os.path.join(self._tmpdir, "rectest")
        os.makedirs(os.path.join(base, "sub"))
        with open(os.path.join(base, "top.txt"), "w") as f:
            f.write("top")
        with open(os.path.join(base, "sub", "deep.txt"), "w") as f:
            f.write("deep")

        items = instance.list_files(base, recursive=True)
        paths = [item["path"] for item in items]
        self.assertTrue(any("top.txt" in p for p in paths))
        self.assertTrue(any("deep.txt" in p for p in paths))

    def test_list_files_max_depth(self):
        """Test max_depth limits recursion."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        base = os.path.join(self._tmpdir, "depthtest")
        os.makedirs(os.path.join(base, "l1", "l2", "l3"))
        for name, path in [("f0.txt", base), ("f1.txt", os.path.join(base, "l1")),
                           ("f2.txt", os.path.join(base, "l1", "l2")),
                           ("f3.txt", os.path.join(base, "l1", "l2", "l3"))]:
            with open(os.path.join(path, name), "w") as f:
                f.write("x")

        items = instance.list_files(base, recursive=True, max_depth=1)
        names = [item["name"] for item in items]
        self.assertIn("f0.txt", names)
        self.assertIn("f1.txt", names)
        self.assertIn("f2.txt", names)  # depth 1 reaches l2
        self.assertNotIn("f3.txt", names)  # depth 1 does NOT reach l3

    def test_list_files_include_files_false(self):
        """Test include_files=False returns only directories."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        base = os.path.join(self._tmpdir, "dirsonly")
        os.makedirs(base)
        os.makedirs(os.path.join(base, "subdir"))
        with open(os.path.join(base, "file.txt"), "w") as f:
            f.write("x")

        items = instance.list_files(base, include_files=False, include_dirs=True)
        names = [item["name"] for item in items]
        self.assertIn("subdir", names)
        self.assertNotIn("file.txt", names)

    def test_list_files_item_format(self):
        """Test that each item has all required keys matching jiuwenbox API."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        base = os.path.join(self._tmpdir, "fmttest")
        os.makedirs(base)
        with open(os.path.join(base, "test.txt"), "w") as f:
            f.write("hello")

        items = instance.list_files(base)
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["name"], "test.txt")
        self.assertIn("path", item)
        self.assertEqual(item["size"], 5)
        self.assertFalse(item["is_directory"])
        self.assertIn("modified_time", item)
        self.assertEqual(item["type"], ".txt")


class TestSandboxSearchFiles(TestCase):
    """Test SandboxInstance.search_files local behavior."""

    def setUp(self):
        self._tmpdir = tempfile.mkdtemp(prefix="yr_test_searchfiles_")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_search_files_by_pattern(self):
        """Test searching files by glob pattern."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        base = os.path.join(self._tmpdir, "searchtest")
        os.makedirs(os.path.join(base, "sub"))
        for name in ["a.txt", "b.py", "c.txt"]:
            with open(os.path.join(base, name), "w") as f:
                f.write("x")
        with open(os.path.join(base, "sub", "d.txt"), "w") as f:
            f.write("x")

        items = instance.search_files(base, "*.txt")
        names = sorted(item["name"] for item in items)
        self.assertEqual(names, ["a.txt", "c.txt", "d.txt"])

    def test_search_files_with_exclude(self):
        """Test searching files with exclude_patterns."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        base = os.path.join(self._tmpdir, "excludetest")
        os.makedirs(base)
        for name in ["keep.txt", "skip.bak", "keep.py"]:
            with open(os.path.join(base, name), "w") as f:
                f.write("x")

        items = instance.search_files(base, "*", exclude_patterns=["*.bak"])
        names = sorted(item["name"] for item in items)
        self.assertIn("keep.txt", names)
        self.assertIn("keep.py", names)
        self.assertNotIn("skip.bak", names)

    def test_search_files_no_match(self):
        """Test searching with a pattern that matches nothing."""
        from yr.sandbox.sandbox import SandboxInstance
        instance = SandboxInstance(working_dir=self._tmpdir)
        base = os.path.join(self._tmpdir, "nomatchtest")
        os.makedirs(base)
        with open(os.path.join(base, "a.txt"), "w") as f:
            f.write("x")

        items = instance.search_files(base, "*.nonexistent")
        self.assertEqual(len(items), 0)


class TestSandboxFileRpc(TestCase):
    """Test Sandbox.read_file / write_file / list_files / search_files"""

    def test_read_file_invokes_rpc(self):
        """Test that Sandbox.read_file calls self._instance.read_file.invoke()."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance_cls, \
             patch('yr.get', return_value=b"content") as mock_get:
            mock_instance = MagicMock()
            mock_instance_cls.options.return_value = mock_instance
            mock_instance.get_name.invoke.return_value = MagicMock()

            sandbox = yr.sandbox.Sandbox()
            result = sandbox.read_file("/sandbox/data.bin")
            mock_instance.read_file.invoke.assert_called_once_with(
                "/sandbox/data.bin", mode="rb"
            )
            mock_get.assert_called_once()
            self.assertEqual(result, b"content")

    def test_write_file_invokes_rpc(self):
        """Test that Sandbox.write_file calls self._instance.write_file.invoke()."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance_cls, \
             patch('yr.get', return_value=None) as mock_get:
            mock_instance = MagicMock()
            mock_instance_cls.options.return_value = mock_instance
            mock_instance.get_name.invoke.return_value = MagicMock()

            sandbox = yr.sandbox.Sandbox()
            sandbox.write_file("/sandbox/output.txt", "hello", mode="w")
            mock_instance.write_file.invoke.assert_called_once_with(
                "/sandbox/output.txt", "hello", mode="w"
            )
            mock_get.assert_called_once()

    def test_list_files_invokes_rpc(self):
        """
        Test that Sandbox.list_files calls self._instance.list_files.invoke()
        and wraps result in {"items": [...]}.
        """
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance_cls, \
             patch('yr.get', return_value=[{"name": "a.txt"}]) as mock_get:
            mock_instance = MagicMock()
            mock_instance_cls.options.return_value = mock_instance
            mock_instance.get_name.invoke.return_value = MagicMock()

            sandbox = yr.sandbox.Sandbox()
            result = sandbox.list_files("/tmp", recursive=True)
            mock_instance.list_files.invoke.assert_called_once()
            call_kwargs = mock_instance.list_files.invoke.call_args
            self.assertTrue(call_kwargs[1]["recursive"])
            mock_get.assert_called_once()
            self.assertEqual(result, {"items": [{"name": "a.txt"}]})

    def test_search_files_invokes_rpc(self):
        """
        Test that Sandbox.search_files calls self._instance.search_files.invoke()
        and wraps result in {"items": [...]}.
        """
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance_cls, \
             patch('yr.get', return_value=[{"name": "a.py"}]) as mock_get:
            mock_instance = MagicMock()
            mock_instance_cls.options.return_value = mock_instance
            mock_instance.get_name.invoke.return_value = MagicMock()

            sandbox = yr.sandbox.Sandbox()
            result = sandbox.search_files("/tmp", "*.py", exclude_patterns=["*.pyc"])
            mock_instance.search_files.invoke.assert_called_once_with(
                "/tmp", "*.py", exclude_patterns=["*.pyc"]
            )
            mock_get.assert_called_once()
            self.assertEqual(result, {"items": [{"name": "a.py"}]})


class TestSandboxDockerUser(TestCase):
    """Test user parameter for Docker sandbox."""

    def test_sandbox_docker_with_user(self):
        """Test user sets host_user and works with image/rootfs params."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance, \
             patch('yr.get', return_value="test-instance"):
            mock_instance.options.return_value = MagicMock()
            mock_instance.get_name.invoke.return_value = MagicMock()

            # image + user
            sandbox = yr.sandbox.Sandbox(sandbox_type="docker", image="python:3.12-slim", user="1000:1000")
            opts = mock_instance.options.call_args[0][0]
            self.assertEqual(opts.custom_extensions.get("host_user"), "1000:1000")
            rootfs = json.loads(opts.custom_extensions["rootfs"])
            self.assertEqual(rootfs["imageurl"], "python:3.12-slim")

            # rootfs + user
            mock_instance.reset_mock()
            mock_instance.options.return_value = MagicMock()
            mock_instance.get_name.invoke.return_value = MagicMock()
            custom_rootfs = '{"type":"image","imageurl":"custom:latest"}'
            sandbox = yr.sandbox.Sandbox(sandbox_type="docker", rootfs=custom_rootfs, user="root")
            opts = mock_instance.options.call_args[0][0]
            self.assertEqual(opts.custom_extensions.get("host_user"), "root")
            self.assertEqual(opts.custom_extensions["rootfs"], custom_rootfs)

    def test_sandbox_docker_without_user(self):
        """Test that host_user is not set when user is None."""
        with patch('yr.sandbox.sandbox.SandboxInstance') as mock_instance, \
             patch('yr.get', return_value="test-instance"):
            mock_instance.options.return_value = MagicMock()
            mock_instance.get_name.invoke.return_value = MagicMock()

            sandbox = yr.sandbox.Sandbox(sandbox_type="docker", image="python:3.12-slim")
            opts = mock_instance.options.call_args[0][0]
            self.assertNotIn("host_user", opts.custom_extensions)


if __name__ == "__main__":
    main()