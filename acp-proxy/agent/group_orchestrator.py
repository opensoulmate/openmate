"""
AI 群组任务编排 — 路线图 5.3（群组协作：接现有 ai_groups 能力）

对标 Hermes 的群组协作场景：SoulMate 作为编排者，把一个目标交给 AI 群组，
由现有 opensoul `/api/ai-groups` 能力完成 自动分解→按角色分配→并行执行→
事件流追踪，SoulMate 侧负责编排与收尾合并（确定性 merge 不走 LLM）。

设计：
- GroupOrchestrator.orchestrate(group_id, goal)：提交主任务（ai_groups 自动
  分解子任务+按角色自动分配）→ 逐个触发子任务执行（失败隔离：一个 execute
  请求失败不影响兄弟子任务）→ 有界轮询等待全部子任务到终态 → 收集结果
  （终态后读事件流判定 finished/failed，失败可见不假装成功）→ 确定性 merge。
- 传输层可注入（transport(method, path, payload) -> (status, data)），默认走
  httpx 调 SOULMATE_AI_GROUPS_URL（缺省 http://127.0.0.1:8090）——测试零网络。
- 等待预算有界（5-600s，缺省180s）：预算耗尽的子任务记 TIMEOUT 显式标注，
  绝不静默当成功；409（已在执行）容忍为在途照常轮询。
- merge()：成功/失败分节+按输入顺序+失败收尾警示"不要把失败子任务当成
  已完成"（与 5.1 子代理 merge 同款纪律）。

诚实边界：
- 编排对象是 opensoul ai_groups 的既有任务执行面（run_agent_proxy 跑真实
  agent binary），本模块不重复实现执行器；群组内 agent binary 可用性由
  ai_groups 侧 _resolve_agent_binary 决定。
- 轮询为 GET list_tasks 快照轮询（事件流仅终态收集时读取），非 WS 实时尾随。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("acp-proxy.group-orch")

DEFAULT_BASE_URL = "http://127.0.0.1:8090"
API_PREFIX = "/api/ai-groups"

MIN_WAIT_S, MAX_WAIT_S, DEFAULT_WAIT_S = 5.0, 600.0, 180.0
DEFAULT_POLL_INTERVAL_S = 2.0
# 合并文本里单个子任务结果的截断上限（超长自报截断，不静默）
MAX_RESULT_CHARS = 2000

# 任务终态（ai_groups：execute 收尾 scored；complete/verify/失败另有形态）
TERMINAL_STATUSES = {"scored", "completed", "verified", "failed"}
# 事件流里的收尾事件（后出现者覆盖先出现者，用于 finished/failed 判定）
_CLOSING_EVENTS = ("finished", "failed")


class GroupTaskStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class SubtaskOutcome:
    subtask_id: str
    agent_id: str
    goal: str
    status: GroupTaskStatus
    result: str = ""
    quality_score: float = 0.0
    note: str = ""


@dataclass
class GroupRunReport:
    group_id: str
    task_id: str
    goal: str
    subtasks: list[SubtaskOutcome] = field(default_factory=list)
    timed_out: bool = False


# 传输契约：async transport(method, path, payload) -> (http_status, data)
Transport = Callable[[str, str, Optional[dict]], Awaitable[tuple[int, Any]]]


async def _default_transport(
    method: str, path: str, payload: Optional[dict], *, base_url: str, timeout_s: float,
) -> tuple[int, Any]:
    """缺省传输：httpx 调 ai_groups REST API。"""
    import httpx

    url = f"{base_url}{path}"
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        try:
            resp = await client.request(method, url, json=payload)
        except Exception as e:
            logger.warning(f"[group-orch] {method} {path} 传输失败: {type(e).__name__}: {e}")
            return 0, {"error": f"{type(e).__name__}: {e}"}
        try:
            data = resp.json()
        except Exception:
            data = {"text": resp.text[:2000]}
        return resp.status_code, data


class GroupOrchestrator:
    """AI 群组任务编排器：提交目标→触发执行→有界等待→确定性合并。"""

    def __init__(
        self,
        base_url: Optional[str] = None,
        transport: Optional[Transport] = None,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        timeout_s: float = 10.0,
    ):
        self.base_url = (
            base_url
            or os.environ.get("SOULMATE_AI_GROUPS_URL", "")
            or DEFAULT_BASE_URL
        ).rstrip("/")
        self._transport = transport
        self.poll_interval_s = max(0.0, float(poll_interval_s))
        self.timeout_s = float(timeout_s)

    # ── 传输层 ──────────────────────────────────────────────
    async def _request(
        self, method: str, path: str, payload: Optional[dict] = None
    ) -> tuple[int, Any]:
        if self._transport is not None:
            return await self._transport(method, path, payload)
        return await _default_transport(
            method, path, payload, base_url=self.base_url, timeout_s=self.timeout_s
        )

    # ── 只读查询面 ──────────────────────────────────────────
    async def list_groups(self) -> str:
        status, data = await self._request("GET", f"{API_PREFIX}")
        if status != 200 or not isinstance(data, list):
            return f"[错误] 查询AI群组失败: HTTP {status} {data}"
        if not data:
            return "AI群组列表为空（可先在 OpenMate AI群组页创建群组与成员）"
        lines = [f"AI群组（{len(data)}个）："]
        for g in data:
            lines.append(
                f"- {g.get('id', '?')} | {g.get('name', '')} | "
                f"status={g.get('status', '')} | {g.get('description', '')[:60]}"
            )
        return "\n".join(lines)

    async def task_status(self, group_id: str, task_id: str) -> str:
        group_id = str(group_id or "").strip()
        task_id = str(task_id or "").strip()
        if not group_id or not task_id:
            raise ValueError("group_id 和 task_id 不能为空")
        status, data = await self._request("GET", f"{API_PREFIX}/{group_id}/tasks")
        if status != 200 or not isinstance(data, list):
            return f"[错误] 查询任务失败: HTTP {status} {data}"
        row = _find_task(data, task_id)
        if row is None:
            return f"[错误] 任务不存在: {task_id}"
        lines = [
            f"任务 {task_id} | status={row.get('status', '')} | "
            f"agent={row.get('assigned_agent_id') or '-'} | goal={str(row.get('goal', ''))[:80]}"
        ]
        for st in row.get("subtasks") or []:
            lines.append(
                f"  - {st.get('id', '?')} | {st.get('status', '')} | "
                f"{st.get('assigned_agent_id') or '-'} | {str(st.get('goal', ''))[:60]}"
            )
        return "\n".join(lines)

    # ── 编排主流程 ──────────────────────────────────────────
    async def orchestrate(
        self,
        group_id: str,
        goal: str,
        constraints: Optional[list] = None,
        completion_criteria: Optional[list] = None,
        wait_s: float = DEFAULT_WAIT_S,
    ) -> GroupRunReport:
        group_id = str(group_id or "").strip()
        goal = str(goal or "").strip()
        if not group_id:
            raise ValueError("group_id 不能为空")
        if not goal:
            raise ValueError("goal 不能为空")
        try:
            wait_s = float(wait_s)
        except (TypeError, ValueError):
            wait_s = DEFAULT_WAIT_S
        wait_s = min(MAX_WAIT_S, max(MIN_WAIT_S, wait_s))

        # 1) 提交主任务（ai_groups 自动分解+按角色自动分配）
        status, data = await self._request(
            "POST",
            f"{API_PREFIX}/{group_id}/tasks",
            {
                "goal": goal,
                "constraints": [str(c) for c in (constraints or [])],
                "completion_criteria": [str(c) for c in (completion_criteria or [])],
            },
        )
        if status not in (200, 201) or not isinstance(data, dict) or not data.get("task_id"):
            raise RuntimeError(f"提交群组任务失败: HTTP {status} {data}")
        task_id = str(data["task_id"])
        report = GroupRunReport(group_id=group_id, task_id=task_id, goal=goal)

        # 2) 取子任务清单
        subtasks = await self._fetch_subtasks(group_id, task_id)

        # 3) 逐个触发执行（失败隔离：单个 execute 失败不阻塞兄弟）
        fired: list[dict] = []
        for st in subtasks:
            st_id = str(st.get("id", "") or "")
            agent_id = str(st.get("assigned_agent_id", "") or "").strip()
            st_goal = str(st.get("goal", "") or "")
            if not agent_id:
                report.subtasks.append(SubtaskOutcome(
                    subtask_id=st_id, agent_id="", goal=st_goal,
                    status=GroupTaskStatus.FAILED,
                    note="无可用agent（子任务未分配执行者）",
                ))
                continue
            try:
                ex_status, ex_data = await self._request(
                    "POST",
                    f"{API_PREFIX}/{group_id}/tasks/{st_id}/execute",
                    {"agent_id": agent_id, "goal": ""},
                )
            except Exception as e:
                report.subtasks.append(SubtaskOutcome(
                    subtask_id=st_id, agent_id=agent_id, goal=st_goal,
                    status=GroupTaskStatus.FAILED,
                    note=f"execute请求异常: {type(e).__name__}: {e}",
                ))
                continue
            if ex_status in (200, 202):
                fired.append(st)
            elif ex_status == 409:
                # 已在执行（并发保护）——容忍为在途，照常轮询
                fired.append(st)
            else:
                report.subtasks.append(SubtaskOutcome(
                    subtask_id=st_id, agent_id=agent_id, goal=st_goal,
                    status=GroupTaskStatus.FAILED,
                    note=f"execute请求失败: HTTP {ex_status} {ex_data}",
                ))

        # 4) 有界轮询到终态（预算耗尽→TIMEOUT 显式标注）
        pending = {str(st.get("id", "") or ""): st for st in fired}
        terminal_rows: dict[str, dict] = {}
        deadline = time.monotonic() + wait_s
        while pending and time.monotonic() < deadline:
            try:
                ls_status, tasks = await self._request("GET", f"{API_PREFIX}/{group_id}/tasks")
            except Exception as e:
                logger.warning(f"[group-orch] 轮询失败（继续）: {e}")
                tasks = None
                ls_status = 0
            if ls_status == 200 and isinstance(tasks, list):
                flat = _flatten(tasks)
                for st_id in list(pending):
                    row = flat.get(st_id)
                    if row is not None and str(row.get("status", "")) in TERMINAL_STATUSES:
                        terminal_rows[st_id] = row
                        pending.pop(st_id, None)
            if pending:
                await asyncio.sleep(self.poll_interval_s)

        # 5) 收集结果：终态的读事件流判定 finished/failed，未终态=TIMEOUT
        for st_id, st in pending.items():
            report.subtasks.append(SubtaskOutcome(
                subtask_id=st_id,
                agent_id=str(st.get("assigned_agent_id", "") or ""),
                goal=str(st.get("goal", "") or ""),
                status=GroupTaskStatus.TIMEOUT,
                note=f"等待预算耗尽（{wait_s:.0f}s内未到终态）",
            ))
            report.timed_out = True
        for st_id, st in {str(s.get("id", "") or ""): s for s in fired}.items():
            row = terminal_rows.get(st_id)
            if row is None:
                continue
            report.subtasks.append(await self._collect_one(group_id, st_id, st, row))
        # 按输入顺序排序（fired+failed 的录入顺序=子任务创建顺序）
        order = {str(st.get("id", "") or ""): i for i, st in enumerate(subtasks)}
        report.subtasks.sort(key=lambda o: order.get(o.subtask_id, 1 << 30))
        return report

    async def _fetch_subtasks(self, group_id: str, task_id: str) -> list[dict]:
        status, data = await self._request("GET", f"{API_PREFIX}/{group_id}/tasks")
        if status != 200 or not isinstance(data, list):
            return []
        row = _find_task(data, task_id)
        return list((row or {}).get("subtasks") or [])

    async def _collect_one(
        self, group_id: str, st_id: str, st: dict, row: dict
    ) -> SubtaskOutcome:
        """终态子任务收集：事件流收尾事件判定 finished/failed（失败可见）。"""
        agent_id = str(st.get("assigned_agent_id", "") or "")
        goal = str(st.get("goal", "") or "")
        result = str(row.get("result", "") or "")
        try:
            score = float(row.get("quality_score", 0) or 0)
        except (TypeError, ValueError):
            score = 0.0
        if str(row.get("status", "")) == "failed":
            return SubtaskOutcome(
                subtask_id=st_id, agent_id=agent_id, goal=goal,
                status=GroupTaskStatus.FAILED, result=result, quality_score=score,
                note="任务状态failed",
            )
        closing = ""
        try:
            ev_status, events = await self._request(
                "GET", f"{API_PREFIX}/{group_id}/tasks/{st_id}/events?after_id=0"
            )
            ev_list = events.get("events") if isinstance(events, dict) else events
            if ev_status == 200 and isinstance(ev_list, list):
                for ev in ev_list:
                    et = str((ev or {}).get("event_type", ""))
                    if et in _CLOSING_EVENTS:
                        closing = et
        except Exception as e:
            logger.warning(f"[group-orch] 读取事件流失败（按结果判定）: {e}")
        if closing == "failed":
            return SubtaskOutcome(
                subtask_id=st_id, agent_id=agent_id, goal=goal,
                status=GroupTaskStatus.FAILED, result=result, quality_score=score,
                note="执行事件failed（agent执行失败/重试耗尽）",
            )
        return SubtaskOutcome(
            subtask_id=st_id, agent_id=agent_id, goal=goal,
            status=GroupTaskStatus.OK, result=result, quality_score=score,
        )

    # ── 确定性合并（不走 LLM）──────────────────────────────
    @staticmethod
    def merge(report: GroupRunReport) -> str:
        ok = [o for o in report.subtasks if o.status == GroupTaskStatus.OK]
        bad = [o for o in report.subtasks if o.status != GroupTaskStatus.OK]
        lines = [
            "📋 AI群组任务编排结果",
            f"目标任务: {report.goal}",
            f"群组/任务: {report.group_id} / {report.task_id}",
            "",
        ]
        if not report.subtasks:
            lines.append("（无子任务——群组分解为空，检查群组是否有在线成员）")
            return "\n".join(lines)
        if ok:
            lines.append(f"✅ 成功（{len(ok)}）")
            for i, o in enumerate(ok, 1):
                lines.append(f"{i}. [{o.agent_id or '-'}] {o.goal}（评分{o.quality_score:g}）")
                excerpt = o.result.strip() or "（无输出）"
                if len(excerpt) > MAX_RESULT_CHARS:
                    excerpt = (
                        excerpt[:MAX_RESULT_CHARS]
                        + f"\n…（截断，原长{len(o.result)}字）"
                    )
                lines.append(f"   {excerpt}")
            lines.append("")
        if bad:
            lines.append(f"❌ 失败/未完成（{len(bad)}）")
            for i, o in enumerate(bad, 1):
                lines.append(f"{i}. [{o.agent_id or '-'}] {o.goal}")
                lines.append(f"   原因: {o.status.value} — {o.note or '无输出'}")
            lines.append("")
        if bad:
            lines.append("⚠️ 以下子任务失败/超时，不要把失败子任务当成已完成")
        return "\n".join(lines)


# ── 工具函数 ─────────────────────────────────────────────────
def _find_task(tasks: list, task_id: str) -> Optional[dict]:
    for t in tasks:
        if str(t.get("id", "")) == task_id:
            return t
        for st in t.get("subtasks") or []:
            if str(st.get("id", "")) == task_id:
                return {**st, "subtasks": []}
    return None


def _flatten(tasks: list) -> dict:
    flat: dict = {}
    for t in tasks:
        flat[str(t.get("id", ""))] = t
        for st in t.get("subtasks") or []:
            flat[str(st.get("id", ""))] = st
    return flat


_orchestrator: Optional[GroupOrchestrator] = None


def get_group_orchestrator() -> GroupOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = GroupOrchestrator()
    return _orchestrator
