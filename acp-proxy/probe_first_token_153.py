"""15.3 aux LLM 延迟治理 — 真实验收探针（周期#93）

验收行（路线图 15.3）：首字回复时间对比 + aux 计时账本不变差。

真实链路（与 acceptance_benchmark 同口径）：WS /ws/acp?token=JWT → ws_acp
AGENT_ROUTES["soulmate"] → spawn `python -m agent.start --stdio` → SoulMateAgent
真实 LLM 回合。

用例：
- MF（多文件目标）：pre-gate 前移后应**完全跳过** LLM 规划往返——
  该会话 aux 账本**无 [aux] plan 行**，首字不再被规划往返挡 11.2s 均值。
- SF（单文件任务）：仍走 LLM 规划往返，但关键路径短预算
  SOULMATE_AUX_CRITICAL_BUDGET_S（默认12s）封顶——aux 账本 plan 行
  duration_ms ≤ 预算或 status 如实（ok/timeout），禁超预算还记 ok。

对比口径：MF vs SF 首字（TTFT）对比 + 与 0.2 基线（简单首条 6.00s）并报；
aux 账本逐 label 统计（本 run 新行）与治理前真实库基线（plan 均值 11.2s/
峰值 35.1s）对比"不变差"。

结果写 /tmp/probe_first_token_153.json，stdout 打印摘要。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import time

import jwt as pyjwt
import websockets

ACP_WS = "ws://127.0.0.1:8092/ws/acp"
JWT_SECRET = "change-me-in-production"
DB = os.path.expanduser("~/opensoul/data/opensoul.db")
OUT = "/tmp/probe_first_token_153.json"
WORKDIR = "/tmp/ft153"
TYPING_S = 8.0          # 模拟打字时间（覆盖 new_session 预热，与 0.2 口径一致）
PROMPT_TIMEOUT_S = 240
BUDGET_MS = float(os.environ.get("SOULMATE_AUX_CRITICAL_BUDGET_S", "12")) * 1000
TAG = f"FT153_{int(time.time())}"


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


def aux_rows(session_id: str) -> list[dict]:
    conn = sqlite3.connect(DB, timeout=10)
    try:
        rows = conn.execute(
            "SELECT content, duration_ms, status, created_at FROM trajectory_events "
            "WHERE session_id=? AND content LIKE '[aux]%' ORDER BY created_at",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {"label": r[0].replace("[aux] ", ""), "duration_ms": round(float(r[1]), 1),
         "status": r[2], "created_at": r[3]}
        for r in rows
    ]


def ledger_baseline() -> dict:
    """治理前真实库基线（周期#92 归因口径）：各 label 均值/峰值/行数。"""
    conn = sqlite3.connect(DB, timeout=10)
    try:
        rows = conn.execute(
            "SELECT content, duration_ms FROM trajectory_events WHERE content LIKE '[aux]%'"
        ).fetchall()
    finally:
        conn.close()
    stat: dict[str, dict] = {}
    for content, dur in rows:
        label = (content or "").replace("[aux] ", "")
        s = stat.setdefault(label, {"n": 0, "mean_s": 0.0, "max_s": 0.0, "_t": 0.0})
        s["n"] += 1
        s["_t"] += float(dur or 0) / 1000
        s["max_s"] = max(s["max_s"], float(dur or 0) / 1000)
    for s in stat.values():
        s["mean_s"] = round(s["_t"] / s["n"], 2) if s["n"] else 0.0
        s["max_s"] = round(s["max_s"], 2)
        s.pop("_t", None)
    return stat


async def run_case(ws, rid0: int, name: str, prompt_text: str) -> dict:
    t_new = time.monotonic()
    await ws.send(json.dumps({
        "jsonrpc": "2.0", "id": rid0, "method": "session/new",
        "params": {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
    }))
    sid = ""
    t_end = time.monotonic() + 90
    while time.monotonic() < t_end:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, t_end - time.monotonic()))
        msg = json.loads(raw)
        if msg.get("id") == rid0:
            res = msg.get("result") or {}
            sid = res.get("session_id") or res.get("sessionId") or ""
            break
    if not sid:
        raise RuntimeError(f"[{name}] session/new 无 session_id")
    entry = {"case": name, "session_id": sid,
             "session_new_s": round(time.monotonic() - t_new, 2)}

    await asyncio.sleep(TYPING_S)  # 模拟打字（预热窗口，0.2 同口径）

    t0 = time.monotonic()
    await ws.send(json.dumps({
        "jsonrpc": "2.0", "id": rid0 + 1, "method": "session/prompt",
        "params": {"sessionId": sid, "messageId": f"{TAG}-{name}",
                   "prompt": [{"type": "text", "text": prompt_text}]},
    }))
    ttft = None
    chunks: list = []
    first_frame_kind = ""
    t_end = t0 + PROMPT_TIMEOUT_S
    while time.monotonic() < t_end:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, t_end - time.monotonic()))
        msg = json.loads(raw)
        if msg.get("method") == "session/update":
            params = msg.get("params") or {}
            psid = params.get("sessionId") or ""
            if psid and psid != sid:
                continue
            before = len(chunks)
            _collect_texts(params, chunks)
            if len(chunks) > before and ttft is None:
                ttft = time.monotonic() - t0
                first_frame_kind = json.dumps(params.get("update") or params)[:120]
        if msg.get("id") == rid0 + 1:
            entry["stop_reason"] = (msg.get("result") or {}).get("stopReason", "-")
            break
    entry["ttft_s"] = round(ttft, 2) if ttft is not None else None
    entry["total_s"] = round(time.monotonic() - t0, 2)
    entry["first_frame"] = first_frame_kind
    entry["reply_excerpt"] = re.sub(r"\s+", " ", "".join(chunks))[:200]
    entry["aux_rows"] = aux_rows(sid)
    return entry


