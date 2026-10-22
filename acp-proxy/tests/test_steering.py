# -*- coding: utf-8 -*-
"""P0-4/P1 插话队列 + 活动可观测测试

调研来源：Khoj interrupt_queue（research.py:516-535）、goose Steer（ops_steer.rs 78行）、
nanobot注入上限、goose peek三指标、claude-code noop自报streak。
无网络依赖：SQLite用/tmp隔离库，SoulMateAgent用__new__轻量harness（不跑完整__init__）。
"""
import asyncio
import inspect
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.steering import (
    ABORT_MESSAGE,
    MAX_INJECTIONS_PER_TURN,
    MAX_QUEUE_DEPTH,
    ActivityStore,
    SessionActivity,
    SteeringQueue,
)
from agent.sandbox_policy import SandboxPolicy
from agent.soulmate_agent import SoulMateAgent


def run(coro):
    return asyncio.run(coro)


def tmp_db(tmp_path) -> str:
    return str(Path(tmp_path) / "activity.db")


# ════════════════════════════════════════════════════════════════
# SteeringQueue — Khoj interrupt_queue + nanobot上限
# ════════════════════════════════════════════════════════════════

class TestSteeringQueue:
    def test_enqueue_drain_fifo(self):
        q = SteeringQueue()
        q.enqueue("s1", "第一条")
        q.enqueue("s1", "第二条")
        drained = q.drain("s1")
        assert [m.text for m in drained] == ["第一条", "第二条"]
        assert drained[0].seq < drained[1].seq

    def test_drain_empty_returns_empty(self):
        q = SteeringQueue()
        assert q.drain("s1") == []

    def test_drain_cap_per_turn(self):
        """nanobot _MAX_INJECTIONS_PER_TURN：每轮注入封顶"""
        q = SteeringQueue()
        for i in range(MAX_INJECTIONS_PER_TURN + 2):
            q.enqueue("s1", f"msg{i}")
        first = q.drain("s1")
        assert len(first) == MAX_INJECTIONS_PER_TURN
        assert q.pending("s1") == 2  # 剩余留到下个turn

    def test_pending_count(self):
        q = SteeringQueue()
        q.enqueue("s1", "a")
        q.enqueue("s1", "b")
        assert q.pending("s1") == 2
        q.drain("s1", max_n=1)
        assert q.pending("s1") == 1

    def test_queue_depth_limit(self):
        """Khoj asyncio.Queue(maxsize=10)：超深拒绝新消息（返回None，显式丢弃）"""
        q = SteeringQueue()
        for i in range(MAX_QUEUE_DEPTH):
            assert q.enqueue("s1", f"m{i}") is not None
        assert q.enqueue("s1", "overflow") is None
        assert q.pending("s1") == MAX_QUEUE_DEPTH

    def test_session_isolation(self):
        q = SteeringQueue()
        q.enqueue("s1", "only s1")
        q.enqueue("s2", "only s2")
        assert [m.text for m in q.drain("s1")] == ["only s1"]
        assert q.pending("s1") == 0
        assert q.pending("s2") == 1

    def test_clear(self):
        q = SteeringQueue()
        q.enqueue("s1", "x")
        q.clear("s1")
        assert q.pending("s1") == 0

    @pytest.mark.parametrize("text", ["/abort", "/stop", "abort", "ABORT", " /abort "])
    def test_is_abort_variants(self, text):
        assert SteeringQueue.is_abort(text) is True

    @pytest.mark.parametrize("text", ["继续", "/abortall", "abort task", ""])
    def test_is_abort_negative(self, text):
        assert SteeringQueue.is_abort(text) is False

    def test_abort_flag_on_message(self):
        q = SteeringQueue()
        msg = q.enqueue("s1", ABORT_MESSAGE)
        assert msg.is_abort is True


# ════════════════════════════════════════════════════════════════
# SessionActivity — goose peek三指标 + claude-code noop自报
# ════════════════════════════════════════════════════════════════

