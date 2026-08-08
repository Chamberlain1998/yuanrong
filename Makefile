.PHONY: help frontend datasystem functionsystem runtime_launcher runtime runtime-ut yuanrong dashboard rust rust-ut sandbox-sdk pkg aio image all clean local-init local-release local-ut

# Bazel remote cache server (optional, can be set via environment variable)
# Example: REMOTE_CACHE=https://192.0.2.1:9090 make yuanrong
REMOTE_CACHE ?=
NPROCS := $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
JOBS ?= $(NPROCS)
FUNCTIONSYSTEM_JOBS ?= 8
DATASYSTEM_PYTHON ?= on
DATASYSTEM_JAVA ?= on
BUILD_VERSION ?=
BUILD_VERSION_ARG := $(if $(BUILD_VERSION),-v $(BUILD_VERSION),)
LOCAL_CACHE_ROOT ?=
LOCAL_CACHE_PROFILE ?=
LOCAL_BUILD_PROTOCOL ?= 2
FUNCTIONSYSTEM_BUILDER ?=
LOCAL_CACHE_RUN :=
FUNCTIONSYSTEM_BUILDER_ARGS :=
LOCAL_DATASYSTEM_BASELINE_ARCHIVE ?=
LOCAL_DATASYSTEM_BASELINE_VERSION ?=
DATASYSTEM_NINJA ?= off
DATASYSTEM_BUILD_DIR ?=
DATASYSTEM_BUILD_DIR_ARG := $(if $(strip $(DATASYSTEM_BUILD_DIR)),-B "$(DATASYSTEM_BUILD_DIR)",)

ifneq ($(strip $(LOCAL_CACHE_ROOT)$(LOCAL_CACHE_PROFILE)),)
ifeq ($(strip $(LOCAL_CACHE_ROOT)),)
$(error LOCAL_CACHE_ROOT is required when LOCAL_CACHE_PROFILE is set)
endif
ifeq ($(strip $(LOCAL_CACHE_PROFILE)),)
$(error LOCAL_CACHE_PROFILE is required when LOCAL_CACHE_ROOT is set)
endif
ifneq ($(LOCAL_CACHE_PROFILE),release)
ifneq ($(LOCAL_CACHE_PROFILE),ut)
$(error LOCAL_CACHE_PROFILE must be release or ut)
endif
endif
LOCAL_CACHE_RUN := bash "$(CURDIR)/scripts/with_local_build_cache.sh" --root "$(abspath $(LOCAL_CACHE_ROOT))" --profile "$(LOCAL_CACHE_PROFILE)" --
endif

ifeq ($(strip $(FUNCTIONSYSTEM_BUILDER)),)
ifeq ($(strip $(LOCAL_CACHE_ROOT)),)
FUNCTIONSYSTEM_BUILDER := cmake
else
FUNCTIONSYSTEM_BUILDER := bazel
endif
endif

ifeq ($(FUNCTIONSYSTEM_BUILDER),bazel)
FUNCTIONSYSTEM_BUILDER_ARGS := --builder bazel
ifneq ($(strip $(LOCAL_CACHE_ROOT)),)
FUNCTIONSYSTEM_BUILDER_ARGS += --bazel_local_cache_root "$(abspath $(LOCAL_CACHE_ROOT))" --bazel_cache_profile "$(LOCAL_CACHE_PROFILE)"
endif
else ifneq ($(FUNCTIONSYSTEM_BUILDER),cmake)
$(error FUNCTIONSYSTEM_BUILDER must be cmake or bazel)
endif

LOCAL_CACHE_ENABLED := 0
ifneq ($(strip $(LOCAL_CACHE_ROOT)),)
LOCAL_CACHE_ENABLED := 1
endif

