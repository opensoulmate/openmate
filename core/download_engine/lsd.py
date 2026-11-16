"""lsd.py — LSD（BEP14）局域网服务发现（纯 Python stdlib + asyncio）。

在组播 239.255.255.250:6771 上收发 BT-SEARCH：
- 发现局域网内做种端 → 回调把 (infohash, peer) 交给 bt_engine 的 peer 池
- 自己也周期广播 BT-SEARCH（带监听端口），同网段对端可连过来
- 组播不可用（容器/防火墙/无多网卡权限）→ start() 返回 False，静默降级
"""

from __future__ import annotations

import asyncio
import socket
from typing import Callable, Optional, Set

MULTICAST_ADDR = "239.255.255.250"
MULTICAST_PORT = 6771
ANNOUNCE_INTERVAL = 300  # BEP14 推荐 5 分钟一次

PeerCallback = Callable[[bytes, tuple], None]  # (info_hash, (host, port))


def build_search(listen_port: int, infohashes: Set[bytes]) -> bytes:
    """BT-SEARCH 报文（BEP14）。"""
    lines = [
        "BT-SEARCH * HTTP/1.1",
        f"Host: {MULTICAST_ADDR}:{MULTICAST_PORT}",
        f"Port: {listen_port}",
    ]
    for h in sorted(infohashes):
        lines.append(f"Infohash: {h.hex().upper()}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def build_response(listen_port: int, infohashes: Set[bytes]) -> bytes:
    lines = ["HTTP/1.1 200 OK", f"Port: {listen_port}"]
    for h in sorted(infohashes):
        lines.append(f"Infohash: {h.hex().upper()}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("ascii")


def parse_search(data: bytes) -> Optional[tuple]:
    """BT-SEARCH 请求报文 → (listen_port, {info_hash…})；非 BT-SEARCH 返回 None。"""
    try:
        text = data.decode("ascii", "replace")
    except Exception:  # noqa: BLE001
        return None
    first = text.split("\r\n", 1)[0].strip()
    if not first.startswith("BT-SEARCH"):
        return None
    return _parse_headers(text)


def parse_response(data: bytes) -> Optional[tuple]:
    """200 OK 响应报文 → (listen_port, {info_hash…})。"""
    try:
        text = data.decode("ascii", "replace")
    except Exception:  # noqa: BLE001
        return None
    first = text.split("\r\n", 1)[0].strip()
    if not first.startswith("HTTP/1.1 200"):
        return None
    return _parse_headers(text)


def _parse_headers(text: str) -> Optional[tuple]:
    port = None
    hashes: Set[bytes] = set()
    for line in text.split("\r\n")[1:]:
        if not line.strip():
            break
        key, _, value = line.partition(":")
        key, value = key.strip().lower(), value.strip()
        if key == "port":
            try:
                port = int(value)
            except ValueError:
                return None
        elif key == "infohash":
            try:
                raw = bytes.fromhex(value)
            except ValueError:
                continue
            if len(raw) == 20:
                hashes.add(raw)
    if port is None or not hashes:
        return None
    return port, hashes


class LsdClient:
    """LSD 收发端。`on_peer(info_hash, (host, port))` 在发现局域网做种端时被调。"""

    def __init__(
        self,
        listen_port: int,
        on_peer: PeerCallback,
        announce_port: Optional[int] = None,
    ):
        self.listen_port = listen_port  # 我们的 BT 监听端口（报文 Port 头）
        self.announce_port = announce_port or listen_port
        self.on_peer = on_peer
        self.info_hashes: Set[bytes] = set()
        self.available = False
        self._transport: Optional[asyncio.DatagramTransport] = None
        self._announce_task: Optional[asyncio.Task] = None

    def add_info_hash(self, info_hash: bytes) -> None:
        self.info_hashes.add(info_hash)

    async def start(self) -> bool:
        """绑定组播端口并加入组播组；失败返回 False（降级容忍）。"""
        loop = asyncio.get_running_loop()
        for local_port in (MULTICAST_PORT, 0):
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _LsdProtocol(self),
                    local_addr=("0.0.0.0", local_port),
                    family=socket.AF_INET,
                )
                sock = transport.get_extra_info("socket")
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                mreq = socket.inet_aton(MULTICAST_ADDR) + socket.inet_aton("0.0.0.0")
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
            except OSError:
                continue
            self._transport = transport
            self.available = True
            self._announce_task = loop.create_task(self._announce_loop())
            return True
        return False

    async def _announce_loop(self) -> None:
        while True:
            self.announce_now()
            await asyncio.sleep(ANNOUNCE_INTERVAL)

    def announce_now(self) -> None:
        if self._transport is None or not self.info_hashes:
            return
        data = build_search(self.announce_port, self.info_hashes)
        try:
            self._transport.sendto(data, (MULTICAST_ADDR, MULTICAST_PORT))
        except OSError:
            pass

    def handle_datagram(self, data: bytes, addr: tuple) -> None:
        host = addr[0]
        parsed = parse_search(data)
        if parsed is not None:
            port, hashes = parsed
            # 回应对方（让它也知道我们）
            if self._transport is not None:
                try:
                    self._transport.sendto(
                        build_response(self.announce_port, self.info_hashes & hashes or self.info_hashes),
                        addr,
                    )
                except OSError:
                    pass
        else:
            parsed = parse_response(data)
            if parsed is None:
                return
            port, hashes = parsed
        for h in hashes:
            try:
                self.on_peer(h, (host, port))
            except Exception:  # noqa: BLE001
                pass

    async def close(self) -> None:
        if self._announce_task is not None:
            self._announce_task.cancel()
        if self._transport is not None:
            self._transport.close()
        self.available = False


class _LsdProtocol(asyncio.DatagramProtocol):
    def __init__(self, client: LsdClient):
        self.client = client

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.client.handle_datagram(data, addr)
