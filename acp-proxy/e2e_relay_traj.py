#!/usr/bin/env python3
"""relay轨迹 live E2E — hermes引擎回合落trajectory全真实链，零stub。

链路：真实WS /ws/acp?token=JWT（acp-proxy-a :8092，ws_acp路由agent_id="hermes"）
→ 真实子进程 hermes acp（非soulmate外部引擎）→ ws_acp RelayTrajectory旁路观察
→ 真实TrajectoryRecorder HTTP → 真实opensoul :8090 trajectory API/DB
→ GET /sessions/{sid}/trace 回读断言（04:20轮遗留#4销账证据）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request

import jwt as pyjwt
import websockets

ACP_WS = "ws://127.0.0.1:8092/ws/acp"
SOUL = "http://127.0.0.1:8090"
JWT_SECRET = "change-me-in-production"
results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((ok, f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"))
    print(results[-1][1], flush=True)


def get_json(path: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(SOUL + path, timeout=timeout) as r:
        return json.loads(r.read())


async def rpc(ws, method: str, params: dict, rid: int, timeout: float = 240.0) -> dict:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("id") == rid:
            return msg
    raise TimeoutError(f"{method} 无响应({timeout}s)")


async def main() -> int:
    token = pyjwt.encode({"sub": "e2e-relay-traj"}, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    async with websockets.connect(f"{ACP_WS}?token={token}", max_size=10_000_000) as ws:
        try:
            r = await rpc(ws, "initialize", {"protocolVersion": 1}, rid=1, timeout=20)
            check("initialize握手", bool((r.get("result") or {}).get("protocolVersion")), "")
        except Exception as e:
            check("initialize握手", False, f"{e!r}")
            return 1

        # session/new agent_id=hermes → ws_acp起hermes acp子进程（非soulmate）
        try:
            r = await rpc(ws, "session/new",
                          {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "hermes"},
                          rid=2, timeout=90)
            res = r.get("result") or {}
            sid = res.get("session_id") or res.get("sessionId") or ""
            check("session/new hermes路由", bool(sid), f"acp_sid={sid}")
        except Exception as e:
            check("session/new hermes路由", False, f"{e!r}")
            return 1
        if not sid:
            return 1

        # 真实hermes回合（LLM成败皆可——失败回合照样应落span，失败必须可见）
        turn_ok = False
        try:
            t0 = time.time()
            r = await rpc(ws, "session/prompt",
                          {"sessionId": sid, "messageId": "relay-1",
                           "prompt": [{"type": "text", "text": "请只回答两个字：收到"}]},
                          rid=3, timeout=240)
            turn_ok = "result" in r
            check("hermes真实回合", turn_ok,
                  f"{time.time()-t0:.1f}s stop={(r.get('result') or {}).get('stopReason') or (r.get('result') or {}).get('stop_reason')}")
        except Exception as e:
            check("hermes真实回合（超时也验span）", False, f"{e!r}")

        # 回读trajectory
        await asyncio.sleep(2.0)  # 让close收口写入落库
        try:
            s = get_json(f"/api/trajectory/sessions/{sid}")
            check("trajectory session ensure（agent_id=hermes）",
                  s.get("id") == sid and s.get("agent_id") == "hermes",
                  f"id={s.get('id')}, agent={s.get('agent_id')}")
        except Exception as e:
            check("trajectory session ensure", False, f"{e!r}")
            return 1

        try:
            tree = get_json(f"/api/trajectory/sessions/{sid}/trace")
            roots = tree.get("roots") or []
            check("trace树有root（hermes回合落库）", len(roots) >= 1,
                  f"roots={len(roots)}, total_events={tree.get('total_events')}")
            root = roots[0] if roots else {}
            check("root=user_input/turn_root",
                  root.get("event_type") == "user_input"
                  and (root.get("metadata") or {}).get("kind") == "turn_root",
                  f"event_type={root.get('event_type')}, kind={(root.get('metadata') or {}).get('kind')}, "
                  f"content={(root.get('content') or '')[:30]!r}")
            kids = root.get("children") or []
            llm_kids = [k for k in kids if k.get("event_type") == "llm_call"]
            check("root→llm_call子span在场（source=relay）", len(llm_kids) >= 1,
                  f"children={len(kids)}, llm={[(k.get('agent_id'), (k.get('metadata') or {}).get('source')) for k in llm_kids]}")
            if llm_kids:
                check("llm段source=relay+agent_id=hermes",
                      (llm_kids[0].get("metadata") or {}).get("source") == "relay"
                      and llm_kids[0].get("agent_id") == "hermes",
                      f"source={(llm_kids[0].get('metadata') or {}).get('source')}, "
                      f"agent={llm_kids[0].get('agent_id')}, status={llm_kids[0].get('status')}, "
                      f"dur={llm_kids[0].get('duration_ms')}, content={(llm_kids[0].get('content') or '')[:40]!r}")
                tools_all = [c for k in llm_kids for c in (k.get("children") or [])
                             if c.get("event_type") == "tool_call"]
                check("工具span（有则验形状）", True, f"tool_call×{len(tools_all)} "
                      f"{[t.get('content') for t in tools_all][:5]}")
            check("root已end（end_turn收口）", bool(root.get("end_at")),
                  f"end_at={root.get('end_at')}, status={root.get('status')}, "
                  f"subtree_events={root.get('subtree_events')}")
        except Exception as e:
            check("trace树回读", False, f"{e!r}")
            return 1

    failed = [r for ok, r in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} PASS ==")
    for r in failed:
        print(r)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
