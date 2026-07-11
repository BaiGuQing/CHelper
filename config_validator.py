# -*- coding: utf-8 -*-
"""
配置验证 - 检查配置文件格式和值的有效性
"""

from typing import List, Tuple


class ConfigValidator:
    """配置文件验证器"""

    @staticmethod
    def validate(config: dict) -> Tuple[bool, List[str]]:
        """验证配置

        Args:
            config: 配置字典

        Returns:
            (是否有效, 错误列表)
        """
        errors = []

        # 验证 llm 配置
        if "llm" not in config:
            errors.append("缺少 'llm' 配置节")
        else:
            llm = config["llm"]

            # 必填项
            if not llm.get("api_url"):
                errors.append("llm.api_url 不能为空")
            if not llm.get("model"):
                errors.append("llm.model 不能为空")

            # 数值范围检查
            temp = llm.get("temperature")
            if temp is not None and (temp < 0 or temp > 2):
                errors.append(f"llm.temperature 应在 0-2 之间，当前值: {temp}")

            max_tokens = llm.get("max_tokens")
            if max_tokens is not None and (max_tokens < 1 or max_tokens > 100000):
                errors.append(f"llm.max_tokens 应在 1-100000 之间，当前值: {max_tokens}")

            timeout = llm.get("timeout")
            if timeout is not None and (timeout < 1 or timeout > 3600):
                errors.append(f"llm.timeout 应在 1-3600 之间，当前值: {timeout}")

            # backend 检查
            backend = llm.get("backend", "llama_cpp")
            if backend not in ["llama_cpp", "vllm"]:
                errors.append(f"llm.backend 必须是 'llama_cpp' 或 'vllm'，当前值: {backend}")

            # conservative_mode 检查
            conservative = llm.get("conservative_mode", "auto")
            if conservative not in ["auto", "on", "off"]:
                errors.append(f"llm.conservative_mode 必须是 'auto', 'on' 或 'off'，当前值: {conservative}")

            # 重试次数检查
            retry = llm.get("max_retry_attempts")
            if retry is not None and (retry < 0 or retry > 10):
                errors.append(f"llm.max_retry_attempts 应在 0-10 之间，当前值: {retry}")

        # 验证 plugin 配置
        if "plugin" not in config:
            errors.append("缺少 'plugin' 配置节")
        else:
            plugin = config["plugin"]

            # 快捷键格式检查（简单）
            hotkey = plugin.get("hotkey", "")
            if hotkey and not any(mod in hotkey for mod in ["Ctrl", "Alt", "Shift"]):
                errors.append(f"plugin.hotkey 格式可能不正确: {hotkey}")

            # 函数大小检查
            max_size = plugin.get("max_function_size")
            if max_size is not None and (max_size < 100 or max_size > 1000000):
                errors.append(f"plugin.max_function_size 应在 100-1000000 之间，当前值: {max_size}")

            # 缓存有效期检查
            cache_age = plugin.get("cache_max_age_days")
            if cache_age is not None and (cache_age < 1 or cache_age > 365):
                errors.append(f"plugin.cache_max_age_days 应在 1-365 之间，当前值: {cache_age}")

        # 验证 optimization 配置
        if "optimization" not in config:
            errors.append("缺少 'optimization' 配置节")

        return len(errors) == 0, errors

    @staticmethod
    def get_friendly_error_message(errors: List[str]) -> str:
        """生成友好的错误消息

        Args:
            errors: 错误列表

        Returns:
            格式化的错误消息
        """
        if not errors:
            return ""

        msg = "配置文件验证失败，发现以下问题：\n\n"
        for i, error in enumerate(errors, 1):
            msg += f"{i}. {error}\n"

        msg += "\n请修改 config.json 后重新加载插件。"
        return msg
