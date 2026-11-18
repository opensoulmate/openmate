"""
协作节点引擎整合测试 — agent_node（A2A 回流续跑）/ human_node（收件箱交付续跑）
/ approval 整合 / 预算闸转人工续跑（C2→C3）/ 超时处置与催办账。

真实性口径：流程引擎/工单账/角色路由/预算闸全真件执行（FlowEngine+
CollabRuntime+WorkOrderStore），A2A 通道为可注入传输（协议字节真走 HTTP 的全链
在 test_flow_collab_e2e.py）。
"""
from __future__ import annotations

import asyncio

import pytest

from plugin.flow_collab import (
    BudgetLedger,
    CollabRuntime,
    STATUS_CANCELLED,
    STATUS_DELIVERED,
    STATUS_FAILED,
    WorkOrderStore,
    reset_collab,
)
from plugin.flow_def import FlowDefError, parse_flow
from plugin.flow_engine import FlowEngine, reset_engine


class FakeChannel:
    """A2A 通道测试桩（形状=dispatch/fetch）：脚本化结果序列。"""

    def __init__(self, results=None):
        self.dispatches = []
        self.fetches = []
        self._results = list(results or [])

    async def dispatch(self, **kw):
        self.dispatches.append(kw)
        return {"a2a_task_id": f"a2a-{len(self.dispatches)}", "status": "pending"}

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
        calls.append((name, args, approved))
        return {"echo": args}

    engine.set_tool_caller(_tool_caller)
    runtime = engine.collab()
    yield engine, runtime, calls
    reset_engine()
    reset_collab()


def _register(engine, data: dict):
    return engine.register_flow(parse_flow(data), plugin_id="test-plugin")


def test_agent_node_dispatch_suspend_deliver_resume(env):
    """agent_node 全程：派发挂起 → a2a/task/result 回流交付 → 流程续跑并消费结果"""
    engine, runtime, calls = env
    channel = FakeChannel(results=[
        {"state": "working", "result": ""},
        {"state": "completed", "result": "SoulMate 干完的结论"},
    ])
    runtime.channel = channel
    _register(engine, {
        "id": "agent-flow", "name": "双agent流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "ask", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "分析数据", "timeout_hours": 1},
            {"id": "use", "type": "tool", "tool": "p__t",
             "args": {"conclusion": "$.ask.result"}},
        ],
    })
    run = asyncio.run(engine.run_now("agent-flow", {"type": "manual"}))
    assert run.status == "waiting-approval"
    assert run.approval.kind == "agent" and run.approval.assignee == "alice"
    task = runtime.store.load(run.approval.task_id)
    assert task.target_agent == "soulmate-a" and task.a2a_task_id == "a2a-1"
    assert task.claimed_by.startswith("soulmate:")          # A1 领取后才出网
    # 第一次轮询仍在途（不误交付）
    assert asyncio.run(runtime.poll_agent_tasks()) == 0
    # 第二次轮询结果回流 → 交付 → 流程续跑 → 下游消费结果
    assert asyncio.run(runtime.poll_agent_tasks()) == 1
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["ask"]["result"] == "SoulMate 干完的结论"
    assert calls and calls[0][1]["conclusion"] == "SoulMate 干完的结论"
    assert runtime.store.load(task.task_id).status == STATUS_DELIVERED


def test_human_node_inbox_deliver_resumes(env):
    """human_node 真收真交：工单入收件箱（归属/角色/期限）→ 交付回流 → 续跑"""
    engine, runtime, calls = env
    _register(engine, {
        "id": "human-flow", "name": "人工节点流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "review", "type": "human", "user": "bob",
             "task": "复核并交付结论", "form": "文本结论", "timeout_hours": 2},
            {"id": "use", "type": "tool", "tool": "p__t",
             "args": {"v": "$.review.result"}},
        ],
    })
    run = asyncio.run(engine.run_now("human-flow", {"type": "manual"}))
    assert run.status == "waiting-approval" and run.approval.kind == "human"
    task = runtime.store.load(run.approval.task_id)
    assert task.assignee == "bob" and task.status in ("todo", "working")
    assert task.bucket() == "open"                          # 收发台在办桶
    # 真收：认领（心跳续租）
    task, token = runtime.store.claim(task.task_id, claimed_by="bob")
    runtime.store.heartbeat(task.task_id, token)
    # 真交：交付回流（带领取令牌 CAS 回执）
    asyncio.run(runtime.deliver_and_resume(task.task_id, "结论：通过",
                                           delivered_by="bob", claim_token=token))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["review"]["result"] == "结论：通过"
    assert calls[0][1]["v"] == "结论：通过"
    assert runtime.store.load(task.task_id).bucket() == "delivered"


