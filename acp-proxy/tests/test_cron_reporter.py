# -*- coding: utf-8 -*-
"""CronReporter — 路线图 3.2 定时汇报（主动向 OpenMate 推送任务进展/异常）。

缺口：3.1 cron引擎只把执行结果落 runs 日志（进程内 _note），用户/前端看不见；
"cron回合turn_origin=cron已落库但wakeup_relay只推wakeup标记行，实时推送归3.2"。

本面（测试即规格）：
1. 汇报文本确定性拼装（任务名/✅❌/结果或异常/执行与下次时间/连续失败提醒），
   不走LLM；
2. 汇报行落 opensoul.db agent_messages（role=assistant, metadata.turn_origin=
   cron_report）——即使定时回合零输出失败，异常也有可见产物；
3. wakeup_relay 把 cron/cron_report 出身行补推给正连前端（任务进展/异常即时
   可见）；普通回合行依旧零推送（不与流式重复——回归面）；
4. 汇报失败/无会话绝不阻塞调度主链路（fire/日志/排下次优先级更高）。
"""

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.cron_reporter import (
    CRON_REPORT_TURN_ORIGIN,
    CronReporter,
    build_run_report,
    report_stats,
)
from agent.soulmate_scheduler import CRON_TURN_ORIGIN, SoulMateScheduler
from agent.wakeup_executor import WAKEUP_TURN_ORIGIN
from agent.wakeup_relay import (
    RELAYED_TURN_ORIGINS,
    WakeupRelay,
    fetch_rows,
    is_relayed_meta,
    turn_origin_of,
)

SID = "om-cron-report-test"
NOW_MS = 1770000000000  # 固定时钟：汇报时间字段确定性可断言
DUE_MS = NOW_MS + 120000  # 越过首跑 next_run 的执行时刻（claim 到点判定用）


def run(coro):
    return asyncio.run(coro)


def _make_msg_db():
    """临时agent_messages库（真实schema子集：含metadata列+会话计数联动表）"""
    db_path = tempfile.mktemp(suffix=".db")
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE agent_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,"
        " timestamp REAL, attachments TEXT, parent_message_id INTEGER, metadata TEXT)"
    )
    db.execute(
        "CREATE TABLE agent_sessions (id TEXT PRIMARY KEY,"
        " message_count INTEGER DEFAULT 0, last_activity_at REAL)"
    )
    db.execute("INSERT INTO agent_sessions(id) VALUES(?)", (SID,))
    db.commit()
    db.close()
    return db_path


def _rows(db_path, sid=SID):
    db = sqlite3.connect(db_path)
    try:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            "SELECT * FROM agent_messages WHERE session_id=? ORDER BY id", (sid,))]
    finally:
        db.close()


def _task(**kw):
    t = {"id": 7, "name": "每日备份", "payload": "备份配置", "session_id": SID,
         "schedule_expr": "cron:0 8 * * *", "next_run_at_ms": NOW_MS + 60000,
         "fail_streak": 0}
    t.update(kw)
    return t


# ── ① 汇报文本：确定性拼装（测试即规格） ─────────────────────────


class TestBuildRunReport:
    def test_ok_report_shape(self):
        text = build_run_report(_task(), "ok", "end_turn",
                                next_run_ms=NOW_MS + 60000, now_ms=NOW_MS)
        assert text.startswith("📊 [定时汇报] 任务#7 每日备份｜✅ 成功")
        assert "结果: end_turn" in text
        assert "执行: " in text and "（下次 " in text
        assert "异常" not in text

    def test_failed_report_shape(self):
        text = build_run_report(_task(), "failed", "RuntimeError: 零输出",
                                next_run_ms=NOW_MS + 60000, now_ms=NOW_MS)
        assert "｜❌ 失败" in text
        assert "异常: RuntimeError: 零输出" in text
        assert "连续失败" not in text  # streak<3 不提醒

    def test_fail_streak_hint_at_threshold(self):
        text = build_run_report(_task(), "failed", "boom",
                                fail_streak=3, now_ms=NOW_MS)
        assert "（连续失败3次，请关注）" in text
        text2 = build_run_report(_task(), "ok", "end_turn",
                                 fail_streak=3, now_ms=NOW_MS)
        assert "连续失败" not in text2  # 成功态不带失败提醒

    def test_empty_detail_fallback(self):
        assert "（无详情）" in build_run_report(_task(), "ok", "", now_ms=NOW_MS)

    def test_detail_truncated(self):
        text = build_run_report(_task(), "failed", "x" * 1000, now_ms=NOW_MS)
        assert len(text) < 500


