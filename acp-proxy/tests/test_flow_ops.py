"""
流程日常使用体验单测 — 一键操作（克隆/编辑参数/暂停/批量）/ 失败可懂可修（错误归一+
从失败节点续跑）/ 结果触达（微信通知链路，投递桩）/ 运行历史（时间筛选+成败统计）

纪律：
- 微信投递一律 set_sender 桩（PYTEST_CURRENT_TEST 下无桩=不投递，绝不真发微信）
- 覆盖件（data/flow-defs）持久化在 tmp_path，不碰真实仓
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.flow_def import FlowDefError, dump_flow, parse_flow
from plugin.flow_engine import FlowEngine, FlowRunError
from plugin.flow_errors import (
    CODE_APPROVAL_REJECTED,
    CODE_EXPR,
    CODE_GOVERNANCE_BLOCKED,
    CODE_NETWORK,
    CODE_NEEDS_APPROVAL,
    CODE_TIMEOUT,
    CODE_TOOL_MISSING,
    normalize_error,
)
from plugin import flow_notify


# ─────────────────── 工具桩 / 夹具 ───────────────────

class FakeTools:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail_times: dict[str, int] = {}
        self.responses: dict[str, list] = {}

    async def __call__(self, name: str, args: dict, approved: bool = False) -> dict:
        self.calls.append((name, dict(args)))
        if self.fail_times.get(name, 0) > 0:
            self.fail_times[name] -= 1
            raise RuntimeError(f"{name} 人为失败")
        queue = self.responses.get(name)
        if queue:
            return queue.pop(0)
        return {"ok": True, "echo": args}


CHAIN_FLOW = {
    "id": "t-chain",
    "name": "链路流",
    "version": 1,
    "triggers": [{"type": "manual"}],
    "nodes": [
        {"id": "one", "type": "tool", "tool": "p__one", "args": {"x": 1}},
        {"id": "two", "type": "tool", "tool": "p__two", "args": {"y": 2},
         "retry": {"max": 0}},
        {"id": "three", "type": "action",
         "actions": [{"type": "notify", "channel": "in-app", "message": "完成"}]},
    ],
    "observability": {"retain_runs": 50},
}


@pytest.fixture()
def engine(tmp_path):
    tools = FakeTools()
    eng = FlowEngine(tmp_path / "runs", tools)
    eng._fake_tools = tools
    yield eng
    flow_notify.set_sender(None)


@pytest.fixture()
def stub_sender():
    sent: list[tuple[str, str]] = []

    def _send(text: str, subject: str) -> dict:
        sent.append((subject, text))
        return {"ok": True, "message_id": f"mid-{len(sent)}", "error": ""}

    flow_notify.set_sender(_send)
    yield sent
    flow_notify.set_sender(None)


def wait_note(engine: FlowEngine, run_id: str, timeout: float = 3.0) -> list[dict]:
    """等后台投递线程把触达 note 从 sending 收口（delivered/error）——只看系统触达 note"""
    deadline = time.time() + timeout
    notes: list[dict] = []
    while time.time() < deadline:
        run = engine.store.find_run(run_id)
        notes = [n for n in (run.notifications if run else []) if n.get("kind")]
        if notes and all(n.get("status") != "sending" for n in notes):
            return notes
        time.sleep(0.02)
    return notes


# ─────────────────── ① 一键操作 ───────────────────

def test_dump_flow_roundtrip_all_node_types():
    """dump_flow → parse_flow 往返一致（克隆/编辑参数的序列化地基）"""
    data = {
        "id": "t-dump", "name": "全形状", "version": 2,
        "triggers": [{"type": "cron", "cron": "0 9 * * 1-5"},
                     {"type": "event", "event": "x.y"}, {"type": "manual"}],
        "nodes": [
            {"id": "a", "type": "tool", "tool": "p__t", "args": {"k": "v"},
             "retry": {"max": 3, "backoff": "exponential", "base_seconds": 2},
             "timeout_seconds": 30, "on_error": "skip"},
            {"id": "b", "type": "decision", "when": "$.a.k == 'v'",
             "then": "c", "else": "d"},
            {"id": "c", "type": "loop", "over": "$.a.items", "body": ["e"]},
            {"id": "e", "type": "tool", "tool": "p__t", "args": {}},
            {"id": "d", "type": "approval", "form": "home", "timeout_hours": 12,
             "on_timeout": "skip"},
            {"id": "f", "type": "human", "task": "做件事", "user": "甲",
             "budget": {"max_tokens": 100}, "package": "compact", "review": True},
            {"id": "g", "type": "action",
             "actions": [{"type": "notify", "channel": "wechat", "message": "嗨"}]},
            {"id": "h", "type": "parallel",
             "branches": [{"id": "b1", "tool": "p__t", "args": {"n": 1}}],
             "max_concurrency": 2, "on_error": "goto:g"},
        ],
        "observability": {"retain_runs": 9},
    }
    defn = parse_flow(data, source="roundtrip")
    again = parse_flow(dump_flow(defn), source="again")
    assert dump_flow(again) == dump_flow(defn)
    assert again.retain_runs == 9 and again.version == 2


def test_clone_flow_auto_id_persist_and_priority(engine):
    """克隆=独立用户件：自动 id/落盘/重启仍在/插件重载不冲掉"""
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    clone = engine.clone_flow("t-chain")
    assert clone["id"] == "t-chain-copy" and clone["plugin_id"] == "user"
    # 再克隆自动避让
    clone2 = engine.clone_flow("t-chain")
    assert clone2["id"] == "t-chain-copy2"
    # 指定非法 id 显式报因
    with pytest.raises(FlowRunError, match="非法"):
        engine.clone_flow("t-chain", new_id="BadID")
    with pytest.raises(FlowRunError, match="已存在"):
        engine.clone_flow("t-chain", new_id="t-chain-copy")
    # 落盘 + 重启装载仍在
    assert (engine.defs_dir / "t-chain-copy.yaml").is_file()
    fresh = FlowEngine(engine.store.runs_dir)
    assert "t-chain-copy" in {f["id"] for f in fresh.list_flows()}
    # 插件重载（register_flow 同 id）不冲掉用户覆盖件
    fresh.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    assert "t-chain-copy" in {f["id"] for f in fresh.list_flows()}


def test_update_flow_edit_params_and_validation(engine):
    """编辑参数：name/args/triggers 任意子集 → 覆盖件落盘；非法参数报人话"""
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    updated = engine.update_flow("t-chain", {
        "name": "链路流·改",
        "triggers": [{"type": "manual"}, {"type": "event", "event": "ping"}],
    })
    assert updated["name"] == "链路流·改"
    assert any(t["type"] == "event" for t in updated["triggers"])
    # 改节点参数（two 的工具名 + 参数）
    nodes = [dict(n) for n in dump_flow(engine.get_flow("t-chain"))["nodes"]]
    nodes[1]["tool"] = "p__two-v2"
    nodes[1]["args"] = {"y": 99}
    engine.update_flow("t-chain", {"nodes": nodes})
    defn = engine.get_flow("t-chain")
    assert defn.node_map["two"].tool == "p__two-v2"
    assert defn.node_map["two"].args == {"y": 99}
    # 覆盖件优先：插件重载后仍是编辑版
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    assert engine.get_flow("t-chain").node_map["two"].tool == "p__two-v2"
    # 非法参数（引用不存在节点）→ FlowRunError 带校验原因
    bad = [dict(n) for n in dump_flow(engine.get_flow("t-chain"))["nodes"]]
    bad[1] = {"id": "two", "type": "decision", "when": "true",
              "then": "ghost", "else": "three"}
    with pytest.raises(FlowRunError, match="引用不存在"):
        engine.update_flow("t-chain", {"nodes": bad})


def test_pause_blocks_triggers_force_overrides(engine):
    """暂停挡自动/手动触发（force 可越权）；恢复后照常"""
    data = {**CHAIN_FLOW, "triggers": [
        {"type": "manual"}, {"type": "event", "event": "ping"},
        {"type": "cron", "cron": "* * * * *"}]}
    engine.register_flow(parse_flow(data, source="s"), "demo")
    engine.pause_flow("t-chain", paused=True)
    assert engine.is_paused("t-chain")
    with pytest.raises(FlowRunError, match="已暂停"):
        asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
    with pytest.raises(FlowRunError, match="已暂停"):
        engine.start_run("t-chain", {"type": "cron"})
    assert engine.emit("ping") == []          # 事件触发被挡
    assert engine.tick() == []                # cron 触发被挡
    run = asyncio.run(engine.run_now("t-chain", {"type": "manual"}, force=True))
    assert run.status == "completed"          # force 越权立即触发
    engine.pause_flow("t-chain", paused=False)
    run2 = asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
    assert run2.status == "completed"
    # 暂停态持久化（重启仍在）
    engine.pause_flow("t-chain", paused=True)
    fresh = FlowEngine(engine.store.runs_dir)
    assert fresh.is_paused("t-chain")


def test_batch_ops_api(engine):
    """批量操作：run/pause/resume/delete 逐条独立成败"""
    from plugin.flow_api import build_flow_router
    for suffix in ("a", "b"):
        data = {**CHAIN_FLOW, "id": f"t-batch-{suffix}", "name": f"批量{suffix}",
                "nodes": [dict(n) for n in CHAIN_FLOW["nodes"]]}
        engine.register_flow(parse_flow(data, source="s"), "demo")
    engine.pause_flow("t-batch-b", paused=True)   # b 预置暂停

    app = FastAPI()
    app.include_router(build_flow_router(engine))
    client = TestClient(app)

    runs = client.post("/flows/batch", json={
        "op": "run", "flow_ids": ["t-batch-a", "t-batch-b"]}).json()
    assert runs["results"][0]["ok"] is True          # a 正常跑
    assert runs["results"][1]["ok"] is False and "暂停" in runs["results"][1]["error"]
    paused = client.post("/flows/batch", json={
        "op": "pause", "flow_ids": ["t-batch-a", "t-batch-b", "t-none"]}).json()
    assert paused["ok"] == 2 and paused["results"][2]["ok"] is False
    resumed = client.post("/flows/batch", json={
        "op": "resume", "flow_ids": ["t-batch-a", "t-batch-b"]}).json()
    assert resumed["ok"] == 2
    deleted = client.post("/flows/batch", json={
        "op": "delete", "flow_ids": ["t-batch-a", "t-batch-b"]}).json()
    assert deleted["ok"] == 2
    assert client.get("/flows").json()["count"] == 0


def test_clone_update_pause_api(engine):
    """HTTP 面：clone / PATCH 编辑参数 / pause / resume 真点链路"""
    from plugin.flow_api import build_flow_router
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    app = FastAPI()
    app.include_router(build_flow_router(engine))
    client = TestClient(app)

    clone = client.post("/flows/t-chain/clone", json={"new_name": "克隆件"}).json()
    assert clone["id"] == "t-chain-copy" and clone["name"] == "克隆件"
    patched = client.patch(f"/flows/{clone['id']}", json={
        "name": "克隆件·改", "retain_runs": 7}).json()
    assert patched["name"] == "克隆件·改" and patched["retain_runs"] == 7
    detail = client.get(f"/flows/{clone['id']}").json()
    assert detail["retain_runs"] == 7 and detail["paused"] is False
    assert client.post(f"/flows/{clone['id']}/pause").json()["paused"] is True
    bad = client.post(f"/flows/{clone['id']}/run", json={})
    assert bad.status_code == 409 and "暂停" in bad.json()["detail"]
    forced = client.post(f"/flows/{clone['id']}/run", json={"force": True})
    assert forced.status_code == 200
    assert client.post(f"/flows/{clone['id']}/resume").json()["paused"] is False


# ─────────────────── ② 失败可懂可修 ───────────────────

def test_error_normalize_codes():
    assert normalize_error("ToolGovernanceBlocked: policy deny")["code"] == CODE_GOVERNANCE_BLOCKED
    assert normalize_error("ApprovalRequired: 需要用户审批")["code"] == CODE_NEEDS_APPROVAL
    assert normalize_error("ConnectionRefused: [Errno 111]")["code"] == CODE_NETWORK
    assert normalize_error("TimeoutError: timed out")["code"] == CODE_TIMEOUT
    assert normalize_error("KeyError: 'demo-greeter__ghost' 工具未注册")["code"] == CODE_TOOL_MISSING
    assert normalize_error("表达式求值失败: $.x.y 不存在")["code"] == CODE_EXPR
    assert normalize_error("人工审批驳回: 不行")["code"] == CODE_APPROVAL_REJECTED
    norm = normalize_error("RuntimeError: p__two 人为失败")
    assert norm["code"] == "unknown" and norm["retryable"] is True
    assert norm["human"] and norm["suggestion"] and norm["detail"]


def test_retry_from_failed_node_same_instance(engine):
    """失败可修：从失败节点原地续跑——同实例、不重跑已完成节点"""
    tools = engine._fake_tools
    tools.fail_times["p__two"] = 1
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    run = asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
    assert run.status == "failed" and run.nodes["two"].status == "failed"
    assert run.nodes["one"].status == "ok"
    one_calls = sum(1 for name, _ in tools.calls if name == "p__one")
    assert one_calls == 1

    retried = asyncio.run(engine.retry_run(run.run_id))
    assert retried.run_id == run.run_id                 # 同实例原地续跑
    assert retried.status == "completed" and retried.error is None
    assert retried.nodes["two"].status == "ok"
    # 已完成节点没被重跑
    assert sum(1 for name, _ in tools.calls if name == "p__one") == one_calls
    assert any(e["event"] == "retry-resume" for e in retried.log)
    # 非失败实例不许 retry（409 语义）
    with pytest.raises(FlowRunError, match="回放"):
        asyncio.run(engine.retry_run(run.run_id))


def test_retry_api_and_error_norm_in_summary(engine):
    """HTTP 面：retry 端点 + 实例详情带错误归一（人话原因/建议/失败节点）"""
    from plugin.flow_api import build_flow_router
    engine._fake_tools.fail_times["p__two"] = 1
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    app = FastAPI()
    app.include_router(build_flow_router(engine))
    client = TestClient(app)

    run = client.post("/flows/t-chain/run", json={}).json()
    assert run["status"] == "failed"
    assert run["failed_node"] == "two"
    assert run["error_norm"]["human"]
    assert run["error_norm"]["suggestion"]
    two = next(n for n in run["nodes"] if n["node"] == "two")
    assert two["error_norm"]["code"]

    retried = client.post(f"/flows/runs/{run['run_id']}/retry", json={}).json()
    assert retried["status"] == "completed"
    ok = client.post(f"/flows/runs/{run['run_id']}/retry", json={})
    assert ok.status_code == 409          # 已完成不再许 retry


# ─────────────────── ③ 结果触达 ───────────────────

def test_notify_stub_completed_failed_waiting(engine, stub_sender):
    """触达链路（投递桩）：待审批/失败/完成三态都发微信且落 note"""
    tools = engine._fake_tools
    tools.fail_times["p__two"] = 1
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")

    run = asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
    notes = wait_note(engine, run.run_id)
    kinds = {n["kind"] for n in notes}
    assert kinds == {"failed"}
    assert notes[0]["status"].startswith("delivered")
    assert notes[0]["message_id"] == "mid-1"
    assert "❌" in notes[0]["message"] and "流程「链路流」失败" in notes[0]["message"]

    asyncio.run(engine.retry_run(run.run_id))
    notes = wait_note(engine, run.run_id)
    kinds = {n["kind"] for n in notes}
    assert kinds == {"failed", "completed"}
    completed = next(n for n in notes if n["kind"] == "completed")
    assert "✅" in completed["message"] and completed["status"] == "delivered"

    # 待审批触达（approval 流）
    data = {**CHAIN_FLOW, "id": "t-appr", "name": "审批流",
            "nodes": [dict(CHAIN_FLOW["nodes"][0]),
                      {"id": "gate", "type": "approval", "form": "请拍板"},
                      dict(CHAIN_FLOW["nodes"][2])]}
    engine.register_flow(parse_flow(data, source="s"), "demo")
    waiting = asyncio.run(engine.run_now("t-appr", {"type": "manual"}))
    assert waiting.status == "waiting-approval"
    notes = wait_note(engine, waiting.run_id)
    assert notes[0]["kind"] == "waiting-approval"
    assert "等你审批" in notes[0]["message"]
    assert "请拍板" in notes[0]["message"]
    # 同一迁移绝不重复触达
    engine.notify_run(waiting, "waiting-approval", node="gate",
                      dedupe_key=f"gate:{waiting.approval.created_at}")
    assert len(waiting.notifications) == 1
    # 微信通道名与 Reflex 旁路都挂上
    assert notes[0]["channel"].startswith("wechat")


def test_notify_urge_interval(engine, stub_sender):
    """催办（Pulse 语义）：待审批超间隔才催，间隔内不重复"""
    data = {**CHAIN_FLOW, "id": "t-urge", "name": "催办流",
            "nodes": [dict(CHAIN_FLOW["nodes"][0]),
                      {"id": "gate", "type": "approval", "form": "请拍板"},
                      dict(CHAIN_FLOW["nodes"][2])]}
    engine.register_flow(parse_flow(data, source="s"), "demo")
    run = asyncio.run(engine.run_now("t-urge", {"type": "manual"}))
    assert run.status == "waiting-approval"

    now = time.time()
    assert flow_notify.urge_pending(engine, now=run.approval.created_at + 1) == 0
    assert flow_notify.urge_pending(
        engine, now=run.approval.created_at + 13 * 3600) == 1
    notes = wait_note(engine, run.run_id)
    urge = [n for n in notes if n["kind"] == "urge"]
    assert len(urge) == 1 and "催办" in urge[0]["message"]
    # 刚催过不再催（以催办 note 真实时间戳计间隔）
    assert flow_notify.urge_pending(engine, now=urge[0]["ts"] + 60) == 0


def test_notify_never_blocks_and_stub_failure_recorded(engine):
    """投递失败不吞：note 记 error 状态；无桩（pytest）不发不登记"""
    flow_notify.set_sender(lambda text, subject: (_ for _ in ()).throw(
        RuntimeError("发送炸了")))
    try:
        engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
        run = asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
        assert run.status == "completed"                # 触达失败绝不影响流程
        notes = wait_note(engine, run.run_id)
        assert notes and notes[0]["status"].startswith("error")
    finally:
        flow_notify.set_sender(None)
    # 无桩：测试环境默认不投递、不登记（绝不真发微信）
    run2 = asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
    assert [n for n in run2.notifications if n.get("kind")] == []


def test_notify_deferred_retry_when_session_opens(engine):
    """iLink 发送窗口（对方发来消息前不许 bot 主动发）→ deferred，窗口开后补投送达"""
    sent: list[str] = []

    def flaky_sender(text: str, subject: str) -> dict:
        if not sent:   # 第一次：窗口没开
            return {"ok": False, "message_id": "",
                    "error": "iLink sendmessage session not ready: prepare failed"}
        return {"ok": True, "message_id": "mid-x", "error": ""}

    flow_notify.set_sender(flaky_sender)
    try:
        engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
        run = asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
        notes = wait_note(engine, run.run_id)
        completed = next(n for n in notes if n["kind"] == "completed")
        assert completed["status"].startswith("deferred")   # 窗口没开→待补投
        assert completed["attempts"] == 1
        # 窗口没开时补投不重试（间隔未到）
        assert flow_notify.retry_deferred(engine, now=completed["ts"] + 1) == 0
        # 窗口开了（sent 列表非空=下一次成功）→ 补投送达
        sent.append("go")
        assert flow_notify.retry_deferred(engine, now=completed["ts"] + 700) == 1
        notes = wait_note(engine, run.run_id)
        completed = next(n for n in notes if n["kind"] == "completed")
        assert completed["status"] == "delivered"
        assert completed["message_id"] == "mid-x"
        assert completed["attempts"] == 2
    finally:
        flow_notify.set_sender(None)


# ─────────────────── ④ 运行历史 ───────────────────

def test_run_stats_and_time_filter(engine):
    """按流程/时间筛选 + 成败统计/成功率/耗时"""
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    data = {**CHAIN_FLOW, "id": "t-other", "name": "另一条",
            "nodes": [dict(CHAIN_FLOW["nodes"][0]),
                      dict(CHAIN_FLOW["nodes"][2])]}
    engine.register_flow(parse_flow(data, source="s"), "demo")

    engine._fake_tools.fail_times["p__two"] = 1
    asyncio.run(engine.run_now("t-chain", {"type": "manual"}))     # failed
    asyncio.run(engine.run_now("t-chain", {"type": "manual"}))     # completed
    asyncio.run(engine.run_now("t-other", {"type": "manual"}))     # completed

    stats = engine.run_stats()
    assert stats["total"] == 3
    assert stats["completed"] == 2 and stats["failed"] == 1
    assert stats["success_rate"] == 66.7
    assert stats["avg_duration_ms"] is not None and stats["max_duration_ms"] >= stats["avg_duration_ms"]
    by_flow = {b["flow_id"]: b for b in stats["by_flow"]}
    assert by_flow["t-chain"]["total"] == 2 and by_flow["t-other"]["completed"] == 1

    only_chain = engine.run_stats(flow_id="t-chain")
    assert only_chain["total"] == 2
    future = engine.run_stats(since=time.time() + 3600)
    assert future["total"] == 0
    window = engine.run_stats(since=time.time() - 3600, until=time.time() + 3600)
    assert window["total"] == 3


def test_runs_list_time_filter_api(engine):
    from plugin.flow_api import build_flow_router
    engine.register_flow(parse_flow(CHAIN_FLOW, source="s"), "demo")
    asyncio.run(engine.run_now("t-chain", {"type": "manual"}))
    app = FastAPI()
    app.include_router(build_flow_router(engine))
    client = TestClient(app)

    assert client.get("/flows/runs").json()["count"] == 1
    assert client.get("/flows/runs?since=2020-01-01").json()["count"] == 1
    assert client.get("/flows/runs?since=2100-01-01").json()["count"] == 0
    assert client.get("/flows/runs?until=2020-01-01").json()["count"] == 0
    assert client.get("/flows/runs?since=bad").status_code == 400
    stats = client.get("/flows/runs/stats").json()
    assert stats["total"] == 1 and stats["completed"] == 1
    stats2 = client.get("/flows/runs/stats?flow_id=t-chain&since=2020-01-01").json()
    assert stats2["total"] == 1
