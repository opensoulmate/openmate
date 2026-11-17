"""
插件数据模型定义

包含插件清单(Manifest)、插件实例(Instance)、Hook上下文(Context)等核心数据结构。
所有模型均为纯Python dataclass，用于插件系统的数据交换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional, Union


# ---- 插件状态枚举 ----

class PluginState(str, Enum):
    """插件生命周期状态"""
    INSTALLED = "installed"     # 已安装，未启用
    ENABLED = "enabled"         # 已启用，Hook可触发
    DISABLED = "disabled"       # 已禁用，Hook不触发
    UNINSTALLED = "uninstalled" # 已卸载，待清理


# ---- 权限定义 ----

# 系统内置权限列表，插件声明时只能使用这些权限
KNOWN_PERMISSIONS: set[str] = {
    "session.create",    # 创建会话
    "session.read",      # 读取会话信息
    "session.delete",    # 删除会话
    "tool.call",         # 调用工具
    "tool.read",         # 读取工具信息
    "event.subscribe",   # 订阅事件
    "event.emit",        # 发射事件
    "config.read",       # 读取配置
    "config.write",      # 修改配置
    # ── plugin.yaml 清单式插件（docs/plugin-system-design.md §6）──
    "ui.serve",          # 提供 UI DSL / 自定义 HTML 面板
    "tool.register",     # 注册 MCP 工具
    "flow.register",     # 注册流程定义
    "data.read",         # 读自有插件数据目录
    "data.write",        # 写自有插件数据目录
    "knowledge.read",    # 知识库检索
    "export.write",      # 导出文件
    "notify.send",       # 发送通知
}


# ---- 插件清单 ----

@dataclass(frozen=True)
class PluginManifest:
    """
    插件清单 — 插件的静态元数据描述

    由插件开发者定义，描述插件的名称、版本、所需权限和Hook点。
    作为不可变对象，实例化后不允许修改。

    属性:
        name: 插件唯一标识名（小写+连字符，如 "my-plugin"）
        version: 语义化版本号（如 "1.0.0"）
        author: 作者名
        description: 一句话描述
        hooks: 插件要拦截的Hook点列表，合法值见 HOOK_POINTS
        permissions: 插件声明需要的权限列表，合法值见 KNOWN_PERMISSIONS
    """
    name: str
    version: str
    author: str
    description: str = ""
    hooks: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """校验清单字段合法性"""
        if not self.name or not self.name.strip():
            raise ValueError("插件名称不能为空")
        if not self.version:
            raise ValueError("插件版本不能为空")
        # 校验权限声明是否在已知权限列表中
        unknown = set(self.permissions) - KNOWN_PERMISSIONS
        if unknown:
            raise ValueError(f"未知权限: {unknown}，合法权限: {KNOWN_PERMISSIONS}")


# ---- 合法Hook拦截点 ----
# Pi 取长补短 #1 钩子面映射（六类钩子）：
#   beforeToolCall   → before_tool_call（可 block=cancel / 改参=data["args"]）
#   afterToolCall    → after_tool_call（可覆盖结果=data["result"] / 置 task_complete）
#   finishTurn       → finish_turn（end/continue 续轮——SettleGate 有界消费防打转）
#   prepareRequest   → prepare_request（LLM 请求面可改 messages/system_prompt/tools）
#   steering/followUp → steering.py 人机插话队列（getSteeringMessages/getFollowUpMessages
#                      同位：turn 间隙 drain 注入，既有人机对话通道，不重复建钩子）
HOOK_POINTS: set[str] = {
    "before_session_create",
    "after_session_create",
    "before_tool_call",
    "after_tool_call",
    "prepare_request",
    "finish_turn",
    "on_event",
}


# ---- Hook上下文 ----

@dataclass
class HookContext:
    """
    Hook触发时的上下文数据

    传递给Hook处理函数，包含触发点名称、携带数据和控制标志。

    属性:
        hook_point: 触发的Hook点名称
        data: Hook携带的数据字典，不同Hook点内容不同
        cancelled: 是否被拦截取消（before_*类Hook可设为True阻止操作）
        plugin_name: 触发Hook的插件名（仅插件自身Hook触发时填充）
    """
    hook_point: str
    data: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
    cancel_reason: str = ""
    plugin_name: str = ""

    def cancel(self, reason: str = "") -> None:
        """标记当前操作被取消（仅before_*类Hook有效）；reason 显式可见（Pi #1）。"""
        if not self.hook_point.startswith("before_"):
            raise RuntimeError(f"只有before_*类Hook支持cancel操作，当前: {self.hook_point}")
        self.cancelled = True
        if reason:
            self.cancel_reason = str(reason)


# ---- Hook处理函数类型 ----

# Hook处理函数签名：接受HookContext，可为同步或异步
HookHandler = Union[
    Callable[[HookContext], None],
    Callable[[HookContext], Coroutine[Any, Any, None]],
]


# ---- Hook注册项 ----

@dataclass
class HookEntry:
    """
    Hook注册条目 — 绑定处理函数与其元数据

    属性:
        hook_point: Hook点名称
        handler: 处理函数
        priority: 优先级，数值越小越先执行（默认100）
        plugin_name: 所属插件名（框架级Hook为空字符串）
    """
    hook_point: str
    handler: HookHandler
    priority: int = 100
    plugin_name: str = ""

    def __post_init__(self) -> None:
        if self.hook_point not in HOOK_POINTS:
            raise ValueError(f"未知Hook点: {self.hook_point}，合法值: {HOOK_POINTS}")


# ---- 插件实例 ----

@dataclass
class PluginInstance:
    """
    插件运行时实例 — 管理插件的状态和注册的Hook

    在插件被加载时创建，跟踪生命周期状态和该插件注册的所有Hook。

    属性:
        manifest: 插件清单
        state: 当前生命周期状态
        registered_hooks: 该插件已注册的Hook条目列表
        module_path: 插件Python模块路径（用于动态加载）
    """
    manifest: PluginManifest
    state: PluginState = PluginState.INSTALLED
    registered_hooks: list[HookEntry] = field(default_factory=list)
    module_path: str = ""

    @property
    def name(self) -> str:
        """快捷访问插件名"""
        return self.manifest.name

    @property
    def is_enabled(self) -> bool:
        """插件是否处于启用状态"""
        return self.state == PluginState.ENABLED

    def transition(self, new_state: PluginState) -> None:
        """
        状态流转校验

        合法流转路径:
        - INSTALLED -> ENABLED
        - ENABLED -> DISABLED
        - DISABLED -> ENABLED
        - * -> UNINSTALLED（任何状态均可卸载）
        """
        valid_transitions: dict[PluginState, set[PluginState]] = {
            PluginState.INSTALLED: {PluginState.ENABLED, PluginState.UNINSTALLED},
            PluginState.ENABLED: {PluginState.DISABLED, PluginState.UNINSTALLED},
            PluginState.DISABLED: {PluginState.ENABLED, PluginState.UNINSTALLED},
            PluginState.UNINSTALLED: set(),
        }
        allowed = valid_transitions.get(self.state, set())
        if new_state not in allowed:
            raise RuntimeError(
                f"非法状态流转: {self.state.value} -> {new_state.value}，"
                f"允许的目标: {[s.value for s in allowed]}"
            )
        self.state = new_state
