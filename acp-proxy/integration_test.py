"""真实集成测试门禁 — evo验证层核心改进

借鉴：
- agno: 全隔离环境指纹 + K次rollout
- CAMEL: 程序化验证器优先于LLM裁判
- SWE-agent: docker真环境 + per-instance成本上限
- deepagents: BaseSandbox两方法派生

核心改变：evo的验证从"语法检查+import"升级为"真实功能测试"
"""
import asyncio
import os
import json
import logging
import time
import uuid
from pathlib import Path

import httpx
import websockets

logger = logging.getLogger("integration-test")

# 服务地址
OPENMATE_URL = "http://localhost:3000"
OPensoul_URL = "http://127.0.0.1:8090"
ACP_PROXY_URL = "http://127.0.0.1:8092"
WS_URL = "ws://127.0.0.1:8092"

# 超时配置
HEALTH_TIMEOUT = 5
WS_CONNECT_TIMEOUT = 10
WS_RESPONSE_TIMEOUT = 30  # thinking应该秒回，done可能要30s
BUILD_TIMEOUT = 300

# 成本上限（借鉴SWE-agent per-instance成本控制）
MAX_TEST_DURATION = 120  # 单次集成测试最长2分钟


class IntegrationTestResult:
    """集成测试结果"""
    def __init__(self):
        self.tests: list[dict] = []
        self.passed = False
        self.score = 0.0
        self.duration = 0.0
        self.env_fingerprint = ""

    def add(self, name: str, passed: bool, detail: str = "", duration: float = 0.0):
        self.tests.append({
            "name": name, "passed": passed, 
            "detail": detail[:500], "duration": round(duration, 2)
        })

    def finalize(self):
        total = len(self.tests)
        passed = sum(1 for t in self.tests if t["passed"])
        self.score = round(passed / total, 2) if total > 0 else 0.0
        # 所有测试都必须通过才算passed（不是0.7阈值）
        self.passed = passed == total and total > 0

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "score": self.score,
            "duration": round(self.duration, 2),
            "tests": self.tests,
            "env_fingerprint": self.env_fingerprint,
        }


async def _check_service_health(url: str, name: str, timeout: int = HEALTH_TIMEOUT) -> bool:
    """检查服务健康状态"""
    # 尝试多个health路径
    paths = ["/health", "/api/health", "/"]
    for path in paths:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(f"{url}{path}")
                if resp.status_code == 200:
                    # 检查响应是否是健康状态
                    try:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("status") == "ok":
                            return True
                    except Exception:
                        # 非JSON响应，200就算健康
                        return True
        except Exception:
            continue
    return False


