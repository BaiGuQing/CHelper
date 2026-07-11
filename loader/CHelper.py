# -*- coding: utf-8 -*-
"""
CHelper 插件加载器

IDA 9.x 只自动加载 plugins 目录下直接的 .py 文件，不扫描子目录。
本文件作为入口，导入同名的 CHelper/ 子目录包（与 patching.py 的模式一致）。

部署结构：
  plugins/
    CHelper.py          <- 本文件（IDA 加载入口）
    CHelper/            <- 插件包目录
      __init__.py       <- 包初始化，导出 PLUGIN_ENTRY
      CHelper.py        <- 主插件逻辑
      config.py, ...
      VibeThinker-3B/   <- 模型权重
"""

import sys
import os

# 确保 plugins 目录在 sys.path 中（IDA 通常已添加，这里兜底）
_plugins_dir = os.path.dirname(os.path.abspath(__file__))
if _plugins_dir not in sys.path:
    sys.path.insert(0, _plugins_dir)

# 导入 CHelper 包（CHelper/ 子目录）
# Python 包（目录+__init__.py）优先级高于同名 .py 文件，不会冲突
import CHelper


def PLUGIN_ENTRY():
    """IDA 插件入口点"""
    return CHelper.PLUGIN_ENTRY()
