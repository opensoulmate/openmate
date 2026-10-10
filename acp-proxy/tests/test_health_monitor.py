"""路线图 3.3 监控告警（agent/health_monitor.py）测试。

覆盖面：目标CRUD+校验 / 三类checker（fake注入+真实socket/HTTP）/ 告警防抖阈值 /
持续续报 / 恢复通知 / 告警落库(turn_origin=health_alert) / relay推送面 /
run_due到点判定 / 后台循环自举 / soulmate_agent四点接线护栏 + 真实dispatch烟测。
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.health_monitor import (  # noqa: E402
    HEALTH_ALERT_TURN_ORIGIN,
    CheckResult,
    HealthMonitor,
    check_port,
    check_website,
    monitor_stats,
)


# ── fixture：临时monitor库 + 临时opensoul消息库 ──────────────────

def _make_report_db(path: str) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS agent_messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL,
            metadata TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_sessions(
            id TEXT PRIMARY KEY,
            message_count INTEGER DEFAULT 0,
            last_activity_at REAL
        );
        """
    )
    db.commit()
    db.close()


@pytest.fixture
def dbs(tmp_path):
    mon_db = str(tmp_path / "monitor.db")
    rep_db = str(tmp_path / "opensoul.db")
    _make_report_db(rep_db)
    return mon_db, rep_db


def _monitor(dbs, checkers=None, now_fn=None) -> HealthMonitor:
    return HealthMonitor(
        db_path=dbs[0], report_db_path=dbs[1],
        checkers=checkers, now_fn=now_fn)


class FakeClock:
    def __init__(self, start=1_700_000_000_000):
        self.t = start

    def __call__(self) -> int:
        return self.t

    def advance(self, ms: int) -> None:
        self.t += ms


# ── 目标 CRUD + 校验 ────────────────────────────────────────────

def test_add_target_valid_and_persist(dbs):
    m = _monitor(dbs)
    r = m.add_target("官网", "website", "https://example.com",
                     interval_s=60, fail_threshold=3, session_id="s1")
    assert r["target_id"] >= 1 and r["interval_s"] == 60
    targets = m.list_targets()
    assert len(targets) == 1
    assert targets[0]["name"] == "官网" and targets[0]["kind"] == "website"
    # 重启（新实例同库）不丢
    m2 = _monitor(dbs)
    assert len(m2.list_targets()) == 1


@pytest.mark.parametrize("kwargs,msg", [
    (dict(name="", kind="website", target="https://x.com"), "name"),
    (dict(name="a", kind="ftp", target="https://x.com"), "kind"),
    (dict(name="a", kind="website", target=""), "target"),
    (dict(name="a", kind="website", target="example.com"), "http"),
    (dict(name="a", kind="port", target="bad"), "host:port"),
    (dict(name="a", kind="port", target="localhost:99999"), "host:port"),
    (dict(name="a", kind="service", target="x", interval_s=5), "30"),
    (dict(name="a", kind="service", target="x", fail_threshold=0), "fail_threshold"),
])
def test_add_target_validation(dbs, kwargs, msg):
    m = _monitor(dbs)
    with pytest.raises(ValueError) as ei:
        m.add_target(**kwargs)
    assert msg in str(ei.value)


def test_remove_disable_then_delete(dbs):
    m = _monitor(dbs)
    tid = m.add_target("svc", "service", "acp-proxy-a")["target_id"]
    ok, msg = m.remove_target(tid)
    assert ok and "停用" in msg
    assert m.list_targets()[0]["enabled"] == 0
    assert not m.has_enabled_targets()
    ok, msg = m.remove_target(tid)  # 幂等
    assert ok
    ok, msg = m.remove_target(tid, delete=True)
    assert ok and "删除" in msg
    assert m.list_targets() == []
    ok, msg = m.remove_target(tid)
    assert not ok and "不存在" in msg


# ── checker：fake注入 + 真实网络/HTTP ───────────────────────────

def test_run_check_unknown_kind_and_checker_exception(dbs):
    def boom(t):
        raise RuntimeError("x")

    m = _monitor(dbs, checkers={"boom": boom})
    r = m.run_check({"kind": "nope", "target": "t"})
    assert r.status == "fail" and "未知监控类型" in r.detail
    r = m.run_check({"kind": "boom", "target": "t"})
    assert r.status == "fail" and "检查异常" in r.detail


def test_check_port_real_socket():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        r = check_port(f"127.0.0.1:{port}")
        assert r.status == "ok" and "监听中" in r.detail
    finally:
        srv.close()
    r = check_port(f"127.0.0.1:{port}")  # 关闭后连接拒绝
    assert r.status == "fail"


def test_check_website_real_http():
    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):  # 静音
            pass

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.handle_request, daemon=True)
    t.start()
    try:
        r = check_website(f"http://127.0.0.1:{port}/")
        assert r.status == "ok" and "HTTP 200" in r.detail
    finally:
        httpd.server_close()
    r = check_website("http://127.0.0.1:1/")  # 拒绝连接
    assert r.status == "fail"


