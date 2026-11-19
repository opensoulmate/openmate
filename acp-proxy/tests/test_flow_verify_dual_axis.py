"""
双轴完成状态 + settle 拒伪完成 + 验收翻档 全链真流程实证

真实性口径：流程引擎/工单账/协作运行时全真件执行（FlowEngine+CollabRuntime+
WorkOrderStore 落真 JSON），工具/探针为可编排桩（验收证据面）。

覆盖（监督闭环三件事）：
① 全链：自报完成（Code·reported done 黄）→ 未验证（done·unverified）→
   内容级探针验收 → Test·verified（绿）——探针调用瞬间抓中间态实证
② 伪完成被拒并留痕：执行者自报 verified 不采信（claim-capped）；
   自审翻档拒收（self_referential_evidence）；无证据翻档拒收（no_evidence）
③ settle 拒伪完成：缺交付物/验收标准未满足 → 拒 ok → 修复 ≤2 轮 → 重规划
④ done·unverified 清单可查（节点/工单/实例三层）+ 工单双轴
"""
from __future__ import annotations

import asyncio

import pytest

from plugin.flow_collab import (
    CollabError,
    CollabRuntime,
    WorkOrderStore,
    reset_collab,
)
from plugin.flow_def import FlowDefError, parse_flow
from plugin.flow_engine import FlowEngine, FlowRunError, reset_engine
from plugin.verify_axis import (
    AXIS_REPORTED,
    AXIS_VERIFIED,
    can_verify,
    run_verdict,
    settle_decision,
)


class FakeChannel:
    """A2A 通道测试桩（形状=dispatch/fetch）：脚本化结果序列。"""

    def __init__(self, results=None, boom=False):
        self.dispatches = []
        self.fetches = []
        self._results = list(results or [])
        self.boom = boom

    async def dispatch(self, **kw):
        self.dispatches.append(kw)
        if self.boom:
            raise RuntimeError("A2A 不可达")
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
    monkeypatch.setenv("FLOW_RECEIPTS_FILE", str(tmp_path / "receipts.json"))
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


# ─────────────────── ① 全链：自报完成→未验证→验收→verified ───────────────────

