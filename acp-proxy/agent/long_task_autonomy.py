"""
P0 长任务自治核心（Long-Task Autonomy Core）——防空转 · 断点续跑 · 过程可回放 · 完成状态多档。

对标天花板并取长：
- Manus   ：断点续跑 + 过程可回放（每步输入/输出/决策可回看可审计）→ TraceRecorder/CheckpointStore。
- OMH     ：完成状态 phase×confidence 双轴 + 三道闸防"自报即完成"→ CompletionConfidence /
            CompletionIntegrityChecker / verify（verified ≠ reported_done）。
- Paperclip：心跳/唤醒三态 + 防饿死（wall-clock 超时不杀健康长活）→ ProgressMonitor 三态降级。
- Multica ：判死不看单一时长、看"是否仍在进展"（网络分区不误杀）→ 进展签名驱动的防空转判据。

设计铁律（与既有 acp-proxy 一致）：
- 全 stdlib，无第三方依赖；增量可复用，不推倒既有 task_engine/loop。
- 持久化一律落 acp-proxy/data/（运行时数据目录），进程重启/被杀后可恢复。
- 一切状态翻转走 CAS（compare-and-set）语义，崩溃安全；"先认领后关单"。
- fail-closed：未知/不可信状态永不升格为"已完成/已验证"。

本模块自成闭环、可独立跑通 kill→resume→replay 演示，同时提供 LongTaskController
薄适配器供 soulmate_agent 真实步骤循环接线（写了≠接线了，故两条路都通）。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# ── 运行时数据目录（acp-proxy/data/）────────────────────────────────────
DATA_DIR = Path(os.environ.get(
    "ACP_PROXY_DATA_DIR",
    str(Path(__file__).resolve().parent.parent / "data"),
))
DB_PATH = Path(os.environ.get(
    "ACP_LONG_TASK_DB",
    str(DATA_DIR / "long_task_autonomy.db"),
))


# ══════════════════════════════════════════════════════════════════════
# 1. 完成状态多档（OMH：自报 ≠ 完成，verified ≠ reported_done）
# ══════════════════════════════════════════════════════════════════════

class CompletionConfidence(str, Enum):
    """OMH confidence 词表（labels.py 语义移植）。fail-closed：未知→NOT_RUN。"""
    NOT_RUN = "not_run"                 # 什么都没跑
    RUNNING = "running"                 # 正在跑
    PARTLY_SEEN = "partly_seen"         # 见到部分过程
    REPORTED_DONE = "reported_done"     # 执行者自报完成，结果**未**核验
    SEEN = "seen"                       # 目睹结束，未核验
    FAILED = "failed"                   # 明确失败
    VERIFIED = "verified"               # 已核验且通过（最高非终态证据档）
    BLOCKED = "blocked"                 # 阻塞，等外部
    CANCELLED = "cancelled"             # 取消

    @classmethod
    def coerce(cls, raw) -> "CompletionConfidence":
        # 已是枚举成员直接返回（避免 str(str-Enum)='CompletionConfidence.X' 往返断裂）
        if isinstance(raw, cls):
            return raw
        v = str(raw or "").strip().lower()
        # 兼容 str(enum)='CompletionConfidence.REPORTED_DONE' 旧形态
        if "." in v:
            v = v.split(".")[-1]
        for c in cls:
            if c.value == v:
                return c
        # 兼容 wire 值 → 置信映射（OMH :138-161）
        wire = {
            "completed": cls.REPORTED_DONE,
            "prepared_not_observed": cls.NOT_RUN,
            "routing_not_execution": cls.NOT_RUN,
            "observed": cls.SEEN,
            "passed": cls.VERIFIED,
            "pending": cls.NOT_RUN,
            "unknown": cls.NOT_RUN,
            "ready": cls.NOT_RUN,          # "ready"不是已发生
            "complete": cls.REPORTED_DONE,
            "done": cls.REPORTED_DONE,
        }
        return wire.get(v, cls.NOT_RUN)   # fail-closed


# 词表闸（OMH 三道闸①）：这些词不许当"已核验"的证据
_UNBACKED_CLAIM_WORDS = {"ready", "complete", "completed", "passed", "merged", "done", "finished", "ok", "success", "successful"}
_UNFINISHED_MARKERS = ("todo", "fixme", "wip", "tbd", "not implemented", "placeholder", "stub", "coming soon")
# 只要证据里出现"issue/tracked/链接"即视为受追踪的未完事项（不是真空口白话）
_TRACKED_EXCEPTION = ("tracked", "http://", "https://", "#", "issue", "ticket", "见需求", "跟踪")


@dataclass
class CompletionClaim:
    """一份完成声明 + 内容级证据 + 具名核验命令（OMH 三道闸的载体）。"""
    status: CompletionConfidence = CompletionConfidence.REPORTED_DONE
    evidence: list[str] = field(default_factory=list)          # 内容级证据（产物/哈希/命令输出）
    verification_command: str = ""                              # 具名核验命令（非空才有资格 verified）
    verification_result: str = ""                               # 核验命令真实输出
    verifier: str = "executor"                                  # executor=自报；external=非执行者核验
    claim_text: str = ""                                        # 声明原文（供完整性分类器扫描）

    def content_backed(self) -> bool:
        """是否有内容级证据（非空、非纯自指）。"""
        real = [e for e in self.evidence if e and e.strip()]
        return bool(real) and not all(_is_self_referential(e) for e in real)


def _is_self_referential(text: str) -> bool:
    """自指证据：只是复述"完成了"，没有可核对的产物内容。"""
    t = (text or "").strip().lower()
    if not t:
        return True
    # 短且全是声明词 → 自指
    tokens = set(t.split())
    if len(t) < 40 and tokens and tokens.issubset(_UNBACKED_CLAIM_WORDS | {"i", "it", "the", "task", "was", "is", "now", "has", "been"}):
        return True
    # 明示自指句式
    if t in ("done", "completed", "finished", "ok", "success"):
        return True
    return False


class CompletionIntegrityChecker:
    """OMH 三道闸②：完成完整性分类器（纯文本分类，不跑命令不读文件）。

    对"完成声明"过若干拒绝规则；命中任一条 → 该声明**不得**升格 verified。
    移植自 OMH src/quality/completion_integrity.py 的 9 类规则（取与本引擎相关的子集）。
    """

    RULES = (
        "empty_evidence",
        "self_referential_evidence",
        "unproven_claim_word",
        "unfinished_work_marker",
        "unnamed_verification_command",
        "stub_implementation",
        "prepared_evidence_claimed_as_observed",
        "self_reporter_as_verifier",
    )

    def check(self, claim: CompletionClaim) -> list[str]:
        """返回命中的拒绝理由（空列表=通过完整性检查）。"""
        reasons: list[str] = []
        blob = " ".join([claim.claim_text, claim.verification_result] + list(claim.evidence)).lower()

        # empty_evidence：没有内容级证据
        if not claim.content_backed():
            reasons.append("empty_evidence")

        # self_referential_evidence：证据全为自指
        real = [e for e in claim.evidence if e and e.strip()]
        if real and all(_is_self_referential(e) for e in real):
            reasons.append("self_referential_evidence")

        # unnamed_verification_command：自称 verified 却没有具名核验命令
        if claim.status == CompletionConfidence.VERIFIED and not (claim.verification_command or "").strip():
            reasons.append("unnamed_verification_command")

        # self_reporter_as_verifier：执行者自己当核验者却标 verified（OMH：须非执行者）
        if claim.status == CompletionConfidence.VERIFIED and claim.verifier == "executor":
            reasons.append("self_reporter_as_verifier")

        # unproven_claim_word：仅用 ready/complete/passed/merged 充当证据
        if not claim.verification_result.strip():
            if any(_is_self_referential(e) for e in real):
                reasons.append("unproven_claim_word")

        # unfinished_work_marker：产物里残留 TODO/FIXME/WIP/TBD（带 issue/链接豁免）
        for marker in _UNFINISHED_MARKERS:
            if marker in blob:
                if not any(exc in blob for exc in _TRACKED_EXCEPTION):
                    reasons.append("unfinished_work_marker")
                    break

        # stub_implementation：明示占位/未实现
        if any(w in blob for w in ("stub", "placeholder", "not implemented", "coming soon")):
            reasons.append("stub_implementation")

        # prepared_evidence_claimed_as_observed：核验结果写着"已准备/将要"却当已观测
        if claim.status in (CompletionConfidence.SEEN, CompletionConfidence.VERIFIED):
            if any(w in claim.verification_result.lower() for w in ("prepared", "will ", "going to", "计划", "准备", "将要")):
                reasons.append("prepared_evidence_claimed_as_observed")

        # 去重保序
        seen, out = set(), []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                out.append(r)
        return out


def evaluate_completion(claim: CompletionClaim) -> tuple[CompletionConfidence, list[str]]:
    """综合裁决完成档位。原则：自报封顶 reported_done；要 verified 必须：
    (a) 非执行者核验 verifier != executor，(b) 具名核验命令 + 真实通过输出，
    (c) 内容级证据，(d) 完整性分类器零命中。否则 fail-closed 降档。"""
    checker = CompletionIntegrityChecker()
    reasons = checker.check(claim)

    # 自报一律封顶 reported_done（OMH §3.2）
    if claim.status in (CompletionConfidence.REPORTED_DONE, CompletionConfidence.SEEN):
        return CompletionConfidence.REPORTED_DONE, ["self_report_capped_at_reported_done"]

    if claim.status == CompletionConfidence.VERIFIED:
        # verified 资格四条件缺一不可
        ok = (
            claim.verifier != "executor"
            and bool((claim.verification_command or "").strip())
            and bool((claim.verification_result or "").strip())
            and claim.content_backed()
            and not reasons
        )
        if ok:
            return CompletionConfidence.VERIFIED, []
        # 不满足 → 降档到 reported_done 并保留失败理由
        return CompletionConfidence.REPORTED_DONE, ["verified_demoted"] + reasons

    # 其余（not_run/running/failed/blocked/cancelled）按原样
    return claim.status, reasons


# ══════════════════════════════════════════════════════════════════════
# 2. 防空转：心跳 + 进展检测（Paperclip 三态 + Multica 判据）
# ══════════════════════════════════════════════════════════════════════

class ProgressLevel(str, Enum):
    OK = "ok"                 # 在进展
    WARNING = "warning"       # N 分钟无实质进展 → 自检（nudge）
    STALLED = "stalled"       # 更久无进展 → 降级（换法/缩范围）
    DEGRADED = "degraded"     # 彻底空转 → 上报人类，停止空转烧钱


@dataclass
class ProgressAssessment:
    level: ProgressLevel
    reason: str
    seconds_no_progress: float
    action: str            # noop | self_check | degrade | escalate_report
    loop_detected: bool = False


@dataclass
class ProgressMonitor:
    """心跳 + 实质进展检测的防空转状态机。

    关键判据（Multica FailStaleTasks 语义移植）：**判"卡死"看的是多久没有实质进展，
    而不是总共跑了多久**。一个持续推进（进展签名在变）的慢任务永远不会被误判空转——
    这正是"wall-clock 超时不杀健康长活（网络分区不误杀多小时工作）"的落地。

    Paperclip 三态降级：ok→warning(自检)→stalled(降级)→degraded(上报)。
    另叠加"环路检测"：同一进展签名反复出现（原地打转）直接拉满降级。
    """
    warn_after_s: float = 120.0        # 无实质进展 N 秒 → warning（自检）
    stall_after_s: float = 300.0       # → stalled（降级）
    degrade_after_s: float = 600.0     # → degraded（上报人类）
    loop_repeat_threshold: int = 3     # 同签名连续 N 次=环路
    _last_progress_ts: float = field(default_factory=time.time)
    _last_signature: str = ""
    _sig_repeat: int = 0
    _last_level: ProgressLevel = ProgressLevel.OK

    def heartbeat(self, signature: str = "", substantive: bool = False, now: Optional[float] = None) -> ProgressAssessment:
        """喂一次心跳。signature=本次进展的指纹（如步骤id+输出哈希）；substantive=True
        表示这是一次**实质进展**（推进了计划），会重置空转时钟。"""
        now = now if now is not None else time.time()

        if signature and signature == self._last_signature:
            self._sig_repeat += 1
        else:
            self._sig_repeat = 0 if signature else self._sig_repeat
        self._last_signature = signature or self._last_signature

        if substantive:
            self._last_progress_ts = now

        return self.assess(now=now)

    def assess(self, now: Optional[float] = None) -> ProgressAssessment:
        now = now if now is not None else time.time()
        idle = now - self._last_progress_ts
        loop = self._sig_repeat >= self.loop_repeat_threshold

        # 环路：无论时长，直接拉满到 degraded（原地打转=永不进展）
        if loop and idle > self.warn_after_s:
            self._last_level = ProgressLevel.DEGRADED
            return ProgressAssessment(
                ProgressLevel.DEGRADED,
                f"loop_detected: signature '{self._last_signature[:40]}' repeated {self._sig_repeat}x without progress",
                idle, "escalate_report", loop_detected=True,
            )

        if idle >= self.degrade_after_s:
            lvl, action = ProgressLevel.DEGRADED, "escalate_report"
        elif idle >= self.stall_after_s:
            lvl, action = ProgressLevel.STALLED, "degrade"
        elif idle >= self.warn_after_s:
            lvl, action = ProgressLevel.WARNING, "self_check"
        else:
            lvl, action = ProgressLevel.OK, "noop"

        self._last_level = lvl
        reason = f"{idle:.0f}s since last substantive progress"
        return ProgressAssessment(lvl, reason, idle, action)


# ══════════════════════════════════════════════════════════════════════
# 3. 断点续跑：检查点落盘 data/（Manus + Paperclip crash-safe resume）
# ══════════════════════════════════════════════════════════════════════

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"

    @classmethod
    def coerce(cls, raw) -> "StepStatus":
        v = str(raw or "").lower().split(".")[-1]
        for s in cls:
            if s.value == v:
                return s
        return cls.PENDING


@dataclass
class StepState:
    run_id: str
    step_id: str
    seq: int
    description: str = ""
    status: StepStatus = StepStatus.PENDING
    confidence: CompletionConfidence = CompletionConfidence.NOT_RUN
    attempts: int = 0
    input_data: dict = field(default_factory=dict)
    output_data: dict = field(default_factory=dict)
    decision: str = ""
    evidence: list[str] = field(default_factory=list)
    verification_command: str = ""
    verification_result: str = ""
    started_at: float = 0.0
    completed_at: float = 0.0
    dur_ms: float = 0.0


@dataclass
class RunState:
    run_id: str
    task_id: str
    goal: str
    status: str = "running"          # running|completed|failed|blocked|cancelled
    confidence: CompletionConfidence = CompletionConfidence.RUNNING
    total_steps: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    steps: list[StepState] = field(default_factory=list)

    def completed_steps(self) -> list[StepState]:
        return [s for s in self.steps if s.status in (StepStatus.SUCCESS, StepStatus.SKIPPED)]

    def next_step(self) -> Optional[StepState]:
        for s in sorted(self.steps, key=lambda x: x.seq):
            if s.status in (StepStatus.PENDING, StepStatus.RUNNING, StepStatus.FAILED):
                return s
        return None


class CheckpointStore:
    """SQLite 检查点库（data/long_task_autonomy.db）。崩溃安全：
    - 状态翻转走 CAS（WHERE 上一状态），部分写永不产生"谎报完成"。
    - 先认领后关单：步骤先置 running（认领），成功/失败才置终态。
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS long_task_runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT,
                    goal TEXT,
                    status TEXT DEFAULT 'running',
                    confidence TEXT DEFAULT 'running',
                    total_steps INTEGER DEFAULT 0,
                    created_at REAL, updated_at REAL
                )""")
            c.execute("""
                CREATE TABLE IF NOT EXISTS long_task_steps (
                    run_id TEXT, step_id TEXT, seq INTEGER,
                    description TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    confidence TEXT DEFAULT 'not_run',
                    attempts INTEGER DEFAULT 0,
                    input_json TEXT DEFAULT '{}',
                    output_json TEXT DEFAULT '{}',
                    decision TEXT DEFAULT '',
                    evidence_json TEXT DEFAULT '[]',
                    verification_command TEXT DEFAULT '',
                    verification_result TEXT DEFAULT '',
                    started_at REAL DEFAULT 0,
                    completed_at REAL DEFAULT 0,
                    dur_ms REAL DEFAULT 0,
                    PRIMARY KEY (run_id, step_id)
                )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_lts_run ON long_task_steps(run_id, seq)")

    # ── run CRUD ──
    def create_run(self, task_id: str, goal: str, steps: list[dict], run_id: str | None = None) -> RunState:
        run_id = run_id or f"run-{uuid.uuid4().hex[:10]}"
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO long_task_runs(run_id,task_id,goal,status,confidence,total_steps,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (run_id, task_id, goal, "running", CompletionConfidence.RUNNING.value, len(steps), now, now),
            )
            for i, s in enumerate(steps):
                c.execute(
                    "INSERT OR REPLACE INTO long_task_steps(run_id,step_id,seq,description,status,confidence,input_json)"
                    " VALUES(?,?,?,?,?,?,?)",
                    (run_id, str(s.get("id", f"step_{i}")), i, str(s.get("description", "")),
                     StepStatus.PENDING.value, CompletionConfidence.NOT_RUN.value,
                     json.dumps(s.get("input", {}), ensure_ascii=False)),
                )
        return self.load_run(run_id)  # type: ignore

    def load_run(self, run_id: str) -> Optional[RunState]:
        with self._conn() as c:
            r = c.execute("SELECT * FROM long_task_runs WHERE run_id=?", (run_id,)).fetchone()
            if not r:
                return None
            run = RunState(
                run_id=r["run_id"], task_id=r["task_id"], goal=r["goal"],
                status=r["status"], confidence=CompletionConfidence.coerce(r["confidence"]),
                total_steps=r["total_steps"], created_at=r["created_at"], updated_at=r["updated_at"],
            )
            rows = c.execute("SELECT * FROM long_task_steps WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
            for s in rows:
                run.steps.append(StepState(
                    run_id=s["run_id"], step_id=s["step_id"], seq=s["seq"], description=s["description"],
                    status=StepStatus.coerce(s["status"]), confidence=CompletionConfidence.coerce(s["confidence"]),
                    attempts=s["attempts"], input_data=json.loads(s["input_json"] or "{}"),
                    output_data=json.loads(s["output_json"] or "{}"), decision=s["decision"],
                    evidence=json.loads(s["evidence_json"] or "[]"),
                    verification_command=s["verification_command"], verification_result=s["verification_result"],
                    started_at=s["started_at"], completed_at=s["completed_at"], dur_ms=s["dur_ms"],
                ))
            return run

    def find_resumable(self, task_id: str) -> Optional[RunState]:
        """找该任务最近一个可续跑的 run（断点续跑目标）。

        可续跑 = 未收尾（running/failed/blocked）且仍有未完成步。失败 run 也须可续跑
        （需求："进程重启/失败后从检查点恢复，不从头再来"）；已 completed/cancelled 不续。
        """
        with self._conn() as c:
            rows = c.execute(
                "SELECT run_id, status FROM long_task_runs WHERE task_id=?"
                " AND status IN ('running','failed','blocked')"
                " ORDER BY created_at DESC LIMIT 5", (task_id,)).fetchall()
        for r in rows:
            run = self.load_run(r["run_id"])
            if run is not None and run.next_step() is not None:
                return run
        return None

    # ── CAS 状态翻转 ──
    def claim_step(self, run_id: str, step_id: str, input_data: dict) -> bool:
        """先认领后关单：置 running（CAS 仅当 PENDING/FAILED）。返回是否认领成功。"""
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE long_task_steps SET status=?, confidence=?, started_at=?, attempts=attempts+1, input_json=?"
                " WHERE run_id=? AND step_id=? AND status IN (?,?)",
                (StepStatus.RUNNING.value, CompletionConfidence.RUNNING.value, now,
                 json.dumps(input_data, ensure_ascii=False), run_id, step_id,
                 StepStatus.PENDING.value, StepStatus.FAILED.value),
            )
            return cur.rowcount == 1

    def settle_step(self, run_id: str, step_id: str, *, success: bool,
                    output_data: dict | None = None, decision: str = "",
                    evidence: list[str] | None = None,
                    verification_command: str = "", verification_result: str = "",
                    confidence: CompletionConfidence | None = None,
                    dur_ms: float = 0.0) -> bool:
        now = time.time()
        status = StepStatus.SUCCESS if success else StepStatus.FAILED
        conf = confidence or (
            CompletionConfidence.REPORTED_DONE if success else CompletionConfidence.FAILED
        )
        with self._conn() as c:
            cur = c.execute(
                "UPDATE long_task_steps SET status=?, confidence=?, output_json=?, decision=?,"
                " evidence_json=?, verification_command=?, verification_result=?, completed_at=?, dur_ms=?"
                " WHERE run_id=? AND step_id=? AND status=?",
                (status.value, conf.value, json.dumps(output_data or {}, ensure_ascii=False),
                 decision, json.dumps(evidence or [], ensure_ascii=False),
                 verification_command, verification_result, now, dur_ms,
                 run_id, step_id, StepStatus.RUNNING.value),
            )
            return cur.rowcount == 1

    def set_run_status(self, run_id: str, status: str, confidence: CompletionConfidence) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE long_task_runs SET status=?, confidence=?, updated_at=? WHERE run_id=?",
                (status, confidence.value, time.time(), run_id),
            )

    def record_recovery(self, run_id: str) -> int:
        """恢复扫描（Paperclip 恢复扫描兜底）：把"上次进程被杀时卡在 running"的
        步骤 fail-safe 回退到 pending（至少一次重跑优于永久卡死），返回回退条数。"""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE long_task_steps SET status=? WHERE run_id=? AND status=?",
                (StepStatus.PENDING.value, run_id, StepStatus.RUNNING.value),
            )
            return cur.rowcount


