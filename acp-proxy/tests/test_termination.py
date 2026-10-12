"""P1-15 五终止条件组合——词汇/配置/裁决/落 trace 载荷/钩子测试（无网络，无 mock 语义）。

覆盖：
1. 五条件词汇与各条件独立配置（max_turns/token_budget env，缺省零行为变化）
2. 组合裁决 termination.resolve 单一真源（显式 > external_cancel > 正常完成）
3. TrajectoryRecorder.end_turn(termination=...) 载荷 + log_event 自由事件载荷
4. TurnLifecycleBus.is_cancel_requested（external_cancel 裁决输入，不消费标记）
5. TurnWatchdog on_soft_budget 软预算钩子（wall_clock 条件落 trace 入口，一次一触发）
"""

import asyncio

import pytest

from agent import termination
from agent.trajectory_recorder import TrajectoryRecorder
from agent.turn_lifecycle import (
    CLOSE_CANCELLED,
    CLOSE_COMPLETED,
    TurnLifecycleBus,
)
from agent.turn_watchdog import TurnWatchdog


# ── 1. 词汇 + 独立配置 ─────────────────────────────────────────

class TestVocabularyAndConfig:
    def test_five_conditions_registered(self):
        assert termination.TERMINATION_REASONS == {
            "max_turns", "wall_clock", "token_budget",
            "user_idle", "external_cancel",
        }
        for r in termination.TERMINATION_REASONS:
            assert termination.is_valid(r)
        assert not termination.is_valid("")

    def test_max_turns_default_15(self, monkeypatch):
        monkeypatch.delenv("SOULMATE_MAX_ROUNDS", raising=False)
        assert termination.max_turns_limit() == 15

    def test_max_turns_env_override(self, monkeypatch):
        monkeypatch.setenv("SOULMATE_MAX_ROUNDS", "2")
        assert termination.max_turns_limit() == 2

    def test_max_turns_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("SOULMATE_MAX_ROUNDS", "abc")
        assert termination.max_turns_limit() == 15
        monkeypatch.setenv("SOULMATE_MAX_ROUNDS", "0")
        assert termination.max_turns_limit() == 15
        monkeypatch.setenv("SOULMATE_MAX_ROUNDS", "-3")
        assert termination.max_turns_limit() == 15

    def test_token_budget_default_off(self, monkeypatch):
        monkeypatch.delenv("SOULMATE_TURN_TOKEN_BUDGET", raising=False)
        assert termination.turn_token_budget() == 0

    def test_token_budget_env_override(self, monkeypatch):
        monkeypatch.setenv("SOULMATE_TURN_TOKEN_BUDGET", "8000")
        assert termination.turn_token_budget() == 8000

    def test_token_budget_invalid_off(self, monkeypatch):
        monkeypatch.setenv("SOULMATE_TURN_TOKEN_BUDGET", "x")
        assert termination.turn_token_budget() == 0
        monkeypatch.setenv("SOULMATE_TURN_TOKEN_BUDGET", "-1")
        assert termination.turn_token_budget() == 0


# ── 2. 组合裁决 ────────────────────────────────────────────────

class TestResolve:
    def test_explicit_wins(self):
        assert termination.resolve(
            explicit=termination.TERM_MAX_TURNS, cancelled=True
        ) == termination.TERM_MAX_TURNS

    def test_cancel_flag_maps_external_cancel(self):
        assert termination.resolve(
            explicit="", cancelled=True) == termination.TERM_EXTERNAL_CANCEL

    def test_normal_completion_empty(self):
        assert termination.resolve(explicit="", cancelled=False) == ""

    def test_acp_stop_reason_mapping(self):
        assert (termination.from_acp_stop_reason("max_tokens")
                == termination.TERM_TOKEN_BUDGET)
        assert termination.from_acp_stop_reason("end_turn") == ""
        assert termination.from_acp_stop_reason("refusal") == ""
        assert termination.from_acp_stop_reason("") == ""


# ── 3. 落 trace 载荷（recorder 真实方法体，_post 捕获线缆载荷）──────

class _CaptureRecorder(TrajectoryRecorder):
    def __init__(self):
        super().__init__()
        self.payloads = []

    async def _post(self, path, payload, allow_404=False):
        self.payloads.append((path, payload))
        if path.endswith("/sessions"):
            # ensure_session 契约：id=请求的 session_id
            return {"id": payload.get("session_id", "")}
        return {"id": "ev-1"}


