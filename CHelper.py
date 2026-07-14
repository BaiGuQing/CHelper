# -*- coding: utf-8 -*-
"""
CHelper - IDA Pro反编译代码优化插件
主插件文件
"""

import ida_kernwin
import ida_hexrays
import idaapi

from .config import get_config
from .handler import OptimizationHandler, HotkeyHandler
from .service_manager import init_service_manager, get_service_manager
from .logger import init_logger, get_logger
from .cache import init_cache
from .config_validator import ConfigValidator


# 插件元信息
PLUGIN_NAME = "CHelper"
PLUGIN_VERSION = "1.4.0"
PLUGIN_AUTHOR = "BaiGuQing"
ACTION_NAME = "chelper:optimize_code"
ACTION_NAME_FORCE = "chelper:optimize_code_force"  # 强制刷新动作


class _PopupHooks(ida_kernwin.UI_Hooks):
    """Attach actions to the actual pseudocode popup invocation.

    ``attach_action_to_popup(None, None, ...)`` is not a global menu
    registration API: the widget must be supplied for permanent actions. A UI
    hook gives us the concrete widget and popup handle for each invocation.
    """

    def populating_widget_popup(self, widget, popup, ctx):
        try:
            if ida_kernwin.get_widget_type(widget) == ida_kernwin.BWN_PSEUDOCODE:
                ida_kernwin.attach_action_to_popup(
                    widget, popup, ACTION_NAME, f"{PLUGIN_NAME}/", ida_kernwin.SETMENU_APP
                )
                ida_kernwin.attach_action_to_popup(
                    widget, popup, ACTION_NAME_FORCE, f"{PLUGIN_NAME}/", ida_kernwin.SETMENU_APP
                )
        except Exception as exc:
            print(f"[{PLUGIN_NAME}] 添加右键菜单动作失败: {exc}")
        return super().populating_widget_popup(widget, popup, ctx)


