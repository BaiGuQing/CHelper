# -*- coding: utf-8 -*-
"""
处理器 - 处理F6快捷键和执行优化流程
"""

import ida_kernwin
import ida_hexrays
import threading
import time

from .config import get_config
from .extractor import CodeExtractor
from .llm_client import LLMClient
from .processor import CodeProcessor
from .presenter import ResultPresenter, ProgressDialog
from .service_manager import get_service_manager
from .logger import get_logger
from .cache import get_cache


class OptimizationHandler:
    """优化处理器 - 核心业务逻辑"""

    def __init__(self):
        self.config = get_config()
        self.llm_client = LLMClient(self.config)
        self.logger = get_logger()
        self.cache = get_cache()
        self._busy_lock = threading.Lock()
        self._busy = False
        self._cancel_event = threading.Event()
        self._generation = 0

    def _ensure_service_ready(self) -> bool:
        """Check service state and show a useful message when it is unavailable."""
        svc = get_service_manager()
        if svc.is_ready() or svc.check_now():
            return True

        status = svc.get_status()
        hotkey = self.config.get("plugin.hotkey", "Ctrl+Alt+C")
        if status == "starting":
            elapsed = svc.get_elapsed()
            ResultPresenter.show_info(
                f"模型服务正在启动中（已等待 {elapsed} 秒），\n"
                f"模型加载到显存可能需要一段时间，\n"
                f"请稍后再按 {hotkey} 重试。"
            )
            return False
        if status == "failed":
            ResultPresenter.show_error(
                f"模型服务启动失败：{svc.get_error()}\n"
                f"请检查 config.json 的 auto_start/model_path 配置，\n"
                f"或手动启动模型服务后重试。"
            )
            return False

        # ``disabled`` means auto_start is off. Let the request proceed so a
        # manually managed endpoint can still be used; requests will report a
        # concrete connection error if it is actually unavailable.
        return True

    @staticmethod
    def _is_non_modal_model_rejection(message: str) -> bool:
        """Return True for expected bad-model output, not plugin failures."""
        markers = (
            "模型输出未通过安全校验",
            "模型未输出完整函数",
            "模型输出达到 max_tokens 限制",
            "模型没有返回可用的最终代码",
            "LLM优化失败",
            "模型未产生可安全应用的改动",
            "模型输出未通过保守改写校验",
        )
        return any(marker in (message or "") for marker in markers)

    def _report_failure(self, message: str):
        """Keep expected model rejections in IDA's Output window, not modals."""
        if self._is_non_modal_model_rejection(message):
            self.logger.warning(message)
            response_info = self.llm_client.get_last_response_info()
            if response_info:
                usage = response_info.get("usage") or {}
                self.logger.info(
                    "模型响应元数据: finish_reason=%s, completion_tokens=%s, "
                    "content=%s 字符, reasoning=%s 字符"
                    % (
                        response_info.get("finish_reason", "unknown"),
                        usage.get("completion_tokens", "unknown"),
                        response_info.get("content_chars", 0),
                        response_info.get("reasoning_chars", 0),
                    )
                )
            return
        ResultPresenter.show_error(message)
        self.logger.error(message)

    def _format_processing_failure(self, processing_error: str) -> str:
        """Avoid labelling an intentional safety rejection as a plugin error."""
        if self._is_non_modal_model_rejection(processing_error):
            return processing_error
        return f"代码处理失败: {processing_error}"

    def _get_quality_repair_attempts(self) -> int:
        """Read the bounded semantic-repair budget from configuration."""
        try:
            attempts = int(self.config.get("llm.quality_repair_attempts", 1))
        except (TypeError, ValueError):
            attempts = 1
        # This is deliberately separate from HTTP retries and must never turn
        # an unreliable model into an unbounded background loop.
        return min(max(attempts, 0), 3)

    def _apply_local_readability_fallback(self, pseudocode: str) -> tuple:
        """Return a verified local-only readability pass when it has value."""
        if not self.config.get("llm.local_readability_fallback", True):
            return "", {}

        locally_improved, metadata = CodeProcessor.apply_safe_local_readability(pseudocode)
        if not metadata or not CodeProcessor.has_meaningful_change(
            pseudocode, locally_improved
        ):
            return "", {}

        syntax_ok, _syntax_error = CodeProcessor.basic_syntax_check(locally_improved)
        semantic_ok, _semantic_error = CodeProcessor.validate_semantic_preservation(
            pseudocode,
            locally_improved,
            self.config.get("llm.minimum_output_ratio", 0.45),
        )
        if not syntax_ok or not semantic_ok:
            return "", {}
        return locally_improved, metadata

    def _fallback_to_local_readability(self, pseudocode: str, model_reason: str) -> tuple:
        """Use a local verified rename pass when the model gives no safe value."""
        local_code, local_metadata = self._apply_local_readability_fallback(pseudocode)
        if not local_code:
            return "", False, model_reason

        if model_reason:
            if model_reason == "自动修复未产生实质改动":
                reason_text = f"自动修复未产生可应用改动（{model_reason}）"
            else:
                reason_text = f"未采用模型输出（{model_reason}）"
            self.logger.warning(
                "%s，已应用本地安全美化（重命名 %s 个局部变量）"
                % (reason_text, local_metadata.get("renamed_locals", 0))
            )
        else:
            self.logger.info(
                "已应用本地安全美化（重命名 %s 个局部变量）"
                % local_metadata.get("renamed_locals", 0)
            )
        local_metadata = dict(local_metadata)
        local_metadata["result_kind"] = "local_readability_fallback"
        return local_code, True, {
            **local_metadata,
        }

    def _is_conservative_mode(self, pseudocode: str) -> bool:
        """Return the effective rewrite mode, with a safe test-double fallback."""
        checker = getattr(self.llm_client, "should_use_conservative", None)
        if callable(checker):
            return bool(checker(pseudocode))
        mode = str(self.config.get("llm.conservative_mode", "on") or "on").lower()
        return mode != "off"

    def _requires_model_service(self, pseudocode: str) -> bool:
        """Return False when conservative deterministic naming is sufficient."""
        if not self._is_conservative_mode(pseudocode):
            return True
        if not self.config.get("llm.conservative_model_renames", True):
            return False
        deterministic = CodeProcessor.build_safe_local_rename_mapping(pseudocode)
        return bool(CodeProcessor.get_conservative_rename_candidates(
            pseudocode, excluded=deterministic
        )[:24])

    def _process_conservative_rename_plan(
        self, pseudocode: str, context: dict, cancel_event=None
    ) -> tuple:
        """Apply a small-model rename plan without letting it regenerate C."""
        deterministic = CodeProcessor.build_safe_local_rename_mapping(pseudocode)
        candidates = CodeProcessor.get_conservative_rename_candidates(
            pseudocode, excluded=deterministic
        )[:24]
        proposed = {}
        model_error = ""
        use_model = self.config.get("llm.conservative_model_renames", True)

        if candidates and use_model:
            proposed = self.llm_client.suggest_conservative_renames(
                pseudocode,
                context,
                candidates,
                cancel_event=cancel_event,
            )
            if proposed is None:
                proposed = {}
                model_error = (
                    self.llm_client.get_last_error()
                    or "模型未返回可用的局部变量命名建议"
                )

        improved, metadata = CodeProcessor.apply_conservative_rename_plan(
            pseudocode, proposed, allowed_model_sources=candidates
        )
        mapping = metadata.get("rename_mapping", {})
        if not mapping or not CodeProcessor.has_meaningful_change(
            pseudocode, improved
        ):
            return "", False, (
                model_error or "未发现可安全应用的局部变量重命名"
            )

        rename_valid, rename_error, confirmed = (
            CodeProcessor.validate_local_rename_only(pseudocode, improved)
        )
        syntax_ok, syntax_error = CodeProcessor.basic_syntax_check(improved)
        semantic_ok, semantic_error = CodeProcessor.validate_semantic_preservation(
            pseudocode,
            improved,
            self.config.get("llm.minimum_output_ratio", 0.45),
        )
        if not rename_valid or not syntax_ok or not semantic_ok:
            reason = rename_error or syntax_error or semantic_error
            return "", False, f"保守重命名本地校验失败: {reason}"

        model_mapping = metadata.get("model_mapping", {})
        deterministic_mapping = metadata.get("deterministic_mapping", {})
        rejected = metadata.get("rejected_model_mapping", {})
        if rejected:
            self.logger.info(
                "已忽略 %s 个不安全或无效的模型命名建议" % len(rejected)
            )
        if model_error and deterministic_mapping:
            self.logger.warning(
                f"{model_error}，已直接应用本地确定性命名"
            )

        result_kind = (
            "model_local_rename" if model_mapping
            else "local_readability_fallback"
        )
        return improved, True, {
            "result_kind": result_kind,
            "renamed_locals": len(confirmed),
            "rename_mapping": confirmed,
            "model_renamed_locals": len(model_mapping),
            "deterministic_renamed_locals": len(deterministic_mapping),
        }

    def _get_model_result_metadata(self, pseudocode: str, processed_code: str):
        """Describe a validated full rewrite for presentation and caching."""
        rename_valid, rename_error, rename_metadata = (
            CodeProcessor.validate_local_rename_only(pseudocode, processed_code)
        )
        # Full rewrites may also rename locals, but token-level mapping is not
        # reliable after a control-flow or expression transformation.
        return {
            "result_kind": "model_full_rewrite",
            "renamed_locals": len(rename_metadata) if rename_valid else 0,
            "rename_mapping": rename_metadata if rename_valid else {},
        }, ""

    @staticmethod
    def _format_model_validation_failure(rename_error: str, conservative=False) -> str:
        return (
            "模型输出未通过全量改写校验: "
            f"{rename_error}。原始伪代码未被替换，结果未缓存"
        )

    def _process_model_candidate(
        self, pseudocode: str, context: dict, candidate: str, cancel_event=None
    ) -> tuple:
        """Validate one model output, with at most one bounded semantic repair.

        A repair is only warranted after the semantic guard rejects a complete
        model answer.  It is not used for connection errors, token truncation,
        malformed fragments, or syntax failures.  Every repaired candidate
        goes through the same processor and quality guard again.
        """
        processed_code, success, processing_error = CodeProcessor.process(
            candidate, pseudocode, self.config
        )
        if success:
            result_metadata, rename_error = self._get_model_result_metadata(
                pseudocode, processed_code
            )
            if result_metadata is None:
                return "", False, (
                    self._format_model_validation_failure(
                        rename_error
                    )
                )
            if not CodeProcessor.has_meaningful_change(pseudocode, processed_code):
                return self._fallback_to_local_readability(
                    pseudocode, "模型未产生实质改动"
                )
            return processed_code, True, result_metadata

        if not CodeProcessor.is_quality_rejection(processing_error):
            return self._fallback_to_local_readability(pseudocode, processing_error)

        attempts = self._get_quality_repair_attempts()
        required_anchors = CodeProcessor.get_required_semantic_anchors(
            pseudocode,
            self.config.get("llm.quality_guard_profile", "balanced"),
        )
        repair_label = "自动全量修复"
        for attempt in range(1, attempts + 1):
            if cancel_event is not None and cancel_event.is_set():
                return "", False, "请求已取消"
            violation = CodeProcessor.get_quality_rejection_reason(processing_error)
            self.logger.info(
                "模型输出未通过安全校验（%s），正在进行%s（%s/%s）"
                % (violation or "未保留原始语义", repair_label, attempt, attempts)
            )
            if cancel_event is None:
                repaired_code = self.llm_client.repair_code(
                    pseudocode, context, violation, required_anchors
                )
            else:
                repaired_code = self.llm_client.repair_code(
                    pseudocode,
                    context,
                    violation,
                    required_anchors,
                    cancel_event=cancel_event,
                )
            if repaired_code is None:
                return "", False, (
                    self.llm_client.get_last_error()
                    or "自动安全修复未返回可用代码"
                )

            processed_code, success, processing_error = CodeProcessor.process(
                repaired_code, pseudocode, self.config
            )
            if success:
                result_metadata, rename_error = self._get_model_result_metadata(
                    pseudocode, processed_code
                )
                if result_metadata is None:
                    return "", False, (
                        self._format_model_validation_failure(
                            rename_error
                        )
                    )
                if not CodeProcessor.has_meaningful_change(pseudocode, processed_code):
                    return self._fallback_to_local_readability(
                        pseudocode, "自动修复未产生实质改动"
                    )
                self.logger.info(f"{repair_label}通过安全校验")
                return processed_code, True, result_metadata
            if not CodeProcessor.is_quality_rejection(processing_error):
                return self._fallback_to_local_readability(pseudocode, processing_error)

        return self._fallback_to_local_readability(pseudocode, processing_error)

    @staticmethod
    def _accepted_model_rewrite(success: bool, result_metadata) -> bool:
        """Return whether the model candidate (or its repair) passed validation."""
        return bool(
            success
            and isinstance(result_metadata, dict)
            and result_metadata.get("result_kind") == "model_full_rewrite"
        )

    def _show_rejected_candidate(
        self, pseudocode: str, candidate: str, context: dict
    ) -> bool:
        """Show a rejected full-rewrite candidate without caching it."""
        if not self.config.get("llm.show_rejected_candidate", False):
            return False
        if not candidate or not candidate.strip():
            return False

        display_context = dict(context or {})
        display_context["result_kind"] = "model_rejected_candidate"
        display_context["renamed_locals"] = 0
        stats = CodeProcessor.calculate_diff_stats(pseudocode, candidate)
        shown = ResultPresenter.show_optimized_window(
            candidate, display_context, stats
        )
        if shown:
            ResultPresenter.print_to_output(
                "已展示未通过安全流程的模型候选（不缓存）"
            )
        else:
            self.logger.warning("显示被拒绝的模型候选失败")
        return shown

    def _publish_result(
        self,
        pseudocode: str,
        processed_code: str,
        context: dict,
        address: str,
        function_name: str,
        cache_metadata: dict,
        cache_hit: bool = False,
        result_metadata: dict = None,
    ) -> bool:
        """Publish a validated result on the IDA/UI thread and cache it."""
        stats = CodeProcessor.calculate_diff_stats(pseudocode, processed_code)
        result_metadata = result_metadata or {}
        if not result_metadata:
            rename_valid, _rename_error, inferred_mapping = (
                CodeProcessor.validate_local_rename_only(pseudocode, processed_code)
            )
            if rename_valid and inferred_mapping:
                result_metadata = {
                    "result_kind": "model_local_rename",
                    "renamed_locals": len(inferred_mapping),
                    "rename_mapping": inferred_mapping,
                }
        result_kind = result_metadata.get("result_kind", "model_local_rename")
        renamed_locals = int(result_metadata.get("renamed_locals", 0) or 0)
        stats["result_kind"] = result_kind
        stats["renamed_locals"] = renamed_locals

        if not CodeProcessor.has_meaningful_change(pseudocode, processed_code):
            message = "模型未产生可安全应用的改动，已保留原始伪代码"
            self.logger.warning(message)
            ResultPresenter.print_to_output(message)
            return False

        display_context = dict(context or {})
        display_context["result_kind"] = result_kind
        display_context["renamed_locals"] = renamed_locals
        # Carry Hex-Rays' native token colors over to renamed locals. Cached
        # results do not persist this map, so infer the mapping again when
        # necessary from the already validated source/result pair.
        semantic_colors = dict(display_context.get("semantic_colors") or {})
        rename_mapping = result_metadata.get("rename_mapping") or {}
        if not rename_mapping:
            try:
                _valid, _error, rename_mapping = (
                    CodeProcessor.validate_local_rename_only(
                        pseudocode, processed_code
                    )
                )
            except Exception:
                rename_mapping = {}
        for source_name, renamed_name in rename_mapping.items():
            source_color = semantic_colors.get(source_name)
            if source_color and isinstance(renamed_name, str):
                semantic_colors.setdefault(renamed_name, source_color)
        if semantic_colors:
            display_context["semantic_colors"] = semantic_colors
        if not ResultPresenter.show_optimized_window(processed_code, display_context, stats):
            return False

        # Only persist a newly generated result after the viewer has accepted
        # it.  Otherwise a transient IDA viewer failure would turn into a cache
        # hit on the next invocation even though the user never saw the result.
        if not cache_hit and self.cache and self.config.get("plugin.enable_cache", True):
            try:
                self.cache.set(
                    address,
                    pseudocode,
                    processed_code,
                    function_name,
                    stats,
                    cache_metadata,
                )
                self.logger.debug(f"已缓存优化结果: {function_name}")
            except Exception as exc:
                self.logger.warning(f"缓存保存失败: {exc}")

        cache_suffix = " (缓存)" if cache_hit else ""
        source_label = (
            "本地安全美化"
            if result_kind == "local_readability_fallback"
            else "模型全量重写"
            if result_kind == "model_full_rewrite"
            else "模型辅助命名"
        )
        rename_suffix = f"，重命名 {renamed_locals} 个局部变量" if renamed_locals else ""
        result_msg = (
            f"{source_label}完成{cache_suffix}: {stats['original_lines']} -> {stats['optimized_lines']} 行, "
            f"{stats['original_chars']} -> {stats['optimized_chars']} 字符{rename_suffix}"
        )
        ResultPresenter.print_to_output(result_msg)
        self.logger.info(result_msg)
        return True

    def _get_cached_result_metadata(self, pseudocode: str, cached: dict) -> dict:
        """Recover display metadata from a cache entry without trusting old no-ops."""
        cached_code = (cached or {}).get("optimized_code", "")
        if not cached_code or not CodeProcessor.has_meaningful_change(
            pseudocode, cached_code
        ):
            return {}
        stats = (cached or {}).get("stats") or {}
        result_kind = stats.get("result_kind", "model_local_rename")
        renamed_locals = stats.get("renamed_locals", 0)
        return {
            "result_kind": result_kind,
            "renamed_locals": renamed_locals,
        }

    @staticmethod
    def _is_usable_cached_result(pseudocode: str, cached: dict) -> bool:
        """Do not reuse pre-v4 whitespace-only cache entries."""
        cached_code = (cached or {}).get("optimized_code", "")
        return bool(cached_code and CodeProcessor.has_meaningful_change(
            pseudocode, cached_code
        ))

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
                cache_metadata = {
                    "function_name": context.get("function_name", ""),
                    "function_type": context.get("function_type", ""),
                    "types": context.get("types", ""),
                }
                self.logger.info(f"正在优化函数: {function_name} @ {address}")
                ResultPresenter.print_to_output(f"正在优化函数: {function_name}")

                # 2. 检查缓存
                processed_code = None
                result_metadata = {}
                cache_hit = False
                if self.cache and self.config.get("plugin.enable_cache", True) and not force_refresh:
                    progress.update("CHelper - 检查缓存...")
                    cached = self.cache.get(address, pseudocode, cache_metadata)
                    if self._is_usable_cached_result(pseudocode, cached):
                        processed_code = cached.get("optimized_code")
                        if processed_code:
                            result_metadata = self._get_cached_result_metadata(
                                pseudocode, cached
                            )
                            cache_hit = True
                            self.logger.info(f"缓存命中: {function_name}")
                            ResultPresenter.print_to_output(f"缓存命中，跳过 LLM 调用")
                elif force_refresh:
                    self.logger.info("强制刷新模式，跳过缓存检查")

                # 3. 调用LLM优化（未命中缓存时）
                if not cache_hit:
                    if self._requires_model_service(pseudocode):
                        if not self._ensure_service_ready():
                            return False
                        progress.update("CHelper - 正在调用大模型优化...")
                    else:
                        progress.update("CHelper - 正在应用本地确定性命名...")

                    # 检查是否可以取消
                    if progress.check_cancelled():
                        self.logger.info("用户取消优化")
                        return False

                    if self._is_conservative_mode(pseudocode):
                        processed_code, success, result_metadata = (
                            self._process_conservative_rename_plan(
                                pseudocode, context
                            )
                        )
                    else:
                        optimized_code = self.llm_client.optimize_code(
                            pseudocode, context
                        )
                        if optimized_code is None:
                            model_error = (
                                self.llm_client.get_last_error()
                                or "LLM优化失败，请检查配置和模型服务"
                            )
                            processed_code, success, result_metadata = (
                                self._fallback_to_local_readability(
                                    pseudocode, model_error
                                )
                            )
                        else:
                            # 4. 后处理
                            progress.update("CHelper - 正在处理结果...")

                            # 再次检查取消
                            if progress.check_cancelled():
                                self.logger.info("用户取消优化")
                                return False

                            processed_code, success, result_metadata = self._process_model_candidate(
                                pseudocode, context, optimized_code
                            )
                            if not self._accepted_model_rewrite(
                                success, result_metadata
                            ):
                                self._show_rejected_candidate(
                                    pseudocode, optimized_code, context
                                )

                    if not success:
                        error_msg = self._format_processing_failure(result_metadata)
                        self._report_failure(error_msg)
                        # Do not display or cache an output that failed the
                        # structural checks. The original IDA code remains
                        # available in the pseudocode view.
                        return False

                    if not processed_code or not processed_code.strip():
                        ResultPresenter.show_error("代码处理失败：结果为空")
                        self.logger.error("代码处理失败：结果为空")
                        return False

            return self._publish_result(
                pseudocode,
                processed_code,
                context,
                address,
                function_name,
                cache_metadata,
                cache_hit,
                result_metadata,
            )

        except Exception as e:
            error_msg = f"执行过程中出错: {str(e)}"
            ResultPresenter.show_error(error_msg)
            self.logger.exception(error_msg)
            return False

    def execute_async(self, force_refresh: bool = False) -> bool:
        """Start optimization without blocking IDA's main/UI thread.

        Extraction and presentation stay on the IDA thread. Only the network
        request and pure-Python post-processing run in the worker thread; the
        completion callback is queued back through ``execute_ui_requests``.
        """
        can_exec, error_msg = self.can_execute()
        if not can_exec:
            ResultPresenter.show_error(error_msg)
            return False

        with self._busy_lock:
            if self._busy:
                self.logger.info("CHelper 已有任务在处理中，本次快捷键已忽略。")
                return False
            self._busy = True
            self._cancel_event.clear()
            self._generation += 1
            generation = self._generation

        started = False
        try:
            pseudocode, context, _cfunc = CodeExtractor.get_current_pseudocode()
            if pseudocode is None or context is None:
                ResultPresenter.show_error("提取伪C代码失败")
                return False

            max_size = self.config.get("plugin.max_function_size", 10000)
            if not CodeExtractor.validate_pseudocode_size(pseudocode, max_size):
                ResultPresenter.show_error(
                    f"函数代码过大 ({len(pseudocode)} 字符)，超过限制 ({max_size} 字符)"
                )
                return False

            function_name = context.get("function_name", "unknown")
            address = context.get("address", "")
            cache_metadata = {
                "function_name": context.get("function_name", ""),
                "function_type": context.get("function_type", ""),
                "types": context.get("types", ""),
            }

            if self.cache and self.config.get("plugin.enable_cache", True) and not force_refresh:
                cached = self.cache.get(address, pseudocode, cache_metadata)
                if self._is_usable_cached_result(pseudocode, cached):
                    result = self._publish_result(
                        pseudocode,
                        cached["optimized_code"],
                        context,
                        address,
                        function_name,
                        cache_metadata,
                        cache_hit=True,
                        result_metadata=self._get_cached_result_metadata(
                            pseudocode, cached
                        ),
                    )
                    return result

            requires_service = self._requires_model_service(pseudocode)
            if not requires_service:
                ResultPresenter.print_to_output(f"正在优化函数: {function_name}")
                ResultPresenter.print_to_output("正在应用本地确定性命名...")
                target = self._async_worker
                worker = threading.Thread(
                    target=target,
                    args=(pseudocode, context, address, function_name, cache_metadata, generation),
                    name="CHelper-Optimization",
                    daemon=True,
                )
                worker.start()
                started = True
                return True

            # Do not discard the user's first request while the background
            # service loader is still warming up.  Cache hits above work even
            # before the model is ready; uncached requests are queued below.
            svc = get_service_manager()
            service_ready = svc.is_ready()
            if not service_ready and svc.get_status() == "idle":
                service_ready = svc.check_now()
            service_status = svc.get_status()
            if not service_ready and service_status == "failed":
                ResultPresenter.show_error(f"模型服务启动失败：{svc.get_error()}")
                return False
            if not service_ready and service_status == "disabled":
                ResultPresenter.show_error(
                    "模型服务未启动。请启动服务后重试，或在 config.json 中开启 auto_start。"
                )
                return False

            ResultPresenter.print_to_output(f"正在优化函数: {function_name}")
            if service_ready:
                ResultPresenter.print_to_output("正在调用大模型优化...")
                target = self._async_worker
            else:
                elapsed = svc.get_elapsed()
                ResultPresenter.print_to_output(
                    f"模型服务启动中（已等待 {elapsed} 秒），已排队并会在就绪后自动开始。"
                )
                target = self._wait_for_service_then_optimize
            worker = threading.Thread(
                target=target,
                args=(pseudocode, context, address, function_name, cache_metadata, generation),
                name="CHelper-Optimization",
                daemon=True,
            )
            worker.start()
            started = True
            return True
        except Exception as exc:
            self._set_busy(False)
            ResultPresenter.show_error(f"启动异步优化失败: {exc}")
            self.logger.exception(f"启动异步优化失败: {exc}")
            return False
        finally:
            # The worker owns the busy state after it has been started.
            if not started:
                self._set_busy(False)

    def _set_busy(self, value: bool):
        with self._busy_lock:
            self._busy = value

    def cancel(self):
        """Request cancellation of the active background optimization."""
        with self._busy_lock:
            if not self._busy:
                return False
            self._cancel_event.set()
            self._generation += 1
        ResultPresenter.print_to_output("优化任务已请求取消")
        self.logger.info("CHelper 优化任务已请求取消")
        return True

    def shutdown(self):
        """Invalidate pending work before the plugin is unloaded."""
        with self._busy_lock:
            self._cancel_event.set()
            self._generation += 1

    def _is_cancelled(self, generation=None):
        with self._busy_lock:
            return self._cancel_event.is_set() or (
                generation is not None and generation != self._generation
            )

    def _wait_for_service_then_optimize(
        self, pseudocode, context, address, function_name, cache_metadata, generation
    ):
        """Wait off the UI thread, then run the normal optimization worker."""
        svc = get_service_manager()
        try:
            timeout = max(1, int(self.config.get("llm.startup_timeout", 180)))
        except (TypeError, ValueError):
            timeout = 180
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self._is_cancelled(generation):
                self._set_busy(False)
                return
            if svc.is_ready() or svc.check_now():
                self._async_worker(
                    pseudocode, context, address, function_name, cache_metadata, generation
                )
                return

            status = svc.get_status()
            if status == "failed":
                self._queue_async_completion(
                    pseudocode, context, address, function_name, cache_metadata,
                    None, f"模型服务启动失败：{svc.get_error()}",
                    generation=generation,
                )
                return
            if status == "disabled":
                self._queue_async_completion(
                    pseudocode, context, address, function_name, cache_metadata,
                    None, "模型服务未启动，请启动服务后重试。",
                    generation=generation,
                )
                return
            self._cancel_event.wait(1.0)

        self._queue_async_completion(
            pseudocode, context, address, function_name, cache_metadata,
            None, f"等待模型服务就绪超时（{timeout} 秒）",
            generation=generation,
        )

    def _async_worker(self, pseudocode, context, address, function_name, cache_metadata, generation):
        """Run network/post-processing work off the IDA UI thread."""
        rejected_candidate = None
        try:
            if self._is_cancelled(generation):
                return
            if self._is_conservative_mode(pseudocode):
                processed_code, success, result_metadata = (
                    self._process_conservative_rename_plan(
                        pseudocode, context, cancel_event=self._cancel_event
                    )
                )
            else:
                optimized_code = self.llm_client.optimize_code(
                    pseudocode, context, cancel_event=self._cancel_event
                )
                if optimized_code is None:
                    model_error = (
                        self.llm_client.get_last_error()
                        or "LLM优化失败，请检查配置和模型服务"
                    )
                    processed_code, success, result_metadata = self._fallback_to_local_readability(
                        pseudocode, model_error
                    )
                else:
                    processed_code, success, result_metadata = self._process_model_candidate(
                        pseudocode, context, optimized_code, cancel_event=self._cancel_event
                    )
                    if (
                        self.config.get("llm.show_rejected_candidate", False)
                        and not self._accepted_model_rewrite(success, result_metadata)
                    ):
                        rejected_candidate = optimized_code
            if self._is_cancelled(generation):
                return
            if not success:
                error_msg = self._format_processing_failure(result_metadata)
                self._queue_async_completion(
                    pseudocode, context, address, function_name, cache_metadata,
                    None, error_msg, generation=generation,
                    rejected_candidate=rejected_candidate,
                )
                return
            if not processed_code or not processed_code.strip():
                self._queue_async_completion(
                    pseudocode, context, address, function_name, cache_metadata,
                    None, "代码处理失败：结果为空", generation=generation,
                    rejected_candidate=rejected_candidate,
                )
                return

            self._queue_async_completion(
                pseudocode, context, address, function_name, cache_metadata,
                processed_code, "", result_metadata, generation,
                rejected_candidate=rejected_candidate,
            )
        except Exception as exc:
            self._queue_async_completion(
                pseudocode, context, address, function_name, cache_metadata,
                None, f"异步优化失败: {exc}", generation=generation
            )
        finally:
            if self._is_cancelled(generation):
                self._set_busy(False)

    def _queue_async_completion(
        self,
        pseudocode,
        context,
        address,
        function_name,
        cache_metadata,
        processed_code,
        error_message,
        result_metadata=None,
        generation=None,
        rejected_candidate=None,
    ):
        def complete_on_ui():
            try:
                if self._is_cancelled(generation):
                    return False
                if rejected_candidate:
                    self._show_rejected_candidate(
                        pseudocode, rejected_candidate, context
                    )
                if error_message:
                    self._report_failure(error_message)
                    return False
                if not self._publish_result(
                    pseudocode,
                    processed_code,
                    context,
                    address,
                    function_name,
                    cache_metadata,
                    result_metadata=result_metadata,
                ):
                    self.logger.error("显示异步优化结果失败")
            finally:
                self._set_busy(False)
            return False

        try:
            ida_kernwin.execute_ui_requests([complete_on_ui])
        except Exception as exc:
            self.logger.exception(f"提交异步完成回调失败: {exc}")
            self._set_busy(False)


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
        self.handler.execute_async(force_refresh=self.force_refresh)
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
