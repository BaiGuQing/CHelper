# -*- coding: utf-8 -*-
"""
CHelper - IDA Pro反编译代码优化插件配置
"""

import os
import json

class Config:
    """插件配置管理"""

    # 默认配置
    DEFAULT_CONFIG = {
        "llm": {
            "api_url": "http://localhost:8000/v1/chat/completions",  # OpenAI兼容API地址
            "model": "VibeThinker-3B",
            "temperature": 0.3,
            "max_tokens": 4096,
            "timeout": 180,
            "api_key": "",  # 本地模型通常不需要
            "strip_reasoning": True,  # 剥离推理模型的<think>块
            "reasoning_tags": ["think", "thinking"],  # 需要剥离的推理标签
            "auto_start": True,  # 插件加载时自动检测并启动 LLM 服务
            "backend": "llama_cpp",  # 后端类型: "llama_cpp" 或 "vllm"
            "model_path": "",  # 模型权重路径，空则自动查找（llama_cpp找.gguf，vllm找目录）
            "vllm_binary": "vllm",  # vLLM 可执行文件名
            "llama_server_binary": "",  # llama-server 路径，空则自动找插件目录/.llama_bin/
            "n_gpu_layers": -1,  # llama.cpp  offload 到 GPU 的层数，-1 表示全部
            "context_size": 8192,  # llama.cpp 上下文长度
            "startup_timeout": 180,  # 服务启动超时（秒）
            # --- 重复退化抑制（治小模型重复输出的 bug） ---
            "repeat_penalty": 1.15,  # llama.cpp 重复惩罚，>1 抑制重复 token
            "frequency_penalty": 0.3,  # OpenAI 风格频率惩罚
            "presence_penalty": 0.3,  # OpenAI 风格存在惩罚
            # --- 保守模式（治小模型重建复杂逻辑失败） ---
            # "off" 总是全量优化；"on" 总是保守(只改名+注释)；"auto" 自动检测硬骨头走保守
            "conservative_mode": "auto",
            "degeneration_guard": True,  # 后处理退化检测兜底，发现重复行自动截断
            # --- 网络重试 ---
            "max_retry_attempts": 3,  # 最大重试次数
            "retry_delay": 1.0,  # 初始重试延迟（秒）
        },
        "plugin": {
            "hotkey": "Ctrl+Shift+C",
            "hotkey_force": "Ctrl+Shift+R",  # 强制刷新快捷键（忽略缓存）
            "show_diff": True,
            "auto_apply": False,  # 是否自动应用优化后的代码
            "max_function_size": 10000,  # 最大处理函数大小（字符）
            # --- 日志配置 ---
            "debug": False,  # 调试模式
            "log_file": "chelper.log",  # 日志文件路径（相对插件目录）
            # --- 缓存配置 ---
            "enable_cache": True,  # 启用缓存
            "cache_dir": ".cache",  # 缓存目录（相对插件目录）
            "cache_max_age_days": 30,  # 缓存最大有效期（天）
            "cache_cleanup_on_start": True,  # 启动时清理过期缓存
            # --- 流式输出 ---
            "enable_streaming": False,  # 启用流式输出（实验性功能）
        },
        "optimization": {
            "deobfuscate_ollvm": True,
            "simplify_expressions": True,
            "improve_naming": True,
            "add_comments": True,
            "unroll_simple_loops": False  # 展开简单循环
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
                    user_config = json.load(f)
                # 合并用户配置和默认配置
                config = self._merge_config(self.DEFAULT_CONFIG.copy(), user_config)
                return config
            except Exception as e:
                print(f"[CHelper] 加载配置文件失败: {e}，使用默认配置")
                return self.DEFAULT_CONFIG.copy()
        else:
            # 创建默认配置文件
            self.save_config(self.DEFAULT_CONFIG)
            return self.DEFAULT_CONFIG.copy()

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
