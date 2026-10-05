"""Live E2E：/ws/acp WebSocket soulmate路由 — 验证本轮token校准+预算manage接线。

流程：JWT(opensoul/.env secret) → ws://127.0.0.1:8092/ws/acp?token=... →
initialize + session/new(agent_id=soulmate) → session/prompt(文本) →
收集至prompt响应 → 检查token归因账本是否出现本session的新record且携带calibrated_*字段。
HTTP /acp/send走的是proxy.py硬编码的hermes acp路由（不写soulmate账本），soulmate真实
前端聊天路径=本/ws/acp soulmate路由，故E2E必须走WS。
"""
import asyncio
import os
import json
import sys
import time
from pathlib import Path

import jwt as pyjwt
import websockets

LEDGER = Path.home() / ".hermes" / "soulmate" / "token_attribution" / "attribution_ledger.jsonl"


def _load_jwt_secret() -> str:
    _env = os.path.join(os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul"), ".env")
    for line in open(_env):
        line = line.strip()
        if line.startswith("JWT_SECRET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "openmate-jwt-secret"


def _ledger_rows():
    rows = []
    if LEDGER.exists():
        for line in open(LEDGER):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


async def main():
    secret = _load_jwt_secret()
    token = pyjwt.encode(
        {"sub": "cron-e2e", "exp": int(time.time()) + 3600},
        secret, algorithm="HS256",
    )
    before = len(_ledger_rows())
    url = f"ws://127.0.0.1:8092/ws/acp?token={token}"
    acp_sid = None
    prompt_done = False
    agent_texts = []
    async with websockets.connect(url, max_size=20 * 1024 * 1024) as ws:
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": 1,
                       "clientCapabilities": {"fs": {"readTextFile": False,
                                                     "writeTextFile": False}}},
        }))
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 2, "method": "session/new",
            "params": {"cwd": os.environ.get("USER_HOME", "/home/climbing"), "mcpServers": [],
                       "agent_id": "soulmate",
                       # 会话级provenance（jobs.source同款产生侧标注）：测试探针会话
                       # 显式声明出身，ExperienceCollector据此不产经验——探针不再污染
                       # 生产learned_patterns（"请回复ok两个字即可"治理轮）
                       "_meta": {"source": "test"}},
        }))
        deadline = time.time() + 60
        while time.time() < deadline and acp_sid is None:
            raw = await asyncio.wait_for(ws.recv(), timeout=70)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 2 and "result" in msg:
                acp_sid = (msg["result"] or {}).get("sessionId")
                print("session/new result sessionId:", acp_sid)
        if acp_sid is None:
            print("FAIL: no session/new response within 60s")
            return 1
        await ws.send(json.dumps({
            "jsonrpc": "2.0", "id": 3, "method": "session/prompt",
            "params": {"sessionId": acp_sid,
                       "prompt": [{"type": "text", "text": "请回复OK两个字即可"}]},
        }))
        deadline = time.time() + 150
        while time.time() < deadline and not prompt_done:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=160)
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            # agent消息文本（session/update通知）+ prompt完成（id==3 result）
            if msg.get("method") == "session/update":
                upd = (msg.get("params") or {}).get("update") or {}
                agent_texts.append(json.dumps(upd, ensure_ascii=False)[:200])
            if msg.get("id") == 3 and ("result" in msg or "error" in msg):
                prompt_done = True
                print("prompt response:", json.dumps(msg.get("result") or msg.get("error"),
                                                     ensure_ascii=False)[:300])
    print("agent update chunks:", len(agent_texts))
    for t in agent_texts[-3:]:
        print("  update:", t[:160])
    await asyncio.sleep(2)  # 账本落盘缓冲
    rows = _ledger_rows()
    new_rows = rows[before:]
    print(f"ledger rows before={before} after={len(rows)} new={len(new_rows)}")
    hits = []
    for r in new_rows:
        if r.get("backfill"):
            print("  backfill row:", json.dumps(r, ensure_ascii=False)[:200])
            continue
        u = r.get("usage", {})
        print("  record session:", r.get("session_id"),
              "| calibration_factor:", u.get("calibration_factor"),
              "| calibrated_total:", u.get("calibrated_total_tokens"),
              "| total:", u.get("total_tokens"))
        if r.get("session_id") == acp_sid and u.get("calibration_factor") is not None:
            hits.append(r)
    if hits:
        print("E2E PASS: soulmate路由record携带calibrated_*字段（soulmate_agent.py:1058接线live实证）")
        return 0
    print("E2E FAIL: 本session无携带calibration_factor的record")
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
