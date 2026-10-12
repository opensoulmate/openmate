# -*- coding: utf-8 -*-
"""⚡周期#47 轨迹采集同源过滤 — relay_trajectory 的 content 不再粘系统帧/引擎chrome。

实证背景（真实库 trajectory_events）：hermes acp 启动横幅"╭─── Hermes Agent v0.15.2 …
Available Tools …"与回答走同一 agent_message_chunk 通道，relay 记账把横幅并进
llm_call content（content[:500] 全被框线占满，真实回答被完全遮蔽）——8.5c-⑤ 同源
粘连在轨迹采集侧的孪生缺陷。

安全声明（测试即规格）：
1. 引擎chrome横幅（开头框线块+窄签名）剥除，真实回答进 content；
2. 窄签名=块内须命中 Available Tools/Agent v/upstream，含框线的正文/ASCII 图不误伤；
3. 只剥开头，正文中间的框线不动；
4. soulmate 生产者系统帧（心跳/需求自检/治理横幅）帧级过滤（split_frames）；
5. full_text 回退真源同样过滤；observe_cli_turn（CLI兜底回合）同样过滤；
6. 纯回答零过滤零变化（回归不伤）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.frame_origin import (
    KIND_ENGINE_CHROME,
    strip_engine_chrome,
)
from agent.relay_trajectory import RelayTrajectory
from agent.self_verify import REPORT_HEADER
from agent.turn_watchdog import SNAPSHOT_PREFIX


def run(coro):
    return asyncio.run(coro)


class FakeRecorder:
    def __init__(self, root_id="root-1"):
        self.calls = []
        self.root_id = root_id

    async def start_turn(self, session_id, user_text="", agent_id="soulmate"):
        self.calls.append(("start_turn", session_id, user_text, agent_id))
        return self.root_id

    async def record_llm_call(self, session_id, parent_id, content="", duration_ms=0.0,
                              token_usage=0, status="ok", tools=None,
                              agent_id="soulmate", source=""):
        self.calls.append(("record_llm_call", content, status))
        return "llm-1"

    async def end_turn(self, session_id, root_id, status="ok", token_usage=0,
                       termination=""):
        self.calls.append(("end_turn", status))


def _rt(recorder=None, sid="om-chrome-1", agent="hermes"):
    return RelayTrajectory(
        agent_id=agent,
        session_id_getter=lambda: sid,
        recorder=recorder or FakeRecorder(),
    )


PROMPT = {
    "jsonrpc": "2.0", "id": 7, "method": "session/prompt",
    "params": {"sessionId": "om-chrome-1", "messageId": "m1",
               "prompt": [{"type": "text", "text": "hi"}]},
}
CHUNK = lambda t: {
    "jsonrpc": "2.0", "method": "session/update",
    "params": {"sessionId": "om-chrome-1",
               "update": {"sessionUpdate": "agent_message_chunk",
                          "content": {"type": "text", "text": t}}},
}
AGENT_MESSAGE = lambda t: {
    "jsonrpc": "2.0", "method": "session/update",
    "params": {"sessionId": "om-chrome-1",
               "update": {"sessionUpdate": "agent_message",
                          "content": {"type": "text", "text": t}}},
}
RESPONSE = {
    "jsonrpc": "2.0", "id": 7, "result": {"stopReason": "end_turn"},
}


async def _observe(rt, msgs):
    """在事件循环内观察（observe_* 需 running loop 才入队）+ drain 收尾。"""
    for m in msgs:
        if m is PROMPT:
            rt.observe_client(m)
        else:
            rt.observe_agent(m)
    await rt.drain()


# 真实库实证的 hermes 启动横幅形状（trajectory_events 行首500字节）
HERMES_BANNER = (
    "╭─────────── Hermes Agent v0.15.2 (2026.5.29.2) · upstream 46d7718a ───────────╮\n"
    "│                                     Available Tools                          │\n"
    "│   ⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢀⣀⡀⠀⣀⣀⠀⢀⣀⡀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀    browser: browser_back, │\n"
    "│   ⠀⠀⠀⠀⠀⠀⢀⣠⣴⣾⣿⣿⣇⠸⣿⣿⠇⣸⣿⣿⣷⣦⣄⡀⠀⠀⠀⠀⠀⠀    ...                    │\n"
    "╰────────────────────────────────────────────────────────────────────────────╯"
)
ANSWER = "两步执行结果如下：已完成。"


# ── 1. strip_engine_chrome 单元 ─────────────────────────────

def test_strip_real_hermes_banner():
    text, n = strip_engine_chrome(HERMES_BANNER + "\n\n" + ANSWER)
    assert n == 1
    assert text == ANSWER, text
    assert "╭" not in text and "Available Tools" not in text


def test_strip_truncated_banner_no_bottom():
    # 横幅被截断（无 ╰ 底边）：到首个非框线行为止
    text, n = strip_engine_chrome("╭─── Foo Agent v9 · upstream abc ───\n│ side │\n" + ANSWER)
    assert n == 1
    assert text == ANSWER, text


def test_box_diagram_without_marker_not_stripped():
    # 含框线但窄签名不命中的正文/ASCII图：不误伤
    diagram = "╭────────╮\n│ 你好图 │\n╰────────╯\n说明文字"
    text, n = strip_engine_chrome(diagram)
    assert n == 0
    assert text == diagram


def test_chrome_mid_text_untouched():
    # 只剥开头：正文中间的横幅不动
    body = ANSWER + "\n\n" + HERMES_BANNER
    text, n = strip_engine_chrome(body)
    assert n == 0
    assert text == body


def test_empty_and_blank():
    assert strip_engine_chrome("") == ("", 0)
    assert strip_engine_chrome("   \n") == ("   \n", 0)


# ── 2. relay 记账集成 ───────────────────────────────────────

def test_relay_content_drops_banner_keeps_answer():
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_observe(rt, [PROMPT, CHUNK(HERMES_BANNER), CHUNK(ANSWER), RESPONSE]))
    recorded = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert len(recorded) == 1
    assert recorded[0][1] == ANSWER, recorded[0][1]
    assert recorded[0][2] == "ok"


def test_relay_content_banner_single_frame_with_body():
    # 横幅+正文同帧（非流式整发）
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_observe(rt, [PROMPT, CHUNK(HERMES_BANNER + "\n\n" + ANSWER), RESPONSE]))
    recorded = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert recorded[0][1] == ANSWER, recorded[0][1]


def test_relay_filters_soulmate_system_frames():
    # 8.5c-⑤ 三类系统帧走同一 chunk 通道 → 轨迹 content 不收
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_observe(rt, [
        PROMPT,
        CHUNK(SNAPSHOT_PREFIX + "：已用 60s｜第 2/15 轮"),
        CHUNK(ANSWER),
        CHUNK(REPORT_HEADER + "\n1. ✅ met"),
        RESPONSE,
    ]))
    recorded = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert recorded[0][1] == ANSWER, recorded[0][1]


def test_relay_full_text_fallback_filtered():
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_observe(rt, [PROMPT, AGENT_MESSAGE(HERMES_BANNER + "\n\n" + ANSWER), RESPONSE]))
    recorded = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert recorded[0][1] == ANSWER, recorded[0][1]


def test_relay_plain_answer_zero_change():
    # 回归不伤：纯回答逐字不变
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_observe(rt, [PROMPT, CHUNK("你好"), CHUNK("，世界"), RESPONSE]))
    recorded = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert recorded[0][1] == "你好，世界"
    assert recorded[0][2] == "ok"


def test_cli_fallback_answer_filtered():
    rec = FakeRecorder()
    rt = _rt(rec)

    async def _go():
        rt.observe_cli_turn("hi", HERMES_BANNER + "\n\n" + ANSWER, stop_reason="end_turn")
        await rt.drain()

    run(_go())
    recorded = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert len(recorded) == 1
    assert recorded[0][1] == ANSWER, recorded[0][1]


def test_filtered_text_counts_observable():
    rt = _rt()

    async def _go():
        rt.observe_client(PROMPT)
        rt.observe_agent(CHUNK(HERMES_BANNER))
        rt.observe_agent(CHUNK(SNAPSHOT_PREFIX + "：x"))
        rt.observe_agent(CHUNK(ANSWER))

    run(_go())
    text, counts = rt._turn.filtered_text()
    assert text == ANSWER
    assert counts.get(KIND_ENGINE_CHROME) == 1
    assert counts.get("heartbeat") == 1
