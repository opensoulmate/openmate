# -*- coding: utf-8 -*-
"""SoulMate 智能搜索链真实验证（非 pytest，直接 python 运行）——2026-11-17。

真实验证「用户口吻提问→结果真相关且可一键加任务」，全程真件：
- 真 LLM：agent/llm_engine.LLMEngine.chat（opensoul/.env 配置的真实模型，经
  agent/smart_search.py 意图查询改写 + 语义相关性筛选两段提示词）；
- 真后端：opensoul src/api/download_api.py 全套路由（/search、/download、/tasks、
  /remote/{id}/metadata）+ download_search 管道（bing_magnet 真 cn.bing.com 候选
  + IA/AT 逐源降级如实回报）；
- 真工具链：agent/download_tools.py DownloadTools.torrent_search/download_add/
  download_task_status/list（用户口吻 query 直接进智能链，与 SoulMate
  _download_dispatch 同一执行体）。

两段验证：
A. 真实候选语义验证：用户口吻「最新有什么好的科幻电影帮我找一下」「三体的
   有声书」→ 真 bing 多源候选 → 真 LLM 语义筛选——科普页/百科页/不相干项
   必须被丢弃（或至少不得进入精选），保留项必须带语义相关性理由；
B. 一键加任务：向真后端注册自定义 JSON 搜索源（确定性候选池=真实形状资源行
   + 科普页/不相干干扰行，走真后端 download_search 管道）→「三体的有声书」→
   语义层只留真相关行→ download_add 精选第 1 条的 _add 参数→ 任务创建+
   进度/总览可见。

用法：cd ~/openmate/acp-proxy && PYTHONPATH=. python tests/integ_smart_search_real.py
退出码 0=验证通过（真实网络/LLM 波动会如实报 ❌，不造假绿）。持久化状态隔离
进临时目录（DOWNLOAD_TASKS_DB 等），不污染真实运行库。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ── 环境隔离（持久化状态进临时目录，禁污染真实运行库）──────────────
_TMP = Path(tempfile.mkdtemp(prefix="dl-smart-integ-"))
os.environ["DOWNLOAD_TASKS_DB"] = str(_TMP / "download_tasks.db")
os.environ["DOWNLOAD_ENGINE_CONFIG"] = str(_TMP / "engine_config.json")

OPENSOUL = Path.home() / "opensoul"
ACP_PROXY = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(OPENSOUL))
sys.path.insert(0, str(ACP_PROXY))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from src.api.download_api import router as download_router  # noqa: E402

PORT = int(os.environ.get("DL_SMART_INTEG_PORT", "8396"))
FILES_PORT = PORT + 1
BASE = f"http://127.0.0.1:{PORT}"
FILES = f"http://127.0.0.1:{FILES_PORT}"
API = f"{BASE}/api/download"
SAMPLE_BYTES = 4 * 1024 * 1024  # 4MB 环回：验证真实字节推进

app = FastAPI()
app.include_router(download_router, prefix="/api/download")

# Phase B 确定性候选池（真实形状资源行 + 科普页/不相干干扰行）：
# 语义层期望：留「三体 有声书 全集」（+可能「三体 广播剧」），丢百科/西游记/Python
_POOL = [
    {"title": "三体 有声书 全集（刘慈欣）M4A 高音质", "size": SAMPLE_BYTES,
     "seeders": 23, "source": "integ-smart",
     "url": f"{FILES}/files/sample.m4a"},
    {"title": "三体 广播剧 全集 多人剧", "size": 800_000_000,
     "seeders": 12, "source": "integ-smart",
     "magnet": "magnet:?xt=urn:btih:" + "ab" * 20},
    {"title": "三体是什么？_百度百科", "size": 0, "seeders": 0,
     "source": "integ-smart", "url": "https://baike.baidu.com/item/%E4%B8%89%E4%BD%93"},
    {"title": "西游记 评书 全集下载", "size": 2_000_000_000, "seeders": 9,
     "source": "integ-smart", "url": "https://example.com/xyj.mp3"},
    {"title": "Python 编程入门教程", "size": 1_000_000_000, "seeders": 30,
     "source": "integ-smart", "url": "https://example.com/py.mp4"},
]


class _FilesHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_GET(self):
        if self.path.startswith("/files/sample.m4a"):
            body = b"\x00" * SAMPLE_BYTES
            self.send_response(200)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/files/search.json"):
            # 形状=download_search._custom_json 消费的 {results:[候选行]}
            body = json.dumps({"results": _POOL}, ensure_ascii=False).encode()
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


async def _phase_a(tools, engine, failures: list) -> None:
    """A. 真实候选语义验证（真 bing + 真 LLM）。"""
    for q in ("最新有什么好的科幻电影帮我找一下", "三体的有声书"):
        print(f"\n========== 用户口吻提问: {q} ==========")
        res = await engine.run(q)
        out = await tools.torrent_search({"query": q})
        print(out)
        _check("SoulMate 智能搜索" in out, f"[A:{q[:8]}] 智能链产出", out, failures)
        item_titles = [str(r.get("title") or "") for r in res.items]
        dropped_titles = [str(d.get("title") or "") for d in res.dropped]
        pool = item_titles + dropped_titles
        if not pool:
            # 候选为空=底层源没搜到：如实自报 0 结果+改写多路可见，不造数据
            _check("0 个结果" in out and "多路查询" in out,
                   f"[A:{q[:8]}] 候选为空如实自报（改写多路查询可见，不造数据）",
                   out, failures)
            continue
        _check(res.llm_used, f"[A:{q[:8]}] 真 LLM 语义层生效（非字面匹配）",
               " ".join(res.notes), failures)
        # 科普页/百科页（判定基准：百科/是什么/含义类标题）不得进精选
        kep = [t for t in pool
               if re.search(r"百度百科|是什么|什么意思|含义|科普", t)]
        bad = [t for t in kep if t in item_titles]
        if kep:
            _check(not bad, f"[A:{q[:8]}] 科普页/百科页被语义层丢弃", str(bad),
                   failures)
        if res.items:
            _check(all((r.get("_smart") or {}).get("reason") for r in res.items),
                   f"[A:{q[:8]}] 精选项逐条带语义相关性理由", out, failures)
            cores = ("科幻", "电影") if "科幻" in q else ("三体", "有声", "听书")
            off = [t for t in item_titles if not any(c in t for c in cores)]
            _check(not off, f"[A:{q[:8]}] 精选项标题主题真相关", str(off), failures)
        else:
            # 有候选但空精选=真判定「无相关资源」，丢弃清单必须如实列因
            _check(bool(dropped_titles),
                   f"[A:{q[:8]}] 无精选时必须有丢弃清单（不给假候选）",
                   out, failures)
        if res.clarify:
            print(f"（歧义追问: {res.clarify}）")


async def _phase_b(tools, engine, failures: list) -> None:
    """B. 真后端管道 + 真 LLM 语义筛选 + 一键加任务。"""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(f"{API}/search/sources", json={
            "name": "integ-smart", "kind": "json",
            "url": f"{FILES}/files/search.json?query={{query}}"})
        print(f"\n--- 注册自定义 JSON 搜索源 → HTTP {r.status_code} ---")
    q = "三体的有声书"
    print(f"\n========== Phase B: 用户口吻提问: {q}（候选池含科普/不相干干扰行）==========")
    res = await engine.run(q)
    out = await tools.torrent_search({"query": q})
    print(out)
    item_titles = [str(r.get("title") or "") for r in res.items]
    dropped_titles = [str(d.get("title") or "") for d in res.dropped]
    _check(res.llm_used, "[B] 真 LLM 语义层生效", " ".join(res.notes), failures)
    _check(any("三体 有声书 全集" in t for t in item_titles),
           "[B] 真相关行（三体 有声书 全集）被语义层保留", out, failures)
    _check(not any("三体是什么" in t for t in item_titles),
           "[B] 科普页（三体是什么_百度百科）被丢弃",
           f"items={item_titles}", failures)
    _check(not any(("西游记" in t or "Python" in t) for t in item_titles),
           "[B] 不相干行（西游记/Python）被丢弃",
           f"items={item_titles}", failures)
    _check(any("三体是什么" in t for t in dropped_titles),
           "[B] 丢弃清单如实列科普页", f"dropped={dropped_titles}", failures)

    # 一键加任务：精选第 1 条的 _add 参数直接 download_add
    if not res.items:
        _check(False, "[B] 一键加任务（精选为空）", out, failures)
        return
    top = res.items[0]
    add = top.get("_add") or {}
    _check(bool(add.get("url")), "[B] 精选第 1 条自带可入任务参数",
           json.dumps(add, ensure_ascii=False), failures)
    args = {"url": add.get("url", ""), "target": "remote",
            "dest": str(_TMP / "downloads")}
    if add.get("extract_code"):
        args["extract_code"] = add["extract_code"]
    r2 = await tools.download_add(args)
    print(f"\n--- download_add(一键: {top.get('title')}) ---\n{r2}")
    _check("✅ 下载任务已添加" in r2 and "task_id=" in r2,
           "[B] 一键加任务成功（真后端 /download 入任务）", r2, failures)
    if "task_id=" not in r2:
        return
    task_id = r2.split("task_id=")[1].split(" ")[0].strip()
    r3 = await tools.download_task_status({"task_id": task_id})
    print(f"\n--- download_task_status ---\n{r3}")
    _check(f"📥 {task_id}" in r3, "[B] 任务进度回报（真后端三源兜底）", r3, failures)
    r4 = await tools.download_task_list({})
    _check(task_id in r4, "[B] 任务总览可见（一键入任务可跟进）", r4, failures)
    # http 直链行=环回真实字节推进；磁力行=任务创建即成立（引擎面终态如实回报）
    if str(add.get("url", "")).startswith("http"):
        deadline = time.time() + 40
        while time.time() < deadline:
            r3 = await tools.download_task_status({"task_id": task_id})
            if "| done |" in r3:
                break
            await asyncio.sleep(0.5)
        _check("| done |" in r3, "[B] 一键任务真实下载完成（环回真实字节）",
               r3, failures)


async def _main() -> int:
    from agent.download_tools import DownloadTools
    from agent.llm_engine import LLMEngine
    from agent.smart_search import SmartSearchEngine

    # 真 LLM 预检（无 key=如实跳过，不假绿）
    from utils.llm_config import resolve_agent_llm_config
    cfg = resolve_agent_llm_config()
    if not cfg.get("api_key"):
        print("❌ 真 LLM 未配置（resolve_agent_llm_config 无 api_key）——无法做"
              "真实语义验证，退出 2（不假绿）")
        return 2
    engine_llm = LLMEngine()
    tools = DownloadTools(api_base=API, timeout=60.0, llm_fn=engine_llm.chat)
    engine = SmartSearchEngine(llm_fn=engine_llm.chat,
                               fetch=tools.search_candidates)

    failures: list = []
    files_httpd = ThreadingHTTPServer(("127.0.0.1", FILES_PORT), _FilesHandler)
    threading.Thread(target=files_httpd.serve_forever, daemon=True).start()
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
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
        await _phase_a(tools, engine, failures)
        await _phase_b(tools, engine, failures)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        files_httpd.shutdown()
        files_httpd.server_close()
        try:
            from src.api.download_engine import get_download_engine
            stopped = get_download_engine().stop()
            if asyncio.iscoroutine(stopped):
                await stopped
        except Exception as e:
            print(f"（引擎收尾提示：{e}）")
    print("\n===== 智能搜索真实验证: "
          + ("全部通过 ✅" if not failures else f"失败 {len(failures)} 项 ❌ {failures}")
          + " =====")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
