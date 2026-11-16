"""bencode 编解码（BEP3）。纯函数，单测覆盖。"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple, Union

Bencodable = Union[int, bytes, List[Any], Dict[bytes, Any]]


class BencodeError(ValueError):
    pass


def decode(data: bytes) -> Bencodable:
    """解码一个 bencode 值（不允许尾部垃圾）。"""
    value, pos = _decode(data, 0, 0)
    if pos != len(data):
        raise BencodeError(f"尾部存在多余数据（偏移 {pos}）")
    return value


def decode_prefix(data: bytes) -> Tuple[Bencodable, int]:
    """解码并返回 (value, 消费字节数)，用于从网络流里取值。"""
    return _decode(data, 0, 0)


def _decode(data: bytes, pos: int, depth: int) -> Tuple[Bencodable, int]:
    if depth > 64:
        raise BencodeError("嵌套过深")
    if pos >= len(data):
        raise BencodeError("数据意外结束")
    c = data[pos]
    if c == ord("i"):
        end = data.find(b"e", pos + 1)
        if end < 0:
            raise BencodeError("整数缺少结束符")
        body = data[pos + 1 : end]
        if body in (b"", b"-") or (body.startswith(b"-0")) or (body.startswith(b"0") and len(body) > 1):
            raise BencodeError(f"整数非法: {body!r}")
        try:
            return int(body), end + 1
        except ValueError as e:
            raise BencodeError(f"整数非法: {body!r}") from e
    if c == ord("l"):
        pos += 1
        out: List[Any] = []
        while not (pos < len(data) and data[pos] == ord("e")):
            item, pos = _decode(data, pos, depth + 1)
            out.append(item)
        return out, pos + 1
    if c == ord("d"):
        pos += 1
        out_d: Dict[bytes, Any] = {}
        while not (pos < len(data) and data[pos] == ord("e")):
            key, pos = _decode(data, pos, depth + 1)
            if not isinstance(key, bytes):
                raise BencodeError("字典键必须是字节串")
            val, pos = _decode(data, pos, depth + 1)
            out_d[key] = val
        return out_d, pos + 1
    if ord("0") <= c <= ord("9"):
        colon = data.find(b":", pos)
        if colon < 0:
            raise BencodeError("字节串缺少冒号")
        try:
            length = int(data[pos:colon])
        except ValueError as e:
            raise BencodeError("字节串长度非法") from e
        if length < 0 or (colon > pos and data[pos] == ord("0") and colon - pos > 1):
            raise BencodeError("字节串长度非法")
        start = colon + 1
        end = start + length
        if end > len(data):
            raise BencodeError("字节串越界")
        return data[start:end], end
    raise BencodeError(f"非法起始字节 0x{c:02x}")


def encode(value: Bencodable) -> bytes:
    """编码为 bencode 字节（dict 键自动排序）。"""
    if isinstance(value, bool):  # bool 是 int 子类，先挡掉
        raise BencodeError("不支持 bool")
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, (bytes, bytearray)):
        return b"%d:%s" % (len(value), bytes(value))
    if isinstance(value, str):
        raw = value.encode("utf-8")
        return b"%d:%s" % (len(raw), raw)
    if isinstance(value, (list, tuple)):
        return b"l" + b"".join(encode(v) for v in value) + b"e"
    if isinstance(value, dict):
        items = []
        for k, v in value.items():
            kb = k if isinstance(k, bytes) else str(k).encode("utf-8")
            items.append((kb, v))
        items.sort(key=lambda kv: kv[0])
        return b"d" + b"".join(encode(k) + encode(v) for k, v in items) + b"e"
    raise BencodeError(f"无法编码类型 {type(value)}")
