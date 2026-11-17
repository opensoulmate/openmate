# -*- coding: utf-8 -*-
"""Pi 取长补短 #1——钩子面接线（plugin/hooks 注册中心消费方，「写了≠接线了」修复）。

六类钩子映射：beforeToolCall→before_tool_call（block/改参，权限门禁前跑——
改参不绕审批）/ afterToolCall→after_tool_call（覆盖结果/置 task_complete 并入
#12 整批 terminate）/ finishTurn→finish_turn（end/continue 续轮，SettleGate
有界消费防打转）/ prepareRequest→prepare_request（请求面可改）/ steering、
followUp→steering.py 人机插话队列（既有人机对话通道，不重复建钩子）。

整合面：plugin/hooks.py（既有 HookRegistry——注册/优先级/隔离/取消拦截全有，
此前零消费方）+ plugin/models.py HOOK_POINTS 扩点；接线在 soulmate_agent 工具
管道（主循环 + code-mode 批内双路）与 LLM 请求面/收尾边界。

通用钩子能力（非投标专用）。
"""
import inspect

import pytest

import agent.soulmate_agent as sma
from agent.soulmate_agent import SoulMateAgent
from plugin.hooks import hook_registry
from plugin.models import HOOK_POINTS, HookContext
from tests.test_loop_guard_wiring import _tc, _wire_agent
from tests.test_steering import run


@pytest.fixture(autouse=True)
def _clean_hooks():
    hook_registry.clear()
    yield
    hook_registry.clear()


TOOL_STEP = [_tc("terminal", {"command": "echo hi"}, tc_id="tc1")]


# ── ① 钩子面声明（HOOK_POINTS 六类映射）──────────────────────────

class TestHookPoints:
    def test_six_pi_hooks_declared(self):
        for p in ("before_tool_call", "after_tool_call", "finish_turn",
                  "prepare_request"):
            assert p in HOOK_POINTS
        # steering/followUp → 既有人机插话队列（steering.py），不重复建钩子
        from plugin import models as plugin_models
        assert "getSteeringMessages" in inspect.getsource(plugin_models)

    def test_manifest_accepts_new_points(self):
        from plugin.models import PluginManifest
        m = PluginManifest(name="p", version="1.0", author="t",
                           hooks=["before_tool_call", "finish_turn"])
        assert m.hooks == ["before_tool_call", "finish_turn"]


# ── ② before_tool_call（block / 改参）────────────────────────────

class TestBeforeToolCall:
    def test_block_skips_execution(self, tmp_path):
        def blocker(ctx: HookContext):
            ctx.cancel("策略禁止 terminal")

        hook_registry.register_hook("before_tool_call", blocker, priority=10)
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert len(tools) == 1
        assert tools[0]["permission"] == "hook-blocked"
        assert "策略禁止 terminal" in tools[0]["result_preview"]

    def test_args_mutation_applied(self, tmp_path):
        def rewriter(ctx: HookContext):
            ctx.data["args"] = {"command": "echo hooked"}

        hook_registry.register_hook("before_tool_call", rewriter, priority=10)
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert tools[0]["arguments"] == {"command": "echo hooked"}   # 改参生效

    def test_args_mutation_does_not_bypass_gate(self, tmp_path):
        """改参在权限门禁前生效——门禁看到的是最终参数（源序钉）。"""
        src = inspect.getsource(SoulMateAgent)
        assert src.index('"before_tool_call", {') < \
            src.index("gate_result = await self._permission_gate.check(")

    def test_hook_exception_isolated(self, tmp_path):
        def bad(ctx: HookContext):
            raise RuntimeError("boom")

        hook_registry.register_hook("before_tool_call", bad, priority=1)
        hook_registry.register_hook(
            "before_tool_call",
            lambda ctx: ctx.data.__setitem__("args", {"command": "echo survived"}),
            priority=2)
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert tools[0]["arguments"] == {"command": "echo survived"}  # 坏钩子不破坏链

    def test_priority_order(self, tmp_path):
        order = []
        hook_registry.register_hook(
            "before_tool_call",
            lambda ctx: order.append("late"), priority=50)
        hook_registry.register_hook(
            "before_tool_call",
            lambda ctx: order.append("early"), priority=10)
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        assert order == ["early", "late"]


# ── ③ after_tool_call（覆盖结果 / task_complete）──────────────────

