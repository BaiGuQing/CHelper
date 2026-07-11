# -*- coding: utf-8 -*-
"""
CHelper - IDA Pro反编译代码优化插件
主插件文件
"""

import ida_kernwin
import ida_hexrays
import idaapi

from config import get_config
from handler import OptimizationHandler, HotkeyHandler
from presenter import ResultPresenter
from service_manager import init_service_manager, get_service_manager
from logger import init_logger, get_logger
from cache import init_cache
from config_validator import ConfigValidator


# 插件元信息
PLUGIN_NAME = "CHelper"
PLUGIN_VERSION = "1.1.0"
PLUGIN_AUTHOR = "BaiGuQing"
ACTION_NAME = "chelper:optimize_code"
ACTION_NAME_FORCE = "chelper:optimize_code_force"  # 强制刷新动作


class CHelperPlugin(idaapi.plugin_t):
    """CHelper插件主类"""

    flags = idaapi.PLUGIN_KEEP
    comment = "使用本地大模型优化IDA反编译代码"
    help = "优化当前函数的伪C代码"
    wanted_name = PLUGIN_NAME
    wanted_hotkey = ""  # 通过action注册快捷键

    def init(self):
        """插件初始化"""
        # 检查IDA版本
        if not self._check_ida_version():
            print(f"[{PLUGIN_NAME}] IDA版本不兼容")
            return idaapi.PLUGIN_SKIP

        # 检查Hex-Rays反编译器
        if not ida_hexrays.init_hexrays_plugin():
            print(f"[{PLUGIN_NAME}] Hex-Rays反编译器未找到")
            return idaapi.PLUGIN_SKIP

        # 加载配置
        try:
            self.config = get_config()
            print(f"[{PLUGIN_NAME}] 配置加载成功")

            # 验证配置
            valid, errors = ConfigValidator.validate(self.config.config)
            if not valid:
                error_msg = ConfigValidator.get_friendly_error_message(errors)
                print(f"[{PLUGIN_NAME}] 配置验证失败:\n{error_msg}")
                ida_kernwin.warning(error_msg)
                # 配置错误仍然加载插件，但会有警告
        except Exception as e:
            print(f"[{PLUGIN_NAME}] 配置加载失败: {e}")
            return idaapi.PLUGIN_SKIP

        # 初始化日志系统
        try:
            init_logger(self.config)
            logger = get_logger()
            logger.info(f"{PLUGIN_NAME} v{PLUGIN_VERSION} 初始化中...")
        except Exception as e:
            print(f"[{PLUGIN_NAME}] 日志系统初始化失败: {e}")
            # 日志失败不影响插件加载

        # 初始化缓存系统
        try:
            if self.config.get("plugin.enable_cache", True):
                init_cache(self.config)
                logger.info("缓存系统已启用")
        except Exception as e:
            logger.warning(f"缓存系统初始化失败: {e}")
            # 缓存失败不影响插件加载

        # 创建处理器
        self.handler = OptimizationHandler()

        # 读取快捷键配置
        self.hotkey = self.config.get("plugin.hotkey", "Ctrl+Shift+C")
        self.hotkey_force = self.config.get("plugin.hotkey_force", "Ctrl+Shift+R")  # 强制刷新快捷键

        # 启动 LLM 服务管理（后台线程检测/启动 vLLM，不阻塞 IDA）
        auto_start = self.config.get("llm.auto_start", False)
        if auto_start:
            print(f"[{PLUGIN_NAME}] auto_start 已开启，正在后台检测/启动模型服务...")
        init_service_manager(self.config)

        # 注册快捷键和菜单
        if not self._register_actions():
            print(f"[{PLUGIN_NAME}] 注册快捷键失败")
            return idaapi.PLUGIN_SKIP

        print(f"[{PLUGIN_NAME}] v{PLUGIN_VERSION} 加载成功")
        print(f"[{PLUGIN_NAME}] 在反编译窗口按 {self.hotkey} 优化代码")
        print(f"[{PLUGIN_NAME}] 在反编译窗口按 {self.hotkey_force} 强制刷新（忽略缓存）")
        print(f"[{PLUGIN_NAME}] LLM API: {self.config.get('llm.api_url')}")
        print(f"[{PLUGIN_NAME}] 模型: {self.config.get('llm.model')}")

        return idaapi.PLUGIN_KEEP

    def run(self, arg):
        """插件运行（通过菜单调用）

        Args:
            arg: 参数
        """
        self.handler.execute()

    def term(self):
        """插件卸载"""
        from logger import get_logger
        logger = get_logger()

        # 注销动作
        self._unregister_actions()

        # 清理子进程
        try:
            svc = get_service_manager()
            if svc and svc._process:
                logger.info(f"正在终止后台服务进程 (PID: {svc._process.pid})...")
                svc._process.terminate()
                try:
                    svc._process.wait(timeout=5)
                    logger.info("后台服务进程已终止")
                except Exception:
                    svc._process.kill()
                    logger.warning("后台服务进程强制终止")
        except Exception as e:
            logger.warning(f"清理子进程失败: {e}")

        logger.info(f"{PLUGIN_NAME} 已卸载")
        print(f"[{PLUGIN_NAME}] 已卸载")

    def _check_ida_version(self) -> bool:
        """检查IDA版本是否支持

        IDA SDK 版本号编码：9.0 -> 900, 8.4 -> 840, 7.7 -> 770
        因此 //100 得到主版本号（9、8、7...），需 >= 9

        Returns:
            支持返回True
        """
        sdk_version = idaapi.IDA_SDK_VERSION
        major = sdk_version // 100

        # 支持IDA 9.0及以上
        if major >= 9:
            return True

        minor = (sdk_version % 100) // 10
        print(f"[{PLUGIN_NAME}] IDA版本 {major}.{minor} 不支持，需要9.0+")
        return False

    def _register_actions(self) -> bool:
        """注册快捷键和菜单项

        Returns:
            成功返回True
        """
        # 创建普通优化 action 描述
        action_desc = ida_kernwin.action_desc_t(
            ACTION_NAME,                    # 动作名称
            "优化伪C代码",                   # 显示名称
            HotkeyHandler(self.handler, force_refresh=False),    # 处理器
            self.hotkey,                    # 快捷键（从config读取）
            "使用本地大模型优化当前函数的反编译代码",  # 提示
            -1                              # 图标（-1为默认）
        )

        # 注册普通优化 action
        if not ida_kernwin.register_action(action_desc):
            return False

        # 创建强制刷新 action 描述
        action_desc_force = ida_kernwin.action_desc_t(
            ACTION_NAME_FORCE,              # 动作名称
            "优化伪C代码（强制刷新）",        # 显示名称
            HotkeyHandler(self.handler, force_refresh=True),  # 处理器
            self.hotkey_force,              # 快捷键
            "忽略缓存，强制重新优化当前函数",  # 提示
            -1                              # 图标
        )

        # 注册强制刷新 action
        if not ida_kernwin.register_action(action_desc_force):
            return False

        # 附加到反编译窗口的右键菜单
        if not ida_kernwin.attach_action_to_popup(
            None,  # 所有窗口
            None,  # 无父菜单
            ACTION_NAME,
            f"{PLUGIN_NAME}/",
            ida_kernwin.SETMENU_APP
        ):
            print(f"[{PLUGIN_NAME}] 警告：添加到右键菜单失败")

        # 附加强制刷新到右键菜单
        if not ida_kernwin.attach_action_to_popup(
            None,
            None,
            ACTION_NAME_FORCE,
            f"{PLUGIN_NAME}/",
            ida_kernwin.SETMENU_APP
        ):
            print(f"[{PLUGIN_NAME}] 警告：添加强制刷新到右键菜单失败")

        return True

    def _unregister_actions(self):
        """注销快捷键和菜单"""
        ida_kernwin.unregister_action(ACTION_NAME)
        ida_kernwin.unregister_action(ACTION_NAME_FORCE)


def PLUGIN_ENTRY():
    """IDA插件入口点

    Returns:
        插件实例
    """
    return CHelperPlugin()
