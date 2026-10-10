"""SoulMate 验收总标准对标实测 — 10 类任务（开发/调试/运维/调研/发布/办公/定时/多渠道/记忆/协作）

真实链路（v2 修正）：WS /ws/acp?token=JWT → ws_acp AGENT_ROUTES["soulmate"]
→ spawn `python -m agent.start --stdio` → SoulMateAgent 真实 LLM 回合 + 真实工具执行。
⚠️ v1 走 HTTP /acp/send 是错误路径——proxy.py 硬编码 hermes acp 路由（L97/L141），
测到的是 Hermes 不是 SoulMate（10回合全打偏，见周期#30日志）。

验证纪律（P0"写了≠接线了"）：
- 服务端副作用优先（文件内容/SQLite 行），回复文本只作辅证；
- 回复文本无法独立验证的类别显式标注 verify_mode=reply_assisted（诚实边界）；
- 每类任务独立 session（同一 stdio 子进程，上下文互不污染）；
- 测试产物显式清理（MEMORY.md 标记行、accept_probe 定时任务行、/tmp 工作目录）。

结果写 /tmp/soulmate_accept_results.json，stdout 打印摘要表。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile

import jwt as pyjwt
import websockets

ACP_WS = "ws://127.0.0.1:8092/ws/acp"
JWT_SECRET = "change-me-in-production"
WORKDIR = "/tmp/soulmate_accept"
RESULTS_PATH = "/tmp/soulmate_accept_results.json"
MEM_PATH = os.path.expanduser("~/.hermes/soulmate/memory/MEMORY.md")
CRON_DB = os.path.expanduser("~/.hermes/soulmate/memory/cron_tasks.db")
SUB_DB = os.path.expanduser("~/.hermes/soulmate/memory/subagent_runs.db")
PROMPT_TIMEOUT_S = 300

MEM_MARKER = "ACCEPT_MEM_MARK_9"

# ── 8.5 验收复测支持（周期#40）────────────────────────────────────
# ACCEPT_CATEGORIES="定时,多渠道" 只跑指定类别（"每完成一个⚡项即重跑对应类别"）；
# 每次运行注入唯一 RUN_TAG 防缓存标记——semantic-cache EXACT 命中会回放旧响应
# （周期#31 实证假跑 0.98s 级），唯一标记让缓存永不命中。
RETEST_CATEGORIES = {c.strip() for c in os.environ.get("ACCEPT_CATEGORIES", "").split(",") if c.strip()}
RUN_TAG = os.environ.get("ACCEPT_RUN_TAG") or f"RETEST_{int(time.time())}"


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _query(db_path: str, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []


# ── 各类别独立验证（服务端副作用优先）────────────────────────────


def verify_dev(reply: str) -> tuple[bool, str]:
    path = os.path.join(WORKDIR, "dev_hello.py")
    content = _read(path)
    ok = os.path.exists(path) and "SOULMATE_DEV_OK" in content
    return ok, f"dev_hello.py {'存在' if os.path.exists(path) else '不存在'}，marker={'命中' if 'SOULMATE_DEV_OK' in content else '未命中'}"


def verify_debug(reply: str) -> tuple[bool, str]:
    content = _read(os.path.join(WORKDIR, "buggy.py"))
    fixed = "return a + b" in content
    tagged = "FIXED" in content
    return fixed and tagged, f"buggy.py 修复={'是' if fixed else '否'}(return a + b)、FIXED标记={'有' if tagged else '无'}"


def verify_ops(reply: str) -> tuple[bool, str]:
    ok = "8090" in reply and ("%" in reply or "磁盘" in reply or "disk" in reply.lower())
    return ok, f"回复含8090={'是' if '8090' in reply else '否'}、磁盘信息={'是' if ('%' in reply or '磁盘' in reply) else '否'}"


# 自报搜索不可用=真实声称，判据=主语邻接（确定性规则，周期#41两轮实证收敛）：
# "web_search 工具不可用"/"搜索工具不可用"（主体与"不可用"间≤6字且不在引号内）才算；
# 引用/抽象提及（'因此"不可用时…'、'不存在"不可用"情况'、'无需声明不可用'）不计——
# 模型会引用提示语里的"不可用"字样，裸子串判据会假阴性杀掉正确回答。
_UNAVAIL_CLAIM_RE = re.compile(
    r"(web_search|web_extract|搜索工具|搜索后端|搜索功能|搜索|工具)"
    r"[^\n。；！？\"'“”‘’]{0,6}不可用"
)


def verify_research(reply: str) -> tuple[bool, str]:
    text = reply or ""
    claim = bool(_UNAVAIL_CLAIM_RE.search(text))
    ok = "3.13" in text and len(text) > 30 and not claim
    return ok, (f"回复含'3.13'={'是' if '3.13' in text else '否'}、"
                f"自报搜索不可用={'是' if claim else '否'}（主语邻接判据，引用不计）、"
                f"长度{len(text)}字")


def verify_publish(reply: str) -> tuple[bool, str]:
    rows = _query(
        os.path.expanduser("~/opensoul/data/opensoul.db"),
        "SELECT content FROM agent_messages WHERE content LIKE ? ORDER BY rowid DESC LIMIT 5",
        ("%SOULMATE_PUBLISH_PROBE%",),
    )
    return bool(rows), f"opensoul.db agent_messages 命中 SOULMATE_PUBLISH_PROBE 行数={len(rows)}"


def verify_office(reply: str) -> tuple[bool, str]:
    path = os.path.join(WORKDIR, "report.xlsx")
    exists = os.path.exists(path)
    valid = exists and zipfile.is_zipfile(path)
    size = os.path.getsize(path) if exists else 0
    return valid and size > 400, f"report.xlsx {'存在' if exists else '不存在'}、有效xlsx={'是' if valid else '否'}、{size}字节"


def verify_cron(reply: str) -> tuple[bool, str]:
    rows = _query(CRON_DB, "SELECT * FROM tasks WHERE name = ?", ("accept_probe",))
    return bool(rows), f"cron_tasks.db tasks 表 accept_probe 行数={len(rows)}"


def verify_multichannel(reply: str) -> tuple[bool, str]:
    ok = ("accept_peer" in reply or "微信" in reply) and len(reply) > 20
    return ok, f"回复提及目标/微信={'是' if ('accept_peer' in reply or '微信' in reply) else '否'}"


def verify_memory(reply: str) -> tuple[bool, str]:
    content = _read(MEM_PATH)
    return MEM_MARKER in content, f"MEMORY.md 含 {MEM_MARKER}={'是' if MEM_MARKER in content else '否'}"


def verify_collab(reply: str) -> tuple[bool, str]:
    rows = _query(SUB_DB, "SELECT status FROM subagent_runs ORDER BY id DESC LIMIT 8")
    both = "ALPHA_DONE" in reply and "BETA_DONE" in reply
    db_ok = len(rows) >= 2
    return both and db_ok, f"回复含双worker产出={'是' if both else '否'}、subagent_runs近8行={len(rows)}"


SERVER_SIDE = {"开发", "调试", "发布", "办公", "定时", "记忆", "协作"}

TASKS = [
    {
        "category": "开发",
        "prompt": (
            "用 write_file 创建文件 /tmp/soulmate_accept/dev_hello.py，内容为一行："
            "print('SOULMATE_DEV_OK')。然后用 terminal 运行它，告诉我输出。"
        ),
        "verify": verify_dev,
    },
    {
        "category": "调试",
        "prompt": (
            "打开 /tmp/soulmate_accept/buggy.py，里面有一个 bug：add(2,3) 应该输出 5 但结果不对。"
            "请修复这个 bug（把注释里的 BUG 字样改成 FIXED），不要做其他改动。"
        ),
        "verify": verify_debug,
    },
    {
        "category": "运维",
        "prompt": (
            "用 system_checkup 工具做一次系统体检，告诉我磁盘使用百分比和 8090 端口是否在监听。"
        ),
        "verify": verify_ops,
    },
    {
        "category": "调研",
        "prompt": (
            "用 web_search 搜索 Python 3.13 的正式发布时间，简短回答（一句话）。"
            "如果搜索工具不可用，明确说明不可用。"
        ),
        "verify": verify_research,
    },
    {
        "category": "发布",
        "prompt": (
            "用 message_send 工具向 openmate 通道发送一条消息：channel='openmate'，"
            "target='accept-pub-target'，text='SOULMATE_PUBLISH_PROBE 验收发布探针'。"
            "发送后告诉我结果。"
        ),
        "verify": verify_publish,
    },
    {
        "category": "办公",
        "prompt": (
            "用 doc_generate 工具生成 Excel 文件 /tmp/soulmate_accept/report.xlsx，"
            "内容为二维表格：表头['名称','数量']，数据行[['苹果',3],['香蕉',5]]。完成后告诉我。"
        ),
        "verify": verify_office,
    },
    {
        "category": "定时",
        "prompt": (
            "依次执行：①用 schedule_task 创建定时任务 name='accept_probe'，"
            "schedule='interval:1h'，payload='验收探针任务'；②用 schedule_list 确认它在列表里；"
            "③用 schedule_cancel 停用它（不要 delete）。把任务 id 告诉我。"
        ),
        "verify": verify_cron,
    },
    {
        "category": "多渠道",
        "prompt": (
            "依次执行：①用 wechat_route_list 查看微信会话路由映射；"
            "②用 message_send 向微信通道发一条测试消息：channel='wechat'，"
            "target='accept_peer'，text='验收多渠道探针'。把两步结果都告诉我。"
        ),
        "verify": verify_multichannel,
    },
    {
        "category": "记忆",
        "prompt": (
            "依次执行：①用 memory_append 追加一行到持久记忆：MEMORY: ACCEPT_MEM_MARK_9 验收探针记录；"
            "②用 memory_read 读出记忆末尾并展示。"
        ),
        "verify": verify_memory,
    },
    {
        "category": "协作",
        "prompt": (
            "用 delegate_tasks 工具并行派发两个子任务（max_concurrency=2）："
            "任务1 goal='只回复一个词：ALPHA_DONE'；任务2 goal='只回复一个词：BETA_DONE'。"
            "完成后把两个子任务的结果都告诉我。"
        ),
        "verify": verify_collab,
    },
]


def setup() -> None:
    cleanup()  # 复测铁律：验证前先清陈旧产物（上次被打断轮的 accept_probe 行/记忆标记会假阳性）
    shutil.rmtree(WORKDIR, ignore_errors=True)
    os.makedirs(WORKDIR, exist_ok=True)
    with open(os.path.join(WORKDIR, "buggy.py"), "w", encoding="utf-8") as f:
        f.write(
            "def add(a, b):\n"
            "    return a - b  # BUG: should be addition\n"
            "\n"
            "print(add(2, 3))\n"
        )


def cleanup() -> None:
    if os.path.exists(MEM_PATH):
        lines = _read(MEM_PATH).splitlines(keepends=True)
        kept = [ln for ln in lines if MEM_MARKER not in ln]
        if len(kept) != len(lines):
            with open(MEM_PATH, "w", encoding="utf-8") as f:
                f.writelines(kept)
    try:
        conn = sqlite3.connect(CRON_DB, timeout=10)
        conn.execute("DELETE FROM tasks WHERE name = ?", ("accept_probe",))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass


# ── WS 驱动（soulmate 真实用户路径）──────────────────────────────


def _collect_texts(obj, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("text", "content") and isinstance(v, str) and v.strip():
                out.append(v)
            else:
                _collect_texts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_texts(v, out)


async def _rpc(ws, method: str, params: dict, rid: int, timeout: float,
               collect_sid: str = "") -> tuple[dict, str]:
    """发请求等同 id 响应；期间收集 session/update 文本块为回复文本。

    collect_sid 非空时只收集该 session 的帧——同一 WS 连接上前一回合的
    未完帧会继续到达，不按 sessionId 过滤会串进下一任务的回复（周期#30
    实测踩坑：任务7超时回合的帧污染任务8回复采集）。
    """
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    chunks: list = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, timeout - (time.time() - t0)))
        msg = json.loads(raw)
        if msg.get("id") == rid:
            _collect_texts(msg, chunks)
            return msg, "".join(chunks)
        if msg.get("method") == "session/update":
            psid = (msg.get("params") or {}).get("sessionId") or ""
            if collect_sid and psid and psid != collect_sid:
                continue  # 其他会话的在途帧，不串进本任务回复
            _collect_texts(msg.get("params") or msg, chunks)
    raise TimeoutError(f"{method} 无响应({timeout}s)")


async def main() -> None:
    setup()
    tasks = [t for t in TASKS if not RETEST_CATEGORIES or t["category"] in RETEST_CATEGORIES]
    if RETEST_CATEGORIES:
        missing = RETEST_CATEGORIES - {t["category"] for t in tasks}
        if missing:
            print(f"⚠️ 未知类别被忽略: {sorted(missing)}", flush=True)
    results = []
    token = pyjwt.encode({"sub": f"accept-{int(time.time())}"}, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    async with websockets.connect(f"{ACP_WS}?token={token}", max_size=20_000_000) as ws:
        r, _ = await _rpc(ws, "initialize", {"protocolVersion": 1}, rid=1, timeout=30)
        if not (r.get("result") or {}).get("protocolVersion"):
            print("initialize 握手失败:", r, flush=True)
        for i, task in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task['category']} (tag={RUN_TAG}) ...", flush=True)
            t0 = time.monotonic()
            entry = {"category": task["category"], "prompt": task["prompt"][:120], "run_tag": RUN_TAG}
            prompt_text = f"{task['prompt']}\n（复测标记 {RUN_TAG}，此行仅为防缓存标记，忽略其语义）"
            try:
                r, _ = await _rpc(
                    ws, "session/new",
                    {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
                    rid=100 + i * 2, timeout=90,
                )
                res = r.get("result") or {}
                sid = res.get("session_id") or res.get("sessionId") or ""
                if not sid:
                    raise RuntimeError(f"session/new 无session_id: {r}")
                r, reply = await _rpc(
                    ws, "session/prompt",
                    {"sessionId": sid, "messageId": f"accept-{i}",
                     "prompt": [{"type": "text", "text": prompt_text}]},
                    rid=101 + i * 2, timeout=PROMPT_TIMEOUT_S, collect_sid=sid,
                )
                stop = (r.get("result") or {}).get("stopReason", "-")
                entry["session_id"] = sid
                entry["stop_reason"] = stop
                entry["http_ok"] = "result" in r
            except Exception as e:  # noqa: BLE001
                entry.update(ok=False, latency_s=round(time.monotonic() - t0, 2),
                             verify_mode="error", detail=f"WS失败: {type(e).__name__}: {e}",
                             reply_excerpt="")
                results.append(entry)
                print(f"    -> ❌ {entry['detail']} ({entry['latency_s']}s)", flush=True)
                continue
            latency = round(time.monotonic() - t0, 2)
            entry["reply_excerpt"] = re.sub(r"\s+", " ", reply)[:200]
            entry["latency_s"] = latency
            try:
                ok, detail = task["verify"](reply)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"验证器异常: {type(e).__name__}: {e}"
            entry["ok"] = bool(ok) and entry.get("http_ok", False)
            entry["verify_mode"] = "server_side" if task["category"] in SERVER_SIDE else "reply_assisted"
            entry["detail"] = detail
            results.append(entry)
            print(f"    -> {'✅' if entry['ok'] else '❌'} {detail} "
                  f"({latency}s, stop={entry.get('stop_reason')}) reply={entry['reply_excerpt'][:80]!r}",
                  flush=True)
    cleanup()
    summary = {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_tag": RUN_TAG,
        "categories": sorted(RETEST_CATEGORIES) if RETEST_CATEGORIES else "ALL",
        "passed": sum(1 for r in results if r["ok"]),
        "total": len(results),
        "results": results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("=" * 60)
    print(f"验收结果: {summary['passed']}/{summary['total']} 通过")
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['category']:<4} {r['latency_s']:>7}s  {r['detail']}")
    print(f"完整结果: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
