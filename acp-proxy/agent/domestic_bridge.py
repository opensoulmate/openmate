"""国内网关通道（P2）：钉钉 + 飞书接入 SoulMate agent 通道（对齐微信体验）。

对齐微信体验（agent/wechat_bridge.py）：两条通道各支持两级——

**Level 1 · webhook 机器人快路（收 agent 消息推送）**
    用户只需填一个 webhook 地址即可收到 SoulMate 真消息推送。推送体走
    **echo 口径**（opensoul/src/echo/dispatcher.py::_send_dingtalk / _send_feishu
    同款负载），不另立山头——单一消息格式标准归 echo，本模块是 echo 口径的
    agent 出站客户端（test_domestic_bridge::test_echo_payload_matches_opensoul_echo
    用 opensoul 真 dispatcher 比对负载，杜绝格式漂移）。

**Level 2 · 自建应用全双工（收消息 → SoulMate 处理 → 回消息）**
    配置 appid/secret 后开启：DomesticAppTransport（钉钉/飞书开放平台自建应用）
    拉取/接收入站消息 → DomesticBridge 跑 SoulMate 回合 → 平台 API 回消息。
    真实传输（Stream / 事件回调）需用户在开放平台注册自建应用并配回调地址，
    留 scripts/test_full_duplex.py 待用户提供凭据后验收；本切片交付 loopback
    传输把收发回路跑通（对齐 wechat_bridge 的 loopback 先例）。

纪律（对齐 wechat_bridge / message_router）：
- 单条消息处理失败绝不打断回路（失败告知 + 计数，继续后续）；
- 回合失败（refusal / LLM 级失败 / 零输出）发失败告知，不假装成功、不无声消失；
- msg_id 去重持久化（重启/重复投递不双跑回合）；
- 出站痕迹落 outbound_audit（旁路 never-raise）。

凭据纪律：appid / app_secret / webhook secret 一律本地配置（环境变量或本地
.env，见 .env.example），**不进 git**。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

logger = logging.getLogger("acp-proxy.domestic_bridge")

# ── 通道常量（与 message_router 对齐） ──────────────────────────
CHANNEL_DINGTALK = "dingtalk"
CHANNEL_FEISHU = "feishu"
DOMESTIC_CHANNELS = (CHANNEL_DINGTALK, CHANNEL_FEISHU)

DEFAULT_FAILURE_NOTICE = "⚠️ 消息处理失败，请稍后重试"
DOMESTIC_TURN_ORIGIN = "domestic"
DOMESTIC_TURN_MARKER = "[国内网关回合"

_DEFAULT_DB_PATH = os.path.expanduser(
    "~/.hermes/soulmate/memory/domestic_bridge.db")
_DB_TIMEOUT_S = 10.0
_HTTP_TIMEOUT_S = 10.0


# ── echo 口径负载（归位 opensoul/src/echo/dispatcher.py） ──────────


def echo_payload(channel: str, title: str, content: str) -> dict:
    """构建 echo 口径推送负载——钉钉 markdown / 飞书 interactive 卡片。

    **归位单一口径**：逐字段对齐 opensoul/src/echo/dispatcher.py 的
    _send_dingtalk / _send_feishu 生成的 JSON 负载（不另立山头）。改动此函数
    必须同步改 echo dispatcher，并让 test_echo_payload_matches_opensoul_echo 通过。

    只负责"负载体"，不含签名（钉钉加签 / 飞书签名校验在 _signed_request 处按需附加）。
    """
    ch = str(channel or "").strip().lower()
    if ch == CHANNEL_DINGTALK:
        return {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": f"## {title}\n\n{content}",
            },
        }
    if ch == CHANNEL_FEISHU:
        return {
            "msg_type": "interactive",
            "card": {
                "header": {"title": {"tag": "plain_text", "content": title}},
                "elements": [{"tag": "markdown", "content": content}],
            },
        }
    raise ValueError(f"echo 口径不支持通道: {channel}（支持 {'/'.join(DOMESTIC_CHANNELS)}）")


# ── 可选签名（webhook 机器人安全设置开启时） ────────────────────────


def _dingtalk_sign(secret: str, timestamp_ms: int) -> str:
    """钉钉加签：sign = base64(hmac_sha256(secret, f"{timestamp}\\n{secret}"))。"""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _feishu_sign(secret: str, timestamp_s: int, nonce: str) -> str:
    """飞书群机器人签名校验：sign = sha256(f"{timestamp}\\n{nonce}\\n{secret}") 后 base64。

    注意飞书官方算法是把 "{timestamp}\\n{nonce}\\n{secret}" 作为 string_to_sign 做
    sha256 再 base64（部分文档写作 HMAC，此处按官方 sha256 摘要口径）。仅在机器人
    开启"签名校验"时需要；未开启则不加 sign 字段。
    """
    string_to_sign = f"{timestamp_s}\n{nonce}\n{secret}"
    digest = hashlib.sha256(string_to_sign.encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")


# ── 出站 HTTP（Level 1 webhook 快路核心） ─────────────────────────


def _post_json_full(
    url: str, payload: dict, *, headers: dict | None = None
) -> tuple[bool, str, dict]:
    """POST JSON，返回 (ok, detail, parsed_json)。成功判定看 HTTP 2xx + 平台 errcode。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    hdrs = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", "replace").strip()
            status = getattr(resp, "status", 200) or 200
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}: {e.reason}", {}
    except Exception as e:  # noqa: BLE001 — 网络层异常归一为失败明细
        return False, f"{type(e).__name__}: {e}", {}

    # 平台业务错误（钉钉 errcode / 飞书 code）——HTTP 200 也算失败
    try:
        data = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        data = {}
    if isinstance(data, dict):
        if "errcode" in data and data.get("errcode", 0) != 0:
            return False, f"钉钉 errcode={data.get('errcode')}: {data.get('errmsg', '')}", data
        if "code" in data and data.get("code", 0) not in (0, None):
            return False, f"飞书 code={data.get('code')}: {data.get('msg', '')}", data
    ok = 200 <= status < 300
    return ok, (f"HTTP {status}" if ok else f"HTTP {status}"), data


