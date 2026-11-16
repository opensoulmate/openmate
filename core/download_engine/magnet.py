"""magnet 链接解析（BEP9 磁力）。纯函数，单测覆盖。"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import parse_qs, unquote, urlsplit


class MagnetError(ValueError):
    pass


@dataclass
class Magnet:
    info_hash: bytes                 # 20 字节
    info_hash_hex: str               # 40 hex
    name: Optional[str] = None       # dn
    trackers: List[str] = field(default_factory=list)  # tr
    select_only: Optional[List[int]] = None  # x.pe 参数（BEP9 select-only，少用）

    @property
    def magnet_uri(self) -> str:
        return build_magnet(self.info_hash, self.name, self.trackers)


def _decode_btih(token: str) -> bytes:
    """btih 可能是 40-hex 或 32-base32。"""
    t = token.strip()
    if re.fullmatch(r"[0-9a-fA-F]{40}", t):
        return binascii.unhexlify(t)
    if re.fullmatch(r"[A-Za-z2-7]{32}", t):
        padded = t.upper() + "=" * (-len(t) % 8)
        raw = base64.b32decode(padded)
        if len(raw) != 20:
            raise MagnetError(f"base32 btih 解码长度错误: {len(raw)}")
        return raw
    raise MagnetError(f"无法识别的 btih: {t}")


def parse(uri: str) -> Magnet:
    """解析 magnet:?xt=urn:btih:...&dn=...&tr=..."""
    s = uri.strip()
    if not s.lower().startswith("magnet:?"):
        raise MagnetError("不是 magnet 链接")
    # parse_qs 需要裸 query
    query = s.split("?", 1)[1] if "?" in s else ""
    params = parse_qs(query, keep_blank_values=True)
    btih: Optional[bytes] = None
    for xt in params.get("xt", []):
        m = re.match(r"urn:btih:([0-9a-zA-Z]+)", xt, re.I)
        if m:
            btih = _decode_btih(m.group(1))
            break
    if btih is None:
        raise MagnetError("magnet 缺少 xt=urn:btih")
    name = None
    if params.get("dn"):
        name = unquote(params["dn"][0]).strip() or None
    trackers = [unquote(t).strip() for t in params.get("tr", []) if t.strip()]
    return Magnet(
        info_hash=btih,
        info_hash_hex=btih.hex(),
        name=name,
        trackers=trackers,
    )


def build_magnet(info_hash: bytes, name: Optional[str] = None, trackers: Optional[List[str]] = None) -> str:
    from urllib.parse import quote

    parts = [f"magnet:?xt=urn:btih:{info_hash.hex()}"]
    if name:
        parts.append(f"dn={quote(name)}")
    for tr in trackers or []:
        parts.append(f"tr={quote(tr, safe=':/')}")
    return "&".join(parts)


def is_bare_infohash(s: str) -> bool:
    """40 位裸 hex 也当磁力（librqbit 同款容错）。"""
    return bool(re.fullmatch(r"[0-9a-fA-F]{40}", s.strip()))
