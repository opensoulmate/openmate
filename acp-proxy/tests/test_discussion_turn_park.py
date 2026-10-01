# -*- coding: utf-8 -*-
"""discussion_engine 讨论turn接入turn级park预算（销账2026-10-01 14:46轮遗留#1）。

背景：turn级park合并轮把park粒度从"单次LLM调用"合并到"整个turn"（kilocode
ask()一次挂起turn），但只接了soulmate TurnLifecycleBus——discussion_engine的
AI群讨论turn没有生命周期，群讨论多轮×多Agent的LLM调用断网时仍按调用各自park：
最坏 total_rounds×agents×MAX_WAIT_S 叠加干等（3轮×3Agent=9×30s≈4.5分钟）+
9条wait记录。

本轮语义（kilocode "park整个turn"逐字对应）：一个讨论任务=一次ask（goal）→
最终reply（discussion_end）的完整turn。run()入口begin_turn(f"discussion:{task_id}")
/finally end_turn，turn预算随contextvar穿过run→_get_agent_response→
asyncio.wait_for子task→chat_stream→maybe_park真实调用链：断网时整个讨论最多
park一次（1条wait记录+turn_fail_fast记账），不再叠加等待。RESUMED不占预算
（新outage新park）；网络恢复后调用照常（fail-fast只在"仍离线"时短路）。

覆盖：
1. turn接线：run()内LLM调用可见turn上下文/turn_id=discussion:{task_id}/finally必清（含异常路径）
2. park预算：一次讨论断网=1条wait记录+其余fail-fast零叠加；agent失败可见（agent_skip）不吞
3. RESUMED不占预算：新outage再park（2条记录诚实粒度）；跨讨论预算独立
4. 回归钉：在线讨论零park零变化；源码接线钉（run/finally/ws_group真实路径）
"""
import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import discussion_engine
from discussion_engine import (
    DiscussionAgent,
    DiscussionOrchestrator,
    DiscussionTask,
)
from utils import network_state

HERE = Path(__file__).parent.parent


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


# ── fixtures：状态清零 + 快速park常量 + 可控probe（沿用test_turn_park_merge范式）──

@pytest.fixture
def clean(monkeypatch, tmp_path):
    """模块级状态清零 + turn上下文清零，防测试互相污染；
    状态目录重定向tmp（park快照/拒绝标志不落data/network_park真实目录）"""
    monkeypatch.setenv("NETWORK_PARK_STATE_DIR", str(tmp_path))
    network_state._waits.clear()
    for k in network_state._stats:
        network_state._stats[k] = 0
    network_state._active_park = None
    network_state.end_turn()
    saved = list(network_state._restore_callbacks)
    network_state._restore_callbacks.clear()
    yield
    network_state._waits.clear()
    for k in network_state._stats:
        network_state._stats[k] = 0
    network_state._active_park = None
    network_state.end_turn()
    network_state._restore_callbacks[:] = saved


def _fast_parks(monkeypatch):
    """park常量调小+_sleep seam假实现（记录时长，不真睡）"""
    monkeypatch.setattr(network_state, "POLL_S", 0.01)
    monkeypatch.setattr(network_state, "RESUME_GRACE_S", 0.02)
    monkeypatch.setattr(network_state, "MAX_WAIT_S", 0.2)
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(float(delay))

    monkeypatch.setattr(network_state, "_sleep", _fake_sleep)
    return sleeps


def _stub_probe(monkeypatch, results):
    """probe_online顺序桩：按序返回，用完重复最后一个；记录调用次数"""
    seq = list(results)
    calls = {"n": 0}

    async def _fake_probe():
        idx = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return bool(seq[idx])

    monkeypatch.setattr(network_state, "probe_online", _fake_probe)
    return calls


def _offline_exc():
    return httpx.ConnectError("All connection attempts failed")


