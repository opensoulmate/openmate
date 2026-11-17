"""
插件锻造坊 HTTP 面 — 自我开发闭环的 API（SoulMate 与 UI 共用）

设计文档: docs/plugin-system-design.md §7
- POST   /plugins/forge/drafts              生成草稿（spec 模板 | LLM 自写 files）+ 即时 dry-run
- GET    /plugins/forge/drafts              草稿列表
- GET    /plugins/forge/drafts/{id}         草稿内容（确认安装前看 diff）
- POST   /plugins/forge/drafts/{id}/dry-run 重跑校验
- POST   /plugins/forge/drafts/{id}/install 安装（**必须 confirmed=true 用户确认**——铁律）
- DELETE /plugins/forge/drafts/{id}         丢弃草稿

安装后热装载：工具进 MCP 桥、页面进 /plugins/{id}/ui/{page}、菜单进 /plugins、
流程进流程引擎——不重启宿主，菜单/页面/工具即刻长出。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin.flow_engine import FlowEngine, get_engine
from plugin.mcp_bridge import PluginToolRegistry
from plugin.plugin_forge import ForgeError, PluginForge

logger = logging.getLogger("plugin.forge_api")


class DraftRequest(PydanticBaseModel):
    spec: dict[str, Any] | None = None
    files: dict[str, str] | None = None


class InstallRequest(PydanticBaseModel):
    confirmed: bool = False    # 用户确认（生成≠安装，未确认只回确认所需信息）
    decided_by: str = "用户"


def make_hot_loader(
    plugins_dir: Path | str,
    registry: PluginToolRegistry,
    engine: FlowEngine,
    include_router: Callable[[APIRouter], None] | None = None,
    on_installed: Callable[[dict[str, Any]], None] | None = None,
) -> Callable[[str], dict[str, Any]]:
    """安装后的热装载：扫描→工具绑定→路由注册→流程注册→（可选）索引更新"""

    def hot_load(plugin_id: str) -> dict[str, Any]:
        from plugin.dsl_api import build_plugin_router
        from plugin.scanner import bind_tools, scan_plugins

        scan = scan_plugins(plugins_dir)
        descriptor = scan.get(plugin_id)
        if descriptor is None:
            errors = [e["error"] for e in scan.errors if e.get("plugin") == plugin_id]
            raise ForgeError(f"装载失败：{plugin_id} 扫描不通过（{errors or scan.errors}）")
        tools_registered = bind_tools(descriptor, registry)
        router = build_plugin_router(descriptor, registry)
        if include_router is not None:
            include_router(router)
        flows = []
        for rel in descriptor.manifest.flows:
            defn = engine.register_flow_file(descriptor.dir_path / rel, plugin_id)
            flows.append(defn.id)
        grown = {
            "plugin_id": plugin_id,
            "tools_registered": tools_registered,
            "tool_names": [f"{plugin_id}__{t.name}" for t in descriptor.manifest.tools],
            "pages": [p.id for p in descriptor.manifest.pages],
            "flows": flows,
            "nav": descriptor.nav_item(),
            "frontend_manifest": descriptor.manifest.to_frontend_manifest(),
        }
        if on_installed is not None:
            on_installed(grown)
        return grown

    return hot_load


def make_hot_unloader(
    registry: PluginToolRegistry,
    engine: FlowEngine,
) -> Callable[[str], dict[str, Any]]:
    """安装热装载的镜像（market step 2 卸载生命周期）：工具解绑 + 流程注销。
    路由随插件目录消失自惰（dsl_api 目录守卫→404 插件已卸载）。"""

    def hot_unload(plugin_id: str) -> dict[str, Any]:
        tools = registry.unregister_plugin(plugin_id)
        flows = engine.unregister_plugin(plugin_id)
        return {
            "plugin_id": plugin_id,
            "tools_unbound": tools,
            "flows_unbound": flows,
        }

    return hot_unload


def build_forge_router(
    plugins_dir: Path | str,
    registry: PluginToolRegistry | None = None,
    engine: FlowEngine | None = None,
    include_router: Callable[[APIRouter], None] | None = None,
    on_installed: Callable[[dict[str, Any]], None] | None = None,
) -> APIRouter:
    from plugin.mcp_bridge import tool_registry as default_registry

    registry = registry or default_registry
    engine = engine or get_engine()
    forge = PluginForge(plugins_dir)
    hot_load = make_hot_loader(plugins_dir, registry, engine, include_router, on_installed)
    router = APIRouter(prefix="/plugins/forge", tags=["plugin-forge"])

    @router.post("/drafts")
    async def create_draft(req: DraftRequest) -> dict[str, Any]:
        try:
            return forge.draft(spec=req.spec, files=req.files)
        except ForgeError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/drafts")
    async def list_drafts() -> dict[str, Any]:
        drafts = forge.list_drafts()
        return {"drafts": drafts, "count": len(drafts)}

    @router.get("/drafts/{draft_id}")
    async def read_draft(draft_id: str) -> dict[str, Any]:
        try:
            return forge.read_draft(draft_id)
        except ForgeError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/drafts/{draft_id}/dry-run")
    async def dry_run_draft(draft_id: str) -> dict[str, Any]:
        try:
            return forge.dry_run(draft_id)
        except ForgeError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/drafts/{draft_id}/install")
    async def install_draft(draft_id: str, req: InstallRequest) -> dict[str, Any]:
        try:
            result = forge.install(
                draft_id, confirmed=req.confirmed, hot_load=hot_load)
        except ForgeError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return result

    @router.delete("/drafts/{draft_id}")
    async def delete_draft(draft_id: str) -> dict[str, Any]:
        try:
            forge.delete_draft(draft_id)
        except ForgeError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"deleted": draft_id}

    @router.post("/{plugin_id}/uninstall")
    async def uninstall_plugin(plugin_id: str, req: InstallRequest) -> dict[str, Any]:
        """卸载（DELETE 门禁：未确认只回预览绝不删；确认后热卸载+删目录）。"""
        try:
            return forge.uninstall(
                plugin_id, confirmed=req.confirmed,
                hot_unload=make_hot_unloader(registry, engine),
                decided_by=req.decided_by)
        except ForgeError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return router