def _post_json(url: str, payload: dict, *, headers: dict | None = None) -> tuple[bool, str]:
    """POST JSON 到 webhook，返回 (ok, detail)。_post_json_full 的薄封装。"""
    ok, detail, _ = _post_json_full(url, payload, headers=headers)
    return ok, detail


def send_webhook(
    channel: str,
    webhook_url: str,
    title: str,
    content: str,
    *,
    secret: str = "",
) -> tuple[bool, str]:
    """Level 1 · webhook 机器人快路：把一条消息按 **echo 口径** 推到 webhook 地址。

    用户只需给一个钉钉/飞书 webhook 地址即能收到 SoulMate 真消息（验收钩子）。

    secret 非空时按通道附加签名（钉钉加签 / 飞书签名校验）——机器人开了安全设置
    才需要；默认不带签名，与 echo 口径负载逐字节一致。

    返回 (ok, detail)。URL 为空 → (False, "webhook 地址不能为空")。
    """
    url = str(webhook_url or "").strip()
    if not url:
        return False, "webhook 地址不能为空"
    ch = str(channel or "").strip().lower()
    try:
        payload = echo_payload(ch, title, content)
    except ValueError as e:
        return False, str(e)

    if str(secret or "").strip():
        payload = dict(payload)  # 不改 echo_payload 原返回（签名只作用于请求）
        if ch == CHANNEL_DINGTALK:
            ts = int(time.time() * 1000)
            sign = _dingtalk_sign(secret.strip(), ts)
            url = f"{url}&timestamp={ts}&sign={sign}" if "?" in url else f"{url}?timestamp={ts}&sign={sign}"
        elif ch == CHANNEL_FEISHU:
            ts = int(time.time())
            nonce = f"{ts}"
            sign = _feishu_sign(secret.strip(), ts, nonce)
            payload["timestamp"] = str(ts)
            payload["nonce"] = nonce
            payload["sign"] = sign

    ok, detail = _post_json(url, payload)
    return ok, detail


# ── 配置（本地凭据，不进 git） ─────────────────────────────────────


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def webhook_url(channel: str) -> str:
    """取通道 webhook 地址（环境变量 DINGTALK_WEBHOOK_URL / FEISHU_WEBHOOK_URL）。"""
    ch = str(channel or "").strip().lower()
    if ch == CHANNEL_DINGTALK:
        return _env("DINGTALK_WEBHOOK_URL")
    if ch == CHANNEL_FEISHU:
        return _env("FEISHU_WEBHOOK_URL")
    return ""