# ── ② 汇报落库：turn_origin=cron_report + 会话计数联动 ────────────


class TestWriteReport:
    def test_write_persists_report_row(self):
        db_path = _make_msg_db()
        rep = CronReporter(db_path=db_path, now_fn=lambda: NOW_MS)
        row_id = rep.write_report(SID, "📊 汇报", task_id=7, status="ok")
        rows = _rows(db_path)
        assert len(rows) == 1 and rows[0]["id"] == row_id
        assert rows[0]["role"] == "assistant"
        assert rows[0]["content"] == "📊 汇报"
        meta = json.loads(rows[0]["metadata"])
        assert meta["turn_origin"] == CRON_REPORT_TURN_ORIGIN
        assert meta["cron_task_id"] == 7 and meta["status"] == "ok"
        # 会话计数/活跃时间联动
        db = sqlite3.connect(db_path)
        cnt = db.execute("SELECT message_count FROM agent_sessions WHERE id=?",
                         (SID,)).fetchone()[0]
        db.close()
        assert cnt == 1

    def test_report_writes_and_returns_text(self):
        db_path = _make_msg_db()
        rep = CronReporter(db_path=db_path, now_fn=lambda: NOW_MS)
        text = rep.report(_task(), "ok", "end_turn")
        assert text.startswith("📊 [定时汇报]")
        rows = _rows(db_path)
        assert len(rows) == 1 and rows[0]["content"] == text

    def test_no_session_skips_write(self):
        db_path = _make_msg_db()
        rep = CronReporter(db_path=db_path, now_fn=lambda: NOW_MS)
        before = report_stats()["skipped_no_session"]
        text = rep.report(_task(session_id=""), "ok", "end_turn")
        assert "📊" in text  # 文本仍生成（进程内 _note 可用）
        assert _rows(db_path) == []
        assert report_stats()["skipped_no_session"] == before + 1

    def test_write_error_never_raises(self):
        rep = CronReporter(db_path="/nonexistent-dir/xx/db.sqlite")
        before = report_stats()["write_errors"]
        text = rep.report(_task(), "failed", "boom")  # 绝不抛
        assert "❌ 失败" in text
        assert report_stats()["write_errors"] == before + 1


# ── ③ relay推送面：cron/cron_report 行补推，普通回合行零推送 ──────


class TestRelayPushesCronRows:
    def _seed(self, db_path, rows):
        db = sqlite3.connect(db_path)
        for role, content, origin in rows:
            meta = json.dumps({"turn_origin": origin}) if origin else None
            db.execute(
                "INSERT INTO agent_messages(session_id, role, content, timestamp,"
                " metadata) VALUES(?,?,?,?,?)", (SID, role, content, 1.0, meta))
        db.commit()
        db.close()

    def test_origin_classification(self):
        assert turn_origin_of(json.dumps({"turn_origin": "cron"})) == "cron"
        assert is_relayed_meta(json.dumps({"turn_origin": CRON_TURN_ORIGIN}))
        assert is_relayed_meta(json.dumps({"turn_origin": CRON_REPORT_TURN_ORIGIN}))
        assert is_relayed_meta(json.dumps({"turn_origin": WAKEUP_TURN_ORIGIN}))
        assert not is_relayed_meta(None)
        assert not is_relayed_meta(json.dumps({"turn_origin": "normal"}))
        assert RELAYED_TURN_ORIGINS == {
            WAKEUP_TURN_ORIGIN, CRON_TURN_ORIGIN, CRON_REPORT_TURN_ORIGIN}

    def test_poll_once_pushes_cron_and_report_rows_only(self):
        db_path = _make_msg_db()
        self._seed(db_path, [
            ("user", "普通用户消息", None),              # 不推
            ("assistant", "普通回复", None),             # 不推
            ("user", "⏰ [定时任务 触发行", CRON_TURN_ORIGIN),   # 推
            ("assistant", "定时输出", CRON_TURN_ORIGIN),        # 推
            ("assistant", "📊 [定时汇报] 任务#7", CRON_REPORT_TURN_ORIGIN),  # 推
        ])
        pushed = []

        async def send(payload):
            pushed.append(payload)

        relay = WakeupRelay(lambda: SID, send, db_path=db_path)
        n = run(relay.poll_once(SID))
        assert n == 3
        texts = [p["params"]["update"]["content"]["text"] for p in pushed]
        assert texts == ["⏰ [定时任务 触发行", "定时输出", "📊 [定时汇报] 任务#7"]
        # 角色保真：user出身行=user_message，assistant出身行=agent_message
        kinds = [p["params"]["update"]["sessionUpdate"] for p in pushed]
        assert kinds == ["user_message", "agent_message", "agent_message"]

    def test_fetch_rows_flags_both_keys(self):
        db_path = _make_msg_db()
        self._seed(db_path, [
            ("assistant", "定时输出", CRON_TURN_ORIGIN),
            ("assistant", "普通回复", None),
        ])
        rows = fetch_rows(db_path, SID, 0)
        assert rows[0]["is_wakeup"] is False and rows[0]["is_relayed"] is True
        assert rows[1]["is_wakeup"] is False and rows[1]["is_relayed"] is False


