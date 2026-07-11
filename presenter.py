# -*- coding: utf-8 -*-
"""
结果展示器 - 在IDA中显示优化结果

使用 simplecustviewer_t 创建独立窗口显示优化后的代码，
不 hook、不修改原始反编译缓存，不影响已有的伪代码窗口。
通过 IDA 颜色标签实现 C 语法高亮，外观接近原生伪代码窗口。
支持 Ctrl+A 全选复制、双击跳转定位等增强交互。
"""

import re
import ida_kernwin
import ida_lines
import idc

from constants import C_KEYWORDS, C_TYPES, IDA_VAR_PATTERN
from logger import get_logger


_IDA_VAR_RE = re.compile(IDA_VAR_PATTERN)


def _colorize_c_line(line: str) -> str:
    """给一行 C 代码加 IDA 颜色标签

    使用与 Hex-Rays 伪代码窗口一致的颜色方案：
    - 关键字/类型: SCOLOR_KEYWORD（蓝色）
    - 局部变量: SCOLOR_LOCNAME（绿色）
    - 数字: SCOLOR_NUMBER（青色）
    - 字符串: SCOLOR_STRING
    - 注释: SCOLOR_REGCMT（灰色）
    - 其他: 默认色

    Args:
        line: 原始 C 代码行

    Returns:
        带颜色标签的行字符串
    """
    if not line.strip():
        return line

    result = []
    i = 0
    n = len(line)

    while i < n:
        c = line[i]

        # 行注释 //
        if c == '/' and i + 1 < n and line[i + 1] == '/':
            result.append(ida_lines.COLSTR(line[i:], ida_lines.SCOLOR_REGCMT))
            break

        # 块注释 /* */ (同行)
        if c == '/' and i + 1 < n and line[i + 1] == '*':
            end = line.find('*/', i + 2)
            if end == -1:
                result.append(ida_lines.COLSTR(line[i:], ida_lines.SCOLOR_REGCMT))
                break
            result.append(ida_lines.COLSTR(line[i:end + 2], ida_lines.SCOLOR_REGCMT))
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
            result.append(ida_lines.COLSTR(line[i:end], ida_lines.SCOLOR_STRING))
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
            result.append(ida_lines.COLSTR(line[i:end], ida_lines.SCOLOR_STRING))
            i = end
            continue

        # 数字（含十六进制 0x、浮点）
        if c.isdigit() or (c == '-' and i + 1 < n and line[i + 1].isdigit()):
            end = i + 1
            while end < n and (line[end].isalnum() or line[end] in '.xXuUlLfF'):
                end += 1
            result.append(ida_lines.COLSTR(line[i:end], ida_lines.SCOLOR_NUMBER))
            i = end
            continue

        # 标识符
        if c.isalpha() or c == '_':
            end = i + 1
            while end < n and (line[end].isalnum() or line[end] == '_'):
                end += 1
            word = line[i:end]

            if word in C_KEYWORDS or word in C_TYPES:
                result.append(ida_lines.COLSTR(word, ida_lines.SCOLOR_KEYWORD))
            elif _IDA_VAR_RE.match(word):
                result.append(ida_lines.COLSTR(word, ida_lines.SCOLOR_LOCNAME))
            else:
                result.append(word)
            i = end
            continue

        result.append(c)
        i += 1

    return ''.join(result)


class ResultPresenter:
    """在IDA中展示优化结果"""

    @staticmethod
    def show_optimized_window(optimized: str, context: dict, stats: dict = None) -> bool:
        logger = get_logger()
        viewer = OptimizedCodeViewer(optimized, context, stats)
        title = viewer.build_title()

        if not viewer.Create(title):
            ResultPresenter.show_error("创建优化结果窗口失败")
            logger.error("创建优化结果窗口失败")
            return False

        viewer.Show()
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
    """进度对话框"""

    def __init__(self, title: str = "CHelper正在处理..."):
        self.title = title
        self.cancelled = False

    def __enter__(self):
        ida_kernwin.show_wait_box(self.title)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        ida_kernwin.hide_wait_box()

    def update(self, message: str):
        ida_kernwin.replace_wait_box(message)

    def check_cancelled(self) -> bool:
        return ida_kernwin.user_cancelled()


