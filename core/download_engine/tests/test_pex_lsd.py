"""PEX（BEP11 ut_pex）与 LSD（BEP14 局域网发现）单测。"""

import asyncio
import socket

import pytest

from download_engine import lsd, peer as pw
from download_engine.bt_engine import BtSession
from download_engine.dht import DhtClient
from download_engine.models import DownloadTask, TaskType
from download_engine.tests.test_bt_engine import _make_torrent


# ── PEX 编解码 ───────────────────────────────────────────────


def test_pex_codec_roundtrip():
    added = [("1.2.3.4", 6881), ("5.6.7.8", 51413)]
    dropped = [("9.9.9.9", 1)]
    payload = pw.build_pex(added=added, dropped=dropped)
    a, a6, d = pw.parse_pex(payload)
    assert sorted(a) == sorted(added)
    assert a6 == []
    assert d == dropped
    # 空载荷
    a, a6, d = pw.parse_pex(pw.build_pex())
    assert a == a6 == d == []


def test_ext_handshake_advertises_ut_pex():
    msg = pw.build_ext_handshake(123)
    # build_ext_handshake 返回完整 wire 帧（4B 长度 + msg id 20 + ext id + 载荷）
    ext_id, body = pw.parse_extended(msg[5:])
    assert ext_id == 0
    ut, size, ut_pex = pw.parse_ext_handshake(body)
    assert ut == pw.LOCAL_UT_METADATA_ID
    assert ut_pex == pw.LOCAL_UT_PEX_ID
    assert size == 123


def test_session_on_pex_merges_and_drops():
    raw, meta = _make_torrent()
    task = DownloadTask(id="bt-1", url="x", dir="/tmp", type=TaskType.BT)
    dht = DhtClient(bootstrap=[])
    sess = BtSession(task, "x", meta, None, None, lambda: None, dht)
    sess.used_peers.add(("5.6.7.8", 51413))   # 已连接的不能被 added 覆盖
    payload = pw.build_pex(added=[("1.2.3.4", 6881), ("5.6.7.8", 51413)], dropped=[("9.9.9.9", 1)])
    sess._on_pex(payload)
    assert ("1.2.3.4", 6881) in sess.known_peers
    assert ("9.9.9.9", 1) not in sess.known_peers
    # 已在用的 peer 不因 added 重复登记
    assert sess.known_peers >= {("1.2.3.4", 6881), ("5.6.7.8", 51413)}
    # dropped 剔除
    sess.known_peers.add(("9.9.9.9", 1))
    sess._on_pex(pw.build_pex(dropped=[("9.9.9.9", 1)]))
    assert ("9.9.9.9", 1) not in sess.known_peers
    # 非法载荷不炸
    sess._on_pex(b"garbage")


def test_user_added_peers_enter_pool():
    """options.peers（用户添加）与 tracker/DHT 同池。"""
    import os
    import tempfile

    from download_engine.bt_engine import BtEngine

    raw, meta = _make_torrent()
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "p.torrent")
    with open(path, "wb") as f:
        f.write(raw)

    async def scenario():
        engine = BtEngine(persist=lambda: None, default_dir=tmp)
        task = await engine.add(path, tmp, options={"peers": ["7.7.7.7:6881", "bad-peer"]})
        sess = engine._sessions[task.id]
        await engine.shutdown()
        return sess

    sess = asyncio.run(scenario())
    assert ("7.7.7.7", 6881) in sess.known_peers  # 用户添加的 peer 入池
    assert len(sess.known_peers) == 1  # "bad-peer" 被忽略


# ── LSD 编解码 ───────────────────────────────────────────────


def test_lsd_message_codec():
    h = b"\x01" * 20
    data = lsd.build_search(6881, {h})
    assert data.startswith(b"BT-SEARCH * HTTP/1.1")
    assert b"Port: 6881" in data
    assert f"Infohash: {h.hex().upper()}".encode() in data
    port, hashes = lsd.parse_search(data)
    assert port == 6881 and hashes == {h}
    resp = lsd.build_response(6882, {h})
    port, hashes = lsd.parse_response(resp)
    assert port == 6882 and hashes == {h}
    assert lsd.parse_search(b"GET / HTTP/1.1\r\n\r\n") is None
    assert lsd.parse_search(b"BT-SEARCH * HTTP/1.1\r\nPort: x\r\n\r\n") is None


def test_lsd_discovery_or_skip():
    """LSD 端到端：BT-SEARCH 解析/回调/回包走真实 socket。

    先试真实组播广播（环境支持则顺带验证组播收发）；组播在部分容器/防火墙下
    环回不可达属预期（降级容忍），此时以单播注入同一报文做确定性验证。
    """
    info_hash = b"\x77" * 20
    found = []
    reply = []
    injected = False

    async def scenario():
        nonlocal injected
        b = lsd.LsdClient(6882, lambda h, p: found.append((h, p)))
        b.add_info_hash(info_hash)
        if not await b.start():
            return "unavailable"
        try:
            a = lsd.LsdClient(6883, lambda h, p: None)
            a.add_info_hash(info_hash)
            if await a.start():
                a.announce_now()          # 真实组播广播
                await asyncio.sleep(1.0)
                await a.close()
            if not any(h == info_hash for h, _ in found):
                # 组播环回不可达（或只有外来 LSD 流量）：单播注入同一 BT-SEARCH 报文（等价收包路径）
                injected = True
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(3)
                bound_port = b._transport.get_extra_info("sockname")[1]  # noqa: SLF001
                for _ in range(3):
                    sock.sendto(lsd.build_search(6883, {info_hash}), ("127.0.0.1", bound_port))
                    await asyncio.sleep(0.7)
                    if found:
                        break
                try:
                    data, _ = await asyncio.to_thread(sock.recvfrom, 2048)
                    reply.append(data)
                except OSError:
                    pass
                sock.close()
                await asyncio.sleep(0.3)
        finally:
            await b.close()
        return "ok"

    state = asyncio.run(scenario())
    if state == "unavailable":
        pytest.skip("组播 239.255.255.250:6771 不可绑定（防火墙/容器），降级容忍")
    assert any(h == info_hash for h, _ in found), f"LSD 应发现局域网做种端: {found}"
    if injected:
        assert reply and reply[0].startswith(b"HTTP/1.1 200"), "发现方应回 200 OK（带自身端口）"