# ══════════════════════════════════════════════════════════════════════
# 4. 过程可回放：执行轨迹（Manus 每步输入/输出/决策可回看可审计）
# ══════════════════════════════════════════════════════════════════════

@dataclass
class TraceEvent:
    seq: int
    run_id: str
    step_id: str
    ts: float
    phase: str            # step_start | step_decision | step_end | heartbeat | recovery | verify | run_end
    event: str            # 语义事件名
    input_data: dict = field(default_factory=dict)
    output_data: dict = field(default_factory=dict)
    decision: str = ""
    status: str = ""
    evidence: list[str] = field(default_factory=list)
    dur_ms: float = 0.0
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


class TraceRecorder:
    """结构化执行轨迹：每步输入/输出/决策/时长/状态/证据落 SQLite（data/），可回放审计。

    对标 Manus 过程回放：get_timeline() 给 UI/API 完整有序轨迹；replay() 给人类可读
    时间线。append-only + 严格递增 seq，回放即按 seq 还原当时决策链。
    """

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(str(self.db_path), timeout=30)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS long_task_trace (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT, step_id TEXT, ts REAL,
                    phase TEXT, event TEXT,
                    input_json TEXT DEFAULT '{}', output_json TEXT DEFAULT '{}',
                    decision TEXT DEFAULT '', status TEXT DEFAULT '',
                    evidence_json TEXT DEFAULT '[]', dur_ms REAL DEFAULT 0,
                    extra_json TEXT DEFAULT '{}'
                )""")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ltt_run ON long_task_trace(run_id, seq)")

    def record(self, run_id: str, step_id: str, phase: str, event: str,
               input_data: dict | None = None, output_data: dict | None = None,
               decision: str = "", status: str = "", evidence: list[str] | None = None,
               dur_ms: float = 0.0, extra: dict | None = None) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO long_task_trace(run_id,step_id,ts,phase,event,input_json,output_json,"
                " decision,status,evidence_json,dur_ms,extra_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, step_id, time.time(), phase, event,
                 json.dumps(input_data or {}, ensure_ascii=False),
                 json.dumps(output_data or {}, ensure_ascii=False),
                 decision, status, json.dumps(evidence or [], ensure_ascii=False),
                 dur_ms, json.dumps(extra or {}, ensure_ascii=False)),
            )
            return int(cur.lastrowid or 0)

    def get_timeline(self, run_id: str) -> list[dict]:
        """完整有序轨迹（供 UI/API 回放）：每步输入/输出/决策/状态/证据。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM long_task_trace WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        out = []
        for r in rows:
            out.append({
                "seq": r["seq"], "run_id": r["run_id"], "step_id": r["step_id"],
                "ts": r["ts"], "phase": r["phase"], "event": r["event"],
                "input_data": json.loads(r["input_json"] or "{}"),
                "output_data": json.loads(r["output_json"] or "{}"),
                "decision": r["decision"], "status": r["status"],
                "evidence": json.loads(r["evidence_json"] or "[]"),
                "dur_ms": r["dur_ms"], "extra": json.loads(r["extra_json"] or "{}"),
            })
        return out

    def replay(self, run_id: str, max_output_chars: int = 120) -> str:
        """人类可读时间线（对标 Manus 回放体验）。"""
        tl = self.get_timeline(run_id)
        if not tl:
            return f"(no trace for run {run_id})"
        lines = [f"=== Replay: {run_id} ({len(tl)} events) ==="]
        for e in tl:
            stamp = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            head = f"[{e['seq']:>3}] {stamp} {e['phase']:<13} {e['event']}"
            if e["step_id"]:
                head += f"  ({e['step_id']})"
            lines.append(head)
            if e["input_data"]:
                lines.append(f"        in : {_short(e['input_data'], max_output_chars)}")
            if e["output_data"]:
                lines.append(f"        out: {_short(e['output_data'], max_output_chars)}")
            if e["decision"]:
                lines.append(f"        decision: {e['decision']}")
            if e["evidence"]:
                lines.append(f"        evidence: {_short(e['evidence'], max_output_chars)}")
            if e["status"]:
                lines.append(f"        status: {e['status']}")
        return "\n".join(lines)


