#!/bin/bash
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

set -e

readonly USAGE="
Usage: bash build.sh [-thdDcCrvPSbEm:j:GU]

Options:
    -t run test.
    -d the directory that contains datasystem dir. default: ../
    -D build with debug mode
    -c coverage
    -C clean build environment
    -v the version of yuanrong
    -p the specified version of python runtime
            if it is "multi", will compile python3.9 and python3.11.
            If it is not specified, python3.9 is compiled by default.
            for example bash build.sh -p multi; bash build.sh -p python3.11
    -P download third party packages, default: true
    -S Use Google Sanitizers tools to detect bugs. Choose from off/address/thread,
           if set the value to 'address' enable AddressSanitizer,
           if set the value to 'thread' enable ThreadSanitizer,
           default off.
    -b Use bep
    -E adapter CI java codecheck
    -m mem limit(MB)
    -h usage.
    -j concurrency limit
    -G enable gloo collective operations (default: disabled)
    -U enable UCC collective operations (default: disabled)
"

BASE_DIR=$(
	cd "$(dirname "$0")"
	pwd
)
BUILD_BASE="${BAZEL_OUTPUT_USER_ROOT:-${BASE_DIR}/build}"
OUTPUT_DIR="${BASE_DIR}/output"
OUTPUT_BASE="${BAZEL_OUTPUT_BASE:-${BUILD_BASE}/output}"
BAZEL_COMMAND="build"
BUILD_VERSION="v0.0.1"
BAZEL_OPTIONS="--experimental_cc_shared_library=true --verbose_failures --strategy=CcStrip=standalone --@opentelemetry_cpp//api:with_abseil=true"
BAZEL_OPTIONS_CONFIG=" --config=release "
BAZEL_TARGETS="//api/cpp:yr_cpp_pkg //api/java:yr_java_pkg //api/python:yr_python_pkg //api/go:yr_go_pkg //api/rust:yr_rust_pkg"
SDK_COMMON_BAZEL_TARGETS="//api/cpp:cpp_strip"
SDK_WHEEL_BAZEL_TARGETS="//api/cpp:yr_cpp_pkg //api/python:yr_python_pkg"
BUILD_SDK_COMMON_ONLY="${BUILD_SDK_COMMON_ONLY:-0}"
BUILD_SDK_WHEEL_ONLY="${BUILD_SDK_WHEEL_ONLY:-0}"
LOCAL_DISK_CACHE="${BAZEL_DISK_CACHE:-}"

# On macOS, check for Java availability and adjust targets
if [[ "$(uname)" == "Darwin" ]]; then
	if ! java -version &>/dev/null; then
		echo "Warning: Java not available, skipping Java targets"
		BAZEL_TARGETS="//api/cpp:yr_cpp_pkg //api/python:yr_python_pkg"
	else
		BAZEL_TARGETS="//api/cpp:yr_cpp_pkg //api/java:yr_java_pkg //api/python:yr_python_pkg"
	fi
fi

# rrt-runtime (Rust) now ships as the standalone openyuanrong-rrt wheel, built
# once per arch (see api/python-rrt). Per-cp SDK builds set BUILD_SKIP_RUST=1 so
# rrt is not recompiled once per Python version.
if [ "${BUILD_SKIP_RUST}" = "1" ]; then
	BAZEL_TARGETS="${BAZEL_TARGETS// \/\/api\/rust:yr_rust_pkg/}"
elif ! command -v cargo >/dev/null 2>&1; then
	echo "Warning: cargo not available, skipping //api/rust:yr_rust_pkg"
	BAZEL_TARGETS="${BAZEL_TARGETS// \/\/api\/rust:yr_rust_pkg/}"
fi

# Buildkite SDK jobs use a native cache-prime phase followed by one ABI wheel
# phase per interpreter. The default target set remains unchanged for Jenkins
# and all existing build.sh callers.
if [ "${BUILD_SDK_COMMON_ONLY}" = "1" ]; then
	BAZEL_TARGETS="${SDK_COMMON_BAZEL_TARGETS}"
elif [ "${BUILD_SDK_WHEEL_ONLY}" = "1" ]; then
	BAZEL_TARGETS="${SDK_WHEEL_BAZEL_TARGETS}"
fi

BAZEL_PRE_OPTIONS="--output_user_root=${BUILD_BASE} --output_base=${OUTPUT_BASE}"
THIRD_PARTY_DIR="${BASE_DIR}/thirdparty"
PYTHON3_BIN_PATH="python3"
PYTHON3_SDK_BIN_PATH=$PYTHON3_BIN_PATH
PYTHON_BAZEL_TARGETS="//api/python:yr_python_pkg"
MULTI_PYTHON_VERSION="true"
SANITIZER="off"
BAZEL_OPTIONS_ENV=""
SECBRELLA_CCE="OFF"
PACKAGE_ALL="false"
ENABLE_GLOO="false"
ENABLE_UCC="false"
ENABLE_DATASYSTEM="true"
MACOS_DEPLOYMENT_TARGET=""
LD_LIBRARY_PATH=/opt/buildtools/python3.7/lib:/opt/buildtools/python3.9/lib:/opt/buildtools/python3.11/lib:/opt/buildtools/python3.12/lib:/opt/buildtools/python3.13/lib:/opt/buildtools/python3.14/lib:${LD_LIBRARY_PATH}
BOOST_VERSION="1.87.0"
export BUILD_ALL="false"

