# -*- coding: utf-8 -*-
"""
三级分流隐式化·真对话验收（2026-11-19 归位）：
真实 WS 用户路径（WS → ws_acp → soulmate stdio → 真实 LLM），全程只说话、无第二入口。

三条路径（用户裁定原话："在对话中跟soulmate沟通完，它觉得需要协作，它自动转到群组或者流程化"）：
  P1 小任务 → SoulMate 直接干完（正常 LLM 回复，无升级告知、无分流记录）
  P2 中任务 → 对话中自动进 AI 群组（回复含"转到群组了"+产物深链，群真建、任务真发布）
  P3 大任务 → 自动起流程（回复含流程深链，记录 status=running + run_id，不是等确认）

证据：stdout + data/task_triage.db（tri-* 记录 context.session_id=真会话）。
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, "/home/climbing/openmate/acp-proxy")

import httpx
import jwt as pyjwt
import websockets

ACP_WS = "ws://127.0.0.1:8092/ws/acp"
JWT_SECRET = "change-me-in-production"
TRIAGE_API = "http://127.0.0.1:8092/triage"
OPSOUL_API = "http://127.0.0.1:8090"

TAG = int(time.time())
EV = {"tag": TAG, "checks": [], "pass": None}


def rec(name: str, ok: bool, detail: str = "") -> None:
    EV["checks"].append({"name": name, "ok": bool(ok), "detail": detail[:500]})
    print(f"{'✅' if ok else '❌'} {name}: {detail[:300]}", flush=True)


def walk_texts(obj, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "text" and isinstance(v, str):
                out.append(v)
            else:
                walk_texts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            walk_texts(v, out)


class Conn:
    """同连接并发 RPC 分发器 + session/update 文本收集（消费式）。"""

    def __init__(self, ws):
        self.ws = ws
        self.pending = {}
        self.updates = []
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                mid = msg.get("id")
                if mid is not None and mid in self.pending:
                    self.pending.pop(mid).set_result(msg)
                elif msg.get("method") == "session/update":
                    self.updates.append(msg)
        except Exception:
            pass

    async def rpc(self, method, params, rid, timeout):
        fut = asyncio.get_event_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send(json.dumps({
            "jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        return await asyncio.wait_for(fut, timeout)

    def collect(self, sid: str) -> str:
        chunks = []
        pending, self.updates = self.updates, []
        for m in pending:
            psid = (m.get("params") or {}).get("sessionId") or ""
            if sid and psid and psid != sid:
                continue
            walk_texts(m.get("params") or m, chunks)
        return "".join(chunks)


async def main() -> None:
    token = pyjwt.encode({"sub": f"triage-implicit-{TAG}"}, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    async with httpx.AsyncClient(timeout=30) as client:
        base_rec = (await client.get(f"{TRIAGE_API}/records")).json()
        base_ids = {r["id"] for r in base_rec.get("records", [])}

        async with websockets.connect(f"{ACP_WS}?token={token}", max_size=20_000_000) as ws:
            conn = Conn(ws)
            await conn.start()
            await conn.rpc("initialize", {"protocolVersion": 1}, rid=1, timeout=30)
            r = await conn.rpc(
                "session/new",
                {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
                rid=2, timeout=90)
            sid = ((r.get("result") or {}).get("session_id")
                   or (r.get("result") or {}).get("sessionId") or "")
            rec("W0 真会话建立（用户只说话的对话面）", bool(sid), sid)
            if not sid:
                EV["pass"] = False
                return

            # ── P1 小任务：SoulMate 直接干完，无第二入口、无升级 ──
            rid = 3
            await conn.rpc(
                "session/prompt",
                {"sessionId": sid, "messageId": f"tri-small-{TAG}",
                 "prompt": [{"type": "text",
                             "text": "解释一下什么是向量数据库，用一两句话就行"}]},
                rid=rid, timeout=300)
            reply_small = conn.collect(sid)
            rec("P1a 小任务 SoulMate 直接回答", len(reply_small.strip()) > 5,
                reply_small[-200:].replace("\n", " "))
            rec("P1b 小任务零打扰（无升级告知）",
                ("转到群组" not in reply_small) and ("转成流程" not in reply_small)
                and ("流程草稿" not in reply_small), reply_small[-200:].replace("\n", " "))

            # ── P2 中任务：对话中自动转 AI 群组（用户没有选任何东西）──
            rid += 1
            await conn.rpc(
                "session/prompt",
                {"sessionId": sid, "messageId": f"tri-medium-{TAG}",
                 "prompt": [{"type": "text",
                             "text": "帮我调研对比 Notion 和 Obsidian 的优劣，并给出选型方案"}]},
                rid=rid, timeout=300)
            reply_med = conn.collect(sid)
            rec("P2a 中任务透明告知「转到群组了」", "转到群组了" in reply_med,
                reply_med[-250:].replace("\n", " "))
            rec("P2b 告知含群组产物深链", "/ai-groups?group=" in reply_med,
                reply_med[-250:].replace("\n", " "))

            # ── P3 大任务：自动起流程（草稿→装载跑，不是等用户确认）──
            rid += 1
            await conn.rpc(
                "session/prompt",
                {"sessionId": sid, "messageId": f"tri-large-{TAG}",
                 "prompt": [{"type": "text",
                             "text": "搭建一个每周发布对账的审批流程，长期例行运行"}]},
                rid=rid, timeout=600)
            reply_large = conn.collect(sid)
            rec("P3a 大任务透明告知流程化", "流程" in reply_large and "/will?flow=" in reply_large,
                reply_large[-250:].replace("\n", " "))
            rec("P3b 大任务自动起流程（非等确认）",
                ("跑了" in reply_large) or ("已按多阶段流程执行" in reply_large),
                reply_large[-250:].replace("\n", " "))

        # ── 记录/产物服务端真源回读 ──
        board = (await client.get(f"{TRIAGE_API}/records")).json()
        new_recs = [r for r in board.get("records", []) if r["id"] not in base_ids]
        by_track = {}
        for rr in new_recs:
            by_track.setdefault(rr.get("track"), []).append(rr)
        rec("R1 分流记录留痕（会话 id 归属）",
            all((rr.get("context") or {}).get("session_id") == sid for rr in new_recs) and bool(new_recs),
            json.dumps([{ "id": r["id"], "track": r["track"], "status": r["status"],
                          "session": (r.get("context") or {}).get("session_id")}
                        for r in new_recs], ensure_ascii=False))

        med = (by_track.get("group") or [None])[-1]
        if med:
            gid = (med.get("artifact") or {}).get("group_id", "")
            g = await client.get(f"{OPSOUL_API}/api/ai-groups/{gid}")
            ok_g = g.status_code == 200 and (g.json().get("tasks") or [])
            rec("R2 群组真建+任务真发布（OpenSoul 真源回读）", ok_g,
                f"gid={gid} http={g.status_code} tasks={len((g.json().get('tasks') or []) if g.status_code == 200 else [])}")
        else:
            rec("R2 群组真建+任务真发布（OpenSoul 真源回读）", False, "无 group 轨记录")

        large = (by_track.get("flow") or [None])[-1]
        if large:
            art = large.get("artifact") or {}
            rec("R3 流程实例真跑（run_id 回读）",
                large.get("status") == "running" and bool(art.get("run_id")),
                json.dumps({"status": large.get("status"), "flow_id": art.get("flow_id"),
                            "run_id": art.get("run_id")}, ensure_ascii=False))
        else:
            rec("R3 流程实例真跑（run_id 回读）", False, "无 flow 轨记录")

        rec("R4 小任务不落分流记录（小=会话内直接办）",
            len(new_recs) == 2 and "chat" not in by_track,
            f"新记录数={len(new_recs)} 轨道={sorted(by_track)}")

    EV["pass"] = all(c["ok"] for c in EV["checks"])
    out = f"/home/climbing/openmate/acp-proxy/data/triage-implicit-acceptance-{TAG}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(EV, f, ensure_ascii=False, indent=2)
    print(f"\n{'PASS' if EV['pass'] else 'FAIL'} — 证据: {out}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
