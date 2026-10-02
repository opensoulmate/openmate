"""ACP proxy — stable version with proper lifecycle management.

Key fixes over original:
1. Chunks tagged by msg_id (no broadcast cross-contamination)
2. 90s prompt timeout (was 15s)
3. Proper cleanup on timeout/cancel
4. File + console logging
5. Subprocess restart with backoff
6. id=0 system message filtering
7. Health loop resilient to restart failures
"""

import asyncio
import base64
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any

# relay轨迹观察者（10:02轮遗留#3销账）：HTTP /acp/send入口的hermes回合记账
from agent.relay_trajectory import RelayTrajectory
from agent.trajectory_recorder import TrajectoryRecorder

# --- Logging setup ---
LOG_DIR = "/tmp"
LOG_FILE = os.path.join(LOG_DIR, "acp-proxy.log")

logger = logging.getLogger("acp-proxy")
logger.setLevel(logging.DEBUG)

# File handler — persistent, rotatable
_fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_fh.setLevel(logging.DEBUG)
logger.addHandler(_fh)

# Console handler
_ch = logging.StreamHandler()
_ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_ch.setLevel(logging.INFO)
logger.addHandler(_ch)


@dataclass
class PendingPrompt:
    """State for a single in-flight prompt."""
    msg_id: str
    # ACP session this prompt belongs to — used to filter session/update
    # chunk notifications by params.sessionId under concurrency.
    session_id: str = ""
    chunks: list[str] = field(default_factory=list)
    response: dict = field(default_factory=dict)
    done: asyncio.Event = field(default_factory=asyncio.Event)


