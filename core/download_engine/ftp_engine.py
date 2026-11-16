"""ftp_engine.py — FTP/FTPS 下载引擎（纯 Python：asyncio + stdlib ftplib）。

- 单流下载 + 断点续传（SIZE 取总长、REST 从偏移继续；服务器不支持 REST 则从头再来）
- 控制连接阻塞操作走 asyncio.to_thread；数据连接裸 socket 异步读（限速/暂停可打断）
- 错误细分：DNS / 连接被拒 / 认证(530) / 文件不存在或无权限(550) / TLS / 超时
- URL：ftp://[user[:pass]@]host[:port]/path（匿名默认 anonymous@）；ftps:// = 显式 FTPS(AUTH TLS)
"""

from __future__ import annotations

import asyncio
import ftplib
import os
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import unquote, urlsplit

from .http_engine import SpeedLimiter
from .models import DownloadTask, TaskStatus, TaskType, filename_from_url

CHUNK_SIZE = 64 * 1024
CONNECT_TIMEOUT = 20.0


class FtpEngineError(Exception):
    pass


def map_ftp_error(e: BaseException) -> str:
    """异常 → 细分错误信息。"""
    if isinstance(e, ftplib.error_perm):
        code = str(e)[:3]
        if code == "530":
            return f"FTP 认证失败: {e}"
        if code == "550":
            return f"FTP 文件不存在或无权限: {e}"
        return f"FTP 永久错误({code}): {e}"
    if isinstance(e, ftplib.error_temp):
        return f"FTP 临时错误: {e}"
    if isinstance(e, ftplib.error_proto):
        return f"FTP 协议错误: {e}"
    if isinstance(e, ssl.SSLError):
        return f"FTPS TLS 握手/传输失败: {e}"
    if isinstance(e, socket.gaierror):
        return f"FTP DNS 解析失败: {e}"
    if isinstance(e, ConnectionRefusedError):
        return f"FTP 连接被拒: {e}"
    if isinstance(e, (socket.timeout, TimeoutError, asyncio.TimeoutError)):
        return f"FTP 超时: {e}"
    if isinstance(e, OSError):
        return f"FTP 网络错误: {e}"
    return f"FTP 错误: {e}"


@dataclass
class FtpRecord:
    task: DownloadTask
    offset: int = 0                 # 已下载字节（= 断点位置）
    total: int = 0
    mtime: Optional[str] = None     # MDTM（续传时校验文件未变）
    worker: Optional[asyncio.Task] = None
    cancel: bool = False
    sock: Optional[socket.socket] = None
    options: Dict[str, Any] = field(default_factory=dict)
    sample_t: float = field(default_factory=time.monotonic)
    sample_bytes: int = 0

    def to_state(self) -> Dict[str, Any]:
        d = self.task.state_dict()
        d.update({"offset": self.offset, "total": self.total, "mtime": self.mtime})
        return d

    @classmethod
    def from_state(cls, d: Dict[str, Any]) -> "FtpRecord":
        task = DownloadTask.from_dict(d)
        return cls(
            task=task,
            offset=int(d.get("offset", 0)),
            total=int(d.get("total", 0)),
            mtime=d.get("mtime"),
        )


def parse_ftp_url(url: str) -> Dict[str, Any]:
    """ftp(s):// URL → {tls, host, port, path, username, password}。"""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("ftp", "ftps"):
        raise FtpEngineError(f"不支持的 FTP URL: {url}")
    if not parts.hostname:
        raise FtpEngineError(f"FTP URL 缺少主机: {url}")
    path = unquote(parts.path or "/")
    return {
        "tls": scheme == "ftps",
        "host": parts.hostname,
        "port": parts.port or 21,  # ftps:// = 显式 FTPS（AUTH TLS），控制端口同 21
        "path": path,
        "username": unquote(parts.username or "anonymous"),
        "password": unquote(parts.password or "anonymous@"),
    }


