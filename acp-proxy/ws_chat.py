"""WebSocket chat endpoint — stable version.

Key improvements:
1. Never expose raw Python exceptions to frontend
2. Proper WebSocket lifecycle management
3. Robust error recovery with user-friendly messages
4. Logging to file for debugging
"""

import asyncio
import json
import logging
import os
import shutil
import sqlite3
import time
from uuid import UUID

import jwt
import httpx
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from proxy import get_acp_process
from utils.llm_config import describe, resolve_llm_config

_OPENSOUL_ROOT = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
_OPENSOUL_DB = f"{_OPENSOUL_ROOT}/data/opensoul.db"


def _get_agent_db():
    """Get a connection to the OpenSoul database for agent session storage."""
    db = sqlite3.connect(_OPENSOUL_DB)
    db.row_factory = sqlite3.Row
    return db


def _create_or_get_agent_session(agent_id: str, session_id: str | None = None) -> str:
    """Create a new agent session or return existing one. Returns session_id."""
    db = _get_agent_db()
    try:
        if session_id:
            row = db.execute("SELECT id FROM agent_sessions WHERE id = ?", (session_id,)).fetchone()
            if row:
                return session_id
        # Create new session
        new_id = session_id or f"agent_{agent_id}_{int(time.time() * 1000)}"
        db.execute(
            "INSERT OR IGNORE INTO agent_sessions (id, agent_id, title, created_at, last_activity_at) VALUES (?, ?, ?, ?, ?)",
            (new_id, agent_id, "New Chat", time.time(), time.time()),
        )
        db.commit()
        return new_id
    finally:
        db.close()


