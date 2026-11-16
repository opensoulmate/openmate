"""trackers.py — tracker announce：HTTP（BEP3）+ UDP（BEP15），compact peers 解析。

磁力下载先靠 tracker（公共 tracker 列表注入）；DHT 见 dht.py（二期接口位）。
"""

from __future__ import annotations

import asyncio
import os
import random
import socket
import struct
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlsplit

import httpx

from . import bencode

# 公共 tracker 列表（磁力注入；来源：社区常用公共 tracker）
PUBLIC_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
    "http://tracker.openbittorrent.com:80/announce",
    "http://tracker.gbitt.info:80/announce",
    "udp://tracker.moeking.me:6969/announce",
    "udp://tracker.birkenwald.de:6969/announce",
    "udp://tracker.0x.tf:6969/announce",
]

EVENT_NONE, EVENT_COMPLETED, EVENT_STARTED, EVENT_STOPPED = 0, 1, 2, 3

UDP_PROTOCOL_ID = 0x41727101980
UDP_ACTION_CONNECT, UDP_ACTION_ANNOUNCE, UDP_ACTION_ERROR = 0, 1, 3


class TrackerError(Exception):
    pass


def build_announce_params(
    info_hash: bytes,
    peer_id: bytes,
    port: int,
    uploaded: int,
    downloaded: int,
    left: int,
    event: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "info_hash": info_hash,
        "peer_id": peer_id,
        "port": port,
        "uploaded": uploaded,
        "downloaded": downloaded,
        "left": left,
        "compact": 1,
        "numwant": 50,
    }
    if event:
        params["event"] = event
    return params


def parse_compact_peers(data: bytes) -> List[Tuple[str, int]]:
    """compact peers（6 字节/个：4 IP + 2 port）→ [(host, port)]。"""
    out = []
    for i in range(0, len(data) - len(data) % 6, 6):
        ip = socket.inet_ntoa(data[i : i + 4])
        (port,) = struct.unpack(">H", data[i + 4 : i + 6])
        if port:
            out.append((ip, port))
    return out


def parse_compact_peers6(data: bytes) -> List[Tuple[str, int]]:
    out = []
    for i in range(0, len(data) - len(data) % 18, 18):
        ip = socket.inet_ntop(socket.AF_INET6, data[i : i + 16])
        (port,) = struct.unpack(">H", data[i + 16 : i + 18])
        if port:
            out.append((ip, port))
    return out


def parse_announce_response(data: bytes) -> Tuple[int, List[Tuple[str, int]]]:
    """bencode 响应 → (interval, peers)。"""
    try:
        d = bencode.decode(data)
    except bencode.BencodeError as e:
        raise TrackerError(f"tracker 响应非法: {e}") from e
    if not isinstance(d, dict):
        raise TrackerError("tracker 响应必须是字典")
    if b"failure reason" in d:
        raise TrackerError(f"tracker 拒绝: {d[b'failure reason'].decode('utf-8', 'replace')}")
    interval = d.get(b"interval")
    interval = interval if isinstance(interval, int) and interval > 0 else 1800
    peers: List[Tuple[str, int]] = []
    raw = d.get(b"peers")
    if isinstance(raw, (bytes, bytearray)):
        peers.extend(parse_compact_peers(bytes(raw)))
    elif isinstance(raw, list):
        for p in raw:
            if isinstance(p, dict) and b"ip" in p and b"port" in p:
                try:
                    ip = p[b"ip"].decode() if isinstance(p[b"ip"], (bytes, bytearray)) else str(p[b"ip"])
                    peers.append((ip, int(p[b"port"])))
                except (ValueError, UnicodeDecodeError):
                    continue
    raw6 = d.get(b"peers6")
    if isinstance(raw6, (bytes, bytearray)):
        peers.extend(parse_compact_peers6(bytes(raw6)))
    return interval, peers


