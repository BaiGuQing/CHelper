# -*- coding: utf-8 -*-
"""
代码提取器 - 从IDA中提取伪C代码和上下文信息
"""

import ida_hexrays
import ida_kernwin
import ida_funcs
import ida_name
import ida_nalt
import ida_typeinf
import idaapi


class CodeExtractor:
    """从IDA Hex-Rays反编译器中提取伪C代码"""

    @staticmethod
    def is_decompiler_available() -> bool:
        """检查Hex-Rays反编译器是否可用

        Returns:
            可用返回True
        """
        return ida_hexrays.init_hexrays_plugin()

    @staticmethod
    def get_current_function():
        """获取当前光标所在的函数

        Returns:
            函数对象(ida_funcs.func_t)，失败返回None
        """
        # 获取当前地址
        ea = ida_kernwin.get_screen_ea()
        if ea == idaapi.BADADDR:
            return None

        # 获取函数
        func = ida_funcs.get_func(ea)
        return func

    @staticmethod
    def get_pseudocode(func) -> tuple:
        """获取函数的伪C代码

        Args:
            func: 函数对象(ida_funcs.func_t)

        Returns:
            (伪C代码字符串, cfunc对象)，失败返回(None, None)
        """
        if func is None:
            return None, None

        try:
            # 反编译函数
            cfunc = ida_hexrays.decompile(func.start_ea)
            if cfunc is None:
                return None, None

            # 获取伪C代码
            pseudocode = str(cfunc)
            return pseudocode, cfunc

        except ida_hexrays.DecompilationFailure as e:
            print(f"[CHelper] 反编译失败: {e}")
            return None, None

    @staticmethod
    def get_function_name(func) -> str:
        """获取函数名

        Args:
            func: 函数对象

        Returns:
            函数名
        """
        if func is None:
            return ""

        return ida_name.get_ea_name(func.start_ea)

    @staticmethod
    def get_function_type(func) -> str:
        """获取函数类型签名

        Args:
            func: 函数对象

        Returns:
            函数类型字符串
        """
        if func is None:
            return ""

        try:
            tinfo = ida_typeinf.tinfo_t()
            if ida_nalt.get_tinfo(tinfo, func.start_ea):
                return str(tinfo)
        except:
            pass

        return ""

    @staticmethod
    def get_local_types(cfunc) -> str:
        """获取函数中使用的本地类型定义

        Args:
            cfunc: cfunc对象

        Returns:
            类型定义字符串
        """
        if cfunc is None:
            return ""

        types_list = []

        try:
            # 获取局部变量类型
            lvars = cfunc.get_lvars()
            for lvar in lvars:
                tinfo = lvar.type()
                if tinfo:
                    type_str = str(tinfo)
                    if type_str and type_str not in types_list:
                        types_list.append(type_str)

            return "\n".join(types_list)

        except Exception as e:
            print(f"[CHelper] 获取类型信息失败: {e}")
            return ""

    @staticmethod
    def extract_context(func, cfunc) -> dict:
        """提取完整的上下文信息

        Args:
            func: 函数对象
            cfunc: cfunc对象

        Returns:
            包含上下文信息的字典
        """
        context = {
            "function_name": CodeExtractor.get_function_name(func),
            "function_type": CodeExtractor.get_function_type(func),
            "types": CodeExtractor.get_local_types(cfunc),
            "address": f"0x{func.start_ea:X}" if func else "",
        }

        return context

    @staticmethod
    def get_current_pseudocode() -> tuple:
        """一键获取当前函数的伪C代码和上下文

        Returns:
            (伪C代码, 上下文字典, cfunc对象)
        """
        # 检查反编译器
        if not CodeExtractor.is_decompiler_available():
            print("[CHelper] Hex-Rays反编译器不可用")
            return None, None, None

        # 获取当前函数
        func = CodeExtractor.get_current_function()
        if func is None:
            print("[CHelper] 未找到当前函数")
            return None, None, None

        # 获取伪C代码
        pseudocode, cfunc = CodeExtractor.get_pseudocode(func)
        if pseudocode is None:
            print("[CHelper] 获取伪C代码失败")
            return None, None, None

        # 提取上下文
        context = CodeExtractor.extract_context(func, cfunc)

        return pseudocode, context, cfunc

    @staticmethod
    def validate_pseudocode_size(pseudocode: str, max_size: int) -> bool:
        """验证伪C代码大小是否在允许范围内

        Args:
            pseudocode: 伪C代码
            max_size: 最大允许大小（字符数）

        Returns:
            合法返回True
        """
        if pseudocode is None:
            return False

        return len(pseudocode) <= max_size
