"""15.3 aux LLM 延迟治理 — 回归测试

对象：
1. TaskPlanner.plan 多文件确定性拆步**前移**（pre-gate）——多文件目标完全跳过
   LLM 规划往返（旧顺序 LLM 规划完即被 decomposer 覆盖丢弃=纯浪费）
2. _aux_llm_chat 关键路径 label（plan/task_judge）短预算 SOULMATE_AUX_CRITICAL_BUDGET_S
   到点抛 TimeoutError → 调用点既有确定性回退（plan→"简单任务"、task_judge→"new"）
3. 账本诚实：超时轮 status="timeout" 如实落账（禁把超时记成 ok）
4. 非关键路径 label（title/reflect/replan/infection_audit）不设限，语义不变
"""

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.soulmate_agent import (
    SoulMateAgent,
    _AUX_TRAJ_CTX,
    _aux_critical_budget_s,
)
from agent.task_engine import TaskPlanner


# ════════════════════════════════════════════════════════════════
# 测试替身
# ════════════════════════════════════════════════════════════════

class FakeRecorder:
    """record_aux_llm_call 落账替身：捕获 kwargs"""

    def __init__(self):
        self.rows = []

    async def record_aux_llm_call(self, **kw):
        self.rows.append(kw)


class SlowLLM:
    """chat 永不返回（除非被 wait_for 取消）"""

    def __init__(self, delay: float = 30.0):
        self.delay = delay
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        await asyncio.sleep(self.delay)
        return '{"needs_planning": false, "reason": "x", "subtasks": []}'


class ScriptedLLM:
    def __init__(self, response: str = ""):
        self.response = response
        self.calls = 0

    async def chat(self, messages):
        self.calls += 1
        return self.response


class ExplodingLLM:
    """llm_call_fn 替身：被调用即失败（证明 pre-gate 完全跳过 LLM）"""

    def __init__(self):
        self.calls = 0

    async def __call__(self, messages):
        self.calls += 1
        raise AssertionError("LLM planning must not be called for multi-file goals")


def make_agent(llm) -> SoulMateAgent:
    """最小构造（绕过 __init__ 重副作用）：只挂 _aux_llm_chat 需要的两属性"""
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent.llm_engine = llm
    agent._traj_recorder = FakeRecorder()
    return agent


def run(coro, sid: str = "sess-aux-test"):
    """跑协程并提供 _AUX_TRAJ_CTX 落账上下文（_aux_llm_chat 契约：回合外
    无 session 不落账——测试用 ctx 模拟 _prompt_inner 回合内环境）"""
    async def _wrapped():
        token = _AUX_TRAJ_CTX.set({"session_id": sid, "root_id": ""})
        try:
            return await coro
        finally:
            _AUX_TRAJ_CTX.reset(token)

    return asyncio.run(_wrapped())


# ════════════════════════════════════════════════════════════════
# 1. pre-gate：多文件目标跳过 LLM 规划
# ════════════════════════════════════════════════════════════════

def test_multifile_goal_skips_planning_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSOUL_DB", str(tmp_path / "tasks.db"))
    llm = ExplodingLLM()
    planner = TaskPlanner(llm_call_fn=llm)

    goal = "修改 src/a.py 和 src/b.py 的错误处理"
    plan = run(planner.plan(goal, "sess-pre-gate"))

    assert llm.calls == 0, "多文件目标必须跳过 LLM 规划往返（15.3 pre-gate）"
    assert len(plan.subtasks) == 3  # 2 文件步 + 1 整体验证步
    assert "src/a.py" in plan.subtasks[0].description
    assert "src/b.py" in plan.subtasks[1].description
    assert plan.status == "active"


