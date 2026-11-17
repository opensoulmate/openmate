"""
MCP 工具注册桥 — 插件后端能力统一注册为 MCP 工具

设计文档: docs/plugin-system-design.md §4
- 工具名 = <plugin-id>__<tool-name>（UI 面板、SoulMate 对话、流程引擎共用一份 handler）
- 注册/列取（MCP tools/list 形状）/调用（MCP tools/call 形状）/卸载
- 参数按 input_schema（JSON Schema 子集）校验；权限/审批闸在桥上把关
"""

from __future__ import annotations

import inspect
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from plugin.yaml_manifest import ToolSpec

logger = logging.getLogger("plugin.mcp_bridge")

# handler 签名: def handler(args: dict, ctx: ToolContext) -> dict（可为 async）
ToolHandler = Callable[[dict[str, Any], "ToolContext"], Any]


class ToolNotFound(KeyError):
    """工具未注册"""


class SchemaValidationError(ValueError):
    """入参不满足 input_schema"""


class ApprovalRequired(RuntimeError):
    """工具需要用户审批（权限/审批闸，设计文档 §6.2）"""

    def __init__(self, tool_name: str, reason: str = ""):
        self.tool_name = tool_name
        super().__init__(f"工具 {tool_name} 需要用户审批后方可调用{': ' + reason if reason else ''}")


@dataclass
class ToolContext:
    """传给工具 handler 的运行上下文（最小授权：能力都从这里走，逐项查权限）"""

    plugin_id: str
    data_dir: str = ""
    logger: logging.Logger = field(default_factory=lambda: logger)

    def require_permission(self, permission: str, manifest_permissions: set[str]) -> None:
        if permission not in manifest_permissions:
            raise PermissionError(f"插件 {self.plugin_id} 未声明权限 {permission}")


@dataclass
class RegisteredTool:
    plugin_id: str
    spec: ToolSpec
    handler: ToolHandler
    manifest_permissions: frozenset[str] = frozenset()

    @property
    def full_name(self) -> str:
        return f"{self.plugin_id}__{self.spec.name}"


