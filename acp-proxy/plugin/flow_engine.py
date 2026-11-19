"""
流程引擎运行时 — 实例执行 / 幂等 / 失败重试 / 人工审批闸 / 可观测运行日志 / 回放

设计文档: docs/plugin-system-design.md §5
- 触发器：cron（定时）/ event（事件）/ manual（面板按钮、对话「跑一下」）
- 节点：tool（MCP 工具）/ decision（表达式分支）/ loop（逐项）/ approval（人工审批）/ action（收尾动作）
- 幂等：触发级 idempotency_key 去重（cron 按分钟、event 按载荷哈希）；实例状态落 JSON 文件，断点续跑
- 失败重试：节点级 retry（max/backoff/base_seconds）+ on_error(fail|skip|goto:<node>)
- 超时：节点 timeout_seconds 是**软预算**——超时只记 over_budget 日志，绝不杀执行（用户铁律：禁硬超时杀）
- 可观测：每实例运行日志（节点状态/尝试/重试/耗时/输出摘要/事件时间线），支持回放（replay）
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from plugin.flow_def import (
    EvalContext,
    ExprError,
    FlowDefError,
    FlowDefinition,
    NodeSpec,
    cron_matches,
    dump_flow,
    eval_expr,
    load_flow_file,
    parse_flow,
    resolve_value,
)
from plugin.verify_axis import (
    CLAIM_EVIDENCE,
    CLAIM_SELF,
    REJECT_ACCEPTANCE_MISSES,
    REJECT_MISSING_DELIVERABLE,
    REJECT_NO_EVIDENCE,
    REJECT_SELF_VERIFY,
    REJECT_UNKNOWN_VERIFIER,
    SETTLE_MAX_FIXES,
    can_verify,
    node_axis,
    node_confidence,
    reject_fixable,
    run_verdict,
    settle_decision,
)

logger = logging.getLogger("plugin.flow_engine")

# 工具调用注入点：async (tool_name, args, approved) -> dict（宿主接 MCP 工具注册桥）
# approved=True = 本实例已过人工审批闸（放行 approval 类工具，token 语义按实例）
ToolCaller = Callable[[str, dict[str, Any], bool], Awaitable[dict[str, Any]]]

NODE_STATUS_PENDING = "pending"
NODE_STATUS_RUNNING = "running"
NODE_STATUS_OK = "ok"
NODE_STATUS_FAILED = "failed"
NODE_STATUS_SKIPPED = "skipped"

# 子代理治理运行时基线（Pi #14）：parallel 节点默认基础并发与统一轮数上限
PARALLEL_BASE_CONCURRENCY = 4      # 实际并发上限 = 基础 × 2（SubagentGovernor 乘数）
SUBAGENT_MAX_TURNS = 100           # 全分支共享统一轮数上限（Pi CHILD_AGENT_MAX_TURNS_PER_PROMPT）
NODE_STATUS_WAITING = "waiting-approval"

RUN_STATUS_RUNNING = "running"
RUN_STATUS_WAITING = "waiting-approval"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"


class FlowRunError(RuntimeError):
    """流程实例级错误（实例不存在、状态不允许该操作）"""


class _FlowAbort(Exception):
    """内部：流程实例按 on_error=fail 终止"""


# ---- 实例模型 ----

@dataclass
class NodeRun:
    node_id: str
    type: str = ""
    status: str = NODE_STATUS_PENDING
    attempts: int = 0            # 总尝试次数（含首次）
    retries: int = 0             # 重试次数（不含首次）
    started_at: float | None = None
    finished_at: float | None = None
    duration_ms: float | None = None
    error: str | None = None
    output_summary: str | None = None
    loop_items: int | None = None
    over_budget: bool = False    # 软超时预算超出（只记录，不杀）
    # ── 双轴完成状态（监督闭环：reported done ≠ verified）──
    executed_by: str = ""        # 执行者身份（tool:<name> / 交付人 / SoulMate）
    claim_source: str = CLAIM_SELF   # self-report（自报封顶黄）| evidence（验收翻绿）
    verified: bool = False       # Test·verified 翻档标记（只由验收证据翻）
    verified_by: str = ""        # 验收者（≠执行者才许翻档）
    verified_at: float | None = None
    verify_evidence: str = ""    # 内容级探针/验收证据摘要（报成功没用）
    settle_rejects: int = 0      # settle 拒伪完成次数（留痕计数）
    fix_rounds: int = 0          # 验证不过修复轮（≤2，超出→重规划）
    owned_by: str = ""           # 归属节点（loop body / parallel 分支合成件：不独立判档）

    # 只读派生（不改状态机，只加渲染——fail-closed：未知=未验证）
    @property
    def status_axis(self) -> str:
        return node_axis(self.status, self.verified)

    @property
    def confidence(self) -> str:
        return node_confidence(self.status, self.verified)


@dataclass
class ApprovalRecord:
    node_id: str
    status: str = "pending"      # pending | approved | rejected | timed-out
    created_at: float = 0.0
    timeout_hours: float = 72.0
    on_timeout: str = "fail"
    decided_at: float | None = None
    decided_by: str = ""
    note: str = ""
    # 协作工单整合（agent/human 节点与 approval 同一挂起/恢复面）：
    kind: str = "approval"       # approval | human | agent
    task_id: str = ""            # 关联工单号（收发台可见/可交付）
    assignee: str = ""
    role: str = ""
    result: Any = None           # 交付物（human/agent 交付回流）


@dataclass
class FlowRun:
    run_id: str
    flow_id: str
    flow_name: str = ""
    plugin_id: str = ""
    status: str = RUN_STATUS_RUNNING
    trigger: dict[str, Any] = field(default_factory=dict)
    inputs: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    replay_of: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float | None = None
    error: str | None = None
    cursor: str | None = None            # 断点续跑：下一个待执行节点
    outputs: dict[str, Any] = field(default_factory=dict)   # 节点 id → 输出
    nodes: dict[str, NodeRun] = field(default_factory=dict)  # 节点 id → 记录
    log: list[dict[str, Any]] = field(default_factory=list)  # 事件时间线
    approval: ApprovalRecord | None = None
    notifications: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    def verdict(self) -> dict[str, Any]:
        """实例判档（双轴）：verified | done·unverified+理由码 | 进行态原词。"""
        return run_verdict(self.status, self.nodes.values())

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "FlowRun":
        nodes = {
            nid: NodeRun(**record) for nid, record in (data.get("nodes") or {}).items()
        }
        approval = data.get("approval")
        return FlowRun(
            run_id=data["run_id"],
            flow_id=data["flow_id"],
            flow_name=data.get("flow_name", ""),
            plugin_id=data.get("plugin_id", ""),
            status=data.get("status", RUN_STATUS_RUNNING),
            trigger=data.get("trigger") or {},
            inputs=data.get("inputs") or {},
            idempotency_key=data.get("idempotency_key", ""),
            replay_of=data.get("replay_of", ""),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            finished_at=data.get("finished_at"),
            error=data.get("error"),
            cursor=data.get("cursor"),
            outputs=data.get("outputs") or {},
            nodes=nodes,
            log=data.get("log") or [],
            approval=ApprovalRecord(**approval) if approval else None,
            notifications=data.get("notifications") or [],
        )


# ---- 实例存储（JSON 文件，一实例一文件 + 幂等键表）----

class FlowStore:
    """流程实例落盘：<runs_dir>/<flow_id>/<run_id>.json + _keys.json（幂等键）"""

    def __init__(self, runs_dir: Path | str):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def _flow_dir(self, flow_id: str) -> Path:
        d = self.runs_dir / flow_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_path(self, flow_id: str, run_id: str) -> Path:
        return self._flow_dir(flow_id) / f"{run_id}.json"

    def save(self, run: FlowRun) -> None:
        run.updated_at = time.time()
        path = self._run_path(run.flow_id, run.run_id)
        # 唯一临时名：触达线程与执行线程并发保存不互踩（原子替换，后写胜）
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        tmp.write_text(
            json.dumps(run.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(path)

    def load(self, flow_id: str, run_id: str) -> FlowRun:
        path = self._run_path(flow_id, run_id)
        if not path.is_file():
            raise FlowRunError(f"流程实例不存在: {run_id}")
        return FlowRun.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_runs(self, flow_id: str | None = None, status: str | None = None,
                  limit: int = 50, since: float | None = None,
                  until: float | None = None) -> list[FlowRun]:
        runs: list[FlowRun] = []
        flow_dirs = [self.runs_dir / flow_id] if flow_id else [
            d for d in sorted(self.runs_dir.iterdir()) if d.is_dir()
        ]
        for flow_dir in flow_dirs:
            if not flow_dir.is_dir():
                continue
            for path in flow_dir.glob("*.json"):
                if path.name.startswith("_"):
                    continue
                try:
                    run = FlowRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, KeyError, TypeError):
                    continue
                if status and run.status != status:
                    continue
                if since is not None and run.created_at < since:
                    continue
                if until is not None and run.created_at > until:
                    continue
                runs.append(run)
        runs.sort(key=lambda r: r.created_at, reverse=True)
        return runs[:limit]

    def find_run(self, run_id: str) -> FlowRun | None:
        for flow_dir in sorted(self.runs_dir.iterdir()):
            if not flow_dir.is_dir():
                continue
            path = flow_dir / f"{run_id}.json"
            if path.is_file():
                return FlowRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
        return None

    def delete_run(self, flow_id: str, run_id: str) -> None:
        self._run_path(flow_id, run_id).unlink(missing_ok=True)

    def purge_flow(self, flow_id: str) -> int:
        """清空一条流程的全部实例与幂等键（数据侧彻底清理，返回清掉的实例数）"""
        flow_dir = self.runs_dir / flow_id
        if not flow_dir.is_dir():
            return 0
        count = sum(1 for p in flow_dir.glob("*.json") if not p.name.startswith("_"))
        shutil.rmtree(flow_dir, ignore_errors=True)
        return count

    # 幂等键（跨进程/重启仍生效）
    def _keys_path(self, flow_id: str) -> Path:
        return self._flow_dir(flow_id) / "_keys.json"

    def lookup_key(self, flow_id: str, key: str) -> str | None:
        if not key:
            return None
        path = self._keys_path(flow_id)
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get(key)

    def put_key(self, flow_id: str, key: str, run_id: str) -> None:
        if not key:
            return
        path = self._keys_path(flow_id)
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        data[key] = run_id
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def trim(self, flow_id: str, retain: int) -> int:
        """按 retain_runs 保留最近实例（可观测配置）"""
        flow_dir = self._flow_dir(flow_id)
        runs = []
        for path in flow_dir.glob("*.json"):
            if path.name.startswith("_"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                runs.append((data.get("created_at", 0.0), path))
            except (json.JSONDecodeError, TypeError):
                continue
        runs.sort(key=lambda item: item[0], reverse=True)
        removed = 0
        for _, path in runs[retain:]:
            path.unlink(missing_ok=True)
            removed += 1
        return removed


# ---- 执行器 ----

@dataclass
class _NodeOutcome:
    next_id: str | None
    suspended: bool = False   # approval 挂起：停止推进，等人工恢复


class FlowExecutor:
    """单实例执行器：顺序推进 + decision 跳转 + loop 逐项 + approval 挂起/恢复"""

    def __init__(self, engine: "FlowEngine"):
        self.engine = engine

    async def execute(self, run: FlowRun, start_at: str | None = None) -> FlowRun:
        defn = self.engine.get_flow(run.flow_id)
        if run.status in (RUN_STATUS_COMPLETED, RUN_STATUS_FAILED):
            return run
        if run.status == RUN_STATUS_WAITING and run.approval and run.approval.status == "pending":
            return run  # 等人工审批，不推进
        self._log(run, start_at or run.cursor or "-", "run-resume" if run.log else "run-start",
                  f"trigger={run.trigger.get('type', '?')}")
        run.status = RUN_STATUS_RUNNING
        self.engine.store.save(run)
        current = start_at or run.cursor or defn.first_node_id()
        try:
            while current:
                node = defn.node_map.get(current)
                if node is None:
                    raise _FlowAbort(f"节点不存在: {current}")
                try:
                    outcome = await self._run_node(run, defn, node)
                except _NodeJump as jump:
                    # on_error=goto:<node>：跳转到目标节点继续推进（不重入执行器）
                    target = str(jump)
                    self._log(run, node.id, "goto", f"跳转 → {target}")
                    current = target
                    continue
                if outcome.suspended:
                    return run  # 等人工审批：实例落盘挂起，不推进
                current = outcome.next_id
            run.status = RUN_STATUS_COMPLETED
            run.finished_at = time.time()
            run.cursor = None
            self._log(run, "-", "run-ok", f"耗时 {self._total_ms(run):.0f}ms")
        except _FlowAbort as e:
            run.status = RUN_STATUS_FAILED
            run.finished_at = time.time()
            run.error = str(e)
            self._log(run, "-", "run-failed", str(e))
        self.engine.store.save(run)
        self.engine._trim(run.flow_id)
        # 结果触达（状态迁移点恰好一次；重试后再失败是新迁移，会再触达）
        if run.status == RUN_STATUS_COMPLETED:
            self.engine.notify_run(run, "completed", dedupe_key=str(run.finished_at))
        elif run.status == RUN_STATUS_FAILED:
            self.engine.notify_run(run, "failed", node=_last_failed_node(run),
                                   dedupe_key=str(run.finished_at))
        return run

    # ── 节点执行 ──

    async def _run_node(self, run: FlowRun, defn: FlowDefinition, node: NodeSpec) -> _NodeOutcome:
        if node.type in ("approval", "human", "agent") and run.approval \
                and run.approval.node_id == node.id and run.approval.status == "pending":
            # 断点/跳转重入到待决闸：幂等重挂（不重建工单不重置超时时钟），绝不推进
            self._suspend_for_approval(run, node)
            return _NodeOutcome(next_id=None, suspended=True)

        record = self._record(run, node)
        record.status = NODE_STATUS_RUNNING
        record.started_at = time.time()
        if node.type == "tool" and not record.executed_by:
            record.executed_by = f"tool:{node.tool}"   # 双轴：执行者身份（验收防自审用）
        self._log(run, node.id, "node-start", f"type={node.type}")
        self.engine.store.save(run)

        suspended = False
        try:
            if node.type == "tool":
                output = await self._run_tool_node(run, defn, node, record)
                suspended = bool(isinstance(output, dict) and output.get("waiting"))
            elif node.type == "decision":
                output = self._run_decision(run, defn, node, record)
            elif node.type == "loop":
                output = await self._run_loop(run, defn, node, record)
            elif node.type == "parallel":
                output = await self._run_parallel(run, defn, node, record)
            elif node.type == "verify":
                output = await self._run_verify_node(run, defn, node, record)
            elif node.type == "approval":
                output = self._suspend_for_approval(run, node)
                suspended = True
            elif node.type in ("human", "agent"):
                if node.type == "human":
                    output = await self._run_human_node(run, defn, node, record)
                else:
                    output = await self._run_agent_node(run, defn, node, record)
                # 挂起以 {"waiting": True} 为准；on_error=skip/goto 失败路径不挂起
                suspended = bool(isinstance(output, dict) and output.get("waiting"))
            else:  # action
                output = self._run_action(run, defn, node, record)
        except _FlowAbort:
            record.status = NODE_STATUS_FAILED
            record.finished_at = time.time()
            raise

        run.outputs[node.id] = output
        record.finished_at = time.time()
        record.duration_ms = (record.finished_at - record.started_at) * 1000
        record.output_summary = _summarize(output)
        if record.status == NODE_STATUS_RUNNING:
            # 只有执行路径未另行定级（skip/failed/waiting）时才落 ok
            record.status = NODE_STATUS_OK
            self._log(run, node.id, "node-ok",
                      f"{record.duration_ms:.0f}ms out={record.output_summary}")
        self.engine.store.save(run)
        next_id = None if suspended else self._next_for(run, defn, node)
        return _NodeOutcome(next_id=next_id, suspended=suspended)

    def _next_for(self, run: FlowRun, defn: FlowDefinition, node: NodeSpec) -> str | None:
        """节点后继：decision 按分支、verify 修复轮回跳目标、其余顺序推进"""
        if node.type == "decision":
            branch = (run.outputs.get(node.id) or {}).get("branch")
            return node.then if branch == "then" else node.else_
        if node.type == "verify":
            fix_goto = (run.outputs.get(node.id) or {}).get("fix_goto")
            if fix_goto:
                return str(fix_goto)   # 验证不过修复轮：回跳被验收节点重跑
        return defn.next_node_id(node.id)

    # ── tool 节点（重试 + 软超时预算）──

    async def _run_tool_node(self, run: FlowRun, defn: FlowDefinition,
                             node: NodeSpec, record: NodeRun) -> Any:
        ctx = EvalContext(outputs=run.outputs, run=_run_ctx(run))
        args = resolve_value(node.args, ctx)
        approved = _run_approved(run)
        attempt = 0
        while True:
            attempt += 1
            record.attempts = attempt
            started = time.time()
            self._log(run, node.id, "attempt",
                      f"#{attempt} tool={node.tool} args={_summarize(args)}")
            try:
                output = await self.engine.call_tool(
                    node.tool, args if isinstance(args, dict) else {}, approved=approved,
                    ctx={"run_id": run.run_id, "flow_id": run.flow_id,
                         "node_id": node.id, "role": node.role})
            except Exception as e:  # noqa: BLE001 — 节点失败细分报因，按 retry/on_error 处理
                elapsed_ms = (time.time() - started) * 1000
                self._check_budget(run, node, record, elapsed_ms)
                if type(e).__name__ == "ApprovalRequired":
                    # 工具级安全审批未放行：重试无意义，显式报因（流程内先放 approval 节点）
                    record.error = f"{e}"
                    self._log(run, node.id, "node-failed",
                              f"工具需用户审批（未放行）: {e}")
                    return self._on_error(run, defn, node, record)
                if type(e).__name__ == "ToolGovernanceNeedsApproval":
                    if node.id in defn.node_map:
                        # F1 require_approval 转 human_node：人工放行后原地续跑（真闸）
                        gate_task = self._open_tool_gate_task(run, node, str(e))
                        self._log(run, node.id, "tool-gate",
                                  f"策略要求人工放行 {node.tool}：{e}"
                                  f" → 转人工（工单 {getattr(gate_task, 'task_id', '-')}）")
                        return self._suspend_for_approval(
                            run, node, kind="tool-gate", task=gate_task,
                            resume_at=node.id)
                    # parallel 合成节点不挂全局闸：显式报因按分支隔离处置
                    record.error = f"{e}"
                    self._log(run, node.id, "node-failed",
                              f"策略要求人工放行（合成节点不挂全局闸）: {e}")
                    return self._on_error(run, defn, node, record)
                if type(e).__name__ == "ToolGovernanceBlocked":
                    # 治理拦截：重试无意义，按 on_error 处置
                    record.error = f"{e}"
                    self._log(run, node.id, "node-failed",
                              f"治理策略拦截（重试无意义）: {e}")
                    return self._on_error(run, defn, node, record)
                if attempt <= node.retry.max:
                    record.retries += 1
                    delay = node.retry.delay(attempt)
                    self._log(run, node.id, "retry",
                              f"第{record.retries}次重试失败: {e}，{delay:.1f}s 后重试")
                    await asyncio.sleep(delay)
                    continue
                record.error = f"{type(e).__name__}: {e}"
                self._log(run, node.id, "node-failed",
                          f"尝试{attempt}次后失败: {record.error}")
                return self._on_error(run, defn, node, record)
            elapsed_ms = (time.time() - started) * 1000
            self._check_budget(run, node, record, elapsed_ms)
            return output

    def _check_budget(self, run: FlowRun, node: NodeSpec, record: NodeRun, elapsed_ms: float) -> None:
        """软超时预算：只记录不杀（禁硬超时杀铁律）"""
        if node.timeout_seconds and elapsed_ms > node.timeout_seconds * 1000:
            record.over_budget = True
            self._log(run, node.id, "over-budget",
                      f"耗时 {elapsed_ms:.0f}ms 超出预算 {node.timeout_seconds:.0f}s（只记录不杀）")

    def _on_error(self, run: FlowRun, defn: FlowDefinition,
                  node: NodeSpec, record: NodeRun) -> Any:
        """retry 耗尽后按 on_error 处理：fail | skip | goto:<node>"""
        if node.on_error == "skip":
            record.status = NODE_STATUS_SKIPPED
            self._log(run, node.id, "node-skipped", f"on_error=skip: {record.error}")
            run.outputs[node.id] = {"skipped": True, "error": record.error}
            return run.outputs[node.id]
        if node.on_error.startswith("goto:"):
            target = node.on_error.split(":", 1)[1]
            self._log(run, node.id, "node-failed-goto", f"on_error=goto:{target}")
            run.outputs[node.id] = {"failed": True, "error": record.error}
            record.status = NODE_STATUS_FAILED
            record.finished_at = time.time()
            raise _NodeJump(target)
        raise _FlowAbort(f"节点 {node.id} 失败: {record.error}")

    # ── decision 节点 ──

    def _run_decision(self, run: FlowRun, defn: FlowDefinition,
                      node: NodeSpec, record: NodeRun) -> Any:
        ctx = EvalContext(outputs=run.outputs, run=_run_ctx(run))
        try:
            result = bool(eval_expr(node.when, ctx))
        except ExprError as e:
            record.error = f"表达式求值失败: {e}"
            raise _FlowAbort(f"节点 {node.id} {record.error}")
        branch = "then" if result else "else"
        self._log(run, node.id, "decision", f"when={node.when!r} → {branch}（{node.then if result else node.else_}）")
        return {"branch": branch, "when": node.when, "value": result}

    # ── loop 节点 ──

    async def _run_loop(self, run: FlowRun, defn: FlowDefinition,
                        node: NodeSpec, record: NodeRun) -> Any:
        ctx = EvalContext(outputs=run.outputs, run=_run_ctx(run))
        items = resolve_value(node.over, ctx)
        if not isinstance(items, list):
            record.error = f"loop.over 未解析为列表: {node.over!r}"
            raise _FlowAbort(f"节点 {node.id} {record.error}")
        record.loop_items = len(items)
        self._log(run, node.id, "loop-start", f"over={node.over} 共 {len(items)} 项")
        item_outputs: list[Any] = []
        for index, item in enumerate(items):
            self._log(run, node.id, "loop-item", f"第 {index + 1}/{len(items)} 项")
            for body_id in node.body:
                body_node = defn.node_map[body_id]
                self._record(run, body_node).owned_by = node.id   # body 归 loop 节点判档
                item_ctx = EvalContext(outputs=run.outputs, item=item, run=_run_ctx(run))
                item_outputs.append(
                    await self._run_body_item(run, defn, body_node, item_ctx, index)
                )
        return {"items": item_outputs, "count": len(item_outputs)}

    async def _run_body_item(self, run: FlowRun, defn: FlowDefinition,
                             body: NodeSpec, ctx: EvalContext, index: int) -> Any:
        """loop body 单项执行（同样走 retry/on_error）"""
        record = self._record(run, body)
        record.started_at = record.started_at or time.time()
        args = resolve_value(body.args, ctx)
        approved = _run_approved(run)
        attempt = 0
        while True:
            attempt += 1
            record.attempts += 1
            self._log(run, body.id, "attempt",
                      f"loop#{index + 1} #{attempt} tool={body.tool} args={_summarize(args)}")
            try:
                output = await self.engine.call_tool(
                    body.tool, args if isinstance(args, dict) else {}, approved=approved,
                    ctx={"run_id": run.run_id, "flow_id": run.flow_id,
                         "node_id": body.id, "role": body.role})
                record.status = NODE_STATUS_OK
                record.finished_at = time.time()
                record.output_summary = _summarize(output)
                return output
            except Exception as e:  # noqa: BLE001
                if attempt <= body.retry.max:
                    record.retries += 1
                    delay = body.retry.delay(attempt)
                    self._log(run, body.id, "retry",
                              f"loop#{index + 1} 第{record.retries}次重试失败: {e}，{delay:.1f}s 后重试")
                    await asyncio.sleep(delay)
                    continue
                record.error = f"{type(e).__name__}: {e}"
                record.finished_at = time.time()
                self._log(run, body.id, "node-failed",
                          f"loop#{index + 1} 尝试{attempt}次后失败: {record.error}")
                if body.on_error == "skip":
                    record.status = NODE_STATUS_SKIPPED
                    return {"skipped": True, "error": record.error}
                record.status = NODE_STATUS_FAILED
                if body.on_error.startswith("goto:"):
                    raise _NodeJump(body.on_error.split(":", 1)[1])
                raise _FlowAbort(f"节点 {body.id} 失败: {record.error}")

    # ── approval / human / agent 闸（挂起等人工或 SoulMate 交付，恢复同一面）──

    def _suspend_for_approval(self, run: FlowRun, node: NodeSpec,
                              kind: str = "approval", task: Any = None,
                              resume_at: str | None = None) -> Any:
        """闸挂起（幂等：同节点已挂不重建工单/不重置超时时钟）。

        approval 也开工单进收件箱（kind=approval，与 human 节点整合：
        审批/交付共用工单账、收发台可见、超时催办同一套）。
        resume_at：恢复起点（缺省=后继节点；预算闸=本节点原地续跑）。
        """
        record = self._record(run, node)
        record.status = NODE_STATUS_WAITING
        run.status = RUN_STATUS_WAITING
        if run.approval is not None and run.approval.node_id == node.id \
                and run.approval.status == "pending":
            # 重入：保留原记录与超时时钟
            self.engine.store.save(run)
            return {"waiting": True, "node": node.id, "kind": run.approval.kind,
                    "task_id": run.approval.task_id, "form": node.form}
        if task is None:
            task = self._open_gate_task(run, node, kind)
        run.approval = ApprovalRecord(
            node_id=node.id,
            status="pending",
            created_at=time.time(),
            timeout_hours=node.timeout_hours,
            on_timeout=node.on_timeout,
            kind=kind,
            task_id=getattr(task, "task_id", ""),
            assignee=getattr(task, "assignee", ""),
            role=getattr(task, "role", ""),
        )
        run.cursor = resume_at or self.engine.get_flow(run.flow_id).next_node_id(node.id)
        self._log(run, node.id, "approval-pending",
                  f"等待{'人工审批' if kind == 'approval' else '交付'}"
                  f"（kind={kind} task={run.approval.task_id or '-'} "
                  f"assignee={run.approval.assignee or '-'} "
                  f"超时 {node.timeout_hours:g}h，on_timeout={node.on_timeout}）")
        self.engine.store.save(run)
        # 结果触达：「该我审批了」（新建审批记录恰好一次，幂等重挂不重复发）
        self.engine.notify_run(
            run, "waiting-approval", node=node.id,
            detail=node.form or ("交付" if kind not in ("approval",) else "审批"),
            dedupe_key=f"{node.id}:{run.approval.created_at}")
        return {"waiting": True, "node": node.id, "kind": kind,
                "task_id": run.approval.task_id, "form": node.form}

    def _open_gate_task(self, run: FlowRun, node: NodeSpec, kind: str):
        """闸开单进收件箱（approval/human/agent 共用；失败只记日志不阻断挂起）。"""
        try:
            collab = self.engine.collab()
            assignee, role = collab.router.resolve(
                role=node.role, user=node.user or ("用户" if kind == "approval" else ""),
                kind=kind)
            return collab.create(
                kind=kind, run_id=run.run_id, flow_id=run.flow_id, node_id=node.id,
                assignee=assignee, role=role, assignee_kind="human",
                title=node.form or f"{run.flow_name}:{node.id}",
                instruction=node.task or node.form or "审批",
                form=node.form, timeout_hours=node.timeout_hours,
                on_timeout=node.on_timeout, budget=node.budget)
        except Exception as e:  # noqa: BLE001 — 开单失败不吞挂起（日志显式报因）
            self._log(run, node.id, "collab-task-error", f"开单失败: {e}")
            return None

    def _open_tool_gate_task(self, run: FlowRun, node: NodeSpec, reason: str):
        """F1 工具放行闸开单（require_approval 转 human，人工放行后一次性豁免续跑）。"""
        try:
            collab = self.engine.collab()
            assignee, role = collab.router.resolve(
                role=node.role, user=node.user or "用户", kind="human")
            return collab.create(
                kind="human", run_id=run.run_id, flow_id=run.flow_id, node_id=node.id,
                assignee=assignee, role=role, assignee_kind="human",
                title=f"工具放行闸：{node.tool}",
                instruction=f"工具 {node.tool} 被治理策略要求人工放行。原因：{reason}",
                form="审批放行（approve=一次性放行该工具）",
                timeout_hours=node.timeout_hours, on_timeout=node.on_timeout,
                payload={"gate": "tool-policy", "tool": node.tool})
        except Exception as e:  # noqa: BLE001
            self._log(run, node.id, "collab-task-error", f"工具放行开单失败: {e}")
            return None

    def _refresh_task_package(self, run: FlowRun, node: NodeSpec, instruction: Any,
                              task: Any, collab) -> str:
        """E1/E2/E3 任务包刷新（非在途才刷新，coalesce 合并保留原派发件）。

        - 上下文隔离：只带本节点 $. 引用的上游节点输出（不整包重灌）
        - full/compact 两档（node.package）防上下文爆
        - 快照 diff：自上次派发以来上游变更摘要（E2，不整包重灌）
        - 配置指纹：有效输入变更 → 留痕丢弃旧会话连续性（E3，correctness over continuity）
        """
        from plugin.flow_collab import (
            build_task_package, collect_upstream_refs, config_fingerprint)
        refs = collect_upstream_refs(node.task, node.args, node.form)
        upstream = {rid: run.outputs[rid] for rid in refs if rid in run.outputs}
        payload = getattr(task, "payload", None) or {}
        package = build_task_package(
            goal=str(instruction), form=node.form, upstream=upstream,
            prev_snapshot=payload.get("upstream_snapshot"),
            mode=node.package)
        fingerprint = config_fingerprint(
            node.role or node.user, node.target_agent, asdict(node.budget),
            node.package)
        in_flight = bool(getattr(task, "a2a_task_id", "")) \
            or getattr(task, "status", "") == "working"
        if not in_flight:
            if payload.get("config_fingerprint") and \
                    payload["config_fingerprint"] != fingerprint:
                self._log(run, node.id, "config-changed",
                          "配置指纹变更，丢弃旧会话连续性（E3 correctness over continuity）")
            task.instruction = package
            task.payload = {**payload,
                            "upstream_snapshot": upstream,
                            "config_fingerprint": fingerprint}
            collab.store.save(task)
        return package

    async def _run_human_node(self, run: FlowRun, defn: FlowDefinition,
                              node: NodeSpec, record: NodeRun) -> Any:
        """human 节点：任务入收件箱 → 通知（Reflex 应激）→ 人交付回流，流程恢复。

        E1 任务包（上下文隔离+full/compact 两档）+ E4 可选验收门（review: true）。
        """
        ctx = EvalContext(outputs=run.outputs, run=_run_ctx(run))
        instruction = resolve_value(node.task, ctx)
        collab = self.engine.collab()
        assignee, role = collab.router.resolve(role=node.role, user=node.user, kind="human")
        task = collab.create(
            kind="human", run_id=run.run_id, flow_id=run.flow_id, node_id=node.id,
            assignee=assignee, role=role, assignee_kind="human",
            title=node.form or f"{run.flow_name}:{node.id}",
            instruction=str(instruction), form=node.form,
            timeout_hours=node.timeout_hours, on_timeout=node.on_timeout,
            budget=node.budget)
        self._refresh_task_package(run, node, instruction, task, collab)
        self._log(run, node.id, "human-task",
                  f"工单 {task.task_id} → {assignee}（role={role or '-'}，"
                  f"期限 {node.timeout_hours:g}h{'，验收门' if node.review else ''}）")
        return self._suspend_for_approval(run, node, kind="human", task=task)

    async def _run_agent_node(self, run: FlowRun, defn: FlowDefinition,
                              node: NodeSpec, record: NodeRun) -> Any:
        """agent 节点：任务经 A2A 发到指定 SoulMate 干活，a2a/task/result 回流。

        员工归属按组织角色路由（D1/D2：org 表+RBAC），target_agent 为 A2A 目标。
        C2 预算闸转人工：派发前查预算 block，被挡 → human 审批放行 → 原地续跑
        （C3 恢复续跑，真闸不是只观测）；放行后经 A1 原子领取锁住才出网。
        """
        ctx = EvalContext(outputs=run.outputs, run=_run_ctx(run))
        instruction = resolve_value(node.task, ctx)
        collab = self.engine.collab()
        assignee, role = collab.router.resolve(role=node.role, user=node.user, kind="agent")
        gate_key = f"{run.run_id}:{node.id}"
        reasons = collab.ledger.blocked(assignee=assignee, flow_id=run.flow_id)
        if reasons and not collab.ledger.has_waiver(gate_key):
            # C2 真闸：预算超硬停线 → 转 human 审批放行，放行后重入本节点续跑
            # （waiver 只窥视不消耗，派发闸被真正拦下时才消耗——防双重消耗）
            gate_task = collab.create(
                kind="human", run_id=run.run_id, flow_id=run.flow_id, node_id=node.id,
                assignee=node.user or "用户", role=node.role, assignee_kind="human",
                title=f"预算闸放行：{run.flow_name}:{node.id}",
                instruction=f"预算被拦，需人工放行后继续派发 SoulMate。原因：{'; '.join(reasons)}",
                form="审批放行（approve=拨款放行续跑）",
                timeout_hours=node.timeout_hours, on_timeout=node.on_timeout,
                payload={"gate": "budget", "blocked": reasons},
            )
            self._log(run, node.id, "budget-gate",
                      f"预算拦截 {assignee}/{run.flow_id}：{'; '.join(reasons)[:200]}"
                      f" → 转人工放行（工单 {gate_task.task_id}）")
            return self._suspend_for_approval(run, node, kind="budget-gate",
                                              task=gate_task, resume_at=node.id)
        task = collab.create(
            kind="agent", run_id=run.run_id, flow_id=run.flow_id, node_id=node.id,
            assignee=assignee, role=role, assignee_kind="agent",
            target_agent=node.target_agent,
            title=f"{run.flow_name}:{node.id}",
            instruction=str(instruction), form=node.form,
            timeout_hours=node.timeout_hours, on_timeout=node.on_timeout,
            budget=node.budget)
        self._refresh_task_package(run, node, instruction, task, collab)
        self._log(run, node.id, "agent-task",
                  f"工单 {task.task_id} → {assignee} 的 SoulMate"
                  f"（target_agent={node.target_agent}，role={role or '-'}）")
        try:
            task = await collab.dispatch(task)
        except Exception as e:  # noqa: BLE001 — 派发失败：on_error 显式处置 + 本地降级兜底
            record.error = f"agent 派发失败: {type(e).__name__}: {e}"
            self._log(run, node.id, "node-failed", record.error)
            if node.on_error != "fail":
                # 显式 skip | goto 处置（绝不静默）
                return self._on_error(run, defn, node, record)
            # 派发失败本地降级（agent_node 四补之一）：任务不丢——降级为本地人工单，
            # 流程挂起等交付（比整条流程死掉强；本地单也开不出来才按 fail 收口）
            try:
                fallback = collab.create(
                    kind="human", run_id=run.run_id, flow_id=run.flow_id,
                    node_id=node.id, assignee=node.user or "用户", role=node.role,
                    assignee_kind="human",
                    title=f"本地降级：{run.flow_name}:{node.id}",
                    instruction=f"{instruction}\n\n【本地降级】SoulMate 派发失败"
                                f"（{record.error}），转本地人工完成。",
                    form=node.form, timeout_hours=node.timeout_hours,
                    on_timeout=node.on_timeout,
                    payload={"fallback": "dispatch-failed", "error": record.error})
            except Exception as e2:  # noqa: BLE001 — 本地降级开单也失败才按 on_error
                self._log(run, node.id, "fallback-failed", f"本地降级开单失败: {e2}")
                fallback = None
            if fallback is not None:
                self._log(run, node.id, "dispatch-fallback-local",
                          f"A2A 派发失败 → 本地人工降级（工单 {fallback.task_id}）")
                return self._suspend_for_approval(run, node, kind="human", task=fallback)
            return self._on_error(run, defn, node, record)
        self._log(run, node.id, "agent-dispatched",
                  f"a2a_task_id={task.a2a_task_id or '-'} status={task.status}")
        if not task.is_open():
            # 派发侧同步收口（INPUT_REQUIRED 转人工等）：改挂后续人工单，
            # 闸面同步到新工单（此时 run.approval 尚未建立，由挂起面直接开单）
            follow = collab.find_by_node(run.run_id, node.id)
            if follow is not None and follow.task_id != task.task_id:
                self._log(run, node.id, "dispatch-converted",
                          f"派发侧收口（{task.status}）→ 改挂 {follow.kind} 工单 {follow.task_id}")
                return self._suspend_for_approval(run, node, kind=follow.kind,
                                                  task=follow)
        return self._suspend_for_approval(run, node, kind="agent", task=task)

    # ── verify 验收节点（双轴翻档面：内容级探针 + 非执行者验收）──

    async def _run_verify_node(self, run: FlowRun, defn: FlowDefinition,
                               node: NodeSpec, record: NodeRun) -> Any:
        """verify 节点：对 `for` 目标做验收——探针证据 + 验收条件 → 翻 verified。

        铁律：报成功没用，要用内容级探针；执行者自报永远封顶 reported done。
        - 翻档判定走 verify_axis.can_verify（证据非空 + 验收者≠执行者）；
        - 验证不过：拒伪完成（目标不许标 completed）——先修复 ≤max_fixes 轮
          （fix_goto 回跳目标重跑），超出→按本节点 on_error 重规划
          （fail | skip | goto:<replan-node>）；
        - 结构性拒绝（自审/验收者未知）修复内容无用，直接定夺。
        """
        target_id = node.for_
        trecord = run.nodes.get(target_id)
        target = defn.node_map.get(target_id)

        def _refuse(code: str, detail: str) -> Any:
            """拒翻档（留痕）→ 修复轮或重规划。"""
            if trecord is not None:
                trecord.settle_rejects += 1
            self._log(run, node.id, "settle-refused",
                      f"目标 {target_id} 拒翻档 [{code}]: {detail}")
            if trecord is not None and reject_fixable(code) \
                    and trecord.fix_rounds < max(0, node.max_fixes):
                trecord.fix_rounds += 1
                self._log(run, node.id, "verify-fix",
                          f"修复轮 {trecord.fix_rounds}/{node.max_fixes}：重跑 {target_id}")
                return {"verified": False, "fix_goto": target_id,
                        "reason_code": code, "detail": detail}
            record.error = f"验收不过（{code}）: {detail}"
            self._log(run, node.id, "verify-exhausted",
                      f"修复轮用尽/结构性拒绝，按 on_error={node.on_error} 重规划")
            return self._on_error(run, defn, node, record)

        if target is None or trecord is None:
            return _refuse("target_not_done", f"验收目标不存在: {target_id!r}")
        if trecord.status != NODE_STATUS_OK:
            return _refuse("target_not_done",
                           f"目标 {target_id} 还没干完（{trecord.status}），无从验收")

        # 内容级探针（验收证据来源；探针失败=验收不过，可修复）
        evidence = ""
        probe_out: Any = None
        if node.probe:
            try:
                probe_out = await self.engine.call_tool(
                    node.probe,
                    {"run_id": run.run_id, "flow_id": run.flow_id,
                     "target": target_id, "output": run.outputs.get(target_id)},
                    approved=_run_approved(run),
                    ctx={"run_id": run.run_id, "flow_id": run.flow_id,
                         "node_id": node.id, "role": "verify"})
                evidence = f"probe={node.probe} {_summarize(probe_out)}"
            except Exception as e:  # noqa: BLE001 — 探针失败=验收不过（可修复轮）
                evidence = ""
                probe_error = f"探针 {node.probe} 失败: {type(e).__name__}: {e}"
                self._log(run, node.id, "probe-failed", probe_error)
                record.error = probe_error

        # 验收条件（when）：目标输出 + 探针输出可寻址（$.<verify-id>.*）
        acceptance_ok: bool | None = None
        if node.when:
            ctx = EvalContext(
                outputs={**run.outputs, node.id: probe_out},
                run=_run_ctx(run))
            try:
                acceptance_ok = bool(eval_expr(node.when, ctx))
            except ExprError as e:
                acceptance_ok = False
                evidence = (evidence + f" when-error: {e}").strip()
            if acceptance_ok:
                evidence = (evidence + f" when={node.when} → true").strip()
            else:
                evidence = (evidence + f" when={node.when} → false").strip()
        if not evidence:
            return _refuse("no_evidence", "探针/验收条件都没有产出证据（报成功没用）")
        if acceptance_ok is False:
            return _refuse("acceptance_misses", f"验收条件未满足: {evidence[:200]}")

        # 翻档判定（防自审：验收者=执行者拒收留痕）
        verifier = (node.verifier or (f"probe:{node.probe}" if node.probe
                                      else f"verify-node:{node.id}"))
        allowed, code, detail = can_verify(
            verifier=verifier, executor=trecord.executed_by, evidence=evidence)
        if not allowed:
            return _refuse(code, detail)
        trecord.verified = True
        trecord.verified_by = verifier
        trecord.verified_at = time.time()
        trecord.verify_evidence = evidence[:500]
        trecord.claim_source = CLAIM_EVIDENCE
        record.executed_by = verifier
        record.claim_source = CLAIM_EVIDENCE
        record.verify_evidence = evidence[:500]
        self._log(run, node.id, "verified",
                  f"目标 {target_id} 翻档 Test·verified（by={verifier} "
                  f"evidence={evidence[:120]}）")
        return {"verified": True, "target": target_id, "by": verifier,
                "evidence": evidence[:500]}

    # ── action 节点（notify | emit-event | update-panel | compress-handoff）──

    def _run_action(self, run: FlowRun, defn: FlowDefinition,
                    node: NodeSpec, record: NodeRun) -> Any:
        results = []
        for action in node.actions:
            if action.type == "notify":
                note = {
                    "channel": action.channel or "in-app",
                    "message": action.message,
                    "ts": time.time(),
                    "run_id": run.run_id,
                }
                run.notifications.append(note)
                self._log(run, node.id, "notify", f"[{note['channel']}] {action.message}")
                results.append(note)
            elif action.type == "emit-event":
                self.engine.emit(action.event, {"run_id": run.run_id, "flow_id": run.flow_id})
                self._log(run, node.id, "emit-event", action.event)
                results.append({"event": action.event})
            elif action.type == "compress-handoff":
                # 阶段切换节点声明压缩要求（Pi #5）：自定义压缩指令经既有事件总线
                # 广播（`compress-handoff`），驱动层接 ContextCompressor 指令注入。
                instructions = action.message
                self.engine.emit("compress-handoff", {
                    "run_id": run.run_id,
                    "flow_id": run.flow_id,
                    "node": node.id,
                    "instructions": instructions,
                })
                self._log(run, node.id, "compress-handoff",
                          f"阶段交接压缩要求: {instructions[:120]}")
                results.append({"compress_handoff": {"instructions": instructions}})
            else:  # update-panel
                self._log(run, node.id, "update-panel", action.panel)
                results.append({"panel": action.panel})
        return {"actions": results}

    # ── parallel 节点（子代理治理运行时基线，Pi #14）──

    async def _run_parallel(self, run: FlowRun, defn: FlowDefinition,
                            node: NodeSpec, record: NodeRun) -> Any:
        """分支子任务并发执行，治理三件套全施加：
        ①并发乘数×2 超出排队（SubagentGovernor，队满/排队超时显式报因）；
        ②全分支共享统一轮数上限（TurnBudget，默认 100，超限显式报因不硬杀）；
        ③分支经 governor.run_child 同源执行（runChildTask 沿用限制语义）。
        分支=合成 tool 节点（id=<节点>__<分支>），复用 _run_tool_node 的重试/
        软预算/审批语义；分支失败隔离（记 skipped/failed），聚合后按本节点
        on_error 定夺（fail | skip | goto:<node>）。"""
        from agent.subagent_governance import (
            SubagentGovernor,
            SubagentQueueFull,
            SubagentQueueTimeout,
            TurnLimitExceeded,
        )

        governor = SubagentGovernor(
            base_concurrency=node.max_concurrency or PARALLEL_BASE_CONCURRENCY,
            max_turns=SUBAGENT_MAX_TURNS)
        self._log(run, node.id, "parallel-start",
                  f"branches={len(node.branches)} capacity={governor.capacity} "
                  f"max_turns={governor.budget.max_turns}")

        async def _one(branch):
            synth = NodeSpec(
                id=f"{node.id}__{branch.id}", type="tool",
                tool=branch.tool, args=dict(branch.args),
                retry=node.retry, timeout_seconds=node.timeout_seconds,
                on_error="skip")     # 分支失败隔离，聚合后按 parallel 节点 on_error 定夺
            brecord = self._record(run, synth)
            brecord.status = NODE_STATUS_RUNNING
            brecord.started_at = time.time()
            brecord.owned_by = node.id   # 分支合成件：判档归 parallel 节点
            try:
                async def _exec():
                    return await self._run_tool_node(run, defn, synth, brecord)
                # ③runChildTask 沿用限制：slot+统一轮数经 governor.run_child 同源施加
                out = await governor.run_child(_exec, child_id=synth.id, turns=1)
            except (SubagentQueueFull, SubagentQueueTimeout, TurnLimitExceeded) as e:
                brecord.status = NODE_STATUS_FAILED
                brecord.error = f"{type(e).__name__}: {e}"
                self._log(run, synth.id, "branch-governed", brecord.error)
                out = {"failed": True, "error": brecord.error}
            brecord.finished_at = time.time()
            brecord.duration_ms = (brecord.finished_at - brecord.started_at) * 1000
            self.engine.store.save(run)
            return branch.id, out

        results = await asyncio.gather(*(_one(b) for b in node.branches))
        outputs = {bid: out for bid, out in results}
        failed = [bid for bid, out in outputs.items()
                  if isinstance(out, dict) and (out.get("failed") or out.get("skipped"))]
        summary = {"branches": outputs, "governor": governor.stats(),
                   "failed_branches": failed}
        if failed:
            record.error = f"分支失败: {failed}"
            self._log(run, node.id, "parallel-failed",
                      f"failed={failed} stats={governor.stats()}")
            return self._on_error(run, defn, node, record)   # 聚合后按节点 on_error
        self._log(run, node.id, "parallel-ok",
                  f"{len(outputs)} 分支完成 stats={governor.stats()}")
        return summary

    # ── 工具 ──

    def _record(self, run: FlowRun, node: NodeSpec) -> NodeRun:
        record = run.nodes.get(node.id)
        if record is None:
            record = NodeRun(node_id=node.id, type=node.type)
            run.nodes[node.id] = record
        return record

    @staticmethod
    def _log(run: FlowRun, node_id: str, event: str, detail: str) -> None:
        run.log.append({
            "ts": time.time(),
            "node": node_id,
            "event": event,
            "detail": detail[:2000],
        })

    @staticmethod
    def _total_ms(run: FlowRun) -> float:
        return sum(r.duration_ms or 0.0 for r in run.nodes.values())


class _NodeJump(Exception):
    """内部：on_error=goto:<node> 跳转"""


def _fire_and_forget(coro) -> None:
    """有运行中事件循环则派生任务；没有则丢弃（调用方 await execute 或等 resume）"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()
        return
    loop.create_task(coro)


