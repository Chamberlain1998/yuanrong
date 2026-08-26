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

set -euo pipefail

BASE_DIR=$(
	cd "$(dirname "$0")/.."
	pwd
)
RUST_WORKSPACE="${RRT_WORKSPACE:-${BASE_DIR}/api/rust}"
TARGET_DIR="${CARGO_TARGET_DIR:-${RUST_WORKSPACE}/target}"
BUILD_ARCH="${RRT_BUILD_ARCH:-$(uname -m)}"

case "${BUILD_ARCH}" in
	x86_64 | amd64)
		REQUIRED_RUST_TARGET="x86_64-unknown-linux-musl"
		EXPECTED_ELF_MACHINE='Advanced Micro Devices X86-64|X86-64'
		;;
	aarch64 | arm64)
		REQUIRED_RUST_TARGET="aarch64-unknown-linux-musl"
		EXPECTED_ELF_MACHINE='AArch64'
		;;
	*)
		echo "Error: unsupported RRT build architecture: ${BUILD_ARCH}" >&2
		exit 1
		;;
esac

if [[ -n "${RRT_TARGET:-}" && "${RRT_TARGET}" != "${REQUIRED_RUST_TARGET}" ]]; then
	echo "Error: RRT_TARGET must be ${REQUIRED_RUST_TARGET} for ${BUILD_ARCH}." >&2
	exit 1
fi
RRT_TARGET="${REQUIRED_RUST_TARGET}"
RRT_OUTPUT="${RRT_OUTPUT:-${BASE_DIR}/output/rrt-runtime}"

command -v cargo >/dev/null 2>&1 || {
	echo "Error: cargo not found. Build inside the rust compile image." >&2
	exit 1
}
command -v rustup >/dev/null 2>&1 || {
	echo "Error: rustup is required to provision ${RRT_TARGET}." >&2
	exit 1
}
command -v readelf >/dev/null 2>&1 || {
	echo "Error: readelf is required to verify the RRT release binary." >&2
	exit 1
}

if ! rustup target list --installed | grep -Fxq "${RRT_TARGET}"; then
	echo "Installing required Rust target: ${RRT_TARGET}"
	rustup target add "${RRT_TARGET}"
fi
rustup target list --installed | grep -Fxq "${RRT_TARGET}" || {
	echo "Error: required Rust target is unavailable: ${RRT_TARGET}" >&2
	exit 1
}

verify_static_musl_elf() {
	local binary="$1"
	local elf_header
	local elf_program_headers
	local elf_dynamic_section

	elf_header=$(readelf -hW "${binary}") || {
		echo "Error: RRT output is not a readable ELF binary: ${binary}" >&2
		exit 1
	}
	if ! grep -Eq "Machine:[[:space:]]*(${EXPECTED_ELF_MACHINE})" <<<"${elf_header}"; then
		echo "Error: RRT ELF machine does not match ${RRT_TARGET}: ${binary}" >&2
		exit 1
	fi
	elf_program_headers=$(readelf -lW "${binary}") || {
		echo "Error: unable to inspect RRT ELF program headers: ${binary}" >&2
		exit 1
	}
	if grep -Eq '(^|[[:space:]])INTERP([[:space:]]|$)' <<<"${elf_program_headers}"; then
		echo "Error: RRT release binary contains an ELF interpreter: ${binary}" >&2
		exit 1
	fi
	elf_dynamic_section=$(readelf -dW "${binary}") || {
		echo "Error: unable to inspect RRT ELF dynamic section: ${binary}" >&2
		exit 1
	}
	if grep -Eq '\(NEEDED\)' <<<"${elf_dynamic_section}"; then
		echo "Error: RRT release binary contains shared-library dependencies: ${binary}" >&2
		exit 1
	fi
}

cd "${RUST_WORKSPACE}"
cargo build --locked --release -p rrt-daemon --target "${RRT_TARGET}" --bin rrt-runtime

RRT_BINARY="${TARGET_DIR}/${RRT_TARGET}/release/rrt-runtime"
test -x "${RRT_BINARY}" || {
	echo "Error: rrt-runtime missing at ${RRT_BINARY}" >&2
	exit 1
}
verify_static_musl_elf "${RRT_BINARY}"

mkdir -p "$(dirname "${RRT_OUTPUT}")"
install -m 0755 "${RRT_BINARY}" "${RRT_OUTPUT}"
verify_static_musl_elf "${RRT_OUTPUT}"
echo "rrt-runtime built successfully: target=${RRT_TARGET} output=${RRT_OUTPUT}"