def test_full_chain_self_report_unverified_acceptance_verified(env):
    """全链实证：agent 自报完成（黄）→ 探针抓到未验证中间态 → 验收翻 verified（绿）"""
    engine, runtime, calls = env
    mid_chain = {}   # 探针调用瞬间的中间态（链条的活证据）

    async def _tool_caller(name, args, approved=False):
        calls.append((name, dict(args), approved))
        if name == "p__probe":
            # 内容级探针运行瞬间：目标必须还是「自报完成·未验证」
            run = engine.store.find_run(args["run_id"])
            trec = run.nodes["work"]
            mid_chain["target_confidence_at_probe"] = trec.confidence
            mid_chain["target_verified_at_probe"] = trec.verified
            mid_chain["verdict_at_probe"] = run.verdict()["verdict"]
            mid_chain["probe_saw_output"] = args.get("output")
            mid_chain["in_unverified_listing_at_probe"] = [
                n["node"] for n in engine.unverified_done()["nodes"]
                if n["run_id"] == args["run_id"]]
            return {"pass": True, "content": "报告内容抽查通过"}
        return {"echo": args}

    engine.set_tool_caller(_tool_caller)
    runtime.channel = FakeChannel()
    _register(engine, {
        "id": "verify-chain", "name": "验收链", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "写报告", "timeout_hours": 1},
            {"id": "check", "type": "verify", "for": "work",
             "probe": "p__probe", "when": "$.check.pass == true"},
            {"id": "end", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ],
    })
    run = asyncio.run(engine.run_now("verify-chain", {"type": "manual"}))
    assert run.status == "waiting-approval" and run.approval.kind == "agent"
    task = runtime.store.load(run.approval.task_id)

    # 自报完成：交付文本还自称 VERIFIED（伪自报词）——照封顶不误
    asyncio.run(runtime.deliver_and_resume(
        task.task_id, "写完了，VERIFIED 全部通过", delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)

    # 全链证据：探针瞬间=自报完成·未验证（进 done·unverified 清单）；终态=verified
    assert mid_chain["target_confidence_at_probe"] == AXIS_REPORTED
    assert mid_chain["target_verified_at_probe"] is False
    assert mid_chain["verdict_at_probe"] != "verified"          # 验收前绝不是绿
    assert "work" in mid_chain["in_unverified_listing_at_probe"]  # 未验证清单可查
    assert mid_chain["probe_saw_output"]["result"] == "写完了，VERIFIED 全部通过"
    trec = run.nodes["work"]
    assert run.status == "completed"
    assert trec.verified is True
    assert trec.confidence == AXIS_VERIFIED
    assert trec.claim_source == "evidence"
    assert trec.verified_by == "probe:p__probe"
    assert "探针" in trec.verify_evidence or "probe=" in trec.verify_evidence
    assert run.verdict()["verdict"] == "verified"

    # 留痕链（事件时间线）：自报封顶（claim-capped）→ 探针验收 → 翻档（verified）
    events = [(e["node"], e["event"]) for e in run.log]
    assert ("work", "claim-capped") in events          # 伪自报词被拒采信
    assert ("work", "approval-approved") in events     # 自报入账（reported done）
    assert ("check", "verified") in events
    assert events.index(("work", "approval-approved")) \
        < events.index(("check", "verified"))
    # 工单双轴同源：交付=黄 reported done（工单面翻绿走独立验收，见工单测试）
    task = runtime.store.load(task.task_id)
    assert task.confidence == AXIS_REPORTED and task.verified is False


def test_claim_verified_text_never_flips(env):
    """执行者自报 verified 一律封顶：只有验收证据能翻绿"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    _register(engine, {
        "id": "claim-flow", "name": "自报流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
        ],
    })
    run = asyncio.run(engine.run_now("claim-flow", {"type": "manual"}))
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "已完成已验证 VERIFIED 测试通过",
        delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)
    trec = run.nodes["work"]
    assert trec.status == "ok" and trec.verified is False
    assert trec.confidence == AXIS_REPORTED          # 黄：自报封顶
    assert run.verdict()["verdict"] == "done·unverified"
    assert any(e["event"] == "claim-capped" for e in run.log)


# ─────────────────── ② 伪完成被拒实测（三种拒收都留痕）───────────────────

def test_fake_completion_rejected_self_verification_and_no_evidence(env):
    """伪完成被拒：自审翻档拒收 + 无证据翻档拒收，都留痕且不翻绿"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    _register(engine, {
        "id": "fake-flow", "name": "伪完成流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
        ],
    })
    run = asyncio.run(engine.run_now("fake-flow", {"type": "manual"}))
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "干完了", delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)

    # 拒收①：验收者=执行者（self_referential_evidence）→ 拒 + 留痕 + 不翻绿
    with pytest.raises(FlowRunError, match="self_referential_evidence"):
        asyncio.run(engine.verify_node(
            run.run_id, "work", verified_by="soulmate-a",
            evidence="我自查过了，没问题"))
    # 拒收②：证据为空（no_evidence，fail-closed）→ 拒 + 留痕
    with pytest.raises(FlowRunError, match="no_evidence"):
        asyncio.run(engine.verify_node(
            run.run_id, "work", verified_by="qa", evidence=""))
    run = engine.store.find_run(run.run_id)
    trec = run.nodes["work"]
    assert trec.verified is False and trec.confidence == AXIS_REPORTED
    assert trec.settle_rejects == 2                   # 两次拒收都计数留痕
    refused = [e for e in run.log if e["event"] == "verify-refused"]
    assert len(refused) == 2
    assert any("self_referential_evidence" in e["detail"] for e in refused)
    assert any("no_evidence" in e["detail"] for e in refused)

    # 拒收③：验收不过（ok=False）→ 拒伪完成（不许标 completed）+ 留痕
    out = asyncio.run(engine.verify_node(
        run.run_id, "work", verified_by="qa", evidence="抽查内容不达标", ok=False))
    assert out["verified"] is False and "拒伪完成" in out["detail"]
    run = engine.store.find_run(run.run_id)
    assert run.nodes["work"].verified is False
    assert any(e["event"] == "verify-failed" for e in run.log)

    # 正路：非执行者 + 内容级证据 → 翻绿（对照实证）
    out = asyncio.run(engine.verify_node(
        run.run_id, "work", verified_by="qa", evidence="抽查交付物内容：报告 3 节齐全"))
    assert out["verified"] is True
    run = engine.store.find_run(run.run_id)
    assert run.nodes["work"].verified is True
    assert run.verdict()["verdict"] == "verified"