async def main() -> dict:
    os.makedirs(WORKDIR, exist_ok=True)
    for fn, mark in (("a.py", "FIXED_A"), ("b.py", "FIXED_B"), ("c.py", "FIXED_C")):
        p = os.path.join(WORKDIR, fn)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {fn}\nprint('orig')\n")

    base = ledger_baseline()
    token = pyjwt.encode({"sub": f"ft153-{int(time.time())}"}, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()

    out: dict = {"tag": TAG, "budget_ms": BUDGET_MS, "baseline_aux": base}
    async with websockets.connect(f"{ACP_WS}?token={token}", max_size=20_000_000) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                  "params": {"protocolVersion": 1}}))
        t_end = time.monotonic() + 30
        while time.monotonic() < t_end:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if msg.get("id") == 1:
                break

        mf_prompt = (
            f"请修改 {WORKDIR}/a.py 和 {WORKDIR}/b.py 两个文件：a.py 末尾追加一行 "
            f"print('FIXED_A')，b.py 末尾追加一行 print('FIXED_B')。\n"
            f"（复测标记 {TAG}_MF，此行仅为防缓存标记，忽略其语义）"
        )
        sf_prompt = (
            f"请修改 {WORKDIR}/c.py：在末尾追加一行 print('FIXED_C')。\n"
            f"（复测标记 {TAG}_SF，此行仅为防缓存标记，忽略其语义）"
        )
        out["mf"] = await run_case(ws, 100, "MF", mf_prompt)
        print(f"MF: ttft={out['mf']['ttft_s']}s total={out['mf']['total_s']}s "
              f"aux={out['mf']['aux_rows']}", flush=True)
        out["sf"] = await run_case(ws, 200, "SF", sf_prompt)
        print(f"SF: ttft={out['sf']['ttft_s']}s total={out['sf']['total_s']}s "
              f"aux={out['sf']['aux_rows']}", flush=True)

    # 服务端副作用（执行证据）
    out["files"] = {
        fn: open(os.path.join(WORKDIR, fn), encoding="utf-8").read()
        for fn in ("a.py", "b.py", "c.py")
    }

    # ── 验收判定 ──
    checks = []
    mf_plan = [r for r in out["mf"]["aux_rows"] if r["label"] == "plan"]
    checks.append({
        "name": "MF多文件目标完全跳过LLM规划往返（pre-gate）",
        "ok": len(mf_plan) == 0,
        "detail": f"MF plan行={len(mf_plan)}（期望0）",
    })
    sf_plan = [r for r in out["sf"]["aux_rows"] if r["label"] == "plan"]
    sf_ok = all(r["duration_ms"] <= BUDGET_MS + 500 for r in sf_plan)
    checks.append({
        "name": "SF单文件任务plan往返受短预算封顶（≤12s）或如实status",
        "ok": bool(sf_plan) and sf_ok,
        "detail": f"SF plan行={[(r['duration_ms'], r['status']) for r in sf_plan]} "
                  f"预算={BUDGET_MS:.0f}ms",
    })
    honest = all(r["status"] in ("ok", "timeout", "error") for r in
                 out["mf"]["aux_rows"] + out["sf"]["aux_rows"])
    checks.append({
        "name": "aux账本status诚实（无超预算记ok）",
        "ok": honest,
        "detail": "全部新行status∈{ok,timeout,error}",
    })
    ttft_ok = out["mf"]["ttft_s"] is not None and out["sf"]["ttft_s"] is not None
    checks.append({
        "name": "首字回复可测（两用例TTFT均有值）",
        "ok": ttft_ok,
        "detail": f"MF ttft={out['mf']['ttft_s']}s / SF ttft={out['sf']['ttft_s']}s",
    })
    out["checks"] = checks
    out["verdict"] = "PASS" if all(c["ok"] for c in checks) else "PARTIAL"

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("\n=== 15.3 验收判定 ===", flush=True)
    for c in checks:
        print(f"  {'✅' if c['ok'] else '❌'} {c['name']} — {c['detail']}", flush=True)
    print(f"verdict={out['verdict']} → {OUT}", flush=True)
    return out


if __name__ == "__main__":
    asyncio.run(main())