help:
	@echo "Available targets:"
	@echo "  make clean          - Clean build outputs"
	@echo "  make frontend        - Build frontend (auto-fixes go.mod path)"
	@echo "  make datasystem     - Build datasystem"
	@echo "  make functionsystem - Build functionsystem"
	@echo "  make runtime_launcher - Build runtime-launcher"
	@echo "  make yuanrong       - Build runtime"
	@echo "  make dashboard      - Build dashboard"
	@echo "  make rust           - Build rrt-runtime"
	@echo "  make rust-ut        - Run rrt-runtime Rust tests"
	@echo "  make runtime-ut     - Run YuanRong runtime tests"
	@echo "  make sandbox-sdk    - Build openyuanrong-sandbox wheel"
	@echo "  make pkg            - Copy packages to example/aio/pkg/"
	@echo "  make aio            - Copy packages and build AIO image"
	@echo "  make image         - Build aio images after make all"
	@echo "  make all           - Build all targets"
	@echo ""
	@echo "Parameters (optional):"
	@echo "  REMOTE_CACHE       - Remote cache server address"
	@echo "                      Example: make yuanrong REMOTE_CACHE=grpc://192.0.2.1:9092"
	@echo "                      If not provided, build will proceed without remote cache"
	@echo "  JOBS               - Default parallelism for datasystem and runtime builds"
	@echo "                      Example: make all JOBS=8"
	@echo "  FUNCTIONSYSTEM_JOBS - Parallelism for functionsystem; defaults to 8"
	@echo "                      Example: make all FUNCTIONSYSTEM_JOBS=6"
	@echo "  DATASYSTEM_JAVA    - Build datasystem Java SDK jar; defaults to on"
	@echo "                      Example: make datasystem DATASYSTEM_JAVA=off"
	@echo "  LOCAL_CACHE_ROOT   - Enable the opt-in local developer cache"
	@echo "  LOCAL_CACHE_PROFILE - Cache profile: release or ut (required with root)"
	@echo "                      Example: make yuanrong LOCAL_CACHE_ROOT=.yr-cache/local LOCAL_CACHE_PROFILE=release"
	@echo "  FUNCTIONSYSTEM_BUILDER - functionsystem builder: cmake by default, bazel with local cache"
	@echo "  LOCAL_DATASYSTEM_BASELINE_ARCHIVE - approved prebuilt DataSystem archive for local builds"
	@echo "  LOCAL_DATASYSTEM_BASELINE_VERSION - version of the approved DataSystem archive"

clean:
	@echo "Cleaning build outputs..."
	@bash frontend/build/clean.sh 2>/dev/null || true
	@cd datasystem && bash build.sh clean 2>/dev/null || true && cd ..
	@rm -rf functionsystem/functionsystem/build
	@rm -rf functionsystem/functionsystem/output
	@rm -rf functionsystem/common/logs/build
	@rm -rf functionsystem/common/logs/output
	@rm -rf functionsystem/common/litebus/build
	@rm -rf functionsystem/common/litebus/output
	@rm -rf functionsystem/common/metrics/build
	@rm -rf functionsystem/common/metrics/output
	@rm -rf functionsystem/vendor/build
	@rm -rf functionsystem/vendor/output
	@rm -rf functionsystem/vendor/src/etcd/bin
	@rm -rf functionsystem/output
	@rm -rf go/output
	@bash build.sh -C 2>/dev/null || true
	@rm -rf output/
	@rm -f functionsystem/vendor/src/yr-datasystem.tar.gz
	@echo "Clean completed!"

frontend:
	@if [ -f "frontend/go.mod" ]; then \
		if grep -q 'yuanrong.org/kernel/runtime.*=>.*\.\./yuanrong/api/go' "frontend/go.mod"; then \
			sed -i 's|yuanrong.org/kernel/runtime.*=>.*\.\./yuanrong/api/go|yuanrong.org/kernel/runtime => ../api/go|g' "frontend/go.mod"; \
			echo "Updated frontend/go.mod: yuanrong.org/kernel/runtime => ../api/go"; \
		else \
			echo "frontend/go.mod already correct"; \
		fi \
	else \
		echo "Warning: frontend/go.mod not found, skipping mod fix"; \
	fi
	@if [ -f "frontend/build.sh" ]; then \
		$(LOCAL_CACHE_RUN) bash frontend/build.sh; \
	else \
		echo "Error: frontend/build.sh not found!"; \
		exit 1; \
	fi
	@mkdir -p output
	@cp frontend/output/yr-frontend*.tar.gz output/ 2>/dev/null || true

datasystem:
ifeq ($(LOCAL_CACHE_ENABLED),1)
ifneq ($(strip $(LOCAL_DATASYSTEM_BASELINE_ARCHIVE)),)
		@test -f "$(LOCAL_DATASYSTEM_BASELINE_ARCHIVE)"
		@test -n "$(LOCAL_DATASYSTEM_BASELINE_VERSION)"
		@rm -rf datasystem/output
		@mkdir -p datasystem/output
		@cp "$(LOCAL_DATASYSTEM_BASELINE_ARCHIVE)" "datasystem/output/yr-datasystem-v$(LOCAL_DATASYSTEM_BASELINE_VERSION).tar.gz"
		@tar --no-same-owner -zxf "datasystem/output/yr-datasystem-v$(LOCAL_DATASYSTEM_BASELINE_VERSION).tar.gz" --strip-components=1 -C datasystem/output

