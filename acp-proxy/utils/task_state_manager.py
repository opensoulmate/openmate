"""
TaskStateManager — 独立任务状态管理（不放进LLM上下文）
参考LangGraph范式：State独立存在，规则+LLM共同判断任务边界
"""
import time
import uuid
import json
import re
import sqlite3
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

TASK_TIMEOUT = 1800  # 30分钟无交互自动挂起
SHORT_MESSAGE_LEN = 15  # 短消息阈值，需要LLM辅助判断


@dataclass
class TaskState:
    task_id: str
    session_id: str
    goal: str  # 任务主题（用户原始需求）
    entities: list[str] = field(default_factory=list)  # type: ignore[assignment]  # 关键实体名词
    task_type: str = "general"  # general/code/document/research/chat
    status: str = "ongoing"  # ongoing/suspended/completed/failed
    parent_task_id: Optional[str] = field(default=None)  # 父任务ID（任务栈）
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    message_count: int = 0


class TaskStateManager:
    """独立任务状态管理器 — 维护每个session的任务元数据"""

    def __init__(self, db_path: str = "data/opensoul.db"):
        self.db_path = db_path
        self._init_db()
        # 内存缓存：session_id -> TaskState
        self._cache: dict[str, TaskState] = {}

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_states (
                task_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                entities TEXT DEFAULT '[]',
                task_type TEXT DEFAULT 'general',
                status TEXT DEFAULT 'ongoing',
                parent_task_id TEXT,
                created_at REAL,
                last_active REAL,
                message_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_session ON task_states(session_id, status)")
        conn.commit()
        conn.close()

    def get_current_task(self, session_id: str) -> Optional[TaskState]:
        """获取session当前活跃任务"""
        if session_id in self._cache:
            task = self._cache[session_id]
            if task.status in ("ongoing", "suspended"):
                return task

        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT * FROM task_states WHERE session_id=? AND status IN ('ongoing','suspended') ORDER BY last_active DESC LIMIT 1",
            (session_id,)
        ).fetchone()
        conn.close()

        if row:
            task = TaskState(
                task_id=row[0], session_id=row[1], goal=row[2],
                entities=json.loads(row[3]), task_type=row[4],
                status=row[5], parent_task_id=row[6],
                created_at=row[7], last_active=row[8], message_count=row[9]
            )
            self._cache[session_id] = task
            return task
        return None

    def create_task(self, session_id: str, goal: str, entities: list[str] = None,
                    task_type: str = "general", parent_task_id: str = None) -> TaskState:
        """创建新任务"""
        task = TaskState(
            task_id=f"task-{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            goal=goal,
            entities=entities or [],
            task_type=task_type,
            parent_task_id=parent_task_id,
        )
        self._save(task)
        self._cache[session_id] = task
        logger.info(f"[TaskState] Created: {task.task_id} goal={goal[:50]}")
        return task

    def update_activity(self, session_id: str):
        """更新活跃时间"""
        task = self.get_current_task(session_id)
        if task:
            task.last_active = time.time()
            task.message_count += 1
            self._save(task)

    def complete_task(self, session_id: str):
        """完成当前任务"""
        task = self.get_current_task(session_id)
        if task:
            task.status = "completed"
            self._save(task)
            if session_id in self._cache:
                del self._cache[session_id]
            logger.info(f"[TaskState] Completed: {task.task_id}")

    def suspend_task(self, session_id: str):
        """挂起当前任务（切换到子任务时）"""
        task = self.get_current_task(session_id)
        if task:
            task.status = "suspended"
            self._save(task)
            logger.info(f"[TaskState] Suspended: {task.task_id}")

    def resume_task(self, session_id: str, task_id: str):
        """恢复挂起的任务"""
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "UPDATE task_states SET status='ongoing', last_active=? WHERE task_id=?",
            (time.time(), task_id)
        )
        conn.commit()
        conn.close()
        if session_id in self._cache:
            del self._cache[session_id]  # 清缓存，下次get_current_task会重新加载
        logger.info(f"[TaskState] Resumed: {task_id}")

    def add_entities(self, session_id: str, new_entities: list[str]):
        """动态追加实体（任务执行过程中发现新关键词）"""
        task = self.get_current_task(session_id)
        if task:
            for e in new_entities:
                if e and e not in task.entities and len(e) > 1:
                    task.entities.append(e)
            # 限制实体数量，避免过多导致误匹配
            if len(task.entities) > 20:
                task.entities = task.entities[-20:]
            self._save(task)
            logger.debug(f"[TaskState] Added entities: {new_entities}, total={len(task.entities)}")

    def remove_entities(self, session_id: str, remove: list[str]):
        """移除过时实体"""
        task = self.get_current_task(session_id)
        if task:
            task.entities = [e for e in task.entities if e not in remove]
            self._save(task)

    def pop_parent_task(self, session_id: str) -> Optional[TaskState]:
        """子任务完成后，弹出父任务"""
        task = self.get_current_task(session_id)
        if task and task.parent_task_id:
            self.complete_task(session_id)
            self.resume_task(session_id, task.parent_task_id)
            return self.get_current_task(session_id)
        return None

    def check_timeout(self, session_id: str) -> bool:
        """检查任务是否超时"""
        task = self.get_current_task(session_id)
        if task and task.status == "ongoing":
            if time.time() - task.last_active > TASK_TIMEOUT:
                task.status = "suspended"
                self._save(task)
                logger.info(f"[TaskState] Timeout suspended: {task.task_id}")
                return True
        return False

    def _save(self, task: TaskState):
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            INSERT OR REPLACE INTO task_states
            (task_id, session_id, goal, entities, task_type, status, parent_task_id, created_at, last_active, message_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            task.task_id, task.session_id, task.goal,
            json.dumps(task.entities, ensure_ascii=False),
            task.task_type, task.status, task.parent_task_id,
            task.created_at, task.last_active, task.message_count
        ))
        conn.commit()
        conn.close()


