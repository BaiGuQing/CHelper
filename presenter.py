# -*- coding: utf-8 -*-
"""
结果展示器 - 在IDA中显示优化结果

使用 simplecustviewer_t 创建独立窗口显示优化后的代码，
不 hook、不修改原始反编译缓存，不影响已有的伪代码窗口。
通过 IDA 颜色标签实现 C 语法高亮，外观接近原生伪代码窗口。
支持双击跳转定位等增强交互。
"""

import re
import ida_kernwin
import ida_lines
import idc

from .constants import C_KEYWORDS, C_TYPES, IDA_VAR_PATTERN
from .logger import get_logger


_IDA_VAR_RE = re.compile(IDA_VAR_PATTERN)
_HEX_ADDRESS_RE = re.compile(r"^0[xX][0-9a-fA-F]+$")
_IDENTIFIER_CHAR_RE = re.compile(r"[A-Za-z0-9_]")


def _comment_color():
    """Return the comment color used by the native listing when available."""
    return getattr(
        ida_lines,
        "SCOLOR_AUTOCMT",
        getattr(ida_lines, "SCOLOR_REGCMT", ida_lines.SCOLOR_DEFAULT),
    )


def _colorize_identifier(word: str, color: str) -> str:
    """Wrap an identifier in an IDA syntax-color tag."""
    return ida_lines.COLSTR(word, color) if color else word


def _resolve_name(name: str):
    """Resolve a clicked C/IDA name, including member-call spellings."""
    candidates = [name]
    for separator in ("->", ".", "::"):
        if separator in name:
            candidates.append(name.rsplit(separator, 1)[-1])

    badaddr = getattr(idc, "BADADDR", -1)
    for candidate in candidates:
        if not candidate:
            continue
        try:
            address = idc.get_name_ea_simple(candidate)
        except (AttributeError, TypeError, ValueError):
            continue
        if isinstance(address, int) and address != badaddr:
            return address
    return None


def _is_loaded_address(address: int) -> bool:
    """Avoid treating ordinary constants such as ``0x52`` as code targets."""
    try:
        import ida_bytes
        if ida_bytes.is_loaded(address):
            return True
    except (ImportError, AttributeError, TypeError, ValueError):
        pass

    try:
        import ida_segment
        return ida_segment.getseg(address) is not None
    except (ImportError, AttributeError, TypeError, ValueError):
        return False


def _parse_address(word: str):
    if not _HEX_ADDRESS_RE.fullmatch(word):
        return None
    try:
        address = int(word, 16)
    except ValueError:
        return None
    if address == 0 or not _is_loaded_address(address):
        return None
    return address


def _word_at_column(line: str, column: int) -> str:
    """Return the identifier under a visible custom-viewer column."""
    plain = ida_lines.tag_remove(line or "")
    if not plain:
        return ""
    try:
        column = int(column)
    except (TypeError, ValueError):
        return ""

    column = min(max(column, 0), len(plain) - 1)
    if not _IDENTIFIER_CHAR_RE.fullmatch(plain[column]):
        if column > 0 and _IDENTIFIER_CHAR_RE.fullmatch(plain[column - 1]):
            column -= 1
        else:
            return ""

    start = column
    while start > 0 and _IDENTIFIER_CHAR_RE.fullmatch(plain[start - 1]):
        start -= 1
    end = column + 1
    while end < len(plain) and _IDENTIFIER_CHAR_RE.fullmatch(plain[end]):
        end += 1
    return plain[start:end]


