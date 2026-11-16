"""metadata.py — ut_metadata 元数据交换（BEP9/BEP10）：磁力链不带 .torrent 时
向对端拉取 info dict（16KiB 分片），sha1 校验 == infohash 后解析为文件清单。"""

from __future__ import annotations

import hashlib
from typing import Dict, Optional, Tuple

from . import bencode

METADATA_PIECE_SIZE = 16 * 1024

MSG_TYPE_REQUEST = 0
MSG_TYPE_DATA = 1
MSG_TYPE_REJECT = 2


class MetadataError(Exception):
    pass


# ── ut_metadata 消息编解码（单测覆盖） ─────────────────────────


def encode_request(piece: int) -> bytes:
    return bencode.encode({b"msg_type": MSG_TYPE_REQUEST, b"piece": piece})


def encode_data(piece: int, total_size: int, block: bytes) -> bytes:
    head = bencode.encode(
        {b"msg_type": MSG_TYPE_DATA, b"piece": piece, b"total_size": total_size}
    )
    return head + block


def encode_reject(piece: int) -> bytes:
    return bencode.encode({b"msg_type": MSG_TYPE_REJECT, b"piece": piece})


def parse_message(payload: bytes) -> Tuple[int, Dict]:
    """返回 (msg_type, 头部字段)。data 消息的原始块通过 split_data 取。"""
    try:
        d, consumed = bencode.decode_prefix(payload)
    except bencode.BencodeError as e:
        raise MetadataError(f"ut_metadata 消息非法: {e}") from e
    if not isinstance(d, dict) or b"msg_type" not in d or b"piece" not in d:
        raise MetadataError("ut_metadata 消息缺少字段")
    info = {
        "msg_type": d[b"msg_type"],
        "piece": d[b"piece"],
        "total_size": d.get(b"total_size"),
        "header_len": consumed,
    }
    return d[b"msg_type"], info


def split_data(payload: bytes) -> Tuple[Dict, bytes]:
    """data 消息：(头部, 原始块)。"""
    msg_type, info = parse_message(payload)
    if msg_type != MSG_TYPE_DATA:
        raise MetadataError(f"不是 data 消息: {msg_type}")
    return info, payload[info["header_len"] :]


class MetadataAssembler:
    """收齐元数据分片 → sha1 校验 → 返回原始 info dict 字节。"""

    def __init__(self, info_hash: bytes, total_size: Optional[int] = None):
        if total_size is not None and total_size <= 0:
            raise MetadataError("metadata_size 非法")
        self.info_hash = info_hash
        self.total_size = total_size
        self.pieces: Dict[int, bytes] = {}

    @property
    def num_pieces(self) -> int:
        if not self.total_size:
            return 0
        return (self.total_size + METADATA_PIECE_SIZE - 1) // METADATA_PIECE_SIZE

    def needed_pieces(self) -> list:
        return [i for i in range(self.num_pieces) if i not in self.pieces]

    def add(self, piece: int, block: bytes) -> None:
        if not self.total_size:
            raise MetadataError("metadata_size 未知")
        if piece < 0 or piece >= self.num_pieces:
            raise MetadataError(f"元数据片号越界: {piece}")
        expect = min(METADATA_PIECE_SIZE, self.total_size - piece * METADATA_PIECE_SIZE)
        if len(block) != expect:
            raise MetadataError(f"元数据片长度错误: {len(block)} != {expect}")
        self.pieces[piece] = bytes(block)

    def complete(self) -> bool:
        return self.total_size is not None and len(self.pieces) == self.num_pieces

    def assemble(self) -> bytes:
        if not self.complete():
            raise MetadataError("元数据未收齐")
        raw = b"".join(self.pieces[i] for i in range(self.num_pieces))
        if hashlib.sha1(raw).digest() != self.info_hash:
            raise MetadataError("元数据 sha1 校验失败（!= infohash）")
        return raw
