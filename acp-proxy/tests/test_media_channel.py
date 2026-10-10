"""路线图4.3 媒体能力（图片/文件收发、截图理解）测试。"""
from __future__ import annotations

import asyncio
import base64
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.media_channel import (
    KIND_FILE,
    KIND_IMAGE,
    MediaAttachment,
    from_paths,
    kind_for,
    media_prompt_block,
    save_inbound,
)
from agent.message_router import (
    CHANNEL_OPENMATE,
    CHANNEL_WECHAT,
    ChannelMessage,
    MSG_TURN_ORIGIN,
    RouterStore,
    UnifiedMessageRouter,
)
from agent.wechat_bridge import (
    BridgeStore,
    LoopbackTransport,
    WeChatBridge,
    WeChatMessage,
    build_wechat_prompt,
)


# ── media_channel 单元 ──────────────────────────────────────────


def test_kind_for_image_and_file():
    assert kind_for("a.png") == KIND_IMAGE
    assert kind_for("a.unknown", "image/jpeg") == KIND_IMAGE
    assert kind_for("report.pdf") == KIND_FILE
    assert kind_for("x.bin", "application/octet-stream") == KIND_FILE


def test_media_attachment_defaults_and_meta(tmp_path):
    p = tmp_path / "shot.png"
    p.write_bytes(b"png")
    a = MediaAttachment(kind="", path=str(p))
    assert a.kind == KIND_IMAGE and a.name == "shot.png"
    assert a.mime_type.startswith("image/")
    meta = a.to_meta()
    assert meta == {
        "type": "image", "name": "shot.png",
        "mime_type": a.mime_type, "path": str(p),
    }


def test_from_paths_ok_dedupe_and_errors(tmp_path):
    f1 = tmp_path / "a.txt"
    f1.write_text("x")
    f2 = tmp_path / "b.png"
    f2.write_bytes(b"y")
    out = from_paths([str(f1), str(f2), str(f1)])  # 去重保序
    assert [a.name for a in out] == ["a.txt", "b.png"]
    assert out[0].kind == KIND_FILE and out[1].kind == KIND_IMAGE

    with pytest.raises(ValueError, match="不存在"):
        from_paths([str(tmp_path / "nope.txt")])
    with pytest.raises(ValueError, match="不能为空"):
        from_paths(["  "])
    assert from_paths([]) == []  # 空列表=无附件，不是错误


def test_save_inbound_persists_decoded_bytes(tmp_path):
    raw = b"\x89PNG-fake-bytes"
    att = save_inbound("shot.png", base64.b64encode(raw).decode(),
                       "image/png", dest_dir=str(tmp_path / "in"))
    assert att.kind == KIND_IMAGE and att.name == "shot.png"
    assert Path(att.path).read_bytes() == raw
    assert att.path.startswith(str(tmp_path / "in"))

    # data URL 前缀剥离
    att2 = save_inbound("a.txt", "data:text/plain;base64," +
                        base64.b64encode(b"hello").decode(),
                        dest_dir=str(tmp_path / "in"))
    assert Path(att2.path).read_bytes() == b"hello"

    with pytest.raises(ValueError, match="不能为空"):
        save_inbound("x.txt", "", dest_dir=str(tmp_path / "in"))
    with pytest.raises(ValueError, match="解码失败"):
        save_inbound("x.txt", "@@not-base64@@", dest_dir=str(tmp_path / "in"))


def test_media_prompt_block():
    assert media_prompt_block([]) == ""
    img = MediaAttachment(kind=KIND_IMAGE, path="/tmp/s.png", name="s.png",
                          mime_type="image/png")
    doc = MediaAttachment(kind=KIND_FILE, path="/tmp/r.pdf", name="r.pdf",
                          mime_type="application/pdf")
    block = media_prompt_block([img, doc])
    assert "本地路径: /tmp/s.png" in block
    assert "vision_analyze" in block and "截图理解" in block  # 截图理解确定性提示
    assert "本地路径: /tmp/r.pdf" in block and "read_file" in block


# ── 测试替身 ─────────────────────────────────────────────────────


class FakeBridge:
    def __init__(self):
        self.deliver_calls: list[WeChatMessage] = []
        self.proactive_calls: list[tuple[str, str]] = []
        self.media_calls: list[tuple[str, list]] = []
        self.deliver_result = "delivered"
        self.proactive_result = 1
        self.media_result = 1
        self.sessions: dict[str, str] = {}

    async def deliver(self, msg: WeChatMessage) -> str:
        self.deliver_calls.append(msg)
        return self.deliver_result

    async def send_proactive(self, peer: str, text: str) -> int:
        self.proactive_calls.append((peer, text))
        return self.proactive_result

    async def send_media_proactive(self, peer: str, media: list) -> int:
        self.media_calls.append((peer, list(media)))
        return self.media_result

    def session_for(self, peer: str):
        return self.sessions.get(peer)


class OldBridge(FakeBridge):
    """无 send_media_proactive 的旧桥（不支持发媒体）。"""

    send_media_proactive = None  # type: ignore[assignment]


