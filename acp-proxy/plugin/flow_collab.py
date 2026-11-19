"""
流程协作层 — 工单语义 / 组织角色路由 / 预算隔离 / A2A 协作通道

Paperclip 对位（用户定调）：自研控制面 = Reflex(应激) + Pulse(心跳调度) +
flow_engine(工单流转) + RBAC(组织/角色)。本模块补两个控制面件：
① 工单语义（参考 VibeKanban 任务抢占/心跳）：
   - 工单号 task_id（TO-<时间戳>-<hex>，全局可读可追溯）
   - 归属：owner（run/flow/node）+ assignee（人/Agent）+ role（组织角色来源）
   - 抢占锁：claim(原子 CAS) + lease + heartbeat（租约过期可被他人抢占）
   - 防重复执行：建单按 (run_id, node_id) 幂等；交付幂等（重交回同一结果）；
     claim_token 持有者才可交付（防并发双写）
   - 状态机：queued → todo → working → delivered（终态）/ failed / cancelled
② 预算隔离（接既有 cost_tracker，软预算禁硬杀）：
   - 节点级预算（max_tokens/max_cost_usd/max_duration_seconds）：超出只记账
     over_budget + 通知，绝不杀在跑执行
   - 员工 Agent 级日预算（按 assignee 隔离）：耗尽只做准入节流（该 assignee 新单
     降级 queued 暂缓派发），其他员工 Agent 不受影响（隔离语义），在跑任务不动

协作节点（flow_def 新增 agent/human 节点）经本层：
- agent_node：工单派发经 A2A（a2a/task/delegate）到指定 SoulMate，a2a/task/result
  轮询回流结果（CollabRuntime.poll_agent_tasks），交付后流程恢复
- human_node：工单入收件箱 → 通知（Reflex 事件流）→ 人交付回流，流程恢复
- 与既有 approval 节点整合：审批闸同样开单进收件箱（kind=approval），
  审批/交付共用工单账与超时催办（Pulse 催办）

数据落盘：<COLLAB_TASKS_DIR>/<task_id>.json（原子写 tmp+replace；缺省
data/collab-tasks，环境变量 COLLAB_TASKS_DIR 覆盖——测试隔离）。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from plugin.flow_def import BudgetSpec
from plugin.verify_axis import can_verify, is_input_required, node_confidence, task_axis

logger = logging.getLogger("plugin.flow_collab")

# ---- 工单状态机 ----
STATUS_QUEUED = "queued"          # 已建单未派发（预算节流隔离中）
STATUS_TODO = "todo"              # 已派发/待认领
STATUS_WORKING = "working"        # 已认领执行中
STATUS_DELIVERED = "delivered"    # 已交付（终态）
STATUS_FAILED = "failed"          # 失败（终态：超时 fail / agent 失败）
STATUS_CANCELLED = "cancelled"    # 取消（终态：超时 skip / 人工取消）
TERMINAL_STATUSES = {STATUS_DELIVERED, STATUS_FAILED, STATUS_CANCELLED}
OPEN_STATUSES = {STATUS_QUEUED, STATUS_TODO, STATUS_WORKING}

KIND_AGENT = "agent"
KIND_HUMAN = "human"
KIND_APPROVAL = "approval"

DEFAULT_LEASE_SECONDS = 600.0             # 抢占锁租约（心跳续租）
DEFAULT_REMIND_INTERVAL_S = 3600.0        # 催办最小间隔（催办去重）
DEFAULT_AGENT_DAILY_BUDGET_USD = float(os.environ.get("COLLAB_AGENT_DAILY_BUDGET_USD") or 5.0)
DEFAULT_AGENT_DAILY_BUDGET_TOKENS = int(os.environ.get("COLLAB_AGENT_DAILY_BUDGET_TOKENS") or 2_000_000)
HISTORY_MAX = 50                          # E5 工单 history 有界（防逐任务累积）
DEFAULT_BEAT_INTERVAL_S = float(os.environ.get("COLLAB_BEAT_INTERVAL_S") or 15.0)  # B3 心跳拍距
DEAD_BEATS = 3                            # B3 三拍判死（Multica 45s=3×15s 同位）

TASK_ID_PATTERN_PREFIX = "TO-"


class CollabError(RuntimeError):
    """协作层显式报因（幂等/锁/状态机拒绝均走此异常，消息带原因）"""


# ---- B1 唤醒三态决策（纯函数，Paperclip wake-queue/domain/policy 对位）----

def wake_decision(facts: dict[str, Any]) -> dict[str, Any]:
    """新唤醒撞上执行锁/已关闭时的三态决策（纯函数+facts 对象，不碰存储）。

    - proceed：锁空闲 → 正常开跑
    - coalesce：同任务已在途 → 并入正在跑的执行（不新开 run，A4 合并防重）
    - defer：锁被他人占着 → 排队等锁释放（不插队不双发）
    - cancel：任务已关闭/被取代 → 本次唤醒作废（CLOSED_TASK_CONTINUATION
      静默取消语义；系统 continuation 不对已关任务行动）

    facts: closed | superseded | in_flight | same_task（全部布尔，缺省 False）
    """
    closed = bool(facts.get("closed"))
    superseded = bool(facts.get("superseded"))
    in_flight = bool(facts.get("in_flight"))
    same_task = bool(facts.get("same_task"))
    if closed or superseded:
        return {"action": "cancel",
                "reason": "closed_task_continuation" if closed else "superseded"}
    if not in_flight:
        return {"action": "proceed", "reason": "lock-free"}
    if same_task:
        return {"action": "coalesce", "reason": "merge-into-in-flight"}
    return {"action": "defer", "reason": "lock-held-queued"}


# ---- B2 派发序（Person>Work>Recovery + 防饿死 aging 份额）----

def dispatch_order(tasks: list, now: float, *, aging_after_s: float = 3600.0,
                   share_every: int = 4) -> list:
    """派发序（对位 queued-run-dispatch-order.ts）：层内就绪→年龄排序 + 防饿死份额。

    三级派发序：person（人发起/人工节点）> work（agent 发起）> recovery（恢复回队），
    层内按等待时长最久优先（先到先做）。
    防饿死：每第 share_every 个名额给等待 >aging_after_s 的最老任务（OLDEST_DISPATCH_SHARE）；
    每第 share_every 个名额（偏移 2）保留给 recovery 级（恢复不被新活挤死）。
    纯函数：输入工单序列，输出派发顺序（不碰存储）。
    """
    tier = {"person": 0, "work": 1, "recovery": 2}

    def _age(t):
        return (t.get("created_at") or 0.0)

    items = [t if isinstance(t, dict) else asdict(t) for t in tasks]
    pool = sorted(items, key=lambda t: (tier.get(str(t.get("origin") or "person"), 1),
                                        _age(t)))
    ordered: list[dict] = []
    while pool:
        slot = len(ordered) + 1
        chosen_index = 0
        if slot % share_every == 0:
            # 防饿死名额：等待 >aging_after_s 的最久者优先（不限层级）
            aged = [i for i, t in enumerate(pool) if now - _age(t) > aging_after_s]
            if aged:
                chosen_index = min(aged, key=lambda i: _age(pool[i]))
            else:
                chosen_index = min(range(len(pool)), key=lambda i: _age(pool[i]))
        elif slot % share_every == 2:
            # recovery 保留名额（有则必占，无则回退正常序）
            recovery = [i for i, t in enumerate(pool)
                        if str(t.get("origin") or "") == "recovery"]
            if recovery:
                chosen_index = min(recovery)
        ordered.append(pool.pop(chosen_index))
    return ordered


# ---- E1/E2 任务包组装（上下文隔离 + 快照 diff 告知变化）----

_UPSTREAM_REF_RE = re.compile(r"\$\.([a-z][a-z0-9-]{0,63})")
_REJECT_VERDICT_RE = re.compile(r"驳回|不通过|reject", re.I)


def collect_upstream_refs(*values: Any) -> list[str]:
    """扫描任务文本/args 中的 $.<node-id> 引用（有序去重）——上下文隔离依据。

    任务包只带这里列出的上游节点输出，不整包重灌全部 run.outputs。
    """
    seen: list[str] = []
    for value in values:
        text = value if isinstance(value, str) \
            else json.dumps(value, ensure_ascii=False, default=str)
        for match in _UPSTREAM_REF_RE.finditer(text or ""):
            node_id = match.group(1)
            if node_id not in seen:
                seen.append(node_id)
    return seen


def _compact_value(value: Any) -> Any:
    """compact 档上游值摘要：标量截 80 字符，容器只留形状标记（防上下文爆）。"""
    if isinstance(value, str):
        return value[:80]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return f"<list×{len(value)}>"
    if isinstance(value, dict):
        return f"<dict×{len(value)}: {','.join(list(value)[:5])}>"
    return str(value)[:80]


def _snapshot_diff(old: dict[str, Any], new: dict[str, Any]) -> str:
    """E2 快照 diff：自上次派发以来上游变了什么（added/changed/removed，不整包重灌）。"""
    parts: list[str] = []
    for key in sorted(set(old) | set(new)):
        if key not in old:
            parts.append(f"+{key}")
        elif key not in new:
            parts.append(f"-{key}")
        elif json.dumps(old[key], ensure_ascii=False, default=str) != \
                json.dumps(new[key], ensure_ascii=False, default=str):
            parts.append(f"~{key}")
    return " ".join(parts)


def build_task_package(*, goal: str, form: str = "",
                       upstream: dict[str, Any] | None = None,
                       prev_snapshot: dict[str, Any] | None = None,
                       mode: str = "full",
                       max_chars: int = 1600) -> str:
    """E1 任务包组装：目标+验收+上游输出（full/compact 两档防上下文爆）
    + E2 变更摘要（上游快照 diff 告知变化）。

    上下文隔离：upstream 只含本节点引用的上游输出（collect_upstream_refs 收口）。
    """
    lines = [f"【任务】{goal}"]
    if form:
        lines.append(f"【验收】{form}")
    upstream = upstream or {}
    if upstream:
        if mode == "compact":
            body = json.dumps({k: _compact_value(v) for k, v in upstream.items()},
                              ensure_ascii=False, default=str)
            lines.append(f"【上游输出(compact)】{body[:max_chars // 2]}")
        else:
            body = json.dumps(upstream, ensure_ascii=False, default=str)
            lines.append(f"【上游输出】{body[:max_chars]}")
    if prev_snapshot is not None and prev_snapshot != upstream:
        diff = _snapshot_diff(prev_snapshot, upstream)
        if diff:
            lines.append(f"【变更摘要】自上次派发以来: {diff}")
    return "\n".join(lines)


def config_fingerprint(*parts: Any) -> str:
    """E3 配置指纹：role/target_agent/预算等有效输入的指纹（变更→换代丢连续性）。"""
    blob = json.dumps(parts, ensure_ascii=False, default=str, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:12]


def review_verdict(result: Any) -> str:
    """E4 review gate 判定：交付物中的验收结论（accept | reject）。

    结构化 {"verdict": "accept|reject"} 优先；散文按驳回/不通过/reject 关键词
    判 reject（缺省 accept——通过与否由关键词显式表达）。
    """
    if isinstance(result, dict):
        value = str(result.get("verdict") or "").strip().lower()
        if value in ("reject", "rejected", "rejects"):
            return "reject"
        if value in ("accept", "accepted", "approve", "approved"):
            return "accept"
    text = str(result or "")
    if _REJECT_VERDICT_RE.search(text):
        return "reject"
    return "accept"


def record_review_receipt(run_id: str, node_id: str, verdict: str,
                          decided_by: str, result: Any) -> None:
    """E4 成果回流留档：验收结论挂 flow_receipts（凭证来自服务端，模型无法伪造）。"""
    try:
        from flow_receipts import record_receipt
        record_receipt(
            task_id=run_id, action=f"review-{verdict}",
            params={"node": node_id, "verdict": verdict, "by": decided_by,
                    "result": str(result)[:200]},
            ok=(verdict == "accept"),
            error="" if verdict == "accept" else f"验收驳回: {str(result)[:200]}",
        )
    except Exception as e:  # noqa: BLE001 — 留档失败不阻断流程
        logger.warning("验收回执留档失败 %s/%s: %s", run_id, node_id, e)


# ---- 工单模型 ----

@dataclass
class WorkOrder:
    """协作工单（任务收发台一等公民）。

    状态机（唯一转移路径，非法转移显式拒绝）：
        queued → todo → working → delivered
                       ↘ failed / cancelled（终态）
    queued=预算节流暂缓派发；working 持 claim_token + 租约（心跳续租，过期可抢占）。
    """
    task_id: str
    kind: str                     # agent | human | approval
    status: str = STATUS_TODO
    # 归属（工单语义）
    run_id: str = ""
    flow_id: str = ""
    node_id: str = ""
    role: str = ""
    assignee: str = ""            # 人（user id）或员工名（agent 单=员工，其 SoulMate 干活）
    assignee_kind: str = "human"  # human | agent
    target_agent: str = ""        # agent 单：A2A 目标 SoulMate
    created_by: str = "flow"
    origin: str = "person"        # B2 派发序三级：person(人发起) | work(agent 发起) | recovery(恢复)
    requeues: int = 0             # B3 失联回队列计数（bounded，超限→failed）
    # 内容
    title: str = ""
    instruction: str = ""
    form: str = ""                # 交付物说明（human 单）
    payload: dict[str, Any] = field(default_factory=dict)
    result: Any = None
    delivered_by: str = ""
    delivered_via: str = ""       # api | a2a | approval
    # 时间线
    created_at: float = 0.0
    updated_at: float = 0.0
    due_at: float = 0.0           # 交付期限（timeout_hours 推导）
    delivered_at: float | None = None
    on_timeout: str = "fail"      # fail | skip（超时处置，与 approval 闸同语义）
    # 抢占锁（claim + lease + heartbeat）
    claim_token: str = ""
    claimed_by: str = ""
    claim_at: float = 0.0
    lease_expires_at: float = 0.0
    heartbeat_at: float = 0.0
    lease_seconds: float = DEFAULT_LEASE_SECONDS
    # 催办
    reminders: int = 0
    last_remind_at: float = 0.0
    # D5 case handoff（带版本校验的交办事务，被取代即作废）
    handoff_version: int = 0
    handoffs: list[dict[str, Any]] = field(default_factory=list)
    # agent 通道 / 预算
    a2a_task_id: str = ""
    handed_off_at: float = 0.0   # C4：派发交接标记（先记后发，防双发双扣）
    budget: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)   # duration_ms/tokens/cost_usd
    over_budget: bool = False
    # 可观测
    history: list[dict[str, Any]] = field(default_factory=list)
    history_trimmed: int = 0      # E5 有界裁剪计数（上下文卫生）
    archived: bool = False        # E5 任务包上下文已归档释放
    # ── 双轴完成状态（监督闭环：reported done ≠ verified）──
    verified: bool = False        # Test·verified（绿）：只由验收证据翻档
    verified_by: str = ""         # 验收者（≠交付者才许翻档，防自审）
    verified_at: float | None = None
    verify_evidence: str = ""     # 内容级探针/验收证据摘要（报成功没用）
    claim_source: str = "self-report"   # self-report（自报封顶黄）| evidence（翻绿）
    settle_rejects: int = 0       # settle 拒伪完成次数（留痕计数）
    fix_rounds: int = 0           # 修复轮（≤2，超出→重规划）

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "WorkOrder":
        known = {f for f in WorkOrder.__dataclass_fields__}  # noqa: C416
        return WorkOrder(**{k: v for k, v in data.items() if k in known})

    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def is_overdue(self, now: float | None = None) -> bool:
        """超时桶判定：未终态且已过交付期限。"""
        if self.status in TERMINAL_STATUSES:
            return False
        now = time.time() if now is None else now
        return bool(self.due_at) and now >= self.due_at

    def bucket(self, now: float | None = None) -> str:
        """收发台三桶：在办 open / 交付 delivered / 超时 overdue。"""
        if self.status == STATUS_DELIVERED:
            return "delivered"
        if self.status in (STATUS_FAILED, STATUS_CANCELLED):
            return "closed"
        return "overdue" if self.is_overdue(now) else "open"

    # ── 双轴（只读派生，fail-closed：交付未验收=黄 reported done）──

    @property
    def confidence(self) -> str:
        status = "ok" if self.status == STATUS_DELIVERED else self.status
        return node_confidence(status, self.verified)

    @property
    def status_axis(self) -> str:
        return task_axis(self.status, self.verified)

    def note(self, event: str, detail: str = "") -> None:
        # E5 上下文卫生：history 有界（防逐任务累积），溢出计数留痕
        if len(self.history) >= HISTORY_MAX:
            dropped = len(self.history) - HISTORY_MAX + 1
            self.history = self.history[dropped:]
            self.history_trimmed += dropped
        self.history.append({"ts": time.time(), "event": event, "detail": detail[:500]})
        self.updated_at = time.time()


# ---- 工单存储（JSON 文件 + 进程内 CAS 锁）----

class WorkOrderStore:
    """工单落盘：<dir>/<task_id>.json 原子写；claim/heartbeat/deliver 走锁内 CAS。"""

    def __init__(self, base_dir: Path | str | None = None):
        env_dir = os.environ.get("COLLAB_TASKS_DIR")
        self.base_dir = Path(base_dir or env_dir or
                             Path(__file__).resolve().parent.parent.parent / "data" / "collab-tasks")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, task_id: str) -> Path:
        return self.base_dir / f"{task_id}.json"

    def save(self, task: WorkOrder) -> None:
        task.updated_at = time.time()
        path = self._path(task.task_id)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(task.to_dict(), ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(path)

    def load(self, task_id: str) -> WorkOrder:
        path = self._path(task_id)
        if not path.is_file():
            raise CollabError(f"工单不存在: {task_id}")
        return WorkOrder.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def find(self, task_id: str) -> WorkOrder | None:
        try:
            return self.load(task_id)
        except (CollabError, json.JSONDecodeError, TypeError):
            return None

    def list(self, *, assignee: str = "", role: str = "", kind: str = "",
             bucket: str = "", limit: int = 100, now: float | None = None) -> list[WorkOrder]:
        """清单（收发台数据源）：bucket=open(在办)/delivered(交付)/overdue(超时)。"""
        now = time.time() if now is None else now
        items: list[WorkOrder] = []
        for path in sorted(self.base_dir.glob("TO-*.json")):
            try:
                task = WorkOrder.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if assignee and task.assignee != assignee:
                continue
            if role and task.role != role:
                continue
            if kind and task.kind != kind:
                continue
            if bucket and task.bucket(now) != bucket:
                continue
            items.append(task)
        items.sort(key=lambda t: t.created_at, reverse=True)
        return items[: max(1, limit)]

    def count_open(self, assignee: str) -> int:
        return sum(1 for t in self.list(assignee=assignee, limit=10_000) if t.is_open())

    # ---- 建单（A2：(run_id, node_id, kind) 开态唯一 ≙ 部分唯一索引 WHERE status IN open）----

    def find_open(self, run_id: str, node_id: str, kind: str) -> WorkOrder | None:
        for task in self.list(limit=10_000):
            if task.run_id == run_id and task.node_id == node_id \
                    and task.kind == kind and task.is_open():
                return task
        return None

    def create(self, *, task_id: str, kind: str, run_id: str, node_id: str,
               **fields: Any) -> tuple[WorkOrder, bool]:
        """原子建单（锁内查重+落盘）。开态重复 → 幂等回既有单（防重复执行）。

        返回 (工单, 是否新建)。锁内校验 ≙ 条件 INSERT + 部分唯一索引兜底。
        """
        with self._lock:
            existing = self.find_open(run_id, node_id, kind)
            if existing is not None:
                # B1 唤醒三态：同任务在途 → coalesce 并入（不新开 run，A4 合并防重）
                decision = wake_decision({"in_flight": True, "same_task": True})
                existing.note("create-coalesced",
                              f"{decision['action']}: {decision['reason']}")
                self.save(existing)
                return existing, False
            now = fields.pop("now", time.time())
            # B2 派发序三级：人发起(person) > agent 发起(work) > 恢复(recovery)
            fields.setdefault("origin",
                              "person" if kind in (KIND_HUMAN, KIND_APPROVAL) else "work")
            task = WorkOrder(task_id=task_id, kind=kind, status=STATUS_TODO,
                             run_id=run_id, node_id=node_id,
                             created_at=now, updated_at=now, **fields)
            self.save(task)
            return task, True

    # ---- 原子转移（抢占锁内 CAS，防重复执行）----

    def _mutate(self, task_id: str, fn: Callable[[WorkOrder], Any]) -> tuple[WorkOrder, Any]:
        with self._lock:
            task = self.load(task_id)
            outcome = fn(task)
            self.save(task)
            return task, outcome

    def claim(self, task_id: str, claimed_by: str,
              lease_seconds: float = DEFAULT_LEASE_SECONDS,
              now: float | None = None) -> tuple[WorkOrder, str]:
        """认领（A1 原子领取 ≙ 条件 UPDATE：todo/queued 才可翻 working）。

        拒领显式报因（A5 领取决策漏斗，reason 码进工单 history）：
        - closed：工单已终态
        - predecessor_still_executing：前任未清场（同 run+node+kind 另有持锁开态单 /
          本单被活锁持有），拒领回队列（A3）
        返回 (工单, claim_token)。重复认领/租约未过期抢占 → CollabError。
        """
        now = time.time() if now is None else now

        def _do(task: WorkOrder) -> str:
            if task.status in TERMINAL_STATUSES:
                raise CollabError(f"closed: 工单已终态({task.status})，不可认领: {task_id}")
            if task.status == STATUS_WORKING and task.lease_expires_at > now:
                raise CollabError(
                    f"predecessor_still_executing: 工单被 {task.claimed_by!r} 持锁中"
                    f"（租约至 {_fmt(task.lease_expires_at)}），前任未清场拒领")
            # A3：同 (run, node, kind) 另有前任持锁开态单 → 拒领（重派须等清场）
            for other in self.list(limit=10_000):
                if other.task_id == task.task_id:
                    continue
                if other.run_id == task.run_id and other.node_id == task.node_id \
                        and other.kind == task.kind and other.is_open() \
                        and other.lease_expires_at > now:
                    raise CollabError(
                        f"predecessor_still_executing: 前任工单 {other.task_id} "
                        f"被 {other.claimed_by!r} 持锁未清场，拒领")
            if task.status == STATUS_WORKING:
                task.note("reclaim", f"{claimed_by} 抢占（原持锁 {task.claimed_by} 租约过期）")
            token = uuid.uuid4().hex[:12]
            task.status = STATUS_WORKING
            task.claim_token = token
            task.claimed_by = claimed_by
            task.claim_at = now
            task.heartbeat_at = now
            task.lease_seconds = lease_seconds
            task.lease_expires_at = now + lease_seconds
            task.note("claim-decision",
                      f"claimed by={claimed_by} lease={lease_seconds:g}s")
            return token

        try:
            task, token = self._mutate(task_id, _do)
        except CollabError as e:
            # A5：拒领决策同样留痕（落 history 后原样抛出）
            task = self.find(task_id)
            if task is not None:
                task.note("claim-decision", f"rejected: {e}")
                self.save(task)
            raise
        return task, token

    def heartbeat(self, task_id: str, claim_token: str,
                  now: float | None = None) -> WorkOrder:
        """心跳续租：仅持有效 claim_token 者可续；租约过期/令牌不符显式拒绝。"""
        now = time.time() if now is None else now

        def _do(task: WorkOrder) -> None:
            if task.status in TERMINAL_STATUSES:
                raise CollabError(f"工单已终态({task.status})，心跳无效: {task_id}")
            if not task.claim_token or task.claim_token != claim_token:
                raise CollabError("claim_token 无效（可能已被抢占）")
            if task.lease_expires_at < now:
                raise CollabError("租约已过期，请重新认领")
            task.heartbeat_at = now
            task.lease_expires_at = now + task.lease_seconds
            task.note("heartbeat", f"by={task.claimed_by}")

        task, _ = self._mutate(task_id, _do)
        return task

    def deliver(self, task_id: str, result: Any, delivered_by: str = "",
                claim_token: str = "", via: str = "api",
                now: float | None = None) -> tuple[WorkOrder, bool]:
        """交付回流：终态重交幂等（回同一结果，重复=True）；带 claim_token 必须匹配。

        返回 (工单, 是否重复交付)。
        """
        now = time.time() if now is None else now

        def _do(task: WorkOrder) -> bool:
            if task.status == STATUS_DELIVERED:
                return True  # 幂等：结果已固化，绝不覆盖（防重复执行）
            if task.status in (STATUS_FAILED, STATUS_CANCELLED):
                raise CollabError(f"工单已终态({task.status})，不可交付: {task_id}")
            if claim_token and task.claim_token != claim_token:
                # CAS 回执：令牌必须匹配当前领取（含交接清场后的空令牌=前任令牌作废）
                raise CollabError("claim_token 无效，交付被拒（防并发双写）")
            task.status = STATUS_DELIVERED
            task.result = result
            task.delivered_by = delivered_by or task.claimed_by or task.assignee
            task.delivered_via = via
            task.delivered_at = now
            task.note("delivered", f"by={task.delivered_by} via={via}")
            return False

        task, dup = self._mutate(task_id, _do)
        return task, dup

    def verify(self, task_id: str, *, verified_by: str, evidence: str,
               ok: bool = True, now: float | None = None) -> WorkOrder:
        """验收证据翻档（工单面，唯一翻绿通道）：证据非空 + 验收者≠交付者。

        伪完成被拒并留痕（对位 completion_integrity 自审拒收规则）：
        自审（self_referential_evidence）/证据为空（no_evidence）→
        note(verify-refused) 留痕后 CollabError 原样上抛，工单保持 reported done（黄）。
        ok=False：验收不过——不许标完成（拒伪完成），留痕计数。
        """
        now = time.time() if now is None else now

        def _do(task: WorkOrder) -> None:
            if task.status != STATUS_DELIVERED:
                raise CollabError(
                    f"只有已交付工单可验收（当前 {task.status}）: {task_id}")
            if not ok:
                task.settle_rejects += 1
                task.note("verify-failed",
                          f"验收不过 by={verified_by or '-'}: {str(evidence)[:200]}")
                return
            allowed, code, detail = can_verify(
                verifier=verified_by, executor=task.delivered_by,
                evidence=evidence)
            if not allowed:
                raise CollabError(f"verify_refused:{code}: {detail}")
            task.verified = True
            task.verified_by = verified_by
            task.verified_at = now
            task.verify_evidence = str(evidence)[:500]
            task.claim_source = "evidence"
            task.note("verified",
                      f"by={verified_by} evidence={str(evidence)[:200]}")

        try:
            task, _ = self._mutate(task_id, _do)
        except CollabError as e:
            if str(e).startswith("verify_refused:"):
                # 拒收留痕（落 history 后原样抛出）
                task = self.find(task_id)
                if task is not None:
                    task.settle_rejects += 1
                    task.note("verify-refused", str(e)[len("verify_refused:"):][:300])
                    self.save(task)
            raise
        return task

    def close(self, task_id: str, status: str, note: str = "") -> WorkOrder:
        """终态收口（failed/cancelled）：终态幂等不覆盖。"""
        if status not in (STATUS_FAILED, STATUS_CANCELLED):
            raise CollabError(f"close 只接受 failed|cancelled，收到 {status!r}")

        def _do(task: WorkOrder) -> None:
            if task.status in TERMINAL_STATUSES:
                return  # 幂等
            task.status = status
            task.note(status, note)

        task, _ = self._mutate(task_id, _do)
        return task

    def remind(self, task_id: str, note: str = "",
               min_interval_s: float = DEFAULT_REMIND_INTERVAL_S,
               now: float | None = None) -> tuple[WorkOrder, bool]:
        """催办（带最小间隔去重）：返回 (工单, 是否真催了)。终态不催。"""
        now = time.time() if now is None else now

        def _do(task: WorkOrder) -> bool:
            if task.status in TERMINAL_STATUSES:
                return False
            if task.last_remind_at and now - task.last_remind_at < min_interval_s:
                return False
            task.reminders += 1
            task.last_remind_at = now
            task.note("reminded", f"第{task.reminders}次催办 {note}".strip())
            return True

        task, did = self._mutate(task_id, _do)
        return task, did

    def handoff(self, task_id: str, *, to_assignee: str, by: str = "",
                note: str = "", expect_version: int | None = None,
                now: float | None = None) -> WorkOrder:
        """D5 case handoff 交办（对位 Paperclip caseHandoffs：版本号+状态机+被取代即作废）。

        - 带版本校验：expect_version 与当前不符 → case_handoff_superseded 拒绝
          （转派/升级途中被新交办取代的旧请求一律作废，防"转了一半被旧任务覆盖"）
        - 交接即清场：释放前任领取锁（claim_token 作废），新归属重新认领
        - 终态不可交办（closed 显式报因）
        """
        now = time.time() if now is None else now

        def _do(task: WorkOrder) -> None:
            if task.status in TERMINAL_STATUSES:
                raise CollabError(f"closed: 工单已终态({task.status})，不可交办: {task_id}")
            if expect_version is not None and expect_version != task.handoff_version:
                raise CollabError(
                    f"case_handoff_superseded: 交办版本不符"
                    f"（当前 v{task.handoff_version}，期望 v{expect_version}）——已被取代即作废")
            if not to_assignee:
                raise CollabError("交办必须指定新归属 to_assignee")
            previous = task.assignee
            task.handoff_version += 1
            task.handoffs.append({
                "version": task.handoff_version,
                "from": previous, "to": to_assignee, "by": by,
                "note": note[:200], "ts": now,
            })
            task.assignee = to_assignee
            task.claim_token = ""        # 交接即清场：前任锁作废
            task.claimed_by = ""
            task.claim_at = 0.0
            task.lease_expires_at = 0.0
            if task.status == STATUS_WORKING:
                task.status = STATUS_TODO
            task.note("handoff",
                      f"v{task.handoff_version}: {previous}→{to_assignee} by={by or '-'} {note}".strip())

        task, _ = self._mutate(task_id, _do)
        return task

    def claim_next(self, assignee: str, *, claimed_by: str = "",
                   lease_seconds: float = DEFAULT_LEASE_SECONDS,
                   now: float | None = None) -> tuple[WorkOrder, str] | None:
        """B2 派发序认领（心跳协议"自查名下工单"的领取面）：

        按 dispatch_order（Person>Work>Recovery+防饿死份额）从该收件人的开态工单
        取下一个可领者认领；无可领返回 None（含排队等待归因留痕）。
        """
        now = time.time() if now is None else now
        candidates = [t for t in self.list(assignee=assignee, limit=10_000, now=now)
                      if t.is_open()]
        if not candidates:
            return None
        for item in dispatch_order([t.to_dict() for t in candidates], now):
            try:
                return self.claim(item["task_id"],
                                  claimed_by=claimed_by or assignee,
                                  lease_seconds=lease_seconds, now=now)
            except CollabError as e:
                # 领取决策漏斗（A5）：被占/被取代→顺延下一个候选
                task = self.find(str(item["task_id"]))
                if task is not None:
                    task.note("claim-decision", f"skipped: {e}")
                    self.save(task)
                continue
        return None


# ---- 组织角色路由（接 RBAC 角色表）----

class RoleRouter:
    """流程 YAML 按 role 分发（D1/D2：角色路由=数据，YAML 只写 role 不写人）。

    解析序（每步显式报因，绝不静默派错人）：
    ① 显式 user/agent → 直接定人
    ② org 表角色成员（rbac/org.py，最少在办优先，平局字典序=确定性）
    ③ 角色成员全不可用 → 沿 reports_to 递归汇报链找 backup（D1，主管兜底）
    ④ org 表无角色 → RBAC 角色表兜底（接既有 RBAC，持有该角色的用户）
    """

    def __init__(self, store: WorkOrderStore | None = None, rbac=None, org=None):
        self._store = store
        self._rbac = rbac  # 惰性：rbac.store.RBACStore() 进程单例
        self._org = org    # 惰性：rbac.org.get_org_chart() 进程单例
        self.member_max_open = int(os.environ.get("ORG_MEMBER_MAX_OPEN") or 3)

    def _rbac_store(self):
        if self._rbac is not None:
            return self._rbac
        from rbac.store import RBACStore
        self._rbac = RBACStore()
        return self._rbac

    def _org_chart(self):
        if self._org is not None:
            return self._org
        from rbac.org import get_org_chart
        self._org = get_org_chart()
        return self._org

    def _load(self, user_id: str) -> int:
        store = self._store
        return store.count_open(user_id) if store is not None else 0

    def resolve(self, *, role: str = "", user: str = "", agent: str = "",
                kind: str = "human") -> tuple[str, str]:
        """解析收件人，返回 (assignee, role)。"""
        if user:
            return user, role
        if agent:
            return agent, role
        if not role:
            raise CollabError("协作节点必须指定 user 或 role（组织角色路由）")
        # ② org 表角色成员（在办未满额者中取最少在办）
        members = self._org_chart().members_with_role(role)
        available = [m for m in members if self._load(m.user_id) < self.member_max_open]
        if available:
            available.sort(key=lambda m: (self._load(m.user_id), m.user_id))
            return available[0].user_id, role
        # ③ 递归汇报链 backup（D1：成员不在线/满载，沿 reports_to 向上兜底）
        if members:
            backup = self._org_chart().backup_for(role)
            if backup is not None:
                return backup.user_id, role
        # ④ RBAC 角色表兜底
        users = [u for u in self._rbac_store().list_users() if u.has_role(role)]
        if users:
            users.sort(key=lambda u: (self._load(u.id), u.id))
            return users[0].id, role
        raise CollabError(
            f"角色 {role!r} 无可用成员（org 表无在办容量成员/无 backup，"
            f"RBAC 角色表亦查无此人），无法派单")


# ---- 预算隔离（接 cost_tracker，软预算禁硬杀）----

# BudgetSpec 定义在 plugin.flow_def（YAML 定义 DSL 的一部分），此处复用同一类型。


class BudgetPolicy:
    """预算策略（C1 三档 scope ≙ Paperclip budget_policies）。

    scope: company（公司总盘）| agent（员工 Agent 级）| project（流程/项目级）
    window: day | month | lifetime；warn_percent=预警阈值；hard_stop=True 超限硬停
    （硬停=派发前拦截转人工，绝不杀在跑执行——C5 软超时不动手）。
    状态机：ok → warning（过 warn%）→ hard_stop（过硬停线）。
    """

    def __init__(self, scope: str, key: str = "*", window: str = "day",
                 max_cost_usd: float = 0.0, max_tokens: int = 0,
                 warn_percent: float = 80.0, hard_stop: bool = True):
        if scope not in ("company", "agent", "project"):
            raise ValueError(f"预算 scope 非法: {scope!r}（company | agent | project）")
        if window not in ("day", "month", "lifetime"):
            raise ValueError(f"预算 window 非法: {window!r}（day | month | lifetime）")
        self.scope = scope
        self.key = key
        self.window = window
        self.max_cost_usd = float(max_cost_usd)
        self.max_tokens = int(max_tokens)
        self.warn_percent = float(warn_percent)
        self.hard_stop = bool(hard_stop)
        self.state = "ok"

    @property
    def id(self) -> str:
        return f"{self.scope}:{self.key}:{self.window}"

    def evaluate(self, usage: dict[str, float]) -> str:
        """按用量定状态（ok/warning/hard_stop）；只标记，不杀执行。"""
        state = "ok"
        for spent, limit in ((usage.get("cost_usd", 0.0), self.max_cost_usd),
                             (usage.get("tokens", 0.0), float(self.max_tokens))):
            if limit <= 0:
                continue
            if spent >= limit:
                state = "hard_stop" if self.hard_stop else "warning"
            elif spent >= limit * self.warn_percent / 100 and state == "ok":
                state = "warning"
        self.state = state
        return state


class BudgetLedger:
    """预算账本（C1-C3 真闸）：三档策略表 + 派发前拦截 + 审批放行 waiver + cost_tracker 落账。

    - charge()：用量按三档 scope 同时记账（company/agent/project），策略状态直出看板
    - blocked()：派发前拦截（C2 getInvocationBlock 同位）——返回 hard_stop 原因清单
    - grant_waiver()/take_waiver()：预算闸转人工审批放行后一次性豁免（C3 恢复续跑）
    - 软预算铁律：在跑执行绝不硬杀；止损靠派发闸（C5，避开 Multica 只观测的坑）
    """

    def __init__(self, cost_tracker=None, daily_budget_usd: float | None = None,
                 daily_budget_tokens: int | None = None, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._cost_tracker = cost_tracker
        self._cost_failed = False
        # 兼容级缺省策略：员工 Agent 日预算（scope=agent, window=day）
        self.daily_budget_usd = (DEFAULT_AGENT_DAILY_BUDGET_USD if daily_budget_usd is None
                                 else float(daily_budget_usd))
        self.daily_budget_tokens = (DEFAULT_AGENT_DAILY_BUDGET_TOKENS if daily_budget_tokens is None
                                    else int(daily_budget_tokens))
        self._policies: dict[str, BudgetPolicy] = {}
        self._usage: dict[tuple[str, str, str], dict[str, float]] = {}  # (scope,key,窗口) → 用量
        self._waivers: set[str] = set()   # 审批放行一次性豁免（C3）

    # -- 策略表（C1）--

    def add_policy(self, policy: BudgetPolicy) -> BudgetPolicy:
        self._policies[policy.id] = policy
        return policy

    def policies(self) -> list[BudgetPolicy]:
        return list(self._policies.values())

    def remove_policy(self, policy_id: str) -> bool:
        return self._policies.pop(policy_id, None) is not None

    def _ensure_default_agent_policy(self, assignee: str) -> None:
        pid = f"agent:{assignee}:day"
        if pid not in self._policies:
            self.add_policy(BudgetPolicy(
                "agent", assignee, "day",
                max_cost_usd=self.daily_budget_usd,
                max_tokens=self.daily_budget_tokens))

    # -- 用量账（三档同记）--

    def _window_key(self, window: str) -> str:
        now = _dt.datetime.fromtimestamp(self._clock())
        if window == "day":
            return now.strftime("%Y-%m-%d")
        if window == "month":
            return now.strftime("%Y-%m")
        return "lifetime"

    def _bucket(self, scope: str, key: str, window: str) -> dict[str, float]:
        return self._usage.setdefault((scope, key, self._window_key(window)),
                                      {"tokens": 0.0, "cost_usd": 0.0, "duration_ms": 0.0})

    def usage_for(self, scope: str, key: str, window: str = "lifetime") -> dict[str, float]:
        """按 (scope, key, 窗口) 精确读账（charge 各窗口桶已各自累计，不跨桶求和防三重计数）。"""
        acc = {"tokens": 0.0, "cost_usd": 0.0, "duration_ms": 0.0}
        acc.update(self._usage.get((scope, key, self._window_key(window)), {}))
        return acc

    def charge(self, *, assignee: str, node_id: str = "", task_id: str = "",
               flow_id: str = "", tokens: int = 0, cost_usd: float = 0.0,
               duration_ms: float = 0.0,
               budget: BudgetSpec | dict | None = None) -> dict[str, Any]:
        """记一笔用量（company/agent/project 三档同记 + 节点级预算核对），返回判定。"""
        tokens = int(tokens or 0)
        cost_usd = float(cost_usd or 0.0)
        duration_ms = float(duration_ms or 0.0)
        spec = _as_budget(budget)
        for scope, key in (("company", "*"), ("agent", assignee),
                           ("project", flow_id or "-")):
            if not key:
                continue
            for window in ("day", "month", "lifetime"):
                acc = self._bucket(scope, key, window)
                acc["tokens"] += tokens
                acc["cost_usd"] += cost_usd
                acc["duration_ms"] += duration_ms

        over_node: list[str] = []
        if spec.max_tokens and tokens > spec.max_tokens:
            over_node.append(f"tokens {tokens}>{spec.max_tokens}")
        if spec.max_cost_usd and cost_usd > spec.max_cost_usd:
            over_node.append(f"cost ${cost_usd:.4f}>${spec.max_cost_usd:.4f}")
        if spec.max_duration_seconds and duration_ms > spec.max_duration_seconds * 1000:
            over_node.append(
                f"duration {duration_ms:.0f}ms>{spec.max_duration_seconds:.0f}s")

        # 策略状态刷新（ok/warning/hard_stop 直出看板）
        self._ensure_default_agent_policy(assignee)
        for policy in self._policies.values():
            policy.evaluate(self.usage_for(policy.scope, policy.key, policy.window))

        tracker = self._tracker()
        if tracker is not None:
            try:
                tracker.record_usage(
                    model="collab", input_tokens=tokens, output_tokens=0,
                    session_id=task_id or f"node:{node_id}",
                    request_type="collab", custom_cost=cost_usd or None)
            except Exception as e:  # noqa: BLE001 — 落账失败不阻断
                logger.warning("cost_tracker 落账失败: %s", e)

        return {
            "over_budget": bool(over_node),
            "over_reasons": over_node,
            "usage": {"tokens": tokens, "cost_usd": cost_usd, "duration_ms": duration_ms},
            "assignee_daily": self.usage_for("agent", assignee, "day"),
            "assignee_exhausted": self.exhausted(assignee),
        }

    # -- 派发前拦截（C2）+ 审批放行（C3）--

    def blocked(self, *, assignee: str = "", flow_id: str = "") -> list[str]:
        """派发前预算闸：hard_stop 策略命中 → 返回拦截原因清单（空=放行）。"""
        if assignee:
            self._ensure_default_agent_policy(assignee)
        reasons: list[str] = []
        for policy in self._policies.values():
            if policy.scope == "agent" and assignee and policy.key != assignee:
                continue
            if policy.scope == "project" and flow_id and policy.key != flow_id:
                continue
            if policy.scope == "project" and not flow_id:
                continue
            usage = self.usage_for(policy.scope, policy.key, policy.window)
            state = policy.evaluate(usage)
            if state == "hard_stop":
                reasons.append(
                    f"{policy.id}: ${usage['cost_usd']:.4f}/{policy.max_cost_usd:.2f} "
                    f"tokens {usage['tokens']:.0f}/{policy.max_tokens} 超硬停线")
        return reasons

    def grant_waiver(self, gate_key: str) -> None:
        """预算闸人工放行（C3）：一次性豁免下一次拦截（记账照常，不重置策略）。"""
        self._waivers.add(gate_key)

    def has_waiver(self, gate_key: str) -> bool:
        """窥视放行标记（不消耗；派发闸真正被拦时才消耗）。"""
        return gate_key in self._waivers

    def take_waiver(self, gate_key: str) -> bool:
        """消耗放行标记（一次性）。"""
        if gate_key in self._waivers:
            self._waivers.discard(gate_key)
            return True
        return False

    def exhausted(self, assignee: str) -> bool:
        """兼容面：员工 Agent 是否已硬停（=agent 档存在 hard_stop 策略）。"""
        return bool(self.blocked(assignee=assignee))

    def usage(self, assignee: str) -> dict[str, float]:
        return self.usage_for("agent", assignee, "day")

    def _tracker(self):
        """cost_tracker 惰性装配（接既有 LLM 成本台账；失败只记账不阻断）。"""
        if self._cost_failed or self._cost_tracker is not None:
            return self._cost_tracker
        try:
            from agent.cost_tracker import CostTracker
            db = os.environ.get("COLLAB_COST_DB") or ""
            self._cost_tracker = CostTracker(db_path=db)
        except Exception as e:  # noqa: BLE001 — 落账失败不阻断派单
            logger.warning("cost_tracker 装配失败（预算自算兜底）: %s", e)
            self._cost_failed = True
        return self._cost_tracker


def _as_budget(budget: BudgetSpec | dict | None) -> BudgetSpec:
    if budget is None:
        return BudgetSpec()
    if isinstance(budget, BudgetSpec):
        return budget
    return BudgetSpec(
        max_tokens=int(budget.get("max_tokens") or 0),
        max_cost_usd=float(budget.get("max_cost_usd") or 0.0),
        max_duration_seconds=float(budget.get("max_duration_seconds") or 0.0),
    )


# ---- A2A 协作通道（a2a/task/delegate + a2a/task/result 回流）----

DEFAULT_A2A_ENDPOINT = os.environ.get("A2A_ENDPOINT") or "http://127.0.0.1:8093/rpc/a2a"


class A2AChannel:
    """A2A v1.0 通道：delegate 派单 → result 轮询回流（JSON-RPC over HTTP）。

    transport 可注入：async (method, params) -> result dict（测试桩/复用连接）；
    缺省走 httpx 打真实 A2A 端点（a2a-server /rpc/a2a）。
    """

    def __init__(self, endpoint: str = "", timeout_s: float = 15.0,
                 transport: Callable | None = None):
        self.endpoint = endpoint or DEFAULT_A2A_ENDPOINT
        self.timeout_s = timeout_s
        self._transport = transport
        self._rpc_id = 0

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._transport is not None:
            return await self._transport(method, params)
        import httpx
        self._rpc_id += 1
        payload = {"jsonrpc": "2.0", "id": self._rpc_id, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(self.endpoint, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise CollabError(f"A2A {method} 错误: {data['error']}")
        return data.get("result") or {}

    async def dispatch(self, *, target_agent: str, instruction: str,
                       task_type: str = "execute", trace_id: str = "",
                       require_result: bool = True, timeout_ms: int = 30000) -> dict[str, Any]:
        """a2a/task/delegate 派单。返回 {a2a_task_id, status, result?}。

        确定性处理端（如 evo）同步回 result_data；SoulMate 类异步端回 pending，
        结果经 fetch() 轮询回流。
        """
        result = await self._call("a2a/task/delegate", {
            "target_agent": target_agent,
            "task_type": task_type,
            "message": instruction,
            "trace_id": trace_id or f"collab-{uuid.uuid4().hex[:8]}",
            "require_result": require_result,
            "timeout": timeout_ms,
        })
        out = {
            "a2a_task_id": str(result.get("task_id") or ""),
            "status": str(result.get("status") or ""),
            "result": (result.get("result_data") or {}).get("reply")
            if isinstance(result.get("result_data"), dict) else None,
        }
        return out

    async def fetch(self, a2a_task_id: str) -> dict[str, Any]:
        """a2a/task/result 查询回流：{state, result, raw}。

        state=completed/failed/working；result=Agent 应答文本
        （artifacts 优先，其次 history 中 agent 消息）。
        """
        dump = await self._call("a2a/task/result", {"task_id": a2a_task_id})
        status = dump.get("status") or {}
        state = str(status.get("state") or "").lower()
        if isinstance(dump.get("status"), str):
            state = str(dump.get("status")).lower()
        return {
            "state": state,
            "result": _extract_result(dump),
            "raw": dump,
        }


def _extract_result(task_dump: dict[str, Any]) -> str:
    """从 A2A task dump 提取 Agent 应答文本：artifacts 优先，其次 agent 消息。"""
    texts: list[str] = []
    for artifact in task_dump.get("artifacts") or []:
        for part in artifact.get("parts") or []:
            text = part.get("text") if isinstance(part, dict) else None
            if text:
                texts.append(text)
    if texts:
        return "\n".join(texts)
    for message in task_dump.get("history") or []:
        role = (message.get("role") or "").lower()
        if role not in ("agent", "model", "assistant"):
            continue
        for part in message.get("parts") or []:
            text = part.get("text") if isinstance(part, dict) else None
            if text:
                texts.append(text)
    return "\n".join(texts)


# ---- 协作运行时（工单收发台 + 引擎恢复 + Pulse 催办 + Reflex 应激）----

class CollabRuntime:
    """协作运行时：建单/派发/交付回流/催办/超时 + 预算隔离 + 控制面接线。

    - 交付回流 → engine.resolve_delivery(run_id, …) 恢复流程（与 approval 同一恢复面）
    - 任务到/交付/催办 → Reflex 事件流（任务到即应激，never-raise 旁路）
    - 超时催办 → Pulse 长任务账（attach_pulse：心跳推进+超时恢复催办）
    """

    def __init__(self, store: WorkOrderStore | None = None,
                 router: RoleRouter | None = None,
                 channel: Any = None,          # A2AChannel 形状（dispatch/fetch，测试可注入）
                 ledger: BudgetLedger | None = None,
                 clock: Callable[[], float] = time.time,
                 notify: Callable[[str, WorkOrder], None] | None = None):
        self.store = store or WorkOrderStore()
        self.router = router or RoleRouter(store=self.store)
        self.channel = channel or A2AChannel()
        self.ledger = ledger or BudgetLedger(clock=clock)
        self._clock = clock
        self._notify = notify
        self._engine = None          # 惰性挂 flow_engine（避免环形 import）
        self._pulse = None
        self.remind_interval_s = float(os.environ.get("COLLAB_REMIND_INTERVAL_S")
                                       or DEFAULT_REMIND_INTERVAL_S)
        self.beat_interval_s = DEFAULT_BEAT_INTERVAL_S   # B3 心跳拍距
        self.max_recoveries = int(os.environ.get("COLLAB_MAX_RECOVERIES") or 3)

    # -- 接线 --

    def attach_engine(self, engine) -> None:
        self._engine = engine

    def _get_engine(self):
        if self._engine is None:
            from plugin.flow_engine import get_engine
            self._engine = get_engine()
        return self._engine

    def attach_pulse(self, loop=None) -> None:
        """接 Pulse 心跳调度：开单登记长任务账（心跳推进/超时恢复催办）。"""
        if loop is None:
            from routes.pulse import get_pulse
            loop = get_pulse()
        self._pulse = loop
        try:
            loop.set_task_overdue_handler(self._on_pulse_overdue)
        except AttributeError:
            loop._on_task_overdue = self._on_pulse_overdue  # 兼容旧 PulseLoop
        for task in self.store.list(limit=10_000):
            if task.is_open():
                self._register_pulse_task(task)

    def _register_pulse_task(self, task: WorkOrder) -> None:
        loop = self._pulse
        if loop is None:
            return
        try:
            from agent.pulse import LongTask
            loop.add_task(LongTask(
                task_id=f"collab:{task.task_id}",
                name=task.title or task.task_id,
                step=self._pulse_step,
                interval_s=min(self.remind_interval_s, 60.0),
                deadline_at=task.due_at or None,
                ctx={"task_id": task.task_id},
            ))
        except Exception as e:  # noqa: BLE001 — 登记失败不阻断工单本身
            logger.warning("Pulse 长任务登记失败 %s: %s", task.task_id, e)

    def _pulse_step(self, ctx: dict) -> dict:
        """心跳推进一步：终态→done；到催办间隔→催办；其余保活。"""
        task = self.store.find(str(ctx.get("task_id") or ""))
        if task is None:
            return {"done": True}
        if task.status in TERMINAL_STATUSES:
            return {"done": True}
        now = self._clock()
        if (task.last_remind_at == 0.0 or now - task.last_remind_at >= self.remind_interval_s) \
                and not task.is_overdue(now):
            self.remind(task.task_id, note="pulse 心跳催办")
        return {"ok": True, "detail": f"status={task.status}"}

    def _on_pulse_overdue(self, task_dict: dict) -> dict:
        """Pulse 超时恢复钩子（复用既有恢复账，勿重建）：判死→requeue；否则催办 defer。"""
        tid = str(task_dict.get("task_id") or "").removeprefix("collab:")
        task = self.store.find(tid)
        if task is None or task.status in TERMINAL_STATUSES:
            return {"action": "attention", "detail": "工单已终态"}
        # B3 三拍判死：领取已死 → 复用 Pulse 既有 requeue 恢复（有界计数在 Pulse 账）
        if self._claim_dead(task):
            self._requeue_offline(task)
            return {"action": "requeue", "detail": "runtime_offline 三拍判死回队"}
        self.remind(tid, note="超时催办")
        recoveries = int(task_dict.get("recoveries") or 0)
        if recoveries + 1 < int(task_dict.get("max_recoveries") or 3):
            return {"action": "defer", "delay_s": self.remind_interval_s,
                    "detail": "已催办，等待交付"}
        return {"action": "attention", "detail": "催办多次未交付，需人工关注"}

    # -- B3 三拍判死（Multica 15s 心跳/45s=3拍 同位；恢复复用既有语义）--

    def _claim_dead(self, task: WorkOrder, now: float | None = None) -> bool:
        """已领取工单是否三拍无心跳判死（心跳即回 online）。"""
        now = self._clock() if now is None else now
        if task.status != STATUS_WORKING:
            return False
        last_seen = max(task.heartbeat_at, task.claim_at)
        return bool(last_seen) and now - last_seen > DEAD_BEATS * self.beat_interval_s

    def _requeue_offline(self, task: WorkOrder) -> WorkOrder:
        """runtime_offline 回队列（可重试来源）：清领取锁，origin=recovery（B2 恢复级）。"""
        def _do(t: WorkOrder) -> None:
            t.status = STATUS_TODO
            t.claim_token = ""
            t.claimed_by = ""
            t.claim_at = 0.0
            t.lease_expires_at = 0.0
            t.origin = "recovery"
            t.requeues += 1
            if t.a2a_task_id:
                t.note("runtime_offline",
                       f"三拍判死（{DEAD_BEATS}×{self.beat_interval_s:g}s 无心跳）"
                       f"→ 回队列 #{t.requeues}（旧 a2a 任务 {t.a2a_task_id} 作废）")
                t.a2a_task_id = ""
            else:
                t.note("runtime_offline",
                       f"三拍判死（{DEAD_BEATS}×{self.beat_interval_s:g}s 无心跳）"
                       f"→ 回队列 #{t.requeues}")
        task, _ = self.store._mutate(task.task_id, _do)
        self._emit("task-requeued", task)
        return task

    def liveness_sweep(self, now: float | None = None) -> dict[str, int]:
        """B3 判死清扫（调度器驱动，与 Pulse 心跳解耦）：三拍无心跳→回队列（有界）。

        requeues < max_recoveries → runtime_offline 回队列（origin=recovery）；
        超限仍失联 → failed 显式收口（绝不静默）。
        """
        now = self._clock() if now is None else now
        requeued = failed = 0
        for task in self.store.list(limit=10_000, now=now):
            if not self._claim_dead(task, now):
                continue
            if task.requeues < self.max_recoveries:
                self._requeue_offline(task)
                requeued += 1
            else:
                self.store.close(task.task_id, STATUS_FAILED,
                                 note=f"三拍判死反复失联（requeues={task.requeues}）")
                self._emit("task-failed", self.store.load(task.task_id))
                failed += 1
        return {"requeued": requeued, "failed": failed}

    # -- E5 上下文卫生（squad nap 对位；接 Pi#5 阶段压缩）--

    def _context_hygiene(self, task: WorkOrder) -> None:
        """交付后任务包上下文归档释放（防逐任务累积）+ run 级压缩指令接 Pi#5。"""
        if task.archived:
            return
        task.archived = True
        if len(task.instruction) > 300:
            task.instruction = task.instruction[:300] + "…（已归档）"
        task.payload.pop("upstream_snapshot", None)
        task.note("context-archived", "任务包上下文归档释放（正文以工单账为准）")
        self.store.save(task)
        try:
            engine = self._get_engine()
            if engine is not None:
                # 既有 compress-handoff 事件（Pi#5）：驱动层 ContextCompressor 接指令
                engine.emit("compress-handoff", {
                    "run_id": task.run_id, "flow_id": task.flow_id,
                    "node": task.node_id, "task_id": task.task_id,
                    "instructions": "任务已交付，任务包上下文压缩归档"
                                    "（正文/台账以工单账为准，不在摘要中摘录）",
                })
        except Exception:  # noqa: BLE001 — 压缩旁路绝不阻断交付
            pass

    def _emit(self, event: str, task: WorkOrder) -> None:
        """通知旁路：Reflex 事件流（任务到即应激）+ 注入式 notify，never-raise。"""
        if self._notify is not None:
            try:
                self._notify(event, task)
            except Exception as e:  # noqa: BLE001
                logger.warning("notify 回调失败: %s", e)
        try:
            from agent.reflex import get_reflex
            get_reflex().emit(
                f"collab.{event}",
                source="flow_collab",
                session_id=task.run_id,
                task_id=task.task_id, assignee=task.assignee,
                task_kind=task.kind, status=task.status,
            )
        except Exception:  # noqa: BLE001 — 应激旁路绝不阻断主流程
            pass

    # -- 建单/派发 --

    def create(self, *, kind: str, run_id: str, flow_id: str, node_id: str,
               assignee: str, role: str = "", assignee_kind: str = "human",
               target_agent: str = "", title: str = "", instruction: str = "",
               form: str = "", timeout_hours: float = 72.0, on_timeout: str = "fail",
               budget: BudgetSpec | dict | None = None,
               payload: dict[str, Any] | None = None,
               created_by: str = "flow") -> WorkOrder:
        """建单（A2 原子建单：同 (run_id, node_id, kind) 开态唯一，防重复执行）。"""
        now = self._clock()
        task, created = self.store.create(
            task_id=f"{TASK_ID_PATTERN_PREFIX}"
                    f"{_dt.datetime.fromtimestamp(now):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}",
            kind=kind, run_id=run_id, node_id=node_id,
            flow_id=flow_id, role=role, assignee=assignee, assignee_kind=assignee_kind,
            target_agent=target_agent, created_by=created_by,
            title=title or instruction[:40],
            instruction=instruction, form=form, payload=dict(payload or {}),
            due_at=now + timeout_hours * 3600, on_timeout=on_timeout,
            budget=asdict(_as_budget(budget)), now=now,
        )
        if created:
            self._register_pulse_task(task)
            self._emit("task-created", task)
            logger.info("协作工单开单: %s (%s → %s/%s, due=%s)",
                        task.task_id, kind, assignee, role or "-", _fmt(task.due_at))
        return task

    def find_by_node(self, run_id: str, node_id: str, kind: str = "") -> WorkOrder | None:
        if kind:
            return self.store.find_open(run_id, node_id, kind)
        for task in self.store.list(limit=10_000):
            if task.run_id == run_id and task.node_id == node_id and task.is_open():
                return task
        return None

    async def dispatch(self, task: WorkOrder) -> WorkOrder:
        """agent 工单派发（A1：先原子领取锁住，锁不住不发 SoulMate）。

        C2 预算闸兜底（引擎侧另有预算闸转人工）；A4 在途合并防双发；
        C4 先写交接标记再出网（防双发双扣，重启可辨"发过没回"）。
        """
        if task.kind != KIND_AGENT:
            return task
        # B1 唤醒三态（纯函数决策）：已关/被取代→cancel 幂等；同任务在途→coalesce 不双发
        decision = wake_decision({
            "closed": task.status in TERMINAL_STATUSES,
            "in_flight": bool(task.a2a_task_id)
            or (task.status == STATUS_WORKING
                and task.lease_expires_at > self._clock()),
            "same_task": True,
        })
        if decision["action"] != "proceed":
            task.note("wake-decision", f"{decision['action']}: {decision['reason']}")
            self.store.save(task)
            return task
        # C2 派发前预算闸（隔离：按 scope 拦截，其他员工 Agent 不受影响）
        reasons = self.ledger.blocked(assignee=task.assignee, flow_id=task.flow_id)
        if reasons and not self.ledger.take_waiver(f"{task.run_id}:{task.node_id}"):
            task.status = STATUS_QUEUED
            task.note("budget-blocked", "; ".join(reasons)[:300])
            self.store.save(task)
            self._emit("task-throttled", task)
            return task
        # A1 原子领取：条件转移抢锁，抢不到显式报因不派发
        try:
            task, _token = self.store.claim(
                task.task_id,
                claimed_by=f"soulmate:{task.target_agent or task.assignee}",
                now=self._clock())
        except CollabError as e:
            # B1 唤醒三态：锁被他人占着 → defer 排队语义（显式留痕后按失败语义上抛）
            d = wake_decision({"in_flight": True, "same_task": False})
            task.note("wake-decision", f"{d['action']}: {d['reason']}（{e}）")
            self.store.save(task)
            raise
        # C4 交接标记先落盘，再出网（防双发双扣）
        task.handed_off_at = self._clock()
        task.note("dispatch-handoff", f"标记后派发 target={task.target_agent}")
        self.store.save(task)
        try:
            out = await self.channel.dispatch(
                target_agent=task.target_agent or "soulmate",
                instruction=task.instruction,
                trace_id=f"collab-{task.task_id}",
            )
        except Exception as e:  # noqa: BLE001 — 派发失败显式记单，可重试
            task.note("dispatch-failed", f"{type(e).__name__}: {e}")
            self.store.save(task)
            raise
        task.a2a_task_id = out.get("a2a_task_id") or ""
        task.note("dispatched", f"a2a_task_id={task.a2a_task_id} status={out.get('status')}")
        self.store.save(task)
        self._emit("task-dispatched", task)
        if is_input_required(str(out.get("state") or ""), out.get("result")):
            # INPUT_REQUIRED 转人工（agent_node 四补之一）：缺输入≠失败，开人工补输入单
            await self._convert_to_human_input(task, out)
            return self.store.load(task.task_id)
        if out.get("result"):
            # 确定性处理端同步回结果：直接交付回流（带领取令牌=CAS 回执）
            await self.deliver_and_resume(task.task_id, out["result"],
                                          delivered_by=task.target_agent, via="a2a",
                                          claim_token=task.claim_token)
            task = self.store.load(task.task_id)
        return task

    # -- 交付回流（流程恢复）--

    async def deliver_and_resume(self, task_id: str, result: Any,
                                 delivered_by: str = "", via: str = "api",
                                 claim_token: str = "") -> WorkOrder:
        """交付 + 回流恢复流程（与 approval 闸同一恢复面 resolve_delivery）。"""
        task, dup = self.store.deliver(task_id, result, delivered_by=delivered_by,
                                       claim_token=claim_token, via=via)
        if dup:
            return task  # 幂等：绝不二次恢复
        self._emit("task-delivered", task)
        self._context_hygiene(task)   # E5 交付即归档任务包上下文（防逐任务累积）
        engine = self._get_engine()
        if task.run_id and engine is not None:
            try:
                if task.kind == KIND_APPROVAL:
                    await engine.resolve_approval(
                        task.run_id, "approve", decided_by=task.delivered_by,
                        note=str(result or "")[:500])
                else:
                    await engine.resolve_delivery(
                        task.run_id, result, decided_by=task.delivered_by)
            except Exception as e:  # noqa: BLE001 — 独立工单/实例不在：显式留痕不吞交付
                if type(e).__name__ == "FlowRunError":
                    task.note("resume-skipped", f"流程实例不可恢复: {e}")
                    self.store.save(task)
                else:
                    raise
        return task

    def remind(self, task_id: str, note: str = "",
               now: float | None = None) -> WorkOrder:
        task, did = self.store.remind(task_id, note=note,
                                      min_interval_s=self.remind_interval_s,
                                      now=self._clock() if now is None else now)
        if did:
            self._emit("task-reminded", task)
        return task

    # -- agent 结果轮询 / 超时账 --

    async def _convert_to_human_input(self, task: WorkOrder,
                                      out: dict[str, Any]) -> None:
        """INPUT_REQUIRED → 转人工（agent_node 四补之一）：SoulMate 卡在缺输入——
        开人工补输入单（同一 run/node 恢复面：人交付答案=该节点交付回流），
        agent 单收口取消（留痕，绝不当失败静默吞）。"""
        question = str(out.get("result") or out.get("message") or "").strip() \
            or "SoulMate 需要补充输入才能继续"
        task.note("input-required", f"转人工补输入: {question[:200]}")
        self.store.save(task)
        hours_left = 72.0
        if task.due_at:
            hours_left = max(1.0, (task.due_at - time.time()) / 3600)
        human_task = self.create(
            kind=KIND_HUMAN, run_id=task.run_id, flow_id=task.flow_id,
            node_id=task.node_id, assignee=task.assignee or "用户",
            role=task.role, assignee_kind="human",
            title=f"补输入：{task.title or task.node_id}",
            instruction=f"SoulMate（{task.target_agent or task.assignee}）"
                        f"需要补充输入才能继续：\n{question}",
            form="补充输入/确认（交付=回答）",
            timeout_hours=hours_left, on_timeout=task.on_timeout,
            payload={"converted_from": task.task_id, "kind": "input-required"})
        self.store.close(task.task_id, STATUS_CANCELLED,
                         note="input-required 转人工")
        engine = self._get_engine()
        if engine is not None and task.run_id:
            try:
                engine.reattach_gate_task(task.run_id, task.node_id, human_task)
            except Exception as e:  # noqa: BLE001 — 重挂闸失败留痕不吞
                logger.warning("input-required 重挂闸失败 %s: %s", task.task_id, e)
        self._emit("task-input-required", human_task)
        logger.info("INPUT_REQUIRED 转人工: %s → %s", task.task_id, human_task.task_id)

    async def poll_agent_tasks(self) -> int:
        """agent 工单回流轮询：completed → 交付恢复；failed → 单据 failed 显式报因。

        回流落库前校验领取归属（A3）：被他人抢占（claimed_by 非派发 SoulMate）
        → 拒绝回流留痕，绝不覆盖新领取者的工作。
        """
        delivered = 0
        for task in self.store.list(kind=KIND_AGENT, limit=200):
            # B3 恢复重派：判死回队的可重试单（requeues>0 且无在途）→ 重新派发
            if task.status == STATUS_TODO and task.requeues > 0 and not task.a2a_task_id:
                try:
                    await self.dispatch(task)
                except Exception as e:  # noqa: BLE001 — 重派失败留痕下拍再试
                    task.note("redispatch-failed", f"{type(e).__name__}: {e}")
                    self.store.save(task)
                continue
            if task.status not in (STATUS_TODO, STATUS_WORKING) or not task.a2a_task_id:
                continue
            if not task.claimed_by.startswith("soulmate:"):
                task.note("deliver-decision",
                          "rejected: superseded（已被他人抢占，agent 回流作废）")
                self.store.save(task)
                continue
            snapshot_token = task.claim_token
            try:
                out = await self.channel.fetch(task.a2a_task_id)
            except Exception as e:  # noqa: BLE001 — 轮询失败留痕，下拍再试
                task.note("poll-failed", f"{type(e).__name__}: {e}")
                self.store.save(task)
                continue
            state = str(out.get("state") or "").lower()
            if state in ("completed", "success", "succeeded"):
                try:
                    await self.deliver_and_resume(
                        task.task_id, out.get("result") or "",
                        delivered_by=task.target_agent or "agent", via="a2a",
                        claim_token=snapshot_token)
                except CollabError as e:  # noqa: BLE001 — CAS 拒绝（被抢占）留痕跳过
                    task.note("deliver-decision", f"rejected: {e}")
                    self.store.save(task)
                    continue
                delivered += 1
            elif state in ("failed", "canceled", "cancelled", "rejected"):
                self.store.close(task.task_id, STATUS_FAILED,
                                 note=f"a2a task {state}: {(out.get('result') or '')[:200]}")
                self._emit("task-failed", self.store.load(task.task_id))
                delivered += 1  # 让引擎侧超时账同步收口
            elif is_input_required(state, out.get("result")):
                # INPUT_REQUIRED 转人工（agent_node 四补之一）：缺输入≠失败
                await self._convert_to_human_input(task, out)
                delivered += 1
        return delivered

    def check_timeouts(self, now: float | None = None) -> dict[str, int]:
        """超时账（引擎侧同步）：过期催办 + B3 三拍判死清扫（调度器独立于 Pulse）。"""
        now = self._clock() if now is None else now
        reminded = 0
        for task in self.store.list(limit=10_000, now=now):
            if not task.is_overdue(now):
                continue
            updated, did = self.store.remind(task.task_id, note="超时催办",
                                             min_interval_s=self.remind_interval_s, now=now)
            if did:
                reminded += 1
                self._emit("task-reminded", updated)
        alive = self.liveness_sweep(now)
        return {"reminded": reminded,
                "requeued": alive.get("requeued", 0),
                "failed": alive.get("failed", 0)}

    def sync_timeout_close(self, task_id: str, on_timeout: str) -> WorkOrder:
        """引擎超时处置同步收口单据：fail→failed，skip→cancelled。"""
        status = STATUS_FAILED if on_timeout == "fail" else STATUS_CANCELLED
        return self.store.close(task_id, status, note=f"超时处置 on_timeout={on_timeout}")


# ---- 进程级单例 ----

_runtime: CollabRuntime | None = None
_runtime_lock = threading.Lock()


def get_collab() -> CollabRuntime:
    """进程级协作运行时单例（FLOW_RUNS_DIR/COLLAB_TASKS_DIR 可覆盖——测试隔离）。"""
    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = CollabRuntime()
        return _runtime


def reset_collab() -> None:
    """测试用：重置单例（目录/环境变量改后生效）。"""
    global _runtime
    with _runtime_lock:
        _runtime = None


# ---- 工具 ----

def _fmt(ts: float | None) -> str:
    if not ts:
        return "-"
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
