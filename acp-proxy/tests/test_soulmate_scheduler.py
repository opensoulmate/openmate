"""路线图 3.1 cron引擎——SoulMateScheduler 单测 + 接线护栏

覆盖：
1. CronExpr 解析/匹配/next_after（*/n、a-b、列表、dom/dow Vixie语义、7=周日）
2. parse_schedule：interval/cron 两形态 + 非法拒绝（含最小10秒）
3. add_task/list_tasks/cancel_task（停用 vs 删除，执行日志保留）
4. run_due：到点执行→runs落日志→自动排下一次（interval与cron）
5. fire 失败可见：failed落日志+fail_streak累计+周期任务照常排下一次
6. at-most-once：双调度器同库 CAS 领取不双跑
7. 停用任务不执行；错过不补跑（next_run推进到下个周期）
8. 周期表损坏→任务停用不炸轮
9. build_cron_prompt 自动回合声明
10. 接线护栏：soulmate_agent 工具面/主dispatch/code-mode dispatch/系统提示
"""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.soulmate_scheduler import (  # noqa: E402
    CronError,
    CronExpr,
    SoulMateScheduler,
    build_cron_prompt,
    parse_schedule,
)


def _sched(tmp_path, fire_fn=None, now_ms=1_000_000):
    clock = {"now": now_ms}
    s = SoulMateScheduler(
        db_path=str(tmp_path / "cron.db"),
        fire_fn=fire_fn or (lambda t: {"stop_reason": "end_turn"}),
        poll_interval_s=0.01,
        now_fn=lambda: clock["now"],
    )
    s._clock = clock  # 供测试推进时间
    return s


def _run(coro):
    return asyncio.run(coro)


# ── 1. CronExpr ────────────────────────────────────────────────

def test_cron_next_after_star_step():
    e = CronExpr("*/15 * * * *")
    assert e.next_after(datetime(2026, 10, 10, 8, 7)) == datetime(2026, 10, 10, 8, 15)
    assert e.next_after(datetime(2026, 10, 10, 8, 15)) == datetime(2026, 10, 10, 8, 30)


def test_cron_next_after_daily_and_weekday():
    assert CronExpr("0 3 * * *").next_after(
        datetime(2026, 10, 10, 8, 0)) == datetime(2026, 10, 11, 3, 0)
    # 2026-10-10 是周六 → 周一8点
    assert CronExpr("0 8 * * 1-5").next_after(
        datetime(2026, 10, 10, 9, 0)) == datetime(2026, 10, 12, 8, 0)


def test_cron_dow_seven_equals_sunday():
    e7 = CronExpr("0 8 * * 7")
    e0 = CronExpr("0 8 * * 0")
    t = datetime(2026, 10, 10, 9, 0)  # 周六
    assert e7.next_after(t) == e0.next_after(t) == datetime(2026, 10, 11, 8, 0)


def test_cron_dom_dow_vixie_or_semantics():
    # 日=1 且 周一 → 任一命中（Vixie）：10月12日既是12号(不命中1)又是周一(命中)
    e = CronExpr("0 0 1 * 1")
    nxt = e.next_after(datetime(2026, 10, 10, 0, 0))
    assert nxt == datetime(2026, 10, 12, 0, 0)  # 周一命中


def test_cron_list_and_range_fields():
    e = CronExpr("0,30 9-10 * * *")
    assert e.next_after(datetime(2026, 10, 10, 9, 0)) == datetime(2026, 10, 10, 9, 30)
    assert e.next_after(datetime(2026, 10, 10, 9, 31)) == datetime(2026, 10, 10, 10, 0)


def test_cron_invalid_expressions_rejected():
    for bad in ("* * * *", "99 * * * *", "*/0 * * * *", "a * * * *", "1-99 * * * *"):
        with pytest.raises(CronError):
            CronExpr(bad)


