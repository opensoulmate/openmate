"""
任务规划与自省引擎
PRD P0-1: 分层任务规划、自省校验、断点续跑

核心流程：
1. 用户输入复杂目标 → LLM 评估是否需要分解
2. 需要分解 → 生成任务树（子任务 + 依赖关系）
3. 逐步执行子任务，每步执行后自省校验
4. 失败时动态重规划，而非直接终止
5. 全状态持久化，支持断点续跑
"""

import json
import sqlite3
import os
import time
import uuid
import logging
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger("acp-agent.task-engine")


# ── 数据模型 ──────────────────────────────────────────

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    REPLANNED = "replanned"


class ErrorType(str, Enum):
    TOOL_ERROR = "tool_error"          # 工具执行报错
    LOGIC_ERROR = "logic_error"        # 结果正确但不符合业务需求
    UNDERSTANDING_ERROR = "understanding_error"  # 需求理解偏差


@dataclass
class SubTask:
    id: str
    description: str
    tool_hint: str = ""           # 建议使用的工具
    depends_on: list[str] = field(default_factory=list)  # 依赖的子任务ID
    status: StepStatus = StepStatus.PENDING
    result: str = ""
    error: str = ""
    error_type: str = ""
    reflection: str = ""
    retry_count: int = 0
    max_retries: int = 2
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


@dataclass
class TaskPlan:
    id: str
    session_id: str
    goal: str                          # 用户原始目标
    subtasks: list[SubTask] = field(default_factory=list)
    status: str = "active"             # active / completed / failed / paused
    current_step_idx: int = 0
    reflection_report: str = ""
    replan_count: int = 0
    max_replans: int = 3
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0.0


# ── 数据库持久化 ──────────────────────────────────────

def _step_status_raw(status) -> str:
    """P0修复（存取往返断裂）：步骤状态入库存value（'pending'）。
    原实现str(step.status)在py3.11的str-Enum下='StepStatus.PENDING'，
    load_plan的StepStatus(s["status"])解析必抛ValueError——中断恢复场景
    （plan留active→下条消息get_active_plan→load_plan）整条消息路径崩溃。"""
    return getattr(status, "value", str(status))


def _coerce_step_status(raw) -> "StepStatus":
    """容错解析步骤状态：兼容旧存档的str(enum)='StepStatus.PENDING'形态；
    未知值fail-safe按PENDING（at-least-once重跑优于静默丢步骤）。"""
    v = str(raw or "")
    if v.startswith("StepStatus."):
        v = v.split(".", 1)[1]
    try:
        return StepStatus(v.lower())
    except ValueError:
        return StepStatus.PENDING


