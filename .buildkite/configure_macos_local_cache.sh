#!/usr/bin/env bash

# Source this file from macOS Buildkite jobs so the cache variables remain
# available to build.sh and pip. Keep the cache outside the checkout because
# macOS jobs use BUILDKITE_CLEAN_CHECKOUT=true.
if [ -n "${YR_MACOS_CACHE_ROOT:-}" ]; then
    _YR_MACOS_CACHE_ROOT="${YR_MACOS_CACHE_ROOT}"
elif [ -n "${BUILDKITE_BUILD_PATH:-}" ]; then
    _YR_MACOS_CACHE_ROOT="${BUILDKITE_BUILD_PATH}/.cache/openyuanrong"
else
    _YR_MACOS_CACHE_ROOT="${HOME}/Library/Caches/openyuanrong-buildkite"
fi

export SDK_BAZEL_DISK_CACHE="${_YR_MACOS_CACHE_ROOT}/bazel-action/bazel6-macos-arm64-v1"
export BAZEL_REPOSITORY_CACHE="${_YR_MACOS_CACHE_ROOT}/bazel-repository"
export PIP_CACHE_DIR="${_YR_MACOS_CACHE_ROOT}/pip"

mkdir -p \
    "${SDK_BAZEL_DISK_CACHE}" \
    "${BAZEL_REPOSITORY_CACHE}" \
    "${PIP_CACHE_DIR}"

report_macos_local_cache() {
    local cache_name
    local cache_path
    local cache_size

    for cache_name in bazel-action bazel-repository pip; do
        case "${cache_name}" in
            bazel-action) cache_path="${SDK_BAZEL_DISK_CACHE}" ;;
            bazel-repository) cache_path="${BAZEL_REPOSITORY_CACHE}" ;;
            pip) cache_path="${PIP_CACHE_DIR}" ;;
        esac
        cache_size="$(du -sh "${cache_path}" 2>/dev/null | awk '{print $1}')"
        printf 'macOS local cache: name=%s path=%s size=%s\n' \
            "${cache_name}" "${cache_path}" "${cache_size:-unknown}"
    done
}

report_macos_local_cache
unset _YR_MACOS_CACHE_ROOT
