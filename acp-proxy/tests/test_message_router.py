"""路线图 4.2 统一消息路由（OpenMate/微信统一收发）测试。

覆盖：信封默认值 / openmate入站三态（delivered/duplicate/error）/ 通道校验 /
微信入站委托真桥（回环e2e+去重）/ 出站双通道（微信拆块主动发+openmate落库
turn_origin=msg且relay补推面消费）/ 默认回合体失败纪律 / soulmate_agent接线护栏
+ __new__真实dispatch烟测。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.message_router import (
    CHANNEL_OPENMATE,
    CHANNEL_WECHAT,
    MSG_TURN_ORIGIN,
    ChannelMessage,
    RouterStore,
    UnifiedMessageRouter,
    make_openmate_turn_fn,
)
from agent.wechat_bridge import (
    BridgeStore,
    LoopbackTransport,
    WeChatBridge,
    WeChatMessage,
)


# ── 测试替身 ─────────────────────────────────────────────────────


class FakeBridge:
    def __init__(self):
        self.deliver_calls: list[WeChatMessage] = []
        self.proactive_calls: list[tuple[str, str]] = []
        self.deliver_result = "delivered"
        self.proactive_result = 1
        self.sessions: dict[str, str] = {}

    async def deliver(self, msg: WeChatMessage) -> str:
        self.deliver_calls.append(msg)
        return self.deliver_result

    async def send_proactive(self, peer: str, text: str) -> int:
        self.proactive_calls.append((peer, text))
        return self.proactive_result

    def session_for(self, peer: str):
        return self.sessions.get(peer)


class FakeAgent:
    """最小SoulMateAgent替身：_get_db + prompt落一行assistant输出。"""

    def __init__(self, db_path: str, reply: str = "好的", stop_reason: str = "end_turn"):
        self._db_path = db_path
        self.reply = reply
        self.stop_reason = stop_reason
        self.prompts: list[tuple[str, str]] = []
        self.origin_set: list[tuple[str, str]] = []

    def _get_db(self):
        return sqlite3.connect(self._db_path)

    def set_turn_origin(self, session_id: str, origin: str) -> None:
        self.origin_set.append((session_id, origin))

    def clear_turn_origin(self, session_id: str) -> None:
        pass

    async def prompt(self, parts, session_id):
        self.prompts.append((session_id, parts[0]["text"]))
        if self.reply:
            db = self._get_db()
            try:
                db.execute(
                    "INSERT INTO agent_messages (session_id, role, content, timestamp)"
                    " VALUES (?,?,?,?)",
                    (session_id, "assistant", self.reply, 1.0),
                )
                db.commit()
            finally:
                db.close()
        return SimpleNamespace(stop_reason=self.stop_reason)


def _mk_msg_db(path: Path) -> None:
    db = sqlite3.connect(str(path))
    db.execute(
        "CREATE TABLE agent_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, role TEXT, content TEXT, timestamp REAL, metadata TEXT)")
    db.commit()
    db.close()


def _mk_router(tmp_path: Path, **kw) -> UnifiedMessageRouter:
    return UnifiedMessageRouter(
        store=RouterStore(db_path=str(tmp_path / "router.db")), **kw)


# ── 信封 / 去重持久层 ────────────────────────────────────────────


def test_channel_message_defaults():
    m = ChannelMessage(channel="openmate", msg_id="m1", sender="u", text="hi")
    assert m.received_at_ms > 0
    assert m.session_id == "" and m.context_token == ""


def test_router_store_dedupe_and_channel_namespace(tmp_path):
    store = RouterStore(db_path=str(tmp_path / "s.db"))
    assert store.first_time_seen("openmate", "m1", 1000) is True
    assert store.first_time_seen("openmate", "m1", 2000) is False  # 重复投递
    # 同msg_id不同通道不互相判重（channel+msg_id复合主键）
    assert store.first_time_seen("wechat", "m1", 3000) is True
    # 跨实例持久（同库再开）
    store2 = RouterStore(db_path=str(tmp_path / "s.db"))
    assert store2.first_time_seen("openmate", "m1", 4000) is False


# ── 入站：openmate 通道 ──────────────────────────────────────────


def test_route_inbound_openmate_delivered(tmp_path):
    calls = []

    async def turn(sid, text):
        calls.append((sid, text))
        return "回复内容"

    r = _mk_router(tmp_path, turn_fn=turn)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="m1", sender="u", text="你好",
        session_id="om-1")))
    assert res.status == "delivered" and res.reply_sent is True
    assert res.session_id == "om-1"
    assert calls == [("om-1", "你好")]
    assert r.stats.inbound_ok == 1


def test_route_inbound_openmate_duplicate(tmp_path):
    calls = []

    async def turn(sid, text):
        calls.append(text)
        return "x"

    r = _mk_router(tmp_path, turn_fn=turn)
    msg = ChannelMessage(channel=CHANNEL_OPENMATE, msg_id="m1", sender="u",
                         text="hi", session_id="om-1")
    first = asyncio.run(r.route_inbound(msg))
    second = asyncio.run(r.route_inbound(msg))
    assert first.status == "delivered"
    assert second.status == "duplicate"
    assert second.detail  # 重复有明确说明
    assert len(calls) == 1  # 回合不双跑
    assert r.stats.inbound_duplicates == 1


def test_route_inbound_openmate_missing_session(tmp_path):
    r = _mk_router(tmp_path)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="m1", sender="u", text="hi")))
    assert res.status == "error" and "session_id" in res.detail


def test_route_inbound_validation_errors(tmp_path):
    r = _mk_router(tmp_path)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel="telegram", msg_id="m1", sender="u", text="hi")))
    assert res.status == "error" and "未知消息通道" in res.detail
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="  ", sender="u", text="hi",
        session_id="om-1")))
    assert res.status == "error" and "msg_id" in res.detail
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="m2", sender="u", text="",
        session_id="om-1")))
    assert res.status == "error" and "text" in res.detail


def test_route_inbound_turn_failure_is_error(tmp_path):
    async def turn(sid, text):
        raise RuntimeError("回合零输出（未落库任何非空assistant输出）")

    r = _mk_router(tmp_path, turn_fn=turn)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="m1", sender="u", text="hi",
        session_id="om-1")))
    assert res.status == "error" and "回合失败" in res.detail
    assert res.reply_sent is False  # 不谎报投递成功
    assert r.stats.inbound_failed == 1


# ── 入站：微信通道委托真桥 ───────────────────────────────────────


def test_route_inbound_wechat_delegates_to_bridge(tmp_path):
    transport = LoopbackTransport()
    turns = []

    async def turn(msg: WeChatMessage):
        turns.append(msg.content)
        return "收到"

    bridge = WeChatBridge(
        transport, turn_fn=turn,
        store=BridgeStore(db_path=str(tmp_path / "b.db")),
        min_send_interval_s=0)
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: bridge)

    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_WECHAT, msg_id="w1", sender="wxid_a", text="在吗")))
    assert res.status == "delivered" and res.reply_sent is True
    assert turns == ["在吗"]
    assert transport.sent and transport.sent[0][1] == "收到"

    # 重复投递：桥判重（单一owner），router如实报duplicate
    res2 = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_WECHAT, msg_id="w1", sender="wxid_a", text="在吗")))
    assert res2.status == "duplicate"
    assert len(turns) == 1
    assert r.stats.inbound_duplicates == 1


def test_route_inbound_wechat_turn_failure_sends_notice(tmp_path):
    transport = LoopbackTransport()

    async def turn(msg: WeChatMessage):
        raise RuntimeError("回合LLM级失败")

    bridge = WeChatBridge(
        transport, turn_fn=turn,
        store=BridgeStore(db_path=str(tmp_path / "b.db")),
        min_send_interval_s=0)
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: bridge)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_WECHAT, msg_id="w1", sender="wxid_a", text="hi")))
    assert res.status == "error"
    # 失败告知已发给微信用户（绝不无声消失）
    assert transport.sent and "失败" in transport.sent[0][1]


def test_bridge_send_proactive_blocks_and_unsupported_transport(tmp_path):
    transport = LoopbackTransport()
    bridge = WeChatBridge(
        transport, store=BridgeStore(db_path=str(tmp_path / "b.db")),
        min_send_interval_s=0, max_block_chars=200)

    async def run():
        # _split_blocks 块上限下限200（既有契约），用450字长文本断言拆多块
        return await bridge.send_proactive("wxid_a", "一二三" * 150)
    sent = asyncio.run(run())
    assert sent >= 2  # 长文本按上限拆多块
    assert [p for p, _ in transport.proactive] == ["wxid_a"] * sent

    # 不支持主动发的传输：默认契约抛 NotImplementedError，0块=失败
    class NoProactive(LoopbackTransport):
        async def send_text(self, peer, text):
            raise NotImplementedError("不支持主动发送")

    bridge2 = WeChatBridge(
        NoProactive(), store=BridgeStore(db_path=str(tmp_path / "b2.db")),
        min_send_interval_s=0)
    assert asyncio.run(bridge2.send_proactive("wxid_a", "hi")) == 0
    with pytest.raises(ValueError):
        asyncio.run(bridge.send_proactive("  ", "hi"))


# ── 出站：统一入口 ───────────────────────────────────────────────


def test_send_outbound_wechat(tmp_path):
    fb = FakeBridge()
    fb.proactive_result = 2
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: fb)
    res = asyncio.run(r.send_outbound("wechat", "wxid_a", "提醒：会议改期"))
    assert res.status == "delivered" and "2块" in res.detail
    assert fb.proactive_calls == [("wxid_a", "提醒：会议改期")]
    assert r.stats.outbound_ok == 1

    # 桥0块成功=失败（不谎报）
    fb.proactive_result = 0
    res = asyncio.run(r.send_outbound("wechat", "wxid_a", "x"))
    assert res.status == "error" and r.stats.outbound_failed == 1


def test_send_outbound_openmate_persists_relayed_row(tmp_path):
    msg_db = tmp_path / "opensoul.db"
    _mk_msg_db(msg_db)
    r = _mk_router(tmp_path, openmate_db_path=str(msg_db))
    res = asyncio.run(r.send_outbound(CHANNEL_OPENMATE, "om-1", "定时任务已完成"))
    assert res.status == "delivered" and "turn_origin=msg" in res.detail

    db = sqlite3.connect(str(msg_db))
    row = db.execute(
        "SELECT role, content, metadata FROM agent_messages WHERE session_id='om-1'"
    ).fetchone()
    db.close()
    assert row[0] == "assistant" and row[1] == "定时任务已完成"
    meta = json.loads(row[2])
    assert meta["turn_origin"] == MSG_TURN_ORIGIN

    # relay补推面必须消费该出身（正连前端实时可见的前提）
    from agent.wakeup_relay import is_relayed_meta
    assert is_relayed_meta(row[2]) is True


def test_send_outbound_validation(tmp_path):
    fb = FakeBridge()
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: fb)
    res = asyncio.run(r.send_outbound("sms", "t", "x"))
    assert res.status == "error" and "未知消息通道" in res.detail
    res = asyncio.run(r.send_outbound("wechat", "", "x"))
    assert res.status == "error" and "target" in res.detail
    res = asyncio.run(r.send_outbound("wechat", "wxid_a", "   "))
    assert res.status == "error" and "text" in res.detail
    assert not fb.proactive_calls


# ── 默认回合体失败纪律 ───────────────────────────────────────────


def test_default_turn_fn_success_and_failure(tmp_path):
    msg_db = tmp_path / "opensoul.db"
    _mk_msg_db(msg_db)

    ok_agent = FakeAgent(str(msg_db), reply="办好了")
    turn = make_openmate_turn_fn(get_agent=lambda: ok_agent)
    out = asyncio.run(turn("om-1", "帮我查下天气"))
    assert out == "办好了"
    assert ok_agent.origin_set == [("om-1", MSG_TURN_ORIGIN)]
    assert "帮我查下天气" in ok_agent.prompts[0][1]

    # 零输出：不假装成功
    silent = FakeAgent(str(msg_db), reply="")
    turn2 = make_openmate_turn_fn(get_agent=lambda: silent)
    with pytest.raises(RuntimeError, match="零输出"):
        asyncio.run(turn2("om-1", "hi"))

    # refusal：拒绝给消失的会话造回合
    ref = FakeAgent(str(msg_db), reply="x", stop_reason="refusal")
    turn3 = make_openmate_turn_fn(get_agent=lambda: ref)
    with pytest.raises(RuntimeError, match="被拒"):
        asyncio.run(turn3("om-1", "hi"))


# ── soulmate_agent 接线护栏 + 真实dispatch烟测 ──────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "message_send"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "message_send"') >= 2
    # 分发helper + lazy-init + 顶部import
    assert "async def _message_dispatch" in src
    assert "_get_message_router" in src
    # 系统提示工具清单
    assert "message_send: 统一消息出站" in src


def test_message_dispatch_smoke(monkeypatch, tmp_path):
    """__new__构造+lazy-init真实dispatch烟测：成功/失败/未知工具三态。"""
    import asyncio as _asyncio

    import agent.soulmate_agent as sa

    fb = FakeBridge()
    router = UnifiedMessageRouter(
        get_wechat_bridge=lambda: fb,
        store=RouterStore(db_path=str(tmp_path / "r.db")))
    monkeypatch.setattr(sa, "_get_message_router", lambda: router)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = _asyncio.run(agent._message_dispatch(
        "message_send", {"channel": "wechat", "target": "wxid_a", "text": "你好"}))
    assert "✅ 消息已发送" in out and "wxid_a" in out

    fb.proactive_result = 0
    out = _asyncio.run(agent._message_dispatch(
        "message_send", {"channel": "wechat", "target": "wxid_a", "text": "你好"}))
    assert "❌ 消息发送失败" in out

    out = _asyncio.run(agent._message_dispatch("message_bogus", {}))
    assert out.startswith("[错误]")

    out = _asyncio.run(agent._message_dispatch(
        "message_send", {"channel": "wechat", "target": "", "text": "x"}))
    assert "❌ 消息发送失败" in out

    # lazy-init真的挂到了实例上
    assert getattr(agent, "_message_router", None) is router