def test_cron_month_jump():
    # 2月30日永不命中→2年内报错；每月30号在2月跳过到3月
    e = CronExpr("0 0 30 2 *")
    with pytest.raises(CronError):
        e.next_after(datetime(2026, 1, 1))
    e2 = CronExpr("0 0 30 * *")
    assert e2.next_after(datetime(2026, 2, 1)) == datetime(2026, 3, 30)


# ── 2. parse_schedule ──────────────────────────────────────────

def test_parse_schedule_interval_and_cron():
    assert parse_schedule("interval:300") == ("interval", 300)
    assert parse_schedule("interval:5m") == ("interval", 300)
    assert parse_schedule("interval:1h") == ("interval", 3600)
    kind, expr = parse_schedule("cron:*/5 * * * *")
    assert kind == "cron" and expr.expr == "*/5 * * * *"


def test_parse_schedule_rejects_garbage_and_tiny_interval():
    with pytest.raises(CronError):
        parse_schedule("every day")
    with pytest.raises(CronError):
        parse_schedule("interval:5")  # <10秒


# ── 3. 任务管理 ─────────────────────────────────────────────────

def test_add_list_cancel(tmp_path):
    s = _sched(tmp_path)
    r = s.add_task("日报", "cron:0 9 * * *", "生成日报", session_id="om-x")
    assert r["task_id"] == 1 and r["schedule"] == "cron:0 9 * * *"
    tasks = s.list_tasks()
    assert len(tasks) == 1 and tasks[0]["name"] == "日报"
    assert tasks[0]["session_id"] == "om-x" and tasks[0]["enabled"] == 1
    ok, msg = s.cancel_task(1)
    assert ok and "停用" in msg
    assert s.list_tasks()[0]["enabled"] == 0
    ok, msg = s.cancel_task(1, delete=True)
    assert ok and "删除" in msg
    assert s.list_tasks() == []
    ok, msg = s.cancel_task(999)
    assert not ok and "不存在" in msg


def test_add_task_validation(tmp_path):
    s = _sched(tmp_path)
    with pytest.raises(CronError):
        s.add_task("", "interval:60", "x")
    with pytest.raises(CronError):
        s.add_task("t", "interval:60", "  ")


# ── 4-5. run_due 执行/日志/排下一次/失败可见 ─────────────────────

def test_run_due_interval_fires_logs_and_reschedules(tmp_path):
    fired = []
    s = _sched(tmp_path, fire_fn=lambda t: fired.append(t["id"]) or {"stop_reason": "end_turn"})
    s.add_task("t1", "interval:60", "做点什么")
    s._clock["now"] += 60_000  # 到点
    assert _run(s.run_due()) == 1
    assert fired == [1]
    runs = s.list_tasks()[0]["recent_runs"]
    assert runs[0]["status"] == "ok" and runs[0]["detail"] == "end_turn"
    t = s.list_tasks()[0]
    assert t["run_count"] == 1 and t["fail_streak"] == 0
    assert t["next_run_at_ms"] == 1_000_000 + 120_000  # now(1.06M)+60s
    # 未到点不再执行
    assert _run(s.run_due()) == 0


def test_run_due_cron_reschedules_by_expression(tmp_path):
    s = _sched(tmp_path, now_ms=int(datetime(2026, 10, 10, 8, 7).timestamp() * 1000))
    s.add_task("t", "cron:*/15 * * * *", "x")
    assert _run(s.run_due()) == 0  # 08:07创建，下次08:15，未到点
    s._clock["now"] = int(datetime(2026, 10, 10, 8, 16).timestamp() * 1000)
    assert _run(s.run_due()) == 1
    t = s.list_tasks()[0]
    nxt = datetime.fromtimestamp(t["next_run_at_ms"] / 1000)
    assert (nxt.hour, nxt.minute) == (8, 30)


