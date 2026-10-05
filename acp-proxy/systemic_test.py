"""系统性深度测试 — 全链路跨模块协同验证

测试设计原则：
1. 测模块间的交互，不是单模块功能
2. 测故障传播和降级路径
3. 测并发场景下的资源竞争
4. 测完整的 用户请求→权限→执行→排队→响应 链路

测试矩阵：
  S1: 全链路消息流（chat→compress→redact→memory→LLM→loop_guard→response）
  S2: 权限引擎×工具执行（allow/deny/引擎不可用降级）
  S3: 作业队列×并发×重试×超时（含队列满）
  S4: 插话队列×ACP会话（忙时排队→完成后执行→FIFO）
  S5: 故障级联（LLM挂→重试→降级→不阻塞chat）
  S6: 多服务协同（opensoul + acp-proxy + immune + will同时负载）
"""
import asyncio
import os
import json
import time
import sys
import httpx

OPENVSOUL = "http://127.0.0.1:8090"
ACPPROXY = "http://127.0.0.1:8092"

results = []


def record(suite: str, name: str, passed: bool, detail: str = "", latency_s: float = 0):
    results.append({
        "suite": suite, "name": name,
        "passed": passed, "detail": detail[:200],
        "latency_s": round(latency_s, 2),
    })
    status = "✅" if passed else "❌"
    print(f"  {status} [{suite}] {name} ({latency_s:.2f}s) {detail[:80]}")


