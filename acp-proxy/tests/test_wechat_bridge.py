"""路线图 4.1 微信桥（agent/wechat_bridge.py）测试。

覆盖面：loopback收发回路e2e / msg_id去重（含跨重启持久） / 回合失败发失败告知
不打断回路 / 长回复拆块+出站限流 / 默认回合体（peer→session映射首见建会话+
复用、refusal/LLM级失败/零输出纪律、turn_origin=wechat打标） / relay推送面 /
start.py接线护栏 + get_wechat_bridge传输选择。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
import time
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.wechat_bridge import (  # noqa: E402
    DEFAULT_FAILURE_NOTICE,
    WECHAT_TURN_ORIGIN,
    BridgeStore,
    LoopbackTransport,
    WeChatBridge,
    WeChatMessage,
    WeChatTransport,
    build_wechat_prompt,
    make_default_turn_fn,
)


def _msg(i: int = 1, **kw) -> WeChatMessage:
    base = {
        "msg_id": f"m-{i}",
        "from_user": f"peer-{i}",
        "content": f"你好 {i}",
        "from_name": f"用户{i}",
        "context_token": f"tok-{i}",
        "received_at_ms": 1_700_000_000_000 + i,
    }
    base.update(kw)
    return WeChatMessage(**base)


def _make_bridge(tmp_path, turn_fn=None, **kw):
    store = BridgeStore(db_path=str(tmp_path / "wechat.db"))
    transport = LoopbackTransport()
    bridge = WeChatBridge(transport, turn_fn=turn_fn, store=store, **kw)
    return bridge, transport, store


# ── loopback收发回路e2e ────────────────────────────────────────


def test_loopback_e2e_turn_and_reply(tmp_path):
    got = []

    async def turn(msg: WeChatMessage) -> str:
        got.append(msg)
        return f"收到：{msg.content}"

    bridge, transport, _ = _make_bridge(
        tmp_path, turn_fn=turn, min_send_interval_s=0.0
    )
    transport.push(_msg(1))
    assert asyncio.run(bridge.run_once()) == 1
    assert len(got) == 1 and got[0].content == "你好 1"
    assert len(transport.sent) == 1
    sent_msg, sent_text = transport.sent[0]
    assert sent_text == "收到：你好 1"
    # iLink协议经验：回复必须回到原消息对端（transport用msg上的context_token回显）
    assert sent_msg.context_token == "tok-1"
    assert bridge.stats.turns_ok == 1 and bridge.stats.replies_sent == 1


def test_run_once_empty_poll_is_noop(tmp_path):
    bridge, transport, _ = _make_bridge(
        tmp_path, turn_fn=lambda m: asyncio.sleep(0, result="x"),
        min_send_interval_s=0.0,
    )
    assert asyncio.run(bridge.run_once()) == 0
    assert transport.sent == []


# ── 去重（重启/重复投递不双跑） ─────────────────────────────────


def test_dedup_same_msg_id_processed_once(tmp_path):
    calls = []

    async def turn(msg):
        calls.append(msg.msg_id)
        return "ok"

    bridge, transport, _ = _make_bridge(
        tmp_path, turn_fn=turn, min_send_interval_s=0.0
    )
    transport.push(_msg(1))
    transport.push(_msg(1))  # 同一msg_id重复投递
    asyncio.run(bridge.run_once())
    assert calls == ["m-1"]
    assert bridge.stats.duplicates == 1
    assert bridge.stats.turns_ok == 1


def test_dedup_persistent_across_restarts(tmp_path):
    db = str(tmp_path / "wechat.db")
    store1 = BridgeStore(db_path=db)
    assert store1.first_time_seen("m-x", 1000) is True
    assert store1.first_time_seen("m-x", 2000) is False
    # 重启（新store实例同一db）仍认得
    store2 = BridgeStore(db_path=db)
    assert store2.first_time_seen("m-x", 3000) is False


# ── 失败隔离：回合失败发告知，回路继续 ──────────────────────────


def test_turn_failure_sends_notice_and_loop_continues(tmp_path):
    async def turn(msg):
        if msg.msg_id == "m-1":
            raise RuntimeError("boom")
        return "fine"

    bridge, transport, _ = _make_bridge(
        tmp_path, turn_fn=turn, min_send_interval_s=0.0
    )
    transport.push(_msg(1))
    transport.push(_msg(2))
    asyncio.run(bridge.run_once())
    assert bridge.stats.turns_failed == 1 and bridge.stats.turns_ok == 1
    assert transport.sent[0][1] == DEFAULT_FAILURE_NOTICE
    assert transport.sent[1][1] == "fine"
    assert "boom" in bridge.stats.last_error


def test_send_exception_does_not_break_loop(tmp_path):
    class FlakyTransport(LoopbackTransport):
        async def send_reply(self, msg, text):
            if msg.msg_id == "m-1":
                raise OSError("net down")
            return await super().send_reply(msg, text)

    async def turn(msg):
        return "ok"

    store = BridgeStore(db_path=str(tmp_path / "wechat.db"))
    transport = FlakyTransport()
    bridge = WeChatBridge(
        transport, turn_fn=turn, store=store, min_send_interval_s=0.0
    )
    transport.push(_msg(1))
    transport.push(_msg(2))
    asyncio.run(bridge.run_once())
    assert bridge.stats.send_failed >= 1 and bridge.stats.replies_sent == 1


# ── 拆块 + 出站限流 ────────────────────────────────────────────


def test_split_blocks_line_boundary_and_chunk(tmp_path):
    bridge, _, _ = _make_bridge(
        tmp_path, turn_fn=lambda m: asyncio.sleep(0, result=""),
        min_send_interval_s=0.0, max_block_chars=200,
    )
    assert bridge._split_blocks("") == []
    assert bridge._split_blocks("单行") == ["单行"]
    long_line = "x" * 450
    blocks = bridge._split_blocks(long_line)
    assert [len(b) for b in blocks] == [200, 200, 50]
    multi = "\n".join(["a" * 120, "b" * 120, "c" * 120])
    blocks = bridge._split_blocks(multi)
    assert all(len(b) <= 200 for b in blocks)
    assert "".join(blocks).replace("\n", "") == "a" * 120 + "b" * 120 + "c" * 120


def test_long_reply_split_and_rate_limited(tmp_path):
    async def turn(msg):
        return "\n".join("x" * 80 for _ in range(10))  # 10行×80字 > 200上限

    bridge, transport, _ = _make_bridge(
        tmp_path, turn_fn=turn, max_block_chars=200, min_send_interval_s=0.05,
    )
    transport.push(_msg(1))
    asyncio.run(bridge.run_once())
    assert len(transport.sent) >= 2  # 超长回复必须拆成多块
    assert bridge._last_send_at > 0  # 限流时间戳被推进


def test_rate_limit_spacing_real_clock(tmp_path):
    async def turn(msg):
        return "\n".join("y" * 150 for _ in range(4))  # 4块（每行一块）

    bridge, transport, _ = _make_bridge(
        tmp_path, turn_fn=turn, max_block_chars=200, min_send_interval_s=0.1,
    )
    transport.push(_msg(1))
    t0 = time.monotonic()
    asyncio.run(bridge.run_once())
    elapsed = time.monotonic() - t0
    assert len(transport.sent) == 4
    # 4块发送经过≥3个0.1s限流间隔（iLink撞限流静默丢→出站必须留间隔）
    assert elapsed >= 0.3


# ── 默认回合体：session映射 + 失败纪律 ─────────────────────────


def _make_agent_db(path: str) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT
        );
        """
    )
    db.commit()
    db.close()


