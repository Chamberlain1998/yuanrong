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

"""LLT for yr.executor.file_handler.FileHandler."""

import base64
import os
import shutil
import tempfile
from unittest import TestCase, main

from yr.executor.file_handler import FileHandler


class TestFileHandlerWrite(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="llt_file_handler_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_single_chunk_no_upload_id(self):
        """Direct write without upload_id should produce the correct file."""
        path = self._path("simple.bin")
        raw = b"Hello, Agent!"
        data_b64 = base64.b64encode(raw).decode("ascii")
        result = FileHandler.file_write(path, data_b64, mode="wb")
        self.assertTrue(result["success"])
        self.assertEqual(result["path"], path)
        self.assertEqual(result["size"], len(raw))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), raw)

    def test_write_empty_data(self):
        """Empty data should produce an empty file."""
        path = self._path("empty.bin")
        result = FileHandler.file_write(path, "", mode="wb")
        self.assertTrue(result["success"])
        self.assertEqual(result["size"], 0)
        self.assertTrue(os.path.exists(path))

    def test_write_multi_chunk_with_upload_id(self):
        """Multi-chunk upload with upload_id should assemble correctly via temp file + rename."""
        path = self._path("multi.bin")
        upload_id = "u123"
        chunk1 = b"A" * 1024
        chunk2 = b"B" * 512

        r1 = FileHandler.file_write(path, base64.b64encode(chunk1).decode(),
                                    mode="wb", upload_id=upload_id, is_last=False)
        self.assertTrue(r1["success"])
        self.assertEqual(r1["size"], 1024)
        tmp_path = path + "." + upload_id + ".tmp"
        self.assertTrue(os.path.exists(tmp_path))
        self.assertFalse(os.path.exists(path))

        r2 = FileHandler.file_write(path, base64.b64encode(chunk2).decode(),
                                    mode="ab", upload_id=upload_id, is_last=True)
        self.assertTrue(r2["success"])
        self.assertEqual(r2["path"], path)
        self.assertEqual(r2["size"], 1536)
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(tmp_path))

        with open(path, "rb") as f:
            content = f.read()
        self.assertEqual(content, chunk1 + chunk2)

    def test_write_creates_parent_dirs(self):
        """Writing to a path with non-existent parent dirs should create them."""
        path = os.path.join(self.tmpdir, "sub", "deep", "file.bin")
        raw = b"data"
        result = FileHandler.file_write(path, base64.b64encode(raw).decode(), mode="wb")
        self.assertTrue(result["success"])
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(), raw)

    def test_write_overwrite_existing_file(self):
        """Writing with mode='wb' to an existing path should overwrite."""
        path = self._path("overwrite.bin")
        FileHandler.file_write(path, base64.b64encode(b"old").decode(), mode="wb")
        FileHandler.file_write(path, base64.b64encode(b"new").decode(), mode="wb")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"new")

    def test_write_binary_data_roundtrip(self):
        """Binary data with all byte values should round-trip correctly."""
        path = self._path("binary.bin")
        raw = bytes(range(256))
        FileHandler.file_write(path, base64.b64encode(raw).decode(), mode="wb")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), raw)

    def test_write_rejects_invalid_mode(self):
        """Non-whitelisted mode should be rejected."""
        path = self._path("bad_mode.bin")
        with self.assertRaises(ValueError):
            FileHandler.file_write(path, base64.b64encode(b"x").decode(), mode="r+")

    def test_write_rejects_invalid_upload_id(self):
        """upload_id with path separators should be rejected."""
        path = self._path("bad_upload_id.bin")
        with self.assertRaises(ValueError):
            FileHandler.file_write(path, base64.b64encode(b"x").decode(),
                                   mode="wb", upload_id="../../evil", is_last=True)

    def test_write_rejects_invalid_base64(self):
        """Invalid base64 characters should be rejected with strict validation."""
        path = self._path("bad_b64.bin")
        with self.assertRaises(Exception):
            FileHandler.file_write(path, "!!!not-base64!!!", mode="wb")

    def _path(self, name):
        return os.path.join(self.tmpdir, name)


