"""复杂任务回合工具账本汇入（P1）— plan复杂路径tool_calls_log恒空修复回归

对象：soulmate_agent._prompt_inner 复杂任务执行环两处 _run_llm_with_tools 返回的
步骤工具账本（修复前 _step_tools/_retry_tools 被丢弃）。
缺口后果：复杂任务回合 tool_calls_log 恒空 → 下游能力评估/技能提取/进化观察/
自动学习/aclose_turn工具计数整体不触发（工具调用被当成"无工具回合"）——
复杂任务的学习/记账链断裂（"不进化"堵点之一，09:42轮遗留#4）。

驱动方式：复用 test_plan_execution.build_env（真实 TaskPlanner.replan /
SelfReflector / TaskStore + FakeLLM步进驱动真实 _run_llm_with_tools），
补 test_tool_output_wiring 的工具执行侧替身（read_file/write_file 走真实
内建工具分支，非stub）；下游POST走 FakeAsyncClient 留痕断言。
"""

import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.tool_output_handler import ToolOutputHandler
from agent.soulmate_agent import SoulMateAgent
from tests.test_plan_execution import (
    JUDGE_CONTINUE,
    RETRY,
    FakeAsyncClient,
    build_env,
    run_prompt,
)
from tests.test_steering import run


class _AllowGate:
    """放行门（测试替身：真实工具分支需要 gate_result 结构化字段）"""

    async def check(self, session_id, tool_name, tool_args, **kw):
        return SimpleNamespace(
            allowed=True, behavior="allow", blocked_reason="",
            rule_source="test", mode="default")


def _wire_tool_side(agent, tmp_path):
    """补工具执行侧依赖（与 test_tool_output_wiring._wire_agent 同配方）"""
    agent._permission_gate = _AllowGate()
    agent._output_handler = ToolOutputHandler(
        spill_dir=str(tmp_path / "spill"),
        char_threshold=1000, line_threshold=50, preview_chars=200,
    )
    agent._tool_cache = SimpleNamespace(get=lambda *a, **k: None,
                                        put=lambda *a, **k: None)
    agent._tool_error_handler = SimpleNamespace(
        handle_error=lambda *a, **k: SimpleNamespace(
            to_model_message=lambda: "工具异常"))
    agent._tool_auditor = SimpleNamespace(audit_call=lambda **k: None)
    agent._todo_list = []
    return agent


def _capture_learned(agent):
    """捕获自动学习收到的回合工具账本（tool_calls_log 在运行时路径的真实去向）"""
    seen = {}

    async def _rec(**kw):
        seen.setdefault("calls", []).append(kw.get("tool_calls"))

    agent._skill_manager.try_learn_skill = _rec
    return seen


def _tool_call(name, args):
    return {"tool_calls": [{
        "id": f"tc-{name}", "function": {"name": name, "arguments": json.dumps(args)},
    }]}


# ════════════════════════════════════════════════════════════════
# 1. 跨步骤工具账本汇入（首试 + retry 都算本回合真实工具面）
# ════════════════════════════════════════════════════════════════

