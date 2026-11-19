"""SoulMate 混合检索召回包（MEM-Semantic ③）：语义 top-k + 关键词兜底。

opensoul src/hippo/memory_hybrid.py 的跨仓镜像（同 RRF 常数、同召回包形——
memory_recall_pack/1；两仓不能互相 import，代码镜像+测试双钉）。OMH 可抄件：
included/excluded 每条带 reason、超预算记录 reason=over_budget 留痕不静默丢、
truncated 标志、limit+max_chars 双预算。

诚实边界：语义通道失败/未配置=channels.semantic 如实标缺席 + pack.degraded，
keyword 通道兜底排名（禁静默降级冒充语义全量）；行向量缺失=unranked 如实。
"""

import re
from dataclasses import dataclass, field

RRF_K = 60
W_SEMANTIC = 2.0
W_KEYWORD = 1.0

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")


def _tokens(text: str) -> set[str]:
    out: set[str] = set()
    for m in _TOKEN_RE.finditer(text or ""):
        seg = m.group(0)
        if "\u4e00" <= seg[0] <= "\u9fff":
            if len(seg) == 1:
                out.add(seg)
            else:
                out.update(seg[i : i + 2] for i in range(len(seg) - 1))
        else:
            out.add(seg.lower())
    return out


def keyword_relevance(query: str, content: str) -> float:
    """keyword 通道相关度 ∈ [0,1]：query token 覆盖比例（CJK bigram）。"""
    q = _tokens(query)
    if not q:
        return 0.0
    d = _tokens(content)
    if not d:
        return 0.0
    return len(q & d) / len(q)


@dataclass
class RecallPack:
    """召回包（OMH project_memory_recall_pack/v1 可抄件形，逐字纪律）。"""

    query: str = ""
    included: list[dict] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    truncated: bool = False
    budget: dict = field(default_factory=dict)
    channels: dict = field(default_factory=dict)
    degraded: bool = False
    reason: str | None = None
    candidates: int = 0
    unranked: int = 0

    @property
    def record_count(self) -> int:
        return len(self.included)

    def to_dict(self) -> dict:
        return {
            "pack_version": "memory_recall_pack/1",
            "query": self.query,
            "included": self.included,
            "excluded": self.excluded,
            "truncated": self.truncated,
            "budget": self.budget,
            "channels": self.channels,
            "degraded": self.degraded,
            "reason": self.reason,
            "record_count": self.record_count,
            "candidates": self.candidates,
            "unranked": self.unranked,
        }