class TaskStore:
    """任务状态持久化到 SQLite"""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.environ.get("OPENSOUL_DB", "/home/climbing/opensoul/data/opensoul.db")
        self._init_tables()

    def _init_tables(self):
        db = sqlite3.connect(self.db_path)
        db.execute("""
            CREATE TABLE IF NOT EXISTS task_plans (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                current_step_idx INTEGER DEFAULT 0,
                reflection_report TEXT DEFAULT '',
                replan_count INTEGER DEFAULT 0,
                created_at REAL,
                completed_at REAL DEFAULT 0
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS task_steps (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL,
                description TEXT NOT NULL,
                tool_hint TEXT DEFAULT '',
                depends_on TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                error_type TEXT DEFAULT '',
                reflection TEXT DEFAULT '',
                retry_count INTEGER DEFAULT 0,
                created_at REAL,
                completed_at REAL DEFAULT 0,
                step_order INTEGER DEFAULT 0,
                FOREIGN KEY (plan_id) REFERENCES task_plans(id)
            )
        """)
        db.commit()
        db.close()

    def save_plan(self, plan: TaskPlan):
        db = sqlite3.connect(self.db_path)
        db.execute("""
            INSERT OR REPLACE INTO task_plans
            (id, session_id, goal, status, current_step_idx, reflection_report, replan_count, created_at, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (plan.id, plan.session_id, plan.goal, plan.status,
              plan.current_step_idx, plan.reflection_report, plan.replan_count,
              plan.created_at, plan.completed_at))
        # 保存子任务
        for idx, step in enumerate(plan.subtasks):
            db.execute("""
                INSERT OR REPLACE INTO task_steps
                (id, plan_id, description, tool_hint, depends_on, status, result, error, error_type, reflection, retry_count, created_at, completed_at, step_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (step.id, plan.id, step.description, step.tool_hint,
                  json.dumps(step.depends_on), _step_status_raw(step.status), step.result,
                  step.error, step.error_type, step.reflection, step.retry_count,
                  step.created_at, step.completed_at, idx))
        # P0修复（持久化镜像失真）：replan替换/移除的步骤已不属于plan——不删行则
        # load_plan按task_steps全量重建会"复活"旧步骤（停留在pending），断点续跑
        # 会重跑已被替换的步骤。save_plan=全量快照语义（INSERT OR REPLACE），
        # 删除plan外步骤行与之对齐（步骤是状态不是历史）。
        if plan.subtasks:
            placeholders = ",".join("?" for _ in plan.subtasks)
            db.execute(
                f"DELETE FROM task_steps WHERE plan_id = ? AND id NOT IN ({placeholders})",
                (plan.id, *[s.id for s in plan.subtasks]),
            )
        else:
            db.execute("DELETE FROM task_steps WHERE plan_id = ?", (plan.id,))
        db.commit()
        db.close()

    def load_plan(self, plan_id: str) -> Optional[TaskPlan]:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT * FROM task_plans WHERE id = ?", (plan_id,)).fetchone()
        if not row:
            db.close()
            return None
        plan = TaskPlan(
            id=row["id"], session_id=row["session_id"], goal=row["goal"],
            status=row["status"], current_step_idx=row["current_step_idx"],
            reflection_report=row["reflection_report"], replan_count=row["replan_count"],
            created_at=row["created_at"], completed_at=row["completed_at"],
        )
        steps = db.execute("SELECT * FROM task_steps WHERE plan_id = ? ORDER BY step_order", (plan_id,)).fetchall()
        for s in steps:
            plan.subtasks.append(SubTask(
                id=s["id"], description=s["description"], tool_hint=s["tool_hint"],
                depends_on=json.loads(s["depends_on"]), status=_coerce_step_status(s["status"]),
                result=s["result"], error=s["error"], error_type=s["error_type"],
                reflection=s["reflection"], retry_count=s["retry_count"],
                created_at=s["created_at"], completed_at=s["completed_at"],
            ))
        db.close()
        return plan

    def get_active_plan(self, session_id: str) -> Optional[TaskPlan]:
        """获取会话的活跃任务计划"""
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id FROM task_plans WHERE session_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (session_id,)
        ).fetchone()
        db.close()
        if row:
            return self.load_plan(row["id"])
        return None

    def list_plans(self, session_id: str, limit: int = 10) -> list[dict]:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT id, goal, status, created_at, completed_at FROM task_plans WHERE session_id = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, limit)
        ).fetchall()
        db.close()
        return [dict(r) for r in rows]


# ── 任务规划器 ────────────────────────────────────────

PLANNING_PROMPT = """你是一个任务规划器。用户给了你一个目标，你需要判断是否需要拆分为子任务。

## 规则
1. 简单任务（一句话能完成、单次工具调用）→ 不拆分，返回空列表
2. 单工具任务（一个工具调用即可完成全部工作）→ 不拆分，返回空列表。例如：PDF/文件解析提取（文件工具一次调用完成读取+解析+OCR提取）、单张图片分析、单次搜索查询
3. 中等任务（需要2-3步，每步用不同工具或产出不同中间结果）→ 拆分为2-3个子任务
4. 复杂任务（需要多步骤、多工具协作）→ 拆分为多个子任务，标注依赖关系

## 拆分注意事项
- 每个步骤必须产出不同的中间结果，且需要不同的工具/能力
- 不要把"读取文件"和"解析文件内容"拆成两步——文件解析工具一次调用同时完成读取和解析，拆开会导致步骤重复执行
- 如果整个任务用一个工具调用就能完成，返回 needs_planning=false

## 输出格式（严格JSON）
```json
{
  "needs_planning": true/false,
  "reason": "判断理由",
  "subtasks": [
    {
      "id": "step_1",
      "description": "子任务描述",
      "tool_hint": "建议使用的工具",
      "depends_on": []
    }
  ]
}
```

只输出JSON，不要其他文字。"""

REPLAN_PROMPT = """你是一个任务重规划器。之前的任务计划中有子任务失败了，需要你决定如何调整。

## 当前任务计划
{plan_json}

## 失败的子任务
{failed_step}

## 错误信息
{error_info}

## 选项
1. retry — 重试当前子任务（换个方式）
2. skip — 跳过当前子任务（不影响后续）
3. replan — 重新规划后续子任务
4. abort — 终止整个任务

## 输出格式（严格JSON）
```json
{{
  "action": "retry|skip|replan|abort",
  "reason": "决策理由",
  "new_subtasks": []
}}
```
只输出JSON。"""

