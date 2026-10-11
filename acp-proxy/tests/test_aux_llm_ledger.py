# -*- coding: utf-8 -*-
"""14.4 落账补洞 — 辅助LLM往返进 trajectory_events（测试即规格）。

背景（周期#85 14.2 归因实证）：planner任务拆解/任务裁决/注入审计判词/标题生成/
技能语义改写等主聊天循环之外的LLM往返零落账，turn_timing_report 四段归因的
"⑤未归属空洞"首段 6-32s（9/9 样本）只能靠 journal 事后解释。

安全声明（测试即规格）：
1. 语义面零变化——_aux_llm_chat 返回值/异常与直接 llm_engine.chat 逐字等价，
   落账是旁路，记账失败绝不阻塞（CowAgent 记账哲学）；
2. 不伪造session——无落账上下文（回合外直调/测试）只跳过落账；
3. 标签确定性——从消息面特征词推断（不赌LLM自报），兜底 plan_call 如实不冒称；
4. 可变ctx共享——fire-and-forget 标题任务先于回合根span创建也能拿到后补的
   root_id（纯ContextVar值做不到，dict引用才可见）；
5. 归因仪表完备——"[aux]"行计入时间覆盖（空洞↓）且与主轮分列（llm_total_s
   不被辅助往返污染）；
6. 接线钉——_prompt_inner 设置ctx、root_id补写、五个调用点全部走 _aux_llm_chat
   （防"写了≠接线了"）。
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.trajectory_recorder import TrajectoryRecorder
from agent import soulmate_agent as sma
from turn_timing_report import report as timing_report


def run(coro):
    return asyncio.run(coro)


class FakePoster:
    """替身传输：捕获payload、按序回吐响应。不碰网络，形状断言在此层。"""

    def __init__(self, responses=None):
        self.calls = []  # (path, payload)
        self.responses = list(responses or [])

    async def __call__(self, path, payload):
        self.calls.append((path, payload))
        if self.responses:
            return self.responses.pop(0)
        return {"id": "evt-x"}


def _rec(fake=None, **kw):
    rec = TrajectoryRecorder(**kw)
    if fake is not None:
        rec._post = fake
    return rec


class FakeEngine:
    """llm_engine.chat 替身：固定回文/可抛异常，记录调用面。"""

    def __init__(self, text="正常回答", exc=None):
        self.text = text
        self.exc = exc
        self.calls = []

    async def chat(self, messages):
        self.calls.append(messages)
        if self.exc is not None:
            raise self.exc
        return self.text


def _agent(engine, recorder):
    ag = sma.SoulMateAgent.__new__(sma.SoulMateAgent)
    ag.llm_engine = engine
    ag._traj_recorder = recorder
    return ag


# ── 1. record_aux_llm_call 事件形状 ────────────────────────────


def test_record_aux_llm_call_shape():
    fake = FakePoster([{"id": "s1"}, {"id": "aux-1"}])
    rec = _rec(fake)
    eid = run(rec.record_aux_llm_call(
        "s1", "plan", duration_ms=1234.5, status="ok", parent_id="root-1"))
    assert eid == "aux-1"
    path, body = fake.calls[-1]
    assert path == "/api/trajectory/sessions/s1/events"
    assert body["event_type"] == "llm_call"
    assert body["content"] == "[aux] plan"
    assert body["metadata"] == {"kind": "aux_llm", "label": "plan"}
    assert body["parent_event_id"] == "root-1"
    assert body["duration_ms"] == 1234.5
    assert body["status"] == "ok"


def test_record_aux_llm_no_parent_is_root_level_and_ensures_session():
    fake = FakePoster([{"id": "s1"}, {"id": "aux-1"}])
    rec = _rec(fake)
    run(rec.record_aux_llm_call("s1", "title", duration_ms=10.0))
    # ensure_session 先行（标题任务可能先于回合根到）；parent 未知挂根层不伪造
    assert fake.calls[0][0] == "/api/trajectory/sessions"
    assert fake.calls[1][1]["parent_event_id"] is None


def test_record_aux_llm_empty_session_noop():
    fake = FakePoster()
    rec = _rec(fake)
    assert run(rec.record_aux_llm_call("", "plan")) == ""
    assert fake.calls == [], "无session不落野行"


def test_record_aux_llm_never_raise():
    async def boom(path, payload):
        raise RuntimeError("opensoul down")

    rec = _rec(boom)
    assert run(rec.record_aux_llm_call("s1", "plan")) == "", "观察面故障绝不冒泡"


# ── 2. _aux_llm_chat：语义面零变化 + 落账旁路 ──────────────────


def test_aux_llm_chat_records_duration_status_and_parent():
    fake = FakePoster([{"id": "s1"}, {"id": "aux-9"}])
    rec = _rec(fake)
    eng = FakeEngine("回答内容")
    ag = _agent(eng, rec)

    async def main():
        tok = sma._AUX_TRAJ_CTX.set({"session_id": "s1", "root_id": "root-1"})
        try:
            return await ag._aux_llm_chat("infection_audit", [{"role": "user", "content": "q"}])
        finally:
            sma._AUX_TRAJ_CTX.reset(tok)

    out = run(main())
    assert out == "回答内容", "返回值与直接 llm_engine.chat 逐字等价"
    body = fake.calls[-1][1]
    assert body["content"] == "[aux] infection_audit"
    assert body["parent_event_id"] == "root-1"
    assert body["duration_ms"] >= 0
    assert body["status"] == "ok"


def test_aux_llm_chat_failure_marker_text_marks_error():
    fake = FakePoster([{"id": "s1"}, {"id": "aux-9"}])
    rec = _rec(fake)
    ag = _agent(FakeEngine("[LLM错误: 402 insufficient_balance]"), rec)

    async def main():
        tok = sma._AUX_TRAJ_CTX.set({"session_id": "s1", "root_id": ""})
        try:
            return await ag._aux_llm_chat("title", [{"role": "user", "content": "q"}])
        finally:
            sma._AUX_TRAJ_CTX.reset(tok)

    out = run(main())
    assert out.startswith("[LLM错误"), "失败文本原样返回（语义面零变化）"
    assert fake.calls[-1][1]["status"] == "error", "失败必须可见"


def test_aux_llm_chat_exception_propagates_but_still_records():
    fake = FakePoster([{"id": "s1"}, {"id": "aux-9"}])
    rec = _rec(fake)
    ag = _agent(FakeEngine(exc=RuntimeError("net down")), rec)

    async def main():
        tok = sma._AUX_TRAJ_CTX.set({"session_id": "s1", "root_id": ""})
        try:
            with pytest.raises(RuntimeError):
                await ag._aux_llm_chat("plan", [{"role": "user", "content": "q"}])
        finally:
            sma._AUX_TRAJ_CTX.reset(tok)

    run(main())
    assert fake.calls[-1][1]["status"] == "error"


def test_aux_llm_chat_without_ctx_skips_record():
    fake = FakePoster()
    rec = _rec(fake)
    ag = _agent(FakeEngine("回文"), rec)
    out = run(ag._aux_llm_chat("plan", [{"role": "user", "content": "q"}]))
    assert out == "回文"
    assert fake.calls == [], "回合外直调不伪造session，只跳过落账"


def test_aux_llm_chat_explicit_session_id_wins():
    fake = FakePoster([{"id": "s1"}, {"id": "aux-1"}])
    rec = _rec(fake)
    ag = _agent(FakeEngine("标题"), rec)

    async def main():
        # 无ctx（如独立调用路径），显式 session_id 仍然落账
        sma._AUX_TRAJ_CTX.set(None)
        return await ag._aux_llm_chat(
            "title", [{"role": "user", "content": "q"}], session_id="s1")

    run(main())
    assert fake.calls[-1][1]["content"] == "[aux] title"


def test_aux_llm_chat_record_failure_does_not_block_semantics():
    async def boom(path, payload):
        raise RuntimeError("opensoul down")

    rec = _rec(boom)
    ag = _agent(FakeEngine("回文"), rec)

    async def main():
        tok = sma._AUX_TRAJ_CTX.set({"session_id": "s1", "root_id": ""})
        try:
            return await ag._aux_llm_chat("plan", [{"role": "user", "content": "q"}])
        finally:
            sma._AUX_TRAJ_CTX.reset(tok)

    assert run(main()) == "回文", "记账失败绝不阻塞（返回值不受影响）"


# ── 3. 标签确定性归类 ──────────────────────────────────────────


def test_aux_llm_label_classifier():
    f = sma.SoulMateAgent._aux_llm_label
    assert f([{"role": "system", "content": "你是任务判断器。…"}]) == "task_judge"
    assert f([{"role": "system", "content": "你是任务重规划器。…"}]) == "replan"
    assert f([{"role": "system", "content": "你是一个自省引擎。…"}]) == "reflect"
    assert f([{"role": "system", "content": "你是一个任务规划器。…needs_planning…"}]) == "plan"
    assert f([{"role": "user", "content": "随便什么"}]) == "plan_call", "兜底如实不冒称"
    assert f([]) == "plan_call"


def test_aux_llm_label_task_engine_prompts_classified():
    """真实 task_engine 提示词常量走 _llm_plan_call 时归类正确（单一真源不漂移）。"""
    from agent.task_engine import PLANNING_PROMPT, REPLAN_PROMPT, REFLECTION_PROMPT

    f = sma.SoulMateAgent._aux_llm_label
    assert f([{"role": "system", "content": PLANNING_PROMPT}]) == "plan"
    assert f([{"role": "system", "content": REPLAN_PROMPT.format(
        plan_json="{}", failed_step="1: x", error_info="e")}]) == "replan"
    assert f([{"role": "system", "content": REFLECTION_PROMPT.format(
        step_description="d", step_result="r", step_expectation="e")}]) == "reflect"


# ── 4. 可变ctx共享（fire-and-forget 标题任务拿到后补 root_id）──


def test_aux_ctx_mutable_root_visible_to_earlier_task():
    async def main():
        ctx = {"session_id": "s1", "root_id": ""}
        tok = sma._AUX_TRAJ_CTX.set(ctx)
        seen = {}

        async def title_task():
            await asyncio.sleep(0.02)  # 跑在 root_id 补写之后
            seen["root"] = (sma._AUX_TRAJ_CTX.get() or {}).get("root_id")

        t = asyncio.create_task(title_task())  # 先于 root_id 补写创建
        ctx["root_id"] = "root-1"
        await t
        sma._AUX_TRAJ_CTX.reset(tok)
        return seen

    assert run(main()) == {"root": "root-1"}, "dict引用共享，后补root_id对已建task可见"


# ── 5. 接线钉（防"写了≠接线了"）──────────────────────────────


def test_soulmate_aux_wiring_pins():
    src = Path(sma.__file__).read_text(encoding="utf-8")
    # ctx 设置在 _prompt_inner 开头（先于标题 create_task/planner 调用）
    assert "_AUX_TRAJ_CTX.set(_aux_ctx)" in src
    assert src.index("_AUX_TRAJ_CTX.set(_aux_ctx)") < src.index(
        "asyncio.create_task(self._generate_session_title("), "ctx必须先于标题任务设置"
    # root_id 在回合根span建立后补写
    assert '_aux_ctx["root_id"] = traj_root_id or ""' in src
    # 五个辅助LLM调用点全部走 _aux_llm_chat（planner/裁决/注入审计/标题/技能改写）
    assert "return await self._aux_llm_chat(self._aux_llm_label(messages), messages)" in src
    assert 'raw = await self._aux_llm_chat("task_judge", judge_messages)' in src
    assert 'return await self._aux_llm_chat("infection_audit", messages)' in src
    assert '"title",' in src and "session_id=session_id," in src
    assert '"skill_sem_rewrite", [{"role": "user", "content": p}]' in src
    # 语义缓存/主循环的 llm_engine.chat 直连只剩 _aux_llm_chat 内部一处
    assert src.count("await self.llm_engine.chat(") == 1
    # import 在文件顶部（仓库铁律）
    assert src.index("import contextvars") < src.index("class SoulMateAgent")


# ── 6. 归因仪表完备：aux 行计入时间覆盖且与主轮分列 ─────────────


def _timing_conn(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE trajectory_events (session_id TEXT, created_at TEXT, "
        "end_at TEXT, event_type TEXT, content TEXT, duration_ms REAL)")
    conn.executemany(
        "INSERT INTO trajectory_events VALUES (?,?,?,?,?,?)", rows)
    return conn


_T0 = "2026-10-11T20:00:00.000000+08:00"
_T5 = "2026-10-11T20:00:05.000000+08:00"
_T12 = "2026-10-11T20:00:12.000000+08:00"
_T13 = "2026-10-11T20:00:13.000000+08:00"


def test_timing_report_aux_covers_gap_and_splits_totals():
    rows = [
        ("s1", _T0, _T13, "user_input", "目标", 13000),
        ("s1", _T5, None, "llm_call", "[aux] plan", 5000),   # 覆盖 T0→T5
        ("s1", _T12, None, "llm_call", "chat_turn", 7000),   # 覆盖 T5→T12
    ]
    rep = timing_report("s1", _timing_conn(rows))
    assert rep["gap_total_s"] == 0, f"aux行计入覆盖后空洞应≈0，实际 {rep['gaps']}"
    assert rep["aux_total_s"] == 5.0
    assert rep["aux_rounds"][0]["preview"] == "[aux] plan"
    assert rep["llm_total_s"] == 7.0, "主轮合计不被辅助往返污染（分列）"
    assert rep["reply_recovery_s"] == 1.0


def test_timing_report_without_aux_row_has_gap():
    rows = [
        ("s1", _T0, _T13, "user_input", "目标", 13000),
        ("s1", _T12, None, "llm_call", "chat_turn", 7000),
    ]
    rep = timing_report("s1", _timing_conn(rows))
    assert rep["gap_total_s"] == 5.0, "无aux行时首段空洞=5s（14.2 形态）"
    assert rep["aux_total_s"] == 0.0
