"""
任务收发台 HTTP 面测试 — /collab/*（在办/交付/超时三桶 + 认领/心跳/交付/审批/催办）

真实链路：TestClient → collab_api → CollabRuntime（工单账）→ FlowEngine 恢复续跑。
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.collab_api import build_collab_router
from plugin.flow_collab import reset_collab
from plugin.flow_def import parse_flow
from plugin.flow_engine import FlowEngine, reset_engine


class FakeChannel:
    async def dispatch(self, **kw):
        return {"a2a_task_id": "a2a-x", "status": "pending"}

    async def fetch(self, a2a_task_id):
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

    async def _tc(name, args, approved=False):
        return {"echo": args}

    engine.set_tool_caller(_tc)
    runtime = engine.collab()
    runtime.channel = FakeChannel()
    app = FastAPI()
    app.include_router(build_collab_router(runtime))
    client = TestClient(app)
    yield client, engine, runtime
    reset_engine()
    reset_collab()


def test_inbox_buckets_open_delivered_overdue(env):
    """收发台三桶：在办 open / 交付 delivered / 超时 overdue"""
    client, engine, runtime = env
    t1 = runtime.create(kind="human", run_id="r1", flow_id="f", node_id="n1",
                        assignee="alice", instruction="在办任务")
    t2 = runtime.create(kind="human", run_id="r2", flow_id="f", node_id="n2",
                        assignee="alice", instruction="已交任务")
    runtime.store.deliver(t2.task_id, "交了", delivered_by="alice")
    t3 = runtime.create(kind="human", run_id="r3", flow_id="f", node_id="n3",
                        assignee="alice", instruction="超时任务")
    runtime.store._mutate(t3.task_id, lambda t: setattr(t, "due_at", 1.0))  # 造超时

    open_ids = {t["task_id"] for t in client.get(
        "/collab/tasks", params={"assignee": "alice", "bucket": "open"}).json()["tasks"]}
    delivered_ids = {t["task_id"] for t in client.get(
        "/collab/tasks", params={"assignee": "alice", "bucket": "delivered"}).json()["tasks"]}
    overdue_ids = {t["task_id"] for t in client.get(
        "/collab/tasks", params={"assignee": "alice", "bucket": "overdue"}).json()["tasks"]}
    assert open_ids == {t1.task_id}
    assert delivered_ids == {t2.task_id}
    assert overdue_ids == {t3.task_id}
    stats = client.get("/collab/stats").json()
    assert stats["buckets"] == {"open": 1, "delivered": 1, "overdue": 1, "closed": 0}
    # 负载计数含超时未终态单（角色路由按真实占用算，不看桶）
    assert stats["open_by_assignee"] == {"alice": 2}


def test_claim_heartbeat_deliver_resumes_flow(env):
    """真收真交全链：HTTP 认领→心跳→交付回流→流程续跑完成"""
    client, engine, runtime = env
    engine.register_flow(parse_flow({
        "id": "hf", "name": "hf", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "review", "type": "human", "user": "bob", "task": "交结论",
             "timeout_hours": 1},
            {"id": "use", "type": "tool", "tool": "p__t",
             "args": {"v": "$.review.result"}},
        ],
    }), plugin_id="t")
    run = asyncio.run(engine.run_now("hf", {"type": "manual"}))
    tid = run.approval.task_id
    # 认领
    resp = client.post(f"/collab/tasks/{tid}/claim",
                       json={"claimed_by": "bob", "lease_seconds": 300})
    assert resp.status_code == 200
    token = resp.json()["claim_token"]
    # 心跳续租
    resp = client.post(f"/collab/tasks/{tid}/heartbeat", json={"claim_token": token})
    assert resp.status_code == 200
    # 错误令牌拒交（CAS 回执）
    resp = client.post(f"/collab/tasks/{tid}/deliver",
                       json={"result": "x", "claim_token": "bad"})
    assert resp.status_code == 409
    # 真交付 → 流程续跑
    resp = client.post(f"/collab/tasks/{tid}/deliver",
                       json={"result": "结论OK", "delivered_by": "bob",
                             "claim_token": token})
    assert resp.status_code == 200
    assert resp.json()["task"]["bucket"] == "delivered"
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed" and run.outputs["review"]["result"] == "结论OK"


def test_decide_approve_and_reject(env):
    """审批整合：approval 工单经收发台 approve 续跑 / reject 终止"""
    client, engine, runtime = env
    for decision, expect in (("approve", "completed"), ("reject", "failed")):
        engine.register_flow(parse_flow({
            "id": f"af-{decision}", "name": "af", "triggers": [{"type": "manual"}],
            "nodes": [
                {"id": "confirm", "type": "approval", "timeout_hours": 1},
                {"id": "after", "type": "action",
                 "actions": [{"type": "notify", "message": "go"}]},
            ],
        }), plugin_id="t")
        run = asyncio.run(engine.run_now(f"af-{decision}", {"type": "manual"}))
        tid = run.approval.task_id
        resp = client.post(f"/collab/tasks/{tid}/decide",
                           json={"decision": decision, "decided_by": "boss",
                                 "note": "行"})
        assert resp.status_code == 200
        assert engine.store.find_run(run.run_id).status == expect
        body = client.get(f"/collab/tasks/{tid}").json()
        assert body["bucket"] in ("delivered", "closed")
    # 非法决定显式拒绝
    t = runtime.create(kind="approval", run_id="rr", flow_id="f", node_id="n",
                       assignee="x", instruction="审")
    resp = client.post(f"/collab/tasks/{t.task_id}/decide", json={"decision": "maybe"})
    assert resp.status_code == 400


def test_remind_cancel_and_404(env):
    client, engine, runtime = env
    t = runtime.create(kind="human", run_id="r", flow_id="f", node_id="n",
                       assignee="carol", instruction="催我")
    resp = client.post(f"/collab/tasks/{t.task_id}/remind", json={"note": "快交"})
    assert resp.status_code == 200 and resp.json()["reminders"] == 1
    resp = client.post(f"/collab/tasks/{t.task_id}/cancel", json={"reason": "不需要了"})
    assert resp.status_code == 200 and resp.json()["task"]["status"] == "cancelled"
    # 终态不可再领（显式报因）
    resp = client.post(f"/collab/tasks/{t.task_id}/claim", json={"claimed_by": "x"})
    assert resp.status_code == 409
    assert client.get("/collab/tasks/TO-none").status_code == 404


def test_case_handoff_versioned_reassignment(env):
    """D5 case handoff：版本校验交办（转派/升级），被取代即作废，交接即清场"""
    client, engine, runtime = env
    t = runtime.create(kind="human", run_id="r", flow_id="f", node_id="n",
                       assignee="alice", instruction="办件")
    _, token = runtime.store.claim(t.task_id, claimed_by="alice")
    # 带版本交办：alice → 主管 bob（升级），锁清场
    resp = client.post(f"/collab/tasks/{t.task_id}/handoff",
                       json={"to_assignee": "bob", "decided_by": "alice",
                             "note": "升级主管", "expect_version": 0})
    assert resp.status_code == 200 and resp.json()["handoff_version"] == 1
    body = resp.json()["task"]
    assert body["assignee"] == "bob" and body["claimed_by"] == ""
    assert body["status"] == "todo"
    # 前任令牌作废（交接即清场）
    resp = client.post(f"/collab/tasks/{t.task_id}/deliver",
                       json={"result": "x", "claim_token": token})
    assert resp.status_code == 409
    # 旧版本交办被取代即作废（case_handoff_superseded）
    resp = client.post(f"/collab/tasks/{t.task_id}/handoff",
                       json={"to_assignee": "carol", "expect_version": 0})
    assert resp.status_code == 409 and "case_handoff_superseded" in resp.json()["detail"]
    # 新版本交办成功（v1→v2），交办历史留痕
    resp = client.post(f"/collab/tasks/{t.task_id}/handoff",
                       json={"to_assignee": "carol", "expect_version": 1})
    assert resp.status_code == 200 and resp.json()["handoff_version"] == 2
    detail = client.get(f"/collab/tasks/{t.task_id}").json()
    assert detail["assignee"] == "carol" and detail["handoff_version"] == 2
    reloaded = runtime.store.load(t.task_id)
    assert [h["to"] for h in reloaded.handoffs] == ["bob", "carol"]
    # 终态不可交办
    runtime.store.close(t.task_id, "cancelled", note="done")
    resp = client.post(f"/collab/tasks/{t.task_id}/handoff",
                       json={"to_assignee": "dave"})
    assert resp.status_code == 409 and "closed" in resp.json()["detail"]


def test_agent_task_visible_in_inbox_with_claim_lock(env):
    """agent 工单进收件台：派发后带领取锁与 a2a_task_id（A1 领取可见）"""
    client, engine, runtime = env
    engine.register_flow(parse_flow({
        "id": "gf", "name": "gf", "triggers": [{"type": "manual"}],
        "nodes": [{"id": "ask", "type": "agent", "user": "alice",
                   "target_agent": "soulmate-a", "task": "干活",
                   "timeout_hours": 1}],
    }), plugin_id="t")
    run = asyncio.run(engine.run_now("gf", {"type": "manual"}))
    body = client.get(f"/collab/tasks/{run.approval.task_id}").json()
    assert body["kind"] == "agent" and body["assignee"] == "alice"
    assert body["target_agent"] == "soulmate-a" and body["a2a_task_id"] == "a2a-x"
    assert body["claimed_by"].startswith("soulmate:")
    assert body["bucket"] == "open"
