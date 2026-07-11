# -*- coding: utf-8 -*-
"""
LLM 服务管理器 - 检测 API 可用性，必要时后台启动 vLLM 服务

插件加载时若开启 auto_start，会在后台线程检测 API：
  - 已可达 -> 直接标记就绪，不启动
  - 不可达 -> 用 subprocess 后台启动 vLLM，轮询直到就绪或超时
启动过程不阻塞 IDA，用户按 F6 时若服务还没就绪会收到友好提示。
"""

import os
import sys
import time
import socket
import threading
import subprocess
import json
import urllib.request
import urllib.error
from urllib.parse import urlparse

from logger import get_logger
from constants import (
    SERVICE_POLL_INTERVAL,
    API_READY_TIMEOUT,
    PORT_CONNECT_TIMEOUT,
    DEFAULT_STARTUP_TIMEOUT,
)


class LLMServiceManager:
    """管理本地 LLM 服务（vLLM）的启动和状态检测"""

    def __init__(self, config):
        self.config = config
        self.api_url = config.get("llm.api_url", "")
        self.auto_start = config.get("llm.auto_start", False)
        self.model_path = config.get("llm.model_path", "")
        self.backend = config.get("llm.backend", "llama_cpp")
        self.vllm_binary = config.get("llm.vllm_binary", "vllm")
        self.llama_server_binary = config.get("llm.llama_server_binary", "")
        self.llama_n_gpu_layers = config.get("llm.n_gpu_layers", -1)
        self.llama_context_size = config.get("llm.context_size", 8192)
        self.startup_timeout = config.get("llm.startup_timeout", DEFAULT_STARTUP_TIMEOUT)
        self.logger = get_logger()

        # 状态: "idle" / "starting" / "ready" / "failed" / "disabled"
        self._status = "idle"
        self._error = ""
        self._process = None
        self._start_time = 0
        self._lock = threading.Lock()

        # 推断插件目录（用于默认 model_path）
        self._plugin_dir = os.path.dirname(os.path.abspath(__file__))

        # 推断 llama-server 可执行文件默认路径（插件目录旁的 .llama_bin）
        if not self.llama_server_binary:
            default_bin = os.path.join(self._plugin_dir, ".llama_bin", "llama-server.exe")
            if sys.platform == "win32" and os.path.isfile(default_bin):
                self.llama_server_binary = default_bin

    def _resolve_model_path(self) -> str:
        """解析模型权重路径

        优先级:
        1. config.llm.model_path（若非空）- vLLM 用目录，llama.cpp 用 .gguf 文件
        2. 插件目录下的默认权重（vLLM: VibeThinker-3B/ 目录；llama.cpp: *.gguf 文件）

        Returns:
            模型路径（目录或文件），找不到返回空字符串
        """
        if self.model_path:
            path = self.model_path
            # 相对路径基于插件目录解析
            if not os.path.isabs(path):
                path = os.path.join(self._plugin_dir, path)
            if os.path.exists(path):
                return path
            return ""

        # 按 backend 查找默认权重
        if self.backend == "llama_cpp":
            # 查找插件目录下的 GGUF 文件
            for name in ("VibeThinker-3B-Q4_K_M.gguf", "VibeThinker-3B.gguf"):
                candidate = os.path.join(self._plugin_dir, name)
                if os.path.isfile(candidate):
                    return candidate
            # 通配查找任意 .gguf
            for fname in os.listdir(self._plugin_dir):
                if fname.lower().endswith(".gguf"):
                    return os.path.join(self._plugin_dir, fname)
        else:
            # vLLM: 找 safetensors 目录
            default_path = os.path.join(self._plugin_dir, "VibeThinker-3B")
            if os.path.isdir(default_path):
                return default_path

        return ""

    def _parse_host_port(self) -> tuple:
        """从 api_url 解析 host 和 port

        Returns:
            (host, port)，失败返回 ("", 0)
        """
        try:
            parsed = urlparse(self.api_url)
            host = parsed.hostname or "localhost"
            port = parsed.port or 8000
            return host, port
        except Exception:
            return "localhost", 8000

    def _is_port_open(self, host: str, port: int, timeout: float = 2.0) -> bool:
        """检测端口是否可连接

        Args:
            host: 主机
            port: 端口
            timeout: 连接超时

        Returns:
            可连接返回True
        """
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

    def _is_api_ready(self, timeout: float = 3.0) -> bool:
        """检测 LLM API 是否就绪（GET /v1/models）

        端口可连不等于服务就绪（vLLM 还在加载模型），需要 API 真正能响应。

        Args:
            timeout: 请求超时

        Returns:
            API 就绪返回True
        """
        try:
            parsed = urlparse(self.api_url)
            base_url = f"{parsed.scheme}://{parsed.netloc}"
            models_url = f"{base_url}/v1/models"

            req = urllib.request.Request(models_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        return False

    def _build_vllm_command(self, model_path: str, host: str, port: int) -> list:
        """构建 vLLM 启动命令

        Args:
            model_path: 模型权重路径（safetensors 目录）
            host: 监听 host
            port: 监听端口

        Returns:
            命令参数列表
        """
        model_name = self.config.get("llm.model", "VibeThinker-3B")

        cmd = [
            self.vllm_binary,
            "serve",
            model_path,
            "--host",
            host,
            "--port",
            str(port),
            "--served-model-name",
            model_name,
            "--trust-remote-code",
            "--dtype",
            "bfloat16",
        ]
        return cmd

    def _build_llama_server_command(self, model_path: str, host: str, port: int) -> list:
        """构建 llama-server 启动命令

        llama-server 提供 OpenAI 兼容的 /v1/chat/completions API，
        可直接被 llm_client.py 调用，无需改插件其他代码。

        Args:
            model_path: GGUF 文件路径
            host: 监听 host
            port: 监听端口

        Returns:
            命令参数列表
        """
        cmd = [
            self.llama_server_binary,
            "--model",
            model_path,
            "--host",
            host,
            "--port",
            str(port),
            "--ctx-size",
            str(self.llama_context_size),
            "--n-gpu-layers",
            str(self.llama_n_gpu_layers),
            "--jinja",
        ]
        return cmd

    def _start_service(self, model_path: str, host: str, port: int) -> bool:
        """后台启动 LLM 服务进程（根据 backend 选择 vLLM 或 llama-server）

        Args:
            model_path: 模型路径（vLLM 为目录，llama.cpp 为 .gguf 文件）
            host: 监听 host
            port: 监听端口

        Returns:
            进程启动成功返回True（不等于服务就绪）
        """
        if self.backend == "llama_cpp":
            if not self.llama_server_binary or not os.path.isfile(self.llama_server_binary):
                self._error = (
                    f"找不到 llama-server 可执行文件。请在 config.json 的 "
                    f"llm.llama_server_binary 填写路径，或将 llama-server.exe "
                    f"放到插件目录的 .llama_bin/ 子目录下"
                )
                return False
            cmd = self._build_llama_server_command(model_path, host, port)
            bin_label = "llama-server"
        else:
            cmd = self._build_vllm_command(model_path, host, port)
            bin_label = "vLLM"

        try:
            # Windows 下隐藏控制台窗口，不阻塞 IDA
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            self._print(f"{bin_label} 进程已启动 (PID: {self._process.pid})")
            self._print(f"命令: {' '.join(cmd)}")
            return True
        except FileNotFoundError:
            self._error = (
                f"找不到可执行文件 '{cmd[0]}'，"
                f"请检查 config.json 的 llm.backend / vllm_binary / llama_server_binary 配置"
            )
            return False
        except Exception as e:
            self._error = f"启动 {bin_label} 失败: {e}"
            return False

    def _print(self, msg: str):
        """打印到 IDA 输出窗口（线程安全方式）"""
        self.logger.info(msg)
        print(f"[CHelper] {msg}")

    def _run_async(self):
        """后台线程主逻辑：检测 -> 启动 -> 轮询就绪"""
        with self._lock:
            if self._status in ("ready", "starting"):
                return
            self._status = "starting"
            self._start_time = time.time()

        host, port = self._parse_host_port()

        # 1. 先检测 API 是否已经可达
        self._print("正在检测模型服务状态...")
        if self._is_api_ready():
            self._print("模型服务已在运行，无需启动")
            with self._lock:
                self._status = "ready"
            return

        # 2. 若未开启 auto_start，标记为 disabled（让 handler 提示用户手动启动）
        if not self.auto_start:
            self._print(f"模型服务未运行，auto_start 未开启，请手动启动 {self.backend}")
            with self._lock:
                self._status = "disabled"
            return

        # 3. 解析模型路径
        model_path = self._resolve_model_path()
        if not model_path:
            self._error = (
                f"未找到模型权重。请在 config.json 的 llm.model_path 填写路径"
                f"（vLLM 用 safetensors 目录，llama.cpp 用 .gguf 文件），"
                f"或将权重放到插件目录下。（插件目录: {self._plugin_dir}）"
            )
            self._print(self._error)
            with self._lock:
                self._status = "failed"
            return

        # 4. 启动服务
        bin_label = "llama-server" if self.backend == "llama_cpp" else "vLLM"
        self._print(f"正在启动 {bin_label}（模型: {model_path}）...")
        self._print(f"首次加载模型到显存需要 10~60 秒，请耐心等待")
        if not self._start_service(model_path, host, port):
            with self._lock:
                self._status = "failed"
            return

        # 5. 轮询直到 API 就绪或超时
        poll_interval = SERVICE_POLL_INTERVAL
        while True:
            elapsed = time.time() - self._start_time
            if elapsed > self.startup_timeout:
                self._error = (
                    f"{bin_label} 启动超时（{self.startup_timeout}秒），"
                    f"可能是模型过大或显存不足。可调大 config.json 的 startup_timeout"
                )
                self._print(self._error)
                with self._lock:
                    self._status = "failed"
                return

            # 检查进程是否已退出（启动失败）
            if self._process and self._process.poll() is not None:
                self._error = (
                    f"{bin_label} 进程已退出（code={self._process.returncode}），"
                    f"可能是显存不足或参数错误。建议手动运行查看错误信息"
                )
                self._print(self._error)
                with self._lock:
                    self._status = "failed"
                return

            if self._is_api_ready():
                total = int(time.time() - self._start_time)
                self._print(f"模型服务就绪（耗时 {total} 秒）")
                with self._lock:
                    self._status = "ready"
                return

            time.sleep(poll_interval)

    # ===== 公开接口 =====

    def start_check_async(self):
        """异步启动状态检测（不阻塞 IDA 加载）

        插件 init 时调用，后台线程会自动检测/启动 vLLM。
        """
        if not self.api_url:
            self._print("警告: llm.api_url 未配置，跳过服务管理")
            return

        thread = threading.Thread(target=self._run_async, daemon=True)
        thread.start()

    def is_ready(self) -> bool:
        """API 是否就绪可调用"""
        with self._lock:
            return self._status == "ready"

    def get_status(self) -> str:
        """获取当前状态

        Returns:
            "idle" / "starting" / "ready" / "failed" / "disabled"
        """
        with self._lock:
            return self._status

    def get_error(self) -> str:
        """获取错误信息"""
        with self._lock:
            return self._error

    def get_elapsed(self) -> int:
        """获取已等待秒数（starting 状态下）"""
        with self._lock:
            if self._status == "starting" and self._start_time:
                return int(time.time() - self._start_time)
            return 0

    def check_now(self) -> bool:
        """同步检测一次 API（供 handler 在调用前快速确认）

        Returns:
            就绪返回True
        """
        if self._is_api_ready():
            with self._lock:
                self._status = "ready"
            return True
        return False


# 全局服务管理器实例
_global_service_manager = None


def init_service_manager(config):
    """初始化全局服务管理器并启动异步检测

    在插件 init 时调用。

    Args:
        config: Config 实例
    """
    global _global_service_manager
    if _global_service_manager is None:
        _global_service_manager = LLMServiceManager(config)
    _global_service_manager.start_check_async()
    return _global_service_manager


def get_service_manager() -> LLMServiceManager:
    """获取全局服务管理器实例"""
    global _global_service_manager
    if _global_service_manager is None:
        # 兜底：未初始化时创建一个（不会自动启动）
        from config import get_config
        _global_service_manager = LLMServiceManager(get_config())
    return _global_service_manager
