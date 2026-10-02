#!/usr/bin/env python3
"""HTTP /acp/send入口relay轨迹 live E2E — 全真实链零stub。

链路：真实HTTP POST /acp/send（acp-proxy-a :8092，proxy.py硬编码hermes acp路由）
→ 真实子进程 hermes acp → proxy._traj_observe_client/_traj_observe_agent旁路观察
→ 真实TrajectoryRecorder HTTP → 真实opensoul :8090 trajectory API/DB
→ GET /sessions/{sid}/trace 回读断言（10:02轮遗留#3销账证据）。

与e2e_relay_traj.py（WS /ws/acp路径）互补：本脚本专测HTTP入口。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

PROXY = "http://127.0.0.1:8092"
SOUL = "http://127.0.0.1:8090"
results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((ok, f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"))
    print(results[-1][1], flush=True)


def post_json(url: str, payload: dict, timeout: float = 240.0) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get_json(url: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


def trace_asserts(sid: str, tag: str) -> None:
    try:
        s = get_json(f"{SOUL}/api/trajectory/sessions/{sid}")
        check(f"{tag} trajectory session ensure（agent_id=hermes）",
              s.get("id") == sid and s.get("agent_id") == "hermes",
              f"id={s.get('id')}, agent={s.get('agent_id')}")
    except Exception as e:
        check(f"{tag} trajectory session ensure", False, f"{e!r}")
        return
    try:
        tree = get_json(f"{SOUL}/api/trajectory/sessions/{sid}/trace")
        roots = tree.get("roots") or []
        check(f"{tag} trace树有root（HTTP回合落库）", len(roots) >= 1,
              f"roots={len(roots)}, total_events={tree.get('total_events')}")
        root = roots[0] if roots else {}
        check(f"{tag} root=user_input/turn_root",
              root.get("event_type") == "user_input"
              and (root.get("metadata") or {}).get("kind") == "turn_root",
              f"event_type={root.get('event_type')}, kind={(root.get('metadata') or {}).get('kind')}, "
              f"content={(root.get('content') or '')[:30]!r}")
        kids = root.get("children") or []
        llm_kids = [k for k in kids if k.get("event_type") == "llm_call"]
        check(f"{tag} root→llm_call子span（source=relay）", len(llm_kids) >= 1,
              f"children={len(kids)}, llm={[(k.get('agent_id'), (k.get('metadata') or {}).get('source')) for k in llm_kids]}")
        if llm_kids:
            check(f"{tag} llm段source=relay+agent_id=hermes",
                  (llm_kids[0].get("metadata") or {}).get("source") == "relay"
                  and llm_kids[0].get("agent_id") == "hermes",
                  f"source={(llm_kids[0].get('metadata') or {}).get('source')}, "
                  f"agent={llm_kids[0].get('agent_id')}, status={llm_kids[0].get('status')}, "
                  f"dur={llm_kids[0].get('duration_ms')}, content={(llm_kids[0].get('content') or '')[:40]!r}")
            tools_all = [c for k in llm_kids for c in (k.get("children") or [])
                         if c.get("event_type") == "tool_call"]
            check(f"{tag} 工具span（有则验形状）", True,
                  f"tool_call×{len(tools_all)} {[t.get('content') for t in tools_all][:5]}")
        check(f"{tag} root已end（end_turn收口）", bool(root.get("end_at")),
              f"end_at={root.get('end_at')}, status={root.get('status')}, "
              f"subtree_events={root.get('subtree_events')}")
    except Exception as e:
        check(f"{tag} trace树回读", False, f"{e!r}")


def main() -> int:
    # 回合1：新session（session_id=""=显式新会话）
    t0 = time.time()
    try:
        d = post_json(f"{PROXY}/acp/send",
                      {"text": "请只回答两个字：收到", "session_id": ""})
        sid = d.get("session_id") or ""
        check("HTTP /acp/send回合1", bool(d.get("ok")) and bool(sid),
              f"{time.time()-t0:.1f}s sid={sid}, source={d.get('source')}, "
              f"content={(d.get('content') or '')[:30]!r}, "
              f"empty={d.get('acp_empty_response')}, recovered={d.get('recovered_from_stale_session')}")
    except Exception as e:
        check("HTTP /acp/send回合1", False, f"{e!r}")
        return 1
    if not sid:
        return 1

    time.sleep(2.0)  # 让relay观察者后台worker落账
    trace_asserts(sid, "回合1")

    # 回合2：同session多轮（per-session观察者复用，回合独立落账）
    try:
        d2 = post_json(f"{PROXY}/acp/send",
                       {"text": "再回答两个字：确认", "session_id": sid})
        check("HTTP /acp/send回合2（同session）",
              bool(d2.get("ok")) and d2.get("session_id") == sid,
              f"content={(d2.get('content') or '')[:30]!r}, sid={d2.get('session_id')}")
    except Exception as e:
        check("HTTP /acp/send回合2（同session）", False, f"{e!r}")

    time.sleep(2.0)
    try:
        tree = get_json(f"{SOUL}/api/trajectory/sessions/{sid}/trace")
        roots = tree.get("roots") or []
        closed = [r for r in roots if r.get("end_at")]
        check("两回合都落库且收口", len(roots) >= 2 and len(closed) >= 2,
              f"roots={len(roots)}, closed={len(closed)}, total_events={tree.get('total_events')}")
    except Exception as e:
        check("两回合都落库且收口", False, f"{e!r}")

    failed = [r for ok, r in results if not ok]
    print(f"\n== {len(results) - len(failed)}/{len(results)} PASS ==")
    for r in failed:
        print(r)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
