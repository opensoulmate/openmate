"""
agent/group_orchestrator.py（路线图 5.3 AI群组任务编排）测试。

覆盖：编排主流程（提交→触发→有界轮询→收集→确定性合并）、失败隔离
（execute 失败不阻塞兄弟）、409容忍、无agent显式失败、事件流 finished/failed
判定、等待预算耗尽=TIMEOUT显式标注不假装成功、merge 顺序/截断自报/失败警示、
校验与错误面、list_groups/task_status 只读查询、soulmate_agent 接线护栏+
__new__真实dispatch烟测。传输层全部注入 fake（零网络）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import agent.group_orchestrator as go
from agent.group_orchestrator import (
    DEFAULT_WAIT_S,
    MAX_RESULT_CHARS,
    GroupOrchestrator,
    GroupRunReport,
    GroupTaskStatus,
    SubtaskOutcome,
)


# ── fake 传输层 ────────────────────────────────────────────────


def _tasks_payload(subtasks):
    """ai_groups list_tasks 形状：主任务带 subtasks 数组。"""
    return [{
        "id": "t1",
        "goal": "总目标",
        "status": "planning",
        "subtasks": subtasks,
    }]


def make_transport(subtasks, *, exec_status=202, events=None, submit_status=200,
                   exec_statuses=None):
    """构造记录调用的 fake transport。exec_statuses: {subtask_id: status} 覆盖。"""
    calls: list = []
    exec_statuses = exec_statuses or {}

    async def transport(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "POST" and path == "/api/ai-groups/g1/tasks":
            return submit_status, (
                {"task_id": "t1", "subtasks_count": len(subtasks), "status": "planning"}
                if submit_status in (200, 201) else {"detail": "boom"}
            )
        if method == "POST" and "/execute" in path:
            st_id = path.split("/tasks/")[1].split("/")[0]
            return exec_statuses.get(st_id, exec_status), {"status": "executing"}
        if method == "GET" and "/events" in path:
            return 200, (events if events is not None else [{"event_type": "finished"}])
        if method == "GET" and path == "/api/ai-groups/g1/tasks":
            return 200, _tasks_payload(subtasks)
        if method == "GET" and path == "/api/ai-groups":
            return 200, [{"id": "g1", "name": "核心组", "status": "active",
                          "description": "测试群组"}]
        return 404, {"detail": "not found"}

    return transport, calls


def run(coro):
    return asyncio.run(coro)


# ── 编排主流程 ─────────────────────────────────────────────────


def test_orchestrate_happy_path_order_and_sections():
    subtasks = [
        {"id": "s1", "goal": "规划：分析需求", "assigned_agent_id": "advisor-1",
         "status": "scored", "result": "方案已产出", "quality_score": 8.5},
        {"id": "s2", "goal": "执行：完成功能", "assigned_agent_id": "executor-1",
         "status": "scored", "result": "功能已实现", "quality_score": 9.0},
    ]
    transport, calls = make_transport(subtasks)
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)
    report = run(orch.orchestrate("g1", "做一个登录页", wait_s=5))
    assert report.task_id == "t1"
    assert [o.subtask_id for o in report.subtasks] == ["s1", "s2"]  # 按输入顺序
    assert all(o.status == GroupTaskStatus.OK for o in report.subtasks)
    text = orch.merge(report)
    assert "📋 AI群组任务编排结果" in text
    assert "✅ 成功（2）" in text
    assert "方案已产出" in text and "功能已实现" in text
    assert "不要把失败子任务" not in text  # 全成功无失败警示
    # 每个子任务都被触发执行
    executed = [c[1] for c in calls if c[0] == "POST" and "/execute" in c[1]]
    assert len(executed) == 2


def test_execute_failure_isolated():
    subtasks = [
        {"id": "s1", "goal": "任务A", "assigned_agent_id": "a1", "status": "scored"},
        {"id": "s2", "goal": "任务B", "assigned_agent_id": "a2", "status": "scored"},
    ]
    transport, calls = make_transport(subtasks, exec_statuses={"s1": 500})
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)
    report = run(orch.orchestrate("g1", "目标X", wait_s=5))
    by_id = {o.subtask_id: o for o in report.subtasks}
    assert by_id["s1"].status == GroupTaskStatus.FAILED
    assert "HTTP 500" in by_id["s1"].note
    assert by_id["s2"].status == GroupTaskStatus.OK  # 兄弟不受影响
    # 兄弟确实被触发执行
    assert any(c[1].endswith("/s2/execute") for c in calls)
    text = orch.merge(report)
    assert "❌ 失败/未完成（1）" in text
    assert "⚠️ 以下子任务失败/超时，不要把失败子任务当成已完成" in text


def test_conflict_409_tolerated_as_inflight():
    subtasks = [
        {"id": "s1", "goal": "任务A", "assigned_agent_id": "a1",
         "status": "scored", "result": "完成内容", "quality_score": 7},
    ]
    transport, _ = make_transport(subtasks, exec_status=409)
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)
    report = run(orch.orchestrate("g1", "目标X", wait_s=5))
    assert report.subtasks[0].status == GroupTaskStatus.OK


def test_no_agent_marks_failed_and_siblings_continue():
    subtasks = [
        {"id": "s1", "goal": "任务A", "assigned_agent_id": None, "status": "scored"},
        {"id": "s2", "goal": "任务B", "assigned_agent_id": "a2", "status": "scored"},
    ]
    transport, calls = make_transport(subtasks)
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)
    report = run(orch.orchestrate("g1", "目标X", wait_s=5))
    by_id = {o.subtask_id: o for o in report.subtasks}
    assert by_id["s1"].status == GroupTaskStatus.FAILED
    assert "无可用agent" in by_id["s1"].note
    assert by_id["s2"].status == GroupTaskStatus.OK
    # s1 不发 execute（没有agent可派）
    assert not any(c[1].endswith("/s1/execute") for c in calls)


def test_failed_event_marks_failed_even_if_scored():
    subtasks = [
        {"id": "s1", "goal": "任务A", "assigned_agent_id": "a1",
         "status": "scored", "result": "", "quality_score": 0},
    ]
    transport, _ = make_transport(subtasks, events=[
        {"event_type": "started"}, {"event_type": "failed"},
    ])
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)
    report = run(orch.orchestrate("g1", "目标X", wait_s=5))
    assert report.subtasks[0].status == GroupTaskStatus.FAILED
    assert "failed" in report.subtasks[0].note


def test_timeout_marked_not_pretend_success(monkeypatch):
    monkeypatch.setattr(go, "MIN_WAIT_S", 0.05)
    subtasks = [
        {"id": "s1", "goal": "任务A", "assigned_agent_id": "a1",
         "status": "executing"},  # 永不到终态
    ]
    transport, _ = make_transport(subtasks)
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)
    report = run(orch.orchestrate("g1", "目标X", wait_s=0.05))
    assert report.timed_out is True
    assert report.subtasks[0].status == GroupTaskStatus.TIMEOUT
    text = orch.merge(report)
    assert "timeout" in text
    assert "⚠️ 以下子任务失败/超时" in text


def test_submit_failure_raises():
    subtasks = [{"id": "s1", "goal": "A", "assigned_agent_id": "a1", "status": "pending"}]
    transport, _ = make_transport(subtasks, submit_status=500)
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)
    with pytest.raises(RuntimeError):
        run(orch.orchestrate("g1", "目标X"))


# ── 校验与合并 ─────────────────────────────────────────────────


def test_validation_rejects_empty_inputs():
    orch = GroupOrchestrator(transport=make_transport([])[0], poll_interval_s=0.01)
    with pytest.raises(ValueError):
        run(orch.orchestrate("", "目标"))
    with pytest.raises(ValueError):
        run(orch.orchestrate("g1", "   "))
    with pytest.raises(ValueError):
        run(orch.task_status("", "t1"))
    with pytest.raises(ValueError):
        run(orch.task_status("g1", ""))


def test_merge_deterministic_and_truncation_self_report():
    long_result = "结" * (MAX_RESULT_CHARS + 500)
    report = GroupRunReport(
        group_id="g1", task_id="t1", goal="目标",
        subtasks=[
            SubtaskOutcome("s2", "a2", "任务B", GroupTaskStatus.OK,
                           result=long_result, quality_score=8),
            SubtaskOutcome("s1", "a1", "任务A", GroupTaskStatus.FAILED,
                           note="execute请求失败"),
        ],
    )
    m1 = GroupOrchestrator.merge(report)
    m2 = GroupOrchestrator.merge(report)
    assert m1 == m2  # 确定性
    assert f"…（截断，原长{len(long_result)}字）" in m1
    assert "不要把失败子任务当成已完成" in m1


def test_merge_empty_subtasks():
    text = GroupOrchestrator.merge(
        GroupRunReport(group_id="g1", task_id="t1", goal="目标"))
    assert "无子任务" in text


# ── 只读查询面 ─────────────────────────────────────────────────


def test_list_groups_formats_and_empty():
    transport, _ = make_transport([])
    orch = GroupOrchestrator(transport=transport)
    text = run(orch.list_groups())
    assert "核心组" in text and "g1" in text

    async def empty_transport(method, path, payload=None):
        return 200, []
    text2 = run(GroupOrchestrator(transport=empty_transport).list_groups())
    assert "为空" in text2

    async def bad_transport(method, path, payload=None):
        return 500, {"detail": "boom"}
    text3 = run(GroupOrchestrator(transport=bad_transport).list_groups())
    assert text3.startswith("[错误]")


def test_task_status_formats_subtasks():
    subtasks = [
        {"id": "s1", "goal": "规划：分析", "assigned_agent_id": "a1", "status": "scored"},
        {"id": "s2", "goal": "执行：实现", "assigned_agent_id": "a2", "status": "executing"},
    ]
    transport, _ = make_transport(subtasks)
    orch = GroupOrchestrator(transport=transport)
    text = run(orch.task_status("g1", "t1"))
    assert "任务 t1" in text
    assert "s1" in text and "s2" in text
    text2 = run(orch.task_status("g1", "不存在"))
    assert text2.startswith("[错误] 任务不存在")


# ── soulmate_agent 接线护栏 + 真实dispatch烟测 ─────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "group_task"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "group_task"') >= 2
    # 分发helper + lazy-init + 顶部import
    assert "async def _group_dispatch" in src
    assert "_get_group_orchestrator" in src
    assert "from agent.group_orchestrator import" in src
    # 系统提示工具清单
    assert "group_task: AI群组任务编排" in src


def test_group_dispatch_smoke(monkeypatch):
    """__new__构造+lazy-init真实dispatch烟测：查询/校验错误/未知action三态。"""
    subtasks = [
        {"id": "s1", "goal": "规划：分析需求", "assigned_agent_id": "a1",
         "status": "scored", "result": "方案OK", "quality_score": 8},
    ]
    transport, _ = make_transport(subtasks)
    orch = GroupOrchestrator(transport=transport, poll_interval_s=0.01)

    import agent.soulmate_agent as sa

    monkeypatch.setattr(sa, "_get_group_orchestrator", lambda: orch)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = run(agent._group_dispatch({"action": "list_groups"}))
    assert "核心组" in out

    out2 = run(agent._group_dispatch(
        {"action": "orchestrate", "group_id": "g1", "goal": "做一个登录页"}))
    assert "📋 AI群组任务编排结果" in out2
    assert "方案OK" in out2

    out3 = run(agent._group_dispatch({"action": "orchestrate", "group_id": "", "goal": "x"}))
    assert out3.startswith("错误:")

    out4 = run(agent._group_dispatch({"action": "bogus"}))
    assert "未知 action" in out4
