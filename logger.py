# -*- coding: utf-8 -*-
"""
统一日志系统 - 支持日志级别、文件输出、调试模式
"""

import os
import sys
import time
import threading
from enum import IntEnum
from typing import Optional


class LogLevel(IntEnum):
    """日志级别"""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    ERROR = 3


class Logger:
    """统一日志管理器

    功能：
    - 支持日志级别过滤
    - 同时输出到 IDA 控制台和文件
    - 线程安全
    - 支持调试模式
    """

    def __init__(self, name: str = "CHelper", level: LogLevel = LogLevel.INFO):
        self.name = name
        self.level = level
        self.log_file: Optional[str] = None
        self.debug_mode = False
        self._lock = threading.Lock()

    def set_level(self, level: LogLevel):
        """设置日志级别"""
        with self._lock:
            self.level = level

    def set_log_file(self, path: str):
        """设置日志文件路径"""
        with self._lock:
            try:
                # 确保目录存在
                log_dir = os.path.dirname(path)
                if log_dir and not os.path.exists(log_dir):
                    os.makedirs(log_dir, exist_ok=True)
                self.log_file = path
            except Exception as e:
                print(f"[{self.name}] 设置日志文件失败: {e}")

    def set_debug_mode(self, enabled: bool):
        """启用/禁用调试模式"""
        with self._lock:
            self.debug_mode = enabled
            if enabled:
                self.level = LogLevel.DEBUG

    def _format_message(self, level_str: str, message: str) -> str:
        """格式化日志消息"""
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        return f"[{timestamp}] [{self.name}] [{level_str}] {message}"

    def _write(self, level: LogLevel, message: str):
        """写日志（内部方法）"""
        with self._lock:
            if level < self.level:
                return

            level_names = {
                LogLevel.DEBUG: "DEBUG",
                LogLevel.INFO: "INFO",
                LogLevel.WARNING: "WARNING",
                LogLevel.ERROR: "ERROR"
            }
            level_str = level_names.get(level, "UNKNOWN")
            formatted = self._format_message(level_str, message)

            # 输出到 IDA 控制台
            print(formatted)

            # 输出到文件
            if self.log_file:
                try:
                    with open(self.log_file, 'a', encoding='utf-8') as f:
                        f.write(formatted + '\n')
                except Exception:
                    pass  # 静默失败，避免日志写入错误影响主流程

    def debug(self, message: str):
        """调试级别日志"""
        self._write(LogLevel.DEBUG, message)

    def info(self, message: str):
        """信息级别日志"""
        self._write(LogLevel.INFO, message)

    def warning(self, message: str):
        """警告级别日志"""
        self._write(LogLevel.WARNING, message)

    def error(self, message: str):
        """错误级别日志"""
        self._write(LogLevel.ERROR, message)

    def exception(self, message: str):
        """记录异常（包含堆栈）"""
        import traceback
        tb = traceback.format_exc()
        self._write(LogLevel.ERROR, f"{message}\n{tb}")


# 全局日志实例
_global_logger: Optional[Logger] = None


def init_logger(config=None) -> Logger:
    """初始化全局日志器

    Args:
        config: Config 实例（可选）

    Returns:
        Logger 实例
    """
    global _global_logger

    if _global_logger is None:
        _global_logger = Logger()

    if config:
        # 从配置读取日志设置
        debug_mode = config.get("plugin.debug", False)
        _global_logger.set_debug_mode(debug_mode)

        # 设置日志文件
        log_file = config.get("plugin.log_file", "")
        if log_file:
            # 支持相对路径（基于插件目录）
            if not os.path.isabs(log_file):
                plugin_dir = os.path.dirname(os.path.abspath(__file__))
                log_file = os.path.join(plugin_dir, log_file)
            _global_logger.set_log_file(log_file)
        else:
            # 默认日志文件路径
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            default_log = os.path.join(plugin_dir, "chelper.log")
            _global_logger.set_log_file(default_log)

    return _global_logger


def get_logger() -> Logger:
    """获取全局日志器实例

    Returns:
        Logger 实例
    """
    global _global_logger
    if _global_logger is None:
        _global_logger = Logger()
    return _global_logger
