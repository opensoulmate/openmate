# -*- coding: utf-8 -*-
"""❤️Pulse 心跳层测试 — 后台心跳循环（周期唤醒/自检清单/长任务推进/超时恢复/
僵尸回收，软预算禁硬杀）。

真实验证（非纸面）：真心跳跑真 asyncio 循环量节拍与漂移；真心跳自检**实测发现
真故障**（真缺文件/真拒连端口）；真僵尸子进程实测收割；真 run_guard 空闲语义
实测唤醒（含 pause 抑制/verdict=stop 可见）。系统性覆盖：故障降级（单段炸不倒
心跳）× 软预算（慢步骤完整跑完绝不中途杀）× 并发 × 禁硬杀（monkeypatch os.kill
断言全程零调用）。
"""
import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.pulse import (
    CATEGORY_MEMORY,
    CATEGORY_WORK_ORDER,
    DEFAULT_MAX_RECOVERIES,
    KIND_BATCH,
    STATUS_DONE,
    STATUS_NEEDS_ATTENTION,
    STATUS_OVERDUE,
    LongTask,
    PulseCheck,
    PulseLoop,
    reap_pid,
    scan_zombies,
)
from agent.run_guard import RunGuard


def run(coro):
    return asyncio.run(coro)


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture(autouse=True)
def _forbid_hard_kill(monkeypatch):
    """铁律验证器：Pulse 全程禁止硬杀——os.kill 被调用即失败。

    测试自清理（test 自己拉起的子进程收尾）不属于 Pulse 行为：清理前调
    permit_cleanup() 临时放行。
    """
    calls = []
    real_kill = os.kill

    def _boom(pid, sig):  # pragma: no cover - 只在违规时触发
        calls.append((pid, sig))
        raise AssertionError(f"Pulse 违规硬杀: os.kill({pid}, {sig})")

    monkeypatch.setattr(os, "kill", _boom)

    class Guard:
        def permit_cleanup(self):
            monkeypatch.setattr(os, "kill", real_kill)

    yield Guard()
    assert not calls


# ═══════════════════════════════════════════════════════════════════
# ① 定时心跳（真 asyncio 循环实测节拍/漂移/漏拍）
# ═══════════════════════════════════════════════════════════════════

class TestHeartbeat:
    def test_real_heartbeat_beats_and_drift_measured(self):
        pl = PulseLoop(interval_s=0.05)

        async def scenario():
            pl.start()
            await asyncio.sleep(0.4)
            t0 = time.time()
            await pl.stop(timeout_s=2.0)
            return t0

        run(scenario())
        st = pl.status()
        print(f"\n[实测] Pulse 真心跳: beats={st['beats']} "
              f"avg_drift={st['avg_drift_ms']:.2f}ms max_drift={st['max_drift_ms']:.2f}ms "
              f"missed={st['missed_beats']}")
        assert st["beats"] >= 3, "0.4s/50ms 周期至少 3 拍（真跑）"
        assert st["last_beat_at"] > 0
        assert st["missed_beats"] == 0

    def test_beat_counter_monotonic_and_report_bounded(self):
        pl = PulseLoop(interval_s=0.01)
        for _ in range(250):
            pl.beat()
        assert pl.beats == 250
        assert len(pl._beat_reports) <= 200   # 观测面有界不膨胀

    def test_stop_is_soft_and_prompt(self):
        pl = PulseLoop(interval_s=10.0)   # 长周期：stop 不等满一拍

        async def scenario():
            pl.start()
            await asyncio.sleep(0.1)
            t0 = time.monotonic()
            await pl.stop(timeout_s=2.0)
            return time.monotonic() - t0

        dt = run(scenario())
        assert dt < 2.0, "stop 软停不该等满 10s 周期"


# ═══════════════════════════════════════════════════════════════════
# ② 周期自检（真心跳实测发现真故障）
# ═══════════════════════════════════════════════════════════════════