class CHelperPlugin(idaapi.plugin_t):
    """CHelper插件主类"""

    flags = idaapi.PLUGIN_KEEP
    comment = "使用本地大模型优化IDA反编译代码"
    help = "优化当前函数的伪C代码"
    wanted_name = PLUGIN_NAME
    wanted_hotkey = ""  # 通过action注册快捷键

    def init(self):
        """插件初始化"""
        self._popup_hooks = None
        self._action_handlers = []
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
        logger = get_logger()
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
        requested_hotkey = self.config.get("plugin.hotkey", "Ctrl+Alt+C")
        requested_force_hotkey = self.config.get("plugin.hotkey_force", "Ctrl+Alt+R")
        self.hotkey = self._resolve_hotkey(
            requested_hotkey,
            ("Ctrl+Alt+C", "Ctrl+Alt+Shift+C"),
        )
        self.hotkey_force = self._resolve_hotkey(
            requested_force_hotkey,
            ("Ctrl+Alt+R", "Ctrl+Alt+Shift+R"),
            reserved={self.hotkey},
        )

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
        if self.hotkey:
            print(f"[{PLUGIN_NAME}] 在反编译窗口按 {self.hotkey} 优化代码")
        else:
            print(f"[{PLUGIN_NAME}] 普通优化未绑定快捷键，请从右键菜单或 Shortcut editor 执行")
        if self.hotkey_force:
            print(f"[{PLUGIN_NAME}] 在反编译窗口按 {self.hotkey_force} 强制刷新（忽略缓存）")
        else:
            print(f"[{PLUGIN_NAME}] 强制刷新未绑定快捷键，请从右键菜单或 Shortcut editor 执行")
        print(f"[{PLUGIN_NAME}] LLM API: {self.config.get('llm.api_url')}")
        print(f"[{PLUGIN_NAME}] 模型: {self.config.get('llm.model')}")

        return idaapi.PLUGIN_KEEP

    def run(self, arg):
        """插件运行（通过菜单调用）

        Args:
            arg: 参数
        """
        self.handler.execute_async()

    def term(self):
        """插件卸载"""
        from .logger import get_logger
        logger = get_logger()

        if self._popup_hooks is not None:
            try:
                self._popup_hooks.unhook()
            except Exception as exc:
                logger.warning(f"注销右键菜单钩子失败: {exc}")
            self._popup_hooks = None

        # 注销动作
        self._unregister_actions()

        try:
            if self.handler:
                self.handler.shutdown()
        except Exception as exc:
            logger.warning(f"取消后台优化任务失败: {exc}")

        # 清理子进程
        try:
            from .service_manager import shutdown_service_manager
            shutdown_service_manager()
            logger.info("后台服务进程清理完成")
        except Exception as e:
            logger.warning(f"清理子进程失败: {e}")

        try:
            from .cache import reset_cache
            from .config import reset_config
            reset_cache()
            reset_config()
        except Exception as e:
            logger.warning(f"清理插件单例状态失败: {e}")

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

    @staticmethod
    def _normalize_hotkey(shortcut: str) -> str:
        return (shortcut or "").replace(" ", "").replace("-", "+").casefold()

    def _find_hotkey_conflict(self, shortcut: str):
        """Find an existing action with the requested shortcut when possible."""
        wanted = self._normalize_hotkey(shortcut)
        if not wanted:
            return None
        try:
            registered_actions = ida_kernwin.get_registered_actions()
            for action_name in registered_actions:
                if action_name in (ACTION_NAME, ACTION_NAME_FORCE):
                    continue
                assigned = ida_kernwin.get_action_shortcut(action_name)
                if self._normalize_hotkey(assigned) == wanted:
                    return action_name
        except Exception:
            # Older IDAPython builds may not expose shortcut enumeration.  In
            # that case action registration still works and IDA remains the
            # final authority for a user-customized key.
            return None
        return None

    def _resolve_hotkey(self, requested: str, fallbacks=(), reserved=None) -> str:
        """Prefer a configured shortcut, then choose a collision-free fallback."""
        reserved = {self._normalize_hotkey(key) for key in (reserved or set()) if key}
        legacy_defaults = {
            self._normalize_hotkey("Ctrl+Shift+C"): "Ctrl+Alt+C",
            self._normalize_hotkey("Ctrl+Shift+R"): "Ctrl+Alt+R",
        }
        migrated = legacy_defaults.get(self._normalize_hotkey(requested))
        if migrated:
            print(f"[{PLUGIN_NAME}] 旧快捷键 {requested} 与 IDA 内置动作冲突，改用 {migrated}")
            candidates = (migrated,) + tuple(fallbacks)
        else:
            candidates = (requested,) + tuple(fallbacks)
        seen = set()
        for candidate in candidates:
            normalized = self._normalize_hotkey(candidate)
            if not normalized or normalized in seen or normalized in reserved:
                continue
            seen.add(normalized)
            conflict = self._find_hotkey_conflict(candidate)
            if conflict is None:
                if candidate != requested:
                    print(
                        f"[{PLUGIN_NAME}] 快捷键 {requested} 已冲突，"
                        f"自动改用 {candidate}"
                    )
                return candidate
            print(f"[{PLUGIN_NAME}] 快捷键 {candidate} 与 {conflict} 冲突")

        print(f"[{PLUGIN_NAME}] 没有可用快捷键，请从 Shortcut editor 手动绑定")
        return ""

    def _register_actions(self) -> bool:
        """注册快捷键和菜单项

        Returns:
            成功返回True
        """
        # 创建普通优化 action 描述
        normal_handler = HotkeyHandler(self.handler, force_refresh=False)
        force_handler = HotkeyHandler(self.handler, force_refresh=True)
        self._action_handlers = [normal_handler, force_handler]

        action_desc = ida_kernwin.action_desc_t(
            ACTION_NAME,                    # 动作名称
            f"优化伪C代码 ({self.hotkey})" if self.hotkey else "优化伪C代码",  # 显示名称
            normal_handler,                  # 处理器
            self.hotkey,                    # 快捷键（从config读取）
            "使用本地大模型优化当前函数的反编译代码",  # 提示
            -1                              # 图标（-1为默认）
        )

        # 注册普通优化 action
        if not ida_kernwin.register_action(action_desc):
            self._action_handlers = []
            return False

        # 创建强制刷新 action 描述
        action_desc_force = ida_kernwin.action_desc_t(
            ACTION_NAME_FORCE,              # 动作名称
            (
                f"优化伪C代码（强制刷新 {self.hotkey_force}）"
                if self.hotkey_force else "优化伪C代码（强制刷新）"
            ),
            force_handler,                  # 处理器
            self.hotkey_force,              # 快捷键
            "忽略缓存，强制重新优化当前函数",  # 提示
            -1                              # 图标
        )

        # 注册强制刷新 action
        if not ida_kernwin.register_action(action_desc_force):
            ida_kernwin.unregister_action(ACTION_NAME)
            self._action_handlers = []
            return False

        self._popup_hooks = _PopupHooks()
        if not self._popup_hooks.hook():
            print(f"[{PLUGIN_NAME}] 警告：右键菜单钩子注册失败")
            self._popup_hooks = None

        return True

    def _unregister_actions(self):
        """注销快捷键和菜单"""
        ida_kernwin.unregister_action(ACTION_NAME)
        ida_kernwin.unregister_action(ACTION_NAME_FORCE)
        self._action_handlers = []


def PLUGIN_ENTRY():
    """IDA插件入口点

    Returns:
        插件实例
    """
    return CHelperPlugin()
