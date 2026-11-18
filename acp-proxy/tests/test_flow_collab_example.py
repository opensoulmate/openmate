"""
协作示例流程真实验证 — examples/collab-demo/flows/team-handoff.yaml 全链：

D2 角色路由=数据实证：流程 YAML 只写 role（engineer/manager）不写人；org 表
（rbac/org.py）定接单人，agent_node→SoulMate 干活回流→human_node 验收交付→完成。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.flow_collab import reset_collab
from plugin.flow_engine import FlowEngine, reset_engine

EXAMPLE_FLOW = (Path(__file__).resolve().parent.parent.parent
                / "examples" / "collab-demo" / "flows" / "team-handoff.yaml")


class FakeChannel:
    def __init__(self):
        self.dispatches = []

    async def dispatch(self, **kw):
        self.dispatches.append(kw)
        return {"a2a_task_id": "a2a-demo", "status": "pending"}

    async def fetch(self, a2a_task_id):
        return {"state": "completed", "result": "结论：3+7+11=21，分布均匀"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_RUNS_DIR", str(tmp_path / "flow-runs"))
    monkeypatch.setenv("COLLAB_TASKS_DIR", str(tmp_path / "collab"))
    monkeypatch.setenv("COLLAB_COST_DB", str(tmp_path / "costs.db"))
    monkeypatch.setenv("ORG_CHART_FILE", str(tmp_path / "org.json"))
    reset_engine()
    reset_collab()
    from rbac.org import reset_org_chart
    reset_org_chart()
    engine = FlowEngine(tmp_path / "flow-runs")

    async def _tc(name, args, approved=False):
        if name == "collab-demo__collect_summary":
            numbers = [float(x) for x in (args.get("numbers") or [])]
            return {"count": len(numbers), "total": sum(numbers),
                    "average": sum(numbers) / len(numbers)}
        return {"echo": args}

    engine.set_tool_caller(_tc)
    runtime = engine.collab()
    runtime.channel = FakeChannel()
    yield engine, runtime
    reset_engine()
    reset_collab()
    reset_org_chart()


def test_example_flow_role_routing_full_chain(env):
    """示例流程全链：role 路由派单 → SoulMate 回流 → manager 验收交付 → 完成"""
    from rbac.org import get_org_chart, OrgMember
    engine, runtime = env
    # 组织架构表（数据驱动路由）：engineer=alice/bob，manager=carol（bob 的上级）
    chart = get_org_chart()
    chart.upsert(OrgMember(user_id="alice", role="engineer", team="eng", reports_to="carol"))
    chart.upsert(OrgMember(user_id="bob", role="engineer", team="eng", reports_to="carol"))
    chart.upsert(OrgMember(user_id="carol", role="manager", team="eng"))

    engine.register_flow_file(EXAMPLE_FLOW, plugin_id="collab-demo")
    run = asyncio.run(engine.run_now("team-handoff", {"type": "manual"}))
    # 取数节点真执行（确定性合计 21）
    assert run.outputs["fetch"]["total"] == 21
    # agent 节点按 role=engineer 路由（最少在办=alice/bob 平局取字典序 alice）
    assert run.status == "waiting-approval" and run.approval.kind == "agent"
    assert run.approval.assignee == "alice" and run.approval.role == "engineer"
    # SoulMate 回流 → 续跑 → human 节点按 role=manager 路由到 carol
    assert asyncio.run(runtime.poll_agent_tasks()) == 1
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval" and run.approval.kind == "human"
    assert run.approval.assignee == "carol" and run.approval.role == "manager"
    # manager 验收交付 → 流程完成
    asyncio.run(runtime.deliver_and_resume(run.approval.task_id, "验收通过",
                                           delivered_by="carol"))
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert "21" in run.outputs["analyze"]["result"]
    assert run.outputs["review"]["result"] == "验收通过"


def test_example_flow_backup_routing_when_engineers_saturated(env):
    """D1 递归汇报链 backup：engineer 全满载 → manager（上级）兜底接单"""
    from rbac.org import get_org_chart, OrgMember
    engine, runtime = env
    chart = get_org_chart()
    chart.upsert(OrgMember(user_id="alice", role="engineer", reports_to="carol"))
    chart.upsert(OrgMember(user_id="carol", role="manager"))
    engine.register_flow_file(EXAMPLE_FLOW, plugin_id="collab-demo")
    runtime.router.member_max_open = 0   # engineer 全员满载
    run = asyncio.run(engine.run_now("team-handoff", {"type": "manual"}))
    assert run.approval.kind == "agent"
    assert run.approval.assignee == "carol"    # 汇报链 backup（主管兜底）