else
		@rm -rf datasystem/output/*
		$(LOCAL_CACHE_RUN) bash datasystem/build.sh -X off -P $(DATASYSTEM_PYTHON) -J $(DATASYSTEM_JAVA) -G on -i on -n $(DATASYSTEM_NINJA) $(DATASYSTEM_BUILD_DIR_ARG) -j $(JOBS) $(BUILD_VERSION_ARG)

endif
else
		@rm -rf datasystem/output/*
		bash datasystem/build.sh -X off -P $(DATASYSTEM_PYTHON) -J $(DATASYSTEM_JAVA) -G on -i on -j $(JOBS) $(BUILD_VERSION_ARG)
endif
	@mkdir -p output
	@cp datasystem/output/yr-datasystem-*.tar.gz output/
	@mkdir -p functionsystem/vendor/src
	@cp datasystem/output/yr-datasystem-*.tar.gz functionsystem/vendor/src/yr-datasystem.tar.gz
	@if [ ! -d datasystem/output/sdk ]; then tar --no-same-owner -zxf datasystem/output/yr-datasystem-*.tar.gz --strip-components=1 -C datasystem/output; fi
	@cp datasystem/output/*.whl output/ 2>/dev/null || true
	@true

runtime_launcher:
	@echo "Building runtime-launcher..."
	cd functionsystem && $(LOCAL_CACHE_RUN) bash run.sh build --component runtime_launcher $(BUILD_VERSION_ARG) && cd -
	@mkdir -p output
	@cp functionsystem/runtime-launcher/bin/runtime/runtime-launcher output/runtime-launcher
	@echo "Runtime-launcher built successfully!"

rust:
	@echo "Building rrt-runtime (Rust sandbox runtime)..."
	$(LOCAL_CACHE_RUN) bash scripts/build_rrt_runtime.sh

rust-ut:
	@echo "Running rrt-runtime Rust tests..."
	$(LOCAL_CACHE_RUN) cargo test --manifest-path api/rust/Cargo.toml -p rrt-daemon

functionsystem:
	cd functionsystem && $(LOCAL_CACHE_RUN) bash run.sh build -j $(FUNCTIONSYSTEM_JOBS) $(FUNCTIONSYSTEM_BUILDER_ARGS) $(BUILD_VERSION_ARG) && bash run.sh pack $(BUILD_VERSION_ARG) && cd -
	mkdir -p output
	cp -ar functionsystem/output/metrics ./
	cp functionsystem/output/yr-functionsystem*.tar.gz output/
	cp functionsystem/output/*.whl output/
	cp functionsystem/runtime-launcher/bin/runtime/runtime-launcher output/ 2>/dev/null || true

dashboard:
	cd go && $(LOCAL_CACHE_RUN) bash build.sh && cd -
	mkdir -p output
	cp go/output/yr-dashboard*.tar.gz output/
	cp go/output/yr-faas*.tar.gz output/

runtime:
	@echo "Building yuanrong runtime..."
	$(LOCAL_CACHE_RUN) bash build.sh -j $(JOBS) $(BUILD_VERSION_ARG)

runtime-ut:
	@echo "Running yuanrong runtime tests..."
	$(LOCAL_CACHE_RUN) bash build.sh -t -j $(JOBS) $(BUILD_VERSION_ARG)

yuanrong:
	@echo "Building yuanrong..."
	$(LOCAL_CACHE_RUN) bash build.sh -P -j $(JOBS) $(BUILD_VERSION_ARG)

image:
	@echo "Building aio images via deploy/sandbox/docker/build-images.sh..."
	@./deploy/sandbox/docker/build-images.sh

sandbox-sdk:
	@echo "Building openyuanrong-sandbox (sandbox-sdk) wheel..."
	@if [ ! -f sandbox-sdk/build.sh ]; then \
		echo "sandbox-sdk submodule not initialized; run: git submodule update --init sandbox-sdk"; \
		exit 1; \
	fi
	@mkdir -p output
	@$(LOCAL_CACHE_RUN) bash sandbox-sdk/build.sh "$(CURDIR)/output"

pkg:
	@echo "Copying packages to example/aio/pkg/..."
	@mkdir -p example/aio/pkg
	@cp datasystem/output/sdk/openyuanrong_datasystem_sdk-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp datasystem/output/openyuanrong_datasystem-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp functionsystem/output/openyuanrong_functionsystem-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp output/openyuanrong-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp output/openyuanrong_sdk-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp output/openyuanrong_runtime-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp output/openyuanrong_dashboard-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp output/openyuanrong_faas-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp output/openyuanrong_cpp_sdk-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp output/openyuanrong_full-*.whl example/aio/pkg/ 2>/dev/null || true
	@cp functionsystem/runtime-launcher/bin/runtime/runtime-launcher example/aio/pkg/runtime-launcher 2>/dev/null || true
	@mkdir -p example/aio/docs
	@cp example/aio/TRAEFIK_ETCD.md example/aio/docs/ 2>/dev/null || true
	@echo "Packages copied successfully!"
	@ls -la example/aio/pkg/

aio: pkg
	@echo "Building Docker image openyuanrongaio:latest..."
	@cd example/aio && docker build -t openyuanrongaio:latest -f Dockerfile . && cd - || (cd -; exit 1)

all: frontend datasystem functionsystem dashboard yuanrong sandbox-sdk
	@echo "Build completed!"
	@echo "Artifacts are ready under output/."

# Define dependencies for parallel make
functionsystem: datasystem
yuanrong: datasystem

local-init:
	@bash scripts/local-build.sh init

local-release:
	@bash scripts/local-build.sh release

local-ut:
	@bash scripts/local-build.sh ut