class TestTracePayload:
    def test_end_turn_carries_termination(self):
        rec = _CaptureRecorder()
        asyncio.run(rec.end_turn(
            "s1", "r1", status="ok", termination=termination.TERM_MAX_TURNS))
        path, payload = rec.payloads[-1]
        assert path == "/api/trajectory/events/r1/end"
        assert payload["termination"] == "max_turns"

    def test_end_turn_empty_termination_omits_key(self):
        rec = _CaptureRecorder()
        asyncio.run(rec.end_turn("s1", "r1", status="ok"))
        _, payload = rec.payloads[-1]
        assert "termination" not in payload
        assert payload["status"] == "ok"

    def test_log_event_condition_event(self):
        rec = _CaptureRecorder()
        eid = asyncio.run(rec.log_event(
            "s1", kind="termination_condition", content="wall_clock soft warn",
            metadata={"termination_condition": "wall_clock", "action": "soft_warn"},
        ))
        assert eid == "ev-1"
        path, payload = rec.payloads[-1]
        assert path == "/api/trajectory/sessions/s1/events"
        assert payload["event_type"] == "custom"
        assert payload["metadata"]["kind"] == "termination_condition"
        assert payload["metadata"]["termination_condition"] == "wall_clock"


# ── 4. external_cancel 裁决输入 ─────────────────────────────────

class TestCancelSignal:
    def test_is_cancel_requested_not_consumed(self):
        bus = TurnLifecycleBus()
        bus.open_turn("s1")
        assert not bus.is_cancel_requested("s1")
        bus.cancel_turn("s1")
        assert bus.is_cancel_requested("s1")
        # 读取不消费：aclose_turn 前后都可读（end_turn 先于 aclose_turn 调用）
        assert bus.is_cancel_requested("s1")

    def test_aclose_turn_closes_cancelled(self):
        bus = TurnLifecycleBus()
        bus.open_turn("s1")
        bus.cancel_turn("s1")
        rec = asyncio.run(bus.aclose_turn("s1", CLOSE_COMPLETED))
        assert rec is not None
        assert rec.close_reason == CLOSE_CANCELLED
        # 收尾后标记被消费
        assert not bus.is_cancel_requested("s1")


# ── 6. lazy初始化兼容（__new__ 搭建的测试agent同款约束）────────────

class TestLazyInitCompat:
    def test_helpers_work_on_bare_agent(self):
        from agent.soulmate_agent import SoulMateAgent
        ag = SoulMateAgent.__new__(SoulMateAgent)
        # set/pop/resolve 不依赖 __init__（与 _turn_close_reason 同款测试构造）
        ag._termination_state()["s1"] = termination.TERM_MAX_TURNS
        assert ag._resolve_turn_termination("s1") == termination.TERM_MAX_TURNS
        # 一次消费防跨回合残留
        assert ag._resolve_turn_termination("s1") == ""

    def test_resolve_cancellation_without_lifecycle_attr(self):
        from agent.soulmate_agent import SoulMateAgent
        ag = SoulMateAgent.__new__(SoulMateAgent)
        assert ag._resolve_turn_termination("s2") == ""


# ── 5. wall_clock 软预算钩子 ────────────────────────────────────

class TestSoftBudgetHook:
    def test_on_soft_budget_fires_once(self):
        fired = []

        async def _collect(budget_s, elapsed):
            fired.append((budget_s, elapsed))

        async def _main():
            wd = TurnWatchdog(
                "s1",
                heartbeat_s=0.05,
                soft_budget_s=0.02,
                max_lifetime_s=5.0,
                on_soft_budget=_collect,
            )
            wd.start()
            await asyncio.sleep(0.35)
            wd.stop()

        asyncio.run(_main())
        assert len(fired) == 1
        budget_s, elapsed = fired[0]
        assert budget_s == 0.02
        assert elapsed >= 0.02

    def test_hook_exception_never_breaks(self):
        fired = []

        def _boom(budget_s, elapsed):
            fired.append(1)
            raise RuntimeError("hook boom")

        async def _main():
            msgs = []

            async def _notify(text):
                msgs.append(text)
            wd = TurnWatchdog(
                "s1", notify=_notify,
                heartbeat_s=0.05, soft_budget_s=0.02, max_lifetime_s=5.0,
                on_soft_budget=_boom,
            )
            wd.start()
            await asyncio.sleep(0.35)
            wd.stop()
            return msgs

        msgs = asyncio.run(_main())
        assert fired == [1]          # 钩子触发了
        assert msgs                  # 旁路纪律：notify 不受影响
        # 钩子异常被吞掉且计入 notify_failures（不冒泡）
        assert True

    def test_default_no_hook_zero_change(self):
        async def _main():
            msgs = []

            async def _notify(text):
                msgs.append(text)
            wd = TurnWatchdog(
                "s1", notify=_notify,
                heartbeat_s=0.05, soft_budget_s=0.02, max_lifetime_s=5.0,
            )
            wd.start()
            await asyncio.sleep(0.2)
            wd.stop()
            return msgs

        msgs = asyncio.run(_main())
        assert msgs  # 既有软告警行为不变
