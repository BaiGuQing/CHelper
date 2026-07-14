"""
CHelper - IDA Pro反编译代码优化插件

IDA 加载子目录插件时会把本目录作为 Python 包导入（import CHelper）。

包内模块全部使用相对导入，避免与 plugins/ 下其他插件的 config、logger
等通用模块名冲突。入口采用延迟导入：这既让 IDA 能取得 PLUGIN_ENTRY，也
允许 cache、processor 等不依赖 IDA 的模块被独立测试。
"""

__version__ = "1.4.0"
__author__ = "BaiGuQing"


def PLUGIN_ENTRY():
    """IDA 插件入口（延迟加载 IDA 相关模块）。"""
    from .CHelper import PLUGIN_ENTRY as plugin_entry
    return plugin_entry()
