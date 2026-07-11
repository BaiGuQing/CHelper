# -*- coding: utf-8 -*-
"""
处理器 - 处理F6快捷键和执行优化流程
"""

import ida_kernwin
import ida_hexrays

from config import get_config
from extractor import CodeExtractor
from llm_client import LLMClient
from processor import CodeProcessor
from presenter import ResultPresenter, ProgressDialog
from service_manager import get_service_manager
from logger import get_logger
from cache import get_cache
from result import Result, ErrorCode


class OptimizationHandler:
    """优化处理器 - 核心业务逻辑"""

    def __init__(self):
        self.config = get_config()
        self.llm_client = LLMClient(self.config)
        self.logger = get_logger()
        self.cache = get_cache()

    def can_execute(self) -> tuple:
        """检查是否可以执行优化

        Returns:
            (是否可执行, 错误信息)
        """
        # 检查反编译器
        if not CodeExtractor.is_decompiler_available():
            return False, "Hex-Rays反编译器不可用"

        # 检查当前窗口类型
        widget = ida_kernwin.get_current_widget()
        if not widget:
            return False, "无法获取当前窗口"

        widget_type = ida_kernwin.get_widget_type(widget)

        # 必须在伪代码视图中
        if widget_type != ida_kernwin.BWN_PSEUDOCODE:
            return False, "请在反编译伪代码窗口中使用此功能"

        # 检查函数
        func = CodeExtractor.get_current_function()
        if func is None:
            return False, "未找到当前函数"

        return True, ""

    def execute(self, force_refresh: bool = False) -> bool:
        """执行优化流程

        Args:
            force_refresh: 强制刷新，忽略缓存

        Returns:
            成功返回True
        """
        # 检查是否可执行
        can_exec, error_msg = self.can_execute()
        if not can_exec:
            ResultPresenter.show_error(error_msg)
            return False

        # 检查 LLM 服务是否就绪（auto_start 场景下可能还在启动中）
        svc = get_service_manager()
        if not svc.is_ready():
            status = svc.get_status()
            hotkey = self.config.get("plugin.hotkey", "Ctrl+Shift+C")
            if status == "starting":
                elapsed = svc.get_elapsed()
                ResultPresenter.show_info(
                    f"模型服务正在启动中（已等待 {elapsed} 秒），\n"
                    f"vLLM 加载模型到显存需要 30~90 秒，\n"
                    f"请稍后再按 {hotkey} 重试。"
                )
                return False
            elif status == "failed":
                ResultPresenter.show_error(
                    f"模型服务启动失败：{svc.get_error()}\n"
                    f"请检查 config.json 的 auto_start/model_path 配置，\n"
                    f"或手动启动 vLLM 后重试。"
                )
                return False

        try:
            with ProgressDialog("CHelper - 正在提取代码...") as progress:
                # 1. 提取代码和上下文
                pseudocode, context, cfunc = CodeExtractor.get_current_pseudocode()

                if pseudocode is None:
                    ResultPresenter.show_error("提取伪C代码失败")
                    self.logger.error("提取伪C代码失败")
                    return False

                # 检查代码大小
                max_size = self.config.get("plugin.max_function_size", 10000)
                if not CodeExtractor.validate_pseudocode_size(pseudocode, max_size):
                    msg = f"函数代码过大 ({len(pseudocode)} 字符)，超过限制 ({max_size} 字符)"
                    ResultPresenter.show_error(msg)
                    self.logger.warning(msg)
                    return False

                function_name = context.get('function_name', 'unknown')
                address = context.get('address', '')
                self.logger.info(f"正在优化函数: {function_name} @ {address}")
                ResultPresenter.print_to_output(f"正在优化函数: {function_name}")

                # 2. 检查缓存
                processed_code = None
                cache_hit = False
                if self.cache and self.config.get("plugin.enable_cache", True) and not force_refresh:
                    progress.update("CHelper - 检查缓存...")
                    cached = self.cache.get(address, pseudocode)
                    if cached:
                        processed_code = cached.get("optimized_code")
                        if processed_code:
                            cache_hit = True
                            self.logger.info(f"缓存命中: {function_name}")
                            ResultPresenter.print_to_output(f"缓存命中，跳过 LLM 调用")
                elif force_refresh:
                    self.logger.info("强制刷新模式，跳过缓存检查")

                # 3. 调用LLM优化（未命中缓存时）
                if not cache_hit:
                    progress.update("CHelper - 正在调用本地大模型优化...")

                    # 检查是否可以取消
                    if progress.check_cancelled():
                        self.logger.info("用户取消优化")
                        return False

                    optimized_code = self.llm_client.optimize_code(pseudocode, context)

                    if optimized_code is None:
                        ResultPresenter.show_error("LLM优化失败，请检查配置和模型服务")
                        self.logger.error("LLM优化失败")
                        return False

                    # 4. 后处理
                    progress.update("CHelper - 正在处理结果...")

                    # 再次检查取消
                    if progress.check_cancelled():
                        self.logger.info("用户取消优化")
                        return False

                    processed_code, success, proc_error = CodeProcessor.process(
                        optimized_code, pseudocode, self.config
                    )

                    if not success:
                        warning_msg = f"代码处理: {proc_error}（仍会显示结果）"
                        ResultPresenter.print_to_output(f"警告: {warning_msg}")
                        self.logger.warning(warning_msg)
                        processed_code = optimized_code

            # 4. 计算统计
            stats = CodeProcessor.calculate_diff_stats(pseudocode, processed_code)

            # 5. 保存到缓存（仅在非缓存命中时）
            if not cache_hit and self.cache and self.config.get("plugin.enable_cache", True):
                try:
                    self.cache.set(address, pseudocode, processed_code, function_name, stats)
                    self.logger.debug(f"已缓存优化结果: {function_name}")
                except Exception as e:
                    self.logger.warning(f"缓存保存失败: {e}")

            # 6. 自动复制优化代码到剪贴板
            try:
                ida_kernwin.copy_to_clipboard(processed_code)
                clipboard_msg = "已复制到剪贴板"
            except Exception:
                clipboard_msg = "复制到剪贴板失败"

            # 7. 在新窗口显示优化后的代码（类似 F5 伪代码窗口）
            show_diff = self.config.get("plugin.show_diff", True)
            auto_apply = self.config.get("plugin.auto_apply", False)

            ResultPresenter.show_optimized_window(processed_code, context, stats)

            # 打印统计信息
            cache_suffix = " (缓存)" if cache_hit else ""
            result_msg = (
                f"优化完成{cache_suffix}: {stats['original_lines']} -> {stats['optimized_lines']} 行, "
                f"{stats['original_chars']} -> {stats['optimized_chars']} 字符, "
                f"{clipboard_msg}"
            )
            ResultPresenter.print_to_output(result_msg)
            self.logger.info(result_msg)

            # auto_apply 模式下额外提示
            if auto_apply:
                ResultPresenter.show_info(
                    f"优化完成，代码{clipboard_msg}。\n"
                    f"您可以在 IDA 中手动重命名变量/函数、添加注释。"
                )

            return True

        except Exception as e:
            error_msg = f"执行过程中出错: {str(e)}"
            ResultPresenter.show_error(error_msg)
            self.logger.exception(error_msg)
            return False

    def _apply_to_ida(self, code: str, cfunc) -> bool:
        """将优化后的代码应用到IDA（当前仅复制到剪贴板）

        注意：直接修改IDA反编译结果是复杂操作，当前版本仅复制到剪贴板。
        后续版本将通过 ida_name.set_name() 等接口直接重命名。

        Args:
            code: 优化后的代码
            cfunc: cfunc对象

        Returns:
            成功返回True
        """
        try:
            ida_kernwin.copy_to_clipboard(code)
            return True
        except Exception as e:
            ResultPresenter.show_error(f"应用代码失败: {str(e)}")
            return False


class HotkeyHandler(ida_kernwin.action_handler_t):
    """快捷键处理器"""

    def __init__(self, handler: OptimizationHandler, force_refresh: bool = False):
        ida_kernwin.action_handler_t.__init__(self)
        self.handler = handler
        self.force_refresh = force_refresh

    def activate(self, ctx):
        """快捷键被触发时调用

        Args:
            ctx: 上下文

        Returns:
            1表示成功
        """
        self.handler.execute(force_refresh=self.force_refresh)
        return 1

    def update(self, ctx):
        """更新快捷键状态

        Args:
            ctx: 上下文

        Returns:
            AST_ENABLE_ALWAYS表示始终启用
        """
        # 只在反编译窗口中启用
        widget = ida_kernwin.get_current_widget()
        if widget:
            widget_type = ida_kernwin.get_widget_type(widget)
            if widget_type == ida_kernwin.BWN_PSEUDOCODE:
                return ida_kernwin.AST_ENABLE_ALWAYS

        return ida_kernwin.AST_DISABLE
