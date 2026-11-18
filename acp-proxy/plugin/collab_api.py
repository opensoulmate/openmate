"""
任务收发台 HTTP 面 — 每人任务收件箱 API（在办/交付/超时 三桶 + 认领/心跳/交付/催办）

路由前缀: /collab（与 /flows 并列，flow_api 同款装配风格）
数据源: plugin.flow_collab.CollabRuntime（工单账=JSON 文件，角色路由=org 表+RBAC）
- GET  /collab/tasks                  收件箱清单（assignee/role/kind/bucket 过滤）
                                       bucket=open(在办) | delivered(交付) | overdue(超时) | closed
- GET  /collab/tasks/{task_id}        工单详情（含领取锁/预算/催办/历史）
- POST /collab/tasks/{task_id}/claim      认领（抢占锁 CAS，返回 claim_token）
- POST /collab/tasks/{task_id}/heartbeat  心跳续租（防被抢占）
- POST /collab/tasks/{task_id}/deliver    交付回流（→ 流程恢复续跑；带 token=CAS 回执）
- POST /collab/tasks/{task_id}/decide     审批决定（approval/预算闸：approve|reject）
- POST /collab/tasks/{task_id}/remind     催办（记次数+通知，最小间隔去重）
- POST /collab/tasks/{task_id}/cancel     取消（终态幂等）
- GET  /collab/stats                  收发台看板（三桶计数+预算策略状态+角色路由健康）
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel as PydanticBaseModel

from plugin.flow_collab import (
    CollabError,
    STATUS_CANCELLED,
    STATUS_FAILED,
    WorkOrder,
    get_collab,
)

logger = logging.getLogger("plugin.collab_api")


class ClaimRequest(PydanticBaseModel):
    claimed_by: str = "用户"
    lease_seconds: float = 600.0


class HeartbeatRequest(PydanticBaseModel):
    claim_token: str


class DeliverRequest(PydanticBaseModel):
    result: Any = None
    delivered_by: str = "用户"
    claim_token: str = ""
    note: str = ""


class DecideRequest(PydanticBaseModel):
    decision: str                    # approve | reject
    decided_by: str = "用户"
    note: str = ""
    result: Any = None


class RemindRequest(PydanticBaseModel):
    note: str = ""


class HandoffRequest(PydanticBaseModel):
    to_assignee: str
    decided_by: str = ""
    note: str = ""
    expect_version: int | None = None   # D5 版本校验（缺省不校验）


class CancelRequest(PydanticBaseModel):
    reason: str = ""


def _task_view(runtime, task: WorkOrder, now: float) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "kind": task.kind,
        "status": task.status,
        "bucket": task.bucket(now),
        "title": task.title,
        "instruction": task.instruction,
        "form": task.form,
        "run_id": task.run_id,
        "flow_id": task.flow_id,
        "node_id": task.node_id,
        "role": task.role,
        "assignee": task.assignee,
        "assignee_kind": task.assignee_kind,
        "target_agent": task.target_agent,
        "created_at": task.created_at,
        "due_at": task.due_at,
        "delivered_at": task.delivered_at,
        "delivered_by": task.delivered_by,
        "delivered_via": task.delivered_via,
        "result": task.result,
        "claimed_by": task.claimed_by,
        "claim_at": task.claim_at,
        "lease_expires_at": task.lease_expires_at,
        "heartbeat_at": task.heartbeat_at,
        "a2a_task_id": task.a2a_task_id,
        "reminders": task.reminders,
        "last_remind_at": task.last_remind_at,
        "handoff_version": task.handoff_version,
        "over_budget": task.over_budget,
        "budget": task.budget,
        "usage": task.usage,
        "on_timeout": task.on_timeout,
    }


def build_collab_router(runtime=None) -> APIRouter:
    runtime = runtime or get_collab()
    router = APIRouter(prefix="/collab", tags=["collab-inbox"])

    def _load(task_id: str) -> WorkOrder:
        task = runtime.store.find(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"工单不存在: {task_id}")
        return task

    @router.get("/tasks")
    async def list_tasks(assignee: str = "", role: str = "", kind: str = "",
                         bucket: str = "", limit: int = 50) -> dict[str, Any]:
        """收件箱清单（在办/交付/超时三桶为收发台主视图）。"""
        now = runtime._clock()
        tasks = runtime.store.list(assignee=assignee, role=role, kind=kind,
                                   bucket=bucket, limit=max(1, min(limit, 200)),
                                   now=now)
        return {
            "tasks": [_task_view(runtime, t, now) for t in tasks],
            "count": len(tasks),
            "assignee": assignee,
            "bucket": bucket,
        }

    @router.get("/tasks/{task_id}")
    async def get_task(task_id: str) -> dict[str, Any]:
        task = _load(task_id)
        view = _task_view(runtime, task, runtime._clock())
        view["history"] = task.history
        return view

    @router.post("/tasks/{task_id}/claim")
    async def claim_task(task_id: str, req: ClaimRequest) -> dict[str, Any]:
        """认领（抢占锁 CAS）：持锁者可心跳续租与交付。"""
        try:
            task, token = runtime.store.claim(
                task_id, claimed_by=req.claimed_by,
                lease_seconds=max(1.0, req.lease_seconds))
        except CollabError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"task": _task_view(runtime, task, runtime._clock()),
                "claim_token": token}

    @router.post("/tasks/{task_id}/heartbeat")
    async def heartbeat_task(task_id: str, req: HeartbeatRequest) -> dict[str, Any]:
        try:
            task = runtime.store.heartbeat(task_id, req.claim_token)
        except CollabError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"task": _task_view(runtime, task, runtime._clock()),
                "lease_expires_at": task.lease_expires_at}

    @router.post("/tasks/{task_id}/deliver")
    async def deliver_task(task_id: str, req: DeliverRequest) -> dict[str, Any]:
        """交付回流（真收真交）：交付物进工单账并恢复流程续跑。"""
        try:
            task = await runtime.deliver_and_resume(
                task_id, req.result, delivered_by=req.delivered_by,
                via="api", claim_token=req.claim_token)
        except CollabError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"task": _task_view(runtime, task, runtime._clock())}

    @router.post("/tasks/{task_id}/decide")
    async def decide_task(task_id: str, req: DecideRequest) -> dict[str, Any]:
        """审批决定（approval/预算闸工单）：approve=放行续跑 / reject=驳回终止。"""
        from plugin.flow_engine import FlowRunError
        task = _load(task_id)
        engine = runtime._get_engine()
        try:
            if req.decision == "approve":
                task = await runtime.deliver_and_resume(
                    task_id, req.result if req.result is not None else (req.note or "同意"),
                    delivered_by=req.decided_by, via="approval")
            elif req.decision == "reject":
                if task.run_id:
                    try:
                        await engine.resolve_approval(
                            task.run_id, "reject", decided_by=req.decided_by, note=req.note)
                    except FlowRunError as e:
                        raise HTTPException(status_code=409, detail=str(e))
                runtime.store.close(task_id, STATUS_FAILED,
                                    note=f"驳回: {req.note}")   # 幂等同步收口
                task = _load(task_id)
            else:
                raise HTTPException(status_code=400,
                                    detail=f"decision 非法: {req.decision!r}（approve | reject）")
        except CollabError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"task": _task_view(runtime, task, runtime._clock())}

    @router.post("/tasks/{task_id}/remind")
    async def remind_task(task_id: str, req: RemindRequest) -> dict[str, Any]:
        """催办（Pulse 超时催办钩子同源；最小间隔去重）。"""
        _load(task_id)
        task = runtime.remind(task_id, note=req.note)
        return {"task": _task_view(runtime, task, runtime._clock()),
                "reminders": task.reminders}

    @router.post("/tasks/{task_id}/handoff")
    async def handoff_task(task_id: str, req: HandoffRequest) -> dict[str, Any]:
        """D5 case handoff 交办/转派（带版本校验，被取代即作废 409 case_handoff_superseded）。"""
        _load(task_id)
        try:
            task = runtime.store.handoff(
                task_id, to_assignee=req.to_assignee, by=req.decided_by,
                note=req.note, expect_version=req.expect_version)
        except CollabError as e:
            raise HTTPException(status_code=409, detail=str(e))
        return {"task": _task_view(runtime, task, runtime._clock()),
                "handoff_version": task.handoff_version}

    @router.post("/tasks/{task_id}/cancel")
    async def cancel_task(task_id: str, req: CancelRequest) -> dict[str, Any]:
        _load(task_id)
        task = runtime.store.close(task_id, STATUS_CANCELLED, note=req.reason or "人工取消")
        return {"task": _task_view(runtime, task, runtime._clock())}

    @router.get("/stats")
    async def collab_stats() -> dict[str, Any]:
        """收发台看板：三桶计数 + 预算策略状态（ok/warning/hard_stop 直出）。"""
        now = runtime._clock()
        all_tasks = runtime.store.list(limit=10_000, now=now)
        buckets = {"open": 0, "delivered": 0, "overdue": 0, "closed": 0}
        by_assignee: dict[str, int] = {}
        for task in all_tasks:
            buckets[task.bucket(now)] = buckets.get(task.bucket(now), 0) + 1
            if task.is_open():
                by_assignee[task.assignee] = by_assignee.get(task.assignee, 0) + 1
        policies = [
            {"id": p.id, "scope": p.scope, "key": p.key, "window": p.window,
             "max_cost_usd": p.max_cost_usd, "max_tokens": p.max_tokens,
             "warn_percent": p.warn_percent, "hard_stop": p.hard_stop,
             "state": p.state}
            for p in runtime.ledger.policies()
        ]
        return {
            "buckets": buckets,
            "total": len(all_tasks),
            "open_by_assignee": by_assignee,
            "budget_policies": policies,
        }

    return router
