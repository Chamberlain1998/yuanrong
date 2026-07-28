#!/usr/bin/env python3
# coding=UTF-8
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

"""Tests for structured yr exceptions."""
from concurrent.futures import Future

import pytest

from yr.err_type import ErrorCode, ErrorInfo, ModuleCode
from yr.exception import (
    YRError,
    YRInvokeError,
    YRRuntimeError,
    YRTimeoutError,
    YRTypeError,
    YRValueError,
    raise_yr_type_error,
    raise_yr_value_error,
)
from yr.object_ref import _set_future_helper


def test_runtime_error_has_structured_fields_and_runtime_compatibility():
    """Structured runtime errors are still normal RuntimeError instances."""
    error = YRRuntimeError(code=ErrorCode.ERR_PARAM_INVALID, module_code=ModuleCode.RUNTIME, message="bad")

    assert isinstance(error, RuntimeError)
    assert isinstance(error, YRError)
    assert error.code == ErrorCode.ERR_PARAM_INVALID
    assert error.module_code == ModuleCode.RUNTIME
    assert error.message == "bad"
    assert error.error_info is None
    assert str(error) == "bad"


def test_error_info_is_preserved():
    """ErrorInfo fields stay structured while the legacy message remains parseable."""
    info = ErrorInfo(ErrorCode.ERR_GET_OPERATION_FAILED, ModuleCode.RUNTIME, "get failed")
    error = YRRuntimeError.from_error_info(info)

    assert error.error_info is info
    assert error.code == ErrorCode.ERR_GET_OPERATION_FAILED
    assert error.module_code == ModuleCode.RUNTIME
    assert error.message == "code: 4005, module code 20, msg: get failed"
    assert str(error) == "code: 4005, module code 20, msg: get failed"


def test_error_info_message_prefix_is_preserved():
    """ErrorInfo prefixes and numeric codes remain in the public message."""
    info = ErrorInfo(ErrorCode.ERR_GET_OPERATION_FAILED, ModuleCode.RUNTIME, "boom")
    error = YRRuntimeError.from_error_info(info, message_prefix="failed to get object")

    assert error.error_info is info
    assert error.message == "failed to get object, code: 4005, module code 20, msg: boom"
    assert str(error) == "failed to get object, code: 4005, module code 20, msg: boom"


def test_integer_error_info_codes_remain_in_legacy_messages():
    """Cython integer error fields remain parseable with and without a prefix."""
    info = ErrorInfo(4005, 20, "get failed")

    error = YRRuntimeError.from_error_info(info)
    prefixed_error = YRRuntimeError.from_error_info(info, message_prefix="failed to get object")

    assert error.message == "code: 4005, module code 20, msg: get failed"
    assert prefixed_error.message == "failed to get object, code: 4005, module code 20, msg: get failed"


def test_timeout_value_and_type_errors_keep_builtin_catch_compatibility():
    """Specialized yr errors keep compatibility with built-in except clauses."""
    assert isinstance(YRTimeoutError(message="timeout"), TimeoutError)
    assert isinstance(YRValueError(message="bad value"), ValueError)
    assert isinstance(YRTypeError(message="bad type"), TypeError)
    assert YRValueError("bad value").message == "bad value"
    assert YRTypeError("bad type").message == "bad type"


def test_invoke_error_wrapping_yr_error_keeps_runtime_compatibility():
    """Invoke errors carrying structured runtime errors still match RuntimeError."""
    error = YRInvokeError(YRRuntimeError(message="inner"), "traceback")

    origin = error.origin_error()

    assert isinstance(origin, RuntimeError)
    assert str(origin) == "traceback"


def test_structured_errors_are_exported_from_yr():
    """Public structured exceptions are exported from the top-level yr module."""
    import yr

    assert yr.YRError is YRError
    assert yr.YRRuntimeError is YRRuntimeError
    assert yr.YRTimeoutError is YRTimeoutError
    assert yr.YRValueError is YRValueError
    assert yr.YRTypeError is YRTypeError


def test_python_validation_helpers_preserve_builtin_compatibility():
    """Validation helpers raise structured errors while preserving old catches."""
    with pytest.raises(ValueError) as value_exc:
        raise_yr_value_error("invalid timeout")
    assert isinstance(value_exc.value, YRValueError)
    assert value_exc.value.message == "invalid timeout"

    with pytest.raises(TypeError) as type_exc:
        raise_yr_type_error("invalid type")
    assert isinstance(type_exc.value, YRTypeError)
    assert type_exc.value.message == "invalid type"


def test_object_ref_future_error_info_becomes_structured_runtime_error():
    """ErrorInfo returned through object futures is not flattened to a string."""
    future = Future()
    info = ErrorInfo(ErrorCode.ERR_GET_OPERATION_FAILED, ModuleCode.RUNTIME, "boom")

    _set_future_helper(info, f=future)

    error = future.exception()
    assert isinstance(error, YRRuntimeError)
    assert isinstance(error, RuntimeError)
    assert error.error_info is info
    assert error.code == ErrorCode.ERR_GET_OPERATION_FAILED