class _RecordingEngine:
    """LLMEngine桩：记录每次chat_stream看到的turn上下文（真实contextvar读取），
    offline模式下照llm_engine消费契约调用network_state.maybe_park（同款三态）。"""

    seen: list = []          # [(turn_id or None, park_outcome or None)]
    offline_mode = False     # 类级开关：True=每条调用先maybe_park（模拟断网）
    park_outcomes: list = []

    def __init__(self, model="", system_prompt="", **kw):
        pass

    async def chat_stream(self, messages=None, cancel_event=None, system_prompt=None, **kw):
        st = network_state._current_turn.get()
        outcome = None
        if _RecordingEngine.offline_mode:
            outcome = await network_state.maybe_park(_offline_exc(), cancel_event)
            _RecordingEngine.park_outcomes.append(outcome)
            if outcome == network_state.ABORTED:
                # llm_engine失败契约形态：可见失败（discussion按"[LLM错误"跳过）
                raise RuntimeError("network offline, park aborted")
        _RecordingEngine.seen.append((st.turn_id if st else None, outcome))
        yield "回复内容"
        return


@pytest.fixture
def engine_stub(monkeypatch):
    _RecordingEngine.seen = []
    _RecordingEngine.park_outcomes = []
    _RecordingEngine.offline_mode = False
    monkeypatch.setattr(discussion_engine, "LLMEngine", _RecordingEngine)
    yield _RecordingEngine
    _RecordingEngine.seen = []
    _RecordingEngine.park_outcomes = []
    _RecordingEngine.offline_mode = False


def _make_task(task_id="task-1", rounds=2):
    return DiscussionTask(group_id="g1", task_id=task_id, goal="测试目标", total_rounds=rounds)


def _make_agents(n=2):
    return [
        DiscussionAgent(agent_id=f"a{i}", name=f"Agent{i}", role=role)
        for i, role in enumerate(["advisor", "executor", "verifier"][:n])
    ]


class _Broadcaster:
    """broadcast/persist收集器（记录事件类型，persist可配置抛错）"""

    def __init__(self, persist_raises=False):
        self.events: list[dict] = []
        self.persist_calls = 0
        self.persist_raises = persist_raises

    async def broadcast(self, group_id, data):
        self.events.append(data)

    def persist(self, **kw):
        self.persist_calls += 1
        if self.persist_raises:
            raise RuntimeError("persist boom")
        return f"msg-{self.persist_calls}"

    def events_of(self, etype):
        return [
            e for e in self.events
            if (e.get("data", {}).get("event") == etype)
            or (etype == "agent_message" and e.get("type") == "agent_message")
            or (etype == "discussion_round" and e.get("type") == "discussion_round")
        ]


# ════════════════════════════════════════════════════════════════
# 1. turn接线（run()入口begin_turn / finally end_turn）
# ════════════════════════════════════════════════════════════════