def test_fire_failure_logged_and_keeps_schedule(tmp_path):
    def boom(t):
        raise RuntimeError("LLM 402 余额不足")
    s = _sched(tmp_path, fire_fn=boom)
    s.add_task("t", "interval:60", "x")
    s._clock["now"] += 60_000
    assert _run(s.run_due()) == 1
    t = s.list_tasks()[0]
    assert t["recent_runs"][0]["status"] == "failed"
    assert "402" in t["recent_runs"][0]["detail"]
    assert t["fail_streak"] == 1
    assert t["enabled"] == 1  # 周期任务失败不禁用，下个周期照跑
    s._clock["now"] += 60_000
    assert _run(s.run_due()) == 1  # 下个周期照常执行
    assert s.list_tasks()[0]["fail_streak"] == 2
    snap = s.snapshot()
    assert snap["stats"]["fire_failed"] == 2 and snap["stats"]["fire_ok"] == 0
    assert any("❌" in line for line in snap["recent"])


def test_async_fire_fn_supported(tmp_path):
    async def afire(t):
        await asyncio.sleep(0)
        return {"stop_reason": "end_turn"}
    s = _sched(tmp_path, fire_fn=afire)
    s.add_task("t", "interval:60", "x")
    s._clock["now"] += 60_000
    assert _run(s.run_due()) == 1
    assert s.list_tasks()[0]["recent_runs"][0]["status"] == "ok"


# ── 6. at-most-once CAS ────────────────────────────────────────

def test_claim_cas_no_double_fire_across_schedulers(tmp_path):
    fired = []
    db = str(tmp_path / "cron.db")
    clock = {"now": 1_000_000}
    s1 = SoulMateScheduler(db_path=db, poll_interval_s=0.01,
                           fire_fn=lambda t: fired.append(1), now_fn=lambda: clock["now"])
    s2 = SoulMateScheduler(db_path=db, poll_interval_s=0.01,
                           fire_fn=lambda t: fired.append(2), now_fn=lambda: clock["now"])
    s1.add_task("t", "interval:60", "x")
    clock["now"] += 60_000
    assert _run(s1.run_due()) == 1
    assert _run(s2.run_due()) == 0  # 第二个调度器领取失败（CAS输家）
    assert fired == [1]
    assert s1.snapshot()["stats"]["claimed"] == 1
    assert s2.snapshot()["stats"]["claim_lost"] == 0  # s2连due都没有（已被推进）


def test_claim_lost_when_cas_update_loses(tmp_path):
    # CAS输家路径确定性验证：SELECT看到due行、但UPDATE命中0行（另一进程已领取）
    class _FakeRes:
        def __init__(self, rows=None, rowcount=0):
            self._rows = rows or []
            self.rowcount = rowcount

        def fetchall(self):
            return self._rows

    class _FakeConn:
        def __init__(self, row):
            self._row = row

        def execute(self, sql, params=()):
            if sql.strip().startswith("SELECT"):
                return _FakeRes([self._row])
            return _FakeRes(rowcount=0)  # CAS UPDATE输家

        def commit(self):
            pass

        def close(self):
            pass

    clock = {"now": 1_000_000}
    s1 = SoulMateScheduler(db_path=str(tmp_path / "cron.db"),
                           fire_fn=lambda t: None, now_fn=lambda: clock["now"])
    s1.add_task("t", "interval:60", "x")
    clock["now"] += 60_000
    row = dict(s1.list_tasks()[0])  # 陈旧due行（另一进程视角）
    s2 = SoulMateScheduler(db_path=str(tmp_path / "cron2.db"),
                           fire_fn=lambda t: None, now_fn=lambda: clock["now"])
    s2._connect = lambda: _FakeConn(row)  # type: ignore[assignment]
    claimed = s2.claim_due()
    assert claimed == []  # CAS输家不执行
    assert s2.snapshot()["stats"]["claim_lost"] == 1


# ── 7. 停用/错过不补跑 ─────────────────────────────────────────