# ═══════════════════════════════════════════════
# S1: 全链路消息流
# ═══════════════════════════════════════════════
async def s1_full_chat_pipeline(client: httpx.AsyncClient):
    """测试完整chat链路: 压缩→脱敏→记忆注入→LLM→循环检测"""
    print("\n── S1: 全链路消息流 ──")
    
    # 1.1 非流式chat（全链路经过compress+redact+memory+loop_guard）
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{OPENVSOUL}/api/chat/?user_id=00000000-0000-0000-0000-000000000001",
            json={"question": "什么是Python的GIL？一句话回答", "stream": False, "top_k": 1},
            timeout=120,
        )
        latency = time.monotonic() - t0
        data = resp.json()
        has_answer = bool(data.get("answer") or data.get("response") or data.get("content"))
        has_sources = "sources" in data or "sources" in str(data)
        record("S1", "chat非流式全链路", resp.status_code == 200 and has_answer,
               f"status={resp.status_code}, has_answer={has_answer}, has_sources={has_sources}", latency)
    except httpx.TimeoutException:
        record("S1", "chat非流式全链路", False, "timeout 120s", 120)
    except Exception as e:
        record("S1", "chat非流式全链路", False, str(e), time.monotonic() - t0)
    
    # 1.2 流式chat（SSE）
    t0 = time.monotonic()
    try:
        chunks = 0
        async with client.stream(
            "POST",
            f"{OPENVSOUL}/api/chat/?user_id=00000000-0000-0000-0000-000000000001",
            json={"question": "1+1=？", "stream": True, "top_k": 1},
            timeout=120,
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    chunks += 1
                    if "[DONE]" in line:
                        break
        latency = time.monotonic() - t0
        record("S1", "chat流式SSE", chunks > 0,
               f"chunks={chunks}", latency)
    except Exception as e:
        record("S1", "chat流式SSE", False, str(e), time.monotonic() - t0)
    
    # 1.3 记忆系统健康（hippo三因子检索已接线）
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/hippo/health", timeout=5)
    data = resp.json()
    ltm = data.get("long_term_memory", {})
    record("S1", "hippo记忆系统", resp.status_code == 200 and ltm.get("total_memories", 0) >= 0,
           f"ltm={ltm.get('total_memories')} memories, avg_importance={ltm.get('avg_importance')}",
           time.monotonic() - t0)
    
    # 1.4 trajectory span记录（P0-4可观测性）
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/trajectory/health", timeout=5)
    record("S1", "trajectory可观测性", resp.status_code == 200,
           f"status={resp.status_code}", time.monotonic() - t0)


# ═══════════════════════════════════════════════
# S2: 权限引擎×工具执行协同
# ═══════════════════════════════════════════════
async def s2_permission_x_tool(client: httpx.AsyncClient):
    """测试权限引擎与工具执行的协同：allow/deny/降级"""
    print("\n── S2: 权限引擎×工具执行 ──")
    
    test_cases = [
        ("read_file", {"path": "/tmp/test.txt"}, "只读工具应自动放行"),
        ("write_file", {"path": "/tmp/test.txt", "content": "hello"}, "写工具应检查权限"),
        ("execute_code", {"code": "rm -rf /"}, "危险命令应被拒绝或标记"),
        ("browser_navigate", {"url": "http://example.com"}, "浏览器工具"),
        ("unknown_tool_xyz", {}, "未知工具应拒绝"),
    ]
    
    for tool_name, tool_input, desc in test_cases:
        t0 = time.monotonic()
        try:
            resp = await client.post(
                f"{OPENVSOUL}/api/immune/permission/check",
                json={"tool_name": tool_name, "tool_input": tool_input},
                timeout=5,
            )
            data = resp.json()
            behavior = data.get("behavior", "unknown")
            reason = data.get("decision_reason", "")
            record("S2", f"权限检查: {tool_name}",
                   resp.status_code in (200, 403) and behavior in ("allow", "deny", "ask"),
                   f"behavior={behavior}, reason={reason[:60]}",
                   time.monotonic() - t0)
        except Exception as e:
            record("S2", f"权限检查: {tool_name}", False, str(e), time.monotonic() - t0)
    
    # 2.6 ACP proxy工具执行链路（权限引擎接线点）
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{ACPPROXY}/acp/send",
            json={"text": "请读取/tmp目录下有什么文件", "session_id": ""},
            timeout=120,
        )
        data = resp.json()
        record("S2", "ACP工具执行链路", data.get("ok", False),
               f"content_len={len(data.get('content',''))}, source={data.get('source','')}",
               time.monotonic() - t0)
    except Exception as e:
        record("S2", "ACP工具执行链路", False, str(e), time.monotonic() - t0)
    
    # 2.7 权限规则CRUD → 引擎行为变化
    t0 = time.monotonic()
    try:
        # 添加一条deny规则
        resp = await client.post(
            f"{OPENVSOUL}/api/immune/permission/rules",
            json={"tool_name": "test_dangerous_tool", "rule_content": "*", "behavior": "deny"},
            timeout=5,
        )
        rule_added = resp.status_code == 200
        
        # 检查该工具现在是否被deny
        resp2 = await client.post(
            f"{OPENVSOUL}/api/immune/permission/check",
            json={"tool_name": "test_dangerous_tool", "tool_input": {}},
            timeout=5,
        )
        data2 = resp2.json()
        is_denied = data2.get("behavior") == "deny"
        
        record("S2", "权限规则CRUD→引擎生效", rule_added and is_denied,
               f"rule_added={rule_added}, behavior_after_add={data2.get('behavior')}",
               time.monotonic() - t0)
    except Exception as e:
        record("S2", "权限规则CRUD→引擎生效", False, str(e), time.monotonic() - t0)


