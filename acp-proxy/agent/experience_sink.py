# -*- coding: utf-8 -*-
"""可复用经验沉淀器 — 路线图 1.3（记忆自动沉淀，带去重审计）。

与既有沉淀链路的分工（查代码实证）：
- MemoryConsolidator.add_fragment：episodic 片段流（按消息长度存，事后 consolidate
  只做 content[:100] 精确哈希合并）——存"发生过什么"，不是"学到什么"。
- skill_manager.try_learn_skill：把工具链提炼成技能（≥2 工具调用），面向流程复用。
- **本模块**：任务完成后把"可复用经验"（结论/教训/坑/正确做法）写入记忆，
  **写入前去重**（精确指纹 + 近似重复），重复经验只累计 times_seen 不重复写，
  并可同步一行到 MEMORY.md 型持久记忆（仅新建时，杜绝重复追加）。

设计要点：
- 存储 SQLite（默认 ~/.hermes/soulmate/memory/experiences.db，EXPERIENCE_DB_PATH 可覆盖）。
- 去重两级：①规范化内容 sha256 精确指纹 ②difflib 近似比对（ratio>=0.85 判重，
  对最近 NEAR_DUP_WINDOW 条比对，防止 O(n²) 全表扫描）。
- 审计字段：times_seen / first_seen / last_seen / source_session / tags，
  可 list_experiences()/audit() 复盘"哪些经验被反复遇到"。
- 判定门槛：回合成功（turn_outcome_success 单一真源）且任务有实质内容
  （有工具调用或回复含经验标记词）才沉淀，避免闲聊污染。
"""

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

logger = logging.getLogger("acp-proxy.experience-sink")

DEFAULT_DB_PATH = str(
    Path.home() / ".hermes" / "soulmate" / "memory" / "experiences.db"
)

# 近似重复判定阈值与比对窗口
NEAR_DUP_RATIO = 0.85
NEAR_DUP_WINDOW = 200

# 经验标记词：回复中含这些词视为"有可复用经验"的弱信号
EXPERIENCE_MARKERS = (
    "经验", "教训", "注意", "坑", "原因", "解决", "正确做法",
    "下次", "以后", "应该", "避免", "铁律", "结论",
)

# 强经验标记：句子以/含这些词才可作为经验摘要（弱标记只用于可沉淀判定）
_STRONG_MARKERS = ("经验", "教训", "注意", "坑", "铁律", "结论", "正确做法", "避免", "下次", "以后")


def _normalize(text: str) -> str:
    """规范化用于指纹/比对：小写、去所有空白、去标点。

    去空白（而非折叠成单空格）让指纹对空格/换行位置不敏感——
    "sqlite 死锁" 与 "sqlite死锁" 同指纹，去重方向保守。
    """
    t = (text or "").lower()
    t = re.sub(r"\s+", "", t)
    t = re.sub(r"[，。！？；：、,.!?;:\"'（）()\[\]【】<>《》\-—*#`~]+", "", t)
    return t.strip()


