# -*- coding: utf-8 -*-
"""8.3 真实用户路径探针：schedule 3 工具链（验收#7 同款）跑 soulmate 真实回合，
观察 [turn-watchdog] 心跳可见性 + 定时任务落库（8.5 定时类预热复测）。

复用 acceptance_benchmark 的 WS 连接/采集器（含 sessionId 过滤防串帧）。
"""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/climbing/openmate/acp-proxy")

import acceptance_benchmark as ab

MARKER = f"wdprobe-{int(time.time())}"
PROMPT = (
    "依次执行：①用 schedule_task 创建定时任务 name='wd_probe_1791631'，"
    "schedule='interval:1h'，payload='看门狗心跳探针'；"
    "②用 schedule_list 确认它在列表里；③用 schedule_cancel 停用它（不要 delete）。"
    f"把任务 id 告诉我。[防缓存标记 {MARKER}]"
)


async def main() -> None:
    import websockets
    import jwt as pyjwt

    token = pyjwt.encode({"sub": f"wd-{int(time.time())}"}, ab.JWT_SECRET,
                         algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    t0 = time.monotonic()
    async with websockets.connect(f"{ab.ACP_WS}?token={token}",
                                  max_size=20_000_000) as ws:
        r, _ = await ab._rpc(ws, "initialize", {"protocolVersion": 1},
                             rid=1, timeout=30)
        print("init:", bool((r.get("result") or {}).get("protocolVersion")),
              flush=True)
        r, _ = await ab._rpc(
            ws, "session/new",
            {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
            rid=2, timeout=90)
        res = r.get("result") or {}
        sid = res.get("session_id") or res.get("sessionId") or ""
        print("sid:", sid, flush=True)
        r, reply = await ab._rpc(
            ws, "session/prompt",
            {"sessionId": sid, "messageId": "wd-probe",
             "prompt": [{"type": "text", "text": PROMPT}]},
            rid=3, timeout=320, collect_sid=sid)
        print("elapsed:", round(time.monotonic() - t0, 1), "s", flush=True)
        print("stop:", (r.get("result") or {}).get("stopReason", "-"), flush=True)
        print("REPLY:", reply[-600:], flush=True)

    conn = sqlite3.connect(
        str(Path.home() / ".hermes" / "soulmate" / "memory" / "cron_tasks.db"),
        timeout=10)
    try:
        rows = conn.execute(
            "SELECT id, name, schedule, enabled FROM tasks WHERE name LIKE 'wd_probe%'"
        ).fetchall()
    except Exception as e:  # noqa: BLE001
        rows = [f"db error: {e}"]
    finally:
        conn.close()
    print("CRON_ROWS:", rows, flush=True)
    print("MARKER:", MARKER, flush=True)


if __name__ == "__main__":
    asyncio.run(main())
