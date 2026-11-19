# -*- coding: utf-8 -*-
"""
插件市场 API — "默认未安装，谁使用谁安装"（docs/plugin-spec.md §生命周期）

- GET  /plugins/market               可装包清单（含安装态预览：L1/L2 菜单将长成什么样）
- POST /plugins/market/{id}/install  安装（**生成≠安装**：confirmed=false 只回确认信息）
- POST /plugins/market/{id}/uninstall 卸载（同一确认门禁；包目录保留=能力零删除）

安装 = 安装态翻转 + 热装载（工具进 MCP 桥、页面/路由注册、流程进引擎、菜单即刻长出）；
卸载 = 热卸载 + 安装态翻转（菜单/工具/流程即刻收走，插件包留在 plugins/ 供再装）。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin.flow_engine import FlowEngine, get_engine
from plugin.install_state import is_installed, set_installed, uninstalled_ids
from plugin.mcp_bridge import PluginToolRegistry
from plugin.plugin_forge import ForgeError

logger = logging.getLogger("plugin.market_api")


class MarketActionRequest(PydanticBaseModel):
    confirmed: bool = False    # 生成≠安装（确认链同 forge）
    decided_by: str = "用户"


# 延迟绑定的宿主回调（app.py 注册，避免模块环引）
_APP_ROUTES_HOST: Callable[[str], bool] | None = None
_YAML_INDEX_GETTER: Callable[[], dict[str, Any]] | None = None


def bind_host(
    *,
    register_legacy_routes: Callable[[str], bool],
    yaml_index: Callable[[], dict[str, Any]],
) -> None:
    """app.py 注入：legacy 路由注册器 + /plugins yaml 索引取值器
    （索引在 load_plugins 时整体重绑，必须走取值器拿活引用）"""
    global _APP_ROUTES_HOST, _YAML_INDEX_GETTER
    _APP_ROUTES_HOST = register_legacy_routes
    _YAML_INDEX_GETTER = yaml_index


def build_market_router(
    plugins_dir: Path | str,
    registry: PluginToolRegistry | None = None,
    engine: FlowEngine | None = None,
    include_router: Any = None,
    on_installed: Callable[[dict[str, Any]], None] | None = None,
) -> APIRouter:
    from plugin.forge_api import make_hot_loader, make_hot_unloader
    from plugin.mcp_bridge import tool_registry as default_registry

    plugins_dir = Path(plugins_dir)
    registry = registry or default_registry
    engine = engine or get_engine()
    hot_load = make_hot_loader(plugins_dir, registry, engine, include_router, on_installed)
    hot_unload = make_hot_unloader(registry, engine)
    router = APIRouter(prefix="/plugins/market", tags=["plugin-market"])

    def _menu_preview(plugin_id: str) -> dict[str, Any] | None:
        """装成后菜单预览（生成≠安装：只回信息，菜单不长）"""
        from plugin.scanner import scan_plugins

        scan = scan_plugins(plugins_dir)
        descriptor = scan.get(plugin_id)
        if descriptor is None:
            return None
        manifest = descriptor.manifest.to_frontend_manifest()
        return manifest.get("frontend", {}).get("menu")

    @router.get("")
    async def market_catalog() -> dict[str, Any]:
        """可装包清单：plugins/ 全部包 + 安装态 + 菜单预览（manifest 声明，零硬编码）"""
        from plugin.scanner import scan_plugins

        scan = scan_plugins(plugins_dir)
        items: list[dict[str, Any]] = []
        for descriptor in scan.descriptors:
            manifest = descriptor.manifest.to_frontend_manifest()
            items.append({
                "id": descriptor.id,
                "name": manifest.get("name", descriptor.id),
                "version": manifest.get("version", "0.0.0"),
                "description": manifest.get("description", ""),
                "installed": is_installed(plugins_dir, descriptor.id),
                "menu": (manifest.get("frontend") or {}).get("menu"),
                "pages": len(descriptor.manifest.pages),
                "tools": len(descriptor.manifest.tools),
            })
        # legacy plugin.json 包（无 ui.menu 的按 nav 形状回显，安装态同门禁）
        for plugin_dir in sorted(plugins_dir.iterdir()) if plugins_dir.is_dir() else []:
            if not plugin_dir.is_dir() or not (plugin_dir / "plugin.json").is_file():
                continue
            if any(i["id"] == plugin_dir.name for i in items):
                continue
            import json

            try:
                legacy = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            items.append({
                "id": legacy.get("id", plugin_dir.name),
                "name": legacy.get("name", plugin_dir.name),
                "version": legacy.get("version", "0.0.0"),
                "description": legacy.get("description", ""),
                "installed": is_installed(plugins_dir, legacy.get("id", plugin_dir.name)),
                "menu": (legacy.get("frontend") or {}).get("menu") or (legacy.get("frontend") or {}).get("nav"),
                "pages": 0,
                "tools": len(legacy.get("tools", [])),
            })
        return {"packages": items, "count": len(items),
                "available": sorted(uninstalled_ids(plugins_dir))}

    @router.post("/{plugin_id}/install")
    async def install_plugin(plugin_id: str, req: MarketActionRequest) -> dict[str, Any]:
        """一键安装（生成≠安装：未确认只回预览，菜单绝不长出）"""
        if is_installed(plugins_dir, plugin_id):
            raise HTTPException(status_code=409, detail=f"插件已安装：{plugin_id}")
        if not (plugins_dir / plugin_id).is_dir():
            raise HTTPException(status_code=404, detail=f"插件包不存在：{plugin_id}")
        if not req.confirmed:
            return {
                "plugin_id": plugin_id,
                "confirmed": False,
                "action": "install",
                "menu_preview": _menu_preview(plugin_id),
                "decided_by": req.decided_by,
            }
        set_installed(plugins_dir, plugin_id, True)
        grown: dict[str, Any] = {}
        errors: list[str] = []
        try:
            grown = hot_load(plugin_id)
        except ForgeError as e:
            errors.append(f"yaml 热装载: {e}")
        legacy_loaded = False
        if _APP_ROUTES_HOST is not None:
            try:
                legacy_loaded = _APP_ROUTES_HOST(plugin_id)
            except Exception as e:  # noqa: BLE001
                errors.append(f"legacy 装载: {e}")
        if grown.get("plugin_id") is None and not legacy_loaded and errors:
            # 两路都没装上——回滚安装态（可重试）
            set_installed(plugins_dir, plugin_id, False)
            raise HTTPException(status_code=409, detail="；".join(errors))
        return {
            "plugin_id": plugin_id,
            "confirmed": True,
            "action": "install",
            "installed": True,
            "grown": grown,
            "legacy_loaded": legacy_loaded,
            "errors": errors,
        }

    @router.post("/{plugin_id}/uninstall")
    async def uninstall_plugin(plugin_id: str, req: MarketActionRequest) -> dict[str, Any]:
        """一键卸载（未确认只回预览）：菜单/工具/流程即刻收走，插件包保留（能力零删除）"""
        if not is_installed(plugins_dir, plugin_id):
            raise HTTPException(status_code=404, detail=f"插件未安装：{plugin_id}")
        if not req.confirmed:
            return {
                "plugin_id": plugin_id,
                "confirmed": False,
                "action": "uninstall",
                "menu_preview": _menu_preview(plugin_id),
                "decided_by": req.decided_by,
            }
        removed = hot_unload(plugin_id)
        if _YAML_INDEX_GETTER is not None:
            index = _YAML_INDEX_GETTER()
            index.setdefault("plugins", [])[:] = [
                item for item in index.get("plugins", [])
                if (item.get("manifest") or {}).get("id") != plugin_id
            ]
        set_installed(plugins_dir, plugin_id, False)
        return {
            "plugin_id": plugin_id,
            "confirmed": True,
            "action": "uninstall",
            "installed": False,
            "unloaded": removed,
            "package_kept": (plugins_dir / plugin_id).is_dir(),
        }

    return router