REFLECTION_PROMPT = """你是一个自省引擎。一个子任务刚执行完，你需要校验结果。

## 子任务
{step_description}

## 执行结果
{step_result}

## 期望
这个子任务应该完成什么：{step_expectation}

## 校验规则
1. 工具是否成功执行（无报错）
2. 结果是否符合子任务的目标
3. 是否有遗漏或错误

## 输出格式（严格JSON）
```json
{{
  "passed": true/false,
  "confidence": 0.0-1.0,
  "error_type": "tool_error|logic_error|understanding_error|null",
  "summary": "一句话总结",
  "suggestion": "如果失败，建议怎么修"
}}
```
只输出JSON。"""


class TaskPlanner:
    """任务规划器 — 使用 LLM 分解复杂目标"""

    def __init__(self, llm_call_fn):
        """
        llm_call_fn: async fn(messages: list[dict]) -> str
        一个调用 LLM 并返回文本的函数
        """
        self._llm_call = llm_call_fn
        self.store = TaskStore()

    async def plan(self, goal: str, session_id: str) -> TaskPlan:
        """分析目标，决定是否需要拆分，生成任务计划"""
        # 先检查是否有活跃任务
        existing = self.store.get_active_plan(session_id)
        if existing and existing.status == "active":
            # 有活跃任务，让LLM判断新消息是继续旧任务还是新任务
            judge_messages = [
                {"role": "system", "content": (
                    "你是任务判断器。用户有一个正在进行的任务，现在发了新消息。\\n"
                    "判断新消息是：1)继续/补充旧任务 2)全新的任务 3)简单的问答/操作（不需要任务规划）\\n"
                    "返回JSON: {\"action\": \"continue\"|\"new\"|\"simple\", \"reason\": \"...\"}\\n"
                    "如果新消息是'把文件发送给我'、'发送给我'、与旧任务无关的请求，返回new或simple。"
                )},
                {"role": "user", "content": f"当前进行中的任务: {existing.goal}\\n用户新消息: {goal}"},
            ]
            try:
                raw = await self._llm_call(judge_messages)
                raw = raw.strip()
                if raw.startswith("```"):
                    raw = raw.split("\\n", 1)[1].rsplit("```", 1)[0].strip()
                judge = json.loads(raw)
                action = judge.get("action", "simple")
                logger.info(f"[plan] LLM judge: action={action}, reason={judge.get('reason','')}")
            except Exception as e:
                logger.warning(f"[plan] Judge failed: {e}, treating as new")
                action = "new"

            if action == "continue":
                logger.info(f"[plan] Resuming existing plan: {existing.id}")
                return existing
            else:
                # 新任务或简单任务，清除旧plan
                existing.status = "completed"
                existing.completed_at = time.time()
                self.store.save_plan(existing)
                logger.info(f"[plan] Cleared old plan: {existing.id}, action={action}")

        messages = [
            {"role": "system", "content": PLANNING_PROMPT},
            {"role": "user", "content": f"目标：{goal}"},
        ]

        try:
            raw = await self._llm_call(messages)
            # 提取 JSON
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"[plan] LLM planning failed: {e}, treating as simple task")
            data = {"needs_planning": False, "reason": "规划失败，作为简单任务处理", "subtasks": []}

        # 2.3 复杂任务分解器：多文件改动目标 → 确定性拆步（不赌LLM规划质量，
        # 多文件改动是最容易"步骤越权/漏文件"的形态）。拆解失败静默回退LLM计划。
        try:
            from agent.task_decomposer import TaskDecomposer
            _decomposer = TaskDecomposer()
            if _decomposer.is_multi_file_change(goal):
                dsteps = _decomposer.decompose(goal, session_id)
                mf_subtasks = [
                    SubTask(
                        id=s.id,
                        description=s.description,
                        tool_hint=s.tool_hint,
                        depends_on=list(s.depends_on),
                    )
                    for s in dsteps
                ]
                mf_plan = TaskPlan(
                    id=f"plan-{uuid.uuid4().hex[:8]}",
                    session_id=session_id,
                    goal=goal,
                    subtasks=mf_subtasks,
                )
                self.store.save_plan(mf_plan)
                logger.info(
                    f"[plan] multi-file decomposition: {len(mf_subtasks)} steps "
                    f"for goal: {goal[:50]}"
                )
                return mf_plan
        except Exception as e:
            logger.warning(f"[plan] task_decomposer failed, fallback to LLM plan: {e}")

        if not data.get("needs_planning") or not data.get("subtasks"):
            # 简单任务，不需要规划
            plan = TaskPlan(
                id=f"plan-{uuid.uuid4().hex[:8]}",
                session_id=session_id,
                goal=goal,
                status="completed",  # 简单任务直接标记完成
            )
            logger.info(f"[plan] Simple task, no decomposition needed: {data.get('reason', '')}")
            return plan

        # 构建任务计划
        subtasks = []
        for item in data["subtasks"]:
            subtasks.append(SubTask(
                id=item.get("id", f"step_{uuid.uuid4().hex[:6]}"),
                description=item["description"],
                tool_hint=item.get("tool_hint", ""),
                depends_on=item.get("depends_on", []),
            ))

        plan = TaskPlan(
            id=f"plan-{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            goal=goal,
            subtasks=subtasks,
        )
        self.store.save_plan(plan)
        logger.info(f"[plan] Created plan {plan.id} with {len(subtasks)} subtasks")
        return plan

    async def replan(self, plan: TaskPlan, failed_step: SubTask, error: str) -> TaskPlan:
        """失败时动态重规划"""
        if plan.replan_count >= plan.max_replans:
            logger.warning(f"[replan] Max replans reached for plan {plan.id}")
            plan.status = "failed"
            plan.reflection_report = f"超过最大重规划次数({plan.max_replans})，任务终止。最后错误：{error}"
            self.store.save_plan(plan)
            return plan

        plan_json = json.dumps({
            "goal": plan.goal,
            "subtasks": [{"id": s.id, "desc": s.description, "status": str(s.status)} for s in plan.subtasks],
        }, ensure_ascii=False, indent=2)

        messages = [
            {"role": "system", "content": REPLAN_PROMPT.format(
                plan_json=plan_json,
                failed_step=f"{failed_step.id}: {failed_step.description}",
                error_info=error[:500],
            )},
            {"role": "user", "content": "请决定如何处理这个失败。"},
        ]

        try:
            raw = await self._llm_call(messages)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
        except Exception as e:
            logger.warning(f"[replan] LLM replan failed: {e}, defaulting to retry")
            data = {"action": "retry", "reason": "重规划失败，默认重试"}

        action = data.get("action", "retry")
        reason = data.get("reason", "")
        logger.info(f"[replan] Action: {action}, reason: {reason}")

        if action == "retry":
            failed_step.retry_count += 1
            failed_step.status = StepStatus.PENDING
            failed_step.error = ""
        elif action == "skip":
            failed_step.status = StepStatus.SKIPPED
            failed_step.reflection = f"跳过：{reason}"
        elif action == "replan":
            failed_step.status = StepStatus.REPLANNED
            # 替换后续子任务
            new_subtasks: list[dict] = data.get("new_subtasks", [])
            if new_subtasks:
                idx = plan.subtasks.index(failed_step)
                plan.subtasks = plan.subtasks[:idx]  # 保留已完成的
                for item in new_subtasks:
                    plan.subtasks.append(SubTask(
                        id=str(item.get("id", f"step_{uuid.uuid4().hex[:6]}")),
                        description=str(item.get("description", "")),
                        tool_hint=str(item.get("tool_hint", "")),
                        depends_on=list(item.get("depends_on", [])),
                    ))
            plan.replan_count += 1
        elif action == "abort":
            plan.status = "failed"
            plan.reflection_report = f"任务终止：{reason}"

        self.store.save_plan(plan)
        return plan


