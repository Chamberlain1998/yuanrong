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

usage() {
	cat <<'EOF'
Usage:
  bash scripts/with_local_build_cache.sh \
    --root <cache-root> --profile <release|ut> -- <command> [args...]

The release and ut profiles share downloaded dependencies, but keep build
outputs and action caches separate. Nothing is changed unless this wrapper is
used explicitly.
EOF
}

CACHE_ROOT=""
CACHE_PROFILE=""

while [ "$#" -gt 0 ]; do
	case "$1" in
	--root)
		[ "$#" -ge 2 ] || { echo "Error: --root requires a value" >&2; exit 2; }
		CACHE_ROOT="$2"
		shift 2
		;;
	--profile)
		[ "$#" -ge 2 ] || { echo "Error: --profile requires a value" >&2; exit 2; }
		CACHE_PROFILE="$2"
		shift 2
		;;
	--)
		shift
		break
		;;
	-h|--help)
		usage
		exit 0
		;;
	*)
		echo "Error: unknown argument: $1" >&2
		usage >&2
		exit 2
		;;
	esac
done

[ -n "${CACHE_ROOT}" ] || { echo "Error: --root is required" >&2; exit 2; }
[ -n "${CACHE_PROFILE}" ] || { echo "Error: --profile is required" >&2; exit 2; }
[ "$#" -gt 0 ] || { echo "Error: command after -- is required" >&2; exit 2; }

case "${CACHE_PROFILE}" in
release|ut) ;;
*)
	echo "Error: --profile must be release or ut" >&2
	exit 2
	;;
esac

mkdir -p "${CACHE_ROOT}"
CACHE_ROOT="$(cd "${CACHE_ROOT}" && pwd -P)"

platform="$(uname -s | tr '[:upper:]' '[:lower:]')"
machine="$(uname -m)"
bazel_major="unknown"
if command -v bazel >/dev/null 2>&1; then
	bazel_major="$(bazel --version 2>/dev/null | awk '{print $2}' | cut -d. -f1)"
	[ -n "${bazel_major}" ] || bazel_major="unknown"
fi
rust_version="unknown"
if command -v rustc >/dev/null 2>&1; then
	rust_version="$(rustc --version 2>/dev/null | awk '{print $2}')"
	[ -n "${rust_version}" ] || rust_version="unknown"
fi
go_version="unknown"
if command -v go >/dev/null 2>&1; then
	go_version="$(go env GOVERSION 2>/dev/null | sed 's/^go//')"
	[ -n "${go_version}" ] || go_version="unknown"
fi

bazel_namespace="${platform}-${machine}-bazel${bazel_major}"
rust_namespace="${platform}-${machine}-rust${rust_version}"
go_namespace="${platform}-${machine}-go${go_version}"
common_dir="${CACHE_ROOT}/common"
profile_dir="${CACHE_ROOT}/profiles/${CACHE_PROFILE}"

export YR_LOCAL_CACHE_ROOT="${CACHE_ROOT}"
export YR_LOCAL_CACHE_PROFILE="${CACHE_PROFILE}"

# Download/source caches are safe to share between release and UT builds.
export BAZEL_REPOSITORY_CACHE="${common_dir}/bazel-repository/${bazel_namespace}"
export CARGO_HOME="${common_dir}/cargo"
export GOPATH="${common_dir}/go"
export GOMODCACHE="${common_dir}/go-mod"
export PIP_CACHE_DIR="${common_dir}/pip"
export npm_config_cache="${common_dir}/npm"
export GRADLE_USER_HOME="${common_dir}/gradle"
export CCACHE_DIR="${common_dir}/ccache"
export SCCACHE_DIR="${common_dir}/sccache"

# Compiled outputs and Bazel action results are deliberately profile-specific.
export BAZEL_OUTPUT_USER_ROOT="${profile_dir}/bazel-output/${bazel_namespace}"
export BAZEL_OUTPUT_BASE="${BAZEL_OUTPUT_USER_ROOT}/output-base"
export BAZEL_DISK_CACHE="${profile_dir}/bazel-action/${bazel_namespace}"
export CARGO_TARGET_DIR="${profile_dir}/cargo-target/${rust_namespace}"
export GOCACHE="${profile_dir}/go-build/${go_namespace}"

mkdir -p \
	"${BAZEL_REPOSITORY_CACHE}" \
	"${CARGO_HOME}" \
	"${GOPATH}" \
	"${GOMODCACHE}" \
	"${PIP_CACHE_DIR}" \
	"${npm_config_cache}" \
	"${GRADLE_USER_HOME}" \
	"${CCACHE_DIR}" \
	"${SCCACHE_DIR}" \
	"${BAZEL_OUTPUT_USER_ROOT}" \
	"${BAZEL_DISK_CACHE}" \
	"${CARGO_TARGET_DIR}" \
	"${GOCACHE}"

exec "$@"
