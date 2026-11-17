"""
流程运行台 — 宿主内建流程运行面板的工具面（可观测：实例状态/节点日志/重试/回放）

设计文档: docs/plugin-system-design.md §5（可观测面板为宿主内建，不需要插件写 UI）
工具经 MCP 注册桥，UI 面板（ui/home.json）与 SoulMate 对话共用同一 handler。
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from plugin.flow_engine import FlowRunError, get_engine


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def handle_flow_list(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：已注册流程定义清单"""
    flows = get_engine().list_flows()
    return {"flows": flows, "count": len(flows)}


async def handle_run_flow(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：手动触发流程（到完成或人工审批挂起）"""
    engine = get_engine()
    flow_id = str(args.get("flow_id", "")).strip()
    inputs = args.get("inputs") or {}
    try:
        run = await engine.run_now(
            flow_id,
            {"type": "manual", "source": "flow-console"},
            inputs=inputs if isinstance(inputs, dict) else {},
            idempotency_key=str(args.get("idempotency_key", "") or ""),
        )
    except FlowRunError as e:
        return {"error": str(e)}
    return engine.run_summary(run)


def handle_run_list(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：流程实例列表（list 面板数据源）"""
    engine = get_engine()
    limit = int(args.get("limit", 20))
    runs = engine.store.list_runs(
        flow_id=str(args.get("flow_id", "") or "") or None,
        status=str(args.get("status", "") or "") or None,
        limit=max(1, min(limit, 200)),
    )
    items = []
    for run in runs:
        summary = engine.run_summary(run)
        items.append({
            "run_id": summary["run_id"],
            "flow": summary["flow_name"],
            "status": summary["status"],
            "trigger": summary["trigger"],
            "created_at": summary["created_at"],
            "duration_ms": summary["duration_ms"],
            "error": summary["error"],
        })
    return {"items": items, "count": len(items)}


def handle_run_detail(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：实例详情（report 面板注入形状：扁平 kv）"""
    engine = get_engine()
    run_id = str(args.get("run_id", "")).strip()
    run = engine.store.find_run(run_id)
    if run is None:
        return {"error": f"流程实例不存在: {run_id}"}
    return engine.run_detail_flat(run)


def handle_run_log(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：事件日志时间线（list 面板数据源；run_id 缺省=全局最近）"""
    engine = get_engine()
    limit = max(1, min(int(args.get("limit", 30)), 200))
    run_id = str(args.get("run_id", "") or "").strip()
    items: list[dict[str, Any]] = []
    if run_id:
        run = engine.store.find_run(run_id)
        if run is None:
            return {"error": f"流程实例不存在: {run_id}", "items": [], "count": 0}
        entries = run.log
    else:
        entries = []
        for run in engine.store.list_runs(limit=50):
            for entry in run.log:
                tagged = dict(entry)
                tagged["run_id"] = run.run_id
                entries.append(tagged)
        entries.sort(key=lambda e: e.get("ts", 0))
    for entry in entries[-limit:]:
        items.append({
            "time": _fmt_ts(entry.get("ts")),
            "run_id": entry.get("run_id", "") or "",
            "node": entry.get("node", ""),
            "event": entry.get("event", ""),
            "detail": entry.get("detail", ""),
        })
    if run_id:
        for item in items:
            item["run_id"] = run_id
    return {"items": items, "count": len(items)}


async def handle_run_approve(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：人工审批闸（approve 恢复执行 / reject 终止）"""
    engine = get_engine()
    run_id = str(args.get("run_id", "")).strip()
    decision = str(args.get("decision", "")).strip()
    note = str(args.get("note", "") or "")
    try:
        run = await engine.resolve_approval(
            run_id, decision, decided_by="用户", note=note)
    except FlowRunError as e:
        return {"error": str(e)}
    return engine.run_summary(run)


async def handle_run_replay(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：回放实例（同触发/输入重跑）"""
    engine = get_engine()
    run_id = str(args.get("run_id", "")).strip()
    try:
        run = await engine.replay(run_id)
    except FlowRunError as e:
        return {"error": str(e)}
    return engine.run_summary(run)
