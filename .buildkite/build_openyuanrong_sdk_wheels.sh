#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${HOME:-}" ]]; then
    if [[ "$(uname)" == "Darwin" ]]; then
        HOME="$(dscl . -read "/Users/$(id -un)" NFSHomeDirectory | awk '{print $2}')"
    else
        HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"
    fi
    if [[ -z "${HOME}" ]]; then
        printf 'Cannot resolve HOME for SDK build user %s\n' "$(id -un)" >&2
        exit 1
    fi
    export HOME
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-${ROOT_DIR}/output}"
SDK_PYTHON_VERSIONS="${SDK_PYTHON_VERSIONS:-python3.9 python3.10 python3.11 python3.12 python3.13 python3.14}"
BUILD_VERSION="${BUILD_VERSION:-$(cat "${ROOT_DIR}/VERSION")}"
BOOST_VERSION="${BOOST_VERSION:-1.87.0}"
SDK_BAZEL_JOBS="${SDK_BAZEL_JOBS:-8}"
SDK_BAZEL_BUILD_ROOT="${SDK_BAZEL_BUILD_ROOT:-${ROOT_DIR}/build/sdk-${BUILDKITE_JOB_ID:-local}}"
SDK_BUILD_MODE="${SDK_BUILD_MODE:-wheel}"

case "${OUTPUT_DIR}" in
    /*) ;;
    *) OUTPUT_DIR="${ROOT_DIR}/${OUTPUT_DIR}" ;;
esac

resolve_sdk_python() {
    local py_version="$1"
    local py_minor="${py_version#python}"
    local py_env="py${py_minor//./}"
    local conda_root="${CONDA_PREFIX:-${HOME}/miniforge3}"
    local candidate

    for candidate in \
        "${PYTHON314_PREFIX:-${HOME}/.cache/openyuanrong/python/3.14.6}/bin/${py_version}" \
        "${py_version}" \
        "/opt/buildtools/${py_version}/bin/${py_version}" \
        "${conda_root}/bin/${py_version}" \
        "${conda_root}/envs/${py_env}/bin/${py_version}" \
        "${conda_root}/envs/yuanrong/bin/${py_version}" \
        "/opt/homebrew/opt/python@${py_minor}/bin/${py_version}" \
        "/usr/local/opt/python@${py_minor}/bin/${py_version}"; do
        if command -v "${candidate}" >/dev/null 2>&1; then
            command -v "${candidate}"
            return 0
        fi
        if [ -x "${candidate}" ]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done
    if [ -d "${conda_root}/envs" ]; then
        candidate="$(find "${conda_root}/envs" -maxdepth 3 -type f -path "*/bin/${py_version}" 2>/dev/null | sort | head -1)"
        if [ -n "${candidate}" ] && [ -x "${candidate}" ]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    fi

    printf 'Missing SDK Python interpreter: %s\n' "${py_version}" >&2
    exit 1
}

pip_flags_for_python() {
    local python_bin="$1"
    if "${python_bin}" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
        printf '%s\n' '--break-system-packages'
    fi
}

ensure_sdk_python_packages() {
    local python_bin="$1"
    local pip_flag

    if "${python_bin}" -c 'import packaging, setuptools, wheel; import wheel.bdist_wheel' >/dev/null 2>&1; then
        return 0
    fi

    pip_flag="$(pip_flags_for_python "${python_bin}")"
    "${python_bin}" -m pip install ${pip_flag:+${pip_flag}} -q --retries 2 --timeout 60 --upgrade \
        --index-url "${PIP_INDEX_URL:-https://mirrors.huaweicloud.com/repository/pypi/simple}" \
        --trusted-host "${PIP_TRUSTED_HOST:-mirrors.huaweicloud.com}" \
        packaging setuptools wheel
}

