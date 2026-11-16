"""DHT（BEP5）单测：路由表/token/紧凑编解码 + 假 DHT 端点的 KRPC 端到端。"""

import asyncio
import socket
import struct

import pytest

from download_engine import bencode, dht


# ── 纯逻辑 ───────────────────────────────────────────────────


def test_xor_distance_and_compact_roundtrip():
    a, b = b"\x00" * 20, b"\x00" * 19 + b"\x01"
    assert dht.xor_distance(a, a) == 0
    assert dht.xor_distance(a, b) == 1
    nodes = [dht.Node(id=bytes([i]) * 20, host="1.2.3.4", port=6881) for i in range(3)]
    raw = dht.encode_nodes(nodes)
    assert len(raw) == 26 * 3
    back = dht.decode_nodes(raw)
    assert [(n.id, n.host, n.port) for n in back] == [(n.id, n.host, n.port) for n in nodes]
    peers = dht.decode_peers(dht.encode_peers([("9.9.9.9", 51413), ("8.8.8.8", 1)]))
    assert peers == [("9.9.9.9", 51413), ("8.8.8.8", 1)]


def test_routing_table_buckets_and_closest():
    me = b"\x00" * 20
    t = dht.RoutingTable(me)
    for i in range(1, 21):
        t.add(dht.Node(id=bytes([i]) * 20, host="10.0.0.1", port=1000 + i))
    assert len(t) == 20
    # 目标贴近 0x03… 的节点最近
    closest = t.closest(bytes([3]) * 20, k=3)
    assert closest[0].id == bytes([3]) * 20
    # 重复地址不重复入表；k 淘汰
    t.add(dht.Node(id=b"\xff" * 20, host="10.0.0.1", port=1001))
    assert len(t) == 20
    t.remove(("10.0.0.1", 1005))
    assert len(t) == 19


def test_token_issue_and_validate():
    tb = dht.TokenBucket()
    tok = tb.issue("1.2.3.4")
    assert tb.validate("1.2.3.4", tok)
    assert not tb.validate("5.6.7.8", tok)       # 换 IP 不认
    assert not tb.validate("1.2.3.4", b"bad!!bad")
    # 同 IP 稳定
    assert tb.issue("1.2.3.4") == tok


# ── 假 DHT 端点（独立实现，不复用被测代码路径） ─────────────────


class FakeDhtNode:
    """最小 KRPC 服务端：ping/find_node/get_peers/announce_peer + token 校验。"""

    def __init__(self, node_id: bytes, known_nodes=None, peers=None):
        self.node_id = node_id
        self.known_nodes = known_nodes or []
        self.peers = peers or {}
        self.announced = []          # [(info_hash, port, token)]
        self.transport = None
        self.port = None

    async def start(self):
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _FakeProto(self), local_addr=("127.0.0.1", 0)
        )
        self.port = self.transport.get_extra_info("sockname")[1]

    def close(self):
        if self.transport:
            self.transport.close()

    def as_node(self):
        return dht.Node(id=self.node_id, host="127.0.0.1", port=self.port)

    def handle(self, data: bytes, addr):
        msg = bencode.decode(data)
        if msg.get(b"y") != b"q":
            return
        tid = msg.get(b"t")
        q = msg.get(b"q")
        args = msg.get(b"a") or {}
        r = {b"id": self.node_id}
        if q == b"get_peers":
            r[b"token"] = b"tok" + str(self.port).encode()
            vals = self.peers.get(args.get(b"info_hash"))
            if vals:
                r[b"values"] = [dht.encode_peers([p]) for p in vals]
            r[b"nodes"] = dht.encode_nodes(self.known_nodes)
        elif q == b"find_node":
            r[b"nodes"] = dht.encode_nodes(self.known_nodes)
        elif q == b"announce_peer":
            tok = args.get(b"token")
            expect = b"tok" + str(self.port).encode()
            if tok != expect:
                self.transport.sendto(
                    bencode.encode({b"t": tid, b"y": b"e", b"e": [203, b"Bad token"]}), addr
                )
                return
            self.announced.append((args.get(b"info_hash"), args.get(b"port"), tok))
        self.transport.sendto(bencode.encode({b"t": tid, b"y": b"r", b"r": r}), addr)


class _FakeProto(asyncio.DatagramProtocol):
    def __init__(self, node):
        self.node = node

    def connection_made(self, transport):
        self.node.transport = transport

    def datagram_received(self, data, addr):
        self.node.handle(data, addr)


# ── 端到端（loopback UDP） ───────────────────────────────────