class TestSessionActivity:
    def test_mark_running(self):
        a = SessionActivity(session_id="s1")
        a.mark_running()
        assert a.status == "running"
        assert a.started_at > 0

    def test_mark_progress_increments_durable_turn(self):
        a = SessionActivity(session_id="s1")
        a.mark_running()
        a.mark_progress(tool=True, tool_count=3)
        assert a.durable_turns == 1
        assert a.tool_calls_total == 3
        assert a.last_progress_at > 0

    def test_progress_resets_noop_streak(self):
        """真实进展后noop streak清零（claude-code streak语义）"""
        a = SessionActivity(session_id="s1")
        a.report_noop()
        a.report_noop()
        assert a.noop_streak == 2
        a.mark_progress(tool=False)
        assert a.noop_streak == 0
        assert a.total_noops == 2  # 总数不回退

    def test_noop_streak_folds_max(self):
        a = SessionActivity(session_id="s1")
        for _ in range(4):
            a.report_noop()
        a.mark_progress(tool=False)
        a.report_noop()
        assert a.max_noop_streak == 4
        assert a.noop_streak == 1
        assert a.total_noops == 5

    def test_peek_three_metrics_fields(self):
        """goose peek三指标：durable turns / idle时长 / buffered通知数"""
        a = SessionActivity(session_id="s1")
        a.mark_running()
        a.mark_progress(tool=True, tool_count=2)
        a.buffered = 3
        p = a.peek()
        assert p["durable_turns"] == 1
        assert p["buffered"] == 3
        assert "idle_seconds" in p
        assert p["status"] == "running"

    def test_idle_seconds_zero_without_progress(self):
        a = SessionActivity(session_id="s1")
        assert a.idle_seconds() == 0.0

    def test_idle_seconds_grows(self):
        a = SessionActivity(session_id="s1")
        a.mark_progress(tool=False)
        now = a.last_progress_at + 42.0
        assert a.idle_seconds(now) == pytest.approx(42.0, abs=0.1)

    def test_mark_aborted(self):
        a = SessionActivity(session_id="s1")
        a.mark_running()
        a.mark_aborted()
        assert a.status == "aborted"
        assert a.aborted == 1

    def test_mark_idle_after_run(self):
        a = SessionActivity(session_id="s1")
        a.mark_running()
        a.mark_idle()
        assert a.status == "idle"

    def test_peek_includes_steer_stats(self):
        a = SessionActivity(session_id="s1")
        a.steer_queued = 2
        a.steer_injected = 1
        p = a.peek()
        assert p["steer_queued"] == 2
        assert p["steer_injected"] == 1


# ════════════════════════════════════════════════════════════════
# ActivityStore — SQLite持久化（agent子进程写，FastAPI跨进程读）
# ════════════════════════════════════════════════════════════════

class TestActivityStore:
    def test_upsert_peek_roundtrip(self, tmp_path):
        store = ActivityStore(db_path=tmp_db(tmp_path))
        a = SessionActivity(session_id="s1")
        a.mark_running()
        a.mark_progress(tool=True, tool_count=2)
        a.buffered = 1
        store.upsert(a)
        p = store.peek("s1")
        assert p["durable_turns"] == 1
        assert p["tool_calls_total"] == 2
        assert p["buffered"] == 1
        assert p["status"] == "running"

    def test_upsert_updates_existing(self, tmp_path):
        store = ActivityStore(db_path=tmp_path / "a.db" if False else tmp_db(tmp_path))
        a = SessionActivity(session_id="s1")
        a.mark_running()
        store.upsert(a)
        a.mark_progress(tool=False)
        a.mark_idle()
        store.upsert(a)
        p = store.peek("s1")
        assert p["durable_turns"] == 1
        assert p["status"] == "idle"

    def test_peek_nonexistent_returns_none(self, tmp_path):
        store = ActivityStore(db_path=tmp_db(tmp_path))
        assert store.peek("nope") is None

    def test_peek_all_summary(self, tmp_path):
        store = ActivityStore(db_path=tmp_db(tmp_path))
        for sid, noop_times in [("s1", 2), ("s2", 0)]:
            a = SessionActivity(session_id=sid)
            a.mark_running()
            for _ in range(noop_times):
                a.report_noop()
            a.buffered = 1 if sid == "s1" else 0
            store.upsert(a)
        data = store.peek_all()
        assert data["summary"]["total_sessions"] == 2
        assert data["summary"]["by_status"].get("running") == 2
        assert data["summary"]["total_noops"] == 2
        assert data["summary"]["buffered_total"] == 1

    def test_noop_persisted(self, tmp_path):
        """noop自报跨进程可见（monitoring探测）"""
        store = ActivityStore(db_path=tmp_db(tmp_path))
        a = SessionActivity(session_id="s1")
        a.report_noop()
        a.report_noop()
        a.report_noop()
        store.upsert(a)
        p = store.peek("s1")
        assert p["noop_streak"] == 3
        assert p["max_noop_streak"] == 3

    def test_init_idempotent(self, tmp_path):
        path = tmp_db(tmp_path)
        ActivityStore(db_path=path)
        ActivityStore(db_path=path)  # 二次初始化不报错
        assert Path(path).exists()