def test_disabled_task_not_fired_and_missed_runs_skipped(tmp_path):
    fired = []
    s = _sched(tmp_path, fire_fn=lambda t: fired.append(t["id"]))
    s.add_task("t", "interval:60", "x")
    s.cancel_task(1)
    s._clock["now"] += 600_000
    assert _run(s.run_due()) == 0
    assert fired == []
    # 错过不补跑：一个due任务只跑一次，next_run从now推进
    s2 = _sched(tmp_path / "d2", fire_fn=lambda t: fired.append("b"))
    s2.add_task("t", "interval:60", "x")
    s2._clock["now"] += 600_000  # 过了10个周期
    assert _run(s2.run_due()) == 1  # 只补跑1次，不积压10次
    nxt = s2.list_tasks()[0]["next_run_at_ms"]
    assert nxt == s2._clock["now"] + 60_000


# ── 8. 周期表损坏 → 停用不炸轮 ──────────────────────────────────

def test_corrupt_schedule_disables_task(tmp_path):
    s = _sched(tmp_path)
    s.add_task("t", "interval:60", "x")
    # 手工破坏周期表
    db = s._connect()
    db.execute("UPDATE tasks SET schedule_expr='interval:garbage' WHERE id=1")
    db.commit()
    db.close()
    s._clock["now"] += 60_000
    assert _run(s.run_due()) == 0  # 不执行
    t = s.list_tasks()[0]
    assert t["enabled"] == 0  # 已停用防刷屏
    assert any("无法解析" in line for line in s.snapshot()["recent"])


# ── 9. build_cron_prompt ───────────────────────────────────────

def test_build_cron_prompt_declares_auto_turn():
    text = build_cron_prompt({"name": "日报", "payload": "生成日报",
                              "schedule_expr": "cron:0 9 * * *", "run_count": 2})
    assert text.startswith("⏰ [定时任务 日报")
    assert "系统自动回合，非用户消息" in text
    assert "生成日报" in text and "第3次执行" in text


# ── 10. 接线护栏 ───────────────────────────────────────────────

def test_wiring_tools_registered_in_builtin_tools():
    src = Path(__file__).resolve().parents[1].joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    assert '"name": "schedule_task"' in src
    assert '"name": "schedule_list"' in src
    assert '"name": "schedule_cancel"' in src
    assert "builtin_tools.append(cron_tools" in src or "builtin_tools.extend(cron_tools" in src


def test_wiring_dispatch_in_main_loop_and_code_mode():
    src = Path(__file__).resolve().parents[1].joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    assert 'if func_name in ("schedule_task", "schedule_list", "schedule_cancel"):' in src
    assert src.count("self._cron_dispatch(") >= 2  # 主循环 + code-mode 内层
    assert "def _cron_dispatch" in src  # 共用入口


def test_wiring_system_prompt_lists_cron_tools():
    src = Path(__file__).resolve().parents[1].joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    assert "schedule_task / schedule_list / schedule_cancel" in src


def test_wiring_start_stdio_boots_scheduler():
    src = Path(__file__).resolve().parents[1].joinpath(
        "agent/start.py").read_text(encoding="utf-8")
    assert "soulmate_scheduler" in src and "ensure_started" in src


# ── 11. 错过留痕（15.1：missed 落 runs 日志可见，"不补跑"语义保持） ──

def _runs(s):
    db = s._connect()
    try:
        return [dict(r) for r in db.execute("SELECT * FROM runs ORDER BY id").fetchall()]
    finally:
        db.close()