def _short(obj: Any, n: int) -> str:
    s = json.dumps(obj, ensure_ascii=False) if isinstance(obj, (dict, list)) else str(obj)
    return s if len(s) <= n else s[:n] + "…"


# ══════════════════════════════════════════════════════════════════════
# 5. 长任务引擎：串起 检查点 + 心跳 + 轨迹 + 完成状态
# ══════════════════════════════════════════════════════════════════════

class LongTaskEngine:
    """长任务自治执行器。run(step_runner) 逐步执行：
    - 每步执行前 claim（CAS 认领）+ 轨迹 step_start；
    - 执行后 settle + 轨迹 step_end（含输入/输出/决策/证据/时长）+ 心跳(实质进展)；
    - 每步落检查点 → 进程被杀/重启后 resume() 从首个未完成步续跑，不从头来；
    - 全程 ProgressMonitor 防空转（N 分钟无进展=自检/降级/上报，而非无限转圈）；
    - 终局跑 evaluate_completion + 完整性分类器 → 多档完成状态（自报≠verified）。

    step_runner: fn(StepState) -> dict  返回 {output, decision, evidence,
    verification_command, verification_result, success, confidence?}
    """

    def __init__(self, task_id: str, goal: str,
                 store: CheckpointStore | None = None,
                 tracer: TraceRecorder | None = None,
                 monitor: ProgressMonitor | None = None):
        self.task_id = task_id
        self.goal = goal
        self.store = store or CheckpointStore()
        self.tracer = tracer or TraceRecorder(db_path=self.store.db_path)
        self.monitor = monitor or ProgressMonitor()
        self.run: RunState | None = None

    # ── 生命周期 ──
    def start(self, steps: list[dict], run_id: str | None = None) -> RunState:
        self.run = self.store.create_run(self.task_id, self.goal, steps, run_id=run_id)
        self.tracer.record(self.run.run_id, "", "run_start", "run_started",
                           input_data={"goal": self.goal, "steps": len(steps)},
                           extra={"run_id": self.run.run_id})
        return self.run

    def resume(self) -> RunState:
        """断点续跑入口：找回未收尾 run，恢复扫描回退 running→pending，续跑。"""
        run = self.store.find_resumable(self.task_id)
        if run is None:
            raise LookupError(f"no resumable run for task {self.task_id}")
        self.run = run
        recovered = self.store.record_recovery(run.run_id)  # 卡 running 的步骤回退 pending
        self.tracer.record(run.run_id, "", "recovery", "resume_from_checkpoint",
                           decision=f"resumed run {run.run_id}; recovered {recovered} in-flight steps",
                           extra={"recovered_steps": recovered, "completed": len(run.completed_steps())})
        return run

    def run_steps(self, step_runner: Callable[[StepState], dict],
                  on_progress: Callable[[ProgressAssessment], None] | None = None) -> RunState:
        assert self.run is not None, "call start()/resume() first"
        run = self.run
        run_id = run.run_id

        for step in sorted(run.steps, key=lambda x: x.seq):
            if step.status in (StepStatus.SUCCESS, StepStatus.SKIPPED):
                continue  # 断点续跑：已完成步不再重做

            ok_claim = self.store.claim_step(run_id, step.step_id, step.input_data)
            if not ok_claim:
                continue  # 已被并发认领/已完成，跳过（CAS 语义）
            step.status = StepStatus.RUNNING
            t0 = time.time()
            self.tracer.record(run_id, step.step_id, "step_start", "step_started",
                               input_data=step.input_data,
                               decision=f"seq={step.seq} desc={step.description}")

            try:
                result = step_runner(step) or {}
            except Exception as e:  # noqa: BLE001
                result = {"success": False, "output": {"error": str(e)},
                          "decision": "step_runner_raised", "evidence": []}

            dur_ms = (time.time() - t0) * 1000.0
            success = bool(result.get("success", False))
            output = result.get("output", {}) or {}
            decision = result.get("decision", "") or ""
            evidence = result.get("evidence", []) or []
            vcmd = result.get("verification_command", "") or ""
            vres = result.get("verification_result", "") or ""

            # 完成状态裁决（OMH）：自报封顶 reported_done；verified 须非执行者+内容证据
            claim = CompletionClaim(
                status=CompletionConfidence.coerce(result.get("confidence",
                             CompletionConfidence.REPORTED_DONE if success else CompletionConfidence.FAILED)),
                evidence=evidence, verification_command=vcmd, verification_result=vres,
                verifier=result.get("verifier", "executor"),
                claim_text=decision,
            )
            conf, conf_reasons = evaluate_completion(claim)

            self.store.settle_step(
                run_id, step.step_id, success=success, output_data=output,
                decision=decision, evidence=evidence,
                verification_command=vcmd, verification_result=vres,
                confidence=conf, dur_ms=dur_ms,
            )
            self.tracer.record(run_id, step.step_id, "step_end", "step_finished",
                               output_data=output, decision=decision, status=conf.value,
                               evidence=evidence, dur_ms=dur_ms,
                               extra={"success": success, "confidence_reasons": conf_reasons})

            # 实质进展心跳（推进计划=实质进展，重置空转时钟）
            sig = f"{step.step_id}:{hashlib.sha1(json.dumps(output, sort_keys=True, default=str).encode()).hexdigest()[:8]}"
            assess = self.monitor.heartbeat(signature=sig, substantive=True)
            self.tracer.record(run_id, step.step_id, "heartbeat", "progress_tick",
                               status=assess.level.value,
                               extra={"action": assess.action, "idle_s": assess.seconds_no_progress})
            if on_progress:
                on_progress(assess)

        # 终局：多档完成状态
        final = self._finalize()
        return final

    def _finalize(self) -> RunState:
        run = self.run
        assert run is not None
        run_id = run.run_id
        # 重新读最新状态
        run = self.store.load_run(run_id) or run
        self.run = run

        statuses = [s.status for s in run.steps]
        if any(s == StepStatus.FAILED for s in statuses):
            status, conf = "failed", CompletionConfidence.FAILED
        elif all(s in (StepStatus.SUCCESS, StepStatus.SKIPPED) for s in statuses) and statuses:
            status = "completed"
            # completed 也要分档：全部 verified 才 VERIFIED，否则 reported_done（自报封顶）
            if all(s.confidence == CompletionConfidence.VERIFIED for s in run.steps):
                conf = CompletionConfidence.VERIFIED
            else:
                conf = CompletionConfidence.REPORTED_DONE
        elif any(s == StepStatus.RUNNING for s in statuses):
            status, conf = "blocked", CompletionConfidence.BLOCKED
        else:
            status, conf = "running", CompletionConfidence.RUNNING

        self.store.set_run_status(run_id, status, conf)
        run.status, run.confidence = status, conf
        self.tracer.record(run_id, "", "run_end", "run_finalized",
                           status=conf.value, decision=f"run status={status}, confidence={conf.value}",
                           output_data={"total_steps": len(run.steps),
                                        "completed": len(run.completed_steps())})
        return run

    # ── 回放 / 审计 ──
    def replay(self) -> str:
        assert self.run is not None
        return self.tracer.replay(self.run.run_id)

    def timeline(self) -> list[dict]:
        assert self.run is not None
        return self.tracer.get_timeline(self.run.run_id)

    def completion_report(self) -> dict:
        """多档完成状态 + 完整性审计（对标 OMH 三道闸），供 UI/API 呈现。"""
        assert self.run is not None
        run = self.store.load_run(self.run.run_id) or self.run
        steps_out = []
        for s in run.steps:
            claim = CompletionClaim(status=s.confidence, evidence=s.evidence,
                                    verification_command=s.verification_command,
                                    verification_result=s.verification_result,
                                    verifier="external" if s.verification_command else "executor",
                                    claim_text=s.decision)
            reasons = CompletionIntegrityChecker().check(claim)
            steps_out.append({
                "step_id": s.step_id, "seq": s.seq, "status": s.status.value,
                "confidence": s.confidence.value, "integrity_flags": reasons,
                "verified": s.confidence == CompletionConfidence.VERIFIED,
            })
        return {
            "run_id": run.run_id, "task_id": run.task_id, "goal": run.goal,
            "run_status": run.status, "run_confidence": run.confidence.value,
            "self_reported_count": sum(1 for s in run.steps if s.confidence == CompletionConfidence.REPORTED_DONE),
            "verified_count": sum(1 for s in run.steps if s.confidence == CompletionConfidence.VERIFIED),
            "steps": steps_out,
        }