# 实体提取停用词（纯虚词token进实体会污染匹配——单字/虚词bigram几乎命中一切消息，
# 规则C会把无关消息误判continue）
_ENTITY_STOP_CHARS = set("的了是在有和与对也都而把被让给从到很还不就个们我你他她它们这那时会能可要说做一二两")


def extract_entities(user_text: str, limit: int = 10) -> list[str]:
    """从用户消息提取关键实体（规则C实体匹配词料；匹配语义=子串 `e in user_message`）。

    P1修复（2026-09-28）：旧调用面 `[w for w in user_text if len(w) > 1 ...]` 按
    **字符**迭代字符串，len(w)>1 恒False → entities 恒为空 → 规则C（实体→continue）
    与规则F的 has_old_entity 永不命中，任务延续判定全被推向 llm_judge（当时还是死
    路径）→ 任务延续完全失效。单一真源收口到本函数，调用面不得再自扫切词。

    词法（DeerMem / memory_retrieval.query_relevance 同款确定性切词，无嵌入依赖）：
    - 拉丁/数字：整词（保留原大小写——规则C匹配是大小写敏感子串）
    - CJK连续段：切bigram（不足2字取原字）
    - 过滤：长度<2、纯停用词token、重复（保序去重）；limit封顶（默认10，
      与TaskState.entities[:10]旧契约一致）
    """
    if not user_text:
        return []
    tokens: list[str] = re.findall(r"[A-Za-z0-9_]+", user_text)
    for seg in re.findall(r"[\u4e00-\u9fff]+", user_text):
        if len(seg) < 2:
            tokens.append(seg)
        else:
            tokens.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    seen: set[str] = set()
    out: list[str] = []
    for tok in tokens:
        if len(tok) < 2:
            continue
        if all(ch in _ENTITY_STOP_CHARS for ch in tok):
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= limit:
            break
    return out


def judge_task_continuation(
    current_task: Optional[TaskState],
    user_message: str,
    conversation_context: str = ""
) -> dict:
    """
    判断新消息是继续旧任务还是新任务。
    规则层优先（确定性），LLM意图为辅（模糊输入）。

    返回: {"action": "continue"|"new"|"simple", "reason": "..."}
    """
    # ── 无活跃任务 → 新任务 ──
    if not current_task:
        return {"action": "new", "reason": "无活跃任务"}

    # ── 规则A：显式指令 ──
    new_keywords = ["新任务", "新建任务", "换个话题", "忽略上面", "重新开始", "清空上下文", "new task", "start over"]
    if any(k in user_message for k in new_keywords):
        return {"action": "new", "reason": "用户显式要求新任务"}

    continue_keywords = ["继续", "下一步", "重试", "再来", "接着", "然后", "继续执行",
                         "go on", "next", "continue", "keep going"]
    if any(k in user_message for k in continue_keywords):
        return {"action": "continue", "reason": "用户显式要求继续"}

    # ── 规则B：超时 → 挂起，询问用户确认 ──
    if time.time() - current_task.last_active > TASK_TIMEOUT:
        return {"action": "ask", "reason": f"任务超时({TASK_TIMEOUT}s)，已挂起，请确认继续还是新建"}

    # ── 预计算文本重叠度（规则F和规则E都用）──
    goal_chars = set(current_task.goal)
    msg_chars = set(user_message)
    overlap = len(goal_chars & msg_chars) / max(len(goal_chars | msg_chars), 1)

    # ── 规则F：多意图检测（消息同时含新旧任务关键词）──
    # 如果消息很长且包含明显的新任务动词，可能是混合意图
    new_task_verbs = ["帮我写", "创建", "生成", "编写", "设计", "开发", "新建",
                      "write", "create", "generate", "build", "design"]
    has_new_intent = any(v in user_message for v in new_task_verbs)
    has_old_entity = any(e in user_message for e in current_task.entities) if current_task.entities else False
    if has_new_intent and has_old_entity:
        # 混合意图，交给LLM判断
        return {"action": "llm_judge", "reason": f"混合意图：含新任务动词+旧实体", "overlap": overlap}


    # ── 规则C：实体匹配（仅在任务非挂起状态下生效）──
    if current_task.status == "ongoing" and current_task.entities:
        matched = [e for e in current_task.entities if e in user_message]
        if matched:
            return {"action": "continue", "reason": f"匹配实体: {matched}"}
    elif current_task.status == "suspended":
        # 挂起任务命中实体，也要询问确认
        matched = [e for e in current_task.entities if e in user_message]
        if matched:
            return {"action": "ask", "reason": f"挂起任务命中实体{matched}，请确认"}

    # ── 规则D：短消息 → 可能是模糊指代，需LLM辅助 ──
    if len(user_message) < SHORT_MESSAGE_LEN:
        # 短消息且无法判断，返回"ask"让调用方询问用户
        return {"action": "ask", "reason": f"消息过短({len(user_message)}字)，无法判断意图"}

    # ── 规则E：文本重叠度作为辅助特征，不单独决策（overlap已预计算）──

    # ── 所有规则无法确定，交给LLM（附带overlap作为参考特征）──
    return {"action": "llm_judge", "reason": f"规则层无法确定(overlap={overlap:.1%})", "overlap": overlap}
