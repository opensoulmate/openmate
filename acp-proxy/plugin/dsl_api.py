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

from fastapi import APIRouter, Depends, HTTPException
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


# ═══════════════════════════════════════════════════════════════════
# 自定义 HTML 面板（层②）— Sprint C：静态只读服务 + postMessage RPC 宿主侧
# ═══════════════════════════════════════════════════════════════════
#
# 设计 docs/plugin-system-design.md §3：内容来自插件 html/ 目录（只读静态）、
# 通信=postMessage RPC（{id, method, params}→{id, result|error}）、宿主 method
# 白名单=该插件声明的 MCP 工具 + ui.* 事件；工具调用与 DSL 面**同一审批闸**
# （approved → ApprovalRequired→approval_required 409 同构回传）。

UI_PANEL_EVENTS = ("ui.toast", "ui.refresh", "ui.store.get", "ui.store.set")
HTML_RPC_MAX_STORE_BYTES = 64 * 1024   # ui.store 单值上限（防面板灌爆存储）
_HTML_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def html_panel_methods(descriptor: PluginDescriptor) -> dict[str, Any]:
    """宿主 method 白名单（设计 §3）：插件声明的 MCP 工具 + ui.* 事件。"""
    return {
        "tools": sorted(t.name for t in descriptor.manifest.tools),
        "ui_events": list(UI_PANEL_EVENTS),
    }


def _html_file(descriptor: PluginDescriptor, rel: str) -> Path:
    """html/ 静态文件定位（只读 + 越界必拒——与 zip-slip 同一路径约束语义）。"""
    root = (descriptor.dir_path / "html").resolve()
    if not root.is_dir():
        raise HTTPException(status_code=404, detail=f"插件 {descriptor.id} 无 html/ 面板目录")
    cand = (root / rel).resolve()
    if cand != root and root not in cand.parents:
        raise HTTPException(status_code=403, detail=f"路径越界: {rel!r}（html/ 只读子树）")
    if not cand.is_file():
        raise HTTPException(status_code=404, detail=f"面板文件不存在: {rel!r}")
    return cand


def _store_file(descriptor: PluginDescriptor) -> Path:
    """ui.store 落点：插件 store 目录（ToolContext.store_dir 同一持久面）。"""
    return ToolContext(plugin_id=descriptor.id,
                       data_dir=str(descriptor.dir_path)).store_dir() / "html_store.json"


async def html_panel_rpc(descriptor: PluginDescriptor,
                         registry: PluginToolRegistry,
                         payload: dict[str, Any], *,
                         approved: bool = False) -> dict[str, Any]:
    """postMessage RPC 宿主侧核心（纯逻辑可测）：{id, method, params} → {id, result|error}。

    - 白名单外 method 必拒（method_not_allowed，未声明工具绝不可调）；
    - 工具 method → plugin_id__method 走 registry（与 DSL 面同一审批闸；
      ApprovalRequired → error.approval_required——前端弹审批再带 approved 重发）；
    - ui.* 事件：toast/refresh=回执（前端壳消费），store.get/set=插件自有存储。
    """
    req_id = payload.get("id")
    method = str(payload.get("method", "") or "").strip()
    raw_params = payload.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    try:
        if method in ("ui.toast", "ui.refresh"):
            return {"id": req_id, "result": {"ok": True, "method": method}}
        if method == "ui.store.get":
            store = _store_file(descriptor)
            data = json.loads(store.read_text(encoding="utf-8")) if store.is_file() else {}
            return {"id": req_id, "result": {"value": data.get("value")}}
        if method == "ui.store.set":
            blob = json.dumps(params.get("value"), ensure_ascii=False)
            if len(blob.encode("utf-8")) > HTML_RPC_MAX_STORE_BYTES:
                return {"id": req_id, "error": {
                    "code": "store_value_too_large",
                    "message": f"ui.store.set 值超上限 {HTML_RPC_MAX_STORE_BYTES}B"}}
            store = _store_file(descriptor)
            store.write_text(json.dumps({"value": params.get("value")},
                                        ensure_ascii=False), encoding="utf-8")
            return {"id": req_id, "result": {"ok": True}}
        allowed = {t.name for t in descriptor.manifest.tools}
        if method not in allowed:
            logger.warning(
                f"[html-panel] method_not_allowed: {descriptor.id}.{method}")
            return {"id": req_id, "error": {
                "code": "method_not_allowed",
                "message": (f"method {method!r} 不在白名单（声明工具: {sorted(allowed)}"
                            f"；ui 事件: {list(UI_PANEL_EVENTS)}）")}}
        ctx = ToolContext(plugin_id=descriptor.id, data_dir=str(descriptor.dir_path))
        result = await registry.call_tool_async(
            f"{descriptor.id}__{method}", params, approved=approved, ctx=ctx)
        return {"id": req_id, "result": result}
    except ApprovalRequired as e:
        return {"id": req_id, "error": {"code": "approval_required",
                                        "message": str(e)}}
    except ToolNotFound as e:
        return {"id": req_id, "error": {"code": "tool_not_found", "message": str(e)}}
    except (SchemaValidationError, PermissionError, ValueError, OSError) as e:
        return {"id": req_id, "error": {"code": "invalid_request", "message": str(e)}}