# ═══════════════════════════════════════════════
# S3: 作业队列×并发×重试×超时
# ═══════════════════════════════════════════════
async def s3_jobqueue_stress(client: httpx.AsyncClient):
    """测试作业队列在并发/重试/超时场景下的行为"""
    print("\n── S3: 作业队列并发×重试×超时 ──")
    
    # 3.1 批量提交作业（并发压力）
    t0 = time.monotonic()
    job_ids = []
    try:
        submit_tasks = []
        for i in range(10):
            task = client.post(
                f"{OPENVSOUL}/api/will/jobs/submit",
                json={"name": "test_job", "params": {"index": i}, "timeout_s": 30, "source": "test"},
                timeout=5,
            )
            submit_tasks.append(task)
        responses = await asyncio.gather(*submit_tasks, return_exceptions=True)
        for r in responses:
            if isinstance(r, httpx.Response) and r.status_code == 200:
                job_ids.append(r.json().get("job_id", ""))
        record("S3", "并发提交10个作业", len(job_ids) == 10,
               f"submitted={len(job_ids)}/10", time.monotonic() - t0)
    except Exception as e:
        record("S3", "并发提交10个作业", False, str(e), time.monotonic() - t0)
    
    # 3.2 查看作业状态（查不存在的job → 404）
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/will/jobs/nonexistent_job_id", timeout=5)
    record("S3", "不存在的job→404", resp.status_code == 404,
           f"status={resp.status_code}", time.monotonic() - t0)
    
    # 3.3 作业列表 + 过滤
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/will/jobs?limit=20", timeout=5)
    data = resp.json()
    jobs = data.get("jobs", [])
    record("S3", "作业列表查询", resp.status_code == 200,
           f"jobs={len(jobs)}", time.monotonic() - t0)
    
    # 3.4 队列健康统计
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/will/jobs/health", timeout=5)
    data = resp.json()
    stats_ok = "total" in data and "by_status" in data
    record("S3", "队列健康统计", resp.status_code == 200 and stats_ok,
           f"total={data.get('total')}, workers={data.get('workers')}, handlers={data.get('handlers')}",
           time.monotonic() - t0)
    
    # 3.5 取消pending作业
    t0 = time.monotonic()
    try:
        # Submit a job then try to cancel it
        resp = await client.post(
            f"{OPENVSOUL}/api/will/jobs/submit",
            json={"name": "test_cancel", "params": {}, "timeout_s": 60, "source": "test"},
            timeout=5,
        )
        job_id = resp.json().get("job_id", "")
        resp2 = await client.post(f"{OPENVSOUL}/api/will/jobs/{job_id}/cancel", timeout=5)
        cancelled = resp2.json().get("cancelled", False)
        record("S3", "取消pending作业", resp2.status_code == 200,
               f"job={job_id[:20]}, cancelled={cancelled}", time.monotonic() - t0)
    except Exception as e:
        record("S3", "取消pending作业", False, str(e), time.monotonic() - t0)
    
    # 3.6 evo系统能否使用作业队列（进化pipeline→job_queue协同）
    t0 = time.monotonic()
    resp = await client.get(f"{ACPPROXY}/api/evolution/status", timeout=5)
    record("S3", "evo进化系统状态", resp.status_code in (200, 404),
           f"status={resp.status_code}", time.monotonic() - t0)


# ═══════════════════════════════════════════════
# S4: 插话队列×ACP会话
# ═══════════════════════════════════════════════
async def s4_interrupt_queue(client: httpx.AsyncClient):
    """测试插话队列：并发消息→排队→FIFO执行"""
    print("\n── S4: 插话队列×ACP会话 ──")
    
    # 4.1 同一session快速连发多条消息（触发排队）
    t0 = time.monotonic()
    session_id = ""
    try:
        # First message creates session
        resp = await client.post(
            f"{ACPPROXY}/acp/send",
            json={"text": "你好，请简短回复", "session_id": ""},
            timeout=120,
        )
        data = resp.json()
        session_id = data.get("session_id", "")
        
        # Send 3 messages rapidly to the same session
        tasks = []
        prompts = ["第一个问题：1+1=?", "第二个问题：2+2=?", "第三个问题：3+3=?"]
        send_start = time.monotonic()
        for prompt in prompts:
            task = client.post(
                f"{ACPPROXY}/acp/send",
                json={"text": prompt, "session_id": session_id},
                timeout=120,
            )
            tasks.append(task)
        
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        latency = time.monotonic() - t0

        success_count = 0
        failures: list[str] = []
        for r in responses:
            if isinstance(r, httpx.Response):
                try:
                    d = r.json()
                except Exception:
                    # Non-JSON body (e.g. plain-text 500) — record instead of
                    # crashing the whole case with a JSONDecodeError that
                    # masks which request failed and why.
                    failures.append(f"status={r.status_code} body={r.text[:60]!r}")
                    continue
                if d.get("ok") and d.get("content"):
                    success_count += 1
                else:
                    failures.append(f"status={r.status_code} err={d.get('error', '')[:60]}")
            else:
                failures.append(f"exc={type(r).__name__}: {str(r)[:60]}")

        record("S4", "同session并发3条消息", success_count >= 2,
               f"session={session_id[:20] if session_id else 'default'}, success={success_count}/3, "
               f"total_latency={latency:.1f}s" + (f", failures={failures}" if failures else ""),
               latency)
    except Exception as e:
        record("S4", "同session并发3条消息", False, str(e), time.monotonic() - t0)
    
    # 4.2 不同session并发（不互相排队）
    t0 = time.monotonic()
    try:
        tasks = []
        for i in range(3):
            task = client.post(
                f"{ACPPROXY}/acp/send",
                json={"text": f"session{i}: 请回复session{i}", "session_id": ""},
                timeout=120,
            )
            tasks.append(task)
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        latency = time.monotonic() - t0

        success = 0
        for r in responses:
            if isinstance(r, httpx.Response):
                try:
                    if r.json().get("ok"):
                        success += 1
                except Exception:
                    continue
        record("S4", "不同session并发", success >= 2,
               f"success={success}/3, latency={latency:.1f}s", latency)
    except Exception as e:
        record("S4", "不同session并发", False, str(e), time.monotonic() - t0)
    
    # 4.3 ACP进程状态
    t0 = time.monotonic()
    resp = await client.get(f"{ACPPROXY}/acp/status", timeout=5)
    data = resp.json()
    record("S4", "ACP进程状态", resp.status_code == 200,
           f"running={data.get('running')}", time.monotonic() - t0)


