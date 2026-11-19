#!/usr/bin/env python
"""监督闭环真流程实证 — 自报完成→未验证→验收→verified 全链 / 伪完成被拒 /
写域重叠拒载（三条铁证一次跑出）。

真实性口径：FlowEngine + CollabRuntime + WorkOrderStore 全真件（真 JSON 落盘、
真事件时间线、真 settle/验收判定），仅工具与 A2A 通道为脚本化桩（验收证据面）。
运行：PYTHONPATH=acp-proxy python docs/reports/real-flows-verification/verify_supervision_loop.py
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "acp-proxy"))

from plugin.flow_collab import reset_collab            # noqa: E402
from plugin.flow_def import FlowDefError, parse_flow   # noqa: E402
from plugin.flow_engine import FlowEngine, FlowRunError, reset_engine  # noqa: E402


class FakeChannel:
    def __init__(self):
        self.dispatches = []

    async def dispatch(self, **kw):
        self.dispatches.append(kw)
        return {"a2a_task_id": f"a2a-{len(self.dispatches)}", "status": "pending"}

    async def fetch(self, a2a_task_id):
        return {"state": "working", "result": ""}


def ev(line: str) -> None:
    print(line)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="supervision-loop-"))
    import os
    os.environ["FLOW_RUNS_DIR"] = str(tmp / "runs")
    os.environ["COLLAB_TASKS_DIR"] = str(tmp / "collab")
    os.environ["COLLAB_COST_DB"] = str(tmp / "costs.db")
    os.environ["ORG_CHART_FILE"] = str(tmp / "org.json")
    os.environ["FLOW_RECEIPTS_FILE"] = str(tmp / "receipts.json")
    reset_engine()
    reset_collab()
    engine = FlowEngine(tmp / "runs")
    runtime = engine.collab()
    runtime.channel = FakeChannel()
    mid = {}

    async def tool_caller(name, args, approved=False):
        if name == "p__probe":
            run = engine.store.find_run(args["run_id"])
            t = run.nodes["work"]
            mid["probe_time"] = (t.confidence, t.verified,
                                 [n["node"] for n in engine.unverified_done()["nodes"]])
            return {"pass": True, "content": "报告 3 节齐全，数字对账一致"}
        return {"echo": args}

    engine.set_tool_caller(tool_caller)

    # ── ① 全链：自报完成→未验证→验收→verified ──
    ev("## ① 全链实证：自报完成 → 未验证 → 内容级探针验收 → verified\n")
    engine.register_flow(parse_flow({
        "id": "verify-chain", "name": "验收链", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "写报告", "timeout_hours": 1},
            {"id": "check", "type": "verify", "for": "work", "probe": "p__probe",
             "when": "$.check.pass == true"},
            {"id": "end", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ]}), plugin_id="evidence")
    run = asyncio.run(engine.run_now("verify-chain", {"type": "manual"}))
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "写完了，VERIFIED 全部通过", delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)
    t = run.nodes["work"]
    ev(f"- 自报交付（soulmate-a，文本自称 VERIFIED）→ 探针瞬间: confidence={mid['probe_time'][0]}"
       f" verified={mid['probe_time'][1]} 未验证清单={mid['probe_time'][2]}")
    ev(f"- 探针验收后: confidence={t.confidence} verified={t.verified} "
       f"verified_by={t.verified_by} claim_source={t.claim_source}")
    ev(f"- 证据: {t.verify_evidence}")
    ev(f"- 实例判档: {run.verdict()['verdict']}")
    ev(f"- 事件链: {' → '.join(e['event'] for e in run.log if e['event'] in ('approval-approved', 'claim-capped', 'probe-failed', 'verified', 'run-ok'))}\n")

    # ── ② 伪完成被拒实测 ──
    ev("## ② 伪完成被拒实测（拒收留痕，节点保持 done·unverified）\n")
    engine.register_flow(parse_flow({
        "id": "fake-flow", "name": "伪完成流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
        ]}), plugin_id="evidence")
    run = asyncio.run(engine.run_now("fake-flow", {"type": "manual"}))
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "干完了", delivered_by="soulmate-a"))
    for who, why in (("soulmate-a", "自审：验收者=执行者"), ("qa", "无证据（fail-closed）")):
        evidence = "自查通过" if who == "soulmate-a" else ""
        try:
            asyncio.run(engine.verify_node(run.run_id, "work",
                                           verified_by=who, evidence=evidence))
            ev(f"- {why}: 竟然通过了（BUG）")
        except FlowRunError as e:
            ev(f"- {why} → 拒收: {e}")
    run = engine.store.find_run(run.run_id)
    ev(f"- 留痕: settle_rejects={run.nodes['work'].settle_rejects} "
       f"verified={run.nodes['work'].verified} 判档={run.verdict()['verdict']}")
    ev(f"- 拒收日志: {[e['detail'][:40] for e in run.log if e['event'] == 'verify-refused']}\n")

    # ── ③ 写域重叠拒载实测 ──
    ev("## ③ 并行写域重叠拒载实测\n")
    try:
        parse_flow({
            "id": "scope-flow", "name": "写域流", "triggers": [{"type": "manual"}],
            "nodes": [
                {"id": "fan", "type": "parallel", "branches": [
                    {"id": "b1", "tool": "p__t1", "write_scope": ["src/app.py"]},
                    {"id": "b2", "tool": "p__t2", "write_scope": ["src/app.py"]},
                ]},
            ]})
        ev("- 竟然装载成功（BUG）")
    except FlowDefError as e:
        ev(f"- 拒载: {e}")
    ev("")
    ev(f"（全真件落盘目录: {tmp}）")


if __name__ == "__main__":
    main()