def _colorize_c_line(line: str, semantic_colors: dict = None) -> str:
    """给一行 C 代码加 IDA 颜色标签，模拟原生 Hex-Rays 伪码窗口配色

    配色规则（与原生伪码窗口对齐）：
    - Hex-Rays 已识别的标识符: 复用原生伪码的颜色标签
    - 控制流关键字（if/for/while/return/break/continue等）: SCOLOR_KEYWORD
    - 类型关键字（int/void/char/struct等）: 默认色（不上色）
    - 函数调用名（printf/sub_401000等，后跟'('）: SCOLOR_CNAME
    - 全局符号（byte_/dword_/g_等IDA前缀）: SCOLOR_DNAME
    - 局部变量（v3/a1及语义明确的重命名）: SCOLOR_LOCNAME
    - 数字: SCOLOR_NUMBER
    - 字符串: SCOLOR_STRING
    - 字符串: SCOLOR_DSTR（与原生伪码的暗绿色一致）
    - 注释: SCOLOR_AUTOCMT（与原生伪码的自动注释一致）

    Args:
        line: 原始 C 代码行

    Returns:
        带颜色标签的行字符串
    """
    if not line.strip():
        return line

    semantic_colors = semantic_colors or {}

    # 控制流关键字（原生伪码只对这些上蓝色）
    FLOW_KEYWORDS = frozenset([
        'if', 'else', 'for', 'while', 'do', 'switch', 'case', 'default',
        'break', 'continue', 'return', 'goto'
    ])

    result = []
    i = 0
    n = len(line)

    while i < n:
        c = line[i]

        # 行注释 //
        if c == '/' and i + 1 < n and line[i + 1] == '/':
            result.append(ida_lines.COLSTR(line[i:], _comment_color()))
            break

        # 块注释 /* */
        if c == '/' and i + 1 < n and line[i + 1] == '*':
            end = line.find('*/', i + 2)
            if end == -1:
                result.append(ida_lines.COLSTR(line[i:], _comment_color()))
                break
            result.append(ida_lines.COLSTR(line[i:end + 2], _comment_color()))
            i = end + 2
            continue

        # 预处理指令 #
        if c == '#' and (i == 0 or line[:i].isspace()):
            result.append(ida_lines.COLSTR(line[i:], ida_lines.SCOLOR_KEYWORD))
            break

        # 字符串
        if c == '"':
            end = i + 1
            while end < n:
                if line[end] == '\\' and end + 1 < n:
                    end += 2
                elif line[end] == '"':
                    end += 1
                    break
                else:
                    end += 1
            # Hex-Rays pseudocode uses the darker data-string green here;
            # SCOLOR_STRING is the brighter instruction-string green.
            string_color = getattr(
                ida_lines, "SCOLOR_DSTR", ida_lines.SCOLOR_STRING
            )
            result.append(ida_lines.COLSTR(line[i:end], string_color))
            i = end
            continue

        # 字符常量
        if c == "'":
            end = i + 1
            while end < n:
                if line[end] == '\\' and end + 1 < n:
                    end += 2
                elif line[end] == "'":
                    end += 1
                    break
                else:
                    end += 1
            char_color = getattr(
                ida_lines, "SCOLOR_DCHAR",
                getattr(ida_lines, "SCOLOR_CHAR", ida_lines.SCOLOR_STRING),
            )
            result.append(ida_lines.COLSTR(line[i:end], char_color))
            i = end
            continue

        # 数字（含十六进制 0x、浮点）
        if c.isdigit():
            end = i + 1
            while end < n and (line[end].isalnum() or line[end] in '.xXuUlLfF'):
                end += 1
            result.append(line[i:end])
            i = end
            continue

        # 标识符
        if c.isalpha() or c == '_':
            end = i + 1
            while end < n and (line[end].isalnum() or line[end] == '_'):
                end += 1
            word = line[i:end]

            # 跳过标识符后的空白，检查是否为函数调用
            next_pos = end
            while next_pos < n and line[next_pos].isspace():
                next_pos += 1
            is_function_call = (next_pos < n and line[next_pos] == '(')

            # Prefer the colors assigned by Hex-Rays. This keeps the result
            # viewer aligned with the pseudocode view even after a local
            # variable has been renamed by the optimizer.
            mapped_color = semantic_colors.get(word)
            if mapped_color and mapped_color != getattr(ida_lines, "SCOLOR_DEFAULT", ""):
                result.append(_colorize_identifier(word, mapped_color))
            # 1. 控制流关键字（蓝色）
            elif word in FLOW_KEYWORDS or word in C_KEYWORDS:
                result.append(ida_lines.COLSTR(word, ida_lines.SCOLOR_KEYWORD))
            # 2. 类型保持默认色，和原生伪码的声明区域一致
            elif word in C_TYPES:
                result.append(word)
            # 3. 函数调用（库函数或sub_xxx等）
            elif is_function_call:
                code_name_color = getattr(
                    ida_lines,
                    "SCOLOR_CNAME",
                    getattr(ida_lines, "SCOLOR_IMPNAME", ida_lines.SCOLOR_DEFAULT),
                )
                result.append(ida_lines.COLSTR(word, code_name_color))
            # 4. IDA 全局符号（g_/byte_/dword_/off_/sub_/loc_等前缀）
            elif word.startswith(('g_', 'byte_', 'word_', 'dword_', 'qword_',
                                  'off_', 'sub_', 'loc_', 'asc_', 'stru_',
                                  'unk_', 'flt_', 'dbl_', 'nullsub_', 'aByte_')):
                result.append(ida_lines.COLSTR(word, ida_lines.SCOLOR_DNAME))
            # 5. IDA 原始局部变量（v3/a1等模式）
            elif _IDA_VAR_RE.match(word):
                result.append(ida_lines.COLSTR(word, ida_lines.SCOLOR_LOCNAME))
            # 6. 重命名后的局部变量（语义明确的下划线命名）
            # 只对明显的局部变量模式上色：全小写+下划线（input_buffer/scan_result）
            elif '_' in word and word.islower() and not word.startswith('__'):
                result.append(ida_lines.COLSTR(word, ida_lines.SCOLOR_LOCNAME))
            # 7. 其他（结构体名、普通标识符等保持默认色）
            else:
                result.append(word)

            i = end
            continue

        result.append(c)
        i += 1

    return ''.join(result)