class TestSelfCheckRealFault:
    def test_real_beat_discovers_real_missing_file_fault(self, tmp_path):
        """真故障：记忆一致性检查指向真缺失文件 → 真心跳拍里被发现（禁静默）。"""
        missing = tmp_path / "MEMORY.md"   # 故意不创建

        def memory_consistency():
            text = Path(missing).read_text()   # 真缺失 → 真异常
            return {"ok": True, "detail": f"{len(text)} bytes"}

        pl = PulseLoop(interval_s=0.01)
        pl.add_check(PulseCheck("memory_consistency", memory_consistency,
                                category=CATEGORY_MEMORY))
        report = pl.beat()
        assert report["findings"], "真故障必须被心跳发现"
        f = report["findings"][0]
        assert f["check"] == "memory_consistency" and f["ok"] is False
        assert "FileNotFoundError" in f["detail"] or "Errno" in f["detail"]
        # 连续拍 streak 递增（持续故障可见）
        r2 = pl.beat()
        assert r2["findings"][0]["streak"] == 2
        assert pl.status()["checks"][0]["fail_streak"] == 2

    def test_real_beat_discovers_dead_port_fault(self):
        """真故障：后端服务探活打真拒连端口 → 心跳发现服务未监听。"""
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()   # 端口确定无人监听

        def backend_services():
            socket.create_connection(("127.0.0.1", dead_port), timeout=0.3).close()
            return {"ok": True}

        pl = PulseLoop()
        pl.add_check(PulseCheck("backend_services", backend_services, critical=True))
        report = pl.beat()
        f = report["findings"][0]
        assert f["ok"] is False and f["severity"] == "critical"

    def test_healthy_checks_report_ok(self):
        pl = PulseLoop()
        pl.add_check(PulseCheck("ok_bool", lambda: True))
        pl.add_check(PulseCheck("ok_dict", lambda: {"ok": True, "detail": "好"}))
        pl.add_check(PulseCheck("ok_none", lambda: None))
        report = pl.beat()
        assert not report["findings"]
        assert len(report["checks"]) == 3

    def test_on_finding_callback_invoked(self):
        seen = []
        pl = PulseLoop(on_finding=lambda f: seen.append(f["check"]))
        pl.add_check(PulseCheck("bad", lambda: {"ok": False, "detail": "坏"}))
        pl.beat()
        assert seen == ["bad"]


# ═══════════════════════════════════════════════════════════════════
# ③ 自主推进长任务（分批采集/软预算禁硬杀/故障降级）
# ═══════════════════════════════════════════════════════════════════

class TestLongTasks:
    def test_batch_task_advances_across_beats(self):
        batches = []

        def step(ctx):
            batches.append(len(batches))
            return {"done": len(batches) >= 3}   # 3 批收工

        clock = FakeClock()
        pl = PulseLoop(now=clock)
        pl.add_task(LongTask("t1", "分批采集", kind=KIND_BATCH, step=step,
                             interval_s=0.0))
        for _ in range(5):
            pl.beat()
            clock.advance(1)
        assert len(batches) == 3
        assert pl.tasks()[0].status == STATUS_DONE
        assert pl.tasks()[0].batches == 3

    def test_soft_budget_slow_step_completes_never_killed(self):
        """软预算：慢步骤完整跑完（副作用可见）只记账超支，绝不中途硬杀。"""
        finished = []

        def slow_step(ctx):
            time.sleep(0.08)          # 远超 budget_s=0.01
            finished.append(True)
            return {"done": True}

        clock = FakeClock()
        pl = PulseLoop(now=clock, beat_budget_s=5.0)
        pl.add_task(LongTask("t1", "慢工单", step=slow_step,
                             budget_s=0.01, interval_s=0.0))
        report = pl.beat()
        assert finished == [True], "步骤必须完整跑完（禁硬杀）"
        t = pl.tasks()[0]
        assert t.over_budget == 1     # 超支显式记账
        assert t.status == STATUS_DONE

    def test_task_step_exception_degrades_not_fatal(self):
        def boom(ctx):
            raise RuntimeError("步骤炸了")

        clock = FakeClock()
        pl = PulseLoop(now=clock)
        pl.add_task(LongTask("t1", "坏工单", step=boom, interval_s=0.0))
        pl.add_check(PulseCheck("ok", lambda: True))
        report = pl.beat()
        t = pl.tasks()[0]
        assert t.errors == 1 and "RuntimeError" in t.last_error
        assert report["checks"], "工单故障不阻断自检段（段间故障降级）"

    def test_phase_budget_defers_remaining_tasks_explicitly(self):
        """拍级软预算超支 → 剩余工单显式顺延（deferred 计数，不杀）。"""
        def slow(ctx):
            time.sleep(0.06)
            return {}

        clock = FakeClock()
        pl = PulseLoop(now=clock, beat_budget_s=0.05)
        for i in range(3):
            pl.add_task(LongTask(f"t{i}", f"工单{i}", step=slow,
                                 budget_s=0.01, interval_s=0.0))
        report = pl.beat()
        assert report["deferred"] > 0
        assert pl.deferred_total > 0
        assert sum(t.deferred for t in pl.tasks()) > 0

    def test_concurrent_task_registration_during_beats(self):
        import threading
        pl = PulseLoop()
        stop = False

        def registrar():
            i = 0
            while not stop and i < 100:
                try:
                    pl.add_task(LongTask(f"dyn{i}", f"动态{i}",
                                         step=lambda ctx: {}, interval_s=1.0))
                except ValueError:
                    pass
                i += 1

        th = threading.Thread(target=registrar)
        th.start()
        try:
            for _ in range(20):
                pl.beat()
        finally:
            stop = True
            th.join(timeout=5)