class TestFileHandlerRead(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="llt_file_handler_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_read_full_file(self):
        """Read entire file in one chunk."""
        path = self._path("read.bin")
        raw = b"Hello, Agent! This is a test file."
        with open(path, "wb") as f:
            f.write(raw)
        result = FileHandler.file_read(path, offset=0, length=4194304)
        self.assertEqual(base64.b64decode(result["data"]), raw)
        self.assertTrue(result["is_last"])
        self.assertEqual(result["total_size"], len(raw))
        self.assertEqual(result["offset"], 0)

    def test_read_partial_chunk(self):
        """Read a partial chunk from a larger file."""
        path = self._path("partial.bin")
        raw = b"0" * 100
        with open(path, "wb") as f:
            f.write(raw)
        result = FileHandler.file_read(path, offset=10, length=50)
        self.assertEqual(base64.b64decode(result["data"]), b"0" * 50)
        self.assertFalse(result["is_last"])
        self.assertEqual(result["total_size"], 100)

    def test_read_multi_chunk_until_eof(self):
        """Read a file in multiple chunks until is_last is True."""
        path = self._path("multi_read.bin")
        raw = b"X" * 100
        with open(path, "wb") as f:
            f.write(raw)
        offset = 0
        chunks = []
        for _ in range(10):
            result = FileHandler.file_read(path, offset=offset, length=30)
            chunks.append(base64.b64decode(result["data"]))
            offset += len(chunks[-1])
            if result["is_last"]:
                break
        self.assertEqual(b"".join(chunks), raw)

    def test_read_empty_file(self):
        """Reading an empty file should return empty data with is_last=True."""
        path = self._path("empty.bin")
        open(path, "wb").close()
        result = FileHandler.file_read(path, offset=0, length=1024)
        self.assertEqual(base64.b64decode(result["data"]), b"")
        self.assertTrue(result["is_last"])
        self.assertEqual(result["total_size"], 0)

    def test_read_nonexistent_file_raises(self):
        """Reading a non-existent file should raise FileNotFoundError."""
        path = self._path("no_such_file.bin")
        with self.assertRaises(FileNotFoundError):
            FileHandler.file_read(path)

    def test_read_offset_at_eof(self):
        """Reading at or beyond EOF should return empty data with is_last=True."""
        path = self._path("eof.bin")
        raw = b"12345"
        with open(path, "wb") as f:
            f.write(raw)
        result = FileHandler.file_read(path, offset=5, length=1024)
        self.assertEqual(base64.b64decode(result["data"]), b"")
        self.assertTrue(result["is_last"])

    def test_read_rejects_negative_offset(self):
        """Negative offset should be rejected."""
        path = self._path("neg_offset.bin")
        with open(path, "wb") as f:
            f.write(b"data")
        with self.assertRaises(ValueError):
            FileHandler.file_read(path, offset=-1, length=10)

    def test_read_rejects_non_positive_length(self):
        """Zero or negative length should be rejected."""
        path = self._path("zero_len.bin")
        with open(path, "wb") as f:
            f.write(b"data")
        with self.assertRaises(ValueError):
            FileHandler.file_read(path, offset=0, length=0)

    def test_read_rejects_oversized_length(self):
        """Length exceeding _MAX_READ_LENGTH should be rejected."""
        path = self._path("oversized_len.bin")
        with open(path, "wb") as f:
            f.write(b"data")
        with self.assertRaises(ValueError):
            FileHandler.file_read(path, offset=0, length=8 * 1024 * 1024)

    def _path(self, name):
        return os.path.join(self.tmpdir, name)


class TestFileHandlerRoundTrip(TestCase):
    """Write then read back to verify data integrity."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="llt_file_handler_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_write_then_read_small(self):
        path = os.path.join(self.tmpdir, "rt_small.bin")
        raw = b"round-trip test data"
        FileHandler.file_write(path, base64.b64encode(raw).decode(), mode="wb")
        result = FileHandler.file_read(path, offset=0, length=4194304)
        self.assertEqual(base64.b64decode(result["data"]), raw)

    def test_write_then_read_large_with_upload_id(self):
        path = os.path.join(self.tmpdir, "rt_large.bin")
        raw = bytes(range(256)) * 1024  # 256KB
        upload_id = "rt_large"
        chunk_size = 4096
        for i in range(0, len(raw), chunk_size):
            chunk = raw[i:i + chunk_size]
            is_last = (i + chunk_size) >= len(raw)
            mode = "wb" if i == 0 else "ab"
            FileHandler.file_write(path, base64.b64encode(chunk).decode(),
                                   mode=mode, upload_id=upload_id, is_last=is_last)

        offset = 0
        collected = b""
        while True:
            result = FileHandler.file_read(path, offset=offset, length=chunk_size)
            collected += base64.b64decode(result["data"])
            offset += len(base64.b64decode(result["data"]))
            if result["is_last"]:
                break
        self.assertEqual(collected, raw)


if __name__ == "__main__":
    main()