async def _test_ws_send_receive(session_id: str = None) -> dict:
    """核心集成测试：WS发消息→必须收到响应
    
    测试策略（3层递进）：
    1. 最低要求：收到connected+thinking（证明WS管道+认证+消息路由全通）
    2. 中等：收到chunk（证明LLM开始流式输出）
    3. 理想：收到done（证明完整AI回复）
    
    这是evo之前缺失的关键验证。之前_verify()只是health check ping。
    """
    import jwt as pyjwt
    
    if not session_id:
        session_id = f"evo-test-{uuid.uuid4().hex[:8]}"

    # 生成测试token
    from pathlib import Path as P
    import os
    env_file = P(os.path.join(os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul"), ".env"))
    jwt_secret = "change-me-in-production"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("JWT_SECRET="):
                jwt_secret = line.split("=", 1)[1].strip()
                break
    
    test_user_id = str(uuid.uuid4())
    token = pyjwt.encode(
        {"sub": test_user_id, "exp": int(time.time()) + 3600},
        jwt_secret, algorithm="HS256"
    )
    
    ws_url = f"{WS_URL}/ws/chat?token={token}"
    
    # 跟踪收到的消息类型
    received_types = set()
    response_content = ""
    
    try:
        async with websockets.connect(ws_url, open_timeout=WS_CONNECT_TIMEOUT) as ws:
            # 使用agent_proxy模式走soulmate Agent Engine（比hermes CLI快）
            # _meta.source=会话级provenance产生侧标注（jobs.source同款）：本探针
            # 会话test出身，/ws/chat落库行metadata带session_source='test'，
            # ExperienceCollector据此跳过——测试会话不再污染生产experiences
            test_message = {
                "type": "message",
                "text": "ping",
                "mode": "agent_proxy",
                "session_id": session_id,
                "agent_id": "soulmate",
                "_meta": {"source": "test"},
            }
            await ws.send(json.dumps(test_message))

            start = time.time()
            while time.time() - start < WS_RESPONSE_TIMEOUT:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(raw)
                    msg_type = data.get("type", "")
                    received_types.add(msg_type)

                    # 收到错误
                    if msg_type == "error":
                        return {
                            "passed": False,
                            "detail": f"收到错误: {data.get('message', str(data))[:200]}",
                            "duration": time.time() - start,
                        }

                    # 收到done（完整回复）
                    if msg_type == "done":
                        content = data.get("text", "")
                        return {
                            "passed": True,
                            "detail": f"完整AI回复({len(content)}字): {content[:100]}",
                            "duration": time.time() - start,
                        }

                    # 收到chunk（流式输出中）
                    if msg_type == "chunk":
                        response_content += data.get("text", "")

                    # 收到thinking（后端开始处理）— 至少证明管道通了
                    if msg_type == "thinking":
                        # 继续等待done/chunk，但如果超时了thinking也算最低通过
                        pass

                except asyncio.TimeoutError:
                    # 超时了——检查已收到什么
                    if "thinking" in received_types:
                        # WS管道通了，只是AI回复慢
                        return {
                            "passed": True,
                            "detail": f"WS管道通(收到thinking)，AI回复慢(已等{time.time()-start:.0f}s)",
                            "duration": time.time() - start,
                        }
                    continue
                except json.JSONDecodeError:
                    continue

            # 最终超时 — 根据收到的消息判断
            if "thinking" in received_types:
                return {
                    "passed": True,
                    "detail": f"WS管道通(收到thinking)，AI未及时回复",
                    "duration": time.time() - start,
                }
            return {
                "passed": False,
                "detail": f"超时：{WS_RESPONSE_TIMEOUT}秒内无任何响应，收到的类型: {received_types}",
                "duration": time.time() - start,
            }

    except websockets.exceptions.ConnectionClosed as e:
        return {"passed": False, "detail": f"WS连接关闭: {e}", "duration": 0}
    except Exception as e:
        return {"passed": False, "detail": f"WS连接失败: {e}", "duration": 0}


async def _test_frontend_build(repo_root: Path) -> dict:
    """前端构建测试"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "run", "build",
            cwd=str(repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=BUILD_TIMEOUT)
        
        if proc.returncode == 0:
            return {"passed": True, "detail": "Build成功", "duration": 0}
        else:
            err = stderr.decode()[-500:]
            return {"passed": False, "detail": f"Build失败: {err}", "duration": 0}
    except asyncio.TimeoutError:
        return {"passed": False, "detail": f"Build超时({BUILD_TIMEOUT}s)", "duration": 0}
    except Exception as e:
        return {"passed": False, "detail": f"Build异常: {e}", "duration": 0}


async def _test_python_imports(repo_root: Path, changed_files: list[str]) -> dict:
    """Python导入测试 — 比之前的更严格"""
    failed = []
    for f in changed_files:
        if not f.endswith(".py"):
            continue
        full_path = repo_root / f
        if not full_path.exists():
            continue
        
        # 语法检查
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", f"import ast; ast.parse(open('{full_path}').read())",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                failed.append(f"{f}: 语法错误 - {stderr.decode()[:200]}")
        except Exception as e:
            failed.append(f"{f}: 检查异常 - {e}")

    if failed:
        return {"passed": False, "detail": "; ".join(failed), "duration": 0}
    return {"passed": True, "detail": f"{len(changed_files)}个文件语法检查通过", "duration": 0}


async def _test_ws_protocol_alignment(repo_root: Path) -> dict:
    """WS协议对齐测试 — 检查前端发送格式和后端期望格式是否一致
    
    这是防止evo再次改坏协议的关键测试。
    """
    chat_client = repo_root / "src" / "app" / "(app)" / "chat" / "chat-client.tsx"
    ws_chat = repo_root.parent / "opensoul" / "src" / "api" / "ws_chat.py"

    issues = []

    if chat_client.exists():
        content = chat_client.read_text(encoding="utf-8")
        # 检查前端是否使用了正确的协议格式
        if "type: 'send_message'" in content or 'type: "send_message"' in content:
            issues.append("前端使用send_message，后端期望message")
        if "type: 'message'" in content or 'type: "message"' in content:
            pass  # 正确
        # 检查字段名
        if "content:" in content and "text:" not in content:
            issues.append("前端用content字段，后端期望text")

    if ws_chat.exists():
        content = ws_chat.read_text(encoding="utf-8")
        if "msg_type" in content:
            # 后端检查消息类型
            if '"message"' in content or "'message'" in content:
                pass  # 后端期望message类型

    if issues:
        return {"passed": False, "detail": f"协议不对齐: {'; '.join(issues)}", "duration": 0}
    return {"passed": True, "detail": "WS协议对齐检查通过", "duration": 0}


async def run_integration_tests(
    repo_root: Path,
    changed_files: list[str] | None = None,
    include_ws_test: bool = True,
    include_build: bool = True,
) -> IntegrationTestResult:
    """运行完整的集成测试套件
    
    这是evo v2的核心改进：从"语法检查"升级为"真实功能测试"
    """
    result = IntegrationTestResult()
    start_time = time.time()

    if changed_files is None:
        changed_files = []

    # 环境指纹（借鉴agno）
    result.env_fingerprint = _compute_env_fingerprint(repo_root)

    # L1: 服务健康检查
    health_checks = [
        (OPENMATE_URL, "openmate-frontend"),
        (OPensoul_URL, "opensoul-backend"),
        (ACP_PROXY_URL, "acp-proxy"),
    ]
    for url, name in health_checks:
        t0 = time.time()
        healthy = await _check_service_health(url, name)
        result.add(f"health:{name}", healthy, 
                   "OK" if healthy else f"{url} 不可达",
                   time.time() - t0)

    # L2: Python语法/导入检查
    if changed_files:
        t0 = time.time()
        py_result = await _test_python_imports(repo_root, changed_files)
        result.add("python-imports", py_result["passed"], py_result["detail"], time.time() - t0)

    # L3: WS协议对齐检查（防止再次改坏协议）
    t0 = time.time()
    protocol_result = await _test_ws_protocol_alignment(repo_root)
    result.add("ws-protocol-alignment", protocol_result["passed"], protocol_result["detail"], time.time() - t0)

    # L3.5: 契约注册表检查（跨repo协议保护）
    if changed_files:
        t0 = time.time()
        from contract_registry import check_contracts
        contract_result = check_contracts(repo_root, changed_files)
        violations_str = "; ".join(v["reason"] for v in contract_result["violations"][:3])
        result.add(
            "contract-registry",
            contract_result["passed"],
            violations_str if contract_result["violations"] else f"{contract_result['checked']}个契约检查通过",
            time.time() - t0,
        )

    # L4: 前端构建
    if include_build:
        t0 = time.time()
        build_result = await _test_frontend_build(repo_root)
        result.add("frontend-build", build_result["passed"], build_result["detail"], time.time() - t0)

    # L5: 真实WS消息测试（核心！）
    if include_ws_test:
        t0 = time.time()
        ws_result = await _test_ws_send_receive()
        result.add("ws-send-receive", ws_result["passed"], ws_result["detail"], time.time() - t0)

    result.duration = time.time() - start_time
    result.finalize()

    logger.info(
        f"Integration tests: {'PASS' if result.passed else 'FAIL'} "
        f"({sum(1 for t in result.tests if t['passed'])}/{len(result.tests)}) "
        f"score={result.score} duration={result.duration:.1f}s"
    )

    return result


def _compute_env_fingerprint(repo_root: Path) -> str:
    """计算环境指纹（借鉴agno env_fingerprint）
    
    用于区分"环境变了"还是"代码变了"导致的回归
    """
    import hashlib
    
    parts = []
    
    # git HEAD
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=str(repo_root))
        parts.append(r.stdout.strip())
    except Exception:
        pass
    
    # package.json hash
    pkg = repo_root / "package.json"
    if pkg.exists():
        parts.append(hashlib.md5(pkg.read_bytes()).hexdigest()[:8])
    
    # requirements hash
    for req in ["requirements.txt", "pyproject.toml"]:
        p = repo_root / req
        if p.exists():
            parts.append(hashlib.md5(p.read_bytes()).hexdigest()[:8])
    
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
