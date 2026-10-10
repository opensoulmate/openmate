"""
子代理并行执行器 — 路线图 5.1（拆分任务给并行 worker，父代理收尾合并）

对标 Hermes delegate_task：父代理把可并行的独立子任务拆给多个子代理 worker
并发执行（默认 worker=隔离工作会话的无客户端 SoulMate 回合），全部返回后
父代理收尾合并（确定性 merge 不走 LLM：成功/失败分节+按输入顺序输出，
失败可见不假装成功）。

设计：
- SubagentManager.run(subtasks)：asyncio.Semaphore 限并发 + 每个 worker 独立
  asyncio.wait_for 超时隔离——一个 worker 失败/超时不影响其余 worker。
- 默认 runner=default_subagent_runner：唤醒回合同款失败纪律（refusal/LLM级
  失败/零输出→抛异常按失败记，不假装成功），worker 输出从 agent_messages
  turn 归因锚点之后收集（复用 wakeup_executor._max_assistant_id/
  _llm_failure_reply/_has_turn_output 单一真源，不另起判定）。
- 每次执行持久化 subagent_runs 表（~/.hermes/soulmate/memory/subagent_runs.db，
  SOULMATE_SUBAGENT_DB_PATH 覆盖）：失败/超时同样落行，snapshot() 观测面。
  日志写失败只记 warning，绝不阻塞工具链路。
- merge() 确定性合并：父代理（调用 delegate_tasks 的回合）拿到合并文本写
  收尾结论。

诚实边界：
- worker 为真实 LLM 回合（各自调模型+工具），不是模拟执行；
- 多会话并发的工具执行层隔离硬化归路线图 5.2（当前 worker 各用独立会话，
  共享 agent 实例的观测采集端属弱共享）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger("acp-proxy.subagent")

# ── 契约常量（资源合同，非防御性守卫）────────────────────────────
MAX_SUBTASKS = 8
MIN_CONCURRENCY, MAX_CONCURRENCY, DEFAULT_CONCURRENCY = 1, 4, 3
MIN_TIMEOUT_S, MAX_TIMEOUT_S, DEFAULT_TIMEOUT_S = 10.0, 600.0, 120.0
# 合并文本里单个 worker 输出的截断上限（超长自报截断，不静默）
MAX_WORKER_OUTPUT_CHARS = 4000

DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".hermes", "soulmate", "memory", "subagent_runs.db"
)

SUBAGENT_PROMPT_MARKER = "【SoulMate子代理任务】"


class SubagentStatus(str, Enum):
    OK = "ok"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class Subtask:
    """一个子任务（自包含描述——worker 看不到父代理的对话上下文）"""

    goal: str
    context: str = ""
    subtask_id: str = field(default_factory=lambda: f"st_{uuid.uuid4().hex[:10]}")
    timeout_s: float = DEFAULT_TIMEOUT_S


@dataclass
class SubagentResult:
    """单个 worker 的执行结果"""

    subtask_id: str
    goal: str
    status: SubagentStatus
    output: str = ""
    error: str = ""
    elapsed_s: float = 0.0


def build_subagent_prompt(subtask: Subtask, parent_goal: str = "") -> str:
    """worker 回合 prompt——显式声明是子代理任务（不是用户消息）+ 单任务闭环指令。"""
    lines = [
        f"{SUBAGENT_PROMPT_MARKER}｜系统派发的并行子任务，非用户消息]",
        f"子任务目标：{subtask.goal}",
    ]
    if parent_goal.strip():
        lines.append(f"（所属父任务背景：{parent_goal.strip()[:300]}）")
    if subtask.context.strip():
        lines.append(f"补充背景/约束：{subtask.context.strip()[:1000]}")
    lines.append(
        "要求：只完成上面这一个子任务，不要做无关的事；"
        "完成后输出该子任务的结论/结果摘要（≤300字），不要寒暄、不要复述任务。"
    )
    return "\n".join(lines)


def _collect_output(agent: Any, session_id: str, after_id: Optional[int]) -> str:
    """收集 turn 归因锚点之后落库的 assistant 输出（worker 最终产出）。

    超长显式截断自报；取数失败抛异常（交给上层按失败记，不返回空假装成功）。
    """
    if after_id is None:
        raise RuntimeError("worker 输出锚点缺失，无法归因本轮产出")
    db = agent._get_db()
    try:
        rows = db.execute(
            "SELECT content FROM agent_messages "
            "WHERE session_id=? AND role='assistant' AND id>? ORDER BY id",
            (session_id, int(after_id)),
        ).fetchall()
    finally:
        db.close()
    parts = [(r[0] or "").strip() for r in rows]
    text = "\n".join(p for p in parts if p).strip()
    if not text:
        raise RuntimeError("worker 零输出（本轮未落库任何非空 assistant 输出）")
    if len(text) > MAX_WORKER_OUTPUT_CHARS:
        total = len(text)
        text = text[:MAX_WORKER_OUTPUT_CHARS] + f"\n…（输出过长已截断，共{total}字）"
    return text


async def default_subagent_runner(
    subtask: Subtask,
    agent_getter: Optional[Callable[[], Any]] = None,
) -> str:
    """默认 worker 执行体：隔离工作会话跑一个无客户端 SoulMate 回合。

    失败纪律复用唤醒回合单一真源（refusal/LLM级失败/零输出→抛异常按失败记，
    不假装成功）。agent_getter 可注入（测试注入 stub agent，不打真实 LLM）。
    """
    if agent_getter is None:
        # 懒 import：wakeup_executor 顶层不 import soulmate_agent，无环
        # （不用 `as agent_getter`——函数内 import 遮蔽同名参数是既有坑型）
        from agent.wakeup_executor import _get_headless_agent

        getter = _get_headless_agent
    else:
        getter = agent_getter
    agent = getter()
    # 隔离工作会话：每个 worker 独立 session，不碰父代理/用户会话
    resp = await agent.new_session()
    sid = str(getattr(resp, "session_id", "") or "")
    if not sid:
        raise RuntimeError("子代理工作会话创建失败")
    # turn 归因锚点：只把本轮新落库的 assistant 输出算作 worker 产出
    from agent.wakeup_executor import _has_turn_output, _llm_failure_reply, _max_assistant_id

    pre_id = _max_assistant_id(agent, sid)
    r = await agent.prompt(
        [{"type": "text", "text": build_subagent_prompt(subtask)}],
        sid,
    )
    stop_reason = str(getattr(r, "stop_reason", "") or "")
    if stop_reason == "refusal":
        raise RuntimeError("子代理回合被拒（工作会话不存在）")
    bad = _llm_failure_reply(agent, sid, pre_id)
    if bad:
        raise RuntimeError(f"子代理回合LLM级失败（不假装成功）: {bad.strip()[-200:]}")
    if _has_turn_output(agent, sid, pre_id) is False:
        raise RuntimeError("子代理回合零输出（回合无声消失，按失败记）")
    return _collect_output(agent, sid, pre_id)


class SubagentManager:
    """子代理并行执行管理：拆分→并发执行→确定性合并。"""

    def __init__(
        self,
        runner: Optional[Callable[[Subtask], Awaitable[str]]] = None,
        db_path: Optional[str] = None,
        clock: Callable[[], float] = time.time,
    ):
        self._runner = runner or (
            lambda st: default_subagent_runner(st)
        )
        self._db_path = (
            db_path
            or os.environ.get("SOULMATE_SUBAGENT_DB_PATH")
            or DEFAULT_DB_PATH
        )
        self._clock = clock
        self._stats = {
            "total_runs": 0,
            "total_workers": 0,
            "ok": 0,
            "failed": 0,
            "timeout": 0,
        }

    # ── 校验与归一化 ──────────────────────────────────────────
    @staticmethod
    def _normalize(
        subtasks: Any,
        max_concurrency: Any = None,
    ) -> tuple[list[Subtask], int]:
        if not isinstance(subtasks, (list, tuple)) or not subtasks:
            raise ValueError("tasks 必须是非空子任务数组")
        if len(subtasks) > MAX_SUBTASKS:
            raise ValueError(f"子任务最多 {MAX_SUBTASKS} 个，收到 {len(subtasks)} 个")
        subs: list[Subtask] = []
        for i, t in enumerate(subtasks):
            if isinstance(t, Subtask):
                goal = t.goal.strip()
                context = t.context
                timeout_raw = t.timeout_s
            elif isinstance(t, dict):
                goal = str(t.get("goal", "") or "").strip()
                context = str(t.get("context", "") or "")
                timeout_raw = t.get("timeout_s", DEFAULT_TIMEOUT_S)
            else:
                goal, context, timeout_raw = "", "", DEFAULT_TIMEOUT_S
            if not goal:
                raise ValueError(f"第{i + 1}个子任务缺少 goal（自包含目标描述）")
            try:
                timeout = float(timeout_raw)
            except (TypeError, ValueError):
                raise ValueError(f"第{i + 1}个子任务 timeout_s 非法: {timeout_raw!r}")
            timeout = min(MAX_TIMEOUT_S, max(MIN_TIMEOUT_S, timeout))
            if isinstance(t, Subtask):
                t.goal, t.timeout_s = goal, timeout
                subs.append(t)
            else:
                subs.append(Subtask(goal=goal, context=context, timeout_s=timeout))
        try:
            conc = int(max_concurrency) if max_concurrency is not None else DEFAULT_CONCURRENCY
        except (TypeError, ValueError):
            raise ValueError(f"max_concurrency 非法: {max_concurrency!r}")
        conc = min(MAX_CONCURRENCY, max(MIN_CONCURRENCY, conc))
        return subs, conc

    # ── 执行 ─────────────────────────────────────────────────
    async def run(
        self,
        subtasks: Any,
        max_concurrency: Any = None,
        parent_goal: str = "",
        parent_session_id: str = "",
    ) -> list[SubagentResult]:
        """并行执行子任务，返回与输入同序的结果列表（失败/超时不抛，随行可见）。"""
        subs, conc = self._normalize(subtasks, max_concurrency)
        # 父任务背景注入每个 worker 的 context（worker 看不到父对话，
        # parent_goal 是它仅有的全局背景来源）
        if parent_goal.strip():
            for st in subs:
                st.context = (
                    (st.context + "\n" if st.context else "")
                    + f"（父任务背景）{parent_goal.strip()[:300]}"
                )
        run_group = uuid.uuid4().hex[:12]
        sem = asyncio.Semaphore(conc)
        self._stats["total_runs"] += 1

        async def _one(st: Subtask) -> SubagentResult:
            async with sem:
                t0 = self._clock()
                try:
                    out = await asyncio.wait_for(self._runner(st), timeout=st.timeout_s)
                    res = SubagentResult(
                        subtask_id=st.subtask_id, goal=st.goal,
                        status=SubagentStatus.OK, output=str(out),
                    )
                except asyncio.TimeoutError:
                    res = SubagentResult(
                        subtask_id=st.subtask_id, goal=st.goal,
                        status=SubagentStatus.TIMEOUT,
                        error=f"子代理执行超时（>{st.timeout_s:.0f}s），已中断不计入成功",
                    )
                except Exception as e:
                    res = SubagentResult(
                        subtask_id=st.subtask_id, goal=st.goal,
                        status=SubagentStatus.FAILED,
                        error=f"{type(e).__name__}: {e}",
                    )
                res.elapsed_s = max(0.0, self._clock() - t0)
                self._record(run_group, res, parent_session_id)
                return res

        results = await asyncio.gather(*(_one(s) for s in subs))
        return list(results)

    # ── 父代理收尾合并（确定性，不走LLM）────────────────────────
    @staticmethod
    def merge(results: list[SubagentResult], parent_goal: str = "") -> str:
        ok = [r for r in results if r.status == SubagentStatus.OK]
        bad = [r for r in results if r.status != SubagentStatus.OK]
        lines = [
            f"# 🧑‍🤝‍🧑 子代理并行执行完成（{len(results)}个子任务：成功{len(ok)}/失败{len(bad)}）",
            "",
        ]
        for i, r in enumerate(results, 1):
            mark = "✅" if r.status == SubagentStatus.OK else (
                "⏱️" if r.status == SubagentStatus.TIMEOUT else "❌")
            lines.append(f"## 子任务{i} {mark} {r.goal}（{r.elapsed_s:.1f}s）")
            if r.status == SubagentStatus.OK:
                lines.append(r.output.strip() or "（无输出）")
            else:
                lines.append(f"失败原因: {r.error}")
            lines.append("")
        if bad:
            lines.append(
                "---"
            )
            lines.append(
                f"⚠️ {len(bad)}个子任务失败/超时（已在上方标注原因）。"
                "父代理收尾时请如实反映：可补做或向用户说明，不要把失败子任务当成已完成。"
            )
        return "\n".join(lines)

    # ── 持久化与观测 ──────────────────────────────────────────
    def _record(self, run_group: str, res: SubagentResult, parent_session_id: str) -> None:
        self._stats["total_workers"] += 1
        key = res.status.value if res.status.value in ("ok", "failed", "timeout") else "failed"
        self._stats[key] += 1
        try:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT INTO subagent_runs "
                    "(run_group, subtask_id, goal, status, elapsed_ms, output_len, "
                    " error, parent_session_id, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        run_group, res.subtask_id, res.goal[:500], res.status.value,
                        round(res.elapsed_s * 1000, 1), len(res.output),
                        res.error[:500], parent_session_id,
                        time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
                    ),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("[subagent] runs 日志写失败（不阻塞工具链路）: %s", e)

    def _connect(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS subagent_runs ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " run_group TEXT, subtask_id TEXT, goal TEXT, status TEXT,"
            " elapsed_ms REAL, output_len INTEGER, error TEXT,"
            " parent_session_id TEXT, created_at TEXT)"
        )
        return conn

    def recent(self, limit: int = 20) -> list[dict]:
        """最近 worker 执行记录（审计用，只读）。"""
        try:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT run_group, subtask_id, goal, status, elapsed_ms, "
                    "output_len, error, parent_session_id, created_at "
                    "FROM subagent_runs ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
            finally:
                conn.close()
        except Exception as e:
            logger.warning("[subagent] recent 查询失败: %s", e)
            return []
        keys = [
            "run_group", "subtask_id", "goal", "status", "elapsed_ms",
            "output_len", "error", "parent_session_id", "created_at",
        ]
        return [dict(zip(keys, r)) for r in rows]

    def snapshot(self) -> dict:
        return {
            **self._stats,
            "db_path": self._db_path,
        }


# ── 进程内单例（与 _get_message_router 同款惯例）──────────────────
_manager: Optional[SubagentManager] = None


def get_subagent_manager() -> SubagentManager:
    global _manager
    if _manager is None:
        _manager = SubagentManager()
    return _manager