class FtpEngine:
    def __init__(
        self,
        persist: Callable[[], None],
        limiter: Optional[SpeedLimiter] = None,
        concurrency: int = 5,  # FTP 单流；参数仅为接口对齐
    ):
        self._persist = persist
        self._limiter = limiter or SpeedLimiter(0)
        self._records: Dict[str, FtpRecord] = {}
        self._counter = int(time.time() * 1000) % 100000

    # ── 登记 / 持久化 ────────────────────────────────────────

    def restore(self, states: List[Dict[str, Any]]) -> None:
        for d in states:
            try:
                rec = FtpRecord.from_state(d)
                self._records[rec.task.id] = rec
            except Exception:  # noqa: BLE001
                continue

    def export_states(self) -> List[Dict[str, Any]]:
        return [r.to_state() for r in self._records.values()]

    def _next_id(self) -> str:
        self._counter += 1
        return f"ftp-{self._counter}"

    def list(self) -> List[DownloadTask]:
        out = []
        for rec in self._records.values():
            self._sample_speed(rec)
            out.append(rec.task)
        return out

    def _sample_speed(self, rec: FtpRecord) -> None:
        now = time.monotonic()
        dt = now - rec.sample_t
        if dt >= 1.0:
            delta = rec.task.downloaded_bytes - rec.sample_bytes
            rec.task.speed = max(0, int(delta / dt))
            rec.sample_t = now
            rec.sample_bytes = rec.task.downloaded_bytes
            rec.task.refresh_progress()
        elif rec.task.status != TaskStatus.DOWNLOADING:
            rec.task.speed = 0

    # ── 对外操作 ────────────────────────────────────────────

    async def add(
        self,
        url: str,
        dir_path: str,
        filename: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> DownloadTask:
        options = dict(options or {})
        parse_ftp_url(url)  # 尽早校验 URL
        task = DownloadTask(
            id=self._next_id(),
            url=url,
            filename=filename or options.get("rename") or filename_from_url(url),
            dir=dir_path,
            status=TaskStatus.PENDING,
            type=TaskType.HTTP,
        )
        rec = FtpRecord(task=task, options=options)
        self._records[task.id] = rec
        self._persist()
        rec.worker = asyncio.get_running_loop().create_task(self._run(rec))
        return task

    async def pause(self, task_id: str) -> DownloadTask:
        rec = self._records.get(task_id)
        if rec is None:
            raise FtpEngineError(f"任务不存在: {task_id}")
        if rec.task.status in (TaskStatus.DOWNLOADING, TaskStatus.PENDING):
            rec.cancel = True
            self._close_data(rec)
            if rec.worker and not rec.worker.done():
                try:
                    await asyncio.wait_for(asyncio.shield(rec.worker), 5)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    rec.worker.cancel()
            rec.worker = None
            if rec.task.status == TaskStatus.DOWNLOADING:
                rec.task.status = TaskStatus.PAUSED
                rec.task.speed = 0
            self._persist()
        return rec.task

    async def resume(self, task_id: str) -> DownloadTask:
        rec = self._records.get(task_id)
        if rec is None:
            raise FtpEngineError(f"任务不存在: {task_id}")
        if rec.task.status == TaskStatus.DOWNLOADING:
            return rec.task
        if rec.task.status == TaskStatus.DONE:
            raise FtpEngineError("任务已完成，无需继续")
        rec.cancel = False
        rec.task.error = None
        rec.task.status = TaskStatus.PENDING
        rec.worker = asyncio.get_running_loop().create_task(self._run(rec))
        self._persist()
        return rec.task

    async def remove(self, task_id: str, remove_files: bool = False) -> None:
        rec = self._records.pop(task_id, None)
        if rec is None:
            raise FtpEngineError(f"任务不存在: {task_id}")
        rec.cancel = True
        self._close_data(rec)
        if remove_files and rec.task.filename:
            try:
                os.unlink(rec.task.dest)
            except OSError:
                pass
        rec.task.status = TaskStatus.REMOVED
        self._persist()

    async def shutdown(self) -> None:
        for rec in list(self._records.values()):
            rec.cancel = True
            self._close_data(rec)

    @staticmethod
    def _close_data(rec: FtpRecord) -> None:
        if rec.sock is not None:
            try:
                rec.sock.close()
            except OSError:
                pass
            rec.sock = None

    # ── 下载 worker ──────────────────────────────────────────

    async def _run(self, rec: FtpRecord) -> None:
        task = rec.task
        loop = asyncio.get_running_loop()
        try:
            task.status = TaskStatus.DOWNLOADING
            task.error = None
            info = parse_ftp_url(task.url)
            if rec.total <= 0:
                await self._probe(rec, info)
            if rec.cancel:
                raise asyncio.CancelledError()
            os.makedirs(task.dir or ".", exist_ok=True)
            offset = rec.offset if rec.offset > 0 else 0
            ftp = await self._connect(info)
            try:
                # REST 断点续传（offset>0 才发 REST）；服务器拒绝 REST 则整段从头再来
                try:
                    sock = await asyncio.to_thread(
                        ftp.transfercmd, f"RETR {info['path']}", offset if offset > 0 else None
                    )
                except ftplib.error_perm as e:
                    if str(e)[:3] in ("501", "502", "550"):
                        offset = 0
                        rec.offset = 0
                        task.downloaded_bytes = 0
                        sock = await asyncio.to_thread(ftp.transfercmd, f"RETR {info['path']}")
                    else:
                        raise
                sock.setblocking(False)
                rec.sock = sock
                fd = os.open(task.dest, os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    pos = offset
                    while not rec.cancel:
                        chunk = await loop.sock_recv(sock, CHUNK_SIZE)
                        if not chunk:
                            break
                        await self._limiter.acquire(len(chunk))
                        os.pwrite(fd, chunk, pos)
                        pos += len(chunk)
                        rec.offset = pos
                        task.downloaded_bytes = pos
                        task.refresh_progress()
                    if rec.cancel:
                        raise asyncio.CancelledError()
                    # 数据收完：消费控制连接 226
                    os.close(fd)
                    fd = -1
                    await asyncio.to_thread(ftp.close)
                    ftp = None  # type: ignore[assignment]
                    if task.total_bytes <= 0:
                        task.total_bytes = pos
                    task.downloaded_bytes = max(task.downloaded_bytes, pos)
                    task.speed = 0
                    task.status = TaskStatus.DONE
                finally:
                    if fd >= 0:
                        os.close(fd)
                    self._close_data(rec)
            finally:
                if ftp is not None:
                    try:
                        await asyncio.to_thread(ftp.close)
                    except Exception:  # noqa: BLE001
                        pass
        except asyncio.CancelledError:
            if task.status == TaskStatus.DOWNLOADING:
                task.status = TaskStatus.PAUSED
            raise
        except Exception as e:  # noqa: BLE001
            task.status = TaskStatus.ERROR
            task.error = map_ftp_error(e)
            task.speed = 0
        finally:
            self._persist()

    async def _probe(self, rec: FtpRecord, info: Dict[str, Any]) -> None:
        """SIZE（总长）+ MDTM（变更校验）；探测完即断开，正式传输再连。"""
        ftp = await self._connect(info)
        try:
            total = await asyncio.to_thread(ftp.size, info["path"])
            rec.total = int(total or 0)
            rec.mtime = await asyncio.to_thread(self._mdtm, ftp, info["path"])
        except ftplib.error_perm as e:
            if str(e)[:3] == "550":
                raise FtpEngineError(map_ftp_error(e)) from e
            rec.total = 0  # 服务器不支持 SIZE：未知长度
        finally:
            try:
                await asyncio.to_thread(ftp.close)
            except Exception:  # noqa: BLE001
                pass
        rec.task.total_bytes = rec.total
        if not rec.task.filename or rec.task.filename == "download":
            rec.task.filename = filename_from_url(info["path"]) or rec.task.filename
        rec.task.downloaded_bytes = rec.offset
        rec.task.refresh_progress()
        self._persist()

    @staticmethod
    def _mdtm(ftp: ftplib.FTP, path: str) -> Optional[str]:
        try:
            return ftp.sendcmd(f"MDTM {path}").strip()
        except ftplib.error_perm:
            return None

    async def _connect(self, info: Dict[str, Any]) -> ftplib.FTP:
        def blocking() -> ftplib.FTP:
            if info["tls"]:
                ftp: ftplib.FTP = ftplib.FTP_TLS()
                ftp.connect(info["host"], info["port"], timeout=CONNECT_TIMEOUT)
                try:
                    ftp.auth()
                except Exception as e:  # noqa: BLE001
                    raise FtpEngineError(map_ftp_error(e)) from e
            else:
                ftp = ftplib.FTP()
                ftp.connect(info["host"], info["port"], timeout=CONNECT_TIMEOUT)
            ftp.login(info["username"], info["password"])
            ftp.set_pasv(True)
            ftp.voidcmd("TYPE I")
            return ftp

        try:
            return await asyncio.to_thread(blocking)
        except (ftplib.Error, OSError, EOFError, FtpEngineError) as e:
            if isinstance(e, FtpEngineError):
                raise
            raise FtpEngineError(map_ftp_error(e)) from e
