"""国内网关通道（agent/domestic_bridge.py + message_router 接线）测试。

覆盖面：
- **echo 口径归位**（不另立山头）：echo_payload 与 opensoul/src/echo/dispatcher.py
  真 dispatcher 发出的负载逐字段一致（格式漂移即红）；
- Level 1 webhook 快路：POST 负载形状 / 空 URL 拒 / 钉钉加签 / 飞书签名校验；
- Level 2 全双工桥：loopback 收发回路 / msg_id 去重 / 回合失败发失败告知不打断回路；
- 自建应用传输：token 获取解析 / 发消息 API 负载形状（真网络换桩）；
- message_router 接线：send_outbound 走 webhook 快路 / 通道校验。
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.domestic_bridge import (  # noqa: E402
    CHANNEL_DINGTALK,
    CHANNEL_FEISHU,
    DEFAULT_FAILURE_NOTICE,
    DomesticAppTransport,
    DomesticBridge,
    DomesticMessage,
    DomesticStore,
    LoopbackTransport,
    build_domestic_prompt,
    echo_payload,
    send_webhook,
)


def _msg(i: int = 1, channel: str = CHANNEL_DINGTALK, **kw) -> DomesticMessage:
    base = {
        "channel": channel,
        "msg_id": f"m-{i}",
        "from_user": f"peer-{i}",
        "content": f"你好 {i}",
        "from_name": f"用户{i}",
        "received_at_ms": 1_700_000_000_000 + i,
    }
    base.update(kw)
    return DomesticMessage(**base)


# ── echo 口径负载 ─────────────────────────────────────────────────


def test_echo_payload_dingtalk_shape():
    assert echo_payload("dingtalk", "T", "C") == {
        "msgtype": "markdown",
        "markdown": {"title": "T", "text": "## T\n\nC"},
    }


def test_echo_payload_feishu_shape():
    assert echo_payload("feishu", "T", "C") == {
        "msg_type": "interactive",
        "card": {
            "header": {"title": {"tag": "plain_text", "content": "T"}},
            "elements": [{"tag": "markdown", "content": "C"}],
        },
    }


def test_echo_payload_unknown_channel():
    with pytest.raises(ValueError):
        echo_payload("telegram", "T", "C")


def test_echo_payload_matches_opensoul_echo(monkeypatch):
    """归位硬约束：本模块 echo_payload 必须与 opensoul 真 dispatcher 发出的负载一致。

    opensoul 缺席则跳过（跨仓依赖非本仓测试硬条件）；一旦在场就逐字节比对，
    杜绝"另立山头"（钉钉/飞书推送格式只认 echo 一个口径）。
    """
    try:
        sys.path.insert(0, "/home/climbing/opensoul")
        from src.echo.dispatcher import Channel, MessageDispatcher
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"opensoul echo 不可导入，跳过归位比对: {e}")

    captured: list[bytes] = []

    class _Resp:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _fake_urlopen(req, *a, **kw):
        captured.append(req.data)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    for ch_name, ch_enum in (("dingtalk", Channel.DINGTALK), ("feishu", Channel.FEISHU)):
        captured.clear()
        d = MessageDispatcher()
        d.configure_channel(ch_enum, endpoint="https://example.invalid/hook", enabled=True)
        d.send(ch_enum, "标题", "内容")
        assert len(captured) == 1
        assert json.loads(captured[0].decode("utf-8")) == echo_payload(ch_name, "标题", "内容")


# ── Level 1 webhook 快路 ─────────────────────────────────────────


def _fake_urlopen_capture(captured, resp_body=b"{}", status=200):
    class _Resp:
        status = 200

        def read(self):
            return resp_body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, *a, **kw):
        captured.append((req.full_url, req.data, dict(req.headers)))
        r = _Resp()
        r.status = status
        return r

    return _urlopen


def test_send_webhook_posts_echo_payload(monkeypatch):
    captured = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_capture(captured))
    ok, detail = send_webhook("dingtalk", "https://hook/x", "标题", "内容")
    assert ok, detail
    url, data, _ = captured[0]
    assert url == "https://hook/x"
    assert json.loads(data.decode("utf-8")) == echo_payload("dingtalk", "标题", "内容")


def test_send_webhook_feishu_posts_card(monkeypatch):
    captured = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_capture(captured))
    ok, detail = send_webhook("feishu", "https://hook/x", "标题", "内容")
    assert ok, detail
    assert json.loads(captured[0][1].decode("utf-8")) == echo_payload("feishu", "标题", "内容")


def test_send_webhook_empty_url():
    ok, detail = send_webhook("dingtalk", "", "T", "C")
    assert not ok and "不能为空" in detail


def test_send_webhook_platform_errcode_is_failure(monkeypatch):
    captured = []
    monkeypatch.setattr(
        urllib.request, "urlopen",
        _fake_urlopen_capture(captured, resp_body=b'{"errcode": 310000, "errmsg": "sign not match"}'))
    ok, detail = send_webhook("dingtalk", "https://hook/x", "T", "C")
    assert not ok and "errcode" in detail


def test_send_webhook_dingtalk_sign_appended(monkeypatch):
    captured = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_capture(captured))
    ok, _ = send_webhook("dingtalk", "https://hook/x", "T", "C", secret="SEC")
    assert ok
    url = captured[0][0]
    assert "timestamp=" in url and "sign=" in url


def test_send_webhook_feishu_sign_in_payload(monkeypatch):
    captured = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_capture(captured))
    ok, _ = send_webhook("feishu", "https://hook/x", "T", "C", secret="SEC")
    assert ok
    payload = json.loads(captured[0][1].decode("utf-8"))
    assert {"timestamp", "nonce", "sign"} <= set(payload)


# ── Level 2 全双工桥（loopback 收发回路） ──────────────────────────


def _make_bridge(tmp_path, channel=CHANNEL_DINGTALK, turn_fn=None, **kw):
    store = DomesticStore(db_path=str(tmp_path / f"{channel}.db"))
    transport = LoopbackTransport(channel)
    bridge = DomesticBridge(channel, transport, turn_fn=turn_fn, store=store, **kw)
    return bridge, transport, store


def test_loopback_e2e_turn_and_reply(tmp_path):
    got = []

    async def turn(msg: DomesticMessage) -> str:
        got.append(msg)
        return f"收到：{msg.content}"

    bridge, transport, _ = _make_bridge(tmp_path, turn_fn=turn, channel=CHANNEL_FEISHU)
    transport.push(_msg(1, channel=CHANNEL_FEISHU))
    assert asyncio.run(bridge.run_once()) == 1
    assert len(got) == 1 and got[0].content == "你好 1"
    assert len(transport.sent) == 1
    _, sent_text = transport.sent[0]
    assert sent_text == "收到：你好 1"


def test_dedup_same_msg_id_processed_once(tmp_path):
    calls = []

    async def turn(msg: DomesticMessage) -> str:
        calls.append(msg.msg_id)
        return "ok"

    bridge, transport, _ = _make_bridge(tmp_path, turn_fn=turn)
    transport.push(_msg(7))
    transport.push(_msg(7))  # 同 msg_id 重复投递
    asyncio.run(bridge.run_once())
    assert calls == ["m-7"]  # 回合只跑一次
    assert bridge.stats.duplicates == 1
    assert bridge.stats.turns_ok == 1


def test_dedup_persistent_across_restarts(tmp_path):
    db = str(tmp_path / "dom.db")
    store1 = DomesticStore(db_path=db)
    assert store1.first_time_seen("m-x", 1000) is True
    assert store1.first_time_seen("m-x", 2000) is False
    store2 = DomesticStore(db_path=db)  # 重启（新 store 同 db）仍认得
    assert store2.first_time_seen("m-x", 3000) is False


def test_turn_failure_sends_failure_notice(tmp_path):
    async def turn(msg: DomesticMessage) -> str:
        raise RuntimeError("回合炸了")

    bridge, transport, _ = _make_bridge(tmp_path, turn_fn=turn)
    transport.push(_msg(3))
    assert asyncio.run(bridge.run_once()) == 1
    # 失败必须可见：给用户发失败告知，绝不无声消失
    assert len(transport.sent) == 1
    assert transport.sent[0][1] == DEFAULT_FAILURE_NOTICE
    assert bridge.stats.turns_failed == 1


def test_build_domestic_prompt_mentions_channel():
    m = _msg(1, channel=CHANNEL_FEISHU, from_name="张三")
    p = build_domestic_prompt(m)
    assert "feishu" in p and "张三" in p and "请直接回复" in p


# ── 自建应用传输（token / 发消息 API 负载，真网络换桩） ─────────────


def test_app_transport_token_parse_dingtalk():
    calls = []

    def fake_http_full(url, payload, headers=None):
        calls.append((url, payload))
        return True, "HTTP 200", {"accessToken": "tok-abc", "expireIn": 7200}

    t = DomesticAppTransport(CHANNEL_DINGTALK, "key", "secret", http_full=fake_http_full)
    assert t.ensure_token() == "tok-abc"
    assert calls[0][0].endswith("/oauth2/accessToken")
    assert calls[0][1] == {"appKey": "key", "appSecret": "secret"}
    # 二次调用走缓存，不再发请求
    t.ensure_token()
    assert len(calls) == 1


def test_app_transport_token_parse_feishu():
    def fake_http_full(url, payload, headers=None):
        return True, "HTTP 200", {"tenant_access_token": "t-fs", "expire": 7200}

    t = DomesticAppTransport(CHANNEL_FEISHU, "id", "sec", http_full=fake_http_full)
    assert t.ensure_token() == "t-fs"


def test_app_transport_send_api_shape_dingtalk():
    def fake_http_full(url, payload, headers=None):
        return True, "HTTP 200", {}

    t = DomesticAppTransport(CHANNEL_DINGTALK, "rc", "sec", http_full=fake_http_full)
    t._token = "tok"
    url, payload, headers = t._send_api("user-1", "hello")
    assert "robot/oToMessages/batchSend" in url
    assert payload["robotCode"] == "rc" and payload["userIds"] == ["user-1"]
    assert json.loads(payload["msgParam"])["content"] == "hello"
    assert headers["x-acs-dingtalk-access-token"] == "tok"


def test_app_transport_send_api_shape_feishu():
    t = DomesticAppTransport(CHANNEL_FEISHU, "id", "sec", http_full=lambda *a, **k: (True, "", {}))
    t._token = "tok"
    url, payload, headers = t._send_api("ou-x", "hello")
    assert "im/v1/messages" in url
    assert payload["receive_id"] == "ou-x" and payload["msg_type"] == "text"
    assert json.loads(payload["content"])["text"] == "hello"
    assert headers["Authorization"] == "Bearer tok"


# ── message_router 接线 ───────────────────────────────────────────


def test_message_router_send_outbound_webhook_fast_path(monkeypatch):
    captured = []
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen_capture(captured))
    from agent.message_router import UnifiedMessageRouter

    r = UnifiedMessageRouter.__new__(UnifiedMessageRouter)
    # 手工补最小字段（不走 __init__，避免建库）
    from agent.message_router import _RouterStats
    r.stats = _RouterStats()
    r._note_error = lambda msg: None
    r._record_out = lambda *a, **k: None
    res = asyncio.run(
        r._send_domestic("dingtalk", "https://hook/x", "SoulMate 说你好"))
    assert res.status == "delivered"
    assert json.loads(captured[0][1].decode("utf-8")) == echo_payload(
        "dingtalk", "SoulMate", "SoulMate 说你好")


def test_message_router_channels_include_domestic():
    from agent.message_router import CHANNELS, DOMESTIC_CHANNELS

    assert set(DOMESTIC_CHANNELS) <= set(CHANNELS)
    assert "dingtalk" in CHANNELS and "feishu" in CHANNELS
