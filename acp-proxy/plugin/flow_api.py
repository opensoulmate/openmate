"""
流程引擎 HTTP 面 — 运行面板/对话共用的流程 API

设计文档: docs/plugin-system-design.md §5（可观测面板为宿主内建）
- GET  /flows                          流程定义清单（含 paused/一键操作面数据）
- POST /flows/{flow_id}/run            manual 触发（面板按钮/对话「跑一下」；paused 须 force）
- POST /flows/{flow_id}/clone          克隆流程（用户件，独立 id/实例，可继续编辑参数）
- PATCH /flows/{flow_id}               编辑参数（name/triggers/nodes/retain_runs 任意子集）
- POST /flows/{flow_id}/pause|resume   暂停/恢复（暂停挡 cron/event/手动触发）
- POST /flows/batch                    批量操作（run/pause/resume/delete 多条流程）
- DELETE /flows/{flow_id}              删除流程定义 + 清空实例/幂等键（测试流清理）
- GET  /flows/runs                     实例列表（flow_id/status/since/until 筛选）
- GET  /flows/runs/stats               运行历史统计（按流程/时间：成败/成功率/耗时）
- GET  /flows/runs/{run_id}            实例详情（节点状态/重试/耗时/错误归一/通知送达）
- GET  /flows/runs/{run_id}/log        实例事件日志（节点日志时间线）
- POST /flows/runs/{run_id}/approval   人工审批闸（approve | reject）
- POST /flows/runs/{run_id}/replay     回放（同触发/输入重跑，链回 replay_of）
- POST /flows/runs/{run_id}/retry      失败可修：从失败节点原地续跑（同实例）
- POST /flows/events                   事件触发（emit-event 动作/外部系统）
"""

from __future__ import annotations

import datetime as _dt
import logging
import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin.flow_engine import FlowEngine, FlowRunError, get_engine

logger = logging.getLogger("plugin.flow_api")


class RunRequest(PydanticBaseModel):
    inputs: dict[str, Any] = {}
    idempotency_key: str = ""
    force: bool = False            # 暂停中的流程：越权立即触发


class ApprovalRequest(PydanticBaseModel):
    decision: str                    # approve | reject
    decided_by: str = "用户"
    note: str = ""


class ReplayRequest(PydanticBaseModel):
    pass


class RetryRequest(PydanticBaseModel):
    from_node: str = ""              # 缺省=失败节点；可指定续跑起点


class VerifyRequest(PydanticBaseModel):
    verified_by: str                 # 验收者（≠执行者；自审拒收留痕）
    evidence: str = ""               # 内容级探针/验收证据（空=拒翻档 fail-closed）
    ok: bool = True                  # False=验收不过（拒伪完成，不许标 completed）


class ReassignRequest(PydanticBaseModel):
    to_assignee: str = ""            # 新归属（人/员工）
    target_agent: str = ""           # 可换 A2A 目标 SoulMate
    by: str = ""                     # 发起人（留痕）
    note: str = ""
    expect_version: int | None = None   # D5 交办版本校验（缺省不校验）


class CloneRequest(PydanticBaseModel):
    new_id: str = ""                 # 缺省 <id>-copy（冲突自动加号）
    new_name: str = ""


class UpdateRequest(PydanticBaseModel):
    name: str | None = None
    triggers: list[dict[str, Any]] | None = None
    nodes: list[dict[str, Any]] | None = None
    retain_runs: int | None = None


class BatchRequest(PydanticBaseModel):
    op: str                          # run | pause | resume | delete
    flow_ids: list[str]
    force: bool = False              # op=run 且流程暂停时越权触发


class EventRequest(PydanticBaseModel):
    event: str
    payload: dict[str, Any] = {}


