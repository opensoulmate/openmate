"""路线图12.1 多渠道出站独立可查痕迹（agent/outbound_audit.py）测试。

覆盖面：审计行形状 / 微信三条出站路径（回复·主动文本·媒体）留痕 / 失败也留痕 /
openmate 出站留痕（成功+失败）/ 统一出站不双份记账 / 过滤回读 / clear 守卫 /
跨实例持久 / 写失败旁路不阻塞发送 / acceptance_benchmark 服务端判据接线护栏。
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.outbound_audit import OutboundAudit, get_outbound_audit  # noqa: E402
from agent.wechat_bridge import (  # noqa: E402
    BridgeStore,
    LoopbackTransport,
    WeChatBridge,
    WeChatMessage,
)
from agent.media_channel import MediaAttachment  # noqa: E402


def _msg(i: int = 1, **kw) -> WeChatMessage:
    base = {
        "msg_id": f"om-{i}",
        "from_user": f"peer-{i}",
        "content": f"你好 {i}",
        "received_at_ms": 1_700_000_000_000 + i,
    }
    base.update(kw)
    return WeChatMessage(**base)


def _bridge(tmp_path, transport=None, turn_fn=None, **kw):
    store = BridgeStore(db_path=str(tmp_path / "wechat.db"))
    audit = OutboundAudit(db_path=str(tmp_path / "out.db"))
    transport = transport or LoopbackTransport()
    bridge = WeChatBridge(transport, turn_fn=turn_fn, store=store,
                          audit=audit, min_send_interval_s=0, **kw)
    return bridge, transport, audit


# ── 审计行形状 / 过滤回读 / clear ──────────────────────────────


def test_record_shape_and_recent_filters(tmp_path):
    a = OutboundAudit(db_path=str(tmp_path / "out.db"))
    key = a.record("wechat", "peer-1", "text", "sent",
                   text_preview="验收多渠道探针", now_ms=1000)
    assert key.startswith("out-")
    rows = a.recent(channel="wechat", target="peer-1", status="sent")
    assert len(rows) == 1
    r = rows[0]
    assert r["sender"] == "soulmate" and r["kind"] == "text"
    assert r["dedup_key"] == key and r["ts_ms"] == 1000
    assert r["text_preview"] == "验收多渠道探针"
    assert a.recent(channel="openmate") == []
    assert a.recent(text_like="不存在的文案") == []
    assert a.recent(since_ms=2000) == []
    assert a.recent(status="failed") == []


def test_clear_guard_and_filter(tmp_path):
    a = OutboundAudit(db_path=str(tmp_path / "out.db"))
    a.record("wechat", "peer-1", "text", "sent", text_preview="x1")
    a.record("wechat", "peer-2", "text", "sent", text_preview="x2")
    with pytest.raises(ValueError):
        a.clear()  # 拒绝全表清空
    assert a.clear(target="peer-1") == 1
    assert len(a.recent()) == 1
    assert a.clear(text_like="x2") == 1
    assert a.recent() == []


def test_persistence_across_instances(tmp_path):
    db = str(tmp_path / "out.db")
    OutboundAudit(db_path=db).record(
        "wechat", "p", "text", "sent", dedup_key="k-1", text_preview="持久")
    rows = OutboundAudit(db_path=db).recent(target="p")
    assert len(rows) == 1 and rows[0]["dedup_key"] == "k-1"


def test_record_write_failure_bypass(tmp_path):
    """旁路纪律：写失败只计数返回''，绝不抛出阻塞发送主链路。"""
    a = OutboundAudit(db_path=str(tmp_path / "out.db"))
    sqlite3.connect(a._db_path).execute(
        "DROP TABLE outbound_log").connection.commit()
    key = a.record("wechat", "p", "text", "sent")
    assert key == "" and a.write_failures == 1


# ── 微信三条出站路径留痕 ──────────────────────────────────────


def test_bridge_reply_path_records_with_msg_id(tmp_path):
    bridge, transport, audit = _bridge(
        tmp_path, turn_fn=lambda m: asyncio.sleep(0, result="回复内容"))

    async def run():
        transport.push(_msg(1))
        return await bridge.run_once()

    assert asyncio.run(run()) == 1
    rows = audit.recent(channel="wechat", target="peer-1")
    assert len(rows) == 1
    assert rows[0]["kind"] == "reply"
    assert rows[0]["dedup_key"] == "om-1"  # 入站 msg_id 作去重键
    assert rows[0]["status"] == "sent" and rows[0]["blocks"] == 1
    assert "回复内容" in rows[0]["text_preview"]


def test_bridge_send_proactive_text_records(tmp_path):
    bridge, transport, audit = _bridge(tmp_path)

    async def run():
        return await bridge.send_proactive("accept_peer", "验收多渠道探针")

    assert asyncio.run(run()) == 1
    rows = audit.recent(channel="wechat", target="accept_peer")
    assert len(rows) == 1
    assert rows[0]["kind"] == "text" and rows[0]["status"] == "sent"
    assert "验收多渠道探针" in rows[0]["text_preview"]
    assert rows[0]["dedup_key"]  # 自动生成出站去重键


def test_bridge_send_media_records(tmp_path):
    bridge, transport, audit = _bridge(tmp_path)
    att = MediaAttachment(kind="image", name="a.png",
                          mime_type="image/png", path="/tmp/a.png")

    async def run():
        return await bridge.send_media_proactive("peer-9", [att])

    assert asyncio.run(run()) == 1
    rows = audit.recent(channel="wechat", target="peer-9")
    assert rows and rows[0]["kind"] == "media"
    assert "a.png" in rows[0]["text_preview"]


def test_bridge_failed_send_still_records(tmp_path):
    class FailTransport(LoopbackTransport):
        async def send_text(self, peer, text):
            return False

    bridge, transport, audit = _bridge(tmp_path, transport=FailTransport())

    async def run():
        return await bridge.send_proactive("peer-2", "发不出去")

    assert asyncio.run(run()) == 0
    rows = audit.recent(channel="wechat", target="peer-2")
    assert rows and rows[0]["status"] == "failed" and rows[0]["blocks"] == 0


# ── 统一消息路由留痕（openmate 通道 + 不双份记账） ────────────


def test_router_openmate_outbound_records(tmp_path):
    from agent.message_router import RouterStore, UnifiedMessageRouter

    router = UnifiedMessageRouter(
        store=RouterStore(db_path=str(tmp_path / "router.db")),
        openmate_db_path=str(tmp_path / "om.db"),
        openmate_send=lambda sid, text, media=(): 42,
    )
    router._audit = OutboundAudit(db_path=str(tmp_path / "out.db"))

    res = asyncio.run(router.send_outbound(
        "openmate", "sess-1", "多渠道出站痕迹测试"))
    assert res.status == "delivered"
    rows = router._audit.recent(channel="openmate", target="sess-1")
    assert len(rows) == 1
    assert rows[0]["status"] == "sent" and rows[0]["dedup_key"] == "om-42"
    assert "多渠道出站痕迹测试" in rows[0]["text_preview"]


def test_router_openmate_failure_records(tmp_path):
    from agent.message_router import UnifiedMessageRouter, RouterStore

    def boom(sid, text, media=()):
        raise RuntimeError("落库炸了")

    router = UnifiedMessageRouter(
        store=RouterStore(db_path=str(tmp_path / "router.db")),
        openmate_db_path=str(tmp_path / "om.db"),
        openmate_send=boom,
    )
    router._audit = OutboundAudit(db_path=str(tmp_path / "out.db"))
    res = asyncio.run(router.send_outbound("openmate", "sess-2", "失败路径"))
    assert res.status == "error"
    rows = router._audit.recent(channel="openmate", target="sess-2")
    assert rows and rows[0]["status"] == "failed"


def test_router_wechat_delegates_single_record(tmp_path):
    """wechat 出站经桥留痕一次（单一真源不双份记账）。"""
    from agent.message_router import (
        CHANNEL_WECHAT, RouterStore, UnifiedMessageRouter)

    bridge, transport, audit = _bridge(tmp_path)
    router = UnifiedMessageRouter(
        store=RouterStore(db_path=str(tmp_path / "router.db")),
        openmate_db_path=str(tmp_path / "om.db"),
        get_wechat_bridge=lambda: bridge,
    )
    router._audit = audit

    res = asyncio.run(router.send_outbound(
        CHANNEL_WECHAT, "accept_peer", "验收多渠道探针"))
    assert res.status == "delivered"
    rows = audit.recent(channel="wechat", target="accept_peer")
    assert len(rows) == 1  # 恰一行：桥留痕，路由不再重复记
    assert rows[0]["kind"] == "text"


# ── 接线护栏 ──────────────────────────────────────────────────


def test_wiring_send_paths_call_record_out():
    """三个发送路径源码都接了 _record_out（写了≠接线了护栏）。"""
    src = Path(__file__).resolve().parent.parent / "agent" / "wechat_bridge.py"
    text = src.read_text(encoding="utf-8")
    assert text.count("self._record_out(") == 3
    for sig in ('"reply"', '"text"', '"media"'):
        assert sig in text


def test_wiring_acceptance_server_side(tmp_path, monkeypatch):
    """验收 harness 多渠道判据=服务端痕迹回读（不信回复辅证）。"""
    import acceptance_benchmark as bench

    db = str(tmp_path / "out.db")
    monkeypatch.setattr(bench, "OUTBOUND_DB", db)
    assert "多渠道" in bench.SERVER_SIDE
    # 无痕迹行：即使回复吹牛也算不过
    ok, detail = bench.verify_multichannel("已发送给 accept_peer 微信通道成功")
    assert not ok and "出站痕迹行" in detail
    # 有痕迹行：回复为空也过（服务端真源判据）
    OutboundAudit(db_path=db).record(
        "wechat", "accept_peer", "text", "sent",
        text_preview=bench.MULTICHANNEL_MARKER)
    ok, detail = bench.verify_multichannel("")
    assert ok and "=1" in detail


def test_get_outbound_audit_singleton(monkeypatch, tmp_path):
    monkeypatch.setenv("SOULMATE_OUTBOUND_AUDIT_DB", str(tmp_path / "s.db"))
    import agent.outbound_audit as mod
    monkeypatch.setattr(mod, "_audit", None)
    a1 = get_outbound_audit()
    a2 = get_outbound_audit()
    assert a1 is a2
