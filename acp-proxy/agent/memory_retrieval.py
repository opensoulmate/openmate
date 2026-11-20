"""
记忆检索引擎 — 借鉴Mem0/Zep/MemGPT的分层记忆系统
核心思想：短期记忆（对话上下文）→ 工作记忆（当前任务）→ 长期记忆（持久化知识）
针对OpenSoul的四层记忆体系设计

P0 gap (SUMMARY.md: "hippo | 三因子检索 ... generative-agents ... | P0")：
检索排序升级为 generative-agents 三因子公式（69-generative-agents-source.md
#1："recency=0.99^i衰减 + importance=poignancy + relevance=cos_sim，三者
normalize到[0,1]后等权相加取top"）。此前 retrieve() 的排序键 relevance_score
与 query 完全无关（query 只做 FTS 候选过滤）——高重要性但答非所问的记忆会
挤掉真正相关的记忆。本模块 relevance 因子用 token 覆盖度做 cos_sim 的确定性
代理（无嵌入模型依赖；CJK 用 bigram，DeerMem 同款做法）。
"""

import logging
import json
import re
import time
import sqlite3
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from enum import Enum

from agent.memory_admission import (
    DECISION_APPROVED,
    DECISION_DEFERRED,
    DECISION_REJECTED,
    evaluate_admission,
)
from agent.memory_vector_index import MemoryVectorIndex, cosine

logger = logging.getLogger("acp-proxy.memory-retrieval")


class MemoryType(str, Enum):
    EPISODIC = "episodic"      # 情景记忆：具体事件
    SEMANTIC = "semantic"      # 语义记忆：事实知识
    PROCEDURAL = "procedural"  # 程序记忆：操作步骤
    WORKING = "working"        # 工作记忆：当前任务上下文


class MemoryImportance(int, Enum):
    TRIVIAL = 1
    LOW = 2
    NORMAL = 3
    HIGH = 4
    CRITICAL = 5


@dataclass
class MemoryEntry:
    memory_id: str
    memory_type: MemoryType
    content: str
    importance: MemoryImportance = MemoryImportance.NORMAL
    session_id: str = ""
    tags: list[str] = field(default_factory=list)
    embedding_text: str = ""  # 用于检索的文本
    created_at: float = field(default_factory=time.time)
    last_accessed: float = field(default_factory=time.time)
    access_count: int = 0
    decay_rate: float = 0.01  # 遗忘速率
    metadata: dict = field(default_factory=dict)

    @property
    def relevance_score(self) -> float:
        """旧启发式综合分（重要性 + 新近度 + 使用频率）。

        注意：与 query 无关（历史命名遗留）。检索排序已升级为三因子公式
        three_factor_score(entry, query)（generative-agents），query 为空时
        本属性作为退化排序键保留。
        """
        age_hours = (time.time() - self.created_at) / 3600
        recency = 1.0 / (1.0 + self.decay_rate * age_hours)
        frequency = min(1.0, self.access_count / 10.0)
        importance = self.importance.value / 5.0

        return 0.4 * importance + 0.3 * recency + 0.3 * frequency


# ── 三因子检索（generative-agents retrieve.py 祖师爷公式，~40行核心） ──────
# 因子1 recency：0.99^i 衰减序列的时间版等价物（沿用本模块既有遗忘曲线，
#       归一到 (0,1]）；因子2 importance：poignancy 1-10 的本模块等价物
#       （MemoryImportance 1-5 / 5）；因子3 relevance：cos_sim 嵌入相似度的
#       确定性代理（token 覆盖度；CJK bigram，DeerMem 同款）。三者归一到
#       [0,1] 后等权相加（generative-agents 等权归一，不加权）。

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def _relevance_tokens(text: str) -> set[str]:
    """检索 token 集合：拉丁/数字词整词小写 + CJK 连续段切 bigram（不足2字取原字）。"""
    tokens: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        seg = m.group(0)
        if "\u4e00" <= seg[0] <= "\u9fff":
            if len(seg) == 1:
                tokens.add(seg)
            else:
                tokens.update(seg[i:i + 2] for i in range(len(seg) - 1))
        else:
            tokens.add(seg.lower())
    return tokens