class PluginToolRegistry:
    """插件 MCP 工具注册表（进程内虚拟 MCP server）"""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._approved_once: set[str] = set()  # on-first-use 已批准的工具

    # ── 注册 / 注销 ──

    def register(
        self,
        plugin_id: str,
        spec: ToolSpec,
        handler: ToolHandler,
        manifest_permissions: set[str] | frozenset[str] = frozenset(),
    ) -> RegisteredTool:
        if not callable(handler):
            raise ValueError(f"工具 {spec.name} 的 handler 不可调用: {spec.handler!r}")
        if "tool.register" not in manifest_permissions:
            raise PermissionError(f"插件 {plugin_id} 未声明 tool.register 权限，不能注册工具")
        tool = RegisteredTool(
            plugin_id=plugin_id,
            spec=spec,
            handler=handler,
            manifest_permissions=frozenset(manifest_permissions),
        )
        if tool.full_name in self._tools:
            raise ValueError(f"工具已注册: {tool.full_name}")
        self._tools[tool.full_name] = tool
        logger.info("MCP 工具已注册: %s (approval=%s)", tool.full_name, spec.approval)
        return tool

    def unregister_plugin(self, plugin_id: str) -> int:
        """插件禁用/卸载时注销其全部工具（对话与 UI 同步失去能力）"""
        names = [n for n, t in self._tools.items() if t.plugin_id == plugin_id]
        for name in names:
            del self._tools[name]
            self._approved_once.discard(name)
        return len(names)

    # ── MCP 契约 ──

    def list_tools(self, plugin_id: str | None = None) -> list[dict[str, Any]]:
        """MCP tools/list 形状，可直接喂 function-calling 工具表"""
        result = []
        for tool in self._tools.values():
            if plugin_id is not None and tool.plugin_id != plugin_id:
                continue
            result.append(
                {
                    "name": tool.full_name,
                    "description": tool.spec.description,
                    "inputSchema": tool.spec.input_schema,
                }
            )
        return sorted(result, key=lambda item: item["name"])

    def call_tool(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        approved: bool = False,
        ctx: ToolContext | None = None,
    ) -> dict[str, Any]:
        """调用工具（同步 handler；async handler 返回协程由调用方 await）"""
        tool = self._require_tool(name)
        args = dict(args or {})
        self._validate_args(tool, args)

        # 审批闸（设计文档 §6.2）
        approval = tool.spec.approval
        if approval == "always" and not approved:
            raise ApprovalRequired(tool.full_name, "工具声明 approval=always")
        if approval == "on-first-use":
            if tool.full_name not in self._approved_once:
                if not approved:
                    raise ApprovalRequired(tool.full_name, "首次调用需确认")
                self._approved_once.add(tool.full_name)

        if ctx is None:
            ctx = ToolContext(plugin_id=tool.plugin_id)
        result = tool.handler(args, ctx)
        if inspect.isawaitable(result):
            return {"awaitable": result}  # async handler：由 call_tool_async 消费
        return self._wrap_result(result)

    async def call_tool_async(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        approved: bool = False,
        ctx: ToolContext | None = None,
    ) -> dict[str, Any]:
        outcome = self.call_tool(name, args, approved=approved, ctx=ctx)
        if isinstance(outcome, dict) and "awaitable" in outcome:
            return self._wrap_result(await outcome["awaitable"])
        return outcome

    def call_tool_mcp(
        self,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        approved: bool = False,
        ctx: ToolContext | None = None,
    ) -> dict[str, Any]:
        """MCP tools/call 形状：结果包装为 content[{type:text}]，异常 → isError"""
        try:
            result = self.call_tool(name, args, approved=approved, ctx=ctx)
            if isinstance(result, dict) and "awaitable" in result:
                raise ValueError(f"工具 {name} 是异步 handler，请使用 call_tool_async")
            return {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            }
        except Exception as e:  # noqa: BLE001 — MCP 边界统一错误包装
            return {
                "content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}],
                "isError": True,
            }

    # ── 内部 ──

    def _require_tool(self, name: str) -> RegisteredTool:
        tool = self._tools.get(name)
        if tool is None:
            raise ToolNotFound(f"工具未注册: {name}")
        return tool

    def _validate_args(self, tool: RegisteredTool, args: dict[str, Any]) -> None:
        """JSON Schema 子集校验：required / properties.type / enum"""
        schema = tool.spec.input_schema or {}
        if schema.get("type", "object") != "object":
            raise SchemaValidationError(f"工具 {tool.full_name} input_schema 顶层应为 object")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise SchemaValidationError(f"工具 {tool.full_name} input_schema.properties 应为映射")
        required = schema.get("required", [])
        missing = [key for key in required if key not in args]
        if missing:
            raise SchemaValidationError(f"工具 {tool.full_name} 缺少必填参数: {missing}")
        for key, value in args.items():
            spec = properties.get(key)
            if spec is None:
                continue  # 允许额外参数（前向兼容）
            expected = spec.get("type")
            type_map = {"string": str, "number": (int, float), "integer": int, "boolean": bool, "object": dict, "array": list}
            if expected in type_map and not isinstance(value, type_map[expected]):
                raise SchemaValidationError(
                    f"工具 {tool.full_name} 参数 {key} 类型应为 {expected}，实际 {type(value).__name__}"
                )
            enum = spec.get("enum")
            if enum is not None and value not in enum:
                raise SchemaValidationError(f"工具 {tool.full_name} 参数 {key} 取值应为 {enum}，实际 {value!r}")

    @staticmethod
    def _wrap_result(result: Any) -> dict[str, Any]:
        if isinstance(result, dict):
            return result
        return {"result": result}


# ---- 全局注册表单例（dsl_api / app.py 粘合层共用）----

tool_registry = PluginToolRegistry()