# ════════════════════════════════════════════════════════════════
# SoulMateAgent harness — prompt()插话分支 + _run_llm_with_tools接线
# ════════════════════════════════════════════════════════════════

class FakeFence:
    """writer fence替身：主循环每轮调 heartbeat（2026-10-23 接线），替身须建模该协作者"""
    def heartbeat(self, session_id):
        pass


class FakeArch:
    """session_guard模拟：acquired=False模拟'会话正在运行'"""
    def __init__(self, acquired: bool):
        self._acquired = acquired
        self.writer_fence = FakeFence()

    @asynccontextmanager
    async def session_guard(self, session_id, writer_id=""):
        yield self._acquired


class FakeGate:
    """权限门禁deny：工具走合成阻断结果continue，避开真实工具执行机器"""
    def __init__(self, allow=False):
        self.allow = allow
        self.calls = []

    async def check(self, session_id, tool_name, tool_args, **kw):
        self.calls.append((tool_name, tool_args))
        return SimpleNamespace(
            allowed=self.allow,
            behavior="allow" if self.allow else "deny",
            blocked_reason="blocked by test gate",
            rule_source="test",
            mode="default",
        )


class FakeLLM:
    """脚本化LLM：每个script元素=一次chat_stream_with_tools产出；
    callable元素在流开始时执行（用于模拟'运行中用户发消息'）"""
    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.seen_messages = []

    def chat_stream_with_tools(self, messages=None, tools=None, system_prompt=None, **kw):
        self.seen_messages.append([dict(m) for m in (messages or [])])
        step = self.scripts.pop(0) if self.scripts else []

        async def gen():
            for item in step:
                if callable(item):
                    r = item()
                    if inspect.isawaitable(r):
                        await r
                else:
                    yield item
        return gen()


def make_agent(tmp_path, acquired=False, scripts=None) -> SoulMateAgent:
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent._pending_choices = {}
    agent.sessions = {"s1": {"workspace": "/tmp"}}  # prompt()用真值检查，空dict会被当not found
    agent._steering = SteeringQueue()
    # kilocode #7 Turn生命周期（abort路径写close_reason，_prompt_inner open/close）
    from agent import turn_lifecycle as _tl
    agent._turn_lifecycle = _tl.TurnLifecycleBus()
    agent._turn_close_reason = {}
    agent._activity_store = ActivityStore(db_path=tmp_db(tmp_path))
    agent._activities = {}
    agent._client = None
    agent._arch = FakeArch(acquired=acquired)
    # _run_llm_with_tools 所需轻量依赖
    agent._session_cwds = {}
    agent._project_root = "/tmp"
    agent._streamed_flags = {}
    agent._evolution_engine = None
    agent._pref_learner = MagicMock()
    agent._pref_learner.get_context_prompt.return_value = ""
    agent._reflection_engine = MagicMock()
    agent._reflection_engine.get_improvement_context.return_value = ""
    agent._memory_consolidator = MagicMock()
    agent._skill_learner = MagicMock()
    agent._skill_learner.get_context_prompt.return_value = ""

    async def _no_mcp():
        return []

    agent._fetch_mcp_tools = _no_mcp
    agent._permission_gate = FakeGate(allow=False)
    # kilocode supplement3 #15：每agent独立store（默认不受限，测试按需set_restricted）
    agent._sandbox_policy = SandboxPolicy(
        store_path=str(Path(tempfile.mkdtemp()) / "sandbox_policy.json"))
    agent.llm_engine = FakeLLM(scripts or [])
    return agent


class TestArchFenceReject:
    """EnhancedArchitecture栅栏必须REJECT（修复QUEUE假串行化bug）"""

    def test_contended_guard_rejected_immediately(self):
        from agent.architecture_enhanced import EnhancedArchitecture
        arch = EnhancedArchitecture()

        async def scenario():
            async with arch.session_guard("s1", writer_id="prompt:1") as first:
                assert first is True
                async with arch.session_guard("s1", writer_id="prompt:2") as second:
                    assert second is False  # 立即False→prompt()走插话队列
            # 前一个释放后可再次获取
            async with arch.session_guard("s1", writer_id="prompt:3") as third:
                assert third is True
        run(scenario())

    def test_different_sessions_not_contended(self):
        from agent.architecture_enhanced import EnhancedArchitecture
        arch = EnhancedArchitecture()

        async def scenario():
            async with arch.session_guard("s1", writer_id="a") as g1:
                async with arch.session_guard("s2", writer_id="b") as g2:
                    assert g1 is True and g2 is True
        run(scenario())