def query_relevance(query: str, content: str) -> float:
    """relevance 因子 ∈ [0,1]：query token 被 content 覆盖的比例（cos_sim 代理）。"""
    q = _relevance_tokens(query)
    if not q:
        return 0.0
    d = _relevance_tokens(content)
    if not d:
        return 0.0
    return len(q & d) / len(q)


def three_factor_score(entry: MemoryEntry, query: str, *, now: Optional[float] = None) -> float:
    """generative-agents 三因子分：(recency + importance + relevance) / 3，各因子 ∈ [0,1]。"""
    ts = time.time() if now is None else now
    age_hours = max(0.0, (ts - entry.created_at) / 3600)
    recency = 1.0 / (1.0 + entry.decay_rate * age_hours)  # (0,1]
    importance = entry.importance.value / 5.0              # [0.2,1]
    relevance = query_relevance(query, entry.content)      # [0,1]
    return (recency + importance + relevance) / 3.0


def _semantic_boost(sims: dict[str, float], memory_id: str, weight: float = 1.0 / 3.0) -> float:
    """语义因子（三因子公式的第4因子等权形态）：w·cos_sim，cos 钳 [0,1]。

    无向量/通道缺席=0.0——排序退化纯三因子（旧行为字节级保持）。
    """
    sim = sims.get(memory_id)
    if sim is None:
        return 0.0
    return weight * min(1.0, max(0.0, sim))


