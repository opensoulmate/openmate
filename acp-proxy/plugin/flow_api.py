"""
流程引擎 HTTP 面 — 运行面板/对话共用的流程 API

设计文档: docs/plugin-system-design.md §5（可观测面板为宿主内建）
- GET  /flows                          流程定义清单
- DELETE /flows/{flow_id}              删除流程定义 + 清空实例/幂等键（测试流清理）
- POST /flows/{flow_id}/run            manual 触发（面板按钮/对话「跑一下」）
- GET  /flows/runs                     实例列表（可观测面板 list 数据源）
- GET  /flows/runs/{run_id}            实例详情（节点状态/重试/耗时/输出摘要）
- GET  /flows/runs/{run_id}/log        实例事件日志（节点日志时间线）
- POST /flows/runs/{run_id}/approval   人工审批闸（approve | reject）
- POST /flows/runs/{run_id}/replay     回放（同触发/输入重跑，链回 replay_of）
- POST /flows/events                   事件触发（emit-event 动作/外部系统）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin.flow_engine import FlowEngine, FlowRunError, get_engine

logger = logging.getLogger("plugin.flow_api")


class RunRequest(PydanticBaseModel):
    inputs: dict[str, Any] = {}
    idempotency_key: str = ""


class ApprovalRequest(PydanticBaseModel):
    decision: str                    # approve | reject
    decided_by: str = "用户"
    note: str = ""


class ReplayRequest(PydanticBaseModel):
    pass


class EventRequest(PydanticBaseModel):
    event: str
    payload: dict[str, Any] = {}


def build_flow_router(engine: FlowEngine | None = None) -> APIRouter:
    engine = engine or get_engine()
    router = APIRouter(prefix="/flows", tags=["flow-engine"])

    @router.get("")
    async def list_flows() -> dict[str, Any]:
        return {"flows": engine.list_flows(), "count": len(engine.list_flows())}

    @router.delete("/{flow_id}")
    async def delete_flow(flow_id: str) -> dict[str, Any]:
        """删除流程定义并清空其全部实例/幂等键（彻底清理测试流，不留面板残留）"""
        result = engine.delete_flow(flow_id)
        if not result["removed_definition"] and result["purged_runs"] == 0:
            raise HTTPException(status_code=404, detail=f"流程不存在: {flow_id}")
        return {"flow_id": flow_id, **result}

    @router.post("/{flow_id}/run")
    async def run_flow(flow_id: str, req: RunRequest) -> dict[str, Any]:
        try:
            run = await engine.run_now(
                flow_id,
                {"type": "manual", "source": "api"},
                inputs=req.inputs,
                idempotency_key=req.idempotency_key,
            )
        except FlowRunError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return engine.run_summary(run)

    @router.get("/runs")
    async def list_runs(flow_id: str = "", status: str = "", limit: int = 50) -> dict[str, Any]:
        runs = engine.store.list_runs(
            flow_id=flow_id or None, status=status or None, limit=max(1, min(limit, 500)))
        return {"runs": [engine.run_summary(run) for run in runs], "count": len(runs)}

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str) -> dict[str, Any]:
        run = engine.store.find_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"流程实例不存在: {run_id}")
        detail = engine.run_summary(run)
        detail["log"] = run.log
        detail["outputs"] = run.outputs
        detail["approval"] = run.approval.__dict__ if run.approval else None
        return detail

    @router.get("/runs/{run_id}/log")
    async def get_run_log(run_id: str) -> dict[str, Any]:
        run = engine.store.find_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"流程实例不存在: {run_id}")
        return {"run_id": run_id, "log": run.log, "count": len(run.log)}

    @router.post("/runs/{run_id}/approval")
    async def approve_run(run_id: str, req: ApprovalRequest) -> dict[str, Any]:
        try:
            run = await engine.resolve_approval(
                run_id, req.decision, decided_by=req.decided_by, note=req.note)
        except FlowRunError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return engine.run_summary(run)

    @router.post("/runs/{run_id}/replay")
    async def replay_run(run_id: str, req: ReplayRequest) -> dict[str, Any]:
        try:
            run = await engine.replay(run_id)
        except FlowRunError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return engine.run_summary(run)

    @router.post("/events")
    async def emit_event(req: EventRequest) -> dict[str, Any]:
        runs = engine.emit(req.event, req.payload)
        return {
            "event": req.event,
            "started": [engine.run_summary(run) for run in runs],
            "count": len(runs),
        }

    return router
