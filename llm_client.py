# -*- coding: utf-8 -*-
"""
LLM客户端 - 与本地大模型通信
"""

import json
import ast
import re
import requests
import time
from email.utils import parsedate_to_datetime
from typing import Optional

from .constants import (
    HEAVY_OBF_PATTERN_THRESHOLD,
    MAX_RETRY_ATTEMPTS,
    INITIAL_RETRY_DELAY,
    RETRY_DELAY_MULTIPLIER,
    MAX_RETRY_DELAY,
    OLLVM_MAGIC_PATTERNS,
    PROMPT_VERSION as PROMPT_PROTOCOL_VERSION,
)
from .logger import get_logger


class LLMClient:
    """本地大模型客户端，支持OpenAI兼容的API"""

    # Bump this whenever prompt semantics change; cache.py includes it in its
    # namespace so an old, less-safe prompt result is never silently reused.
    PROMPT_VERSION = PROMPT_PROTOCOL_VERSION

    @staticmethod
    def _normalize_chat_completions_url(api_url: str) -> str:
        """Accept either an OpenAI base URL or a full chat endpoint."""
        url = str(api_url or "").strip().rstrip("/")
        if not url:
            return ""
        if url.endswith("/chat/completions"):
            return url
        if url.endswith("/v1"):
            return f"{url}/chat/completions"
        return f"{url}/v1/chat/completions"

    @staticmethod
    def _models_url_from_api_url(api_url: str) -> str:
        """Build /models while preserving any configured proxy path prefix."""
        from urllib.parse import urlparse

        parsed = urlparse(str(api_url or "").strip())
        path = (parsed.path or "").rstrip("/")
        suffix = "/chat/completions"
        if path.endswith(suffix):
            path = path[:-len(suffix)]
        elif not path.endswith("/v1"):
            path = f"{path}/v1" if path else "/v1"
        return parsed._replace(
            path=f"{path}/models", params="", query="", fragment=""
        ).geturl()

    def __init__(self, config):
        """初始化LLM客户端

        Args:
            config: Config实例
        """
        self.config = config
        self.api_url = self._normalize_chat_completions_url(
            config.get("llm.api_url")
        )
        self.model = config.get("llm.model")
        self.temperature = config.get("llm.temperature", 0.1)
        self.max_tokens = config.get("llm.max_tokens", 4096)
        self.timeout = config.get("llm.timeout", 120)
        self.api_key = config.get("llm.api_key", "")
        # High frequency/presence penalties are counterproductive for C: they
        # discourage the model from repeating variable names and API calls.
        self.repeat_penalty = config.get("llm.repeat_penalty", 1.05)
        self.frequency_penalty = config.get("llm.frequency_penalty", 0.0)
        self.presence_penalty = config.get("llm.presence_penalty", 0.0)
        self.send_extended_parameters = config.get("llm.send_extended_parameters", True)
        self.conservative_mode = config.get("llm.conservative_mode", "auto")
        self.top_p = config.get("llm.top_p", 0.95)
        self.top_k = config.get("llm.top_k", 40)
        self.min_p = config.get("llm.min_p", 0.0)
        self.seed = config.get("llm.seed", None)
        self.reasoning_effort = config.get("llm.reasoning_effort", None)
        self.last_error = ""
        self.last_response_info = {}
        try:
            configured_attempts = int(config.get("llm.max_retry_attempts", MAX_RETRY_ATTEMPTS))
        except (TypeError, ValueError):
            configured_attempts = MAX_RETRY_ATTEMPTS
        self.max_retry_attempts = max(1, configured_attempts)
        try:
            self.retry_delay = float(config.get("llm.retry_delay", INITIAL_RETRY_DELAY))
        except (TypeError, ValueError):
            self.retry_delay = INITIAL_RETRY_DELAY
        self.logger = get_logger()

        # 预编译 OLLVM 混淆检测正则
        self._heavy_obf_patterns = [re.compile(p) for p in OLLVM_MAGIC_PATTERNS]

    def _is_heavy_obfuscation(self, pseudocode: str) -> bool:
        """检测是否为重度混淆（小模型强行重建会失败）"""
        hits = 0
        for pat in self._heavy_obf_patterns:
            if pat.search(pseudocode):
                hits += 1
        # 命中 2 类以上，或出现魔数除法 idiom，判定为硬骨头
        return hits >= HEAVY_OBF_PATTERN_THRESHOLD or bool(re.search(r'0x[Cc]{16}', pseudocode, re.IGNORECASE))

    def _should_use_conservative(self, pseudocode: str) -> bool:
        mode = (self.conservative_mode or "auto").lower()
        if mode == "on":
            return True
        if mode == "off":
            return False
        # auto
        return self._is_heavy_obfuscation(pseudocode)

    def should_use_conservative(self, pseudocode: str) -> bool:
        """Expose the effective rewrite mode to the result validator."""
        return self._should_use_conservative(pseudocode)

    def _effective_quality_profile(self) -> str:
        """Return off when the master quality switch is disabled."""
        if not self.config.get("llm.quality_guard", True):
            return "off"
        profile = str(
            self.config.get("llm.quality_guard_profile", "balanced")
            or "balanced"
        ).lower()
        return profile if profile in ("strict", "balanced", "globals_only") else "balanced"

    def _apply_penalties(self, payload: dict):
        """向请求 payload 注入重复退化抑制参数

        llama-server 同时支持 llama.cpp 原生的 repeat_penalty 和 OpenAI 风格的
        frequency_penalty / presence_penalty。三者叠加可有效抑制小模型
        生成重复行（如 vN = vN+1 ^ vN+2 死循环）的退化现象。
        值为 0/None 时跳过，避免覆盖后端默认。
        """
        if self.send_extended_parameters:
            if self.repeat_penalty and self.repeat_penalty > 0:
                payload["repeat_penalty"] = self.repeat_penalty
            if self.frequency_penalty and self.frequency_penalty != 0:
                payload["frequency_penalty"] = self.frequency_penalty
            if self.presence_penalty and self.presence_penalty != 0:
                payload["presence_penalty"] = self.presence_penalty
        if self.top_p and self.top_p > 0:
            payload["top_p"] = self.top_p
        if self.send_extended_parameters:
            if self.top_k and self.top_k > 0:
                payload["top_k"] = self.top_k
            if self.min_p and self.min_p > 0:
                payload["min_p"] = self.min_p
        # OpenAI and llama.cpp both accept an optional seed.  Leaving it null
        # preserves backend defaults; setting one makes a bad output replayable.
        if isinstance(self.seed, int) and not isinstance(self.seed, bool):
            payload["seed"] = self.seed

    def _build_prompt(self, pseudocode: str, context: dict) -> str:
        """Build the full-rewrite prompt.

        Conservative mode has a separate structured rename-plan API and must
        never reach this complete-function generation path.
        """
        return self._build_full_prompt(pseudocode, context)

    @staticmethod
    def _get_source_signature(pseudocode: str) -> str:
        """Return the decompiler's visible declaration as the sole authority."""
        for line in (pseudocode or "").splitlines():
            stripped = line.strip()
            if stripped and "(" in stripped and not stripped.startswith("//"):
                return stripped
        return ""

    @staticmethod
    def _build_messages(
        prompt: str, conservative: bool = True, system_content: str = None
    ) -> list:
        """Build a role-separated request supported by OpenAI-compatible APIs."""
        if system_content is not None:
            pass
        elif conservative:
            system_content = (
                "You are a careful reverse engineer. Return only one complete C "
                "function. Never invent behavior. Preserve every token except "
                "the explicitly requested local-variable renames and comments."
            )
        else:
            system_content = (
                "You are a careful reverse engineer. Return only one complete C "
                "function. You may safely improve expressions, control flow, and "
                "local names, but never invent behavior or drop verified semantic "
                "anchors from the source."
            )
        return [
            {
                "role": "system",
                "content": system_content,
            },
            {"role": "user", "content": prompt},
        ]

    @staticmethod
    def _parse_rename_plan(content: str) -> Optional[dict]:
        """Parse a small-model rename reply with light envelope tolerance."""
        if not content:
            return None
        text = content.strip()
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)

        decoded = None
        decoder = json.JSONDecoder()
        for index, char in enumerate(text):
            if char not in "{[":
                continue
            try:
                decoded, _end = decoder.raw_decode(text[index:])
                break
            except json.JSONDecodeError:
                continue
        if decoded is None:
            # Some small models emit a Python-style dict with single quotes.
            # literal_eval keeps this tolerance local and does not execute code.
            starts = [index for index, char in enumerate(text) if char in "{["]
            for index in starts:
                for end in range(len(text), index, -1):
                    try:
                        decoded = ast.literal_eval(text[index:end])
                        break
                    except (SyntaxError, ValueError):
                        continue
                if decoded is not None:
                    break
        if decoded is None:
            return None

        if isinstance(decoded, dict):
            renames = decoded.get("renames", decoded)
        else:
            renames = decoded

        if isinstance(renames, list):
            items = renames
            renames = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                source = item.get("from") or item.get("source")
                target = item.get("to") or item.get("target")
                if isinstance(source, str) and isinstance(target, str):
                    renames[source] = target

        if not isinstance(renames, dict):
            return None
        return {
            str(source): target
            for source, target in renames.items()
            if isinstance(source, str) and isinstance(target, str)
        }

    def _build_conservative_rename_prompt(
        self, pseudocode: str, context: dict, candidates: list
    ) -> str:
        """Build a short constrained naming task suitable for small models."""
        candidate_json = json.dumps(candidates, ensure_ascii=False)
        function_name = context.get("function_name", "") if context else ""
        naming_context = self._compact_rename_context(pseudocode, candidates)
        return (
            "为 IDA 伪代码中的占位局部变量建议简短、保守的英文名称。\n"
            "你不需要重写代码，也不要解释代码。\n"
            f"函数名: {function_name or 'unknown'}\n"
            f"只允许重命名这些变量: {candidate_json}\n\n"
            "规则:\n"
            "1. 只输出一行 JSON，格式必须是: "
            '{"renames":{"v1":"meaningful_name"}}\n'
            "2. JSON 中的键必须来自允许列表；不确定的变量直接省略。\n"
            "3. 新名称必须是合法 C 标识符，使用 snake_case，不要与函数、类型或 API 同名。\n"
            "4. 不要输出注释、Markdown、代码或额外字段。没有可靠建议时输出 "
            '{"renames":{}}。\n\n'
            f"IDA 伪代码上下文:\n{naming_context}\n"
        )

    @staticmethod
    def _compact_rename_context(
        pseudocode: str, candidates: list, max_chars: int = 5000
    ) -> str:
        """Keep small functions intact and excerpt only candidate uses in large ones."""
        source = pseudocode or ""
        if len(source) <= max_chars or not candidates:
            return source
        lines = source.splitlines()
        pattern = re.compile(
            r"\b(?:" + "|".join(re.escape(name) for name in candidates) + r")\b"
        )
        selected = set(range(min(3, len(lines))))
        for index, line in enumerate(lines):
            if pattern.search(line):
                selected.update(range(max(0, index - 2), min(len(lines), index + 3)))

        rendered = []
        previous = None
        for index in sorted(selected):
            if previous is not None and index > previous + 1:
                rendered.append("// ...")
            rendered.append(lines[index])
            previous = index
        compact = "\n".join(rendered)
        return compact[:max_chars]

    def _build_full_prompt(self, pseudocode: str, context: dict) -> str:
        optimization_flags = self.config.get("optimization", {})
        source_signature = self._get_source_signature(pseudocode)
        quality_profile = self._effective_quality_profile()

        if quality_profile == "off":
            invariant_rule = (
                "2. 当前未启用安全检测；除必须输出一个 C 函数外，"
                "不设置签名、调用、字符串、常量或全局符号不变约束。\n"
            )
            rewrite_rule = (
                "3. 可以重写函数的任意内容，包括调用、字符串、数值、"
                "全局符号引用、表达式、控制流和局部变量。\n"
            )
        elif quality_profile == "globals_only":
            invariant_rule = (
                "2. 函数签名和参数类型必须保留；原文中所有 IDA 全局符号"
                "（如 g_*/byte_*/dword_*/qword_*）必须逐字保留，不得删除、"
                "改名或新增。\n"
            )
            rewrite_rule = (
                "3. 除上述不变项外，可以重写调用组织、字符串、数值、"
                "表达式、循环、分支和局部变量。\n"
            )
        else:
            invariant_rule = (
                "2. 函数签名和参数类型必须保留；不得凭空引入外部调用或全局符号。\n"
            )
            rewrite_rule = (
                "3. 可以重写表达式、循环、分支和局部变量，删除能够证明冗余的代码。\n"
            )

        prompt_parts = [
            "你是专业的逆向工程专家。将以下 IDA 伪 C 做安全的全量可读性重写。\n",
            "【输出要求】\n",
            "1. 只输出一个完整 C 函数；不要解释、Markdown 或第二个答案。\n",
            invariant_rule,
            rewrite_rule,
            "4. 重写循环/分支时必须保持边界、条件、返回值、副作用和必要类型转换等价。\n",
            "5. 不确定时保留原始逻辑；绝不能猜测或虚构行为。\n",
            "\n可选优化目标：\n",
        ]

        if optimization_flags.get("rewrite_control_flow"):
            prompt_parts.append(
                "- 主动重写循环和分支：可将指针循环改成索引循环、合并等价分支、"
                "整理提前返回，但必须重新核对原始边界、终止条件和副作用。\n"
            )
        else:
            prompt_parts.append(
                "- 保持原有控制流、循环边界、指针哨兵和短路求值关系不变；"
                "不要将指针循环改写成索引循环。\n"
            )

        # Asking a small model to deobfuscate every ordinary XOR or branch is
        # a strong hallucination trigger.  Only issue that instruction when a
        # concrete OLLVM pattern was actually detected.
        if optimization_flags.get("deobfuscate_ollvm") and self._is_heavy_obfuscation(pseudocode):
            prompt_parts.append("- 识别并简化OLLVM混淆（控制流平坦化、虚假控制流、指令替换）\n")

        if optimization_flags.get("simplify_expressions"):
            prompt_parts.append("- 简化复杂的表达式和冗余计算\n")

        if optimization_flags.get("improve_naming"):
            prompt_parts.append("- 为局部变量使用更有意义的名称（不可修改函数名或参数名）\n")

        if optimization_flags.get("add_comments"):
            prompt_parts.append("- 添加关键逻辑的注释说明\n")

        if optimization_flags.get("unroll_simple_loops"):
            prompt_parts.append("- 适当展开简单的循环结构\n")

        prompt_parts.append("\n---\n")

        # 添加上下文信息
        if context.get("function_name"):
            prompt_parts.append(f"\n函数名: {context['function_name']}")

        if source_signature:
            if quality_profile == "off":
                prompt_parts.append(
                    f"\n原始函数签名（仅供参考，不做强制校验）: {source_signature}"
                )
            else:
                prompt_parts.append(f"\n不可变函数签名（必须逐字保留）: {source_signature}")

        if context.get("types"):
            prompt_parts.append(
                f"\n附加 IDA 类型线索（仅供局部变量参考，不能覆盖函数签名）:\n{context['types']}"
            )

        prompt_parts.append(f"\n\n待优化的代码:\n{pseudocode}\n")
        if quality_profile == "off":
            prompt_parts.append("\n只输出一个 C 函数，不要附加解释或 Markdown：")
        else:
            prompt_parts.append("\n只输出完整 C 函数（从原函数签名开始，到最后的闭合大括号结束）：")

        return "".join(prompt_parts)

    def _build_repair_prompt(
        self, pseudocode: str, context: dict, violation: str,
        required_anchors: str = "",
    ) -> str:
        """Build a one-shot, invariant-focused retry prompt.

        The original IDA pseudocode is the only authoritative source.  We do
        not include the failed candidate: small local models tend to repeat a
        hallucinated rewrite when it is shown again as context.
        """
        source_signature = self._get_source_signature(pseudocode)
        quality_profile = self._effective_quality_profile()
        if quality_profile == "globals_only":
            invariant_rule = (
                "2. 必须保留函数签名和参数类型；所有原始 IDA 全局符号"
                "必须逐字保留，不得删除、改名或新增。\n"
            )
        else:
            invariant_rule = (
                "2. 必须保留函数签名和参数类型；不得引入原文不存在的外部调用或 IDA 全局符号。\n"
            )
        prompt_parts = [
            "上一轮 C 代码全量重写没有通过安全校验，必须进行一次严格修复。\n",
            f"失败原因：{violation or '未保留原始语义锚点'}\n\n",
            "【唯一权威输入】下面的 IDA 伪 C 原文是唯一可信来源。\n",
            "【不可违反的规则】\n",
            "1. 只输出一个完整 C 函数；不得输出解释或 Markdown。\n",
            invariant_rule,
            "3. 可以重写表达式、循环、分支和局部变量，也可以删除能够证明冗余的代码。\n",
            "4. 重写循环/分支时必须保持边界、条件、返回值、副作用和必要类型转换等价。\n",
            "5. 必须修复失败原因；不确定时保留原始逻辑。\n",
            "6. 不要用注释、字符串或新变量名伪造原始 IDA 全局符号。\n",
        ]
        if self.config.get("optimization.rewrite_control_flow", False):
            prompt_parts.append(
                "主动重写循环和分支：可将指针循环改成索引循环、合并等价分支、"
                "整理提前返回，但必须重新核对原始边界、终止条件和副作用。\n"
            )
        else:
            prompt_parts.append(
                "保持原有控制流、循环边界和指针哨兵关系不变；"
                "不要将指针循环改写成索引循环。\n"
            )
        if context.get("function_name"):
            prompt_parts.append(f"\n函数名: {context['function_name']}\n")
        if source_signature:
            prompt_parts.append(f"不可变函数签名（必须逐字保留）: {source_signature}\n")
        if required_anchors:
            anchor_label = "以下是原始语义锚点清单，仅用于判断哪些内容可以安全整理："
            prompt_parts.append(f"\n{anchor_label}\n{required_anchors}\n")
        prompt_parts.append(f"\n原始 IDA 伪 C:\n{pseudocode}\n")
        prompt_parts.append("\n现在仅输出安全修复后的完整 C 函数：")
        return "".join(prompt_parts)

    def _make_request_with_retry(
        self, headers: dict, payload: dict, cancel_event=None
    ) -> Optional[dict]:
        """发送请求并支持重试

        Args:
            headers: 请求头
            payload: 请求体

        Returns:
            响应 JSON，失败返回 None
        """
        last_error = None
        delay = self.retry_delay

        def retry_after_seconds(response):
            value = response.headers.get("Retry-After") if response is not None else None
            if not value:
                return None
            try:
                return max(0.0, float(value))
            except (TypeError, ValueError):
                try:
                    return max(0.0, (parsedate_to_datetime(value).timestamp() - time.time()))
                except (TypeError, ValueError, OverflowError):
                    return None

        for attempt in range(self.max_retry_attempts):
            if cancel_event is not None and cancel_event.is_set():
                self.last_error = "请求已取消"
                return None
            try:
                self.logger.debug(f"LLM API 请求 (尝试 {attempt + 1}/{self.max_retry_attempts})")

                response = requests.post(
                    self.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout
                )

                response.raise_for_status()
                return response.json()

            except requests.exceptions.Timeout as e:
                last_error = f"请求超时: {e}"
                self.logger.warning(f"{last_error} (尝试 {attempt + 1}/{self.max_retry_attempts})")

            except requests.exceptions.ConnectionError as e:
                last_error = f"连接错误: {e}"
                self.logger.warning(f"{last_error} (尝试 {attempt + 1}/{self.max_retry_attempts})")

            except requests.exceptions.HTTPError as e:
                last_error = f"HTTP 错误: {e}"
                self.logger.warning(f"{last_error} (尝试 {attempt + 1}/{self.max_retry_attempts})")
                # Retry rate limits and transient server failures; other 4xx
                # responses are configuration/authentication errors.
                if response.status_code < 500 and response.status_code != 429:
                    break
                retry_after = retry_after_seconds(response)
                if retry_after is not None:
                    delay = min(retry_after, MAX_RETRY_DELAY)

            except requests.exceptions.RequestException as e:
                last_error = f"请求异常: {e}"
                self.logger.warning(f"{last_error} (尝试 {attempt + 1}/{self.max_retry_attempts})")

            except Exception as e:
                last_error = f"未知错误: {e}"
                self.logger.warning(f"{last_error} (尝试 {attempt + 1}/{self.max_retry_attempts})")
                break  # 未知错误不重试

            # 最后一次尝试失败，不等待
            if attempt < self.max_retry_attempts - 1:
                self.logger.debug(f"等待 {delay:.1f} 秒后重试...")
                if cancel_event is not None:
                    if cancel_event.wait(delay):
                        self.last_error = "请求已取消"
                        return None
                else:
                    time.sleep(delay)
                delay = min(delay * RETRY_DELAY_MULTIPLIER, MAX_RETRY_DELAY)

        self.logger.error(f"LLM API 调用失败（已重试 {self.max_retry_attempts} 次）: {last_error}")
        self.last_error = last_error or "LLM API 调用失败"
        return None

    def get_last_error(self) -> str:
        """Return a user-facing reason for the most recent failed request."""
        return self.last_error

    def get_last_response_info(self) -> dict:
        """Return a copy of lightweight metadata for the most recent reply."""
        return dict(self.last_response_info)

    def _request_code(
        self, prompt: str, request_kind: str = "optimize", temperature: float = None,
        conservative: bool = True, cancel_event=None, max_tokens: int = None,
        system_content: str = None,
    ) -> Optional[str]:
        """Submit one non-streaming code request and validate its envelope."""
        self.last_error = ""
        self.last_response_info = {}

        headers = {
            "Content-Type": "application/json"
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": self._build_messages(
                prompt, conservative, system_content=system_content
            ),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False
        }
        if self.reasoning_effort in ("none", "low", "medium", "high", "max"):
            payload["reasoning_effort"] = self.reasoning_effort
        self._apply_penalties(payload)

        # 调试模式：保存请求
        if self.config.get("plugin.debug", False):
            self._save_debug_request(payload)

        # 使用重试机制发送请求
        result = self._make_request_with_retry(headers, payload, cancel_event)
        if result is None:
            return None

        # 调试模式：保存响应
        if self.config.get("plugin.debug", False):
            self._save_debug_response(result)

        try:
            # Record termination metadata.  A `stop` result can still be
            # semantically unsafe, but `length` is definitely truncated and
            # should not reach post-processing as a candidate function.
            if "choices" in result and len(result["choices"]) > 0:
                choice = result["choices"][0]
                message = choice.get("message", {}) or {}
                content = message.get("content", "")
                finish_reason = choice.get("finish_reason", "")
                reasoning_content = message.get("reasoning_content", "")
                self.last_response_info = {
                    "finish_reason": finish_reason,
                    "content_chars": len(content) if isinstance(content, str) else 0,
                    "reasoning_chars": len(reasoning_content) if isinstance(reasoning_content, str) else 0,
                    "model": result.get("model", self.model),
                    "usage": result.get("usage", {}),
                    "request_kind": request_kind,
                }
                self.logger.debug(
                    "LLM 响应: finish_reason=%s, content=%s 字符, reasoning=%s 字符"
                    % (
                        finish_reason or "unknown",
                        self.last_response_info["content_chars"],
                        self.last_response_info["reasoning_chars"],
                    )
                )
                if finish_reason == "length":
                    self.last_error = "模型输出达到 max_tokens 限制而被截断"
                    self.logger.warning(self.last_error)
                    return None
                if not isinstance(content, str) or not content.strip():
                    self.last_error = "模型没有返回可用的最终代码"
                    self.logger.warning(self.last_error)
                    return None
                return self._post_process_code(content)

            self.last_error = "LLM 响应格式无效：缺少 choices 字段"
            self.logger.error(self.last_error)
            return None

        except Exception as e:
            self.last_error = f"处理 LLM 响应时出错: {e}"
            self.logger.exception(self.last_error)
            return None

    def optimize_code(
        self, pseudocode: str, context: dict = None, cancel_event=None
    ) -> Optional[str]:
        """Ask for a complete full-mode rewrite candidate."""
        if context is None:
            context = {}
        quality_profile = self._effective_quality_profile()
        system_content = None
        if quality_profile == "off":
            system_content = (
                "Return only one C function. The safety and invariant guard is "
                "disabled, so you may rewrite the signature, calls, strings, "
                "constants, global references, expressions, control flow, and "
                "local variables without preservation constraints."
            )
        elif quality_profile == "globals_only":
            system_content = (
                "You are a careful reverse engineer. Return only one complete C "
                "function. Preserve the exact function signature and the exact set "
                "of IDA global symbols such as g_*, byte_*, dword_*, and qword_*. "
                "You may rewrite calls, strings, constants, expressions, control "
                "flow, and local variables, but do not rename, add, or remove those "
                "IDA global symbols."
            )
        return self._request_code(
            self._build_prompt(pseudocode, context),
            "optimize",
            conservative=False,
            cancel_event=cancel_event,
            system_content=system_content,
        )

    def suggest_conservative_renames(
        self, pseudocode: str, context: dict = None, candidates=None,
        cancel_event=None,
    ) -> Optional[dict]:
        """Ask for a rename map instead of asking a small model to copy C code."""
        context = context or {}
        candidates = list(candidates or [])
        if not candidates:
            return {}
        prompt = self._build_conservative_rename_prompt(
            pseudocode, context, candidates
        )
        content = self._request_code(
            prompt,
            "conservative_rename_plan",
            temperature=0.0,
            conservative=True,
            cancel_event=cancel_event,
            max_tokens=min(
                int(self.max_tokens) if str(self.max_tokens).isdigit() else 384,
                384,
            ),
            system_content=(
                "Return only a compact JSON object mapping allowed IDA local "
                "variables to conservative snake_case names. Never return C code."
            ),
        )
        if content is None:
            return None
        plan = self._parse_rename_plan(content)
        if plan is None:
            self.last_error = "模型未返回有效的局部变量重命名 JSON"
            self.logger.warning(self.last_error)
            return None
        return plan

    def repair_code(
        self, pseudocode: str, context: dict = None, violation: str = "",
        required_anchors: str = "", cancel_event=None,
    ) -> Optional[str]:
        """Ask once for an invariant-preserving replacement after a rejection.

        This deliberately uses temperature 0 even if the normal profile is
        exploratory.  The caller remains responsible for applying the exact
        same post-processing and semantic guard to this candidate.
        """
        if context is None:
            context = {}
        prompt = self._build_repair_prompt(
            pseudocode, context, violation, required_anchors
        )
        return self._request_code(
            prompt,
            "quality_repair",
            temperature=0.0,
            conservative=False,
            cancel_event=cancel_event,
        )

    def _post_process_code(self, code: str) -> str:
        """后处理生成的代码

        Args:
            code: LLM生成的原始代码

        Returns:
            清理后的代码
        """
        # 剥离推理模型的思考块（VibeThinker 等 3B 推理模型会输出 <think>...</think>）
        code = self._strip_reasoning(code)

        # 移除markdown代码块标记
        code = code.strip()

        if code.startswith("```c"):
            code = code[4:]
        elif code.startswith("```"):
            code = code[3:]

        if code.endswith("```"):
            code = code[:-3]

        code = code.strip()

        return code

    def _strip_reasoning(self, code: str) -> str:
        """剥离推理模型的思考块

        推理模型（VibeThinker-3B、DeepSeek-R1 等）在给出最终答案前会先
        输出一段   <think>思考内容</think> 的内部推理过程。这段内容对
        代码优化结果无用，且会污染语法检查与对比展示，必须剔除。

        可通过 config 的 llm.strip_reasoning 开关控制（默认开启）。

        回退逻辑：某些推理模型（如 VibeThinker-3B）会把最终答案也写进
        <think> 块内部，闭合标签之后无任何内容，或因 max_tokens 截断
        导致未输出闭合后的答案。此时直接 strip 会让结果为空。检测到
        strip 后为空时，回退从思考块内部提取最后的代码块作为答案。

        Args:
            code: LLM原始输出

        Returns:
            剥离思考块后的内容
        """
        if not code:
            return code

        if not self.config.get("llm.strip_reasoning", True):
            return code

        tags = self.config.get("llm.reasoning_tags", ["think", "thinking"])

        result = code
        for tag in tags:
            # 先抓取完整闭合思考块的内部内容，留作回退
            closed_pattern = re.compile(
                r'<' + re.escape(tag) + r'>(.*?)</' + re.escape(tag) + r'>',
                re.DOTALL | re.IGNORECASE
            )
            think_contents = closed_pattern.findall(result)

            # 1. 匹配完整闭合的思考块（非贪婪）
            pattern = re.compile(
                r'<' + re.escape(tag) + r'>.*?</' + re.escape(tag) + r'>',
                re.DOTALL | re.IGNORECASE
            )
            result = pattern.sub('', result)

            # 1b. 抓取未闭合思考块的内部内容（max_tokens 截断场景），留作回退
            unclosed_pattern = re.compile(
                r'<' + re.escape(tag) + r'>(.*)$',
                re.DOTALL | re.IGNORECASE
            )
            unclosed_m = unclosed_pattern.search(result)
            unclosed_content = unclosed_m.group(1) if unclosed_m else ""

            # 2. 处理未闭合的情况（流式中断或模型未输出结束标签）
            #    丢弃开标签及之后的所有内容，因为此时还没进入答案部分
            open_pattern = re.compile(
                r'<' + re.escape(tag) + r'>.*',
                re.DOTALL | re.IGNORECASE
            )
            result = open_pattern.sub('', result)

            # 3. 回退：strip 后为空，但思考块内部有内容
            #    VibeThinker-3B 会把最终答案塞进  <think> 块里
            if not result.strip() and think_contents:
                result = self._extract_code_from_think(think_contents[-1])
            elif not result.strip() and unclosed_content:
                result = self._extract_code_from_think(unclosed_content)

        return result.strip()

    def _extract_code_from_think(self, think_content: str) -> str:
        """从思考块内部提取最完整的代码片段（回退用）

        VibeThinker-3B 在思考块内会贴多个代码片段（边推理边分析），
        最终答案通常是最长且括号平衡的那个。取"最后一个"会误取正在
        分析的中间片段。改为在所有 fenced 块里挑最完整的。

        选择优先级：
        1. 所有 ```c / ``` 代码块中，括号平衡且最长的那个
        2. 都不平衡时，取最长的那个
        3. 关键词（Thus final code / 最终代码 等）之后的内容
        4. 整个思考块内容（让后续清理兜底）

        Args:
            think_content: 思考块内部文本

        Returns:
            提取出的代码文本
        """
        if not think_content:
            return ""

        def brace_balanced(s: str) -> bool:
            return s.count('{') - s.count('}') == 0 and s.count('(') - s.count(')') == 0

        # 1. 收集所有 fenced 代码块
        fences = re.findall(
            r'```(?:[a-zA-Z]*)\n?(.*?)```',
            think_content,
            re.DOTALL
        )
        if fences:
            # 优先：括号平衡中最长的
            balanced = [f for f in fences if brace_balanced(f.strip())]
            if balanced:
                return max(balanced, key=len).strip()
            # 否则取最长的
            return max(fences, key=len).strip()

        # 2. 关键词之后的内容
        kw_pattern = re.compile(
            r'(?:Thus\s+(?:the\s+)?(?:final\s+)?(?:code|answer|output)|'
            r'Final\s+code|最终代码|最终答案|答案[是为：:])'
            r'[：:.\s]*(.*)$',
            re.DOTALL | re.IGNORECASE
        )
        m = kw_pattern.search(think_content)
        if m:
            return m.group(1).strip()

        # 3. 兜底：返回全部
        return think_content.strip()

    def _save_debug_request(self, payload: dict):
        """保存调试请求到文件

        Args:
            payload: 请求体
        """
        try:
            import os
            from .constants import DEBUG_OUTPUT_DIR, DEBUG_REQUEST_PREFIX

            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            debug_dir = os.path.join(plugin_dir, DEBUG_OUTPUT_DIR)
            os.makedirs(debug_dir, exist_ok=True)

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{DEBUG_REQUEST_PREFIX}{timestamp}.json"
            filepath = os.path.join(debug_dir, filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

            self.logger.debug(f"请求已保存: {filepath}")
        except Exception as e:
            self.logger.warning(f"保存调试请求失败: {e}")

    def _save_debug_response(self, response: dict):
        """保存调试响应到文件

        Args:
            response: 响应体
        """
        try:
            import os
            from .constants import DEBUG_OUTPUT_DIR, DEBUG_RESPONSE_PREFIX

            plugin_dir = os.path.dirname(os.path.abspath(__file__))
            debug_dir = os.path.join(plugin_dir, DEBUG_OUTPUT_DIR)
            os.makedirs(debug_dir, exist_ok=True)

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"{DEBUG_RESPONSE_PREFIX}{timestamp}.json"
            filepath = os.path.join(debug_dir, filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(response, f, ensure_ascii=False, indent=2)

            self.logger.debug(f"响应已保存: {filepath}")
        except Exception as e:
            self.logger.warning(f"保存调试响应失败: {e}")

    def test_connection(self) -> bool:
        """测试与LLM的连接

        Returns:
            连接成功返回True
        """
        try:
            from urllib.parse import urlparse

            models_url = self._models_url_from_api_url(self.api_url)
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            # A health probe must not force a large local model to generate
            # tokens.  Ollama may need longer than ten seconds just to load a
            # model, while /v1/models is immediately responsive.
            response = requests.get(models_url, headers=headers, timeout=min(self.timeout, 30))
            return response.status_code == 200
        except Exception:
            return False
