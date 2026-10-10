"""
路线图 5.1 子代理并行执行（拆分任务给并行 worker，父代理收尾合并）测试。

覆盖：prompt构建 / 校验与夹取 / 并行执行顺序 / worker失败与超时隔离 /
并发上限 / 确定性merge / runs持久化与写失败不阻塞 / 默认runner四态
（成功收集/拒答/LLM级失败/零输出/截断自报）/ soulmate_agent接线护栏+
__new__真实dispatch烟测。
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.subagent_manager as sm
from agent.subagent_manager import (
    MAX_SUBTASKS,
    MAX_WORKER_OUTPUT_CHARS,
    SUBAGENT_PROMPT_MARKER,
    SubagentManager,
    SubagentResult,
    SubagentStatus,
    Subtask,
    build_subagent_prompt,
    default_subagent_runner,
)


# ── prompt 构建 ────────────────────────────────────────────────


def test_build_subagent_prompt_core():
    st = Subtask(goal="调研A框架", context="只看官方文档")
    p = build_subagent_prompt(st, parent_goal="对比三个框架")
    assert p.startswith(SUBAGENT_PROMPT_MARKER)
    assert "调研A框架" in p
    assert "只看官方文档" in p
    assert "对比三个框架" in p
    assert "只完成上面这一个子任务" in p


def test_build_subagent_prompt_optional_fields():
    p = build_subagent_prompt(Subtask(goal="任务X"))
    assert "补充背景" not in p
    assert "父任务背景" not in p


# ── 校验与归一化 ────────────────────────────────────────────────


def test_normalize_rejects_bad_input():
    with pytest.raises(ValueError):
        SubagentManager._normalize([])
    with pytest.raises(ValueError):
        SubagentManager._normalize(None)
    with pytest.raises(ValueError):
        SubagentManager._normalize([{"goal": ""}])
    with pytest.raises(ValueError):
        SubagentManager._normalize(["not-a-dict"])
    with pytest.raises(ValueError):
        SubagentManager._normalize([{"goal": "x", "timeout_s": "abc"}])
    too_many = [{"goal": f"g{i}"} for i in range(MAX_SUBTASKS + 1)]
    with pytest.raises(ValueError):
        SubagentManager._normalize(too_many)


def test_normalize_clamps_and_accepts_both_shapes():
    subs, conc = SubagentManager._normalize(
        [
            {"goal": " g1 ", "timeout_s": 5},
            Subtask(goal="g2", timeout_s=9999),
        ],
        max_concurrency=99,
    )
    assert subs[0].goal == "g1" and subs[0].timeout_s == sm.MIN_TIMEOUT_S
    assert subs[1].goal == "g2" and subs[1].timeout_s == sm.MAX_TIMEOUT_S
    assert conc == sm.MAX_CONCURRENCY
    _, conc_default = SubagentManager._normalize([{"goal": "x"}])
    assert conc_default == sm.DEFAULT_CONCURRENCY
    _, conc_low = SubagentManager._normalize([{"goal": "x"}], max_concurrency=0)
    assert conc_low == sm.MIN_CONCURRENCY


# ── 并行执行 ───────────────────────────────────────────────────


def _mk_mgr(runner, tmp_path, **kw):
    return SubagentManager(
        runner=runner,
        db_path=str(tmp_path / "runs.db"),
        clock=kw.pop("clock", time.time),
        **kw,
    )


def test_run_parallel_preserves_order(tmp_path):
    async def runner(st: Subtask) -> str:
        # 逆序耗时：输入顺序必须原样保留
        await asyncio.sleep({"g1": 0.15, "g2": 0.08, "g3": 0.01}[st.goal])
        return f"out-{st.goal}"

    mgr = _mk_mgr(runner, tmp_path)
    results = asyncio.run(mgr.run([
        {"goal": "g1"}, {"goal": "g2"}, {"goal": "g3"},
    ]))
    assert [r.goal for r in results] == ["g1", "g2", "g3"]
    assert [r.output for r in results] == ["out-g1", "out-g2", "out-g3"]
    assert all(r.status == SubagentStatus.OK for r in results)


def test_run_worker_failure_isolated(tmp_path):
    async def runner(st: Subtask) -> str:
        if st.goal == "boom":
            raise RuntimeError("worker炸了")
        return f"ok-{st.goal}"

    mgr = _mk_mgr(runner, tmp_path)
    results = asyncio.run(mgr.run([
        {"goal": "a"}, {"goal": "boom"}, {"goal": "c"},
    ]))
    assert results[0].status == SubagentStatus.OK
    assert results[1].status == SubagentStatus.FAILED
    assert "worker炸了" in results[1].error
    assert results[2].status == SubagentStatus.OK


def test_run_timeout_isolated_and_cancels(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "MIN_TIMEOUT_S", 0.1)
    cancelled = []

    async def runner(st: Subtask) -> str:
        if st.goal != "slow":
            return f"ok-{st.goal}"
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.append(st.goal)
            raise
        return "never"

    mgr = _mk_mgr(runner, tmp_path)
    results = asyncio.run(mgr.run([
        {"goal": "slow", "timeout_s": 0.3},
        {"goal": "fast", "timeout_s": 5},
    ]))
    # slow 超时并被取消，fast 不受影响
    assert results[0].status == SubagentStatus.TIMEOUT
    assert "超时" in results[0].error
    assert "slow" in cancelled
    assert results[1].status == SubagentStatus.OK and results[1].output == "ok-fast"


def test_run_timeout_does_not_affect_fast_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, "MIN_TIMEOUT_S", 0.1)

    async def runner(st: Subtask) -> str:
        if st.goal == "slow":
            await asyncio.sleep(30)
        return f"ok-{st.goal}"

    mgr = _mk_mgr(runner, tmp_path)
    results = asyncio.run(mgr.run([
        {"goal": "slow", "timeout_s": 0.2},
        {"goal": "quick", "timeout_s": 5},
    ]))
    assert results[0].status == SubagentStatus.TIMEOUT
    assert results[1].status == SubagentStatus.OK and results[1].output == "ok-quick"


def test_run_concurrency_limit(tmp_path):
    state = {"cur": 0, "max": 0}

    async def runner(st: Subtask) -> str:
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.05)
        state["cur"] -= 1
        return "ok"

    mgr = _mk_mgr(runner, tmp_path)
    asyncio.run(mgr.run([{"goal": f"g{i}"} for i in range(5)], max_concurrency=2))
    assert state["max"] <= 2


def test_run_injects_parent_goal_into_worker_context(tmp_path):
    seen = {}

    async def runner(st: Subtask) -> str:
        seen[st.goal] = st.context
        return "ok"

    mgr = _mk_mgr(runner, tmp_path)
    asyncio.run(mgr.run(
        [{"goal": "g1", "context": "已有背景"}],
        parent_goal="总目标Z",
    ))
    assert "总目标Z" in seen["g1"]
    assert "已有背景" in seen["g1"]


# ── merge（父代理收尾合并，确定性）─────────────────────────────────


def test_merge_mixed_results():
    results = [
        SubagentResult("s1", "调研A", SubagentStatus.OK, output="A结论"),
        SubagentResult("s2", "调研B", SubagentStatus.FAILED, error="RuntimeError: x"),
        SubagentResult("s3", "调研C", SubagentStatus.TIMEOUT, error="超时"),
    ]
    text = SubagentManager.merge(results)
    assert "3个子任务：成功1/失败2" in text
    assert "✅ 调研A" in text and "A结论" in text
    assert "❌ 调研B" in text and "RuntimeError: x" in text
    assert "⏱️ 调研C" in text
    assert "不要把失败子任务当成已完成" in text
    # 输入顺序保留
    assert text.index("调研A") < text.index("调研B") < text.index("调研C")


def test_merge_all_ok_no_failure_note():
    results = [SubagentResult("s1", "g", SubagentStatus.OK, output="o")]
    text = SubagentManager.merge(results)
    assert "成功1/失败0" in text
    assert "不要把失败子任务当成已完成" not in text


# ── 持久化与观测 ────────────────────────────────────────────────


def test_runs_persisted_and_snapshot(tmp_path):
    async def runner(st: Subtask) -> str:
        if st.goal == "bad":
            raise ValueError("坏")
        return "好" * 10

    mgr = _mk_mgr(runner, tmp_path)
    asyncio.run(mgr.run([{"goal": "good"}, {"goal": "bad"}], parent_session_id="om-p"))
    rows = mgr.recent()
    assert len(rows) == 2
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["failed", "ok"]
    for r in rows:
        assert r["parent_session_id"] == "om-p"
        assert r["created_at"]
    snap = mgr.snapshot()
    assert snap["total_workers"] == 2 and snap["ok"] == 1 and snap["failed"] == 1


def test_record_db_failure_does_not_block(tmp_path):
    async def runner(st: Subtask) -> str:
        return "ok"

    # db_path 指向目录 → sqlite 打不开 → _record 只 warning 不抛
    mgr = SubagentManager(runner=runner, db_path=str(tmp_path))
    results = asyncio.run(mgr.run([{"goal": "g"}]))
    assert results[0].status == SubagentStatus.OK


# ── 默认 runner（stub agent，不打真实 LLM）────────────────────────


def _mk_fake_agent(tmp_path, reply="结论X", stop_reason="end_turn", mode="ok"):
    db_path = tmp_path / "agent.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE agent_messages ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, role TEXT, content TEXT)"
    )
    conn.commit()
    conn.close()

    class FakeAgent:
        def __init__(self):
            self.sessions = {}

        def _get_db(self):
            return sqlite3.connect(db_path)

        async def new_session(self, cwd="/", mcp_servers=None, field_meta=None, **kw):
            sid = f"om-w{len(self.sessions) + 1}"
            self.sessions[sid] = {"messages": []}
            return SimpleNamespace(session_id=sid)

        async def prompt(self, blocks, session_id):
            assert blocks[0]["type"] == "text"
            assert SUBAGENT_PROMPT_MARKER in blocks[0]["text"]
            if mode == "ok":
                c = self._get_db()
                c.execute(
                    "INSERT INTO agent_messages (session_id, role, content) VALUES (?,?,?)",
                    (session_id, "assistant", reply),
                )
                c.commit()
                c.close()
            elif mode == "llm_failure":
                c = self._get_db()
                c.execute(
                    "INSERT INTO agent_messages (session_id, role, content) VALUES (?,?,?)",
                    (session_id, "assistant", "[LLM错误: 402 余额不足]"),
                )
                c.commit()
                c.close()
            elif mode == "empty":
                c = self._get_db()
                c.execute(
                    "INSERT INTO agent_messages (session_id, role, content) VALUES (?,?,?)",
                    (session_id, "assistant", "   "),
                )
                c.commit()
                c.close()
            return SimpleNamespace(stop_reason=stop_reason)

    return FakeAgent()


def test_default_runner_success(tmp_path):
    agent = _mk_fake_agent(tmp_path, reply="结论X")
    out = asyncio.run(
        default_subagent_runner(Subtask(goal="g"), agent_getter=lambda: agent)
    )
    assert out == "结论X"


def test_default_runner_refusal_raises(tmp_path):
    agent = _mk_fake_agent(tmp_path, stop_reason="refusal")
    with pytest.raises(RuntimeError, match="被拒"):
        asyncio.run(
            default_subagent_runner(Subtask(goal="g"), agent_getter=lambda: agent)
        )


def test_default_runner_llm_failure_raises(tmp_path):
    agent = _mk_fake_agent(tmp_path, mode="llm_failure")
    with pytest.raises(RuntimeError, match="LLM级失败"):
        asyncio.run(
            default_subagent_runner(Subtask(goal="g"), agent_getter=lambda: agent)
        )


def test_default_runner_zero_output_raises(tmp_path):
    agent = _mk_fake_agent(tmp_path, mode="empty")
    with pytest.raises(RuntimeError, match="零输出"):
        asyncio.run(
            default_subagent_runner(Subtask(goal="g"), agent_getter=lambda: agent)
        )


def test_default_runner_truncates_with_self_report(tmp_path):
    agent = _mk_fake_agent(tmp_path, reply="长" * (MAX_WORKER_OUTPUT_CHARS + 500))
    out = asyncio.run(
        default_subagent_runner(Subtask(goal="g"), agent_getter=lambda: agent)
    )
    assert "输出过长已截断" in out
    assert len(out) < MAX_WORKER_OUTPUT_CHARS + 100


# ── soulmate_agent 接线护栏 + 真实dispatch烟测 ────────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "delegate_tasks"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "delegate_tasks"') >= 2
    # 分发helper + lazy-init + 顶部import
    assert "async def _subagent_dispatch" in src
    assert "_get_subagent_manager" in src
    assert "from agent.subagent_manager import" in src
    # 系统提示工具清单
    assert "delegate_tasks: 子代理并行执行" in src


def test_subagent_dispatch_smoke(monkeypatch, tmp_path):
    """__new__构造+lazy-init真实dispatch烟测：成功/失败/校验错误三态。"""

    async def runner(st: Subtask) -> str:
        if "炸" in st.goal:
            raise RuntimeError("子代理炸了")
        return f"完成:{st.goal}"

    mgr = SubagentManager(runner=runner, db_path=str(tmp_path / "r.db"))
    monkeypatch.setattr(sm, "get_subagent_manager", lambda: mgr)

    import agent.soulmate_agent as sa

    monkeypatch.setattr(sa, "_get_subagent_manager", lambda: mgr)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = asyncio.run(agent._subagent_dispatch(
        {"tasks": [{"goal": "调研A"}, {"goal": "炸"}], "parent_goal": "对比"},
        session_id="om-p",
    ))
    assert "2个子任务：成功1/失败1" in out
    assert "完成:调研A" in out
    assert "子代理炸了" in out

    # 校验错误 → "错误: " 前缀
    out = asyncio.run(agent._subagent_dispatch({"tasks": []}))
    assert out.startswith("错误: ")
    out = asyncio.run(agent._subagent_dispatch({"tasks": [{"goal": ""}] }))
    assert out.startswith("错误: ")
    out = asyncio.run(agent._subagent_dispatch({"tasks": ["x"]}))
    assert out.startswith("错误: ")

    # lazy-init真的挂到了实例上
    assert getattr(agent, "_subagent_manager", None) is mgr
