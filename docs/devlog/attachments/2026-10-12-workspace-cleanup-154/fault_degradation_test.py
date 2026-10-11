#!/usr/bin/env python3
"""
故障降级测试（真实故障注入）
══════════════════════════════════════════════
与 systemic_test.py S5 的区别：S5 只做被动健康检查；
本套件真实注入故障（停依赖服务），验证降级契约：

  降级契约 = 依赖挂掉时
    ① 主链路（chat）不受阻塞，仍返回答案
    ② 依赖端点优雅失败（2xx空降级 / 503干净拒绝），在预算内返回，
       禁止未处理500、禁止挂起（超时=挂起=违约）
       搜索端点预算 = embedding 12s + 余量 = 20s
    ③ 其他模块（权限/作业队列）不受级联影响
    ④ 故障恢复后自动回到正常行为
    ⑤ 依赖服务自身优雅停机 < 10s（运维契约）

用法: /home/climbing/opensoul/.venv/bin/python fault_degradation_test.py
结果: fault_degradation_results.json
"""
import asyncio
import json
import subprocess
import sys
import time

import httpx

OPENVSOUL = "http://localhost:8090"
ACPA = "http://localhost:8092"
MEILI = "http://localhost:7700"
UID = "00000000-0000-0000-0000-000000000001"

RESULTS = []


def record(suite, name, passed, detail, latency):
    RESULTS.append({
        "suite": suite, "name": name, "passed": bool(passed),
        "detail": str(detail)[:300], "latency_s": round(latency, 2),
    })
    mark = "✅" if passed else "❌"
    print(f"  {mark} [{suite}] {name}: {str(detail)[:160]} ({latency:.1f}s)", flush=True)


