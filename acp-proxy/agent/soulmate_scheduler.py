"""SoulMateScheduler — 路线图 3.1 cron引擎（SoulMate 自己的任务调度：schedule+执行+日志）。

与 schedule_wakeup 的辨析（现状盘点缺口原话："schedule_wakeup 只是延迟唤醒，
非 schedule+执行+日志的任务调度"）：
- schedule_wakeup = 一次性延迟唤醒（opensoul /api/will/wakeup 状态机，闭环靠agent再调）；
- 本模块 = **周期任务调度**：interval（每N秒）或 5字段cron表达式，到点自动执行、
  结果落日志、自动排下一次——不依赖用户/会话在线输入。

链路：agent工具 schedule_task → SoulMateScheduler.add_task（SQLite持久化，重启不丢）
→ 后台run_forever轮询 claim_due（到点CAS领取）→ fire_fn 执行任务回合（默认复用
wakeup_executor 的无客户端SoulMateAgent回合 + LLM级失败/零输出判定）→ runs表
落执行日志（ok/failed + detail）→ 按周期表达式排下一次。

设计纪律（对齐 wakeup_executor 已验证模式）：
1. at-most-once：claim 是 SQLite CAS（`UPDATE ... WHERE id=? AND next_run_at_ms=?`
   rowcount==1 即唯一赢家）——多进程/双实例都跑调度也不会双跑双烧；
2. 失败必须可见：fire 异常 → runs 表落 failed+error，**周期任务照常排下一次**
   （重试语义=下个周期，不搞无限即时重试风暴）；执行日志可审计（list_scheduled_tasks）；
3. 确定性 cron 解析不走LLM：5字段（分 时 日 月 周），支持 `*` `*/n` `a` `a-b`
   `a-b/n` 与逗号列表；dom/dow 同时受限时按 Vixie 语义"或"匹配；
4. 时间语义：存储用毫秒UTC（全局时间规范），cron表达式按**本地时间**匹配
   （用户说"每天8点"指本地8点）。

进程归属：调度循环跑在活着的 SoulMateAgent 进程（stdio子进程，start.py 启动时
有未完成任务则自举；schedule_task 时也会拉起）。诚实声明：没有agent进程存活时
任务不执行（到点错过不补跑，next_run推进到下个周期）——常驻托管（独立daemon/
systemd）留作后续切片。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta

logger = logging.getLogger("acp-proxy.soulmate_scheduler")

# 任务回合出身标记（metadata.turn_origin；wakeup_relay 实时推送面据此把 cron 回合
# 行补推给正连前端——3.2 定时汇报已接）
CRON_TURN_ORIGIN = "cron"
CRON_PROMPT_MARKER = "⏰ [定时任务"

_DEFAULT_DB_PATH = os.path.expanduser("~/.hermes/soulmate/memory/cron_tasks.db")
_DB_TIMEOUT_S = 30.0
_MAX_CLAIM_BATCH = 20
_RECENT_CAP = 20
_DETAIL_CAP = 500

# 连续失败N次后在日志detail中显式提醒（不自动禁用——周期任务语义是持续跑）
_FAIL_STREAK_HINT = 3


# ── cron表达式解析（确定性，不走LLM）─────────────────────────────

class CronError(ValueError):
    """cron表达式/调度串非法"""


class _CronField:
    """单字段：`*` `*/n` `a` `a-b` `a-b/n` 逗号列表 → 有序取值集合。"""

    def __init__(self, spec: str, lo: int, hi: int, name: str):
        self.name = name
        values: set[int] = set()
        for part in str(spec).split(","):
            part = part.strip()
            if not part:
                raise CronError(f"cron {name} 字段含空项: {spec!r}")
            step = 1
            if "/" in part:
                part, step_s = part.split("/", 1)
                if not step_s.isdigit() or int(step_s) < 1:
                    raise CronError(f"cron {name} 步长非法: {step_s!r}")
                step = int(step_s)
            if part == "*":
                a, b = lo, hi
            elif re.fullmatch(r"\d+", part):
                a = b = int(part)
            elif re.fullmatch(r"\d+-\d+", part):
                a, b = (int(x) for x in part.split("-"))
            else:
                raise CronError(f"cron {name} 字段非法: {part!r} (全串 {spec!r})")
            if a < lo or b > hi or a > b:
                raise CronError(f"cron {name} 超出范围[{lo},{hi}]: {a}-{b}")
            values.update(range(a, b + 1, step))
        if not values:
            raise CronError(f"cron {name} 字段为空: {spec!r}")
        self.values = frozenset(values)

    def matches(self, v: int) -> bool:
        return v in self.values


class CronExpr:
    """5字段cron：分(0-59) 时(0-23) 日(1-31) 月(1-12) 周(0-6,0=周日;7也接受=周日)。

    dom/dow 语义（Vixie cron）：两者都受限（非`*`）时**任一**匹配即命中；
    否则只看受限的那个。next_after 用字段跳进算法（月→日→时→分逐级推进），
    不逐分钟盲扫。
    """

    def __init__(self, expr: str):
        fields = str(expr).split()
        if len(fields) != 5:
            raise CronError(f"cron表达式须5字段(分 时 日 月 周): {expr!r}")
        self.expr = " ".join(fields)
        self.minute = _CronField(fields[0], 0, 59, "分")
        self.hour = _CronField(fields[1], 0, 23, "时")
        self.dom = _CronField(fields[2], 1, 31, "日")
        self.month = _CronField(fields[3], 1, 12, "月")
        # 周字段接受 0-7（7=周日，与0同义）：按0-7解析后取模归一
        dow_vals = _CronField(fields[4], 0, 7, "周").values
        self.dow = frozenset(v % 7 for v in dow_vals)
        self._dom_any = fields[2].strip().split("/")[0] == "*"
        self._dow_any = fields[4].strip().split("/")[0] in ("*", "0-6", "0-7")

    def _day_matches(self, t: datetime) -> bool:
        d_dom = self.dom.matches(t.day)
        # datetime.weekday(): Mon=0..Sun=6 → cron dow: Sun=0..Sat=6
        d_dow = ((t.weekday() + 1) % 7) in self.dow
        if self._dom_any and self._dow_any:
            return True
        if self._dom_any:
            return d_dow
        if self._dow_any:
            return d_dom
        return d_dom or d_dow

    def matches(self, t: datetime) -> bool:
        return (self.month.matches(t.month) and self._day_matches(t)
                and self.hour.matches(t.hour) and self.minute.matches(t.minute))

    def next_after(self, after: datetime) -> datetime:
        """after 之后的下一个命中时刻（本地时间，分钟粒度）。2年内无命中抛CronError。"""
        t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        limit = after + timedelta(days=732)
        while t <= limit:
            if not self.month.matches(t.month):
                # 跳到下月1日 00:00
                t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(
                    day=1, hour=0, minute=0)
                continue
            if not self._day_matches(t):
                t = (t + timedelta(days=1)).replace(hour=0, minute=0)
                continue
            if not self.hour.matches(t.hour):
                t = (t + timedelta(hours=1)).replace(minute=0)
                continue
            if not self.minute.matches(t.minute):
                t += timedelta(minutes=1)
                continue
            return t
        raise CronError(f"cron表达式2年内无命中时刻: {self.expr!r}")


_INTERVAL_RE = re.compile(r"^interval:\s*(\d+)\s*([smhd]?)$", re.I)
_INTERVAL_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def _interval_seconds(expr: str) -> int:
    """interval调度串 → 秒数（最小10秒）。"""
    m = _INTERVAL_RE.match(str(expr).strip())
    if not m:
        raise CronError(f"interval调度串无法解析: {expr!r}")
    seconds = int(m.group(1)) * _INTERVAL_UNITS[m.group(2).lower()]
    if seconds < 10:
        raise CronError(f"interval 最小10秒: {expr!r}")
    return seconds


def parse_schedule(schedule: str) -> tuple[str, object]:
    """调度串 → (kind, 参数)。

    两种形态：
    - ``interval:300`` / ``interval:5m``（每300秒 / 每5分钟）→ ("interval", 秒数)
    - ``cron:*/5 * * * *``（5字段cron表达式）→ ("cron", CronExpr)
    """
    s = str(schedule or "").strip()
    if _INTERVAL_RE.match(s):
        return "interval", _interval_seconds(s)
    if s.lower().startswith("cron:"):
        return "cron", CronExpr(s[5:].strip())
    raise CronError(
        f"无法解析调度串: {schedule!r}（支持 interval:<秒|5m|1h> 或 cron:<5字段表达式>）")


def _now_ms() -> int:
    return int(time.time() * 1000)


def build_cron_prompt(task: dict) -> str:
    """定时任务回合prompt——显式声明系统自动回合（非用户消息），与唤醒回合同款纪律。"""
    return (
        f"{CRON_PROMPT_MARKER} {task.get('name', '')}｜系统自动回合，非用户消息]\n"
        f"以下是该定时任务的执行内容，请执行：\n"
        f"{task.get('payload', '')}\n"
        f"（周期: {task.get('schedule_expr', '')}｜第{int(task.get('run_count', 0)) + 1}次执行）\n"
        "这是无人值守的定时回合，只做执行内容范围内的事；完成后简要汇报结果。"
    )


# ── 默认执行体：无客户端SoulMateAgent跑一个定时任务回合 ──────────────

async def default_cron_fire(task: dict) -> dict:
    """默认fire_fn：复用 wakeup_executor 的无客户端回合 + 失败判定纪律。

    LLM级失败（provider错误/超时机器标记）与零输出回合都按失败落日志——
    "跑了但什么都没留下"不能记成功（mem0 §1.1）。
    """
    from agent import wakeup_executor as we

    agent = we._get_headless_agent()
    sid = str(task.get("session_id") or "")
    pre_id = we._max_assistant_id(agent, sid)
    agent.set_turn_origin(sid, CRON_TURN_ORIGIN)
    try:
        resp = await agent.prompt(
            [{"type": "text", "text": build_cron_prompt(task)}], sid)
    finally:
        agent.clear_turn_origin(sid)
    stop_reason = str(getattr(resp, "stop_reason", "") or "")
    if stop_reason == "refusal":
        raise RuntimeError("定时任务回合被拒：会话在服务端不存在，拒绝给消失的会话造回合")
    bad = we._llm_failure_reply(agent, sid, pre_id)
    if bad:
        raise RuntimeError("定时任务回合LLM级失败（provider错误/超时机器标记）: " + bad)
    if we._has_turn_output(agent, sid, pre_id) is False:
        raise RuntimeError("定时任务回合零输出（未落库任何非空assistant输出）")
    return {"stop_reason": stop_reason}


class SoulMateScheduler:
    """周期任务调度器：SQLite持久化 + CAS领取 + 执行日志 + 自动排下一次。"""

    def __init__(
        self,
        db_path: str | None = None,
        fire_fn=None,
        poll_interval_s: float | None = None,
        now_fn=None,
    ):
        self._db_path = db_path or os.environ.get("SOULMATE_CRON_DB_PATH") or _DEFAULT_DB_PATH
        self._fire_fn = fire_fn or default_cron_fire
        self._poll_interval_s = float(
            poll_interval_s or float(os.environ.get("SOULMATE_CRON_POLL_S", "30")))
        self._now_fn = now_fn or _now_ms
        self._task: asyncio.Task | None = None
        self._running = False
        self._recent: list[str] = []
        self._stats: dict = {
            "polls": 0, "due_seen": 0, "claimed": 0, "claim_lost": 0,
            "fire_ok": 0, "fire_failed": 0,
        }
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
                CREATE TABLE IF NOT EXISTS tasks(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    schedule_kind TEXT NOT NULL,
                    schedule_expr TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at_ms INTEGER NOT NULL,
                    last_run_at_ms INTEGER,
                    run_count INTEGER NOT NULL DEFAULT 0,
                    fail_streak INTEGER NOT NULL DEFAULT 0,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL,
                    started_at_ms INTEGER NOT NULL,
                    finished_at_ms INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_due
                    ON tasks(enabled, next_run_at_ms);
                CREATE INDEX IF NOT EXISTS idx_runs_task
                    ON runs(task_id, id);
                """)
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _row_task(row: sqlite3.Row) -> dict:
        return dict(row)

    def _compute_next(self, kind: str, param: "int | CronExpr", after_ms: int) -> int:
        if kind == "interval":
            return int(after_ms) + int(param) * 1000
        after_dt = datetime.fromtimestamp(after_ms / 1000)
        return int(param.next_after(after_dt).timestamp() * 1000)

    # ── 任务管理（工具面） ──────────────────────────────────────

    def add_task(self, name: str, schedule: str, payload: str,
                 session_id: str = "") -> dict:
        name = str(name or "").strip()
        payload = str(payload or "").strip()
        if not name:
            raise CronError("任务名不能为空")
        if not payload:
            raise CronError("任务执行内容(payload)不能为空")
        kind, param = parse_schedule(schedule)
        now = self._now_fn()
        schedule_expr = schedule.strip() if kind == "interval" else f"cron:{param.expr}"
        next_run = self._compute_next(kind, param, now)
        db = self._connect()
        try:
            cur = db.execute(
                "INSERT INTO tasks(name, schedule_kind, schedule_expr, payload,"
                " session_id, enabled, next_run_at_ms, run_count, fail_streak,"
                " created_at_ms) VALUES(?,?,?,?,?,1,?,0,0,?)",
                (name, kind, schedule_expr, payload, str(session_id or ""),
                 next_run, now))
            db.commit()
            task_id = int(cur.lastrowid)
        finally:
            db.close()
        self._note(f"➕ 新任务 #{task_id} {name}（{schedule_expr}）")
        return {"task_id": task_id, "name": name, "schedule": schedule_expr,
                "next_run_at_ms": next_run, "session_id": session_id or ""}

    def list_tasks(self, with_runs: bool = True, run_limit: int = 3) -> list[dict]:
        db = self._connect()
        try:
            rows = db.execute("SELECT * FROM tasks ORDER BY id").fetchall()
            tasks = [self._row_task(r) for r in rows]
            if with_runs:
                for t in tasks:
                    rrows = db.execute(
                        "SELECT started_at_ms, finished_at_ms, status, detail"
                        " FROM runs WHERE task_id=? ORDER BY id DESC LIMIT ?",
                        (t["id"], int(run_limit))).fetchall()
                    t["recent_runs"] = [dict(r) for r in rrows]
        finally:
            db.close()
        return tasks

    def cancel_task(self, task_id: int, delete: bool = False) -> tuple[bool, str]:
        db = self._connect()
        try:
            row = db.execute("SELECT id, name, enabled FROM tasks WHERE id=?",
                             (int(task_id),)).fetchone()
            if row is None:
                return False, f"任务不存在: #{task_id}"
            if delete:
                # 任务删除但执行日志保留（runs不删）——审计可追溯
                db.execute("DELETE FROM tasks WHERE id=?", (int(task_id),))
                db.commit()
                self._note(f"🗑️ 删除任务 #{task_id} {row['name']}（执行日志保留）")
                return True, f"已删除任务 #{task_id}（执行日志保留可审计）"
            db.execute("UPDATE tasks SET enabled=0 WHERE id=?", (int(task_id),))
            db.commit()
            self._note(f"⏸️ 停用任务 #{task_id} {row['name']}")
            return True, f"已停用任务 #{task_id}（不再执行；删除用 delete=true）"
        finally:
            db.close()

    # ── 执行引擎 ───────────────────────────────────────────────

    def claim_due(self, now_ms: int | None = None) -> list[dict]:
        """到点任务 CAS 领取：`UPDATE ... WHERE id AND next_run_at_ms` rowcount==1

        即唯一赢家（多进程调度共存不双跑）。领取即把 next_run 推进到下个周期
        （错过不补跑：长停机后按周期表取下一次，不积压重放）。
        """
        now = int(now_ms if now_ms is not None else self._now_fn())
        claimed: list[dict] = []
        db = self._connect()
        try:
            rows = db.execute(
                "SELECT * FROM tasks WHERE enabled=1 AND next_run_at_ms<=?"
                " ORDER BY next_run_at_ms LIMIT ?",
                (now, _MAX_CLAIM_BATCH)).fetchall()
            for row in rows:
                t = self._row_task(row)
                self._stats["due_seen"] += 1
                try:
                    if t["schedule_kind"] == "interval":
                        next_run = self._compute_next(
                            "interval", _interval_seconds(t["schedule_expr"]), now)
                    else:
                        next_run = self._compute_next(
                            "cron", CronExpr(t["schedule_expr"][5:]), now)
                except Exception as e:
                    # 周期表损坏：停用该任务防坏任务刷屏，失败可见
                    db.execute("UPDATE tasks SET enabled=0 WHERE id=?",
                               (t["id"],))
                    db.commit()
                    self._note(f"⚠️ 任务#{t['id']}周期表无法解析已停用: {e}")
                    continue
                cur = db.execute(
                    "UPDATE tasks SET next_run_at_ms=?, last_run_at_ms=?,"
                    " run_count=run_count+1 WHERE id=? AND enabled=1"
                    " AND next_run_at_ms=?",
                    (next_run, now, t["id"], t["next_run_at_ms"]))
                db.commit()
                if cur.rowcount == 1:
                    t["next_run_at_ms"] = next_run
                    t["last_run_at_ms"] = now
                    t["run_count"] = int(t["run_count"]) + 1
                    claimed.append(t)
                    self._stats["claimed"] += 1
                else:
                    self._stats["claim_lost"] += 1
        finally:
            db.close()
        return claimed

    async def _fire_one(self, task: dict) -> tuple[str, str]:
        started = self._now_fn()
        try:
            result = self._fire_fn(task)
            if asyncio.iscoroutine(result):
                result = await result
            detail = str(result.get("stop_reason", "")) if isinstance(result, dict) \
                else str(result)
            return "ok", detail[:_DETAIL_CAP]
        except Exception as e:
            return "failed", f"{type(e).__name__}: {e}"[:_DETAIL_CAP]
        finally:
            _ = started  # started/finished 同毫秒级记录由调用方落库

    def _log_run(self, task_id: int, started_ms: int, status: str, detail: str) -> None:
        db = self._connect()
        try:
            db.execute(
                "INSERT INTO runs(task_id, started_at_ms, finished_at_ms, status,"
                " detail) VALUES(?,?,?,?,?)",
                (int(task_id), int(started_ms), self._now_fn(), status, detail))
            if status == "ok":
                db.execute("UPDATE tasks SET fail_streak=0 WHERE id=?", (int(task_id),))
            else:
                db.execute(
                    "UPDATE tasks SET fail_streak=fail_streak+1 WHERE id=?",
                    (int(task_id),))
            db.commit()
        except Exception as e:  # 日志写失败不得阻塞任务链路
            logger.warning("[cron] 执行日志落库失败（不阻塞）: %s", e)
        finally:
            db.close()

    async def run_due(self, now_ms: int | None = None) -> int:
        """执行一轮到点任务：领取→执行→落日志。返回本轮执行条数。"""
        self._stats["polls"] += 1
        claimed = self.claim_due(now_ms)
        for task in claimed:
            started = self._now_fn()
            status, detail = await self._fire_one(task)
            self._log_run(task["id"], started, status, detail)
            if status == "ok":
                self._stats["fire_ok"] += 1
                self._note(f"✅ 执行 #{task['id']} {task['name']} → {detail[:60]}")
            else:
                self._stats["fire_failed"] += 1
                hint = ""
                if int(task.get("fail_streak", 0)) + 1 >= _FAIL_STREAK_HINT:
                    hint = f"（连续失败{int(task.get('fail_streak', 0)) + 1}次，请关注）"
                self._note(f"❌ 执行 #{task['id']} {task['name']} 失败: {detail[:80]}{hint}")
            self._report_run(task, status, detail)
        return len(claimed)

    def _report_run(self, task: dict, status: str, detail: str) -> None:
        """3.2 定时汇报：执行结果（进展/异常）汇报给 OpenMate——落 opensoul.db
        汇报行（wakeup_relay 推给正连前端）；汇报失败绝不阻塞调度链路。"""
        try:
            from agent.cron_reporter import CronReporter
            if not hasattr(self, "_reporter") or self._reporter is None:
                self._reporter = CronReporter()
            fail_streak = 0 if status == "ok" else int(task.get("fail_streak", 0)) + 1
            self._reporter.report(
                task, status, detail,
                next_run_ms=task.get("next_run_at_ms"),
                fail_streak=fail_streak,
            )
        except Exception as e:  # 汇报面异常不得阻塞 fire/日志/排下次
            logger.warning("[cron] 定时汇报失败（不阻塞调度）: %s", e)

    def _note(self, msg: str) -> None:
        self._recent.append(f"{datetime.now().strftime('%H:%M:%S')} {msg}")
        del self._recent[:-_RECENT_CAP]
        logger.info("[cron] %s", msg)

    def snapshot(self) -> dict:
        return {"stats": dict(self._stats), "recent": list(self._recent),
                "running": self._running, "db_path": self._db_path}

    # ── 后台循环 ───────────────────────────────────────────────

    async def run_forever(self) -> None:
        self._running = True
        try:
            while self._running:
                try:
                    await self.run_due()
                except Exception as e:  # 单轮异常不退出循环
                    logger.warning("[cron] run_due 异常（循环继续）: %s", e)
                await asyncio.sleep(self._poll_interval_s)
        finally:
            self._running = False

    def ensure_started(self) -> bool:
        """拉起后台调度循环（幂等；无运行中事件循环时不拉起，返回False）。"""
        if self._running or self._task is not None:
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._task = loop.create_task(self.run_forever())
        logger.info("[cron] 调度循环已启动（poll=%ss, db=%s）",
                    self._poll_interval_s, self._db_path)
        return True

    def has_enabled_tasks(self) -> bool:
        db = self._connect()
        try:
            row = db.execute(
                "SELECT COUNT(*) FROM tasks WHERE enabled=1").fetchone()
            return bool(row and int(row[0]) > 0)
        finally:
            db.close()

    def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None


# ── 单例（get_wakeup_executor 同款惯例） ──────────────────────────

_scheduler: SoulMateScheduler | None = None


def get_scheduler() -> SoulMateScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = SoulMateScheduler()
    return _scheduler
