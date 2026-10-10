"""SoulMate 媒体通道（路线图4.3）：图片/文件收发 + 截图理解。

缺口（路线图原文）："4.3 媒体能力：图片/文件收发、截图理解"。此前消息信封只有
纯文本（ChannelMessage.text / WeChatMessage.content），sense_* 只有感知无收发：
收到的图片/文件没有入站形态，要发文件也没有出站形态。

本切片交付：
1. **媒体附件中立形状** MediaAttachment（kind=image|file / path / name / mime_type），
   `to_meta()` 对齐 soulmate_agent 既有附件持久化格式（agent_messages.attachments
   列 JSON：{type,name,mime_type,path}）。
2. **入站媒体落盘** save_inbound()：transport 侧拿到的 base64（含 data URL 前缀）
   解码存本地，返回带真实路径的附件——回合 prompt 用 media_prompt_block() 声明
   本地路径，截图理解走 vision_analyze/read_image（确定性提示，不靠 LLM 猜）。
3. **出站媒体**：from_paths() 校验本地文件存在+推断 kind，供 message_router
   send_outbound 携带（wechat→transport.send_media / openmate→MEDIA 标签 +
   attachments 列持久化）。

诚实边界：媒体"收发"覆盖消息路由层；微信通道仍 loopback 传输（4.1b iLink 待
用户拍板），iLink AES 媒体 CDN 未接。
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import time
from dataclasses import dataclass

KIND_IMAGE = "image"
KIND_FILE = "file"
KINDS = (KIND_IMAGE, KIND_FILE)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico"}

_DEFAULT_MEDIA_DIR = os.path.expanduser("~/.hermes/soulmate/media")


def _media_root() -> str:
    return os.environ.get("SOULMATE_MEDIA_DIR") or _DEFAULT_MEDIA_DIR


def guess_mime(name: str) -> str:
    """按文件名推断 mime（未知→application/octet-stream）。"""
    return mimetypes.guess_type(name or "")[0] or "application/octet-stream"


def kind_for(name: str = "", mime_type: str = "") -> str:
    """推断媒体类型：image/* mime 或图片扩展名→image，其余→file。"""
    mt = (mime_type or "").strip().lower()
    if mt.startswith("image/"):
        return KIND_IMAGE
    ext = os.path.splitext(name or "")[1].lower()
    if ext in _IMAGE_EXTS:
        return KIND_IMAGE
    return KIND_FILE


def _safe_name(name: str) -> str:
    base = os.path.basename(str(name or "").strip()) or "media.bin"
    return re.sub(r"[^\w\-. ]", "_", base)[:120]


@dataclass
class MediaAttachment:
    """一个媒体附件（通道无关的中立形状）。"""

    kind: str
    path: str
    name: str = ""
    mime_type: str = ""

    def __post_init__(self) -> None:
        self.path = str(self.path or "")
        if not self.name:
            self.name = os.path.basename(self.path) or self.path
        if not self.mime_type:
            self.mime_type = guess_mime(self.name)
        if self.kind not in KINDS:
            self.kind = kind_for(self.name, self.mime_type)

    def to_meta(self) -> dict:
        """对齐 soulmate_agent 附件持久化格式（agent_messages.attachments 列）。"""
        return {
            "type": self.kind,
            "name": self.name,
            "mime_type": self.mime_type,
            "path": self.path,
        }


def from_paths(paths, must_exist: bool = True) -> list[MediaAttachment]:
    """本地文件路径列表 → 附件列表（保序去重）。

    - 空串/空白路径 → ValueError（不静默吞）
    - must_exist=True 时文件不存在/不是普通文件 → ValueError
    """
    out: list[MediaAttachment] = []
    seen: set[str] = set()
    for raw in list(paths or []):
        p = str(raw or "").strip()
        if not p:
            raise ValueError("media 路径不能为空")
        p = os.path.expanduser(p)
        if must_exist and not os.path.isfile(p):
            raise ValueError(f"媒体文件不存在: {p}")
        real = os.path.realpath(p)
        if real in seen:
            continue
        seen.add(real)
        out.append(MediaAttachment(kind=kind_for(p), path=p))
    return out


def save_inbound(
    name: str,
    data_b64: str,
    mime_type: str = "",
    dest_dir: str | None = None,
) -> MediaAttachment:
    """入站媒体落盘：base64（可带 data URL 前缀）→ 本地文件，返回附件。

    落盘目录：dest_dir 或 $SOULMATE_MEDIA_DIR/inbound/（默认
    ~/.hermes/soulmate/media/inbound/），文件名带毫秒时间戳防撞。
    """
    b64 = str(data_b64 or "").strip()
    if not b64:
        raise ValueError("媒体数据不能为空")
    if b64.startswith("data:") and "," in b64:
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=False)
    except Exception as e:
        raise ValueError(f"媒体 base64 解码失败: {e}") from e

    safe = _safe_name(name)
    mt = (mime_type or "").strip() or guess_mime(safe)
    target_dir = dest_dir or os.path.join(_media_root(), "inbound")
    os.makedirs(target_dir, exist_ok=True)
    path = os.path.join(target_dir, f"{int(time.time() * 1000)}_{safe}")
    with open(path, "wb") as fp:
        fp.write(raw)
    return MediaAttachment(kind=kind_for(safe, mt), path=path, name=safe, mime_type=mt)


def media_prompt_block(atts) -> str:
    """媒体提示块：声明本地路径 + 读取方式（截图理解走 vision 工具，确定性提示）。

    空附件列表返回空串（纯文本消息零改动）。
    """
    atts = list(atts or [])
    if not atts:
        return ""
    lines = ["[媒体附件] 对方随消息发来以下内容，请处理后回复："]
    for i, a in enumerate(atts, 1):
        if a.kind == KIND_IMAGE:
            lines.append(
                f"{i}. 图片/截图「{a.name}」（{a.mime_type}）→ 本地路径: {a.path}\n"
                "   （截图理解：请用 vision_analyze/read_image 工具读取并理解图片内容，"
                "不要凭空臆测）"
            )
        else:
            lines.append(
                f"{i}. 文件「{a.name}」（{a.mime_type}）→ 本地路径: {a.path}\n"
                "   （请用 read_file 等工具读取文件内容）"
            )
    return "\n".join(lines)