class FakeAgent:
    def __init__(self, db_path: str, replies=None, stop_reason="end_turn"):
        self._db_path = db_path
        self.replies = replies if replies is not None else ["这是回复"]
        self.stop_reason = stop_reason
        self.prompt_calls = []
        self.new_session_calls = 0
        self.origin_ops = []

    def _get_db(self):
        db = sqlite3.connect(self._db_path)
        db.row_factory = sqlite3.Row
        return db

    def set_turn_origin(self, sid, origin):
        self.origin_ops.append(("set", sid, origin))

    def clear_turn_origin(self, sid):
        self.origin_ops.append(("clear", sid))

    async def new_session(self, **kw):
        self.new_session_calls += 1
        return types.SimpleNamespace(session_id=f"s-{self.new_session_calls}")

    async def prompt(self, parts, sid):
        self.prompt_calls.append((parts, sid))
        db = self._get_db()
        try:
            for c in self.replies:
                db.execute(
                    "INSERT INTO agent_messages(session_id, role, content) "
                    "VALUES(?, 'assistant', ?)",
                    (sid, c),
                )
            db.commit()
        finally:
            db.close()
        return types.SimpleNamespace(stop_reason=self.stop_reason)


def test_default_turn_success_and_turn_origin(tmp_path):
    db_path = str(tmp_path / "agent.db")
    _make_agent_db(db_path)
    agent = FakeAgent(db_path, replies=["第一段", "第二段"])
    store = BridgeStore(db_path=str(tmp_path / "wechat.db"))
    turn = make_default_turn_fn(store, get_agent=lambda: agent)
    msg = _msg(1, from_user="wxid_a", from_name="小明")
    text = asyncio.run(turn(msg))
    assert text == "第一段\n\n第二段"
    # turn_origin=wechat 打标/清除（wakeup_relay补推面归因）
    assert agent.origin_ops == [("set", "s-1", WECHAT_TURN_ORIGIN), ("clear", "s-1")]
    # prompt携带微信来源声明
    prompt_text = agent.prompt_calls[0][0][0]["text"]
    assert "小明" in prompt_text and "你好 1" in prompt_text
    # peer→session映射已持久化
    assert store.session_for("wxid_a") == "s-1"