def test_missed_occurrences_logged_interval(tmp_path):
    fired = []
    s = _sched(tmp_path,
               fire_fn=lambda t: fired.append(t["id"]) or {"stop_reason": "end_turn"})
    s.add_task("t", "interval:60", "x")   # due = now+60s
    s._clock["now"] += 600_000            # 10个周期后才被宿主拾取
    assert _run(s.run_due()) == 1         # 仍只补跑1次（不积压语义保持，既有断言不破）
    rows = _runs(s)
    missed = [r for r in rows if r["status"] == "missed"]
    assert len(missed) == 1
    # due+60k<=now → k=1..9 共9个执行点溜掉（due 那个=本次补跑）
    assert "错过9个执行点" in missed[0]["detail"]
    assert "不积压" in missed[0]["detail"]
    assert len([r for r in rows if r["status"] == "ok"]) == 1
    # missed 不算失败
    assert s.list_tasks(with_runs=False)[0]["fail_streak"] == 0
    assert s.snapshot()["stats"]["missed"] == 9
    assert any("错过留痕" in line for line in s.snapshot()["recent"])


def test_missed_occurrences_logged_cron(tmp_path):
    s = _sched(tmp_path, now_ms=int(datetime(2026, 10, 10, 8, 7).timestamp() * 1000))
    s.add_task("t", "cron:0 * * * *", "x")  # due = 09:00
    s._clock["now"] = int(datetime(2026, 10, 10, 12, 30).timestamp() * 1000)
    assert _run(s.run_due()) == 1           # 09:00 补跑1次
    missed = [r for r in _runs(s) if r["status"] == "missed"]
    assert len(missed) == 1
    # 10:00/11:00/12:00 三个执行点溜掉
    assert "错过3个执行点" in missed[0]["detail"]


def test_no_missed_row_when_slight_lateness(tmp_path):
    s = _sched(tmp_path, fire_fn=lambda t: {"stop_reason": "end_turn"})
    s.add_task("t", "interval:60", "x")   # due = now+60s
    s._clock["now"] += 90_000             # 迟到30s（<1周期）
    assert _run(s.run_due()) == 1
    rows = _runs(s)
    assert [r["status"] for r in rows] == ["ok"]  # 正常迟到无missed噪音


def test_missed_cap_truncates_long_downtime(tmp_path):
    s = _sched(tmp_path, fire_fn=lambda t: {"stop_reason": "end_turn"})
    s.add_task("t", "interval:60", "x")
    s._clock["now"] += 10 * 86_400_000    # 停机10天
    assert _run(s.run_due()) == 1
    missed = [r for r in _runs(s) if r["status"] == "missed"]
    assert len(missed) == 1
    assert "错过100个执行点" in missed[0]["detail"]  # 封顶如实
    assert "截断" in missed[0]["detail"]             # 截断自报
    assert s.snapshot()["stats"]["missed"] == 100


def test_missed_row_does_not_bump_fail_streak(tmp_path):
    def boom(t):
        raise RuntimeError("boom")
    s = _sched(tmp_path, fire_fn=boom)
    s.add_task("t", "interval:60", "x")
    s._clock["now"] += 600_000
    assert _run(s.run_due()) == 1
    rows = _runs(s)
    assert {r["status"] for r in rows} == {"failed", "missed"}
    assert s.list_tasks(with_runs=False)[0]["fail_streak"] == 1  # 只有failed计失败


def test_missed_logged_once_not_repeated(tmp_path):
    s = _sched(tmp_path, fire_fn=lambda t: {"stop_reason": "end_turn"})
    s.add_task("t", "interval:60", "x")
    s._clock["now"] += 600_000
    assert _run(s.run_due()) == 1
    assert _run(s.run_due()) == 0         # 无到点，不再领取
    assert len([r for r in _runs(s) if r["status"] == "missed"]) == 1


def test_wiring_missed_mark_and_logger():
    src = Path(__file__).resolve().parents[1].joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    assert '"missed": "⏭️"' in src  # schedule_list 展示 missed 不冒充❌失败
    sched = Path(__file__).resolve().parents[1].joinpath(
        "agent/soulmate_scheduler.py").read_text(encoding="utf-8")
    assert "self._log_missed(db, t, due_ms, now)" in sched  # claim赢家路径真实调用
