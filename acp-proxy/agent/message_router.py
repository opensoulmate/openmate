"""SoulMate 统一消息路由（路线图4.2）：OpenMate/微信统一收发。

缺口（路线图原文）："4.2 消息路由：OpenMate/微信统一收发"。4.1a 微信桥只有
微信单通道自己的回路，OpenMate 会话侧消息走的是 ws_acp 客户端路径——两通道
各收各发，没有统一出入站口。

本切片交付：
1. **统一信封** ChannelMessage（channel/msg_id/sender/text/session_id/context_token），
   入站一个入口 `route_inbound()` 按 channel 分发：
   - `wechat` → 整体委托 WeChatBridge.deliver()（去重/回合/回复的单一 owner 仍在
     桥内，不双份记账——桥去重在 BridgeStore，本模块去重表只服务 openmate 通道）；
   - `openmate` → 本模块去重（channel+msg_id 复合主键持久化）+ 无客户端
     SoulMate 回合（失败纪律复用 wakeup_executor：refusal/LLM级失败/零输出都
     抛异常不假装成功），回合落库行 turn_origin=msg，wakeup_relay 补推面纳入
     msg（正连前端实时可见）。
2. **统一出站** `send_outbound(channel, target, text)` 一个入口发任意通道：
   - `wechat` → WeChatBridge.send_proactive()（拆块+限流同回复路径）发给 peer；
   - `openmate` → 落库 assistant 行到目标会话（turn_origin=msg，正连前端实时
     可见；未连接时历史加载同样可见）。

soulmate_agent 侧工具：message_send（见四点接线）。
诚实边界：iLink 真实传输未接（4.1b 待用户拍板接入形态），当前微信通道跑在
loopback 传输上；openmate 通道出站是"落库+补推"，不是改写 ws 客户端连接。
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Sequence

from agent.media_channel import (
    MediaAttachment,
    media_prompt_block as _media_prompt_block,
)

logger = logging.getLogger("acp-proxy.message_router")

# 回合出身标记：入站回合行 + 出站消息行统一用 msg（wakeup_relay 补推面消费）
MSG_TURN_ORIGIN = "msg"
MSG_TURN_MARKER = "[统一消息回合"

CHANNEL_OPENMATE = "openmate"
CHANNEL_WECHAT = "wechat"
CHANNELS = (CHANNEL_OPENMATE, CHANNEL_WECHAT)

_DEFAULT_ROUTER_DB = os.path.expanduser(
    "~/.hermes/soulmate/memory/message_router.db")
_OPENSOUL_ROOT = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
_DEFAULT_OPENMATE_DB = f"{_OPENSOUL_ROOT}/data/opensoul.db"
_DB_TIMEOUT_S = 10.0


@dataclass
class ChannelMessage:
    """一条入站消息（通道无关的中立信封）。"""

    channel: str
    msg_id: str
    sender: str
    text: str
    session_id: str = ""       # openmate 通道必填（路由到哪个会话）
    context_token: str = ""    # 微信传输自用（iLink 回显），协议知识不下沉
    received_at_ms: int = 0
    media: list = field(default_factory=list)  # 媒体附件（MediaAttachment，路线图4.3）

    def __post_init__(self) -> None:
        if not self.received_at_ms:
            self.received_at_ms = int(time.time() * 1000)


@dataclass
class RouteResult:
    """路由结果（入站/出站同构）：status=delivered|duplicate|error。"""

    status: str
    channel: str
    target: str = ""
    session_id: str = ""
    reply_sent: bool = False
    detail: str = ""


@dataclass
class _RouterStats:
    inbound_ok: int = 0
    inbound_duplicates: int = 0
    inbound_failed: int = 0
    outbound_ok: int = 0
    outbound_failed: int = 0
    last_error: str = ""
    last_error_at: int = 0
    extra: dict = field(default_factory=dict)


# ── 持久层：openmate 通道入站去重 ────────────────────────────────


class RouterStore:
    """SQLite 持久化：routed_inbound(channel+msg_id 复合主键去重)。

    只服务 openmate 通道——微信通道去重归 BridgeStore 单一真源，
    不搞两份记账（重复投递只被一处判重，语义唯一）。
    """

    def __init__(self, db_path: str | None = None):
        self._db_path = (
            db_path or os.environ.get("SOULMATE_MSG_ROUTER_DB") or _DEFAULT_ROUTER_DB)
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path, timeout=_DB_TIMEOUT_S)
        return db

    def _ensure_db(self) -> None:
        db = self._connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS routed_inbound("
                "channel TEXT NOT NULL,"
                "msg_id TEXT NOT NULL,"
                "seen_at_ms INTEGER NOT NULL,"
                "PRIMARY KEY(channel, msg_id))"
            )
            db.commit()
        finally:
            db.close()

    def first_time_seen(self, channel: str, msg_id: str, now_ms: int) -> bool:
        """记录并返回是否首次见到该 (channel, msg_id)（重启/重复投递→False，不双跑回合）。"""
        db = self._connect()
        try:
            try:
                db.execute(
                    "INSERT INTO routed_inbound(channel, msg_id, seen_at_ms)"
                    " VALUES (?,?,?)",
                    (str(channel), str(msg_id), int(now_ms)),
                )
                db.commit()
                return True
            except sqlite3.IntegrityError:
                return False
        finally:
            db.close()


# ── 默认回合体：openmate 通道无客户端 SoulMate 回合 ──────────────


def build_msg_prompt(text: str, sender: str = "") -> str:
    """统一消息回合prompt——显式声明消息经统一路由转入本会话。"""
    who = sender or "消息方"
    return (
        f"{MSG_TURN_MARKER}｜来自「{who}」的{CHANNEL_OPENMATE}通道消息，系统转入本会话执行]\n"
        f"{text}\n"
        "请直接回复这条消息（简洁、对方能懂）；只做该消息要求的事。"
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


def make_openmate_turn_fn(
    get_agent: Callable[[], Any] | None = None,
) -> Callable[[str, str], Coroutine[Any, Any, str]]:
    """默认回合体：openmate 通道消息 → 无客户端 SoulMate 回合。

    失败纪律复用 wakeup_executor（refusal/LLM级失败/零输出都抛异常不假装成功，
    由 router 记 error——不给消失的会话造回合，也不谎报投递成功）。
    回合出身 turn_origin=msg 落库（wakeup_relay 补推面已纳入 msg）。
    """

    async def _turn(session_id: str, text: str) -> str:
        from agent import wakeup_executor as we

        agent = (get_agent or we._get_headless_agent)()
        pre_id = we._max_assistant_id(agent, session_id)
        agent.set_turn_origin(session_id, MSG_TURN_ORIGIN)
        try:
            resp = await agent.prompt(
                [{"type": "text", "text": build_msg_prompt(text)}], session_id)
        finally:
            agent.clear_turn_origin(session_id)
        stop_reason = str(getattr(resp, "stop_reason", "") or "")
        if stop_reason == "refusal":
            raise RuntimeError(
                "消息回合被拒：会话在服务端不存在，拒绝给消失的会话造回合")
        bad = we._llm_failure_reply(agent, session_id, pre_id)
        if bad:
            raise RuntimeError(
                "消息回合LLM级失败（provider错误/超时机器标记）: " + bad)
        out = _collect_assistant_text(agent, session_id, pre_id)
        if not out.strip():
            raise RuntimeError(
                "消息回合零输出（未落库任何非空assistant输出）——不谎报投递成功")
        return out

    return _turn


# ── 路由主体 ─────────────────────────────────────────────────────


class UnifiedMessageRouter:
    """统一消息路由：入站 route_inbound 一个入口按 channel 分发，
    出站 send_outbound 一个入口发任意通道。"""

    def __init__(
        self,
        *,
        get_wechat_bridge: Callable[[], Any] | None = None,
        turn_fn: Callable[[str, str], Coroutine[Any, Any, str]] | None = None,
        openmate_send: Callable[..., int] | None = None,
        store: RouterStore | None = None,
        openmate_db_path: str | None = None,
        now_fn: Callable[[], float] | None = None,
    ):
        self._get_wechat_bridge = get_wechat_bridge
        self._turn_fn = turn_fn or make_openmate_turn_fn()
        self._openmate_send = openmate_send
        self._audit = None  # 出站痕迹（lazy get_outbound_audit）
        self._store = store or RouterStore()
        self._openmate_db_path = (
            openmate_db_path or os.environ.get("SOULMATE_MSG_OPENMATE_DB")
            or _DEFAULT_OPENMATE_DB)
        self._now_fn = now_fn or time.time
        self.stats = _RouterStats()

    # ── 依赖取用（lazy import 防模块级循环引用） ────────────────

    def _bridge(self):
        if self._get_wechat_bridge is not None:
            return self._get_wechat_bridge()
        from agent.wechat_bridge import get_wechat_bridge

        return get_wechat_bridge()

    def _note_error(self, msg: str) -> None:
        self.stats.last_error = str(msg)[:300]
        self.stats.last_error_at = int(time.time() * 1000)
        logger.warning("[msg_router] %s", msg)

    def _record_out(self, channel: str, target: str, kind: str, status: str,
                    *, blocks: int = 1, dedup_key: str | None = None,
                    text_preview: str = "") -> None:
        """出站痕迹落审计行（路线图12.1；旁路纪律失败只计数不冒泡）。

        wechat 通道由 WeChatBridge 传输边界留痕（单一真源不双份记账），
        本处只留 openmate 通道出站痕迹。
        """
        try:
            audit = self._audit
            if audit is None:
                from agent.outbound_audit import get_outbound_audit
                audit = get_outbound_audit()
                self._audit = audit
            audit.record(channel, target, kind, status, blocks=blocks,
                         dedup_key=dedup_key, text_preview=text_preview)
        except Exception:
            pass

    # ── 入站 ───────────────────────────────────────────────────

    async def route_inbound(self, msg: ChannelMessage) -> RouteResult:
        """统一入站入口：校验 → 通道分发（wechat 委托桥 / openmate 自走回合）。"""
        channel = str(msg.channel or "").strip().lower()
        if channel not in CHANNELS:
            self.stats.inbound_failed += 1
            return RouteResult(
                "error", str(msg.channel or ""),
                detail=f"未知消息通道: {msg.channel}（支持 {'/'.join(CHANNELS)}）")
        if not str(msg.msg_id or "").strip():
            self.stats.inbound_failed += 1
            return RouteResult("error", channel, detail="msg_id 不能为空")
        if not str(msg.text or "").strip() and not msg.media:
            self.stats.inbound_failed += 1
            return RouteResult("error", channel, detail="text 不能为空（text 与 media 至少给一个）")

        if channel == CHANNEL_WECHAT:
            return await self._route_wechat(msg)

        # ── openmate 通道 ──
        sid = str(msg.session_id or "").strip()
        if not sid:
            self.stats.inbound_failed += 1
            return RouteResult(
                "error", channel, target=str(msg.sender or ""),
                detail="openmate 通道消息必须带 session_id（路由到哪个会话）")
        if not self._store.first_time_seen(channel, str(msg.msg_id), msg.received_at_ms):
            self.stats.inbound_duplicates += 1
            return RouteResult(
                "duplicate", channel, target=str(msg.sender or ""), session_id=sid,
                detail="重复投递/重启重放，回合不双跑")
        try:
            # 媒体附件：提示块拼进回合文本（本地路径+读取方式，截图理解走vision工具）
            turn_text = str(msg.text)
            if msg.media:
                block = _media_prompt_block(msg.media)
                turn_text = f"{turn_text}\n\n{block}" if turn_text.strip() else block
            reply = await self._turn_fn(sid, turn_text)
        except Exception as e:
            self.stats.inbound_failed += 1
            self._note_error(f"openmate回合失败({msg.msg_id}): {e}")
            return RouteResult(
                "error", channel, target=str(msg.sender or ""), session_id=sid,
                detail=f"回合失败: {e}")
        self.stats.inbound_ok += 1
        return RouteResult(
            "delivered", channel, target=str(msg.sender or ""), session_id=sid,
            reply_sent=True, detail=f"回合完成，回复{len(reply)}字已落库")

    async def _route_wechat(self, msg: ChannelMessage) -> RouteResult:
        """微信通道整体委托桥（去重/回合/回复单一 owner=WeChatBridge）。"""
        from agent.wechat_bridge import WeChatMessage

        wmsg = WeChatMessage(
            msg_id=str(msg.msg_id),
            from_user=str(msg.sender or ""),
            content=str(msg.text),
            context_token=str(msg.context_token or ""),
            received_at_ms=int(msg.received_at_ms),
            media=list(msg.media or []),
        )
        try:
            status = await self._bridge().deliver(wmsg)
        except Exception as e:
            self.stats.inbound_failed += 1
            self._note_error(f"微信投递异常({msg.msg_id}): {e}")
            return RouteResult(
                "error", CHANNEL_WECHAT, target=str(msg.sender or ""),
                detail=f"桥投递异常: {e}")
        sid = ""
        try:
            sid = str(self._bridge().session_for(msg.sender) or "")
        except Exception:
            sid = ""
        if status == "duplicate":
            self.stats.inbound_duplicates += 1
            return RouteResult(
                "duplicate", CHANNEL_WECHAT, target=str(msg.sender or ""),
                session_id=sid, detail="重复投递/重启重放，回合不双跑")
        if status == "delivered":
            self.stats.inbound_ok += 1
            return RouteResult(
                "delivered", CHANNEL_WECHAT, target=str(msg.sender or ""),
                session_id=sid, reply_sent=True, detail="桥已处理并回复")
        self.stats.inbound_failed += 1
        return RouteResult(
            "error", CHANNEL_WECHAT, target=str(msg.sender or ""),
            session_id=sid, detail=f"桥返回: {status}")

    # ── 出站 ───────────────────────────────────────────────────

    def _default_openmate_send(self, session_id: str, text: str,
                               media: Sequence[MediaAttachment] = ()) -> int:
        """openmate 出站默认落库：assistant 行 + turn_origin=msg（relay 补推消费）。

        媒体附件（路线图4.3）：内容追加 MEDIA:<path> 标签行（前端渲染下载卡片）
        + attachments 列持久化 to_meta() JSON（历史加载可恢复附件）；表无该列时
        ALTER 兜底（对齐 soulmate_agent 附件迁移惯例）。
        timestamp=time.time()（agent_messages 现行约定：秒；与 soulmate_agent
        _save_message 同款）。返回新行 id。
        """
        meta = {"turn_origin": MSG_TURN_ORIGIN, "channel": CHANNEL_OPENMATE}
        atts = list(media or [])
        content = str(text)
        if atts:
            tags = "\n".join(f"MEDIA:{a.path}" for a in atts)
            content = f"{content}\n\n{tags}" if content.strip() else tags
        db = sqlite3.connect(self._openmate_db_path, timeout=_DB_TIMEOUT_S)
        try:
            if atts:
                attachments_json = json.dumps(
                    [a.to_meta() for a in atts], ensure_ascii=False)
                try:
                    cur = db.execute(
                        "INSERT INTO agent_messages (session_id, role, content,"
                        " timestamp, metadata, attachments) VALUES (?,?,?,?,?,?)",
                        (str(session_id), "assistant", content,
                         self._now_fn(), json.dumps(meta, ensure_ascii=False),
                         attachments_json),
                    )
                except sqlite3.OperationalError:
                    db.execute(
                        "ALTER TABLE agent_messages ADD COLUMN attachments TEXT")
                    cur = db.execute(
                        "INSERT INTO agent_messages (session_id, role, content,"
                        " timestamp, metadata, attachments) VALUES (?,?,?,?,?,?)",
                        (str(session_id), "assistant", content,
                         self._now_fn(), json.dumps(meta, ensure_ascii=False),
                         attachments_json),
                    )
            else:
                cur = db.execute(
                    "INSERT INTO agent_messages (session_id, role, content, timestamp, metadata)"
                    " VALUES (?,?,?,?,?)",
                    (str(session_id), "assistant", content,
                     self._now_fn(), json.dumps(meta, ensure_ascii=False)),
                )
            db.commit()
            return int(cur.lastrowid or 0)
        finally:
            db.close()

    async def send_outbound(
        self, channel: str, target: str, text: str,
        media: Sequence[MediaAttachment] | None = None) -> RouteResult:
        """统一出站入口：发消息（可带媒体附件）到任意通道目标。

        media：MediaAttachment 序列（路线图4.3 图片/文件收发）；text 与 media
        至少给一个（纯文件/图片消息合法）。
        """
        ch = str(channel or "").strip().lower()
        tgt = str(target or "").strip()
        body = str(text or "")
        atts = list(media or [])
        if ch not in CHANNELS:
            self.stats.outbound_failed += 1
            return RouteResult(
                "error", str(channel or ""), target=tgt,
                detail=f"未知消息通道: {channel}（支持 {'/'.join(CHANNELS)}）")
        if not tgt:
            self.stats.outbound_failed += 1
            return RouteResult("error", ch, detail="target 不能为空（微信peer或会话session_id）")
        if not body.strip() and not atts:
            self.stats.outbound_failed += 1
            return RouteResult("error", ch, target=tgt, detail="text 不能为空（text 与 media 至少给一个）")

        if ch == CHANNEL_WECHAT:
            sent = 0
            media_sent = 0
            try:
                if body.strip():
                    sent = await self._bridge().send_proactive(tgt, body)
                if atts:
                    send_media = getattr(self._bridge(), "send_media_proactive", None)
                    if send_media is None:
                        self.stats.outbound_failed += 1
                        return RouteResult(
                            "error", ch, target=tgt,
                            detail="该微信桥不支持发送媒体附件（send_media_proactive 未实现）")
                    media_sent = await send_media(tgt, atts)
                    if media_sent <= 0:
                        self.stats.outbound_failed += 1
                        return RouteResult(
                            "error", ch, target=tgt,
                            detail="媒体发送失败：0 个成功（传输不支持/拒发）")
            except Exception as e:
                self.stats.outbound_failed += 1
                self._note_error(f"微信主动发送异常({tgt}): {e}")
                return RouteResult(
                    "error", ch, target=tgt, detail=f"发送失败: {e}")
            if body.strip() and sent <= 0:
                self.stats.outbound_failed += 1
                return RouteResult(
                    "error", ch, target=tgt, detail="发送失败：0 块成功（传输拒发/全失败）")
            self.stats.outbound_ok += 1
            parts = []
            if body.strip():
                parts.append(f"文本{sent}块")
            if atts:
                parts.append(f"媒体{media_sent}个")
            return RouteResult(
                "delivered", ch, target=tgt, reply_sent=True,
                detail=f"已发{'+'.join(parts)}给微信peer「{tgt}」")

        # ── openmate 出站：落库 + relay 补推（正连前端实时可见） ──
        out_kind = "media" if (atts and not body.strip()) else "text"
        try:
            sender = self._openmate_send or self._default_openmate_send
            row_id = sender(tgt, body, atts)
        except Exception as e:
            self.stats.outbound_failed += 1
            self._note_error(f"openmate出站落库异常({tgt}): {e}")
            self._record_out(ch, tgt, out_kind, "failed",
                             text_preview=body)
            return RouteResult(
                "error", ch, target=tgt, detail=f"落库失败: {e}")
        self.stats.outbound_ok += 1
        self._record_out(ch, tgt, out_kind, "sent", blocks=1,
                         dedup_key=f"om-{row_id}", text_preview=body)
        return RouteResult(
            "delivered", ch, target=tgt, session_id=tgt, reply_sent=True,
            detail=f"已落库消息行#{row_id}（turn_origin={MSG_TURN_ORIGIN}，正连前端实时可见）")

    # ── 观测 ───────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "openmate_db": self._openmate_db_path,
            "router_db": self._store._db_path,
            **self.stats.__dict__,
        }


# ── 单例（get_scheduler/get_health_monitor 同款惯例） ──────────

_router: UnifiedMessageRouter | None = None


def get_message_router() -> UnifiedMessageRouter:
    """懒构建统一消息路由单例。"""
    global _router
    if _router is None:
        _router = UnifiedMessageRouter()
    return _router