class TestDiscussionTurnWiring:
    def test_llm_calls_see_discussion_turn_context(self, clean, engine_stub):
        """run()内全部LLM调用（跨wait_for子task）读到turn上下文且turn_id正确"""
        orch = DiscussionOrchestrator()
        bc = _Broadcaster()
        task = _make_task(task_id="tid-42", rounds=2)
        asyncio.run(orch.run(task, _make_agents(2), bc.broadcast, bc.persist))

        assert len(_RecordingEngine.seen) == 4  # 2轮×2Agent
        for turn_id, _ in _RecordingEngine.seen:
            assert turn_id == "discussion:tid-42"
        assert network_state._current_turn.get() is None  # run结束=turn结束

    def test_end_turn_on_error_path(self, clean, engine_stub):
        """persist_fn抛异常→外层except→finally仍清turn上下文（异常路径不泄漏）"""
        orch = DiscussionOrchestrator()
        bc = _Broadcaster(persist_raises=True)
        task = _make_task(task_id="tid-err", rounds=1)
        asyncio.run(orch.run(task, _make_agents(1), bc.broadcast, bc.persist))

        assert network_state._current_turn.get() is None
        assert bc.events_of("discussion_error"), "异常应广播可见错误"

    def test_turn_context_visible_through_wait_for_child_task(self, clean, engine_stub):
        """真实调用形态：_get_agent_response的asyncio.wait_for子task里contextvar继承"""
        seen_inside = {}

        async def _scenario():
            network_state.begin_turn("discussion:ctx")

            async def _child():
                await asyncio.sleep(0)
                st = network_state._current_turn.get()
                seen_inside["turn_id"] = st.turn_id if st else None

            await asyncio.wait_for(_child(), timeout=5)
            network_state.end_turn()

        asyncio.run(_scenario())
        assert seen_inside["turn_id"] == "discussion:ctx"

    def test_source_wiring_pins(self):
        """接线钉：begin_turn在run()入口/finally必有end_turn/ws_group真实调用路径"""
        src = _src("discussion_engine.py")
        assert 'network_state.begin_turn(f"discussion:{task.task_id}")' in src
        assert "network_state.end_turn()" in src
        # finally块内end_turn（清理语义钉）
        finally_idx = src.index("finally:")
        assert src.index("network_state.end_turn()") > finally_idx
        # 生产调用路径：ws_group后台task调用orchestrator.run
        ws_src = _src("ws_group.py")
        assert "_discussion_orchestrator.run(" in ws_src
        assert "asyncio.create_task(" in ws_src
        # network_state接线注释与实现一致（防文档漂移）
        ns_src = _src("utils/network_state.py")
        assert "discussion_engine讨论task=一个turn" in ns_src


# ════════════════════════════════════════════════════════════════
# 2. park预算（一个讨论=一次park，其余fail-fast不叠加）
# ════════════════════════════════════════════════════════════════

