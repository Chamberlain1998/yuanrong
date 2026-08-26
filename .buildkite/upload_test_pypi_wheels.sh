#!/usr/bin/env bash
set -euo pipefail

TEST_PYPI_URL="${TEST_PYPI_REPOSITORY_URL:-https://test.pypi.org/legacy/}"
PYPI_URL="${PYPI_REPOSITORY_URL:-https://upload.pypi.org/legacy/}"
PUBLISH_TO_TEST_PYPI="${PUBLISH_TEST_PYPI:-}"
PUBLISH_TO_PYPI="${PUBLISH_PYPI:-}"
PUBLISH_OPENYUANRONG_SDK_PYPI="${PUBLISH_OPENYUANRONG_SDK_PYPI:-false}"
PUBLISH_OPENYUANRONG_SANDBOX_PYPI="${PUBLISH_OPENYUANRONG_SANDBOX_PYPI:-true}"

is_enabled() {
    case "$1" in
        1|true|TRUE|yes|YES|on|ON) return 0 ;;
        *) return 1 ;;
    esac
}

tag_version="${YR_RELEASE_TAG:-${BUILDKITE_TAG:-}}"
tag_version="${tag_version#refs/tags/}"
case "${tag_version}" in
    v[0-9]*) tag_version="${tag_version#v}" ;;
esac

is_prerelease_version() {
    local version
    version="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
    case "${version}" in
        *a[0-9]*|*alpha[0-9]*|*b[0-9]*|*beta[0-9]*|*rc[0-9]*|*dev[0-9]*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

if [ -z "${PUBLISH_TO_TEST_PYPI}" ] && [ -z "${PUBLISH_TO_PYPI}" ] && [ -n "${tag_version}" ]; then
    if is_prerelease_version "${tag_version}"; then
        PUBLISH_TO_TEST_PYPI=1
    else
        PUBLISH_TO_PYPI=1
    fi
fi

if is_enabled "${PUBLISH_TO_TEST_PYPI}" && is_enabled "${PUBLISH_TO_PYPI}"; then
    printf 'ERROR: PUBLISH_TEST_PYPI and PUBLISH_PYPI cannot both be enabled.\n' >&2
    exit 1
fi

if ! is_enabled "${PUBLISH_TO_TEST_PYPI}" && ! is_enabled "${PUBLISH_TO_PYPI}"; then
    printf 'PyPI publishing is not enabled, skipping wheel upload.\n' >&2
    exit 0
fi

repository_name="PyPI"
repository_url="${PYPI_URL}"
api_token="${PYPI_API_TOKEN:-}"
if is_enabled "${PUBLISH_TO_TEST_PYPI}"; then
    repository_name="TestPyPI"
    repository_url="${TEST_PYPI_URL}"
    api_token="${TEST_PYPI_API_TOKEN:-}"
fi

if [ -z "${api_token}" ]; then
    if is_enabled "${PUBLISH_TO_TEST_PYPI}"; then
        printf 'TEST_PYPI_API_TOKEN is required when TestPyPI publishing is enabled.\n' >&2
        exit 1
    fi
    printf 'PYPI_API_TOKEN is required when PyPI publishing is enabled.\n' >&2
    exit 1
fi

if [ "$#" -eq 0 ]; then
    printf 'No wheel paths were requested for PyPI upload.\n' >&2
    exit 1
fi

is_selected_wheel() {
    case "$(basename "$1")" in
        openyuanrong_sdk*.whl)
            is_enabled "${PUBLISH_OPENYUANRONG_SDK_PYPI}"
            ;;
        openyuanrong_sandbox*.whl)
            is_enabled "${PUBLISH_OPENYUANRONG_SANDBOX_PYPI}"
            ;;
        *)
            return 1
            ;;
    esac
}

files=()
for path in "$@"; do
    if [ -d "${path}" ]; then
        while IFS= read -r -d '' file; do
            if is_selected_wheel "${file}"; then
                files+=("${file}")
            fi
        done < <(find "${path}" -type f \( -name 'openyuanrong_sdk*.whl' -o -name 'openyuanrong_sandbox*.whl' \) -print0)
        continue
    fi
    for file in ${path}; do
        [ -f "${file}" ] || continue
        if is_selected_wheel "${file}"; then
            files+=("${file}")
        fi
    done
done

if [ "${#files[@]}" -eq 0 ]; then
    printf 'No enabled openYuanRong wheel files matched for PyPI upload.\n' >&2
    exit 1
fi

for file in "${files[@]}"; do
    case "$(basename "${file}")" in
        *+*.whl)
            printf 'ERROR: %s uses a local version; PyPI repositories reject local versions.\n' "${file}" >&2
            exit 1
            ;;
    esac
done

if ! python3 -m pip show twine >/dev/null 2>&1; then
    pip_install_args=(
        install
        -q
        --retries 2
        --timeout 60
        --index-url "${PIP_INDEX_URL:-https://mirrors.huaweicloud.com/repository/pypi/simple}"
        --trusted-host "${PIP_TRUSTED_HOST:-mirrors.huaweicloud.com}"
        twine
    )
    if python3 -m pip install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
        pip_install_args+=(--break-system-packages)
    fi
    PIP_BREAK_SYSTEM_PACKAGES=1 python3 -m pip "${pip_install_args[@]}"
fi

attempt=1
until TWINE_USERNAME=__token__ TWINE_PASSWORD="${api_token}" \
    python3 -m twine upload \
        --repository-url "${repository_url}" \
        --skip-existing \
        "${files[@]}"; do
    if [ "${attempt}" -ge 3 ]; then
        printf 'ERROR: %s upload failed after %s attempts.\n' "${repository_name}" "${attempt}" >&2
        exit 1
    fi
    attempt=$((attempt + 1))
    printf 'WARNING: %s upload failed; retrying attempt %s/3.\n' "${repository_name}" "${attempt}" >&2
    sleep 15
done
