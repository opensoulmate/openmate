"""任务模型、状态机与来源分类（纯函数，单测覆盖）。

DownloadTask 字段与前端 DownloadTask 契约对齐：
id/url/filename/dest/totalBytes/downloadedBytes/speed/eta/progress/status/type/addedAt
+ origin + remove_files（+ error 可选）。
"""

from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlsplit


class TaskStatus(str, Enum):
    """任务状态机。与前端 DownloadStatus 取值一致（多一个内部终态 removed）。"""

    PENDING = "pending"          # 排队/等待
    DOWNLOADING = "downloading"  # 下载中
    PAUSED = "paused"            # 已暂停
    DONE = "done"                # 已完成
    ERROR = "error"              # 出错
    REMOVED = "removed"          # 已移除（终态，不出现在列表）

    def can_transition(self, to: "TaskStatus") -> bool:
        """状态机合法迁移（自迁移恒合法；removed 为终态）。"""
        if self is to:
            return True
        return (self, to) in {
            (TaskStatus.PENDING, TaskStatus.DOWNLOADING),
            (TaskStatus.PENDING, TaskStatus.PAUSED),
            (TaskStatus.PENDING, TaskStatus.ERROR),
            (TaskStatus.PENDING, TaskStatus.REMOVED),
            (TaskStatus.DOWNLOADING, TaskStatus.PENDING),
            (TaskStatus.DOWNLOADING, TaskStatus.PAUSED),
            (TaskStatus.DOWNLOADING, TaskStatus.DONE),
            (TaskStatus.DOWNLOADING, TaskStatus.ERROR),
            (TaskStatus.DOWNLOADING, TaskStatus.REMOVED),
            (TaskStatus.PAUSED, TaskStatus.PENDING),
            (TaskStatus.PAUSED, TaskStatus.DOWNLOADING),
            (TaskStatus.PAUSED, TaskStatus.ERROR),
            (TaskStatus.PAUSED, TaskStatus.REMOVED),
            (TaskStatus.DONE, TaskStatus.REMOVED),
            (TaskStatus.ERROR, TaskStatus.PENDING),
            (TaskStatus.ERROR, TaskStatus.REMOVED),
        }


class TaskType(str, Enum):
    """任务来源类型（前端 DownloadType 取值）。"""

    HTTP = "http"
    BT = "bt"
    VIDEO = "video"
    THUNDER = "thunder"


# 视频站走 yt-dlp 插件/后端，本机引擎不直接下载
_VIDEO_HOSTS = re.compile(
    r"(^|\.)(youtube\.com|youtu\.be|bilibili\.com|b23\.tv|tiktok\.com|douyin\.com)$"
)


def classify_source(source: str) -> TaskType:
    """按来源（url / magnet / .torrent 路径 / thunder 链）分类。"""
    s = source.strip()
    low = s.lower()
    if low.startswith("magnet:?"):
        return TaskType.BT
    if low.startswith(("thunder://", "flashget://", "qqdl://")):
        return TaskType.THUNDER
    if low.endswith(".torrent") or ".torrent?" in low:
        return TaskType.BT
    try:
        host = (urlsplit(s).hostname or "").lower()
    except ValueError:
        host = ""
    if host and _VIDEO_HOSTS.search(host):
        return TaskType.VIDEO
    return TaskType.HTTP


def thunder_decode(source: str) -> str:
    """快雷链解码：thunder/flashget/qqdl → 真实 URL（失败抛 ValueError）。

    thunder  = base64("AA" + url + "ZZ")
    flashget = base64("[FLASHGET]" + url + "[FLASHGET]")（含历史变体）
    qqdl     = base64(url)
    """
    low = source.strip().lower()
    if not low.startswith(("thunder://", "flashget://", "qqdl://")):
        return source
    payload = source.split("://", 1)[1]
    try:
        raw = base64.b64decode(payload + "=" * (-len(payload) % 4))
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"快雷链 base64 解码失败: {e}") from e
    text = raw.decode("utf-8", "replace")
    m = re.search(r"(https?|ftp)://[^\x00\s]+", text)
    if not m:
        raise ValueError("快雷链解码后未找到真实 URL")
    url = m.group(0)
    # 去掉 thunder 的 AA/ZZ 包裹残留与 flashget 的 [FLASHGET] 残留
    url = re.sub(r"(ZZ|\[FLASHGET\])$", "", url)
    return url


