"""http_engine.py — HTTP(S) 分段下载引擎（asyncio + httpx）。

能力：Range 多线段并发、断点续传（分段进度持久化 + If-Range/ETag 校验）、
重定向、Cookie/UA/Referer、全局令牌桶限速、Content-Disposition/URL 文件名解析。
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import httpx

from .util import O_BINARY, pwrite
from .models import (
    DownloadTask,
    TaskStatus,
    TaskType,
    filename_from_content_disposition,
    filename_from_url,
)

CHUNK_SIZE = 64 * 1024
# 超过该大小才分段；单段最小 1 MiB
SEGMENT_THRESHOLD = 4 * 1024 * 1024
MIN_SEGMENT_SIZE = 1024 * 1024
SEGMENT_RETRIES = 3
USER_AGENT = "Mozilla/5.0 (OpenMate DownloadEngine/0.1)"


class HttpEngineError(Exception):
    pass


class SpeedLimiter:
    """全局限速令牌桶（bytes/s，0=不限）。"""

    def __init__(self, bps: int = 0):
        self._bps = max(0, int(bps))
        self._tokens = float(self._bps)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    def set_limit(self, bps: int) -> None:
        self._bps = max(0, int(bps))
        self._tokens = float(self._bps)

    @property
    def limit(self) -> int:
        return self._bps

    async def acquire(self, n: int) -> None:
        if self._bps <= 0 or n <= 0:
            return
        # 单块大于限速值时切片获取，避免永久等不到满额令牌
        while n > 0:
            take = min(n, self._bps)
            await self._acquire_one(take)
            n -= take

    async def _acquire_one(self, n: int) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(float(self._bps), self._tokens + (now - self._last) * self._bps)
                self._last = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                need = (n - self._tokens) / self._bps
                await asyncio.sleep(min(1.0, max(0.001, need)))


@dataclass
class Seg:
    start: int          # 绝对起始字节
    end: int            # 绝对结束字节（不含）；0 = 未知长度（单段到 EOF）
    done: int = 0       # 本段已写字节数

    def to_dict(self) -> Dict[str, int]:
        return {"start": self.start, "end": self.end, "done": self.done}


@dataclass
class HttpRecord:
    task: DownloadTask
    segs: List[Seg] = field(default_factory=list)
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    accept_ranges: bool = False
    # 实际下载 URL（pan_share/thunder 任务：task.url 保留用户原始输入，
    # 下载走解析出的直链；直链有时效，resume 前由 engine 重新解析覆盖）
    download_url: Optional[str] = None
    worker: Optional[asyncio.Task] = None
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    # 速度采样窗口
    sample_t: float = field(default_factory=time.monotonic)
    sample_bytes: int = 0

    def url(self) -> str:
        return self.download_url or self.task.url

    def to_state(self) -> Dict[str, Any]:
        d = self.task.state_dict()
        d.update(
            {
                "segs": [s.to_dict() for s in self.segs],
                "etag": self.etag,
                "last_modified": self.last_modified,
                "accept_ranges": self.accept_ranges,
                "download_url": self.download_url,
            }
        )
        return d

    @classmethod
    def from_state(cls, d: Dict[str, Any]) -> "HttpRecord":
        task = DownloadTask.from_dict(d)
        segs = [Seg(int(s["start"]), int(s["end"]), int(s.get("done", 0))) for s in d.get("segs", [])]
        return cls(
            task=task,
            segs=segs,
            etag=d.get("etag"),
            last_modified=d.get("last_modified"),
            accept_ranges=bool(d.get("accept_ranges", False)),
            download_url=d.get("download_url"),
        )


def plan_segments(total: int, concurrency: int, accept_ranges: bool) -> List[Seg]:
    """分段规划（纯函数，单测覆盖）：不支持 Range 或小文件 → 单段。"""
    if not accept_ranges or total <= 0 or total < SEGMENT_THRESHOLD:
        return [Seg(0, total)]
    n = min(max(1, concurrency), max(1, total // MIN_SEGMENT_SIZE))
    seg_len = total // n
    segs: List[Seg] = []
    for i in range(n):
        start = i * seg_len
        end = total if i == n - 1 else (i + 1) * seg_len
        segs.append(Seg(start, end))
    return segs


class HttpEngine:
    def __init__(
        self,
        persist: Callable[[], None],
        limiter: Optional[SpeedLimiter] = None,
        concurrency: int = 5,
    ):
        self._persist = persist
        self._limiter = limiter or SpeedLimiter(0)
        self._concurrency = max(1, int(concurrency))
        self._records: Dict[str, HttpRecord] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._counter = int(time.time() * 1000) % 100000

    # ── 生命周期 ──────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, read=60.0),
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    async def shutdown(self) -> None:
        for rec in list(self._records.values()):
            rec.cancel.set()
            if rec.worker and not rec.worker.done():
                rec.worker.cancel()
        for rec in list(self._records.values()):
            if rec.worker:
                try:
                    await rec.worker
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ── 记录登记 / 恢复 ───────────────────────────────────────

    def restore(self, states: List[Dict[str, Any]]) -> None:
        for d in states:
            try:
                rec = HttpRecord.from_state(d)
            except Exception:  # noqa: BLE001
                continue
            self._records[rec.task.id] = rec

    def export_states(self) -> List[Dict[str, Any]]:
        return [r.to_state() for r in self._records.values()]

    def _next_id(self) -> str:
        self._counter += 1
        return f"http-{self._counter}"

    def get(self, task_id: str) -> Optional[HttpRecord]:
        return self._records.get(task_id)

    # ── 对外操作 ──────────────────────────────────────────────

    async def add(
        self,
        url: str,
        dir_path: str,
        filename: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> DownloadTask:
        options = options or {}
        task = DownloadTask(
            id=self._next_id(),
            url=url,
            filename=filename or options.get("rename") or filename_from_url(url),
            dir=dir_path,
            status=TaskStatus.PENDING,
            type=TaskType.HTTP,
        )
        rec = HttpRecord(task=task)
        rec.options = options  # type: ignore[attr-defined]
        self._records[task.id] = rec
        self._persist()
        rec.worker = asyncio.get_running_loop().create_task(self._run(rec))
        return task

    async def pause(self, task_id: str) -> DownloadTask:
        rec = self._records.get(task_id)
        if rec is None:
            raise HttpEngineError(f"任务不存在: {task_id}")
        if rec.task.status in (TaskStatus.DOWNLOADING, TaskStatus.PENDING):
            rec.cancel.set()
            if rec.worker and not rec.worker.done():
                rec.worker.cancel()
                try:
                    await rec.worker
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            rec.worker = None
            if rec.task.status.can_transition(TaskStatus.PAUSED):
                rec.task.status = TaskStatus.PAUSED
                rec.task.speed = 0
            self._persist()
        return rec.task

    async def resume(self, task_id: str) -> DownloadTask:
        rec = self._records.get(task_id)
        if rec is None:
            raise HttpEngineError(f"任务不存在: {task_id}")
        if rec.task.status == TaskStatus.DOWNLOADING:
            return rec.task
        if rec.task.status == TaskStatus.DONE:
            raise HttpEngineError("任务已完成，无需继续")
        rec.cancel = asyncio.Event()
        rec.task.error = None
        rec.task.status = TaskStatus.PENDING
        rec.worker = asyncio.get_running_loop().create_task(self._run(rec))
        self._persist()
        return rec.task

    async def remove(self, task_id: str, remove_files: bool = False) -> None:
        rec = self._records.pop(task_id, None)
        if rec is None:
            raise HttpEngineError(f"任务不存在: {task_id}")
        rec.cancel.set()
        if rec.worker and not rec.worker.done():
            rec.worker.cancel()
            try:
                await rec.worker
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if remove_files and rec.task.filename:
            try:
                os.unlink(rec.task.dest)
            except OSError:
                pass
        rec.task.status = TaskStatus.REMOVED
        self._persist()

    def list(self) -> List[DownloadTask]:
        out = []
        for rec in self._records.values():
            self._sample_speed(rec)
            out.append(rec.task)
        return out

    def _sample_speed(self, rec: HttpRecord) -> None:
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

    # ── 下载 worker ───────────────────────────────────────────

    async def _run(self, rec: HttpRecord) -> None:
        task = rec.task
        try:
            task.status = TaskStatus.DOWNLOADING
            task.error = None
            options = getattr(rec, "options", {}) or {}
            await self._probe(rec, options)
            if rec.cancel.is_set():
                raise asyncio.CancelledError()
            os.makedirs(task.dir or ".", exist_ok=True)
            fd = os.open(task.dest, os.O_RDWR | os.O_CREAT | O_BINARY, 0o644)
            try:
                if task.total_bytes > 0:
                    os.ftruncate(fd, max(task.total_bytes, os.fstat(fd).st_size))
                await self._run_segments(rec, fd, options)
            finally:
                os.close(fd)
            if rec.cancel.is_set():
                raise asyncio.CancelledError()
            # 完成
            if task.total_bytes <= 0:
                task.total_bytes = task.downloaded_bytes
            task.downloaded_bytes = task.total_bytes
            task.speed = 0
            task.status = TaskStatus.DONE
        except asyncio.CancelledError:
            # pause/remove 触发；状态由调用方决定（pause 会置 PAUSED）
            if task.status == TaskStatus.DOWNLOADING and not rec.cancel.is_set():
                task.status = TaskStatus.PAUSED
            raise
        except Exception as e:  # noqa: BLE001
            task.status = TaskStatus.ERROR
            task.error = str(e)
            task.speed = 0
        finally:
            self._persist()

    async def _probe(self, rec: HttpRecord, options: Dict[str, Any]) -> None:
        """探测总大小/Range 支持/文件名。已有分段进度时跳过（断点续传）。"""
        task = rec.task
        if rec.segs and any(s.done > 0 for s in rec.segs):
            return
        headers = self._build_headers(options)
        client = self._get_client()
        req_headers = dict(headers)
        req_headers["Range"] = "bytes=0-0"
        resp = await client.get(rec.url(), headers=req_headers)
        try:
            if resp.status_code == 206:
                cr = resp.headers.get("Content-Range", "")
                total = 0
                if "/" in cr:
                    try:
                        total = int(cr.rsplit("/", 1)[1])
                    except ValueError:
                        total = 0
                task.total_bytes = total
                rec.accept_ranges = "bytes" in resp.headers.get("Accept-Ranges", "bytes")
            elif resp.status_code == 200:
                task.total_bytes = int(resp.headers.get("Content-Length") or 0)
                rec.accept_ranges = "bytes" in resp.headers.get("Accept-Ranges", "")
            else:
                raise HttpEngineError(f"探测失败 HTTP {resp.status_code}")
            rec.etag = resp.headers.get("ETag") or rec.etag
            rec.last_modified = resp.headers.get("Last-Modified") or rec.last_modified
            # 仅当文件名还是 URL 派生的默认值时才用 Content-Disposition 覆盖（尊重显式命名）
            if task.filename in ("", "download", filename_from_url(rec.url())):
                name = filename_from_content_disposition(resp.headers.get("Content-Disposition"))
                if name:
                    task.filename = name
        finally:
            await resp.aclose()
        # 分段规划（断点恢复时保留原分段）
        if not rec.segs:
            rec.segs = plan_segments(task.total_bytes, self._concurrency, rec.accept_ranges)
        task.downloaded_bytes = sum(s.done for s in rec.segs)
        task.refresh_progress()
        self._persist()

    async def _run_segments(self, rec: HttpRecord, fd: int, options: Dict[str, Any]) -> None:
        if len(rec.segs) <= 1:
            await self._run_segment(rec, rec.segs[0], fd, options)
            return
        tasks = [
            asyncio.get_running_loop().create_task(self._run_segment(rec, seg, fd, options))
            for seg in rec.segs
        ]
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            raise
        for r in results:
            if isinstance(r, Exception):
                raise r

    async def _run_segment(self, rec: HttpRecord, seg: Seg, fd: int, options: Dict[str, Any]) -> None:
        task = rec.task
        if seg.end > 0 and seg.done >= seg.end - seg.start:
            return  # 该段上次已完成（断点恢复），跳过
        headers = self._build_headers(options)
        client = self._get_client()
        last_err: Optional[Exception] = None
        for attempt in range(SEGMENT_RETRIES):
            if rec.cancel.is_set():
                return
            try:
                req_headers = dict(headers)
                want_start = seg.start + seg.done
                if seg.end > 0:
                    req_headers["Range"] = f"bytes={want_start}-{seg.end - 1}"
                elif seg.done > 0:
                    req_headers["Range"] = f"bytes={want_start}-"
                if rec.etag:
                    req_headers["If-Range"] = rec.etag
                async with client.stream("GET", rec.url(), headers=req_headers) as resp:
                    if resp.status_code == 200 and (seg.done > 0 or seg.start > 0):
                        # 服务器忽略 Range / 内容已变：本段从头再来
                        seg.done = 0
                        want_start = seg.start
                    if resp.status_code not in (200, 206):
                        body = (await resp.aread())[:200]
                        raise HttpEngineError(
                            f"分段下载失败 HTTP {resp.status_code}: {body!r}"
                        )
                    async for chunk in resp.aiter_bytes(CHUNK_SIZE):
                        if rec.cancel.is_set():
                            return
                        if seg.end > 0:
                            remain = seg.end - want_start
                            if remain <= 0:
                                return
                            chunk = chunk[:remain]
                        if not chunk:
                            continue
                        await self._limiter.acquire(len(chunk))
                        pwrite(fd, chunk, want_start)
                        want_start += len(chunk)
                        seg.done += len(chunk)
                        task.downloaded_bytes += len(chunk)
                        task.refresh_progress()
                # 成功走完本段
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                await asyncio.sleep(min(5.0, 0.5 * (2**attempt)))
        raise HttpEngineError(f"分段下载重试耗尽: {last_err}")

    @staticmethod
    def _build_headers(options: Dict[str, Any]) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if options.get("user_agent"):
            headers["User-Agent"] = str(options["user_agent"])
        if options.get("referer"):
            headers["Referer"] = str(options["referer"])
        if options.get("cookie"):
            headers["Cookie"] = str(options["cookie"])
        for k, v in (options.get("headers") or {}).items():
            headers[str(k)] = str(v)
        return headers
