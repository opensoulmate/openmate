"""
流程引擎单测 — 定义校验/安全表达式/cron/执行器/幂等/重试/审批闸/回放/断点续跑/软超时

覆盖（docs/plugin-system-design.md §5 语义要点）：
① 表达式只读上游节点输出（jsonpath 子集，禁任意代码）
② 节点级失败重试（max/backoff）+ on_error(fail|skip|goto:<node>)
③ 人工审批节点：暂停→批准/驳回→恢复；超时策略 fail|skip
④ 幂等（触发级 idempotency_key）+ 实例状态落库 + 断点续跑 + 回放
⑤ 软超时预算：超时只记录 over_budget，绝不杀执行（禁硬超时杀铁律）
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from pathlib import Path

import pytest

from plugin.flow_def import (
    EvalContext,
    ExprError,
    FlowDefError,
    cron_matches,
    eval_expr,
    parse_flow,
    resolve_value,
)
from plugin.flow_engine import FlowEngine, FlowRunError


# ─────────────────────────── 工具桩 ───────────────────────────

class FakeTools:
    """可编排的工具桩：按名字出结果/注入失败，记录每次调用"""

    def __init__(self):
        self.calls: list[tuple[str, dict, bool]] = []
        self.fail_times: dict[str, int] = {}
        self.responses: dict[str, list] = {}

    async def __call__(self, name: str, args: dict, approved: bool = False) -> dict:
        self.calls.append((name, dict(args), approved))
        if self.fail_times.get(name, 0) > 0:
            self.fail_times[name] -= 1
            raise RuntimeError(f"{name} 人为失败")
        queue = self.responses.get(name)
        if queue:
            return queue.pop(0)
        if name == "p__log":   # 默认：喂给 decision($.count>0) 与 loop 的数据源
            return {"items": [{"name": "甲"}, {"name": "乙"}], "count": 2}
        return {"ok": True, "echo": args}


def make_engine(tmp_path: Path, tools: FakeTools | None = None) -> FlowEngine:
    tools = tools or FakeTools()
    engine = FlowEngine(tmp_path / "runs", tools)
    engine._fake_tools = tools  # 测试断言用
    return engine


BASIC_FLOW = {
    "id": "t-basic",
    "name": "基础流",
    "version": 1,
    "triggers": [{"type": "manual"}],
    "nodes": [
        {"id": "fetch", "type": "tool", "tool": "p__log", "args": {"limit": 5}},
        {"id": "check", "type": "decision", "when": "$.fetch.count > 0",
         "then": "loop1", "else": "done"},
        {"id": "loop1", "type": "loop", "over": "$.fetch.items", "body": "send"},
        {"id": "send", "type": "tool", "tool": "p__greet", "args": {"name": "$item.name"}},
        {"id": "confirm", "type": "approval", "form": "home",
         "timeout_hours": 72, "on_timeout": "fail"},
        {"id": "report", "type": "tool", "tool": "p__report",
         "args": {"run": "$run.id", "greeting": "$.send.echo.name"}},
        {"id": "done", "type": "action",
         "actions": [{"type": "notify", "channel": "in-app", "message": "完成"}]},
    ],
    "observability": {"panel": "flow-run", "retain_runs": 50},
}


# ─────────────────────────── ① 表达式 ───────────────────────────

def test_expr_reads_upstream_outputs():
    ctx = EvalContext(
        outputs={"fetch": {"count": 3, "items": [{"name": "甲"}]}},
        item={"name": "甲"},
        run={"id": "run_1", "trigger": "cron"},
    )
    assert eval_expr("$.fetch.count > 0", ctx) is True
    assert eval_expr("$.fetch.count > 5", ctx) is False
    assert eval_expr("$.fetch.count >= 3 && $item.name == '甲'", ctx) is True
    assert eval_expr("$.fetch.count > 5 || $run.trigger == 'cron'", ctx) is True
    assert eval_expr("!($.fetch.count > 5)", ctx) is True
    assert eval_expr("$.fetch.items.0.name", ctx) == "甲"
    assert eval_expr("$run.id", ctx) == "run_1"
    assert eval_expr("$.missing.field", ctx) is None


def test_expr_rejects_arbitrary_code():
    ctx = EvalContext()
    for bad in ("__import__('os')", "1 + 1", "open('/etc/passwd')", "a b c"):
        with pytest.raises(ExprError):
            eval_expr(bad, ctx)


def test_resolve_value_substitution():
    ctx = EvalContext(outputs={"a": {"v": 42}}, item={"name": "甲"})
    assert resolve_value({"x": "$.a.v", "y": "$item.name", "z": 1}, ctx) == {
        "x": 42, "y": "甲", "z": 1}
    assert resolve_value(["$.a.v", "常量"], ctx) == [42, "常量"]


def test_cron_matches_fields():
    assert cron_matches("0 9 * * 1-5", dt.datetime(2026, 11, 17, 9, 0))
    assert not cron_matches("0 9 * * 1-5", dt.datetime(2026, 11, 17, 10, 0))
    assert not cron_matches("0 9 * * 1-5", dt.datetime(2026, 11, 15, 9, 0))  # 周日
    assert cron_matches("*/5 * * * *", dt.datetime(2026, 11, 17, 10, 15))
    assert cron_matches("0 0 * * 7", dt.datetime(2026, 11, 15, 0, 0))  # 7=周日
    assert cron_matches("30 8-18 * * *", dt.datetime(2026, 11, 17, 14, 30))
    with pytest.raises(ValueError):
        cron_matches("bad", dt.datetime(2026, 11, 17, 9, 0))


# ─────────────────────────── 定义校验 ───────────────────────────

def test_flow_def_validates_references():
    with pytest.raises(FlowDefError, match="引用不存在的节点"):
        parse_flow({
            "id": "t1", "name": "x", "triggers": [{"type": "manual"}],
            "nodes": [{"id": "a", "type": "decision", "when": "1 == 1",
                       "then": "zzz", "else": "a"}],
        }, source="t1")


def test_flow_def_rejects_bad_tool_and_retry():
    with pytest.raises(FlowDefError, match="tool 非法"):
        parse_flow({
            "id": "t2", "name": "x", "triggers": [{"type": "manual"}],
            "nodes": [{"id": "a", "type": "tool", "tool": "bad"}],
        }, source="t2")
    with pytest.raises(FlowDefError, match="retry.max"):
        parse_flow({
            "id": "t3", "name": "x", "triggers": [{"type": "manual"}],
            "nodes": [{"id": "a", "type": "tool", "tool": "p__t",
                       "retry": {"max": -1}}],
        }, source="t3")


def test_flow_def_loop_body_skips_in_sequence():
    defn = parse_flow(BASIC_FLOW, source="basic")
    assert defn.body_node_ids == frozenset({"send"})
    assert defn.next_node_id("loop1") == "confirm"   # body 节点不占顺序位
    assert defn.first_node_id() == "fetch"


# ─────────────────────────── 执行器 ───────────────────────────

def test_full_chain_manual_trigger_with_approval():
    """manual 触发 → 工具 → decision → loop → 人工审批挂起 → 批准 → 出报告"""
    tools = FakeTools()
    tools.responses["p__log"] = [{"items": [{"name": "甲"}, {"name": "乙"}], "count": 2}]
    engine = make_engine(tmp_path_runs(), tools)
    engine.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")

    async def scenario():
        run = await engine.run_now("t-basic", {"type": "manual"})
        assert run.status == "waiting-approval"
        assert run.nodes["fetch"].status == "ok"
        assert run.nodes["loop1"].loop_items == 2
        assert run.nodes["confirm"].status == "waiting-approval"
        # 审批前不出报告（工具级 approval=always 也不该被调用）
        assert all(name != "p__report" for name, _, _ in tools.calls)
        run = await engine.resolve_approval(run.run_id, "approve",
                                            decided_by="用户", note="OK")
        assert run.status == "completed", run.error
        assert run.outputs["report"]["echo"]["run"] == run.run_id
        assert run.notifications[0]["message"] == "完成"
        return run

    run = asyncio.run(scenario())
    detail = engine.run_detail_flat(run)
    assert "节点 fetch" in detail and "事件日志" in detail
    assert any(e["event"] == "approval-approved" for e in run.log)


def test_approval_reject_fails_run():
    engine = make_engine(tmp_path_runs())
    engine.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")

    async def scenario():
        run = await engine.run_now("t-basic", {"type": "manual"})
        run = await engine.resolve_approval(run.run_id, "reject", note="不行")
        assert run.status == "failed" and "驳回" in run.error
        with pytest.raises(FlowRunError):
            await engine.resolve_approval(run.run_id, "approve")

    asyncio.run(scenario())


def test_retry_then_success_and_exhaust():
    tools = FakeTools()
    tools.fail_times["p__flaky"] = 2   # 前两次失败，第三次成功
    tools.fail_times["p__dead"] = 99   # 永远失败
    engine = make_engine(tmp_path_runs(), tools)
    engine.register_flow(parse_flow({
        "id": "t-retry", "name": "x", "version": 1,
        "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "flaky", "type": "tool", "tool": "p__flaky",
             "retry": {"max": 3, "backoff": "fixed", "base_seconds": 0.001}},
            {"id": "dead", "type": "tool", "tool": "p__dead",
             "retry": {"max": 1, "backoff": "fixed", "base_seconds": 0.001}},
            {"id": "tail", "type": "action",
             "actions": [{"type": "notify", "channel": "in-app", "message": "到此"}]},
        ],
    }, source="retry"), "test")

    async def scenario():
        run = await engine.run_now("t-retry", {"type": "manual"})
        assert run.status == "failed"
        assert run.nodes["flaky"].retries == 2 and run.nodes["flaky"].attempts == 3
        assert run.nodes["flaky"].status == "ok"
        assert run.nodes["dead"].retries == 1 and run.nodes["dead"].attempts == 2
        assert run.nodes["dead"].status == "failed"
        assert "人为失败" in (run.nodes["dead"].error or "")

    asyncio.run(scenario())


def test_on_error_skip_and_goto():
    tools = FakeTools()
    tools.fail_times["p__dead"] = 99
    engine = make_engine(tmp_path_runs(), tools)
    engine.register_flow(parse_flow({
        "id": "t-onerr", "name": "x", "version": 1,
        "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "dead", "type": "tool", "tool": "p__dead", "on_error": "skip"},
            {"id": "next", "type": "action",
             "actions": [{"type": "notify", "channel": "in-app", "message": "继续"}]},
        ],
    }, source="onerr"), "test")

    async def scenario():
        run = await engine.run_now("t-onerr", {"type": "manual"})
        assert run.status == "completed"
        assert run.nodes["dead"].status == "skipped"
        assert run.outputs["dead"]["skipped"] is True

    asyncio.run(scenario())

    engine2 = make_engine(tmp_path_runs(), tools)
    tools.fail_times["p__dead"] = 99
    engine2.register_flow(parse_flow({
        "id": "t-goto", "name": "x", "version": 1,
        "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "dead", "type": "tool", "tool": "p__dead",
             "on_error": "goto:rescue"},
            {"id": "never", "type": "tool", "tool": "p__never"},
            {"id": "rescue", "type": "action",
             "actions": [{"type": "notify", "channel": "in-app", "message": "救援"}]},
        ],
    }, source="goto"), "test")

    async def scenario_goto():
        run = await engine2.run_now("t-goto", {"type": "manual"})
        assert run.status == "completed"
        assert run.nodes["never"].status == "pending"   # 被跳过未执行
        assert run.notifications[0]["message"] == "救援"

    asyncio.run(scenario_goto())


def test_soft_timeout_never_kills():
    """软超时预算：超时只记 over_budget，执行照常完成（禁硬超时杀）"""
    tools = FakeTools()
    engine = make_engine(tmp_path_runs(), tools)

    async def slow_caller(name, args, approved=False):
        await asyncio.sleep(0.05)
        return {"ok": True}

    engine.set_tool_caller(slow_caller)
    engine.register_flow(parse_flow({
        "id": "t-slow", "name": "x", "version": 1,
        "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "slow", "type": "tool", "tool": "p__slow",
             "timeout_seconds": 0.01},
        ],
    }, source="slow"), "test")

    async def scenario():
        run = await engine.run_now("t-slow", {"type": "manual"})
        assert run.status == "completed"          # 没被杀
        assert run.nodes["slow"].over_budget is True
        assert any(e["event"] == "over-budget" for e in run.log)

    asyncio.run(scenario())


def test_idempotency_key_dedupes():
    engine = make_engine(tmp_path_runs())
    engine.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")
    r1 = engine.start_run("t-basic", {"type": "manual"}, idempotency_key="k-1")
    r2 = engine.start_run("t-basic", {"type": "manual"}, idempotency_key="k-1")
    r3 = engine.start_run("t-basic", {"type": "manual"}, idempotency_key="k-2")
    assert r1.run_id == r2.run_id
    assert r3.run_id != r1.run_id


def test_cron_tick_and_minute_dedupe():
    tools = FakeTools()
    engine = make_engine(tmp_path_runs(), tools)
    engine.register_flow(parse_flow({
        "id": "t-cron", "name": "x", "version": 1,
        "triggers": [{"type": "cron", "cron": "* * * * *"}],
        "nodes": [{"id": "a", "type": "tool", "tool": "p__ping"}],
    }, source="cron"), "test")
    base = (time.time() // 60) * 60 + 5

    async def scenario():
        first = engine.tick(now=base)
        again = engine.tick(now=base + 20)     # 同一分钟：不重复触发
        later = engine.tick(now=base + 60)     # 下一分钟：新实例
        assert len(first) == 1 and len(again) == 0 and len(later) == 1
        await asyncio.sleep(0.05)
        run = engine.store.find_run(first[0].run_id)
        assert run.status == "completed"
        assert run.trigger["type"] == "cron" and run.trigger["cron"] == "* * * * *"

    asyncio.run(scenario())


def test_event_trigger_and_idempotent_payload():
    engine = make_engine(tmp_path_runs())
    engine.register_flow(parse_flow({
        "id": "t-evt", "name": "x", "version": 1,
        "triggers": [{"type": "event", "event": "order.created"}],
        "nodes": [{"id": "a", "type": "tool", "tool": "p__ping"}],
    }, source="evt"), "test")

    async def scenario():
        started = engine.emit("order.created", {"order": 1})
        assert len(started) == 1
        again = engine.emit("order.created", {"order": 1})   # 同载荷幂等
        assert len(again) == 1 and again[0].run_id == started[0].run_id
        other = engine.emit("order.created", {"order": 2})
        assert other[0].run_id != started[0].run_id
        await asyncio.sleep(0.05)

    asyncio.run(scenario())


def test_replay_links_original():
    engine = make_engine(tmp_path_runs())
    engine.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")

    async def scenario():
        run = await engine.run_now("t-basic", {"type": "manual"}, inputs={"x": 1})
        replay = await engine.replay(run.run_id)
        assert replay.replay_of == run.run_id
        assert replay.inputs == {"x": 1}
        assert replay.status == "waiting-approval"

    asyncio.run(scenario())


def test_persistence_and_breakpoint_resume(tmp_path):
    """断点续跑：status=running + cursor 的实例（进程重启后）能恢复执行"""
    tools = FakeTools()
    engine = make_engine(tmp_path, tools)
    engine.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")

    async def scenario():
        # 手工造一个"跑到一半断掉"的实例：fetch 完成，cursor 停在 check
        run = engine.start_run("t-basic", {"type": "manual"})
        run.outputs["fetch"] = {"items": [{"name": "甲"}], "count": 1}
        run.nodes["fetch"].status = "ok"
        run.cursor = "check"
        engine.store.save(run)
        # 模拟进程重启：新引擎实例（同 runs_dir）+ resume
        engine2 = FlowEngine(engine.store.runs_dir, tools)
        engine2.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")
        assert engine2.resume_incomplete() == 1
        await asyncio.sleep(0.1)
        resumed = engine2.store.find_run(run.run_id)
        assert resumed.status == "waiting-approval"   # 恢复后走到审批挂起
        assert resumed.nodes["send"].attempts == 1     # loop 项已补跑

    asyncio.run(scenario())


def test_approval_timeout_fail_and_skip():
    engine = make_engine(tmp_path_runs())
    engine.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")

    async def scenario():
        run = await engine.run_now("t-basic", {"type": "manual"})
        assert run.status == "waiting-approval"
        # 时间快进到超时后
        handled = engine.check_timeouts(now=run.approval.created_at + 73 * 3600)
        assert handled == 1
        expired = engine.store.find_run(run.run_id)
        assert expired.status == "failed" and "超时" in expired.error

    asyncio.run(scenario())

    engine2 = make_engine(tmp_path_runs())
    engine2.register_flow(parse_flow({
        "id": "t-toskip", "name": "x", "version": 1,
        "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "confirm", "type": "approval", "timeout_hours": 1,
             "on_timeout": "skip"},
            {"id": "after", "type": "action",
             "actions": [{"type": "notify", "channel": "in-app", "message": "跳过继续"}]},
        ],
    }, source="toskip"), "test")

    async def scenario_skip():
        run = await engine2.run_now("t-toskip", {"type": "manual"})
        assert run.status == "waiting-approval"
        engine2.check_timeouts(now=run.approval.created_at + 2 * 3600)
        await asyncio.sleep(0.1)
        done = engine2.store.find_run(run.run_id)
        assert done.status == "completed"          # skip 后继续跑完
        assert done.notifications[0]["message"] == "跳过继续"

    asyncio.run(scenario_skip())


def test_approval_token_unblocks_gated_tool():
    """approved token：过人工审批闸后，approval 类工具放行（生成文档类须确认）"""
    engine = make_engine(tmp_path_runs())

    async def gated_caller(name, args, approved=False):
        if not approved:
            from plugin.mcp_bridge import ApprovalRequired
            raise ApprovalRequired(name, "工具声明 approval=always")
        return {"report": "ok"}

    engine.set_tool_caller(gated_caller)
    engine.register_flow(parse_flow({
        "id": "t-gate", "name": "x", "version": 1,
        "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "confirm", "type": "approval", "timeout_hours": 1},
            {"id": "report", "type": "tool", "tool": "p__gated"},
        ],
    }, source="gate"), "test")

    async def scenario():
        run = await engine.run_now("t-gate", {"type": "manual"})
        run = await engine.resolve_approval(run.run_id, "approve")
        assert run.status == "completed"
        assert run.outputs["report"] == {"report": "ok"}

    asyncio.run(scenario())


def test_reject_runs_trim():
    engine = make_engine(tmp_path_runs())
    defn = parse_flow({
        "id": "t-trim", "name": "x", "version": 1,
        "triggers": [{"type": "manual"}],
        "nodes": [{"id": "a", "type": "tool", "tool": "p__ping"}],
        "observability": {"retain_runs": 3},
    }, source="trim")
    engine.register_flow(defn, "test")
    for i in range(6):
        engine.start_run("t-trim", {"type": "manual"}, inputs={"i": i})
        asyncio.run(engine.execute(engine.store.list_runs(limit=1)[0].run_id))
    assert len(engine.store.list_runs(limit=50)) == 3


def test_run_summary_shape():
    engine = make_engine(tmp_path_runs())
    engine.register_flow(parse_flow(BASIC_FLOW, source="basic"), "test")

    async def scenario():
        run = await engine.run_now("t-basic", {"type": "manual"})
        summary = engine.run_summary(run)
        assert summary["status"] == "waiting-approval"
        assert summary["trigger"] == "manual"
        assert {n["node"] for n in summary["nodes"]} >= {"fetch", "confirm"}
        fetch = next(n for n in summary["nodes"] if n["node"] == "fetch")
        assert fetch["status"] == "ok" and fetch["attempts"] == 1

    asyncio.run(scenario())


# ─────────────────────────── 辅助 ───────────────────────────

_RUNS_SEQ = {"n": 0}


def tmp_path_runs() -> Path:
    """每次独立 runs 目录（不依赖 fixture，方便内嵌 async 场景）"""
    import tempfile

    _RUNS_SEQ["n"] += 1
    return Path(tempfile.mkdtemp(prefix=f"flow-runs-{_RUNS_SEQ['n']}-"))
