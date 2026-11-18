"""
建流零门槛 HTTP 面 — 一句话建流 / 模板库 / 预览校验 / 常驻保存 / YAML 导出

设计见 plugin/flow_studio.py 模块头。路由注册顺序：本 router 必须 include 在
build_flow_router 之前（FastAPI 按注册序匹配，/flows/templates 会被 /flows/{flow_id}
吞掉）；本 router 无单段 GET 兜底路由，不会反吞 flow_api 的 /flows/runs* 等。

- GET  /flows/templates                流程模板库（三语元数据 + 参数规格）
- POST /flows/templates/instantiate    模板+参数 → 校验过的定义（预览，未注册）
- POST /flows/draft                    一句话建流：自然语言 → LLM 生成 → 校验（预览，未注册）
- POST /flows/preview                  定义字典 → 校验 + 规范化（可视化编辑"校验"，不保存）
- POST /flows/save                     存为常驻流程（校验→覆盖件落盘→注册进引擎）
- GET  /flows/{flow_id}/yaml           YAML 导出（dump_flow 单一真源）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin.flow_def import FlowDefError
from plugin.flow_engine import FlowEngine, FlowRunError, get_engine
from plugin.flow_studio import (
    data_to_yaml,
    defn_to_data,
    generate_from_nl,
    instantiate_template,
    list_templates,
    save_user_flow,
    validate_data,
)

logger = logging.getLogger("plugin.flow_studio_api")


class DraftRequest(PydanticBaseModel):
    prompt: str


class TemplateRequest(PydanticBaseModel):
    template_id: str
    params: dict[str, Any] = {}


class PreviewRequest(PydanticBaseModel):
    definition: dict[str, Any]


class SaveRequest(PydanticBaseModel):
    definition: dict[str, Any]


def build_flow_studio_router(engine: FlowEngine | None = None) -> APIRouter:
    engine = engine or get_engine()
    router = APIRouter(prefix="/flows", tags=["flow-studio"])

    @router.get("/templates")
    async def templates() -> dict[str, Any]:
        items = list_templates()
        return {"templates": items, "count": len(items)}

    @router.post("/templates/instantiate")
    async def instantiate(req: TemplateRequest) -> dict[str, Any]:
        try:
            return instantiate_template(req.template_id, req.params)
        except FlowDefError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/draft")
    async def draft(req: DraftRequest) -> dict[str, Any]:
        """一句话建流：生成+校验都在服务端（LLMEngine 单一生成方，失败带报因）"""
        result = await generate_from_nl(req.prompt)
        if not result.get("ok"):
            raise HTTPException(status_code=422, detail=result.get("error", "生成失败"))
        return result

    @router.post("/preview")
    async def preview(req: PreviewRequest) -> dict[str, Any]:
        """校验 + 规范化（不落盘不注册）——可视化编辑的「校验」按钮"""
        try:
            defn, canonical = validate_data(req.definition, source="<preview>")
        except FlowDefError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "flow_id": defn.id, "name": defn.name,
                "definition": canonical, "yaml": data_to_yaml(canonical)}

    @router.post("/save")
    async def save(req: SaveRequest) -> dict[str, Any]:
        """存为常驻流程：校验 → 覆盖件落盘 → 注册进引擎（重启不丢、插件重载不冲掉）"""
        try:
            return save_user_flow(req.definition, engine=engine)
        except FlowDefError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.get("/{flow_id}/yaml")
    async def export_yaml(flow_id: str) -> dict[str, Any]:
        try:
            defn = engine.get_flow(flow_id)
        except FlowRunError:
            raise HTTPException(status_code=404, detail=f"流程未注册: {flow_id}")
        data = defn_to_data(defn)
        return {"flow_id": flow_id, "yaml": data_to_yaml(data), "definition": data}

    return router
