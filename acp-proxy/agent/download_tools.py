# -*- coding: utf-8 -*-
"""下载中心 MCP 工具组（SoulMate 触发面，工具形状=MCP 工具契约
docs/download-mcp-tools.json 四件套，REST x-endpoint 对齐后端
opensoul src/api/download_search.py + src/api/download_api.py）：

- torrent_search(query, category?, limit?)  种子搜索→候选列表（标题/大小/做种数/源）
- download_add(url, target=local|remote)    url|magnet|.torrent 直链入下载任务
- download_task_status(task_id)             单任务进度回报
- download_task_list(status?, origin?)      任务总览/进度回报

后端 REST（x-endpoint 同款；联调用 tests/mock_download_backend.py 本地 mock）：
- GET  {base}/search?query=&category=&limit=  → {"results": [{title,size,seeders,
       source,url|magnet}], "errors": [{source,error}], "total"}
       （⚠️ errors=逐源降级如实回报，单源挂不拖垮其它源，绝不造数据）
- POST {base}/download  body {url,target,filename?,dest?} → Task ack {id|task_id,...}
- GET  {base}/tasks[?status=&origin=]        → {"tasks":[...]}（facade 还会返回
       plugin_tasks/remote_tasks 重复行，按 task_id 去重后合并）
- GET  {base}/remote/{task_id}/metadata      → {"task":DownloadTask,...}；任务不在
       引擎面（native/plugin）时回落 GET {base}/tasks 按 id 过滤，插件链终态再
       回落 GET {base}/results/{task_id}（api doc §5 异步结果面，三源兜底）

DownloadTask 形状按 docs/download-engine-api.md §3：camelCase 主形 + 蛇形别名
（task_id/total_bytes/downloaded_bytes/progress_pct）双认；错误形状
{"error":{"code","message"}} 细分报因（对照 §2 错误码纪律）。

配置（环境变量）：
- SOULMATE_DOWNLOAD_API_BASE    后端基址（缺省 http://127.0.0.1:8090/api/download）
- SOULMATE_DOWNLOAD_API_TOKEN   可选 Bearer 鉴权
- SOULMATE_DOWNLOAD_HTTP_TIMEOUT 出站超时秒（缺省 20）
- SOULMATE_DOWNLOAD_SEARCH_PATH / SOULMATE_DOWNLOAD_ADD_PATH /
  SOULMATE_DOWNLOAD_TASKS_PATH  路径覆盖（REST 对齐时只改这里）

显式报因禁静默：query/url/task_id 空、后端不可达、HTTP 细分错误、非 JSON
响应都返回明确「错误: …」文案，绝不吞失败。

铁律对齐（2026-11-16 用户硬约束）：下载功能**全 Python 禁异语言**——搜索/
下载业务逻辑只在 OpenSoul Python FastAPI 侧（download_search.py/download_api.py）
单一真源；本模块是 acp-proxy 侧薄转发层（REST 调用 + 形状对齐 + 结果呈现），
不得把搜索/下载逻辑写进来，不得引入 Rust/Go/C/JS。
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_API_BASE = "http://127.0.0.1:8090/api/download"
DEFAULT_TIMEOUT_S = 20.0

# 计划书工具形状（锁定四件套）
TOOL_NAMES = ("torrent_search", "download_add",
              "download_task_status", "download_task_list")

_DEFAULT_SEARCH_PATH = "/search"
_DEFAULT_ADD_PATH = "/download"
_DEFAULT_TASKS_PATH = "/tasks"
_DEFAULT_REMOTE_PATH = "/remote"  # /remote/{task_id}/metadata（契约 status 主面）
_DEFAULT_RESULTS_PATH = "/results"  # /results/{task_id}（api doc §5 异步结果面）


def _pick(d: dict, keys: tuple) -> Any:
    """取第一个非 None 字段（camelCase/蛇形双认的唯一收口）。"""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def _fmt_size(n) -> str:
    try:
        n = float(n or 0)
    except Exception:
        return "未知"
    if n <= 0:
        return "未知"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}TB"


def _fmt_search_item(item: dict) -> str:
    """候选行：标题 | 大小 | 做种数 | 源 | 可入任务链（契约形状 url|magnet）。"""
    title = str(_pick(item, ("title", "name")) or "(无标题)")
    size = _pick(item, ("size", "item_size", "length", "contentLength")) or 0
    seeders = _pick(item, ("seeders", "seeds", "mirrors"))
    source = str(_pick(item, ("source", "from")) or "(未知源)")
    # 可入任务链（download_add 可直接消费）：magnet 优先，其次 http/.torrent URL
    url = str(_pick(item, ("magnet",))
              or _pick(item, ("url", "torrentUrl", "torrent_url", "torrent"))
              or "")
    seed_txt = "做种 ?（源不公开）" if seeders in (None, -1) else f"做种 {int(seeders)}"
    line = f"{title} | 大小 {_fmt_size(size)} | {seed_txt} | 源 {source}"
    if url:
        line += f" | 可入任务: {url}"
    return line


def _fmt_task(task: dict) -> str:
    """任务行：id | 状态 | 文件名 | 进度 | 速度 | 剩余 | 目标（DownloadTask 双认）。"""
    tid = str(_pick(task, ("task_id", "id")) or "?")
    status = str(_pick(task, ("status",)) or "?")
    name = str(_pick(task, ("filename", "dest", "url")) or "?")
    total = int(_pick(task, ("totalBytes", "total_bytes", "total")) or 0)
    downloaded = int(_pick(task, ("downloadedBytes", "downloaded_bytes")) or 0)
    speed = int(_pick(task, ("speed",)) or 0)
    eta = int(_pick(task, ("eta",)) or 0)
    origin = str(_pick(task, ("origin", "target")) or "")
    progress = _pick(task, ("progress", "progress_pct"))
    try:
        progress = float(progress)
    except Exception:
        progress = (downloaded / total * 100.0) if total > 0 else 0.0
    parts = [tid, status, name,
             f"进度 {progress:.1f}%（{_fmt_size(downloaded)}/{_fmt_size(total)}）"]
    if speed:
        parts.append(f"速度 {_fmt_size(speed)}/s")
    if eta and status not in ("done", "error"):
        parts.append(f"剩余 {eta}s")
    if origin:
        parts.append(f"目标 {origin}")
    err = task.get("error")
    if err:
        parts.append(f"错误: {err}")
    return " | ".join(parts)


class DownloadTools:
    """下载中心工具组（确定性 REST 转发 + 形状对齐，无 LLM 参与）。"""

    def __init__(self, api_base: str | None = None, timeout: float | None = None):
        self._base = (api_base
                      or os.environ.get("SOULMATE_DOWNLOAD_API_BASE")
                      or DEFAULT_API_BASE).rstrip("/")
        try:
            self._timeout = float(
                timeout or os.environ.get("SOULMATE_DOWNLOAD_HTTP_TIMEOUT")
                or DEFAULT_TIMEOUT_S)
        except Exception:
            self._timeout = DEFAULT_TIMEOUT_S
        self._token = os.environ.get("SOULMATE_DOWNLOAD_API_TOKEN", "")
        self._search_path = os.environ.get(
            "SOULMATE_DOWNLOAD_SEARCH_PATH", _DEFAULT_SEARCH_PATH)
        self._add_path = os.environ.get(
            "SOULMATE_DOWNLOAD_ADD_PATH", _DEFAULT_ADD_PATH)
        self._tasks_path = os.environ.get(
            "SOULMATE_DOWNLOAD_TASKS_PATH", _DEFAULT_TASKS_PATH).rstrip("/")
        self._remote_path = os.environ.get(
            "SOULMATE_DOWNLOAD_REMOTE_PATH", _DEFAULT_REMOTE_PATH).rstrip("/")
        self._results_path = os.environ.get(
            "SOULMATE_DOWNLOAD_RESULTS_PATH", _DEFAULT_RESULTS_PATH).rstrip("/")

    # ── HTTP（出站有界超时，失败细分报因）─────────────────────────

    async def _request(self, method: str, path: str, *,
                       params: dict | None = None,
                       body: dict | None = None) -> tuple[object, str]:
        """返回 (data, err)。err 非空=已带「错误」前缀的细分报因。"""
        url = f"{self._base}{path}"
        headers = {"Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self._timeout)) as client:
                resp = await client.request(
                    method, url, params=params, json=body, headers=headers)
        except Exception as e:
            return None, (f"错误: 下载后端不可达（{self._base}）: "
                          f"{type(e).__name__}{f': {e}' if str(e) else ''}"
                          f"——请确认后端 /search 与 /download 服务已启动")
        if resp.status_code >= 400:
            code, msg = "", ""
            try:
                payload = resp.json()
                err = payload.get("error") if isinstance(payload, dict) else None
                if isinstance(err, dict):
                    code = str(err.get("code") or "")
                    msg = str(err.get("message") or "")
                if not msg and isinstance(payload, dict):
                    # FastAPI HTTPException 形状 {"detail": "..."}（真后端惯例）
                    msg = str(payload.get("detail") or "")
            except Exception:
                msg = resp.text[:200]
            detail = f"{code + ': ' if code else ''}{msg or '未知错误'}"
            return None, f"错误[HTTP {resp.status_code}] {detail}"
        try:
            return resp.json(), ""
        except Exception:
            return None, f"错误: 后端返回非 JSON（{resp.text[:120]}）"

    @staticmethod
    def _task_of(data: object) -> dict:
        if isinstance(data, dict):
            inner = data.get("task")
            if isinstance(inner, dict):
                return inner
            return data
        return {}

    @staticmethod
    def _task_list_of(data: object) -> list:
        """合并任务列表（facade 三键 task/plugin_tasks/remote_tasks 有重复行，
        按 task_id 去重保序）。"""
        if isinstance(data, list):
            rows = [t for t in data if isinstance(t, dict)]
        elif isinstance(data, dict):
            rows = []
            for key in ("tasks", "remote_tasks", "plugin_tasks",
                        "results", "items"):
                lst = data.get(key)
                if isinstance(lst, list):
                    rows.extend(t for t in lst if isinstance(t, dict))
        else:
            return []
        seen: set = set()
        out: list = []
        for t in rows:
            tid = _pick(t, ("task_id", "id")) or id(t)
            if tid in seen:
                continue
            seen.add(tid)
            out.append(t)
        return out

    @staticmethod
    def _search_list_of(data: object) -> list:
        if isinstance(data, list):
            return [r for r in data if isinstance(r, dict)]
        if isinstance(data, dict):
            for key in ("results", "items", "torrents", "entries", "data"):
                lst = data.get(key)
                if isinstance(lst, list):
                    return [r for r in lst if isinstance(r, dict)]
        return []

    # ── 四工具（计划书形状）───────────────────────────────────────

    async def torrent_search(self, params: dict) -> str:
        query = str(params.get("query", "") or "").strip()
        if not query:
            return "错误: query 参数不能为空"
        try:
            limit = int(params.get("limit", 20) or 20)
        except Exception:
            limit = 20
        limit = max(1, min(limit, 100))  # 契约 1-100，缺省 20
        category = str(params.get("category", "") or "").strip()
        q = {"query": query, "limit": limit}
        if category:
            q["category"] = category
        data, err = await self._request("GET", self._search_path, params=q)
        if err:
            return err
        results = self._search_list_of(data)
        src_errors = (data.get("errors") or []) \
            if isinstance(data, dict) else []
        lines: list = []
        if not results:
            lines.append(
                f"🔍 种子搜索 {query}：0 个结果（换个关键词或去掉 category 再试）")
        else:
            shown = results[:limit]
            lines.append(
                f"🔍 种子搜索 {query}：{len(results)} 个结果"
                f"（展示前 {len(shown)} 条）：")
            for i, item in enumerate(shown, 1):
                lines.append(f"{i}. {_fmt_search_item(item)}")
        # 契约 errors=[{source,error}]：逐源降级如实回报（单源挂不造数据）
        for e in src_errors:
            if isinstance(e, dict):
                lines.append(f"⚠️ 源降级: {e.get('source', '?')}: "
                             f"{e.get('error', '?')}")
        if results:
            lines.append("→ 候选已呈现：多个候选有歧义先问用户要哪部（可用 clarify），"
                         "默认取最合适的一个；选定后 download_add(url=该候选的磁力/"
                         ".torrent 链) 加任务，再用 download_task_status/list 回报进度")
        return "\n".join(lines)

    async def download_add(self, params: dict) -> str:
        url = str(params.get("url", "") or "").strip()
        if not url:
            return ("错误: url 参数不能为空"
                    "（http/https 链接、magnet: 磁力链或 .torrent 直链）")
        target = str(params.get("target", "") or "remote").strip().lower()
        if target not in ("local", "remote"):
            return f"错误: target 只支持 local|remote，收到: {target}"
        body: dict = {"url": url, "target": target}
        filename = str(params.get("filename", "") or "").strip()
        if filename:
            body["filename"] = filename
        dest = str(params.get("dest", "") or "").strip()
        if dest:
            body["dest"] = dest
        data, err = await self._request("POST", self._add_path, body=body)
        if err:
            return err
        task = self._task_of(data)
        task_id = _pick(task, ("task_id", "id"))
        if not task_id:
            return ("错误: 后端未返回 task_id"
                    f"（响应: {json.dumps(data, ensure_ascii=False)[:200]}）")
        status = str(_pick(task, ("status",)) or "pending")
        name = str(_pick(task, ("filename", "dest")) or url)
        return (f"✅ 下载任务已添加: task_id={task_id} | 状态 {status} | "
                f"目标 {target} | {name}\n"
                f"跟进: download_task_status(task_id={task_id}) 查进度 / "
                f"download_task_list 看全部")

    async def download_task_status(self, params: dict) -> str:
        task_id = str(params.get("task_id", "") or params.get("id", "") or "").strip()
        if not task_id:
            return "错误: task_id 参数不能为空"
        # 契约 x-endpoint：GET /remote/{task_id}/metadata 优先；任务不在引擎面
        # （native/plugin 任务）时回落 GET /tasks 按 id 过滤；插件链终态行只在
        # GET /results/{task_id}（api doc §5 异步结果面）——三源逐一兜底。
        data, err = await self._request(
            "GET", f"{self._remote_path}/{task_id}/metadata")
        task = self._task_of(data) if not err else {}
        err2 = err3 = ""
        if not task:
            data2, err2 = await self._request("GET", self._tasks_path)
            rows = self._task_list_of(data2) if not err2 else []
            task = next(
                (t for t in rows
                 if str(_pick(t, ("task_id", "id")) or "") == task_id), {})
        if not task:
            data3, err3 = await self._request(
                "GET", f"{self._results_path}/{task_id}")
            if not err3 and isinstance(data3, dict) and data3:
                task = data3
        if not task:
            # 后端面真挂（列表面报错/结果面非 404 错误）如实报，不误报"不存在"
            hard_err = err2 or (err3 if err3 and "[HTTP 404]" not in err3 else "")
            if hard_err:
                return hard_err
            return (f"错误: 任务 {task_id} 不存在（引擎面/任务表/结果表均未命中"
                    + (f"；引擎面另报: {err}" if err else "")
                    + "；用 download_task_list 查看全部）")
        return f"📥 {_fmt_task(task)}"

    async def download_task_list(self, params: dict) -> str:
        status_f = str(params.get("status", "") or "").strip().lower()
        if status_f and status_f not in ("pending", "downloading", "paused",
                                        "done", "error"):
            return ("错误: status 只支持 pending/downloading/paused/done/error，"
                    f"收到: {status_f}")
        origin_f = str(params.get("origin", "") or "").strip().lower()
        if origin_f and origin_f not in ("local", "remote"):
            return f"错误: origin 只支持 local|remote，收到: {origin_f}"
        data, err = await self._request("GET", self._tasks_path)
        if err:
            return err
        tasks = self._task_list_of(data)
        # 客户端兜底过滤（后端可能忽略过滤参数）：origin 缺字段的行按 local 计
        # （引擎任务契约固定 origin='remote'，其余是本机侧任务）
        if status_f:
            tasks = [t for t in tasks
                     if str(_pick(t, ("status",)) or "").lower() == status_f]
        if origin_f:
            tasks = [t for t in tasks
                     if str(_pick(t, ("origin",)) or "local").lower() == origin_f]
        filters = "、".join(
            f"{k}={v}" for k, v in (("status", status_f), ("origin", origin_f)) if v)
        if not tasks:
            return ("📥 暂无下载任务"
                    + (f"（{filters}）" if filters else ""))
        cap = 50  # 契约无 limit 参数：展示上限自报，不截断隐瞒
        shown = tasks[:cap]
        head = f"📥 下载任务 {len(shown)} 个"
        if filters:
            head += f"（{filters}）"
        if len(tasks) > cap:
            head += f"（共 {len(tasks)} 个，展示前 {cap} 个）"
        return "\n".join([head + "："] + [f"- {_fmt_task(t)}" for t in shown])

    # ── 统一入口（soulmate_agent._download_dispatch 消费）──────────

    async def run(self, name: str, params: dict, session_id: str = "") -> str:
        if name == "torrent_search":
            return await self.torrent_search(params)
        if name == "download_add":
            return await self.download_add(params)
        if name == "download_task_status":
            return await self.download_task_status(params)
        if name == "download_task_list":
            return await self.download_task_list(params)
        return f"错误: 未知下载工具: {name or '(空)'}；可用: {', '.join(TOOL_NAMES)}"