class TestStepToolsAccumulate:
    def test_tools_accumulate_across_steps(self, tmp_path, monkeypatch):
        """两个步骤各一次工具调用 → 回合账本按执行顺序含2条（修复前恒空）"""
        f = tmp_path / "src.txt"
        f.write_text("alpha-content\n")
        out = tmp_path / "out.txt"
        FakeAsyncClient.calls.clear()
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            # 每步两轮：工具轮 + 文本收尾轮
            llm_scripts=[
                [_tool_call("read_file", {"path": str(f), "limit": 5})], ["ok A"],
                [_tool_call("write_file", {"path": str(out), "content": "hello"})], ["ok B"],
            ],
            llm_call_responses=[JUDGE_CONTINUE],
        )
        _wire_tool_side(agent, tmp_path)
        seen = _capture_learned(agent)
        run_prompt(agent)
        # 回合工具账本=跨步骤累积、顺序=执行顺序（真实内建工具执行产生的条目）
        assert [t["name"] for t in seen["calls"][0]] == ["read_file", "write_file"]
        # 真实执行证据：read_file条目带真实文件内容预览，write_file真实落盘
        assert "alpha-content" in seen["calls"][0][0]["result_preview"]
        assert out.read_text() == "hello"
        # 下游门（tool_calls_log非空才发）对复杂回合真实触发
        assert "http://127.0.0.1:8090/api/benchmark/capability/evaluate" in FakeAsyncClient.calls
        assert "http://127.0.0.1:8090/api/gene/skill/extract" in FakeAsyncClient.calls

    def test_retry_round_tools_accumulate(self, tmp_path, monkeypatch):
        """失败首试+retry的工具都汇入账本（按真实执行记，不因结局回滚）"""
        f = tmp_path / "src2.txt"
        f.write_text("beta-content\n")
        FakeAsyncClient.calls.clear()
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A")],
            llm_scripts=[
                [_tool_call("read_file", {"path": str(f), "limit": 5})], ["Error: broke"],
                [_tool_call("read_file", {"path": str(f), "limit": 5})], ["ok A now"],
            ],
            llm_call_responses=[JUDGE_CONTINUE, RETRY],
        )
        _wire_tool_side(agent, tmp_path)
        seen = _capture_learned(agent)
        run_prompt(agent)
        ledger = seen["calls"][0]
        assert [t["name"] for t in ledger] == ["read_file", "read_file"]  # 首试+重试
        assert all("beta-content" in t["result_preview"] for t in ledger)


# ════════════════════════════════════════════════════════════════
# 2. 下游门条件在复杂路径不走样
# ════════════════════════════════════════════════════════════════

class TestDownstreamGates:
    def test_no_tools_complex_round_skips_capability_and_extract(self, tmp_path, monkeypatch):
        """复杂回合零工具调用 → capability/extract照旧不发（if tool_calls_log门保持）"""
        FakeAsyncClient.calls.clear()
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[["ok A"], ["ok B"]],
            llm_call_responses=[JUDGE_CONTINUE],
        )
        _wire_tool_side(agent, tmp_path)
        seen = _capture_learned(agent)
        run_prompt(agent)
        assert seen["calls"][0] == []  # 空账本如实传给学习侧
        assert "http://127.0.0.1:8090/api/benchmark/capability/evaluate" not in FakeAsyncClient.calls
        assert "http://127.0.0.1:8090/api/gene/skill/extract" not in FakeAsyncClient.calls

    def test_failure_outcome_keeps_capability_skips_extract(self, tmp_path, monkeypatch):
        """有工具但回合结局失败（LLM失败标记入正文）→ capability照发、extract被门拦"""
        f = tmp_path / "src3.txt"
        f.write_text("gamma-content\n")
        FakeAsyncClient.calls.clear()
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A")],
            llm_scripts=[
                [_tool_call("read_file", {"path": str(f), "limit": 5})],
                # 机器生成失败形态（llm_engine.py回传文本chunk）：正文含\n[LLM响应超时
                # → turn_outcome_success=False（无错误关键词→步骤照过自省，报告全收敛）
                ["收集完成\n[LLM响应超时]"],
            ],
            llm_call_responses=[JUDGE_CONTINUE],
        )
        _wire_tool_side(agent, tmp_path)
        seen = _capture_learned(agent)
        run_prompt(agent)
        assert [t["name"] for t in seen["calls"][0]] == ["read_file"]  # 账本不因结局丢
        assert "http://127.0.0.1:8090/api/benchmark/capability/evaluate" in FakeAsyncClient.calls
        assert "http://127.0.0.1:8090/api/gene/skill/extract" not in FakeAsyncClient.calls


# ════════════════════════════════════════════════════════════════
# 3. 接线防死代码（inspect.getsource）
# ════════════════════════════════════════════════════════════════

class TestWiring:
    def test_step_and_retry_tools_extend_ledger(self):
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert "tool_calls_log.extend(step_tools)" in src
        assert "tool_calls_log.extend(retry_tools)" in src
        assert "_step_tools" not in src.replace("原_step_tools丢弃", "")  # 丢弃写法已清除
        assert "_retry_tools" not in src

    def test_simple_path_untouched(self):
        """简单路径账本来源不变（零回归护栏）"""
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert "full_response, tool_calls_log = await self._run_llm_with_tools" in src
