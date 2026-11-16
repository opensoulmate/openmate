"""torrent.py — .torrent 元数据解析（info dict → 文件清单 / 分片 / infohash）。

infohash = sha1(原始 info dict 字节)，因此这里不重编码，直接取原始字节。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from . import bencode


class TorrentError(ValueError):
    pass


@dataclass
class TorrentFile:
    path: str      # 相对路径（多文件含目录前缀，'/' 分隔）
    length: int
    offset: int    # 在扁平字节流中的起始偏移


@dataclass
class TorrentMeta:
    info_hash: bytes
    name: str
    piece_length: int
    pieces: List[bytes]                 # 每片 20 字节 SHA1
    files: List[TorrentFile]
    total_length: int
    info_raw: bytes = b""               # 原始 info dict 字节（ut_metadata 就是它）
    announce: Optional[str] = None
    announce_list: List[str] = field(default_factory=list)
    private: bool = False

    @property
    def num_pieces(self) -> int:
        return len(self.pieces)


def _find_info_raw(data: bytes) -> bytes:
    """定位顶层 info dict 的原始字节（infohash 需要原始字节，不能重编码）。"""
    if not data.startswith(b"d"):
        raise TorrentError("torrent 不是字典")
    pos = 1
    while pos < len(data):
        if data[pos] == ord("e"):
            break
        try:
            key, kcons = bencode.decode_prefix(data[pos:])
        except bencode.BencodeError as e:
            raise TorrentError(f"torrent 结构损坏: {e}") from e
        pos += kcons
        try:
            value, vcons = bencode.decode_prefix(data[pos:])
        except bencode.BencodeError as e:
            raise TorrentError(f"torrent 结构损坏: {e}") from e
        if key == b"info":
            return data[pos : pos + vcons]
        pos += vcons
    raise TorrentError("torrent 缺少 info 字典")


def parse_torrent(data: bytes) -> TorrentMeta:
    """解析 .torrent 字节流 → TorrentMeta。"""
    root = bencode.decode(data)
    if not isinstance(root, dict):
        raise TorrentError("torrent 顶层必须是字典")
    info_raw = _find_info_raw(data)
    return _meta_from_info(info_raw, root)


def parse_info_raw(info_raw: bytes, announce_list: Optional[List[str]] = None) -> TorrentMeta:
    """从原始 info dict 字节构造 TorrentMeta（ut_metadata 拉回来的就是它）。"""
    return _meta_from_info(info_raw, {b"announce-list": [[u.encode()] for u in announce_list or []]})


def _meta_from_info(info_raw: bytes, root: dict) -> TorrentMeta:
    info_hash = hashlib.sha1(info_raw).digest()
    info = bencode.decode(info_raw)
    if not isinstance(info, dict):
        raise TorrentError("info 必须是字典")

    name = _decode_str(info.get(b"name")) or "unknown"
    piece_length = info.get(b"piece length")
    if not isinstance(piece_length, int) or piece_length <= 0:
        raise TorrentError("info.piece length 非法")
    pieces_raw = info.get(b"pieces")
    if not isinstance(pieces_raw, (bytes, bytearray)) or len(pieces_raw) % 20 != 0:
        raise TorrentError("info.pieces 非法")
    pieces = [bytes(pieces_raw[i : i + 20]) for i in range(0, len(pieces_raw), 20)]

    files: List[TorrentFile] = []
    offset = 0
    if b"files" in info:
        entries = info[b"files"]
        if not isinstance(entries, list):
            raise TorrentError("info.files 非法")
        for ent in entries:
            if not isinstance(ent, dict):
                raise TorrentError("info.files 条目非法")
            length = ent.get(b"length")
            if not isinstance(length, int) or length < 0:
                raise TorrentError("file.length 非法")
            parts = ent.get(b"path")
            if not isinstance(parts, list) or not parts:
                raise TorrentError("file.path 非法")
            segs = []
            for p in parts:
                s = _decode_str(p)
                if not s or s in (".", "..") or "/" in s or "\\" in s:
                    raise TorrentError(f"file.path 段非法: {s!r}")
                segs.append(s)
            files.append(TorrentFile(path=name + "/" + "/".join(segs), length=length, offset=offset))
            offset += length
    else:
        length = info.get(b"length")
        if not isinstance(length, int) or length < 0:
            raise TorrentError("info.length 非法")
        files.append(TorrentFile(path=name, length=length, offset=0))
        offset = length

    announce = _decode_str(root.get(b"announce"))
    announce_list: List[str] = []
    al = root.get(b"announce-list")
    if isinstance(al, list):
        for tier in al:
            if isinstance(tier, list):
                for u in tier:
                    s = _decode_str(u)
                    if s and s not in announce_list:
                        announce_list.append(s)
    if announce and announce not in announce_list:
        announce_list.insert(0, announce)

    return TorrentMeta(
        info_hash=info_hash,
        name=name,
        piece_length=piece_length,
        pieces=pieces,
        files=files,
        total_length=offset,
        info_raw=info_raw,
        announce=announce,
        announce_list=announce_list,
        private=bool(info.get(b"private", 0)),
    )


def file_list(meta: TorrentMeta) -> List[Tuple[str, int]]:
    """文件清单 [(path, length)]。"""
    return [(f.path, f.length) for f in meta.files]


def _decode_str(v) -> Optional[str]:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).decode("utf-8", "replace")
    return None


def build_torrent_bytes(
    name: str,
    files: List[Tuple[str, int]],
    piece_length: int,
    pieces: List[bytes],
    announce: Optional[str] = None,
) -> bytes:
    """构造 .torrent 字节（测试与做种 announce 用）。files=[(相对路径, 长度)]。"""
    info: dict = {b"name": name.encode(), b"piece length": piece_length}
    if pieces:
        info[b"pieces"] = b"".join(pieces)
    if len(files) == 1 and "/" not in files[0][0]:
        info[b"length"] = files[0][1]
    else:
        fl = []
        for path, length in files:
            segs = [s.encode() for s in path.split("/") if s and s != name]
            fl.append({b"length": length, b"path": segs or [path.encode()]})
        info[b"files"] = fl
    root: dict = {b"info": info}
    if announce:
        root[b"announce"] = announce.encode()
    return bencode.encode(root)
