"""
Agent自我反思引擎 — 借鉴Reflexion/Self-Refine/CRITIC
核心思想：执行后自动反思结果质量，识别错误模式，生成改进策略
"""

import logging
import json
import time
import sqlite3
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

logger = logging.getLogger("acp-proxy.reflection")


@dataclass
class ReflectionEntry:
    reflection_id: str
    session_id: str
    task_description: str
    result_summary: str
    quality_score: float  # 0.0 - 1.0
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    improvement_suggestions: list[str] = field(default_factory=list)
    error_patterns: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    applied: bool = False


class ReflectionEngine:
    """自我反思引擎"""

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "soulmate" / "reflection"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "reflections.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reflections (
                    reflection_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    task_description TEXT NOT NULL,
                    result_summary TEXT DEFAULT '',
                    quality_score REAL DEFAULT 0.5,
                    strengths TEXT DEFAULT '[]',
                    weaknesses TEXT DEFAULT '[]',
                    improvement_suggestions TEXT DEFAULT '[]',
                    error_patterns TEXT DEFAULT '[]',
                    created_at REAL NOT NULL,
                    applied INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_reflections_session
                ON reflections(session_id, created_at)
            """)
            conn.commit()

    def record_reflection(
        self,
        session_id: str,
        task_description: str,
        result_summary: str,
        quality_score: float,
        strengths: Optional[list[str]] = None,
        weaknesses: Optional[list[str]] = None,
        improvement_suggestions: Optional[list[str]] = None,
        error_patterns: Optional[list[str]] = None,
    ) -> ReflectionEntry:
        """记录一次反思"""
        entry = ReflectionEntry(
            # 20.2：毫秒时间戳单独作主键会同毫秒碰撞（第二条 INSERT 静默丢失）
            # ——补 uuid 短后缀保证唯一，refl_ 前缀保持兼容
            reflection_id=f"refl_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}",
            session_id=session_id,
            task_description=task_description,
            result_summary=result_summary,
            quality_score=max(0.0, min(1.0, quality_score)),
            strengths=strengths or [],
            weaknesses=weaknesses or [],
            improvement_suggestions=improvement_suggestions or [],
            error_patterns=error_patterns or [],
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO reflections
                   (reflection_id, session_id, task_description, result_summary,
                    quality_score, strengths, weaknesses, improvement_suggestions,
                    error_patterns, created_at, applied)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (entry.reflection_id, entry.session_id, entry.task_description,
                 entry.result_summary, entry.quality_score,
                 json.dumps(entry.strengths), json.dumps(entry.weaknesses),
                 json.dumps(entry.improvement_suggestions),
                 json.dumps(entry.error_patterns), entry.created_at),
            )
            conn.commit()

        logger.info(
            f"Reflection recorded: score={quality_score:.2f}, "
            f"weaknesses={len(entry.weaknesses)}, "
            f"suggestions={len(entry.improvement_suggestions)}"
        )
        return entry

    def get_recent_reflections(self, session_id: str, limit: int = 5) -> list[dict]:
        """获取最近的反思记录"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT * FROM reflections WHERE session_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()

        return [dict(r) for r in rows]

    def get_improvement_context(self, session_id: str) -> str:
        """获取改进上下文（注入到prompt中）"""
        reflections = self.get_recent_reflections(session_id, limit=3)

        if not reflections:
            return ""

        context_parts = ["## 历史反思与改进\n"]

        for r in reflections:
            if r["quality_score"] < 0.7:
                weaknesses = json.loads(r["weaknesses"])
                suggestions = json.loads(r["improvement_suggestions"])
                if weaknesses or suggestions:
                    context_parts.append(f"### 任务: {r['task_description'][:100]}")
                    context_parts.append(f"质量分数: {r['quality_score']:.1f}")
                    if weaknesses:
                        context_parts.append(f"不足: {'; '.join(weaknesses[:3])}")
                    if suggestions:
                        context_parts.append(f"改进建议: {'; '.join(suggestions[:3])}")
                    context_parts.append("")

        return "\n".join(context_parts)

    def get_error_pattern_stats(self) -> dict:
        """获取错误模式统计"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT error_patterns FROM reflections ORDER BY created_at DESC LIMIT 100"
            ).fetchall()

        pattern_counts: dict[str, int] = {}
        for row in rows:
            patterns = json.loads(row["error_patterns"])
            for p in patterns:
                pattern_counts[p] = pattern_counts.get(p, 0) + 1

        return dict(sorted(pattern_counts.items(), key=lambda x: -x[1])[:10])

    def get_quality_trend(self, session_id: str, limit: int = 20) -> list[dict]:
        """获取质量趋势"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT quality_score, created_at, task_description
                   FROM reflections WHERE session_id = ?
                   ORDER BY created_at ASC LIMIT ?""",
                (session_id, limit),
            ).fetchall()

        return [
            {
                "score": r["quality_score"],
                "timestamp": r["created_at"],
                "task": r["task_description"][:50],
            }
            for r in rows
        ]

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM reflections").fetchone()[0]
            avg_score = conn.execute(
                "SELECT AVG(quality_score) FROM reflections"
            ).fetchone()[0] or 0
            low_quality = conn.execute(
                "SELECT COUNT(*) FROM reflections WHERE quality_score < 0.5"
            ).fetchone()[0]

        return {
            "total_reflections": total,
            "avg_quality_score": f"{avg_score:.2f}",
            "low_quality_count": low_quality,
            "error_patterns": self.get_error_pattern_stats(),
        }
