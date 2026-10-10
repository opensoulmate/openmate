"""HealthMonitor — 路线图 3.3 监控告警（对指定服务/网站做周期健康检查）。

缺口（路线图原文）："3.3 监控告警：对指定服务/网站做周期健康检查"。

方案（对齐 3.2 已验证的"落库行=单一真源+relay轮询推送"模式，不新造通道）：
1. **监控目标持久化**：monitor_targets（name/kind/target/interval_s/fail_threshold/
   session_id/enabled）+ monitor_state（fail_streak/ok_streak/last_status/latency）
   + monitor_events（每次检查一行执行日志）三表 SQLite，重启不丢；
2. **确定性健康检查**（不走LLM）三类 checker：
   - website：HTTP GET，2xx/3xx=ok，其余/异常=fail（超时可配）；
   - service：`systemctl --user is-active`，active=ok；
   - port：TCP connect（host:port），连通=ok。
   checker 可注入（测试 fake），单项检查异常降级 fail 不炸整轮；
3. **告警防抖**：fail_threshold 次连续失败才告警（防单次抖动误报），
   达阈值发 🚨 告警、之后每 _REMIND_EVERY 次续报一次（不失联）；
   恢复时发 ✅ 恢复通知（含中断累计失败次数）——状态迁移才说话，
   正常运行零打扰；告警行写进目标绑定会话的 agent_messages
   （role=assistant, turn_origin=health_alert），wakeup_relay 补推正连前端；
4. **周期执行**：run_forever 后台循环按 interval_s 到点检查（对齐
   SoulMateScheduler.ensure_started 惯例），start.py 有启用目标才自举。

纪律：
- 检查/告警内容确定性拼装，不走LLM——监控是事实转述；
- 告警落库失败绝不阻塞检查主链路，只计数+日志；
- 只读监控：检查动作不改目标系统（不kill/不重启/不写目标盘）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import sqlite3
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger("acp-proxy.health_monitor")

# 告警行出身标记（wakeup_relay 实时推送识别依据；与 wakeup/cron/cron_report 同族）
HEALTH_ALERT_TURN_ORIGIN = "health_alert"

_OPENSOUL_ROOT = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
DEFAULT_DB_PATH = os.environ.get(
    "SOULMATE_MONITOR_DB_PATH",
    os.path.expanduser("~/.hermes/soulmate/memory/monitor.db"))
# 告警行落库目标（与 cron_reporter 同一 opensoul.db 消息表）
DEFAULT_REPORT_DB_PATH = os.environ.get(
    "SOULMATE_REPORT_DB_PATH", f"{_OPENSOUL_ROOT}/data/opensoul.db")
_DB_TIMEOUT_S = 30.0
_DETAIL_CAP = 300

_MONITOR_KINDS = ("website", "service", "port")
_MIN_INTERVAL_S = 30          # 防手滑高频检查打爆目标
_DEFAULT_INTERVAL_S = 300
_DEFAULT_FAIL_THRESHOLD = 2   # 连续失败N次才告警（防抖）
_REMIND_EVERY = 10            # 持续故障每N次失败续报一次
_HTTP_TIMEOUT_S = float(os.environ.get("SOULMATE_MONITOR_HTTP_TIMEOUT_S", "10"))
_CONNECT_TIMEOUT_S = float(os.environ.get("SOULMATE_MONITOR_CONNECT_TIMEOUT_S", "5"))

# 模块级观测计数（失败必须可见，mem0 §1.1）
_STATS: dict = {
    "checks_run": 0,
    "check_errors": 0,
    "alerts_built": 0,
    "alerts_written": 0,
    "write_errors": 0,
    "skipped_no_session": 0,
}


def monitor_stats() -> dict:
    """监控面观测快照（测试/巡检消费面）"""
    return dict(_STATS)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _fmt_ms(ms: int | None) -> str:
    """毫秒UTC → 本地 'MM-DD HH:MM'（全局时间规范：存储UTC展示本地）"""
    if not ms:
        return "-"
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "-"


@dataclass
class CheckResult:
    """一次健康检查结果（status: ok/fail）"""
    status: str
    latency_ms: int
    detail: str


# ── 三类确定性 checker（可注入替换，测试用fake） ─────────────────

def check_website(target: str) -> CheckResult:
    """website：HTTP GET，2xx/3xx=ok，其余/异常=fail。只读GET不改目标。"""
    t0 = time.monotonic()

    def _lat() -> int:
        return int((time.monotonic() - t0) * 1000)

    try:
        req = urllib.request.Request(
            target, method="GET",
            headers={"User-Agent": "SoulMate-HealthMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT_S) as resp:
            code = int(getattr(resp, "status", 0) or resp.getcode() or 0)
            if 200 <= code < 400:
                return CheckResult("ok", _lat(), f"HTTP {code}")
            return CheckResult("fail", _lat(), f"HTTP {code}")
    except Exception as e:
        return CheckResult("fail", _lat(), f"{type(e).__name__}: {e}"[:_DETAIL_CAP])


def check_service(target: str) -> CheckResult:
    """service：systemd --user is-active，active=ok（五态其余=fail+原样状态词）。"""
    t0 = time.monotonic()
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", str(target)],
            capture_output=True, text=True, errors="replace", timeout=10)
        state = (proc.stdout or "").strip() or (proc.stderr or "").strip() or "unknown"
        latency = int((time.monotonic() - t0) * 1000)
        if state == "active":
            return CheckResult("ok", latency, "服务运行中(active)")
        return CheckResult("fail", latency, f"服务状态: {state}"[:_DETAIL_CAP])
    except Exception as e:
        return CheckResult(
            "fail", int((time.monotonic() - t0) * 1000),
            f"{type(e).__name__}: {e}"[:_DETAIL_CAP])


def check_port(target: str) -> CheckResult:
    """port：TCP connect（host:port），连通=ok。"""
    t0 = time.monotonic()

    def _lat() -> int:
        return int((time.monotonic() - t0) * 1000)

    host, _, port_s = str(target).rpartition(":")
    try:
        port = int(port_s)
        if not host or not (0 < port < 65536):
            raise ValueError(f"目标格式应为 host:port，收到: {target!r}")
    except ValueError as e:
        return CheckResult("fail", _lat(), f"目标格式错误: {e}"[:_DETAIL_CAP])
    try:
        with socket.create_connection((host, port), timeout=_CONNECT_TIMEOUT_S):
            return CheckResult("ok", _lat(), f"端口 {port} 监听中")
    except Exception as e:
        return CheckResult("fail", _lat(), f"{type(e).__name__}: {e}"[:_DETAIL_CAP])


_DEFAULT_CHECKERS = {
    "website": check_website,
    "service": check_service,
    "port": check_port,
}


class HealthMonitor:
    """监控告警面：目标持久化 + 周期检查 + 防抖告警落库（relay单一真源）。"""

    def __init__(
        self,
        db_path: str | None = None,
        report_db_path: str | None = None,
        now_fn=None,
        checkers: dict | None = None,
    ):
        self._db_path = db_path or DEFAULT_DB_PATH
        self._report_db_path = report_db_path or DEFAULT_REPORT_DB_PATH
        self._now_fn = now_fn or _now_ms
        self._checkers = dict(_DEFAULT_CHECKERS)
        if checkers:
            self._checkers.update(checkers)
        self._poll_interval_s = float(os.environ.get("SOULMATE_MONITOR_POLL_S", "30"))
        self._task: asyncio.Task | None = None
        self._running = False
        self._recent: list[str] = []
        parent = os.path.dirname(self._db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._ensure_db()

    # ── 存储层 ────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path, timeout=_DB_TIMEOUT_S)
        db.row_factory = sqlite3.Row
        return db

    def _ensure_db(self) -> None:
        db = self._connect()
        try:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitor_targets(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    target TEXT NOT NULL,
                    interval_s INTEGER NOT NULL DEFAULT 300,
                    fail_threshold INTEGER NOT NULL DEFAULT 2,
                    session_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS monitor_state(
                    target_id INTEGER PRIMARY KEY,
                    last_status TEXT NOT NULL DEFAULT '',
                    last_checked_at_ms INTEGER,
                    last_latency_ms INTEGER,
                    fail_streak INTEGER NOT NULL DEFAULT 0,
                    ok_streak INTEGER NOT NULL DEFAULT 0,
                    check_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS monitor_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_id INTEGER NOT NULL,
                    ts_ms INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_events_target
                    ON monitor_events(target_id, id);
                """
            )
            db.commit()
        finally:
            db.close()

    # ── 目标 CRUD ─────────────────────────────────────────────

    def add_target(
        self,
        name: str,
        kind: str,
        target: str,
        interval_s: int | None = None,
        fail_threshold: int | None = None,
        session_id: str = "",
    ) -> dict:
        """登记监控目标（校验失败抛 ValueError，dispatch 层转错误文本）。"""
        name = str(name or "").strip()
        kind = str(kind or "").strip().lower()
        target = str(target or "").strip()
        if not name:
            raise ValueError("name 必填（如『官网可用性』）")
        if kind not in _MONITOR_KINDS:
            raise ValueError(f"kind 必须是 {_MONITOR_KINDS} 之一，收到: {kind!r}")
        if not target:
            raise ValueError("target 必填（website=URL / service=服务名 / port=host:port）")
        if kind == "website" and not target.startswith(("http://", "https://")):
            raise ValueError(f"website 目标必须是 http(s) URL，收到: {target!r}")
        if kind == "port":
            host, _, port_s = target.rpartition(":")
            try:
                port = int(port_s)
            except ValueError:
                raise ValueError(f"port 目标格式应为 host:port，收到: {target!r}")
            if not host or not (0 < port < 65536):
                raise ValueError(f"port 目标格式应为 host:port，收到: {target!r}")
        try:
            interval = int(interval_s if interval_s is not None else _DEFAULT_INTERVAL_S)
        except (TypeError, ValueError):
            raise ValueError("interval_s 必须是整数秒")
        if interval < _MIN_INTERVAL_S:
            raise ValueError(f"interval_s 不能小于 {_MIN_INTERVAL_S} 秒（防高频打爆目标）")
        try:
            threshold = int(
                fail_threshold if fail_threshold is not None else _DEFAULT_FAIL_THRESHOLD)
        except (TypeError, ValueError):
            raise ValueError("fail_threshold 必须是整数")
        if threshold < 1:
            raise ValueError("fail_threshold 不能小于1")

        now = self._now_fn()
        db = self._connect()
        try:
            cur = db.execute(
                "INSERT INTO monitor_targets (name, kind, target, interval_s,"
                " fail_threshold, session_id, enabled, created_at_ms)"
                " VALUES (?,?,?,?,?,?,1,?)",
                (name, kind, target, interval, threshold,
                 str(session_id or ""), now),
            )
            db.commit()
            target_id = int(cur.lastrowid or 0)
        finally:
            db.close()
        self._note(f"监控目标已创建 #{target_id}「{name}」{kind}:{target}")
        return {
            "id": target_id, "target_id": target_id, "name": name, "kind": kind,
            "target": target,
            "interval_s": interval, "fail_threshold": threshold,
            "session_id": str(session_id or ""), "created_at_ms": now,
        }

    def _row_target(self, row: sqlite3.Row) -> dict:
        return dict(row)

    def list_targets(self, with_events: bool = True, event_limit: int = 3) -> list[dict]:
        """全部监控目标 + 当前状态 + 最近检查日志。"""
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT t.*, s.last_status, s.last_checked_at_ms, s.last_latency_ms,"
                " s.fail_streak, s.ok_streak, s.check_count, s.last_error"
                " FROM monitor_targets t LEFT JOIN monitor_state s"
                " ON s.target_id = t.id ORDER BY t.id").fetchall()
            targets = [self._row_target(r) for r in rows]
            if with_events:
                for t in targets:
                    evs = db.execute(
                        "SELECT kind, status, detail, ts_ms FROM monitor_events"
                        " WHERE target_id=? ORDER BY id DESC LIMIT ?",
                        (t["id"], int(event_limit)),
                    ).fetchall()
                    t["recent_events"] = [dict(e) for e in evs]
            return targets
        finally:
            db.close()

    def remove_target(self, target_id: int, delete: bool = False) -> tuple[bool, str]:
        """停用（缺省，可再启用）或删除监控目标（检查日志保留可审计）。"""
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return False, "target_id 必须是整数"
        if target_id <= 0:
            return False, "target_id 必填（monitor_list 可见）"
        db = self._connect()
        try:
            row = db.execute(
                "SELECT name, enabled FROM monitor_targets WHERE id=?",
                (target_id,)).fetchone()
            if row is None:
                return False, f"监控目标 #{target_id} 不存在"
            if delete:
                db.execute("DELETE FROM monitor_targets WHERE id=?", (target_id,))
                db.execute("DELETE FROM monitor_state WHERE target_id=?", (target_id,))
                db.commit()
                self._note(f"监控目标已删除 #{target_id}「{row['name']}」")
                return True, f"监控目标 #{target_id}「{row['name']}」已删除（检查日志保留）"
            if not row["enabled"]:
                return True, f"监控目标 #{target_id}「{row['name']}」已是停用状态"
            db.execute(
                "UPDATE monitor_targets SET enabled=0 WHERE id=?", (target_id,))
            db.commit()
            self._note(f"监控目标已停用 #{target_id}「{row['name']}」")
            return True, f"监控目标 #{target_id}「{row['name']}」已停用（不再检查）"
        finally:
            db.close()

    # ── 检查执行 ──────────────────────────────────────────────

    def run_check(self, target: dict) -> CheckResult:
        """对单个目标跑一次健康检查（checker缺失/异常降级fail，不炸整轮）。"""
        kind = str(target.get("kind", ""))
        checker = self._checkers.get(kind)
        if checker is None:
            return CheckResult("fail", 0, f"未知监控类型: {kind!r}")
        _STATS["checks_run"] += 1
        try:
            r = checker(str(target.get("target", "")))
            return CheckResult(
                "ok" if r.status == "ok" else "fail",
                int(r.latency_ms or 0), str(r.detail or "")[:_DETAIL_CAP])
        except Exception as e:
            _STATS["check_errors"] += 1
            return CheckResult("fail", 0, f"检查异常: {type(e).__name__}: {e}"[:_DETAIL_CAP])

    def _due_targets(self, now_ms: int, target_ids: list[int] | None,
                     respect_interval: bool) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT t.*, s.last_checked_at_ms FROM monitor_targets t"
                " LEFT JOIN monitor_state s ON s.target_id = t.id"
                " WHERE t.enabled=1 ORDER BY t.id").fetchall()
        finally:
            db.close()
        out = []
        for r in rows:
            t = self._row_target(r)
            if target_ids and int(t["id"]) not in target_ids:
                continue
            if respect_interval:
                last = t.get("last_checked_at_ms")
                if last and (now_ms - int(last)) < int(t["interval_s"]) * 1000:
                    continue
            out.append(t)
        return out

    def _update_state(self, target: dict, result: CheckResult,
                      now_ms: int) -> dict:
        """更新状态/连击计数，返回状态迁移判定（prev_status/prev_fail_streak/新streak）。"""
        db = self._connect()
        try:
            row = db.execute(
                "SELECT last_status, fail_streak, ok_streak, check_count"
                " FROM monitor_state WHERE target_id=?",
                (target["id"],)).fetchone()
            prev_status = str(row["last_status"]) if row else ""
            prev_fail_streak = int(row["fail_streak"]) if row else 0
            fail_streak = prev_fail_streak
            ok_streak = int(row["ok_streak"]) if row else 0
            check_count = int(row["check_count"]) if row else 0
            if result.status == "ok":
                fail_streak, ok_streak = 0, ok_streak + 1
            else:
                ok_streak, fail_streak = 0, fail_streak + 1
            check_count += 1
            db.execute(
                "INSERT INTO monitor_state (target_id, last_status, last_checked_at_ms,"
                " last_latency_ms, fail_streak, ok_streak, check_count, last_error)"
                " VALUES (?,?,?,?,?,?,?,?)"
                " ON CONFLICT(target_id) DO UPDATE SET"
                " last_status=excluded.last_status,"
                " last_checked_at_ms=excluded.last_checked_at_ms,"
                " last_latency_ms=excluded.last_latency_ms,"
                " fail_streak=excluded.fail_streak, ok_streak=excluded.ok_streak,"
                " check_count=excluded.check_count, last_error=excluded.last_error",
                (target["id"], result.status, now_ms, result.latency_ms,
                 fail_streak, ok_streak, check_count,
                 "" if result.status == "ok" else result.detail),
            )
            db.execute(
                "INSERT INTO monitor_events (target_id, ts_ms, kind, status, detail)"
                " VALUES (?,?,?,?,?)",
                (target["id"], now_ms, "check", result.status, result.detail),
            )
            db.commit()
        finally:
            db.close()
        return {
            "prev_status": prev_status,
            "prev_fail_streak": prev_fail_streak,
            "fail_streak": fail_streak,
            "ok_streak": ok_streak,
            "check_count": check_count,
        }

    def _build_alert(self, target: dict, event_kind: str,
                     result: CheckResult, state: dict, now_ms: int) -> str:
        """告警/恢复行文本——确定性拼装（任务名/状态/详情/时间），不走LLM。"""
        head = {
            "alert": "🚨 [监控告警]",
            "recovery": "✅ [监控恢复]",
            "remind": "🚨 [监控告警·持续]",
        }.get(event_kind, "🚨 [监控告警]")
        label = (f"「{target.get('name', '')}」"
                 f"({target.get('kind', '')}: {target.get('target', '')})")
        lines = [f"{head} {label}｜"
                 f"{'❌ 异常' if event_kind != 'recovery' else '已恢复'}"]
        lines.append(f"详情: {result.detail or '（无详情）'}")
        if event_kind == "recovery":
            lines.append(
                f"中断期间累计失败{state.get('prev_fail_streak', 0)}次"
                f"｜检查: {_fmt_ms(now_ms)}")
        else:
            lines.append(
                f"连续失败{state.get('fail_streak', 0)}次"
                f"（阈值{target.get('fail_threshold', 0)}次）"
                f"｜检查: {_fmt_ms(now_ms)}")
        _STATS["alerts_built"] += 1
        return "\n".join(lines)

    def _write_alert(self, target: dict, text: str) -> None:
        """告警行落 agent_messages（turn_origin=health_alert → relay补推）。

        绝不抛异常——告警落库失败不能阻塞检查主链路；无session_id显式跳过。
        """
        sid = str(target.get("session_id") or "").strip()
        if not sid:
            _STATS["skipped_no_session"] += 1
            logger.info("[monitor] 目标#%s 无session_id，告警无处可投，跳过落库",
                        target.get("id", "?"))
            return
        meta = {
            "turn_origin": HEALTH_ALERT_TURN_ORIGIN,
            "monitor_target_id": int(target.get("id") or 0),
            "kind": str(target.get("kind", "")),
            "target": str(target.get("target", "")),
        }
        try:
            db = sqlite3.connect(self._report_db_path, timeout=_DB_TIMEOUT_S)
            try:
                db.execute(
                    "INSERT INTO agent_messages (session_id, role, content, timestamp,"
                    " metadata) VALUES (?,?,?,?,?)",
                    (sid, "assistant", str(text),
                     self._now_fn() / 1000, json.dumps(meta, ensure_ascii=False)),
                )
                try:  # 会话计数/活跃时间联动（表缺失只跳过不炸）
                    db.execute(
                        "UPDATE agent_sessions SET message_count=message_count+1,"
                        " last_activity_at=? WHERE id=?",
                        (self._now_fn() / 1000, sid))
                except sqlite3.OperationalError:
                    pass
                db.commit()
            finally:
                db.close()
            _STATS["alerts_written"] += 1
        except Exception as e:
            _STATS["write_errors"] += 1
            logger.warning("[monitor] 告警落库失败（不阻塞检查）: %s", e)

    def _process(self, target: dict, result: CheckResult,
                 now_ms: int, emit: bool) -> dict | None:
        """单目标检查后处理：状态更新 + 告警判定（防抖/续报/恢复）+ 落库。

        返回告警事件dict（无告警=None）。
        """
        state = self._update_state(target, result, now_ms)
        event_kind = None
        threshold = int(target.get("fail_threshold") or _DEFAULT_FAIL_THRESHOLD)
        if result.status == "fail":
            streak = state["fail_streak"]
            if streak == threshold or (
                    state["prev_status"] != "fail" and streak >= threshold):
                # 失败连击首次跨过阈值：告警（防单次抖动误报）
                event_kind = "alert"
            elif streak > threshold and streak % _REMIND_EVERY == 0:
                event_kind = "remind"  # 持续故障续报（不失联）
        elif state["prev_status"] == "fail" and \
                state["prev_fail_streak"] >= threshold:
            # 故障→恢复迁移才说话，且只恢复"告过警"的故障（未达阈值的
            # 抖动恢复零通知——与防抖对称）
            event_kind = "recovery"
        if event_kind is None:
            return None
        text = self._build_alert(target, event_kind, result, state, now_ms)
        if emit:
            self._write_alert(target, text)
        self._note(text.splitlines()[0] if text else event_kind)
        return {
            "event_kind": event_kind,
            "target_id": target.get("id"),
            "name": target.get("name", ""),
            "status": result.status,
            "text": text,
        }

    def run_due(self, now_ms: int | None = None) -> list[dict]:
        """周期检查入口：到点（interval_s已过）的启用目标全查一轮。

        返回本轮产生的告警事件（检查结果已各自落monitor_events）。
        单目标异常不炸整轮。
        """
        now = int(now_ms if now_ms is not None else self._now_fn())
        events: list[dict] = []
        for target in self._due_targets(now, None, respect_interval=True):
            try:
                result = self.run_check(target)
                ev = self._process(target, result, now, emit=True)
                if ev:
                    events.append(ev)
            except Exception as e:
                logger.warning("[monitor] 目标#%s 检查轮异常（循环继续）: %s",
                               target.get("id"), e)
        return events

    def check_now(self, target_id: int | None = None) -> tuple[list[dict], list[dict]]:
        """立即检查（monitor_check 工具入口）：不等interval，检查单个/全部启用目标。

        返回 (逐目标检查结果, 产生的告警事件)。告警同样落库。
        """
        ids = [int(target_id)] if target_id else None
        now = self._now_fn()
        results: list[dict] = []
        events: list[dict] = []
        for target in self._due_targets(now, ids, respect_interval=False):
            result = self.run_check(target)
            results.append({
                "target_id": target["id"], "name": target["name"],
                "kind": target["kind"], "target": target["target"],
                "status": result.status, "latency_ms": result.latency_ms,
                "detail": result.detail,
            })
            ev = self._process(target, result, now, emit=True)
            if ev:
                events.append(ev)
        return results, events

    def recent_events(self, target_id: int | None = None, limit: int = 10) -> list[dict]:
        db = self._connect()
        try:
            if target_id:
                rows = db.execute(
                    "SELECT * FROM monitor_events WHERE target_id=?"
                    " ORDER BY id DESC LIMIT ?", (int(target_id), int(limit)),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM monitor_events ORDER BY id DESC LIMIT ?",
                    (int(limit),)).fetchall()
            return [dict(r) for r in rows]
        finally:
            db.close()

    def _note(self, msg: str) -> None:
        self._recent.append(f"{datetime.now().strftime('%H:%M:%S')} {msg}")
        del self._recent[:-50]
        logger.info("[monitor] %s", msg)

    def snapshot(self) -> dict:
        return {"stats": monitor_stats(), "recent": list(self._recent),
                "running": self._running, "db_path": self._db_path}

    # ── 后台循环（对齐 SoulMateScheduler 惯例） ─────────────────

    async def run_forever(self) -> None:
        self._running = True
        try:
            while self._running:
                try:
                    self.run_due()
                except Exception as e:  # 单轮异常不退出循环
                    logger.warning("[monitor] run_due 异常（循环继续）: %s", e)
                await asyncio.sleep(self._poll_interval_s)
        finally:
            self._running = False

    def ensure_started(self) -> bool:
        """拉起后台监控循环（幂等；无运行中事件循环时不拉起，返回False）。"""
        if self._running or self._task is not None:
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._task = loop.create_task(self.run_forever())
        logger.info("[monitor] 监控循环已启动（poll=%ss, db=%s）",
                    self._poll_interval_s, self._db_path)
        return True

    def has_enabled_targets(self) -> bool:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT COUNT(*) FROM monitor_targets WHERE enabled=1").fetchone()
            return bool(row and int(row[0]) > 0)
        finally:
            db.close()

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None


# ── 单例（get_scheduler 同款惯例） ──────────────────────────────

_monitor: HealthMonitor | None = None


def get_health_monitor() -> HealthMonitor:
    global _monitor
    if _monitor is None:
        _monitor = HealthMonitor()
    return _monitor
