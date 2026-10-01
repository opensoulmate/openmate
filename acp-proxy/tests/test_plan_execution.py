"""任务计划执行链 P0 状态语义修复 — 回归测试（真实 _prompt_inner 路径）

对象：soulmate_agent._prompt_inner 复杂任务执行环（plan→逐步执行→自省→动态重规划→报告）。
本轮修复的六个缺口（全部为静默错误，逐项锁死）：
1. Bug-D replan(action=replan)替换后续步骤后仍执行旧列表（enumerate绑定旧list对象，
   新步骤永不执行）→ while按索引推进+每轮重取plan.subtasks
2. Bug-C retry结果跳过自省直接SUCCESS → retry同样过SelfReflector
3. Bug-B retry结局落库被continue跳过（断点续跑丢结局）→ 所有路径统一save_plan
4. Bug-A 含FAILED步骤的计划被标completed（状态说谎，失败步骤脱离断点续跑）→
   终局状态按步骤结局诚实判定
5. Bug-E 报告图标str(enum)="StepStatus.X"恒命中❓ → 用枚举value
6. Bug-F save_plan不删replan移除的步骤行，load_plan"复活"旧步骤 → 全量快照语义

驱动方式：真实 TaskPlanner.replan / SelfReflector / TaskStore(tmp sqlite) +
FakeLLM步进驱动真实 _run_llm_with_tools；httpx整体替身（9模块注入/DAG零外部副作用）。
"""

import asyncio
import inspect
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.soulmate_agent import SoulMateAgent
from agent.task_engine import (
    SelfReflector,
    StepStatus,
    SubTask,
    TaskPlan,
    TaskPlanner,
    TaskStore,
)
from tests.test_steering import make_agent, run


# ════════════════════════════════════════════════════════════════
# 测试替身
# ════════════════════════════════════════════════════════════════

class FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class FakeAsyncClient:
    """httpx.AsyncClient替身：9模块注入/DAG规划/标题同步全200空载荷——
    测试零外部副作用（不对跑着的opensoul发真实请求）"""

    calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None, **kw):
        FakeAsyncClient.calls.append(url)
        return FakeResp(200, {})

    async def get(self, url, timeout=None, **kw):
        FakeAsyncClient.calls.append(url)
        return FakeResp(200, {})

    async def patch(self, url, json=None, timeout=None, **kw):
        FakeAsyncClient.calls.append(url)
        return FakeResp(200, {})


