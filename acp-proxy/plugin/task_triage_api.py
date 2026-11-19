"""
任务三级分流门 HTTP 面 — 判级 / 三轨分流 / 一键换轨 / 统一回看板 / 个人-企业双语义

路由（acp-proxy :8092，CORS *，与 /flows /collab 同宿主）：
- GET  /triage/mode                     租户模式（personal=一人公司 | enterprise=企业内部流程）
- PUT  /triage/mode                     切换模式（文案与模板偏好随之切换）
- POST /triage/analyze                  一句话 → AI 判级（small/medium/large + 理由 + 建议轨道）
- POST /triage/dispatch                 分流下达（chat=开会话 / group=建组发任务 / flow=建流草稿）
- POST /triage/records/{id}/switch      一键换轨（上下文带过去，旧记录留痕 switched_to）
- POST /triage/records/{id}/confirm     流程草稿装载跑（save_user_flow → engine.run_now）
- GET  /triage/records                  统一回看板（三轨产物 + 实时状态 + 深链）
- GET  /triage/records/{id}             单条详情（含实时状态）
- GET  /triage/templates                流程模板库（按租户模式排好偏好序）

Authorization 头原样透传 OpenSoul（租户身份派生，禁自报）。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin import task_triage as core

logger = logging.getLogger("plugin.task_triage_api")


class AnalyzeRequest(PydanticBaseModel):
    prompt: str


class DispatchRequest(PydanticBaseModel):
    prompt: str
    track: str = ""                       # 缺省按判级建议轨道
    level: str = ""                       # 可带前端已判级结果（跳过重复判级）
    reasons: list[str] = []
    level_source: str = ""
    context: dict[str, Any] = {}


class SwitchRequest(PydanticBaseModel):
    to_track: str
    note: str = ""


class ModeRequest(PydanticBaseModel):
    mode: str


def build_triage_router() -> APIRouter:
    router = APIRouter(prefix="/triage", tags=["task-triage"])

    @router.get("/mode")
    async def get_mode() -> dict[str, Any]:
        mode = core.get_mode()
        return {
            "mode": mode,
            "track_hint": ("一人公司" if mode == "personal" else "企业内部流程"),
            "labels": {
                "personal": {"title": "一人公司", "flow_hint": "流程化 = 一个人的公司"},
                "enterprise": {"title": "企业内部流程", "flow_hint": "流程化 = 企业的内部流程"},
            },
        }

    @router.put("/mode")
    async def set_mode(req: ModeRequest) -> dict[str, Any]:
        try:
            mode = core.set_mode(req.mode)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True, "mode": mode}

    @router.post("/analyze")
    async def analyze(req: AnalyzeRequest) -> dict[str, Any]:
        """一句话 → AI 判级（LLMEngine 生成唯一入口；失败回落规则判级并如实标注）"""
        if not req.prompt.strip():
            raise HTTPException(status_code=400, detail="任务描述为空")
        result = await core.analyze_level(req.prompt)
        return {"ok": True, **result}

    @router.post("/dispatch")
    async def dispatch(req: DispatchRequest,
                       authorization: str | None = Header(None)) -> dict[str, Any]:
        prompt = req.prompt.strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="任务描述为空")
        track = req.track
        level = req.level
        reasons = list(req.reasons)
        level_source = req.level_source
        if not level or not track:
            judged = await core.analyze_level(prompt)
            level = level or judged["level"]
            reasons = reasons or judged["reasons"]
            level_source = level_source or judged["source"]
        track = track or core.LEVEL_TRACK.get(level, "")
        if track not in core.TRACKS:
            raise HTTPException(status_code=400,
                                detail=f"track 非法: {track!r}（chat|group|flow）")
        rec = core.create_record(
            prompt=prompt, level=level, reasons=reasons,
            level_source=level_source, track=track, status="created",
            context=req.context, tenant_mode=core.get_mode(),
        )
        try:
            rec = await core.dispatch(rec, track, auth=authorization)
        except Exception as e:
            rec = core.update_record(rec["id"], status="failed",
                                     detail={"error": str(e)}) or rec
            raise HTTPException(status_code=502, detail=f"分流执行失败：{e}")
        return {"ok": True, "record": rec}

    @router.post("/records/{record_id}/switch")
    async def switch(record_id: str, req: SwitchRequest,
                     authorization: str | None = Header(None)) -> dict[str, Any]:
        rec = core.get_record(record_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"记录不存在: {record_id}")
        if req.note.strip():
            ctx = dict(rec.get("context") or {})
            ctx["switch_note"] = req.note.strip()
            rec = core.update_record(record_id, context=ctx) or rec
        try:
            new_rec = await core.switch_track(rec, req.to_track, auth=authorization)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"换轨执行失败：{e}")
        return {"ok": True, "record": new_rec, "previous": record_id}

    @router.post("/records/{record_id}/confirm")
    async def confirm(record_id: str) -> dict[str, Any]:
        """装载跑：流程草稿 → 落盘注册 → 真跑（flow 轨专属）"""
        rec = core.get_record(record_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"记录不存在: {record_id}")
        if rec["track"] != "flow":
            raise HTTPException(status_code=400, detail="仅流程轨记录支持装载跑")
        if rec["status"] != "draft_ready":
            raise HTTPException(
                status_code=409,
                detail=f"当前状态不可装载跑: {rec['status']}（需 draft_ready）")
        try:
            rec = await core.confirm_flow(rec)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"装载跑失败：{e}")
        return {"ok": True, "record": rec}

    @router.get("/records")
    async def records(limit: int = 50,
                      authorization: str | None = Header(None)) -> dict[str, Any]:
        items = await core.board(limit, auth=authorization)
        return {"records": items, "count": len(items)}

    @router.get("/records/{record_id}")
    async def record_detail(record_id: str,
                            authorization: str | None = Header(None)) -> dict[str, Any]:
        rec = core.get_record(record_id)
        if rec is None:
            raise HTTPException(status_code=404, detail=f"记录不存在: {record_id}")
        rec["live"] = await core.live_status(rec, auth=authorization)
        rec["link"] = (rec.get("artifact") or {}).get("link", "")
        return rec

    @router.get("/templates")
    async def templates(mode: str = "") -> dict[str, Any]:
        """流程模板库（按租户模式排偏好序——个人/企业双语义）"""
        return core.templates_for_mode(mode or None)

    return router
