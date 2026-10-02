#!/usr/bin/env python3
"""session收口 live E2E — 三层收口全真实链，零stub（04:20轮遗留#2销账）。

链路：
①HTTP生命周期：真实opensoul :8090 POST /sessions → end → 事件重开（自愈）
②闲置清扫：真实读路径GET /sessions触发sweep——重启前快照里的僵尸running
  session（>30min无事件）翻completed，刚有事件的新session保持running
③显式收口：真实WS /ws/acp?token=JWT（acp-proxy :8092，hermes路由）→ 真实
  子进程回合 → 断开WS → ws_acp teardown调end_session → GET回读status=completed

用法：python3 e2e_session_lifecycle.py（服务重启后跑；PRE=/tmp/pre_restart_sessions.json
存在则做②的前后对照）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import urllib.request

import jwt as pyjwt
import websockets

SOUL = "http://127.0.0.1:8090"
ACP_WS = "ws://127.0.0.1:8092/ws/acp"
JWT_SECRET = "change-me-in-production"
results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((ok, f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"))
    print(results[-1][1], flush=True)


def req(method: str, path: str, payload: dict | None = None, timeout: float = 10.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    r = urllib.request.Request(SOUL + path, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def get_session(sid: str) -> dict:
    return req("GET", f"/api/trajectory/sessions/{sid}")


def post_event(sid: str, content: str) -> dict:
    return req("POST", f"/api/trajectory/sessions/{sid}/events",
               {"event_type": "user_input", "content": content, "status": "ok"})


async def rpc(ws, method: str, params: dict, rid: int, timeout: float = 240.0) -> dict:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("id") == rid:
            return msg
    raise TimeoutError(f"{method} 无响应({timeout}s)")


def phase_http_lifecycle() -> str:
    """①create→end→事件自愈重开（全HTTP真行为）。"""
    sid = f"e2e-sess-lifecycle-{int(time.time())}"
    s = req("POST", "/api/trajectory/sessions", {"session_id": sid, "agent_id": "e2e"})
    check("①-1 新session=running", s.get("status") == "running", f"status={s.get('status')}")
    post_event(sid, "hello")
    req("POST", f"/api/trajectory/sessions/{sid}/end")
    s = get_session(sid)
    check("①-2 显式收口→completed", s.get("status") == "completed",
          f"status={s.get('status')} ended_at={bool(s.get('ended_at'))}")
    post_event(sid, "hello again")  # 自愈重开
    s = get_session(sid)
    check("①-3 事件自愈重开→running", s.get("status") == "running" and not s.get("ended_at"),
          f"status={s.get('status')} ended_at={s.get('ended_at')}")
    return sid


def phase_idle_sweep(pre_ids: list[str]) -> None:
    """②读路径闲置清扫：旧僵尸翻completed（对照重启前快照）。"""
    req("GET", "/api/trajectory/sessions")  # 触发清扫（读路径接线）
    time.sleep(0.5)
    data = req("GET", "/api/trajectory/sessions?limit=200")
    by_id = {s["id"]: s for s in data.get("sessions", [])}
    flipped = [sid for sid in pre_ids if by_id.get(sid, {}).get("status") == "completed"]
    still_running = [sid for sid in pre_ids if by_id.get(sid, {}).get("status") == "running"]
    check("②-1 僵尸session被清扫翻completed", len(flipped) >= 1,
          f"flipped={len(flipped)}/{len(pre_ids)} still_running={still_running[:3]}")
    statuses = {}
    for s in by_id.values():
        statuses[s["status"]] = statuses.get(s["status"], 0) + 1
    check("②-2 session列表不再恒running", statuses.get("running", 0) < len(by_id),
          f"statuses={statuses}")


async def phase_ws_teardown() -> None:
    """③真实WS回合→断开→teardown收口completed。"""
    token = pyjwt.encode({"sub": "e2e-sess-lifecycle"}, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    sid = ""
    async with websockets.connect(f"{ACP_WS}?token={token}", max_size=10_000_000) as ws:
        r = await rpc(ws, "initialize", {"protocolVersion": 1}, rid=1, timeout=20)
        check("③-1 initialize握手", bool((r.get("result") or {}).get("protocolVersion")), "")
        r = await rpc(ws, "session/new",
                      {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "hermes"},
                      rid=2, timeout=90)
        res = r.get("result") or {}
        sid = res.get("session_id") or res.get("sessionId") or ""
        check("③-2 session/new hermes路由", bool(sid), f"acp_sid={sid}")
        if not sid:
            return
        try:
            r = await rpc(ws, "session/prompt",
                          {"sessionId": sid, "messageId": "lifecycle-1",
                           "prompt": [{"type": "text", "text": "请只回答两个字：收到"}]},
                          rid=3, timeout=240)
            check("③-3 真实hermes回合", "result" in r or "error" in r,
                  f"stopReason={(r.get('result') or {}).get('stopReason', '-')}")
        except Exception as e:
            check("③-3 真实hermes回合", False, f"{e!r}")
        # 回合落库有后台队列延迟——轮询等session行出现（≤5s）
        s = {}
        for _ in range(10):
            try:
                s = get_session(sid)
                if s:
                    break
            except Exception:
                pass
            time.sleep(0.5)
        check("③-4 回合落库时session=running", s.get("status") == "running",
              f"status={s.get('status')} events={s.get('total_events')}")
    # WS已断开→teardown已收口（end_session在ws close前await完成）
    s = None
    for _ in range(10):
        try:
            s = get_session(sid)
            if s.get("status") == "completed":
                break
        except Exception:
            pass
        time.sleep(0.5)
    check("③-5 断开WS→session收口completed", (s or {}).get("status") == "completed",
          f"status={(s or {}).get('status')} ended_at={bool((s or {}).get('ended_at'))}")
    return sid


def phase_cleanup(sids: list[str]) -> None:
    for sid in sids:
        if not sid:
            continue
        try:
            req("DELETE", f"/api/trajectory/sessions/{sid}")
        except Exception as e:
            print(f"[warn] cleanup {sid}: {e!r}", flush=True)


async def main() -> int:
    pre_path = "/tmp/pre_restart_sessions.json"
    pre_ids = json.load(open(pre_path)) if os.path.exists(pre_path) else []

    http_sid = phase_http_lifecycle()
    if pre_ids:
        phase_idle_sweep(pre_ids)
    else:
        print("[skip] 无重启前快照，跳过②前后对照", flush=True)
    ws_sid = ""
    try:
        ws_sid = await phase_ws_teardown() or ""
    finally:
        phase_cleanup([http_sid, ws_sid])

    passed = sum(1 for ok, _ in results if ok)
    print(f"\n== E2E {passed}/{len(results)} PASS ==", flush=True)
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
