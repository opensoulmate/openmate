"""SoulMate 微信桥（路线图4.1切片a）：微信消息 → 无客户端SoulMate回合 → 回复微信。

设计边界（4.1"复用现有gateway经验，不改hermes侧"）：
- **传输层抽象** WeChatTransport（fetch_inbound/send_reply）。本切片交付
  LoopbackTransport（进程内回环，e2e实证"接收→回合→回复"回路）；iLink Bot API
  传输（对齐 hermes gateway platforms/weixin.py 的 context_token 回显/限流经验）
  留待4.1b——**绝不与hermes gateway抢同一iLink会话的getupdates长轮询**
  （双消费会分流hermes的入站消息，等于变相改坏hermes侧）。
- **context_token 回显**：iLink协议要求回复携带对端最新context_token——token放在
  入站消息上，由transport.send_reply自行取用（协议知识不下沉到bridge）。
- **出站限流**：min_send_interval_s 最小发送间隔（gateway经验：撞iLink限流会
  静默丢消息），长回复拆块逐块发送。
- **回合纪律**复用 wakeup_executor 的失败判定（refusal/LLM级失败/零输出都不假装
  成功），失败发失败告知而不是无声消失；回合出身 turn_origin=wechat 落库，
  wakeup_relay 补推面纳入wechat（正连前端可见微信回合）。
- 去重持久化（SQLite msg_id主键）：重启/重复投递不双跑回合；peer→session映射
  持久化，同一微信用户固定进同一会话（首见自动建会话）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger("acp-proxy.wechat_bridge")

WECHAT_TURN_ORIGIN = "wechat"
WECHAT_TURN_MARKER = "[微信消息回合"
DEFAULT_FAILURE_NOTICE = "⚠️ 消息处理失败，请稍后重试"

_DEFAULT_DB_PATH = os.path.expanduser("~/.hermes/soulmate/memory/wechat_bridge.db")
_DB_TIMEOUT_S = 10.0


@dataclass
class WeChatMessage:
    """一条入站微信消息（transport无关的中立形状）。"""

    msg_id: str
    from_user: str
    content: str
    from_name: str = ""
    msg_type: str = "text"
    context_token: str = ""  # iLink协议：回复须回显对端最新token（transport自用）
    received_at_ms: int = 0

    def __post_init__(self) -> None:
        if not self.received_at_ms:
            self.received_at_ms = int(time.time() * 1000)


class WeChatTransport(ABC):
    """微信传输层抽象：拉取入站 + 发送回复。"""

    @abstractmethod
    async def fetch_inbound(self) -> list[WeChatMessage]:
        """拉取待处理入站消息（空轮询返回[]，不得抛超时类异常）。"""

    @abstractmethod
    async def send_reply(self, msg: WeChatMessage, text: str) -> bool:
        """向该消息的对端发送回复文本（须回显msg.context_token）。返回是否成功。"""


class LoopbackTransport(WeChatTransport):
    """进程内回环传输（测试/本地验证收发回路；4.1b iLink传输接入前的默认实现）。"""

    def __init__(self) -> None:
        self._inbound: asyncio.Queue[WeChatMessage] = asyncio.Queue()
        self.sent: list[tuple[WeChatMessage, str]] = []

    def push(self, msg: WeChatMessage) -> None:
        """注入一条入站消息（模拟微信来消息）。"""
        self._inbound.put_nowait(msg)

    async def fetch_inbound(self) -> list[WeChatMessage]:
        out: list[WeChatMessage] = []
        while True:
            try:
                out.append(self._inbound.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    async def send_reply(self, msg: WeChatMessage, text: str) -> bool:
        self.sent.append((msg, text))
        return True


# ── 持久层：去重 + peer→session映射 ─────────────────────────────


class BridgeStore:
    """SQLite持久化：seen(msg_id去重) + peers(peer→session映射)。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.environ.get("SOULMATE_WECHAT_DB_PATH") or _DEFAULT_DB_PATH
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path, timeout=_DB_TIMEOUT_S)
        db.row_factory = sqlite3.Row
        return db

    def _ensure_db(self) -> None:
        db = self._connect()
        try:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS seen(
                    msg_id TEXT PRIMARY KEY,
                    seen_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS peers(
                    peer TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    last_seen_ms INTEGER NOT NULL
                );
                """
            )
            db.commit()
        finally:
            db.close()

    def first_time_seen(self, msg_id: str, now_ms: int) -> bool:
        """记录并返回是否首次见到该msg_id（重复投递/重启重放→False，不双跑回合）。"""
        db = self._connect()
        try:
            cur = db.execute(
                "INSERT OR IGNORE INTO seen(msg_id, seen_at_ms) VALUES(?, ?)",
                (str(msg_id), int(now_ms)),
            )
            db.commit()
            return cur.rowcount == 1
        finally:
            db.close()

    def session_for(self, peer: str) -> str | None:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT session_id FROM peers WHERE peer=?", (str(peer),)
            ).fetchone()
            return str(row[0]) if row else None
        finally:
            db.close()

    def remember_session(self, peer: str, session_id: str, now_ms: int) -> None:
        db = self._connect()
        try:
            db.execute(
                "INSERT INTO peers(peer, session_id, last_seen_ms) VALUES(?, ?, ?) "
                "ON CONFLICT(peer) DO UPDATE SET session_id=excluded.session_id, "
                "last_seen_ms=excluded.last_seen_ms",
                (str(peer), str(session_id), int(now_ms)),
            )
            db.commit()
        finally:
            db.close()


# ── 默认回合体：无客户端SoulMateAgent跑一个微信回合 ──────────────


def build_wechat_prompt(msg: WeChatMessage) -> str:
    """微信回合prompt——显式声明消息来自微信用户，要求直接回复该消息。"""
    who = msg.from_name or msg.from_user or "微信用户"
    return (
        f"{WECHAT_TURN_MARKER}｜来自微信用户「{who}」的消息，系统转入本会话执行]\n"
        f"{msg.content}\n"
        "请直接回复这条微信消息（简洁、对方能懂）；只做该消息要求的事。"
    )


def _collect_assistant_text(agent, session_id: str, after_id: int | None) -> str:
    """收集本轮新落库的assistant输出文本（turn归因锚点之后，按id序拼接）。"""
    if after_id is None:
        return ""
    db = agent._get_db()
    try:
        rows = db.execute(
            "SELECT content FROM agent_messages "
            "WHERE session_id=? AND role='assistant' AND id>? ORDER BY id",
            (session_id, int(after_id)),
        ).fetchall()
    finally:
        db.close()
    return "\n\n".join((r[0] or "").strip() for r in rows if (r[0] or "").strip())


def make_default_turn_fn(
    store: BridgeStore,
    get_agent: Callable[[], Any] | None = None,
) -> Callable[[WeChatMessage], Coroutine[Any, Any, str]]:
    """默认回合体：peer→session映射（首见自动建会话）+ 无客户端SoulMate回合。

    失败纪律复用 wakeup_executor（refusal/LLM级失败/零输出都抛异常不假装成功，
    由bridge发失败告知——微信用户不能面对无声消失）。
    """

    async def _turn(msg: WeChatMessage) -> str:
        from agent import wakeup_executor as we

        agent = (get_agent or we._get_headless_agent)()
        sid = store.session_for(msg.from_user)
        if not sid:
            resp_new = await agent.new_session()
            sid = str(
                getattr(resp_new, "session_id", None)
                or getattr(resp_new, "sessionId", None)
                or ""
            )
            if not sid:
                raise RuntimeError("微信回合建会话失败：new_session未返回session_id")
            store.remember_session(msg.from_user, sid, msg.received_at_ms)
        pre_id = we._max_assistant_id(agent, sid)
        agent.set_turn_origin(sid, WECHAT_TURN_ORIGIN)
        try:
            resp = await agent.prompt(
                [{"type": "text", "text": build_wechat_prompt(msg)}], sid
            )
        finally:
            agent.clear_turn_origin(sid)
        stop_reason = str(getattr(resp, "stop_reason", "") or "")
        if stop_reason == "refusal":
            raise RuntimeError(
                "微信回合被拒：会话在服务端不存在，拒绝给消失的会话造回合"
            )
        bad = we._llm_failure_reply(agent, sid, pre_id)
        if bad:
            raise RuntimeError("微信回合LLM级失败（provider错误/超时机器标记）: " + bad)
        text = _collect_assistant_text(agent, sid, pre_id)
        if not text.strip():
            raise RuntimeError(
                "微信回合零输出（未落库任何非空assistant输出）——不给微信用户发空回复"
            )
        return text

    return _turn


# ── 桥主体 ─────────────────────────────────────────────────────


@dataclass
class _BridgeStats:
    polls: int = 0
    fetched: int = 0
    duplicates: int = 0
    turns_ok: int = 0
    turns_failed: int = 0
    replies_sent: int = 0
    send_failed: int = 0
    last_error: str = ""
    last_error_at: int = 0
    extra: dict = field(default_factory=dict)


class WeChatBridge:
    """微信桥：拉取入站 → 去重 → 跑SoulMate回合 → 回复（拆块+限流）。

    单条消息失败绝不打断回路（失败告知+计数，继续处理后续消息）。
    """

    def __init__(
        self,
        transport: WeChatTransport,
        turn_fn: Callable[[WeChatMessage], Coroutine[Any, Any, str]] | None = None,
        *,
        store: BridgeStore | None = None,
        poll_interval_s: float | None = None,
        min_send_interval_s: float | None = None,
        max_block_chars: int | None = None,
        failure_notice: str = DEFAULT_FAILURE_NOTICE,
    ):
        self._transport = transport
        self._store = store or BridgeStore()
        self._turn_fn = turn_fn or make_default_turn_fn(self._store)
        self._poll_interval_s = float(
            poll_interval_s
            if poll_interval_s is not None
            else float(os.environ.get("SOULMATE_WECHAT_POLL_S", "5"))
        )
        self._min_send_interval_s = float(
            min_send_interval_s
            if min_send_interval_s is not None
            else float(os.environ.get("SOULMATE_WECHAT_MIN_SEND_S", "2"))
        )
        self._max_block_chars = int(
            max_block_chars
            if max_block_chars is not None
            else int(os.environ.get("SOULMATE_WECHAT_MAX_BLOCK", "2000"))
        )
        self._failure_notice = failure_notice
        self._last_send_at: float = 0.0
        self._task: asyncio.Task | None = None
        self._running = False
        self.stats = _BridgeStats()

    # ── 发送侧：拆块 + 限流 ────────────────────────────────────

    def _split_blocks(self, text: str) -> list[str]:
        """长回复按行边界贪心拆块（微信单条有长度上限；块间独立发送）。"""
        limit = max(200, self._max_block_chars)
        blocks: list[str] = []
        buf = ""
        for line in (text or "").splitlines():
            candidate = f"{buf}\n{line}" if buf else line
            if len(candidate) > limit and buf:
                blocks.append(buf)
                buf = line
            else:
                buf = candidate
            while len(buf) > limit:
                blocks.append(buf[:limit])
                buf = buf[limit:]
        if buf.strip():
            blocks.append(buf)
        return blocks or ([text] if text.strip() else [])

    async def _rate_limit(self) -> None:
        """出站最小间隔（iLink限流经验：撞限流静默丢消息）。"""
        if self._min_send_interval_s <= 0:
            return
        now = time.monotonic()
        wait = self._last_send_at + self._min_send_interval_s - now
        if wait > 0:
            await asyncio.sleep(wait)

    async def _send_blocks(self, msg: WeChatMessage, text: str) -> int:
        sent = 0
        for block in self._split_blocks(text):
            await self._rate_limit()
            try:
                ok = await self._transport.send_reply(msg, block)
            except Exception as e:  # 传输异常不炸回路
                self.stats.send_failed += 1
                self._note_error(f"send_reply异常: {e}")
                ok = False
            finally:
                self._last_send_at = time.monotonic()
            if ok:
                sent += 1
                self.stats.replies_sent += 1
            else:
                self.stats.send_failed += 1
        return sent

    def _note_error(self, msg: str) -> None:
        self.stats.last_error = str(msg)[:300]
        self.stats.last_error_at = int(time.time() * 1000)
        logger.warning("[wechat] %s", msg)

    # ── 处理回路 ───────────────────────────────────────────────

    async def _handle(self, msg: WeChatMessage) -> None:
        if not self._store.first_time_seen(msg.msg_id, msg.received_at_ms):
            self.stats.duplicates += 1
            return
        self.stats.fetched += 1
        try:
            reply = await self._turn_fn(msg)
        except Exception as e:
            self.stats.turns_failed += 1
            self._note_error(f"回合失败({msg.msg_id}): {e}")
            # 失败必须可见：给微信用户发失败告知，绝不无声消失
            await self._send_blocks(msg, self._failure_notice)
            return
        self.stats.turns_ok += 1
        await self._send_blocks(msg, reply)

    async def run_once(self) -> int:
        """处理当前待处理入站消息一批（返回处理条数；重复投递不计）。"""
        self.stats.polls += 1
        try:
            inbound = await self._transport.fetch_inbound()
        except Exception as e:
            self._note_error(f"fetch_inbound异常: {e}")
            return 0
        handled = 0
        for msg in inbound:
            try:
                await self._handle(msg)
                handled += 1
            except Exception as e:  # 兜底：单条炸不打断整批
                self._note_error(f"handle异常({getattr(msg, 'msg_id', '?')}): {e}")
        return handled

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            await self.run_once()
            await asyncio.sleep(self._poll_interval_s)

    def ensure_started(self) -> None:
        """幂等拉起后台回路（start.py stdio入口惯例）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.get_event_loop().create_task(self.run_forever())
            logger.info("[wechat] 微信桥回路已启动（poll=%ss）", self._poll_interval_s)

    def snapshot(self) -> dict:
        return {
            "transport": type(self._transport).__name__,
            "poll_interval_s": self._poll_interval_s,
            "min_send_interval_s": self._min_send_interval_s,
            **self.stats.__dict__,
        }


# ── 单例（get_scheduler/get_health_monitor 同款惯例） ──────────

_bridge: WeChatBridge | None = None


def get_wechat_bridge() -> WeChatBridge:
    """懒构建微信桥单例（传输由SOULMATE_WECHAT_TRANSPORT选择；默认loopback）。"""
    global _bridge
    if _bridge is None:
        kind = (os.environ.get("SOULMATE_WECHAT_TRANSPORT") or "loopback").strip().lower()
        if kind == "loopback":
            transport: WeChatTransport = LoopbackTransport()
        elif kind == "ilink":
            raise RuntimeError(
                "iLink传输尚未接入（4.1b）——为不与hermes gateway抢同一iLink会话，"
                "本切片只交付loopback传输"
            )
        else:
            raise RuntimeError(f"未知微信传输: {kind}")
        _bridge = WeChatBridge(transport)
    return _bridge
