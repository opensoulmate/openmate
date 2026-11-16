# -*- coding: utf-8 -*-
"""本地 mock 后端 /search 与 /download REST（docs/download-mcp-tools.json
契约 + docs/download-engine-api.md 形状的联调服务，下载中心工具组
agent/download_tools.py 的消费方）。

契约（DownloadTask 形状按 docs/download-engine-api.md §3：camelCase 主形 +
task_id/total_bytes/downloaded_bytes/progress_pct 蛇形别名；错误形状
{"error":{"code","message"}}）：
- GET  /search?query=&category=&limit=   → {"results":[候选],"errors":[],"total"}
  候选形状按 download-mcp-tools.json outputSchema：title/size/seeders/source/
  url|magnet（含 size=0/seeders=-1 未知值样本）；errors=[{source,error}] 逐源降级
- POST /download  body {url,target,filename?,dest?,total_bytes?,speed?}
  → Task ack（total_bytes/speed 测试用覆盖项，缺省 64MB@256KB/s=持续下载中）
- GET  /tasks                             → {"tasks":[...],"remote_tasks":同批
  重复行,"plugin_tasks":[],"total"}（对齐真实 facade：重复行用于验证消费方按
  task_id 去重；status/origin 查询参数故意忽略，验证工具层客户端兜底过滤）
- GET  /remote/{id}/metadata              → {"task":Task,"metadata":{...}}
  （契约 status 主面）/ 404 {"error":{"code":"not_found"}}
- GET  /results/{id}                      → 13 键异步结果行（api doc §5，仅终态
  done/error 有记录）/ 404（status 三源兜底第三面）

路径自动兼容带/不带 /api/download 前缀（真实后端 REST 挂 /api/download 下）。
任务进度按时间模拟推进（min(total, speed*elapsed)），跑满自动 done。

用法：
- CLI: python tests/mock_download_backend.py --port 8399
- 测试: MockDownloadBackend().start() → base_url，stop() 收尾
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

_PREFIX = "/api/download"


def _search_results(query: str, category: str, limit: int) -> list:
    """确定性候选（契约形状 title/size/seeders/source/url|magnet）：
    2 个磁力 + 2 个 http/.torrent URL + 1 个未知值样本。"""
    items = []
    sources = ("Internet Archive", "Academic Torrents")
    for i in range(4):
        infohash = hashlib.sha1(f"{query}-{i}".encode()).hexdigest()
        title = f"{query} Sample {i + 1}"
        if i < 2:
            link = {"magnet": f"magnet:?xt=urn:btih:{infohash}&dn={title}"}
        else:
            link = {"url": f"https://example.org/{infohash}.torrent"}
        items.append({
            "title": title,
            "size": 150_000_000 * (i + 1),
            "seeders": 42 - i * 7,
            "source": sources[i % 2],
            **link,
        })
    # 未知值样本：size=0、seeders=-1（源不公开）
    items.append({
        "title": f"{query} (size/seeders 未知样本)",
        "size": 0,
        "seeders": -1,
        "source": "Custom RSS",
        "magnet": f"magnet:?xt=urn:btih:{hashlib.sha1(query.encode()).hexdigest()}",
    })
    return items[:max(1, min(limit, 100))]


class _Store:
    def __init__(self):
        self.lock = threading.RLock()  # live() 在 list() 锁内被调用，需可重入
        self.tasks: dict = {}
        self.seq = 0

    def add(self, body: dict) -> dict:
        with self.lock:
            self.seq += 1
            task_id = f"mock{self.seq:04d}"
            url = str(body.get("url") or "")
            dest = str(body.get("dest") or "")
            filename = str(body.get("filename") or "") or (
                os.path.basename(dest) if dest else f"task{self.seq}.bin")
            total = int(body.get("total_bytes") or 64 * 1024 * 1024)
            speed = int(body.get("speed") or 256 * 1024)
            kind = ("bt" if url.startswith("magnet:")
                    or ".torrent" in url else "http")
            task = {
                "id": task_id, "task_id": task_id,
                "url": url, "filename": filename,
                "dest": dest or f"/downloads/{filename}",
                "totalBytes": total, "total_bytes": total,
                "downloadedBytes": 0, "downloaded_bytes": 0,
                "speed": speed, "eta": total // speed if speed else 0,
                "progress": 0.0, "progress_pct": 0.0,
                "status": "downloading", "type": kind,
                "addedAt": time.time(), "origin": "remote",
                "target": str(body.get("target") or "remote"),
                "error": None,
                "_t0": time.time(),
            }
            self.tasks[task_id] = task
            return task

    def live(self, task: dict) -> dict:
        """按时间推进模拟进度（不改 _t0 契约字段，返回契约字段视图）。"""
        with self.lock:
            t = dict(task)
            elapsed = time.time() - t["_t0"]
            total, speed = t["totalBytes"], t["speed"]
            downloaded = min(total, int(speed * elapsed))
            pct = round(downloaded / total * 100, 1) if total else 0.0
            t.update({
                "downloadedBytes": downloaded, "downloaded_bytes": downloaded,
                "progress": pct, "progress_pct": pct,
                "status": "done" if downloaded >= total else "downloading",
                "eta": (int((total - downloaded) / speed)
                        if speed and downloaded < total else 0),
            })
            t.pop("_t0", None)
            return t

    def list(self) -> list:
        with self.lock:
            return [self.live(t) for t in self.tasks.values()]

    def get(self, task_id: str):
        with self.lock:
            t = self.tasks.get(task_id)
        return self.live(t) if t else None


class _Handler(BaseHTTPRequestHandler):
    server_version = "MockDownloadBackend/1.0"

    def log_message(self, format, *args):  # noqa: A002 测试输出静音（基类签名）
        pass

    # ── helpers ────────────────────────────────────────────────
    @property
    def store(self) -> _Store:
        return getattr(self.server, "store")

    def _send(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _err(self, code: int, err_code: str, message: str):
        self._send(code, {"error": {"code": err_code, "message": message}})

    def _norm_path(self) -> str:
        path = urlparse(self.path).path
        if path.startswith(_PREFIX):
            path = path[len(_PREFIX):] or "/"
        return path

    # ── routes ─────────────────────────────────────────────────
    def do_GET(self):
        path = self._norm_path()
        query = parse_qs(urlparse(self.path).query)
        if path == "/search":
            q = (query.get("query") or [""])[0]
            if not q:
                return self._err(400, "bad_request", "query 不能为空")
            category = (query.get("category") or [""])[0]
            limit = int((query.get("limit") or ["20"])[0])
            results = _search_results(q, category, limit)
            # 契约：errors=[{source,error}] 逐源降级如实回报
            # （query 含 failsource=确定性触发一条降级，供联调验证）
            errors = ([{"source": "Broken RSS", "error": "timeout (15s)"}]
                      if "failsource" in q else [])
            return self._send(200, {
                "query": q, "category": category or None,
                "results": results, "errors": errors, "total": len(results),
            })
        if path == "/tasks":
            rows = self.store.list()
            # 对齐真实 facade GET /api/download/tasks：remote 行在 tasks 与
            # remote_tasks 两个键里重复出现（消费方须按 task_id 去重）
            return self._send(200, {
                "tasks": rows, "remote_tasks": rows, "plugin_tasks": [],
                "total": len(rows),
            })
        m = re.fullmatch(r"/remote/([^/]+)/metadata", path)
        if m:
            task = self.store.get(m.group(1))
            if task is None:
                return self._err(404, "not_found",
                                 f"task not found: {m.group(1)}")
            return self._send(200, {
                "task": task,
                "metadata": {"files": [task["filename"]], "trackers": []},
            })
        m = re.fullmatch(r"/results/([^/]+)", path)
        if m:
            task = self.store.get(m.group(1))
            # 异步结果面（api doc §5）：终态行才有记录，在途/未知 404 如实拒
            if task is None or task["status"] not in ("done", "error"):
                return self._err(404, "not_found",
                                 f"no async result record: {m.group(1)}")
            return self._send(200, {
                "task_id": task["id"], "registry_key": task["dest"],
                "url": task["url"], "dest": task["dest"],
                "status": task["status"], "plugin": "mock-plugin",
                "error": task["error"], "fallback_chain": ["mock-plugin"],
                "total_bytes": task["totalBytes"],
                "downloaded_bytes": task["downloadedBytes"],
                "progress_pct": task["progress"],
                "started_at": task["addedAt"], "finished_at": time.time(),
            })
        return self._err(404, "not_found", f"no route: {path}")

    def do_POST(self):
        path = self._norm_path()
        if path != "/download":
            return self._err(404, "not_found", f"no route: {path}")
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            return self._err(400, "bad_request", "请求体不是合法 JSON")
        url = str(body.get("url") or "").strip()
        if not url:
            return self._err(400, "bad_request", "url 不能为空")
        target = str(body.get("target") or "remote")
        if target not in ("local", "remote"):
            return self._err(400, "bad_request",
                             f"target 只支持 local|remote: {target}")
        task = self.store.add({**body, "url": url, "target": target})
        return self._send(201, self.store.live(task))


class MockDownloadBackend:
    """可启停的 mock 后端（线程化 http.server，端口 0=自动选空闲）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        self._httpd = ThreadingHTTPServer((self._host, self._port), _Handler)
        self._httpd.store = _Store()  # type: ignore[attr-defined]
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self.base_url

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def base_url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("mock backend 未启动")
        return f"http://{self._host}:{self._httpd.server_address[1]}{_PREFIX}"


def main():
    parser = argparse.ArgumentParser(description="mock /search + /download REST")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8399)
    args = parser.parse_args()
    backend = MockDownloadBackend(args.host, args.port)
    base = backend.start()
    print(f"mock download backend listening: {base}", flush=True)
    print(f"  GET  {base}/search?query=demo&limit=5", flush=True)
    print(f"  POST {base}/download", flush=True)
    print(f"  GET  {base}/tasks", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        backend.stop()


if __name__ == "__main__":
    main()
