"""多渠道出站独立可查痕迹（路线图12.1）。

每条出站发送尝试落一行确定性审计记录（发送方/通道/去重键/时间/状态），
与回复文本解耦——验收 harness 可服务端回读痕迹判定，不再依赖"回复辅证"。

- 单写入源：本模块是出站痕迹的唯一落库入口（wechat 桥 / message_router 调用）。
- 旁路纪律：审计写失败只计数不冒泡，绝不阻塞发送主链路。
- iLink 真实传输待用户拍板不动；本模块只对现有通道（loopback/落库）留痕。

DB 默认 ~/.hermes/soulmate/memory/outbound_audit.db
（SOULMATE_OUTBOUND_AUDIT_DB 可覆盖；测试注入临时路径）。
"""

from __future__ import annotations

import os
import sqlite3
import time
import uuid

_DB_TIMEOUT_S = 10.0
_DEFAULT_DB_PATH = os.path.expanduser(
    "~/.hermes/soulmate/memory/outbound_audit.db")
_PREVIEW_LEN = 200


class OutboundAudit:
    """出站痕迹持久层：outbound_log 表 + 过滤回读。"""

    def __init__(self, db_path: str | None = None):
        self._db_path = (
            db_path or os.environ.get("SOULMATE_OUTBOUND_AUDIT_DB") or _DEFAULT_DB_PATH)
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.write_failures = 0
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=_DB_TIMEOUT_S)

    def _ensure_db(self) -> None:
        db = self._connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbound_log("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "ts_ms INTEGER NOT NULL,"
                "channel TEXT NOT NULL,"
                "target TEXT NOT NULL,"
                "kind TEXT NOT NULL,"
                "status TEXT NOT NULL,"
                "blocks INTEGER NOT NULL DEFAULT 1,"
                "sender TEXT NOT NULL DEFAULT 'soulmate',"
                "dedup_key TEXT NOT NULL,"
                "text_preview TEXT NOT NULL DEFAULT '')"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_outbound_target"
                " ON outbound_log(channel, target, ts_ms)")
            db.commit()
        finally:
            db.close()

    # ── 写入 ───────────────────────────────────────────────────

    def record(
        self,
        channel: str,
        target: str,
        kind: str,
        status: str,
        *,
        blocks: int = 1,
        sender: str = "soulmate",
        dedup_key: str | None = None,
        text_preview: str = "",
        now_ms: int | None = None,
    ) -> str:
        """落一行出站痕迹，返回 dedup_key。写失败只计数返回 ''（旁路纪律）。"""
        key = str(dedup_key or f"out-{uuid.uuid4().hex[:16]}")
        preview = str(text_preview or "")[:_PREVIEW_LEN]
        try:
            db = self._connect()
            try:
                db.execute(
                    "INSERT INTO outbound_log"
                    "(ts_ms, channel, target, kind, status, blocks, sender,"
                    " dedup_key, text_preview) VALUES (?,?,?,?,?,?,?,?,?)",
                    (int(now_ms if now_ms is not None else time.time() * 1000),
                     str(channel or "").strip().lower(),
                     str(target or "").strip(),
                     str(kind or "text").strip().lower(),
                     str(status or "sent").strip().lower(),
                     int(blocks), str(sender or "soulmate"), key, preview),
                )
                db.commit()
            finally:
                db.close()
        except Exception:
            self.write_failures += 1
            return ""
        return key

    # ── 回读（验收 harness 服务端真源） ─────────────────────────

    def recent(
        self,
        channel: str | None = None,
        target: str | None = None,
        text_like: str | None = None,
        status: str | None = None,
        since_ms: int | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """按通道/目标/文本片段/状态/时间过滤回读（时间倒序）。"""
        sql = ("SELECT ts_ms, channel, target, kind, status, blocks, sender,"
               " dedup_key, text_preview FROM outbound_log WHERE 1=1")
        args: list = []
        if channel:
            sql += " AND channel = ?"
            args.append(str(channel).strip().lower())
        if target:
            sql += " AND target = ?"
            args.append(str(target).strip())
        if text_like:
            sql += " AND text_preview LIKE ?"
            args.append(f"%{text_like}%")
        if status:
            sql += " AND status = ?"
            args.append(str(status).strip().lower())
        if since_ms is not None:
            sql += " AND ts_ms >= ?"
            args.append(int(since_ms))
        sql += " ORDER BY ts_ms DESC, id DESC LIMIT ?"
        args.append(max(1, int(limit)))
        db = self._connect()
        try:
            rows = db.execute(sql, args).fetchall()
        finally:
            db.close()
        return [
            {
                "ts_ms": r[0], "channel": r[1], "target": r[2], "kind": r[3],
                "status": r[4], "blocks": r[5], "sender": r[6],
                "dedup_key": r[7], "text_preview": r[8],
            }
            for r in rows
        ]

    def clear(
        self,
        target: str | None = None,
        text_like: str | None = None,
    ) -> int:
        """清痕迹行（harness 复测铁律：验证前清陈旧行防假阳性）。返回删除行数。

        无过滤条件拒绝全表清空（防误删全部审计）。
        """
        if not target and not text_like:
            raise ValueError("clear 需要 target 或 text_like 过滤条件（拒绝全表清空）")
        sql = "DELETE FROM outbound_log WHERE 1=1"
        args: list = []
        if target:
            sql += " AND target = ?"
            args.append(str(target).strip())
        if text_like:
            sql += " AND text_preview LIKE ?"
            args.append(f"%{text_like}%")
        db = self._connect()
        try:
            cur = db.execute(sql, args)
            db.commit()
            return int(cur.rowcount or 0)
        finally:
            db.close()


_audit: OutboundAudit | None = None


def get_outbound_audit() -> OutboundAudit:
    """懒构建单例（get_scheduler/get_health_monitor 同款惯例）。"""
    global _audit
    if _audit is None:
        _audit = OutboundAudit()
    return _audit
