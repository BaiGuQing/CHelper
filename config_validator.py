# -*- coding: utf-8 -*-
"""
配置验证 - 检查配置文件格式和值的有效性
"""

import math
from typing import List, Tuple
from urllib.parse import urlparse


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
        if not isinstance(config, dict):
            return False, ["配置根节点必须是对象"]

        if "llm" not in config:
            errors.append("缺少 'llm' 配置节")
        else:
            llm = config["llm"]
            if not isinstance(llm, dict):
                errors.append("'llm' 配置节必须是对象")
                llm = {}

            def number_in_range(name, value, low, high):
                if value is None:
                    return
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    errors.append(f"llm.{name} 必须是数字，当前值: {value!r}")
                elif isinstance(value, float) and not math.isfinite(value):
                    errors.append(f"llm.{name} 必须是有限数字，当前值: {value!r}")
                elif value < low or value > high:
                    errors.append(f"llm.{name} 应在 {low}-{high} 之间，当前值: {value}")

            # 必填项
            if not isinstance(llm.get("api_url"), str) or not llm.get("api_url"):
                errors.append("llm.api_url 不能为空")
            if not isinstance(llm.get("model"), str) or not llm.get("model"):
                errors.append("llm.model 不能为空")

            api_url = llm.get("api_url", "")
            try:
                parsed_url = urlparse(api_url) if isinstance(api_url, str) else None
                if parsed_url and parsed_url.scheme not in ("http", "https"):
                    errors.append("llm.api_url 必须使用 http 或 https")
                elif parsed_url and not parsed_url.netloc:
                    errors.append("llm.api_url 必须包含主机名")
                elif parsed_url:
                    _ = parsed_url.port
            except ValueError:
                errors.append("llm.api_url 格式无效")

            # 数值范围检查
            number_in_range("temperature", llm.get("temperature"), 0, 2)
            number_in_range("max_tokens", llm.get("max_tokens"), 1, 100000)
            number_in_range("timeout", llm.get("timeout"), 1, 3600)
            number_in_range("startup_timeout", llm.get("startup_timeout"), 1, 7200)
            number_in_range("context_size", llm.get("context_size"), 256, 1048576)
            number_in_range("top_p", llm.get("top_p"), 0, 1)
            number_in_range("top_k", llm.get("top_k"), 0, 100000)
            number_in_range("min_p", llm.get("min_p"), 0, 1)
            number_in_range("repeat_penalty", llm.get("repeat_penalty"), 0, 10)
            number_in_range("frequency_penalty", llm.get("frequency_penalty"), -2, 2)
            number_in_range("presence_penalty", llm.get("presence_penalty"), -2, 2)
            number_in_range("retry_delay", llm.get("retry_delay"), 0, 300)

            for bool_name in ("auto_start", "strip_reasoning"):
                value = llm.get(bool_name)
                if value is not None and not isinstance(value, bool):
                    errors.append(f"llm.{bool_name} 必须是布尔值，当前值: {value!r}")

            tags = llm.get("reasoning_tags")
            if tags is not None and (
                not isinstance(tags, list)
                or not all(isinstance(tag, str) and tag.strip() for tag in tags)
            ):
                errors.append("llm.reasoning_tags 必须是非空字符串数组")

            # backend 检查
            backend = llm.get("backend", "llama_cpp")
            if backend not in ["llama_cpp", "vllm", "openai"]:
                errors.append(
                    "llm.backend 必须是 'llama_cpp'、'vllm' 或 'openai'，"
                    f"当前值: {backend}"
                )
            elif backend == "openai" and llm.get("auto_start", False):
                errors.append("使用 llm.backend='openai' 时必须关闭 llm.auto_start")
            model_path = llm.get("model_path", "")
            if model_path is not None and not isinstance(model_path, str):
                errors.append("llm.model_path 必须是字符串")
            llama_binary = llm.get("llama_server_binary", "")
            if llama_binary is not None and not isinstance(llama_binary, str):
                errors.append("llm.llama_server_binary 必须是字符串")
            if backend == "vllm" and llm.get("auto_start", False):
                binary = llm.get("vllm_binary", "")
                if not isinstance(binary, str) or not binary.strip():
                    errors.append("llm.vllm_binary 不能为空")

            # conservative_mode 检查
            conservative = llm.get("conservative_mode", "auto")
            if conservative not in ["auto", "on", "off"]:
                errors.append(f"llm.conservative_mode 必须是 'auto', 'on' 或 'off'，当前值: {conservative}")

            quality_profile = llm.get("quality_guard_profile", "balanced")
            if quality_profile not in ("strict", "balanced", "globals_only"):
                errors.append(
                    "llm.quality_guard_profile 必须是 'strict'、'balanced' "
                    f"或 'globals_only'，当前值: {quality_profile!r}"
                )

            # llama.cpp reasoning 开关
            reasoning = llm.get("reasoning", "off")
            if reasoning not in ["auto", "on", "off"]:
                errors.append(f"llm.reasoning 必须是 'auto', 'on' 或 'off'，当前值: {reasoning}")

            reasoning_effort = llm.get("reasoning_effort")
            if reasoning_effort is not None and reasoning_effort not in (
                "none", "low", "medium", "high", "max"
            ):
                errors.append(
                    "llm.reasoning_effort 必须是 null、'none'、'low'、"
                    f"'medium'、'high' 或 'max'，当前值: {reasoning_effort!r}"
                )

            budget = llm.get("reasoning_budget", 0)
            if budget is not None:
                if isinstance(budget, bool) or not isinstance(budget, int):
                    errors.append(f"llm.reasoning_budget 必须是整数或 null，当前值: {budget!r}")
                elif budget < -1 or budget > 100000:
                    errors.append(f"llm.reasoning_budget 应在 -1-100000 之间，当前值: {budget}")

            number_in_range("minimum_output_ratio", llm.get("minimum_output_ratio"), 0.1, 1.0)

            quality_repair_attempts = llm.get("quality_repair_attempts", 1)
            if isinstance(quality_repair_attempts, bool) or not isinstance(quality_repair_attempts, int):
                errors.append(
                    "llm.quality_repair_attempts 必须是整数，当前值: "
                    f"{quality_repair_attempts!r}"
                )
            elif quality_repair_attempts < 0 or quality_repair_attempts > 3:
                errors.append(
                    "llm.quality_repair_attempts 应在 0-3 之间，当前值: "
                    f"{quality_repair_attempts}"
                )

            for bool_name in (
                "quality_guard", "degeneration_guard", "send_extended_parameters",
                "restore_unused_parameter_signature", "local_readability_fallback",
                "conservative_model_renames", "show_rejected_candidate",
            ):
                value = llm.get(bool_name)
                if value is not None and not isinstance(value, bool):
                    errors.append(f"llm.{bool_name} 必须是布尔值，当前值: {value!r}")

            seed = llm.get("seed")
            if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
                errors.append(f"llm.seed 必须是整数或 null，当前值: {seed!r}")

            # 重试次数检查
            retry = llm.get("max_retry_attempts")
            if retry is not None:
                if isinstance(retry, bool) or not isinstance(retry, int):
                    errors.append(f"llm.max_retry_attempts 必须是整数，当前值: {retry!r}")
                elif retry < 1 or retry > 10:
                    errors.append(f"llm.max_retry_attempts 应在 1-10 之间，当前值: {retry}")

        # 验证 plugin 配置
        if "plugin" not in config:
            errors.append("缺少 'plugin' 配置节")
        else:
            plugin = config["plugin"]
            if not isinstance(plugin, dict):
                errors.append("'plugin' 配置节必须是对象")
                plugin = {}

            # 快捷键格式检查（简单）
            hotkey = plugin.get("hotkey", "")
            if hotkey and not any(mod in hotkey for mod in ["Ctrl", "Alt", "Shift"]):
                errors.append(f"plugin.hotkey 格式可能不正确: {hotkey}")

            hotkey_force = plugin.get("hotkey_force", "")
            if hotkey_force and not any(mod in hotkey_force for mod in ["Ctrl", "Alt", "Shift"]):
                errors.append(f"plugin.hotkey_force 格式可能不正确: {hotkey_force}")
            if hotkey and hotkey_force and hotkey.replace(" ", "").lower() == hotkey_force.replace(" ", "").lower():
                errors.append("plugin.hotkey 与 plugin.hotkey_force 不能相同")

            # 函数大小检查
            max_size = plugin.get("max_function_size")
            if max_size is not None:
                if isinstance(max_size, bool) or not isinstance(max_size, int):
                    errors.append(f"plugin.max_function_size 必须是整数，当前值: {max_size!r}")
                elif max_size < 100 or max_size > 1000000:
                    errors.append(f"plugin.max_function_size 应在 100-1000000 之间，当前值: {max_size}")

            # 缓存有效期检查
            cache_age = plugin.get("cache_max_age_days")
            if cache_age is not None:
                if isinstance(cache_age, bool) or not isinstance(cache_age, int):
                    errors.append(f"plugin.cache_max_age_days 必须是整数，当前值: {cache_age!r}")
                elif cache_age < 1 or cache_age > 365:
                    errors.append(f"plugin.cache_max_age_days 应在 1-365 之间，当前值: {cache_age}")

            for bool_name in (
                "debug", "enable_cache", "cache_cleanup_on_start",
            ):
                value = plugin.get(bool_name)
                if value is not None and not isinstance(value, bool):
                    errors.append(f"plugin.{bool_name} 必须是布尔值，当前值: {value!r}")

            for string_name in ("log_file", "cache_dir", "service_log_file"):
                value = plugin.get(string_name)
                if value is not None and not isinstance(value, str):
                    errors.append(f"plugin.{string_name} 必须是字符串，当前值: {value!r}")

        # 验证 optimization 配置
        if "optimization" not in config:
            errors.append("缺少 'optimization' 配置节")
        elif not isinstance(config["optimization"], dict):
            errors.append("'optimization' 配置节必须是对象")

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
