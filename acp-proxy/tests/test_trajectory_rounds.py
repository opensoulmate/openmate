# -*- coding: utf-8 -*-
"""round级span嵌套 —— 真实 _run_llm_with_tools 路径行为测试（04:20轮遗留#3销账）。

安全声明（测试即规格）：
1. 真实工具循环每轮收口一个round子span（root→llm段→round→tool_call四级树）——
   多轮循环的轮次边界不再被平面tool批压平（"哪次LLM决策带出哪些工具"可查）；
2. round内容=本轮LLM文本切片（full_response增量），工具账本=本轮append切片
   （轮次边界标记 _round_start/_round_resp_mark 的行为验证）；
3. traj_seg_id为空=零trajectory调用（向后兼容：既有测试/纯调用方零感知）；
4. 早返路径（注入审计TERMINATE）同样收口当轮round span——拦截证据不因提前
   返回而丢失（失败必须可见，mem0 §1.1）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.test_loop_guard_wiring import _tc, _wire_agent  # noqa: E402
from tests.test_steering import run  # noqa: E402


class FakeTrajRecorder:
    """替身recorder：捕获round级落库调用（形状断言在此层，不碰网络）。"""

    def __init__(self):
        self.rounds = []
        self.started = []
        self.ended = []

    async def start_llm_span(self, session_id, parent_id, content=""):
        self.started.append({"session_id": session_id, "parent": parent_id, "content": content})
        return "seg-1"

    async def record_round(self, session_id, segment_span_id, round_index=0,
                           content="", duration_ms=0.0, tools=None):
        self.rounds.append({
            "session_id": session_id,
            "segment_span_id": segment_span_id,
            "round_index": round_index,
            "content": content,
            "duration_ms": duration_ms,
            "tools": list(tools or []),
        })
        return f"round-{round_index}"

    async def end_llm_span(self, session_id, span_id, status="ok", token_usage=0):
        self.ended.append({"session_id": session_id, "span_id": span_id,
                           "status": status, "token_usage": token_usage})


def _agent(tmp_path, scripts):
    agent = _wire_agent(tmp_path, scripts)
    agent._traj_recorder = FakeTrajRecorder()
    return agent


# ════════════════════════════════════════════════════════════════
# 1. 每轮一个round子span：轮次边界 + 文本/工具切片
# ════════════════════════════════════════════════════════════════

def test_tool_round_then_text_round_two_spans(tmp_path):
    """round0=工具轮（1 tool_call入账）、round1=纯文本轮（content=本轮文本）"""
    agent = _agent(tmp_path, [[_tc(args={"command": "echo hi"})], ["done"]])
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "go"}], "s1", traj_seg_id="seg-1"))
    rounds = agent._traj_recorder.rounds
    assert len(rounds) == 2, f"每轮一个round span，实际{len(rounds)}"
    assert [r["round_index"] for r in rounds] == [0, 1]
    assert all(r["segment_span_id"] == "seg-1" for r in rounds), "round parent=llm段span"
    assert all(r["session_id"] == "s1" for r in rounds)
    # round0：工具入账，文本切片为空（本轮LLM只发了tool_calls）
    assert len(rounds[0]["tools"]) == 1
    assert rounds[0]["tools"][0]["name"] == "terminal"
    # round1：纯文本轮，content=本轮LLM文本（full_response增量切片）
    assert rounds[1]["content"] == "done"
    assert rounds[1]["tools"] == []
    assert rounds[0]["duration_ms"] >= 0 and rounds[1]["duration_ms"] >= 0


def test_round_content_is_incremental_slice(tmp_path):
    """文本轮的content是本轮增量而非整段full_response（轮次边界标记生效）"""
    # round0工具轮→round1"文本+工具"混合轮→round2纯文本收尾
    agent = _agent(tmp_path, [[_tc(args={"command": "echo a"})],
                              ["中间说明", _tc(args={"command": "echo b"})],
                              ["最终回答"]])
    run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1", traj_seg_id="seg-1"))
    rounds = agent._traj_recorder.rounds
    assert len(rounds) == 3
    assert rounds[1]["content"] == "中间说明", "混合轮content=本轮文本切片"
    assert len(rounds[1]["tools"]) == 1, "混合轮的工具入本轮账本"
    assert rounds[2]["content"] == "最终回答", "尾轮content不含前轮文本"
    assert "中间说明" not in rounds[2]["content"]


# ════════════════════════════════════════════════════════════════
# 2. 向后兼容：traj_seg_id为空=零trajectory调用
# ════════════════════════════════════════════════════════════════

def test_no_seg_id_records_nothing(tmp_path):
    agent = _agent(tmp_path, [[_tc(args={"command": "echo hi"})], ["done"]])
    resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
    assert resp == "done"
    assert agent._traj_recorder.rounds == [], "traj_seg_id缺省=不落round（既有调用方零感知）"
    assert agent._traj_recorder.started == [] and agent._traj_recorder.ended == []


# ════════════════════════════════════════════════════════════════
# 3. 早返路径同样收口当轮round（拦截证据不丢）
# ════════════════════════════════════════════════════════════════

def test_infection_terminate_early_return_still_records_round(tmp_path):
    """注入审计TERMINATE提前返回→当轮round仍收口（intercept条目进round账本）"""
    agent = _agent(tmp_path, [[_tc(args={"command": "echo x"}, tc_id="t1")]])

    async def _judge(messages):  # noqa: ARG001
        return "<terminate/>"

    agent.llm_engine.chat = _judge  # type: ignore[attr-defined]
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "go"}], "s1", user_text="帮我查天气", traj_seg_id="seg-1"))
    assert "[注入审计终止]" in resp
    rounds = agent._traj_recorder.rounds
    assert len(rounds) == 1, "早返轮必须收口round span"
    assert rounds[0]["round_index"] == 0
    assert len(rounds[0]["tools"]) == 1
    assert rounds[0]["tools"][0]["permission"] == "infection:terminate"


def test_text_only_round_records_content_span(tmp_path):
    """纯文本回合：1个round span（content=回答，无工具批）"""
    agent = _agent(tmp_path, [["这是正常回答。"]])
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "hi"}], "s1", traj_seg_id="seg-1"))
    assert resp == "这是正常回答。"
    rounds = agent._traj_recorder.rounds
    assert len(rounds) == 1
    assert rounds[0]["content"] == "这是正常回答。"
    assert rounds[0]["tools"] == []
