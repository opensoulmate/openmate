"""
MCP 治理网关测试 — Profile/Binding/Policy + require_approval 转人工 + rate_limit + 审计

对照 paperclip-control-plane-study.md F1-F2 映射表实证：
- F1 Profile+Binding+Policy 三件套（allow/deny 包绑 scope，deny 优先；
  调用时 allow/block/require_approval/rate_limit）
- F1 require_approval 转 human_node：未批 → tool-gate 人工放行 → 一次性豁免 → 原地续跑
- F2 风险分级 + CallEvent 审计（谁/节点/参数脱敏/判定/结果/耗时，模型无法伪造）
- 未配置零行为变化（无策略一律 allow）
"""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from plugin.flow_def import parse_flow
from plugin.flow_engine import FlowEngine, reset_engine
from plugin.flow_collab import reset_collab
from plugin.tool_governance import (
    GovernanceProfile,
    ToolGovernance,
    ToolPolicy,
    get_governance,
    reset_governance,
)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_RUNS_DIR", str(tmp_path / "flow-runs"))
    monkeypatch.setenv("COLLAB_TASKS_DIR", str(tmp_path / "collab"))
    monkeypatch.setenv("COLLAB_COST_DB", str(tmp_path / "costs.db"))
    monkeypatch.setenv("ORG_CHART_FILE", str(tmp_path / "org.json"))
    monkeypatch.setenv("FLOW_RECEIPTS_FILE", str(tmp_path / "receipts.json"))
    monkeypatch.setenv("TOOL_CALL_AUDIT_FILE", str(tmp_path / "audit.json"))
    reset_engine()
    reset_collab()
    reset_governance()
    engine = FlowEngine(tmp_path / "flow-runs")
    calls = []

    async def _tc(name, args, approved=False):
        calls.append((name, args, approved))
        return {"echo": args}

    engine.set_tool_caller(_tc)
    runtime = engine.collab()
    governance = get_governance()
    yield engine, runtime, governance, calls
    reset_engine()
    reset_collab()
    reset_governance()


def _run_flow(engine, fid, nodes):
    engine.register_flow(parse_flow({
        "id": fid, "name": fid, "triggers": [{"type": "manual"}],
        "nodes": nodes,
    }), plugin_id="t")
    return asyncio.run(engine.run_now(fid, {"type": "manual"}))


# ── F1 判定序（deny 优先）与作用域 ────────────────────────────────

def test_policy_precedence_deny_first():
    gov = ToolGovernance()
    gov.set_risk("p__rm*", "destructive")          # 前缀模式分级
    gov.add_profile(GovernanceProfile(name="safe", deny=("p__rm*",)))
    gov.bind("company", "safe")
    gov.add_policy(ToolPolicy("allow-all", "allow", tool="*"))
    # profile deny 优先于 policy allow
    assert gov.evaluate("p__rm_file").action == "block"
    assert "profile-deny" in gov.evaluate("p__rm_file").reason
    # 风险分级可见（F2）
    assert gov.evaluate("p__rm_file").risk == "destructive"
    assert gov.evaluate("p__other").action == "allow"
    # policy block 优先于 require_approval
    gov.add_policy(ToolPolicy("ra", "require_approval", tool="p__x"))
    gov.add_policy(ToolPolicy("blk", "block", tool="p__x"))
    d = gov.evaluate("p__x")
    assert d.action == "block" and d.policy_id == "blk"
    # require_approval：未批拦截，approved token 放行
    gov2 = ToolGovernance()
    gov2.add_policy(ToolPolicy("ra2", "require_approval", tool="p__y"))
    assert gov2.evaluate("p__y").action == "require_approval"
    assert gov2.evaluate("p__y", approved=True).action == "allow"


def test_policy_scope_binding():
    gov = ToolGovernance()
    gov.add_policy(ToolPolicy("flow-blk", "block", tool="p__t", scope="flow:flow-a"))
    assert gov.evaluate("p__t", flow_id="flow-a").action == "block"
    assert gov.evaluate("p__t", flow_id="flow-b").action == "allow"
    gov.add_policy(ToolPolicy("node-ra", "require_approval", tool="p__t",
                              scope="node:n1"))
    assert gov.evaluate("p__t", node_id="n1").action == "require_approval"
    assert gov.evaluate("p__t", node_id="n2").action == "allow"


def test_rate_limit_window_counting():
    gov = ToolGovernance(clock=lambda: 3600 * 100)
    gov.add_policy(ToolPolicy("rl", "rate_limit", tool="p__t", limit_per_hour=2))
    assert gov.evaluate("p__t").action == "allow"
    assert gov.evaluate("p__t").action == "allow"
    d = gov.evaluate("p__t")
    assert d.action == "block" and "rate_limit" in d.reason
    # 下一小时窗口重置
    gov._clock = lambda: 3600 * 101
    assert gov.evaluate("p__t").action == "allow"


# ── F1 引擎整合：block 走 on_error / require_approval 转人工续跑 ──