class TestDiscussionParkBudget:
    def test_one_park_per_discussion_rest_fail_fast(self, monkeypatch, clean, engine_stub):
        """3Agent×2轮=6条断网调用：1条wait记录+5次fail-fast零叠加+失败全可见"""
        sleeps = _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])  # 持续离线
        _RecordingEngine.offline_mode = True

        orch = DiscussionOrchestrator()
        bc = _Broadcaster()
        task = _make_task(task_id="tid-offline", rounds=2)
        asyncio.run(orch.run(task, _make_agents(3), bc.broadcast, bc.persist))

        # 契约：整个讨论最多park一次
        assert network_state._stats["total_parks"] == 1
        assert len(network_state._waits) == 1
        wait = next(iter(network_state._waits.values()))
        assert wait.turn_id == "discussion:tid-offline"
        assert wait.state == network_state.ABORTED
        assert network_state._stats["turn_fail_fast"] == 5  # 其余5条fail-fast
        # 零叠加干等直接证据：fake sleep只来自单次park（不随调用数增长）
        assert len(sleeps) <= 25  # 单park上限=max_polls(21)+grace，6条各自park会到~130
        # 失败可见不吞：6条全失败→6条agent_skip+讨论正常收尾
        assert len(bc.events_of("agent_skip")) == 6
        assert bc.events_of("discussion_end"), "讨论应正常收尾（可见失败而非挂死）"
        assert bc.persist_calls == 0  # 无内容可持久化

    def test_resumed_not_spend_budget_new_outage_parks_again(self, monkeypatch, clean, engine_stub):
        """RESUMED不占turn预算：恢复续跑后新outage允许再park（kilocode语义）"""
        _fast_parks(monkeypatch)
        # 调用1：probe离线→park→poll探测恢复→RESUMED；调用2：新outage→再park→超时放弃
        _stub_probe(monkeypatch, [False, True, False])
        _RecordingEngine.offline_mode = True

        orch = DiscussionOrchestrator()
        bc = _Broadcaster()
        task = _make_task(task_id="tid-flap", rounds=2)
        asyncio.run(orch.run(task, _make_agents(1), bc.broadcast, bc.persist))

        assert network_state._stats["total_parks"] == 2
        assert network_state._stats["resumed"] == 1
        assert network_state._stats["aborted"] == 1
        assert network_state._stats["turn_fail_fast"] == 0  # RESUMED未花预算
        assert len(network_state._waits) == 2  # 诚实粒度：新outage新记录

    def test_fail_fast_only_when_still_offline(self, monkeypatch, clean, engine_stub):
        """预算用尽但网络恢复→调用照常通过（fail-fast只在仍离线时短路）"""
        _fast_parks(monkeypatch)
        flag = {"online": False}

        async def _probe():
            return flag["online"]

        monkeypatch.setattr(network_state, "probe_online", _probe)

        class _SwitchingEngine(_RecordingEngine):
            async def chat_stream(self, messages=None, cancel_event=None, system_prompt=None, **kw):
                st = network_state._current_turn.get()
                outcome = await network_state.maybe_park(_offline_exc(), cancel_event)
                _RecordingEngine.park_outcomes.append(outcome)
                if outcome == network_state.ABORTED:
                    flag["online"] = True  # park放弃后网络恢复
                    raise RuntimeError("network offline, park aborted")
                _RecordingEngine.seen.append((st.turn_id if st else None, outcome))
                yield "回复内容"

        monkeypatch.setattr(discussion_engine, "LLMEngine", _SwitchingEngine)

        orch = DiscussionOrchestrator()
        bc = _Broadcaster()
        task = _make_task(task_id="tid-recov", rounds=1)
        asyncio.run(orch.run(task, _make_agents(2), bc.broadcast, bc.persist))

        assert network_state._stats["total_parks"] == 1
        assert network_state._stats["turn_fail_fast"] == 0  # 调用2在线未fail-fast
        outcomes = _RecordingEngine.park_outcomes
        assert outcomes[0] == network_state.ABORTED
        assert outcomes[1] == network_state.NOT_OFFLINE
        assert len(bc.events_of("agent_message")) == 1  # 第2个Agent正常出消息

    def test_budget_independent_across_discussions(self, monkeypatch, clean, engine_stub):
        """跨讨论预算独立：上一个讨论park放弃不影响下一个讨论（新turn新预算）"""
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])
        _RecordingEngine.offline_mode = True

        orch = DiscussionOrchestrator()
        for tid in ("tid-A", "tid-B"):
            bc = _Broadcaster()
            asyncio.run(
                orch.run(_make_task(task_id=tid, rounds=1), _make_agents(1),
                         bc.broadcast, bc.persist)
            )

        assert network_state._stats["total_parks"] == 2  # 各自park一次（新预算）
        assert network_state._stats["turn_fail_fast"] == 0
        assert {w.turn_id for w in network_state._waits.values()} == {
            "discussion:tid-A", "discussion:tid-B"
        }


# ════════════════════════════════════════════════════════════════
# 3. 回归（在线讨论零park零变化）
# ════════════════════════════════════════════════════════════════

class TestOnlineRegression:
    def test_online_discussion_unchanged(self, clean, engine_stub):
        """在线讨论：内容广播/持久化/历史上下文逐字不变，零park零wait"""
        orch = DiscussionOrchestrator()
        bc = _Broadcaster()
        task = _make_task(task_id="tid-ok", rounds=2)
        asyncio.run(orch.run(task, _make_agents(2), bc.broadcast, bc.persist))

        assert network_state._stats["total_parks"] == 0
        assert len(network_state._waits) == 0
        assert bc.persist_calls == 4
        assert len(bc.events_of("agent_message")) == 4
        assert len(bc.events_of("discussion_round")) == 4  # 2轮×start/end
        assert bc.events_of("discussion_start") and bc.events_of("discussion_end")

    def test_no_turn_context_calls_keep_per_call_behavior(self, monkeypatch, clean):
        """未接线面（title生成/摘要压缩等无turn上下文调用）旧行为逐字不变：
        两次断网调用=2条wait记录（回归钉，见test_turn_park_merge同款契约）"""
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])

        async def _scenario():
            for _ in range(2):
                await network_state.maybe_park(_offline_exc())

        asyncio.run(_scenario())
        assert len(network_state._waits) == 2  # 无turn上下文=按调用park（旧行为）
        assert network_state._stats["turn_fail_fast"] == 0
