"""ACP WebSocket端点 — 所有Agent统一通过stdio子进程连接

传输层：WebSocket(前端) ↔ ACP Proxy ↔ stdio子进程(Agent)
- SoulMate: python -m agent.start --stdio
- Hermes: hermes acp
- OpenClaw: openclaw acp

ACP Proxy只做消息转发，不做任何协议转换。
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from uuid import UUID

import jwt
import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect
from fastapi import APIRouter
import select

logger = logging.getLogger("acp-proxy.ws_acp")

# 路径根（2026-09-21硬编码治理：env可覆盖，默认值=原路径，行为不变）
_OPENSOUL_ROOT = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
_ACPPROXY_DIR = os.path.dirname(os.path.abspath(__file__))
_USER_HOME = os.environ.get("USER_HOME", "/home/climbing")

# JWT配置
_OPSOUL_ENV = {}
_env_path = f"{_OPENSOUL_ROOT}/.env"
try:
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                _OPSOUL_ENV[k.strip()] = v.strip().strip('"').strip("'")
except FileNotFoundError:
    pass

JWT_SECRET = os.getenv("JWT_SECRET", _OPSOUL_ENV.get("JWT_SECRET", "openmate-jwt-secret"))
JWT_ALGORITHM = "HS256"

# Agent路由配置：agent_id → subprocess命令
# soulmate/default用sys.executable（proxy自身解释器=依赖齐全的venv python）：
# 裸"python"在systemd环境下解析到系统python3.14（无aiohttp等依赖）→ agent子进程import即死
AGENT_ROUTES = {
    # 原生 ACP（各自独立的大脑）
    "soulmate":  {"cmd": [sys.executable, "-m", "agent.start", "--stdio"], "cwd": _ACPPROXY_DIR},
    "hermes":    {"cmd": ["hermes", "acp"], "cwd": _USER_HOME},
    "mimo":  {"cmd": ["mimo", "acp"], "cwd": _USER_HOME},
    "opencode":  {"cmd": ["opencode", "acp"], "cwd": _USER_HOME},
    # 自研适配器（ollama 无生态桥，已实测可用）
    "ollama":    {"cmd": [sys.executable, "-m", "agent.ollama_acp"], "cwd": _ACPPROXY_DIR},
    # 生态标准 ACP 桥包（AionUi 同款姿势：不逐一自写，用官方/社区桥）
    "codex":       {"cmd": ["npx", "-y", "@zed-industries/codex-acp@0.16.0"], "cwd": _USER_HOME, "probe_timeout": 30},
    "pi":          {"cmd": ["npx", "-y", "pi-acp@0.0.34"], "cwd": _USER_HOME, "probe_timeout": 30},
    "claude-code": {"cmd": ["npx", "-y", "@agentclientprotocol/claude-agent-acp@0.89.1"], "cwd": _USER_HOME, "probe_timeout": 30},
    # OpenClaw：自带ACP桥到其Gateway（探测/使用需gateway在线）
    "openclaw":  {"cmd": ["openclaw", "acp"], "cwd": _USER_HOME},
}

# 默认路由
DEFAULT_ROUTE = {"cmd": [sys.executable, "-m", "agent.start", "--stdio"], "cwd": _ACPPROXY_DIR}


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = payload.get("sub") or payload.get("user_id")
        if uid:
            try:
                return str(UUID(uid))
            except ValueError:
                return uid
        return None
    except jwt.ExpiredSignatureError:
        logger.warning("[ACP] Token expired")
        return None
    except jwt.InvalidTokenError:
        logger.warning("[ACP] Invalid token")
        return None


async def _send_acp(ws: WebSocket, data: dict):
    await ws.send_text(json.dumps(data, ensure_ascii=False))


def _extract_acp_session_id(parsed: dict, session_new_id) -> str | None:
    """从session/new的JSON-RPC响应中提取ACP sessionId

    子进程崩溃重启后proxy需重放session/load恢复会话连续性（遗留#1闭环），
    前提是知道当前ACP sessionId——它只出现在session/new响应里，
    proxy做纯透传时不解析，此处集中捕获。"""
    if session_new_id is None or parsed.get("id") != session_new_id:
        return None
    result = parsed.get("result")
    if not isinstance(result, dict):
        return None
    return result.get("sessionId") or result.get("session_id") or None


# ───会话级常驻子进程注册表（2026-10-09 观察窗解耦）───
# 语义：生成任务归系统生命周期，页面只是观察窗。WS断开=摘除观察窗，子进程
# 继续在后台生成并落库；新WS连同一会话=接回观察窗，流式继续。系统没退出
# 就不中断会话；空闲静默超时才由回收器兜底（生成中的任务持续有输出，不会误杀）。
_LIVE: dict[str, dict] = {}
_DETACH_TTL = 30 * 60  # 观察窗摘除后，输出静默超过30分钟才回收


async def _reap_detached(reg_key: str, entry: dict) -> None:
    """兜底回收：已摘窗且输出静默超TTL的子进程才结束；仍在流动则顺延。"""
    await asyncio.sleep(_DETACH_TTL)
    try:
        proc = entry.get("proc")
        if entry.get("subs") or proc is None or proc.returncode is not None:
            return  # 已接回观察窗 / 已自然结束
        if time.time() - float(entry.get("last") or 0) < _DETACH_TTL:
            asyncio.create_task(_reap_detached(reg_key, entry))  # 仍在输出，顺延检查
            return
        logger.info(f"[ACP] reap idle detached subprocess pid={proc.pid} key={reg_key}")
        proc.terminate()
    except Exception as e:
        logger.debug(f"[ACP] reap failed (非致命): {e}")


async def ws_acp_endpoint(client_ws: WebSocket):
    """//ws/acp WebSocket入口 — ACP JSON-RPC 2.0多Agent路由

    流程（ACP v1.0）：
    1. JWT鉴权
    2. 等待 initialize + session/new 消息提取agent_id
    3. 启动对应agent的stdio子进程
    4. 双向透传：WebSocket ↔ subprocess stdin/stdout
    """
    await client_ws.accept()

    token = client_ws.query_params.get("token", "")
    if not token:
        await _send_acp(client_ws, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Missing token"}})
        await client_ws.close()
        return

    user_id = decode_token(token)
    if not user_id:
        await _send_acp(client_ws, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid token"}})
        await client_ws.close()
        return

    logger.info(f"[ACP] user {user_id} connected")

    # 收集客户端消息，等newSession来确定agent_id
    init_msg = None
    session_msg = None
    agent_id = None
    route = None

    # 先等initialize
    try:
        init_msg = await asyncio.wait_for(client_ws.receive_json(), timeout=10)
    except Exception as e:
        logger.warning(f"[ACP] Failed to receive initialize: {e}")
        await client_ws.close()
        return

    if init_msg.get("method") != "initialize":
        await _send_acp(client_ws, {"jsonrpc": "2.0", "id": init_msg.get("id"), "error": {"code": -32600, "message": "First message must be initialize"}})
        await client_ws.close()
        return

    # 立即回复 initialize（代理响应，不含具体 agent 信息）
    await _send_acp(client_ws, {
        "jsonrpc": "2.0",
        "id": init_msg.get("id"),
        "result": {
            "protocolVersion": 1,
            "agentInfo": {"name": "openmate-proxy", "version": "0.1.0"},
            "agentCapabilities": {
                "promptCapabilities": {},
                "sessionCapabilities": {"fork": {}, "list": {}, "resume": {}}
            },
        }
    })
    logger.info(f"[ACP] initialize handshake OK for user {user_id}")

    # 初始化消息暂存，等session/new到了一起发
    buffered_msgs = [init_msg]
    
    # 等session/new（或newSession兼容旧前端）
    try:
        session_msg = await asyncio.wait_for(client_ws.receive_json(), timeout=30)
        buffered_msgs.append(session_msg)
    except Exception as e:
        logger.warning(f"[ACP] Failed to receive newSession: {e}")
        await client_ws.close()
        return

    # 提取agent_id
    params = session_msg.get("params", {})
    logger.info(f"[ACP] session/new raw params: {json.dumps(params, ensure_ascii=False)[:200]}")
    agent_id = params.get("agent_id") or params.get("agentId") or "soulmate"
    route = AGENT_ROUTES.get(agent_id)
    if route is None:
        # 未知agent自动尝试 {agent_id} acp，fallback到soulmate
        import shutil
        if shutil.which(agent_id):
            route = {"cmd": [agent_id, "acp"], "cwd": _USER_HOME}
            logger.info(f"Dynamic route for '{agent_id}': {route['cmd']}")
        else:
            route = DEFAULT_ROUTE
            logger.warning(f"Agent '{agent_id}' not found, falling back to soulmate")
    logger.info(f"[ACP] user {user_id} → agent {agent_id} → {route['cmd']}")

    # ACP sessionId捕获 + 重启重放过滤（子进程重启session/load恢复用）
    session_new_id = session_msg.get("id")
    acp_state: dict = {"acp_sid": None}
    extra_filter_ids: set = set()  # 重放initialize的响应id，不透传给客户端

    # relay轨迹观察者（04:20轮遗留#4销账）：非soulmate引擎（hermes/openclaw/
    # opencode/mimo等外部进程）的回合由本层按消息流记账落trajectory——soulmate
    # 路由（agent.start --stdio，引擎内TrajectoryRecorder深记录）跳过防双记。
    # 只旁路观察，转发内容零改动；记账失败绝不阻塞会话流。
    # session收口recorder（04:20轮遗留#2销账：session.status不再恒running）：
    # 连接teardown时把trajectory session翻completed（soulmate路由也用——引擎内
    # 深记录只管span不管session生命周期）。与relay观察者共享同一实例。
    traj_recorder = None
    try:
        from agent.trajectory_recorder import TrajectoryRecorder

        traj_recorder = TrajectoryRecorder()
    except Exception as e:
        logger.warning(f"[ACP] trajectory recorder init failed (非致命): {e}")

    traj_relay = None
    if route["cmd"] != DEFAULT_ROUTE["cmd"]:
        try:
            from agent.relay_trajectory import RelayTrajectory

            traj_relay = RelayTrajectory(
                agent_id=agent_id,
                session_id_getter=lambda: acp_state.get("acp_sid"),
                recorder=traj_recorder,
            )
            logger.info(f"[ACP] relay trajectory observer on for agent '{agent_id}'")
        except Exception as e:
            logger.warning(f"[ACP] relay trajectory observer init failed (非致命): {e}")

    # 启动Agent子进程（Hermes需要用pty模式，因为hermes acp的asyncio不支持非TTY stdin）
    # 同一会话已有后台子进程 → 接回观察窗，不重复起进程（防双生成器）
    _key_client = ((session_msg or {}).get("params") or {}).get("_meta", {}).get("session_id") \
        or client_ws.query_params.get("sessionId") or ""
    reg_key = f"{agent_id}:{_key_client}" if _key_client else None
    entry = None
    if reg_key and (e := _LIVE.get(reg_key)) is not None and e["proc"].returncode is None:
        entry = e
    reattached = entry is not None
    proc = None
    use_pty = agent_id == "hermes"
    master_fd = None
    if reattached:
        proc = entry["proc"]
        use_pty = entry["use_pty"]
        master_fd = entry.get("master_fd")
        entry["subs"].add(client_ws)
        logger.info(f"[ACP] reattach: 会话已有后台子进程 (pid={proc.pid}), 接回观察窗")
    try:
        if reattached:
            logger.info(f"[ACP] reuse detached subprocess (pid={proc.pid})")
        elif use_pty:
            import pty, termios, tty as tty_mod
            master_fd, slave_fd = pty.openpty()
            # 禁用 echo 和行缓冲，防止输入被回显到 stdout
            attrs = termios.tcgetattr(slave_fd)
            attrs[3] &= ~termios.ECHO & ~termios.ICANON  # lflag: 关闭 ECHO 和 CANONICAL
            termios.tcsetattr(slave_fd, termios.TCSANOW, attrs)
            proc = await asyncio.create_subprocess_exec(
                *route["cmd"],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                cwd=route.get("cwd"),
            )
            os.close(slave_fd)  # 子进程已fork，关闭slave端
        else:
            # 传递进化引擎API地址给子进程
            # （P0修复2026-10-02：此处原有函数内"import os"，把os变成函数局部
            # 变量——pty分支（hermes）的os.close/os.write在其执行前引用即
            # UnboundLocalError"cannot access local variable 'os'"，hermes路由
            # 每次启动子进程必炸。os已由模块顶部import，删除局部import即可。）
            env = dict(os.environ)
            env["EVOLUTION_API_URL"] = "http://127.0.0.1:8092"
            proc = await asyncio.create_subprocess_exec(
                *route["cmd"],
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=route.get("cwd"),
                env=env,
            )
            # 增大 readline 限制（某些 agent 输出超长单行，如 opencode）
            proc.stdout._limit = 1024 * 1024  # 1MB
        logger.info(f"[ACP] Started {agent_id} subprocess (pid={proc.pid}, pty={use_pty})")
    except Exception as e:
        logger.error(f"[ACP] Failed to start {agent_id}: {e}")
        # 子进程可能已spawn成功但后续步骤炸了（如P0 os遮蔽案例：create_*
        # 成功后os.close抛UnboundLocalError）——不清理会留孤儿进程占pty
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
            except Exception:
                pass
        await _send_acp(client_ws, {"jsonrpc": "2.0", "id": session_msg.get("id"), "error": {"code": -32603, "message": f"Agent {agent_id} unavailable: {e}"}})
        await client_ws.close()
        return

    if entry is None:
        entry = {"proc": proc, "use_pty": use_pty,
                 "master_fd": master_fd if use_pty else None,
                 "subs": {client_ws}, "last": time.time(), "keys": set()}
    if reg_key:
        entry["keys"].add(reg_key)
        _LIVE[reg_key] = entry
    keep_running = False

    # 转发所有缓冲消息到子进程（initialize + session/new）
    # 注意：子进程（ACP SDK）期望 initialize params 里有 protocolVersion
    try:
        for msg in buffered_msgs:
            if msg.get("method") == "initialize":
                msg.setdefault("params", {})["protocolVersion"] = 1
            # session/new 的 agent_id 是给proxy路由用的，子进程不认识，去掉
            if msg.get("method") == "session/new":
                msg.setdefault("params", {}).pop("agent_id", None)
                msg.get("params", {}).pop("agentId", None)
                # 注入子进程需要的必填字段（cwd, mcpServers）
                p = msg["params"]
                if "cwd" not in p:
                    p["cwd"] = route.get("cwd", _USER_HOME)
                if "mcpServers" not in p:
                    p["mcpServers"] = []
            _msg = json.dumps(msg, ensure_ascii=False)
            if use_pty:
                os.write(master_fd, (_msg + "\n").encode())
            else:
                proc.stdin.write((_msg + "\n").encode())
                await proc.stdin.drain()
    except Exception as e:
        logger.error(f"[ACP] Failed to forward initial messages: {e}")
        proc.terminate()
        await client_ws.close()
        return

    # 唤醒回合实时可见（dev-reports 11:11遗留#3跨进程推送）：本WS连接正连期间，
    # 由WakeupRelay把唤醒回合落库行（turn_origin=wakeup标记）补推给前端——唤醒
    # 回合在headless实例/对端proxy实例执行，_notify_client天然no-op。仅soulmate
    # 路由（agent_messages单一真源的agent）需要；acp_sid由_process_line捕获，
    # relay自带等待（session/new响应透传途中）。连接结束在最终清理cancel。
    relay_task = None
    if agent_id == "soulmate":
        try:
            from agent.wakeup_relay import WakeupRelay

            relay_task = asyncio.create_task(
                WakeupRelay(
                    session_id_getter=lambda: acp_state.get("acp_sid"),
                    send=lambda payload: _send_acp(client_ws, payload),
                ).run()
            )
        except Exception as e:
            logger.warning(f"[ACP] wakeup relay start failed (非致命): {e}")

    # 双向透传：WebSocket ↔ subprocess
    async def ws_to_stdin():
        """WebSocket → subprocess stdin"""
        try:
            while True:
                raw = await client_ws.receive_text()
                # 过滤心跳ping消息，不传给子进程
                if '"method":"ping"' in raw or '"method": "ping"' in raw:
                    continue
                logger.info(f"[{user_id}] ws_to_stdin: forwarding {len(raw)} chars")
                if traj_relay is not None:
                    # relay轨迹观察（旁路，never-raise）：session/prompt=回合起点
                    try:
                        traj_relay.observe_client(json.loads(raw))
                    except (json.JSONDecodeError, ValueError):
                        pass
                if use_pty:
                    os.write(master_fd, (raw + "\n").encode())
                else:
                    proc.stdin.write((raw + "\n").encode())
                    await proc.stdin.drain()
        except (WebSocketDisconnect, ConnectionError):
            pass
        except Exception as e:
            logger.info(f"[{user_id}] ws_to_stdin ERROR: {e}")
        finally:
            # 观察窗解耦（2026-10-09）：WS断开≠会话终止——绝不能在这里关子进程的
            # stdin/pty，agent收到EOF会立刻自杀（实测：断连瞬间进程退出、生成全丢）。
            # 子进程生命周期由 _LIVE 注册表 + _reap_detached 空闲兜底管理。
            pass

    # ANSI 转义码清理正则
    ansi_escape = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?\x07|\r')

    async def _process_line(msg: str, init_ids: set, agent_id: str, ws: WebSocket):
        """解析单行 JSON-RPC 消息并转发给客户端"""
        # 跳过非 JSON 行（某些 agent 的 banner/版本信息）
        try:
            parsed = json.loads(msg)
        except (json.JSONDecodeError, ValueError):
            logger.debug(f"[{agent_id}] skip non-JSON: {msg[:100]}")
            return
        # 过滤 subprocess 的 initialize 响应（Proxy 已经回复过）
        if parsed.get("id") in init_ids and "result" in parsed:
            logger.debug(f"[{agent_id}] skip subprocess initialize response (id={parsed['id']})")
            init_ids.discard(parsed["id"])
            return
        # 捕获session/new响应中的ACP sessionId（一次性；子进程重启重放用）
        if acp_state["acp_sid"] is None:
            _sid = _extract_acp_session_id(parsed, session_new_id)
            if _sid:
                acp_state["acp_sid"] = _sid
                logger.info(f"[{agent_id}] captured ACP sessionId: {_sid}")
        # 记录并转发给客户端
        logger.info(f"[{agent_id}] → client: id={parsed.get('id')} method={parsed.get('method')} has_result={'result' in parsed}")
        if traj_relay is not None:
            # relay轨迹观察（旁路，never-raise）：prompt响应=回合收口，
            # session/update增量（agent_message_chunk/tool_call[_update]）入账
            traj_relay.observe_agent(parsed)
        await ws.send_text(msg)

    async def _pump_line(msg: str, init_ids: set, agent_id: str, entry: dict):
        """输出泵单行处理：投递到当前观察窗（entry["ws"]）；窗不在只排空不投递。
        投递失败不断排空（管道不堵塞，后台生成不中断）。"""
        entry["last"] = time.time()
        _sid = acp_state.get("acp_sid")
        if _sid and _sid not in entry["keys"]:
            entry["keys"].add(_sid)
            _LIVE[f"{agent_id}:{_sid}"] = entry
        ws_targets = entry.get("subs") or set()
        if not ws_targets:
            return  # 无观察窗：继续排空（后台生成不中断）
        for ws_target in list(ws_targets):
            try:
                await _process_line(msg, init_ids, agent_id, ws_target)
            except Exception as pe:
                logger.debug(f"[ACP] pump deliver failed (摘除死窗，继续排空): {pe}")
                ws_targets.discard(ws_target)

    async def stdout_to_ws():
        """subprocess stdout → WebSocket（过滤掉 subprocess 的 initialize 响应）"""
        # 只跟踪 initialize 的 request id（不包括 session/new 等其他 buffered 消息）
        init_ids: set = set(extra_filter_ids)  # type: ignore
        for m in buffered_msgs:
            if m.get("method") == "initialize":
                init_ids.add(m.get("id"))
        logger.info(f"[{agent_id}] initialize request ids to filter: {init_ids}")
        loop = asyncio.get_event_loop()
        line_buf = ""  # pty 模式下的行缓冲
        try:
            while True:
                if use_pty:
                    # pty模式：用run_in_executor避免阻塞
                    raw = await loop.run_in_executor(None, os.read, master_fd, 65536)
                    if not raw:
                        break
                    # 清理 ANSI 转义码和 \r
                    decoded = ansi_escape.sub("", raw.decode(errors="replace"))
                    line_buf += decoded
                    # 按行分割，最后一段可能不完整，保留在 buffer 里
                    while "\n" in line_buf:
                        line, line_buf = line_buf.split("\n", 1)
                        msg = line.strip()
                        if msg:
                            await _pump_line(msg, init_ids, agent_id, entry)
                else:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    msg = line.decode().strip()
                    if msg:
                        await _pump_line(msg, init_ids, agent_id, entry)
        except (WebSocketDisconnect, ConnectionError):
            pass
        except Exception as e:
            logger.debug(f"[{user_id}] stdout_to_ws: {e}")

    async def stderr_drain():
        """subprocess stderr → log"""
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                logger.info(f"[{agent_id}] stderr: {line.decode().strip()}")
        except Exception:
            pass

    # 接回观察窗：输出泵（stdout_to_ws/stderr_drain）仍在原处理器里常驻运行，
    # 这里只跑本WS的输入泵；WS断开同样只是摘窗。
    if reattached:
        try:
            await ws_to_stdin()
        except Exception:
            pass
        entry["subs"].discard(client_ws)
        keep_running = True
        logger.info(f"[ACP] WS detached (reattach), subprocess keeps running (pid={proc.pid})")
        if reg_key:
            asyncio.create_task(_reap_detached(reg_key, entry))

    # 主循环：子进程退出后如果WebSocket还活着，用同一ACP session_id重启
    max_restarts = 3
    restart_count = 0
    while not reattached:
        t1 = asyncio.create_task(ws_to_stdin())
        t2 = asyncio.create_task(stdout_to_ws())
        t3 = asyncio.create_task(stderr_drain())

        try:
            done, pending = await asyncio.wait(
                [t1, t2], return_when=asyncio.FIRST_COMPLETED
            )
            ws_disconnected = t1 in done and not t2.done()

            if ws_disconnected:
                # 观察窗摘除：页面切走≠会话终止（系统没退出就不中断会话）。
                # 子进程继续在后台生成并落库；t2/t3输出泵继续运行（无窗只排空）；
                # 空闲静默由 _reap_detached 兜底回收。
                logger.info(f"[ACP] WS detached; subprocess keeps running (pid={proc.pid}, key={reg_key})")
                entry["subs"].discard(client_ws)
                keep_running = True
                if reg_key:
                    asyncio.create_task(_reap_detached(reg_key, entry))
                break  # 退出主循环；t2/t3在后台继续运行

            # 子进程退出但WebSocket还活着 — 重启子进程
            for t in pending:
                t.cancel()
            t3.cancel()
            restart_count += 1
            if restart_count > max_restarts:
                logger.warning(f"[ACP] max restarts ({max_restarts}) exceeded, closing")
                break

            # 清理旧子进程
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    proc.kill()

            # 重启子进程（与初始路由相同命令）；重放initialize+session/load
            # 恢复进程内会话连续性（遗留#1闭环）：agent侧load_session支持
            # SQLite恢复（soulmate_agent._reload_session_from_db）；即使重放
            # 失败，下一条session/prompt在agent侧仍会自愈——双重保险
            sid_for_restart = acp_state.get("acp_sid")
            logger.info(
                f"[ACP] subprocess exited, respawning route cmd for {agent_id} "
                f"(attempt {restart_count}, acpSessionId={sid_for_restart})"
            )
            new_proc = await asyncio.create_subprocess_exec(
                *route["cmd"],
                cwd=str(route["cwd"]),  # 所有route都定义了cwd
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            if new_proc.returncode is not None:
                err = await new_proc.stderr.read() if new_proc.stderr else b""
                logger.error(f"[ACP] subprocess failed to restart: {err.decode()[:500]}")
                break
            proc = new_proc
            logger.info(f"[ACP] subprocess restarted: {agent_id}, pid={proc.pid}")
            if sid_for_restart:
                try:
                    reinit_id = f"proxy-reinit-{restart_count}"
                    extra_filter_ids.add(reinit_id)
                    replay = [
                        {"jsonrpc": "2.0", "id": reinit_id, "method": "initialize",
                         "params": {"protocolVersion": 1}},
                        {"jsonrpc": "2.0", "id": f"proxy-reload-{restart_count}",
                         "method": "session/load",
                         "params": {"sessionId": sid_for_restart,
                                    "cwd": route.get("cwd", _USER_HOME),
                                    "mcpServers": []}},
                    ]
                    for _m in replay:
                        proc.stdin.write((json.dumps(_m, ensure_ascii=False) + "\n").encode())
                    await proc.stdin.drain()
                    logger.info(f"[ACP] replayed initialize+session/load for {sid_for_restart} after respawn")
                except Exception as _e:
                    logger.warning(f"[ACP] session/load replay failed after respawn: {_e}")
            else:
                logger.info("[ACP] no ACP sessionId captured yet, skip session replay (prompt侧仍可自愈)")
            # 循环继续，重新创建t1/t2/t3
        except Exception as e:
            logger.error(f"[ACP] restart loop error: {e}")
            break

    # 最终清理（keep_running=观察窗已摘但子进程仍活着：常驻泵继续运行，不取消）
    if not keep_running:
        try:
            t3.cancel()
        except Exception:
            pass
    if relay_task is not None:
        relay_task.cancel()
    if traj_relay is not None:
        # relay轨迹收尾：未收口回合判error落账（失败必须可见）后停worker
        try:
            await traj_relay.close()
        except Exception as e:
            logger.debug(f"[ACP] relay trajectory close failed (非致命): {e}")
    if proc.returncode is None and not keep_running:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except Exception:
            proc.kill()
    _end_sid = acp_state.get("acp_sid") or ""
    if traj_recorder is not None and _end_sid and not keep_running:
        # session收口（04:20轮遗留#2销账）：连接结束=观察窗结束，trajectory
        # session翻completed（opensoul侧add_event自愈可翻回running）。
        # 顺序纪律：必须在traj_relay.close()（drain完回合事件）与proc死透
        # （soulmate写入方不再有在途事件）之后——先落事件后收口。
        try:
            await traj_recorder.end_session(_end_sid, status="completed")
            logger.info(f"[ACP] trajectory session closed: {_end_sid}")
        except Exception as e:
            logger.debug(f"[ACP] trajectory session end failed (非致命): {e}")
    logger.info(f"[ACP] user {user_id} session with {agent_id} ended (exit={proc.returncode})")
    try:
        await client_ws.close(code=1000, reason="session ended")
    except Exception:
        pass


# ============================================================
# Agent 注册表 + 能力探测 HTTP 端点（注册表=唯一真源；
# "测试连接"=真ACP握手，状态徽章不说谎；曾被git还原抹掉，已固化）
# ============================================================
agents_router = APIRouter()


@agents_router.get("/agents")
async def list_agents():
    """注册表=唯一真源：返回已注册agent及其二进制安装状态。"""
    import shutil as _sh
    out = []
    for aid, route in AGENT_ROUTES.items():
        cmd = route["cmd"]
        first = cmd[0]
        # npx 桥包：检查 npm 生态而非本地二进制
        if first in ("npx", "bun"):
            installed = True  # 桥包按需下载
        else:
            installed = bool(_sh.which(first)) or first == sys.executable
        out.append({"id": aid, "cmd": cmd, "installed": installed})
    return {"agents": out}


def _probe_agent(cmd: list) -> dict:
    """真 ACP 能力探测：PTY 拉起 → 发 initialize → 认 JSON-RPC 应答（ACPen，不是"装没装"）。
    支持 per-route probe_timeout（npx 桥包首次下载慢，30秒）。"""
    import pty, termios
    timeout = 8.0
    for route in AGENT_ROUTES.values():
        if route.get("cmd") == cmd:
            timeout = float(route.get("probe_timeout", 8))
            break
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(_USER_HOME)
            os.execvp(cmd[0], cmd)
        except Exception:
            os._exit(127)
    try:
        attrs = termios.tcgetattr(fd)
        attrs[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except Exception:
        pass
    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                       "params": {"protocolVersion": 1}}) + "\n"
    try:
        os.write(fd, init.encode())
    except Exception:
        pass
    out = b""
    start = time.time()
    while time.time() - start < timeout:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 8192)
            except Exception:
                break
            if not chunk:
                break
            out += chunk
            for line in out.splitlines():
                if b'"jsonrpc"' in line and b'"result"' in line:
                    os.close(fd)
                    try:
                        os.kill(pid, 9)
                    except Exception:
                        pass
                    return {"ok": True, "detail": "ACP handshake OK"}
    try:
        os.close(fd)
        os.kill(pid, 9)
    except Exception:
        pass
    # 拉起失败 vs 起来了但不答 ACP
    detail = "no ACP response (not an ACP agent)"
    if not out:
        detail = "spawn failed or no output"
    return {"ok": False, "detail": detail}


@agents_router.post("/agents/probe")
async def probe_agent(payload: dict):
    """对某个 agent 做真 ACP 握手探测（"测试连接"按钮的真身）。
    payload: {agent_id?, cmd?: [..]} — 未注册的agent可显式给cmd探测。"""
    agent_id = payload.get("agent_id", "")
    cmd = payload.get("cmd")
    if not cmd:
        route = AGENT_ROUTES.get(agent_id)
        if not route:
            return {"agent_id": agent_id, "ok": False, "detail": "no command"}
        cmd = route["cmd"]
    result = await asyncio.to_thread(_probe_agent, cmd)
    return {"agent_id": agent_id, "cmd": cmd, **result}