def test_approval_node_shares_inbox_and_resolution(env):
    """与既有 approval 整合：审批闸同样开单进收件箱，经收件台交付=批准续跑"""
    engine, runtime, calls = env
    _register(engine, {
        "id": "approval-flow", "name": "审批整合", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "confirm", "type": "approval", "form": "确认表单",
             "timeout_hours": 1},
            {"id": "after", "type": "tool", "tool": "p__t", "args": {}},
        ],
    })
    run = asyncio.run(engine.run_now("approval-flow", {"type": "manual"}))
    assert run.approval.kind == "approval" and run.approval.task_id
    task = runtime.store.load(run.approval.task_id)
    assert task.kind == "approval" and task.form == "确认表单"
    # 经收件台交付（=approve）→ 流程恢复
    asyncio.run(runtime.deliver_and_resume(task.task_id, "同意",
                                           delivered_by="boss"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["confirm"]["decision"] == "approved"   # approval 输出形状不变
    assert runtime.store.load(task.task_id).status == STATUS_DELIVERED


def test_budget_gate_blocks_dispatch_then_human_approve_resumes(env):
    """C2→C3 真闸：预算硬停拦截派发 → 转 human 审批放行 → 原地续跑真派发"""
    engine, runtime, calls = env
    channel = FakeChannel(results=[{"state": "completed", "result": "干完了"}])
    runtime.channel = channel
    runtime.ledger = BudgetLedger(daily_budget_usd=1.0)
    runtime.ledger.charge(assignee="alice", flow_id="budget-flow", cost_usd=5.0)
    _register(engine, {
        "id": "budget-flow", "name": "预算闸流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
            {"id": "end", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ],
    })
    run = asyncio.run(engine.run_now("budget-flow", {"type": "manual"}))
    # 被拦：挂预算闸（human 工单），不出网
    assert run.status == "waiting-approval" and run.approval.kind == "budget-gate"
    assert run.cursor == "work"                              # C3：放行后原地续跑
    assert channel.dispatches == []
    gate_task = runtime.store.load(run.approval.task_id)
    assert gate_task.kind == "human" and gate_task.payload.get("gate") == "budget"
    # 人工放行（经收件台交付=审批批准）→ waiver → 重入 work → 真派发
    asyncio.run(runtime.deliver_and_resume(gate_task.task_id, "同意放行",
                                           delivered_by="boss"))
    assert len(channel.dispatches) == 1                      # 放行后真出网
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval" and run.approval.kind == "agent"
    asyncio.run(runtime.deliver_and_resume(run.approval.task_id, "干完了",
                                           delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["work"]["result"] == "干完了"


def test_human_node_timeout_fail_and_skip_sync_task(env):
    """超时处置：fail→实例失败+工单 failed；skip→跳过续跑+工单 cancelled"""
    engine, runtime, calls = env
    for on_timeout, expect_status in (("fail", "failed"), ("skip", "completed")):
        reset_engine()
        reset_collab()
        engine = FlowEngine(engine.store.runs_dir)
        async def _tc(name, args, approved=False):
            calls.append((name, args, approved))
            return {"echo": args}
        engine.set_tool_caller(_tc)
        runtime = engine.collab()
        fid = f"timeout-{on_timeout}"
        _register(engine, {
            "id": fid, "name": fid, "triggers": [{"type": "manual"}],
            "nodes": [
                {"id": "wait", "type": "human", "user": "carol", "task": "交",
                 "timeout_hours": 1, "on_timeout": on_timeout},
                {"id": "next", "type": "action",
                 "actions": [{"type": "notify", "message": "继续"}]},
            ],
        })
        run = asyncio.run(engine.run_now(fid, {"type": "manual"}))
        task = runtime.store.load(run.approval.task_id)
        engine.check_timeouts(now=run.approval.created_at + 2 * 3600)
        if on_timeout == "skip":
            # skip 路径由调度器后台续跑（同步上下文 _fire_and_forget 不推进）——
            # 断点续跑语义：驱动一次执行收尾
            asyncio.run(engine.execute(run.run_id))
        run = engine.store.find_run(run.run_id)
        assert run.status == expect_status
        closed = runtime.store.load(task.task_id)
        if on_timeout == "fail":
            assert closed.status == STATUS_FAILED
        else:
            assert closed.status == STATUS_CANCELLED


def test_remind_sweep_marks_overdue_tasks(env):
    """催办账：过期未交付 → remind 记次数+留痕（Pulse 催办钩子同源）"""
    engine, runtime, calls = env
    task = runtime.create(kind="human", run_id="r", flow_id="f", node_id="n",
                          assignee="dave", instruction="交", timeout_hours=1)
    later = task.due_at + 10
    out = runtime.check_timeouts(now=later)
    assert out["reminded"] == 1
    reloaded = runtime.store.load(task.task_id)
    assert reloaded.reminders == 1 and reloaded.is_overdue(later)
    assert any(h["event"] == "reminded" for h in reloaded.history)
    # 最小间隔去重：立即再催不加次数
    runtime.check_timeouts(now=later + 1)
    assert runtime.store.load(task.task_id).reminders == 1


def test_agent_node_dispatch_failure_uses_on_error(env):
    """派发失败显式报因（A5 决策留痕）：on_error=skip → 跳过续跑，绝不静默"""
    engine, runtime, calls = env

    class BoomChannel:
        async def dispatch(self, **kw):
            raise RuntimeError("A2A 不可达")

        async def fetch(self, a2a_task_id):
            return {"state": "working", "result": ""}

    runtime.channel = BoomChannel()
    _register(engine, {
        "id": "fail-flow", "name": "派发失败流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "ask", "type": "agent", "user": "alice", "task": "x",
             "on_error": "skip"},
            {"id": "after", "type": "action",
             "actions": [{"type": "notify", "message": "继续"}]},
        ],
    })
    run = asyncio.run(engine.run_now("fail-flow", {"type": "manual"}))
    assert run.status == "completed"
    assert run.outputs["ask"]["skipped"] is True
    assert "A2A 不可达" in (run.outputs["ask"].get("error") or "")


def test_flow_def_validates_collab_nodes():
    """flow_def 校验：task/user|role 必填、budget 解析、target_agent 缺省"""
    with pytest.raises(FlowDefError, match="task"):
        parse_flow({"id": "f", "name": "f", "triggers": [{"type": "manual"}],
                    "nodes": [{"id": "n", "type": "human", "user": "a"}]})
    with pytest.raises(FlowDefError, match="user 或 role"):
        parse_flow({"id": "f", "name": "f", "triggers": [{"type": "manual"}],
                    "nodes": [{"id": "n", "type": "agent", "task": "x"}]})
    defn = parse_flow({
        "id": "f", "name": "f", "triggers": [{"type": "manual"}],
        "nodes": [{"id": "n", "type": "agent", "role": "engineer", "task": "x",
                   "budget": {"max_tokens": 10, "max_cost_usd": 0.5}}],
    })
    node = defn.node_map["n"]
    assert node.target_agent == "soulmate" and node.role == "engineer"
    assert node.budget.max_tokens == 10 and node.budget.max_cost_usd == 0.5
    with pytest.raises(FlowDefError, match="max_tokens"):
        parse_flow({"id": "f", "name": "f", "triggers": [{"type": "manual"}],
                    "nodes": [{"id": "n", "type": "human", "user": "a", "task": "x",
                               "budget": {"max_tokens": -1}}]})


# ── E 组：任务包/上下文隔离/成果回流 ──────────────────────────────

def test_task_package_context_isolation_and_modes(env):
    """E1 任务包：上下文隔离（只带 $. 引用的上游）+ compact 档 + 快照落 payload"""
    from plugin.flow_collab import build_task_package, collect_upstream_refs
    # 纯函数：引用收集（有序去重）/两档/快照 diff（E2 变更摘要）
    assert collect_upstream_refs("看 $.a 与 $.b", {"x": "$.a.c"}) == ["a", "b"]
    pkg_full = build_task_package(goal="汇总", form="结论", upstream={"a": {"v": 1}})
    assert "【任务】汇总" in pkg_full and "【验收】结论" in pkg_full
    assert "【上游输出】" in pkg_full and '"v": 1' in pkg_full
    pkg_compact = build_task_package(
        goal="汇总", upstream={"a": {"v": 1, "w": 2}}, mode="compact")
    assert "【上游输出(compact)】" in pkg_compact and "<dict×2" in pkg_compact
    pkg_diff = build_task_package(
        goal="汇总", upstream={"a": {"v": 2}, "b": 1},
        prev_snapshot={"a": {"v": 1}, "c": 9})
    assert "【变更摘要】" in pkg_diff and "~a" in pkg_diff \
        and "+b" in pkg_diff and "-c" in pkg_diff

    engine, runtime, calls = env
    _register(engine, {
        "id": "pkg-flow", "name": "任务包流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "a", "type": "tool", "tool": "p__t", "args": {"tag": "alpha"}},
            {"id": "b", "type": "tool", "tool": "p__t", "args": {"tag": "beta"}},
            {"id": "ask", "type": "human", "user": "x", "package": "full",
             "task": "看 $.a 的数据出结论", "form": "结论", "timeout_hours": 1},
        ],
    })
    run = asyncio.run(engine.run_now("pkg-flow", {"type": "manual"}))
    task = runtime.store.load(run.approval.task_id)
    # 上下文隔离：只带 a 的输出（引用的），绝不整包重灌 b（compact 档见纯函数断言）
    assert "alpha" in task.instruction and "beta" not in task.instruction
    assert "【上游输出】" in task.instruction
    assert task.payload["upstream_snapshot"] == {"a": {"echo": {"tag": "alpha"}}}
    assert task.payload["config_fingerprint"]


def test_review_gate_accept_reject_and_receipt(env):
    """E4 review gate：验收通过→完成；驳回→不算节点完成（flow_receipts 挂结论）"""
    import json
    import os
    engine, runtime, calls = env
    receipts_path = os.environ["FLOW_RECEIPTS_FILE"]
    for verdict_text, expect in (("验收通过，结论可靠", "completed"),
                                 ("驳回：数据口径不对", "failed")):
        fid = f"review-{expect}"
        _register(engine, {
            "id": fid, "name": fid, "triggers": [{"type": "manual"}],
            "nodes": [
                {"id": "accept", "type": "human", "user": "qa", "review": True,
                 "task": "验收交付物", "form": "验收意见", "timeout_hours": 1},
                {"id": "after", "type": "action",
                 "actions": [{"type": "notify", "message": "收尾"}]},
            ],
        })
        run = asyncio.run(engine.run_now(fid, {"type": "manual"}))
        asyncio.run(runtime.deliver_and_resume(
            run.approval.task_id, verdict_text, delivered_by="qa"))
        run = engine.store.find_run(run.run_id)
        assert run.status == expect
    # 验收结论挂 flow_receipts（凭证来自服务端，模型无法伪造）
    receipts = json.loads(open(receipts_path, encoding="utf-8").read())
    verdicts = [r for r in receipts if str(r.get("action", "")).startswith("review-")]
    assert {r["action"] for r in verdicts} == {"review-accept", "review-reject"}
    reject = next(r for r in verdicts if r["action"] == "review-reject")
    assert reject["ok"] is False and "验收驳回" in reject.get("error", "")


def test_config_fingerprint_changes_with_inputs():
    """E3 配置指纹：有效输入变更→指纹换代（丢旧会话连续性的依据）"""
    from plugin.flow_collab import config_fingerprint
    a = config_fingerprint("engineer", "soulmate", {"max_tokens": 10}, "full")
    assert a == config_fingerprint("engineer", "soulmate", {"max_tokens": 10}, "full")
    assert a != config_fingerprint("engineer", "soulmate", {"max_tokens": 20}, "full")
    assert a != config_fingerprint("manager", "soulmate", {"max_tokens": 10}, "full")