def hybrid_retrieve(
    query: str,
    rows: list[dict],
    *,
    vectors: dict[str, list[float]] | None = None,
    embed_query_fn=None,
    embed_batch_fn=None,
    limit: int = 6,
    max_chars: int = 1200,
) -> RecallPack:
    """语义 top-k + 关键词兜底混合检索 → 召回包（RRF 融合，确定性排序）。"""
    from agent.memory_vector_index import cosine

    pack = RecallPack(query=query)
    vectors = dict(vectors or {})
    q = (query or "").strip()

    # ── 语义通道 ──────────────────────────────────────────────
    sem_scores: dict[str, float] = {}
    if not q:
        pack.channels["semantic"] = "skipped_empty_query"
    elif embed_query_fn is None and embed_batch_fn is None and not vectors:
        pack.channels["semantic"] = "unavailable"
        pack.degraded = True
        pack.reason = "semantic channel unavailable: no embedding fn/vectors provided"
    else:
        try:
            q_vec = (embed_query_fn([q]) or [[]])[0] if embed_query_fn else None
        except Exception as exc:
            q_vec = None
            pack.channels["semantic"] = "degraded"
            pack.degraded = True
            pack.reason = f"query embedding failed: {type(exc).__name__}: {exc}"
        if q_vec:
            missing = [
                (r["memory_id"], r["content"])
                for r in rows
                if r["memory_id"] not in vectors
            ]
            if missing and embed_batch_fn is not None:
                try:
                    vecs = embed_batch_fn([c for _, c in missing])
                    for (mid, _c), vec in zip(missing, vecs):
                        if vec:
                            vectors[mid] = vec
                except Exception as exc:
                    pack.reason = pack.reason or (
                        f"row embedding failed: {type(exc).__name__}: {exc}"
                    )
            for r in rows:
                vec = vectors.get(r["memory_id"])
                if vec:
                    sem_scores[r["memory_id"]] = cosine(q_vec, vec)
                else:
                    pack.unranked += 1
            pack.channels.setdefault("semantic", "ok")
        elif "semantic" not in pack.channels:
            pack.channels["semantic"] = "degraded"
            pack.degraded = True
            pack.reason = pack.reason or "query embedding returned empty vector"

    # ── keyword 通道（兜底）────────────────────────────────────
    kw_scores: dict[str, float] = {}
    if q:
        for r in rows:
            kw = keyword_relevance(q, r.get("content", ""))
            if kw > 0:
                kw_scores[r["memory_id"]] = kw
        pack.channels["keyword"] = "ok"
    else:
        pack.channels["keyword"] = "skipped_empty_query"

    # ── RRF 融合 ──────────────────────────────────────────────
    sem_ranked = sorted(sem_scores.items(), key=lambda t: (-t[1], t[0]))
    kw_ranked = sorted(kw_scores.items(), key=lambda t: (-t[1], t[0]))
    fused: dict[str, float] = {}
    for rank, (mid, _s) in enumerate(sem_ranked, start=1):
        fused[mid] = fused.get(mid, 0.0) + W_SEMANTIC / (RRF_K + rank)
    for rank, (mid, _s) in enumerate(kw_ranked, start=1):
        fused[mid] = fused.get(mid, 0.0) + W_KEYWORD / (RRF_K + rank)

    by_id = {r["memory_id"]: r for r in rows}
    ranked_ids = sorted(fused.keys(), key=lambda mid: (-fused[mid], mid))
    pack.candidates = len(ranked_ids)
    considered_unranked = [
        mid
        for mid in (r["memory_id"] for r in rows)
        if mid not in fused and mid not in vectors and q
    ]

    # ── 预算裁剪（OMH：over_budget 留痕不静默丢）──────────────────
    used_chars = 0
    budget_open = True
    for mid in ranked_ids:
        r = by_id[mid]
        score = fused[mid]
        content = r.get("content", "")
        if not budget_open or len(pack.included) >= limit:
            pack.excluded.append(
                {"memory_id": mid, "reason": "over_budget", "detail": f"score={score:.4f}"}
            )
            pack.truncated = True
            continue
        if used_chars + len(content) > max_chars and pack.included:
            pack.excluded.append(
                {
                    "memory_id": mid,
                    "reason": "over_budget",
                    "detail": f"score={score:.4f}, chars={len(content)}",
                }
            )
            pack.truncated = True
            budget_open = False
            continue
        channels_hit = []
        if mid in sem_scores:
            channels_hit.append("semantic")
        if mid in kw_scores:
            channels_hit.append("keyword")
        pack.included.append(
            {
                "memory_id": mid,
                "content": content,
                "score": round(score, 6),
                "semantic": round(sem_scores[mid], 6) if mid in sem_scores else None,
                "keyword": round(kw_scores[mid], 6) if mid in kw_scores else None,
                "reason": f"rrf_fused({'+'.join(channels_hit)})",
            }
        )
        used_chars += len(content)

    for mid in considered_unranked:
        pack.excluded.append(
            {
                "memory_id": mid,
                "reason": "unranked_no_embedding",
                "detail": "row vector missing/failed; keyword channel had no hit",
            }
        )

    pack.budget = {"max_records": limit, "max_chars": max_chars, "used_chars": used_chars}
    return pack
