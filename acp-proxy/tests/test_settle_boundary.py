# -*- coding: utf-8 -*-
"""Pi 取长补短 #2——settle 收尾边界（agent_before_settle 追加+continue /
agent_settled 才算彻底结束）。

与已落 #3 三段式修复天然成环：「产出→校验→不过退回同会话修复」缺的就是
settle 挂点——本测试钉住 SettleGate 语义（continue 有界/事件/幂等 settled）、
make_repair_hook×RepairLoop 成环、以及真实工具循环（_run_llm_with_tools）里的
接线（钩子 continue → 追加 entry 再发一轮模型请求）。

通用收尾治理能力（非投标专用）。
"""
import inspect

import pytest

from agent import repair_loop, turn_lifecycle
from agent.soulmate_agent import SoulMateAgent
from agent.turn_lifecycle import (
    EVENT_SETTLE_BEFORE,
    EVENT_SETTLED,
    SettleGate,
    SettleRecord,
    TurnLifecycleBus,
    make_repair_hook,
)
from tests.test_loop_guard_wiring import _wire_agent
from tests.test_steering import run


def _continue_hook(entry="请修复 X", reason="校验不过", name="hook", times=10**6):
    """continue 钩子替身：最多 continue times 次，之后放行（模拟校验推进）。"""
    calls = []

    def hook(ctx):
        calls.append(ctx)
        if len([c for c in calls]) <= times:
            return {"continue": True, "entry": entry, "reason": reason}
        return None

    hook.__name__ = name
    return hook, calls


# ── ① SettleGate 核心语义 ─────────────────────────────────────────

class TestSettleGateBasic:
    def test_zero_hooks_passes_immediately(self):
        g = SettleGate(session_id="s1", max_continues=2)
        out = g.before_settle({})
        assert out["settled"] is True and out["entry"] == "" and out["passes"] == 0

    def test_continue_appends_entry_and_counts(self):
        hook, calls = _continue_hook()
        g = SettleGate(session_id="s1", hooks=[hook], max_continues=2)
        out1 = g.before_settle({})
        assert out1["settled"] is False and out1["entry"] == "请修复 X"
        assert out1["passes"] == 1 and out1["hook"] == "hook"
        out2 = g.before_settle({})
        assert out2["settled"] is False and out2["passes"] == 2

    def test_continue_bounded_forces_settle_with_visible_reason(self):
        hook, _ = _continue_hook()
        g = SettleGate(hooks=[hook], max_continues=2)
        g.before_settle({})
        g.before_settle({})
        out3 = g.before_settle({})                      # 第3次：超限强制放行
        assert out3["settled"] is True
        assert "上限 2" in out3["reason"] and "防打转" in out3["reason"]

    def test_max_continues_zero_passes_with_reason(self):
        hook, _ = _continue_hook()
        g = SettleGate(hooks=[hook], max_continues=0)
        out = g.before_settle({})
        assert out["settled"] is True and "上限 0" in out["reason"]

    def test_negative_max_continues_rejected(self):
        with pytest.raises(ValueError):
            SettleGate(max_continues=-1)

    def test_continue_without_entry_is_pass(self):
        def hook(ctx):
            return {"continue": True, "entry": "   ", "reason": "空entry不续轮"}

        g = SettleGate(hooks=[hook], max_continues=2)
        out = g.before_settle({})
        assert out["settled"] is True                  # 空 entry 不消耗 continue

    def test_multiple_continue_hooks_merge_entries(self):
        h1 = lambda ctx: {"continue": True, "entry": "修A"}
        h2 = lambda ctx: {"continue": True, "entry": "修B"}
        h2.__name__ = "h2"
        g = SettleGate(hooks=[h1, h2], max_continues=2)
        out = g.before_settle({})
        assert out["settled"] is False
        assert out["entry"] == "修A\n\n修B"            # 合并追加一次续轮
        assert out["passes"] == 1                      # 一批只消耗一次 continue

    def test_hook_exception_does_not_block_but_visible(self):
        def bad(ctx):
            raise RuntimeError("boom")

        ok_hook, _ = _continue_hook()
        g = SettleGate(hooks=[bad, ok_hook], max_continues=2)
        out = g.before_settle({})
        assert out["settled"] is False                 # 坏钩子不破坏宿主流
        assert out["entry"] == "请修复 X"
        out2 = g.before_settle({})                     # 坏钩子异常记账在 reason
        out3 = g.before_settle({})
        assert out3["settled"] is True and "异常" in out3["reason"]

    def test_ctx_gets_session_and_passes(self):
        seen = {}

        def hook(ctx):
            seen.update(ctx)
            return None

        g = SettleGate(session_id="sX", hooks=[hook], max_continues=1)
        g.before_settle({"response": "resp"})
        assert seen["session_id"] == "sX" and seen["passes"] == 0
        assert seen["response"] == "resp"