build_sdk_wheel() {
    local py_version="$1"
    local python_bin="$2"
    local output_root="${SDK_BAZEL_BUILD_ROOT}/${py_version}"
    local cache_args=()

    ensure_sdk_python_packages "${python_bin}"
    if [ -n "${SDK_BAZEL_DISK_CACHE:-}" ]; then
        cache_args=(-l "${SDK_BAZEL_DISK_CACHE}")
    fi

    # rrt-runtime ships as the standalone openyuanrong-rrt wheel (built once per
    # arch), so the per-cp SDK build skips the Rust target instead of recompiling
    # it once per Python version.
    BUILD_SKIP_RUST=1 \
        BUILD_SDK_WHEEL_ONLY=1 \
        BAZEL_OUTPUT_USER_ROOT="${output_root}" \
        BAZEL_OUTPUT_BASE="${output_root}/output" \
        bash "${ROOT_DIR}/build.sh" \
            -p "${python_bin}" \
            -v "${BUILD_VERSION}" \
            -j "${SDK_BAZEL_JOBS}" \
            "${cache_args[@]}"
    if [ "${OUTPUT_DIR}" != "${ROOT_DIR}/output" ]; then
        cp -R "${ROOT_DIR}"/output/openyuanrong_sdk-*.whl "${OUTPUT_DIR}/"
    fi

    if [ "${py_version}" = "python3.14" ]; then
        local cp314_wheels=("${OUTPUT_DIR}"/openyuanrong_sdk-*-cp314-cp314-*.whl)
        if [ "${#cp314_wheels[@]}" -ne 1 ] || [ ! -f "${cp314_wheels[0]}" ]; then
            printf 'Expected exactly one cp314 SDK wheel in %s\n' "${OUTPUT_DIR}" >&2
            find "${OUTPUT_DIR}" -maxdepth 1 -type f -name 'openyuanrong_sdk-*.whl' -print >&2
            exit 1
        fi
        bash "${ROOT_DIR}/.buildkite/verify_python314_sdk_wheel.sh" "${python_bin}" "${cp314_wheels[0]}"
    fi
}

build_sdk_common() {
    local py_version="$1"
    local python_bin="$2"
    local output_root="${SDK_BAZEL_BUILD_ROOT}/common"
    local cache_args=()

    printf 'Priming shared SDK native Bazel actions with %s (%s)\n' "${py_version}" "${python_bin}" >&2
    if [ -n "${SDK_BAZEL_DISK_CACHE:-}" ]; then
        cache_args=(-l "${SDK_BAZEL_DISK_CACHE}")
    fi
    BUILD_SKIP_RUST=1 \
        BUILD_SDK_COMMON_ONLY=1 \
        BAZEL_OUTPUT_USER_ROOT="${output_root}" \
        BAZEL_OUTPUT_BASE="${output_root}/output" \
        bash "${ROOT_DIR}/build.sh" \
            -p "${python_bin}" \
            -v "${BUILD_VERSION}" \
            -j "${SDK_BAZEL_JOBS}" \
            "${cache_args[@]}"
}

main() {
    local py_version
    local python_bin

    mkdir -p "${OUTPUT_DIR}"
    if [ "${SDK_BUILD_MODE}" = "common" ]; then
        py_version="${SDK_COMMON_PYTHON_VERSION:-${SDK_PYTHON_VERSIONS%% *}}"
        python_bin="$(resolve_sdk_python "${py_version}")"
        build_sdk_common "${py_version}" "${python_bin}"
        return
    fi
    if [ "${SDK_BUILD_MODE}" != "wheel" ]; then
        printf 'Unsupported SDK_BUILD_MODE: %s (expected common or wheel)\n' "${SDK_BUILD_MODE}" >&2
        exit 1
    fi
    for py_version in ${SDK_PYTHON_VERSIONS}; do
        python_bin="$(resolve_sdk_python "${py_version}")"
        printf 'Building openyuanrong-sdk for %s with %s\n' "${py_version}" "${python_bin}" >&2
        build_sdk_wheel "${py_version}" "${python_bin}"
    done
}

main "$@"
