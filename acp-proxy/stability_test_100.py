"""Soulmate会话稳定性测试 — 同一会话100轮对话

测试路径: WebSocket → /ws/acp → proxy → stdio → Soulmate Agent Engine
协议: ACP JSON-RPC 2.0 (initialize → session/new → session/prompt×100)

会话级provenance（jobs.source同款产生侧标注）：session/new 声明
`_meta {"source": "test"}`——落库行metadata带session_source，ExperienceCollector
整会话跳过，100轮稳定性探针不再污染生产experiences/learned_patterns。
轮数可用环境变量 STABILITY_ROUNDS 覆盖（smoke验证用 STABILITY_ROUNDS=1）。

记录每轮: success/fail, latency, response_len, error_type, chunk_count
"""
import asyncio
import json
import os
import time
import sys
import httpx
import jwt as pyjwt
import websockets

PROXY_URL = "http://127.0.0.1:8092"
OPENVSOUL_URL = "http://127.0.0.1:8090"
WS_URL = "ws://127.0.0.1:8092/ws/acp"
NUM_ROUNDS = int(os.environ.get("STABILITY_ROUNDS", "100"))
PROMPT_TIMEOUT = 120  # seconds per prompt

# Test prompts — varied to exercise different code paths
TEST_PROMPTS = [
    "你好，请用一句话介绍你自己",
    "1+1等于几？",
    "请列出3个Python列表去重的方法",
    "什么是快速排序？简要说明",
    "帮我写一个Python的hello world",
    "解释一下什么是RESTful API",
    "今天适合做什么？给个建议",
    "请用一句话总结什么是机器学习",
    "HTTP和HTTPS有什么区别？",
    "请推荐3本技术书籍",
    "什么是Docker？简短回答",
    "请解释什么是递归",
    "Python的GIL是什么？",
    "什么是微服务架构？",
    "请说明TCP三次握手",
    "什么是索引？数据库索引的作用",
    "解释一下什么是缓存穿透",
    "什么是CAP定理？",
    "请解释什么是死锁",
    "git rebase和merge有什么区别？",
]