def _mk_router(tmp_path: Path, **kw) -> UnifiedMessageRouter:
    return UnifiedMessageRouter(
        store=RouterStore(db_path=str(tmp_path / "router.db")), **kw)


def _mk_msg_db(path: Path) -> None:
    db = sqlite3.connect(str(path))
    db.execute(
        "CREATE TABLE agent_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " session_id TEXT, role TEXT, content TEXT, timestamp REAL, metadata TEXT)")
    db.commit()
    db.close()


def _mk_att(tmp_path: Path, name: str = "shot.png") -> MediaAttachment:
    p = tmp_path / name
    p.write_bytes(b"fake")
    return from_paths([str(p)])[0]


# ── 入站：媒体进回合（截图理解提示） ────────────────────────────


def test_channel_message_media_default():
    m = ChannelMessage(channel="openmate", msg_id="m1", sender="u", text="hi")
    assert m.media == []


def test_route_inbound_openmate_media_only(tmp_path):
    """纯图片消息（text空）合法；回合文本带媒体提示块（路径+vision提示）。"""
    att = _mk_att(tmp_path)
    calls = []

    async def turn(sid, text):
        calls.append((sid, text))
        return "看到了，截图是…"

    r = _mk_router(tmp_path, turn_fn=turn)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="m1", sender="u", text="",
        session_id="om-1", media=[att])))
    assert res.status == "delivered" and res.reply_sent is True
    text = calls[0][1]
    assert att.path in text and "vision_analyze" in text
    assert "[媒体附件]" in text


def test_route_inbound_openmate_text_plus_media(tmp_path):
    att = _mk_att(tmp_path, "doc.pdf")
    calls = []

    async def turn(sid, text):
        calls.append(text)
        return "x"

    r = _mk_router(tmp_path, turn_fn=turn)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="m1", sender="u", text="帮我读一下",
        session_id="om-1", media=[att])))
    assert res.status == "delivered"
    text = calls[0]
    assert text.startswith("帮我读一下")  # 原文保留
    assert att.path in text and "read_file" in text


def test_route_inbound_empty_text_no_media_rejected(tmp_path):
    r = _mk_router(tmp_path)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_OPENMATE, msg_id="m1", sender="u", text="  ",
        session_id="om-1")))
    assert res.status == "error" and "text" in res.detail


def test_route_inbound_wechat_forwards_media(tmp_path):
    att = _mk_att(tmp_path)
    fb = FakeBridge()
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: fb)
    res = asyncio.run(r.route_inbound(ChannelMessage(
        channel=CHANNEL_WECHAT, msg_id="w1", sender="wxid_a", text="看图",
        media=[att])))
    assert res.status == "delivered"
    assert fb.deliver_calls[0].media == [att]  # 媒体随信封进桥


def test_build_wechat_prompt_media_block(tmp_path):
    att = _mk_att(tmp_path)
    msg = WeChatMessage(msg_id="w1", from_user="wxid_a", content="",
                        media=[att])
    prompt = build_wechat_prompt(msg)
    assert att.path in prompt and "vision_analyze" in prompt

    # 纯文本消息零改动（媒体块为空串）
    plain = WeChatMessage(msg_id="w2", from_user="wxid_a", content="你好")
    assert "[媒体附件]" not in build_wechat_prompt(plain)


def test_wechat_bridge_deliver_media_e2e(tmp_path):
    """回环e2e：带图消息→回合prompt含路径（默认回合体走真实build_wechat_prompt）。"""
    att = _mk_att(tmp_path)
    captured = []

    async def turn(msg: WeChatMessage) -> str:
        captured.append(build_wechat_prompt(msg))
        return "已理解截图"

    transport = LoopbackTransport()
    bridge = WeChatBridge(
        transport, turn_fn=turn,
        store=BridgeStore(db_path=str(tmp_path / "b.db")))
    msg = WeChatMessage(msg_id="w1", from_user="wxid_a", content="这是截图",
                        media=[att])
    status = asyncio.run(bridge.deliver(msg))
    assert status == "delivered"
    assert att.path in captured[0] and "vision_analyze" in captured[0]


# ── 出站：媒体发送 ──────────────────────────────────────────────


def test_loopback_send_media_and_bridge_proactive(tmp_path):
    att = _mk_att(tmp_path)
    transport = LoopbackTransport()
    bridge = WeChatBridge(
        transport, store=BridgeStore(db_path=str(tmp_path / "b.db")))
    n = asyncio.run(bridge.send_media_proactive("wxid_a", [att]))
    assert n == 1
    assert transport.media_sent == [("wxid_a", [att])]
    assert bridge.stats.media_sent == 1


def test_bridge_media_unsupported_transport(tmp_path):
    """传输未实现send_media→返回0+记错误（不谎报成功）。"""

    class TextOnlyTransport(LoopbackTransport):
        async def send_media(self, peer, media):  # 覆写为基类默认=不支持
            raise NotImplementedError("不支持发送媒体")

    transport = TextOnlyTransport()
    bridge = WeChatBridge(
        transport, store=BridgeStore(db_path=str(tmp_path / "b.db")))
    att = _mk_att(tmp_path)
    n = asyncio.run(bridge.send_media_proactive("wxid_a", [att]))
    assert n == 0
    assert "不支持" in bridge.stats.last_error
    assert not transport.media_sent


