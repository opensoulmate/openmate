# -*- coding: utf-8 -*-
"""pulse.py — ❤️Pulse 心跳层：时间驱动后台自主循环（保活 + 主动推进）。

原始设计四条（照此实现，禁走样）：
① **定时心跳**：用户没发消息/会话空闲时主动唤醒 Agent（周期性 beat，漏拍可见）；
② **周期自检**：后端服务/连接状态/任务队列/未完成工单/记忆一致性（自检清单
   框架 + 五类目钩子由宿主接既有件，检查失败=故障发现，显式 findings）；
③ **自主推进长任务**：持续监控/分批采集/定时复盘/超时任务恢复/僵尸子进程回收；
④ **维持"活着"**：主动感知主动推进（心跳计数/漂移统计/状态快照全可观测）。

分工铁律：Reflex（reflex.py）管前台应激（快、短路径）；Pulse 管后台自治
（持续、保活）。**软预算禁硬杀**：每拍/每工单步进只给软时间预算——超支只顺延
（deferred 显式计数）+ 记账，绝不 kill 执行中的步骤/进程；超时工单走恢复
（requeue/defer/attention 三路，恢复次数有界），僵尸回收只收割**已死**子进程
（os.waitpid(WNOHANG) 收尸体，绝不给活进程发信号）。

与既有件整合勿平行造轮子：
- 空闲语义单一真源 = **run_guard**（idle_seconds 暂停期冻结、check verdict 处决
  结论）+ steering.SessionActivity（status/idle_seconds）——guard_provider 由
  宿主把二者喂进来，本模块不自造空闲定义；
- 等待用户（run_guard pause，ask-user 期）不算可唤醒空闲：suppressed 留痕；
- run_guard verdict=stop（无进展超时）→ finding 显式可见（结论不硬杀）。

独立可测（tests/test_pulse.py）；零 LLM 依赖（测试断言源码无 llm 导入）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger("acp-agent.pulse")

__all__ = [
    "DEFAULT_INTERVAL_S", "DEFAULT_BEAT_BUDGET_S", "DEFAULT_TASK_BUDGET_S",
    "DEFAULT_IDLE_THRESHOLD_S", "DEFAULT_WAKE_COOLDOWN_S", "DEFAULT_MAX_RECOVERIES",
    "CATEGORY_BACKEND", "CATEGORY_CONNECTION", "CATEGORY_QUEUE",
    "CATEGORY_WORK_ORDER", "CATEGORY_MEMORY",
    "KIND_MONITOR", "KIND_BATCH", "KIND_REVIEW", "KIND_RECOVER",
    "STATUS_ACTIVE", "STATUS_PAUSED", "STATUS_DONE", "STATUS_OVERDUE",
    "STATUS_NEEDS_ATTENTION",
    "PulseCheck", "PulseFinding", "LongTask", "PulseLoop",
    "scan_zombies", "reap_pid",
]

DEFAULT_INTERVAL_S = 30.0           # 定时心跳周期
DEFAULT_BEAT_BUDGET_S = 5.0         # 每拍软预算（超支顺延，禁硬杀）
DEFAULT_TASK_BUDGET_S = 2.0         # 单工单步进软预算
DEFAULT_CHECK_BUDGET_S = 1.0        # 单自检软预算（超时标 slow，不杀）
DEFAULT_IDLE_THRESHOLD_S = 120.0    # 会话空闲唤醒阈值
DEFAULT_WAKE_COOLDOWN_S = 300.0     # 同会话唤醒冷却（防唠叨）
DEFAULT_MAX_RECOVERIES = 3          # 超时恢复次数上限（超出→needs_attention）

# ② 周期自检五类目（宿主按类目接既有件）
CATEGORY_BACKEND = "backend_service"
CATEGORY_CONNECTION = "connection_state"
CATEGORY_QUEUE = "task_queue"
CATEGORY_WORK_ORDER = "work_order"
CATEGORY_MEMORY = "memory_consistency"

# ③ 长任务四形态
KIND_MONITOR = "monitor"    # 持续监控
KIND_BATCH = "batch"        # 分批采集
KIND_REVIEW = "review"      # 定时复盘
KIND_RECOVER = "recover"    # 超时恢复工单

STATUS_ACTIVE = "active"
STATUS_PAUSED = "paused"
STATUS_DONE = "done"
STATUS_OVERDUE = "overdue"
STATUS_NEEDS_ATTENTION = "needs_attention"


@dataclass
class PulseFinding:
    """一条自检发现（故障必须可见）。"""
    check: str
    category: str
    ok: bool
    detail: str
    severity: str = "warn"       # warn / critical
    streak: int = 1              # 连续失败次数
    slow: bool = False
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"check": self.check, "category": self.category, "ok": self.ok,
                "detail": self.detail, "severity": self.severity,
                "streak": self.streak, "slow": self.slow, "ts": self.ts}


class PulseCheck:
    """一个自检项：fn() -> {"ok", "detail"} | bool | None（None=ok）。

    异常=ok False（检查自身崩溃=真故障，显式发现禁静默）。
    """

    def __init__(self, name: str, fn: Callable[[], Any],
                 category: str = "", critical: bool = False):
        self.name = str(name)
        self.fn = fn
        self.category = str(category or "")
        self.critical = bool(critical)
        self.fail_streak = 0
        self.runs = 0

    def run(self, now: float) -> PulseFinding:
        self.runs += 1
        t0 = time.perf_counter()
        try:
            r = self.fn()
            if r is None:
                ok, detail = True, ""
            elif isinstance(r, bool):
                ok, detail = r, ""
            elif isinstance(r, dict):
                ok = bool(r.get("ok", True))
                detail = str(r.get("detail", "") or "")
            else:
                ok, detail = True, str(r)[:200]
            err = ""
        except Exception as e:  # noqa: BLE001 — 检查崩溃=发现故障，禁静默
            ok, detail, err = False, f"{type(e).__name__}: {e}", str(e)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.fail_streak = self.fail_streak + 1 if not ok else 0
        return PulseFinding(
            check=self.name, category=self.category, ok=ok,
            detail=detail or ("检查异常: " + err if err else "ok"),
            severity="critical" if (not ok and self.critical) else "warn",
            streak=self.fail_streak,
            slow=elapsed_ms > DEFAULT_CHECK_BUDGET_S * 1000.0,
            ts=now,
        )


@dataclass
class LongTask:
    """长任务工单（③ 自主推进：持续监控/分批采集/定时复盘/超时恢复）。

    step(ctx) -> {"done": bool} | {"ok": bool, "detail"} | None：
    一步有界工作（分批采集=每拍一批）。软预算禁硬杀：超时只记账顺延。
    """
    task_id: str
    name: str
    kind: str = KIND_MONITOR
    step: Callable[[dict], Any] | None = None
    interval_s: float = DEFAULT_INTERVAL_S
    deadline_at: float | None = None      # None=无期限（持续型）
    budget_s: float = DEFAULT_TASK_BUDGET_S
    max_recoveries: int = DEFAULT_MAX_RECOVERIES
    session_id: str = ""
    ctx: dict = field(default_factory=dict)
    # 记账（全可观测）
    status: str = STATUS_ACTIVE
    steps_done: int = 0
    batches: int = 0
    deferred: int = 0
    over_budget: int = 0
    recoveries: int = 0
    errors: int = 0
    last_error: str = ""
    last_step_at: float = 0.0
    next_due_at: float = 0.0
    created_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id, "name": self.name, "kind": self.kind,
            "status": self.status, "steps_done": self.steps_done,
            "batches": self.batches, "deferred": self.deferred,
            "over_budget": self.over_budget, "recoveries": self.recoveries,
            "errors": self.errors, "last_error": self.last_error[:200],
            "deadline_at": self.deadline_at, "next_due_at": self.next_due_at,
            "session_id": self.session_id,
        }


# ── 僵尸子进程回收（只收割尸体，禁硬杀活进程） ──────────────────────


def scan_zombies(ppid: int | None = None) -> list[int]:
    """/proc 扫描本进程的僵尸子进程（state Z）。非 Linux/无 /proc → 空列表。"""
    ppid = os.getpid() if ppid is None else int(ppid)
    out: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat", "r", encoding="utf-8",
                      errors="replace") as f:
                data = f.read()
            # comm 可含空格/括号：最后一个 ')' 之后才是 state/ppid 字段
            rest = data[data.rfind(")") + 1:].split()
            state, parent = rest[0], int(rest[1])
        except (OSError, ValueError, IndexError):
            continue
        if state == "Z" and parent == ppid:
            out.append(int(entry))
    return out


def reap_pid(pid: int) -> dict:
    """收割一个子进程尸体（os.waitpid(WNOHANG)）。

    活进程返回 not reaped（**绝不发信号**，禁硬杀）；非子进程/已收割幂等。
    """
    try:
        got, status = os.waitpid(int(pid), os.WNOHANG)
    except ChildProcessError:
        return {"pid": int(pid), "reaped": False, "detail": "already reaped"}
    except OSError as e:
        return {"pid": int(pid), "reaped": False, "detail": f"waitpid: {e}"}
    if got == 0:
        return {"pid": int(pid), "reaped": False,
                "detail": "still running（未死不收割，禁硬杀）"}
    return {"pid": int(pid), "reaped": True, "status": status}


class PulseLoop:
    """后台心跳循环：beat()=一拍（同步核心，可直接驱动可测）；run_forever=异步
    心跳（周期唤醒，漂移/漏拍可观测）。每拍六段（逐段隔离，单段故障不倒心跳）：

    1. 心跳记账（beats/漂移/漏拍）        4. 僵尸子进程回收（只收尸体）
    2. 周期自检（五类目清单→findings）     5. 空闲唤醒（run_guard 空闲语义）
    3. 长任务推进（软预算/超时恢复）       6. 软预算记账（超支顺延禁硬杀）
    """

    def __init__(self, interval_s: float = DEFAULT_INTERVAL_S,
                 checks: list | None = None, tasks: list | None = None,
                 guard_provider: Callable[[], dict] | None = None,
                 child_lister: Callable[[], list[int]] | None = None,
                 on_idle_wake: Callable[[str, str], Any] | None = None,
                 on_finding: Callable[[dict], Any] | None = None,
                 on_task_overdue: Callable[[dict], Any] | None = None,
                 idle_threshold_s: float = DEFAULT_IDLE_THRESHOLD_S,
                 wake_cooldown_s: float = DEFAULT_WAKE_COOLDOWN_S,
                 beat_budget_s: float = DEFAULT_BEAT_BUDGET_S,
                 now=None):
        self.interval_s = float(interval_s)
        self.idle_threshold_s = float(idle_threshold_s)
        self.wake_cooldown_s = float(wake_cooldown_s)
        self.beat_budget_s = float(beat_budget_s)
        self._now = now or time.time
        self._checks: list[PulseCheck] = []
        self._tasks: dict[str, LongTask] = {}
        for c in (checks or []):
            self.add_check(c)
        for t in (tasks or []):
            self.add_task(t)
        self._guard_provider = guard_provider
        self._child_lister = child_lister
        self._on_idle_wake = on_idle_wake
        self._on_finding = on_finding
        self._on_task_overdue = on_task_overdue
        self.beats = 0
        self.missed_beats = 0
        self.beat_errors = 0
        self.last_beat_error = ""
        self.last_beat_at = 0.0
        self.drift_ms_max = 0.0
        self.drift_ms_sum = 0.0
        self.reaped_total = 0
        self.wakes_total = 0
        self.deferred_total = 0
        self._last_wake_at: dict[str, float] = {}
        self._beat_reports: list[dict] = []
        self._running = False
        self._task_obj: asyncio.Task | None = None

    # ── 注册面 ──────────────────────────────────────────────────────

    def add_check(self, check: PulseCheck) -> PulseCheck:
        self._checks = [c for c in self._checks if c.name != check.name]
        self._checks.append(check)
        return check

    def add_task(self, task: LongTask, now: float | None = None) -> LongTask:
        if task.task_id in self._tasks:
            raise ValueError(f"task_id 已存在: {task.task_id}")
        task.next_due_at = task.next_due_at or (now if now is not None
                                                else self._now())
        self._tasks[task.task_id] = task
        return task

    def remove_task(self, task_id: str) -> bool:
        return self._tasks.pop(str(task_id), None) is not None

    def tasks(self) -> list[LongTask]:
        return list(self._tasks.values())

    def checks(self) -> list[PulseCheck]:
        return list(self._checks)

    # ── 心跳主入口（同步核心：一拍六段，逐段隔离） ──────────────────

    def beat(self) -> dict:
        t0 = time.perf_counter()
        now = self._now()
        self.beats += 1
        self.last_beat_at = now
        report = {
            "beat": self.beats, "ts": now,
            "checks": [], "findings": [],
            "tasks_advanced": [], "deferred": 0,
            "overdue": [], "recovered": [], "needs_attention": [],
            "zombies_reaped": [], "zombies_refused": [],
            "wakes": [], "suppressed": [],
            "phase_errors": [], "over_budget": False,
        }
        # 2. 周期自检
        self._phase(report, "self_check", self._phase_self_check, report, now)
        # 3. 长任务推进 + 超时恢复
        self._phase(report, "tasks", self._phase_tasks, report, now)
        # 4. 僵尸子进程回收
        self._phase(report, "zombies", self._phase_zombies, report)
        # 5. 空闲唤醒（run_guard 空闲语义）
        self._phase(report, "idle_wake", self._phase_idle_wake, report, now)
        # 6. 软预算记账
        report["elapsed_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)
        report["over_budget"] = report["elapsed_ms"] > self.beat_budget_s * 1000.0
        if report["over_budget"]:
            report["phase_errors"].append(
                f"beat 软预算超支 {report['elapsed_ms']:.0f}ms>"
                f"{self.beat_budget_s * 1000:.0f}ms（顺延禁硬杀）")
        self._beat_reports.append(report)
        if len(self._beat_reports) > 200:
            self._beat_reports = self._beat_reports[-200:]
        return report

    def _phase(self, report: dict, name: str, fn: Callable, *args) -> None:
        """单段故障降级：段内异常只记 phase_errors，绝不倒心跳。"""
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001
            report["phase_errors"].append(f"{name}: {type(e).__name__}: {e}")
            logger.warning(f"[pulse] phase {name} failed (心跳继续): {e}")

    # ── 2. 周期自检 ─────────────────────────────────────────────────

    def _phase_self_check(self, report: dict, now: float) -> None:
        for check in list(self._checks):
            finding = check.run(now)
            report["checks"].append(finding.as_dict())
            if not finding.ok:
                report["findings"].append(finding.as_dict())
                self._fire_finding(finding.as_dict())

    def _fire_finding(self, finding: dict) -> None:
        if self._on_finding is None:
            return
        try:
            r = self._on_finding(finding)
            if asyncio.iscoroutine(r):
                r.close()   # 反射/心跳回调只收同步快速回调
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[pulse] on_finding 回调失败(非致命): {e}")

    # ── 3. 长任务推进（软预算禁硬杀）+ 超时恢复 ─────────────────────

    def _phase_tasks(self, report: dict, now: float) -> None:
        due = [t for t in self._tasks.values()
               if t.status in (STATUS_ACTIVE, STATUS_OVERDUE)
               and t.next_due_at <= now]
        phase_t0 = time.perf_counter()
        for i, task in enumerate(due):
            # 拍级软预算：超支则剩余工单显式顺延（不杀任何在跑步骤）
            spent = time.perf_counter() - phase_t0
            if spent > self.beat_budget_s:
                report["deferred"] += len(due) - i
                self.deferred_total += len(due) - i
                for rest in due[i:]:
                    rest.deferred += 1
                break
            self._advance_task(task, report, now)
        # 无期限持续型走不到 due 的（interval 未到）什么都不做——保活语义
        for task in self._tasks.values():
            if task.status == STATUS_ACTIVE and task.deadline_at is not None \
                    and now >= task.deadline_at:
                self._recover_task(task, report, now)

    def _advance_task(self, task: LongTask, report: dict, now: float) -> None:
        if task.step is None:
            task.next_due_at = now + task.interval_s
            return
        t0 = time.perf_counter()
        try:
            r = task.step(dict(task.ctx)) or {}
            err = ""
        except Exception as e:  # noqa: BLE001 — 步骤故障=记账可见，禁杀任务
            r, err = {"ok": False}, f"{type(e).__name__}: {e}"
        elapsed = time.perf_counter() - t0
        task.last_step_at = now
        task.steps_done += 1
        task.batches += 1 if task.kind == KIND_BATCH else 0
        if elapsed > task.budget_s:
            # 软预算超支：只记账（步骤已自然跑完，绝不中途硬杀）
            task.over_budget += 1
        if err:
            task.errors += 1
            task.last_error = err
        elif r.get("ok") is False:
            task.errors += 1
            task.last_error = str(r.get("detail") or r.get("error") or "step not ok")
        if r.get("done"):
            task.status = STATUS_DONE
            report["tasks_advanced"].append(task.task_id)
            return
        if task.deadline_at is not None and now >= task.deadline_at:
            self._recover_task(task, report, now)
            return
        task.next_due_at = now + task.interval_s
        report["tasks_advanced"].append(task.task_id)

    def _recover_task(self, task: LongTask, report: dict, now: float) -> None:
        """超时任务恢复（③）：requeue/defer/attention 三路，恢复有界，禁硬杀。"""
        task.status = STATUS_OVERDUE
        report["overdue"].append(task.task_id)
        verdict = {"action": "attention",
                   "detail": "timeout（默认：标记需关注，禁硬杀）"}
        if self._on_task_overdue is not None:
            try:
                r = self._on_task_overdue(task.as_dict())
                if isinstance(r, dict):
                    verdict = r
            except Exception as e:  # noqa: BLE001
                verdict = {"action": "attention", "detail": f"recovery error: {e}"}
        action = str(verdict.get("action") or "attention")
        if action == "requeue" and task.recoveries < task.max_recoveries:
            task.recoveries += 1
            task.status = STATUS_ACTIVE
            task.next_due_at = now + task.interval_s
            report["recovered"].append(
                {"task_id": task.task_id, "action": "requeue",
                 "recoveries": task.recoveries,
                 "detail": str(verdict.get("detail") or "")})
        elif action == "defer" and task.recoveries < task.max_recoveries:
            task.recoveries += 1
            task.next_due_at = now + max(task.interval_s,
                                         float(verdict.get("delay_s") or 0))
            report["recovered"].append(
                {"task_id": task.task_id, "action": "defer",
                 "recoveries": task.recoveries,
                 "detail": str(verdict.get("detail") or "")})
        else:
            # 恢复耗尽/要求关注：needs_attention（工单保留可见，禁硬杀禁静默）
            task.status = STATUS_NEEDS_ATTENTION
            report["needs_attention"].append(task.task_id)
            self._fire_finding({
                "check": "pulse.task_timeout", "category": CATEGORY_WORK_ORDER,
                "ok": False, "severity": "warn",
                "detail": (f"工单 {task.name!r}({task.task_id}) 超时，"
                           f"恢复 {task.recoveries}/{task.max_recoveries} 次后需关注"
                           f"（软预算禁硬杀，任务保留可见）"),
                "streak": task.recoveries + 1, "ts": now})

    # ── 4. 僵尸子进程回收（只收尸体） ────────────────────────────────

    def _phase_zombies(self, report: dict) -> None:
        try:
            pids = self._child_lister() if self._child_lister else scan_zombies()
        except Exception as e:  # noqa: BLE001
            report["phase_errors"].append(f"zombie-scan: {e}")
            return
        for pid in pids or []:
            r = reap_pid(pid)
            if r.get("reaped"):
                self.reaped_total += 1
                report["zombies_reaped"].append(r)
            else:
                report["zombies_refused"].append(r)

    # ── 5. 空闲唤醒（①：用户没发消息/会话空闲时主动唤醒；run_guard 空闲语义）─

    def _phase_idle_wake(self, report: dict, now: float) -> None:
        data = {}
        if self._guard_provider is not None:
            try:
                data = self._guard_provider() or {}
            except Exception as e:  # noqa: BLE001
                report["phase_errors"].append(f"guard_provider: {e}")
                return
        sessions = data.get("sessions") or {}
        due_tasks = int(data.get("due_tasks") or 0)
        for sid, info in sessions.items():
            if not isinstance(info, dict):
                continue
            busy = bool(info.get("busy"))
            guard_paused = bool(info.get("guard_paused"))
            has_guard = bool(info.get("has_guard"))
            # run_guard 空闲语义单一真源：有守卫以守卫无进展时长为准
            # （ask-user 等待期 pause 冻结），无守卫回落 SessionActivity 口径
            idle_s = float(info.get("guard_idle_seconds") or 0.0) if has_guard \
                else float(info.get("idle_seconds") or 0.0)
            pending = int(info.get("pending") or 0)
            if busy:
                continue
            if guard_paused:
                # 等待用户作答期（run_guard pause）：不算可唤醒空闲，留痕
                report["suppressed"].append(
                    {"session_id": sid, "reason": "waiting_user(run_guard paused)"})
                continue
            if idle_s < self.idle_threshold_s:
                continue
            last = self._last_wake_at.get(sid, 0.0)
            if now - last < self.wake_cooldown_s:
                continue
            reason = ("idle_pending_work"
                      if pending > 0 or due_tasks > 0 else "idle_keepalive")
            woke = False
            if self._on_idle_wake is not None:
                try:
                    r = self._on_idle_wake(sid, reason)
                    if asyncio.iscoroutine(r):
                        r.close()
                    woke = True
                except Exception as e:  # noqa: BLE001
                    report["phase_errors"].append(f"on_idle_wake({sid}): {e}")
            self._last_wake_at[sid] = now
            if woke:
                self.wakes_total += 1
                report["wakes"].append(
                    {"session_id": sid, "reason": reason,
                     "idle_seconds": round(idle_s, 1)})
            # run_guard verdict=stop（无进展超时）→ finding 显式可见（结论不硬杀）
            if str(info.get("guard_verdict") or "") == "stop":
                self._fire_finding({
                    "check": "run_guard.no_progress",
                    "category": CATEGORY_WORK_ORDER, "ok": False,
                    "severity": "warn",
                    "detail": (f"会话 {sid} run_guard verdict=stop"
                               f"（无进展 {idle_s:.0f}s 超时）——只出结论不硬杀，"
                               f"建议收尾交付已有产物并如实报告"),
                    "streak": 1, "ts": now})

    # ── 异步心跳（定时唤醒） ────────────────────────────────────────

    def start(self) -> asyncio.Task:
        """启动后台心跳（已在跑则复用）。"""
        if self._task_obj is not None and not self._task_obj.done():
            return self._task_obj
        self._running = True
        self._task_obj = asyncio.get_event_loop().create_task(self.run_forever())
        return self._task_obj

    async def stop(self, timeout_s: float = 5.0) -> None:
        """软停：等当前拍自然结束（不 cancel 正在跑的拍），随后退出。"""
        self._running = False
        task, self._task_obj = self._task_obj, None
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()

    async def run_forever(self) -> None:
        """定时心跳：按 interval 对齐节拍，漂移/漏拍可观测。"""
        next_at = time.monotonic()
        while self._running:
            drift_ms = (time.monotonic() - next_at) * 1000.0
            try:
                self.beat()
                self.drift_ms_sum += max(drift_ms, 0.0)
                self.drift_ms_max = max(self.drift_ms_max, drift_ms)
            except Exception as e:  # noqa: BLE001 — 单拍异常不倒心跳
                self.beat_errors += 1
                self.last_beat_error = f"{type(e).__name__}: {e}"
                logger.warning(f"[pulse] beat 异常(心跳继续): {e}")
            next_at += self.interval_s
            delay = next_at - time.monotonic()
            if delay < -self.interval_s:    # 落后超一拍：计漏拍重新对齐
                self.missed_beats += int(-delay // self.interval_s)
                next_at = time.monotonic()
                delay = 0.0
            if not self._running:
                break
            # 可中断睡眠（stop() 免等满一拍）
            try:
                await asyncio.wait_for(_stop_waiter(self), timeout=max(delay, 0.01))
            except asyncio.TimeoutError:
                pass

    # ── 观测（④ 维持"活着"） ───────────────────────────────────────

    def status(self) -> dict:
        return {
            "running": self._running,
            "beats": self.beats,
            "missed_beats": self.missed_beats,
            "beat_errors": self.beat_errors,
            "last_beat_error": self.last_beat_error,
            "last_beat_at": self.last_beat_at,
            "interval_s": self.interval_s,
            "avg_drift_ms": round(self.drift_ms_sum / self.beats, 2)
            if self.beats else 0.0,
            "max_drift_ms": round(self.drift_ms_max, 2),
            "checks": [{"name": c.name, "category": c.category,
                        "runs": c.runs, "fail_streak": c.fail_streak}
                       for c in self._checks],
            "tasks": {
                "total": len(self._tasks),
                "by_status": _count_by(self._tasks.values(), "status"),
                "items": [t.as_dict() for t in self._tasks.values()],
            },
            "reaped_total": self.reaped_total,
            "wakes_total": self.wakes_total,
            "deferred_total": self.deferred_total,
        }

    def recent_beats(self, limit: int = 10) -> list[dict]:
        return self._beat_reports[-max(1, int(limit)):]


def _count_by(items, attr: str) -> dict:
    out: dict[str, int] = {}
    for it in items:
        key = str(getattr(it, attr, ""))
        out[key] = out.get(key, 0) + 1
    return out


async def _stop_waiter(loop: "PulseLoop") -> None:
    """可中断睡眠：stop() 置 _running=False 后 50ms 内苏醒退出。"""
    while loop._running:
        await asyncio.sleep(0.05)
