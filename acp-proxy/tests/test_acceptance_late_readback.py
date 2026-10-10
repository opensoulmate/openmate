# -*- coding: utf-8 -*-
"""10.4 验收 harness 降级适配——WS 超时任务的"服务端后到产出"二次回读（周期#58）。

实证缺陷（周期#54）：定时类任务 WS 300s 到点超时，但服务端回合照跑（观察窗纪律：
WS 连接只是观察窗），accept_probe 行后到落库——纯 harness 口径把真实完成记成失败，
harness 结果与服务端真源不一致。

修复契约：
  1. late_readback 预算内轮询服务端真源（落库回复 get_reply + 各类别验证器），
     后到产出出现即判过，附"后到回读 Ns/M 次"可观测；
  2. 预算耗尽如实 False（禁把超时当成功），detail 显式报因；
  3. verify 异常按未过计不炸回读环；
  4. _read_back_reply 只取该 session 的 assistant 落库行（服务端真源）；
  5. _drain_ws 排水后到帧（防读缓冲顶满反压 agent stdout 把回合拖死），
     按 sessionId 过滤不混入他会话帧，连接断开只停排水不冒泡；
  6. 接线护栏：main() 超时路径真实调用 late_readback + _read_back_reply，
     verify_mode 走 server_side_late/reply_assisted_late/ws_timeout_unrecovered。
"""
import asyncio
import inspect
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from acceptance_benchmark import (  # noqa: E402
    _drain_ws,
    _read_back_reply,
    late_readback,
)
import acceptance_benchmark  # noqa: E402


class FakeWs:
    """排队帧版假 WS：队列空即抛 asyncio.TimeoutError（等价 wait_for 到点）。"""

    def __init__(self, messages):
        self._q = list(messages)

    async def recv(self):
        if self._q:
            return self._q.pop(0)
        raise asyncio.TimeoutError()


def _update_frame(sid: str, text: str) -> str:
    return json.dumps({
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"sessionId": sid,
                   "update": {"sessionUpdate": "agent_message_chunk", "content": {"text": text}}},
    })


# ── late_readback 核心语义 ─────────────────────────────────────────


def test_late_output_lands_within_budget():
    """后到产出在预算内出现 → 判过，attempts 记录轮询次数（≥2 证明是轮询不是单发）。"""
    state = {"n": 0}

    def get_reply():
        state["n"] += 1
        return "SOULMATE_DEV_OK" if state["n"] >= 3 else ""

    ok, detail, waited, attempts = asyncio.run(late_readback(
        lambda r: ("SOULMATE_DEV_OK" in r, "marker判据"),
        get_reply, budget_s=2.0, poll_s=0.02,
    ))
    assert ok, detail
    assert attempts >= 3, f"应轮询到第3次才见产出，实为{attempts}"
    assert "后到回读" in detail


def test_budget_exhausted_honest_fail():
    """预算耗尽仍无产出 → 如实 False（禁把超时当成功），detail 显式报因。"""
    calls = {"n": 0}

    def never(_reply):
        calls["n"] += 1
        return False, "副作用未出现"

    ok, detail, waited, attempts = asyncio.run(late_readback(
        never, lambda: "", budget_s=0.1, poll_s=0.02,
    ))
    assert not ok
    assert "预算耗尽" in detail and "副作用未出现" in detail
    assert calls["n"] >= 2, f"预算内应轮询多次，实为{calls['n']}"
    assert waited >= 0.1


def test_immediate_pass_single_attempt():
    """首次即过 → 1 次收工，不无谓等待（poll 窗口不应被消费）。"""
    ok, detail, waited, attempts = asyncio.run(late_readback(
        lambda r: (True, "立即过"), lambda: "x", budget_s=5.0, poll_s=1.0,
    ))
    assert ok and attempts == 1
    assert waited < 1.0, f"立即过不应等一个 poll 周期，实为{waited}s"


def test_verify_exception_not_crash():
    """验证器异常按未过计，回读环继续到预算耗尽，不抛不崩。"""
    def boom(_reply):
        raise RuntimeError("验证器炸了")

    ok, detail, waited, attempts = asyncio.run(late_readback(
        boom, lambda: "", budget_s=0.1, poll_s=0.02,
    ))
    assert not ok
    assert "验证器异常" in detail and "验证器炸了" in detail


def test_reply_composes_db_and_frames():
    """验证对象=落库回复+排水后到帧（帧文本也能让回复类判据命中）。"""
    ws = FakeWs([_update_frame("s1", "两个子任务完成：ALPHA_DONE / BETA_DONE")])
    ok, detail, waited, attempts = asyncio.run(late_readback(
        lambda r: ("ALPHA_DONE" in r and "BETA_DONE" in r, "回复判据"),
        lambda: "", ws=ws, collect_sid="s1", budget_s=1.0, poll_s=0.05,
    ))
    assert ok, detail


# ── _read_back_reply 服务端真源 ────────────────────────────────────


def test_read_back_reply_reads_assistant_rows_only(tmp_path):
    db = str(tmp_path / "agent.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE agent_messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
    rows = [
        ("s1", "user", "用户问题"),
        ("s1", "assistant", "第一段产出"),
        ("s9", "assistant", "他会话产出"),
        ("s1", "assistant", "第二段产出"),
    ]
    conn.executemany("INSERT INTO agent_messages (session_id, role, content) VALUES (?,?,?)", rows)
    conn.commit()
    conn.close()
    out = _read_back_reply("s1", db_path=db)
    assert out == "第一段产出\n\n第二段产出", out
    assert "他会话产出" not in out and "用户问题" not in out


def test_read_back_reply_missing_db_returns_empty(tmp_path):
    assert _read_back_reply("s1", db_path=str(tmp_path / "nope.db")) == ""


# ── _drain_ws 排水 ────────────────────────────────────────────────


def test_drain_ws_filters_sid_and_counts():
    frames = [
        _update_frame("s1", "本会话后到帧"),
        _update_frame("s2", "他会话帧"),
        json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"ok": 1}}),
        _update_frame("s1", "第二帧"),
    ]
    out = []

    async def run():
        return await _drain_ws(FakeWs(frames), "s1", out, timeout=0.2)

    got = asyncio.run(run())
    assert got == 4, f"排水应吃掉全部4帧，实为{got}"
    joined = "\n".join(out)
    assert "本会话后到帧" in joined and "第二帧" in joined
    assert "他会话帧" not in joined


def test_drain_ws_connection_dead_does_not_raise():
    class DeadWs:
        async def recv(self):
            raise ConnectionResetError("连接已断")

    out = []

    async def run():
        return await _drain_ws(DeadWs(), "s1", out, timeout=0.2)

    assert asyncio.run(run()) == 0  # 只停排水不冒泡
    assert out == []


# ── 接线护栏（写了≠接线了）────────────────────────────────────────


def test_wiring_main_calls_late_readback_on_prompt_timeout():
    src = inspect.getsource(acceptance_benchmark.main)
    assert "prompt_timed_out" in src, "超时路径须与普通WS失败路径区分"
    assert "await late_readback(" in src, "main() 超时路径必须真实调用 late_readback"
    assert "_read_back_reply(sid)" in src, "超时路径必须回读服务端落库回复"
    assert "ws_timeout_unrecovered" in src and "_late" in src, "verify_mode 须区分恢复/未恢复"


def test_wiring_prompt_timeout_wrapped_separately():
    """session/prompt 的 TimeoutError 须被捕获标记（区分 session/new 等其他失败）。"""
    src = inspect.getsource(acceptance_benchmark.main)
    assert "except (TimeoutError, asyncio.TimeoutError):" in src
