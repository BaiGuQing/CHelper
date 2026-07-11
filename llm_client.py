# -*- coding: utf-8 -*-
"""
LLM客户端 - 与本地大模型通信
"""

import json
import re
import requests
import time
from typing import Optional, Generator

from constants import (
    HEAVY_OBF_PATTERN_THRESHOLD,
    MAX_RETRY_ATTEMPTS,
    INITIAL_RETRY_DELAY,
    RETRY_DELAY_MULTIPLIER,
    MAX_RETRY_DELAY,
    SSE_DATA_PREFIX,
    SSE_DONE_MARKER,
    OLLVM_MAGIC_PATTERNS,
)
from logger import get_logger


class LLMClient:
    """本地大模型客户端，支持OpenAI兼容的API"""

    def __init__(self, config):
        """初始化LLM客户端

        Args:
            config: Config实例
        """
        self.config = config
        self.api_url = config.get("llm.api_url")
        self.model = config.get("llm.model")
        self.temperature = config.get("llm.temperature", 0.3)
        self.max_tokens = config.get("llm.max_tokens", 4096)
        self.timeout = config.get("llm.timeout", 120)
        self.api_key = config.get("llm.api_key", "")
        self.repeat_penalty = config.get("llm.repeat_penalty", 1.15)
        self.frequency_penalty = config.get("llm.frequency_penalty", 0.3)
        self.presence_penalty = config.get("llm.presence_penalty", 0.3)
        self.conservative_mode = config.get("llm.conservative_mode", "auto")
        self.top_p = config.get("llm.top_p", 0.9)
        self.top_k = config.get("llm.top_k", 40)
        self.min_p = config.get("llm.min_p", 0.05)
        self.max_retry_attempts = config.get("llm.max_retry_attempts", MAX_RETRY_ATTEMPTS)
        self.retry_delay = config.get("llm.retry_delay", INITIAL_RETRY_DELAY)
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

    def _apply_penalties(self, payload: dict):
        """向请求 payload 注入重复退化抑制参数

        llama-server 同时支持 llama.cpp 原生的 repeat_penalty 和 OpenAI 风格的
        frequency_penalty / presence_penalty。三者叠加可有效抑制小模型
        生成重复行（如 vN = vN+1 ^ vN+2 死循环）的退化现象。
        值为 0/None 时跳过，避免覆盖后端默认。
        """
        if self.repeat_penalty and self.repeat_penalty > 0:
            payload["repeat_penalty"] = self.repeat_penalty
        if self.frequency_penalty and self.frequency_penalty != 0:
            payload["frequency_penalty"] = self.frequency_penalty
        if self.presence_penalty and self.presence_penalty != 0:
            payload["presence_penalty"] = self.presence_penalty
        if self.top_p and self.top_p > 0:
            payload["top_p"] = self.top_p
        if self.top_k and self.top_k > 0:
            payload["top_k"] = self.top_k
        if self.min_p and self.min_p > 0:
            payload["min_p"] = self.min_p

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

    def _build_full_prompt(self, pseudocode: str, context: dict) -> str:
        optimization_flags = self.config.get("optimization", {})

        prompt_parts = [
            "你是一个专业的逆向工程专家，精通代码反混淆和可读性优化。",
            "\n任务：将IDA Pro反编译的伪C代码优化为更易读、更符合人类编写习惯的代码。\n",
            "\n【严格要求】",
            "1. 输出格式：仅输出纯C代码，不要任何解释性文字",
            "2. 不要使用markdown代码块标记（```c 或 ```）",
            "3. 必须输出完整的函数，包含函数签名和完整的函数体",
            "4. 保持大括号平衡，确保语法正确",
            "\n优化目标："
        ]

        if optimization_flags.get("deobfuscate_ollvm"):
            prompt_parts.append("- 识别并简化OLLVM混淆（控制流平坦化、虚假控制流、指令替换）")

        if optimization_flags.get("simplify_expressions"):
            prompt_parts.append("- 简化复杂的表达式和冗余计算")

        if optimization_flags.get("improve_naming"):
            prompt_parts.append("- 根据上下文推断更有意义的变量名和函数名")

        if optimization_flags.get("add_comments"):
            prompt_parts.append("- 添加关键逻辑的注释说明")

        if optimization_flags.get("unroll_simple_loops"):
            prompt_parts.append("- 适当展开简单的循环结构")

        prompt_parts.extend([
            "\n\n优化原则：",
            "1. 保持代码逻辑完全等价，不能改变程序行为",
            "2. 移除编译器优化引入的晦涩写法",
            "3. 恢复高层次的控制流结构（for/while/if-else）",
            "4. 保留所有关键的类型信息和常量",
            "5. 变量命名要有意义且符合C语言规范",
            "6. 必须输出完整函数，不能只输出片段",
            "\n---\n"
        ])

        # 添加上下文信息
        if context.get("function_name"):
            prompt_parts.append(f"\n函数名: {context['function_name']}")

        if context.get("types"):
            prompt_parts.append(f"\n相关类型定义:\n{context['types']}")

        prompt_parts.append(f"\n\n待优化的代码:\n{pseudocode}\n")
        prompt_parts.append("\n请直接输出优化后的完整C代码（从函数签名开始，到最后的闭合大括号结束）：")

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
        prompt_parts = [
            "你是一个专业的逆向工程专家。",
            "\n任务：对IDA Pro反编译的伪C代码做【保守美化】，不改动任何逻辑。\n",
            "\n【严格输出格式】",
            "1. 仅输出纯C代码，不要任何解释",
            "2. 不要使用markdown代码块标记（```c 或 ```）",
            "3. 必须输出完整函数（从函数签名到最后的}）",
            "\n【保守规则 - 违反任何一条都算失败】：",
            "1. 保留全部运算、操作符、常量、位运算、魔数，一字不改",
            "2. 不得删除、合并、重排任何表达式或语句",
            "3. 不得简化魔数除法、位旋转、状态机更新等复杂运算",
            "4. 只允许：把 v1/v2/a1 等无意义变量名改成反映用途的有语义名字",
            "5. 只允许：在关键步骤前加 // 注释解释其作用（注释要简短）",
            "6. 输出必须与输入逻辑完全等价，仅可读性更好",
            "\n---\n",
        ]

        if context.get("function_name"):
            prompt_parts.append(f"\n函数名: {context['function_name']}")

        if context.get("types"):
            prompt_parts.append(f"\n相关类型定义:\n{context['types']}")

        prompt_parts.append(f"\n\n待美化的代码:\n{pseudocode}\n")
        prompt_parts.append("\n请直接输出美化后的完整C代码（从函数签名开始）：")

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
        return None

    def optimize_code(self, pseudocode: str, context: dict = None) -> Optional[str]:
        """同步调用LLM优化代码

        Args:
            pseudocode: 伪C代码
            context: 上下文信息

        Returns:
            优化后的代码，失败返回None
        """
        if context is None:
            context = {}

        prompt = self._build_prompt(pseudocode, context)

        headers = {
            "Content-Type": "application/json"
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": self.temperature,
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
            # 提取生成的代码
            if "choices" in result and len(result["choices"]) > 0:
                content = result["choices"][0].get("message", {}).get("content", "")
                return self._post_process_code(content)

            self.logger.error("LLM 响应格式无效：缺少 choices 字段")
            return None

        except Exception as e:
            self.logger.exception(f"处理 LLM 响应时出错: {e}")
            return None

    def optimize_code_stream(self, pseudocode: str, context: dict = None) -> Generator[str, None, None]:
        """流式调用LLM优化代码

        对推理模型（VibeThinker 等），会自动过滤  <think>...</think> 思考块，
        只向下游 yield 最终的代码内容。

        Args:
            pseudocode: 伪C代码
            context: 上下文信息

        Yields:
            代码片段（已剥离思考块）
        """
        if context is None:
            context = {}

        prompt = self._build_prompt(pseudocode, context)

        headers = {
            "Content-Type": "application/json"
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True
        }
        self._apply_penalties(payload)

        strip_reasoning = self.config.get("llm.strip_reasoning", True)
        reasoning_tags = self.config.get("llm.reasoning_tags", ["think", "thinking"])

        # 流式剥离思考块的缓冲区
        # 策略：累积所有内容，检测是否进入 <think> 块；在闭合前暂存，闭合后丢弃
        # 闭合标签之后的内容才向下游 yield
        buffer = ""
        in_think = False
        current_tag = None

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
                stream=True
            )

            response.raise_for_status()

            for line in response.iter_lines():
                if line:
                    line_text = line.decode('utf-8')
                    if line_text.startswith("data: "):
                        line_text = line_text[6:]  # 移除 "data: " 前缀

                    if line_text.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(line_text)
                        if "choices" in chunk and len(chunk["choices"]) > 0:
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if not content:
                                continue

                            if not strip_reasoning:
                                yield content
                                continue

                            # 流式思考块过滤
                            buffer += content
                            while buffer:
                                if in_think:
                                    # 寻找闭合标签
                                    close_tag = f"</{current_tag}>"
                                    idx = buffer.lower().find(close_tag.lower())
                                    if idx == -1:
                                        # 还没闭合，继续累积（不输出）
                                        # 保留末尾可能的不完整标签片段
                                        if len(buffer) > len(close_tag):
                                            buffer = buffer[-len(close_tag):]
                                        break
                                    else:
                                        # 找到闭合标签，丢弃思考内容，跳过闭合标签
                                        buffer = buffer[idx + len(close_tag):]
                                        in_think = False
                                        current_tag = None
                                else:
                                    # 寻找开标签
                                    found_tag = None
                                    found_idx = -1
                                    for tag in reasoning_tags:
                                        open_tag = f"<{tag}>"
                                        idx = buffer.lower().find(open_tag.lower())
                                        if idx != -1 and (found_idx == -1 or idx < found_idx):
                                            found_idx = idx
                                            found_tag = tag
                                    if found_idx == -1:
                                        # 没有开标签，输出安全部分（保留末尾可能的不完整标签）
                                        safe_len = len(buffer)
                                        for tag in reasoning_tags:
                                            open_tag = f"<{tag}"
                                            # 检查 buffer 末尾是否有不完整的开标签前缀
                                            for plen in range(min(len(open_tag) - 1, len(buffer)), 0, -1):
                                                if buffer.lower().endswith(open_tag[:plen].lower()):
                                                    safe_len = len(buffer) - plen
                                                    break
                                        if safe_len > 0:
                                            yield buffer[:safe_len]
                                            buffer = buffer[safe_len:]
                                        break
                                    else:
                                        # 找到开标签，输出其前的内容，进入思考状态
                                        if found_idx > 0:
                                            yield buffer[:found_idx]
                                        buffer = buffer[found_idx + len(f"<{found_tag}>"):]
                                        in_think = True
                                        current_tag = found_tag

                    except json.JSONDecodeError:
                        continue

            # 流结束后，若不在思考块中，冲出缓冲区残余
            if strip_reasoning and not in_think and buffer:
                yield buffer

        except requests.exceptions.RequestException as e:
            print(f"[CHelper] LLM流式API调用失败: {e}")
            yield None
        except Exception as e:
            print(f"[CHelper] 流式处理时出错: {e}")
            yield None

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
            from constants import DEBUG_OUTPUT_DIR, DEBUG_REQUEST_PREFIX

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
            from constants import DEBUG_OUTPUT_DIR, DEBUG_RESPONSE_PREFIX

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
        headers = {
            "Content-Type": "application/json"
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 发送简单的测试请求
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": "test"
                }
            ],
            "max_tokens": 10
        }

        try:
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=10
            )
            return response.status_code == 200
        except:
            return False