class TestAfterToolCall:
    def test_result_override(self, tmp_path):
        def overwriter(ctx: HookContext):
            ctx.data["result"] = "【钩子覆盖的结果】"

        hook_registry.register_hook("after_tool_call", overwriter, priority=10)
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        # 覆盖生效在**工具结果面**：下一轮模型看到的是覆盖后的结果
        second = agent.llm_engine.seen_messages[1]
        assert any("【钩子覆盖的结果】" in str(m.get("content", ""))
                   for m in second)
        assert "【钩子覆盖的结果】" in str(tools[0]["result_preview"])

    def test_task_complete_triggers_batch_terminate(self, tmp_path):
        """钩子置 task_complete → 并入 #12 整批 terminate（免总结收尾）。"""
        def finisher(ctx: HookContext):
            ctx.data["task_complete"] = True

        hook_registry.register_hook("after_tool_call", finisher, priority=10)
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["不该出现的第二轮"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert len(agent.llm_engine.seen_messages) == 1      # 免总结：无第二轮
        assert "不该出现的第二轮" not in resp

    def test_zero_hooks_result_unchanged(self, tmp_path):
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert "完" in resp                                   # 零钩子零行为变化


# ── ④ finish_turn（end/continue 续轮，SettleGate 有界）────────────

class TestFinishTurn:
    def test_continue_triggers_extra_round(self, tmp_path):
        def continuer(ctx: HookContext):
            ctx.data["continue"] = True
            ctx.data["entry"] = "【finish_turn 续轮】补一句话"

        hook_registry.register_hook("finish_turn", continuer, priority=10)
        agent = _wire_agent(tmp_path, [["初稿"], ["补充完"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert "初稿" in resp and "补充完" in resp
        second = agent.llm_engine.seen_messages[1]
        assert any("【finish_turn 续轮】" in str(m.get("content", ""))
                   for m in second)

    def test_bounded_by_settle_gate(self, tmp_path):
        """死循环 continue → SettleGate 有界强制放行（防打转复用 #2 计数）。"""
        def always(ctx: HookContext):
            ctx.data["continue"] = True
            ctx.data["entry"] = "续"

        hook_registry.register_hook("finish_turn", always, priority=10)
        agent = _wire_agent(tmp_path, [["r1"], ["r2"], ["r3"], ["r4"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        assert len(agent.llm_engine.seen_messages) == 3      # 2 续轮 + 1 收尾

    def test_no_continue_settles_immediately(self, tmp_path):
        hook_registry.register_hook(
            "finish_turn", lambda ctx: None, priority=10)
        agent = _wire_agent(tmp_path, [["答案"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert len(agent.llm_engine.seen_messages) == 1


# ── ⑤ prepare_request（请求面可改）───────────────────────────────

class TestPrepareRequest:
    def test_messages_mutation_reaches_llm(self, tmp_path):
        def injector(ctx: HookContext):
            ctx.data["messages"] = list(ctx.data["messages"]) + [
                {"role": "user", "content": "【钩子注入的上下文】"}]

        hook_registry.register_hook("prepare_request", injector, priority=10)
        agent = _wire_agent(tmp_path, [["答"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        first = agent.llm_engine.seen_messages[0]
        assert any("【钩子注入的上下文】" in str(m.get("content", ""))
                   for m in first)

    def test_tools_mutation(self, tmp_path):
        def shrink(ctx: HookContext):
            ctx.data["tools"] = []

        hook_registry.register_hook("prepare_request", shrink, priority=10)
        agent = _wire_agent(tmp_path, [["答"]])
        seen_tools = []
        orig = agent.llm_engine.chat_stream_with_tools

        def spy(messages=None, tools=None, system_prompt=None, **kw):
            seen_tools.append(tools)
            return orig(messages=messages, tools=tools,
                        system_prompt=system_prompt, **kw)

        agent.llm_engine.chat_stream_with_tools = spy
        run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        assert seen_tools and seen_tools[0] is None        # 工具面被钩子收缩为空


# ── ⑥ code-mode 批内同款钩子面 ───────────────────────────────────

class TestCodeModeHooks:
    def test_before_block_in_batch(self, tmp_path):
        hook_registry.register_hook(
            "before_tool_call",
            lambda ctx: ctx.cancel("批内拦截"), priority=10)
        agent = _wire_agent(tmp_path, [])
        out = run(agent._code_mode_tool_call(
            "s1", "terminal", {"command": "echo hi"}, "/tmp"))
        assert out == "[被拦截:hook] 批内拦截"

    def test_after_override_in_batch(self, tmp_path):
        hook_registry.register_hook(
            "after_tool_call",
            lambda ctx: ctx.data.__setitem__("result", "批内覆盖"),
            priority=10)
        agent = _wire_agent(tmp_path, [])
        out = run(agent._code_mode_tool_call(
            "s1", "terminal", {"command": "echo hi"}, "/tmp"))
        assert out == "批内覆盖"


# ── ⑦ HookContext 契约 + 接线钉 ──────────────────────────────────

class TestContract:
    def test_cancel_reason_recorded(self):
        ctx = HookContext(hook_point="before_tool_call")
        ctx.cancel("禁止")
        assert ctx.cancelled and ctx.cancel_reason == "禁止"

    def test_cancel_only_before_hooks(self):
        ctx = HookContext(hook_point="after_tool_call")
        with pytest.raises(RuntimeError):
            ctx.cancel()

    def test_wiring_pins(self):
        src = inspect.getsource(SoulMateAgent)
        assert src.count('_trigger_tool_hook("before_tool_call"') == 2   # 主循环+code-mode
        assert src.count('_trigger_tool_hook("after_tool_call"') == 2
        assert '_trigger_tool_hook("prepare_request"' in src
        assert '_trigger_tool_hook("finish_turn"' in src
        # finish_turn 结果经 SettleGate 有界消费（不自建续轮计数）
        assert '"finish_turn": (_fh.data if _fh is not None else {})' in src
        assert 'gate.register(_finish_turn_bridge, name="finish_turn")' in src

    def test_registry_stat_sees_registrations(self):
        hook_registry.register_hook("before_tool_call", lambda ctx: None)
        assert hook_registry.get_stats()["before_tool_call"] == 1
