"""engine.py — 引擎聚合层：HTTP + BT 双引擎路由、目录解析、事件日志、配置与持久化。

目录解析（铁律：引擎内不硬编码 ~/Downloads）：
    显式 dir（调用方每次传） > 配置项 dir > 启动参数 --dir（调用方默认值） > 报错
Electron 侧传 app.getPath('downloads')，后端传配置目录——引擎只用参数。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Dict, List, Optional

from .bt_engine import BtEngine
from .http_engine import HttpEngine, HttpEngineError, SpeedLimiter
from .models import DownloadTask, TaskStatus, TaskType, classify_source, thunder_decode
from .store import TaskStore


class EngineError(Exception):
    pass


class Engine:
    def __init__(
        self,
        data_dir: str,
        default_dir: Optional[str] = None,
        concurrency: int = 5,
        speed_limit: int = 0,
        origin: str = "local",
    ):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.default_dir = default_dir
        self.origin = origin
        self.store = TaskStore(os.path.join(self.data_dir, "engine-state.json"))
        self.config_path = os.path.join(self.data_dir, "engine-config.json")
        self.concurrency = max(1, int(concurrency))
        self.speed_limit = max(0, int(speed_limit))
        self._load_config()
        self.limiter = SpeedLimiter(self.speed_limit)
        self.http = HttpEngine(self._persist, self.limiter, self.concurrency)
        self.bt = BtEngine(self._persist, self.default_dir or self.data_dir)
        self._events: List[Dict[str, Any]] = []
        self._event_seq = 0
        self._snapshot: Dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._load_state()

    # ── 配置 ────────────────────────────────────────────────

    def _load_config(self) -> None:
        try:
            import json

            with open(self.config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self.concurrency = int(cfg.get("concurrency", self.concurrency))
            self.speed_limit = int(cfg.get("speed_limit", self.speed_limit))
            if cfg.get("dir"):
                self.configured_dir = str(cfg["dir"])
            else:
                self.configured_dir = None
        except (OSError, ValueError):
            self.configured_dir = None

    def _save_config(self) -> None:
        import json

        cfg = {
            "dir": self.configured_dir,
            "concurrency": self.concurrency,
            "speed_limit": self.speed_limit,
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

    def config_get(self) -> Dict[str, Any]:
        return {
            "dir": self.configured_dir or self.default_dir or "",
            "concurrency": self.concurrency,
            "speedLimit": self.speed_limit,
            "origin": self.origin,
            "dataDir": self.data_dir,
        }

    def config_set(
        self,
        dir: Optional[str] = None,
        concurrency: Optional[int] = None,
        speed_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        if dir is not None:
            self.configured_dir = dir.strip() or None
        if concurrency is not None:
            self.concurrency = max(1, min(64, int(concurrency)))
        if speed_limit is not None:
            self.speed_limit = max(0, int(speed_limit))
            self.limiter.set_limit(self.speed_limit)
        self._save_config()
        self._persist()
        return self.config_get()

    # ── 目录解析：显式 > 配置 > 启动默认（参数由调用方给） ──────

    def resolve_dir(self, explicit: Optional[str]) -> str:
        for cand in (explicit, self.configured_dir, self.default_dir):
            if cand and str(cand).strip():
                return os.path.abspath(os.path.expanduser(str(cand)))
        raise EngineError("未指定下载目录：请在请求里传 dir，或配置默认目录（config.dir / --dir）")

    # ── 持久化 ──────────────────────────────────────────────

    def _persist(self) -> None:
        with self._lock:
            try:
                self.store.save(self.http.export_states() + self.bt.export_states())
            except OSError:
                pass

    def _load_state(self) -> None:
        data = self.store.load()
        http_states = [d for d in data["tasks"] if str(d.get("id", "")).startswith("http-")]
        bt_states = [d for d in data["tasks"] if str(d.get("id", "")).startswith("bt-")]
        self.http.restore(http_states)
        self.bt.restore(bt_states)

    async def start(self) -> None:
        """启动：恢复上次未完任务（断点续传）。"""
        for t in self.http.list():
            if t.status in (TaskStatus.DOWNLOADING, TaskStatus.PENDING):
                try:
                    await self.http.resume(t.id)
                except Exception:  # noqa: BLE001
                    pass
        for t in self.bt.list():
            if t.status in (TaskStatus.DOWNLOADING, TaskStatus.PENDING):
                try:
                    await self.bt.resume(t.id)
                except Exception:  # noqa: BLE001
                    pass

    async def shutdown(self) -> None:
        self._persist()
        await self.http.shutdown()
        await self.bt.shutdown()

    # ── 任务操作 ────────────────────────────────────────────

    async def add(
        self,
        url: str,
        dir: Optional[str] = None,  # noqa: A002
        filename: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        options = options or {}
        source = (url or "").strip()
        if not source:
            raise EngineError("url 不能为空")
        kind = classify_source(source)
        if kind == TaskType.VIDEO:
            raise EngineError("视频站链接请走后端/插件（yt-dlp），本机引擎不支持")
        dir_path = self.resolve_dir(dir)
        os.makedirs(dir_path, exist_ok=True)
        if kind == TaskType.THUNDER:
            real = thunder_decode(source)
            task = await self.http.add(real, dir_path, filename, options)
            task.url = source  # 契约：url 保留用户原始输入
            task.type = TaskType.THUNDER
        elif kind == TaskType.BT:
            task = await self.bt.add(source, dir_path, filename, options)
            task.origin = self.origin
        else:
            low = source.lower()
            if not low.startswith(("http://", "https://")):
                raise EngineError(f"不支持的来源（FTP 暂缓）: {source}")
            task = await self.http.add(source, dir_path, filename, options)
        task.origin = self.origin
        self._sync_events()
        self._persist()
        return task.to_dict()

    def _find_task(self, task_id: str) -> DownloadTask:
        for t in self.http.list() + self.bt.list():
            if t.id == task_id:
                return t
        raise EngineError(f"任务不存在: {task_id}")

    async def pause(self, task_id: str) -> Dict[str, Any]:
        if task_id.startswith("bt-"):
            task = await self.bt.pause(task_id)
        else:
            task = await self.http.pause(task_id)
        self._sync_events()
        self._persist()
        return task.to_dict()

    async def resume(self, task_id: str) -> Dict[str, Any]:
        if task_id.startswith("bt-"):
            task = await self.bt.resume(task_id)
        else:
            task = await self.http.resume(task_id)
        self._sync_events()
        self._persist()
        return task.to_dict()

    async def remove(self, task_id: str, remove_files: bool = False) -> None:
        if task_id.startswith("bt-"):
            await self.bt.remove(task_id, remove_files)
        else:
            await self.http.remove(task_id, remove_files)
        self._sync_events()
        self._persist()

    def list(self) -> List[Dict[str, Any]]:
        self._sync_events()
        tasks = [t.to_dict() for t in self.http.list() + self.bt.list()]
        tasks.sort(key=lambda d: d.get("addedAt", 0), reverse=True)
        return tasks

    async def metadata(self, source: str, timeout: float = 90.0) -> List[Dict[str, Any]]:
        kind = classify_source(source.strip())
        if kind == TaskType.THUNDER:
            source = thunder_decode(source)
            kind = classify_source(source)
        if kind != TaskType.BT:
            raise EngineError("download_metadata 仅支持 magnet 与 .torrent")
        files = await self.bt.metadata(source, timeout)
        return [{"path": p, "length": ln} for p, ln in files]

    # ── 事件日志（轮询） ─────────────────────────────────────

    def _push_event(self, kind: str, task_id: str, task: Optional[Dict[str, Any]]) -> None:
        self._event_seq += 1
        self._events.append(
            {"seq": self._event_seq, "kind": kind, "taskId": task_id, "task": task}
        )
        if len(self._events) > 512:
            self._events = self._events[-512:]

    def _sync_events(self) -> None:
        """任务快照差异 → 事件（added/progress/status/removed）。"""
        try:
            tasks = self.http.list() + self.bt.list()
        except Exception:  # noqa: BLE001
            return
        now = {t.id: t for t in tasks}
        for tid in list(self._snapshot):
            if tid not in now:
                self._snapshot.pop(tid)
                self._push_event("removed", tid, None)
        for t in tasks:
            snap = self._snapshot.get(t.id)
            if snap is None:
                self._push_event("added", t.id, t.to_dict())
                self._snapshot[t.id] = (t.status, t.downloaded_bytes)
                continue
            prev_status, prev_bytes = snap
            if prev_status != t.status:
                kind = "error" if t.status == TaskStatus.ERROR else "status"
                self._push_event(kind, t.id, t.to_dict())
            if prev_bytes != t.downloaded_bytes:
                self._push_event("progress", t.id, t.to_dict())
            self._snapshot[t.id] = (t.status, t.downloaded_bytes)

    def events(self, after: int = 0) -> Dict[str, Any]:
        self._sync_events()
        evs = [e for e in self._events if e["seq"] > after]
        cursor = self._event_seq if self._event_seq else after
        return {"events": evs, "cursor": cursor}
