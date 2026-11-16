"""peer.py — BT peer wire protocol（BEP3）+ 扩展协议（BEP10）编解码与连接封装。"""

from __future__ import annotations

import asyncio
import struct
from typing import Optional, Tuple

from . import bencode

PROTOCOL = b"BitTorrent protocol"
HANDSHAKE_LEN = 68
EXT_PROTOCOL_BIT = 0x10  # reserved[5] 的扩展协议位（BEP10）

MSG_CHOKE = 0
MSG_UNCHOKE = 1
MSG_INTERESTED = 2
MSG_NOT_INTERESTED = 3
MSG_HAVE = 4
MSG_BITFIELD = 5
MSG_REQUEST = 6
MSG_PIECE = 7
MSG_CANCEL = 8
MSG_EXTENDED = 20

# 本端 ut_metadata 扩展 id（BEP10 m 字典里公布）
LOCAL_UT_METADATA_ID = 3


class PeerError(Exception):
    pass


# ── 纯编解码（单测覆盖） ──────────────────────────────────────


def build_handshake(info_hash: bytes, peer_id: bytes, ext: bool = True) -> bytes:
    assert len(info_hash) == 20 and len(peer_id) == 20
    reserved = bytearray(8)
    if ext:
        reserved[5] |= EXT_PROTOCOL_BIT
    return bytes([len(PROTOCOL)]) + PROTOCOL + bytes(reserved) + info_hash + peer_id


def parse_handshake(data: bytes) -> Tuple[bytes, bytes, bool]:
    """返回 (info_hash, peer_id, 对端支持扩展协议)。"""
    if len(data) != HANDSHAKE_LEN:
        raise PeerError(f"握手长度错误: {len(data)}")
    if data[0] != len(PROTOCOL) or data[1:20] != PROTOCOL:
        raise PeerError("握手协议串错误")
    reserved = data[20:28]
    info_hash = data[28:48]
    peer_id = data[48:68]
    return info_hash, peer_id, bool(reserved[5] & EXT_PROTOCOL_BIT)


def build_msg(msg_id: int, payload: bytes = b"") -> bytes:
    return struct.pack(">IB", 1 + len(payload), msg_id) + payload


def build_keepalive() -> bytes:
    return struct.pack(">I", 0)


def build_bitfield(have: bytes) -> bytes:
    return build_msg(MSG_BITFIELD, have)


def build_request(piece: int, offset: int, length: int) -> bytes:
    return build_msg(MSG_REQUEST, struct.pack(">III", piece, offset, length))


def parse_request(payload: bytes) -> Tuple[int, int, int]:
    if len(payload) != 12:
        raise PeerError("request 载荷长度错误")
    return struct.unpack(">III", payload)


def build_piece(index: int, begin: int, block: bytes) -> bytes:
    return build_msg(MSG_PIECE, struct.pack(">II", index, begin) + block)


def parse_piece(payload: bytes) -> Tuple[int, int, bytes]:
    if len(payload) < 8:
        raise PeerError("piece 载荷长度错误")
    index, begin = struct.unpack(">II", payload[:8])
    return index, begin, payload[8:]


def build_have(piece: int) -> bytes:
    return build_msg(MSG_HAVE, struct.pack(">I", piece))


def build_cancel(piece: int, offset: int, length: int) -> bytes:
    return build_msg(MSG_CANCEL, struct.pack(">III", piece, offset, length))


def parse_have(payload: bytes) -> int:
    if len(payload) != 4:
        raise PeerError("have 载荷长度错误")
    return struct.unpack(">I", payload)[0]


def build_extended(ext_id: int, payload: bytes) -> bytes:
    return build_msg(MSG_EXTENDED, bytes([ext_id]) + payload)


def parse_extended(payload: bytes) -> Tuple[int, bytes]:
    if not payload:
        raise PeerError("extended 载荷为空")
    return payload[0], payload[1:]


def build_ext_handshake(metadata_size: Optional[int] = None, version: str = "OpenMate/0.1") -> bytes:
    """BEP10 扩展握手（返回可直接写到 wire 的完整 extended 消息）。"""
    m = {b"ut_metadata": LOCAL_UT_METADATA_ID}
    d = {b"m": m, b"v": version.encode()}
    if metadata_size is not None:
        d[b"metadata_size"] = metadata_size
    return build_extended(0, bencode.encode(d))


def parse_ext_handshake(payload: bytes) -> Tuple[Optional[int], Optional[int]]:
    """返回 (对端 ut_metadata 扩展 id, metadata_size)。"""
    try:
        d = bencode.decode(payload)
    except bencode.BencodeError as e:
        raise PeerError(f"扩展握手载荷非法: {e}") from e
    if not isinstance(d, dict):
        raise PeerError("扩展握手载荷必须是字典")
    ut = None
    m = d.get(b"m")
    if isinstance(m, dict):
        v = m.get(b"ut_metadata")
        if isinstance(v, int):
            ut = v
    size = d.get(b"metadata_size")
    size = size if isinstance(size, int) and size > 0 else None
    return ut, size


def bitfield_bytes(num_pieces: int, have: list) -> bytes:
    """bool 列表 → bitfield 字节（高位在前）。"""
    out = bytearray((num_pieces + 7) // 8)
    for i, h in enumerate(have):
        if h:
            out[i // 8] |= 0x80 >> (i % 8)
    return bytes(out)


def bitfield_pieces(data: bytes, num_pieces: int) -> list:
    have = []
    for i in range(num_pieces):
        byte = data[i // 8] if i // 8 < len(data) else 0
        have.append(bool(byte & (0x80 >> (i % 8))))
    return have


# ── 连接封装 ────────────────────────────────────────────────


class PeerConnection:
    """单个 peer 连接：握手 + 逐条消息读写。"""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.peer_id: Optional[bytes] = None
        self.supports_ext = False
        self.remote_ut_metadata: Optional[int] = None
        self.remote_metadata_size: Optional[int] = None
        self.choked = True          # 对端是否 choke 我们
        self.interested = False
        self.bitfield: list = []

    @classmethod
    async def connect(cls, host: str, port: int, timeout: float = 10.0) -> "PeerConnection":
        reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        return cls(reader, writer)

    async def do_handshake(self, info_hash: bytes, peer_id: bytes) -> None:
        self.writer.write(build_handshake(info_hash, peer_id))
        await self.writer.drain()
        data = await asyncio.wait_for(self.reader.readexactly(HANDSHAKE_LEN), 15)
        got_hash, self.peer_id, self.supports_ext = parse_handshake(data)
        if got_hash != info_hash:
            raise PeerError("info_hash 不匹配")

    async def send(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def read_message(self) -> Tuple[Optional[int], bytes]:
        """返回 (msg_id, payload)；(None, b"") 表示 keepalive。"""
        hdr = await self.reader.readexactly(4)
        (length,) = struct.unpack(">I", hdr)
        if length == 0:
            return None, b""
        if length > 2 * 1024 * 1024:
            raise PeerError(f"消息长度异常: {length}")
        body = await self.reader.readexactly(length)
        return body[0], body[1:]

    async def close(self) -> None:
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
