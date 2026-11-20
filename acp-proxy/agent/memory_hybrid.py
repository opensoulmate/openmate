"""SoulMate 记忆检索薄客户端（收敛版）——检索能力唯一正主=OpenSoul。

收敛决定（2026-11-20 用户纠偏「记忆系统应该是opensoul，别搞错了」，迁移优先于
删除）：
- 原 acp-proxy 本地混合检索实现（RRF/语义/关键词融合=opensoul memory_hybrid 的
  跨仓镜像）已**迁移归一**：检索能力只在 opensoul/src/hippo/memory_hybrid.py
  一家（AML 抄作业机制层同居：邻居轮/意图加权/字面守卫·实体·日期通道/时间注解/
  rerank/打包 trace/查询侧过滤）；
- SoulMate 记忆检索统一调 OpenSoul API：POST {base}/api/hippo/ltm/search
  （mode=hybrid，响应含 memory_recall_pack/1 召回包）；写入 write-through 见
  agent/memory_retrieval.py（POST /api/hippo/ltm/add）；
- 本地只留：keyword_relevance 纯词法工具（consolidator 去重打分用，无排序能力）
  + TTL 薄缓存（会话暂存，禁本地排名冒充检索能力）；
- OpenSoul 不可达=如实降级空包（degraded=True + reason 可见），**禁本地平行
  排名兜底**——检索能力归 opensoul 一家。

兼容面：hybrid_retrieve() 保留旧函数名/近似签名（旧 rows/vectors/embed_* 参数
弃用忽略——排名由 OpenSoul 做，参数留位仅为调用方零改动过渡）；新代码直接用
search_opensoul()。
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

logger = logging.getLogger("acp-proxy.memory-hybrid")

DEFAULT_OPENSOUL_URL = "http://127.0.0.1:8090"
SEARCH_PATH = "/api/hippo/ltm/search"
HTTP_TIMEOUT = 8.0
CACHE_TTL = 30.0  # 薄缓存（会话暂存）：同查询 30s 内不重打 OpenSoul

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]+")
_CACHE: dict[str, tuple[float, dict]] = {}


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
    """keyword 覆盖度 ∈ [0,1]（纯词法工具：consolidator 去重打分用；无排序能力）。"""
    q = _tokens(query)
    if not q:
        return 0.0
    d = _tokens(content)
    if not d:
        return 0.0
    return len(q & d) / len(q)


@dataclass
class RecallPack:
    """召回包（memory_recall_pack/1 合同形；真源=OpenSoul pack 响应适配）。"""

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
    intent: str = "default"
    trace: dict = field(default_factory=dict)

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
            "intent": self.intent,
            "trace": self.trace,
        }


def _base_url() -> str:
    return (os.environ.get("SOULMATE_OPENSOUL_URL") or DEFAULT_OPENSOUL_URL).rstrip("/")


def _degraded_pack(query: str, reason: str) -> RecallPack:
    pack = RecallPack(query=query)
    pack.degraded = True
    pack.reason = reason
    pack.channels = {"opensoul": "unavailable"}
    return pack


def search_opensoul(
    query: str,
    *,
    limit: int = 6,
    max_chars: int = 1200,
    memory_type: str = "",
    base_url: str = "",
    timeout: float = HTTP_TIMEOUT,
    use_cache: bool = True,
) -> RecallPack:
    """检索统一走 OpenSoul（mode=hybrid 召回包）。失败=如实降级空包。

    薄缓存：同 (query, limit, max_chars, memory_type, base) 30s 内直接回缓存
    （会话暂存语义，非排名）。任何异常都不冒充本地排名结果。
    """
    q = (query or "").strip()
    if not q:
        return _degraded_pack(query, "empty query")
    base = (base_url or _base_url()).rstrip("/")
    key = "|".join((base, q, str(limit), str(max_chars), memory_type))
    if use_cache:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return _pack_from_dict(hit[1], query)
    payload = {
        "query": q,
        "mode": "hybrid",
        "limit": limit,
        "max_chars": max_chars,
        "memory_types": [memory_type] if memory_type else [],
    }
    try:
        req = urllib.request.Request(
            base + SEARCH_PATH,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        logger.warning("opensoul memory search degraded: %s", exc)
        return _degraded_pack(
            query, f"opensoul unavailable: {type(exc).__name__}: {exc}"
        )
    pack_dict = body.get("pack")
    if not isinstance(pack_dict, dict):
        # pack 缺席（keyword 模式响应）=如实降级，禁本地补排名
        return _degraded_pack(query, "opensoul response missing hybrid pack")
    if use_cache:
        _CACHE[key] = (time.time(), pack_dict)
    return _pack_from_dict(pack_dict, query)


def _pack_from_dict(d: dict, query: str) -> RecallPack:
    pack = RecallPack(query=d.get("query", query))
    pack.included = list(d.get("included", []))
    pack.excluded = list(d.get("excluded", []))
    pack.truncated = bool(d.get("truncated", False))
    pack.budget = dict(d.get("budget", {}))
    pack.channels = dict(d.get("channels", {}))
    pack.degraded = bool(d.get("degraded", False))
    pack.reason = d.get("reason")
    pack.candidates = int(d.get("candidates", 0))
    pack.unranked = int(d.get("unranked", 0))
    pack.intent = d.get("intent", "default")
    pack.trace = dict(d.get("trace", {}))
    return pack


def hybrid_retrieve(query: str, rows=None, **kwargs) -> RecallPack:
    """兼容面（弃用注记）：旧混合检索签名 → 统一走 search_opensoul。

    rows/vectors/embed_query_fn/embed_batch_fn 全部**忽略**——排名能力已迁移
    opensoul（本仓禁养平行记忆系统）；limit/max_chars/memory_type 透传。
    """
    return search_opensoul(
        query,
        limit=kwargs.get("limit", 6),
        max_chars=kwargs.get("max_chars", 1200),
        memory_type=kwargs.get("memory_type", ""),
    )
