"""soulmate_daemon 常驻托管守护进程测试（路线图3.1/3.3遗留销账）。

覆盖：①无条件托管（has_enabled=False 也照常托管——后建任务/目标被轮询拾取）
②优雅退出双循环收尾 ③真实调度器/监控器在守护宿主内 claim→fire→落 runs 行
（=无 agent 进程也执行的集成证据）④接线护栏。
"""

import asyncio
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.soulmate_daemon import main, run_daemon  # noqa: E402


class FakeLoop:
    """记录 run_forever/stop 调用的假循环宿主。"""

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        self.started = False
        self.stopped = False
        self._running = False

    def has_enabled_tasks(self) -> bool:
        return self.enabled

    def has_enabled_targets(self) -> bool:
        return self.enabled

    async def run_forever(self):
        self.started = True
        self._running = True
        try:
            while self._running:
                await asyncio.sleep(0.01)
        finally:
            self._running = False

    def stop(self):
        self.stopped = True
        self._running = False


async def _run_with_stop(coro, stop: asyncio.Event, settle_s: float = 0.15):
    """跑 run_daemon，settle 后发停止信号，返回（等待daemon收尾）。"""
    task = asyncio.create_task(coro)
    await asyncio.sleep(settle_s)
    stop.set()
    await asyncio.wait_for(task, timeout=5)


def test_daemon_hosts_unconditionally():
    """即使 has_enabled_tasks/targets=False 也无条件托管两条循环。"""
    cron, mon = FakeLoop(enabled=False), FakeLoop(enabled=False)
    stop = asyncio.Event()

    async def scenario():
        await _run_with_stop(run_daemon(cron, mon, stop_event=stop), stop)

    asyncio.run(scenario())
    assert cron.started, "调度循环未被托管（has_enabled=False 也必须托管）"
    assert mon.started, "监控循环未被托管（has_enabled=False 也必须托管）"


def test_daemon_graceful_stop_calls_stop_on_both():
    """优雅退出：scheduler.stop()/monitor.stop() 都被调用，循环收尾。"""
    cron, mon = FakeLoop(enabled=True), FakeLoop(enabled=True)
    stop = asyncio.Event()

    async def scenario():
        await _run_with_stop(run_daemon(cron, mon, stop_event=stop), stop)

    asyncio.run(scenario())
    assert cron.stopped and mon.stopped, "退出时必须调用两宿主的 stop()"
    assert not cron._running and not mon._running, "退出后循环必须收尾"


def test_daemon_stop_event_injection_no_signal_handler():
    """注入 stop_event 时不注册信号处理器（测试环境无信号语义也能跑）。"""
    cron, mon = FakeLoop(), FakeLoop()
    stop = asyncio.Event()
    stop.set()  # 已置位=立即退出

    async def scenario():
        await asyncio.wait_for(run_daemon(cron, mon, stop_event=stop), timeout=5)

    asyncio.run(scenario())
    assert cron.stopped and mon.stopped, "注入stop_event退出也必须调用两宿主stop()收尾"


def test_daemon_hosts_real_scheduler_claim_and_fire(tmp_path):
    """真实 SoulMateScheduler/HealthMonitor 在守护宿主内跑通 claim→fire→runs 行。

    =无 agent 进程也执行的集成证据（遗留销账的核心语义）。
    fire_fn 注入计数器（不跑真 LLM——真 LLM 留给真机验收）。
    """
    from agent.health_monitor import HealthMonitor
    from agent.soulmate_scheduler import SoulMateScheduler

    fired = []

    async def fake_fire(task):
        fired.append(task["id"])  # claim 回来的 task 字典主键是 id（add_task 返回 task_id）
        return {"stop_reason": "end_turn"}

    cron_db = str(tmp_path / "cron.db")
    mon_db = str(tmp_path / "mon.db")
    cron = SoulMateScheduler(db_path=cron_db, fire_fn=fake_fire,
                             poll_interval_s=0.05)
    mon = HealthMonitor(db_path=mon_db, report_db_path=str(tmp_path / "report.db"))
    mon._poll_interval_s = 0.05  # HealthMonitor 走 SOULMATE_MONITOR_POLL_S，测试直设

    # 到点任务：interval:10s（调度串下限），把 next_run 拨到即刻到期
    task = cron.add_task("daemon_probe", "interval:10s", "守护宿主探针", "")
    db = sqlite3.connect(cron_db)
    try:
        db.execute("UPDATE tasks SET next_run_at_ms=0 WHERE id=?",
                   (task["task_id"],))
        db.commit()
    finally:
        db.close()
    stop = asyncio.Event()

    async def scenario():
        daemon = asyncio.create_task(run_daemon(cron, mon, stop_event=stop))
        # 等到 fire 发生（守护循环自行拾取，无 agent 进程）
        for _ in range(200):
            if fired:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(daemon, timeout=5)

    asyncio.run(scenario())
    assert fired == [task["task_id"]], f"守护宿主未claim并fire任务: fired={fired}"

    # runs 行落库（执行日志真实可见）
    db = sqlite3.connect(cron_db)
    try:
        rows = db.execute(
            "SELECT status FROM runs WHERE task_id=?", (task["task_id"],)
        ).fetchall()
    finally:
        db.close()
    assert rows, "fire 后 runs 表必须有执行行"
    assert rows[0][0] == "ok", f"fire 成功应记 ok，实际 {rows[0][0]}"


def test_daemon_real_monitor_runs_in_host(tmp_path):
    """真实 HealthMonitor 在守护宿主内跑检查（探针目标到点检查）。"""
    from agent.health_monitor import HealthMonitor

    mon_db = str(tmp_path / "mon.db")
    mon = HealthMonitor(db_path=mon_db, report_db_path=str(tmp_path / "report.db"))
    mon._poll_interval_s = 0.05  # HealthMonitor 走 SOULMATE_MONITOR_POLL_S，测试直设
    # 死端口探针：检查必 fail（确定性），但 run_due 会被守护循环真实走到
    mon.add_target(name="daemon_mon_probe", kind="port",
                   target="127.0.0.1:1", interval_s=30)
    cron = FakeLoop()  # 调度侧给假宿主即可
    stop = asyncio.Event()

    async def scenario():
        daemon = asyncio.create_task(run_daemon(cron, mon, stop_event=stop))
        for _ in range(200):
            db = sqlite3.connect(mon_db)
            try:
                n = db.execute("SELECT COUNT(*) FROM monitor_state").fetchone()[0]
            finally:
                db.close()
            if n > 0:
                break
            await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(daemon, timeout=5)

    asyncio.run(scenario())
    db = sqlite3.connect(mon_db)
    try:
        n = db.execute("SELECT COUNT(*) FROM monitor_state").fetchone()[0]
    finally:
        db.close()
    assert n >= 1, "守护宿主内监控检查必须真实执行并落 state 行"


def test_wiring_guard():
    """接线护栏：模块可导入、main 入口存在、run_daemon 可注入。"""
    import inspect

    assert callable(main)
    sig = inspect.signature(run_daemon)
    assert "scheduler" in sig.parameters and "monitor" in sig.parameters
    assert "stop_event" in sig.parameters
