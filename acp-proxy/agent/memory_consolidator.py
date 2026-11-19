"""
Agent记忆整合器 — 借鉴Mem0 memory consolidation + Generative Agents memory stream
核心思想：定期整合零散记忆，去重、合并、更新重要性分数
"""

import logging
import json
import time
import sqlite3
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from agent.memory_hybrid import keyword_relevance
from agent.memory_vector_index import MemoryVectorIndex, cosine

logger = logging.getLogger("acp-proxy.memory-consolidator")


@dataclass
class MemoryFragment:
    fragment_id: str
    content: str
    memory_type: str  # "episodic", "semantic", "procedural"
    importance: float = 0.5
    access_count: int = 0
    last_accessed_at: float = 0.0
    created_at: float = field(default_factory=time.time)
    consolidated: bool = False
    merged_into: str = ""  # 合并到哪个fragment
    tags: list[str] = field(default_factory=list)


class MemoryConsolidator:
    """记忆整合器"""

    def __init__(self, db_path: str = "", embed_fn=None):
        if not db_path:
            base = Path.home() / ".hermes" / "soulmate" / "memory-consolidator"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "fragments.db")
        self.db_path = db_path
        # MEM-Semantic：片段向量索引 + embedding 注入口（与 MemoryRetrievalEngine
        # 同纪律：未配置=语义通道如实缺席，keyword 通道兜底）
        self._vector_index = MemoryVectorIndex(db_path)
        self._embed_fn = embed_fn
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_fragments (
                    fragment_id TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    memory_type TEXT DEFAULT 'episodic',
                    importance REAL DEFAULT 0.5,
                    access_count INTEGER DEFAULT 0,
                    last_accessed_at REAL DEFAULT 0,
                    created_at REAL NOT NULL,
                    consolidated INTEGER DEFAULT 0,
                    merged_into TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_frag_type
                ON memory_fragments(memory_type, importance)
            """)
            conn.commit()

    def add_fragment(
        self,
        content: str,
        memory_type: str = "episodic",
        importance: float = 0.5,
        tags: Optional[list[str]] = None,
    ) -> str:
        """添加记忆片段"""
        fragment_id = f"frag_{hashlib.sha256(f'{content}:{time.time()}'.encode()).hexdigest()[:12]}"

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memory_fragments
                   (fragment_id, content, memory_type, importance, created_at, tags)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (fragment_id, content, memory_type, importance, time.time(),
                 json.dumps(tags or [])),
            )
            conn.commit()

        # MEM-Semantic ②：片段写入时自动向量化（失败不打断，recall 期自愈）
        fn = self._embed_fn
        if fn is None:
            try:
                from agent import memory_embedding

                fn = memory_embedding.embed_texts
            except Exception:
                fn = None
        if fn is not None:
            try:
                vecs = fn([content])
                if vecs and vecs[0]:
                    self._vector_index.upsert(fragment_id, content, vecs[0])
            except Exception as exc:
                logger.debug(f"fragment vectorization failed (self-heals at recall): {exc}")

        return fragment_id

    def consolidate(self, similarity_threshold: float = 0.7) -> dict:
        """执行记忆整合

        1. 去重（相似内容合并）
        2. 重要性衰减（旧记忆降低重要性）
        3. 低重要性记忆归档
        """
        now = time.time()
        stats = {"merged": 0, "archived": 0, "updated": 0}

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # 获取所有活跃片段
            fragments = conn.execute(
                """SELECT * FROM memory_fragments
                   WHERE consolidated = 0 AND merged_into = ''
                   ORDER BY importance DESC"""
            ).fetchall()

            # 1. 去重合并
            seen_content: dict[str, str] = {}  # content_hash -> fragment_id
            for frag in fragments:
                content = frag["content"]
                content_hash = hashlib.sha256(content[:100].encode()).hexdigest()

                if content_hash in seen_content:
                    # 合并到已有片段
                    primary_id = seen_content[content_hash]
                    conn.execute(
                        """UPDATE memory_fragments SET
                           importance = MAX(importance, ?),
                           access_count = access_count + ?
                           WHERE fragment_id = ?""",
                        (frag["importance"], frag["access_count"], primary_id),
                    )
                    conn.execute(
                        """UPDATE memory_fragments SET
                           consolidated = 1, merged_into = ?
                           WHERE fragment_id = ?""",
                        (primary_id, frag["fragment_id"]),
                    )
                    stats["merged"] += 1
                else:
                    seen_content[content_hash] = frag["fragment_id"]

            # 2. 重要性衰减（超过7天未访问的降低重要性）
            cutoff = now - 7 * 86400
            cursor = conn.execute(
                """UPDATE memory_fragments SET
                   importance = importance * 0.9
                   WHERE last_accessed_at < ? AND last_accessed_at > 0""",
                (cutoff,),
            )
            stats["updated"] = cursor.rowcount or 0

            # 3. 归档低重要性片段
            cursor = conn.execute(
                """UPDATE memory_fragments SET consolidated = 1
                   WHERE importance < 0.1 AND access_count < 2""",
            )
            stats["archived"] = cursor.rowcount or 0

            conn.commit()

        logger.info(f"Memory consolidation: {stats}")
        return stats

    def recall(
        self,
        query: str,
        memory_type: str = "",
        limit: int = 10,
    ) -> list[dict]:
        """召回相关记忆"""
        sql = """
            SELECT * FROM memory_fragments
            WHERE consolidated = 0 AND merged_into = ''
        """
        params: list = []

        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type)

        sql += " ORDER BY importance DESC, access_count DESC LIMIT ?"
        params.append(limit * 3)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        # MEM-Semantic ③：混合打分——keyword=token 覆盖度（CJK bigram；旧实现
        # 空格切词对中文整句零命中）+ semantic=向量余弦（通道缺席=0 如实），
        # 取两通道较强者为 relevance；至少一通道命中才进召回。
        fn = self._embed_fn
        if fn is None:
            try:
                from agent import memory_embedding

                fn = memory_embedding.embed_texts
            except Exception:
                fn = None
        dicts = [dict(row) for row in rows]
        sims: dict[str, float] = {}
        if fn is not None and (query or "").strip():
            try:
                contents = {d["fragment_id"]: d["content"] for d in dicts}
                vectors = self._vector_index.load(contents=contents)
                missing = [(fid, c) for fid, c in contents.items() if fid not in vectors]
                if missing:
                    stats = self._vector_index.backfill(missing, fn)
                    if stats["embedded"]:
                        vectors = self._vector_index.load(contents=contents)
                q_vecs = fn([query])
                q_vec = q_vecs[0] if q_vecs else []
                if q_vec:
                    for fid, vec in vectors.items():
                        try:
                            sims[fid] = cosine(q_vec, vec)
                        except ValueError:
                            continue
            except Exception as exc:
                logger.debug(f"recall semantic channel degraded: {exc}")

        scored = []
        for d in dicts:
            kw = keyword_relevance(query, d["content"])
            sem = max(0.0, min(1.0, sims.get(d["fragment_id"], 0.0)))
            relevance = max(kw, sem)
            if relevance > 0:
                d["relevance"] = relevance
                d["keyword"] = kw
                d["semantic"] = round(sem, 4) if d["fragment_id"] in sims else None
                scored.append(d)

        scored.sort(key=lambda x: (x["relevance"], x["importance"]), reverse=True)

        # 更新访问计数
        result_ids = [s["fragment_id"] for s in scored[:limit]]
        if result_ids:
            with sqlite3.connect(self.db_path) as conn:
                placeholders = ",".join("?" * len(result_ids))
                conn.execute(
                    f"""UPDATE memory_fragments SET
                        access_count = access_count + 1,
                        last_accessed_at = ?
                        WHERE fragment_id IN ({placeholders})""",
                    [time.time()] + result_ids,
                )
                conn.commit()

        return scored[:limit]

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM memory_fragments").fetchone()[0]
            active = conn.execute(
                "SELECT COUNT(*) FROM memory_fragments WHERE consolidated = 0 AND merged_into = ''"
            ).fetchone()[0]
            by_type = conn.execute(
                """SELECT memory_type, COUNT(*) FROM memory_fragments
                   WHERE consolidated = 0 GROUP BY memory_type"""
            ).fetchall()
            avg_importance = conn.execute(
                """SELECT AVG(importance) FROM memory_fragments
                   WHERE consolidated = 0"""
            ).fetchone()[0]

        return {
            "total_fragments": total,
            "active_fragments": active,
            "by_type": {t[0]: t[1] for t in by_type},
            "avg_importance": round(avg_importance, 3) if avg_importance else 0,
        }