def webhook_secret(channel: str) -> str:
    """取通道 webhook 安全密钥（机器人加签/签名校验开启时才需）。"""
    ch = str(channel or "").strip().lower()
    if ch == CHANNEL_DINGTALK:
        return _env("DINGTALK_WEBHOOK_SECRET")
    if ch == CHANNEL_FEISHU:
        return _env("FEISHU_WEBHOOK_SECRET")
    return ""


def app_credentials(channel: str) -> tuple[str, str]:
    """取自建应用凭据 (app_id, app_secret)——Level 2 全双工开启条件。"""
    ch = str(channel or "").strip().lower()
    if ch == CHANNEL_DINGTALK:
        return _env("DINGTALK_APP_KEY"), _env("DINGTALK_APP_SECRET")
    if ch == CHANNEL_FEISHU:
        return _env("FEISHU_APP_ID"), _env("FEISHU_APP_SECRET")
    return "", ""


# ── 入站消息中立信封（对齐 WeChatMessage） ─────────────────────────


@dataclass
class DomesticMessage:
    """一条入站国内网关消息（transport 无关的中立形状）。"""

    channel: str
    msg_id: str
    from_user: str
    content: str
    from_name: str = ""
    msg_type: str = "text"
    received_at_ms: int = 0
    media: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.received_at_ms:
            self.received_at_ms = int(time.time() * 1000)


# ── 传输层抽象（Level 2 全双工） ───────────────────────────────────


class DomesticTransport(ABC):
    """国内网关传输层抽象：拉取入站 + 回复 + 主动发（对齐 WeChatTransport）。"""

    @abstractmethod
    async def fetch_inbound(self) -> list[DomesticMessage]:
        """拉取待处理入站消息（空轮询返回 []，不得抛超时类异常）。"""

    @abstractmethod
    async def send_reply(self, msg: DomesticMessage, text: str) -> bool:
        """回复该消息的对端。返回是否成功。"""

    async def send_text(self, peer: str, text: str) -> bool:
        """主动发消息给 peer（无入站上下文）。默认不支持，支持的传输覆写。"""
        raise NotImplementedError(
            f"{type(self).__name__} 不支持主动发送（send_text 未实现）")


