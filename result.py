# -*- coding: utf-8 -*-
"""
结果封装 - 统一错误处理
"""

from typing import Generic, TypeVar, Optional
from enum import IntEnum


class ErrorCode(IntEnum):
    """错误码"""
    SUCCESS = 0

    # 通用错误 1xx
    UNKNOWN_ERROR = 100
    INVALID_INPUT = 101
    TIMEOUT = 102

    # IDA 相关 2xx
    DECOMPILER_NOT_AVAILABLE = 200
    FUNCTION_NOT_FOUND = 201
    EXTRACTION_FAILED = 202
    FUNCTION_TOO_LARGE = 203
    NOT_IN_PSEUDOCODE_VIEW = 204

    # LLM 相关 3xx
    LLM_API_ERROR = 300
    LLM_TIMEOUT = 301
    LLM_CONNECTION_FAILED = 302
    LLM_SERVICE_NOT_READY = 303
    LLM_SERVICE_START_FAILED = 304
    LLM_RESPONSE_INVALID = 305

    # 代码处理 4xx
    SYNTAX_ERROR = 400
    INCOMPLETE_CODE = 401
    PROCESSING_FAILED = 402


T = TypeVar('T')


class Result(Generic[T]):
    """统一结果封装

    替代原来到处返回 None 的方式，明确表示成功/失败及原因

    使用示例:
        result = some_function()
        if result.is_success():
            data = result.get_data()
        else:
            print(result.get_error_message())
    """

    def __init__(
        self,
        success: bool,
        data: Optional[T] = None,
        error_code: ErrorCode = ErrorCode.SUCCESS,
        error_message: str = ""
    ):
        self._success = success
        self._data = data
        self._error_code = error_code
        self._error_message = error_message

    @classmethod
    def success(cls, data: Optional[T] = None) -> 'Result[T]':
        """创建成功结果"""
        return cls(True, data, ErrorCode.SUCCESS, "")

    @classmethod
    def failure(cls, error_code: ErrorCode, error_message: str) -> 'Result[T]':
        """创建失败结果"""
        return cls(False, None, error_code, error_message)

    def is_success(self) -> bool:
        """是否成功"""
        return self._success

    def is_failure(self) -> bool:
        """是否失败"""
        return not self._success

    def get_data(self) -> Optional[T]:
        """获取数据（成功时）"""
        return self._data

    def get_error_code(self) -> ErrorCode:
        """获取错误码"""
        return self._error_code

    def get_error_message(self) -> str:
        """获取错误消息"""
        return self._error_message

    def unwrap(self) -> T:
        """解包数据（失败时抛出异常）"""
        if self._success:
            return self._data
        raise RuntimeError(f"Result unwrap failed: [{self._error_code}] {self._error_message}")

    def unwrap_or(self, default: T) -> T:
        """解包数据（失败时返回默认值）"""
        return self._data if self._success else default

    def __bool__(self) -> bool:
        """支持 if result: 语法"""
        return self._success

    def __repr__(self) -> str:
        if self._success:
            return f"Result.success({self._data})"
        return f"Result.failure({self._error_code}, {self._error_message})"
