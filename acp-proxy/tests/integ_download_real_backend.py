# -*- coding: utf-8 -*-
"""下载中心工具组 ↔ opensoul 真后端联调脚本（非 pytest，直接 python 运行）。

拉起 opensoul 的真实下载路由（src/api/download_api.py：/search、/download、
/tasks、/remote/{id}/metadata 全套真代码 + 下载引擎门面），用 SoulMate 真实
dispatch（SoulMateAgent._download_dispatch）跑完整对话流工具链：
torrent_search → download_add → download_task_status（真实字节进度）→
download_task_list + 未知任务细分报因。

确定性保障：向真后端注册一个自定义 JSON 搜索源（本脚本自带的 /files/search.json
路由，REST POST /api/download/search/sources 注册），搜索链路走真后端
download_search 管道（适配器/缓存/errors 逐源降级），不依赖外网。

用法：cd ~/openmate/acp-proxy && python tests/integ_download_real_backend.py
退出码 0=联调通过。持久化状态隔离进临时目录（DOWNLOAD_TASKS_DB 等）。
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

# ── 环境隔离（持久化状态进临时目录，禁污染真实运行库）──────────────
_TMP = Path(tempfile.mkdtemp(prefix="dl-integ-"))
os.environ["DOWNLOAD_TASKS_DB"] = str(_TMP / "download_tasks.db")
os.environ["DOWNLOAD_ENGINE_CONFIG"] = str(_TMP / "engine_config.json")

OPENSOUL = Path.home() / "opensoul"
ACP_PROXY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OPENSOUL))
sys.path.insert(0, str(ACP_PROXY))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from src.api.download_api import router as download_router  # noqa: E402

PORT = int(os.environ.get("DL_INTEG_PORT", "8398"))
FILES_PORT = PORT + 1  # /files/* 独立线程服务器（禁自调用阻塞 uvicorn 事件循环）
BASE = f"http://127.0.0.1:{PORT}"
FILES = f"http://127.0.0.1:{FILES_PORT}"
API = f"{BASE}/api/download"
SAMPLE_BYTES = 16 * 1024 * 1024  # 16MB 环回：留出在途窗口验证任务表可见性

app = FastAPI()
app.include_router(download_router, prefix="/api/download")


class _FilesHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self):
        if self.path.startswith("/files/sample.bin"):
            body = b"\x00" * SAMPLE_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/files/search.json"):
            # 形状=download_search._custom_json 消费的
            # {results:[{title,size,seeders,url|magnet}]}；磁力与 http URL
            # 各一，验证两类可入任务链
            import json as _json
            query = self.path.split("query=")[-1] if "query=" in self.path else ""
            body = _json.dumps({"results": [
                {"title": f"OpenMate Integration Sample A ({query})",
                 "size": SAMPLE_BYTES, "seeders": 8, "source": "integ-mock",
                 "url": f"{FILES}/files/sample.bin"},
                {"title": f"OpenMate Integration Sample B ({query})",
                 "size": 0, "seeders": -1, "source": "integ-mock",
                 "magnet": "magnet:?xt=urn:btih:" + "0" * 40},
            ]}, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)


def _check(cond: bool, label: str, detail: str, failures: list) -> None:
    mark = "✅" if cond else "❌"
    print(f"{mark} {label}" + ("" if cond else f"  ← {detail[:300]}"))
    if not cond:
        failures.append(label)


async def _flow(failures: list) -> None:
    from agent.download_tools import DownloadTools
    from agent.soulmate_agent import SoulMateAgent

    agent = SoulMateAgent.__new__(SoulMateAgent)
    # timeout 需覆盖真后端搜索管道最坏耗时：内置两源在无外网环境各挂满 15s
    # 适配器超时（契约语义=逐源降级进 errors，不拖垮整体），30s+ 余量
    agent._download_tools_inst = DownloadTools(api_base=API, timeout=60.0)
    sid = "sess-integ"

    async def call(name, args):
        out = await agent._download_dispatch(name, args, sid)
        print(f"\n--- {name} {args} ---\n{out}")
        return out

    # ① 注册确定性搜索源（真后端 REST）
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{API}/search/sources", json={
            "name": "integ-mock", "kind": "json",
            "url": f"{FILES}/files/search.json?query={{query}}"})
        print(f"--- 注册搜索源 → HTTP {r.status_code} ---")
    # ② 搜索（真后端 download_search 管道）
    r1 = await call("torrent_search", {"query": "integration sample"})
    _check("🔍 种子搜索" in r1 and "OpenMate Integration Sample A" in r1,
           "torrent_search 走真后端 /api/download/search 返回候选",
           r1, failures)
    _check("可入任务: magnet:?xt=urn:btih:" in r1,
           "候选含磁力链（可直接入任务）", r1, failures)
    _check("可入任务: " + FILES in r1 or "可入任务: http" in r1,
           "候选含 http 可入任务链", r1, failures)
    # ③ 加任务（真后端 /api/download/download + 引擎门面真实下载）
    r2 = await call("download_add",
                    {"url": f"{FILES}/files/sample.bin", "target": "remote"})
    _check("✅ 下载任务已添加" in r2 and "task_id=" in r2,
           "download_add 走真后端 /api/download/download 入任务", r2, failures)
    if "task_id=" not in r2:
        return
    task_id = r2.split("task_id=")[1].split(" ")[0].strip()
    # ④ 进度回报（真字节推进）：轮询 status + list，直到真实 done
    status = ""
    list_seen = False
    r4 = ""
    deadline = time.time() + 30
    while time.time() < deadline:
        status = await call("download_task_status", {"task_id": task_id})
        if "| done |" in status:
            break
        r4 = await call("download_task_list", {})
        if task_id in r4:
            list_seen = True
        await asyncio.sleep(0.5)
    _check(f"📥 {task_id}" in status and "进度 " in status,
           "download_task_status 走真后端（remote/{id}/metadata 主面）回报进度",
           status, failures)
    _check("| done |" in status,
           "下载真实完成（done，环回真实字节推进，结果面终态行兜底）",
           status, failures)
    # ⑤ 总览（真 facade /tasks；任务完成即从任务面消失→在途期可见性或诚实空表）
    if not list_seen:
        r4 = await call("download_task_list", {})
    _check("📥" in r4,
           "download_task_list 走真后端 /api/download/tasks", r4, failures)
    _check(list_seen or "暂无" in r4,
           "任务表可见性诚实（在途可见或终态后如实空表）", r4, failures)
    # ⑥ 未知任务细分报因
    r5 = await call("download_task_status", {"task_id": "nope-does-not-exist"})
    _check("错误" in r5 and "nope-does-not-exist" in r5,
           "未知任务细分报因（禁静默）", r5, failures)


async def _main() -> int:
    failures: list = []
    files_httpd = ThreadingHTTPServer(("127.0.0.1", FILES_PORT), _FilesHandler)
    threading.Thread(target=files_httpd.serve_forever, daemon=True).start()
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT,
                            log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):  # 等 uvicorn 就绪
        if server.started:
            break
        await asyncio.sleep(0.1)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{API}/health")
        print(f"--- 真后端 /api/download/health → HTTP {r.status_code} ---")
        await _flow(failures)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        files_httpd.shutdown()
        files_httpd.server_close()
        # 引擎门面子进程优雅退出（门面 stop：POST /shutdown → SIGTERM）
        try:
            from src.api.download_engine import get_download_engine
            stopped = get_download_engine().stop()
            if asyncio.iscoroutine(stopped):
                await stopped
        except Exception as e:
            print(f"（引擎收尾提示：{e}）")
    print("\n===== 联调结果: "
          + ("全部通过 ✅" if not failures else f"失败 {len(failures)} 项 ❌ {failures}")
          + " =====")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