def _fingerprint(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


class ExperienceSink:
    """可复用经验沉淀器（SQLite + 两级去重 + 审计）。"""

    def __init__(
        self,
        db_path: str = "",
        persistent_memory=None,
    ):
        if not db_path:
            db_path = os.environ.get("EXPERIENCE_DB_PATH") or DEFAULT_DB_PATH
        self.db_path = db_path
        self._persistent_memory = persistent_memory
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL,
                    content TEXT NOT NULL,
                    task_gist TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    times_seen INTEGER DEFAULT 1,
                    first_seen REAL NOT NULL,
                    last_seen REAL NOT NULL,
                    source_session TEXT DEFAULT ''
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_exp_fp ON experiences(fingerprint)"
            )
            conn.commit()

    # ── 判定与提取 ──────────────────────────────────────────────

    @staticmethod
    def _is_reusable(user_text: str, assistant_response: str, tool_calls: list) -> bool:
        """是否值得沉淀为可复用经验。"""
        if not assistant_response or len(assistant_response.strip()) < 20:
            return False
        if tool_calls:
            return True
        resp = assistant_response
        return any(m in resp for m in EXPERIENCE_MARKERS)

    @staticmethod
    def _extract_experience(user_text: str, assistant_response: str) -> str:
        """从回复中提取经验摘要（确定性规则，不走LLM）。

        两级提取：优先含强标记词（经验/教训/坑/铁律…）的句子；无强标记时取
        回复首个实质句子，限长防膨胀。
        """
        resp = re.sub(r"MEDIA:[\w\-/\.]+", "", assistant_response or "").strip()
        sentences = [s.strip() for s in re.split(r"[\n。！？!?]", resp) if s.strip()]
        strong = [s for s in sentences if any(m in s for m in _STRONG_MARKERS)]
        pick = strong[0] if strong else (sentences[0] if sentences else resp)
        pick = pick[:200]
        return pick

    @staticmethod
    def _task_gist(user_text: str) -> str:
        t = re.sub(r"\s+", " ", (user_text or "").strip())
        return t[:60]

    # ── 去重 ────────────────────────────────────────────────────

    def _find_duplicate(self, content: str, fingerprint: str):
        """两级去重：精确指纹 → 近似比对（最近窗口）。返回 (id, times_seen) 或 None。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT id, times_seen FROM experiences WHERE fingerprint = ? "
                "ORDER BY id DESC LIMIT 1",
                (fingerprint,),
            ).fetchone()
            if row:
                return row["id"], row["times_seen"]
            # 近似重复：只比对最近窗口，防 O(n²)
            recent = conn.execute(
                "SELECT id, times_seen, content FROM experiences "
                "ORDER BY id DESC LIMIT ?",
                (NEAR_DUP_WINDOW,),
            ).fetchall()
        norm_new = _normalize(content)
        for r in recent:
            ratio = SequenceMatcher(None, norm_new, _normalize(r["content"])).ratio()
            if ratio >= NEAR_DUP_RATIO:
                return r["id"], r["times_seen"]
        return None

    # ── 主入口 ──────────────────────────────────────────────────

    def record_experience(
        self,
        user_text: str,
        assistant_response: str,
        tool_calls: Optional[list] = None,
        session_id: str = "",
    ) -> dict:
        """任务完成后沉淀可复用经验（带去重）。

        Returns:
            {"action": "created"/"duplicate"/"skipped", ...}
            - created: 新经验入库（若配置了 persistent_memory 同步追加一行）
            - duplicate: 命中已有经验，仅 times_seen+1，不重复写
            - skipped: 不满足沉淀条件
        """
        from agent.trajectory_recorder import turn_outcome_success

        tool_calls = tool_calls or []
        if not turn_outcome_success(assistant_response):
            return {"action": "skipped", "reason": "turn_failed"}
        if not self._is_reusable(user_text, assistant_response, tool_calls):
            return {"action": "skipped", "reason": "not_reusable"}

        content = self._extract_experience(user_text, assistant_response)
        if not content:
            return {"action": "skipped", "reason": "empty_experience"}

        fingerprint = _fingerprint(content)
        now = time.time()
        tags = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                name = tc.get("function", {}).get("name") or tc.get("name") or ""
                if name and name not in tags:
                    tags.append(name)

        dup = self._find_duplicate(content, fingerprint)
        if dup:
            exp_id, times_seen = dup
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "UPDATE experiences SET times_seen = ?, last_seen = ? WHERE id = ?",
                    (times_seen + 1, now, exp_id),
                )
                conn.commit()
            logger.info(
                "[experience] duplicate (id=%s, times_seen=%d): %s",
                exp_id, times_seen + 1, content[:60],
            )
            return {
                "action": "duplicate",
                "id": exp_id,
                "times_seen": times_seen + 1,
                "content": content,
            }

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO experiences
                   (fingerprint, content, task_gist, tags, times_seen,
                    first_seen, last_seen, source_session)
                   VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
                (
                    fingerprint,
                    content,
                    self._task_gist(user_text),
                    json.dumps(tags, ensure_ascii=False),
                    now,
                    now,
                    session_id or "",
                ),
            )
            conn.commit()
            exp_id = cur.lastrowid

        # 同步一行到 MEMORY.md 型持久记忆（仅新建时，去重由本层保证）
        if self._persistent_memory is not None:
            try:
                self._persistent_memory.append(f"经验: {content}")
            except Exception as e:
                logger.debug("[experience] persistent memory append failed: %s", e)

        logger.info("[experience] created (id=%s): %s", exp_id, content[:60])
        return {"action": "created", "id": exp_id, "content": content}

    # ── 审计/复盘 ───────────────────────────────────────────────

    def list_experiences(self, limit: int = 50) -> list:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM experiences ORDER BY times_seen DESC, id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def audit(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT COUNT(*) AS total, COALESCE(SUM(times_seen), 0) AS seen, "
                "SUM(CASE WHEN times_seen > 1 THEN 1 ELSE 0 END) AS repeated "
                "FROM experiences"
            ).fetchone()
        return {
            "total_unique": row["total"],
            "total_seen": row["seen"],
            "repeated_experiences": row["repeated"],
        }