class MemoryRetrievalEngine:
    """记忆检索引擎"""

    def __init__(self, db_path: str = "", embed_fn=None):
        if not db_path:
            base = Path.home() / ".hermes" / "soulmate" / "memory"
            base.mkdir(parents=True, exist_ok=True)
            db_path = str(base / "memory.db")
        self.db_path = db_path
        # MEM-Semantic：向量索引 + embedding 注入口（缺省=agent.memory_embedding
        # 既有配置口径；测试注入假向量=零网络；未配置=语义通道如实缺席）
        self._vector_index = MemoryVectorIndex(db_path)
        self._embed_fn = embed_fn
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER DEFAULT 3,
                    session_id TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    embedding_text TEXT DEFAULT '',
                    created_at REAL NOT NULL,
                    last_accessed REAL NOT NULL,
                    access_count INTEGER DEFAULT 0,
                    decay_rate REAL DEFAULT 0.01,
                    metadata TEXT DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_type
                ON memories(memory_type, importance)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_session
                ON memories(session_id)
            """)
            # FTS全文搜索
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(memory_id, content, embedding_text, tokenize='unicode61')
            """)
            # MEM-Semantic 加码①：写入决策留痕（OMH memory_admission 可抄件——
            # 保留/拒绝/延后三岔全带书面理由，审计可查可统计）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_write_audit (
                    audit_id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    content_preview TEXT DEFAULT '',
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_mw_audit_decision
                ON memory_write_audit(decision, created_at)
            """)
            conn.commit()

    def store(
        self,
        content: str,
        memory_type: MemoryType = MemoryType.EPISODIC,
        importance: MemoryImportance = MemoryImportance.NORMAL,
        session_id: str = "",
        tags: Optional[list[str]] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[MemoryEntry]:
        """存储记忆（MEM-Semantic：写入决策留痕 + 写入时自动向量化）。

        准入三岔（OMH memory_admission 可抄件，三岔全带书面理由落
        memory_write_audit）：auto_approved 入库照常召回；rejected 不入库
        （空内容/类凭据——记忆库不是密钥库）；deferred_review 入库但**不进
        召回**，待 review_memory 复核（指令形内容禁变成注入指令）。返回
        None=拒绝入库。近重复候选照常入库（去重交 consolidate）但审计盖
        duplicate_of 章。
        """
        dup_of = ""
        if (content or "").strip():
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT memory_id FROM memories WHERE content = ? LIMIT 1",
                    (content,),
                ).fetchone()
                dup_of = row[0] if row else ""
        admission = evaluate_admission(content, duplicate_of=dup_of)
        self._write_admission_audit(
            memory_id=f"gate_{hashlib.sha256(f'{content}:{time.time()}'.encode()).hexdigest()[:12]}",
            decision=admission.decision,
            rule=admission.rule,
            reason=admission.reason,
            content=content,
        )
        if admission.decision == DECISION_REJECTED:
            logger.info(f"[memory-admission] rejected ({admission.rule}): {admission.reason}")
            return None

        memory_id = f"mem_{hashlib.sha256(f'{content}:{time.time()}'.encode()).hexdigest()[:12]}"

        entry_meta = dict(metadata or {})
        entry_meta["admission"] = admission.decision
        entry_meta["admission_reason"] = admission.reason

        entry = MemoryEntry(
            memory_id=memory_id,
            memory_type=memory_type,
            content=content,
            importance=importance,
            session_id=session_id,
            tags=tags or [],
            embedding_text=self._extract_keywords(content),
            metadata=entry_meta,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memories
                   (memory_id, memory_type, content, importance, session_id,
                    tags, embedding_text, created_at, last_accessed,
                    access_count, decay_rate, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (entry.memory_id, entry.memory_type.value, entry.content,
                 entry.importance.value, entry.session_id,
                 json.dumps(entry.tags), entry.embedding_text,
                 entry.created_at, entry.last_accessed,
                 entry.access_count, entry.decay_rate,
                 json.dumps(entry.metadata)),
            )
            # FTS索引
            conn.execute(
                "INSERT INTO memories_fts (memory_id, content, embedding_text) VALUES (?, ?, ?)",
                (entry.memory_id, entry.content, entry.embedding_text),
            )
            conn.commit()

        # MEM-Semantic ②：写入时自动向量化（失败不打断写入，检索期自愈回填）
        self._auto_vectorize(entry.memory_id, content)

        # 收敛版（2026-11-20 用户纠偏）：记忆写入 write-through 统一调 OpenSoul
        # （/api/hippo/ltm/add），本地行=会话暂存/薄缓存。失败不打断主流程（本地
        # 暂存仍可用），warning 可见——该条在 OpenSoul 侧补同步前不可被统一检索召回。
        self._write_through_opensoul(entry)

        logger.debug(f"Stored memory: {memory_id} ({memory_type.value}, importance={importance.value})")
        return entry

    def _write_through_opensoul(self, entry: MemoryEntry) -> bool:
        """记忆写入统一面（SoulMate 记忆读写/检索统一调 OpenSoul API）。"""
        import urllib.error
        import urllib.request

        from agent.memory_hybrid import _base_url

        payload = {
            "content": entry.content,
            "memory_type": entry.memory_type.value,
            "tags": list(entry.tags),
            "metadata": dict(entry.metadata),
            "write_mode": "explicit",  # 引擎写入=显式路径（非自动抽取）
            "dup_policy": "reject",
            "echo_guard": False,  # 引擎写入非回合 digest，不过回声闸
        }
        try:
            req = urllib.request.Request(
                _base_url() + "/api/hippo/ltm/add",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.warning(
                "write-through to opensoul failed (staged locally only, "
                "not recallable via unified search until synced): %s", exc
            )
            return False
        if not body.get("added", False):
            logger.info(
                "opensoul admission declined write-through (%s): %s",
                body.get("outcome", "?"),
                entry.content[:80],
            )
        return bool(body.get("added", False))

    # ── MEM-Semantic：写入审计 + 自动向量化 ──────────────────────────
    def _write_admission_audit(
        self, memory_id: str, decision: str, rule: str, reason: str, content: str
    ) -> None:
        """写入决策留痕（三岔全带书面理由——OMH「处置动词全带 reason」逐字纪律）。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO memory_write_audit
                   (audit_id, memory_id, decision, rule, reason, content_preview, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"mwa_{hashlib.sha256(f'{memory_id}:{time.time()}'.encode()).hexdigest()[:12]}",
                    memory_id,
                    decision,
                    rule,
                    reason,
                    (content or "")[:120],
                    time.time(),
                ),
            )
            conn.commit()

    def _resolve_embed_fn(self):
        """embedding 注入口：测试注入优先；缺省=agent.memory_embedding（EMBEDDING_*
        同 opensoul 配置口径）。未配置=embed_texts 恒空向量（语义通道如实缺席）。"""
        if self._embed_fn is not None:
            return self._embed_fn
        try:
            from agent import memory_embedding

            return memory_embedding.embed_texts
        except Exception as exc:
            logger.warning("memory embedding infra unavailable: %s", exc)
            return None

    def _auto_vectorize(self, memory_id: str, content: str) -> bool:
        """写入时自动向量化（失败不打断写入主流程；空向量=不落索引禁假缓存；
        漏算由 retrieve 期 is_fresh 自愈回填兜底）。"""
        fn = self._resolve_embed_fn()
        if fn is None:
            return False
        try:
            vecs = fn([content])
            vec = vecs[0] if vecs else []
            if vec and self._vector_index.upsert(memory_id, content, vec):
                return True
            logger.debug(f"write-time vectorization degraded (empty vector): {memory_id}")
        except Exception as exc:
            logger.warning(f"write-time vectorization failed, self-heals at recall: {exc}")
        return False

    def _semantic_sims(
        self,
        query: str,
        memory_type: Optional[MemoryType],
        session_id: str,
        min_importance: MemoryImportance,
    ) -> dict[str, float]:
        """语义通道：候选=过滤后全部活跃行（非字面查询全靠这一步进候选），向量
        缺失自愈回填。返回 {memory_id: cosine}；失败/未配置=空 dict（调用方
        按通道缺席降级，禁静默冒充）。"""
        fn = self._resolve_embed_fn()
        if fn is None or not (query or "").strip():
            return {}
        sql = "SELECT memory_id, content, metadata FROM memories WHERE 1=1"
        params: list = []
        if memory_type:
            sql += " AND memory_type = ?"
            params.append(memory_type.value)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " AND importance >= ?"
        params.append(min_importance.value)
        sql += " ORDER BY created_at DESC LIMIT 500"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        rows = [
            r for r in rows
            if json.loads(r.get("metadata") or "{}").get("admission", "auto_approved")
            != "deferred_review"
        ]
        if not rows:
            return {}
        contents = {r["memory_id"]: r["content"] for r in rows}
        vectors = self._vector_index.load(contents=contents)  # 陈旧向量不进排名
        missing = [(mid, c) for mid, c in contents.items() if mid not in vectors]
        if missing:
            try:
                stats = self._vector_index.backfill(missing, fn)
                if stats["embedded"]:
                    vectors = self._vector_index.load(contents=contents)
            except Exception as exc:
                logger.warning(f"recall-time vector backfill failed: {exc}")
        try:
            q_vecs = fn([query])
            q_vec = q_vecs[0] if q_vecs else []
        except Exception as exc:
            logger.warning(f"query embedding failed, semantic channel off: {exc}")
            return {}
        if not q_vec:
            return {}
        sims: dict[str, float] = {}
        for mid, vec in vectors.items():
            try:
                sims[mid] = cosine(q_vec, vec)
            except ValueError:
                continue  # 维度不齐=该行如实不参与语义排名
        return sims

    def retrieve(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        session_id: str = "",
        limit: int = 10,
        min_importance: MemoryImportance = MemoryImportance.TRIVIAL,
    ) -> list[MemoryEntry]:
        """检索记忆"""
        # 关键词搜索
        keywords = self._extract_keywords(query)
        fts_query = " OR ".join(keywords.split()) if keywords else query

        sql = """
            SELECT m.* FROM memories m
            JOIN memories_fts f ON m.memory_id = f.memory_id
            WHERE memories_fts MATCH ?
        """
        params: list = [fts_query]

        if memory_type:
            sql += " AND m.memory_type = ?"
            params.append(memory_type.value)

        if session_id:
            sql += " AND m.session_id = ?"
            params.append(session_id)

        sql += " AND m.importance >= ?"
        params.append(min_importance.value)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(sql, params).fetchall()
            except Exception:
                # FTS查询失败，fallback到LIKE
                rows = conn.execute(
                    """SELECT * FROM memories WHERE content LIKE ? LIMIT ?""",
                    (f"%{query}%", limit * 2),
                ).fetchall()

        # 转换为MemoryEntry并排序
        entries = [self._row_to_entry(row) for row in rows]

        # ── MEM-Semantic ③：语义通道候选并入（「上周聊的X」类非字面查询的
        # 召回来源——全文匹配盲区由语义通道补齐）────────────────────────
        sims = self._semantic_sims(query, memory_type, session_id, min_importance)
        if sims:
            known = {e.memory_id for e in entries}
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                for mid in sims:
                    if mid in known:
                        continue
                    row = conn.execute(
                        "SELECT * FROM memories WHERE memory_id = ?", (mid,)
                    ).fetchone()
                    if row is not None:
                        entries.append(self._row_to_entry(row))

        # fallback（LIKE）路径SQL侧不带type/importance/session过滤——在此统一
        # 补过滤（FTS路径已过滤，重施无害），fail-closed：过滤条件不满足不许混入
        entries = [
            e for e in entries
            if (memory_type is None or e.memory_type == memory_type)
            and (not session_id or e.session_id == session_id)
            and e.importance.value >= min_importance.value
            and e.metadata.get("admission", "auto_approved") != "deferred_review"
        ]

        # 三因子排序（generative-agents：recency+importance+relevance等权归一，
        # relevance=query相关度——修复旧排序与query无关的缺陷）；语义通道在场
        # 时以第4因子等权并入（_semantic_boost；无向量=0.0 退化纯三因子）；
        # query为空时退化为旧启发式relevance_score；memory_id做确定性tie-break
        if query and query.strip():
            entries.sort(key=lambda e: (-(three_factor_score(e, query) + _semantic_boost(sims, e.memory_id)), e.memory_id))
        else:
            entries.sort(key=lambda e: (-e.relevance_score, e.memory_id))

        # 更新访问计数
        for entry in entries[:limit]:
            self._update_access(entry.memory_id)

        return entries[:limit]

    @staticmethod
    def _row_to_entry(row) -> MemoryEntry:
        return MemoryEntry(
            memory_id=row["memory_id"],
            memory_type=MemoryType(row["memory_type"]),
            content=row["content"],
            importance=MemoryImportance(row["importance"]),
            session_id=row["session_id"],
            tags=json.loads(row["tags"]),
            embedding_text=row["embedding_text"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            access_count=row["access_count"],
            decay_rate=row["decay_rate"],
            metadata=json.loads(row["metadata"]),
        )

    def retrieve_pack(
        self,
        query: str,
        memory_type: Optional[MemoryType] = None,
        session_id: str = "",
        limit: int = 6,
        max_chars: int = 1200,
        min_importance: MemoryImportance = MemoryImportance.TRIVIAL,
    ) -> dict:
        """混合检索召回包（收敛版）——检索能力唯一正主=OpenSoul。

        2026-11-20 用户纠偏（「记忆系统应该是opensoul」）：本地 rows/向量/RRF
        排名实现已迁移 opensoul src/hippo/memory_hybrid.py（AML 抄作业机制层
        同居），本口只做 API 透传（agent.memory_hybrid.search_opensoul 薄客户端
        +TTL 缓存）。OpenSoul 不可达=如实降级空包（degraded+reason），禁本地
        平行排名兜底。session_id/min_importance 为兼容留位（OpenSoul 侧过滤
        口径待接），不参与本轮排名。
        """
        from agent.memory_hybrid import search_opensoul

        pack = search_opensoul(
            query,
            limit=limit,
            max_chars=max_chars,
            memory_type=memory_type.value if memory_type else "",
        )
        return pack.to_dict()

    def retrieve_opensoul(self, query: str, limit: int = 3) -> list[MemoryEntry]:
        """SoulMate 回调口（soulmate_agent 召回统一走 OpenSoul）；不可达=空表。"""
        from agent.memory_hybrid import search_opensoul

        pack = search_opensoul(query, limit=limit)
        out: list[MemoryEntry] = []
        for item in pack.included[:limit]:
            out.append(
                MemoryEntry(
                    memory_id=item.get("memory_id", ""),
                    memory_type=MemoryType.EPISODIC,
                    content=item.get("content", ""),
                    importance=MemoryImportance.NORMAL,
                    metadata={
                        "source": "opensoul_ltm",
                        "hybrid_reason": item.get("reason", ""),
                        "recall_pack_degraded": pack.degraded,
                    },
                )
            )
        return out

    def review_memory(self, memory_id: str, verdict: str, reason: str = "") -> tuple[bool, str]:
        """延后记忆复核（OMH 三岔动词全带 reason）：verdict=approve 放行进召回 /
        reject 落审计后遗忘 / defer 维持延后。每次复核落 memory_write_audit。"""
        if verdict not in ("approve", "reject", "defer"):
            return False, f"invalid verdict: {verdict!r} (approve|reject|defer)"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM memories WHERE memory_id = ?", (memory_id,)
            ).fetchone()
        if row is None:
            return False, f"memory not found: {memory_id}"
        entry = self._row_to_entry(row)
        if verdict == "reject":
            self.forget(memory_id)
            self._write_admission_audit(
                memory_id, "review_rejected", "review", reason or "rejected at review", entry.content
            )
            return True, "rejected and removed"
        meta = dict(entry.metadata)
        meta["admission"] = (
            DECISION_APPROVED if verdict == "approve" else DECISION_DEFERRED
        )
        meta["admission_reason"] = reason or f"{verdict} at review"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET metadata = ? WHERE memory_id = ?",
                (json.dumps(meta), memory_id),
            )
            conn.commit()
        self._write_admission_audit(
            memory_id,
            f"review_{verdict}",
            "review",
            reason or f"{verdict} at review",
            entry.content,
        )
        return True, f"{meta['admission']}: {meta['admission_reason']}"

    def get_audit(self, memory_id: str = "", limit: int = 50) -> list[dict]:
        """写入决策审计查询（保留/拒绝/延后全带书面理由，可按记忆过滤）。"""
        sql = "SELECT * FROM memory_write_audit"
        params: list = []
        if memory_id:
            sql += " WHERE memory_id = ?"
            params.append(memory_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_context_memories(
        self,
        session_id: str,
        query: str = "",
        max_tokens: int = 4000,
    ) -> str:
        """获取上下文记忆（用于注入到prompt）"""
        # 获取会话相关的记忆
        memories = self.retrieve(
            query=query or session_id,
            session_id=session_id,
            limit=20,
        )

        # 按token预算组装
        context_parts = []
        total_chars = 0

        for mem in memories:
            text = f"[{mem.memory_type.value}] {mem.content}"
            if total_chars + len(text) > max_tokens * 2:  # rough char-to-token
                break
            context_parts.append(text)
            total_chars += len(text)

        return "\n".join(context_parts)

    def consolidate(self, session_id: str):
        """整合会话记忆 — 去重、合并、降级旧记忆"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # 获取会话所有记忆
            rows = conn.execute(
                "SELECT * FROM memories WHERE session_id = ? ORDER BY importance DESC",
                (session_id,),
            ).fetchall()

            # 简单去重：内容相似度>0.8的只保留重要性高的
            seen_contents: dict[str, str] = {}
            to_delete: list[str] = []

            for row in rows:
                content_hash = hashlib.sha256(row["content"][:100].encode()).hexdigest()
                if content_hash in seen_contents:
                    to_delete.append(row["memory_id"])
                else:
                    seen_contents[content_hash] = row["memory_id"]

            # 删除重复
            for mem_id in to_delete:
                conn.execute("DELETE FROM memories WHERE memory_id = ?", (mem_id,))
                conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (mem_id,))

            conn.commit()

            if to_delete:
                logger.info(f"Consolidated {len(to_delete)} duplicate memories for session {session_id}")

    def forget(self, memory_id: str):
        """遗忘特定记忆"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM memories WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
            conn.commit()

    def _update_access(self, memory_id: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET access_count = access_count + 1, last_accessed = ? WHERE memory_id = ?",
                (time.time(), memory_id),
            )
            conn.commit()

    def _extract_keywords(self, text: str) -> str:
        """提取关键词（简单实现）"""
        import re
        # 移除标点，保留中英文
        words = re.findall(r"[\w\u4e00-\u9fff]+", text)
        # 去除常见停用词
        stopwords = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一", "a", "an", "the", "is", "are", "was", "were"}
        keywords = [w for w in words if w.lower() not in stopwords and len(w) > 1]
        return " ".join(keywords[:20])

    def get_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            by_type = conn.execute(
                "SELECT memory_type, COUNT(*) as cnt FROM memories GROUP BY memory_type"
            ).fetchall()
            avg_importance = conn.execute(
                "SELECT AVG(importance) FROM memories"
            ).fetchone()[0] or 0

        return {
            "total_memories": total,
            "by_type": {r[0]: r[1] for r in by_type},
            "avg_importance": f"{avg_importance:.1f}",
            "db_path": self.db_path,
        }
