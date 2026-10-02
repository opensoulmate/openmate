#!/usr/bin/env python3
"""gene技能消费回执 live E2E — 全真实链零stub。

链路：真实WS /ws/acp?token=JWT（acp-proxy-a :8092，ws_acp路由agent_id="soulmate"）
→ 真实子进程 SoulMateAgent._prompt_inner（本轮接线：recommend注入记账→回合收口
  POST /api/gene/skill/{id}/usage）→ 真实 opensoul :8090 record_usage
→ skills.db计数漂移回读（usage_count +1 = 回执真实落库）。

种子：POST /api/gene/skill/extract 真实提炼一个带唯一marker的技能，再把计数
顶高保证 find_relevant 必进前3（注入前提），turn后按usage_count精确delta断言。

断言全过 exit 0；任何一条不过 exit 1 并打印哪条断了。
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import jwt as pyjwt
import websockets

ACP_WS = "ws://127.0.0.1:8092/ws/acp"
SOUL = "http://127.0.0.1:8090"
JWT_SECRET = "change-me-in-production"  # opensoul/.env JWT_SECRET（ws_acp同源读取）
SKILLS_DB = Path.home() / ".hermes" / "opensoul" / "gene" / "skills.db"
results: list[tuple[bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append((ok, f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}"))
    print(results[-1][1], flush=True)


def get_json(path: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(SOUL + path, timeout=timeout) as r:
        return json.loads(r.read())


def post_json(path: str, payload: dict, timeout: float = 10.0) -> tuple[int, dict]:
    req = urllib.request.Request(
        SOUL + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def delete(path: str, timeout: float = 10.0) -> int:
    req = urllib.request.Request(SOUL + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def db_counts(skill_id: str) -> tuple[int, int]:
    with sqlite3.connect(SKILLS_DB) as conn:
        row = conn.execute(
            "SELECT usage_count, success_count FROM learned_skills WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
    return (row[0], row[1]) if row else (-1, -1)


def bump_usage(skill_id: str, usage: int = 9999, success: int = 9999) -> None:
    """测试种子：顶高计数保证 find_relevant 必进前3（ORDER BY usage DESC）。"""
    with sqlite3.connect(SKILLS_DB) as conn:
        conn.execute(
            "UPDATE learned_skills SET usage_count = ?, success_count = ? "
            "WHERE skill_id = ?",
            (usage, success, skill_id),
        )
        conn.commit()


async def rpc(ws, method: str, params: dict, rid: int, timeout: float = 280.0) -> dict:
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        if msg.get("id") == rid:
            return msg
    raise TimeoutError(f"{method} 无响应({timeout}s)")


async def main() -> int:
    marker = f"e2e-usagereceipt-{int(time.time())}"
    skill_id = ""

    # 0. gene health
    try:
        h = get_json("/api/gene/health")
        check("opensoul gene health", bool(h), f"keys={list(h)[:6]}")
    except Exception as e:
        check("opensoul gene health", False, f"{e!r}")
        return 1

    # 1. 种子：真实extract提炼技能（read_file→write_file命中meaningful pattern）
    try:
        status, data = post_json("/api/gene/skill/extract", {
            "task_description": f"{marker} 复用流程验证",
            "execution_log": json.dumps([
                {"function": {"name": "read_file"}},
                {"function": {"name": "write_file"}},
            ]),
            "success": True,
        })
        skill_id = data.get("skill_id", "")
        check("种子技能提炼", status == 200 and bool(skill_id),
              f"status={status}, skill_id={skill_id}, event={data.get('event')}")
    except Exception as e:
        check("种子技能提炼", False, f"{e!r}")
        return 1
    if not skill_id:
        return 1

    # 2. 注入前提：计数顶高 + recommend必须返回该技能（agent注入前3）
    bump_usage(skill_id)
    user_text = f"{marker} 请只回复两个字：好的"
    try:
        status, data = post_json("/api/gene/skill/recommend", {"task_description": user_text})
        ids = [s.get("skill_id") for s in data.get("skills", [])]
        check("recommend含种子技能（注入前提）", skill_id in ids,
              f"status={status}, returned={ids}")
    except Exception as e:
        check("recommend含种子技能（注入前提）", False, f"{e!r}")
        return 1

    usage_before, success_before = db_counts(skill_id)

    # 3. 真实WS soulmate回合（真实子进程SoulMateAgent._prompt_inner）
    token = pyjwt.encode({"sub": "e2e-usage-user"}, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    try:
        async with websockets.connect(f"{ACP_WS}?token={token}", max_size=10_000_000) as ws:
            r = await rpc(ws, "initialize", {"protocolVersion": 1}, rid=1, timeout=20)
            check("initialize握手", bool((r.get("result") or {}).get("protocolVersion")),
                  f"agentInfo={(r.get('result') or {}).get('agentInfo')}")
            r = await rpc(ws, "session/new",
                          {"cwd": "/", "mcpServers": [], "agent_id": "soulmate"},
                          rid=2, timeout=60)
            sid = (r.get("result") or {}).get("session_id") or (r.get("result") or {}).get("sessionId") or ""
            check("session/new soulmate路由", bool(sid), f"acp_sid={sid}")
            if not sid:
                return 1
            t0 = time.time()
            r = await rpc(ws, "session/prompt",
                          {"sessionId": sid, "messageId": "e2e-usage-1",
                           "prompt": [{"type": "text", "text": user_text}]},
                          rid=3, timeout=280)
            check("真实LLM回合完成", "result" in r,
                  f"{time.time()-t0:.1f}s, stop_reason={(r.get('result') or {}).get('stop_reason')}")
            # 断开前等后处理链（usage回执在回合响应后异步收尾，给足时间）
            await asyncio.sleep(6)
    except Exception as e:
        check("真实WS soulmate回合", False, f"{e!r}")
        return 1

    # 4. 回执落库断言：usage_count精确+1（成功回执恰好一次）
    usage_after, success_after = db_counts(skill_id)
    check("usage回执落库（usage_count+1）", usage_after == usage_before + 1,
          f"before={usage_before}/{success_before} -> after={usage_after}/{success_after}")
    check("success计数漂移∈{0,+1}（结局诚实）",
          success_after - success_before in (0, 1),
          f"delta={success_after - success_before}（1=回合无错误标记，0=有错误标记）")

    # 5. 清理：DELETE种子技能（不给live库留e2e残留）
    code = delete(f"/api/gene/skill/{skill_id}?reason=e2e-usage-cleanup")
    code2 = delete(f"/api/gene/skill/{skill_id}?reason=e2e-usage-cleanup")
    check("清理种子技能", code == 200 and code2 == 404, f"DELETE={code}, 再DELETE={code2}")

    ok = all(r for r, _ in results)
    print(f"\n{'=' * 60}\nE2E {sum(1 for r, _ in results if r)}/{len(results)} PASS" if ok
          else f"\n{'=' * 60}\nE2E FAIL: {sum(1 for r, _ in results if r)}/{len(results)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