function usage() {
	echo -e "$USAGE"
}

log_info() {
	echo "[BUILD_INFO][$(date +%b\ %d\ %H:%M:%S)]$*"
}

log_warning() {
	echo "[BUILD_WARN][$(date +%b\ %d\ %H:%M:%S)]$*"
}

log_error() {
	echo "[BUILD_ERROR][$(date +%b\ %d\ %H:%M:%S)]$*"
}

log_fatal() {
	echo "[BUILD_FATAL][$(date +%b\ %d\ %H:%M:%S)]$*"
	exit 1
}

function ensure_datasystem_submodule() {
	if [[ "${ENABLE_DATASYSTEM}" != "true" || "${BAZEL_COMMAND}" == "clean" || "$(uname)" == "Darwin" ]]; then
		return
	fi

	local required_patch="${BASE_DIR}/datasystem/third_party/patches/spdlog/change-namespace.patch"
	if [[ -f "${required_patch}" ]]; then
		return
	fi

	if [[ -f "${BASE_DIR}/.gitmodules" ]] && command -v git >/dev/null 2>&1 &&
		git -C "${BASE_DIR}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		log_info "ensure datasystem submodule is initialized"
		git -C "${BASE_DIR}" submodule sync -- datasystem
		git -C "${BASE_DIR}" submodule update --init --recursive --force -- datasystem
	fi

	if [[ ! -f "${required_patch}" ]]; then
		log_fatal "datasystem submodule is not initialized: missing ${required_patch}"
	fi
}

function pip_flags_for_python() {
	local py="$1"
	if "${py}" -m pip install --help 2>/dev/null | grep -q -- '--break-system-packages'; then
		echo "--break-system-packages"
	fi
}

function package_python_runtime_abi_extensions() {
    local service_python_dir=$1
    local wheel
    local member
    local pattern
    local exporter
    local metrics_lib_dir
    local target_file

    for wheel in "${OUTPUT_BASE}"/runtime/sdk/python/openyuanrong_sdk-*.whl; do
        [ -f "${wheel}" ] || continue
        for pattern in 'yr/fnruntime*.so' 'yr/cpp/lib/libobservability-*-exporter.so'; do
            while read -r member; do
                [ -n "${member}" ] || continue
                target_file="${service_python_dir}/yr/$(basename "${member}")"
                rm -f "${target_file}"
                unzip -p "${wheel}" "${member}" > "${target_file}"
                if [[ "$(uname)" != "Darwin" ]]; then
                    chrpath -r '$ORIGIN' "${target_file}" 2>/dev/null || true
                fi
                chmod 550 "${target_file}"
            done < <(unzip -Z1 "${wheel}" "${pattern}" 2>/dev/null || true)
        done
    done

    for exporter in \
        libobservability-metrics-file-exporter.so \
        libobservability-prometheus-push-exporter.so \
        libobservability-prometheus-pull-exporter.so \
        libobservability-aom-alarm-exporter.so; do
        target_file="${service_python_dir}/yr/${exporter}"
        [ -f "${target_file}" ] && continue
        for metrics_lib_dir in "${BASE_DIR}/metrics/lib" "${BASE_DIR}/functionsystem/output/metrics/lib"; do
            if [ -f "${metrics_lib_dir}/${exporter}" ]; then
                cp -f "${metrics_lib_dir}/${exporter}" "${target_file}"
                chmod 550 "${target_file}"
                break
            fi
        done
    done
}

function package_java_runtime_launchers() {
    local java_bin_dir="${OUTPUT_BASE}/runtime/service/java/bin"

    mkdir -p "${java_bin_dir}"
    cat > "${java_bin_dir}/java1.8" <<'EOF'
#!/usr/bin/env bash
set -e

if [ -n "${JAVA_HOME:-}" ] && [ -x "${JAVA_HOME}/bin/java" ]; then
    exec "${JAVA_HOME}/bin/java" "$@"
fi

if [ -x /opt/buildtools/jdk8/bin/java ]; then
    exec /opt/buildtools/jdk8/bin/java "$@"
fi

exec java "$@"
EOF
    chmod 550 "${java_bin_dir}/java1.8"
}

MODULE_LIST=(
	"runtime_go"
)

PYTHON_VERSION_LIST=(
	"python3.14"
	"python3.13"
	"python3.12"
	"python3.11"
	"python3.10"
	"python3.9"
)

