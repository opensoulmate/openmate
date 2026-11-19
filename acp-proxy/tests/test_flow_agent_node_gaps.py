"""
agent_node 四缺口补齐实测 — INPUT_REQUIRED 转人工 / 任务级断点恢复 /
换人重派 / 派发失败本地降级

真实性口径：FlowEngine+CollabRuntime+WorkOrderStore 全真件；A2A 通道为可注入
传输桩（协议字节真走 HTTP 的链路见 test_flow_collab_e2e.py）。
"""
from __future__ import annotations

import asyncio

import pytest

from plugin.flow_collab import (
    CollabError,
    STATUS_CANCELLED,
    STATUS_DELIVERED,
    reset_collab,
)
from plugin.flow_engine import FlowEngine, FlowRunError, reset_engine


class FakeChannel:
    """A2A 通道测试桩：dispatch 返回可脚本化，fetch 按序出结果。"""

    def __init__(self, results=None, dispatch_out=None, boom=False):
        self.dispatches = []
        self.fetches = []
        self._results = list(results or [])
        self.dispatch_out = dispatch_out
        self.boom = boom

    async def dispatch(self, **kw):
        self.dispatches.append(kw)
        if self.boom:
            raise RuntimeError("A2A 不可达")
        return self.dispatch_out or {"a2a_task_id": f"a2a-{len(self.dispatches)}",
                                     "status": "pending"}

    async def fetch(self, a2a_task_id):
        self.fetches.append(a2a_task_id)
        if self._results:
            return self._results.pop(0)
        return {"state": "working", "result": ""}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_RUNS_DIR", str(tmp_path / "flow-runs"))
    monkeypatch.setenv("COLLAB_TASKS_DIR", str(tmp_path / "collab"))
    monkeypatch.setenv("COLLAB_COST_DB", str(tmp_path / "costs.db"))
    monkeypatch.setenv("ORG_CHART_FILE", str(tmp_path / "org.json"))
    reset_engine()
    reset_collab()
    engine = FlowEngine(tmp_path / "flow-runs")
    calls = []

    async def _tool_caller(name, args, approved=False):
        calls.append((name, dict(args), approved))
        return {"echo": args}

    engine.set_tool_caller(_tool_caller)
    runtime = engine.collab()
    yield engine, runtime, calls
    reset_engine()
    reset_collab()


def _register(engine, data: dict):
    return engine.register_flow(parse_flow(data), plugin_id="test-plugin")


from plugin.flow_def import parse_flow   # noqa: E402  （夹具上方不引用）


