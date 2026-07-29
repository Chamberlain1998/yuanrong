#!/usr/bin/env python3
# coding=UTF-8
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

"""yr exception type"""
import cloudpickle

from yr.common import utils
from yr.err_type import ErrorCode, ErrorInfo, ModuleCode


class YRError(Exception):
    """
    Base class for all custom exceptions in the YR module.
    """
    def __init__(
            self,
            code=ErrorCode.ERR_INNER_SYSTEM_ERROR,
            module_code=ModuleCode.RUNTIME,
            message: str = "",
            error_info: ErrorInfo = None,
            cause: Exception = None,
            stack_trace_infos=None):
        if isinstance(code, str) and message == "":
            message = code
            code = ErrorCode.ERR_INNER_SYSTEM_ERROR
        if error_info is not None:
            code = getattr(error_info, "error_code", code)
            module_code = getattr(error_info, "module_code", module_code)
            if not message:
                message = getattr(error_info, "msg", message)
            stack_trace_infos = getattr(error_info, "stack_trace_infos", stack_trace_infos)
        self.code = code
        self.module_code = module_code
        self.message = message
        self.error_info = error_info
        self.cause = cause
        self.stack_trace_infos = stack_trace_infos
        super().__init__(message)

    def __str__(self):
        return str(self.message)

    @classmethod
    def from_error_info(cls, error_info: ErrorInfo, message_prefix: str = None, cause: Exception = None):
        """Build a structured exception from ErrorInfo."""
        code = getattr(error_info, "error_code", ErrorCode.ERR_INNER_SYSTEM_ERROR)
        module_code = getattr(error_info, "module_code", ModuleCode.RUNTIME)
        code_value = getattr(code, "value", code)
        module_code_value = getattr(module_code, "value", module_code)
        message = (
            f"code: {code_value}, module code: {module_code_value}, "
            f"msg: {getattr(error_info, 'msg', '')}"
        )
        if message_prefix:
            message = f"{message_prefix}, {message}"
        return cls(message=message, error_info=error_info, cause=cause)


class YRRuntimeError(YRError, RuntimeError):
    """Structured runtime error that remains compatible with RuntimeError."""


class YRTimeoutError(YRRuntimeError, TimeoutError):
    """Structured timeout error that remains compatible with TimeoutError."""


class YRValueError(YRError, ValueError):
    """Structured value error that remains compatible with ValueError."""


class YRTypeError(YRError, TypeError):
    """Structured type error that remains compatible with TypeError."""


def raise_yr_runtime_error(
        message: str,
        code=ErrorCode.ERR_INNER_SYSTEM_ERROR,
        module_code=ModuleCode.RUNTIME,
        cause: Exception = None):
    """Raise a structured runtime error."""
    raise YRRuntimeError(code=code, module_code=module_code, message=message, cause=cause)


def raise_yr_timeout_error(
        message: str,
        code=ErrorCode.ERR_GET_OPERATION_FAILED,
        module_code=ModuleCode.RUNTIME,
        cause: Exception = None):
    """Raise a structured timeout error."""
    raise YRTimeoutError(code=code, module_code=module_code, message=message, cause=cause)


def raise_yr_value_error(
        message: str,
        code=ErrorCode.ERR_PARAM_INVALID,
        module_code=ModuleCode.RUNTIME,
        cause: Exception = None):
    """Raise a structured value error."""
    raise YRValueError(code=code, module_code=module_code, message=message, cause=cause)


def raise_yr_type_error(
        message: str,
        code=ErrorCode.ERR_PARAM_INVALID,
        module_code=ModuleCode.RUNTIME,
        cause: Exception = None):
    """Raise a structured type error."""
    raise YRTypeError(code=code, module_code=module_code, message=message, cause=cause)


class CancelError(YRError):
    """Stateless request cancel error."""
    __slots__ = ["__task_id"]

    def __init__(self, task_id: str = ""):
        super().__init__()
        self.__task_id = task_id

    def __str__(self):
        return f"stateless request has been cancelled: {self.__task_id}"


