# -*- coding: utf-8 -*-
"""
代码后处理器 - 验证和清理优化后的代码
"""

import re
from constants import (
    DEGENERATION_THRESHOLD,
    INCOMPLETE_FRAGMENT_RATIO,
    FUNCTION_SIGNATURE_PATTERN,
)
from logger import get_logger


class CodeProcessor:
    """处理和验证优化后的代码"""

    @staticmethod
    def basic_syntax_check(code: str) -> tuple:
        """基础语法检查

        Args:
            code: C代码

        Returns:
            (是否通过, 错误信息)
        """
        if not code or not code.strip():
            return False, "代码为空"

        # 检查大括号匹配
        brace_count = code.count('{') - code.count('}')
        if brace_count != 0:
            return False, f"大括号不匹配 (差值: {brace_count})"

        # 检查小括号匹配
        paren_count = code.count('(') - code.count(')')
        if paren_count != 0:
            return False, f"小括号不匹配 (差值: {paren_count})"

        # 检查方括号匹配
        bracket_count = code.count('[') - code.count(']')
        if bracket_count != 0:
            return False, f"方括号不匹配 (差值: {bracket_count})"

        return True, ""

    @staticmethod
    def remove_extra_whitespace(code: str) -> str:
        """移除多余的空白字符

        Args:
            code: C代码

        Returns:
            清理后的代码
        """
        # 移除行尾空白
        lines = code.split('\n')
        lines = [line.rstrip() for line in lines]

        # 移除连续的空行（最多保留一个）
        result = []
        prev_empty = False

        for line in lines:
            if line.strip():
                result.append(line)
                prev_empty = False
            else:
                if not prev_empty:
                    result.append(line)
                prev_empty = True

        # 去除首尾空行（首尾空行无意义）
        while result and not result[0].strip():
            result.pop(0)
        while result and not result[-1].strip():
            result.pop()

        return '\n'.join(result)

    @staticmethod
    def normalize_indentation(code: str, indent_size: int = 2) -> str:
        """规范化缩进（简单版本）

        Args:
            code: C代码
            indent_size: 缩进空格数

        Returns:
            规范化后的代码
        """
        lines = code.split('\n')
        result = []
        indent_level = 0

        for line in lines:
            stripped = line.strip()

            if not stripped:
                result.append('')
                continue

            # 检查是否减少缩进
            if stripped.startswith('}'):
                indent_level = max(0, indent_level - 1)

            # 应用缩进
            result.append(' ' * (indent_level * indent_size) + stripped)

            # 检查是否增加缩进
            if stripped.endswith('{'):
                indent_level += 1
            elif stripped.startswith('}') and stripped.endswith('{'):
                indent_level += 1

        return '\n'.join(result)

    @staticmethod
    def clean_llm_artifacts(code: str) -> str:
        """清理LLM生成的非代码内容

        Args:
            code: 可能包含解释文本的代码

        Returns:
            纯代码
        """
        # 先剥离推理模型的思考块（<think>...</think> 等）
        code = CodeProcessor.strip_reasoning_blocks(code)

        # 移除markdown代码块标记
        code = re.sub(r'^```[a-z]*\n?', '', code, flags=re.MULTILINE)
        code = re.sub(r'\n?```$', '', code)

        # 移除常见的解释性前缀和后缀
        lines = code.split('\n')
        cleaned = []

        skip_patterns = [
            r'^(这是|以下是|优化后的代码|最终代码|Here is|The optimized code|Final code)',
            r'^(解释|说明|注意|Explanation|Note|Summary|总结)[：:]',
            r'^(思考|分析|Analysis|Thinking)[：:]',
        ]

        in_code = False
        for line in lines:
            stripped = line.strip()

            # 检测到函数签名行，开始收集代码
            if not in_code and re.match(
                r'^(?:int|void|char|unsigned|_BYTE|_WORD|_DWORD|_QWORD|bool|float|double|long|short|size_t|__int64|__int32|__int16|__int8)',
                stripped
            ):
                in_code = True

            # 已进入代码块，收集所有行
            if in_code:
                cleaned.append(line)
                continue

            # 尚未进入代码，跳过解释性行
            if any(re.match(pattern, stripped, re.IGNORECASE) for pattern in skip_patterns):
                continue

            # 保留可能是代码的行
            if stripped and ('{' in stripped or '}' in stripped or ';' in stripped or '#include' in stripped):
                in_code = True
                cleaned.append(line)
            elif not stripped:
                # 空行保留（可能是代码中的分隔）
                cleaned.append(line)

        return '\n'.join(cleaned)

    @staticmethod
    def strip_reasoning_blocks(code: str, tags=None) -> str:
        """剥离推理模型的思考块（如 <think>...</think>）

        推理模型（VibeThinker、DeepSeek-R1 等）会先输出一段 <think> 思考过程
        再给出最终答案。这段思考内容对用户无用，且会污染后续的语法检查和
        对比展示，必须先剥离。

        处理两种情况：
        1. 完整闭合：<think>思考内容</think>最终代码 -> 只保留最终代码
        2. 未闭合（流式中断）：<think>思考内容（无结束标签）-> 整体丢弃，
           因为此时还没有进入答案部分

        Args:
            code: LLM原始输出
            tags: 需要剥离的标签名列表，默认 ["think", "thinking"]

        Returns:
            剥离思考块后的内容
        """
        if not code:
            return code

        if tags is None:
            tags = ["think", "thinking"]

        result = code
        for tag in tags:
            # 优先匹配完整闭合的块，非贪婪
            pattern = re.compile(
                r'<' + re.escape(tag) + r'>.*?</' + re.escape(tag) + r'>',
                re.DOTALL | re.IGNORECASE
            )
            result = pattern.sub('', result)

            # 处理未闭合的块（流式中断或模型未输出结束标签）
            # 如果存在开标签但无闭标签，删除开标签及其后的所有内容
            open_pattern = re.compile(
                r'<' + re.escape(tag) + r'>.*',
                re.DOTALL | re.IGNORECASE
            )
            result = open_pattern.sub('', result)

        return result.strip()

    @staticmethod
    def preserve_ida_annotations(original: str, optimized: str) -> str:
        """尝试保留IDA的特殊注释和标注

        Args:
            original: 原始IDA伪C代码
            optimized: 优化后的代码

        Returns:
            合并了IDA注释的优化代码
        """
        # 提取IDA特有的注释（如XREF、JUMPOUT等）
        ida_comments = re.findall(r'//.*?(?:XREF|JUMPOUT|DATA XREF).*', original)

        # 如果有IDA特殊注释，尝试添加到优化代码的开头
        if ida_comments:
            header = '\n'.join(ida_comments) + '\n\n'
            return header + optimized

        return optimized

    @staticmethod
    def detect_and_truncate_degeneration(code: str, threshold: int = DEGENERATION_THRESHOLD) -> tuple:
        """检测并截断重复退化输出

        小模型在啃不动硬骨头时会进入重复退化，生成大量结构相同、仅编号递增的
        行（如 `unsigned __int16 vN = vN+1 ^ vN+2;` 重复上百行）。这是即使
        加了 repeat_penalty 仍可能发生的兜底场景。

        策略：
        - 把行归一化（去掉变量编号数字、空白）得到"骨架"
        - 若同一骨架连续出现 >= threshold 次，判定退化
        - 保留退化起点之前的全部内容 + 首个重复行，丢弃后续重复
        - 若退化点之后还有明显的新结构（如 `for`、`return`），尝试保留

        Args:
            code: LLM 生成的代码
            threshold: 连续重复触发阈值

        Returns:
            (截断后的代码, 是否检测到退化)
        """
        if not code:
            return code, False

        lines = code.split('\n')
        if len(lines) < threshold * 2:
            return code, False

        def skeleton(line: str) -> str:
            # 去掉变量编号数字（v123 -> v），压缩空白，转小写
            s = re.sub(r'\b[vV]\d+\b', 'v', line)
            s = re.sub(r'\b[a-zA-Z_]+\d+\b', 'x', s)  # 任意标识符带数字也归一
            s = re.sub(r'\d+', '0', s)
            s = re.sub(r'\s+', ' ', s).strip().lower()
            return s

        skel_prev = None
        run_len = 1
        truncate_at = None

        for idx, line in enumerate(lines):
            sk = skeleton(line)
            if not sk:
                run_len = 1
                skel_prev = None
                continue
            if sk == skel_prev:
                run_len += 1
                if run_len >= threshold and truncate_at is None:
                    # 退化起点：保留到首个重复行（含），截断后续
                    truncate_at = idx
                    break
            else:
                run_len = 1
                skel_prev = sk

        if truncate_at is None:
            return code, False

        # 保留退化起点行（首个重复行），丢弃其后所有重复
        kept = lines[:truncate_at + 1]
        truncated = '\n'.join(kept)

        # 尝试从被丢弃的部分里捞回真正的代码收尾（return/} 等）
        tail_patterns = [r'^\s*return\b', r'^\s*\}\s*$', r'^\s*for\b', r'^\s*printf']
        for line in lines[truncate_at + 1:]:
            if any(re.match(p, line) for p in tail_patterns):
                truncated += '\n' + line

        return truncated, True

    @staticmethod
    def is_incomplete_fragment(code: str, original: str = None) -> bool:
        """检测是否为不完整的代码片段（而非完整函数）

        判定依据：
        - 没有函数签名行（含括号的类型+函数名声明）→ 不完整
        - 缺少闭合大括号（顶层没有配对的 `{ }`）→ 不完整
        - 无函数签名且相对原文极短（<30%）→ 不完整（辅助确认片段）

        注：有完整签名+平衡括号的短函数视为完整（优化本就会缩短代码）。

        Args:
            code: 待检测的代码
            original: 原始代码（可选，用于长度对比）

        Returns:
            不完整返回 True
        """
        if not code or not code.strip():
            return True

        stripped = code.strip()

        # 1. 必须有函数签名：匹配 "类型 函数名(" 模式
        has_signature = bool(re.search(FUNCTION_SIGNATURE_PATTERN, stripped, re.MULTILINE))

        # 2. 顶层大括号必须平衡
        braces_ok = stripped.count('{') - stripped.count('}') == 0

        # 有签名且括号平衡 → 完整（不论长度，优化本就会缩短）
        if has_signature and braces_ok:
            return False

        # 3. 无签名时，辅助用长度判定确认是片段
        if not has_signature and original and original.strip():
            if len(stripped) < len(original.strip()) * INCOMPLETE_FRAGMENT_RATIO and '{' in original:
                return True

        # 无签名或括号不平衡 → 不完整
        return True

    @staticmethod
    def process(code: str, original: str = None, config=None) -> tuple:
        """完整的后处理流程

        Args:
            code: LLM生成的代码
            original: 原始IDA代码（可选，用于保留注释）
            config: Config实例（可选，用于读取 degeneration_guard 开关）

        Returns:
            (处理后的代码, 是否成功, 错误信息)
        """
        logger = get_logger()

        if not code:
            logger.error("处理失败：代码为空")
            return "", False, "代码为空"

        # 1. 清理LLM生成的非代码内容
        code = CodeProcessor.clean_llm_artifacts(code)
        logger.debug("已清理 LLM 生成的非代码内容")

        # 2. 退化检测兜底（在语法检查前，避免重复行撑爆统计）
        guard = True
        if config is not None:
            guard = config.get("llm.degeneration_guard", True)
        degenerated = False
        if guard:
            code, degenerated = CodeProcessor.detect_and_truncate_degeneration(code)
            if degenerated:
                logger.warning("检测到重复退化输出，已截断")

        # 2b. 完整性兜底：保守模式下模型输出不完整片段时，回退返回原始代码
        #     保守模式宗旨是"不改逻辑"，原始代码本身就是正确答案
        conservative = None
        if config is not None:
            conservative = (config.get("llm.conservative_mode", "auto") or "auto").lower()
        if original and conservative in ("on", "auto"):
            if CodeProcessor.is_incomplete_fragment(code, original):
                logger.warning("模型输出不完整，回退到原始代码（保守模式）")
                fallback = CodeProcessor.remove_extra_whitespace(original)
                if original:
                    fallback = CodeProcessor.preserve_ida_annotations(original, fallback)
                return fallback, True, "模型输出不完整，已回退原始代码（保守模式）"

        # 3. 基础语法检查
        passed, error = CodeProcessor.basic_syntax_check(code)
        if not passed:
            msg = f"语法检查失败: {error}"
            if degenerated:
                msg += "（已截断重复退化输出）"
            logger.error(msg)
            return code, False, msg

        # 4. 清理空白字符
        code = CodeProcessor.remove_extra_whitespace(code)

        # 5. 规范化缩进
        # code = CodeProcessor.normalize_indentation(code)  # 可选，可能影响原有格式

        # 6. 保留IDA注释
        if original:
            code = CodeProcessor.preserve_ida_annotations(original, code)

        if degenerated:
            logger.info("处理成功（已截断重复退化输出）")
            return code, True, "已截断重复退化输出"

        logger.debug("代码处理完成")
        return code, True, ""

    @staticmethod
    def calculate_diff_stats(original: str, optimized: str) -> dict:
        """计算代码差异统计

        Args:
            original: 原始代码
            optimized: 优化后的代码

        Returns:
            差异统计字典
        """
        original_lines = original.split('\n')
        optimized_lines = optimized.split('\n')

        stats = {
            "original_lines": len(original_lines),
            "optimized_lines": len(optimized_lines),
            "original_chars": len(original),
            "optimized_chars": len(optimized),
            "line_diff": len(optimized_lines) - len(original_lines),
            "char_diff": len(optimized) - len(original)
        }

        return stats