class TestMarkSettled:
    def test_settled_hooks_run_once_and_idempotent(self):
        runs = []
        g = SettleGate(session_id="s1", on_settled=[lambda ctx: runs.append(ctx)])
        out1 = g.mark_settled()
        out2 = g.mark_settled()
        assert out1["kind"] == EVENT_SETTLED and out1["settled"] is True
        assert out2 is None                            # 幂等：第二次无事
        assert len(runs) == 1

    def test_settled_hook_exception_isolated(self):
        def bad(ctx):
            raise RuntimeError("boom")

        g = SettleGate(on_settled=[bad])
        out = g.mark_settled()
        assert out["kind"] == EVENT_SETTLED            # 收尾不被钩子异常打断

    def test_is_settled_flag(self):
        g = SettleGate()
        assert g.is_settled is False
        g.mark_settled()
        assert g.is_settled is True


class TestSettleEvents:
    def test_events_published_on_bus(self):
        bus = TurnLifecycleBus()
        seen = []
        bus.subscribe(EVENT_SETTLE_BEFORE, lambda r: seen.append(r))
        bus.subscribe(EVENT_SETTLED, lambda r: seen.append(r))
        hook, _ = _continue_hook()
        g = SettleGate(session_id="s1", bus=bus, hooks=[hook], max_continues=1)
        g.before_settle({})
        g.before_settle({})
        g.mark_settled()
        kinds = [r.kind for r in seen]
        assert kinds == [EVENT_SETTLE_BEFORE, EVENT_SETTLE_BEFORE, EVENT_SETTLED]
        assert seen[0].settled is False and seen[1].settled is True
        assert seen[0].entry == "请修复 X"

    def test_event_subscriber_failure_isolated(self):
        bus = TurnLifecycleBus()
        seen = []
        bus.subscribe(EVENT_SETTLE_BEFORE, lambda r: (_ for _ in ()).throw(RuntimeError("x")))
        bus.subscribe(EVENT_SETTLE_BEFORE, lambda r: seen.append(r))
        g = SettleGate(bus=bus)
        g.before_settle({})                            # 坏订阅器不破坏事件流
        assert len(seen) == 1

    def test_record_as_dict_shape(self):
        r = SettleRecord(session_id="s1", kind=EVENT_SETTLED, settled=True)
        d = r.as_dict()
        assert set(d) == {"session_id", "kind", "settled", "entry",
                          "reason", "hook", "passes", "meta"}


# ── ② settle × 三段式修复成环（#2×#3：产出→校验→不过退回同会话修复）──

class TestRepairHookLoop:
    def _loop_until_settled(self, gate, max_passes=10):
        """模拟工具循环的 settle 消费（continue 则再跑一轮），返回追加的 entries。"""
        entries = []
        for _ in range(max_passes):
            out = gate.before_settle({})
            if out["settled"]:
                return entries, out
            entries.append(out["entry"])
        raise AssertionError("settle 环没在上限内收敛")

    def test_full_repair_circle(self):
        """校验不过→repair 退回同会话→修掉问题→accept 放行（成环实证）。"""
        repair = repair_loop.RepairLoop(goal="交付报告")
        # 验收真源随修复推进（问题逐轮消失）——程序校验器输出形态
        rounds = [
            {"ok": False, "issues": [{"id": "i1", "severity": "blocking", "detail": "缺工期"},
                                     {"id": "i2", "severity": "minor", "detail": "错字"}]},
            {"ok": False, "issues": [{"id": "i2", "severity": "minor", "detail": "错字"}]},
            {"ok": True, "issues": []},
        ]
        it = iter(rounds)
        gate = SettleGate(session_id="s1",
                          hooks=[make_repair_hook(lambda ctx: next(it), repair)],
                          max_continues=3)
        entries, out = self._loop_until_settled(gate)
        assert len(entries) == 2                       # 两轮修复才通过
        assert "🔴阻塞" in entries[0] and "[i1]" in entries[0]
        assert out["settled"] is True
        assert repair.status()["rounds"] == 3          # 三步验收全记账

    def test_stop_decision_settles_with_visible_failure(self):
        """minimum 仍失败→stop：不再续轮，失败可见（禁假称完成）。"""
        repair = repair_loop.RepairLoop(goal="g", max_stall=1)
        bad = {"ok": False, "issues": [{"id": "i1", "severity": "blocking", "detail": "硬缺失"}]}
        gate = SettleGate(hooks=[make_repair_hook(lambda ctx: dict(bad), repair)],
                          max_continues=10)
        entries = []
        for _ in range(10):
            out = gate.before_settle({})
            if out["settled"]:
                break
            entries.append(out["entry"])
        assert out["settled"] is True
        assert "stop" in out["reason"] or "最低" in out["reason"] or "停止" in out["reason"]
        assert repair.mode == "minimum"                # 已推进到最低目标阶段

    def test_accept_decision_passes_first_time(self):
        repair = repair_loop.RepairLoop()
        gate = SettleGate(hooks=[make_repair_hook(
            {"ok": True, "issues": []}, repair)])
        out = gate.before_settle({})
        assert out["settled"] is True and out["passes"] == 0