class TestPromptSteer:
    """prompt() writer-fence未获取 → 插话排队（原实现直接拒绝丢弃）"""

    def test_busy_message_queued_not_dropped(self, tmp_path):
        agent = make_agent(tmp_path, acquired=False)
        blocks = [SimpleNamespace(text="顺便把config也检查一下")]
        resp = run(agent.prompt(blocks, "s1"))
        assert resp.stop_reason == "end_turn"       # 接受排队，不是refusal
        assert agent._steering.pending("s1") == 1   # 消息在队列里，没丢
        act = agent._activities["s1"]
        assert act.steer_queued == 1
        assert act.buffered == 1

    def test_busy_persisted_for_peek(self, tmp_path):
        agent = make_agent(tmp_path, acquired=False)
        run(agent.prompt([SimpleNamespace(text="插话A")], "s1"))
        run(agent.prompt([SimpleNamespace(text="插话B")], "s1"))
        p = agent._activity_store.peek("s1")
        assert p["buffered"] == 2
        assert p["steer_queued"] == 2

    def test_busy_abort_queued(self, tmp_path):
        """Khoj abort_message语义：/abort在运行中→排队，loop在边界停止"""
        agent = make_agent(tmp_path, acquired=False)
        resp = run(agent.prompt([SimpleNamespace(text="/abort")], "s1"))
        assert resp.stop_reason == "end_turn"
        pending = agent._steering.drain("s1")
        assert len(pending) == 1 and pending[0].is_abort

    def test_busy_queue_full_drop_marked(self, tmp_path):
        """队列满显式标记丢弃（AIHawk：丢弃必须可见）"""
        agent = make_agent(tmp_path, acquired=False)
        for i in range(MAX_QUEUE_DEPTH):
            agent._steering.enqueue("s1", f"m{i}")
        resp = run(agent.prompt([SimpleNamespace(text="第11条")], "s1"))
        assert resp.stop_reason == "end_turn"
        assert agent._steering.pending("s1") == MAX_QUEUE_DEPTH  # 新消息未入队
        assert agent._activities["s1"].steer_queued == 0         # 未计入排队数

    def test_dict_text_block_also_extracted(self, tmp_path):
        agent = make_agent(tmp_path, acquired=False)
        run(agent.prompt([{"type": "text", "text": "来自dict block的插话"}], "s1"))
        msgs = agent._steering.drain("s1")
        assert msgs[0].text == "来自dict block的插话"