def test_get_peers_end_to_end_via_fake_nodes():
    info_hash = b"\xab" * 20
    peers_for_hash = [("5.5.5.5", 6881), ("6.6.6.6", 51413)]

    async def scenario():
        node_b = FakeDhtNode(b"\x02" * 20, peers={info_hash: peers_for_hash})
        await node_b.start()
        node_a = FakeDhtNode(b"\x01" * 20, known_nodes=[node_b.as_node()])
        await node_a.start()
        client = dht.DhtClient(
            bootstrap=[f"127.0.0.1:{node_a.port}"],
            node_id=b"\xaa" * 20,
            announce_port=7777,
        )
        got = await client.get_peers(info_hash)
        table_len = len(client.table)
        await client.close()
        node_a.close()
        node_b.close()
        return got, table_len, node_b

    got, table_len, node_b = asyncio.run(scenario())
    assert sorted(got) == sorted(peers_for_hash), f"DHT 应找到 peers: {got}"
    assert table_len >= 2, "bootstrap/find_node 应填充路由表"
    # announce_port 配置后应向给了 token 的节点 announce_peer（token 校验通过）
    assert node_b.announced and node_b.announced[0][0] == info_hash
    assert node_b.announced[0][1] == 7777


def test_announce_peer_token_validation():
    info_hash = b"\xcd" * 20

    async def scenario():
        node = FakeDhtNode(b"\x03" * 20, peers={info_hash: [("9.9.9.9", 1)]})
        await node.start()
        client = dht.DhtClient(bootstrap=[f"127.0.0.1:{node.port}"], node_id=b"\xaa" * 20)
        # 先 get_peers 拿 token
        await client.get_peers(info_hash)
        # 正确 token → announce 成功
        ok_node = node.as_node()
        resp_ok = await client._proto.query(  # noqa: SLF001
            ok_node, "announce_peer",
            {b"id": client.node_id, b"info_hash": info_hash, b"port": 4242,
             b"token": b"tok" + str(node.port).encode(), b"implied_port": 0},
        )
        # 错误 token → KRPC 203 错误（query 抛 DhtError）
        err = None
        try:
            await client._proto.query(  # noqa: SLF001
                ok_node, "announce_peer",
                {b"id": client.node_id, b"info_hash": info_hash, b"port": 4242,
                 b"token": b"wrong-token", b"implied_port": 0},
            )
        except dht.DhtError as e:
            err = e
        await client.close()
        node.close()
        return resp_ok, err, node

    resp_ok, err, node = asyncio.run(scenario())
    assert resp_ok.get(b"y") == b"r"
    assert err is not None and "203" in str(err), f"错误 token 必须被拒: {err}"
    assert node.announced[-1][1] == 4242


def test_get_peers_without_network_degrades():
    """bootstrap 全不可达：快速返回空集，不抛异常。"""

    async def scenario():
        client = dht.DhtClient(bootstrap=["127.0.0.1:1"], node_id=b"\x00" * 20)
        return await asyncio.wait_for(client.get_peers(b"\xee" * 20), 15)

    assert asyncio.run(scenario()) == []


def test_incoming_query_served_with_token():
    """我们自身也是 DHT 节点：get_peers 入站 → 回 token + nodes；announce_peer 校验。"""

    async def scenario():
        client = dht.DhtClient(bootstrap=[], node_id=b"\x11" * 20, announce_port=None)
        assert await client._ensure_started()  # noqa: SLF001
        addr = client._proto.transport.get_extra_info("sockname")  # noqa: SLF001
        peer = dht.Node(id=b"\x22" * 20, host="127.0.0.1", port=addr[1])
        client.table.add(peer)
        # 直接向自己发 get_peers（走真实 UDP 环回）
        tok_resp = await client._proto.query(peer, "get_peers", {b"id": peer.id, b"info_hash": b"\x33" * 20})  # noqa: SLF001
        tok = tok_resp[b"r"][b"token"]
        good = await client._proto.query(peer, "announce_peer", {b"id": peer.id, b"info_hash": b"\x33" * 20, b"port": 9, b"token": tok})  # noqa: SLF001
        err = None
        try:
            await client._proto.query(peer, "announce_peer", {b"id": peer.id, b"info_hash": b"\x33" * 20, b"port": 9, b"token": b"x" * 8})  # noqa: SLF001
        except dht.DhtError as e:
            err = e
        await client.close()
        return tok_resp, good, err

    tok_resp, good, err = asyncio.run(scenario())
    assert b"token" in tok_resp[b"r"]
    assert good[b"y"] == b"r"
    assert err is not None and "203" in str(err)