# ── 告警防抖/续报/恢复 ─────────────────────────────────────────

def _seq(m, target, results, t0=1_700_000_000_000):
    """按序跑一轮check_now（每结果间隔1ms时钟推进），返回全部events。"""
    evs = []
    now = t0
    for r in results:
        ev = m._process(target, r, now, emit=False)
        if ev:
            evs.append(ev)
        now += 1
    return evs


def test_alert_threshold_antiflap(dbs):
    m = _monitor(dbs)
    target = {"id": 1, "name": "官网", "kind": "website",
              "target": "https://x", "fail_threshold": 2, "session_id": ""}
    ok = CheckResult("ok", 5, "HTTP 200")
    fail = CheckResult("fail", 5, "timeout")
    # 单次抖动：fail+ok → 零告警（防抖核心）
    evs = _seq(m, target, [fail, ok])
    assert evs == []
    # 连续失败达到阈值2 → 恰好1条alert
    evs = _seq(m, target, [fail, fail])
    assert [e["event_kind"] for e in evs] == ["alert"]
    # 继续失败但未到续报点 → 无新告警
    evs = _seq(m, target, [fail, fail])
    assert evs == []


def test_alert_remind_and_recovery(dbs):
    m = _monitor(dbs)
    target = {"id": 1, "name": "svc", "kind": "service",
              "target": "x", "fail_threshold": 2, "session_id": ""}
    fail = CheckResult("fail", 5, "inactive")
    ok = CheckResult("ok", 5, "active")
    evs = _seq(m, target, [fail] * 10)  # 2=alert, 10=remind(10%10==0)
    assert [e["event_kind"] for e in evs] == ["alert", "remind"]
    evs = _seq(m, target, [ok])
    assert [e["event_kind"] for e in evs] == ["recovery"]
    assert "累计失败10次" in evs[0]["text"]
    # 恢复后保持ok → 零打扰
    assert _seq(m, target, [ok, ok]) == []


def test_alert_line_shape(dbs):
    m = _monitor(dbs)
    target = {"id": 7, "name": "官网", "kind": "website",
              "target": "https://x", "fail_threshold": 1, "session_id": ""}
    ev = m._process(target, CheckResult("fail", 5, "HTTP 502"),
                    1_700_000_000_000, emit=False)
    assert ev["event_kind"] == "alert"
    text = ev["text"]
    assert text.startswith("🚨 [监控告警] 「官网」(website: https://x)｜❌ 异常")
    assert "HTTP 502" in text and "连续失败1次（阈值1次）" in text


# ── 告警落库 + relay推送面 ──────────────────────────────────────

def test_alert_written_to_report_db(dbs):
    clock = FakeClock()
    m = _monitor(dbs, now_fn=clock)
    target = {"id": 3, "name": "官网", "kind": "website",
              "target": "https://x", "fail_threshold": 1, "session_id": "sess-1"}
    m._process(target, CheckResult("fail", 5, "HTTP 502"), clock(), emit=True)
    db = sqlite3.connect(dbs[1])
    rows = db.execute(
        "SELECT session_id, role, content, metadata FROM agent_messages").fetchall()
    db.close()
    assert len(rows) == 1
    sid, role, content, meta = rows[0]
    assert sid == "sess-1" and role == "assistant"
    assert "🚨 [监控告警]" in content
    meta_d = json.loads(meta)
    assert meta_d["turn_origin"] == HEALTH_ALERT_TURN_ORIGIN
    assert meta_d["monitor_target_id"] == 3


def test_alert_no_session_skipped_and_write_error_no_raise(dbs):
    clock = FakeClock()
    m = _monitor(dbs, now_fn=clock)
    target = {"id": 3, "name": "t", "kind": "website",
              "target": "https://x", "fail_threshold": 1, "session_id": ""}
    before = monitor_stats()["skipped_no_session"]
    m._process(target, CheckResult("fail", 1, "x"), clock(), emit=True)
    assert monitor_stats()["skipped_no_session"] == before + 1
    # 落库失败不抛：report db路径坏掉也只计数、告警事件照常产出
    m2 = HealthMonitor(db_path=dbs[0], report_db_path="/nonexistent-dir/x.db")
    t2 = m2.add_target("t2", "website", "https://y",
                       fail_threshold=1, session_id="s")
    ev = m2._process(t2, CheckResult("fail", 1, "x"), clock(), emit=True)
    assert ev is not None and ev["event_kind"] == "alert"
    assert monitor_stats()["write_errors"] >= 1


