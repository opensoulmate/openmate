"""SoulMate 记忆向量索引（MEM-Semantic ②）：SQLite 持久化 memory_id→embedding。

opensoul src/hippo/memory_vector_index.py 的跨仓镜像（同 schema、同契约；两仓
不能互相 import，代码镜像+测试双钉）。向量落各自记忆库（memory.db/fragments.db
的 memory_vectors 表），随库迁移/隔离。

诚实边界：空向量=不落表（禁缓存假向量）；content_hash 不变=幂等跳过；内容
变更=覆盖旧向量（陈旧向量禁参与排名）。
"""

import hashlib
import json
import math
import sqlite3
import time


def cosine(a: list[float], b: list[float]) -> float:
    """cosine 相似度纯函数。零范数=0.0；维度不齐=ValueError（禁 zip 静默截断）。"""
    if len(a) != len(b):
        raise ValueError(f"vector dimension mismatch: {len(a)} != {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _content_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


class MemoryVectorIndex:
    """memory_id→embedding 持久索引（宿主库内 memory_vectors 表）。"""

    def __init__(self, db_path: str, table: str = "memory_vectors"):
        self.db_path = db_path
        self.table = table
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self.table} (
                    memory_id TEXT PRIMARY KEY,
                    embedding TEXT NOT NULL,
                    dims INTEGER NOT NULL,
                    model TEXT DEFAULT '',
                    content_hash TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )"""
            )
            conn.commit()

    def upsert(self, memory_id: str, content: str, embedding: list[float]) -> bool:
        """落一条向量。空向量=False 禁缓存；成功=True。"""
        if not embedding:
            return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                f"""INSERT INTO {self.table}
                    (memory_id, embedding, dims, content_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(memory_id) DO UPDATE SET
                        embedding = excluded.embedding,
                        dims = excluded.dims,
                        content_hash = excluded.content_hash,
                        updated_at = excluded.updated_at""",
                (
                    str(memory_id),
                    json.dumps([float(x) for x in embedding]),
                    len(embedding),
                    _content_hash(content),
                    time.time(),
                ),
            )
            conn.commit()
        return True

    def get(self, memory_id: str) -> list[float] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT embedding FROM {self.table} WHERE memory_id = ?",
                (str(memory_id),),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def is_fresh(self, memory_id: str, content: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                f"SELECT content_hash FROM {self.table} WHERE memory_id = ?",
                (str(memory_id),),
            ).fetchone()
        return bool(row) and row[0] == _content_hash(content)

    def delete(self, memory_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                f"DELETE FROM {self.table} WHERE memory_id = ?", (str(memory_id),)
            )
            conn.commit()
        return (cur.rowcount or 0) > 0

    def load(self, contents: dict[str, str] | None = None) -> dict[str, list[float]]:
        """读全量向量；contents 在场=陈旧行过滤（hash 不符不返回，禁旧向量排名）。"""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT memory_id, embedding, content_hash FROM {self.table}"
            ).fetchall()
        out: dict[str, list[float]] = {}
        for mid, emb, ch in rows:
            if contents is not None and (
                mid not in contents or _content_hash(contents[mid]) != ch
            ):
                continue
            out[mid] = json.loads(emb)
        return out

    def backfill(self, items: list[tuple[str, str]], embed_fn) -> dict:
        """批量回填缺失/陈旧向量（写入漏算自愈通道）。返回
        {requested, embedded, failed, skipped} 如实计数。"""
        stats = {"requested": len(items), "embedded": 0, "failed": 0, "skipped": 0}
        todo = []
        for mid, content in items:
            if self.is_fresh(mid, content):
                stats["skipped"] += 1
            else:
                todo.append((mid, content))
        if not todo:
            return stats
        vecs = embed_fn([c for _, c in todo])
        if len(vecs) != len(todo):
            stats["failed"] += len(todo)
            return stats
        for (mid, content), vec in zip(todo, vecs):
            if vec and self.upsert(mid, content, vec):
                stats["embedded"] += 1
            else:
                stats["failed"] += 1
        return stats

    def count(self) -> int:
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {self.table}").fetchone()[0]