def test_normal_goal_still_calls_planning_llm(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSOUL_DB", str(tmp_path / "tasks.db"))

    class FakeResp:
        def __init__(self):
            self.calls = 0

        async def __call__(self, messages):
            self.calls += 1
            return '{"needs_planning": false, "reason": "简单问答", "subtasks": []}'

    llm = FakeResp()
    planner = TaskPlanner(llm_call_fn=llm)
    plan = run(planner.plan("今天天气怎么样", "sess-normal"))

    assert llm.calls == 1, "非多文件目标仍走 LLM 规划（行为不变）"
    assert not plan.subtasks
    assert plan.status == "completed"


# ════════════════════════════════════════════════════════════════
# 2. 关键路径 aux 短预算
# ════════════════════════════════════════════════════════════════

def test_aux_critical_label_timeout_records_status(monkeypatch):
    monkeypatch.setenv("SOULMATE_AUX_CRITICAL_BUDGET_S", "0.3")
    agent = make_agent(SlowLLM(delay=30.0))

    t0 = time.monotonic()
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        run(agent._aux_llm_chat("plan", [{"role": "user", "content": "目标：x"}]))
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0, "到点必须快速回退，不等满上游 120s read timeout"
    rows = agent._traj_recorder.rows
    assert len(rows) == 1
    assert rows[0]["status"] == "timeout", "超时轮账本必须如实落 status=timeout（禁记 ok）"
    assert rows[0]["duration_ms"] >= 250


def test_aux_fast_call_records_ok():
    agent = make_agent(ScriptedLLM("你好"))
    out = run(agent._aux_llm_chat("plan", [{"role": "user", "content": "目标：x"}]))
    assert out == "你好"
    assert agent._traj_recorder.rows[0]["status"] == "ok"


def test_aux_noncritical_label_not_budgeted(monkeypatch):
    """title/reflect/replan/infection_audit 非首字关键路径：不设限，语义不变"""
    monkeypatch.setenv("SOULMATE_AUX_CRITICAL_BUDGET_S", "0.3")
    agent = make_agent(ScriptedLLM("标题OK"))

    async def slow_then_reply(messages):
        await asyncio.sleep(0.5)  # 超过预算也不应被打断
        return "标题OK"

    agent.llm_engine = SimpleNamespace(chat=slow_then_reply)
    out = run(agent._aux_llm_chat("title", [{"role": "user", "content": "x"}]))
    assert out == "标题OK"
    assert agent._traj_recorder.rows[0]["status"] == "ok"


def test_budget_env_semantics(monkeypatch):
    monkeypatch.delenv("SOULMATE_AUX_CRITICAL_BUDGET_S", raising=False)
    assert _aux_critical_budget_s("plan") == 12.0
    assert _aux_critical_budget_s("task_judge") == 12.0
    assert _aux_critical_budget_s("title") == 0.0
    assert _aux_critical_budget_s("reflect") == 0.0
    monkeypatch.setenv("SOULMATE_AUX_CRITICAL_BUDGET_S", "5")
    assert _aux_critical_budget_s("plan") == 5.0
    monkeypatch.setenv("SOULMATE_AUX_CRITICAL_BUDGET_S", "0")
    assert _aux_critical_budget_s("plan") == 0.0, "<=0 = 关闭预算"
    monkeypatch.setenv("SOULMATE_AUX_CRITICAL_BUDGET_S", "garbage")
    assert _aux_critical_budget_s("plan") == 12.0, "非法值回退默认（禁炸）"


# ════════════════════════════════════════════════════════════════
# 3. 端到端：超时走既有确定性回退
# ════════════════════════════════════════════════════════════════

def test_plan_falls_back_to_simple_on_budget_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSOUL_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("SOULMATE_AUX_CRITICAL_BUDGET_S", "0.3")
    agent = make_agent(SlowLLM(delay=30.0))
    planner = TaskPlanner(llm_call_fn=agent._llm_plan_call)

    t0 = time.monotonic()
    plan = run(planner.plan("帮我写一篇关于AI的文章", "sess-budget"))
    elapsed = time.monotonic() - t0

    assert elapsed < 5.0
    assert not plan.subtasks
    assert plan.status == "completed", "超时回退=既有'简单任务'确定性回退"
    assert agent._traj_recorder.rows[0]["status"] == "timeout"


def test_task_judge_timeout_raises_for_caller_fallback(monkeypatch):
    """task_judge 超时向上抛 → _prompt_inner 既有 except 默认 'new'（调用点已存在）"""
    monkeypatch.setenv("SOULMATE_AUX_CRITICAL_BUDGET_S", "0.3")
    agent = make_agent(SlowLLM(delay=30.0))

    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        run(agent._judge_task_continuation_llm(
            SimpleNamespace(goal="旧任务"), "用户新消息"
        ))
    assert agent._traj_recorder.rows[0]["status"] == "timeout"