def _store_agent_message(session_id: str, role: str, content: str):
    """Store a message in an agent session.

    P0-10消息树parentId（open-webui chat_fork + pi追加树）：parent_message_id=
    会话内上一条消息id（线性追加=树的主干），前端/接口据此重建消息树与fork分支。
    缺列时probe+ALTER幂等迁移（attachments列既有模式同款）。
    """
    db = _get_agent_db()
    try:
        now = time.time()
        try:
            db.execute("SELECT parent_message_id FROM agent_messages LIMIT 1")
        except sqlite3.OperationalError:
            db.execute("ALTER TABLE agent_messages ADD COLUMN parent_message_id INTEGER")
        last = db.execute(
            "SELECT id FROM agent_messages WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        parent_id = last["id"] if last else None
        db.execute(
            "INSERT INTO agent_messages (session_id, role, content, timestamp, parent_message_id) VALUES (?, ?, ?, ?, ?)",
            (session_id, role, content, now, parent_id),
        )
        db.execute(
            "UPDATE agent_sessions SET last_activity_at = ?, message_count = message_count + 1 WHERE id = ?",
            (now, session_id),
        )
        db.commit()
    finally:
        db.close()

logger = logging.getLogger("acp-proxy.ws")
router = APIRouter()

# Read JWT config from OpenSoul's .env (shared secret)
_OPSOUL_ENV = {}
_env_path = f"{_OPENSOUL_ROOT}/.env"
try:
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                _OPSOUL_ENV[k.strip()] = v.strip()
except FileNotFoundError:
    logger.warning(f"OpenSoul .env not found at {_env_path}")

JWT_SECRET = _OPSOUL_ENV.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = _OPSOUL_ENV.get("JWT_ALGORITHM", "HS256")


def decode_token(token: str) -> UUID | None:
    """Decode JWT and return user UUID, or None."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        logger.info(f"[AUTH] decoded sub={user_id!r} type={type(user_id).__name__}")
        if user_id:
            try:
                return UUID(str(user_id))
            except ValueError:
                return UUID(int=int(user_id))
    except Exception as e:
        logger.warning(f"[AUTH] decode failed: {e}")
    return None


# Agent CLI arg overrides — only for agents with non-standard args.
# All other agents default to: binary -p "message"
# System LLM config (OPENAI_API_KEY etc.) is injected for ALL agents automatically.
AGENT_ARGS_OVERRIDE: dict[str, list[str]] = {
    "hermes": ["-z"],
    "mimo": ["run"],
    "codex": ["exec"],
    "opencode": ["-q"],
    "openclaw": ["agent", "--agent", "main", "-m"],
    "copilot": ["copilot", "-p"],
    "amazon-q": ["chat", "--no-interactive", "-p"],
}

# Cache: available agents from OpenSoul /api/agents/detect
_available_agents: dict[str, dict] = {}
_agents_cache_ts: float = 0.0


async def _refresh_agent_list():
    """Fetch installed agents from OpenSoul (cached 60s)."""
    global _available_agents, _agents_cache_ts
    now = time.time()
    if now - _agents_cache_ts < 60 and _available_agents:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get("http://localhost:8090/api/agents/detect")
            if resp.status_code == 200:
                data = resp.json()
                _available_agents = {
                    a["id"]: a for a in data.get("agents", []) if a.get("available")
                }
                _agents_cache_ts = now
                logger.info(f"Agent list refreshed: {len(_available_agents)} available")
    except Exception as e:
        logger.warning(f"Agent list refresh failed: {e}")


def _get_llm_config() -> dict:
    """Read system LLM config — 收口 utils.llm_config.resolve_llm_config 单一真源。

    2026-09-27销账00:10遗留#4：本函数曾自扫 opensoul/.env + 环境变量，与
    resolve_llm_config 双入口并存——解析容错（旧实现在 try 外 int()，坏的
    LLM_MAX_TOKENS 直接抛 ValueError 炸掉代理）、文件覆盖、key_source 观测
    三处语义各走各的。现委托单一真源：opensoul/.env → acp-proxy/.env →
    进程环境兜底，坏值容错，带 key_source 观测（key原文绝不进日志）。

    include_mimo_overrides=False：MIMO_* 是进化strand专用覆写，聊天/agent
    代理流量绝不跟随（运维把进化指向另一套LLM时用户会话仍走系统LLM配置）。
    """
    return resolve_llm_config(include_mimo_overrides=False)


async def run_agent_proxy(agent_id: str, text: str) -> tuple[str, str, bool]:
    """Run a message through any installed agent with system LLM config injected."""
    await _refresh_agent_list()

    # Determine binary: from OpenSoul detect API, fallback to agent_id itself
    agent_info = _available_agents.get(agent_id)
    binary = agent_info["binary"] if agent_info else agent_id

    if not shutil.which(binary):
        return f"Agent未安装: {binary}", "error", False

    # Args: use override if known, default to -p for all others
    args = AGENT_ARGS_OVERRIDE.get(agent_id, ["-p"])
    cmd = [binary] + args + [text]
    try:
        # Always inject system LLM config — works for ALL agents
        env = os.environ.copy()
        llm_cfg = _get_llm_config()
        logger.debug(f"agent-proxy[{agent_id}] LLM config: {describe(llm_cfg)}")
        if llm_cfg.get("api_key"):
            env["OPENAI_API_KEY"] = llm_cfg["api_key"]
            env["ANTHROPIC_API_KEY"] = llm_cfg["api_key"]  # Claude-style agents
        if llm_cfg.get("base_url"):
            env["OPENAI_BASE_URL"] = llm_cfg["base_url"]
            env["OPENAI_API_BASE"] = llm_cfg["base_url"]   # compat alias
        if llm_cfg.get("model"):
            env["OPENAI_MODEL"] = llm_cfg["model"]
            env["MODEL"] = llm_cfg["model"]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        response = stdout.decode("utf-8", errors="replace").strip()
        if not response and proc.returncode != 0:
            response = stderr.decode("utf-8", errors="replace").strip()
        return response or "（无响应）", agent_id, proc.returncode == 0
    except Exception as e:
        logger.error(f"Agent proxy error: {e}")
        return f"Agent执行出错: {type(e).__name__}", "error", False


async def forward_to_agent_engine(
    client_ws: WebSocket,
    text: str,
    session_id: str | None = None,
    attachments: list[dict] | None = None,
) -> tuple[str, str, bool]:
    """通过WebSocket连接Agent Engine(8787)做协议桥接，用于soulmate模式。

    ACP v1.0 JSON-RPC 2.0 over NDJSON协议流程：
    1. initialize握手
    2. session.create (或复用已有session)
    3. session.prompt提交用户消息
    4. 流式接收session.event(event_type=message, content_delta) → 转发chunk给前端
    5. session.event(event_type=completed/failed) → 发送done/error

    Returns:
        (accumulated_text, source, success)
    """
    engine_url = "ws://127.0.0.1:8787"
    accumulated_text = ""
    msg_id = 0

    def next_id() -> str:
        nonlocal msg_id
        msg_id += 1
        return str(msg_id)

    async def rpc(ws, method: str, params: dict) -> dict:
        rid = next_id()
        req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
        await ws.send(json.dumps(req, ensure_ascii=False) + "\n")
        async for raw in ws:
            resp = json.loads(raw.strip())
            if str(resp.get("id", "")) == rid:
                if "error" in resp:
                    raise RuntimeError(f"ACP error({method}): {resp['error']}")
                return resp.get("result", {})
        raise RuntimeError(f"WebSocket closed, RPC({method}) no response")

    try:
        async with websockets.connect(engine_url) as engine_ws:
            logger.info(f"soulmate: connected to Agent Engine at {engine_url}")

            # Step 1: initialize
            await rpc(engine_ws, "initialize", {
                "protocolVersion": 1,
                "clientInfo": {"name": "soulmate-proxy", "version": "1.0.0"},
            })

            # Step 2: session.create
            result = await rpc(engine_ws, "session.create", {
                "cwd": os.environ.get("USER_HOME", "/home/climbing"),
            })
            engine_session_id = result.get("session_id") or result.get("sessionId", "")
            if not engine_session_id:
                raise RuntimeError("session.create did not return session_id")
            logger.info(f"soulmate: session created: {engine_session_id}")

            # Step 3: session.prompt (fire, then stream notifications)
            prompt_text = text
            saved_files = []
            if attachments:
                import base64, tempfile
                file_parts = [a for a in attachments if a.get("type") == "file"]
                for f in file_parts:
                    try:
                        b64_data = f.get("data", "")
                        if "," in b64_data:
                            b64_data = b64_data.split(",")[-1]
                        file_bytes = base64.b64decode(b64_data)
                        fname = f.get("name", "file")
                        mime = f.get("mimeType", f.get("mime_type", "application/octet-stream"))
                        ext = ""
                        if "." in fname:
                            ext = "." + fname.rsplit(".", 1)[-1]
                        elif "/" in mime:
                            ext = "." + mime.split("/")[-1].split(";")[0]
                        tmp_dir = tempfile.mkdtemp(prefix="openmate_file_")
                        safe_name = fname.replace("/", "_").replace("\\", "_") or "file"
                        tmp_path = os.path.join(tmp_dir, safe_name if "." in safe_name else safe_name + ext)
                        with open(tmp_path, "wb") as fp:
                            fp.write(file_bytes)
                        saved_files.append(tmp_path)
                        prompt_text += f"\n[附件已保存到: {tmp_path}]"
                        logger.info(f"File saved: {tmp_path} ({len(file_bytes)} bytes)")
                    except Exception as e:
                        logger.error(f"File save error: {e}")
                        prompt_text += f"\n[附件: {f.get('name', 'file')} - 保存失败]"
                image_parts = [a for a in attachments if a.get("type") == "image"]
                for img in image_parts:
                    prompt_text += f"\n[图片附件: {img.get('mimeType', 'image/png')}]"

            rid = next_id()
            req = {
                "jsonrpc": "2.0",
                "id": rid,
                "method": "session.prompt",
                "params": {"prompt": prompt_text, "session_id": engine_session_id},
            }
            await engine_ws.send(json.dumps(req, ensure_ascii=False) + "\n")

            # Step 4: stream response
            async for raw in engine_ws:
                try:
                    msg = json.loads(raw.strip())
                except json.JSONDecodeError:
                    continue

                method = msg.get("method", "")
                msg_id_field = str(msg.get("id", ""))

                # ack for our prompt request
                if msg_id_field == rid and "result" in msg:
                    continue
                if msg_id_field == rid and "error" in msg:
                    err = msg["error"]
                    error_text = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    await _safe_send_ws(client_ws, {"type": "error", "message": f"Agent Engine错误: {error_text}"})
                    return accumulated_text, "soulmate", False

                if method == "session.event":
                    event_type = msg.get("params", {}).get("event_type", "")
                    if event_type == "message":
                        delta = msg.get("params", {}).get("content_delta", "")
                        if delta:
                            accumulated_text += delta
                            if not await _safe_send_ws(client_ws, {"type": "chunk", "text": delta}):
                                break
                    elif event_type == "completed":
                        logger.info(f"soulmate: completed, {len(accumulated_text)} chars")
                        return accumulated_text, "soulmate", True
                    elif event_type == "failed":
                        error_msg = msg.get("params", {}).get("error", "任务失败")
                        logger.error(f"soulmate: failed: {error_msg}")
                        await _safe_send_ws(client_ws, {"type": "error", "message": str(error_msg)})
                        return accumulated_text, "soulmate", False

            # WebSocket closed without completed/failed
            if accumulated_text:
                logger.warning(f"soulmate: WS closed with {len(accumulated_text)} chars accumulated")
                return accumulated_text, "soulmate", True
            return accumulated_text, "soulmate", False

    except OSError as e:
        logger.error(f"soulmate: cannot connect to Agent Engine: {e}")
        await _safe_send_ws(client_ws, {"type": "error", "message": "无法连接到SoulMate引擎，请检查服务是否运行"})
        return "", "error", False
    except Exception as e:
        logger.error(f"soulmate bridge error: {type(e).__name__}: {e}", exc_info=True)
        await _safe_send_ws(client_ws, {"type": "error", "message": f"SoulMate处理出错: {type(e).__name__}"})
        return "", "error", False


async def _safe_send_ws(websocket: WebSocket, data: dict) -> bool:
    """Send JSON to WebSocket, return False if send fails."""
    try:
        await websocket.send_json(data)
        return True
    except Exception:
        return False


@router.get("/health")
async def ws_chat_health():
    """live /health — 观测性数据聚合应答方。

    路由遮蔽修正（本轮集成校验live取证发现）：app.py:224 include_router(ws_router)
    先于app.py:319自身的@app.get("/health")注册，FastAPI首匹配胜出——本路由才是
    /health的真实应答方，app.py同名路由为死路由（此前多轮加进app.py health的
    agent_activity/tool_output/token_attribution键实际从未对外可见，live curl返回
    {"component":"WSChat"}无任何统计键）。

    观测性数据在此承载：懒import app.py的统计helper（运行时导入无循环依赖），逐key
    fail-safe——任何统计失败只丢该key不影响status=ok存活判定（evolution.py:360/
    dna_evolution.py:1233只消费status_code==200）。新增health观测字段请加在本handler。
    """
    payload: dict = {"status": "ok", "component": "WSChat"}
    # P0-4 peek三指标（"不知道它在干嘛"的行业首个完整实现——goose peek移植）
    try:
        from app import _activity_store
        payload["agent_activity"] = _activity_store().peek_all()["summary"]
    except Exception:
        pass
    # P0-2 工具结果溢出统计（AIHawk SHOWN/SENT双预算可观测）
    try:
        from app import _tool_output_stats
        _ts = _tool_output_stats()
        payload["tool_output"] = {
            k: _ts.get(k)
            for k in ("total_spills", "total_calls", "truncated_calls",
                      "sent_chars_total", "shown_chars_total",
                      "char_threshold", "line_threshold")
        }
    except Exception:
        pass
    # P1 token逐项归因摘要 + d439f163遗留#3估算校准状态（sample_count/calibrated/factor）
    try:
        from app import _token_attribution_stats
        _ta = _token_attribution_stats()
        payload["token_attribution"] = {
            "total_records": _ta.get("total_records", 0),
            **{k: _ta.get("summary", {}).get(k)
               for k in ("over_limit_records", "avg_total_tokens", "max_total_tokens",
                         "backfill_count", "avg_estimate_gap", "calibration")},
        }
    except Exception:
        pass
    # kilocode supplement3 #17：双限额上下文预算推导链（limits→reserved→usable→history_target）
    try:
        from agent.context_budget import budget_snapshot
        payload["context_budget"] = budget_snapshot()
    except Exception:
        pass
    # P0-4 ScheduleWakeup执行侧闭环（claude-code runtime到点重新唤醒agent）：
    # polls/claimed/fire_ok/fire_failed/claim_lost可观测——"到点有没有真醒"直接看这里
    try:
        from agent.wakeup_executor import get_wakeup_executor
        payload["wakeup_executor"] = get_wakeup_executor().snapshot()
    except Exception:
        pass
    # 唤醒回合实时可见推送面（11:11遗留#3跨进程relay）：rows_pushed/push_errors
    # 可观测——"唤醒回合推没推给前端"直接看这里
    try:
        from agent.wakeup_relay import relay_stats
        payload["wakeup_relay"] = relay_stats()
    except Exception:
        pass
    return payload


@router.get("/acp/status")
async def acp_status():
    acp = get_acp_process()
    # Idempotent warmup: previously a fresh proxy instance had no health
    # loop (only created inside start()), so running=false persisted until
    # the next send_message lazy-started the subprocess.
    was_running = acp.is_running
    running = acp.ensure_warmup()
    warming = (not was_running) and (not running)
    return {"running": running, "warming": warming}


class ACPMessage:
    def __init__(self, text: str, session_id: str | None = None):
        self.text = text
        self.session_id = session_id


@router.post("/acp/send")
async def acp_send(data: dict):
    """HTTP fallback for sending messages."""
    acp = get_acp_process()
    try:
        result = await acp.send_message(data.get("text", ""), data.get("session_id"))
        return {
            "ok": True,
            # response_text可能为None（空响应显式标记，P0修复）——coalesce为空串
            "content": result.get("response_text") or "",
            "source": result.get("source", "acp"),
            # Return the session id so HTTP clients can continue the same
            # session on follow-up requests (previously dropped — clients
            # had no way to do multi-turn over this endpoint, and the S4
            # systemic test's "same session" case silently created 3 new
            # sessions instead of exercising the interrupt queue).
            # 过期session恢复时此字段携带NEW session_id，客户端须重新绑定。
            "session_id": result.get("session_id") or data.get("session_id") or "",
            # 可观测性：过期session恢复标记（原session_id），None=无恢复发生
            "recovered_from_stale_session": result.get("recovered_from_stale_session"),
            # 可观测性：恢复策略收紧标记（2026-09-20晚轮）——
            # acp_empty_response=ACP侧空响应已发生；acp_stop_reason=空响应时的
            # stopReason；acp_recovery_skipped=True表示非stale签名（非refusal），
            # 按收紧策略未销毁session重建，走了CLI兜底（客户端可据此提示用户）。
            "acp_empty_response": result.get("acp_empty_response", False),
            "acp_recovery_skipped": result.get("acp_recovery_skipped", False),
            "acp_stop_reason": result.get("acp_stop_reason"),
        }
    except TimeoutError:
        return {"ok": False, "error": "请求超时，请重试"}
    except asyncio.CancelledError:
        # CancelledError is a BaseException — a bare `except Exception`
        # lets it escape to uvicorn, which returns a plain-text 500 that
        # breaks every JSON client ("Expecting value: line 1 column 1").
        # Surface it as a normal JSON error instead.
        logger.warning("HTTP send cancelled (process restart or client disconnect)")
        return {"ok": False, "error": "请求被中断（服务重启或连接断开），请重试"}
    except Exception as e:
        logger.error(f"HTTP send error: {e}")
        return {"ok": False, "error": "处理消息时出错，请重试"}


@router.post("/acp/send-image")
async def acp_send_image(data: dict):
    """HTTP fallback for sending images."""
    acp = get_acp_process()
    try:
        result = await acp.send_message_with_image(
            data.get("text", ""), data.get("image_data", ""),
            data.get("mime_type", "image/png"), data.get("session_id")
        )
        return {
            "ok": True,
            "content": result.get("response_text") or "",
            "source": result.get("source", "acp"),
            "session_id": result.get("session_id") or data.get("session_id") or "",
            "recovered_from_stale_session": result.get("recovered_from_stale_session"),
        }
    except TimeoutError:
        return {"ok": False, "error": "图片处理超时，请重试"}
    except asyncio.CancelledError:
        logger.warning("HTTP image send cancelled (process restart or client disconnect)")
        return {"ok": False, "error": "请求被中断（服务重启或连接断开），请重试"}
    except Exception as e:
        logger.error(f"HTTP image send error: {e}")
        return {"ok": False, "error": "处理图片时出错，请重试"}


@router.post("/acp/send-file")
async def acp_send_file(data: dict):
    """HTTP fallback for sending file attachments."""
    acp = get_acp_process()
    try:
        result = await acp.send_message_with_file(
            data.get("text", ""), data.get("file_data", ""),
            data.get("file_name", "file"), data.get("mime_type", "application/octet-stream"),
            data.get("session_id")
        )
        return {
            "ok": True,
            "content": result.get("response_text") or "",
            "source": result.get("source", "acp"),
            "session_id": result.get("session_id") or data.get("session_id") or "",
            "recovered_from_stale_session": result.get("recovered_from_stale_session"),
        }
    except TimeoutError:
        return {"ok": False, "error": "文件处理超时，请重试"}
    except asyncio.CancelledError:
        logger.warning("HTTP file send cancelled (process restart or client disconnect)")
        return {"ok": False, "error": "请求被中断（服务重启或连接断开），请重试"}
    except Exception as e:
        logger.error(f"HTTP file send error: {e}")
        return {"ok": False, "error": "处理文件时出错，请重试"}


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket):
    """[已废弃] WebSocket endpoint for real-time chat.

    ⚠️ 此端点已废弃，请迁移到 /ws/acp（ACP JSON-RPC 2.0纯透传）。
    旧协议继续可用，但不再维护新功能。

    Protocol:
    - Client: {"type":"message","text":"...","mode":"hermes|acp|agent_proxy","session_id":"...","agent_id":"...","attachments":[...]}
    - Server: {"type":"thinking"} / {"type":"chunk","text":"..."} / {"type":"done","text":"...","source":"..."} / {"type":"error","message":"..."}
    """
    await websocket.accept()

    # 发送废弃警告 — 客户端应迁移到 /ws/acp
    await _safe_send_ws(websocket, {
        "type": "deprecation_warning",
        "message": "/ws/chat 已废弃，请迁移到 /ws/acp (ACP JSON-RPC 2.0)",
        "new_endpoint": "/ws/acp",
        "docs": "https://github.com/anthropics/agent-client-protocol",
    })

    token = websocket.query_params.get("token", "")
    if not token:
        await _safe_send_ws(websocket, {"type": "error", "message": "缺少认证令牌"})
        await websocket.close()
        return

    user_id = decode_token(token)
    if not user_id:
        await _safe_send_ws(websocket, {"type": "error", "message": "认证令牌无效或已过期"})
        await websocket.close()
        return

    await _safe_send_ws(websocket, {"type": "connected", "user_id": str(user_id)})
    logger.info(f"WebSocket connected: user={user_id}")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "message":
                text = data.get("text", "").strip()
                mode = data.get("mode", "hermes")
                # SoulMate sends mode='openmate', treat as hermes (no agent)
                if mode == "openmate":
                    mode = "hermes"
                session_id = data.get("session_id")
                agent_id = data.get("agent_id")
                attachments = data.get("attachments", [])

                if not text and attachments:
                    image_parts = [a for a in attachments if a.get("type") == "image"]
                    file_parts = [a for a in attachments if a.get("type") == "file"]
                    if image_parts:
                        text = "用户发送了一张图片"
                    elif file_parts:
                        text = f"用户发送了文件: {', '.join(a.get('name', 'file') for a in file_parts)}"

                if not text:
                    await _safe_send_ws(websocket, {"type": "error", "message": "消息内容为空"})
                    continue

                await _safe_send_ws(websocket, {"type": "thinking"})

                try:
                    image_attachments = [a for a in attachments if a.get("type") == "image"]
                    file_attachments = [a for a in attachments if a.get("type") == "file"]

                    if mode == "agent_proxy" and agent_id:
                        # Create or get agent session for persistence
                        agent_session_id = _create_or_get_agent_session(agent_id, session_id)
                        # Handle file attachments — save to temp and append path to text
                        if file_attachments:
                            import tempfile, base64 as b64mod, os
                            file_paths = []
                            for f in file_attachments:
                                b64 = f.get("data", "")
                                if "," in b64:
                                    b64 = b64.split(",")[-1]
                                ext = os.path.splitext(f.get("name", "file"))[1] or ".bin"
                                fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="openmate_file_")
                                with os.fdopen(fd, "wb") as fp:
                                    fp.write(b64mod.b64decode(b64))
                                file_paths.append(tmp_path)
                                logger.info(f"File saved for agent_proxy: {tmp_path} ({f.get('name', 'file')})")
                            text = text + "\n\n" + "\n".join(f"[文件已保存到: {p}]" for p in file_paths)
                        _store_agent_message(agent_session_id, "user", text)

                        if agent_id == "soulmate":
                            # Soulmate模式：通过WebSocket桥接Agent Engine(8787)
                            # forward_to_agent_engine直接流式发送chunks/done，跳过后续处理
                            response_text, source, success = await forward_to_agent_engine(
                                websocket, text, session_id, attachments,
                            )
                            if response_text:
                                _store_agent_message(agent_session_id, "assistant", response_text)
                            if not session_id:
                                db = _get_agent_db()
                                try:
                                    db.execute(
                                        "UPDATE agent_sessions SET title = ? WHERE id = ? AND title = 'New Chat'",
                                        (text[:50], agent_session_id),
                                    )
                                    db.commit()
                                finally:
                                    db.close()
                            continue
                        else:
                            response_text, source, success = await run_agent_proxy(agent_id, text)

                        if response_text:
                            _store_agent_message(agent_session_id, "assistant", response_text)
                        # Update title from first user message
                        if not session_id:
                            db = _get_agent_db()
                            try:
                                db.execute(
                                    "UPDATE agent_sessions SET title = ? WHERE id = ? AND title = 'New Chat'",
                                    (text[:50], agent_session_id),
                                )
                                db.commit()
                            finally:
                                db.close()
                    else:
                        acp = get_acp_process()
                        if image_attachments and mode in ("hermes", "acp"):
                            img = image_attachments[0]
                            b64 = img.get("data", "")
                            if "," in b64:
                                b64 = b64.split(",")[-1]
                            parts = []
                            if text:
                                parts.append({"type": "text", "text": text})
                            parts.append({"type": "image", "data": b64, "mimeType": img.get("mime_type", "image/png")})
                            async for chunk_data in acp.stream_message_parts(parts, session_id):
                                if not await _safe_send_ws(websocket, chunk_data):
                                    break
                            continue
                        elif file_attachments and mode in ("hermes", "acp"):
                            f = file_attachments[0]
                            import tempfile, base64 as b64mod, os
                            b64 = f.get("data", "")
                            if "," in b64:
                                b64 = b64.split(",")[-1]
                            ext = os.path.splitext(f.get("name", "file"))[1] or ".bin"
                            fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="openmate_file_")
                            with os.fdopen(fd, "wb") as fp:
                                fp.write(b64mod.b64decode(b64))
                            prompt_text = text or f"用户发送了文件: {f.get('name', 'file')}"
                            prompt_text += f"\n\n[文件已保存到: {tmp_path}]"
                            parts = [{"type": "text", "text": prompt_text}]
                            async for chunk_data in acp.stream_message_parts(parts, session_id):
                                if not await _safe_send_ws(websocket, chunk_data):
                                    break
                            continue
                        else:
                            # Real streaming: forward chunks as they arrive
                            async for chunk_data in acp.stream_message(text, session_id):
                                if not await _safe_send_ws(websocket, chunk_data):
                                    break
                            continue

                    if response_text:
                        # agent_proxy: stream in chunks for real-time feel
                        chunk_size = 20
                        for i in range(0, len(response_text), chunk_size):
                            chunk = response_text[i : i + chunk_size]
                            if not await _safe_send_ws(websocket, {"type": "chunk", "text": chunk}):
                                break
                            await asyncio.sleep(0.05)
                        done_msg = {"type": "done", "text": response_text, "source": source}
                        if mode == "agent_proxy" and agent_id:
                            done_msg["session_id"] = agent_session_id
                        await _safe_send_ws(websocket, done_msg)
                    else:
                        await _safe_send_ws(websocket, {"type": "error", "message": "未收到响应，请重试"})

                except TimeoutError:
                    logger.warning("WS chat prompt timeout")
                    await _safe_send_ws(websocket, {"type": "error", "message": "响应超时，请重试"})
                except (BrokenPipeError, OSError, ConnectionResetError) as e:
                    logger.warning(f"WS chat pipe error: {e}")
                    # Try once more
                    try:
                        acp = get_acp_process()
                        result = await acp.send_message(text, session_id)
                        response_text = result.get("response_text", "")
                        source = result.get("source", "hermes")
                        if response_text:
                            await _safe_send_ws(websocket, {"type": "chunk", "text": response_text})
                            await _safe_send_ws(websocket, {"type": "done", "text": response_text, "source": source})
                        else:
                            await _safe_send_ws(websocket, {"type": "error", "message": "连接已断开，请重试"})
                    except Exception as retry_e:
                        logger.error(f"WS chat retry failed: {retry_e}")
                        await _safe_send_ws(websocket, {"type": "error", "message": "连接已断开，请重试"})
                except Exception as e:
                    logger.error(f"WS chat error: {type(e).__name__}: {e}", exc_info=True)
                    await _safe_send_ws(websocket, {"type": "error", "message": "处理消息时出错，请重试"})

            elif msg_type == "ping":
                await _safe_send_ws(websocket, {"type": "pong"})

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: user={user_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {type(e).__name__}: {e}")
        try:
            await _safe_send_ws(websocket, {"type": "error", "message": "连接异常断开"})
        except Exception:
            pass
