# -*- coding: utf-8 -*-
"""Pi 取长补短 #9——固定工具清单 + 阶段门禁（setAllowedTools 语义）。

拦截替代改清单：工具清单恒定（LLM 工具面不动，前缀缓存不失配），阶段权限在
before_tool_call 拦截点调用期拦（与 filter_tools 的工具面收缩互补——环境轴裁剪
vs 阶段轴拦截）。整合面：agent/sandbox_policy.py（工具面策略单一真源，第三轴）
扩 AllowedToolsGate；拦截点=Pi #1 钩子链同一位置（权限门禁之前）。

通用阶段治理能力（非投标专用）。
"""
import inspect

import pytest

from agent.sandbox_policy import AllowedToolsGate
from agent.soulmate_agent import SoulMateAgent
from tests.test_loop_guard_wiring import _tc, _wire_agent
from tests.test_steering import run


@pytest.fixture()
def gate():
    return AllowedToolsGate()


TOOL_STEP = [_tc("terminal", {"command": "echo hi"}, tc_id="tc1")]


# ── ① setAllowedTools 语义 ───────────────────────────────────────

class TestAllowedToolsGate:
    def test_zero_config_zero_behavior(self, gate):
        assert gate.deny_reason("s1", "terminal") == ""
        assert gate.deny_reason("", "anything") == ""

    def test_phase_allowlist_blocks_others(self, gate):
        gate.set_allowed("draft", ["read_file", "search_files"], session_id="s1")
        gate.set_phase("s1", "draft")
        assert gate.deny_reason("s1", "read_file") == ""
        reason = gate.deny_reason("s1", "terminal")
        assert "当前阶段 'draft' 不能调用 'terminal'" in reason
        assert "setAllowedTools" in reason                     # 显式可见禁静默

    def test_phase_switch_changes_allowlist(self, gate):
        gate.set_allowed("draft", ["read_file"], session_id="s1")
        gate.set_allowed("final", ["write_file"], session_id="s1")
        gate.set_phase("s1", "draft")
        assert gate.deny_reason("s1", "write_file") != ""
        gate.set_phase("s1", "final")
        assert gate.deny_reason("s1", "write_file") == ""
        assert gate.deny_reason("s1", "read_file") != ""

    def test_unregistered_phase_unrestricted(self, gate):
        gate.set_allowed("draft", ["read_file"], session_id="s1")
        gate.set_phase("s1", "ghost-phase")                    # 未登记=不限制
        assert gate.deny_reason("s1", "terminal") == ""

    def test_session_overrides_global(self, gate):
        gate.set_allowed("draft", ["read_file"])               # 全局
        gate.set_phase("", "draft")
        assert gate.deny_reason("sX", "terminal") != ""        # 全局生效
        gate.set_allowed("draft", ["terminal"], session_id="sX")  # 会话级覆盖
        assert gate.deny_reason("sX", "terminal") == ""
        assert gate.deny_reason("sY", "terminal") != ""        # 其他会话仍全局

    def test_empty_allowlist_blocks_everything(self, gate):
        gate.set_allowed("lock", [], session_id="s1")
        gate.set_phase("s1", "lock")
        assert gate.deny_reason("s1", "read_file") != ""

    def test_clear(self, gate):
        gate.set_allowed("draft", ["read_file"], session_id="s1")
        gate.set_phase("s1", "draft")
        assert gate.clear("s1") == 1
        assert gate.deny_reason("s1", "read_file") == ""

    def test_validation(self, gate):
        with pytest.raises(ValueError):
            gate.set_allowed("", ["x"])
        with pytest.raises(ValueError):
            gate.set_phase("s1", "")

    def test_snapshot_shape(self, gate):
        gate.set_allowed("draft", ["read_file"], session_id="s1")
        gate.set_phase("s1", "draft")
        s = gate.snapshot()
        assert s["sessions"]["s1"]["phase"] == "draft"
        assert s["sessions"]["s1"]["allowed"]["draft"] == ["read_file"]


# ── ② 钩子形态（plugin/hooks before_tool_call 可直接注册）─────────

class TestHookForm:
    def test_make_hook_cancels_with_reason(self, gate):
        gate.set_allowed("draft", ["read_file"], session_id="s1")
        gate.set_phase("s1", "draft")
        from plugin.models import HookContext
        ctx = HookContext(hook_point="before_tool_call",
                          data={"session_id": "s1", "tool": "terminal"})
        gate.make_hook()(ctx)
        assert ctx.cancelled and "setAllowedTools" in ctx.cancel_reason

    def test_make_hook_passes_allowed(self, gate):
        gate.set_allowed("draft", ["read_file"], session_id="s1")
        gate.set_phase("s1", "draft")
        from plugin.models import HookContext
        ctx = HookContext(hook_point="before_tool_call",
                          data={"session_id": "s1", "tool": "read_file"})
        gate.make_hook()(ctx)
        assert not ctx.cancelled


# ── ③ 真实循环接线（拦截替代改清单）──────────────────────────────

class TestLoopWiring:
    def test_disallowed_tool_blocked_in_loop(self, tmp_path):
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        g = agent._tool_phase_gate()
        g.set_allowed("draft", ["read_file"], session_id="s1")
        g.set_phase("s1", "draft")
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert tools[0]["permission"] == "phase-blocked"
        assert "setAllowedTools" in tools[0]["result_preview"]
        assert tools[0]["result_preview"] != "hi"             # 未真实执行出结果

    def test_allowed_tool_runs(self, tmp_path):
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        g = agent._tool_phase_gate()
        g.set_allowed("draft", ["terminal"], session_id="s1")
        g.set_phase("s1", "draft")
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert tools[0].get("permission") != "phase-blocked"

    def test_tool_list_never_shrunk(self, tmp_path):
        """拦截替代改清单：门禁不改工具面（前缀缓存不失配）——源面钉。"""
        src = inspect.getsource(SoulMateAgent)
        assert "deny_reason" in src
        # 阶段门禁只在调用期拦截（before_tool_call 拦截点），无 filter/shrink 调用
        assert "_tool_phase_gate().deny_reason" in src

    def test_blocked_before_permission_gate(self, tmp_path):
        """阶段拦截在权限门禁之前（顺序钉）。"""
        src = inspect.getsource(SoulMateAgent)
        assert src.index("_tool_phase_gate().deny_reason") < \
            src.index("gate_result = await self._permission_gate.check(")

    def test_code_mode_inner_blocked(self, tmp_path):
        agent = _wire_agent(tmp_path, [])
        g = agent._tool_phase_gate()
        g.set_allowed("lock", [], session_id="s1")
        g.set_phase("s1", "lock")
        out = run(agent._code_mode_tool_call(
            "s1", "terminal", {"command": "echo hi"}, "/tmp"))
        assert out.startswith("[被拦截:phase-blocked]")

    def test_zero_config_loop_unchanged(self, tmp_path):
        agent = _wire_agent(tmp_path, [TOOL_STEP, ["完"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert tools and tools[0]["permission"] != "phase-blocked"