class TestAgentLoopSteering:
    """_run_llm_with_tools：turn间隙注入 + noop自报 + peek持久化"""

    def test_noop_reported_and_persisted(self, tmp_path):
        """LLM一轮既无工具调用也无文本 → noop自报（claude-code）"""
        agent = make_agent(tmp_path, scripts=[[]])
        resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "hi"}], "s1"))
        assert resp == ""
        p = agent._activity_store.peek("s1")
        assert p["total_noops"] == 1
        assert p["noop_streak"] == 1
        assert p["durable_turns"] == 0
        assert p["status"] == "idle"

    def test_text_round_is_progress_not_noop(self, tmp_path):
        agent = make_agent(tmp_path, scripts=[["这是回答"]])
        resp, _ = run(agent._run_llm_with_tools([{"role": "user", "content": "hi"}], "s1"))
        assert resp == "这是回答"
        p = agent._activity_store.peek("s1")
        assert p["durable_turns"] == 1
        assert p["total_noops"] == 0

    def test_steer_injected_at_turn_boundary(self, tmp_path):
        """goose Steer：运行中入队的消息在turn间隙注入messages（保留已完成迭代）"""
        agent = make_agent(tmp_path)
        scripts = [[
            lambda: agent._steering.enqueue("s1", "顺便检查config.py"),
            {"tool_calls": [{"id": "t1", "function": {"name": "terminal", "arguments": "{}"}}]},
        ], ["完成"]]
        agent.llm_engine = FakeLLM(scripts)
        resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "做任务"}], "s1"))
        # 第二次LLM调用时messages应包含插话（Khoj：拼进当前任务历史）
        second_call_msgs = agent.llm_engine.seen_messages[1]
        steer_msgs = [m for m in second_call_msgs if "[用户插话] 顺便检查config.py" in str(m.get("content", ""))]
        assert len(steer_msgs) == 1
        assert resp == "完成"
        # 工具轮（deny合成结果）+ 文本轮 = 2个durable turns
        p = agent._activity_store.peek("s1")
        assert p["durable_turns"] == 2
        assert p["steer_injected"] == 1
        assert p["status"] == "idle"

    def test_abort_stops_loop(self, tmp_path):
        """Khoj abort：队列中的/abort在turn边界终止任务"""
        agent = make_agent(tmp_path)
        agent._steering.enqueue("s1", "先做A")
        agent._steering.enqueue("s1", "/abort")
        agent.llm_engine = FakeLLM([["不应该被调用的第二轮"]])
        resp, _ = run(agent._run_llm_with_tools([{"role": "user", "content": "长任务"}], "s1"))
        assert "[任务被用户插话中断]" in resp
        # abort在第1轮turn边界（LLM调用前）即生效：LLM一次都没被调用
        assert len(agent.llm_engine.seen_messages) == 0
        p = agent._activity_store.peek("s1")
        assert p["status"] == "aborted"
        assert p["aborted"] == 1

    def test_drain_cap_respected_in_loop(self, tmp_path):
        """nanobot每轮注入上限：队列5条，第一轮只注入3条，余量进下一轮"""
        agent = make_agent(tmp_path)
        enqueue_many = [lambda i=i: agent._steering.enqueue("s1", f"插话{i}") for i in range(5)]
        scripts = [enqueue_many + [
            {"tool_calls": [{"id": "t1", "function": {"name": "terminal", "arguments": "{}"}}]},
        ], ["done"]]
        agent.llm_engine = FakeLLM(scripts)
        run(agent._run_llm_with_tools([{"role": "user", "content": "任务"}], "s1"))
        second_call_msgs = str(agent.llm_engine.seen_messages[1])
        assert second_call_msgs.count("[用户插话]") == MAX_INJECTIONS_PER_TURN
        p = agent._activity_store.peek("s1")
        assert p["steer_injected"] == MAX_INJECTIONS_PER_TURN
        assert p["buffered"] == 2  # 剩余2条仍排队（下个turn或下个任务注入）

    def test_leftover_steer_injected_on_next_run(self, tmp_path):
        """Khoj语义：任务结束时队列未清空 → 下次运行开始时注入"""
        agent = make_agent(tmp_path)
        agent._steering.enqueue("s1", "遗留指令")
        agent.llm_engine = FakeLLM([["新回答"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "新任务"}], "s1"))
        first_call_msgs = str(agent.llm_engine.seen_messages[0])
        assert "[用户插话] 遗留指令" in first_call_msgs

    def test_tool_round_counts_durable_turn(self, tmp_path):
        agent = make_agent(tmp_path)
        scripts = [[
            {"tool_calls": [{"id": "t1", "function": {"name": "terminal", "arguments": "{}"}}]},
        ], ["最终"]]
        agent.llm_engine = FakeLLM(scripts)
        run(agent._run_llm_with_tools([{"role": "user", "content": "执行"}], "s1"))
        p = agent._activity_store.peek("s1")
        assert p["durable_turns"] == 2
        assert p["tool_calls_total"] == 1
        assert agent._permission_gate.calls[0][0] == "terminal"

    def test_peek_sessions_aggregate(self, tmp_path):
        agent = make_agent(tmp_path, scripts=[[]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "x"}], "s1"))
        data = agent.peek_sessions()
        assert data["summary"]["total_sessions"] == 1
        assert data["sessions"][0]["session_id"] == "s1"

    def test_activity_status_running_during_loop(self, tmp_path):
        """运行中status=running可被peek观测（'它在干嘛'中途可见）"""
        agent = make_agent(tmp_path)
        seen = {}

        def capture_status():
            seen["status"] = agent._activity_store.peek("s1")["status"]

        agent.llm_engine = FakeLLM([[capture_status, "ok"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "x"}], "s1"))
        assert seen["status"] == "running"
        assert agent._activity_store.peek("s1")["status"] == "idle"