def svc(action, unit, timeout=90):
    t0 = time.monotonic()
    try:
        r = subprocess.run(["systemctl", "--user", action, unit],
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, time.monotonic() - t0
    except subprocess.TimeoutExpired:
        return False, time.monotonic() - t0


def svc_active(unit):
    r = subprocess.run(["systemctl", "--user", "is-active", unit],
                       capture_output=True, text=True, timeout=15)
    return r.stdout.strip() == "active"


def svc_recover(unit):
    """恢复服务：reset-failed（stop超时会留failed态）+ start"""
    subprocess.run(["systemctl", "--user", "reset-failed", unit],
                   capture_output=True, text=True, timeout=15)
    ok, _ = svc("start", unit)
    return ok


async def wait_ready(client, url, timeout=30):
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        try:
            r = await client.get(url, timeout=3)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(1)
    return False


async def chat_ok(client, q="简短回答：1+1等于几"):
    """chat主链路必须200且有回答。返回 (ok, detail, latency)"""
    t0 = time.monotonic()
    try:
        r = await client.post(f"{OPENVSOUL}/api/chat/?user_id={UID}",
                              json={"question": q, "stream": False, "top_k": 1},
                              timeout=90)
        data = r.json()
        has_answer = bool(data.get("answer") or data.get("response") or data.get("content"))
        return (r.status_code == 200 and has_answer,
                f"status={r.status_code}, has_answer={has_answer}", time.monotonic() - t0)
    except Exception as e:
        return (False, f"{type(e).__name__}: {e}", time.monotonic() - t0)


async def graceful(client, method, url, body=None, deadline=5.0):
    """降级契约：预算内返回且不抛未处理500。超时（挂起）= 违约。"""
    t0 = time.monotonic()
    try:
        if method == "GET":
            r = await client.get(url, timeout=deadline)
        else:
            r = await client.post(url, json=body, timeout=deadline)
        latency = time.monotonic() - t0
        # 2xx=优雅空降级 / 4xx=干净拒绝 / 503=干净拒绝；5xx未处理 = 违约
        ok = r.status_code < 500 or r.status_code == 503
        return (ok, f"status={r.status_code}", latency)
    except httpx.TimeoutException:
        latency = time.monotonic() - t0
        return (False, f"HANG: {deadline}s内无响应(挂起)", latency)
    except Exception as e:
        latency = time.monotonic() - t0
        # 连接层快速失败（拒绝连接）= 干净的失败传播，合格
        return (True, f"{type(e).__name__}(fast-fail {latency:.1f}s)", latency)


async def main():
    stopped = []

    async with httpx.AsyncClient() as client:
        try:
            # ═══ P0 基线 ═══
            print("\n══ P0: 基线健康 ══")
            ok, detail, lat = await chat_ok(client)
            record("P0", "基线chat全链路", ok, detail, lat)
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=hybrid", deadline=20)
            record("P0", "基线hybrid搜索(预算20s)", ok and detail == "status=200", detail, lat)
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=fulltext", deadline=20)
            record("P0", "基线fulltext搜索(预算20s)", ok and detail == "status=200", detail, lat)
            record("P0", "依赖服务状态", True,
                   f"meili={svc_active('meilisearch.service')}, acp_a={svc_active('acp-proxy-a.service')}, "
                   f"acp_b={svc_active('acp-proxy-b.service')}, qdrant=6333未运行(天然故障态)", 0)

            # ═══ P1 天然故障态：qdrant已停（vector搜索降级） ═══
            print("\n══ P1: RAG/向量层故障（qdrant未运行）下的降级 ══")
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=semantic", deadline=20)
            record("P1", "semantic搜索降级(禁500/禁挂起)", ok, detail, lat)
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=hybrid", deadline=20)
            record("P1", "hybrid搜索降级(fulltext兜底)", ok, detail, lat)
            ok, detail, lat = await chat_ok(client, "简短回答：什么是知识库")
            record("P1", "chat不受向量层故障阻塞", ok, detail, lat)

            # ═══ P2 注入故障：meilisearch停机 ═══
            print("\n══ P2: 注入故障 — meilisearch停机 ══")
            ok, stop_lat = svc("stop", "meilisearch.service")
            stopped.append("meilisearch.service")
            record("P2", "运维契约: meilisearch优雅停机<10s",
                   ok and stop_lat < 10 and not svc_active("meilisearch.service"),
                   f"stopped={ok}, took={stop_lat:.1f}s", stop_lat)

            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=fulltext", deadline=20)
            record("P2", "fulltext搜索降级(禁500/禁挂起)", ok, detail, lat)
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=hybrid", deadline=20)
            record("P2", "hybrid搜索降级", ok, detail, lat)
            ok, detail, lat = await chat_ok(client)
            record("P2", "chat不受搜索层故障阻塞", ok, detail, lat)

            # 2b 降级期间的跨模块并发（协同×故障×并发）
            t0 = time.monotonic()
            tasks = []
            for _ in range(3):
                tasks.append(graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=x&mode=fulltext", deadline=20))
            for _ in range(4):
                tasks.append(graceful(client, "POST", f"{OPENVSOUL}/api/immune/permission/check",
                                      {"tool_name": "read_file", "tool_input": {"path": "/tmp/t"}}))
            for i in range(3):
                tasks.append(graceful(client, "POST", f"{OPENVSOUL}/api/will/jobs/submit",
                                      {"name": "fault_test", "params": {"i": i}, "source": "test"}, deadline=10))
            tasks.append(graceful(client, "GET", f"{OPENVSOUL}/api/will/jobs/health"))
            res = await asyncio.gather(*tasks)
            latency = time.monotonic() - t0
            okn = sum(1 for r, _, _ in res if r)
            fails = [f"{d}" for r, d, _ in res if not r]
            record("P2", "故障期间跨模块并发11路(搜索x3+权限x4+作业x3+队列健康)",
                   okn >= 10 and latency < 40,
                   f"ok={okn}/11, total={latency:.1f}s, fails={fails[:3]}", latency)

            # 恢复：等meili自身健康（7700/health），不是opensoul的静态health
            svc("start", "meilisearch.service")
            ready = await wait_ready(client, f"{MEILI}/health", timeout=30)
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=fulltext", deadline=20)
            record("P2", "meilisearch恢复后fulltext复原",
                   ready and detail == "status=200", f"meili_ready={ready}, {detail}", lat)

            # ═══ P3 注入故障：acp-proxy-a停机 ═══
            print("\n══ P3: 注入故障 — acp-proxy-a停机（跨服务隔离） ══")
            ok, stop_lat = svc("stop", "acp-proxy-a.service", timeout=90)
            stopped.append("acp-proxy-a.service")
            record("P3", "运维契约: acp-proxy优雅停机<10s",
                   ok and stop_lat < 10 and not svc_active("acp-proxy-a.service"),
                   f"stopped={ok}, took={stop_lat:.1f}s"
                   + ("" if ok and stop_lat < 10 else " (已知问题: uvicorn后台任务不退出→30s SIGKILL→failed)"),
                   stop_lat)

            ok, detail, lat = await chat_ok(client, "简短回答：你好")
            record("P3", "opensoul chat不受acp-proxy故障影响", ok, detail, lat)
            ok, detail, lat = await graceful(client, "POST", f"{ACPA}/acp/send",
                                             {"text": "hi", "session_id": ""}, deadline=5)
            record("P3", "acp调用快速失败不挂起", ok, detail, lat)
            ok, detail, lat = await graceful(client, "GET", f"{ACPA}/health", deadline=5)
            record("P3", "acp health快速失败不挂起", ok, detail, lat)

            svc_recover("acp-proxy-a.service")
            ready = await wait_ready(client, f"{ACPA}/health", timeout=30)
            # ACP子进程冷启动约10s才running，轮询30s再判定（竞态保护）
            running = False
            t0 = time.monotonic()
            while time.monotonic() - t0 < 30:
                try:
                    r = await client.get(f"{ACPA}/acp/status", timeout=5)
                    if r.status_code == 200 and r.json().get("running"):
                        running = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)
            record("P3", "acp-proxy-a恢复后复原(running,30s内)",
                   ready and running,
                   f"ready={ready}, running={running}, warmup={time.monotonic()-t0:.0f}s", 0)

            # ═══ P4 作业队列压力（并发资源竞争） ═══
            print("\n══ P4: 作业队列并发压力 ══")
            t0 = time.monotonic()

            async def submit(i):
                try:
                    r = await client.post(f"{OPENVSOUL}/api/will/jobs/submit",
                                          json={"name": "stress_fault", "params": {"i": i}, "source": "test"},
                                          timeout=10)
                    return r.status_code in (200, 201, 202, 429)  # 接受或优雅拒绝都算合格
                except Exception:
                    return False

            res = await asyncio.gather(*[submit(i) for i in range(30)])
            latency = time.monotonic() - t0
            record("P4", "30并发作业提交(接受或优雅拒绝)", sum(res) >= 28,
                   f"ok={sum(res)}/30, total={latency:.1f}s", latency)
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/will/jobs/health")
            record("P4", "压力后队列健康", ok and detail == "status=200", detail, lat)

            # ═══ P5 恢复终验 ═══
            print("\n══ P5: 故障恢复终验 ══")
            organs = ["chat", "hippo", "immune", "trajectory", "search", "will/jobs"]
            healthy = 0
            for o in organs:
                try:
                    r = await client.get(f"{OPENVSOUL}/api/{o}/health", timeout=5)
                    healthy += (r.status_code == 200)
                except Exception:
                    pass
            record("P5", "全部器官健康", healthy == len(organs), f"healthy={healthy}/{len(organs)}", 0)
            record("P5", "依赖服务全部复原",
                   svc_active("meilisearch.service") and svc_active("acp-proxy-a.service")
                   and svc_active("acp-proxy-b.service") and svc_active("opensoul.service"),
                   f"meili={svc_active('meilisearch.service')}, acp_a={svc_active('acp-proxy-a.service')}, "
                   f"acp_b={svc_active('acp-proxy-b.service')}, opensoul={svc_active('opensoul.service')}", 0)
            ok, detail, lat = await chat_ok(client, "简短回答：测试结束说完成")
            record("P5", "终验chat全链路", ok, detail, lat)
            ok, detail, lat = await graceful(client, "GET", f"{OPENVSOUL}/api/search/?q=test&mode=hybrid", deadline=20)
            record("P5", "终验hybrid搜索", ok and detail == "status=200", detail, lat)

        finally:
            # 兜底恢复所有被停的服务（含reset-failed）+ 落盘已有结果
            for u in set(stopped):
                if not svc_active(u):
                    svc_recover(u)
                    print(f"  [recovery] restarted {u}", flush=True)
            total = len(RESULTS)
            passed = sum(1 for r in RESULTS if r["passed"])
            with open("fault_degradation_results.json", "w") as f:
                json.dump({"summary": {"total": total, "passed": passed, "failed": total - passed},
                           "results": RESULTS}, f, ensure_ascii=False, indent=2)

    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r["passed"])
    print(f"\n════ 结果: {passed}/{total} 通过, {total-passed} 失败 ════")
    for r in RESULTS:
        if not r["passed"]:
            print(f"  ❌ {r['suite']} {r['name']}: {r['detail']}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
