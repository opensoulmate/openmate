# -*- coding: utf-8 -*-
"""执行旁白（完成度/决定类）路由 —— evo 目标 2026-10-23 回归测试。

目标原文：「回复正文不得混入执行旁白（完成度/决定类），需有回归测试」。

三关（测试即规格）：
1. 拆分语义：旁白行（完成度/决定类）剥出，正文保留；正文中间同类字样不误伤；
   识别不出 → fail-safe 归正文（宁可多显示，不吞用户答案）。
2. 生产者契约：build_self_assess_prompt 必须带 ASSESS_PREFIX 契约 + "不进回复正文"
   约束（生产者改措辞不改常量，测试即红）。
3. 真实路径（_run_llm_with_tools 真实工具循环 + 真实 WS 落库语义的 full_response）：
   自评轮的旁白不进回复正文（resp/推送帧），只进轨迹（trajectory round content）；
   收尾形态的最终总结保留；非自评轮零剥除（不误伤）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.execution_narration import (  # noqa: E402
    ASSESS_PREFIX,
    NarrationSplitter,
    build_self_assess_prompt,
    is_narration_line,
    split_leading_narration,
)
from tests.test_loop_guard_wiring import _tc, _wire_agent  # noqa: E402
from tests.test_steering import run  # noqa: E402
from tests.test_trajectory_rounds import FakeTrajRecorder  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 1. 拆分语义（行级前缀判别 + fail-safe 方向）
# ════════════════════════════════════════════════════════════════

class TestNarrationLineDetect:
    def test_marker_and_item_lines(self):
        assert is_narration_line("【自评】①完成度80%，决定：继续。")
        assert is_narration_line("①距目标的完成度约80%。")
        assert is_narration_line("②没有重复同一失败。")
        assert is_narration_line("③决定：继续/换策略/收尾")
        assert is_narration_line("完成度约80%")
        assert is_narration_line("决定：收尾")

    def test_answer_lines_not_flagged(self):
        # 正文中间/行首的同类字样不误伤（只判行首前缀；①行无旁白关键词=正文）
        assert not is_narration_line("我们决定采用方案A，继续推进。")
        assert not is_narration_line("总结：任务完成，报表已生成。")
        assert not is_narration_line("①苹果 ②香蕉")
        assert not is_narration_line("")


class TestSplitLeadingNarration:
    def test_marker_form_strips_narration_keeps_summary(self):
        body, narr = split_leading_narration(
            "【自评】①完成度100%。②无重复失败。③决定：收尾。\n\n总结：任务完成。")
        assert "【自评】" in narr and "决定" in narr
        assert body.strip() == "总结：任务完成。"
        assert "完成度" not in body

    def test_item_line_form(self):
        body, narr = split_leading_narration(
            "①距目标的完成度约80%。\n②没有重复失败。\n③决定：继续。\n")
        assert "完成度" in narr and "决定" in narr
        assert body == ""

    def test_no_narration_untouched(self):
        body, narr = split_leading_narration("这是正常回答，一词不动。")
        assert body == "这是正常回答，一词不动。"
        assert narr == ""

    def test_mid_text_decision_line_untouched(self):
        # 旁白只取**正文之前的领先块**——正文中间出现"决定："字样不误伤
        body, narr = split_leading_narration("第一段是正文。\n决定：继续推进项目。")
        assert "决定：继续推进项目。" in body
        assert narr == ""

    def test_fail_safe_unrecognized_lines_go_to_body(self):
        # 识别不出的行一律归正文（宁可多显示，不吞用户答案）
        body, narr = split_leading_narration("【奇怪格式】看不出是什么。\n尾巴")
        assert "【奇怪格式】" in body and "尾巴" in body
        assert narr == ""

    def test_streaming_chunk_boundaries(self):
        # chunk 流无分隔符：行被切成多段也要正确拆（按完整行判别）
        sp = NarrationSplitter()
        out_body, out_narr = "", ""
        for c in ["【自评】①完成度", "80% ③决定：继续。\n\n总", "结：完成。"]:
            b, n = sp.feed(c)
            out_body += b
            out_narr += n
        b, n = sp.flush()
        out_body += b
        out_narr += n
        assert "【自评】" in out_narr and "决定：继续" in out_narr
        assert out_body.strip() == "总结：完成。"
        assert "决定" not in out_body


# ════════════════════════════════════════════════════════════════
# 2. 生产者契约（自评请求必须声明"旁白不进正文"）
# ════════════════════════════════════════════════════════════════

class TestProducerContract:
    def test_prompt_carries_marker_and_channel_rule(self):
        p = build_self_assess_prompt(8)
        assert p.startswith("【进展自评】")
        assert ASSESS_PREFIX in p, "请求必须给出自评行的帧首标记（单一真源常量）"
        assert "不会展示给用户" in p and "回复正文" in p, "必须声明旁白不进回复正文"
        assert "完成度" in p and "决定" in p

    def test_prompt_is_injected_verbatim_on_real_path(self, tmp_path):
        # 接线证据：第8轮工具调用后注入的消息=生产者契约原文
        agent = _wire_agent(tmp_path, [[_tc(args={"command": f"echo {i}"})]
                                      for i in range(8)] + [["收尾回答"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        seen = agent.llm_engine.seen_messages
        assert len(seen) >= 9
        injected = [m for m in seen[8] if m.get("role") == "user"
                    and str(m.get("content", "")).startswith("【进展自评】")]
        assert injected, "第8轮后的LLM请求里必须有自评请求（接线）"
        assert ASSESS_PREFIX in injected[0]["content"]


# ════════════════════════════════════════════════════════════════
# 3. 真实路径：_run_llm_with_tools 自评轮旁白路由（正文/轨迹分流）
# ════════════════════════════════════════════════════════════════

class FakeClient:
    """捕获 session_update 推送帧（聊天正文通道的真源）"""

    def __init__(self):
        self.pushed = []

    async def session_update(self, session_id=None, update=None):
        self.pushed.append(update)


def _update_text(u) -> str:
    c = getattr(u, "content", None)
    t = getattr(c, "text", None)
    if isinstance(t, str):
        return t
    return str(u)


def _tool_rounds(n=8):
    return [[_tc(args={"command": f"echo {i}"})] for i in range(n)]


def _agent_with_client(tmp_path, scripts):
    agent = _wire_agent(tmp_path, scripts)
    agent._traj_recorder = FakeTrajRecorder()
    agent._client = FakeClient()
    return agent


NARR_CONTINUE = "【自评】①距目标完成度80%，报告已生成。②未重复失败。③决定：继续。\n"


def test_assess_round_narration_kept_out_of_reply_body(tmp_path):
    """继续形态：自评轮旁白不进回复正文/推送帧，只进轨迹（有用通道）"""
    agent = _agent_with_client(tmp_path, _tool_rounds() + [
        [NARR_CONTINUE, _tc(args={"command": "echo tail"})],
        ["最终回答"],
    ])
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "go"}], "s1", traj_seg_id="seg-1"))
    # 回复正文=只留回答
    assert resp == "最终回答"
    for word in ("【自评】", "完成度", "决定"):
        assert word not in resp, f"执行旁白混进回复正文: {resp!r}"
    # 推送帧（用户看到的聊天正文）同样零旁白
    pushed = "".join(_update_text(u) for u in agent._client.pushed)
    assert "【自评】" not in pushed and "完成度" not in pushed
    # 有用通道=轨迹：round8 content 含旁白（日志/轨迹，不是聊天正文）
    rounds = agent._traj_recorder.rounds
    assert len(rounds) == 10
    assert "【自评】" in rounds[8]["content"] and "决定：继续" in rounds[8]["content"]
    assert rounds[9]["content"] == "最终回答"


def test_finish_form_keeps_summary_strips_narration(tmp_path):
    """收尾形态：自评轮"旁白+总结"同段——总结留正文，旁白剥走"""
    agent = _agent_with_client(tmp_path, _tool_rounds() + [
        ["【自评】①完成度100%。②无重复失败。③决定：收尾。\n\n"
         "总结：任务完成，报表已生成并校验通过。"],
    ])
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "go"}], "s1", traj_seg_id="seg-1"))
    assert "总结：任务完成" in resp, "收尾总结必须保留（不吞用户答案）"
    for word in ("【自评】", "完成度", "决定："):
        assert word not in resp, f"执行旁白混进回复正文: {resp!r}"
    rounds = agent._traj_recorder.rounds
    assert "决定：收尾" in rounds[8]["content"], "旁白进轨迹（有用通道）"


def test_non_assess_round_unmarked_lookalike_not_stripped(tmp_path):
    """非自评轮：无标记的同字样正文不误伤（fail-safe 宁可多显示）"""
    agent = _agent_with_client(tmp_path, [["这是正文测试 ③决定：继续"]])
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "go"}], "s1", traj_seg_id="seg-1"))
    assert resp == "这是正文测试 ③决定：继续"
    assert agent._traj_recorder.rounds[0]["content"] == resp


def test_non_assess_round_marker_line_strips(tmp_path):
    """非自评轮：契约标记行（【自评】…）=旁白，按标记常量语义剥出正文。

    真实样本（周期#670 om-c101c546974f 修后复验实证）：模型在自评轮之外（round=9）
    自发再写一条【自评】行——旧契约"只在自评轮剥"让它混进回复正文（evo 目标
    "回复正文不得混入执行旁白"被击穿）。标记常量是生产者契约单一真源：以标记
    开头的行按其自身语义就是旁白，任何轮次都不属于用户回答。
    """
    agent = _agent_with_client(tmp_path, [["【自评】这是正文测试 ③决定：继续"]])
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "go"}], "s1", traj_seg_id="seg-1"))
    assert "【自评】" not in resp and "决定" not in resp, f"标记行混进正文: {resp!r}"
    # 有用通道=轨迹保留（不丢证据）
    assert "【自评】" in agent._traj_recorder.rounds[0]["content"]


def test_real_leak_sample_round9_marker_stripped(tmp_path):
    """真实泄漏样本逐字钉死：round8 自评轮旁白＋round9 自发标记行都不得进正文"""
    leak_line = "【自评】①9/12 完成 ②无失败重复 ③继续"
    final = "共执行了 12 条命令（TURN1～TURN12），全部成功。"
    agent = _agent_with_client(tmp_path, _tool_rounds() + [
        ["【自评】①8/12条命令已完成 ②无重复失败 ③决定：继续\n", _tc(args={"command": "echo 9"})],
        [leak_line, _tc(args={"command": "echo 10"})],
        [final],
    ])
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "go"}], "s1", traj_seg_id="seg-1"))
    assert resp == final, f"回复正文必须只留回答: {resp!r}"
    for word in ("【自评】", "完成度", "决定"):
        assert word not in resp
    rounds = agent._traj_recorder.rounds
    assert "①8/12条命令已完成" in rounds[8]["content"], "自评轮旁白进轨迹"
    assert leak_line in rounds[9]["content"], "round9 标记行进轨迹（有用通道不丢）"