class LoopbackTransport(DomesticTransport):
    """进程内回环传输（测试/本地验证收发回路；真实平台传输接入前的默认实现）。"""

    def __init__(self, channel: str = CHANNEL_DINGTALK) -> None:
        self.channel = channel
        self._inbound: asyncio.Queue[DomesticMessage] = asyncio.Queue()
        self.sent: list[tuple[DomesticMessage, str]] = []
        self.proactive: list[tuple[str, str]] = []

    def push(self, msg: DomesticMessage) -> None:
        """注入一条入站消息（模拟钉钉/飞书来消息）。"""
        self._inbound.put_nowait(msg)

    async def fetch_inbound(self) -> list[DomesticMessage]:
        out: list[DomesticMessage] = []
        while True:
            try:
                out.append(self._inbound.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    async def send_reply(self, msg: DomesticMessage, text: str) -> bool:
        self.sent.append((msg, text))
        return True

    async def send_text(self, peer: str, text: str) -> bool:
        self.proactive.append((peer, text))
        return True


class WebhookReplyTransport(DomesticTransport):
    """webhook 回复传输（Level 1/2 混合）：回复/主动发都走 webhook 机器人快路。

    入站 fetch_inbound 恒空（webhook 机器人是单向推送，收不到用户消息）——
    这是 Level 1 的诚实边界：只能"发"不能"收"。全双工收消息见 DomesticAppTransport。
    """

    def __init__(self, channel: str, webhook: str, secret: str = "") -> None:
        self.channel = channel
        self._webhook = webhook
        self._secret = secret

    async def fetch_inbound(self) -> list[DomesticMessage]:
        return []  # webhook 机器人无入站

    async def send_reply(self, msg: DomesticMessage, text: str) -> bool:
        ok, _ = send_webhook(self.channel, self._webhook, msg.from_name or "SoulMate", text,
                             secret=self._secret)
        return ok

    async def send_text(self, peer: str, text: str) -> bool:
        ok, _ = send_webhook(self.channel, self._webhook, peer or "SoulMate", text,
                             secret=self._secret)
        return ok


# ── 持久层：去重 + peer→session 映射（对齐 BridgeStore） ────────────


class DomesticStore:
    """SQLite 持久化：seen(msg_id 去重) + peers(peer→session 映射)。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = db_path or os.environ.get("SOULMATE_DOMESTIC_DB_PATH") or _DEFAULT_DB_PATH
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
        db = self._connect()
        try:
            cur = db.execute(
                "INSERT OR IGNORE INTO seen(msg_id, seen_at_ms) VALUES(?, ?)",
                (str(msg_id), int(now_ms)),
            )
            db.commit()
            return cur.rowcount > 0
        finally:
            db.close()

    def session_for(self, peer: str) -> str | None:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT session_id FROM peers WHERE peer=?", (str(peer),)
            ).fetchone()
            return str(row["session_id"]) if row else None
        finally:
            db.close()

    def remember_session(self, peer: str, session_id: str, now_ms: int) -> None:
        db = self._connect()
        try:
            db.execute(
                "INSERT INTO peers(peer, session_id, last_seen_ms) VALUES(?,?,?) "
                "ON CONFLICT(peer) DO UPDATE SET session_id=excluded.session_id, "
                "last_seen_ms=excluded.last_seen_ms",
                (str(peer), str(session_id), int(now_ms)),
            )
            db.commit()
        finally:
            db.close()


# ── 默认回合体（对齐 make_default_turn_fn） ─────────────────────────


def build_domestic_prompt(msg: DomesticMessage) -> str:
    """国内网关回合 prompt——显式声明消息来自钉钉/飞书用户，要求直接回复。"""
    who = msg.from_name or msg.from_user or f"{msg.channel}用户"
    return (
        f"{DOMESTIC_TURN_MARKER}｜来自{msg.channel}用户「{who}」的消息，系统转入本会话执行]\n"
        f"{msg.content}\n"
        "请直接回复这条消息（简洁、对方能懂）；只做该消息要求的事。"
    )


def _collect_assistant_text(agent, session_id: str, after_id: int | None) -> str:
    """收集本轮新落库的 assistant 输出文本（turn 归因锚点之后，按 id 序拼接）。"""
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
    store: DomesticStore,
    get_agent: Callable[[], Any] | None = None,
) -> Callable[[DomesticMessage], Coroutine[Any, Any, str]]:
    """默认回合体：peer→session 映射（首见自动建会话）+ 无客户端 SoulMate 回合。

    失败纪律复用 wakeup_executor（refusal/LLM 级失败/零输出都抛异常不假装成功，
    由 bridge 发失败告知）。
    """

    async def _turn(msg: DomesticMessage) -> str:
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
                raise RuntimeError("国内网关回合建会话失败：new_session 未返回 session_id")
            store.remember_session(msg.from_user, sid, msg.received_at_ms)
        pre_id = we._max_assistant_id(agent, sid)
        agent.set_turn_origin(sid, DOMESTIC_TURN_ORIGIN)
        try:
            resp = await agent.prompt(
                [{"type": "text", "text": build_domestic_prompt(msg)}], sid
            )
        finally:
            agent.clear_turn_origin(sid)
        stop_reason = str(getattr(resp, "stop_reason", "") or "")
        if stop_reason == "refusal":
            raise RuntimeError("国内网关回合被拒：会话在服务端不存在，拒绝给消失的会话造回合")
        bad = we._llm_failure_reply(agent, sid, pre_id)
        if bad:
            raise RuntimeError("国内网关回合 LLM 级失败（provider 错误/超时机器标记）: " + bad)
        text = _collect_assistant_text(agent, sid, pre_id)
        if not text.strip():
            raise RuntimeError("国内网关回合零输出（未落库任何非空 assistant 输出）——不发空回复")
        return text

    return _turn


# ── 桥主体（对齐 WeChatBridge） ────────────────────────────────────


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


class DomesticBridge:
    """国内网关桥：拉取入站 → 去重 → 跑 SoulMate 回合 → 回复（拆块）。

    单条消息失败绝不打断回路（失败告知 + 计数，继续后续）。
    """

    def __init__(
        self,
        channel: str,
        transport: DomesticTransport,
        turn_fn: Callable[[DomesticMessage], Coroutine[Any, Any, str]] | None = None,
        *,
        store: DomesticStore | None = None,
        audit: object | None = None,
        poll_interval_s: float | None = None,
        max_block_chars: int | None = None,
        failure_notice: str = DEFAULT_FAILURE_NOTICE,
    ):
        self.channel = str(channel or "").strip().lower()
        self._transport = transport
        self._store = store or DomesticStore()
        self._audit = audit
        self._turn_fn = turn_fn or make_default_turn_fn(self._store)
        self._poll_interval_s = float(
            poll_interval_s if poll_interval_s is not None
            else float(os.environ.get("SOULMATE_DOMESTIC_POLL_S", "5"))
        )
        self._max_block_chars = int(
            max_block_chars if max_block_chars is not None
            else int(os.environ.get("SOULMATE_DOMESTIC_MAX_BLOCK", "2000"))
        )
        self._failure_notice = failure_notice
        self._task: asyncio.Task | None = None
        self._running = False
        self.stats = _BridgeStats()

    def _split_blocks(self, text: str) -> list[str]:
        """长回复按行边界贪心拆块（单条有长度上限；块间独立发送）。"""
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

    async def _send_blocks(self, msg: DomesticMessage, text: str) -> int:
        sent = 0
        for block in self._split_blocks(text):
            try:
                ok = await self._transport.send_reply(msg, block)
            except Exception as e:  # 传输异常不炸回路
                self.stats.send_failed += 1
                self._note_error(f"send_reply 异常: {e}")
                ok = False
            if ok:
                sent += 1
                self.stats.replies_sent += 1
            else:
                self.stats.send_failed += 1
        self._record_out("reply", str(msg.from_user or ""), "sent" if sent else "failed",
                         blocks=sent, dedup_key=str(msg.msg_id or ""),
                         text_preview=str(text or ""))
        return sent

    def _note_error(self, msg: str) -> None:
        self.stats.last_error = str(msg)[:300]
        self.stats.last_error_at = int(time.time() * 1000)
        logger.warning("[domestic:%s] %s", self.channel, msg)

    def _record_out(self, kind: str, target: str, status: str, *,
                    blocks: int = 1, dedup_key: str | None = None,
                    text_preview: str = "") -> None:
        """出站痕迹落审计行（旁路纪律失败只计数不冒泡）。"""
        try:
            audit = self._audit
            if audit is None:
                from agent.outbound_audit import get_outbound_audit
                audit = get_outbound_audit()
                self._audit = audit
            audit.record(self.channel, target, kind, status, blocks=blocks,
                         dedup_key=dedup_key, text_preview=text_preview)
        except Exception:  # noqa: BLE001
            pass

    async def _handle(self, msg: DomesticMessage) -> None:
        await self.deliver(msg)

    async def deliver(self, msg: DomesticMessage) -> str:
        """投递单条入站消息（去重→回合→回复），返回 'delivered'|'duplicate'|'error'。

        回合失败发失败告知后返回 'error'（失败必须可见，绝不无声消失）。
        """
        if not self._store.first_time_seen(msg.msg_id, msg.received_at_ms):
            self.stats.duplicates += 1
            return "duplicate"
        self.stats.fetched += 1
        try:
            reply = await self._turn_fn(msg)
        except Exception as e:  # noqa: BLE001
            self.stats.turns_failed += 1
            self._note_error(f"回合失败({msg.msg_id}): {e}")
            await self._send_blocks(msg, self._failure_notice)
            return "error"
        self.stats.turns_ok += 1
        sent = await self._send_blocks(msg, reply)
        return "delivered" if sent else "error"

    def session_for(self, peer: str) -> str | None:
        return self._store.session_for(peer)

    async def send_proactive(self, peer: str, text: str) -> int:
        """主动发消息给 peer（无入站上下文）。返回发送成功块数（0=全失败）。"""
        if not str(peer or "").strip():
            raise ValueError("peer 不能为空")
        sent = 0
        for block in self._split_blocks(text):
            try:
                ok = await self._transport.send_text(peer, block)
            except Exception as e:  # noqa: BLE001
                self.stats.send_failed += 1
                self._note_error(f"send_text 异常: {e}")
                ok = False
            if ok:
                sent += 1
                self.stats.replies_sent += 1
            else:
                self.stats.send_failed += 1
        self._record_out("text", str(peer), "sent" if sent else "failed",
                         blocks=sent, text_preview=str(text or ""))
        return sent

    async def run_once(self) -> int:
        """处理当前待处理入站消息一批（返回处理条数；重复投递不计）。"""
        self.stats.polls += 1
        try:
            inbound = await self._transport.fetch_inbound()
        except Exception as e:  # noqa: BLE001
            self._note_error(f"fetch_inbound 异常: {e}")
            return 0
        handled = 0
        for msg in inbound:
            try:
                await self._handle(msg)
                handled += 1
            except Exception as e:  # noqa: BLE001 — 兜底：单条炸不打断整批
                self._note_error(f"handle 异常({getattr(msg, 'msg_id', '?')}): {e}")
        return handled

    async def run_forever(self) -> None:
        self._running = True
        while self._running:
            await self.run_once()
            await asyncio.sleep(self._poll_interval_s)

    def ensure_started(self) -> None:
        """幂等拉起后台回路（start.py stdio 入口惯例）。"""
        if self._task is None or self._task.done():
            self._task = asyncio.get_event_loop().create_task(self.run_forever())
            logger.info("[domestic:%s] 通道回路已启动（poll=%ss）", self.channel, self._poll_interval_s)

    def snapshot(self) -> dict:
        return {
            "channel": self.channel,
            "transport": type(self._transport).__name__,
            "poll_interval_s": self._poll_interval_s,
            **self.stats.__dict__,
        }


# ── Level 2 自建应用传输（真凭据：appid/secret → 平台 API 收发） ─────


class DomesticAppTransport(DomesticTransport):
    """自建应用全双工传输（钉钉/飞书开放平台）。

    发送侧（真）：tenant_access_token / accessToken → 平台机器人发消息 API。
    接收侧（诚实边界）：真实入站需在开放平台配事件回调（HTTP 回调 + 签名/解密）
    或 Stream 长连接，**待用户提供 appid/secret + 回调地址后接入**（见
    scripts/test_full_duplex.py）。当前 fetch_inbound 从内存事件队列取（由回调
    接收器 push），无回调时恒空——不假装能收到消息。

    本类把"能测的"（token 获取、发送 API 负载、签名/解密）都做成纯函数/可 mock，
    真实网络调用走 _http_post_json（测试可换桩）。
    """

    def __init__(self, channel: str, app_id: str, app_secret: str, *,
                 peer: str = "", http_full: Callable[..., tuple[bool, str, dict]] | None = None):
        self.channel = str(channel or "").strip().lower()
        self.app_id = app_id
        self.app_secret = app_secret
        self.peer = peer  # 全双工默认回复对端（单聊 open_id / 群 chat_id）
        self._http_full = http_full or _post_json_full
        self._inbound: asyncio.Queue[DomesticMessage] = asyncio.Queue()
        self._token: str = ""
        self._token_expire_at: float = 0.0

    def push_inbound(self, msg: DomesticMessage) -> None:
        """回调接收器把解出的入站消息塞进来（Level 2 收消息入口）。"""
        self._inbound.put_nowait(msg)

    async def fetch_inbound(self) -> list[DomesticMessage]:
        out: list[DomesticMessage] = []
        while True:
            try:
                out.append(self._inbound.get_nowait())
            except asyncio.QueueEmpty:
                break
        return out

    # ── token（真 API，可 mock） ────────────────────────────────
    def _token_url(self) -> str:
        if self.channel == CHANNEL_DINGTALK:
            return "https://api.dingtalk.com/v1.0/oauth2/accessToken"
        return "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"

    def _token_payload(self) -> dict:
        if self.channel == CHANNEL_DINGTALK:
            return {"appKey": self.app_id, "appSecret": self.app_secret}
        return {"app_id": self.app_id, "app_secret": self.app_secret}

    def ensure_token(self) -> str:
        """取平台访问令牌（带 5 分钟提前量缓存）。"""
        if self._token and time.time() < self._token_expire_at - 300:
            return self._token
        token, expire = self._fetch_token()
        if not token:
            raise RuntimeError(f"{self.channel} 取访问令牌失败（凭据错/网络不通）")
        self._token = token
        self._token_expire_at = time.time() + max(60, expire)
        return self._token

    def _fetch_token(self) -> tuple[str, int]:
        """POST 取 (token, expires_in)。真调用 _post_json_full，解析平台字段。

        钉钉 /v1.0/oauth2/accessToken → {"accessToken", "expireIn"}；
        飞书 /auth/v3/tenant_access_token/internal → {"tenant_access_token", "expire"}。
        """
        ok, detail, data = self._http_full(self._token_url(), self._token_payload())
        if not ok:
            self._note(f"取 token 失败: {detail}")
            return "", 0
        token = str(
            data.get("accessToken")
            or data.get("tenant_access_token")
            or data.get("access_token")
            or ""
        )
        try:
            expire = int(data.get("expireIn") or data.get("expire") or data.get("expires_in") or 7200)
        except (TypeError, ValueError):
            expire = 7200
        return token, expire

    # ── 发送（对齐 WeChatTransport 契约） ────────────────────────
    async def send_reply(self, msg: DomesticMessage, text: str) -> bool:
        return await self.send_text(msg.from_user or self.peer, text)

    async def send_text(self, peer: str, text: str) -> bool:
        """平台机器人发消息 API（真）。_send_api_payload 构造负载，_http_post_json 发。"""
        target = str(peer or self.peer or "").strip()
        if not target:
            return False
        try:
            self.ensure_token()
        except Exception as e:  # noqa: BLE001
            self._note(f"token 失败: {e}")
            return False
        url, payload, headers = self._send_api(target, text)
        ok, detail, _ = self._http_full(url, payload, headers=headers)
        if not ok:
            self._note(f"发送失败({target}): {detail}")
        return ok

    def _send_api(self, target: str, text: str) -> tuple[str, dict, dict]:
        """构造平台发消息 (url, payload, headers)。纯函数，测试可断言负载形状。"""
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self.channel == CHANNEL_DINGTALK:
            headers["x-acs-dingtalk-access-token"] = self._token
            return (
                "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                {
                    "robotCode": self.app_id,
                    "userIds": [target],
                    "msgKey": "sampleText",
                    "msgParam": json.dumps({"content": text}, ensure_ascii=False),
                },
                headers,
            )
        headers["Authorization"] = f"Bearer {self._token}"
        return (
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            {
                "receive_id": target,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            headers,
        )

    def _note(self, msg: str) -> None:
        logger.warning("[domestic-app:%s] %s", self.channel, msg)


# ── 事件校验/解密（Level 2 回调接收，纯函数可测） ──────────────────


def dingtalk_verify_sign(secret: str, timestamp: str, sign: str) -> bool:
    """钉钉回调加签校验（HMAC-SHA256 + base64）。"""
    return hmac.compare_digest(_dingtalk_sign(secret, int(timestamp)), str(sign))


def feishu_decrypt_event(encrypt_key: str, encrypt_b64: str) -> dict:
    """飞书事件加密体解密（AES-256-CBC，key=sha256(encrypt_key)，IV=密文前 16 字节）。

    返回解出的事件 dict。凭据（encrypt_key）本地配置，不进 git。
    """
    try:
        from Crypto.Cipher import AES  # pycryptodome（可选依赖，仅飞书解密用）
    except ImportError as e:  # noqa: BLE001
        raise RuntimeError("飞书事件解密需 pycryptodome（pip install pycryptodome）") from e
    raw = base64.b64decode(encrypt_b64)
    key = hashlib.sha256(encrypt_key.encode("utf-8")).digest()
    iv, cipher_bytes = raw[:16], raw[16:]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plain = cipher.decrypt(cipher_bytes)
    pad = plain[-1]
    plain = plain[:-pad] if 0 < pad <= 16 else plain
    return json.loads(plain.decode("utf-8"))


# ── 单例（get_wechat_bridge 同款惯例） ─────────────────────────────

_bridges: dict[str, DomesticBridge] = {}


def get_domestic_bridge(channel: str) -> DomesticBridge:
    """懒构建国内网关桥单例（传输按配置选择：webhook 快路 / loopback / 自建应用）。

    - 配了 webhook 地址（无 appid）→ WebhookReplyTransport（Level 1 只发不收）；
    - 配了 appid/secret → DomesticAppTransport（Level 2 全双工）；
    - 都没配 → LoopbackTransport（本地回路，默认，不外发）。
    """
    ch = str(channel or "").strip().lower()
    if ch not in DOMESTIC_CHANNELS:
        raise ValueError(f"未知国内网关通道: {channel}（支持 {'/'.join(DOMESTIC_CHANNELS)}）")
    if ch in _bridges:
        return _bridges[ch]
    app_id, app_secret = app_credentials(ch)
    url = webhook_url(ch)
    if app_id and app_secret:
        transport: DomesticTransport = DomesticAppTransport(ch, app_id, app_secret)
    elif url:
        transport = WebhookReplyTransport(ch, url, webhook_secret(ch))
    else:
        transport = LoopbackTransport(ch)
    _bridges[ch] = DomesticBridge(ch, transport)
    return _bridges[ch]