def test_relay_pushes_health_alert():
    from agent.wakeup_relay import RELAYED_TURN_ORIGINS, is_relayed_meta

    assert HEALTH_ALERT_TURN_ORIGIN in RELAYED_TURN_ORIGINS
    meta = json.dumps({"turn_origin": HEALTH_ALERT_TURN_ORIGIN})
    assert is_relayed_meta(meta) is True
    assert is_relayed_meta(json.dumps({"turn_origin": "normal"})) is False


# ── run_due 到点判定 / check_now ────────────────────────────────

def test_run_due_interval_and_disabled(dbs):
    clock = FakeClock()
    calls = []

    def fake(t):
        calls.append(t)
        return CheckResult("ok", 1, "fine")

    m = _monitor(dbs, checkers={"website": fake}, now_fn=clock)
    m.add_target("a", "website", "https://x", interval_s=60)
    m.add_target("b", "website", "https://y", interval_s=60)
    m.remove_target(2)  # b 停用
    assert len(m.run_due()) == 0
    assert calls == ["https://x"]          # 只查启用目标
    calls.clear()
    m.run_due()                             # 60秒未到 → 不查
    assert calls == []
    clock.advance(60_000)
    m.run_due()
    assert calls == ["https://x"]          # 到点再查


def test_check_now_single_and_all(dbs):
    m = _monitor(dbs, checkers={
        "website": lambda t: CheckResult("ok", 3, f"HTTP 200 {t}"),
        "port": lambda t: CheckResult("fail", 3, "refused"),
    })
    m.add_target("a", "website", "https://x", fail_threshold=1)
    m.add_target("b", "port", "localhost:9", fail_threshold=1)
    results, events = m.check_now()
    assert len(results) == 2
    assert {r["status"] for r in results} == {"ok", "fail"}
    assert [e["event_kind"] for e in events] == ["alert"]  # b失败达阈值
    results, _ = m.check_now(1)  # 只查a
    assert len(results) == 1 and results[0]["target_id"] == 1


def test_check_now_error_does_not_break_round(dbs):
    def boom(t):
        raise RuntimeError("boom")

    m = _monitor(dbs, checkers={"website": boom})
    m.add_target("a", "website", "https://x")
    m.add_target("b", "website", "https://y")
    results, _ = m.check_now()
    assert len(results) == 2 and all(r["status"] == "fail" for r in results)


def test_ensure_started_and_stop(dbs):
    import asyncio

    m = _monitor(dbs)

    async def run():
        assert m.ensure_started() is True
        assert m.ensure_started() is True  # 幂等
        await asyncio.sleep(0.05)
        m.stop()
        return True

    assert asyncio.run(run()) is True
    assert m.has_enabled_targets() is False


def test_recent_events_log(dbs):
    m = _monitor(dbs, checkers={"website": lambda t: CheckResult("ok", 1, "fine")})
    m.add_target("a", "website", "https://x")
    m.check_now()
    evs = m.recent_events()
    assert len(evs) == 1 and evs[0]["kind"] == "check" and evs[0]["status"] == "ok"


# ── soulmate_agent 接线护栏 + 真实dispatch烟测 ──────────────────

def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 四点接线：主循环dispatch / code-mode批内dispatch / 工具定义 / 系统提示
    assert src.count('func_name in ("monitor_add", "monitor_list",'
                     ' "monitor_remove", "monitor_check")') >= 2
    assert '"name": "monitor_add"' in src
    assert '"name": "monitor_check"' in src
    assert "monitor_add / monitor_list / monitor_check / monitor_remove" in src
    # 分发helper存在
    assert "def _monitor_dispatch" in src
    # start.py 自举
    start_src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/start.py").read_text(encoding="utf-8")
    assert "has_enabled_targets" in start_src


def test_monitor_dispatch_smoke(dbs, monkeypatch):
    """真实dispatch烟测（__new__构造+lazy-init路径）：add→list→check→remove。"""
    import agent.soulmate_agent as sa

    m = _monitor(dbs, checkers={"website": lambda t: CheckResult("ok", 2, "HTTP 200")})
    monkeypatch.setattr(sa, "_get_health_monitor", lambda: m)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__（lazy-init路径）
    out = agent._monitor_dispatch("monitor_add", {
        "name": "官网", "kind": "website", "target": "https://x",
        "interval_s": 60, "fail_threshold": 1}, session_id="s1")
    assert "✅ 监控目标已创建" in out and "https://x" in out
    out = agent._monitor_dispatch("monitor_list", {})
    assert "📡 监控目标共1个" in out and "官网" in out
    out = agent._monitor_dispatch("monitor_check", {})
    assert "🔍 立即检查结果" in out and "✅" in out
    out = agent._monitor_dispatch("monitor_remove", {"target_id": 1})
    assert "✅" in out and "停用" in out
    out = agent._monitor_dispatch("monitor_remove", {"target_id": 0})
    assert out.startswith("错误") or out.startswith("❌")
    out = agent._monitor_dispatch("monitor_add", {"name": "x", "kind": "bad", "target": "t"})
    assert out.startswith("错误:")
