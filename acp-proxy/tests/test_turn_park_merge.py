# -*- coding: utf-8 -*-
"""turn级park合并专项测试（kilocode "park整个turn"语义）。

背景（2026-09-29 09:30轮遗留"park粒度是'一次调用'不是'一个turn'"销账）：
移植初版park粒度=单次LLM调用——一个逻辑回合的plan/judge/回复/摘要多条调用
各自park，断网回合最坏叠加N×MAX_WAIT_S干等、产生N条wait记录。kilocode park的
单位是整个turn（ask()一次挂起turn）。

覆盖：
1. turn上下文：begin_turn/end_turn（TurnLifecycleBus turn.open/close接线点）——
   contextvar跨await/子task继承、turn_id进wait记录
2. turn预算fail-fast：本回合park放弃后同turn后续调用ABORTED不再叠加等待
   （sleep零新增=不干等的直接证据）；RESUMED不占预算（新outage允许再park）
3. attach合并：同turn并发调用共享一个park会话（一条wait记录+merged_calls）
4. 无turn上下文旧行为逐字不变（按调用park，回归钉）
5. LLMEngine集成：一个turn内3条chat()调用断网→只park一次+2次fail-fast
6. 接线钉：soulmate turn.open/close订阅begin_turn/end_turn + 消费面零改动
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
import pytest

from agent.llm_engine import LLMEngine
from agent import turn_lifecycle
from utils import network_state
from utils.network_state import ABORTED, RESUMED

HERE = Path(__file__).parent.parent


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


# ── fixtures：状态清零 + 快速park常量 + 可控probe（沿用test_network_offline_park范式）──


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


def _fast_parks(monkeypatch, yielding=False):
    """park常量调小+_sleep seam假实现（记录时长，不真睡）。

    yielding=True：fake sleep额外await真实sleep(0)强制让出事件循环——
    attach并发测试需要两个协程真正交错（纯同步fake下协程不交错，
    后者看不到进行中的park会话）。
    """
    monkeypatch.setattr(network_state, "POLL_S", 0.01)
    monkeypatch.setattr(network_state, "RESUME_GRACE_S", 0.02)
    monkeypatch.setattr(network_state, "MAX_WAIT_S", 0.2)
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(float(delay))
        if yielding:
            await asyncio.sleep(0)

    monkeypatch.setattr(network_state, "_sleep", _fake_sleep)
    return sleeps


def _stub_probe(monkeypatch, results, yielding=False):
    """probe_online顺序桩：按序返回，用完重复最后一个；记录调用次数"""
    seq = list(results)
    calls = {"n": 0}

    async def _fake_probe():
        idx = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        if yielding:
            await asyncio.sleep(0)
        return bool(seq[idx])

    monkeypatch.setattr(network_state, "probe_online", _fake_probe)
    return calls


def _offline_exc():
    return httpx.ConnectError("All connection attempts failed")


# ════════════════════════════════════════════════════════════════
# 1. turn上下文（begin_turn/end_turn——TurnLifecycleBus接线点语义）
# ════════════════════════════════════════════════════════════════


class TestTurnScope:
    def test_begin_end_turn_sets_and_clears(self, clean):
        assert network_state._current_turn.get() is None
        network_state.begin_turn("turn_abc")
        st = network_state._current_turn.get()
        assert st is not None and st.turn_id == "turn_abc" and st.aborted is False
        network_state.end_turn()
        assert network_state._current_turn.get() is None

    def test_begin_turn_resets_budget(self, clean):
        network_state.begin_turn("t1")
        network_state._current_turn.get().aborted = True
        network_state.begin_turn("t2")  # 新turn=新预算
        assert network_state._current_turn.get().aborted is False

    def test_contextvar_survives_awaits_and_child_tasks(self, clean):
        # 真实流量形态：open_turn后LLM调用在await链/子task里读到turn上下文
        async def _scenario():
            network_state.begin_turn("turn_x")

            async def _child():
                await asyncio.sleep(0)
                st = network_state._current_turn.get()
                return st.turn_id if st else None

            await asyncio.sleep(0)
            return await asyncio.create_task(_child())

        assert asyncio.run(_scenario()) == "turn_x"

    def test_bus_open_close_drives_context(self, clean):
        # TurnLifecycleBus真实机制：open_turn同步触发订阅器→contextvar对后续
        # await可见；aclose_turn触发close订阅器→清除（soulmate接线的等价验证）
        bus = turn_lifecycle.TurnLifecycleBus()
        bus.subscribe(
            turn_lifecycle.EVENT_TURN_OPEN,
            lambda record: network_state.begin_turn(record.turn_id),
        )
        bus.subscribe(
            turn_lifecycle.EVENT_TURN_CLOSE,
            lambda record: network_state.end_turn(),
        )

        async def _scenario():
            record = bus.open_turn("s1", user_text_len=3)
            await asyncio.sleep(0)
            seen = network_state._current_turn.get()
            await bus.aclose_turn("s1", turn_lifecycle.CLOSE_COMPLETED)
            return record.turn_id, (seen.turn_id if seen else None), network_state._current_turn.get()

        tid, seen_id, after = asyncio.run(_scenario())
        assert seen_id == tid  # open后await链里可见，且turn_id=bus记录的turn_id
        assert after is None  # close后清除


# ════════════════════════════════════════════════════════════════
# 2. turn预算fail-fast（本回合park放弃后不再叠加等待）
# ════════════════════════════════════════════════════════════════


class TestTurnBudgetFailFast:
    def test_abort_then_fail_fast_no_stacked_wait(self, monkeypatch, clean):
        # 同turn第2次遇离线：不再park不干等（sleep零新增=直接证据），fail-fast
        sleeps = _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])  # 一直离线
        network_state.begin_turn("turn_t1")
        r1 = asyncio.run(network_state.maybe_park(_offline_exc()))
        sleeps_after_first = len(sleeps)
        assert r1 == ABORTED
        r2 = asyncio.run(network_state.maybe_park(_offline_exc()))
        r3 = asyncio.run(network_state.maybe_park(_offline_exc()))
        assert r2 == ABORTED and r3 == ABORTED
        assert len(sleeps) == sleeps_after_first  # fail-fast零睡眠零干等
        assert len(network_state.list_waits()) == 1  # 一个turn一条wait记录
        assert network_state.stats()["total_parks"] == 1
        assert network_state.stats()["turn_fail_fast"] == 2

    def test_fail_fast_requires_prior_abort(self, monkeypatch, clean):
        # turn预算未spent（还没park过）→第一次照常park，不误伤
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])  # 第一次park即恢复
        network_state.begin_turn("turn_t2")
        r1 = asyncio.run(network_state.maybe_park(_offline_exc()))
        assert r1 == RESUMED
        assert network_state.stats()["turn_fail_fast"] == 0

    def test_resumed_does_not_spend_budget_new_outage_reparks(self, monkeypatch, clean):
        # RESUMED不占turn预算：恢复续跑后再次遇新断网允许再park
        # （kilocode "reply后再次错误再ask()"同语义——RESUMED≠turn已等待放弃）
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True, False, True])
        network_state.begin_turn("turn_t3")
        r1 = asyncio.run(network_state.maybe_park(_offline_exc()))
        r2 = asyncio.run(network_state.maybe_park(_offline_exc()))
        assert r1 == RESUMED and r2 == RESUMED
        assert len(network_state.list_waits()) == 2  # 两个outage两条记录（诚实粒度）
        assert network_state.stats()["turn_fail_fast"] == 0

    def test_fail_fast_scoped_to_turn(self, monkeypatch, clean):
        # turn结束后新turn有新预算：再次离线照常park（不被上一turn的aborted误伤）
        sleeps = _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])
        network_state.begin_turn("turn_a")
        assert asyncio.run(network_state.maybe_park(_offline_exc())) == ABORTED
        network_state.end_turn()
        network_state.begin_turn("turn_b")
        assert asyncio.run(network_state.maybe_park(_offline_exc())) == ABORTED
        assert len(network_state.list_waits()) == 2  # 各turn各一条
        assert network_state.stats()["turn_fail_fast"] == 0

    def test_cancel_abort_also_spends_budget(self, monkeypatch, clean):
        # 取消park（cancel_event）也标记turn预算spent——turn正在收尾，不叠加等待
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])
        ev = asyncio.Event()

        async def _scenario():
            ev.set()  # 已请求取消：park首轮即aborted "cancelled"
            return await network_state.maybe_park(_offline_exc(), ev)

        network_state.begin_turn("turn_c")
        assert asyncio.run(_scenario()) == ABORTED
        assert network_state.list_waits()[0]["error"] == "cancelled"
        r2 = asyncio.run(network_state.maybe_park(_offline_exc()))
        assert r2 == ABORTED
        assert network_state.stats()["turn_fail_fast"] == 1

    def test_non_disconnect_bypasses_budget(self, monkeypatch, clean):
        # 非断连错误不受turn预算影响（NOT_OFFLINE判定在预算检查之前）
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [True])
        network_state.begin_turn("turn_d")
        network_state._current_turn.get().aborted = True  # 预算已spent
        r = asyncio.run(network_state.maybe_park(ValueError("bad json")))
        assert r == "not_offline"

    def test_endpoint_fault_bypasses_budget(self, monkeypatch, clean):
        # 探测在线=端点级故障不park也不吃turn预算
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [True])
        network_state.begin_turn("turn_e")
        network_state._current_turn.get().aborted = True
        r = asyncio.run(network_state.maybe_park(_offline_exc()))
        assert r == "not_offline"


# ════════════════════════════════════════════════════════════════
# 3. attach合并（同turn并发调用共享一个park会话）
# ════════════════════════════════════════════════════════════════


class TestAttachMerge:
    def test_concurrent_calls_share_one_park_record(self, monkeypatch, clean):
        # 两个并发调用同时遇离线：一个park会话+一条wait记录，另一个attach共享
        _fast_parks(monkeypatch, yielding=True)
        _stub_probe(monkeypatch, [False, False, True], yielding=True)

        async def _scenario():
            return await asyncio.gather(
                network_state.maybe_park(_offline_exc()),
                network_state.maybe_park(_offline_exc()),
            )

        network_state.begin_turn("turn_att")
        r1, r2 = asyncio.run(_scenario())
        assert r1 == RESUMED and r2 == RESUMED  # attach拿到同一会话结局
        assert len(network_state.list_waits()) == 1
        assert network_state.stats()["total_parks"] == 1
        assert network_state.list_waits()[0]["merged_calls"] == 1

    def test_attach_shares_abort_outcome(self, monkeypatch, clean):
        # 会话aborted时attach的调用同样拿到ABORTED（不各park各等30s）
        _fast_parks(monkeypatch, yielding=True)
        _stub_probe(monkeypatch, [False], yielding=True)

        async def _scenario():
            return await asyncio.gather(
                network_state.maybe_park(_offline_exc()),
                network_state.maybe_park(_offline_exc()),
            )

        network_state.begin_turn("turn_att2")
        r1, r2 = asyncio.run(_scenario())
        assert r1 == ABORTED and r2 == ABORTED
        assert len(network_state.list_waits()) == 1
        assert network_state.stats()["total_parks"] == 1
        assert network_state.stats()["aborted"] == 1  # 只记一次放弃（非双份）

    def test_sequential_after_session_done_does_not_attach(self, monkeypatch, clean):
        # 会话结束后到达的调用不attach（done会话已清），走turn预算/新park
        _fast_parks(monkeypatch, yielding=True)
        _stub_probe(monkeypatch, [False, True], yielding=True)
        network_state.begin_turn("turn_att3")
        assert asyncio.run(network_state.maybe_park(_offline_exc())) == RESUMED
        # RESUMED不占预算→新outage再park（不attach旧会话）
        _stub_probe(monkeypatch, [False, True], yielding=True)
        assert asyncio.run(network_state.maybe_park(_offline_exc())) == RESUMED
        assert len(network_state.list_waits()) == 2


# ════════════════════════════════════════════════════════════════
# 4. 无turn上下文旧行为逐字不变（回归钉）+ 观测面
# ════════════════════════════════════════════════════════════════


class TestBackwardCompat:
    def test_no_turn_context_keeps_per_call_park(self, monkeypatch, clean):
        # 无begin_turn（title生成/摘要压缩/discussion等）：按调用park旧行为不变
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])
        r1 = asyncio.run(network_state.maybe_park(_offline_exc()))
        r2 = asyncio.run(network_state.maybe_park(_offline_exc()))
        assert r1 == ABORTED and r2 == ABORTED
        assert len(network_state.list_waits()) == 2  # 调用粒度两条（旧行为）
        assert network_state.stats()["turn_fail_fast"] == 0

    def test_wait_record_carries_turn_id(self, monkeypatch, clean):
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])
        network_state.begin_turn("turn_obs")
        asyncio.run(network_state.maybe_park(_offline_exc()))
        w = network_state.list_waits()[0]
        assert w["turn_id"] == "turn_obs"
        assert w["merged_calls"] == 0
        # 无turn上下文记录turn_id=None
        network_state.end_turn()
        _stub_probe(monkeypatch, [False, True])
        asyncio.run(network_state.maybe_park(_offline_exc()))
        w2 = [x for x in network_state.list_waits() if x["turn_id"] is None]
        assert len(w2) == 1

    def test_stats_shape_has_turn_fail_fast(self, clean):
        st = network_state.stats()
        assert "turn_fail_fast" in st
        assert st["turn_fail_fast"] == 0


# ════════════════════════════════════════════════════════════════
# 5. LLMEngine集成（fake传输——一个turn内多条调用只park一次）
# ════════════════════════════════════════════════════════════════


def _engine():
    return LLMEngine(api_key="k", base_url="http://fake/v1", model="mimo-test")


def _patch_llm_sleep(monkeypatch):
    waits: list[float] = []

    async def _fake_sleep(delay):
        waits.append(float(delay))

    monkeypatch.setattr("agent.llm_engine.asyncio.sleep", _fake_sleep)
    return waits


class _FakeResp:
    def __init__(self, content=None, status_code=200, body=""):
        self.status_code = status_code
        self._body = body
        self.headers = {}
        self._data = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

    def json(self):
        return self._data

    @property
    def text(self):
        return self._body


def _install_post_client(monkeypatch, responses: list) -> dict:
    state = {"calls": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            r = responses[idx]
            return r() if callable(r) else r

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _FakeClient)
    return state


class TestLLMEngineTurnMerge:
    def test_three_chat_calls_one_park_per_turn(self, monkeypatch, clean):
        # 真实形态：一个turn里plan/judge/回复/摘要多条chat()调用遇断网——
        # 旧=每条调用各自park叠加干等；新=整个turn只park一次+fail-fast
        _patch_llm_sleep(monkeypatch)
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])  # 一直离线

        def _raise():
            raise httpx.ConnectError("All connection attempts failed")

        _install_post_client(monkeypatch, [_raise])
        network_state.begin_turn("turn_llm")
        outs = [
            asyncio.run(_engine().chat([{"role": "user", "content": p}]))
            for p in ("plan", "judge", "reply")
        ]
        assert all(o.startswith("[LLM错误:") for o in outs)  # 失败可见契约不变
        waits = network_state.list_waits()
        assert len(waits) == 1  # 一个turn一条wait记录（合并）
        assert waits[0]["turn_id"] == "turn_llm"
        assert network_state.stats()["total_parks"] == 1
        assert network_state.stats()["turn_fail_fast"] >= 2  # 后续调用fail-fast
        assert waits[0]["state"] == ABORTED

    def test_same_calls_without_turn_keep_old_behavior(self, monkeypatch, clean):
        # 回归钉：无turn上下文=按调用park（旧行为逐字不变，可多条记录）
        _patch_llm_sleep(monkeypatch)
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])

        def _raise():
            raise httpx.ConnectError("All connection attempts failed")

        _install_post_client(monkeypatch, [_raise])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out.startswith("[LLM错误:")
        assert len(network_state.list_waits()) >= 1
        assert network_state.stats()["turn_fail_fast"] == 0


# ════════════════════════════════════════════════════════════════
# 6. 接线钉（soulmate turn.open/close订阅 + 消费面零改动）
# ════════════════════════════════════════════════════════════════


class TestWiringPins:
    def test_soulmate_subscribes_begin_end_turn(self):
        src = _src("agent/soulmate_agent.py")
        assert "network_state.begin_turn(record.turn_id)" in src
        assert "network_state.end_turn()" in src
        # 订阅挂的是真实生命周期事件
        assert src.count("turn_lifecycle.EVENT_TURN_OPEN") >= 1
        assert src.count("turn_lifecycle.EVENT_TURN_CLOSE") >= 2  # 记忆采集+park清场

    def test_maybe_park_consumers_unchanged(self):
        # 消费面零改动：llm_engine三处+llm_transport一处（语义不变只加合并）；
        # 14.5 catch 面扩到 DecodingError，钉随规格更新
        eng = _src("agent/llm_engine.py")
        assert eng.count("network_state.maybe_park(") == 3
        assert eng.count("except (httpx.TransportError, httpx.DecodingError) as e:") == 3
        tr = _src("utils/llm_transport.py")
        assert tr.count("network_state.maybe_park(") >= 1

    def test_network_state_merge_symbols_present(self):
        src = _src("utils/network_state.py")
        for needle in (
            "def begin_turn(", "def end_turn(", "class TurnParkState",
            "turn_fail_fast", "merged_calls", "async def _run_park(",
            "_current_turn.get()",
        ):
            assert needle in src, needle