def _run_ctx(run: FlowRun) -> dict[str, Any]:
    return {
        "id": run.run_id,
        "flow_id": run.flow_id,
        "trigger": run.trigger.get("type", ""),
        "inputs": run.inputs,
    }


def _run_approved(run: FlowRun) -> bool:
    """本实例是否已过人工审批闸（approved token：放行 approval 类工具）"""
    return run.approval is not None and run.approval.status == "approved"


def _summarize(value: Any, limit: int = 200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "…"


def _last_failed_node(run: FlowRun) -> str:
    """失败归因节点（先失败者即祸首：后续节点未执行）"""
    for node_id, record in run.nodes.items():
        if record.status == NODE_STATUS_FAILED:
            return node_id
    return ""


# ---- 引擎门面 ----

class FlowEngine:
    """流程注册 / 触发 / 实例生命周期 / 审批 / 回放 / 事件"""

    def __init__(self, runs_dir: Path | str, tool_caller: ToolCaller | None = None):
        self.store = FlowStore(runs_dir)
        self._flows: dict[str, tuple[FlowDefinition, str]] = {}  # flow_id → (defn, plugin_id)
        self._tool_caller = tool_caller
        self._locks: dict[str, asyncio.Lock] = {}
        self._executed_cron_slots: set[str] = set()  # 进程内防重复触发（幂等键表兜底跨重启）
        # 用户面流程定义仓（克隆/编辑参数持久化 + 暂停态）：插件注册有同 id 覆盖件时以覆盖件为准
        import os as _os
        default_defs = self.store.runs_dir.parent / "flow-defs"
        self.defs_dir = Path(_os.environ.get("FLOW_DEFS_DIR") or default_defs)
        self.defs_dir.mkdir(parents=True, exist_ok=True)
        self._paused: dict[str, bool] = {}
        self._state_path = self.defs_dir / "_state.json"
        self._load_state()
        self._load_overlays()

    # ── 用户面定义仓（覆盖件）──

    def _load_state(self) -> None:
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._paused = dict(data.get("paused") or {})
        except (OSError, json.JSONDecodeError, TypeError):
            self._paused = {}

    def _save_state(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps({"paused": self._paused}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError as e:
            logger.warning("流程状态落盘失败: %s", e)

    def _overlay_path(self, flow_id: str) -> Path:
        return self.defs_dir / f"{flow_id}.yaml"

    def _load_overlays(self) -> None:
        """启动装载用户覆盖件（克隆件/编辑件）——早于插件注册，注册时覆盖件优先"""
        import yaml
        for path in sorted(self.defs_dir.glob("*.yaml")):
            try:
                defn = load_flow_file(path)
                self._flows[defn.id] = (defn, "user")
            except Exception as e:  # noqa: BLE001 — 单个坏覆盖件不阻断装载
                logger.warning("流程覆盖件装载失败 %s: %s", path, e)

    def _save_overlay(self, defn: FlowDefinition) -> None:
        import yaml
        path = self._overlay_path(defn.id)
        path.write_text(
            yaml.safe_dump(dump_flow(defn), allow_unicode=True, sort_keys=False),
            encoding="utf-8")

    # 注册
    def register_flow(self, defn: FlowDefinition, plugin_id: str) -> None:
        overlay = self._overlay_path(defn.id)
        if overlay.is_file():
            # 用户编辑/克隆覆盖件优先于插件自带定义（编辑过的参数不被插件重载冲掉）
            try:
                defn = load_flow_file(overlay)
            except Exception as e:  # noqa: BLE001 — 覆盖件坏了退回插件原版
                logger.warning("流程覆盖件解析失败 %s（用插件原版）: %s", overlay, e)
        self._flows[defn.id] = (defn, plugin_id)
        logger.info("流程已注册: %s (plugin=%s, triggers=%s)",
                    defn.id, plugin_id, [t.type for t in defn.triggers])

    def register_flow_file(self, path: Path | str, plugin_id: str) -> FlowDefinition:
        defn = load_flow_file(path)
        self.register_flow(defn, plugin_id)
        return defn

    def unregister_plugin(self, plugin_id: str) -> int:
        names = [fid for fid, (_, pid) in self._flows.items() if pid == plugin_id]
        for name in names:
            del self._flows[name]
        return len(names)

    def delete_flow(self, flow_id: str) -> dict[str, Any]:
        """删除流程定义 + 清空实例/幂等键 + 用户覆盖件/暂停态（防再生：另需移除插件侧 flows: 注册源）"""
        entry = self._flows.pop(flow_id, None)
        purged = self.store.purge_flow(flow_id)
        self._locks.pop(flow_id, None)
        self._overlay_path(flow_id).unlink(missing_ok=True)
        if self._paused.pop(flow_id, None) is not None:
            self._save_state()
        stale = {k for k in self._executed_cron_slots if k.startswith(f"cron:{flow_id}:")}
        self._executed_cron_slots -= stale
        if entry is not None:
            logger.info("流程已删除: %s (清实例 %d 条)", flow_id, purged)
        return {"removed_definition": entry is not None, "purged_runs": purged}

    # ── 一键操作面（克隆 / 编辑参数 / 暂停 / 从失败节点重试）──

    def clone_flow(self, flow_id: str, new_id: str = "", new_name: str = "") -> dict[str, Any]:
        """克隆流程定义为用户件（独立 id/实例/触发，参数随后可编辑）"""
        defn = self.get_flow(flow_id)
        target = (new_id or "").strip()
        if not target:
            base = f"{flow_id}-copy"
            target, index = base, 2
            while target in self._flows:
                target = f"{base}{index}"
                index += 1
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", target):
            raise FlowRunError(
                f"新流程 id 非法: {target!r}（小写字母开头，仅小写字母/数字/连字符）")
        if target in self._flows:
            raise FlowRunError(f"流程 id 已存在: {target}")
        data = dump_flow(defn)
        data["id"] = target
        data["name"] = (new_name or f"{defn.name} 副本").strip() or f"{defn.name} 副本"
        try:
            clone = parse_flow(data, source=f"clone:{flow_id}")
        except FlowDefError as e:
            raise FlowRunError(f"克隆校验失败: {e}") from e
        self._save_overlay(clone)
        self._flows[clone.id] = (clone, "user")
        logger.info("流程已克隆: %s → %s", flow_id, clone.id)
        return {"id": clone.id, "name": clone.name, "version": clone.version,
                "plugin_id": "user", "cloned_from": flow_id,
                "triggers": [asdict(t) for t in clone.triggers],
                "nodes": [{"id": n.id, "type": n.type} for n in clone.nodes],
                "retain_runs": clone.retain_runs, "paused": False}

    def update_flow(self, flow_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        """编辑流程参数（name/triggers/nodes/retain_runs 任意子集）→ 校验 → 覆盖件落盘。

        覆盖件优先于插件定义（register_flow 同 id 覆盖），编辑不被插件重载冲掉。
        """
        defn = self.get_flow(flow_id)
        plugin_id = self._flows[flow_id][1]
        data = dump_flow(defn)
        for key in ("name", "version", "triggers", "nodes"):
            if key in patch and patch[key] is not None:
                data[key] = patch[key]
        if patch.get("retain_runs") is not None:
            data.setdefault("observability", {})
            data["observability"]["retain_runs"] = patch["retain_runs"]
        data["id"] = flow_id
        # 节点兼容两种拼写：GET /flows/{id} 序列化是 else_/for_，YAML/校验口径是 else/for
        for node in data.get("nodes") or []:
            if isinstance(node, dict) and "else_" in node and "else" not in node:
                node["else"] = node.pop("else_")
            if isinstance(node, dict) and "for_" in node and "for" not in node:
                node["for"] = node.pop("for_")
        try:
            updated = parse_flow(data, source=f"edit:{flow_id}")
        except FlowDefError as e:
            raise FlowRunError(f"参数校验失败: {e}") from e
        self._save_overlay(updated)
        self.register_flow(updated, plugin_id)
        logger.info("流程参数已编辑: %s (version=%d)", flow_id, updated.version)
        return {"id": updated.id, "name": updated.name, "version": updated.version,
                "plugin_id": plugin_id,
                "triggers": [asdict(t) for t in updated.triggers],
                "nodes": [{"id": n.id, "type": n.type} for n in updated.nodes],
                "retain_runs": updated.retain_runs,
                "paused": self.is_paused(flow_id)}

    def pause_flow(self, flow_id: str, paused: bool = True) -> dict[str, Any]:
        """暂停/恢复：暂停挡自动触发（cron/event）与手动触发（force 可越权立即触发）"""
        self.get_flow(flow_id)   # 不存在即报错
        self._paused[flow_id] = paused
        self._save_state()
        logger.info("流程 %s: %s", flow_id, "已暂停" if paused else "已恢复")
        return {"id": flow_id, "paused": paused}

    def is_paused(self, flow_id: str) -> bool:
        return bool(self._paused.get(flow_id))

    async def retry_run(self, run_id: str, from_node: str = "") -> FlowRun:
        """从失败节点续跑（同实例原地重试）——失败可修；整体重跑请用回放"""
        run = self.store.find_run(run_id)
        if run is None:
            raise FlowRunError(f"流程实例不存在: {run_id}")
        if run.status != RUN_STATUS_FAILED:
            raise FlowRunError(
                f"只有失败实例可从失败节点重试（当前 {run.status}）；整体重跑请用回放")
        defn = self.get_flow(run.flow_id)
        target = from_node
        if not target:
            for node_id, record in run.nodes.items():
                if record.status == NODE_STATUS_FAILED:
                    target = node_id
                    break
        target = target or run.cursor or defn.first_node_id()
        if target not in defn.node_map:
            raise FlowRunError(f"重试起点节点不存在: {target}")
        record = run.nodes.get(target)
        if record is None:
            record = NodeRun(node_id=target, type=defn.node_map[target].type)
            run.nodes[target] = record
        record.status = NODE_STATUS_PENDING
        record.error = None
        record.attempts = 0
        record.retries = 0
        record.started_at = None
        record.finished_at = None
        record.duration_ms = None
        run.status = RUN_STATUS_RUNNING
        run.error = None
        run.finished_at = None
        run.cursor = target
        self._log(run, target, "retry-resume", f"从失败节点 {target} 原地续跑")
        self.store.save(run)
        return await self.execute(run_id, start_at=target)

    def run_stats(self, flow_id: str = "", since: float | None = None,
                  until: float | None = None, limit: int = 1000) -> dict[str, Any]:
        """运行历史统计（按流程/时间筛选：成败计数/成功率/平均与最差耗时）"""
        runs = self.store.list_runs(
            flow_id=flow_id or None, limit=max(1, min(limit, 5000)),
            since=since, until=until)
        counts: dict[str, int] = {}
        durations: list[float] = []
        by_flow: dict[str, dict[str, Any]] = {}
        for run in runs:
            counts[run.status] = counts.get(run.status, 0) + 1
            if run.finished_at:
                durations.append((run.finished_at - run.created_at) * 1000)
            bucket = by_flow.setdefault(run.flow_id, {
                "flow_id": run.flow_id, "flow_name": run.flow_name,
                "total": 0, "completed": 0, "failed": 0, "waiting-approval": 0,
                "running": 0})
            bucket["total"] += 1
            bucket[run.status] = bucket.get(run.status, 0) + 1
        done = counts.get(RUN_STATUS_COMPLETED, 0) + counts.get(RUN_STATUS_FAILED, 0)
        return {
            "total": len(runs),
            "completed": counts.get(RUN_STATUS_COMPLETED, 0),
            "failed": counts.get(RUN_STATUS_FAILED, 0),
            "running": counts.get(RUN_STATUS_RUNNING, 0),
            "waiting_approval": counts.get(RUN_STATUS_WAITING, 0),
            "success_rate": round(
                counts.get(RUN_STATUS_COMPLETED, 0) / done * 100, 1) if done else None,
            "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else None,
            "max_duration_ms": round(max(durations), 1) if durations else None,
            "by_flow": sorted(by_flow.values(), key=lambda b: -b["total"]),
            "since": _fmt_ts(since) if since else "",
            "until": _fmt_ts(until) if until else "",
        }

    def get_flow(self, flow_id: str) -> FlowDefinition:
        entry = self._flows.get(flow_id)
        if entry is None:
            raise FlowRunError(f"流程未注册: {flow_id}")
        return entry[0]

    def list_flows(self) -> list[dict[str, Any]]:
        result = []
        for defn, plugin_id in self._flows.values():
            result.append({
                "id": defn.id,
                "name": defn.name,
                "version": defn.version,
                "plugin_id": plugin_id,
                "triggers": [asdict(t) for t in defn.triggers],
                "nodes": [{"id": n.id, "type": n.type} for n in defn.nodes],
                "retain_runs": defn.retain_runs,
                "paused": self.is_paused(defn.id),
            })
        return sorted(result, key=lambda item: item["id"])

    # 工具调用注入
    def set_tool_caller(self, caller: ToolCaller) -> None:
        self._tool_caller = caller

    def _log(self, run: FlowRun, node_id: str, event: str, detail: str) -> None:
        """运行日志追加（FlowExecutor._log 同源）"""
        FlowExecutor._log(run, node_id, event, detail)

    def notify_run(self, run: FlowRun, kind: str, *, node: str = "",
                   detail: str = "", dedupe_key: str = "") -> None:
        """结果触达旁路（微信 gateway + Reflex 事件流）：never-raise、不阻断执行"""
        try:
            from plugin import flow_notify
            flow_notify.notify_run_event(
                self, run, kind, node=node, detail=detail, dedupe_key=dedupe_key)
        except Exception as e:  # noqa: BLE001 — 触达旁路绝不阻断主流程
            logger.warning("流程触达旁路失败: %s", e)

    async def call_tool(self, tool_name: str, args: dict[str, Any],
                        approved: bool = False,
                        ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        """工具调用唯一咽喉（tool/loop/parallel 全走此）：F 组治理网关先过闸。

        判定序（deny 优先）：profile deny → policy block → require_approval(未批)
        → rate_limit 超限 → allow；每次调用落审计台账（F2，参数脱敏）。
        """
        if self._tool_caller is None:
            raise FlowRunError("流程引擎未接工具调用（tool_caller 未注入）")
        from plugin.tool_governance import (
            ToolGovernanceBlocked, ToolGovernanceNeedsApproval, get_governance)
        governance = get_governance()
        ctx = dict(ctx or {})
        decision = governance.evaluate(
            tool_name, flow_id=str(ctx.get("flow_id") or ""),
            role=str(ctx.get("role") or ""), node_id=str(ctx.get("node_id") or ""),
            approved=approved)
        if not decision.allowed:
            if decision.action == "require_approval":
                # F1 真闸：转 human 审批放行（tool-gate 原地续跑，一次性豁免）
                gate_key = f"{ctx.get('run_id', '')}:{ctx.get('node_id', '')}"
                if gate_key.strip(":") and governance.take_waiver(gate_key):
                    decision.reason += "（已放行）"
                else:
                    governance.record_call(tool=tool_name, decision=decision,
                                           ok=False, error=decision.reason,
                                           args=args, ctx=ctx)
                    raise ToolGovernanceNeedsApproval(tool_name, decision.reason)
            else:
                governance.record_call(tool=tool_name, decision=decision,
                                       ok=False, error=decision.reason,
                                       args=args, ctx=ctx)
                raise ToolGovernanceBlocked(tool_name, decision.reason)
        started = time.perf_counter()
        try:
            result = await self._tool_caller(tool_name, args, approved)
        except Exception as e:  # noqa: BLE001 — 审计留痕后原样上抛
            governance.record_call(
                tool=tool_name, decision=decision, ok=False,
                error=f"{type(e).__name__}: {e}",
                duration_ms=(time.perf_counter() - started) * 1000,
                args=args, ctx=ctx)
            raise
        governance.record_call(
            tool=tool_name, decision=decision, ok=True,
            duration_ms=(time.perf_counter() - started) * 1000,
            args=args, ctx=ctx)
        return result

    # 触发
    def start_run(self, flow_id: str, trigger: dict[str, Any],
                  inputs: dict[str, Any] | None = None,
                  idempotency_key: str = "", now: float | None = None,
                  force: bool = False) -> FlowRun:
        """创建实例（幂等：同 key 直接返回既有实例，不重复执行）"""
        defn = self.get_flow(flow_id)
        if self.is_paused(flow_id) and not force:
            raise FlowRunError(
                f"流程已暂停: {flow_id}（先恢复再触发；紧急可 force 立即触发）")
        if idempotency_key:
            existing_id = self.store.lookup_key(flow_id, idempotency_key)
            if existing_id:
                existing = self.store.find_run(existing_id)
                if existing is not None:
                    logger.info("流程 %s 幂等命中 %s → %s", flow_id, idempotency_key, existing_id)
                    return existing
        ts = now or time.time()
        run = FlowRun(
            run_id=f"run_{_dt.datetime.fromtimestamp(ts):%Y%m%d%H%M%S}_{uuid.uuid4().hex[:8]}",
            flow_id=flow_id,
            flow_name=defn.name,
            plugin_id=self._flows[flow_id][1],
            status=RUN_STATUS_RUNNING,
            trigger=trigger,
            inputs=dict(inputs or {}),
            idempotency_key=idempotency_key,
            created_at=ts,
            updated_at=ts,
        )
        run.nodes = {n.id: NodeRun(node_id=n.id, type=n.type) for n in defn.nodes}
        run.cursor = defn.first_node_id()
        self.store.put_key(flow_id, idempotency_key, run.run_id)
        self.store.save(run)
        logger.info("流程实例创建: %s (%s, trigger=%s)", run.run_id, flow_id, trigger.get("type"))
        return run

    async def run_now(self, flow_id: str, trigger: dict[str, Any],
                      inputs: dict[str, Any] | None = None,
                      idempotency_key: str = "", force: bool = False) -> FlowRun:
        """创建并立即执行（到完成或审批挂起）"""
        run = self.start_run(flow_id, trigger, inputs, idempotency_key, force=force)
        return await self.execute(run.run_id)

    async def execute(self, run_id: str, start_at: str | None = None) -> FlowRun:
        run = self.store.find_run(run_id)
        if run is None:
            raise FlowRunError(f"流程实例不存在: {run_id}")
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            executor = FlowExecutor(self)
            return await executor.execute(run, start_at=start_at)

    # 审批闸（approval/human/agent 三类共用：human/agent=交付回流）
    async def resolve_approval(self, run_id: str, decision: str,
                               decided_by: str = "", note: str = "",
                               result: Any = None) -> FlowRun:
        run = self.store.find_run(run_id)
        if run is None:
            raise FlowRunError(f"流程实例不存在: {run_id}")
        if run.status != RUN_STATUS_WAITING or run.approval is None \
                or run.approval.status != "pending":
            raise FlowRunError(f"实例 {run_id} 不在待审批状态")
        if decision not in ("approve", "reject"):
            raise FlowRunError(f"审批决定非法: {decision!r}（approve | reject）")
        run.approval.status = "approved" if decision == "approve" else "rejected"
        run.approval.decided_at = time.time()
        run.approval.decided_by = decided_by
        run.approval.note = note
        run.approval.result = result
        defn = self.get_flow(run.flow_id)
        approval_node = defn.node_map.get(run.approval.node_id)
        record = run.nodes.get(run.approval.node_id)
        # ── settle 拒伪完成闸（agent/human 交付回流）：缺交付物/验收标准未满足 → 拒 ok，
        #    先修复 ≤2 轮再重规划；执行者自报 verified 一律封顶 reported done（不翻档）──
        if decision == "approve" and run.approval.kind in ("agent", "human") \
                and approval_node is not None:
            settled = await self._settle_delivery(
                run, approval_node, record, result, decided_by)
            if settled is not None:
                return settled
        if record is not None:
            record.status = NODE_STATUS_OK if decision == "approve" else NODE_STATUS_FAILED
            record.finished_at = time.time()
            record.output_summary = f"审批 {run.approval.status}（{decided_by or '用户'}）"
            if decision == "approve" and run.approval.kind in ("agent", "human"):
                # 双轴：交付=执行者自报（封顶 Code·reported done，黄）
                record.executed_by = decided_by or record.executed_by
                record.claim_source = CLAIM_SELF
        self._sync_gate_task(run.approval, approved=(decision == "approve"),
                             result=result, decided_by=decided_by)
        if decision == "approve" and run.approval.kind == "budget-gate":
            # C3 预算闸放行：一次性豁免下一次拦截，流程原地续跑（记账照常）
            try:
                self.collab().ledger.grant_waiver(
                    f"{run.run_id}:{run.approval.node_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning("预算放行标记失败: %s", e)
        if decision == "approve" and run.approval.kind == "tool-gate":
            # F1 工具放行闸：一次性豁免下一次 require_approval 拦截，原地续跑
            try:
                from plugin.tool_governance import get_governance
                get_governance().grant_waiver(
                    f"{run.run_id}:{run.approval.node_id}")
            except Exception as e:  # noqa: BLE001
                logger.warning("工具放行标记失败: %s", e)
        if decision == "approve" and run.approval.kind == "human" \
                and approval_node is not None and approval_node.review:
            # E4 review gate：验收驳回不算节点完成（成果回流挂 flow_receipts 凭证）
            from plugin.flow_collab import record_review_receipt, review_verdict
            verdict = review_verdict(result)
            record_review_receipt(run.run_id, approval_node.id, verdict,
                                  decided_by, result)
            if verdict == "reject":
                run.status = RUN_STATUS_FAILED
                run.finished_at = time.time()
                run.error = f"验收驳回: {note or str(result or '')[:200]}"
                if record is not None:
                    record.status = NODE_STATUS_FAILED
                self._log(run, approval_node.id, "review-rejected",
                          f"by={decided_by or '用户'}: {str(result)[:200]}")
                self.store.save(run)
                self.notify_run(run, "failed", node=approval_node.id,
                                dedupe_key=str(run.finished_at))
                return run
            self._log(run, approval_node.id, "review-accepted",
                      f"by={decided_by or '用户'}")
            # review 节点自身=验收动作：其产出即验收证据（claim_source=evidence）
            if record is not None:
                record.verified = True
                record.verified_by = decided_by or "用户"
                record.verified_at = time.time()
                record.verify_evidence = f"review-accept: {str(result)[:200]}"
                record.claim_source = CLAIM_EVIDENCE
            # review 验收门带 for 目标：验收结论=独立验收证据 → 目标翻 verified
            # （验收者=执行者 → 拒翻档留痕，节点保持 done·unverified）
            if approval_node.for_:
                try:
                    self.mark_verified(
                        run, approval_node.for_,
                        verified_by=decided_by or "用户",
                        evidence=f"review-accept: {str(result)[:200]}")
                except FlowRunError as e:
                    self._log(run, approval_node.id, "verify-refused",
                              f"review 验收未能翻档 {approval_node.for_}: {e}")
        if decision == "reject":
            run.status = RUN_STATUS_FAILED
            run.finished_at = time.time()
            run.error = f"人工审批驳回: {note or '无说明'}"
            self._log(run, run.approval.node_id, "approval-rejected", note or "（无说明）")
            self.store.save(run)
            self.notify_run(run, "failed", node=run.approval.node_id,
                            dedupe_key=str(run.finished_at))
            return run
        if run.approval.kind in ("human", "agent", "budget-gate", "tool-gate"):
            run.outputs[run.approval.node_id] = {
                "kind": run.approval.kind, "task_id": run.approval.task_id,
                "assignee": run.approval.assignee, "role": run.approval.role,
                "result": result, "delivered_by": decided_by, "note": note,
            }
        else:
            run.outputs[run.approval.node_id] = {
                "decision": "approved", "by": decided_by, "note": note,
            }
        run.status = RUN_STATUS_RUNNING
        self._log(run, run.approval.node_id, "approval-approved",
                  f"by={decided_by or '用户'} note={note or '（无说明）'}")
        self.store.save(run)
        # 恢复执行：从审批节点的后继继续
        resume_from = self._resume_cursor(defn, run, approval_node)
        if resume_from is None:
            run.status = RUN_STATUS_COMPLETED
            run.finished_at = time.time()
            self._log(run, "-", "run-ok", "审批后无后继节点")
            self.store.save(run)
            return run
        return await self.execute(run_id, start_at=resume_from)

    async def resolve_delivery(self, run_id: str, result: Any,
                               decided_by: str = "", note: str = "") -> FlowRun:
        """human/agent 工单交付回流：与 approval 同一恢复面（approve + 交付物）。"""
        return await self.resolve_approval(
            run_id, "approve", decided_by=decided_by, note=note, result=result)

    # ── settle 拒伪完成闸 + 双轴翻档（监督闭环）──

    async def _settle_delivery(self, run: FlowRun, node: NodeSpec,
                               record: NodeRun | None, result: Any,
                               decided_by: str) -> FlowRun | None:
        """settle 拒伪完成（agent/human 交付回流入账处）：

        - 缺交付物 → 拒 ok；声明 acceptance 验收标准未满足 → 拒 ok；
        - 执行者自报 verified 一律不采信（claim 封顶 Code·reported done）；
        - 拒绝留痕（settle_rejects + 日志），先修复 ≤SETTLE_MAX_FIXES 轮
          （原节点重跑重派），超出→重规划（on_error：fail | skip | goto:<replan>）。
        返回 None = settle 过（继续正常收口，节点只到 reported done，等验收翻档）。
        """
        strict = node.type == "agent" or bool(node.form) or bool(node.acceptance)
        acceptance_ok: bool | None = None
        if node.acceptance:
            try:
                ctx = EvalContext(
                    outputs={**run.outputs, node.id: {"result": result}},
                    run=_run_ctx(run))
                acceptance_ok = bool(eval_expr(node.acceptance, ctx))
            except ExprError as e:
                acceptance_ok = False
                self._log(run, node.id, "acceptance-error", f"验收标准求值失败: {e}")
        claim_verified = bool(re.search(
            r"\bverified\b|已验证|验证通过|测试通过",
            str(result or ""), re.IGNORECASE))
        decision = settle_decision(result=result, acceptance_ok=acceptance_ok,
                                   claim_verified=claim_verified)
        if decision["accept"]:
            if decision["capped"]:
                self._log(run, node.id, "claim-capped",
                          "执行者自报 verified 不采信，封顶 Code·reported done（黄）")
            return None
        if not strict:
            return None    # 非严格面（没声明交付物/验收标准）：保持旧收口语义
        if record is not None:
            record.settle_rejects += 1
        self._log(run, node.id, "settle-refused",
                  f"[{decision['reason_code']}] {decision['detail']}")
        if reject_fixable(decision["reason_code"]) and record is not None \
                and record.fix_rounds < SETTLE_MAX_FIXES:
            # 修复轮：原节点重跑重派（agent 重新开单），流程不丢
            record.fix_rounds += 1
            record.status = NODE_STATUS_PENDING
            record.error = None
            record.attempts = 0
            record.retries = 0
            record.started_at = None
            record.finished_at = None
            run.status = RUN_STATUS_RUNNING
            run.cursor = node.id
            self._log(run, node.id, "settle-fix",
                      f"修复轮 {record.fix_rounds}/{SETTLE_MAX_FIXES}：重跑 {node.id}")
            self.store.save(run)
            return await self.execute(run.run_id, start_at=node.id)
        # 修复轮用尽/结构性拒绝 → 重规划（on_error 语义）
        refuse_out = {"settle_refused": True,
                      "reason_code": decision["reason_code"],
                      "detail": decision["detail"]}
        if node.on_error.startswith("goto:"):
            replan = node.on_error.split(":", 1)[1]
            if record is not None:
                record.status = NODE_STATUS_FAILED
                record.error = f"settle 拒伪完成: {decision['reason_code']}"
                record.finished_at = time.time()
            run.status = RUN_STATUS_RUNNING
            run.outputs[node.id] = refuse_out
            run.cursor = replan
            self._log(run, node.id, "settle-replan", f"重规划 → {replan}")
            self.store.save(run)
            return await self.execute(run.run_id, start_at=replan)
        if node.on_error == "skip":
            if record is not None:
                record.status = NODE_STATUS_SKIPPED
                record.error = f"settle 拒伪完成: {decision['reason_code']}"
                record.finished_at = time.time()
            run.status = RUN_STATUS_RUNNING
            run.outputs[node.id] = refuse_out
            self.store.save(run)
            nxt = self.get_flow(run.flow_id).next_node_id(node.id)
            if nxt:
                return await self.execute(run.run_id, start_at=nxt)
            run.status = RUN_STATUS_COMPLETED
            run.finished_at = time.time()
            self.store.save(run)
            return run
        run.status = RUN_STATUS_FAILED
        run.finished_at = time.time()
        run.error = (f"settle 拒伪完成（{decision['reason_code']}）: "
                     f"{decision['detail']}，修复 {SETTLE_MAX_FIXES} 轮后转重规划")
        if record is not None:
            record.status = NODE_STATUS_FAILED
            record.error = run.error
            record.finished_at = time.time()
        self._log(run, node.id, "settle-exhausted",
                  f"修复轮用尽，按 on_error=fail 收口: {decision['detail']}")
        self.store.save(run)
        self.notify_run(run, "failed", node=node.id,
                        dedupe_key=str(run.finished_at))
        return run

    def mark_verified(self, run: FlowRun, node_id: str, *, verified_by: str,
                      evidence: str, ok: bool = True) -> dict[str, Any]:
        """验收证据翻档（唯一翻绿通道）：证据非空 + 验收者≠执行者。

        伪完成被拒并留痕：自审（self_referential_evidence）/证据为空（no_evidence）
        → 拒翻档（FlowRunError）+ settle_refused 日志，节点保持 done·unverified。
        ok=False：验收不过——不许标 completed（拒伪完成），留痕计数。
        """
        record = run.nodes.get(node_id)
        if record is None:
            raise FlowRunError(f"节点不存在: {node_id}")
        if not ok:
            record.settle_rejects += 1
            self._log(run, node_id, "verify-failed",
                      f"验收不过（by={verified_by or '-'}）: {str(evidence)[:200]}")
            self.store.save(run)
            return {"verified": False, "node": node_id,
                    "reason_code": "verification_failed",
                    "detail": "验收不过，不许标 completed（拒伪完成）"}
        allowed, code, detail = can_verify(
            verifier=verified_by, executor=record.executed_by, evidence=evidence)
        if not allowed:
            record.settle_rejects += 1
            self._log(run, node_id, "verify-refused",
                      f"拒翻档 [{code}] {detail}（by={verified_by or '-'}，"
                      f"executor={record.executed_by or '-'}）")
            self.store.save(run)
            raise FlowRunError(f"验收翻档被拒（{code}）: {detail}")
        record.verified = True
        record.verified_by = verified_by
        record.verified_at = time.time()
        record.verify_evidence = str(evidence)[:500]
        record.claim_source = CLAIM_EVIDENCE
        self._log(run, node_id, "verified",
                  f"翻档 Test·verified（by={verified_by} evidence={str(evidence)[:120]}）")
        self.store.save(run)
        return {"verified": True, "node": node_id, "by": verified_by,
                "evidence": str(evidence)[:500]}

    async def verify_node(self, run_id: str, node_id: str, *, verified_by: str,
                          evidence: str, ok: bool = True) -> dict[str, Any]:
        """人工/探针验收入口（看板·API）：内容级验收证据 → 翻 verified / 拒收留痕。"""
        run = self.store.find_run(run_id)
        if run is None:
            raise FlowRunError(f"流程实例不存在: {run_id}")
        result = self.mark_verified(run, node_id, verified_by=verified_by,
                                    evidence=evidence, ok=ok)
        return result

    async def reassign_node(self, run_id: str, node_id: str, *,
                            to_assignee: str = "", target_agent: str = "",
                            by: str = "", note: str = "",
                            expect_version: int | None = None) -> dict[str, Any]:
        """换人重派（agent_node 四补之一）：工单交办新归属（D5 handoff 版本校验）+
        可换 target_agent 重新派发；失败实例原地续跑（同 retry 语义）。
        """
        run = self.store.find_run(run_id)
        if run is None:
            raise FlowRunError(f"流程实例不存在: {run_id}")
        defn = self.get_flow(run.flow_id)
        node = defn.node_map.get(node_id)
        if node is None:
            raise FlowRunError(f"节点不存在: {node_id}")
        if node.type not in ("agent", "human"):
            raise FlowRunError(f"只有 agent/human 协作节点可换人重派（当前 {node.type}）")
        collab = self.collab()
        task = collab.find_by_node(run_id, node_id)
        created_new = False
        if task is None:
            assignee = to_assignee or node.user or "用户"
            task = collab.create(
                kind="agent" if node.type == "agent" else "human",
                run_id=run_id, flow_id=run.flow_id, node_id=node_id,
                assignee=assignee, role=node.role,
                assignee_kind="agent" if node.type == "agent" else "human",
                target_agent=target_agent or node.target_agent,
                title=f"{run.flow_name}:{node_id}",
                instruction=str(node.task), form=node.form,
                timeout_hours=node.timeout_hours, on_timeout=node.on_timeout,
                budget=node.budget, payload={"reassign": {"by": by, "note": note}})
            created_new = True
        else:
            collab.store.handoff(
                task.task_id, to_assignee=to_assignee or task.assignee,
                by=by, note=note or "换人重派", expect_version=expect_version)
            task = collab.store.load(task.task_id)
        if target_agent and target_agent != task.target_agent:
            task.target_agent = target_agent
            task.note("reassign", f"换目标 SoulMate: {target_agent} by={by or '-'}")
            collab.store.save(task)
        if getattr(task, "a2a_task_id", ""):
            # 交接清场：作废在途 a2a 通道（旧 SoulMate 回流按 superseded 规则拒收），
            # 重派才不被唤醒合并挡住（防双发双扣靠 claim 清场 + 通道作废）
            task.a2a_task_id = ""
            task.handed_off_at = 0.0
            task.note("reassign", "作废在途 a2a 通道（旧回流拒收）")
            collab.store.save(task)
        self._log(run, node_id, "reassigned",
                  f"工单 {task.task_id} → {task.assignee}"
                  f"（target_agent={task.target_agent or '-'} by={by or '-'}"
                  f"{' 新开单' if created_new else ''}）")
        self.store.save(run)
        dispatched = False
        if node.type == "agent" and run.status == RUN_STATUS_WAITING:
            try:
                task = await collab.dispatch(task)   # 重派出网（唤醒合并防双发）
                dispatched = True
            except Exception as e:  # noqa: BLE001 — 重派失败留痕，工单保持可重试
                task.note("redispatch-failed", f"{type(e).__name__}: {e}")
                collab.store.save(task)
                self._log(run, node_id, "redispatch-failed", f"{type(e).__name__}: {e}")
        resumed = False
        if run.status == RUN_STATUS_FAILED:
            record = run.nodes.get(node_id)
            if record is not None:
                record.status = NODE_STATUS_PENDING
                record.error = None
                record.attempts = 0
                record.retries = 0
            run.status = RUN_STATUS_RUNNING
            run.error = None
            run.finished_at = None
            run.cursor = node_id
            self._log(run, node_id, "reassign-resume", "换人重派后原地续跑")
            self.store.save(run)
            await self.execute(run_id, start_at=node_id)
            resumed = True
        return {"run_id": run_id, "node": node_id, "task_id": task.task_id,
                "assignee": task.assignee, "target_agent": task.target_agent,
                "created_new": created_new, "dispatched": dispatched,
                "resumed": resumed}

    def reattach_gate_task(self, run_id: str, node_id: str, task: Any) -> bool:
        """INPUT_REQUIRED 转人工（agent_node 四补之一）：挂起闸的工单账重挂到
        人工补输入单（同一恢复面：人交付答案 = 该节点交付回流，流程续跑）。"""
        run = self.store.find_run(run_id)
        if run is None or run.approval is None or run.approval.status != "pending" \
                or run.approval.node_id != node_id:
            return False
        run.approval.kind = "human"
        run.approval.task_id = getattr(task, "task_id", "")
        run.approval.assignee = getattr(task, "assignee", "")
        run.approval.role = getattr(task, "role", "")
        self._log(run, node_id, "input-required-converted",
                  f"SoulMate 要补输入 → 人工工单 {run.approval.task_id}")
        self.store.save(run)
        return True

    async def recover_deliveries(self) -> int:
        """任务级断点恢复（agent_node 四补之一）：WAITING 实例的工单已交付/终态但
        流程没恢复（崩在交付与恢复之间）→ 补恢复续跑/收口。"""
        handled = 0
        for run in self.store.list_runs(status=RUN_STATUS_WAITING, limit=1000):
            approval = run.approval
            if approval is None or approval.status != "pending" or not approval.task_id:
                continue
            try:
                from plugin.flow_collab import get_collab
                task = get_collab().store.find(approval.task_id)
            except Exception:  # noqa: BLE001 — 工单账不可达：下拍再试
                continue
            if task is None:
                continue
            if task.status == "delivered":
                self._log(run, approval.node_id, "recover-delivery",
                          f"工单 {task.task_id} 已交付，断点补恢复续跑")
                self.store.save(run)
                await self.resolve_delivery(
                    run.run_id, task.result,
                    decided_by=task.delivered_by, note="断点补恢复")
                handled += 1
            elif task.status in ("failed", "cancelled"):
                run.status = RUN_STATUS_FAILED
                run.finished_at = time.time()
                run.error = f"工单 {task.task_id} {task.status}，断点恢复收口"
                record = run.nodes.get(approval.node_id)
                if record is not None:
                    record.status = NODE_STATUS_FAILED
                    record.error = run.error
                self._log(run, approval.node_id, "recover-closed", run.error)
                self.store.save(run)
                handled += 1
        return handled

    def unverified_done(self, limit: int = 500) -> dict[str, Any]:
        """done·unverified 清单（监督日清一条）：产活节点 ok 未 verified +
        工单 delivered 未 verified + 完成但未 verified 的实例。"""
        nodes: list[dict[str, Any]] = []
        runs: list[dict[str, Any]] = []
        for run in self.store.list_runs(limit=limit):
            verdict = run.verdict()
            if run.status == RUN_STATUS_COMPLETED and verdict["verdict"] != "verified":
                runs.append({"run_id": run.run_id, "flow_id": run.flow_id,
                             "flow_name": run.flow_name, "verdict": verdict["verdict"],
                             "reasons": verdict["reasons"]})
            for node_id, record in run.nodes.items():
                if record.status == NODE_STATUS_OK and not record.verified \
                        and not record.owned_by \
                        and record.type in ("tool", "agent", "human", "loop", "parallel"):
                    nodes.append({
                        "run_id": run.run_id, "flow_id": run.flow_id,
                        "node": node_id, "type": record.type,
                        "status_axis": record.status_axis,
                        "confidence": record.confidence,
                        "executed_by": record.executed_by,
                        "claim_source": record.claim_source,
                        "settle_rejects": record.settle_rejects,
                        "fix_rounds": record.fix_rounds,
                    })
        tasks: list[dict[str, Any]] = []
        try:
            from plugin.flow_collab import STATUS_DELIVERED, get_collab
            for task in get_collab().store.list(limit=limit):
                if task.status == STATUS_DELIVERED and not getattr(task, "verified", False):
                    tasks.append({"task_id": task.task_id, "kind": task.kind,
                                  "run_id": task.run_id, "node_id": task.node_id,
                                  "assignee": task.assignee,
                                  "delivered_by": task.delivered_by,
                                  "confidence": task.confidence})
        except Exception:  # noqa: BLE001 — 工单账不可达不阻断流程侧清单
            pass
        return {"nodes": nodes, "tasks": tasks, "runs": runs,
                "count": len(nodes) + len(tasks) + len(runs)}

    def _sync_gate_task(self, approval: ApprovalRecord, *, approved: bool,
                        result: Any, decided_by: str) -> None:
        """闸决定同步工单账（幂等：工单侧已终态不覆盖）。"""
        if not approval.task_id:
            return
        try:
            from plugin.flow_collab import STATUS_FAILED, get_collab
            collab = get_collab()
            if approved:
                collab.store.deliver(approval.task_id, result,
                                     delivered_by=decided_by, via="approval")
            else:
                collab.store.close(approval.task_id, STATUS_FAILED,
                                   note=f"审批驳回: {decided_by}")
        except Exception as e:  # noqa: BLE001 — 台账同步失败不阻断流程恢复
            logger.warning("工单台账同步失败 %s: %s", approval.task_id, e)

    def _sync_timeout_task(self, approval: ApprovalRecord) -> None:
        """超时处置同步工单收口：fail→failed，skip→cancelled（幂等）。"""
        if not approval.task_id:
            return
        try:
            from plugin.flow_collab import get_collab
            get_collab().sync_timeout_close(approval.task_id, approval.on_timeout)
        except Exception as e:  # noqa: BLE001
            logger.warning("工单超时收口失败 %s: %s", approval.task_id, e)

    # 协作运行时（工单收发台/角色路由/预算隔离；惰性单例，测试可 reset）
    def collab(self):
        from plugin.flow_collab import get_collab
        runtime = get_collab()
        runtime.attach_engine(self)
        return runtime

    def _resume_cursor(self, defn: FlowDefinition, run: FlowRun,
                       approval_node: NodeSpec | None) -> str | None:
        if run.cursor:
            return run.cursor
        if approval_node is not None:
            return defn.next_node_id(approval_node.id)
        return None

    def check_timeouts(self, now: float | None = None) -> int:
        """审批超时处理（on_timeout=fail | skip）——由调度器周期调用"""
        now = now or time.time()
        handled = 0
        for run in self.store.list_runs(status=RUN_STATUS_WAITING, limit=1000):
            approval = run.approval
            if approval is None or approval.status != "pending":
                continue
            deadline = approval.created_at + approval.timeout_hours * 3600
            if now < deadline:
                continue
            defn = self.get_flow(run.flow_id)
            approval_node = defn.node_map.get(approval.node_id)
            approval.status = "timed-out"
            handled += 1
            self._sync_timeout_task(approval)
            if approval.on_timeout == "skip":
                record = run.nodes.get(approval.node_id)
                if record is not None:
                    record.status = NODE_STATUS_SKIPPED
                    record.output_summary = "审批超时，按 on_timeout=skip 跳过"
                run.status = RUN_STATUS_RUNNING
                run.outputs[approval.node_id] = {"decision": "timed-out", "action": "skip"}
                self._log(run, approval.node_id, "approval-timeout",
                          "超时，按 on_timeout=skip 跳过")
                self.store.save(run)
                if approval.kind in ("budget-gate", "tool-gate"):
                    # 闸超时跳过=跳过整个节点（cursor 是本节点，不能原地续跑兜圈）
                    run.cursor = defn.next_node_id(approval.node_id)
                resume_from = self._resume_cursor(defn, run, approval_node)
                if resume_from:
                    _fire_and_forget(self.execute(run.run_id, start_at=resume_from))
            else:
                run.status = RUN_STATUS_FAILED
                run.finished_at = now
                run.error = f"审批超时（{approval.timeout_hours:g}h 未处理）"
                record = run.nodes.get(approval.node_id)
                if record is not None:
                    record.status = NODE_STATUS_FAILED
                    record.error = run.error
                self._log(run, approval.node_id, "approval-timeout", "超时，按 on_timeout=fail 终止")
                self.store.save(run)
                self.notify_run(run, "failed", node=approval.node_id,
                                dedupe_key=str(run.finished_at))
        return handled

    # 回放
    async def replay(self, run_id: str, now: float | None = None) -> FlowRun:
        """回放：同一流程/触发/输入重跑（新实例，链回 replay_of）"""
        original = self.store.find_run(run_id)
        if original is None:
            raise FlowRunError(f"流程实例不存在: {run_id}")
        trigger = dict(original.trigger)
        trigger["replay_of"] = run_id
        run = self.start_run(
            original.flow_id, trigger, dict(original.inputs),
            idempotency_key=f"replay:{run_id}:{uuid.uuid4().hex[:6]}", now=now,
        )
        run.replay_of = run_id
        self.store.save(run)
        return await self.execute(run.run_id)

    # 事件触发
    def emit(self, event: str, payload: dict[str, Any] | None = None) -> list[FlowRun]:
        payload = payload or {}
        payload_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        started: list[FlowRun] = []
        for defn, _plugin_id in self._flows.values():
            if self.is_paused(defn.id):
                continue   # 暂停：不接事件触发
            for trigger in defn.triggers:
                if trigger.type == "event" and trigger.event == event:
                    key = f"event:{defn.id}:{event}:{payload_hash}"
                    run = self.start_run(
                        defn.id, {"type": "event", "event": event, "payload": payload},
                        inputs=payload, idempotency_key=key,
                    )
                    started.append(run)
        for run in started:
            _fire_and_forget(self.execute(run.run_id))
        return started

    # 定时触发
    def tick(self, now: float | None = None) -> list[FlowRun]:
        """cron 触发检查（分钟粒度 + 幂等键去重）；调度器与测试共用"""
        moment = _dt.datetime.fromtimestamp(now or time.time())
        slot = moment.strftime("%Y%m%d%H%M")
        started: list[FlowRun] = []
        for defn, _plugin_id in self._flows.values():
            if self.is_paused(defn.id):
                continue   # 暂停：不接 cron 触发
            for trigger in defn.triggers:
                if trigger.type != "cron":
                    continue
                try:
                    matched = cron_matches(trigger.cron, moment)
                except ValueError as e:
                    logger.warning("流程 %s cron 非法 %r: %s", defn.id, trigger.cron, e)
                    continue
                if not matched:
                    continue
                key = f"cron:{defn.id}:{slot}"
                if key in self._executed_cron_slots:
                    continue
                self._executed_cron_slots.add(key)
                run = self.start_run(
                    defn.id, {"type": "cron", "cron": trigger.cron, "slot": slot},
                    inputs={}, idempotency_key=key, now=now or time.time(),
                )
                started.append(run)
        for run in started:
            _fire_and_forget(self.execute(run.run_id))
        return started

    def resume_incomplete(self) -> int:
        """断点续跑：status=running 的未完实例从 cursor 恢复（进程重启后）"""
        count = 0
        for run in self.store.list_runs(status=RUN_STATUS_RUNNING, limit=1000):
            if run.cursor is None:
                continue
            _fire_and_forget(self.execute(run.run_id))
            count += 1
        return count

    def _trim(self, flow_id: str) -> None:
        try:
            retain = self.get_flow(flow_id).retain_runs
            self.store.trim(flow_id, retain)
        except FlowRunError:
            pass

    def upcoming_deadlines(self, horizon_s: float = 86400.0) -> list[float]:
        """B4：已知到期时间清单（挂起闸 deadline + 协作工单 due_at）——定时器对齐用。"""
        now = time.time()
        deadlines: list[float] = []
        for run in self.store.list_runs(status=RUN_STATUS_WAITING, limit=200):
            if run.approval is not None and run.approval.status == "pending":
                deadlines.append(
                    run.approval.created_at + run.approval.timeout_hours * 3600)
        try:
            from plugin.flow_collab import get_collab
            for task in get_collab().store.list(limit=500, now=now):
                if task.is_open() and task.due_at:
                    deadlines.append(task.due_at)
        except Exception:  # noqa: BLE001 — 工单账不可达不阻断定时器
            pass
        return [d for d in deadlines if 0 < d - now <= horizon_s]

    # 可观测（面板数据源，经 MCP 工具暴露）
    def run_summary(self, run: FlowRun) -> dict[str, Any]:
        from plugin.flow_errors import normalize_error
        nodes = []
        for node_id, record in run.nodes.items():
            nodes.append({
                "node": node_id,
                "type": record.type,
                "status": record.status,
                "attempts": record.attempts,
                "retries": record.retries,
                "duration_ms": round(record.duration_ms or 0.0, 1),
                "over_budget": record.over_budget,
                "error": record.error or "",
                "error_norm": normalize_error(record.error, where=node_id)
                if record.error else None,
                "output": record.output_summary or "",
                # ── 双轴（reported done ≠ verified）──
                "status_axis": record.status_axis,
                "confidence": record.confidence,
                "claim_source": record.claim_source,
                "executed_by": record.executed_by,
                "verified": record.verified,
                "verified_by": record.verified_by,
                "verify_evidence": record.verify_evidence,
                "settle_rejects": record.settle_rejects,
                "fix_rounds": record.fix_rounds,
            })
        verdict = run.verdict()
        return {
            "run_id": run.run_id,
            "flow_id": run.flow_id,
            "flow_name": run.flow_name,
            "plugin_id": run.plugin_id,
            "status": run.status,
            "verdict": verdict["verdict"],
            "verdict_reasons": verdict["reasons"],
            "trigger": run.trigger.get("type", ""),
            "idempotency_key": run.idempotency_key,
            "replay_of": run.replay_of,
            "created_at": _fmt_ts(run.created_at),
            "finished_at": _fmt_ts(run.finished_at),
            "duration_ms": round(
                ((run.finished_at or time.time()) - run.created_at) * 1000, 1),
            "error": run.error or "",
            "error_norm": normalize_error(run.error) if run.error else None,
            "failed_node": _last_failed_node(run) if run.status == RUN_STATUS_FAILED else "",
            "nodes": nodes,
            "notifications": run.notifications,
        }

    def run_detail_flat(self, run: FlowRun) -> dict[str, Any]:
        """面板 report 注入形状：扁平 kv（节点状态/重试/耗时/输出摘要 + 事件日志）"""
        flat: dict[str, Any] = {
            "实例": run.run_id,
            "流程": f"{run.flow_name}（{run.flow_id}）",
            "状态": run.status,
            "判档": run.verdict()["verdict"],   # 双轴：verified 绿 | done·unverified 黄
            "触发": run.trigger.get("type", ""),
            "创建": _fmt_ts(run.created_at),
            "结束": _fmt_ts(run.finished_at) if run.finished_at else "—",
            "重放自": run.replay_of or "—",
        }
        if run.error:
            flat["错误"] = run.error
        for node_id, record in run.nodes.items():
            budget = " ⏱超预算" if record.over_budget else ""
            err = f" · {record.error}" if record.error else ""
            verify = (f" · 验收[{record.verified_by}]"
                      if record.verified else
                      f" · 未验证（自报 {record.claim_source}）" if record.status == "ok" else "")
            flat[f"节点 {node_id}"] = (
                f"{record.status_axis} · 尝试{record.attempts}次 · 重试{record.retries}次 · "
                f"{record.duration_ms or 0:.0f}ms{budget}{err}{verify} · {record.output_summary or ''}"
            )
        log_lines = [
            f"{_fmt_ts(entry['ts'])} [{entry['node']}] {entry['event']}: {entry['detail']}"
            for entry in run.log[-30:]
        ]
        flat["事件日志"] = "\n".join(log_lines) if log_lines else "（空）"
        return flat


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def next_deadline_wake(now: float, deadlines: list[float], interval: float,
                       *, min_sleep: float = 0.5) -> float:
    """B4 deadline 独立定时器（对位 scheduled-work.ts）：已知到期时间直派——

    唤醒对齐最近 deadline，延迟下限=timer 精度（不等心跳周期）；心跳只做兜底
    recovery scan。无近期到期 → 回落固定 interval。
    """
    future = [d for d in deadlines if d > now]
    if not future:
        return interval
    return max(min_sleep, min(min(future) - now, interval))


# ---- 调度器（cron tick + 审批超时 + 断点续跑）----

class FlowScheduler:
    """后台调度：周期 tick（cron 触发）+ check_timeouts（审批超时）"""

    def __init__(self, engine: FlowEngine, interval_seconds: float = 20.0):
        self.engine = engine
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self.engine.resume_incomplete()
        try:
            await self.engine.recover_deliveries()   # 任务级断点恢复（交付在途补收口）
        except Exception as e:  # noqa: BLE001 — 断点补恢复失败不阻断调度启动
            logger.warning("任务级断点恢复失败: %s", e)
        self._task = asyncio.get_event_loop().create_task(self._loop())
        logger.info("流程调度器已启动（间隔 %.0fs）", self.interval_seconds)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            # B4 deadline 独立定时器：唤醒对齐最近到期点（直派延迟下限=timer 精度），
            # 心跳只做兜底 recovery——不等固定周期
            await asyncio.sleep(next_deadline_wake(
                time.time(), self.engine.upcoming_deadlines(), self.interval_seconds))
            try:
                self.engine.tick()
                self.engine.check_timeouts()
            except Exception as e:  # noqa: BLE001 — 调度循环绝不因单次失败退出
                logger.warning("流程调度 tick 失败: %s", e)
            try:
                collab = self.engine.collab()
                await collab.poll_agent_tasks()   # agent 工单结果回流
                collab.check_timeouts()           # 催办 + B3 三拍判死清扫（独立于 Pulse）
            except Exception as e:  # noqa: BLE001
                logger.warning("协作工单回流轮询失败: %s", e)
            try:
                from plugin import flow_notify
                flow_notify.urge_pending(self.engine)   # 待审批微信催办（间隔去重）
                flow_notify.retry_deferred(self.engine)  # iLink 窗口补投（deferred 通知）
            except Exception as e:  # noqa: BLE001
                logger.warning("待审批催办失败: %s", e)


# ---- 进程级单例（app.py / flow_api / 插件 handler 共用）----

_engine: FlowEngine | None = None


def get_engine() -> FlowEngine:
    """进程级流程引擎单例；运行目录可用 FLOW_RUNS_DIR 覆盖（测试隔离）"""
    global _engine
    if _engine is None:
        import os

        default_dir = Path(__file__).resolve().parent.parent.parent / "data" / "flow-runs"
        runs_dir = os.environ.get("FLOW_RUNS_DIR") or default_dir
        _engine = FlowEngine(runs_dir)
    return _engine


def reset_engine() -> None:
    """测试用：重置单例（FLOW_RUNS_DIR 改后生效）"""
    global _engine
    _engine = None