def test_verify_node_structural_reject_self_verifier(env):
    """verify 节点验收者=执行者：结构性拒绝（修复无用，直接定夺）+ 留痕"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    _register(engine, {
        "id": "self-verify", "name": "自审流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
            {"id": "check", "type": "verify", "for": "work", "probe": "p__probe",
             "verifier": "soulmate-a"},     # 验收者=执行者（伪验收）
        ],
    })
    run = asyncio.run(engine.run_now("self-verify", {"type": "manual"}))
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "干完了", delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "failed"                    # on_error=fail：验收不过不许完成
    trec = run.nodes["work"]
    assert trec.verified is False
    assert trec.settle_rejects == 1
    assert trec.fix_rounds == 0                      # 结构性拒绝不进修复轮
    assert any(e["event"] == "settle-refused"
               and "self_referential_evidence" in e["detail"] for e in run.log)
    assert any(e["event"] == "verify-exhausted" for e in run.log)


def test_verify_fail_fix_rounds_then_replan(env):
    """验证不过：先修复 ≤2 轮（回跳目标重跑），超出→重规划（on_error=goto）"""
    engine, runtime, calls = env
    channel = FakeChannel()
    runtime.channel = channel
    probe_results = [{"pass": False}, {"pass": False}, {"pass": True}]

    async def _tool_caller(name, args, approved=False):
        calls.append((name, dict(args), approved))
        if name == "p__probe":
            return probe_results.pop(0)
        return {"echo": args}

    engine.set_tool_caller(_tool_caller)
    _register(engine, {
        "id": "fix-flow", "name": "修复轮流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "tool", "tool": "p__build"},
            {"id": "check", "type": "verify", "for": "work", "probe": "p__probe",
             "when": "$.check.pass == true", "max_fixes": 2},
            {"id": "end", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ],
    })
    run = asyncio.run(engine.run_now("fix-flow", {"type": "manual"}))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    trec = run.nodes["work"]
    # 修复轮实证：失败 2 次各修 1 轮（work 重跑 3 次），第 3 次探针过 → verified
    assert trec.fix_rounds == 2 and trec.settle_rejects == 2
    assert trec.verified is True
    build_calls = [c for c in calls if c[0] == "p__build"]
    assert len(build_calls) == 3                      # 首跑 + 2 修复轮
    events = [e["event"] for e in run.log]
    assert events.count("verify-fix") == 2
    assert events.count("verify-exhausted") == 0
    assert run.verdict()["verdict"] == "verified"


def test_settle_missing_deliverable_fix_then_replan(env):
    """settle 拒伪完成：缺交付物拒 ok → 修复 2 轮 → 超出转重规划（goto）"""
    engine, runtime, calls = env
    channel = FakeChannel()
    runtime.channel = channel
    _register(engine, {
        "id": "settle-flow", "name": "settle流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "交报告", "form": "报告",
             "timeout_hours": 1, "on_error": "goto:replan"},
            {"id": "replan", "type": "action",
             "actions": [{"type": "notify", "message": "转重规划"}]},
        ],
    })
    run = asyncio.run(engine.run_now("settle-flow", {"type": "manual"}))
    # 三次空交付（报成功没用）：拒 → 修复1 → 拒 → 修复2 → 拒 → 重规划
    for _ in range(3):
        run = engine.store.find_run(run.run_id)
        asyncio.run(runtime.deliver_and_resume(
            run.approval.task_id, "", delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"                  # 重规划节点跑了（goto:replan）
    trec = run.nodes["work"]
    assert trec.settle_rejects == 3 and trec.fix_rounds == 2
    assert trec.verified is False
    assert run.outputs["work"]["settle_refused"] is True
    assert run.outputs["work"]["reason_code"] == "missing_deliverable"
    events = [e["event"] for e in run.log]
    assert events.count("settle-refused") == 3
    assert events.count("settle-fix") == 2
    assert "settle-replan" in events
    # 重规划也进清单：work 是产活且未验证 → done·unverified
    assert run.verdict()["verdict"] == "done·unverified"
    assert any(r["node"] == "work" for r in run.verdict()["reasons"])


def test_settle_acceptance_unmet_refuses_ok(env):
    """settle 拒伪完成：声明验收标准未满足 → 拒 ok（on_error=skip 跳过留痕）"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    _register(engine, {
        "id": "accept-flow", "name": "验收标准流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "交报告",
             "acceptance": "$.work.result == 'ok'",
             "timeout_hours": 1, "on_error": "skip"},
            {"id": "after", "type": "action",
             "actions": [{"type": "notify", "message": "继续"}]},
        ],
    })
    run = asyncio.run(engine.run_now("accept-flow", {"type": "manual"}))
    for _ in range(3):   # 修复 2 轮后 skip
        run = engine.store.find_run(run.run_id)
        asyncio.run(runtime.deliver_and_resume(
            run.approval.task_id, "bad", delivered_by="soulmate-a"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    trec = run.nodes["work"]
    assert trec.settle_rejects == 3 and trec.status == "skipped"
    assert run.outputs["work"]["reason_code"] == "acceptance_misses"
    assert any(e["event"] == "settle-refused"
               and "acceptance_misses" in e["detail"] for e in run.log)


# ─────────────────── ③ done·unverified 清单 + 判档翻绿 ───────────────────

def test_unverified_listing_then_verify_clears(env):
    """done·unverified 清单可查（节点/工单/实例）→ 验收后清单清、判档翻绿"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    _register(engine, {
        "id": "list-flow", "name": "清单流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
            {"id": "done", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ],
    })
    run = asyncio.run(engine.run_now("list-flow", {"type": "manual"}))
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "干完了", delivered_by="soulmate-a"))
    task = runtime.store.load(run.approval.task_id)

    listing = engine.unverified_done()
    assert listing["count"] >= 2
    assert any(n["node"] == "work" and n["confidence"] == AXIS_REPORTED
               for n in listing["nodes"])
    assert any(t["task_id"] == task.task_id for t in listing["tasks"])
    assert any(r["run_id"] == run.run_id and r["verdict"] == "done·unverified"
               for r in listing["runs"])

    # 验收翻绿（工单面 + 流程面各自独立验收）
    runtime.store.verify(task.task_id, verified_by="qa",
                         evidence="抽查交付物：结论可用")
    run = engine.store.find_run(run.run_id)
    asyncio.run(engine.verify_node(
        run.run_id, "work", verified_by="qa", evidence="抽查交付物：结论可用"))
    listing = engine.unverified_done()
    assert not any(n["node"] == "work" and n["run_id"] == run.run_id
                   for n in listing["nodes"])
    assert not any(t["task_id"] == task.task_id for t in listing["tasks"])
    run = engine.store.find_run(run.run_id)
    assert run.verdict()["verdict"] == "verified"


# ─────────────────── ④ 工单双轴 + 伪完成拒收 ───────────────────

def test_workorder_dual_axis_and_fake_completion_rejected(tmp_path):
    """工单双轴：交付=黄 reported done；验收翻绿；自审/无证据/未交付都拒收留痕"""
    store = WorkOrderStore(tmp_path / "collab")
    task = store.create(
        task_id="TO-1-aa", kind="agent", run_id="r1", flow_id="f", node_id="n1",
        assignee="alice", assignee_kind="agent", target_agent="soulmate-a",
        title="干活", instruction="干活", now=1000.0)[0]
    assert task.confidence == "not run"
    store.deliver("TO-1-aa", "干完了", delivered_by="soulmate-a", now=1010.0)
    task = store.load("TO-1-aa")
    assert task.confidence == AXIS_REPORTED and task.status_axis == AXIS_REPORTED
    assert task.verified is False and task.claim_source == "self-report"

    # 伪完成被拒①：自审（验收者=交付者）
    with pytest.raises(CollabError, match="self_referential_evidence"):
        store.verify("TO-1-aa", verified_by="soulmate-a", evidence="自查通过")
    # 伪完成被拒②：无证据（fail-closed）
    with pytest.raises(CollabError, match="no_evidence"):
        store.verify("TO-1-aa", verified_by="qa", evidence="")
    task = store.load("TO-1-aa")
    assert task.verified is False and task.settle_rejects == 2
    assert sum(1 for h in task.history if h["event"] == "verify-refused") == 2

    # 拒伪完成：验收不过不许标完成
    store.verify("TO-1-aa", verified_by="qa", evidence="内容不达标", ok=False)
    task = store.load("TO-1-aa")
    assert task.verified is False and task.settle_rejects == 3
    assert any(h["event"] == "verify-failed" for h in task.history)

    # 正路：非执行者 + 证据 → 绿
    store.verify("TO-1-aa", verified_by="qa", evidence="抽查内容通过")
    task = store.load("TO-1-aa")
    assert task.verified is True and task.confidence == AXIS_VERIFIED
    assert task.verified_by == "qa" and task.verify_evidence == "抽查内容通过"

    # 未交付不可验收
    task2 = store.create(
        task_id="TO-2-bb", kind="agent", run_id="r2", flow_id="f", node_id="n2",
        assignee="bob", assignee_kind="agent", title="t", instruction="i",
        now=1000.0)[0]
    with pytest.raises(CollabError, match="只有已交付工单可验收"):
        store.verify("TO-2-bb", verified_by="qa", evidence="x")


# ─────────────────── review 验收门接 for 目标翻档 ───────────────────

def test_review_gate_for_target_flips_verified(env):
    """E4 review 门带 for：非执行者验收 → 目标翻绿；自审 → 拒翻档留痕"""
    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    for reviewer, expect in (("qa", True), ("soulmate-a", False)):
        fid = f"review-for-{reviewer}"
        _register(engine, {
            "id": fid, "name": fid, "triggers": [{"type": "manual"}],
            "nodes": [
                {"id": "work", "type": "agent", "user": "alice",
                 "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
                {"id": "accept", "type": "human", "user": "qa", "review": True,
                 "for": "work", "task": "验收交付物", "form": "验收意见",
                 "timeout_hours": 1},
            ],
        })
        run = asyncio.run(engine.run_now(fid, {"type": "manual"}))
        asyncio.run(runtime.deliver_and_resume(
            run.approval.task_id, "干完了", delivered_by="soulmate-a"))
        run = engine.store.find_run(run.run_id)
        assert run.nodes["work"].verified is False   # 自报封顶
        asyncio.run(runtime.deliver_and_resume(
            run.approval.task_id, "验收通过，结论可靠", delivered_by=reviewer))
        run = engine.store.find_run(run.run_id)
        assert run.status == "completed"
        assert run.nodes["work"].verified is expect
        if expect:
            assert run.verdict()["verdict"] == "verified"
        else:
            assert run.verdict()["verdict"] == "done·unverified"
            assert any(e["event"] == "verify-refused" for e in run.log)


# ─────────────────── 看板数据源（/collab + /flows HTTP 面双轴）───────────────────

def test_board_api_dual_axis_fields_and_unverified_filter(env):
    """/tasks /will 看板数据源：双轴字段直出 + done·unverified 可查 + 验收接口拒伪完成"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from plugin.collab_api import build_collab_router
    from plugin.flow_api import build_flow_router

    engine, runtime, calls = env
    runtime.channel = FakeChannel()
    app = FastAPI()
    app.include_router(build_collab_router(runtime))
    app.include_router(build_flow_router(engine))
    client = TestClient(app)

    _register(engine, {
        "id": "board-flow", "name": "看板流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "work", "type": "agent", "user": "alice",
             "target_agent": "soulmate-a", "task": "干活", "timeout_hours": 1},
            {"id": "done", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ],
    })
    run = asyncio.run(engine.run_now("board-flow", {"type": "manual"}))
    asyncio.run(runtime.deliver_and_resume(
        run.approval.task_id, "干完了", delivered_by="soulmate-a"))
    task_id = run.approval.task_id

    # /collab 工单双轴直出（黄：reported done）
    view = client.get(f"/collab/tasks/{task_id}").json()
    assert view["verified"] is False
    assert view["confidence"] == AXIS_REPORTED and view["status_axis"] == AXIS_REPORTED
    # done·unverified 可查（/tasks 清单 + 统计计数）
    unv = client.get("/collab/tasks?verification=unverified&bucket=delivered").json()
    assert any(t["task_id"] == task_id for t in unv["tasks"])
    assert client.get("/collab/stats").json()["done_unverified"] == 1
    # 伪完成被拒（自审）→ 409 + 留痕
    r = client.post(f"/collab/tasks/{task_id}/verify",
                    json={"verified_by": "soulmate-a", "evidence": "自查"})
    assert r.status_code == 409 and "self_referential_evidence" in r.json()["detail"]
    # 验收接口翻绿
    r = client.post(f"/collab/tasks/{task_id}/verify",
                    json={"verified_by": "qa", "evidence": "抽查通过"})
    assert r.status_code == 200 and r.json()["verified"] is True
    assert client.get("/collab/stats").json()["done_unverified"] == 0

    # /flows 实例判档 + 节点双轴 + done·unverified 清单 + verify/reassign 接口
    detail = client.get(f"/flows/runs/{run.run_id}").json()
    assert detail["verdict"] == "done·unverified"
    work = next(n for n in detail["nodes"] if n["node"] == "work")
    assert work["confidence"] == AXIS_REPORTED and work["verified"] is False
    listing = client.get("/flows/unverified").json()
    assert any(n["node"] == "work" for n in listing["nodes"])
    r = client.post(f"/flows/runs/{run.run_id}/nodes/work/verify",
                    json={"verified_by": "soulmate-a", "evidence": "自查"})
    assert r.status_code == 409                     # 伪完成被拒（自审）
    r = client.post(f"/flows/runs/{run.run_id}/nodes/work/verify",
                    json={"verified_by": "qa", "evidence": "抽查通过"})
    assert r.status_code == 200 and r.json()["verified"] is True
    detail = client.get(f"/flows/runs/{run.run_id}").json()
    assert detail["verdict"] == "verified"
    assert client.get("/flows/unverified").json()["count"] == 0

def test_run_verdict_fail_closed_semantics():
    class R:
        def __init__(self, node_id, type, status, verified=False,
                     settle_rejects=0, owned_by=""):
            self.node_id, self.type, self.status = node_id, type, status
            self.verified, self.settle_rejects = verified, settle_rejects
            self.owned_by = owned_by

    # 全部 verified → 绿
    v = run_verdict("completed", [R("a", "tool", "ok", verified=True)])
    assert v["verdict"] == "verified"
    # 产活未验证 → 黄 + 归因
    v = run_verdict("completed", [R("a", "agent", "ok")])
    assert v["verdict"] == "done·unverified"
    assert v["reasons"] == [{"node": "a", "reason": "handoff_not_checked"}]
    v = run_verdict("completed", [R("a", "tool", "ok")])
    assert v["reasons"][0]["reason"] == "no_verification_node"
    v = run_verdict("completed", [R("a", "tool", "ok", settle_rejects=1)])
    assert v["reasons"][0]["reason"] == "verification_misses"
    # 无产活 → 黄（fail-closed，宁黄不绿）
    v = run_verdict("completed", [])
    assert v["verdict"] == "done·unverified"
    # 归属合成件不独立判档
    v = run_verdict("completed", [
        R("p", "parallel", "ok", verified=True),
        R("p__b1", "tool", "ok", owned_by="p")])
    assert v["verdict"] == "verified"
    # 非完成实例原词
    assert run_verdict("failed", [])["verdict"] == "failed"
    assert run_verdict("running", [])["verdict"] == "running"


def test_settle_decision_and_can_verify_pure():
    assert settle_decision(result="")["reason_code"] == "missing_deliverable"
    assert settle_decision(result={"v": 1})["accept"] is True
    assert settle_decision(result="x", acceptance_ok=False)["reason_code"] \
        == "acceptance_misses"
    d = settle_decision(result="VERIFIED", claim_verified=True)
    assert d["accept"] is True and d["capped"] is True     # 自报只封顶不翻档
    assert can_verify(verifier="a", executor="a", evidence="e")[0] is False
    assert can_verify(verifier="a", executor="b", evidence="e")[0] is True
    assert can_verify(verifier="", executor="b", evidence="e")[1] \
        == "unknown_verifier"
    assert can_verify(verifier="a", executor="b", evidence="")[1] == "no_evidence"