class ResultPresenter:
    """在IDA中展示优化结果"""

    # Keep Python references alive for the lifetime of the custom viewers.
    # IDA owns the native widget, but callbacks are dispatched through the
    # Python object and therefore must not rely on a local variable surviving.
    _viewers = []

    @staticmethod
    def show_optimized_window(optimized: str, context: dict, stats: dict = None) -> bool:
        logger = get_logger()
        viewer = OptimizedCodeViewer(optimized, context, stats)
        title = viewer.build_title()

        if not viewer.Create(title):
            ResultPresenter.show_error("创建优化结果窗口失败")
            logger.error("创建优化结果窗口失败")
            return False

        ResultPresenter._viewers.append(viewer)
        if not viewer.Show():
            ResultPresenter._viewers.remove(viewer)
            ResultPresenter.show_error("显示优化结果窗口失败")
            logger.error("显示优化结果窗口失败")
            return False
        logger.debug(f"优化结果窗口已显示: {title}")
        return True

    @staticmethod
    def show_error(message: str):
        ida_kernwin.warning(f"[CHelper] {message}")

    @staticmethod
    def show_info(message: str):
        ida_kernwin.info(f"[CHelper] {message}")

    @staticmethod
    def print_to_output(message: str):
        print(f"[CHelper] {message}")


class ProgressDialog:
    """Output-window progress reporter kept for the synchronous path."""

    def __init__(self, title: str = "CHelper正在处理..."):
        self.title = title
        self.cancelled = False

    def __enter__(self):
        ResultPresenter.print_to_output(self.title)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def update(self, message: str):
        ResultPresenter.print_to_output(message)

    def check_cancelled(self) -> bool:
        return False