# ══════════════════════════════════════════════════════════════════════
# 6. 薄适配器：供 soulmate_agent 真实步骤循环接线（写了≠接线了）
# ══════════════════════════════════════════════════════════════════════

class LongTaskController:
    """把上述能力以"步骤边界钩子"形式暴露给既有 task 循环，避免推倒重写。

    用法（在现有 while idx < len(plan.subtasks) 循环里）：
        ctl = LongTaskController.bind(session_id, goal)         # 首步/恢复
        ctl.on_step_start(idx, step.description, input_data)    # 认领+轨迹+心跳
        ... 执行 ...
        ctl.on_step_end(idx, success=..., output=..., decision=...,
                        evidence=..., verification_command=..., verification_result=...)
        ctl.on_heartbeat(step_sig)                              # 可选
        ...
        ctl.finalize()                                          # 终局多档状态

    断点续跑：进程重启后 bind() 会自动 find_resumable + record_recovery，
    on_step_start 对已完成步返回 False（不重做）。
    """

    def __init__(self, engine: LongTaskEngine):
        self.engine = engine
        self._monitor = engine.monitor

    @classmethod
    def bind(cls, task_id: str, goal: str, steps: list[dict] | None = None,
             store: CheckpointStore | None = None) -> "LongTaskController":
        store = store or CheckpointStore()
        engine = LongTaskEngine(task_id, goal, store=store)
        # 优先续跑已有未收尾 run
        existing = store.find_resumable(task_id)
        if existing is not None:
            engine.resume()
        else:
            engine.start(steps or [])
        return cls(engine)

    @property
    def run_id(self) -> str:
        return self.engine.run.run_id  # type: ignore

    def completed_step_ids(self) -> set[str]:
        return {s.step_id for s in self.engine.run.completed_steps()}  # type: ignore

    def on_step_start(self, seq: int, description: str, input_data: dict | None = None,
                      step_id: str | None = None) -> bool:
        """返回 False=该步已完成（断点续跑跳过），True=已认领待执行。"""
        sid = step_id or f"step_{seq}"
        if sid in self.completed_step_ids():
            self.engine.tracer.record(self.run_id, sid, "recovery", "skip_already_done",
                                      decision="step already completed in prior run; not redone")
            return False
        # 确保 step 存在（首次见则建）
        self._ensure_step(seq, sid, description, input_data or {})
        self.engine.store.claim_step(self.run_id, sid, input_data or {})
        self.engine.tracer.record(self.run_id, sid, "step_start", "step_started",
                                  input_data=input_data or {}, decision=description)
        return True

    def on_step_end(self, seq: int, *, success: bool, output: dict | None = None,
                    decision: str = "", evidence: list[str] | None = None,
                    verification_command: str = "", verification_result: str = "",
                    verifier: str = "executor", step_id: str | None = None) -> None:
        sid = step_id or f"step_{seq}"
        claim = CompletionClaim(
            status=CompletionConfidence.REPORTED_DONE if success else CompletionConfidence.FAILED,
            evidence=evidence or [], verification_command=verification_command,
            verification_result=verification_result, verifier=verifier, claim_text=decision,
        )
        conf, reasons = evaluate_completion(claim)
        self.engine.store.settle_step(
            self.run_id, sid, success=success, output_data=output or {}, decision=decision,
            evidence=evidence or [], verification_command=verification_command,
            verification_result=verification_result, confidence=conf,
        )
        self.engine.tracer.record(self.run_id, sid, "step_end", "step_finished",
                                  output_data=output or {}, decision=decision,
                                  status=conf.value, evidence=evidence or [],
                                  extra={"success": success, "confidence_reasons": reasons})
        sig = f"{sid}:{hashlib.sha1(json.dumps(output or {}, sort_keys=True, default=str).encode()).hexdigest()[:8]}"
        self._monitor.heartbeat(signature=sig, substantive=True)

    def on_heartbeat(self, signature: str = "") -> ProgressAssessment:
        assess = self._monitor.heartbeat(signature=signature, substantive=False)
        self.engine.tracer.record(self.run_id, "", "heartbeat", "progress_tick",
                                  status=assess.level.value,
                                  extra={"action": assess.action, "idle_s": assess.seconds_no_progress})
        return assess

    def finalize(self) -> dict:
        self.engine._finalize()
        return self.engine.completion_report()

    def _ensure_step(self, seq: int, step_id: str, description: str, input_data: dict) -> None:
        run = self.engine.run
        assert run is not None
        if any(s.step_id == step_id for s in run.steps):
            return
        st = StepState(run_id=run.run_id, step_id=step_id, seq=seq,
                       description=description, input_data=input_data)
        run.steps.append(st)
        # 直接落库（防止 create_run 全量快照覆盖）
        with self.engine.store._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO long_task_steps(run_id,step_id,seq,description,status,confidence,input_json)"
                " VALUES(?,?,?,?,?,?,?)",
                (run.run_id, step_id, seq, description, StepStatus.PENDING.value,
                 CompletionConfidence.NOT_RUN.value, json.dumps(input_data, ensure_ascii=False)),
            )
