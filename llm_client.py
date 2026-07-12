# -*- coding: utf-8 -*-
"""
LLM客户端 - 与本地大模型通信
"""

import json
import re
import requests
import time
from typing import Optional

from .constants import (
    HEAVY_OBF_PATTERN_THRESHOLD,
    MAX_RETRY_ATTEMPTS,
    INITIAL_RETRY_DELAY,
    RETRY_DELAY_MULTIPLIER,
    MAX_RETRY_DELAY,
    OLLVM_MAGIC_PATTERNS,
)
from .logger import get_logger


class LLMClient:
    """本地大模型客户端，支持OpenAI兼容的API"""

    # Bump this whenever prompt semantics change; cache.py includes it in its
    # namespace so an old, less-safe prompt result is never silently reused.
    PROMPT_VERSION = "safe-c-v8"

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
        """构建优化提示词

        Args:
            pseudocode: IDA反编译的伪C代码
            context: 上下文信息（函数名、类型定义等）

        Returns:
            完整的prompt
        """
        if self._should_use_conservative(pseudocode):
            return self._build_conservative_prompt(pseudocode, context)
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
    def _build_messages(prompt: str, conservative: bool = True) -> list:
        """Build a role-separated request supported by OpenAI-compatible APIs."""
        if conservative:
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

    def _build_full_prompt(self, pseudocode: str, context: dict) -> str:
        optimization_flags = self.config.get("optimization", {})
        source_signature = self._get_source_signature(pseudocode)

        prompt_parts = [
            "你是专业的逆向工程专家。将以下 IDA 伪 C 做安全的全量可读性重写。\n",
            "【输出要求】\n",
            "1. 只输出一个完整 C 函数；不要解释、Markdown 或第二个答案。\n",
            "2. 函数签名和参数类型必须保留；不得凭空引入外部调用或全局符号。\n",
            "3. 可以重写表达式、循环、分支和局部变量，删除能够证明冗余的代码。\n",
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
            prompt_parts.append(f"\n不可变函数签名（必须逐字保留）: {source_signature}")

        if context.get("types"):
            prompt_parts.append(
                f"\n附加 IDA 类型线索（仅供局部变量参考，不能覆盖函数签名）:\n{context['types']}"
            )

        prompt_parts.append(f"\n\n待优化的代码:\n{pseudocode}\n")
        prompt_parts.append("\n只输出完整 C 函数（从原函数签名开始，到最后的闭合大括号结束）：")

        return "".join(prompt_parts)

    def _build_conservative_prompt(self, pseudocode: str, context: dict) -> str:
        """保守模式提示词：只重命名 + 加注释，逻辑一字不改

        对 OLLVM 魔数除法/位旋转/状态机等小模型啃不动的硬骨头，
        强行重建会丢逻辑或重复退化。保守模式下只做低风险美化：
        - 保留每一行原始运算和操作符
        - 仅把 v3/v5 等无意义变量名改成有语义的名字
        - 在关键步骤前加 // 注释说明其作用
        - 不删除、不合并、不重排任何表达式
        """
        source_signature = self._get_source_signature(pseudocode)
        prompt_parts = [
            "你是一个专业的逆向工程专家。",
            "\n任务：对IDA Pro反编译的伪C代码做【保守美化】，不改动任何逻辑。\n",
            "\n【严格输出格式】",
            "1. 仅输出纯C代码，不要任何解释",
            "2. 不要使用markdown代码块标记（```c 或 ```）",
            "3. 必须输出完整函数（从函数签名到最后的}）",
            "\n【保守规则 - 违反任何一条都算失败】：",
            "1. 保留函数签名、参数类型、全部运算、操作符、调用、常量和控制流",
            "2. 不得删除、合并、重排任何表达式或语句",
            "3. 不得简化魔数除法、位旋转、状态机更新等复杂运算",
            "4. 必须在有明确用途时，一致地重命名至少一个 IDA 局部变量；不可改函数名或参数名",
            "5. 可添加简短注释，但不能修改已有语句、类型、调用、全局符号或常量",
            "6. 命名线索：scanf 返回值用 scan_result/status；输入数组用 input_buffer；被解引用和递增的输入指针用 input_cursor；遍历 g_xxx 的指针用 xxx_cursor；输入末端哨兵用 input_end_marker",
            "7. 只在名称含义明确时改名；否则保留该局部变量，绝不猜测逻辑",
            "\n---\n",
        ]

        if context.get("function_name"):
            prompt_parts.append(f"\n函数名: {context['function_name']}")

        if source_signature:
            prompt_parts.append(f"\n不可变函数签名（必须逐字保留）: {source_signature}")

        if context.get("types"):
            prompt_parts.append(
                f"\n附加 IDA 类型线索（仅供局部变量参考，不能覆盖函数签名）:\n{context['types']}"
            )

        # These names come from deterministic, syntax-preserving evidence in
        # the pseudocode. Giving the model an explicit map prevents it from
        # either returning a no-op or inventing a semantic rewrite.
        try:
            from .processor import CodeProcessor
            local_rename_hints = CodeProcessor.build_safe_local_rename_mapping(
                pseudocode
            )
        except Exception:
            local_rename_hints = {}
        if local_rename_hints:
            mapping_text = ", ".join(
                f"{source} -> {target}"
                for source, target in local_rename_hints.items()
            )
            prompt_parts.append(
                "\n【已验证的局部变量映射】必须将以下每个局部变量的所有引用"
                f"一致改名：{mapping_text}。除这些局部变量和注释外，禁止改变任何 token。"
            )

        prompt_parts.append(f"\n\n待美化的代码:\n{pseudocode}\n")
        prompt_parts.append("\n请直接输出美化后的完整C代码（从函数签名开始）：")

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
        conservative = self._should_use_conservative(pseudocode)
        if conservative:
            prompt_parts = [
                "上一轮 C 代码改写没有通过安全校验，必须进行一次严格修复。\n",
                f"失败原因：{violation or '未保留原始语义锚点'}\n\n",
                "【唯一权威输入】下面的 IDA 伪 C 原文是唯一可信来源。\n",
                "【不可违反的规则】\n",
                "1. 只输出一个完整 C 函数；不得输出解释或 Markdown。\n",
                "2. 必须逐字保留函数签名、参数、每个语句的执行顺序、控制流、外部调用、IDA 全局符号、字符串和数值常量。\n",
                "3. 只允许修改局部变量名或添加简短注释；不确定时必须原样复制原函数。\n",
                "4. 绝不能删除、替换、合并或猜测任何逻辑；特别要修复上面的失败原因。\n",
                "5. 修复时先以原始函数为底稿，确保失败原因中列出的每个缺失锚点逐字恢复；"
                "不要用注释、字符串或新变量名代替原始 IDA 全局符号。\n",
            ]
        else:
            prompt_parts = [
                "上一轮 C 代码全量重写没有通过安全校验，必须进行一次严格修复。\n",
                f"失败原因：{violation or '未保留原始语义锚点'}\n\n",
                "【唯一权威输入】下面的 IDA 伪 C 原文是唯一可信来源。\n",
                "【不可违反的规则】\n",
                "1. 只输出一个完整 C 函数；不得输出解释或 Markdown。\n",
                "2. 必须保留函数签名和参数类型；不得引入原文不存在的外部调用或 IDA 全局符号。\n",
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
            if conservative:
                anchor_label = "以下语义锚点是必须保留的最低清单（不是可选建议）："
            else:
                anchor_label = "以下是原始语义锚点清单，仅用于判断哪些内容可以安全整理："
            prompt_parts.append(f"\n{anchor_label}\n{required_anchors}\n")

        try:
            from .processor import CodeProcessor
            local_rename_hints = CodeProcessor.build_safe_local_rename_mapping(
                pseudocode
            )
        except Exception:
            local_rename_hints = {}
        if conservative and local_rename_hints:
            mapping_text = ", ".join(
                f"{source} -> {target}"
                for source, target in local_rename_hints.items()
            )
            prompt_parts.append(
                "\n【已验证的局部变量修复映射】如果进行重命名，只能使用以下映射，"
                f"并保持所有引用一致：{mapping_text}\n"
            )
        prompt_parts.append(f"\n原始 IDA 伪 C:\n{pseudocode}\n")
        prompt_parts.append("\n现在仅输出安全修复后的完整 C 函数：")
        return "".join(prompt_parts)

    def _make_request_with_retry(self, headers: dict, payload: dict) -> Optional[dict]:
        """发送请求并支持重试

        Args:
            headers: 请求头
            payload: 请求体

        Returns:
            响应 JSON，失败返回 None
        """
        last_error = None
        delay = self.retry_delay

        for attempt in range(self.max_retry_attempts):
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
                # HTTP 4xx 错误通常不应重试
                if response.status_code >= 400 and response.status_code < 500:
                    break

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
        conservative: bool = True,
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
            "messages": self._build_messages(prompt, conservative),
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            "stream": False
        }
        self._apply_penalties(payload)

        # 调试模式：保存请求
        if self.config.get("plugin.debug", False):
            self._save_debug_request(payload)

        # 使用重试机制发送请求
        result = self._make_request_with_retry(headers, payload)
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

    def optimize_code(self, pseudocode: str, context: dict = None) -> Optional[str]:
        """Synchronously ask the model for a normal optimization candidate."""
        if context is None:
            context = {}
        return self._request_code(
            self._build_prompt(pseudocode, context),
            "optimize",
            conservative=self._should_use_conservative(pseudocode),
        )

    def repair_code(
        self, pseudocode: str, context: dict = None, violation: str = "",
        required_anchors: str = "",
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
            conservative=self._should_use_conservative(pseudocode),
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

            parsed = urlparse(self.api_url)
            models_url = f"{parsed.scheme}://{parsed.netloc}/v1/models"
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