def _parse_ts(value: str) -> float | None:
    """时间筛选参数：epoch 秒 / YYYY-MM-DD / YYYY-MM-DD HH:MM（本地时区）"""
    if not value:
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return _dt.datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail=f"时间格式非法: {value!r}"
                        "（epoch 秒或 YYYY-MM-DD[ HH:MM]）")


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
                force=req.force,
            )
        except FlowRunError as e:
            # 未注册=404；状态不允许（暂停等）=409
            raise HTTPException(
                status_code=404 if "未注册" in str(e) else 409, detail=str(e))
        return engine.run_summary(run)

    @router.post("/{flow_id}/clone")
    async def clone_flow(flow_id: str, req: CloneRequest) -> dict[str, Any]:
        try:
            return engine.clone_flow(flow_id, new_id=req.new_id, new_name=req.new_name)
        except FlowRunError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.patch("/{flow_id}")
    async def update_flow(flow_id: str, req: UpdateRequest) -> dict[str, Any]:
        patch = {k: v for k, v in req.model_dump().items() if v is not None}
        try:
            return engine.update_flow(flow_id, patch)
        except FlowRunError as e:
            raise HTTPException(status_code=409, detail=str(e))

    @router.post("/{flow_id}/pause")
    async def pause_flow(flow_id: str) -> dict[str, Any]:
        try:
            return engine.pause_flow(flow_id, paused=True)
        except FlowRunError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/{flow_id}/resume")
    async def resume_flow(flow_id: str) -> dict[str, Any]:
        try:
            return engine.pause_flow(flow_id, paused=False)
        except FlowRunError as e:
            raise HTTPException(status_code=404, detail=str(e))

    @router.post("/batch")
    async def batch_ops(req: BatchRequest) -> dict[str, Any]:
        """批量操作（运行/暂停/恢复/删除）：逐条独立成败，互不拖累"""
        if req.op not in ("run", "pause", "resume", "delete"):
            raise HTTPException(status_code=400,
                                detail=f"批量操作非法: {req.op!r}（run|pause|resume|delete）")
        if not req.flow_ids:
            raise HTTPException(status_code=400, detail="flow_ids 为空")
        results = []
        for flow_id in req.flow_ids:
            try:
                if req.op == "run":
                    run = await engine.run_now(
                        flow_id, {"type": "manual", "source": "batch"},
                        force=req.force)
                    results.append({"flow_id": flow_id, "ok": True,
                                    "run_id": run.run_id, "status": run.status})
                elif req.op == "delete":
                    result = engine.delete_flow(flow_id)
                    results.append({"flow_id": flow_id,
                                    "ok": bool(result["removed_definition"]), **result})
                else:
                    state = engine.pause_flow(flow_id, paused=(req.op == "pause"))
                    results.append({"flow_id": flow_id, "ok": True, **state})
            except FlowRunError as e:
                results.append({"flow_id": flow_id, "ok": False, "error": str(e)})
        return {"op": req.op, "results": results,
                "ok": sum(1 for r in results if r.get("ok")),
                "count": len(results)}

    @router.get("/runs")
    async def list_runs(flow_id: str = "", status: str = "", limit: int = 50,
                        since: str = "", until: str = "") -> dict[str, Any]:
        runs = engine.store.list_runs(
            flow_id=flow_id or None, status=status or None, limit=max(1, min(limit, 500)),
            since=_parse_ts(since), until=_parse_ts(until))
        return {"runs": [engine.run_summary(run) for run in runs], "count": len(runs)}

    @router.get("/runs/stats")
    async def runs_stats(flow_id: str = "", since: str = "", until: str = "") -> dict[str, Any]:
        """运行历史统计（按流程/时间筛选：成败计数/成功率/平均与最差耗时）"""
        return engine.run_stats(flow_id=flow_id, since=_parse_ts(since),
                                until=_parse_ts(until))

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

    @router.post("/runs/{run_id}/retry")
    async def retry_run(run_id: str, req: RetryRequest) -> dict[str, Any]:
        """失败可修：从失败节点原地续跑（同实例；整体重跑用 replay）"""
        try:
            run = await engine.retry_run(run_id, from_node=req.from_node)
        except FlowRunError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return engine.run_summary(run)

    @router.post("/runs/{run_id}/nodes/{node_id}/verify")
    async def verify_run_node(run_id: str, node_id: str,
                              req: VerifyRequest) -> dict[str, Any]:
        """验收证据翻档（唯一翻绿通道）：证据非空+验收者≠执行者，否则拒收留痕"""
        try:
            return await engine.verify_node(
                run_id, node_id, verified_by=req.verified_by,
                evidence=req.evidence, ok=req.ok)
        except FlowRunError as e:
            status = 404 if "不存在" in str(e) else 409
            raise HTTPException(status_code=status, detail=str(e))

    @router.post("/runs/{run_id}/nodes/{node_id}/reassign")
    async def reassign_run_node(run_id: str, node_id: str,
                                req: ReassignRequest) -> dict[str, Any]:
        """换人重派：工单交办新归属（可换 target_agent 重派），失败实例原地续跑"""
        try:
            return await engine.reassign_node(
                run_id, node_id, to_assignee=req.to_assignee,
                target_agent=req.target_agent, by=req.by, note=req.note,
                expect_version=req.expect_version)
        except FlowRunError as e:
            status = 404 if "不存在" in str(e) else 409
            raise HTTPException(status_code=status, detail=str(e))

    @router.get("/unverified")
    async def unverified_done(limit: int = 200) -> dict[str, Any]:
        """done·unverified 清单（监督日清一条）：节点/工单/实例三个层面可查"""
        return engine.unverified_done(limit=max(1, min(limit, 1000)))

    @router.post("/events")
    async def emit_event(req: EventRequest) -> dict[str, Any]:
        runs = engine.emit(req.event, req.payload)
        return {
            "event": req.event,
            "started": [engine.run_summary(run) for run in runs],
            "count": len(runs),
        }

    # ⚠️ 必须注册在 /flows/runs* 之后：FastAPI 按定义顺序匹配，
    # 先注册 /{flow_id} 会把 "runs" 吞成 flow_id。
    @router.get("/{flow_id}")
    async def get_flow(flow_id: str) -> dict[str, Any]:
        """流程定义详情（Will 观察窗数据源：节点全量配置 + 判断分支 when/then/else）"""
        from dataclasses import asdict

        try:
            defn = engine.get_flow(flow_id)
        except FlowRunError:
            raise HTTPException(status_code=404, detail=f"流程未注册: {flow_id}")
        plugin_id = next(
            (item["plugin_id"] for item in engine.list_flows() if item["id"] == flow_id),
            "",
        )
        return {
            "id": defn.id,
            "name": defn.name,
            "version": defn.version,
            "plugin_id": plugin_id,
            "paused": engine.is_paused(flow_id),
            "triggers": [asdict(t) for t in defn.triggers],
            "retain_runs": defn.retain_runs,
            "nodes": [asdict(n) for n in defn.nodes],
        }

    return router