# ═══════════════════════════════════════════════════════════════════
# ③ 超时任务恢复（requeue/defer/attention 有界，禁硬杀禁静默）
# ═══════════════════════════════════════════════════════════════════

class TestTimeoutRecovery:
    def test_overdue_requeue_until_needs_attention(self):
        clock = FakeClock()
        recoveries = []
        pl = PulseLoop(now=clock,
                       on_task_overdue=lambda t: (
                           recoveries.append(t["task_id"]),
                           {"action": "requeue"})[1])
        pl.add_task(LongTask("t1", "超时工单", step=lambda ctx: {},
                             deadline_at=clock() - 1, interval_s=0.0))
        for _ in range(DEFAULT_MAX_RECOVERIES + 3):
            pl.beat()
            clock.advance(1)
        t = pl.tasks()[0]
        # 恢复有界：达上限后 needs_attention（工单保留可见，绝不 kill）
        assert t.status == STATUS_NEEDS_ATTENTION
        assert t.recoveries == DEFAULT_MAX_RECOVERIES
        assert len(recoveries) >= DEFAULT_MAX_RECOVERIES

    def test_overdue_finding_visible_and_not_killed(self):
        clock = FakeClock()
        findings = []
        pl = PulseLoop(now=clock, on_finding=lambda f: findings.append(f))
        pl.add_task(LongTask("t1", "工单", step=lambda ctx: {},
                             deadline_at=clock() - 1, interval_s=0.0,
                             max_recoveries=0))
        report = pl.beat()
        assert report["overdue"] == ["t1"]
        assert report["needs_attention"] == ["t1"]
        timeout_findings = [f for f in findings if f["check"] == "pulse.task_timeout"]
        assert timeout_findings and "禁硬杀" in timeout_findings[0]["detail"]
        assert pl.tasks()[0].status == STATUS_NEEDS_ATTENTION   # 仍在册（未被杀）

    def test_defer_action_delays_next_due(self):
        clock = FakeClock()
        pl = PulseLoop(now=clock,
                       on_task_overdue=lambda t: {"action": "defer",
                                                  "delay_s": 100})
        pl.add_task(LongTask("t1", "工单", step=lambda ctx: {},
                             deadline_at=clock() - 1, interval_s=0.0))
        pl.beat()
        t = pl.tasks()[0]
        assert t.recoveries == 1
        assert t.next_due_at > clock() + 50   # 已推迟，不再每拍打扰


# ═══════════════════════════════════════════════════════════════════
# ③ 僵尸子进程回收（真僵尸实测收割；活进程绝不杀）
# ═══════════════════════════════════════════════════════════════════

class TestZombieReaper:
    def test_real_zombie_child_reaped(self):
        """真僵尸：真实子进程退出未 wait → /proc 扫描发现 → 心跳收割。"""
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        deadline = time.time() + 5.0
        while time.time() < deadline and proc.pid not in scan_zombies():
            time.sleep(0.02)
        assert proc.pid in scan_zombies(), "子进程应已成僵尸（未 wait）"
        pl = PulseLoop()
        report = pl.beat()
        assert any(r["pid"] == proc.pid for r in report["zombies_reaped"])
        assert pl.reaped_total == 1
        # 已被收割（二次 waitpid 报 already reaped）
        r = reap_pid(proc.pid)
        assert r["reaped"] is False and "already reaped" in r["detail"]

    def test_running_child_refused_and_alive(self, _forbid_hard_kill):
        """活进程绝不收割更不杀：refused 留痕，进程继续活着（测试收尾自理）。"""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            pl = PulseLoop(child_lister=lambda: [proc.pid])
            report = pl.beat()
            assert any(r["pid"] == proc.pid and not r["reaped"]
                       for r in report["zombies_refused"])
            assert proc.poll() is None, "活进程必须原样活着（禁硬杀）"
        finally:
            _forbid_hard_kill.permit_cleanup()   # 测试自清理≠Pulse行为
            proc.terminate()
            proc.wait(timeout=5)

    def test_reap_idempotent_and_scan_bounded(self):
        assert reap_pid(2 ** 22 + 12345)["reaped"] is False  # 非子进程幂等
        assert isinstance(scan_zombies(), list)


