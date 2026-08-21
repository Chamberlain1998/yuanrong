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

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
SMOKE_PATH = ROOT / "smoke.py"


class FakeYR:
    class NoDataSystemError(RuntimeError):
        code = 4299
        module_code = 42

    class ObjectRef:
        def __init__(self, object_id, **kwargs):
            self.id = object_id
            self.kwargs = kwargs

        @staticmethod
        def get(timeout=None):
            del timeout
            FakeYR.raise_no_ds("ObjectRef.get")

    class Config:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class InvokeOptions:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class ProducerConfig:
        pass

    class SubscriptionConfig:
        def __init__(self, name):
            self.name = name

    class SetParam:
        pass

    class MSetParam:
        pass

    class GetParam:
        pass

    class GetParams:
        def __init__(self):
            self.get_params = []

    def __init__(self):
        self.calls = []
        self.no_ds = False

    def __getattr__(self, name):
        if name.startswith(("kv_", "create_stream_", "query_global_")) or name in {
            "delete_stream",
            "save_state",
            "load_state",
        }:
            def unavailable(*args, **kwargs):
                self.calls.append((name, args, kwargs))
                self.raise_no_ds(name)

            return unavailable
        raise AttributeError(name)

    @staticmethod
    def raise_no_ds(operation):
        raise FakeYR.NoDataSystemError(
            f"[ErrorCode: 4299] [ModuleName: DATASYSTEM] "
            f"DataSystem is disabled in this cluster; {operation} is unavailable"
        )

    def init(self, config):
        self.calls.append(("init", config.kwargs))

    def finalize(self):
        self.calls.append(("finalize",))

    def put(self, value):
        self.calls.append(("put", value))
        if self.no_ds:
            self.raise_no_ds("put")
        return ("put", value)

    def get(self, ref, timeout=None):
        self.calls.append(("get", ref, timeout))
        if isinstance(ref, self.ObjectRef):
            self.raise_no_ds("get")
        return ref[1]

    def wait(self, ref, wait_num=None, timeout=None):
        self.calls.append(("wait", ref, wait_num, timeout))
        self.raise_no_ds("wait")

    def invoke(self, func=None, invoke_options=None):
        def decorate(target):
            fake = self

            class Proxy:
                @staticmethod
                def invoke(*args):
                    fake.calls.append(("invoke", target.__name__, invoke_options))
                    return ("direct", target(*args))

                @staticmethod
                def invoke_direct(*args):
                    fake.calls.append(("invoke_direct", target.__name__, invoke_options))
                    return ("direct", target(*args))

            return Proxy()

        return decorate if func is None else decorate(func)


def load_smoke(fake_yr):
    spec = importlib.util.spec_from_file_location("yr_k8s_smoke_under_test", SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    object_ref_module = types.SimpleNamespace(ObjectRef=fake_yr.ObjectRef)
    err_type_module = types.SimpleNamespace(
        ErrorCode=types.SimpleNamespace(ERR_DATASYSTEM_FAILED=4299),
        ModuleCode=types.SimpleNamespace(DATASYSTEM=42),
    )
    with mock.patch.dict(
        sys.modules,
        {"yr": fake_yr, "yr.err_type": err_type_module, "yr.object_ref": object_ref_module},
    ):
        spec.loader.exec_module(module)
    return module


class SmokeTest(unittest.TestCase):
    def test_default_mode_preserves_datasystem_put_and_invoke(self):
        fake_yr = FakeYR()
        smoke = load_smoke(fake_yr)

        with mock.patch.dict("os.environ", {"YR_SERVER_ADDRESS": "frontend:8888"}, clear=True):
            smoke.main()

        self.assertIn(("put", 42), fake_yr.calls)
        self.assertTrue(any(call[0] == "invoke" for call in fake_yr.calls))
        init_config = next(call[1] for call in fake_yr.calls if call[0] == "init")
        self.assertIsNone(init_config["bypass_datasystem"])

    def test_explicit_bypass_uses_direct_invoke_without_put(self):
        fake_yr = FakeYR()
        smoke = load_smoke(fake_yr)

        with mock.patch.dict(
            "os.environ",
            {"YR_SERVER_ADDRESS": "frontend:8888", "YR_BYPASS_DATASYSTEM": "true"},
            clear=True,
        ):
            smoke.main()

        self.assertFalse(any(call[0] == "put" for call in fake_yr.calls))
        self.assertTrue(any(call[0] == "invoke_direct" for call in fake_yr.calls))

    def test_no_ds_contract_covers_auto_direct_and_ds_failures(self):
        fake_yr = FakeYR()
        fake_yr.no_ds = True
        smoke = load_smoke(fake_yr)

        with mock.patch.dict(
            "os.environ",
            {"YR_SERVER_ADDRESS": "frontend:8888", "YR_K8S_SMOKE_MODE": "ds-api-contract"},
            clear=True,
        ):
            smoke.main()

        self.assertTrue(any(call[0] == "invoke_direct" for call in fake_yr.calls))
        self.assertTrue(any(call[0] == "invoke" for call in fake_yr.calls))
        for operation in ["put", "wait", "kv_write", "create_stream_producer", "save_state"]:
            self.assertTrue(any(call[0] == operation for call in fake_yr.calls), operation)


if __name__ == "__main__":
    unittest.main()