def filename_from_url(url: str) -> str:
    """URL → 落盘文件名（去 query/fragment 与主机；空则 download）。"""
    stripped = url.split("?", 1)[0].split("#", 1)[0]
    try:
        path = urlsplit(stripped).path
    except ValueError:
        path = stripped
    name = unquote(path.rstrip("/").rsplit("/", 1)[-1]) if path.rstrip("/") else ""
    return name or "download"


def filename_from_content_disposition(header: Optional[str]) -> Optional[str]:
    """Content-Disposition → 文件名（支持 filename*=UTF-8''与普通 filename）。"""
    if not header:
        return None
    m = re.search(r"filename\*\s*=\s*([^']*)''([^;]+)", header, re.I)
    if m:
        return unquote(m.group(2).strip().strip('"')) or None
    m = re.search(r'filename\s*=\s*"([^"]+)"', header) or re.search(
        r"filename\s*=\s*([^;]+)", header
    )
    if m:
        name = m.group(1).strip().strip('"')
        return unquote(name) or None
    return None


@dataclass
class DownloadTask:
    """统一任务结构（origin 恒为 local，除非后端部署指定 remote）。"""

    id: str
    url: str
    filename: str = "download"
    dir: str = ""
    total_bytes: int = 0
    downloaded_bytes: int = 0
    speed: int = 0            # bytes/s
    eta: int = 0              # 秒
    progress: float = 0.0     # 0~100
    status: TaskStatus = TaskStatus.PENDING
    type: TaskType = TaskType.HTTP
    added_at: float = field(default_factory=time.time)
    origin: str = "local"
    remove_files: bool = False
    error: Optional[str] = None

    @property
    def dest(self) -> str:
        if not self.dir:
            return self.filename
        sep = "\\" if "\\" in self.dir and "/" not in self.dir else "/"
        return self.dir.rstrip("/\\") + sep + self.filename

    def refresh_progress(self) -> None:
        if self.total_bytes > 0:
            self.progress = round(min(100.0, self.downloaded_bytes * 100.0 / self.total_bytes), 2)
            remain = max(0, self.total_bytes - self.downloaded_bytes)
            self.eta = int(remain / self.speed) if self.speed > 0 else 0
        else:
            self.progress = 0.0
            self.eta = 0

    def transition(self, to: TaskStatus) -> None:
        if not self.status.can_transition(to):
            raise ValueError(f"状态迁移非法: {self.status.value} → {to.value}")
        self.status = to

    def to_dict(self) -> Dict[str, Any]:
        self.refresh_progress()
        return {
            "id": self.id,
            "url": self.url,
            "filename": self.filename,
            "dest": self.dest,
            "dir": self.dir,
            "totalBytes": self.total_bytes,
            "downloadedBytes": self.downloaded_bytes,
            "speed": self.speed,
            "eta": self.eta,
            "progress": self.progress,
            "status": self.status.value,
            "type": self.type.value,
            "addedAt": self.added_at,
            "origin": self.origin,
            "remove_files": self.remove_files,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "DownloadTask":
        return cls(
            id=str(d["id"]),
            url=d.get("url", ""),
            filename=d.get("filename", "download"),
            dir=d.get("dir", ""),
            total_bytes=int(d.get("total_bytes", d.get("totalBytes", 0)) or 0),
            downloaded_bytes=int(d.get("downloaded_bytes", d.get("downloadedBytes", 0)) or 0),
            speed=int(d.get("speed", 0) or 0),
            eta=int(d.get("eta", 0) or 0),
            progress=float(d.get("progress", 0) or 0),
            status=TaskStatus(d.get("status", "pending")),
            type=TaskType(d.get("type", "http")),
            added_at=float(d.get("added_at", d.get("addedAt", time.time())) or time.time()),
            origin=d.get("origin", "local"),
            remove_files=bool(d.get("remove_files", False)),
            error=d.get("error"),
        )

    def state_dict(self) -> Dict[str, Any]:
        """持久化记录（引擎内部 snake_case，含分段/位图等由引擎补充）。"""
        d = self.to_dict()
        d["total_bytes"] = self.total_bytes
        d["downloaded_bytes"] = self.downloaded_bytes
        d["added_at"] = self.added_at
        return d
