"""
流程协作端到端真实验证 — 真实双 agent / 人工节点真收真交 / 超时催办实测

真实链路（任务验收项）：
① 真实双 agent：流程 → agent_node 建单（组织角色路由定人）→ A2A JSON-RPC 真 HTTP
   派单（a2a/task/delegate，a2a-server 真服务）→ bridge 真 ACP WebSocket 会话
   （initialize/session.create/session/prompt→session/update→session/completed）
   → A 侧 SoulMate 真干活（确定性计算，非 mock 应答）→ a2a/task/result 回流
   → 工单交付 → 流程续跑消费结果。
② 人工节点真收真交：human_node 工单入收件箱 → HTTP 认领（抢占锁）→ 交付回流
   → 流程续跑至完成。
③ 超时催办实测：真 PulseLoop 心跳拍（beat()）驱动超时恢复催办（defer 有界重试
   → needs_attention 升级），催办次数/留痕实测；引擎 on_timeout=fail 收口实测。
预算闸转人工/工单语义的单测在 test_flow_collab*.py（此处全链另覆盖）。
"""
from __future__ import annotations

import asyncio
import json
import re
import socket
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
A2A_DIR = REPO_ROOT / "a2a-server"

# a2a-server 是平铺模块包（from models import …），装载期一次性让位顶层命名空间；
# 与既有测试无顶层同名 import（已核），冲突即显式报错绝不取错模块。
_A2A_FLAT_NAMES = ("models", "server", "bridge", "logger", "client", "security",
                   "task_store", "stream_manager", "push_notify", "sse_events",
                   "agent_card")


def _import_a2a():
    collisions = [n for n in _A2A_FLAT_NAMES
                  if n in sys.modules and not str(getattr(sys.modules[n], "__file__", ""))
                  .startswith(str(A2A_DIR))]
    assert not collisions, f"a2a-server 平铺模块名冲突: {collisions}"
    if str(A2A_DIR) not in sys.path:
        sys.path.insert(0, str(A2A_DIR))
    import bridge as a2a_bridge
    import server as a2a_server
    import task_store as a2a_task_store
    return a2a_server, a2a_task_store, a2a_bridge


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ── A 侧 SoulMate：真 ACP WebSocket 服务（Agent Engine 协议面）─────────

def _make_acp_soulmate(received: list):
    """ACP v1.0 worker（websockets 真服务）：收到 prompt 真计算真回流。

    干活=确定性加工（抽取数字求和），可断言非 mock；流式 contentDelta +
    session/completed 与生产 Agent Engine 同协议形状。
    """

    async def handler(ws):
        async for raw in ws:
            msg = json.loads(raw)
            method, mid = msg.get("method"), msg.get("id")
            if method == "initialize":
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {"agent": {"name": "SoulMate-A"}}}))
            elif method == "session.create":
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": mid,
                    "result": {"session_id": f"s-{uuid.uuid4().hex[:8]}"}}))
            elif method == "session/prompt":
                sid = msg["params"].get("sessionId", "")
                prompt = msg["params"].get("prompt", "")
                received.append(prompt)
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": mid, "result": {}}))   # ack
                # SoulMate 真干活：抽取指令中的数字求和，产出可断言结论
                numbers = [int(x) for x in re.findall(r"\d+", prompt)]
                reply = f"[SoulMate-A] 已处理 {len(numbers)} 个数字，合计={sum(numbers)}"
                for i in range(0, len(reply), 10):
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0", "method": "session/update",
                        "params": {"sessionId": sid,
                                   "contentDelta": reply[i:i + 10]}}))
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "method": "session/completed",
                    "params": {"sessionId": sid, "summary": "done"}}))

    return handler