class OptimizedCodeViewer(ida_kernwin.simplecustviewer_t):
    """优化代码查看器 - 独立窗口，带 C 语法高亮

    增强功能：
    - C 语法高亮（关键字、类型、字符串、数字、注释、局部变量、全局符号）
    - 单击: 高亮当前行
    - 双击: 读取鼠标下的词，跳转到有效地址或 IDA 符号
    - IDA 查看器内置的搜索、滚动等操作
    """

    def __init__(self, optimized: str, context: dict, stats: dict = None):
        ida_kernwin.simplecustviewer_t.__init__(self)
        self.optimized = optimized
        self.context = context or {}
        self.stats = stats or {}
        self._lines = []
        self._current_line = -1

    def build_title(self) -> str:
        function_name = self.context.get("function_name", "unknown")
        address = self.context.get("address", "")
        prefix = (
            "CHelper 未通过安全检测"
            if self.context.get("result_kind") == "model_rejected_candidate"
            else "CHelper"
        )
        if address:
            return f"{prefix} - {function_name} @ {address}"
        return f"{prefix} - {function_name}"

    def _add_line(self, text: str):
        self._lines.append(text)
        semantic_colors = self.context.get("semantic_colors", {})
        display = (
            _colorize_c_line(text, semantic_colors) if text.strip() else " "
        )
        display = display if display else " "
        # COLOR_DEFAULT is the custom-viewer's bright blue foreground. Native
        # pseudocode uses SCOLOR_DEFAULT for unclassified text, so keep the
        # base color in the line's IDA syntax tags instead.
        if text.strip():
            display = ida_lines.COLSTR(display, ida_lines.SCOLOR_DEFAULT)
        self.AddLine(display)

    def Create(self, title: str = "") -> bool:
        if not title:
            title = self.build_title()

        if not ida_kernwin.simplecustviewer_t.Create(self, title):
            return False

        function_name = self.context.get("function_name", "")
        address = self.context.get("address", "")

        if function_name or address:
            result_kind = self.context.get("result_kind", "model_local_rename")
            label = (
                "CHelper 本地安全美化"
                if result_kind == "local_readability_fallback"
                else "CHelper 模型候选（未通过安全检测）"
                if result_kind == "model_rejected_candidate"
                else "CHelper 模型全量重写"
                if result_kind == "model_full_rewrite"
                else "CHelper 模型辅助命名"
            )
            self._add_line(f"// {label}")
            if result_kind == "model_rejected_candidate":
                self._add_line(
                    "// 警告: 此候选未被安全流程采用，仅供人工审阅，不会缓存或写回 IDA"
                )
            if function_name:
                self._add_line(f"// 函数: {function_name}")
            if address:
                self._add_line(f"// 地址: {address}")
            if self.stats:
                orig = self.stats.get("original_lines", "?")
                opt = self.stats.get("optimized_lines", "?")
                self._add_line(f"// 行数: {orig} -> {opt}")
            renamed_locals = self.context.get("renamed_locals", 0)
            if renamed_locals:
                self._add_line(f"// 已重命名局部变量: {renamed_locals}")
            self._add_line("")

        for line in self.optimized.split('\n'):
            self._add_line(line)

        return True

    def Show(self):
        return ida_kernwin.simplecustviewer_t.Show(self)

    def OnClose(self):
        try:
            if self in ResultPresenter._viewers:
                ResultPresenter._viewers.remove(self)
        except Exception:
            pass

    def OnKeydown(self, vkey, shift):
        """Handle shortcuts that IDA does not dispatch for custom viewers."""
        ctrl_mask = getattr(ida_kernwin, "VES_CTRL", 4)
        if shift & ctrl_mask and vkey in (ord("A"), ord("a")):
            try:
                # ``simplecustviewer_t`` exposes selection reading but no
                # selection setter. Trigger IDA's native action explicitly so
                # the visible range is selected and the normal Copy action can
                # consume it afterwards.
                if ida_kernwin.process_ui_action("SelectAll"):
                    return True
            except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
                get_logger().debug(f"Ctrl+A 全选失败: {exc}")
        return False

    def OnCursorPosChanged(self):
        """光标位置改变回调 - 用于跟踪当前行（比 OnClick 更可靠）

        Returns:
            Nothing
        """
        try:
            line_num = self.GetLineNo()
            if line_num >= 0 and line_num != self._current_line:
                self._current_line = line_num
        except Exception:
            pass

    def _get_clicked_word(self) -> str:
        """Read the word at the mouse position with viewer API fallbacks."""
        # This path uses the actual visible column and is reliable even when
        # color tags make GetCurrentWord(mouse=1) return an empty result.
        try:
            position = self.GetPos(1)
            line = self.GetCurrentLine(1, 1)
            if position and line:
                word = _word_at_column(line, position[1])
                if word:
                    return word
        except (AttributeError, IndexError, TypeError, ValueError):
            pass

        for mouse in (1, 0):
            try:
                word = self.GetCurrentWord(mouse)
            except (AttributeError, TypeError, ValueError):
                continue
            if word:
                return ida_lines.tag_remove(word).strip()
        return ""

    def OnDblClick(self, shift):
        """双击回调 - 读取鼠标下的词并跳转到有效目标

        只对已加载地址、IDA 名称和当前函数的精确名称执行跳转；普通
        局部变量、类型名和常量不会被误导向函数起点。

        Args:
            shift: 修饰键状态

        Returns:
            True 表示已成功处理跳转，否则返回 False
        """
        try:
            # Read the token under the mouse, not the previous keyboard cursor.
            word = self._get_clicked_word()
            if not word:
                return False

            # 剥离颜色标签
            word = ida_lines.tag_remove(word).strip()
            if not word or word in C_KEYWORDS or word in C_TYPES:
                return False

            # Do not treat ordinary constants such as 0x52 as code targets.
            addr = _parse_address(word)
            if addr is not None:
                return self._jump_to(addr, f"地址 {word}")

            # 2. 全局符号/函数名（用 IDA 名称解析）
            addr = _resolve_name(word)
            if addr is not None:
                return self._jump_to(addr, f"符号 {word}")

            # Use the saved address only when the clicked word is exactly the
            # current function name and IDA cannot resolve it by name.
            if word == self.context.get("function_name"):
                func_addr = _parse_address(str(self.context.get("address", "")))
                if func_addr is not None:
                    return self._jump_to(func_addr, f"当前函数 {word}")

        except Exception as e:
            logger = get_logger()
            logger.debug(f"OnDblClick 异常: {e}")

        return False

    @staticmethod
    def _jump_to(address: int, label: str) -> bool:
        """Jump through IDA's normal navigation path and report success."""
        try:
            result = idc.jumpto(address)
        except (AttributeError, TypeError, ValueError):
            result = ida_kernwin.jumpto(address)

        # IDAPython versions differ: jumpto() may return None on success.
        succeeded = result is None or bool(result)
        if succeeded:
            print(f"[CHelper] 跳转到{label} @ 0x{address:X}")
        return succeeded