# ═══════════════════════════════════════════════════════════════════
# ① 空闲唤醒（run_guard 空闲语义整合实测）
# ═══════════════════════════════════════════════════════════════════

class TestIdleWake:
    def _guard_provider(self, guard, busy=False, pending=0):
        def provider():
            return {"sessions": {"s1": {
                "status": "running" if busy else "idle",
                "busy": busy,
                "pending": pending,
                "idle_seconds": 0.0,
                "has_guard": guard is not None,
                "guard_idle_seconds": guard.idle_seconds() if guard else 0.0,
                "guard_paused": guard.snapshot().get("paused", False) if guard else False,
                "guard_verdict": guard.check().get("verdict", "ok") if guard else "ok",
            }}}
        return provider

    def test_idle_wake_fires_real_guard_real_time(self):
        """空闲唤醒实测：真 run_guard 真时钟无进展超阈值 → 心跳唤醒（真延迟量）。"""
        guard = RunGuard(no_progress_timeout=0.05)   # 真时钟
        wakes = []
        pl = PulseLoop(idle_threshold_s=0.05, wake_cooldown_s=300,
                       guard_provider=self._guard_provider(guard),
                       on_idle_wake=lambda sid, reason: wakes.append((sid, reason)))
        time.sleep(0.12)                              # 真实空闲
        t0 = time.perf_counter()
        report = pl.beat()
        wake_latency = (time.perf_counter() - t0) * 1000.0
        print(f"\n[实测] Pulse 空闲唤醒: guard_idle={guard.idle_seconds():.2f}s "
              f"wake_latency={wake_latency:.2f}ms wakes={wakes}")
        assert wakes == [("s1", "idle_keepalive")]
        assert report["wakes"][0]["session_id"] == "s1"

    def test_idle_pending_work_reason(self):
        guard = RunGuard()
        wakes = []
        pl = PulseLoop(idle_threshold_s=0.0, wake_cooldown_s=0,
                       guard_provider=self._guard_provider(guard, pending=3),
                       on_idle_wake=lambda sid, reason: wakes.append(reason))
        pl.beat()
        assert wakes == ["idle_pending_work"]

    def test_run_guard_pause_suppresses_wake(self):
        """run_guard 空闲语义整合：ask-user 等待期（pause）不算可唤醒空闲。"""
        guard = RunGuard()
        guard.pause()     # 等用户作答
        wakes = []
        pl = PulseLoop(idle_threshold_s=0.0,
                       guard_provider=self._guard_provider(guard),
                       on_idle_wake=lambda sid, reason: wakes.append(reason))
        report = pl.beat()
        assert wakes == []
        assert report["suppressed"][0]["reason"].startswith("waiting_user")

    def test_busy_session_not_woken(self):
        wakes = []
        pl = PulseLoop(idle_threshold_s=0.0,
                       guard_provider=self._guard_provider(RunGuard(), busy=True),
                       on_idle_wake=lambda sid, reason: wakes.append(reason))
        pl.beat()
        assert wakes == []

    def test_wake_cooldown_prevents_nagging(self):
        guard = RunGuard()
        wakes = []
        clock = FakeClock()
        pl = PulseLoop(now=clock, idle_threshold_s=0.0, wake_cooldown_s=300,
                       guard_provider=self._guard_provider(guard),
                       on_idle_wake=lambda sid, reason: wakes.append(reason))
        pl.beat()
        clock.advance(10)
        pl.beat()
        assert len(wakes) == 1
        clock.advance(301)
        pl.beat()
        assert len(wakes) == 2

    def test_run_guard_verdict_stop_surfaced_as_finding(self):
        """run_guard 无进展超时（verdict=stop）→ 心跳 finding 显式可见（不硬杀）。"""
        guard = RunGuard(no_progress_timeout=0.05)
        findings = []
        pl = PulseLoop(idle_threshold_s=0.0,
                       guard_provider=self._guard_provider(guard),
                       on_finding=lambda f: findings.append(f))
        time.sleep(0.1)     # 真超时 → 真 verdict=stop
        pl.beat()
        assert any(f["check"] == "run_guard.no_progress" and f["ok"] is False
                   for f in findings)
        assert "不硬杀" in next(f for f in findings
                               if f["check"] == "run_guard.no_progress")["detail"]

    def test_guard_provider_failure_degrades_heartbeat(self):
        def broken_provider():
            raise RuntimeError("供数炸了")

        pl = PulseLoop(guard_provider=broken_provider,
                       on_idle_wake=lambda s, r: None)
        report = pl.beat()
        assert any("guard_provider" in e for e in report["phase_errors"])
        assert pl.beats == 1   # 心跳本身不倒