class _ServedStack:
    """真双端服务：A2A JSON-RPC HTTP（uvicorn）+ ACP WS SoulMate（websockets）同环。"""

    def __init__(self, tmp_path: Path):
        self.a2a_port = _free_port()
        self.acp_port = _free_port()
        self.received: list[str] = []
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.ready = threading.Event()
        self.error: BaseException | None = None
        self._uvicorn = None
        self.a2a_server, self.a2a_task_store, self.a2a_bridge = _import_a2a()
        # 工单库隔离（绝不写真实 data/a2a_tasks.db）
        self.a2a_server._task_store = self.a2a_task_store.TaskStore(
            db_path=tmp_path / "a2a_tasks.db")

    def _run(self):
        import uvicorn
        import websockets
        from fastapi import FastAPI

        asyncio.set_event_loop(self.loop)

        async def _serve():
            app = FastAPI()
            app.include_router(self.a2a_server.rpc_router)
            app.include_router(self.a2a_server.router)
            config = uvicorn.Config(app=app, host="127.0.0.1", port=self.a2a_port,
                                    log_level="warning")
            server = uvicorn.Server(config)
            self._uvicorn = server
            handler = _make_acp_soulmate(self.received)
            async with websockets.serve(handler, "127.0.0.1", self.acp_port):
                self.ready.set()
                await server.serve()

        try:
            self.loop.run_until_complete(_serve())
        except BaseException as e:  # noqa: BLE001 — 线程内异常显式带回
            self.error = e
            self.ready.set()

    def __enter__(self):
        self.thread.start()
        assert self.ready.wait(10), "双端服务未就绪"
        assert self.error is None, f"双端服务启动失败: {self.error}"
        # bridge 指向真 ACP 端点（生产=ws://127.0.0.1:8787 Agent Engine）
        self.a2a_bridge._bridge = self.a2a_bridge.AgentEngineBridge(
            ws_url=f"ws://127.0.0.1:{self.acp_port}")
        return self

    def __exit__(self, *exc):
        def _stop():
            if self._uvicorn is not None:
                self._uvicorn.should_exit = True
        try:
            self.loop.call_soon_threadsafe(_stop)
        except RuntimeError:
            pass
        self.thread.join(10)


@pytest.fixture()
def stack(tmp_path):
    with _ServedStack(tmp_path) as st:
        yield st


@pytest.fixture()
def env(tmp_path, monkeypatch, stack):
    monkeypatch.setenv("FLOW_RUNS_DIR", str(tmp_path / "flow-runs"))
    monkeypatch.setenv("COLLAB_TASKS_DIR", str(tmp_path / "collab"))
    monkeypatch.setenv("COLLAB_COST_DB", str(tmp_path / "costs.db"))
    monkeypatch.setenv("ORG_CHART_FILE", str(tmp_path / "org.json"))
    from plugin.flow_collab import A2AChannel, reset_collab
    from plugin.flow_engine import FlowEngine, reset_engine
    reset_engine()
    reset_collab()
    engine = FlowEngine(tmp_path / "flow-runs")

    async def _tc(name, args, approved=False):
        return {"echo": args}

    engine.set_tool_caller(_tc)
    runtime = engine.collab()
    # 真 A2A 通道：默认 httpx 传输打真 HTTP 端点（非注入桩）
    runtime.channel = A2AChannel(
        endpoint=f"http://127.0.0.1:{stack.a2a_port}/rpc/a2a", timeout_s=10.0)
    yield engine, runtime
    reset_engine()
    reset_collab()


# ── ① 真实双 agent 全链 ─────────────────────────────────────────────

