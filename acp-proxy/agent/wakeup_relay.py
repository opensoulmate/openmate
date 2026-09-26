"""WakeupRelay — 唤醒回合实时可见的跨进程推送面（dev-reports 11:11遗留#3销账）。

缺口（多轮挂账原文）："唤醒回合以无客户端模式执行：消息落opensoul.db，前端下次
加载可见，但无实时流式推送（_notify_client在client=None时no-op）。若会话正连在
某个proxy的WS上，唤醒回合发生在赢得claim的实例里（可能不是连接所在实例），
实时可见性不保证——如需'运行中可见'要接跨进程推送"。

方案（跨进程推送，对称双活）：opensoul.db的agent_messages是单一真源——唤醒回合
落库行带 turn_origin=wakeup 标记（wakeup_executor.set_turn_origin窗口 +
SoulMateAgent._save_message metadata合并），每个proxy实例给**本地正连的** WS会话
跑一个WakeupRelay轮询任务，把标记行补推为ACP session/update通知。双实例a/b各自
轮询共享库、各自推给各自的WS客户端——唤醒回合无论在哪个实例执行都可见，无需
实例间互知/HTTP扇出（对称、无路由表、断连自愈）。

重复推送防线：普通回合行不带标记（子进程自己流式推送），relay只推标记行——
不与既有流式路径重复。前端chat-client已处理sessionUpdate=agent_message
（一次性完整消息），relay即用该形状。

粒度诚实声明：消息级实时（默认2s轮询，env WAKEUP_RELAY_INTERVAL_S可调），
非token级流式——token chunk跨进程不可复原（不落库），消息落库即推是可达上限。
会话在relay启动前已落库的行不补推（前端历史加载覆盖；毫秒级竞态窗口有意接受）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time

from agent.wakeup_executor import WAKEUP_TURN_ORIGIN

logger = logging.getLogger("acp-proxy.wakeup_relay")

_OPENSOUL_ROOT = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
DEFAULT_DB_PATH = f"{_OPENSOUL_ROOT}/data/opensoul.db"

# 模块级观测计数（ws_chat /health wakeup_relay键透出——失败必须可见，mem0 §1.1）
_STATS: dict = {
    "relays_started": 0,
    "relays_active": 0,
    "sid_timeout": 0,
    "rows_pushed": 0,
    "push_errors": 0,
    "poll_errors": 0,
}


def relay_stats() -> dict:
    """relay观测快照（/health消费面）"""
    return dict(_STATS)


def is_wakeup_meta(metadata: str | None) -> bool:
    """该落库行是否唤醒回合出身（turn_origin标记；解析失败/无标记=否）"""
    if not metadata:
        return False
    try:
        meta = json.loads(metadata)
    except (ValueError, TypeError):
        return False
    return isinstance(meta, dict) and meta.get("turn_origin") == WAKEUP_TURN_ORIGIN


def fetch_rows(db_path: str, session_id: str, after_id: int) -> list[dict]:
    """取会话after_id之后的落库行（id升序），逐行带is_wakeup出身判定。

    连未标记行一起返回：调用方需用其id推进游标（未标记行不重扫），
    推送侧只推is_wakeup=True的行。"""
    db = sqlite3.connect(db_path)
    try:
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT id, role, content, metadata FROM agent_messages "
                "WHERE session_id=? AND id>? ORDER BY id",
                (session_id, int(after_id)),
            ).fetchall()
        except sqlite3.OperationalError:
            # 表/列缺失（空库/旧库）：fail-safe空结果，不炸轮询循环
            return []
    finally:
        db.close()
    return [
        {
            "id": int(r["id"]),
            "role": r["role"],
            "content": r["content"] or "",
            "is_wakeup": is_wakeup_meta(r["metadata"]),
        }
        for r in rows
    ]


def max_row_id(db_path: str, session_id: str) -> int:
    """会话当前最大消息id（relay基线：启动前已有的行不补推）"""
    db = sqlite3.connect(db_path)
    try:
        try:
            row = db.execute(
                "SELECT MAX(id) FROM agent_messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    finally:
        db.close()
    return int(row[0]) if row and row[0] is not None else 0


def update_payload(session_id: str, row: dict) -> dict:
    """落库行 → ACP session/update通知（agent_message一次性完整消息形状）。

    与前端chat-client.tsx既有sessionUpdate=agent_message处理面完全同形
    （update.content.text），无需前端改动。user出身行（唤醒prompt系统自动回合
    声明）同样以agent_message透出——前端session/update无user_message处理面，
    且该文本自带"系统自动回合，非用户消息"声明，展示语义自洽。"""
    return {
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": {
            "sessionId": session_id,
            "update": {
                "sessionUpdate": "agent_message",
                "content": {"type": "text", "text": row.get("content") or ""},
            },
        },
    }


class WakeupRelay:
    """单WS连接的唤醒回合补推任务：等sid → 基线 → 轮询推送标记行。"""

    def __init__(
        self,
        session_id_getter,
        send,
        db_path: str = "",
        interval_s: float = 0.0,
        sid_wait_s: float = 60.0,
    ):
        self._sid_getter = session_id_getter  # () -> str|None（ws_acp捕获acp_sid）
        self._send = send  # async (payload: dict) -> None（推给本WS客户端）
        self._db_path = db_path or DEFAULT_DB_PATH
        self._interval_s = float(
            interval_s or float(os.environ.get("WAKEUP_RELAY_INTERVAL_S", "2"))
        )
        self._sid_wait_s = float(sid_wait_s)
        self._last_id = 0
        self._running = False

    async def _wait_sid(self) -> str | None:
        """等ws_acp捕获到ACP sessionId（session/new响应透传途中）"""
        deadline = time.time() + self._sid_wait_s
        while time.time() < deadline:
            sid = self._sid_getter()
            if sid:
                return str(sid)
            await asyncio.sleep(0.2)
        return None

    async def poll_once(self, session_id: str) -> int:
        """一轮：取新行 → 推标记行 → 推进游标。返回推送条数。"""
        rows = fetch_rows(self._db_path, session_id, self._last_id)
        pushed = 0
        for row in rows:
            # 游标先推进（未标记行不重扫），推送失败也不回头重推已推行
            self._last_id = max(self._last_id, row["id"])
            if not row["is_wakeup"]:
                continue
            try:
                await self._send(update_payload(session_id, row))
                pushed += 1
                _STATS["rows_pushed"] += 1
            except Exception as e:
                # WS已死/发送失败：失败可见（计数+journal），终止本relay
                # （后续send只会继续失败；连接清理由ws_acp端点负责）
                _STATS["push_errors"] += 1
                logger.warning("[wakeup-relay] push failed, stopping relay: %s", e)
                self._running = False
                break
        return pushed

    async def run(self):
        """主循环（ws_acp为每个soulmate WS连接起一个任务，连接清理时cancel）"""
        _STATS["relays_started"] += 1
        _STATS["relays_active"] += 1
        try:
            sid = await self._wait_sid()
            if not sid:
                _STATS["sid_timeout"] += 1
                logger.warning("[wakeup-relay] session id not captured in time, relay退出")
                return
            self._last_id = max_row_id(self._db_path, sid)
            self._running = True
            logger.info(
                "[wakeup-relay] started for session %s (baseline id=%d, interval=%.1fs)",
                sid,
                self._last_id,
                self._interval_s,
            )
            while self._running:
                await asyncio.sleep(self._interval_s)
                try:
                    await self.poll_once(sid)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    _STATS["poll_errors"] += 1
                    logger.warning("[wakeup-relay] poll error (下轮重试): %s", e)
        except asyncio.CancelledError:
            raise
        finally:
            _STATS["relays_active"] -= 1
