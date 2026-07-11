"""
CHelper - IDA Pro反编译代码优化插件

IDA 加载子目录插件时会把本目录作为 Python 包导入（import CHelper）。
因此不能用 `from CHelper import PLUGIN_ENTRY`——那样 Python 会从包对象
（即本 __init__.py 的命名空间）取 PLUGIN_ENTRY，而它不存在，导致
ImportError 被 IDA 静默吞掉，插件完全不加载。

本文件的作用：
1. 把插件目录加入 sys.path，让 CHelper.py 及其子模块里的扁平导入
   （from config import ... 等）能正常工作
2. 用 importlib 以独立模块名加载 CHelper.py，避免与包名 CHelper 冲突
3. 导出 PLUGIN_ENTRY 供 IDA 调用
"""

import os
import sys
import importlib.util

__version__ = "1.1.0"
__author__ = "BaiGuQing"

# 1. 把插件目录加入 sys.path
_plugin_dir = os.path.dirname(os.path.abspath(__file__))
if _plugin_dir not in sys.path:
    sys.path.insert(0, _plugin_dir)

# 2. 显式加载 CHelper.py（用独立模块名 _chelper_main，避免和包名 CHelper 冲突）
_spec = importlib.util.spec_from_file_location(
    "_chelper_main",
    os.path.join(_plugin_dir, "CHelper.py")
)
_main_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_main_module)

# 3. 导出入口函数
PLUGIN_ENTRY = _main_module.PLUGIN_ENTRY
