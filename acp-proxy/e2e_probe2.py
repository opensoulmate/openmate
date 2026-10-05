#!/usr/bin/env python3
"""e2e探针2 — :8090/a2a 验不挂死

检查项（每步独立断言，任一超时即 FAIL=挂死）：
目标 = OpenSoul src/a2a/api.py（:8090 唯一 /a2a 实现）：
  methods: tasks/send（同步阻塞，25s内置预算）/ tasks/get / tasks/cancel
  metadata.llm_mode=stub → 确定性离线应答，不打真实LLM
1. GET  /a2a                  → 405 且秒回（端点存活）
2. POST /a2a 非法JSON          → -32700 秒回
3. POST /a2a 未知方法          → error(Method not found) 秒回
4. tasks/send llm_mode=stub   → ≤10s 返回 completed（stub链路不挂死）
5. tasks/get 回读              → 秒回且 task id 一致
6. tasks/send 真实链路         → ≤35s 到终态（25s预算+余量；completed/failed均算不挂死）
7. GET  /.well-known/agent.json → 秒回
全部通过 exit 0，任何挂死/超时 exit 1。断言全过后才写 /tmp/e2e_probe2_report.json。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8090"
FAST = 5.0        # 快速端点秒回上限
STUB_MAX = 10.0   # stub链路返回上限（llm_mode=stub 不打真实LLM）
REAL_MAX = 35.0   # 真实链路上限（task_manager 25s内置预算 + 余量）
results: list[dict] = []


def rpc(method: str, params: dict, timeout: float, req_id: int = 1) -> tuple[dict | None, float, str]:
    """POST JSON-RPC，返回 (resp_json, 耗时, 状态描述)。超时抛 TimeoutError 由调用方判定挂死。"""
    payload = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        f"{BASE}/a2a", data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
        return body, time.monotonic() - t0, f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}"), time.monotonic() - t0, f"HTTP {e.code}"


def http_get(path: str, timeout: float) -> tuple[int, float]:
    t0 = time.monotonic()
    req = urllib.request.Request(f"{BASE}{path}", method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            code = r.status
    except urllib.error.HTTPError as e:
        e.read()
        code = e.code
    return code, time.monotonic() - t0


def check(name: str, ok: bool, detail: str) -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def main() -> int:
    # 1. GET /a2a 存活（405 = 只收 POST，属预期）
    try:
        code, dt = http_get("/a2a", FAST)
        check("GET /a2a 存活", dt < FAST and code in (200, 405), f"HTTP {code}, {dt*1000:.0f}ms")
    except Exception as e:
        check("GET /a2a 存活", False, f"挂死/异常: {e!r}")

    # 2. 非法JSON → -32700
    try:
        t0 = time.monotonic()
        req = urllib.request.Request(f"{BASE}/a2a", data=b"{bad json", headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=FAST) as r:
                body = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = json.loads(e.read() or b"{}")
        dt = time.monotonic() - t0
        check("非法JSON→parse error", dt < FAST and body.get("error", {}).get("code") == -32700,
              f"{dt*1000:.0f}ms, error={body.get('error')}")
    except Exception as e:
        check("非法JSON→parse error", False, f"挂死/异常: {e!r}")

    # 3. 未知方法 → error(Method not found) 秒回
    try:
        body, dt, st = rpc("no/such/method", {}, FAST, req_id=2)
        err = (body or {}).get("error", {})
        check("未知方法→error秒回", dt < FAST and "Method not found" in err.get("message", ""),
              f"{dt*1000:.0f}ms, error={err}")
    except Exception as e:
        check("未知方法→error秒回", False, f"挂死/异常: {e!r}")

    # 4. tasks/send llm_mode=stub — 确定性链路 ≤10s completed（不打真实LLM）
    stub_task_id = None
    try:
        body, dt, st = rpc("tasks/send", {
            "message": {"role": "user", "parts": [{"type": "text", "text": "e2e探针2 ping"}]},
            "metadata": {"llm_mode": "stub"},
        }, STUB_MAX, req_id=3)
        res = (body or {}).get("result") or {}
        stub_task_id = res.get("id")
        state = (res.get("status") or {}).get("state")
        check("tasks/send stub→completed", bool(dt < STUB_MAX and state == "completed" and stub_task_id),
              f"{dt*1000:.0f}ms, id={stub_task_id}, state={state}")
    except Exception as e:
        check("tasks/send stub→completed", False, f"挂死/异常: {e!r}")

    # 5. tasks/get 回读 — 秒回且 task id 一致
    if stub_task_id:
        try:
            body, dt, st = rpc("tasks/get", {"id": stub_task_id}, FAST, req_id=4)
            res = (body or {}).get("result") or {}
            check("tasks/get 回读一致", dt < FAST and res.get("id") == stub_task_id,
                  f"{dt*1000:.0f}ms, id={res.get('id')}")
        except Exception as e:
            check("tasks/get 回读一致", False, f"挂死/异常: {e!r}")
    else:
        check("tasks/get 回读一致", False, "无 stub task id 可回读")

    # 6. tasks/send 真实链路 — ≤35s 到终态（25s内置预算内优雅收场即不挂死）
    try:
        body, dt, st = rpc("tasks/send", {
            "message": {"role": "user", "parts": [{"type": "text", "text": "e2e探针2：请只回复 pong"}]},
        }, REAL_MAX, req_id=5)
        res = (body or {}).get("result") or {}
        state = (res.get("status") or {}).get("state")
        check("tasks/send 真实链路到终态", dt < REAL_MAX and state in ("completed", "failed"),
              f"{dt*1000:.0f}ms, state={state}")
    except Exception as e:
        check("tasks/send 真实链路到终态", False, f"挂死/异常: {e!r}")

    # 7. GET /.well-known/agent.json 秒回
    try:
        code, dt = http_get("/.well-known/agent.json", FAST)
        check("GET /.well-known/agent.json 秒回", dt < FAST and code == 200, f"HTTP {code}, {dt*1000:.0f}ms")
    except Exception as e:
        check("GET /.well-known/agent.json 秒回", False, f"挂死/异常: {e!r}")

    # 断言全过才写盘
    all_ok = all(r["ok"] for r in results)
    if all_ok:
        with open("/tmp/e2e_probe2_report.json", "w") as f:
            json.dump({"probe": "e2e-probe2", "target": f"{BASE}/a2a", "all_ok": all_ok, "results": results}, f, ensure_ascii=False, indent=2)
        print(f"\nALL PASS ({len(results)}/{len(results)}) — 报告已写 /tmp/e2e_probe2_report.json")
        return 0
    print(f"\nFAIL ({sum(r['ok'] for r in results)}/{len(results)}) — 存在挂死/异常，报告不落盘")
    return 1


if __name__ == "__main__":
    sys.exit(main())