class YRInvokeError(YRRuntimeError):
    """
    Represents an error that occurred during an invocation.

    Attributes:
        traceback_str (str): The traceback information as a string.
        cause (Exception): The original exception that caused this error.

    Methods:
        __str__(): Returns the string representation of the exception, which is the traceback information.
        origin_error(): Returns the original error that caused this invocation error.

    """

    def __init__(self, cause, traceback_str: str):
        """
        init
        """
        self.traceback_str = traceback_str
        self.cause = cause

    def __str__(self):
        """
        Return the string representation of the exception, which is the traceback information.

        Returns:
             The traceback information as a string.
        """
        return str(self.traceback_str)

    def __reduce__(self):
        """
        Custom pickle reducer: try to pickle the cause object directly,
        fall back to a string representation if the cause is unpicklable.
        This ensures the traceback_str is never lost during serialization.
        """
        try:
            cloudpickle.dumps(self.cause)
            return (YRInvokeError, (self.cause, self.traceback_str))
        except Exception:
            cause_str = _cause_to_str(self.cause)
            return (_UnpicklableYRInvokeError, (cause_str, self.traceback_str))

    def origin_error(self):
        """
        Return a origin error for invoke stateless function.

        Returns:
            The original error that caused this invocation error.
        """

        cause_cls = self.cause.__class__
        if issubclass(YRInvokeError, cause_cls):
            return self
        if issubclass(cause_cls, YRError):
            return self

        error_msg = str(self)

        class Cls(YRInvokeError, cause_cls):
            """
            New error inherit from origin cause
            """
            def __init__(self, cause):
                self.cause = cause
                self.args = (cause,)

            def __getattr__(self, name):
                """
                Get attribute from the original cause
                """
                return getattr(self.cause, name)

            def __str__(self):
                """
                Return the error message.
                """
                return error_msg

        Cls.__name__ = f"YRInvokeError({cause_cls.__name__})"
        Cls.__qualname__ = Cls.__name__

        return Cls(self.cause)


class _UnpicklableYRInvokeError(YRInvokeError):
    """
    Fallback YRInvokeError used when the original cause cannot be pickled.
    Preserves the full traceback_str and a string representation of the cause.
    """

    def __init__(self, cause_str: str, traceback_str: str):
        super().__init__(RuntimeError(cause_str), traceback_str)

    def __str__(self):
        return str(self.traceback_str)

    def origin_error(self):
        return self


def _cause_to_str(cause):
    """Convert an unpicklable cause to a descriptive string."""
    cause_cls = type(cause).__name__
    try:
        cause_repr = repr(cause)
    except Exception:
        cause_repr = "<unrepresentable>"
    return f"{cause_cls}: {cause_repr}"


class YRequestError(YRRuntimeError):
    """Request failed error."""
    __slots__ = ["__request_id"]

    def __init__(self, code: int = 0, message: str = "", request_id=""):
        self.__request_id = request_id
        super().__init__(code=code, module_code=ModuleCode.RUNTIME, message=message)

    def __str__(self):
        return str(f"failed to request, {self.__request_id} code: {self.code}, message: {self.message} ")


class GetTimeoutError(YRTimeoutError):
    """Indicates that a call to the worker timed out."""
    pass


class YRChannelError(YRError):
    """Indicates that encountered a system error related
    to yr.dag.channel.
    """
    pass


class YRChannelTimeoutError(YRError, TimeoutError):
    """Raised when the Compiled Graph channel operation times out."""
    pass


class YRCgraphCapacityExceeded(YRError):
    """Raised when the Compiled Graph channel's buffer is at max capacity"""
    pass


class GeneratorFinished(Exception):
    """
    A custom exception raised when a generator has finished its operation.
    """
    def __init__(self, message):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return f"MyCustomError: {self.message}"


def deal_with_yr_error(future, err):
    """deal with yr invoke error"""
    if isinstance(err, YRInvokeError):
        future.set_exception(err.origin_error())
    else:
        future.set_exception(err)


def deal_with_error(future, code, message, task_id=""):
    """
    processing request exceptions
    """
    try:
        obj = cloudpickle.loads(utils.hex_to_binary(message))
    except Exception:
        future.set_exception(YRequestError(code, message, task_id))
        return
    if isinstance(obj, YRInvokeError):
        future.set_exception(obj.origin_error())
        return
    future.set_exception(YRequestError(code, str(obj), task_id))
