#!/usr/bin/env python3
# coding=UTF-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""file handler for agent instance file operations"""

import base64
import os
import re
import logging

_logger = logging.getLogger(__name__)

_DEFAULT_READ_LENGTH = 2097152  # 2MB, base64 encoded ≈ 2.7MB, under 4MB gRPC limit
_MAX_WRITE_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB raw data per chunk
_MAX_READ_LENGTH = 4 * 1024 * 1024  # 4MB max per read
_ALLOWED_WRITE_MODES = ("wb", "ab")
_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_upload_id(upload_id: str) -> None:
    """Reject upload_id containing path separators or traversal sequences."""
    if not _SAFE_ID_PATTERN.match(upload_id):
        raise ValueError("upload_id contains invalid characters")


class FileHandler:
    """FileHandler provides file read/write capabilities for agent instances.

    These handlers run inside the faasExecutor python runtime and operate on
    files within the container, used to support file upload/download flows
    triggered through the /api/agent endpoint.
    """

    @staticmethod
    def file_write(path, data, mode="wb", upload_id="", is_last=False):
        """Write data to a file.

        When ``upload_id`` is provided, data is written to a temporary file
        ``<path>.<upload_id>.tmp`` and atomically renamed to ``path`` on the
        final chunk (``is_last=True``). This prevents data corruption when
        multiple concurrent uploads target the same path.

        Without ``upload_id``, falls back to direct write (``open(path, mode)``)
        for backward compatibility.

        Args:
            path: Target file path.
            data: Base64-encoded bytes to write.
            mode: File open mode, ``wb`` for first chunk or ``ab`` for append.
            upload_id: Unique upload session ID for temp-file isolation.
            is_last: True for the final chunk, triggers atomic rename.

        Returns:
            dict with ``success``, ``path`` and cumulative ``size``.
        """
        if mode not in _ALLOWED_WRITE_MODES:
            raise ValueError(f"invalid mode '{mode}', must be one of {_ALLOWED_WRITE_MODES}")
        if upload_id:
            _validate_upload_id(upload_id)
        raw_data = base64.b64decode(data, validate=True) if data else b""
        if len(raw_data) > _MAX_WRITE_CHUNK_SIZE:
            raise ValueError(f"data size {len(raw_data)} exceeds max chunk size {_MAX_WRITE_CHUNK_SIZE}")

        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        if upload_id:
            tmp_path = path + "." + upload_id + ".tmp"
            with open(tmp_path, mode) as f:
                f.write(raw_data)
                f.flush()
                os.fsync(f.fileno())
                size = f.tell()
            if is_last:
                os.rename(tmp_path, path)
                _logger.info("file_write done (rename), path: %s, size: %s", path, size)
                return {"success": True, "path": path, "size": size}
            _logger.info("file_write chunk ok, tmp: %s, size: %s", tmp_path, size)
            return {"success": True, "path": tmp_path, "size": size}

        with open(path, mode) as f:
            f.write(raw_data)
            f.flush()
            os.fsync(f.fileno())
            size = f.tell()
        _logger.info("file_write ok, path: %s, size: %s", path, size)
        return {"success": True, "path": path, "size": size}

    @staticmethod
    def file_read(path: str, offset: int = 0, length: int = _DEFAULT_READ_LENGTH) -> dict:
        """Read a chunk of data from a file.

        Opens the file with ``open(path, 'rb')``, seeks to ``offset`` and
        reads up to ``length`` bytes. ``is_last`` is True when the read
        reaches end-of-file.

        Args:
            path: Source file path.
            offset: Byte offset to start reading from.
            length: Maximum number of bytes to read.

        Returns:
            dict with ``data`` (bytes), ``offset``, ``is_last`` and
            ``total_size``. Raises FileNotFoundError when the file does not
            exist (left for the caller to handle).
        """
        if offset < 0:
            raise ValueError(f"offset must be non-negative, got {offset}")
        if length <= 0:
            raise ValueError(f"length must be positive, got {length}")
        if length > _MAX_READ_LENGTH:
            raise ValueError(f"length {length} exceeds max read size {_MAX_READ_LENGTH}")
        total_size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read(length)
        is_last = (offset + len(chunk)) >= total_size
        _logger.info(
            "file_read ok, path: %s, offset: %s, read: %s, total: %s, is_last: %s",
            path, offset, len(chunk), total_size, is_last,
        )
        return {
            "data": base64.b64encode(chunk).decode("ascii"),
            "offset": offset,
            "is_last": is_last,
            "total_size": total_size,
        }