# ── ③ 真实工具循环接线（_run_llm_with_tools：continue → 再发一轮）──

class TestLoopWiring:
    def test_continue_triggers_extra_model_round(self, tmp_path):
        """真实循环：钩子 continue → entry 追加进会话 → 再发一轮模型请求。"""
        hook, calls = _continue_hook(entry="【校验不过】请补工期", reason="i1",
                                     times=1)          # 只修一轮，第二轮放行
        agent = _wire_agent(tmp_path, [["初稿完成"], ["修复版完成"]])
        agent.register_settle_hook(hook, name="validate")
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert "初稿完成" in resp and "修复版完成" in resp   # 两轮模型输出都在
        assert len(calls) == 2                       # before_settle 跑了两次
        # 第二轮模型请求的消息里带上了修复 entry（退回同会话修复）
        second_round_messages = agent.llm_engine.seen_messages[1]
        assert any("【校验不过】请补工期" in str(m.get("content", ""))
                   for m in second_round_messages)

    def test_zero_hooks_single_round_unchanged(self, tmp_path):
        """零钩子=零行为变化：一轮纯文本直接收（回归钉）。"""
        agent = _wire_agent(tmp_path, [["答案文本"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert "答案文本" in resp
        assert len(agent.llm_engine.seen_messages) == 1

    def test_bounded_continue_stops_loop(self, tmp_path):
        """钩子死循环 continue → 有界强制放行（防打转：不烧穿轮次）。"""
        hook, _ = _continue_hook(entry="修")
        agent = _wire_agent(tmp_path, [["r1"], ["r2"], ["r3"], ["r4"]])
        agent.register_settle_hook(hook, name="always")
        # _settle_gate_for max_continues=2：第3次 before_settle 强制放行
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert len(agent.llm_engine.seen_messages) == 3   # 2次续轮+1次收尾
        assert "r3" in resp

    def test_repair_hook_end_to_end(self, tmp_path):
        """#2×#3 成环走真实循环：程序校验不过→修复提示词退回→修好收尾。"""
        repair = repair_loop.RepairLoop(goal="交报告")
        rounds = [
            {"ok": False, "issues": [{"id": "i1", "severity": "blocking", "detail": "缺工期"}]},
            {"ok": True, "issues": []},
        ]
        it = iter(rounds)
        agent = _wire_agent(tmp_path, [["草稿"], ["已补工期"]])
        agent.register_settle_hook(
            make_repair_hook(lambda ctx: next(it), repair), name="validate")
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert "已补工期" in resp
        second = agent.llm_engine.seen_messages[1]
        assert any("修复指令" in str(m.get("content", "")) for m in second)


# ── ④ agent 面（注册/每回合复位/终态 mark）+ 接线钉 ────────────────

class TestAgentSurface:
    def test_register_and_per_turn_gate(self, tmp_path):
        agent = _wire_agent(tmp_path, [])
        seen = []
        agent.register_settle_hook(
            lambda ctx: seen.append(ctx) or None, name="obs")
        g1 = agent._settle_gate_for("s1")
        assert g1.before_settle({})["settled"] is True and len(seen) == 1
        g2 = agent._settle_gate_for("s1")            # 新回合=新 gate（continue 复位）
        assert g2 is not g1 and g2.continues_used == 0

    def test_settle_mark_idempotent_and_cleans_store(self, tmp_path):
        agent = _wire_agent(tmp_path, [])
        ran = []
        agent.register_settle_hook(
            lambda ctx: ran.append(1), name="fin", settled=True)
        agent._settle_gate_for("s1")
        agent._settle_mark("s1")
        agent._settle_mark("s1")                     # 幂等
        assert len(ran) == 1
        assert "s1" not in agent._settle_gates

    def test_settle_mark_without_gate_noop(self, tmp_path):
        agent = _wire_agent(tmp_path, [])
        agent._settle_mark("never-created")          # 不炸

    def test_wiring_pins(self):
        src = inspect.getsource(SoulMateAgent)
        assert "settle_gate = self._settle_gate_for(session_id)" in src
        assert "_settle = settle_gate.before_settle(" in src
        assert 'messages.append({"role": "user", "content": _settle["entry"]})' in src
        assert "self._settle_mark(session_id)" in src

    def test_wiring_pin_mark_before_turn_close(self):
        """agent_settled 必须在 TurnClose 之前收（彻底结束先于生命周期关账）。"""
        src = inspect.getsource(SoulMateAgent)
        assert src.index("self._settle_mark(session_id)") < \
            src.rindex("await self._turn_lifecycle.aclose_turn(")