class OptimizedCodeViewer(ida_kernwin.simplecustviewer_t):
    """优化代码查看器 - 独立窗口，带 C 语法高亮

    增强功能：
    - C 语法高亮（关键字、类型、字符串、数字、注释、局部变量）
    - Ctrl+A: 全选并复制全部代码到剪贴板
    - 双击: 点击地址(0x...)或符号(byte_XXX/sub_XXX/loc_XXX等)跳转定位
    - IDA 查看器内置的搜索、复制、滚动等操作
    """

    def __init__(self, optimized: str, context: dict, stats: dict = None):
        ida_kernwin.simplecustviewer_t.__init__(self)
        self.optimized = optimized
        self.context = context or {}
        self.stats = stats or {}
        self._lines = []

    def build_title(self) -> str:
        function_name = self.context.get("function_name", "unknown")
        address = self.context.get("address", "")
        if address:
            return f"CHelper - {function_name} @ {address}"
        return f"CHelper - {function_name}"

    def _add_line(self, text: str):
        self._lines.append(text)
        display = _colorize_c_line(text) if text.strip() else " "
        self.AddLine(display if display else " ")

    def Create(self, title: str = "") -> bool:
        if not title:
            title = self.build_title()

        if not ida_kernwin.simplecustviewer_t.Create(self, title):
            return False

        function_name = self.context.get("function_name", "")
        address = self.context.get("address", "")

        if function_name or address:
            self._add_line("// CHelper 优化结果")
            if function_name:
                self._add_line(f"// 函数: {function_name}")
            if address:
                self._add_line(f"// 地址: {address}")
            if self.stats:
                orig = self.stats.get("original_lines", "?")
                opt = self.stats.get("optimized_lines", "?")
                self._add_line(f"// 行数: {orig} -> {opt}")
            self._add_line("")

        for line in self.optimized.split('\n'):
            self._add_line(line)

        return True

    def Show(self):
        return ida_kernwin.simplecustviewer_t.Show(self)

    def OnKeydown(self, vkey, shift):
        ctrl = bool(shift & 2)

        if ctrl and vkey == ord('A'):
            ida_kernwin.copy_to_clipboard(self.optimized)
            print("[CHelper] 已复制全部代码到剪贴板")
            logger = get_logger()
            logger.debug("用户通过 Ctrl+A 复制全部代码")
            return True

        return False

    def OnDblClick(self, shift):
        """双击回调 - 跳转到光标所在词对应的地址

        GetCurrentWord() 返回的词可能含颜色标签，用 tag_remove 剥离。
        依次尝试：十六进制地址 -> IDA 符号名 -> 任意可解析名称。

        Args:
            shift: 修饰键状态

        Returns:
            True 表示已跳转，False 表示未处理
        """
        try:
            word = self.GetCurrentWord()
            if not word:
                return False

            # 剥离颜色标签
            word = ida_lines.tag_remove(word)
            if not word:
                return False

            # 十六进制地址
            if word.startswith("0x") or word.startswith("0X"):
                try:
                    addr = int(word, 16)
                    if addr != 0 and ida_kernwin.jumpto(addr):
                        return True
                except (ValueError, TypeError):
                    pass

            # 用 IDA 的名称解析查找地址（涵盖 sub_/loc_/byte_/dword_/off_ 等所有命名符号）
            addr = idc.get_name_ea_simple(word)
            if addr != idc.BADADDR:
                if ida_kernwin.jumpto(addr):
                    return True

            # 尝试去掉可能的 IDA 前缀变体
            for prefix in ("byte_", "word_", "dword_", "qword_", "off_", "unk_", "asc_"):
                if word.startswith(prefix):
                    addr = idc.get_name_ea_simple(word)
                    if addr != idc.BADADDR and ida_kernwin.jumpto(addr):
                        return True
                    break

        except Exception:
            pass
        return False