# ── 自省引擎 ──────────────────────────────────────────

class SelfReflector:
    """自省引擎 — 每步执行后校验结果"""

    def __init__(self, llm_call_fn):
        self._llm_call = llm_call_fn

    async def reflect(self, step: SubTask, expectation: str = "") -> dict:
        """校验子任务执行结果"""
        if not step.result:
            return {
                "passed": False,
                "confidence": 0.0,
                "error_type": "tool_error",
                "summary": "没有执行结果",
                "suggestion": "检查工具是否正确执行",
            }

        # 简单规则校验：有错误关键词
        error_keywords = ["错误", "失败", "error", "failed", "exception", "traceback"]
        has_error = any(kw in step.result.lower() for kw in error_keywords)

        if has_error:
            return {
                "passed": False,
                "confidence": 0.8,
                "error_type": "tool_error",
                "summary": "执行结果包含错误信息",
                "suggestion": "检查工具参数和执行环境",
            }

        # 对于简单结果（短文本），跳过 LLM 校验以节省 token
        if len(step.result) < 100 and not expectation:
            return {
                "passed": True,
                "confidence": 0.7,
                "error_type": None,
                "summary": "执行成功（简短结果，跳过深度校验）",
                "suggestion": "",
            }

        # 用 LLM 深度校验
        messages = [
            {"role": "system", "content": REFLECTION_PROMPT.format(
                step_description=step.description,
                step_result=step.result[:1000],
                step_expectation=expectation or step.description,
            )},
            {"role": "user", "content": "请校验这个子任务的执行结果。"},
        ]

        try:
            raw = await self._llm_call(messages)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"[reflect] LLM reflection failed: {e}")
            # 降级：只要有结果就算通过
            return {
                "passed": bool(step.result),
                "confidence": 0.5,
                "error_type": None,
                "summary": f"自省引擎降级处理（LLM调用失败: {e}）",
                "suggestion": "",
            }
