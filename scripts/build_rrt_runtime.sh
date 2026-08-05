#!/usr/bin/env bash
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

set -e

BASE_DIR=$(
	cd "$(dirname "$0")/.."
	pwd
)
RUST_WORKSPACE="${BASE_DIR}/api/rust"
TARGET_DIR="${CARGO_TARGET_DIR:-${RUST_WORKSPACE}/target}"

command -v cargo >/dev/null 2>&1 || {
	echo "Error: cargo not found. Build inside the rust compile image." >&2
	exit 1
}

cd "${RUST_WORKSPACE}"
cargo build --release -p rrt-daemon --bin rrt-runtime

mkdir -p "${BASE_DIR}/output"
cp "${TARGET_DIR}/release/rrt-runtime" "${BASE_DIR}/output/rrt-runtime"
echo "rrt-runtime built successfully: ${BASE_DIR}/output/rrt-runtime"