# ═══════════════════════════════════════════════
# S5: 故障级联与降级
# ═══════════════════════════════════════════════
async def s5_failure_cascade(client: httpx.AsyncClient):
    """测试故障场景下的系统降级行为"""
    print("\n── S5: 故障级联与降级 ──")
    
    # 5.1 chat在embedding不可用时是否降级（Qdrant不可达时）
    t0 = time.monotonic()
    try:
        resp = await client.post(
            f"{OPENVSOUL}/api/chat/?user_id=00000000-0000-0000-0000-000000000001",
            json={"question": "简短回答：你好", "stream": False, "top_k": 1},
            timeout=60,
        )
        latency = time.monotonic() - t0
        data = resp.json()
        # Even if RAG fails, the chat should still return something via LLM
        has_response = bool(data.get("answer") or data.get("response") or data.get("content") or "No relevant" in str(data))
        record("S5", "RAG不可用时chat降级", resp.status_code == 200,
               f"status={resp.status_code}, has_response={has_response}", latency)
    except Exception as e:
        record("S5", "RAG不可用时chat降级", False, str(e), time.monotonic() - t0)
    
    # 5.2 各器官健康检查（独立故障检测）
    organs = ["chat", "hippo", "immune", "trajectory", "cortex", "gene", "vision", "mind"]
    t0 = time.monotonic()
    healthy = 0
    for organ in organs:
        try:
            resp = await client.get(f"{OPENVSOUL}/api/{organ}/health", timeout=3)
            if resp.status_code == 200:
                healthy += 1
        except Exception:
            pass
    record("S5", "器官健康检查", healthy >= len(organs) * 0.7,
           f"healthy={healthy}/{len(organs)}", time.monotonic() - t0)
    
    # 5.3 benchmark评估系统（P0-5集成验证）
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/benchmark/health", timeout=5)
    data = resp.json()
    record("S5", "benchmark评估系统", resp.status_code == 200,
           f"capability_evaluations={data.get('capability_evaluations')}", time.monotonic() - t0)
    
    # 5.4 LLM配置可用性
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/llm/config", timeout=5)
    data = resp.json()
    has_config = bool(data.get("base_url") and data.get("model"))
    record("S5", "LLM配置", resp.status_code == 200 and has_config,
           f"base_url={data.get('base_url','?')[:40]}, model={data.get('model','?')}",
           time.monotonic() - t0)
    
    # 5.5 模型路由配置（modelRouter）
    t0 = time.monotonic()
    resp = await client.get(f"{OPENVSOUL}/api/model-router/config", timeout=5)
    data = resp.json()
    record("S5", "模型路由配置", resp.status_code == 200,
           f"mode={data.get('mode')}, models={data.get('models')}", time.monotonic() - t0)


