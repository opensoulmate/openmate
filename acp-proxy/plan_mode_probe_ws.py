# -*- coding: utf-8 -*-
"""P0-09 Plan模式禁写盘 终验探针（真实路径三层）：

1. 引擎语义层：explore 模式下 POST /api/immune/permission/check
   - write_file → DENY（explore mode is read-only）
   - 显式 allow 规则也不可授予修改（AgentScope规格）
   - read_file 等只读 → ALLOW（读不受限）
2. 真实用户路径层：WS → ws_acp → soulmate stdio → 真实 LLM 回合
   - explore 模式让 SoulMate 用 write_file 建文件 → 文件必须不存在 + 回复报拦截
   - 恢复 bypass 对照组 → 同样指令文件必须建成（证明拦截是模式所致）
3. finally 恒恢复基线模式（explore 是全局态，绝不残留）

服务端真源：文件存在性 + 引擎 decision JSON + /tmp/plan_mode_evidence.json。
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/climbing/openmate/acp-proxy")

import httpx
import acceptance_benchmark as ab

ENGINE = "http://127.0.0.1:8090/api/immune/permission"
TAG = f"{int(time.time())}"
WORKDIR = Path("/tmp/soulmate_accept")
PROBE_FILE = WORKDIR / f"plan_probe_{TAG}.py"
CONTROL_FILE = WORKDIR / f"plan_control_{TAG}.py"
EVIDENCE = Path("/tmp/plan_mode_evidence.json")

ev = {"tag": TAG, "checks": [], "e2e": {}, "pass": None}


def rec(name: str, ok: bool, detail: str = "") -> None:
    ev["checks"].append({"name": name, "ok": bool(ok), "detail": detail[:400]})
    print(f"{'✅' if ok else '❌'} {name}: {detail[:220]}", flush=True)


async def engine_check(client: httpx.AsyncClient, tool: str, args: dict) -> dict:
    r = await client.post(f"{ENGINE}/check", json={
        "tool_name": tool, "arguments": args,
        "session_id": f"p09-{TAG}", "working_dir": "/home/climbing",
    })
    r.raise_for_status()
    return r.json()


async def ws_turn(prompt_text: str) -> dict:
    """真实用户路径：WS → soulmate 真实 LLM 回合，返回 {reply, stop, elapsed}"""
    import websockets
    import jwt as pyjwt

    token = pyjwt.encode({"sub": f"p09-{TAG}"}, ab.JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    t0 = time.monotonic()
    async with websockets.connect(f"{ab.ACP_WS}?token={token}",
                                  max_size=20_000_000) as ws:
        await ab._rpc(ws, "initialize", {"protocolVersion": 1}, rid=1, timeout=30)
        r, _, _ = await ab._rpc(
            ws, "session/new",
            {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
            rid=2, timeout=90)
        sid = (r.get("result") or {}).get("session_id") or \
              (r.get("result") or {}).get("sessionId") or ""
        r, reply, _ = await ab._rpc(
            ws, "session/prompt",
            {"sessionId": sid, "messageId": f"p09-{TAG}",
             "prompt": [{"type": "text", "text": prompt_text}]},
            rid=3, timeout=320, collect_sid=sid)
    return {
        "reply": reply[-1200:],
        "stop": (r.get("result") or {}).get("stopReason", "-"),
        "elapsed": round(time.monotonic() - t0, 1),
        "sid": sid,
    }


async def main() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=10.0) as client:
        # ---- 基线 ----
        r = await client.get(f"{ENGINE}/mode")
        base_mode = (r.json() or {}).get("mode", "")
        ev["baseline_mode"] = base_mode
        print(f"baseline mode = {base_mode}", flush=True)
        rec("baseline: 探针文件不存在", not PROBE_FILE.exists(), str(PROBE_FILE))
        rule_id = ""

        try:
            # ---- 进入 Plan(explore) 模式 ----
            r = await client.put(f"{ENGINE}/mode", json={"mode": "explore"})
            r.raise_for_status()
            r = await client.get(f"{ENGINE}/mode")
            rec("进入 explore 模式", (r.json() or {}).get("mode") == "explore",
                json.dumps(r.json(), ensure_ascii=False))

            # ---- 语义层：write_file 禁写 ----
            d = await engine_check(client, "write_file", {"path": str(PROBE_FILE), "content": "print(1)\n"})
            ok = d.get("behavior") == "deny" and "read-only" in (
                d.get("decision_reason", "") + d.get("message", ""))
            rec("explore: write_file DENY", ok, json.dumps(d, ensure_ascii=False)[:300])
            ev["write_deny_decision"] = d

            # ---- 语义层：allow 规则不可授予修改 ----
            rr = await client.post(f"{ENGINE}/rules", json={
                "tool_name": "write_file", "rule_content": "",
                "behavior": "allow", "source": "p09-probe",
            })
            rule_id = (rr.json() or {}).get("rule_id", "")
            d2 = await engine_check(client, "write_file", {"path": str(PROBE_FILE), "content": "print(1)\n"})
            ok = d2.get("behavior") == "deny"
            rec("explore: allow 规则不可覆盖禁写", ok, json.dumps(d2, ensure_ascii=False)[:300])

            # ---- 语义层：只读不受限 ----
            d3 = await engine_check(client, "read_file", {"path": "/etc/hostname"})
            rec("explore: read_file ALLOW", d3.get("behavior") == "allow",
                json.dumps(d3, ensure_ascii=False)[:300])
            d4 = await engine_check(client, "web_search", {"query": "test"})
            rec("explore: web_search ALLOW", d4.get("behavior") == "allow",
                json.dumps(d4, ensure_ascii=False)[:300])

            # ---- 真实用户路径：explore 拦截 ----
            prompt_block = (
                "用 write_file 工具创建文件 "
                f"{PROBE_FILE}，内容为一行：print(1)。创建后告诉我结果。"
                f"[防缓存标记 P09B_{TAG}]"
            )
            t = await ws_turn(prompt_block)
            ev["e2e"]["explore_turn"] = t
            blocked_file_absent = not PROBE_FILE.exists()
            rec("e2e explore: 文件未被创建", blocked_file_absent, str(PROBE_FILE))
            reply_says_blocked = any(k in t["reply"] for k in (
                "PERMISSION DENIED", "被拦截", "权限", "拒绝", "read-only", "禁止"))
            rec("e2e explore: 回复报拦截", reply_says_blocked,
                f"stop={t['stop']} elapsed={t['elapsed']} reply=...{t['reply'][-300:]}")
        finally:
            # ---- 恒恢复基线模式（explore 全局态绝不残留）----
            r = await client.put(f"{ENGINE}/mode", json={"mode": base_mode or "bypass"})
            r.raise_for_status()
            r = await client.get(f"{ENGINE}/mode")
            ev["restored_mode"] = (r.json() or {}).get("mode", "")
            rec("恢复基线模式", ev["restored_mode"] == (base_mode or "bypass"),
                ev["restored_mode"])
            if rule_id:
                try:
                    await client.delete(f"{ENGINE}/rules/{rule_id}")
                except Exception as e:
                    print("rule cleanup warn:", e, flush=True)

        # ---- 对照组：bypass 下同样指令必须能写 ----
        prompt_write = (
            "用 write_file 工具创建文件 "
            f"{CONTROL_FILE}，内容为一行：print(1)。创建后告诉我结果。"
            f"[防缓存标记 P09C_{TAG}]"
        )
        t2 = await ws_turn(prompt_write)
        ev["e2e"]["control_turn"] = t2
        created = CONTROL_FILE.exists() and "print(1)" in CONTROL_FILE.read_text()
        rec("e2e 对照: bypass 下文件真实创建", created,
            f"stop={t2['stop']} elapsed={t2['elapsed']}")

    all_ok = all(c["ok"] for c in ev["checks"])
    ev["pass"] = all_ok
    EVIDENCE.write_text(json.dumps(ev, ensure_ascii=False, indent=2))
    print("\n=== P0-09 终验:", "PASS ✅" if all_ok else "FAIL ❌", "===")
    print("evidence:", EVIDENCE)
    # 清理对照产物
    if CONTROL_FILE.exists():
        CONTROL_FILE.unlink()
    if PROBE_FILE.exists():
        PROBE_FILE.unlink()


if __name__ == "__main__":
    asyncio.run(main())