class ScriptedLLM:
    """TaskPlanner/SelfReflector 的 llm_call_fn：按序返回脚本文本"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, messages):
        self.calls.append(messages)
        return self.responses.pop(0) if self.responses else '{"action": "retry"}'


JUDGE_CONTINUE = '{"action": "continue", "reason": "续跑"}'


def executed_descriptions(agent):
    """从FakeLLM记录的真实请求里提取每轮'当前子任务'描述=实际执行序列"""
    out = []
    for call in agent.llm_engine.seen_messages:
        for m in call:
            c = m.get("content") or ""
            if isinstance(c, str) and "当前子任务：" in c:
                out.append(c.split("当前子任务：", 1)[1].split("\n", 1)[0])
    return out


def build_env(tmp_path, monkeypatch, steps, llm_scripts, llm_call_responses,
              pre_status=None):
    """构造可驱动 _prompt_inner 复杂任务路径的 agent（真实plan执行链 + 补全外围依赖）"""
    monkeypatch.setenv("OPENSOUL_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    agent = make_agent(tmp_path, scripts=llm_scripts)

    # _save_message / _recent_trace_entries 的落库
    db_path = tmp_path / "agent.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_messages "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, "
        "content TEXT, timestamp REAL)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS agent_sessions "
        "(id TEXT PRIMARY KEY, message_count INTEGER DEFAULT 0, last_activity_at REAL)")
    conn.commit()
    conn.close()
    agent._db_path = str(db_path)

    agent.sessions = {"s1": {"workspace": "/tmp", "messages": [], "title": "T"}}
    agent._turn_boundary = {}
    agent._intent_clf = SimpleNamespace(classify=lambda t: SimpleNamespace(
        intent=SimpleNamespace(value="chat"), confidence=0.9, suggested_tools=[]))

    async def _noop(*a, **k):
        return None

    agent._event_bus = SimpleNamespace(emit=_noop)
    agent._pref_learner.learn_from_message.return_value = []
    agent._knowledge_graph = SimpleNamespace(extract_from_text=lambda *a, **k: {})
    agent._skill_manager = SimpleNamespace(
        search_skills=lambda *a, **k: [],
        record_usage=lambda *a, **k: None,
        try_learn_skill=_noop,
    )
    agent._context_budget = SimpleNamespace(
        calibration_factor=1.0,
        manage=lambda m, max_tokens=None: m,
        model_history_target=lambda **k: 8000,
    )
    agent._token_attr_ledger = SimpleNamespace(calibration_factor=lambda: 1.0)
    agent._task_state_manager = SimpleNamespace(
        get_current_task=lambda *a, **k: None,
        create_task=lambda *a, **k: None,
        complete_task=lambda *a, **k: None,
        update_activity=lambda *a, **k: None,
    )
    agent._observability = MagicMock()
    # trajectory span写入器（build_env绕过__init__手工搭agent，需补新组件；
    # no-op空root=与"start_turn失败→record/end跳过"同语义，plan执行链测试
    # 不依赖trajectory落库，此处保持确定性零网络）
    class _NoopTraj:
        async def start_turn(self, *a, **k):
            return ""

        async def record_llm_call(self, *a, **k):
            return ""

        async def end_turn(self, *a, **k):
            return None

    agent._traj_recorder = _NoopTraj()  # type: ignore[assignment]  # 测试替身
    agent._checkpoint_mgr = SimpleNamespace(create_checkpoint=lambda **k: None)

    # 真实任务规划链：TaskPlanner（replan走LLM脚本）+ TaskStore(tmp sqlite)
    agent._task_planner = TaskPlanner(llm_call_fn=ScriptedLLM(llm_call_responses))
    agent._self_reflector = SelfReflector(llm_call_fn=ScriptedLLM([]))

    plan = TaskPlan(id="plan-t1", session_id="s1", goal="总目标", subtasks=[
        SubTask(id=sid, description=desc) for sid, desc in steps
    ])
    if pre_status:
        for s in plan.subtasks:
            if s.id in pre_status:
                s.status = pre_status[s.id]
    agent._task_planner.store.save_plan(plan)
    return agent, plan


def run_prompt(agent):
    return run(agent._prompt_inner([{"type": "text", "text": "请执行这个多步骤任务"}], "s1"))


def last_assistant_text(agent):
    msgs = agent.sessions["s1"]["messages"]
    for m in reversed(msgs):
        if m["role"] == "assistant":
            return m["content"]
    return ""


REPLAN_REPLACE = (
    '{"action": "replan", "reason": "B方式不对，换两个新步骤", "new_subtasks": ['
    '{"id": "step_2b", "description": "B2"}, {"id": "step_3b", "description": "C2"}]}'
)
REPLAN_NO_NEW = '{"action": "replan", "reason": "没有新步骤", "new_subtasks": []}'
RETRY = '{"action": "retry", "reason": "换个方式重试"}'


# ════════════════════════════════════════════════════════════════
# 1. Bug-D + Bug-F：replan替换后续步骤 → 新步骤执行/旧步骤不执行/持久化不复活
# ════════════════════════════════════════════════════════════════

class TestReplanListReplacement:
    def test_new_steps_executed_stale_steps_not_executed(self, tmp_path, monkeypatch):
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B"), ("step_3", "C")],
            llm_scripts=[["ok A"], ["Error: B broke"], ["ok B2"], ["ok C2"]],
            llm_call_responses=[JUDGE_CONTINUE, REPLAN_REPLACE],
        )
        run_prompt(agent)
        done = executed_descriptions(agent)
        # 修复前：B失败→replan换列表，但enumerate仍跑旧列表的C，B2/C2永不执行
        assert done == ["A", "B", "B2", "C2"]
        assert "C" not in done

    def test_replaced_step_rows_not_resurrected(self, tmp_path, monkeypatch):
        """Bug-F：save_plan全量快照——被replan移除的B不留在task_steps"""
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B"), ("step_3", "C")],
            llm_scripts=[["ok A"], ["Error: B broke"], ["ok B2"], ["ok C2"]],
            llm_call_responses=[JUDGE_CONTINUE, REPLAN_REPLACE],
        )
        run_prompt(agent)
        loaded = agent._task_planner.store.load_plan(plan.id)
        assert loaded is not None
        assert [s.id for s in loaded.subtasks] == ["step_1", "step_2b", "step_3b"]
        assert all(s.status == StepStatus.SUCCESS for s in loaded.subtasks)
        assert loaded.status == "completed"


# ════════════════════════════════════════════════════════════════
# 2. Bug-B / Bug-C：retry结局落库 + retry结果过自省
# ════════════════════════════════════════════════════════════════

class TestRetrySemantics:
    def test_retry_success_persisted(self, tmp_path, monkeypatch):
        """Bug-B回归：retry成功结局必须落库（原continue跳过save_plan）"""
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[["Error: A broke"], ["now it works"], ["ok B"]],
            llm_call_responses=[JUDGE_CONTINUE, RETRY],
        )
        run_prompt(agent)
        assert executed_descriptions(agent) == ["A", "A", "B"]
        loaded = agent._task_planner.store.load_plan(plan.id)
        by_id = {s.id: s for s in loaded.subtasks}
        assert by_id["step_1"].status == StepStatus.SUCCESS  # 重试结局已持久化
        assert loaded.status == "completed"

    def test_retry_failure_not_marked_success(self, tmp_path, monkeypatch):
        """Bug-C回归：重试结果同样过SelfReflector——坏结果不得无条件SUCCESS"""
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[["Error: A broke"], ["Error: still broken"], ["ok B"]],
            llm_call_responses=[JUDGE_CONTINUE, RETRY],
        )
        run_prompt(agent)
        loaded = agent._task_planner.store.load_plan(plan.id)
        by_id = {s.id: s for s in loaded.subtasks}
        assert by_id["step_1"].status == StepStatus.FAILED  # 不是SUCCESS
        assert by_id["step_2"].status == StepStatus.SUCCESS


# ════════════════════════════════════════════════════════════════
# 3. Bug-A / Bug-E：终局状态诚实 + 报告图标可读
# ════════════════════════════════════════════════════════════════

class TestFinalStatusAndReport:
    def test_partial_failure_marks_plan_failed_not_completed(self, tmp_path, monkeypatch):
        """Bug-A回归：残留FAILED步骤的计划不得标completed（说谎=失败脱离断点续跑）"""
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A")],
            llm_scripts=[["Error: A broke"], ["Error: still broken"]],
            llm_call_responses=[JUDGE_CONTINUE, RETRY],
        )
        run_prompt(agent)
        loaded = agent._task_planner.store.load_plan(plan.id)
        assert loaded.status == "failed"          # 修复前恒"completed"
        assert "未收敛" in loaded.reflection_report
        assert "step_1=failed" in loaded.reflection_report
        report = last_assistant_text(agent)
        assert "任务终止" in report

    def test_report_icons_use_enum_value(self, tmp_path, monkeypatch):
        """Bug-E回归：图标按枚举value渲染——修复前str(enum)恒❓"""
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[["ok A"], ["Error: B broke"], ["Error: B still broken"]],
            llm_call_responses=[JUDGE_CONTINUE, RETRY],
        )
        run_prompt(agent)
        report = last_assistant_text(agent)
        assert "✅ **步骤1**" in report
        assert "❌ **步骤2**" in report
        assert "❓" not in report

    def test_all_success_marks_completed(self, tmp_path, monkeypatch):
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[["ok A"], ["ok B"]],
            llm_call_responses=[JUDGE_CONTINUE],
        )
        run_prompt(agent)
        loaded = agent._task_planner.store.load_plan(plan.id)
        assert loaded.status == "completed"
        report = last_assistant_text(agent)
        assert "✅ **步骤1**" in report and "✅ **步骤2**" in report


# ════════════════════════════════════════════════════════════════
# 4. 断点续跑 + 死循环防护
# ════════════════════════════════════════════════════════════════

class TestResumeAndTermination:
    def test_completed_steps_skipped_on_resume(self, tmp_path, monkeypatch):
        """断点续跑：已SUCCESS的步骤不重跑（终局守卫）"""
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A"), ("step_2", "B")],
            llm_scripts=[["ok B"]],
            llm_call_responses=[JUDGE_CONTINUE],
            pre_status={"step_1": StepStatus.SUCCESS},
        )
        run_prompt(agent)
        assert executed_descriptions(agent) == ["B"]
        loaded = agent._task_planner.store.load_plan(plan.id)
        assert loaded.status == "completed"

    def test_replan_without_new_steps_terminates(self, tmp_path, monkeypatch):
        """replan未给新步骤：REPLANNED终局守卫推进，不空转（防while死循环）"""
        agent, plan = build_env(
            tmp_path, monkeypatch,
            steps=[("step_1", "A")],
            llm_scripts=[["Error: A broke"]],
            llm_call_responses=[JUDGE_CONTINUE, REPLAN_NO_NEW],
        )
        asyncio.run(asyncio.wait_for(
            agent._prompt_inner([{"type": "text", "text": "请执行这个多步骤任务"}], "s1"),
            timeout=15,
        ))
        assert executed_descriptions(agent) == ["A"]  # 只跑一次，未重入


# ════════════════════════════════════════════════════════════════
# 5. TaskStore全量快照语义（Bug-F单元级）
# ════════════════════════════════════════════════════════════════

class TestStoreSnapshotSemantics:
    def test_save_plan_deletes_removed_step_rows(self, tmp_path):
        store = TaskStore(db_path=str(tmp_path / "t.db"))
        plan = TaskPlan(id="p1", session_id="s1", goal="g", subtasks=[
            SubTask(id="a", description="A"), SubTask(id="b", description="B")])
        store.save_plan(plan)
        plan.subtasks = plan.subtasks[:1]  # 模拟replan移除b
        store.save_plan(plan)
        loaded = store.load_plan("p1")
        assert [s.id for s in loaded.subtasks] == ["a"]

    def test_save_plan_with_no_steps_clears_rows(self, tmp_path):
        store = TaskStore(db_path=str(tmp_path / "t.db"))
        plan = TaskPlan(id="p1", session_id="s1", goal="g", subtasks=[
            SubTask(id="a", description="A")])
        store.save_plan(plan)
        plan.subtasks = []
        store.save_plan(plan)
        loaded = store.load_plan("p1")
        assert loaded.subtasks == []

    def test_roundtrip_status_survives(self, tmp_path):
        """Bug-G回归：save→load步骤状态往返不断裂（str-Enum旧写法曾致load必崩）"""
        store = TaskStore(db_path=str(tmp_path / "t.db"))
        plan = TaskPlan(id="p1", session_id="s1", goal="g", subtasks=[
            SubTask(id="a", description="A")])
        plan.subtasks[0].status = StepStatus.SUCCESS
        store.save_plan(plan)
        loaded = store.load_plan("p1")
        assert loaded.subtasks[0].status == StepStatus.SUCCESS

    def test_legacy_str_enum_rows_coerced(self, tmp_path):
        """旧存档 'StepStatus.PENDING' 形态容错解析（存量库不崩）"""
        import sqlite3 as _sq
        db = str(tmp_path / "t.db")
        store = TaskStore(db_path=db)
        plan = TaskPlan(id="p1", session_id="s1", goal="g", subtasks=[
            SubTask(id="a", description="A")])
        store.save_plan(plan)
        conn = _sq.connect(db)
        conn.execute("UPDATE task_steps SET status = 'StepStatus.PENDING' WHERE id = 'a'")
        conn.commit()
        conn.close()
        loaded = store.load_plan("p1")
        assert loaded.subtasks[0].status == StepStatus.PENDING


# ════════════════════════════════════════════════════════════════
# 6. 接线防死代码（inspect.getsource）
# ════════════════════════════════════════════════════════════════

class TestWiring:
    def test_prompt_inner_uses_while_loop(self):
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert "while idx < len(plan.subtasks):" in src
        assert "for idx, step in enumerate(plan.subtasks):" not in src  # 旧死缺口已移除

    def test_retry_path_reflects_via_self_reflector(self):
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert src.count("self._self_reflector.reflect(") == 2  # 首次 + retry

    def test_final_status_uses_unresolved_check(self):
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert "unresolved" in src
        assert "plan.status = \"completed\"" in src

    def test_icon_uses_enum_value(self):
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert "getattr(step.status, \"value\", step.status)" in src

    def test_store_save_plan_snapshot_delete_wired(self):
        src = inspect.getsource(TaskStore.save_plan)
        assert "DELETE FROM task_steps" in src