function go_module_coverage_report() {
	COVERAGE_SUFFIX="_coverage.out"
	[ "${SANITIZER}" != "off" ] && COVERAGE_SUFFIX="_coverage_${SANITIZER}.out"
	MODULE=$1
	MODULE_SOURCE=$(echo "$MODULE" | cut -d '-' -f 1)
	pushd ${GO_SRC_BASE}
	sed -i "/clibruntime.go/d" ${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}${COVERAGE_SUFFIX}
	gocov convert ${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}${COVERAGE_SUFFIX} >${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}_coverage.json
	gocov report ${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}_coverage.json >${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}_coverage.txt
	gocov-html ${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}_coverage.json >${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}_coverage.html
	popd
}

function run_go_coverage_report() {
	for i in "${!MODULE_LIST[@]}"; do
		MODULE_SOURCE=$(echo "${MODULE_LIST[$i]}" | cut -d '-' -f 1)
		go_module_coverage_report ${MODULE_LIST[$i]}
		coverage_info=$(tail -1 ${BASE_DIR}/bazel-bin/go/${MODULE_SOURCE}_coverage.txt)
		((cov_go_total += $(echo $coverage_info | awk -F '[()/]' '{print $(NF-1)}')))
		((cov_go += $(echo $coverage_info | awk -F '[()/]' '{print $(NF-2)}')))
	done
	go_coverage=$(echo "scale=4; $cov_go / $cov_go_total * 100" | bc)
}

function run_java_coverage_report() {
	rm -rf bazel-testlogs/api/java/liblib_yr_api_sdk/
	rm -rf bazel-testlogs/api/java/libfunction_common/
	rm -rf bazel-testlogs/api/java/yr_runtime/
	bazel $BAZEL_PRE_OPTIONS test $BAZEL_OPTIONS //api/java:java_tests
	execs=$(find bazel-out/k8-opt/bin/api/java/ -name "jacoco.exec")
	java -jar build/output/external/jacoco/lib/jacococli.jar merge $execs --destfile bazel-testlogs/api/java/merged.exec
	java -jar build/output/external/jacoco/lib/jacococli.jar report bazel-testlogs/api/java/merged.exec \
		--classfiles bazel-out/k8-opt/bin/api/java/liblib_yr_api_sdk.jar \
		--classfiles bazel-out/k8-opt/bin/api/java/libfunction_common.jar \
		--classfiles bazel-out/k8-opt/bin/api/java/yr_runtime.jar \
		--sourcefiles api/java/yr-api-sdk/src/main/java/ \
		--sourcefiles api/java/function-common/src/main/java/ \
		--sourcefiles api/java/yr-runtime/src/main/java/ \
		--html bazel-testlogs/api/java/html \
		--xml bazel-testlogs/api/java/jacoco-report.xml

	java_total_lines_covered=$(xmllint --xpath 'string(/report/counter[@type="LINE"]/@covered)' bazel-testlogs/api/java/jacoco-report.xml)
	java_total_lines_missed=$(xmllint --xpath 'string(/report/counter[@type="LINE"]/@missed)' bazel-testlogs/api/java/jacoco-report.xml)
	java_total_lines=$((java_total_lines_covered + java_total_lines_missed))
	java_coverage=$(awk "BEGIN {printf \"%.2f\", ($java_total_lines_covered / $java_total_lines) * 100}")
	mkdir -p bazel-testlogs/api/java/liblib_yr_api_sdk/ && unzip bazel-out/k8-opt/bin/api/java/liblib_yr_api_sdk.jar -d bazel-testlogs/api/java/liblib_yr_api_sdk/classes
	mkdir -p bazel-testlogs/api/java/libfunction_common/ && unzip bazel-out/k8-opt/bin/api/java/libfunction_common.jar -d bazel-testlogs/api/java/libfunction_common/classes
	mkdir -p bazel-testlogs/api/java/yr_runtime/ && unzip bazel-out/k8-opt/bin/api/java/yr_runtime.jar -d bazel-testlogs/api/java/yr_runtime/classes
}