def test_default_turn_session_reuse(tmp_path):
    db_path = str(tmp_path / "agent.db")
    _make_agent_db(db_path)
    agent = FakeAgent(db_path)
    store = BridgeStore(db_path=str(tmp_path / "wechat.db"))
    turn = make_default_turn_fn(store, get_agent=lambda: agent)
    asyncio.run(turn(_msg(1, from_user="wxid_a")))
    asyncio.run(turn(_msg(2, from_user="wxid_a")))
    asyncio.run(turn(_msg(3, from_user="wxid_b")))
    assert agent.new_session_calls == 2  # 同peer复用，不同peer各建一个
    sids = [c[1] for c in agent.prompt_calls]
    assert sids == ["s-1", "s-1", "s-2"]


def test_default_turn_refusal_llm_failure_zero_output(tmp_path):
    db_path = str(tmp_path / "agent.db")
    _make_agent_db(db_path)
    store = BridgeStore(db_path=str(tmp_path / "wechat.db"))

    # refusal：会话消失不给造回合
    turn = make_default_turn_fn(
        store, get_agent=lambda: FakeAgent(db_path, stop_reason="refusal")
    )
    with pytest.raises(RuntimeError, match="被拒"):
        asyncio.run(turn(_msg(1)))

    # LLM级失败机器标记不假装成功
    turn = make_default_turn_fn(
        store,
        get_agent=lambda: FakeAgent(db_path, replies=["[LLM错误: 402]"]),
    )
    with pytest.raises(RuntimeError, match="LLM级失败"):
        asyncio.run(turn(_msg(2)))

    # 零输出不给微信用户发空回复
    turn = make_default_turn_fn(
        store, get_agent=lambda: FakeAgent(db_path, replies=["   "])
    )
    with pytest.raises(RuntimeError, match="零输出"):
        asyncio.run(turn(_msg(3)))


def test_build_wechat_prompt_marks_origin():
    text = build_wechat_prompt(_msg(1, from_user="wxid_a", from_name="小明"))
    assert text.startswith("[微信消息回合")
    assert "小明" in text and "你好 1" in text


# ── 接线护栏 ───────────────────────────────────────────────────


def test_relay_pushes_wechat_origin():
    from agent import wakeup_relay

    assert WECHAT_TURN_ORIGIN in wakeup_relay.RELAYED_TURN_ORIGINS


def test_start_py_bootstraps_wechat_bridge():
    src = (
        Path(__file__).resolve().parent.parent / "agent" / "start.py"
    ).read_text(encoding="utf-8")
    assert "SOULMATE_WECHAT_ENABLED" in src
    assert "get_wechat_bridge" in src


def test_get_wechat_bridge_transport_selection(monkeypatch, tmp_path):
    import agent.wechat_bridge as wb

    monkeypatch.setenv("SOULMATE_WECHAT_DB_PATH", str(tmp_path / "wb.db"))
    monkeypatch.setenv("SOULMATE_WECHAT_TRANSPORT", "loopback")
    wb._bridge = None
    try:
        b = wb.get_wechat_bridge()
        assert isinstance(b._transport, LoopbackTransport)
        # 单例惯例
        assert wb.get_wechat_bridge() is b
    finally:
        wb._bridge = None

    # iLink传输4.1b才接入——显式拒绝而不是假装能用（不与hermes gateway抢会话）
    monkeypatch.setenv("SOULMATE_WECHAT_TRANSPORT", "ilink")
    wb._bridge = None
    try:
        with pytest.raises(RuntimeError, match="4.1b"):
            wb.get_wechat_bridge()
    finally:
        wb._bridge = None

    monkeypatch.setenv("SOULMATE_WECHAT_TRANSPORT", "bogus")
    wb._bridge = None
    try:
        with pytest.raises(RuntimeError, match="未知微信传输"):
            wb.get_wechat_bridge()
    finally:
        wb._bridge = None


def test_ensure_started_and_stop(tmp_path):
    async def turn(msg):
        return "ok"

    bridge, transport, _ = _make_bridge(
        tmp_path, turn_fn=turn,
        min_send_interval_s=0.0, poll_interval_s=0.01,
    )

    async def run():
        bridge.ensure_started()
        bridge.ensure_started()  # 幂等
        await asyncio.sleep(0.05)
        bridge._running = False
        assert bridge._task is not None
        await asyncio.wait_for(bridge._task, timeout=2)

    asyncio.run(run())