def test_policy_block_routes_on_error_and_audits(env):
    engine, runtime, governance, calls = env
    governance.add_policy(ToolPolicy("blk", "block", tool="p__t"))
    run = _run_flow(engine, "blk-flow", [
        {"id": "go", "type": "tool", "tool": "p__t", "args": {"x": 1},
         "on_error": "skip"},
        {"id": "after", "type": "action",
         "actions": [{"type": "notify", "message": "继续"}]},
    ])
    assert run.status == "completed"
    assert run.outputs["go"]["skipped"] is True
    assert calls == []                                  # 真拦截：工具没跑
    events = governance.read_audit()
    assert events and events[-1]["action"] == "block" and events[-1]["ok"] is False


def test_require_approval_transfers_to_human_gate_and_resumes(env):
    """F1 真闸：require_approval → human 放行 → 一次性豁免 → 原地续跑真执行"""
    engine, runtime, governance, calls = env
    governance.add_policy(ToolPolicy("ra", "require_approval", tool="p__t"))
    run = _run_flow(engine, "ra-flow", [
        {"id": "go", "type": "tool", "tool": "p__t", "args": {"x": 42},
         "timeout_hours": 1},
        {"id": "after", "type": "action",
         "actions": [{"type": "notify", "message": "继续"}]},
    ])
    # 被拦：挂 tool-gate（human 工单），工具没跑，不出网
    assert run.status == "waiting-approval" and run.approval.kind == "tool-gate"
    assert run.cursor == "go" and calls == []
    gate = runtime.store.load(run.approval.task_id)
    assert gate.kind == "human" and gate.payload.get("gate") == "tool-policy"
    # 人工放行（经收发台交付=批准）→ waiver → 重入 go → 真执行
    asyncio.run(runtime.deliver_and_resume(gate.task_id, "同意放行",
                                           delivered_by="boss"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert calls and calls[0][0] == "p__t" and calls[0][1] == {"x": 42}
    # 放行标记已授予（approved token 先行放行，waiver 为一次性兜底可消耗）
    assert governance.take_waiver(f"{run.run_id}:go") is True
    assert governance.take_waiver(f"{run.run_id}:go") is False   # 一次性，不复用
    # 审计双事件：拦截 + 放行后真调用
    events = governance.read_audit()
    actions = [(e["action"], e["ok"]) for e in events if e["tool"] == "p__t"]
    assert ("require_approval", False) in actions and ("allow", True) in actions


def test_rate_limit_blocks_overflow_call(env):
    engine, runtime, governance, calls = env
    governance.add_policy(ToolPolicy("rl", "rate_limit", tool="p__t",
                                     limit_per_hour=1))
    run = _run_flow(engine, "rl-flow", [
        {"id": "one", "type": "tool", "tool": "p__t", "args": {"n": 1}},
        {"id": "two", "type": "tool", "tool": "p__t", "args": {"n": 2},
         "on_error": "skip"},
    ])
    assert run.status == "completed"
    assert len(calls) == 1                               # 第二次被限流拦下
    assert run.outputs["two"]["skipped"] is True
    assert "rate_limit" in run.outputs["two"]["error"]


# ── F2 审计台账（脱敏 + 上下文 + 耗时）────────────────────────────

def test_audit_ledger_masks_secrets_and_records_context(env):
    engine, runtime, governance, calls = env
    run = _run_flow(engine, "audit-flow", [
        {"id": "go", "type": "tool", "tool": "p__t",
         "args": {"password": "hunter2", "user": "alice"}},
    ])
    assert run.status == "completed"
    events = governance.read_audit()
    event = next(e for e in events if e["tool"] == "p__t")
    assert event["args"]["password"] == "***"            # 敏感参数脱敏（铁律）
    assert event["args"]["user"] == "alice"
    assert event["flow_id"] == "audit-flow" and event["node_id"] == "go"
    assert event["run_id"] == run.run_id and event["ok"] is True
    assert event["duration_ms"] >= 0
    # 审计落盘真文件（滚动台账）
    on_disk = json.loads(open(os.environ["TOOL_CALL_AUDIT_FILE"],
                              encoding="utf-8").read())
    assert any(e.get("tool") == "p__t" for e in on_disk)


def test_no_policy_means_allow_zero_change(env):
    """未配置零行为变化：无策略一律 allow，调用照旧"""
    engine, runtime, governance, calls = env
    run = _run_flow(engine, "free-flow", [
        {"id": "go", "type": "tool", "tool": "p__t", "args": {"x": 1}},
    ])
    assert run.status == "completed" and len(calls) == 1
    assert governance.read_audit()[-1]["action"] == "allow"


def test_governance_file_loading(tmp_path):
    """数据面装载：TOOL_GOVERNANCE_FILE JSON（catalog/profiles/bindings/policies）"""
    config = {
        "catalog": {"p__rm*": "destructive"},
        "profiles": [{"name": "safe", "deny": ["p__rm*"]}],
        "bindings": [{"scope": "company", "profile": "safe"}],
        "policies": [{"id": "ra", "action": "require_approval", "tool": "p__danger"}],
    }
    path = tmp_path / "gov.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    gov = ToolGovernance()
    counts = gov.load_file(path)
    assert counts == {"catalog": 1, "profiles": 1, "bindings": 1, "policies": 1}
    assert gov.risk_of("p__rm_file") == "destructive"
    assert gov.evaluate("p__rm_file").action == "block"
    assert gov.evaluate("p__danger").action == "require_approval"
