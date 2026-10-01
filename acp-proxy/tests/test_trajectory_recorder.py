# -*- coding: utf-8 -*-
"""TrajectoryRecorder — 真实回合→opensoul trajectory嵌套span写入端（dev-reports
09-17 15:37轮langfuse三层模型"零生产写入方"缺口销账）。

安全声明（测试即规格）：
1. 观察面故障绝不破坏会话流——_post失败/超时/opensoul没起时全部方法never-raise，
   start_turn返回''后record/end按空id跳过；
2. ensure幂等——同chat session多次start_turn只ensure一次session（跨turn/跨重启
   收敛到一行，api侧session_id幂等语义在opensoul测试钉）；
3. 嵌套血缘单一真源——tool_call事件parent=llm_call span id、llm_call parent=
   turn root id，与store.get_trace_tree父子边同构；
4. 失败必须可见——工具被拒/拦截status=error（mem0 §1.1不静默吞）；
5. 错误风暴熔断——连续同错进入冷却窗口，不给已死的opensoul每个事件白付timeout
   （agno §1.4）；熔断进出各记warning不静默；
6. soulmate真实回合路径接线钉——start_turn/record_llm_call/end_turn在
   _prompt_inner真实执行路径被调用（防"写了≠接线了"）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.trajectory_recorder import TrajectoryRecorder, llm_outcome_status
from agent import soulmate_agent as sma


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


# ── 1. start_turn：ensure→root事件形状 ─────────────────────────


def test_start_turn_shape():
    fake = FakePoster([{"id": "s1"}, {"id": "root-1"}])
    rec = _rec(fake)
    root = run(rec.start_turn("s1", "你好世界"))
    assert root == "root-1"
    assert [c[0] for c in fake.calls] == [
        "/api/trajectory/sessions",
        "/api/trajectory/sessions/s1/events",
    ]
    ensure_body, root_body = fake.calls[0][1], fake.calls[1][1]
    assert ensure_body["session_id"] == "s1" and ensure_body["agent_id"] == "soulmate"
    assert root_body["event_type"] == "user_input"
    assert root_body["metadata"]["kind"] == "turn_root"
    assert root_body["parent_event_id"] is None
    assert root_body["content"] == "你好世界"


def test_ensure_session_idempotent_across_turns():
    fake = FakePoster([{"id": "s1"}, {"id": "root-1"}, {"id": "root-2"}])
    rec = _rec(fake)
    run(rec.start_turn("s1", "turn1"))
    run(rec.start_turn("s1", "turn2"))
    ensure_calls = [c for c in fake.calls if c[0] == "/api/trajectory/sessions"]
    assert len(ensure_calls) == 1, "同session跨turn只ensure一次（幂等缓存）"
    assert [c[1]["event_type"] for c in fake.calls if "events" in c[0]] == [
        "user_input",
        "user_input",
    ]


# ── 2. record_llm_call：llm段+工具批的嵌套血缘 ─────────────────


def test_llm_span_and_tool_children_lineage():
    tools = [
        {"name": "terminal", "arguments": {"command": "ls"}, "result_preview": "a.txt", "permission": "allow"},
        {"name": "write_file", "arguments": {"path": "x"}, "result_preview": "blocked ...", "permission": "deny"},
    ]
    fake = FakePoster([{"id": "llm-1"}, {"recorded": 2, "events": []}])
    rec = _rec(fake)
    span = run(
        rec.record_llm_call("s1", "root-1", content="llm_rounds tools=2",
                            duration_ms=1234.0, token_usage=88, tools=tools)
    )
    assert span == "llm-1"
    llm_body = fake.calls[0][1]
    assert llm_body["event_type"] == "llm_call"
    assert llm_body["parent_event_id"] == "root-1", "llm段parent=turn root（血缘单一真源）"
    assert llm_body["duration_ms"] == 1234.0 and llm_body["token_usage"] == 88
    batch_body = fake.calls[1][1]
    assert fake.calls[1][0] == "/api/trajectory/sessions/s1/events/batch"
    evs = batch_body["events"]
    assert len(evs) == 2
    assert all(e["event_type"] == "tool_call" and e["parent_event_id"] == "llm-1" for e in evs)
    # 失败必须可见：permission=deny → status=error（mem0 §1.1）
    assert evs[0]["status"] == "ok" and evs[1]["status"] == "error"
    assert evs[1]["metadata"]["permission"] == "deny"
    assert evs[0]["metadata"]["name"] == "terminal"


def test_tool_status_mapping():
    st = TrajectoryRecorder._tool_status
    assert st({"permission": "deny", "result_preview": "x"}) == "error"
    assert st({"permission": "infection:terminate", "result_preview": "x"}) == "error"
    assert st({"result_preview": "failed something"}) == "error"
    assert st({"result_preview": "blocked: ..."}) == "error"
    assert st({"permission": "allow", "result_preview": "ok"}) == "ok"
    assert st({}) == "ok"


# ── 2.5 LLM段结局判定（失败必须可见，live E2E抓到的402失真修复）────


def test_llm_outcome_status_failure_markers():
    # live E2E实测形状：402错误以文本chunk成为回复内容
    err402 = '\n[LLM错误: LLM API error 402: {"error": {"code": "402"}}]'
    assert llm_outcome_status(err402) == "error"
    assert llm_outcome_status("[LLM错误: boom]") == "error"
    assert llm_outcome_status("  [LLM响应超时]  ") == "error"
    assert llm_outcome_status("正常回答") == "ok"
    assert llm_outcome_status("") == "ok"
    # 中段失败标记（流式中途挂）也要判error
    assert llm_outcome_status("前半段正常\n[LLM错误: mid-stream]") == "error"
    # 不误伤：正常回答里出现"错误"二字
    assert llm_outcome_status("错误处理的最佳实践是先捕获再重试") == "ok"


def test_tool_batch_capped_at_50():
    tools = [{"name": f"t{i}", "arguments": {}, "result_preview": ""} for i in range(80)]
    fake = FakePoster([{"id": "llm-1"}, {"recorded": 50, "events": []}])
    rec = _rec(fake)
    run(rec.record_llm_call("s1", "root-1", tools=tools))
    assert len(fake.calls[1][1]["events"]) == 50, "病态回合50工具封顶防刷爆观察面"


# ── 3. never-raise：观察面故障不破坏会话流 ─────────────────────


def test_never_raise_on_transport_failure():
    async def boom(path, payload):
        raise RuntimeError("opensoul down")

    rec = TrajectoryRecorder()
    rec._post = boom
    assert run(rec.start_turn("s1", "hi")) == ""
    assert run(rec.record_llm_call("s1", "root-1", tools=[{"name": "t"}])) == ""
    run(rec.end_turn("s1", "root-1"))  # 不抛即过
    fake = FakePoster([None])  # ensure失败→root不发
    rec2 = _rec(fake)
    assert run(rec2.start_turn("s2", "hi")) == ""
    assert len(fake.calls) == 1


def test_empty_ids_noop():
    fake = FakePoster()
    rec = _rec(fake)
    assert run(rec.start_turn("", "hi")) == ""
    assert run(rec.record_llm_call("", "root", tools=[])) == ""
    assert run(rec.record_llm_call("s1", "", tools=[])) == ""
    run(rec.end_turn("", "")) 
    run(rec.end_turn("s1", ""))
    assert fake.calls == [], "空id全部跳过零网络"
    run(rec.end_turn("s1", "root-1"))
    assert fake.calls[0][0] == "/api/trajectory/events/root-1/end"


# ── 4. 错误风暴熔断（agno §1.4）────────────────────────────────


def test_error_storm_breaker_opens_and_recovers():
    rec = TrajectoryRecorder(base_url="http://127.0.0.1:1", timeout=0.3,
                             max_consecutive_failures=3, cooldown_s=60.0)
    for _ in range(3):
        assert run(rec._post("/x", {})) is None
    assert rec.is_paused, "连续3败进入冷却窗口"
    # 冷却中：直接短路，不再打网络
    assert run(rec._post("/x", {})) is None
    assert rec.is_paused
    # 冷却到期（手动拨表）：恢复尝试且成功后清零
    rec._paused_until = 0.0
    rec._post = FakePoster([{"id": "ok"}])
    assert run(rec._post("/x", {})) == {"id": "ok"}
    assert rec._consecutive_failures == 0 and not rec.is_paused


# ── 5. soulmate真实回合路径接线钉（防"写了≠接线了"）───────────


def test_soulmate_wiring_pins():
    src = Path(sma.__file__).read_text(encoding="utf-8")
    # 初始化
    assert "self._traj_recorder = TrajectoryRecorder()" in src
    # 回合根：_prompt_inner在user_text非空后start_turn
    assert "traj_root_id = await self._traj_recorder.start_turn(session_id, user_text)" in src
    # 三条LLM路径（简单分支/plan步骤/plan重试）+ LLM失败可见
    assert src.count("await self._traj_recorder.record_llm_call(") == 4
    assert 'content="llm_call failed", status="error"' in src
    # 收口：正常回合end_turn + 缓存命中回合end_turn
    assert src.count("self._traj_recorder.end_turn(") == 2
    # 失败必须可见：llm段/回合status按llm_outcome_status单一真源判定（非无条件ok）
    assert src.count("status=llm_outcome_status(") == 4
    # 简单分支把工具账本交给了record_llm_call（tools=tool_calls_log）
    assert "tools=tool_calls_log," in src
    # import在文件顶部（仓库铁律）
    import_line = src.index("from agent.trajectory_recorder import TrajectoryRecorder")
    assert import_line < src.index("class SoulMateAgent"), "import必须在类定义之前"
