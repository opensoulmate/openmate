# -*- coding: utf-8 -*-
"""周期#54 工具面真回合验收探针（销 9.1 send_file / 9.2 process 诚实边界）。

真实用户路径：WS /ws/acp?token=JWT → ws_acp AGENT_ROUTES["soulmate"]
→ spawn `python -m agent.start --stdio` → SoulMateAgent 真实 LLM 回合
→ 真实调用 send_file / process 工具。

服务端副作用断言（不认回复自报）：
- send_file：agent_messages 回合行含 MEDIA:/tmp/tf_probe_*.txt（模型回显或网关
  _merge_pending_send_media 兜底补末行，同一通道不双轨）
- process：processes.db 注册表出现 tag 匹配行（start 动作真实登记）

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

TAG = f"TFPROBE_{int(time.time())}"
PROBE_FILE = f"/tmp/tf_probe_{TAG}.txt"
PROMPT = (
    "依次执行三步：①用 write_file 创建文件 {f}，内容一行：{tag} 工具面验收；"
    "②用 send_file 工具把这个文件发给我；"
    "③用 process 工具 start 启动后台命令 `bash -c 'echo {tag}; sleep 25'`，"
    "然后用 process list 确认它在列表里。"
    "最后只简短告诉我文件名和进程 id。[防缓存标记 {tag}]"
).format(f=PROBE_FILE, tag=TAG)

OPENSOUL_DB = str(Path.home() / "opensoul" / "data" / "opensoul.db")
PROCESS_DB = str(Path.home() / ".hermes" / "soulmate" / "memory" / "processes.db")


def _query(db_path: str, sql: str, args: tuple = ()) -> list:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return [f"db error: {e}"]


async def main() -> None:
    import websockets
    import jwt as pyjwt

    token = pyjwt.encode({"sub": f"tf-{int(time.time())}"}, ab.JWT_SECRET,
                         algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    t0 = time.monotonic()
    sid = ""
    async with websockets.connect(f"{ab.ACP_WS}?token={token}",
                                  max_size=20_000_000) as ws:
        r, _, _ = await ab._rpc(ws, "initialize", {"protocolVersion": 1},
                                rid=1, timeout=30)
        print("init:", bool((r.get("result") or {}).get("protocolVersion")),
              flush=True)
        r, _, _ = await ab._rpc(
            ws, "session/new",
            {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
            rid=2, timeout=90)
        res = r.get("result") or {}
        sid = res.get("session_id") or res.get("sessionId") or ""
        print("sid:", sid, flush=True)
        r, reply, sys_counts = await ab._rpc(
            ws, "session/prompt",
            {"sessionId": sid, "messageId": "toolface-probe",
             "prompt": [{"type": "text", "text": PROMPT}]},
            rid=3, timeout=320, collect_sid=sid)
        print("sys_frames:", sys_counts, flush=True)
        print("elapsed:", round(time.monotonic() - t0, 1), "s", flush=True)
        print("stop:", (r.get("result") or {}).get("stopReason", "-"), flush=True)
        print("REPLY:", reply[-800:], flush=True)

    # ── 服务端副作用断言 ─────────────────────────────────────
    verdicts: dict[str, bool] = {}

    # V1 文件真实创建且内容命中
    try:
        body = Path(PROBE_FILE).read_text(encoding="utf-8", errors="replace")
        ok = TAG in body
    except OSError as e:
        body, ok = f"read error: {e}", False
    verdicts["V1 文件真实落盘+内容命中"] = ok
    print("V1:", ok, repr(body[:80]), flush=True)

    # V2 回合行含 MEDIA: 末行（send_file 通道，模型回显或 merge 兜底）
    rows = _query(
        OPENSOUL_DB,
        "SELECT content FROM agent_messages WHERE session_id=? AND role='assistant' "
        "ORDER BY timestamp DESC LIMIT 8", (sid,))
    joined = "\n".join(str(r[0]) for r in rows if isinstance(r, tuple))
    ok = f"MEDIA:{PROBE_FILE}" in joined
    verdicts["V2 send_file MEDIA标签落库"] = ok
    print("V2:", ok, "| MEDIA hit:",
          f"MEDIA:{PROBE_FILE}" in joined, "| rows:", len(rows), flush=True)

    # V3 process 注册表真实登记（表名兼容探测）
    tables = _query(PROCESS_DB,
                    "SELECT name FROM sqlite_master WHERE type='table'")
    tnames = [t[0] for t in tables if isinstance(t, tuple)]
    proc_rows: list = []
    for tn in tnames:
        if tn.startswith("sqlite_"):
            continue
        try:
            proc_rows += _query(PROCESS_DB, f"SELECT * FROM {tn}")
        except Exception:  # noqa: BLE001
            continue
    hit = [r for r in proc_rows if isinstance(r, tuple) and TAG in str(r)]
    ok = bool(hit)
    verdicts["V3 process注册表登记"] = ok
    print("V3:", ok, "| tables:", tnames, "| tag rows:", len(hit),
          flush=True)

    print("TAG:", TAG, flush=True)
    print("VERDICT:", json.dumps(
        {"pass": sum(verdicts.values()), "total": len(verdicts),
         "detail": verdicts}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
