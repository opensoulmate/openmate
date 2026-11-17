"""
插件 HTTP 面 — FastAPI 路由工厂（/plugins 汇总、DSL 页面、工具调用）

设计文档: docs/plugin-system-design.md §1.1 / §4
- build_plugin_router(descriptor, registry)：每插件一个子路由
  GET  /plugins/{id}/manifest      → 前端 manifest 形状
  GET  /plugins/{id}/ui/{page}     → UI DSL 页面 JSON（装载时已校验，这里带校验兜底）
  POST /plugins/{id}/tools/{tool}  → 调用 MCP 工具（UI 与对话共用的同一 handler）
- build_plugins_index(...)：GET /plugins 响应（含 legacy plugin.json 插件合并）
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin.mcp_bridge import (
    ApprovalRequired,
    PluginToolRegistry,
    SchemaValidationError,
    ToolContext,
    ToolNotFound,
)
from plugin.scanner import PluginDescriptor
from plugin.ui_dsl import DslError, validate_dsl_page

logger = logging.getLogger("plugin.dsl_api")


class ToolCallRequest(PydanticBaseModel):
    args: dict[str, Any] = {}
    approved: bool = False  # 审批闸：用户在审批弹窗确认后置 true


def load_dsl_page(descriptor: PluginDescriptor, page_id: str) -> dict[str, Any]:
    """读取并校验 UI DSL 页面；返回可直接下发前端的 JSON"""
    page_path = descriptor.page_path(page_id)
    if page_path is None:
        raise HTTPException(status_code=404, detail=f"页面不存在: {page_id}")
    page_spec = next(p for p in descriptor.manifest.pages if p.id == page_id)
    if page_spec.dsl is None:
        raise HTTPException(status_code=404, detail=f"页面 {page_id} 为 HTML 面板（层②，PoC 未实现运行时）")
    try:
        raw = json.loads(page_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(status_code=500, detail=f"DSL 读取失败: {e}")
    try:
        validate_dsl_page(raw, source=f"{descriptor.id}/ui/{page_id}")
    except DslError as e:
        raise HTTPException(status_code=500, detail=f"DSL 校验失败: {e}")
    return raw


def build_plugin_router(descriptor: PluginDescriptor, registry: PluginToolRegistry) -> APIRouter:
    """为单个插件生成 FastAPI 子路由"""
    plugin_id = descriptor.manifest.id
    router = APIRouter(prefix=f"/plugins/{plugin_id}", tags=[f"plugin:{plugin_id}"])

    @router.get("/manifest")
    async def get_manifest() -> dict[str, Any]:
        return descriptor.manifest.to_frontend_manifest()

    @router.get("/ui/{page_id}")
    async def get_ui_page(page_id: str) -> dict[str, Any]:
        return load_dsl_page(descriptor, page_id)

    @router.post("/tools/{tool_name}")
    async def call_tool(tool_name: str, req: ToolCallRequest) -> dict[str, Any]:
        full_name = f"{plugin_id}__{tool_name}"
        ctx = ToolContext(plugin_id=plugin_id, data_dir=str(descriptor.dir_path))
        try:
            return await registry.call_tool_async(full_name, req.args, approved=req.approved, ctx=ctx)
        except ApprovalRequired as e:
            raise HTTPException(status_code=409, detail={"error": "approval_required", "message": str(e)})
        except ToolNotFound as e:
            raise HTTPException(status_code=404, detail=str(e))
        except (SchemaValidationError, PermissionError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/tools")
    async def list_tools() -> dict[str, Any]:
        return {"tools": registry.list_tools(plugin_id)}

    return router


def build_plugins_index(
    yaml_descriptors: list[PluginDescriptor],
    legacy_plugins: list[dict[str, Any]] | None = None,
    errors: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """GET /plugins 响应（前端 plugin-manager.ts / plugin-nav.ts 消费）"""
    plugins: list[dict[str, Any]] = []
    for descriptor in yaml_descriptors:
        plugins.append(
            {
                "name": descriptor.manifest.name,
                "version": descriptor.manifest.version,
                "description": descriptor.manifest.description,
                "status": descriptor.state,
                "manifest": descriptor.manifest.to_frontend_manifest(),
            }
        )
    for item in legacy_plugins or []:
        plugins.append(item)
    return {"plugins": plugins, "count": len(plugins), "errors": errors or []}


def scan_and_build(plugins_dir: Path | str, registry: PluginToolRegistry | None = None) -> tuple[list[APIRouter], dict[str, Any]]:
    """一站式：扫描 + 工具绑定 + 路由生成 + /plugins 载荷

    返回 (routers, index_payload)。单插件工具绑定失败记入 errors，不影响其它插件。
    """
    from plugin.scanner import bind_tools, scan_plugins

    registry = registry or PluginToolRegistry()
    scan = scan_plugins(plugins_dir)
    routers: list[APIRouter] = []
    live: list[PluginDescriptor] = []
    errors = list(scan.errors)
    for descriptor in scan.descriptors:
        try:
            bind_tools(descriptor, registry)
        except Exception as e:  # noqa: BLE001 — 装载边界：单插件失败不拖垮整体
            logger.error("插件 %s 工具绑定失败: %s", descriptor.id, e)
            errors.append({"plugin": descriptor.id, "error": f"工具绑定失败: {e}"})
            descriptor.state = "error"
        # 流程定义注册（设计 §5）：清单 flows: → 流程引擎（失败逐插件记录不中断）
        try:
            from plugin.flow_engine import get_engine

            engine = get_engine()
            for rel in descriptor.manifest.flows:
                engine.register_flow_file(descriptor.dir_path / rel, descriptor.id)
        except Exception as e:  # noqa: BLE001
            logger.error("插件 %s 流程注册失败: %s", descriptor.id, e)
            errors.append({"plugin": descriptor.id, "error": f"流程注册失败: {e}"})
        routers.append(build_plugin_router(descriptor, registry))
        live.append(descriptor)
    return routers, build_plugins_index(live, errors=errors)
