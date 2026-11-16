"""dht.py — DHT（BEP5 Kademlia）最小可用实现（纯 Python stdlib + asyncio）。

组成：
- KRPC over UDP：ping / find_node / get_peers / announce_peer（bencode，事务 id 匹配）
- Kademlia 路由表：160 个 k-bucket（k=8），XOR 距离最近节点查找
- token 机制：get_peers 应答发 token（sha1(ip+secret)），announce_peer 校验
- bootstrap：router.bittorrent.com:6881 等公共节点，启动做迭代 find_node(self)
- `get_peers(info_hash)`：迭代查询收集 peers（bt_engine 的磁力 peer 来源之一，
  与 tracker、用户添加 peer 求并集）；配置 `announce_port` 后才 announce_peer
  （当前引擎只出站连接、不接受入站，故默认不 announce）

网络不许（UDP 出站被封）时快速失败返回空集，不影响其他 peer 来源。
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
import struct
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import bencode

NODE_ID_LEN = 20
K = 8                    # 每 bucket 容量
ALPHA = 3                # 迭代查询并发度
BUCKETS = 160
QUERY_TIMEOUT = 3.0
TOKEN_ROTATE_SECS = 300

# 公共 bootstrap 节点（社区常驻）
DEFAULT_BOOTSTRAP = [
    "router.bittorrent.com:6881",
    "router.utorrent.com:6881",
    "dht.transmissionbt.com:6881",
    "dht.libtorrent.org:25401",
]


def random_node_id() -> bytes:
    return os.urandom(NODE_ID_LEN)


def xor_distance(a: bytes, b: bytes) -> int:
    return int.from_bytes(bytes(x ^ y for x, y in zip(a, b)), "big")


def encode_nodes(nodes: Sequence["Node"]) -> bytes:
    """紧凑节点信息（IPv4）：20B id + 4B ip + 2B port = 26B/节点。"""
    out = bytearray()
    for n in nodes:
        try:
            ip = socket.inet_aton(n.host)
        except OSError:
            continue
        out += n.id + ip + struct.pack(">H", n.port)
    return bytes(out)


def decode_nodes(data: bytes) -> List["Node"]:
    out = []
    for i in range(0, len(data) - len(data) % 26, 26):
        nid = data[i : i + 20]
        host = socket.inet_ntoa(data[i + 20 : i + 24])
        (port,) = struct.unpack(">H", data[i + 24 : i + 26])
        if port:
            out.append(Node(id=nid, host=host, port=port))
    return out


def encode_peers(peers: Sequence[Tuple[str, int]]) -> bytes:
    out = bytearray()
    for host, port in peers:
        try:
            out += socket.inet_aton(host) + struct.pack(">H", port)
        except OSError:
            continue
    return bytes(out)


def decode_peers(data: bytes) -> List[Tuple[str, int]]:
    out = []
    for i in range(0, len(data) - len(data) % 6, 6):
        host = socket.inet_ntoa(data[i : i + 4])
        (port,) = struct.unpack(">H", data[i + 4 : i + 6])
        if port:
            out.append((host, port))
    return out


@dataclass
class Node:
    id: bytes
    host: str
    port: int

    @property
    def addr(self) -> Tuple[str, int]:
        return (self.host, self.port)

    def __hash__(self) -> int:
        return hash((self.id, self.host, self.port))


class RoutingTable:
    """Kademlia 路由表：按与自身 id 的 XOR 距离分 160 个 bucket，每桶 FIFO+k 淘汰。"""

    def __init__(self, node_id: bytes):
        assert len(node_id) == NODE_ID_LEN
        self.node_id = node_id
        self.buckets: List[List[Node]] = [[] for _ in range(BUCKETS)]
        self._index: Dict[Tuple[str, int], Node] = {}

    def _bucket_of(self, nid: bytes) -> int:
        d = xor_distance(self.node_id, nid)
        if d == 0:
            return 0
        return max(0, BUCKETS - d.bit_length())

    def add(self, node: Node) -> None:
        if len(node.id) != NODE_ID_LEN or node.id == self.node_id:
            return
        if node.addr in self._index:
            return  # 已入表
        bucket = self.buckets[self._bucket_of(node.id)]
        if len(bucket) >= K:
            bucket.pop(0)  # 淘汰最老
        bucket.append(node)
        self._index[node.addr] = node

    def remove(self, addr: Tuple[str, int]) -> None:
        node = self._index.pop(addr, None)
        if node is not None:
            b = self.buckets[self._bucket_of(node.id)]
            if node in b:
                b.remove(node)

    def closest(self, target: bytes, k: int = K) -> List[Node]:
        nodes = sorted(self._index.values(), key=lambda n: xor_distance(n.id, target))
        return nodes[:k]

    def all(self) -> List[Node]:
        return list(self._index.values())

    def __len__(self) -> int:
        return len(self._index)


# ── token（BEP5：SHA1(ip + secret)，定时轮换） ─────────────────


class TokenBucket:
    def __init__(self) -> None:
        self._secret = os.urandom(16)
        self._prev = self._secret
        self._rotated = time.monotonic()

    def _maybe_rotate(self) -> None:
        if time.monotonic() - self._rotated > TOKEN_ROTATE_SECS:
            self._prev = self._secret
            self._secret = os.urandom(16)
            self._rotated = time.monotonic()

    def issue(self, host: str) -> bytes:
        self._maybe_rotate()
        return self._token(host, self._secret)

    def validate(self, host: str, token: bytes) -> bool:
        self._maybe_rotate()
        return token in (self._token(host, self._secret), self._token(host, self._prev))

    @staticmethod
    def _token(host: str, secret: bytes) -> bytes:
        try:
            ip = socket.inet_aton(host)
        except OSError:
            ip = host.encode()
        return hashlib.sha1(ip + secret).digest()[:8]


# ── KRPC 传输 ────────────────────────────────────────────────


class DhtError(Exception):
    pass


@dataclass
class _Query:
    future: asyncio.Future
    method: str
    node: Node


class KrpcProtocol(asyncio.DatagramProtocol):
    """KRPC 收发：请求事务匹配 + 入站查询应答（我们同时是服务端）。"""

    def __init__(self, client: "DhtClient"):
        self.client = client
        self.transport: Optional[asyncio.DatagramTransport] = None
        self._tid = int.from_bytes(os.urandom(2), "big")
        self._pending: Dict[bytes, _Query] = {}

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        try:
            msg = bencode.decode(data)
        except bencode.BencodeError:
            return
        if not isinstance(msg, dict):
            return
        tid = msg.get(b"t")
        y = msg.get(b"y")
        if y == b"r" and isinstance(tid, bytes):
            q = self._pending.pop(tid, None)
            if q is not None and not q.future.done():
                q.future.set_result(msg)
        elif y == b"q":
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._answer(msg, addr))
        elif y == b"e" and isinstance(tid, bytes):
            q = self._pending.pop(tid, None)
            if q is not None and not q.future.done():
                q.future.set_exception(DhtError(f"KRPC error: {msg.get(b'e')}"))

    async def _answer(self, msg: dict, addr: Tuple[str, int]) -> None:
        resp = self.client.handle_query(msg, addr)
        if resp is not None and self.transport is not None:
            self.transport.sendto(bencode.encode(resp), addr)

    async def query(self, node: Node, method: str, args: dict) -> dict:
        if self.transport is None:
            raise DhtError("DHT 未启动")
        self._tid = (self._tid + 1) & 0xFFFF
        tid = self._tid.to_bytes(2, "big")
        fut = asyncio.get_running_loop().create_future()
        self._pending[tid] = _Query(future=fut, method=method, node=node)
        msg = {b"t": tid, b"y": b"q", b"q": method.encode(), b"a": args}
        self.transport.sendto(bencode.encode(msg), node.addr)
        try:
            return await asyncio.wait_for(fut, QUERY_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(tid, None)
            raise DhtError(f"{method} 查询超时 {node.addr}")

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()


class DhtClient:
    """bt_engine 的 DHT peer 来源。接口与占位版兼容：`get_peers(info_hash)`。"""

    def __init__(
        self,
        bootstrap: Optional[List[str]] = None,
        node_id: Optional[bytes] = None,
        announce_port: Optional[int] = None,
    ):
        self.node_id = node_id or random_node_id()
        self.bootstrap = bootstrap if bootstrap is not None else list(DEFAULT_BOOTSTRAP)
        self.announce_port = announce_port  # 有监听端口才 announce_peer
        self.table = RoutingTable(self.node_id)
        self.tokens = TokenBucket()
        self._known_peers: Dict[bytes, set] = {}  # info_hash -> {(host, port)}（get_peers 应答缓存）
        self._proto: Optional[KrpcProtocol] = None
        self._start_lock = asyncio.Lock()
        self._bootstrapped = False

    # ── 生命周期 ────────────────────────────────────────────

    async def _ensure_started(self) -> bool:
        async with self._start_lock:
            if self._proto is not None:
                return True
            loop = asyncio.get_running_loop()
            try:
                transport, proto = await loop.create_datagram_endpoint(
                    lambda: KrpcProtocol(self), local_addr=("0.0.0.0", 0)
                )
            except OSError:
                return False  # 无网络/防火墙：静默降级
            self._proto = proto
            proto.transport = transport
            return True

    async def bootstrap_once(self) -> int:
        """对 bootstrap 节点做 find_node(self.id) 扩散，填充路由表。返回入表节点数。"""
        if not await self._ensure_started():
            return 0
        seeds = await self._resolve_bootstrap()
        for node in seeds:
            self.table.add(node)  # 种子先入表（占位 id），query 后由真实 id 邻居充实
        before = len(self.table)
        # 种子的 id 未知（占位），直接向它们查询以获得真实邻居
        self._absorb_nodes(await self._query_many(
            seeds, "find_node", {b"id": self.node_id, b"target": self.node_id}
        ))
        for _ in range(2):
            targets = self.table.closest(self.node_id, K * 2)
            if not targets:
                break
            self._absorb_nodes(await self._query_many(
                targets, "find_node", {b"id": self.node_id, b"target": self.node_id}
            ))
        self._bootstrapped = True
        return len(self.table) - before

    def _absorb_nodes(self, responses: List[Tuple[Node, dict]]) -> None:
        for _node, resp in responses:
            r = resp.get(b"r")
            if isinstance(r, dict):
                for n in decode_nodes(r.get(b"nodes") or b""):
                    self.table.add(n)

    async def _resolve_bootstrap(self) -> List[Node]:
        out = []
        for entry in self.bootstrap:
            host, _, port = entry.rpartition(":")
            if not host:
                continue
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host, int(port), proto=socket.IPPROTO_UDP
                )
                addr = infos[0][4]
                out.append(Node(id=b"\x00" * NODE_ID_LEN, host=addr[0], port=addr[1]))
            except (OSError, ValueError):
                continue
        return out

    # ── 查询 API ────────────────────────────────────────────

    async def get_peers(self, info_hash: bytes) -> List[Tuple[str, int]]:
        """迭代 get_peers：收集 peers（tracker 之外的磁力 peer 来源）。"""
        if len(info_hash) != NODE_ID_LEN:
            return []
        if not await self._ensure_started():
            return []
        if not self._bootstrapped or len(self.table) == 0:
            await self.bootstrap_once()
        found: Dict[Tuple[str, int], None] = dict.fromkeys(
            self._known_peers.get(info_hash, ())
        )
        tokens: List[Tuple[Node, bytes]] = []
        seen: set = set()
        shortlist = self.table.closest(info_hash, K * 2)
        rounds = 0
        while shortlist and rounds < 8:
            batch = [n for n in shortlist if n.addr not in seen][:ALPHA]
            if not batch:
                break
            for n in batch:
                seen.add(n.addr)
            responses = await self._query_many(
                batch, "get_peers", {b"id": self.node_id, b"info_hash": info_hash}
            )
            best = min((xor_distance(n.id, info_hash) for n in shortlist), default=1 << 160)
            closer = False
            for node, resp in responses:
                r = resp.get(b"r")
                if not isinstance(r, dict):
                    continue
                for peer in r.get(b"values") or []:
                    if isinstance(peer, (bytes, bytearray)) and len(peer) == 6:
                        for hp in decode_peers(bytes(peer)):
                            found[hp] = None
                tok = r.get(b"token")
                if isinstance(tok, bytes):
                    tokens.append((node, tok))
                for n in decode_nodes(r.get(b"nodes") or b""):
                    self.table.add(n)
                    if n.addr not in seen:
                        shortlist.append(n)
                        if xor_distance(n.id, info_hash) < best:
                            closer = True
            shortlist.sort(key=lambda n: xor_distance(n.id, info_hash))
            shortlist = shortlist[: K * 2]
            rounds += 1
            if found and rounds >= 3:
                break
            if not closer and not found and rounds >= 4:
                break
        if self.announce_port and tokens:
            await self._announce(info_hash, tokens)
        return [p for p in found if p[1] > 0]

    async def _announce(self, info_hash: bytes, tokens: List[Tuple[Node, bytes]]) -> None:
        for node, tok in tokens[:K]:
            args = {
                b"id": self.node_id,
                b"info_hash": info_hash,
                b"port": self.announce_port,
                b"token": tok,
                b"implied_port": 0,
            }
            try:
                await self._proto.query(node, "announce_peer", args)  # type: ignore[union-attr]
            except DhtError:
                continue

    async def ping(self, node: Node) -> bool:
        if not await self._ensure_started():
            return False
        try:
            resp = await self._proto.query(node, "ping", {b"id": self.node_id})  # type: ignore[union-attr]
            r = resp.get(b"r")
            if isinstance(r, dict) and isinstance(r.get(b"id"), bytes) and len(r[b"id"]) == NODE_ID_LEN:
                self.table.add(Node(id=r[b"id"], host=node.host, port=node.port))
                return True
        except DhtError:
            pass
        return False

    async def find_node(self, node: Node, target: bytes) -> List[Node]:
        if not await self._ensure_started():
            return []
        try:
            resp = await self._proto.query(
                node, "find_node", {b"id": self.node_id, b"target": target}
            )  # type: ignore[union-attr]
        except DhtError:
            return []
        r = resp.get(b"r")
        if not isinstance(r, dict):
            return []
        nodes = decode_nodes(r.get(b"nodes") or b"")
        for n in nodes:
            self.table.add(n)
        return nodes

    async def _query_many(
        self, nodes: Iterable[Node], method: str, args: dict
    ) -> List[Tuple[Node, dict]]:
        async def one(n: Node) -> Optional[Tuple[Node, dict]]:
            try:
                return n, await self._proto.query(n, method, args)  # type: ignore[union-attr]
            except DhtError:
                return None

        results = await asyncio.gather(*[one(n) for n in nodes])
        return [r for r in results if r is not None]

    async def close(self) -> None:
        if self._proto is not None:
            self._proto.close()
            self._proto = None

    # ── 服务端：应答入站查询（含 token 校验） ──────────────────

    def handle_query(self, msg: dict, addr: Tuple[str, int]) -> Optional[dict]:
        method = msg.get(b"q")
        args = msg.get(b"a")
        tid = msg.get(b"t", b"\x00\x00")
        if not isinstance(args, dict):
            return None
        reply: dict = {b"id": self.node_id}
        if method == b"ping":
            pass
        elif method == b"find_node":
            target = args.get(b"target")
            target = target if isinstance(target, bytes) and len(target) == NODE_ID_LEN else self.node_id
            reply[b"nodes"] = encode_nodes(self.table.closest(target, K))
        elif method == b"get_peers":
            info_hash = args.get(b"info_hash")
            reply[b"token"] = self.tokens.issue(addr[0])
            peers = self._known_peers.get(info_hash, set()) if isinstance(info_hash, bytes) else set()
            if peers:
                reply[b"values"] = [encode_peers([p]) for p in list(peers)[:50]]
            target = info_hash if isinstance(info_hash, bytes) else self.node_id
            reply[b"nodes"] = encode_nodes(self.table.closest(target, K))
        elif method == b"announce_peer":
            tok = args.get(b"token")
            if not isinstance(tok, bytes) or not self.tokens.validate(addr[0], tok):
                return {b"t": tid, b"y": b"e", b"e": [203, b"Bad token"]}
            info_hash = args.get(b"info_hash")
            port = args.get(b"port")
            if isinstance(info_hash, bytes) and isinstance(port, int):
                use_port = addr[1] if args.get(b"implied_port") == 1 else port
                self._known_peers.setdefault(info_hash, set()).add((addr[0], use_port))
        else:
            return {b"t": tid, b"y": b"e", b"e": [204, b"Method Unknown"]}
        return {b"t": tid, b"y": b"r", b"r": reply}

    def report_peer(self, info_hash: bytes, peer: Tuple[str, int]) -> None:
        """上层（PEX/LSD/tracker）把 peer 喂给 get_peers 应答缓存（回馈网络）。"""
        self._known_peers.setdefault(info_hash, set()).add(peer)
