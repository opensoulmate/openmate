# -*- coding: utf-8 -*-
"""本地/内网插件仓库 HTTP 面（market step 2）：发布上传、检索、版本、下载、
yank、装入。

整合优先：装入走 forge.install_zip 确认链（**生成≠安装**：confirmed=false 只回
确认信息，plugins/<id> 绝不出现）；卸载不在此面——见 /plugins/forge/{id}/uninstall
（同一确认门禁）。仓库只做分发与版本存储。
"""
from __future__ import annotations

import base64
import binascii
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel as PydanticBaseModel

from plugin.forge_api import InstallRequest, make_hot_loader
from plugin.plugin_forge import ForgeError, PluginForge
from plugin.plugin_repo import PluginRepo


class PublishRequest(PydanticBaseModel):
    zip_b64: str                      # 插件 zip（base64）
    publisher: str = ""
    notes: str = ""


def build_repo_router(
    store_dir: Path | str,
    plugins_dir: Path | str,
    registry: Any = None,
    engine: Any = None,
    include_router: Any = None,
    on_installed: Any = None,
) -> APIRouter:
    from plugin.mcp_bridge import tool_registry as default_registry
    from plugin.flow_engine import get_engine

    registry = registry or default_registry
    engine = engine or get_engine()
    repo = PluginRepo(store_dir)
    forge = PluginForge(plugins_dir)
    hot_load = make_hot_loader(plugins_dir, registry, engine, include_router,
                               on_installed)
    router = APIRouter(prefix="/plugins/repo", tags=["plugin-repo"])

    @router.post("/publish")
    async def publish(req: PublishRequest) -> dict[str, Any]:
        try:
            raw = base64.b64decode(req.zip_b64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise HTTPException(status_code=400, detail=f"zip_b64 非法: {e}")
        with tempfile.TemporaryDirectory(prefix="repo-publish-") as td:
            z = Path(td) / "plugin.zip"
            z.write_bytes(raw)
            try:
                return repo.publish(z, publisher=req.publisher, notes=req.notes)
            except ForgeError as e:
                status = 409 if "版本不可变" in str(e) else 400
                raise HTTPException(status_code=status, detail=str(e))

    @router.get("")
    async def search(keyword: str = "", limit: int = 50) -> dict[str, Any]:
        results = repo.search(keyword, limit=limit)
        return {"results": results, "count": len(results)}

    @router.get("/{plugin_id}/versions")
    async def versions(plugin_id: str) -> dict[str, Any]:
        try:
            vs = repo.versions(plugin_id)
        except ForgeError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"id": plugin_id, "versions": vs, "count": len(vs)}

    @router.get("/{plugin_id}/versions/{version}/download")
    async def download(plugin_id: str, version: str) -> FileResponse:
        try:
            z = repo.get_zip(plugin_id, version)
        except ForgeError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return FileResponse(z, media_type="application/zip",
                            filename=f"{plugin_id}-{version}.zip")

    @router.post("/{plugin_id}/versions/{version}/install")
    async def install(plugin_id: str, version: str,
                      req: InstallRequest) -> dict[str, Any]:
        """从仓库装入——复用 forge.install_zip 确认链（生成≠安装）。"""
        try:
            return repo.install(plugin_id, version, forge=forge,
                                confirmed=req.confirmed, hot_load=hot_load,
                                decided_by=req.decided_by)
        except ForgeError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.delete("/{plugin_id}/versions/{version}")
    async def unpublish(plugin_id: str, version: str) -> dict[str, Any]:
        try:
            return repo.unpublish(plugin_id, version)
        except ForgeError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return router
