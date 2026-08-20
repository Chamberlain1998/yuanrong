#!/usr/bin/env python3
# coding=UTF-8
#
# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
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

import gc
import logging
import os
import time

import yr
from yr.err_type import ErrorCode, ModuleCode

LOGGER = logging.getLogger(__name__)
NO_DS_ERROR = "DataSystem is disabled in this cluster"
DS_API_CONTRACT_MODE = "ds-api-contract"


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_optional_bool(name: str):
    value = os.getenv(name)
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on"}


def require_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected {expected}, got {actual}")


def expect_no_datasystem_error(case_name: str, action) -> None:
    started = time.monotonic()
    try:
        action()
    except RuntimeError as error:
        elapsed = time.monotonic() - started
        message = str(error)
        expected = [NO_DS_ERROR, case_name]
        missing = [part for part in expected if part not in message]
        if (
            missing
            or getattr(error, "code", None) != ErrorCode.ERR_DATASYSTEM_FAILED
            or getattr(error, "module_code", None) != ModuleCode.DATASYSTEM
        ):
            raise AssertionError(
                f"{case_name} returned an unexpected error; missing={missing}, "
                f"code={getattr(error, 'code', None)!r}, "
                f"module={getattr(error, 'module_code', None)!r}, message={message!r}"
            ) from error
        if elapsed > 2:
            raise AssertionError(f"{case_name} did not fail fast: elapsed={elapsed:.3f}s") from error
        LOGGER.info("SMOKE no-ds %s failed as expected in %.3fs", case_name, elapsed)
        return
    raise AssertionError(f"{case_name} unexpectedly succeeded while DataSystem is disabled")


def make_get_params():
    get_params = yr.GetParams()
    get_params.get_params = [yr.GetParam()]
    return get_params


def run_datasystem_api_contract_smoke(timeout: int) -> None:
    try:
        from yr.object_ref import ObjectRef as object_ref_class
    except ModuleNotFoundError:
        object_ref_class = yr.ObjectRef

    @yr.invoke(invoke_options=yr.InvokeOptions(bypass_datasystem=True))
    def add_direct(left, right):
        return left + right

    @yr.invoke
    def add_auto(left, right):
        return left + right

    require_equal(yr.get(add_direct.invoke_direct(20, 22), timeout=timeout), 42, "direct invoke get failed")
    require_equal(yr.get(add_auto.invoke(20, 22), timeout=timeout), 42, "auto direct invoke get failed")

    ds_ref = object_ref_class("yr-smoke-ds-ref", need_incre=False, need_decre=False)
    cases = [
        ("put", lambda: yr.put(42)),
        ("get", lambda: yr.get(ds_ref, timeout=1)),
        ("ObjectRef.get", lambda: ds_ref.get(timeout=1)),
        ("wait", lambda: yr.wait(ds_ref, timeout=1)),
        ("create_stream_producer", lambda: yr.create_stream_producer("yr-smoke-stream", yr.ProducerConfig())),
        (
            "create_stream_consumer",
            lambda: yr.create_stream_consumer("yr-smoke-stream", yr.SubscriptionConfig("yr-smoke-sub")),
        ),
        ("query_global_producers_num", lambda: yr.query_global_producers_num("yr-smoke-stream")),
        ("query_global_consumers_num", lambda: yr.query_global_consumers_num("yr-smoke-stream")),
        ("delete_stream", lambda: yr.delete_stream("yr-smoke-stream")),
        ("kv_write", lambda: yr.kv_write("yr-smoke-key", b"value")),
        ("kv_write_with_param", lambda: yr.kv_write_with_param("yr-smoke-key", b"value", yr.SetParam())),
        ("kv_m_write_tx", lambda: yr.kv_m_write_tx(["yr-smoke-key"], [b"value"], yr.MSetParam())),
        ("kv_read", lambda: yr.kv_read("yr-smoke-key", timeout=1)),
        ("kv_set", lambda: yr.kv_set("yr-smoke-key", b"value", yr.SetParam())),
        ("kv_get", lambda: yr.kv_get("yr-smoke-key", timeout=1)),
        ("kv_get_with_param", lambda: yr.kv_get_with_param(["yr-smoke-key"], make_get_params(), timeout=1)),
        ("kv_del", lambda: yr.kv_del("yr-smoke-key")),
        ("save_state", lambda: yr.save_state(timeout_sec=1)),
        ("load_state", lambda: yr.load_state(timeout_sec=1)),
    ]
    for case_name, action in cases:
        expect_no_datasystem_error(case_name, action)
    LOGGER.info("SMOKE no-ds DataSystem API contract ok")


def main() -> None:
    server_address = os.environ["YR_SERVER_ADDRESS"]
    timeout = int(os.getenv("YR_K8S_SMOKE_TIMEOUT", "180"))
    bypass_datasystem = env_optional_bool("YR_BYPASS_DATASYSTEM")
    smoke_mode = os.getenv("YR_K8S_SMOKE_MODE", "").strip().lower()
    conf = yr.Config(
        server_address=server_address,
        ds_address=server_address,
        in_cluster=False,
        enable_tls=env_bool("YR_ENABLE_TLS", False),
        log_level=os.getenv("YR_LOG_LEVEL", "INFO"),
        auth_token=os.getenv("YR_JWT_TOKEN", ""),
        bypass_datasystem=bypass_datasystem,
    )

    yr.init(conf)
    try:
        if smoke_mode == DS_API_CONTRACT_MODE:
            run_datasystem_api_contract_smoke(timeout)
            return

        if bypass_datasystem is True:
            @yr.invoke(invoke_options=yr.InvokeOptions(bypass_datasystem=True))
            def add(left, right):
                return left + right

            require_equal(yr.get(add.invoke_direct(20, 22), timeout=timeout), 42, "SMOKE direct invoke failed")
            LOGGER.info("SMOKE direct invoke ok")
            return

        ref = yr.put(42)
        require_equal(yr.get(ref, timeout=timeout), 42, "SMOKE put/get failed")
        LOGGER.info("SMOKE put/get ok")

        @yr.invoke
        def add(left, right):
            return left + right

        invoke_ref = add.invoke(20, 22)
        require_equal(yr.get(invoke_ref, timeout=timeout), 42, "SMOKE remote invoke failed")
        LOGGER.info("SMOKE remote invoke ok")
        del ref, invoke_ref
        gc.collect()
    finally:
        yr.finalize()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    main()