class ACPProcess:
    def __init__(self, traj_recorder: Any = None):
        # traj_recorder: TrajectoryRecorder或测试替身（duck-typed，可注入）
        self._proc: asyncio.subprocess.Process | None = None
        self._msg_id: int = 0
        self._initialized: bool = False
        self._default_session_id: str | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._health_task: asyncio.Task | None = None
        self._restart_count: int = 0
        self._last_restart: float = 0
        # Pending RPC futures, keyed by msg_id
        self._rpc_pending: dict[str, asyncio.Future] = {}
        # Pending prompts, keyed by msg_id
        self._prompt_pending: dict[str, PendingPrompt] = {}
        # Track the expected prompt msg_id for id=0 filtering
        self._active_prompt_ids: set[str] = set()
        # ── P0-9 插话队列（Khoj interrupt_queue模式）──
        # Keyed by session_id, value = list of queued messages
        self._interrupt_queue: dict[str, list[dict]] = {}
        # Track which sessions are currently processing
        self._sessions_busy: set[str] = set()
        # Per-session execution locks — concurrent messages to the same
        # session are serialized FIFO (asyncio.Lock wakes waiters in order).
        self._session_locks: dict[str, asyncio.Lock] = {}
        # Serializes start()/restart so concurrent callers can't double-spawn
        self._start_lock: asyncio.Lock = asyncio.Lock()
        # Serializes session/new; callers must use ITS return value, never
        # re-read the shared _default_session_id after await (race under
        # concurrent new-session requests — S4 same-session concurrency test).
        self._new_session_lock: asyncio.Lock = asyncio.Lock()
        # Fire-and-forget warmup task (ensure_warmup); also gives the health
        # loop a chance to exist — _health_task is only created inside
        # start(), so a fresh instance has NO self-heal until first start.
        self._warmup_task: asyncio.Task | None = None
        # ── relay轨迹观察者（10:02轮遗留#3销账）──
        # HTTP /acp/send（本文件硬编码hermes acp路由）此前不经ws_acp观察者，
        # 该入口的hermes回合零记账——/trajectory观察面对HTTP入口全盲。
        # per-session观察者（跨会话并发各记各的；同会话已被_session_locks
        # FIFO串行=单回合在途）+共享recorder（测试可注入替身）。
        self._traj_relays: dict[str, RelayTrajectory] = {}
        self._traj_prompt_sid: dict[str, str] = {}
        self._traj_recorder: TrajectoryRecorder | None = traj_recorder

    @property
    def is_running(self) -> bool:
        if self._proc is None or self._proc.returncode is not None:
            return False
        if self._proc.stdin and self._proc.stdin.is_closing():
            return False
        return True

    def ensure_warmup(self) -> bool:
        """Idempotent fire-and-forget start so status checks can bring the
        subprocess (and its health loop) up without waiting for a send."""
        if not self.is_running and (self._warmup_task is None or self._warmup_task.done()):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return self.is_running

            def _done(task: asyncio.Task):
                if not task.cancelled() and task.exception():
                    logger.error(f"Warmup start failed: {task.exception()}")

            self._warmup_task = loop.create_task(self.start())
            self._warmup_task.add_done_callback(_done)
        return self.is_running

    async def start(self):
        async with self._start_lock:
            return await self._start_locked()

    async def _start_locked(self):
        if self.is_running:
            return self._get_agent_info()

        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "hermes", "acp", "--accept-hooks",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(f"ACP subprocess started pid={self._proc.pid}")
        except FileNotFoundError:
            logger.error("hermes binary not found in PATH")
            raise RuntimeError("hermes binary not found")
        except Exception as e:
            logger.error(f"Failed to start ACP subprocess: {e}")
            raise

        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._health_task = asyncio.create_task(self._health_loop())

        try:
            await self._rpc(
                "initialize",
                {"protocolVersion": 1, "clientInfo": {"name": "OpenMate", "version": "1.0.0"}},
            )
            self._initialized = True
            logger.info("ACP initialized successfully")
        except Exception as e:
            logger.error(f"ACP initialize failed: {e}")
            await self.stop()
            raise

        try:
            await self.new_session()
            logger.info(f"ACP default session={self._default_session_id}")
        except Exception as e:
            logger.error(f"ACP session/new failed: {e}")
            await self.stop()
            raise

        self._restart_count = 0
        return self._get_agent_info()

    async def _drain_stderr(self):
        """Drain stderr to prevent buffer deadlock."""
        try:
            while self._proc and self._proc.returncode is None:
                try:
                    raw = await asyncio.wait_for(self._proc.stderr.readline(), timeout=1.0)
                except (asyncio.TimeoutError, OSError):
                    continue
                if not raw:
                    break
                line = raw.decode(errors='replace').rstrip()
                if line:
                    logger.debug(f"ACP stderr: {line}")
        except Exception:
            pass

    async def stop(self):
        logger.info("ACP stopping...")
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
            self._health_task = None
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None
        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except (asyncio.CancelledError, Exception):
                pass
            self._stderr_task = None
        # Resolve all pending RPC futures with a *catchable* exception.
        # NOTE: fut.cancel() made awaiters receive asyncio.CancelledError
        # (a BaseException) which escapes `except Exception` in endpoints →
        # uvicorn ASGI 500 with a plain-text body → clients parsing JSON
        # crash ("Expecting value: line 1 column 1"). BrokenPipeError flows
        # into the existing retry/CLI-fallback path instead.
        for fut in self._rpc_pending.values():
            if not fut.done():
                fut.set_exception(BrokenPipeError("ACP process stopped"))
        self._rpc_pending.clear()
        # Signal all pending prompts as done (with error)
        for p in self._prompt_pending.values():
            if not p.done.is_set():
                p.response = {"error": "ACP process stopped"}
                p.done.set()
        self._prompt_pending.clear()
        self._active_prompt_ids.clear()
        if self._proc:
            try:
                self._proc.terminate()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=5)
                except Exception:
                    self._proc.kill()
            except ProcessLookupError:
                pass
            self._proc = None
        self._initialized = False
        self._default_session_id = None
        logger.info("ACP stopped")

    async def _restart(self):
        now = time.time()
        # Back off: max 1 restart per 5 seconds
        if now - self._last_restart < 5:
            logger.warning("Restart throttled (too frequent)")
            await asyncio.sleep(5)
        self._last_restart = time.time()
        self._restart_count += 1
        logger.warning(f"ACP restarting (attempt #{self._restart_count})")
        await self.stop()
        try:
            await self.start()
            logger.info("ACP restart successful")
        except Exception as e:
            logger.error(f"ACP restart failed: {e}")
            raise

    async def _health_loop(self):
        """Periodically check if hermes acp is responsive; auto-restart if stuck.
        
        连续3次超时才重启，避免LLM长回复时误杀。
        """
        consecutive_timeouts = 0
        while True:
            await asyncio.sleep(30)
            if not self.is_running:
                # Self-heal regardless of _initialized — previously a process
                # that died after stop() (_initialized=False) was NEVER
                # restarted by this loop; /acp/status stayed running=false
                # until the next send_message happened to lazy-start it.
                logger.warning("Health check: process not running, attempting restart")
                try:
                    await self._restart()
                    consecutive_timeouts = 0
                except Exception as e:
                    logger.error(f"Health-triggered restart failed: {e}")
                continue
            if not self._initialized:
                continue
            try:
                await asyncio.wait_for(
                    self._rpc("session/list", {}), timeout=10
                )
                consecutive_timeouts = 0  # 重置计数
            except (BrokenPipeError, OSError) as e:
                logger.warning(f"Health check pipe error: {e}")
                consecutive_timeouts = 0
                try:
                    await self._restart()
                except Exception as e2:
                    logger.error(f"Health-triggered restart failed: {e2}")
            except TimeoutError:
                consecutive_timeouts += 1
                logger.warning(f"Health check timeout ({consecutive_timeouts}/3)")
                if consecutive_timeouts >= 3:
                    logger.warning("Health check: 3 consecutive timeouts, restarting ACP")
                    try:
                        await self._restart()
                        consecutive_timeouts = 0
                    except Exception as e:
                        logger.error(f"Health-triggered restart failed: {e}")
            except Exception as e:
                logger.warning(f"Health check unexpected error: {e}")

    async def _read_loop(self):
        """Background task: continuously read stdout, dispatch by msg_id."""
        try:
            while self._proc and self._proc.returncode is None:
                try:
                    raw = await asyncio.wait_for(self._proc.stdout.readline(), timeout=1.0)
                except (asyncio.TimeoutError, OSError):
                    continue
                if not raw:
                    logger.warning("read_loop: EOF on stdout")
                    break

                msg = self._parse(raw)
                if not msg:
                    continue

                msg_id = str(msg.get("id", ""))
                method = msg.get("method", "")

                # Skip system messages with id=0
                if msg_id == "0":
                    logger.debug(f"Ignoring system message id=0: {method}")
                    continue

                # relay轨迹观察（10:02轮遗留#3销账，旁路never-raise）：
                # 消息流→per-session观察者→trajectory（转发内容零改动）。
                # 放在id=0过滤之后=只记用户可见面（与chunk收集同口径）。
                self._traj_observe_agent(msg)

                # RPC response
                if msg_id and msg_id in self._rpc_pending:
                    fut = self._rpc_pending.pop(msg_id)
                    if not fut.done():
                        if "error" in msg:
                            err = msg["error"]
                            logger.warning(f"RPC error for id={msg_id}: {err}")
                            fut.set_exception(Exception(str(err)))
                        else:
                            fut.set_result(msg.get("result", {}))
                    continue

                # Prompt chunk event
                if method == "session/update":
                    params = msg.get("params", {})
                    update = params.get("update", {})
                    su = update.get("sessionUpdate", "")
                    if su == "agent_message_chunk":
                        content = update.get("content", {})
                        if isinstance(content, dict) and content.get("type") == "text" and content.get("text"):
                            # ACP session/update notifications carry a required
                            # sessionId (acp.schema.SessionNotification). Route
                            # each chunk ONLY to pending prompts of that session
                            # — broadcasting to all active prompts mixed
                            # concurrent responses together (S4 systemic test
                            # observed 3 different sessions all returning
                            # identical 245-char content).
                            update_sid = str(params.get("sessionId", "") or "")
                            for pid, p in self._prompt_pending.items():
                                if p.done.is_set() or pid not in self._active_prompt_ids:
                                    continue
                                if not update_sid or not p.session_id or p.session_id == update_sid:
                                    p.chunks.append(content["text"])
                    continue

                # Prompt response (id matches a pending prompt)
                if msg_id and msg_id in self._prompt_pending:
                    p = self._prompt_pending[msg_id]
                    if not p.done.is_set():
                        p.response = msg.get("result", {})
                        if "error" in msg:
                            p.response = {"error": str(msg["error"])}
                        logger.info(
                            f"Prompt response id={msg_id}, "
                            f"stopReason={p.response.get('stopReason')}, "
                            f"chunks={len(p.chunks)}"
                        )
                        self._active_prompt_ids.discard(msg_id)
                        p.done.set()
                    continue

                # Unhandled message
                logger.debug(f"Unhandled msg id={msg_id} method={method}")

        except asyncio.CancelledError:
            logger.info("read_loop cancelled")
        except Exception as e:
            logger.error(f"read_loop error: {e}", exc_info=True)

        # If we exit the loop, signal all pending prompts
        for pid, p in self._prompt_pending.items():
            if not p.done.is_set():
                p.response = {"error": "ACP read loop exited"}
                self._active_prompt_ids.discard(pid)
                p.done.set()
        # relay轨迹收尾（失败必须可见）：子进程死了，所有未收口回合判error
        self._traj_finalize_all("error")

    # ── relay轨迹观察者（10:02轮遗留#3销账）────────────────────────
    # ws_acp同款RelayTrajectory旁路观察（转发/响应内容零改动，CowAgent
    # 记账哲学：observe同步never-raise，落账走观察者后台单worker）。

    def _traj_relay_for(self, sid: str) -> RelayTrajectory | None:
        """per-session观察者（lazy建）。sid为空→None（绝不写无主span）。"""
        try:
            if not sid:
                return None
            relay = self._traj_relays.get(sid)
            if relay is None:
                rec = self._traj_recorder
                if rec is None:
                    rec = TrajectoryRecorder()
                    self._traj_recorder = rec
                relay = RelayTrajectory(
                    agent_id="hermes",
                    session_id_getter=lambda s=sid: s,
                    recorder=rec,
                )
                self._traj_relays[sid] = relay
            return relay
        except Exception as e:
            logger.debug(f"[relay-traj] relay_for failed (non-fatal): {e}")
            return None

    def _traj_observe_client(self, request: dict, sid: str) -> None:
        """session/prompt发出前观察（回合起点）。never-raise。"""
        try:
            relay = self._traj_relay_for(sid)
            if relay is None:
                return
            msg_id = str(request.get("id", "") or "")
            if msg_id:
                self._traj_prompt_sid[msg_id] = sid
            relay.observe_client(request)
        except Exception as e:
            logger.debug(f"[relay-traj] observe_client failed (non-fatal): {e}")

    def _traj_observe_agent(self, msg) -> None:
        """子进程→客户端消息观察：session/update按sessionId路由，
        prompt响应按msg_id→sid路由（超时后迟到响应自动no-op）。never-raise。"""
        try:
            if not isinstance(msg, dict):
                return
            if msg.get("method") == "session/update":
                sid = str((msg.get("params") or {}).get("sessionId") or "")
                if sid:
                    relay = self._traj_relays.get(sid)
                    if relay is not None:
                        relay.observe_agent(msg)
                else:
                    # sessionId缺失（协议违规）：广播——只影响有未收口回合的观察者
                    for relay in list(self._traj_relays.values()):
                        relay.observe_agent(msg)
                return
            msg_id = str(msg.get("id", "") or "")
            if not msg_id or ("result" not in msg and "error" not in msg):
                return
            sid = self._traj_prompt_sid.pop(msg_id, None)
            if not sid:
                return
            relay = self._traj_relays.get(sid)
            if relay is not None:
                relay.observe_agent(msg)
        except Exception as e:
            logger.debug(f"[relay-traj] observe_agent failed (non-fatal): {e}")

    def _traj_fail_turn(self, msg_id: str, sid: str) -> None:
        """回合确定已死（写管道失败/超时）：收口未完成turn为error（不悬挂root）。"""
        try:
            self._traj_prompt_sid.pop(msg_id, None)
            relay = self._traj_relays.get(sid)
            if relay is not None:
                relay.finalize_open("error")
        except Exception as e:
            logger.debug(f"[relay-traj] fail_turn failed (non-fatal): {e}")

    def _traj_finalize_all(self, status: str = "error") -> None:
        """子进程死亡收尾：所有未收口回合判error（失败必须可见）。"""
        try:
            for relay in list(self._traj_relays.values()):
                relay.finalize_open(status)
            self._traj_prompt_sid.clear()
        except Exception as e:
            logger.debug(f"[relay-traj] finalize_all failed (non-fatal): {e}")

    def _traj_cli_turn(self, text: str, result: dict, sid: str, duration_ms: float = 0.0) -> None:
        """CLI兜底回合记账：ACP失败/空响应后hermes -z给用户的回答也必须在
        观察面可见（否则只见error回合，与用户实际拿到回答对不上）。never-raise。"""
        try:
            relay = self._traj_relay_for(sid)
            if relay is None:
                return
            relay.observe_cli_turn(
                text,
                result.get("response_text") or "",
                stop_reason=str(result.get("stop_reason") or result.get("stopReason") or ""),
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.debug(f"[relay-traj] cli_turn failed (non-fatal): {e}")

    async def send_message(self, text: str, session_id: str | None = None) -> dict[str, Any]:
        if not self.is_running or not self._initialized:
            await self.start()
        # Empty string = explicitly new session (frontend "+" button)
        if session_id == "":
            sid = await self._fresh_session_sid()
        else:
            sid = session_id or self._default_session_id or "default"
        if sid and len(sid) < 36 and sid != "default":
            sid = self._default_session_id or "default"

        # ── P0-9 插话队列：同session消息FIFO串行执行 ──
        # Previous implementation polled _sessions_busy with sleep(0.5) and
        # popped a shared queue after waking — two waiters could both observe
        # "not busy", both pop, and run the SAME session concurrently. A
        # per-session asyncio.Lock gives atomic FIFO serialization, and each
        # request executes ITS OWN text (a shared pop(0) could answer another
        # request's question under reordering).
        lock = self._session_locks.setdefault(sid, asyncio.Lock())
        queued_at = time.time()
        self._interrupt_queue.setdefault(sid, []).append({"text": text, "queued_at": queued_at})
        if len(self._interrupt_queue[sid]) > 1:
            logger.info(f"Interrupt queued for session {sid}: pos={len(self._interrupt_queue[sid])}")
        try:
            async with lock:
                self._sessions_busy.add(sid)
                try:
                    result = await self._send_message_inner(text, sid)
                    # 空响应重试一次（ACP可能在处理排队消息时返回空）
                    if not result.get("response_text"):
                        logger.warning(f"Empty response for session {sid}, retrying once")
                        await asyncio.sleep(1)
                        result = await self._send_message_inner(text, sid)
                    result.setdefault("session_id", sid)
                    return result
                finally:
                    self._sessions_busy.discard(sid)
        finally:
            # Bookkeeping only (observability): remove this request's entry
            q = self._interrupt_queue.get(sid, [])
            for i, m in enumerate(q):
                if m.get("queued_at") == queued_at:
                    del q[i]
                    break
            if not q:
                self._interrupt_queue.pop(sid, None)
            remaining = len(self._interrupt_queue.get(sid, []))
            if remaining:
                logger.info(f"Session {sid}: {remaining} queued messages remaining")

    async def _fresh_session_sid(self) -> str:
        """Create a new ACP session and return the sid THAT call created.

        Concurrent callers must not read the shared _default_session_id
        after await — whichever session/new finished last wins the shared
        attr, so two callers could both grab the same sid (observed race in
        the S4 same-session concurrency test where session_id="" requests
        all funneled into racing new_session calls).
        """
        async with self._new_session_lock:
            resp = await self.new_session()
        sid = resp.get("sessionId") or resp.get("session_id")
        return sid or self._default_session_id or "default"

    async def _send_message_inner(self, text: str, sid: str) -> dict[str, Any]:
        """send_message的实际执行逻辑（不含插话队列管理）"""
        # Track whether the adapter responded with an empty/failed result
        # (as opposed to raising pipe/timeout errors) — stale-session
        # recovery is only meaningful in the former case.
        got_empty_acp_response = False
        # 收紧（2026-09-20晚轮，d439f163遗留#5销账）：记录空响应时的stop_reason，
        # 恢复策略据此区分「stale-session签名」vs「模型侧合法空输出」。
        last_stop_reason = ""
        for attempt in range(2):
            try:
                result = await self._prompt(text, sid)
                if result.get("response_text"):
                    result["session_id"] = sid
                    return result
                got_empty_acp_response = True
                last_stop_reason = str(result.get("stop_reason") or "")
                logger.warning(
                    f"No chunks captured (attempt {attempt+1}, "
                    f"stopReason={result.get('stop_reason')}, session={sid})"
                )
            except TimeoutError as te:
                logger.warning(f"Prompt timeout (attempt {attempt+1}): {te}")
                if attempt == 0:
                    try:
                        await self._restart()
                        # ACP重启后旧session_id失效，创建新session
                        await self.new_session()
                        sid = self._default_session_id or "default"
                    except Exception:
                        pass
                    continue
            except (BrokenPipeError, OSError) as e:
                logger.warning(f"Pipe error (attempt {attempt+1}): {e}")
                if attempt == 0:
                    try:
                        await self._restart()
                        # ACP重启后旧session_id失效，创建新session
                        await self.new_session()
                        sid = self._default_session_id or "default"
                    except Exception as re:
                        logger.error(f"Restart failed: {re}")
                        break
                    continue
            except Exception as e:
                logger.error(f"Unexpected error (attempt {attempt+1}): {e}", exc_info=True)
                break
            break
        # ── P0修复（2026-09-20）：过期session恢复（kilocode/goose会话恢复模式）──
        # adapter重启后旧session全部失效（"session not found"→refusal+0 chunks），
        # 客户端持旧session_id时旧代码返回ok:true空响应。显式恢复：新建session
        # 重发一次，返回新sid+recovered_from_stale_session标记（AIHawk原则：
        # 失败/恢复必须显式可见），客户端据响应中的新session_id重新绑定。
        # ── 策略收紧（2026-09-20晚轮）：仅stale-session签名触发恢复 ──
        # live实证签名：adapter "prompt: session xxx not found" →
        # stopReason=refusal + 0 chunks（/tmp/ws_recovery_e2e.py基线）。
        # 非refusal空响应（end_turn空文本/仅tool-call回合等）=模型侧合法空输出，
        # 此时销毁有效session上下文重建新会话是净损失 → 跳过恢复直接CLI兜底，
        # 但结果必须显式携带acp_recovery_skipped+acp_stop_reason标记
        # （AIHawk显式标记原则 + mem0"禁止静默降级"，evolution-engine-patterns §1.1）。
        recovery_attempted = False
        if got_empty_acp_response and last_stop_reason == "refusal":
            recovery_attempted = True
            try:
                resp = await self.new_session()
                new_sid = resp.get("sessionId") or resp.get("session_id") or self._default_session_id
                if new_sid and new_sid != sid:
                    logger.warning(
                        f"Stale-session recovery: {sid} → fresh session {new_sid}, re-prompting"
                    )
                    result = await self._prompt(text, new_sid)
                    if result.get("response_text"):
                        result["session_id"] = new_sid
                        result["recovered_from_stale_session"] = sid
                        return result
                    # fresh session仍空 → 更新sid继续走CLI兜底
                    sid = new_sid
            except Exception as e:
                logger.error(f"Stale-session recovery failed: {e}")
        elif got_empty_acp_response:
            logger.warning(
                f"Empty ACP response with non-stale stopReason="
                f"{last_stop_reason or 'n/a'} — stale-session recovery skipped "
                f"(session {sid} preserved), CLI fallback with explicit markers"
            )
        # Fallback to CLI — 结果必须携带session_id，客户端才能续接会话
        t_cli = time.time()
        result = await self._cli(text)
        # relay轨迹（10:02轮遗留#3销账）：CLI兜底回合同样记账（禁止静默降级
        # 的观察面语义——用户拿到了回答，观察面不能只有ACP失败回合）
        self._traj_cli_turn(text, result, sid, (time.time() - t_cli) * 1000.0)
        result["session_id"] = sid
        if got_empty_acp_response:
            # ACP侧空响应对客户端可见（禁止静默降级）；恢复策略决策同样可见
            result["acp_empty_response"] = True
            result["acp_stop_reason"] = last_stop_reason or None
            if not recovery_attempted:
                result["acp_recovery_skipped"] = True
        return result

    async def send_message_with_image(
        self, text: str, image_data: str, mime_type: str = "image/png", session_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.is_running or not self._initialized:
            await self.start()
        # Empty string = explicitly new session (frontend "+" button)
        if session_id == "":
            sid = await self._fresh_session_sid()
        else:
            sid = session_id or self._default_session_id or "default"
        if sid and len(sid) < 36 and sid != "default":
            sid = self._default_session_id or "default"
        for attempt in range(2):
            try:
                parts = []
                if text:
                    parts.append({"type": "text", "text": text})
                b64 = image_data.split(",")[-1] if "," in image_data else image_data
                parts.append({"type": "image", "data": b64, "mimeType": mime_type})
                result = await self._prompt_parts(parts, sid)
                if result.get("response_text") is not None:
                    result.setdefault("session_id", sid)
                    return result
            except (BrokenPipeError, OSError, TimeoutError) as e:
                logger.warning(f"Image prompt error (attempt {attempt+1}): {e}")
                if attempt == 0:
                    try:
                        await self._restart()
                    except Exception:
                        pass
                    continue
            except Exception as e:
                logger.error(f"Image prompt error: {e}", exc_info=True)
            break
        # Fallback: save image to temp file
        tmp_path = None
        try:
            b64_clean = image_data.split(",")[-1] if "," in image_data else image_data
            ext = mime_type.split("/")[-1].split(";")[0] or "png"
            fd, tmp_path = tempfile.mkstemp(suffix=f".{ext}", prefix="openmate_img_")
            with os.fdopen(fd, "wb") as f:
                f.write(base64.b64decode(b64_clean))
            prompt = f"{text or '用户发送了一张图片'}\n\n[图片已保存到: {tmp_path}]"
            result = await self._cli(prompt)
            result["session_id"] = sid
            return result
        except Exception as e:
            logger.error(f"Image fallback error: {e}")
            result = await self._cli(text or "用户发送了一张图片")
            result["session_id"] = sid
            return result
        finally:
            if tmp_path:
                asyncio.get_running_loop().call_later(
                    300, lambda: os.unlink(tmp_path) if os.path.exists(tmp_path) else None
                )

    async def send_message_with_file(
        self, text: str, file_data: str, file_name: str = "file",
        mime_type: str = "application/octet-stream", session_id: str | None = None,
    ) -> dict[str, Any]:
        """Send a file attachment to the agent.

        Saves the base64-encoded file to a temp directory and passes the path
        to the agent so it can read/process the file.
        """
        if not self.is_running or not self._initialized:
            await self.start()
        # Empty string = explicitly new session (frontend "+" button)
        if session_id == "":
            sid = await self._fresh_session_sid()
        else:
            sid = session_id or self._default_session_id or "default"
        if sid and len(sid) < 36 and sid != "default":
            sid = self._default_session_id or "default"

        # Decode and save file
        tmp_path = None
        try:
            b64_clean = file_data.split(",")[-1] if "," in file_data else file_data
            # Preserve original extension if present
            ext = ""
            if "." in file_name:
                ext = "." + file_name.rsplit(".", 1)[-1]
            elif "/" in mime_type:
                ext = "." + mime_type.split("/")[-1].split(";")[0]

            tmp_dir = tempfile.mkdtemp(prefix="openmate_file_")
            safe_name = file_name.replace("/", "_").replace("\\", "_") or "file"
            tmp_path = os.path.join(tmp_dir, safe_name if "." in safe_name else safe_name + ext)
            with open(tmp_path, "wb") as f:
                f.write(base64.b64decode(b64_clean))

            file_size = os.path.getsize(tmp_path)
            logger.info(f"File saved: {tmp_path} ({file_size} bytes, {mime_type})")

            # Build prompt with file path
            prompt_text = text or f"用户发送了文件: {file_name}"
            prompt_text += f"\n\n[文件已保存到: {tmp_path}]"

            # Try ACP prompt first
            for attempt in range(2):
                try:
                    result = await self._prompt(prompt_text, sid)
                    if result.get("response_text") is not None:
                        result.setdefault("session_id", sid)
                        return result
                except (BrokenPipeError, OSError, TimeoutError) as e:
                    logger.warning(f"File prompt error (attempt {attempt+1}): {e}")
                    if attempt == 0:
                        try:
                            await self._restart()
                        except Exception:
                            pass
                        continue
                except Exception as e:
                    logger.error(f"File prompt error: {e}", exc_info=True)
                break

            # Fallback to CLI — CLI结果携带session_id（过期session时客户端可续接）
            result = await self._cli(prompt_text)
            result["session_id"] = sid
            return result

        except Exception as e:
            logger.error(f"File send error: {e}")
            result = await self._cli(text or f"用户发送了文件: {file_name}")
            result["session_id"] = sid
            return result
        finally:
            # Schedule cleanup after 10 minutes
            if tmp_path:
                def _cleanup():
                    try:
                        if os.path.exists(tmp_path):
                            os.unlink(tmp_path)
                        if tmp_path.rsplit("/", 1)[0] != "/tmp":
                            shutil.rmtree(tmp_path.rsplit("/", 1)[0], ignore_errors=True)
                    except Exception:
                        pass
                asyncio.get_running_loop().call_later(600, _cleanup)

    async def list_sessions(self) -> list[dict]:
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._rpc("session/list", {})
        return resp.get("sessions", [])

    async def new_session(self, cwd: str | None = None) -> dict:
        if not self.is_running or not self._initialized:
            await self.start()
        resp = await self._rpc("session/new", {"cwd": cwd or os.environ.get("USER_HOME", "/home/climbing"), "mcpServers": []})
        sid = resp.get("sessionId") or resp.get("session_id")
        if sid:
            self._default_session_id = sid
        return resp

    # ---- Internal ----

    async def _rpc(self, method: str, params: dict) -> dict:
        """Send RPC, wait for matching response via background reader."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}

        fut: asyncio.Future[dict] = asyncio.get_running_loop().create_future()
        self._rpc_pending[msg_id] = fut

        if not self._proc or not self._proc.stdin:
            self._rpc_pending.pop(msg_id, None)
            raise BrokenPipeError("ACP process stdin not available")
        try:
            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()
        except (BrokenPipeError, OSError) as e:
            self._rpc_pending.pop(msg_id, None)
            raise

        try:
            return await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError:
            self._rpc_pending.pop(msg_id, None)
            raise TimeoutError(f"ACP RPC timeout: {method}")

    async def stream_message(self, text: str, session_id: str | None = None):
        """Stream message chunks as they arrive via async generator."""
        if not self.is_running or not self._initialized:
            await self.start()
        if session_id == "":
            sid = await self._fresh_session_sid()
        else:
            sid = session_id or self._default_session_id or "default"
        if sid and len(sid) < 36 and sid != "default":
            sid = self._default_session_id or "default"

        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {
            "jsonrpc": "2.0", "id": msg_id,
            "method": "session/prompt",
            "params": {"prompt": [{"type": "text", "text": text}], "sessionId": sid},
        }

        pending = PendingPrompt(msg_id=msg_id, session_id=sid)
        self._prompt_pending[msg_id] = pending
        self._active_prompt_ids.add(msg_id)

        if not self._proc or not self._proc.stdin:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)
            raise BrokenPipeError("ACP process stdin not available")
        try:
            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()
        except (BrokenPipeError, OSError) as e:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)
            raise

        # Yield chunks as they arrive
        last_idx = 0
        chunk_count = 0
        try:
            while not pending.done.is_set():
                await asyncio.sleep(0.05)
                while last_idx < len(pending.chunks):
                    chunk = pending.chunks[last_idx]
                    last_idx += 1
                    chunk_count += 1
                    logger.info(f"Stream chunk #{chunk_count}: {len(chunk)} chars")
                    yield {"type": "chunk", "text": chunk}
            # Yield any remaining chunks
            while last_idx < len(pending.chunks):
                chunk = pending.chunks[last_idx]
                last_idx += 1
                chunk_count += 1
                logger.info(f"Stream final chunk #{chunk_count}: {len(chunk)} chars")
                yield {"type": "chunk", "text": chunk}
            logger.info(f"Stream done: {chunk_count} chunks total")
        finally:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)

        response = pending.response
        if "error" in response:
            yield {"type": "error", "message": str(response["error"])}
        else:
            response_text = "".join(pending.chunks)
            yield {"type": "done", "text": response_text, "source": "acp", "session_id": sid}

    async def stream_message_parts(self, parts: list[dict], session_id: str | None = None):
        """Stream multi-part prompt (text + image/file) chunks via async generator."""
        if not self.is_running or not self._initialized:
            await self.start()
        if session_id == "":
            sid = await self._fresh_session_sid()
        else:
            sid = session_id or self._default_session_id or "default"
        if sid and len(sid) < 36 and sid != "default":
            sid = self._default_session_id or "default"

        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {
            "jsonrpc": "2.0", "id": msg_id,
            "method": "session/prompt",
            "params": {"prompt": parts, "sessionId": sid},
        }

        pending = PendingPrompt(msg_id=msg_id, session_id=sid)
        self._prompt_pending[msg_id] = pending
        self._active_prompt_ids.add(msg_id)

        if not self._proc or not self._proc.stdin:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)
            raise BrokenPipeError("ACP process stdin not available")
        try:
            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()
        except (BrokenPipeError, OSError) as e:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)
            raise

        last_idx = 0
        chunk_count = 0
        try:
            while not pending.done.is_set():
                await asyncio.sleep(0.05)
                while last_idx < len(pending.chunks):
                    chunk = pending.chunks[last_idx]
                    last_idx += 1
                    chunk_count += 1
                    yield {"type": "chunk", "text": chunk}
            while last_idx < len(pending.chunks):
                chunk = pending.chunks[last_idx]
                last_idx += 1
                chunk_count += 1
                yield {"type": "chunk", "text": chunk}
        except asyncio.CancelledError:
            raise
        finally:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)

        response = pending.response
        if "error" in response:
            yield {"type": "error", "message": str(response["error"])}
        else:
            response_text = "".join(pending.chunks)
            yield {"type": "done", "text": response_text, "source": "acp", "session_id": sid}

    async def _prompt(self, text: str, session_id: str) -> dict:
        return await self._prompt_parts([{"type": "text", "text": text}], session_id)

    async def _prompt_parts(self, parts: list[dict], session_id: str) -> dict:
        """Send prompt, collect chunks via background reader."""
        self._msg_id += 1
        msg_id = str(self._msg_id)
        request = {
            "jsonrpc": "2.0", "id": msg_id,
            "method": "session/prompt",
            "params": {"prompt": parts, "sessionId": session_id},
        }

        pending = PendingPrompt(msg_id=msg_id, session_id=session_id)
        self._prompt_pending[msg_id] = pending
        self._active_prompt_ids.add(msg_id)
        # relay轨迹观察（10:02轮遗留#3销账）：回合起点在写stdin前注册，
        # 防"响应先到观察后到"竞态丢收口；转发内容零改动
        self._traj_observe_client(request, session_id)

        if not self._proc or not self._proc.stdin:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)
            self._traj_fail_turn(msg_id, session_id)
            raise BrokenPipeError("ACP process stdin not available")
        try:
            self._proc.stdin.write((json.dumps(request) + "\n").encode())
            await self._proc.stdin.drain()
        except (BrokenPipeError, OSError) as e:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)
            self._traj_fail_turn(msg_id, session_id)
            raise

        # Wait for prompt response (timeout: 120s — ACP LLM calls can be slow but not infinite)
        try:
            await asyncio.wait_for(pending.done.wait(), timeout=120)
        except asyncio.TimeoutError:
            self._prompt_pending.pop(msg_id, None)
            self._active_prompt_ids.discard(msg_id)
            self._traj_fail_turn(msg_id, session_id)
            raise TimeoutError(f"ACP prompt timeout (120s) for msg_id={msg_id}")

        # Clean up
        self._prompt_pending.pop(msg_id, None)
        self._active_prompt_ids.discard(msg_id)

        response = pending.response
        collected = pending.chunks

        if "error" in response:
            raise Exception(str(response["error"]))

        response_text = "".join(collected)
        stop_reason = str(response.get("stopReason", "") or "")
        # ── P0修复（2026-09-20）：空响应必须显式标记为失败，禁止静默空串 ──
        # 实证根因：adapter对不存在的session返回stopReason=refusal+0 chunks
        # （stderr: "prompt: session xxx not found"）；adapter重启后旧session
        # 全部失效，客户端持旧session_id → 100%命中。旧代码无条件设
        # response_text=""，调用方`is not None`判断永真 → CLI fallback成
        # 死代码 → 客户端收到 {"ok":true,"content":""} 无任何错误标记。
        # 参照：AIHawk SHOWN/SENT双预算原则"截断/失败必须显式标记"+
        # open-webui"拒绝=合成错误结果，不能静默断流"（SUMMARY.md P0-2/P0-4）。
        # 空响应→response_text=None激活调用方既有fallback链
        # （_send_message_inner的过期session恢复→CLI兜底）。
        if not response_text.strip():
            logger.warning(
                f"Empty ACP response marked as FAILED "
                f"(stopReason={stop_reason or 'n/a'}, chunks={len(collected)}, "
                f"session={session_id}) — fallback chain will activate"
            )
            response["response_text"] = None
            response["stop_reason"] = stop_reason
            response["empty_response"] = True
            response["source"] = "acp"
            return response
        logger.info(f"Prompt completed: {len(collected)} chunks, {len(response_text)} chars")
        response["response_text"] = response_text
        response["source"] = "acp"
        return response

    async def _cli(self, text: str) -> dict:
        """Fallback: run hermes -z one-shot."""
        logger.info(f"CLI fallback: {text[:80]}...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "hermes", "-z", text,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            result = stdout.decode("utf-8", errors="replace").strip()
            if not result and proc.returncode != 0:
                result = stderr.decode("utf-8", errors="replace").strip()
            return {
                "stopReason": "end_turn",
                "response_text": result or "（无响应）",
                "source": "hermes-cli",
            }
        except TimeoutError:
            logger.error("CLI fallback timeout (120s)")
            return {"stopReason": "timeout", "response_text": "请求超时", "source": "hermes-cli"}
        except Exception as e:
            logger.error(f"CLI fallback error: {e}")
            return {"stopReason": "error", "response_text": f"错误: {e}", "source": "hermes-cli"}

    @staticmethod
    def _parse(raw: bytes) -> dict | None:
        try:
            return json.loads(raw.decode().strip())
        except Exception:
            return None

    def _get_agent_info(self) -> dict:
        return {"agentInfo": {"name": "hermes-agent", "version": "0.20.5"}, "protocolVersion": 1}


_acp: ACPProcess | None = None


def get_acp_process() -> ACPProcess:
    global _acp
    if _acp is None:
        _acp = ACPProcess()
    return _acp