class HtmlRpcRequest(PydanticBaseModel):
    id: Any = None
    method: str = ""
    params: dict[str, Any] = {}
    approved: bool = False   # 审批闸（与 DSL 面 tools 调用同构）


def load_dsl_page(descriptor: PluginDescriptor, page_id: str) -> dict[str, Any]:
    """读取并校验 UI DSL 页面；返回可直接下发前端的 JSON"""
    page_path = descriptor.page_path(page_id)
    if page_path is None:
        raise HTTPException(status_code=404, detail=f"页面不存在: {page_id}")
    page_spec = next(p for p in descriptor.manifest.pages if p.id == page_id)
    if page_spec.dsl is None:
        raise HTTPException(status_code=404, detail=(
            f"页面 {page_id} 为 HTML 面板（层②）——"
            f"内容走 GET /plugins/{descriptor.id}/html/{page_spec.html}"))
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

    def _installed_guard() -> None:
        """卸载即收走：插件目录消失 **或安装态=false** 后本路由组整体 404
        （uninstall 热卸载后自惰，无需从 app 摘除已注册路由）。
        状态门禁见 plugin/install_state.py（"默认未安装，谁使用谁安装"）。"""
        from plugin.install_state import is_installed

        if not descriptor.dir_path.is_dir() or not is_installed(descriptor.dir_path.parent, plugin_id):
            raise HTTPException(status_code=404, detail=f"插件已卸载：{plugin_id}")

    router = APIRouter(prefix=f"/plugins/{plugin_id}", tags=[f"plugin:{plugin_id}"],
                       dependencies=[Depends(_installed_guard)])

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

    # ── 层② HTML 面板：静态只读服务（html/ 子树，越界必拒）──────────
    @router.get("/html/{file_path:path}")
    async def get_html_panel(file_path: str):
        from fastapi.responses import FileResponse

        target = _html_file(descriptor, file_path)
        suffix = target.suffix.lower()
        media = _HTML_CONTENT_TYPES.get(suffix, "application/octet-stream")
        # 只读+禁缓存（面板开发期改完即刷新可见；X-Content-Type-Options 防 MIME 嗅探）
        return FileResponse(target, media_type=media, headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-cache",
        })

    # ── 层② postMessage RPC 宿主侧（白名单=声明工具+ui.*，同一审批闸）──
    @router.post("/html-rpc")
    async def html_rpc(req: HtmlRpcRequest) -> dict[str, Any]:
        return await html_panel_rpc(
            descriptor, registry,
            {"id": req.id, "method": req.method, "params": req.params},
            approved=req.approved)

    @router.get("/html-methods")
    async def html_methods() -> dict[str, Any]:
        """宿主 method 白名单（前端壳 postMessage 前的契约面）。"""
        return html_panel_methods(descriptor)

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


def scan_and_build(
    plugins_dir: Path | str,
    registry: PluginToolRegistry | None = None,
    exclude_ids: set[str] | None = None,
) -> tuple[list[APIRouter], dict[str, Any]]:
    """一站式：扫描 + 工具绑定 + 路由生成 + /plugins 载荷

    返回 (routers, index_payload)。单插件工具绑定失败记入 errors，不影响其它插件。
    exclude_ids：安装态门禁（plugin/install_state.py）——"可用未安装"插件不装载不入清单
    （包目录不动，市场安装后 hot_load 即刻长出）。
    """
    from plugin.scanner import bind_tools, scan_plugins

    registry = registry or PluginToolRegistry()
    scan = scan_plugins(plugins_dir)
    routers: list[APIRouter] = []
    live: list[PluginDescriptor] = []
    errors = list(scan.errors)
    for descriptor in scan.descriptors:
        if exclude_ids and descriptor.id in exclude_ids:
            logger.info("插件 %s 可用未安装（install-state），跳过装载", descriptor.id)
            continue
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