function run_python_coverage_report() {
	coverage_files=$(find bazel-out/k8-opt/testlogs/api/python/yr/tests/ -name coverage.dat)
	for i in $coverage_files; do
		MODULE_NAME=$(basename $(dirname $i))
		lcov -q -r $i 'src/*' '*pb2.py' '*tests*' 'apis*' 'cluster_mode*' 'code_manager*' -o ${BASE_DIR}/bazel-bin/api/python/${MODULE_NAME}_coverage.txt
	done

	mkdir -p ${BASE_DIR}/bazel-bin/api/python/coverage_html
	genhtml -q --ignore-errors source ${BASE_DIR}/bazel-bin/api/python/*_coverage.txt -o ${BASE_DIR}/bazel-bin/api/python/coverage_html

	python_coverage_func=$(grep headerCovTableEntryLo bazel-bin/api/python/coverage_html/index.html | awk -F '>' '{print $2}' | awk -F '<' '{print $1}')
	python_coverage=$(grep headerCovTableEntryMed bazel-bin/api/python/coverage_html/index.html | awk -F '>' '{print $2}' | awk -F '<' '{print $1}')
}

function build_python_sdk() {
    if  [ "${BAZEL_COMMAND}" != "build" ];then
        return
    fi
    API_DIR="$BASE_DIR/api"
    cd $API_DIR/python
    local package_total_start
    package_total_start=$(date +%s)
    rm -rf build/ dist/ *.egg-info
    # Ensure packaging is available (setup.py requires it)
    local pip_flag
    local package_timer_start
    pip_flag="$(pip_flags_for_python "${PYTHON3_SDK_BIN_PATH}")"
    package_timer_start=$(date +%s)
    "${PYTHON3_SDK_BIN_PATH}" -m pip install ${pip_flag:+$pip_flag} -q packaging wheel cloudpickle==3.1.2
    echo "[PACKAGE_TIMER] python-sdk-pip-bootstrap python=${PYTHON3_SDK_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"
    # Determine python runtime version for services.yaml
    if [ "$MULTI_PYTHON_VERSION" == "true" ]; then
        PYTHON_RUNTIME_VERSION=python3.11
    else
        PYTHON_RUNTIME_VERSION=$PYTHON3_BIN_PATH
    fi
    mkdir -p ${OUTPUT_DIR}
    mkdir -p $OUTPUT_BASE/runtime/sdk/python/
    local py_ext_suffix
    py_ext_suffix="$("${PYTHON3_BIN_PATH}" - <<'PY'
import sysconfig
print(sysconfig.get_config_var("EXT_SUFFIX") or ".so")
PY
)"
    local bazel_fnruntime="$BASE_DIR/bazel-bin/api/python/yr/fnruntime.so"
    if [ -f "$bazel_fnruntime" ]; then
        rm -f "$API_DIR/python/yr"/fnruntime*.so
        cp -L "$bazel_fnruntime" "$API_DIR/python/yr/fnruntime${py_ext_suffix}"
        if [[ "$(uname)" != "Darwin" ]]; then
            chrpath -r '$ORIGIN' "$API_DIR/python/yr/fnruntime${py_ext_suffix}" 2>/dev/null || true
        fi
        chmod 550 "$API_DIR/python/yr/fnruntime${py_ext_suffix}"
    fi
    package_timer_start=$(date +%s)
    SETUP_TYPE=sdk PYTHON_RUNTIME_VERSION=$PYTHON_RUNTIME_VERSION $PYTHON3_SDK_BIN_PATH setup.py bdist_wheel
    echo "[PACKAGE_TIMER] python-sdk-wheel-build python=${PYTHON3_SDK_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"
    package_timer_start=$(date +%s)
    cp -R $API_DIR/python/dist/*whl $BASE_DIR/output/
    cp -R $API_DIR/python/dist/*whl $OUTPUT_BASE/runtime/sdk/python/
    chmod 750 $BASE_DIR/output/*.whl
    echo "[PACKAGE_TIMER] python-sdk-wheel-publish python=${PYTHON3_SDK_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"
    package_timer_start=$(date +%s)
    rm -f "$OUTPUT_BASE/runtime/service/python/yr"/fnruntime*.so
    if [ -e "${OUTPUT_BASE}"/runtime/service/python/yr ]; then
        cp -Rf $API_DIR/python/yr/* $OUTPUT_BASE/runtime/service/python/yr
        rm -rf $OUTPUT_BASE/runtime/service/python/yr/tests
    else
        mkdir -p $OUTPUT_BASE/runtime/service/python/yr
        cp -R $API_DIR/python/yr/* $OUTPUT_BASE/runtime/service/python/yr
        rm -rf $OUTPUT_BASE/runtime/service/python/yr/tests
    fi
    rm -f $OUTPUT_BASE/runtime/service/python/yr/fnruntime.pyx
    rm -rf $OUTPUT_BASE/runtime/service/python/yr/runtime
    if [ -f "$bazel_fnruntime" ]; then
        rm -f "$OUTPUT_BASE/runtime/service/python/yr"/fnruntime*.so
        cp -L "$bazel_fnruntime" "$OUTPUT_BASE/runtime/service/python/yr/fnruntime${py_ext_suffix}"
        if [[ "$(uname)" != "Darwin" ]]; then
            chrpath -r '$ORIGIN' "$OUTPUT_BASE/runtime/service/python/yr/fnruntime${py_ext_suffix}" 2>/dev/null || true
        fi
        chmod 550 "$OUTPUT_BASE/runtime/service/python/yr/fnruntime${py_ext_suffix}"
    fi
    echo "[PACKAGE_TIMER] python-runtime-service-assembly python=${PYTHON3_SDK_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"
    package_timer_start=$(date +%s)
    package_python_runtime_abi_extensions "$OUTPUT_BASE/runtime/service/python"
    echo "[PACKAGE_TIMER] python-runtime-abi-extensions python=${PYTHON3_SDK_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"
    package_timer_start=$(date +%s)
    package_java_runtime_launchers
    echo "[PACKAGE_TIMER] python-java-runtime-launchers python=${PYTHON3_SDK_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"
    echo "[PACKAGE_TIMER] python-sdk-assembly-total python=${PYTHON3_SDK_BIN_PATH} elapsed=$(($(date +%s)-package_total_start))s"
}

function stage_python_bazel_outputs() {
    if [ "${BAZEL_COMMAND}" != "build" ]; then
        return
    fi

    local generated_proto="${BASE_DIR}/bazel-bin/api/python/yr/libruntime_pb2.py"
    local workspace_proto="${BASE_DIR}/api/python/yr/libruntime_pb2.py"
    if [ ! -f "${generated_proto}" ]; then
        log_fatal "generated Python runtime proto not found: ${generated_proto}"
    fi
    mkdir -p "$(dirname "${workspace_proto}")"
    cp -f "${generated_proto}" "${workspace_proto}"
    chmod 0644 "${workspace_proto}"
}

function install_python_requirements() {
	local pip_flag
	pip_flag="$(pip_flags_for_python "${PYTHON3_BIN_PATH}")"
	"${PYTHON3_BIN_PATH}" -m pip install ${pip_flag:+$pip_flag} pytest coverage
	"${PYTHON3_BIN_PATH}" -m pip install ${pip_flag:+$pip_flag} -r api/python/requirements.txt
	"${PYTHON3_BIN_PATH}" -m pip install ${pip_flag:+$pip_flag} numpy
	"${PYTHON3_BIN_PATH}" -m pip install ${pip_flag:+$pip_flag} fastapi
}

function check_sanitizers() {
	local name
	name="$1"
	if [[ "X$name" != "Xaddress" && "X$name" != "Xthread" && "X$name" != "Xoff" ]]; then
		log_error "Invalid value $1 for option -S"
		usage
		exit 1
	fi
	if [[ "X$name" == "Xthread" ]]; then
		BAZEL_OPTIONS_ENV="--action_env=TSAN_OPTIONS=suppressions=${BASE_DIR}/test/cpp_tsan.supp"
	fi
}

while getopts 'athr:l:v:S:DcCgPET:p:B:m:j:gGU' opt; do
	case "$opt" in
	a)
		BUILD_ALL="true"
		;;
	t)
		BAZEL_COMMAND="test"
		BAZEL_TARGETS="//test/... //api/python/yr/tests/... //api/java:java_tests"
		;;
	T)
		BAZEL_OPTIONS="$BAZEL_OPTIONS --test_arg=${OPTARG}"
		;;
	h)
		usage
		exit 0
		;;
	r)
		REMOTE_CACHE="${OPTARG}"
		;;
	l)
		LOCAL_DISK_CACHE="${OPTARG}"
		;;
	v)
		BUILD_VERSION="${OPTARG}"
		export BUILD_VERSION="${OPTARG}"
		;;
	D)
		BAZEL_OPTIONS_CONFIG=" --config=debug "
		;;
	m)
		BAZEL_OPTIONS_ENV="$BAZEL_OPTIONS_ENV --local_ram_resources=${OPTARG} "
		;;
	j)
		BAZEL_OPTIONS_ENV="$BAZEL_OPTIONS_ENV --jobs=${OPTARG} "
		;;
	c)
		BAZEL_COMMAND="coverage"
		BAZEL_TARGETS="//api/go:yr_go_test //test/... //api/python/yr/tests/..."
		BAZEL_OPTIONS="$BAZEL_OPTIONS --combined_report=lcov --nocache_test_results --instrumentation_filter=^//.*[/:] --test_tag_filters=-cgo"
		install_python_requirements
		;;
	C)
		BAZEL_COMMAND="clean"
		BAZEL_OPTIONS="$BAZEL_OPTIONS --expunge"
		BAZEL_TARGETS=""
		;;
	S)
		check_sanitizers "${OPTARG}"
		SANITIZER="${OPTARG}"
		if [[ "${OPTARG}" != "off" ]]; then
			BAZEL_OPTIONS_CONFIG=" --config=sanitize_${SANITIZER} "
		fi
		;;
	P)
		PACKAGE_ALL="true"
		;;
	p)
		if [[ "${OPTARG}" == "multi" ]]; then
			MULTI_PYTHON_VERSION="true"
		else
			MULTI_PYTHON_VERSION="false"
			PYTHON3_BIN_PATH="${OPTARG}"
		fi
		;;
	E)
		SECBRELLA_CCE="ON"
		BAZEL_OPTIONS_ENV="${BAZEL_OPTIONS_ENV} --action_env=SECBRELLA_CCE_LD_PRELOAD=${SECBRELLA_CCE_LD_PRELOAD}"
		BAZEL_OPTIONS="${BAZEL_OPTIONS} --sandbox_debug"
		;;
	B)
		BOOST_VERSION="${OPTARG}"
		;;
	g)
		BAZEL_TARGETS="//api/python:cp_yr_proto //src/proto:libruntime_cc_proto //src/proto:libruntime_java_proto //src/proto:socket_cc_proto //src/proto:socket_java_proto"
		;;
	G)
		ENABLE_GLOO="true"
		;;
	U)
		ENABLE_UCC="true"
		;;
	*)
		log_error "invalid command: $opt"
		usage
		exit 1
		;;
	esac
done

if [ "${BUILD_SDK_COMMON_ONLY}" = "1" ] && [ "${BUILD_SDK_WHEEL_ONLY}" = "1" ]; then
	log_fatal "BUILD_SDK_COMMON_ONLY and BUILD_SDK_WHEEL_ONLY are mutually exclusive"
fi
if [ "${BUILD_SDK_COMMON_ONLY}" = "1" ] && [ "${PACKAGE_ALL}" = "true" ]; then
	log_fatal "BUILD_SDK_COMMON_ONLY cannot be combined with -P"
fi

# Support remote cache via environment variable REMOTE_CACHE
# Command-line -r takes precedence; falls back to env var
if [ -n "${REMOTE_CACHE}" ]; then
	cache_addr="${REMOTE_CACHE}"
	# Extract host and port from various formats like:
	#   http://host:port, https://host:port, grpc://host:port, host:port
	host=$(echo "${cache_addr}" | sed -E 's|grpc://||; s|http://||; s|https://||; s|/.*||' | cut -d: -f1)
	port=$(echo "${cache_addr}" | sed -E 's|grpc://||; s|http://||; s|https://||; s|/.*||' | cut -s -d: -f2)

	# Set default port based on protocol
	if [ -z "$port" ]; then
		if echo "${cache_addr}" | grep -q "^https://"; then
			port=443
		elif echo "${cache_addr}" | grep -q "^grpc://"; then
			port=443
		else
			port=80
		fi
	fi

	# Check if port is reachable using multiple methods
	port_reachable=false
	if timeout 3 bash -c "exec 3<>/dev/tcp/${host}/${port}" 2>/dev/null; then
		port_reachable=true
	elif command -v nc &>/dev/null && nc -z -w 3 "${host}" "${port}" 2>/dev/null; then
		port_reachable=true
	elif curl --connect-timeout 3 --max-time 5 "${cache_addr}" &>/dev/null; then
		port_reachable=true
	fi

	if [ "$port_reachable" = true ]; then
		log_info "use remote cache server: ${cache_addr} (host: ${host}, port: ${port})"
		BAZEL_OPTIONS="$BAZEL_OPTIONS --remote_cache=${cache_addr}"
	else
		log_warning "no remote cache server available at ${host}:${port}"
	fi
fi

if [ -n "${BAZEL_REPOSITORY_CACHE:-}" ]; then
	mkdir -p "${BAZEL_REPOSITORY_CACHE}"
	BAZEL_OPTIONS="$BAZEL_OPTIONS --repository_cache=${BAZEL_REPOSITORY_CACHE}"
fi

if [ -n "${LOCAL_DISK_CACHE}" ]; then
	mkdir -p "${LOCAL_DISK_CACHE}"
	BAZEL_OPTIONS="$BAZEL_OPTIONS --disk_cache=${LOCAL_DISK_CACHE}"
fi

if [ -n "${BAZEL_TARGETS_OVERRIDE:-}" ]; then
	if [ "${BAZEL_COMMAND}" != "test" ]; then
		log_fatal "BAZEL_TARGETS_OVERRIDE is only valid with -t"
	fi
	BAZEL_TARGETS="${BAZEL_TARGETS_OVERRIDE}"
	log_info "use explicit test targets: ${BAZEL_TARGETS}"
fi

# Selective targets are an opt-in local workflow. The ordinary `build.sh -t`
# target set and its dependency installation remain unchanged for CI.
LOCAL_SELECTIVE_TESTS=0
if [ -n "${BAZEL_TARGETS_OVERRIDE:-}" ] && [ -n "${YR_LOCAL_CACHE_ROOT:-}" ]; then
	LOCAL_SELECTIVE_TESTS=1
fi

if [ "${BAZEL_COMMAND}" = "test" ] && [ "${YR_SKIP_PYTHON_REQUIREMENTS:-0}" != "1" ]; then
	install_python_requirements
fi

if [ "$BAZEL_COMMAND" != "clean" ] && [ "${SKIP_RUNTIME_DEPENDENCY_DOWNLOAD:-0}" != "1" ]; then
	bash ${BASE_DIR}/tools/download_dependency.sh
fi

API_DIR="${BASE_DIR}/api"
# Use sed with cross-platform compatibility (macOS requires -i '')
if [[ "$(uname)" == "Darwin" ]]; then
	sed -i '' "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/pom.xml
	sed -i '' "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/yr-api-sdk/pom.xml
	sed -i '' "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/function-common/pom.xml
	sed -i '' "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/yr-runtime/pom.xml
else
	sed -i "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/pom.xml
	sed -i "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/yr-api-sdk/pom.xml
	sed -i "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/function-common/pom.xml
	sed -i "s/<version>1.0.0<\/version>/<version>${BUILD_VERSION}<\/version>/" $API_DIR/java/yr-runtime/pom.xml
fi

PYTHON_BIN_FULL_PATH="$(command -v "${PYTHON3_BIN_PATH}" 2>/dev/null || true)"
if [[ -z "${PYTHON_BIN_FULL_PATH}" ]]; then
	log_fatal "python not found: ${PYTHON3_BIN_PATH}"
fi

if [[ "$(uname)" == "Darwin" ]]; then
	ENABLE_DATASYSTEM="false"
	ENABLE_GLOO="false"
	if [[ "$(uname -m)" == "arm64" ]]; then
		MACOS_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-11.0}"
		export MACOSX_DEPLOYMENT_TARGET="${MACOS_DEPLOYMENT_TARGET}"
		BAZEL_OPTIONS="${BAZEL_OPTIONS} --macos_minimum_os=${MACOS_DEPLOYMENT_TARGET}"
	fi
fi

	ensure_datasystem_submodule

# Python is intentionally not an action_env: making it global invalidates
# Python-independent C++/proto/runtime actions for every CPython ABI. The
# repository environment still selects matching headers for fnruntime only.
# - repo_env: for @local_config_python (python headers + libs) to match the selected interpreter
export GOPATH="$(go env GOPATH)"
export GOMODCACHE="$(go env GOMODCACHE)"
export GOENV="$(go env GOENV)"
export GOFLAGS="$(go env GOFLAGS)"
export GOEXPERIMENT="$(go env GOEXPERIMENT)"
export GOCACHE="$(go env GOCACHE)"
	# Keep release metadata in a scoped Make variable. Only Bazel rules that
	# expand $(BUILD_VERSION) should vary across release tags.
	BAZEL_BUILD_VERSION="${BAZEL_BUILD_VERSION:-${BUILD_VERSION}}"
	BAZEL_OPTIONS="${BAZEL_OPTIONS} --define=BUILD_VERSION=${BAZEL_BUILD_VERSION}"
	BAZEL_OPTIONS_ENV="${BAZEL_OPTIONS_ENV} --action_env=BOOST_VERSION=$BOOST_VERSION --action_env=GOPATH --action_env=GOMODCACHE --action_env=GOENV --action_env=GOFLAGS --action_env=GOEXPERIMENT --action_env=GOCACHE --action_env=CARGO_HOME --action_env=CARGO_TARGET_DIR --repo_env=PYTHON3_BIN_PATH=${PYTHON_BIN_FULL_PATH} --define ENABLE_GLOO=${ENABLE_GLOO} --define ENABLE_DATASYSTEM=${ENABLE_DATASYSTEM}"
if [[ -n "${MACOS_DEPLOYMENT_TARGET}" ]]; then
	BAZEL_OPTIONS_ENV="${BAZEL_OPTIONS_ENV} --action_env=MACOSX_DEPLOYMENT_TARGET=${MACOS_DEPLOYMENT_TARGET} --repo_env=MACOSX_DEPLOYMENT_TARGET=${MACOS_DEPLOYMENT_TARGET}"
fi
# Forward sccache into the //api/rust cargo genrule only when a wrapper was
# provisioned (see .buildkite/setup_sccache.sh). RUSTC_WRAPPER is an absolute
# path so the genrule needs no PATH change; SCCACHE_DIR is the hostPath cache.
if [[ -n "${RUSTC_WRAPPER:-}" ]]; then
	BAZEL_OPTIONS_ENV="${BAZEL_OPTIONS_ENV} --action_env=RUSTC_WRAPPER --action_env=SCCACHE_DIR --action_env=SCCACHE_CACHE_SIZE --action_env=SCCACHE_IDLE_TIMEOUT --action_env=CARGO_INCREMENTAL"
fi
BAZEL_OPTIONS="${BAZEL_OPTIONS} ${BAZEL_OPTIONS_CONFIG} ${BAZEL_OPTIONS_ENV}"

if [ "${BAZEL_COMMAND}" = "test" ] && [ "${LOCAL_SELECTIVE_TESTS}" = "1" ]; then
	TEST_LIBRARY_PATHS=(
		"${BASE_DIR}/functionsystem/output/functionsystem/lib"
		"${BASE_DIR}/functionsystem/output/wheel/build/lib/yr/functionsystem/lib"
		"${BASE_DIR}/metrics/lib"
	)
	TEST_LIBRARY_PATH=""
	for test_library_path in "${TEST_LIBRARY_PATHS[@]}"; do
		if [ -d "${test_library_path}" ]; then
			TEST_LIBRARY_PATH="${TEST_LIBRARY_PATH:+${TEST_LIBRARY_PATH}:}${test_library_path}"
		fi
	done
	if [ -n "${TEST_LIBRARY_PATH}" ]; then
		BAZEL_OPTIONS="${BAZEL_OPTIONS} --test_env=LD_LIBRARY_PATH=${TEST_LIBRARY_PATH}"
	fi
fi

cd $BASE_DIR
package_timer_start=$(date +%s)
bazel ${BAZEL_PRE_OPTIONS} ${BAZEL_COMMAND} ${BAZEL_OPTIONS} -- ${BAZEL_TARGETS}
echo "[PACKAGE_TIMER] bazel-build python=${PYTHON3_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"

PYTHON3_SDK_BIN_PATH=$PYTHON3_BIN_PATH
if [ "${BUILD_SDK_COMMON_ONLY}" != "1" ] && {
	[ "${BAZEL_COMMAND}" != "test" ] || [ "${LOCAL_SELECTIVE_TESTS}" != "1" ] || \
	[ "${YR_BUILD_PYTHON_SDK_FOR_TEST:-0}" = "1" ];
}; then
	stage_python_bazel_outputs
	build_python_sdk
fi

if [ "$BAZEL_COMMAND" == "coverage" ]; then
	lcov -q -r ${BASE_DIR}/bazel-out/_coverage/_coverage_report.dat '*python*' '*.pb.*' '*test*' -o ${BASE_DIR}/bazel-out/_coverage/_coverage_report.info
	genhtml -q --ignore-errors source --output genhtml ${BASE_DIR}/bazel-out/_coverage/_coverage_report.info
	cpp_coverage=$(grep headerCovTableEntryMed genhtml/index.html | head -n 1 | awk -F '>' '{print $2}' | awk -F '<' '{print $1}')
	run_go_coverage_report
	run_java_coverage_report
	run_python_coverage_report
	echo "cpp_covearge: $cpp_coverage" >>genhtml/coverage.txt
	echo "python_covearge: $python_coverage" >>genhtml/coverage.txt
	echo "java_coverage: $java_coverage%" >>genhtml/coverage.txt
	echo "go_coverage: $go_coverage%" >>genhtml/coverage.txt
	cat genhtml/coverage.txt
fi

if [ "$BAZEL_COMMAND" == "clean" ]; then
	[[ -n "${BUILD_BASE}" ]] && rm -rf "${BUILD_BASE}"
	[[ -n "${BASE_DIR}/api/python/dist/" ]] && rm -rf "${BASE_DIR}/api/python/dist/"
	rm -rf "${BASE_DIR}/api/rust/target"
fi

if [ "$BAZEL_COMMAND" == "build" ] && [ "${BUILD_SDK_COMMON_ONLY}" != "1" ] && [ "${BUILD_SDK_WHEEL_ONLY}" != "1" ]; then
    mkdir -p ${OUTPUT_DIR}
    runtime_tree_kib=$(du -sk "${OUTPUT_BASE}/runtime" | awk '{print $1}')
    echo "[PACKAGE_SIZE] runtime-tree python=${PYTHON3_BIN_PATH} kib=${runtime_tree_kib}"
    package_timer_start=$(date +%s)
    tar -czf ${OUTPUT_DIR}/yr-runtime-${BUILD_VERSION}.tar.gz -C ${OUTPUT_BASE} runtime
    echo "[PACKAGE_TIMER] runtime-tar python=${PYTHON3_BIN_PATH} elapsed=$(($(date +%s)-package_timer_start))s"
    if [ -d "${OUTPUT_BASE}/symbols" ] && [ "$(ls -A ${OUTPUT_BASE}/symbols 2>/dev/null)" ]; then
        tar -czf ${OUTPUT_DIR}/symbols_libruntime.tar.gz -C ${OUTPUT_BASE} symbols
    fi
fi

if [ "$PACKAGE_ALL" == "true" ]; then
    if [ "$MULTI_PYTHON_VERSION" == "true" ]; then
        PACKAGE_PYTHON_VERSION=python3.11
    else
        PACKAGE_PYTHON_VERSION=$PYTHON3_BIN_PATH
    fi

    start=$(date +%s)
    bash ${BASE_DIR}/scripts/package_yuanrong.sh -v ${BUILD_VERSION}
    end1=$(date +%s)
    echo "Package openyuanrong.tar.gz elapsed: $((end1 - start)) seconds"

    bash "$BASE_DIR/scripts/package_python_wheels.sh" \
        "$API_DIR" "$BASE_DIR/output" "$PYTHON3_SDK_BIN_PATH" "$PACKAGE_PYTHON_VERSION"
    end2=$(date +%s)
    echo "Package openyuanrong.whl elapsed: $((end2 - end1)) seconds"
    chmod 750 $BASE_DIR/output/*.whl
fi
cd -