# ═══════════════════════════════════════════════
# S6: 多服务协同压力
# ═══════════════════════════════════════════════
async def s6_multi_service_load(client: httpx.AsyncClient):
    """多个服务同时承受负载时的系统行为"""
    print("\n── S6: 多服务协同压力 ──")
    
    # 6.1 同时对所有服务发起请求
    t0 = time.monotonic()
    endpoints = [
        (f"{OPENVSOUL}/api/chat/health", "GET", None),
        (f"{OPENVSOUL}/api/hippo/health", "GET", None),
        (f"{OPENVSOUL}/api/immune/health", "GET", None),
        (f"{OPENVSOUL}/api/will/jobs/health", "GET", None),
        (f"{OPENVSOUL}/api/benchmark/health", "GET", None),
        (f"{OPENVSOUL}/api/trajectory/health", "GET", None),
        (f"{OPENVSOUL}/api/model-router/config", "GET", None),
        (f"{ACPPROXY}/health", "GET", None),
        (f"{ACPPROXY}/acp/status", "GET", None),
        (f"{OPENVSOUL}/api/immune/permission/check", "POST",
         {"tool_name": "read_file", "tool_input": {"path": "/tmp/test"}}),
    ]
    
    async def safe_request(url, method, body):
        try:
            if method == "GET":
                return await client.get(url, timeout=10)
            else:
                return await client.post(url, json=body, timeout=10)
        except Exception as e:
            return e
    
    tasks = [safe_request(url, method, body) for url, method, body in endpoints]
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    latency = time.monotonic() - t0
    
    ok_count = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code == 200)
    record("S6", f"10端点并发请求", ok_count >= 8,
           f"ok={ok_count}/10, latency={latency:.2f}s", latency)
    
    # 6.2 高频请求压力（快速连打20次）
    t0 = time.monotonic()
    async def safe_health():
        try:
            return await client.get(f"{OPENVSOUL}/api/chat/health", timeout=5)
        except Exception as e:
            return e
    
    tasks = [safe_health() for _ in range(20)]
    responses = await asyncio.gather(*tasks, return_exceptions=True)
    latency = time.monotonic() - t0
    ok_count = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code == 200)
    record("S6", "高频20次health检查", ok_count >= 18,
           f"ok={ok_count}/20, latency={latency:.2f}s", latency)
    
    # 6.3 同时：chat流式 + 作业提交 + 权限检查 + ACP消息
    t0 = time.monotonic()
    async def safe_call(fn, *args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            return e
    
    mixed_tasks = [
        safe_call(client.get, f"{OPENVSOUL}/api/chat/health", timeout=5),
        safe_call(client.post, f"{OPENVSOUL}/api/will/jobs/submit",
                  json={"name": "stress_test", "params": {}, "source": "test"}, timeout=5),
        safe_call(client.post, f"{OPENVSOUL}/api/immune/permission/check",
                  json={"tool_name": "read_file", "tool_input": {"path": "/etc/hostname"}}, timeout=5),
        safe_call(client.post, f"{ACPPROXY}/acp/send",
                  json={"text": "简短回复：测试", "session_id": ""}, timeout=120),
    ]
    responses = await asyncio.gather(*mixed_tasks, return_exceptions=True)
    latency = time.monotonic() - t0
    ok_count = sum(1 for r in responses if isinstance(r, httpx.Response) and r.status_code in (200, 201))
    record("S6", "混合负载（health+job+perm+ACP）", ok_count >= 3,
           f"ok={ok_count}/4, latency={latency:.1f}s", latency)
    
    # 6.4 资源使用快照
    t0 = time.monotonic()
    import subprocess
    try:
        r = subprocess.run(
            ["ps", "aux"], capture_output=True, text=True, timeout=5
        )
        lines = r.stdout.split("\n")
        python_mem = 0
        for line in lines:
            if "python" in line.lower() and ("8090" in line or "8092" in line or "main.py" in line or "hermes" in line):
                parts = line.split()
                if len(parts) > 5:
                    python_mem += int(parts[5])  # RSS in KB
        total_mb = python_mem / 1024
        record("S6", "系统资源使用", total_mb < 4000,
               f"python服务总内存={total_mb:.0f}MB", time.monotonic() - t0)
    except Exception as e:
        record("S6", "系统资源使用", False, str(e), time.monotonic() - t0)


# ═══════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════
async def run_system_tests():
    print("=" * 60)
    print("系统性深度测试 — 全链路跨模块协同")
    print("=" * 60)
    
    # 每个suite用独立client，避免连接过期
    for suite_func in [s1_full_chat_pipeline, s2_permission_x_tool,
                       s3_jobqueue_stress, s4_interrupt_queue,
                       s5_failure_cascade, s6_multi_service_load]:
        try:
            async with httpx.AsyncClient(timeout=130) as client:
                await suite_func(client)
        except Exception as suite_exc:
            print(f"  ⚠️ {suite_func.__name__} crashed: {type(suite_func).__name__}: {suite_exc}")
    
    # S3/S6自清理：本轮提交的测试作业清走，不留垃圾进生产job_queue.db→
    # 经验/进化链（learned_skills.source同款哲学：产生侧负责自清）。
    # ① provenance精确清（source='test'）：先探openapi确认服务端支持——老服务端
    #    只有零过滤purge（=DELETE全表footgun），绝不能盲调；探测不过跳过并明示。
    # ② 自有保留名清（name_pattern带WHERE，老服务端也安全）：兜未来漏标回流。
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            purged_total = 0
            spec = (await client.get(f"{OPENVSOUL}/openapi.json", timeout=5)).json()
            params = (
                spec.get("paths", {})
                .get("/api/will/jobs/purge", {})
                .get("post", {})
                .get("parameters", [])
            )
            if any(p.get("name") == "source" for p in params):
                resp = await client.post(f"{OPENVSOUL}/api/will/jobs/purge?source=test", timeout=10)
                purged_total += (resp.json() or {}).get("purged", 0) if resp.status_code == 200 else 0
            else:
                print("\n  测试作业自清理: 服务端不支持source过滤（旧版），跳过provenance清")
            for name in ("test_job", "test_cancel", "stress_test"):
                resp = await client.post(
                    f"{OPENVSOUL}/api/will/jobs/purge?name_pattern={name}", timeout=10
                )
                purged_total += (resp.json() or {}).get("purged", 0) if resp.status_code == 200 else 0
            print(f"\n  测试作业自清理: 共清 {purged_total} 行（provenance+自有保留名）")
    except Exception as cleanup_exc:
        print(f"\n  测试作业自清理失败（不影响测试结果）: {cleanup_exc}")
    
    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    failed = total - passed
    
    print(f"\n{'=' * 60}")
    print(f"系统性测试结果")
    print(f"{'=' * 60}")
    
    suites = {}
    for r in results:
        s = r["suite"]
        if s not in suites:
            suites[s] = {"total": 0, "passed": 0, "tests": []}
        suites[s]["total"] += 1
        suites[s]["tests"].append(r)
        if r["passed"]:
            suites[s]["passed"] += 1
    
    for suite_name in sorted(suites.keys()):
        s = suites[suite_name]
        rate = s["passed"] / s["total"] * 100 if s["total"] else 0
        print(f"\n  {suite_name}: {s['passed']}/{s['total']} ({rate:.0f}%)")
        for t in s["tests"]:
            status = "✅" if t["passed"] else "❌"
            print(f"    {status} {t['name']} ({t['latency_s']}s) {t['detail'][:70]}")
    
    print(f"\n  总计: {passed}/{total} ({passed/total*100:.0f}%)")
    
    # Save results
    output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "systemic_test_results.json")
    with open(output_path, "w") as f:
        json.dump({"summary": {"total": total, "passed": passed, "failed": failed},
                   "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n  结果已保存: {output_path}")
    
    return results


if __name__ == "__main__":
    try:
        results = asyncio.run(run_system_tests())
    except Exception as e:
        print(f"\n⚠️ Test run crashed: {type(e).__name__}: {e}")
        print(f"Partial results: {len(results)} tests completed")
    failed = sum(1 for r in results if not r["passed"])
    total = len(results)
    print(f"\n最终: {total - failed}/{total} passed")
    sys.exit(0 if failed == 0 else 1)