# ── ④ 调度器接线：run_due 后汇报行落地；汇报失败不阻塞调度 ────────


class TestSchedulerWiring:
    def _sched(self, fire_fn, report_db):
        s = SoulMateScheduler(
            db_path=tempfile.mktemp(suffix=".db"), fire_fn=fire_fn,
            poll_interval_s=0.01, now_fn=lambda: NOW_MS)
        s._reporter = CronReporter(db_path=report_db, now_fn=lambda: NOW_MS)
        return s

    def test_run_due_reports_ok(self):
        report_db = _make_msg_db()

        def fire(task):
            return {"stop_reason": "end_turn"}

        s = self._sched(fire, report_db)
        s.add_task("每日备份", "interval:60", "备份配置", session_id=SID)
        n = run(s.run_due(now_ms=DUE_MS))
        assert n == 1
        rows = _rows(report_db)
        assert len(rows) == 1
        assert "任务#1 每日备份｜✅ 成功" in rows[0]["content"]
        assert json.loads(rows[0]["metadata"])["turn_origin"] == CRON_REPORT_TURN_ORIGIN

    def test_run_due_reports_failure_with_streak(self):
        report_db = _make_msg_db()

        def fire(task):
            raise RuntimeError("provider 500")

        s = self._sched(fire, report_db)
        s.add_task("巡检", "interval:60", "检查服务", session_id=SID)
        run(s.run_due(now_ms=DUE_MS))
        run(s.run_due(now_ms=DUE_MS + 60000))  # 第二次执行 → fail_streak=2
        rows = _rows(report_db)
        assert len(rows) == 2
        assert "｜❌ 失败" in rows[1]["content"]
        assert "异常: RuntimeError: provider 500" in rows[1]["content"]
        # runs 日志同时落 failed（汇报不替代执行日志）
        db = sqlite3.connect(s._db_path)
        statuses = [r[0] for r in db.execute("SELECT status FROM runs ORDER BY id")]
        db.close()
        assert statuses == ["failed", "failed"]

    def test_reporter_error_does_not_block_scheduler(self):
        report_db = "/nonexistent-dir/xx/db.sqlite"

        def fire(task):
            return {"stop_reason": "end_turn"}

        s = self._sched(fire, report_db)
        s.add_task("备份", "interval:60", "备份", session_id=SID)
        n = run(s.run_due(now_ms=DUE_MS))  # 汇报落库炸了也照常执行+落日志+返回
        assert n == 1
        db = sqlite3.connect(s._db_path)
        cnt = db.execute("SELECT COUNT(*) FROM runs WHERE status='ok'").fetchone()[0]
        db.close()
        assert cnt == 1

    def test_no_session_task_still_runs(self):
        report_db = _make_msg_db()

        def fire(task):
            return {"stop_reason": "end_turn"}

        s = self._sched(fire, report_db)
        s.add_task("无会话任务", "interval:60", "内容", session_id="")
        assert run(s.run_due(now_ms=DUE_MS)) == 1
        assert _rows(report_db) == []  # 无处可投→不写野行