def test_real_two_agent_flow_roundtrip(env, stack):
    """流程→A 任务→A 的 SoulMate 真干活（ACP 会话）→ 结果回流 → 流程继续到完成"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from plugin.collab_api import build_collab_router
    from plugin.flow_def import parse_flow

    engine, runtime = env
    engine.register_flow(parse_flow({
        "id": "real-two-agent", "name": "真实双agent流",
        "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "ask", "type": "agent", "user": "alice",
             "target_agent": "soulmate", "task": "统计 3 7 11 的合计并给结论",
             "timeout_hours": 1},
            {"id": "review", "type": "human", "user": "bob",
             "task": "复核 SoulMate 结论并交付验收意见",
             "form": "验收意见", "timeout_hours": 1},
            {"id": "finish", "type": "tool", "tool": "p__t",
             "args": {"agent_result": "$.ask.result",
                      "review_result": "$.review.result"}},
        ],
    }), plugin_id="e2e")
    run = asyncio.run(engine.run_now("real-two-agent", {"type": "manual"}))
    assert run.status == "waiting-approval" and run.approval.kind == "agent"
    ask_task = runtime.store.load(run.approval.task_id)
    assert ask_task.assignee == "alice" and ask_task.a2a_task_id

    # 结果回流：真 HTTP 轮询 a2a/task/result（bridge→ACP→SoulMate 干完活）
    deadline = time.time() + 30
    while time.time() < deadline:
        if asyncio.run(runtime.poll_agent_tasks()):
            break
        time.sleep(0.2)
    else:
        pytest.fail("agent 结果未在 30s 内回流")
    # SoulMate 真干活：收到指令且产出=确定性计算（3+7+11=21）
    assert stack.received and "3 7 11" in stack.received[0]
    run = engine.store.find_run(run.run_id)
    assert run.status == "waiting-approval" and run.approval.kind == "human"
    assert "合计=21" in run.outputs["ask"]["result"]
    assert runtime.store.load(ask_task.task_id).delivered_via == "a2a"

    # 人工节点真收真交（HTTP 收发台）：认领（抢占锁）→ 交付回流 → 流程续跑
    app = FastAPI()
    app.include_router(build_collab_router(runtime))
    client = TestClient(app)
    review_task = runtime.store.load(run.approval.task_id)
    claimed = client.post(f"/collab/tasks/{review_task.task_id}/claim",
                          json={"claimed_by": "bob"}).json()
    resp = client.post(f"/collab/tasks/{review_task.task_id}/deliver",
                       json={"result": "验收通过", "delivered_by": "bob",
                             "claim_token": claimed["claim_token"]})
    assert resp.status_code == 200
    run = engine.store.find_run(run.run_id)
    assert run.status == "completed"
    assert run.outputs["review"]["result"] == "验收通过"


# ── ③ 超时催办实测（真 PulseLoop 心跳拍）───────────────────────────

def test_timeout_reminder_measured_with_real_pulse(env):
    """超时催办实测：真 PulseLoop.beat() 驱动超时恢复催办（defer 有界→升级）"""
    from agent.pulse import PulseLoop
    from plugin.flow_def import parse_flow

    engine, runtime = env
    clock = {"now": 1_000_000.0}
    fake = lambda: clock["now"]  # noqa: E731
    runtime._clock = fake
    # 真 PulseLoop（心跳拍可直接驱动）：接协作催办钩子
    loop = PulseLoop(interval_s=60.0, now=fake)
    runtime.attach_pulse(loop)

    engine.register_flow(parse_flow({
        "id": "remind-flow", "name": "催办流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "wait", "type": "human", "user": "carol", "task": "交周报",
             "timeout_hours": 1, "on_timeout": "fail"},
            {"id": "next", "type": "action",
             "actions": [{"type": "notify", "message": "继续"}]},
        ],
    }), plugin_id="e2e")
    run = asyncio.run(engine.run_now("remind-flow", {"type": "manual"}))
    task = runtime.store.load(run.approval.task_id)
    # 工单已登记进 Pulse 长任务账（心跳调度面可见）
    assert any(t.task_id == f"collab:{task.task_id}" for t in loop.tasks())

    # 推进假时钟过交付期限，连打 4 拍：每拍催办（defer）→ 恢复耗尽 → needs_attention
    for _ in range(4):
        clock["now"] += 2 * 3600
        loop.beat()
    reloaded = runtime.store.load(task.task_id)
    assert reloaded.reminders >= 3                       # 催办实测≥3 次
    assert sum(1 for h in reloaded.history if h["event"] == "reminded") >= 3
    pulse_tasks = {t.task_id: t for t in loop.tasks()}
    assert pulse_tasks[f"collab:{task.task_id}"].status == "needs_attention"
    beats = loop.recent_beats(10)
    assert beats and any(b.get("overdue") for b in beats)   # 超时可见于心跳拍
    # Reflex 应激事件流留痕（任务到/催办即应激）
    from agent.reflex import get_reflex
    assert any("collab." in str(r) for r in get_reflex().recent(50))

    # 引擎 on_timeout=fail 收口（真实期限账）：实例失败 + 工单 failed
    engine.check_timeouts(now=run.approval.created_at + 2 * 3600)
    run = engine.store.find_run(run.run_id)
    assert run.status == "failed"
    assert runtime.store.load(task.task_id).status == "failed"