def _agent_flow(fid="gap-flow", node_extra=None):
    node = {"id": "work", "type": "agent", "user": "alice",
            "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1}
    node.update(node_extra or {})
    return {
        "id": fid, "name": fid, "triggers": [{"type": "manual"}],
        "nodes": [
            node,
            {"id": "after", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ],
    }


# ─────────────────── ① INPUT_REQUIRED 转人工 ───────────────────

def test_input_required_poll_converts_to_human_and_resumes(env):
    """SoulMate 卡在缺输入（INPUT_REQUIRED）：转人工补输入单 → 人交付答案 → 流程续跑"""
    engine, runtime, calls = env
    channel = FakeChannel(results=[
        {"state": "input-required", "result": "报告口径用月度还是累计？"},
    ])
    runtime.channel = channel
    _register(engine, _agent_flow("input-flow"))
    run = asyncio.run(engine.run_now("input-flow", {"type": "manual"}))
    agent_task = runtime.store.load(run.approval.task_id)

    asyncio.run(runtime.poll_agent_tasks())

    # agent 单收口取消（留痕）；人工补输入单进收发台；闸重挂到人工单
    agent_task = runtime.store.load(agent_task.task_id)
    assert agent_task.status == STATUS_CANCELLED
    assert any(h["event"] == "input-required" for h in agent_task.history)
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval" and run.approval.kind == "human"
    human_task = runtime.store.load(run.approval.task_id)
    assert human_task.kind == "human"
    assert "报告口径用月度还是累计？" in human_task.instruction
    assert human_task.payload.get("kind") == "input-required"
    assert any(e["event"] == "input-required-converted" for e in run.log)

    # 人交付答案 = 该节点交付回流（同一恢复面）→ 流程续跑完成
    asyncio.run(runtime.deliver_and_resume(
        human_task.task_id, "用月度口径", delivered_by="alice"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["work"]["result"] == "用月度口径"


def test_input_required_dispatch_sync_result_converts(env):
    """派发即回 INPUT_REQUIRED（确定性处理端同步回）：同样转人工不静默"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel(dispatch_out={
        "a2a_task_id": "a2a-1", "state": "input-required",
        "result": "INPUT_REQUIRED: 缺目标日期"})
    _register(engine, _agent_flow("input-flow2"))
    run = asyncio.run(engine.run_now("input-flow2", {"type": "manual"}))
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval" and run.approval.kind == "human"
    human_task = runtime.store.load(run.approval.task_id)
    assert "缺目标日期" in human_task.instruction
    asyncio.run(runtime.deliver_and_resume(
        human_task.task_id, "本周五", delivered_by="alice"))
    assert engine.store.find_run(run.run_id).status == "completed"


# ─────────────────── ② 任务级断点恢复 ───────────────────

def test_task_level_breakpoint_resume(env, tmp_path):
    """崩在「交付已落账、流程未恢复」之间：recover_deliveries 补恢复续跑"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    _register(engine, _agent_flow("bp-flow"))
    run = asyncio.run(engine.run_now("bp-flow", {"type": "manual"}))
    task_id = run.approval.task_id
    # 模拟崩溃点：工单交付已落盘，但 deliver_and_resume 的恢复没跑
    runtime.store.deliver(task_id, "干完了", delivered_by="soulmate-a")
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval"       # 流程还挂着（断点）

    # 重启（新引擎实例，同一落盘账本）→ 任务级断点恢复
    reset_engine()
    engine2 = FlowEngine(tmp_path / "flow-runs")
    async def _tc(name, args, approved=False):
        return {"echo": args}
    engine2.set_tool_caller(_tc)
    engine2.register_flow(parse_flow(_agent_flow("bp-flow")), plugin_id="test-plugin")
    handled = asyncio.run(engine2.recover_deliveries())
    assert handled == 1
    run = engine2.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["work"]["result"] == "干完了"
    assert any(e["event"] == "recover-delivery" for e in run.log)


def test_task_level_breakpoint_resume_failed_task_closes(env):
    """工单已判死（failed）而流程还挂着：断点恢复收口为失败（不留僵尸）"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    _register(engine, _agent_flow("bp-flow2"))
    run = asyncio.run(engine.run_now("bp-flow2", {"type": "manual"}))
    runtime.store.close(run.approval.task_id, "failed", note="超时判死")
    handled = asyncio.run(engine.recover_deliveries())
    assert handled == 1
    run = engine.store.find_run(run.run_id)
    assert run.status == "failed" and "failed" in (run.error or "")


# ─────────────────── ③ 换人重派 ───────────────────

def test_reassign_waiting_run_to_other_agent(env):
    """在途实例换人重派：工单交办新归属 + 换 target_agent 重派（唤醒合并防双发）"""
    engine, runtime, calls = env
    channel = FakeChannel()
    runtime.channel = channel
    _register(engine, _agent_flow("re-flow"))
    run = asyncio.run(engine.run_now("re-flow", {"type": "manual"}))
    assert len(channel.dispatches) == 1
    assert channel.dispatches[0]["target_agent"] == "soulmate-a"

    out = asyncio.run(engine.reassign_node(
        run.run_id, "work", to_assignee="bob", target_agent="soulmate-b",
        by="boss", note="soulmate-a 太慢"))
    assert out["assignee"] == "bob" and out["target_agent"] == "soulmate-b"
    assert out["dispatched"] is True
    assert len(channel.dispatches) == 2
    assert channel.dispatches[1]["target_agent"] == "soulmate-b"
    task = runtime.store.load(out["task_id"])
    assert task.assignee == "bob"
    assert any(h["event"] == "handoff" for h in task.history)   # D5 留痕
    assert any(e["event"] == "reassigned" for e in
               engine.store.find_run(run.run_id).log)

    # 新 SoulMate 交付 → 续跑完成（旧归属的结果不再被采信：交接即清场）
    asyncio.run(runtime.deliver_and_resume(
        out["task_id"], "bob 干完了", delivered_by="soulmate-b"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed" and run.outputs["work"]["result"] == "bob 干完了"


def test_reassign_failed_run_resumes_in_place(env):
    """失败实例换人重派：原地续跑（同 retry 语义），新归属重派收口"""
    engine, runtime, calls = env
    channel = FakeChannel()
    runtime.channel = channel
    _register(engine, _agent_flow("re-flow2"))
    run = asyncio.run(engine.run_now("re-flow2", {"type": "manual"}))
    # 打成失败实例（审批驳回）
    asyncio.run(engine.resolve_approval(
        run.run_id, "reject", decided_by="boss", note="不行"))
    assert engine.store.find_run(run.run_id).status == "failed"

    out = asyncio.run(engine.reassign_node(
        run.run_id, "work", to_assignee="bob", by="boss", note="换人再试"))
    assert out["resumed"] is True
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval"       # 重派后挂起等新交付
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "重派后干完了", delivered_by="bob"))
    assert engine.store.find_run(run.run_id).status == "completed"


def test_reassign_rejects_non_collab_node(env):
    """只有 agent/human 协作节点可换人重派（工具节点显式拒绝）"""
    engine, runtime, calls = env
    from plugin.flow_def import parse_flow as _pf
    engine.register_flow(_pf({
        "id": "tool-flow", "name": "t", "triggers": [{"type": "manual"}],
        "nodes": [{"id": "t1", "type": "tool", "tool": "p__t"}],
    }), plugin_id="test-plugin")
    run = asyncio.run(engine.run_now("tool-flow", {"type": "manual"}))
    with pytest.raises(FlowRunError, match="只有 agent/human"):
        asyncio.run(engine.reassign_node(run.run_id, "t1", to_assignee="bob"))


# ─────────────────── ④ 派发失败本地降级 ───────────────────

def test_dispatch_failure_falls_back_to_local_human(env):
    """A2A 派发失败：任务不丢——本地人工降级（挂起等交付），流程继续"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel(boom=True)
    _register(engine, _agent_flow("fb-flow"))
    run = asyncio.run(engine.run_now("fb-flow", {"type": "manual"}))
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval" and run.approval.kind == "human"
    task = runtime.store.load(run.approval.task_id)
    assert task.kind == "human"
    assert task.payload.get("fallback") == "dispatch-failed"
    assert "本地降级" in task.instruction
    assert any(e["event"] == "dispatch-fallback-local" for e in run.log)

    # 本地人工交付 → 流程照常收口
    asyncio.run(runtime.deliver_and_resume(
        task.task_id, "本地干完了", delivered_by="alice"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["work"]["result"] == "本地干完了"


def test_dispatch_failure_on_error_skip_keeps_explicit_semantics(env):
    """显式 on_error=skip/goto 仍按声明处置（本地降级只兜默认 fail 面）"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel(boom=True)
    data = _agent_flow("fb-flow2", {"on_error": "skip"})
    _register(engine, data)
    run = asyncio.run(engine.run_now("fb-flow2", {"type": "manual"}))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["work"]["skipped"] is True
    assert "A2A 不可达" in (run.outputs["work"].get("error") or "")
