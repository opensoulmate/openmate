"""
delegate_tasks 重复派发根修（周期#253 新失败形态：planner 把"一次并行派发两个
子任务"拆成两个 plan_step，每步各派发整批 → subagent_runs 4 行 2 run_group）。

三层语义根修回归锁：
1. PLANNING_PROMPT 把 delegate_tasks 并行派发列为单工具任务不拆步 + 拆分禁令
2. 工具 schema/系统提示去掉 "2-8个" 最少 2 槽误导（步骤执行器据此"凑满两槽"
   把整批重复派发的根源），改为 1-8 + 一次调用派发完毕
3. plan 步骤执行 prompt 增派发范围规则（残留拆步形态下每步只派发本步骤子任务）

功能面：单子任务派发链路无 min-2 拒绝（SubagentManager 只有 MAX 夹取）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import agent.subagent_manager as sm
from agent.subagent_manager import SubagentManager, Subtask
from agent.task_engine import PLANNING_PROMPT

SA_SRC = Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py"


# ── 1. PLANNING_PROMPT：并行派发=单工具任务不拆步 ──────────────────


def test_planning_prompt_no_split_for_delegate_parallel_dispatch():
    assert "delegate_tasks 一次并行派发多个独立子任务" in PLANNING_PROMPT
    # 拆分禁令：点名"子任务1/子任务2"拆步形态与重复派发后果
    assert "并行派发N个子任务" in PLANNING_PROMPT
    assert "重复派发" in PLANNING_PROMPT


# ── 2. 工具 schema/系统提示："2-8" 误导清除 + 单批一次派发语义 ──────


def test_delegate_schema_and_prompt_single_batch_wording():
    src = SA_SRC.read_text(encoding="utf-8")
    # "最少2槽"误导清除（这是步骤执行器凑满两槽重复派发整批的根源）
    assert "2-8个可并行的独立子任务" not in src
    assert "（2-8个）" not in src
    # 单批一次派发语义在 schema 与系统提示双落点
    assert "1-8个可并行的独立子任务" in src
    assert "一次调用派发完毕" in src
    assert "不要分多次重复派发" in src


# ── 3. plan 步骤执行 prompt：派发范围规则 ─────────────────────────


def test_plan_step_prompt_has_dispatch_scope_rule():
    src = SA_SRC.read_text(encoding="utf-8")
    assert "6. 如果当前步骤是派发子代理（delegate_tasks）" in src
    assert "不要替其他步骤重复派发同一批子任务" in src


# ── 4. 功能面：单子任务派发链路无 min-2 拒绝 ──────────────────────


def test_single_task_dispatch_not_rejected(monkeypatch):
    """单子任务派发真实走 _subagent_dispatch → SubagentManager.run 不被拒绝。"""

    async def runner(st: Subtask) -> str:
        return f"done:{st.goal}"

    mgr = SubagentManager(runner=runner, db_path=str(
        Path(sm.__file__).resolve().parent.parent / "data" / "test_delegate_single.db"))
    import agent.soulmate_agent as sa

    monkeypatch.setattr(sa, "_get_subagent_manager", lambda: mgr)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = asyncio.run(agent._subagent_dispatch(
        {"tasks": [{"goal": "仅此一个"}], "parent_goal": "单任务也合法"},
        session_id="om-single",
    ))
    assert "仅此一个" in out
    assert "done:仅此一个" in out

    # 对照：两任务整批一次调用仍是主形态（不因修措辞破坏原语义）
    out2 = asyncio.run(agent._subagent_dispatch(
        {"tasks": [{"goal": "甲"}, {"goal": "乙"}], "parent_goal": "一批"},
        session_id="om-batch",
    ))
    assert "done:甲" in out2 and "done:乙" in out2