async def get_token():
    """Login to opensoul to get JWT token"""
    async with httpx.AsyncClient() as client:
        # Try common endpoints
        for endpoint in ["/api/user/login", "/api/auth/login"]:
            try:
                resp = await client.post(
                    f"{OPENVSOUL_URL}{endpoint}",
                    data={"username": os.environ.get("STABILITY_TEST_USER", "admin"), "password": os.environ.get("STABILITY_TEST_PASSWORD", "")},
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    token = data.get("access_token") or data.get("token", "")
                    if token:
                        return token
            except Exception:
                continue
    # If login fails, try without token (some deployments don't require it)
    return ""


JWT_SECRET = "change-me-in-production"  # opensoul/.env JWT_SECRET（ws_acp同源读取，e2e_*探针同款）


async def send_prompt_and_wait(ws, prompt_id: int, text: str, session_id: str) -> dict:
    """Send one prompt and wait for the full response. Returns result dict."""
    start = time.monotonic()
    result = {
        "round": prompt_id,
        "prompt": text[:50],
        "success": False,
        "latency_s": 0,
        "response_len": 0,
        "chunk_count": 0,
        "error_type": None,
        "error_msg": "",
        "response_preview": "",
    }

    prompt_msg = {
        "jsonrpc": "2.0",
        "id": prompt_id + 100,  # unique id per round
        "method": "session/prompt",
        "params": {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": text}],
        },
    }

    try:
        await ws.send(json.dumps(prompt_msg))
    except Exception as e:
        result["error_type"] = "send_failed"
        result["error_msg"] = str(e)[:200]
        result["latency_s"] = round(time.monotonic() - start, 2)
        return result

    full_text = ""
    chunk_count = 0

    while True:
        try:
            resp = await asyncio.wait_for(ws.recv(), timeout=PROMPT_TIMEOUT)
            msg = json.loads(resp)

            # Streaming update notification
            if msg.get("method") == "session/update":
                update = msg.get("params", {}).get("update", {})
                su_type = update.get("sessionUpdate", "")
                content = update.get("content", {})
                delta = content.get("text", "")
                if delta:
                    full_text += delta
                    chunk_count += 1
                if update.get("last"):
                    break
                # Also check for "agent_message_chunk" type
                if su_type == "agent_message_chunk" and not delta:
                    chunk_count += 1
                continue

            # JSON-RPC response to our prompt
            if "result" in msg and msg.get("id") == prompt_msg["id"]:
                stop_reason = msg.get("result", {}).get("stopReason", "")
                if stop_reason and stop_reason not in ("end_turn", "stop"):
                    result["error_type"] = f"stop_reason:{stop_reason}"
                break

            # JSON-RPC error
            if "error" in msg:
                err = msg["error"]
                result["error_type"] = "rpc_error"
                result["error_msg"] = json.dumps(err, ensure_ascii=False)[:200]
                break

            # Other messages — keep reading
            continue

        except asyncio.TimeoutError:
            result["error_type"] = "timeout"
            result["error_msg"] = f"No response in {PROMPT_TIMEOUT}s"
            break
        except websockets.exceptions.ConnectionClosed as e:
            result["error_type"] = "connection_closed"
            result["error_msg"] = f"code={e.code} reason={e.reason}"[:200]
            break
        except json.JSONDecodeError as e:
            result["error_type"] = "json_decode_error"
            result["error_msg"] = str(e)[:200]
            break
        except Exception as e:
            result["error_type"] = f"unexpected:{type(e).__name__}"
            result["error_msg"] = str(e)[:200]
            break

    elapsed = round(time.monotonic() - start, 2)
    result["latency_s"] = elapsed
    result["response_len"] = len(full_text)
    result["chunk_count"] = chunk_count
    result["response_preview"] = full_text[:100]
    result["success"] = len(full_text) > 0 and result["error_type"] is None
    return result


async def run_test():
    """Main test: create session, run 100 rounds, collect results."""
    print("=" * 60)
    print("Soulmate 会话稳定性测试 — 同一会话100轮")
    print("=" * 60)

    # Step 1: Get token
    print("\n[1/4] 获取认证token...")
    token = await get_token()
    if token:
        print(f"  token: {token[:30]}...")
    else:
        # 登录端点不可用→自签JWT（ws_acp按opensoul/.env JWT_SECRET校验；
        # 裸连会被鉴权层断连——2026-10-06冒烟实锤，e2e_*探针同款自签）
        token = pyjwt.encode({"sub": "stability-test"}, JWT_SECRET, algorithm="HS256")
        if isinstance(token, bytes):
            token = token.decode()
        print("  ⚠️ 未获取到登录token，改用自签JWT")

    # Step 2: Connect WebSocket
    print("\n[2/4] 连接WebSocket /ws/acp...")
    uri = f"{WS_URL}?token={token}" if token else WS_URL
    results = []
    session_id = ""

    try:
        ws = await websockets.connect(uri, ping_timeout=60, max_size=10*1024*1024)
        print("  ✅ 已连接")

        # Step 3: Initialize + newSession
        print("\n[3/4] 初始化会话...")
        init_msg = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}
        await ws.send(json.dumps(init_msg))

        session_msg = {"jsonrpc": "2.0", "id": 2, "method": "session/new",
                       "params": {"cwd": "/", "mcpServers": [], "agent_id": "soulmate",
                                  "_meta": {"source": "test"}}}
        await ws.send(json.dumps(session_msg))

        # Read responses
        for _ in range(10):
            resp = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = json.loads(resp)
            if msg.get("id") == 1:
                print(f"  initialize: {json.dumps(msg.get('result', {}), ensure_ascii=False)[:100]}")
            if msg.get("id") == 2:
                result_data = msg.get("result", {})
                session_id = result_data.get("session_id", result_data.get("sessionId", ""))
                print(f"  session_id: {session_id}")
                break
            if "error" in msg:
                print(f"  ❌ 初始化错误: {msg['error']}")
                await ws.close()
                return []

        if not session_id:
            print("  ❌ 未获取到session_id")
            await ws.close()
            return []

        # Step 4: Run 100 rounds
        print(f"\n[4/4] 开始100轮对话测试 (session={session_id})...")
        for i in range(NUM_ROUNDS):
            prompt_text = TEST_PROMPTS[i % len(TEST_PROMPTS)]
            result = await send_prompt_and_wait(ws, i + 1, prompt_text, session_id)
            results.append(result)

            status = "✅" if result["success"] else f"❌ {result['error_type']}"
            print(f"  Round {i+1:3d}: {status} | {result['latency_s']:6.1f}s | {result['response_len']:4d} chars | {result['chunk_count']:3d} chunks | {result['prompt'][:30]}")

            # If connection closed, try to reconnect and resume
            if result["error_type"] == "connection_closed":
                print(f"  🔌 连接断开，尝试重连...")
                try:
                    await ws.close()
                except Exception:
                    pass
                try:
                    ws = await websockets.connect(uri, ping_timeout=60, max_size=10*1024*1024)
                    # Re-initialize
                    await ws.send(json.dumps(init_msg))
                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "session/new",
                                              "params": {"cwd": "/", "mcpServers": [], "agent_id": "soulmate",
                                                         "_meta": {"source": "test"}}}))
                    for _ in range(10):
                        resp = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(resp)
                        if msg.get("id") == 2:
                            new_sid = msg.get("result", {}).get("session_id", "")
                            if new_sid:
                                session_id = new_sid
                                print(f"  🔗 重连成功，新session: {session_id}")
                            break
                except Exception as e:
                    print(f"  ❌ 重连失败: {e}")
                    # Fill remaining rounds as failed
                    for j in range(i + 1, NUM_ROUNDS):
                        results.append({
                            "round": j + 1,
                            "prompt": "",
                            "success": False,
                            "latency_s": 0,
                            "response_len": 0,
                            "chunk_count": 0,
                            "error_type": "reconnect_failed",
                            "error_msg": str(e)[:200],
                            "response_preview": "",
                        })
                    break

        await ws.close()

    except Exception as e:
        print(f"\n❌ 测试异常: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()

    # Save results
    output_path = "/home/climbing/openmate/acp-proxy/stability_test_results.json"
    summary = analyze_results(results)
    with open(output_path, "w") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n{'=' * 60}")
    print(f"结果已保存: {output_path}")
    print_summary(summary)
    return results


def analyze_results(results: list[dict]) -> dict:
    """Analyze test results."""
    total = len(results)
    successes = [r for r in results if r["success"]]
    failures = [r for r in results if not r["success"]]

    # Group failures by error type
    error_types = {}
    for r in failures:
        et = r.get("error_type", "unknown")
        error_types[et] = error_types.get(et, 0) + 1

    # Latency stats (only successful)
    latencies = [r["latency_s"] for r in successes]
    avg_latency = sum(latencies) / len(latencies) if latencies else 0
    max_latency = max(latencies) if latencies else 0
    min_latency = min(latencies) if latencies else 0

    # Response length stats
    lengths = [r["response_len"] for r in successes]
    avg_len = sum(lengths) / len(lengths) if lengths else 0

    # Find failure rounds (for pattern analysis)
    failure_rounds = [r["round"] for r in failures]

    return {
        "total": total,
        "success": len(successes),
        "fail": len(failures),
        "success_rate": round(len(successes) / total * 100, 1) if total else 0,
        "error_types": error_types,
        "failure_rounds": failure_rounds,
        "latency_avg": round(avg_latency, 2),
        "latency_max": max_latency,
        "latency_min": min_latency,
        "response_len_avg": round(avg_len, 0),
    }


def print_summary(s: dict):
    """Print test summary."""
    print(f"\n{'=' * 60}")
    print("测试结果摘要")
    print(f"{'=' * 60}")
    print(f"  总轮次: {s['total']}")
    print(f"  成功: {s['success']} ({s['success_rate']}%)")
    print(f"  失败: {s['fail']}")
    print(f"  平均延迟: {s['latency_avg']}s")
    print(f"  延迟范围: {s['latency_min']}s ~ {s['latency_max']}s")
    print(f"  平均响应长度: {s['response_len_avg']} chars")
    if s["error_types"]:
        print(f"\n  错误类型分布:")
        for et, count in sorted(s["error_types"].items(), key=lambda x: -x[1]):
            print(f"    {et}: {count}次")
    if s["failure_rounds"]:
        print(f"\n  失败轮次: {s['failure_rounds']}")


if __name__ == "__main__":
    results = asyncio.run(run_test())
    failed = sum(1 for r in results if not r["success"])
    sys.exit(0 if failed == 0 else 1)
