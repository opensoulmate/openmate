"""
用户偏好学习器 — 借鉴MemoryBank/Generative Agents/ChatGPT Memory
核心思想：从对话中自动学习用户偏好，个性化响应
"""

import logging
import json
import time
import sqlite3
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

logger = logging.getLogger("acp-proxy.user-preferences")


@dataclass
class Preference:
    pref_id: str
    category: str  # "communication", "technical", "workflow", "personal"
    key: str
    value: str
    confidence: float = 0.5  # 0.0 - 1.0
    source: str = ""  # "explicit" (用户明确说) or "inferred" (推断)
    evidence: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    use_count: int = 0


class UserPreferenceLearner:
    """用户偏好学习器"""

    # ── 用户画像事实类型（路线图 1.4）────────────────────────
    # preference=偏好（风格/语言/工作习惯），environment=环境事实（系统/机器/
    # 常用工具/部署位置），identity=身份（角色/技能水平）。category 存 fact_type
    # 或旧版语义类（communication/general/technical/workflow→归入偏好组）。
    FACT_TYPES = {"preference": "偏好", "environment": "环境事实", "identity": "身份"}

    def __init__(self, db_path: str = ""):
        if not db_path:
            base = Path.home() / ".hermes" / "soulmate" / "preferences"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "preferences.db")
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS preferences (
                    pref_id TEXT PRIMARY KEY,
                    category TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    confidence REAL DEFAULT 0.5,
                    source TEXT DEFAULT 'inferred',
                    evidence TEXT DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    use_count INTEGER DEFAULT 0,
                    UNIQUE(category, key)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_prefs_category
                ON preferences(category, confidence)
            """)
            conn.commit()

    def learn_from_message(
        self,
        message: str,
        session_id: str = "",
    ) -> list[Preference]:
        """从用户消息中学习偏好"""
        learned = []

        # 显式偏好模式
        # ⚠️ 首个模式不得含裸"我"——裸"我"会把一切"我…"句收成噪音偏好
        # （"我觉得不对"→伪偏好），污染用户画像注入；"我用…"类环境事实走下方
        # env_patterns 专门收进 environment 组（路线图 1.4）。
        explicit_patterns = [
            (r"(?:我喜欢|我想要|我想用|我偏好|请用|请使用|prefer|I prefer)\s*(.+?)(?:[。，,.]|$)", "explicit"),
            (r"(?:不要|别用|别再|我不喜欢|don't|never)\s*(.+?)(?:[。，,.]|$)", "explicit_negative"),
            (r"(?:以后|以后都|以后请|from now on)\s*(.+?)(?:[。，,.]|$)", "explicit"),
            (r"(?:记住|remember|记住这个)\s*(.+?)(?:[。，,.]|$)", "explicit"),
        ]

        for pattern, source in explicit_patterns:
            for match in re.finditer(pattern, message, re.IGNORECASE):
                content = match.group(1).strip()
                if len(content) > 2:
                    pref = self._upsert_preference(
                        category="communication" if "用" in content or "prefer" in content.lower() else "general",
                        key=self._extract_key(content),
                        value=content,
                        confidence=0.9 if source == "explicit" else 0.3,
                        source=source,
                        evidence=[message[:200]],
                    )
                    if pref:
                        learned.append(pref)

        # 推断偏好模式
        inference_patterns = [
            (r"(?:简短|简洁|short|concise|brief)", "communication", "response_style", "concise"),
            (r"(?:详细|详细点|detailed|thorough|comprehensive)", "communication", "response_style", "detailed"),
            (r"(?:中文|Chinese)", "communication", "language", "zh-CN"),
            (r"(?:英文|English)", "communication", "language", "en-US"),
            (r"(?:代码|code|编程|programming)", "technical", "domain", "coding"),
            (r"(?:Python)", "technical", "preferred_language", "Python"),
            (r"(?:TypeScript|TS)", "technical", "preferred_language", "TypeScript"),
            (r"(?:快速|快点|hurry|quick|fast)", "workflow", "speed", "fast"),
        ]

        for pattern, category, key, value in inference_patterns:
            if re.search(pattern, message, re.IGNORECASE):
                pref = self._upsert_preference(
                    category=category,
                    key=key,
                    value=value,
                    confidence=0.4,
                    source="inferred",
                    evidence=[message[:200]],
                )
                if pref:
                    learned.append(pref)

        # ── 环境事实模式（路线图 1.4 用户画像）──────────────────
        # 高置信句式收进 environment 组；主题词做 key（同主题新值 upsert 覆盖）。
        env_patterns = [
            # (正则, 固定key=None表示取主题词组)
            # ⚠️ 终止符只认中文标点——ASCII ./, 会把 "Ubuntu 24.04"/IP 截断在小数点。
            (r"我的(系统|操作系统|发行版|机器|电脑|服务器|环境)(?:是|为|用的?是)\s*(.+?)(?:[。，；！？]|$)", None),
            (r"我(?:跑|部署|运行)在\s*(.+?)(?:[。，；！？]|$)", "运行环境"),
            (r"(?:我在用|我正在用|我使用|我用)\s*(.+?)(?:[。，；！？]|$)", "常用工具"),
        ]
        for pattern, fixed_key in env_patterns:
            for match in re.finditer(pattern, message, re.IGNORECASE):
                if fixed_key is None:
                    key, content = match.group(1).strip(), match.group(2).strip()
                else:
                    key, content = fixed_key, match.group(1).strip()
                if len(content) > 1:
                    pref = self._upsert_preference(
                        category="environment",
                        key=key,
                        value=content[:200],
                        confidence=0.7,
                        source="inferred",
                        evidence=[message[:200]],
                    )
                    if pref:
                        learned.append(pref)

        if learned:
            logger.info(f"Learned {len(learned)} preferences from message")
        return learned

    def _upsert_preference(
        self,
        category: str,
        key: str,
        value: str,
        confidence: float,
        source: str,
        evidence: list[str],
    ) -> Optional[Preference]:
        """创建或更新偏好"""
        import hashlib
        pref_id = f"pref_{hashlib.sha256(f'{category}:{key}'.encode()).hexdigest()[:12]}"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            existing = conn.execute(
                "SELECT * FROM preferences WHERE category = ? AND key = ?",
                (category, key),
            ).fetchone()

            if existing:
                # 更新：加权平均置信度
                old_conf = existing["confidence"]
                new_conf = (old_conf + confidence) / 2
                old_evidence = json.loads(existing["evidence"])
                all_evidence = (old_evidence + evidence)[-5:]  # 保留最近5条

                conn.execute(
                    """UPDATE preferences SET
                       value = ?, confidence = ?, source = ?, evidence = ?, updated_at = ?
                       WHERE category = ? AND key = ?""",
                    (value, new_conf, source, json.dumps(all_evidence),
                     time.time(), category, key),
                )
                conn.commit()
                return Preference(
                    pref_id=pref_id, category=category, key=key, value=value,
                    confidence=new_conf, source=source, evidence=all_evidence,
                )
            else:
                conn.execute(
                    """INSERT INTO preferences
                       (pref_id, category, key, value, confidence, source, evidence, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (pref_id, category, key, value, confidence, source,
                     json.dumps(evidence), time.time(), time.time()),
                )
                conn.commit()
                return Preference(
                    pref_id=pref_id, category=category, key=key, value=value,
                    confidence=confidence, source=source, evidence=evidence,
                )

    def get_preferences(self, category: str = "", min_confidence: float = 0.3) -> list[dict]:
        """获取偏好列表"""
        sql = "SELECT * FROM preferences WHERE confidence >= ?"
        params: list = [min_confidence]

        if category:
            sql += " AND category = ?"
            params.append(category)

        sql += " ORDER BY confidence DESC"

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        return [dict(r) for r in rows]

    def get_context_prompt(self) -> str:
        """生成用户画像上下文（注入到system prompt，路线图 1.4：按类型分组）"""
        prefs = self.get_preferences(min_confidence=0.5)
        if not prefs:
            return ""

        lines: list[str] = []
        for _type_key, type_label in self.FACT_TYPES.items():
            group = self._filter_by_type(prefs, _type_key)
            if not group:
                continue
            lines.append(f"### {type_label}")
            for p in group[:8]:
                source_icon = "📌" if p["source"] == "explicit" else "💡"
                lines.append(f"- {source_icon} {p['key']}: {p['value']} (置信度: {p['confidence']:.0%})")
            lines.append("")
        return "\n".join(lines).strip()

    def record_fact(
        self,
        fact_type: str,
        key: str,
        value: str,
        source: str = "explicit",
        evidence: Optional[list[str]] = None,
        confidence: float = 0.95,
    ) -> tuple[bool, str]:
        """显式记录用户画像事实（profile_record 工具入口，路线图 1.4）。

        同一 fact_type+key 再次记录=更新旧值（upsert），不产生重复条目。
        """
        ft = (fact_type or "").strip().lower()
        if ft not in self.FACT_TYPES:
            return False, f"错误: fact_type 必须是 {list(self.FACT_TYPES)} 之一，收到: {fact_type!r}"
        key = (key or "").strip()
        value = (value or "").strip()
        if not key or not value:
            return False, "错误: key 和 value 不能为空"
        ev = [e.strip() for e in (evidence or []) if str(e).strip()][:3] or ["用户显式告知"]
        pref = self._upsert_preference(
            category=ft,
            key=key[:100],
            value=value[:500],
            confidence=confidence,
            source=source,
            evidence=ev,
        )
        if pref is None:
            return False, "错误: 存储失败"
        logger.info(f"[profile] recorded {ft}: {key}={value}")
        return True, f"✅ 已记录{self.FACT_TYPES[ft]}: {key} = {value}"

    def get_profile_summary(self, fact_type: str = "") -> str:
        """用户画像摘要（profile_read 工具入口），可按 fact_type 过滤。"""
        ft = (fact_type or "").strip().lower()
        if ft and ft not in self.FACT_TYPES:
            return f"错误: fact_type 必须是 {list(self.FACT_TYPES)} 之一，收到: {fact_type!r}"
        prefs = self.get_preferences(min_confidence=0.0)
        if ft:
            prefs = self._filter_by_type(prefs, ft)
        if not prefs:
            return "（用户画像为空：还没有记录任何偏好/环境事实/身份）"

        lines = ["# 用户画像", ""]
        for _type_key, type_label in self.FACT_TYPES.items():
            group = self._filter_by_type(prefs, _type_key)
            if not group:
                continue
            lines.append(f"## {type_label}")
            for p in group:
                icon = "📌" if p["source"] == "explicit" else "💡"
                lines.append(f"- {icon} {p['key']}: {p['value']}（置信度: {p['confidence']:.0%}）")
            lines.append("")
        return "\n".join(lines).strip()

    @classmethod
    def _filter_by_type(cls, prefs: list[dict], fact_type: str) -> list[dict]:
        """按画像类型分组：environment/identity 精确匹配，偏好组收纳其余旧语义类。"""
        if fact_type == "environment":
            return [p for p in prefs if p["category"] == "environment"]
        if fact_type == "identity":
            return [p for p in prefs if p["category"] == "identity"]
        return [p for p in prefs if p["category"] not in ("environment", "identity")]

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM preferences").fetchone()[0]
            by_category = conn.execute(
                "SELECT category, COUNT(*) FROM preferences GROUP BY category"
            ).fetchall()
            explicit = conn.execute(
                "SELECT COUNT(*) FROM preferences WHERE source = 'explicit'"
            ).fetchone()[0]

        return {
            "total_preferences": total,
            "explicit_preferences": explicit,
            "inferred_preferences": total - explicit,
            "by_category": {c[0]: c[1] for c in by_category},
        }

    def _extract_key(self, text: str) -> str:
        """从文本中提取偏好key"""
        # 简单实现：取前20个字符作为key
        return text[:20].strip()