async def announce_http(
    url: str,
    params: Dict[str, Any],
    timeout: float = 15.0,
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[int, List[Tuple[str, int]]]:
    q = []
    for k, v in params.items():
        if isinstance(v, bytes):
            q.append(f"{k}={quote(v)}")  # info_hash/peer_id 需按原始字节百分号编码
        else:
            q.append(urlencode({k: v}))
    sep = "&" if "?" in url else "?"
    full = url + sep + "&".join(q)
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    try:
        resp = await client.get(full)
        if resp.status_code != 200:
            raise TrackerError(f"tracker HTTP {resp.status_code}")
        return parse_announce_response(resp.content)
    finally:
        if own_client:
            await client.aclose()


class _UdpProto(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        self.queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        self.queue.put_nowait(exc)


async def announce_udp(
    url: str,
    params: Dict[str, Any],
    timeout: float = 15.0,
) -> Tuple[int, List[Tuple[str, int]]]:
    """BEP15：connect → announce。"""
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or 80
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, proto=socket.IPPROTO_UDP)
    if not infos:
        raise TrackerError(f"UDP tracker 解析失败: {host}")
    family, _, _, _, addr = infos[0]
    transport, proto = await loop.create_datagram_endpoint(
        _UdpProto, remote_addr=addr, family=family  # type: ignore[arg-type]
    )
    try:
        txid = random.randint(0, 2**31 - 1)
        # connect
        transport.sendto(struct.pack(">QII", UDP_PROTOCOL_ID, UDP_ACTION_CONNECT, txid))
        resp = await asyncio.wait_for(proto.queue.get(), timeout)
        if isinstance(resp, Exception):
            raise TrackerError(f"UDP tracker 错误: {resp}")
        if len(resp) < 16:
            raise TrackerError("UDP connect 响应过短")
        action, rtxid = struct.unpack(">II", resp[:8])
        if rtxid != txid or action != UDP_ACTION_CONNECT:
            raise TrackerError("UDP connect 响应不匹配")
        conn_id = resp[8:16]

        event_map = {"completed": EVENT_COMPLETED, "started": EVENT_STARTED, "stopped": EVENT_STOPPED}
        event = event_map.get(params.get("event", ""), EVENT_NONE)
        key = random.randint(0, 2**31 - 1)
        pkt = (
            conn_id
            + struct.pack(">II", UDP_ACTION_ANNOUNCE, txid)
            + params["info_hash"]
            + params["peer_id"]
            + struct.pack(
                ">QQQIIIiH",
                int(params.get("downloaded", 0)),
                int(params.get("left", 0)),
                int(params.get("uploaded", 0)),
                event,
                0,
                key,
                -1,
                int(params.get("port", 6881)),
            )
        )
        transport.sendto(pkt)
        resp = await asyncio.wait_for(proto.queue.get(), timeout)
        if isinstance(resp, Exception):
            raise TrackerError(f"UDP tracker 错误: {resp}")
        if len(resp) < 20:
            raise TrackerError("UDP announce 响应过短")
        action, rtxid = struct.unpack(">II", resp[:8])
        if action == UDP_ACTION_ERROR:
            raise TrackerError(f"tracker 错误: {resp[8:].decode('utf-8', 'replace')}")
        if rtxid != txid or action != UDP_ACTION_ANNOUNCE:
            raise TrackerError("UDP announce 响应不匹配")
        interval = struct.unpack(">I", resp[8:12])[0] or 1800
        return interval, parse_compact_peers(resp[20:])
    finally:
        transport.close()


async def announce(
    url: str,
    params: Dict[str, Any],
    timeout: float = 15.0,
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[int, List[Tuple[str, int]]]:
    low = url.lower()
    if low.startswith(("http://", "https://")):
        return await announce_http(url, params, timeout, client)
    if low.startswith("udp://"):
        return await announce_udp(url, params, timeout)
    raise TrackerError(f"不支持的 tracker 协议: {url}")
