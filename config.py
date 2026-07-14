# -*- coding: utf-8 -*-
"""
CHelper - IDA Pro反编译代码优化插件配置
"""

import os
import json
import copy

class Config:
    """插件配置管理"""

    # 默认配置
    DEFAULT_CONFIG = {
        "llm": {
            "api_url": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "DeepSeek-R1-SFT-Q4_K_M.gguf",
            "temperature": 0.0,
            "max_tokens": 2048,
            "timeout": 300,
            "api_key": "",  # 本地模型通常不需要
            "strip_reasoning": True,
            "reasoning_tags": ["think", "thinking"],
            "auto_start": True,
            "backend": "llama_cpp",  # "llama_cpp" | "vllm" | "openai"
            "model_path": "DeepSeek-R1-SFT-Q4_K_M.gguf",
            "vllm_binary": "vllm",  # vLLM 可执行文件名
            "llama_server_binary": "",  # llama-server 路径，空则自动找插件目录/.llama_bin/
            "n_gpu_layers": -1,  # llama.cpp  offload 到 GPU 的层数，-1 表示全部
            "context_size": 8192,  # llama.cpp 上下文长度
            "startup_timeout": 180,  # 服务启动超时（秒）
            # --- 重复退化抑制（治小模型重复输出的 bug） ---
            "repeat_penalty": 1.05,  # C 代码需要重复变量名和调用，保持轻度惩罚
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            # 严格 OpenAI 兼容服务可能拒绝 top_k、min_p 等扩展字段。
            "send_extended_parameters": False,
            "top_p": 0.95,
            "top_k": 40,
            "min_p": 0.0,
            # 推理模板会消耗 token 且容易让小模型在最终代码前跑偏。
            # llama.cpp 后端可用 "off" / "on" / "auto"。
            "reasoning": "off",
            # OpenAI/Ollama chat API reasoning control: none/low/medium/high/max.
            "reasoning_effort": None,
            # llama.cpp：0 表示立即结束 <think>，避免推理模型挤占代码 token。
            "reasoning_budget": 0,
            # --- 保守模式（治小模型重建复杂逻辑失败） ---
            # IDA 伪代码包含 ABI、全局符号等高风险细节；默认只做保守美化。
            "conservative_mode": "on",
            # 保守模式只让模型返回短 JSON 命名建议，代码替换由插件本地完成。
            "conservative_model_renames": True,
            # 安全总开关；False 时直接展示模型候选。
            "quality_guard": True,
            # "strict" | "balanced" | "globals_only"
            # globals_only 只冻结 byte_*/g_*/dword_* 等 IDA 全局符号。
            "quality_guard_profile": "balanced",
            # 仅在模型候选最终未被安全流程采用时展示；不缓存不写回 IDA。
            "show_rejected_candidate": False,
            # 设为整数可复现采样；None 使用后端默认随机种子。
            "seed": None,
            # --- 网络重试 ---
            "max_retry_attempts": 3,  # 最大重试次数
            "retry_delay": 1.0,  # 初始重试延迟（秒）
        },
        "plugin": {
            "hotkey": "Ctrl+Alt+C",
            "hotkey_force": "Ctrl+Alt+R",  # 强制刷新快捷键（忽略缓存）
            "max_function_size": 10000,  # 最大处理函数大小（字符）
            # --- 日志配置 ---
            "debug": False,  # 调试模式
            "log_file": "chelper.log",  # 日志文件路径（相对插件目录）
            # --- 缓存配置 ---
            "enable_cache": True,  # 启用缓存
            "cache_dir": ".cache",  # 缓存目录（相对插件目录）
            "cache_max_age_days": 30,  # 缓存最大有效期（天）
            "cache_cleanup_on_start": True,  # 启动时清理过期缓存
            "service_log_file": "",  # 后端 stdout/stderr；为空则使用 .debug/
        },
        "optimization": {
            "deobfuscate_ollvm": True,
            "simplify_expressions": True,
            "improve_naming": True,
            "add_comments": True,
            "unroll_simple_loops": False,  # 展开简单循环
            "rewrite_control_flow": True,  # 允许重写循环/分支结构
        }
    }

    def __init__(self, config_path=None):
        """初始化配置

        Args:
            config_path: 配置文件路径，默认为插件目录下的config.json
        """
        if config_path is None:
            # 默认配置文件路径
            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(plugin_dir, "config.json")

        self.config_path = config_path
        self.config = self._load_config()

    def _load_config(self):
        """加载配置文件"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    # config.json accepts JSONC-style comments so related
                    # options can be documented next to their values.  The
                    # stripper is string-aware and therefore preserves URLs
                    # such as ``http://127.0.0.1`` and comment-like prompt text.
                    user_config = json.loads(
                        self._strip_json_comments(f.read())
                    )
                # 合并用户配置和默认配置
                config = self._merge_config(copy.deepcopy(self.DEFAULT_CONFIG), user_config)
                return config
            except Exception as e:
                print(f"[CHelper] 加载配置文件失败: {e}，使用默认配置")
                return copy.deepcopy(self.DEFAULT_CONFIG)
        else:
            # 创建默认配置文件
            self.save_config(self.DEFAULT_CONFIG)
            return copy.deepcopy(self.DEFAULT_CONFIG)

    @staticmethod
    def _strip_json_comments(source: str) -> str:
        """Remove // and /* */ comments without modifying JSON strings."""
        if not source:
            return source

        output = []
        index = 0
        in_string = False
        escaped = False
        length = len(source)

        while index < length:
            char = source[index]
            next_char = source[index + 1] if index + 1 < length else ""

            if in_string:
                output.append(char)
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                index += 1
                continue

            if char == '"':
                in_string = True
                output.append(char)
                index += 1
                continue

            if char == "/" and next_char == "/":
                index += 2
                while index < length and source[index] not in "\r\n":
                    index += 1
                continue

            if char == "/" and next_char == "*":
                index += 2
                while index < length:
                    if source[index] == "*" and index + 1 < length and source[index + 1] == "/":
                        index += 2
                        break
                    # Preserve line positions for useful JSON error messages.
                    if source[index] in "\r\n":
                        output.append(source[index])
                    index += 1
                continue

            output.append(char)
            index += 1

        return "".join(output)

    def _merge_config(self, default, user):
        """递归合并配置"""
        for key, value in user.items():
            if key in default:
                if isinstance(value, dict) and isinstance(default[key], dict):
                    default[key] = self._merge_config(default[key], value)
                else:
                    default[key] = value
            else:
                default[key] = value
        return default

    def save_config(self, config=None):
        """保存配置到文件

        Args:
            config: 要保存的配置，默认保存当前配置
        """
        if config is None:
            config = self.config

        try:
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"[CHelper] 保存配置文件失败: {e}")
            return False

    def get(self, key_path, default=None):
        """获取配置项

        Args:
            key_path: 配置路径，用.分隔，如 "llm.api_url"
            default: 默认值

        Returns:
            配置值
        """
        keys = key_path.split('.')
        value = self.config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def set(self, key_path, value):
        """设置配置项

        Args:
            key_path: 配置路径，用.分隔
            value: 配置值
        """
        keys = key_path.split('.')
        config = self.config

        # 导航到最后一级
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]

        # 设置值
        config[keys[-1]] = value

    def reload(self):
        """重新加载配置"""
        self.config = self._load_config()
        return self.config


# 全局配置实例
_global_config = None

def get_config():
    """获取全局配置实例"""
    global _global_config
    if _global_config is None:
        _global_config = Config()
    return _global_config


def reset_config():
    """Drop the process-wide configuration so a plugin reload rereads disk."""
    global _global_config
    _global_config = None