def test_send_outbound_openmate_media_persists(tmp_path):
    """openmate出站带媒体：MEDIA标签+attachments列持久化（无列→ALTER兜底）。"""
    msg_db = tmp_path / "opensoul.db"
    _mk_msg_db(msg_db)  # 无attachments列，走ALTER兜底路径
    att = _mk_att(tmp_path)
    r = _mk_router(tmp_path, openmate_db_path=str(msg_db))
    res = asyncio.run(r.send_outbound(
        CHANNEL_OPENMATE, "om-1", "文件发你", media=[att]))
    assert res.status == "delivered" and "turn_origin=msg" in res.detail

    db = sqlite3.connect(str(msg_db))
    row = db.execute(
        "SELECT content, metadata, attachments FROM agent_messages"
        " WHERE session_id='om-1'").fetchone()
    db.close()
    assert f"MEDIA:{att.path}" in row[0]          # 前端下载卡片标签
    assert json.loads(row[1])["turn_origin"] == MSG_TURN_ORIGIN
    atts = json.loads(row[2])
    assert atts == [att.to_meta()]                # 历史加载可恢复附件


def test_send_outbound_wechat_media(tmp_path):
    fb = FakeBridge()
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: fb)
    att = _mk_att(tmp_path)
    # 纯媒体（text空）合法
    res = asyncio.run(r.send_outbound(
        CHANNEL_WECHAT, "wxid_a", "", media=[att]))
    assert res.status == "delivered" and "媒体1个" in res.detail
    assert fb.media_calls == [("wxid_a", [att])]
    assert not fb.proactive_calls  # 无文本不发文本块

    # 文本+媒体：两条都发
    res = asyncio.run(r.send_outbound(
        CHANNEL_WECHAT, "wxid_a", "看图", media=[att]))
    assert res.status == "delivered"
    assert "文本1块" in res.detail and "媒体1个" in res.detail
    assert fb.proactive_calls == [("wxid_a", "看图")]

    # 媒体发送0个=失败（不谎报）
    fb.media_result = 0
    res = asyncio.run(r.send_outbound(
        CHANNEL_WECHAT, "wxid_a", "", media=[att]))
    assert res.status == "error" and "媒体发送失败" in res.detail


def test_send_outbound_media_unsupported_bridge(tmp_path):
    ob = OldBridge()
    ob.send_media_proactive = None
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: ob)
    att = _mk_att(tmp_path)
    res = asyncio.run(r.send_outbound(
        CHANNEL_WECHAT, "wxid_a", "", media=[att]))
    assert res.status == "error" and "不支持发送媒体" in res.detail


def test_send_outbound_validation(tmp_path):
    fb = FakeBridge()
    r = _mk_router(tmp_path, get_wechat_bridge=lambda: fb)
    # text与media都空→拒绝
    res = asyncio.run(r.send_outbound("wechat", "wxid_a", "   "))
    assert res.status == "error" and "text" in res.detail
    res = asyncio.run(r.send_outbound("wechat", "wxid_a", "", media=[]))
    assert res.status == "error" and "text" in res.detail
    assert not fb.proactive_calls and not fb.media_calls


# ── soulmate_agent 接线护栏 + dispatch 烟测 ────────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 顶部import
    assert "from agent.media_channel import from_paths" in src
    # 工具schema含media参数 + text不再强制
    assert '"media": {' in src
    assert '"required": ["channel", "target"]' in src
    # dispatch解析media并透传
    assert "_media_from_paths" in src
    assert "media=media" in src
    # 系统提示工具清单提及媒体
    assert "支持media图片/文件附件收发" in src


def test_message_dispatch_media_smoke(monkeypatch, tmp_path):
    """__new__构造+lazy-init真实dispatch烟测：带媒体成功/坏media类型。"""
    import agent.soulmate_agent as sa
    from agent.message_router import UnifiedMessageRouter as UMR

    fb = FakeBridge()
    router = UMR(
        get_wechat_bridge=lambda: fb,
        store=RouterStore(db_path=str(tmp_path / "r.db")))
    monkeypatch.setattr(sa, "_get_message_router", lambda: router)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    att = _mk_att(tmp_path)
    out = asyncio.run(agent._message_dispatch(
        "message_send",
        {"channel": "wechat", "target": "wxid_a", "text": "发你图片",
         "media": [att.path]}))
    assert "✅ 消息已发送" in out and "媒体1个" in out
    assert fb.media_calls and fb.media_calls[0][0] == "wxid_a"

    # media非数组→错误
    out = asyncio.run(agent._message_dispatch(
        "message_send",
        {"channel": "wechat", "target": "wxid_a", "text": "x",
         "media": "not-a-list"}))
    assert out.startswith("错误:")

    # media路径不存在→错误（不静默吞）
    out = asyncio.run(agent._message_dispatch(
        "message_send",
        {"channel": "wechat", "target": "wxid_a", "text": "x",
         "media": [str(tmp_path / "missing.png")]}))
    assert out.startswith("错误:")
