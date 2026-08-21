#!/usr/bin/env python3
# coding=UTF-8
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

"""Verify Go runtime and system-function plugins share package ABI hashes."""

import logging
import pathlib
import re
import subprocess
import sys


ROOT = pathlib.Path("output/openyuanrong")
RUNTIME = ROOT / "runtime/service/go/bin/goruntime"
PLUGINS = [
    ROOT / "pattern/pattern_faas/faasscheduler/faasscheduler.so",
    ROOT / "pattern/pattern_faas/faasfrontend/faasfrontend.so",
]
# This package is the direct ABI boundary shared by goruntime and both system
# function plugins.  A third-party package can remain identical while this
# project package changes, so checking a generic dependency is insufficient.
SYMBOL = "go:link.pkghashbytes.yuanrong.org/kernel/runtime/libruntime/api"
SECTION_RE = re.compile(
    r"^\s*\[\s*(\d+)\]\s+(\S+)\s+\S+\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)\s+([0-9a-fA-F]+)"
)
SYMBOL_RE = re.compile(
    r"^\s*\d+:\s+([0-9a-fA-F]+)\s+(\d+)\s+\S+\s+\S+\s+\S+\s+(\d+)\s+"
    + re.escape(SYMBOL)
    + r"$"
)


def command_output(args):
    return subprocess.check_output(args).decode("latin1")


def pkghash(path):
    sections = []
    for line in command_output(["readelf", "-SW", str(path)]).splitlines():
        match = SECTION_RE.match(line)
        if match:
            idx, name, addr, off, size = match.groups()
            sections.append((int(idx), name, int(addr, 16), int(off, 16), int(size, 16)))
    data = path.read_bytes()
    for line in command_output(["readelf", "-sW", str(path)]).splitlines():
        match = SYMBOL_RE.match(line)
        if not match:
            continue
        addr = int(match.group(1), 16)
        size = int(match.group(2))
        section_idx = int(match.group(3))
        _, _, section_addr, section_off, _ = next(item for item in sections if item[0] == section_idx)
        offset = section_off + (addr - section_addr)
        return data[offset: offset + size].hex()
    raise RuntimeError(f"{SYMBOL} not found in {path}")


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    runtime_hash = pkghash(RUNTIME)
    logging.info("%s: %s", RUNTIME, runtime_hash)
    failed = False
    for plugin in PLUGINS:
        plugin_hash = pkghash(plugin)
        logging.info("%s: %s", plugin, plugin_hash)
        if plugin_hash != runtime_hash:
            logging.error(
                "Go plugin ABI mismatch: %s has %s, but goruntime has %s",
                plugin,
                plugin_hash,
                runtime_hash,
            )
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
