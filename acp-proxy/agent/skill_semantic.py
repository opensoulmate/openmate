"""agent/skill_semantic.py — 技能语义层：embedding 供应商链 + 相似度（P0 技能自动命中）

对标 OMH 技能路由器（卷一§4）：触发打分+BM25 之外补语义向量层，任务描述与
技能（名称/描述/触发词/历史任务情况语句）做向量相似度，换说法也能进短名单。

opensoul 向量基建"查实再用"（2026-11-19 实测留档）：
- 正门 POST /api/gland/embed 存在且曾返回 1024 维实向量；但其 provider 注册表
  当前 openai(404)/ollama(配了不存在的 nomic-embed-text) 全部 enabled:false，
  请求 502"All providers failed"——正门保留首选，registry 修好即自动复用；
- 实际可用的同一基建 = ollama /api/embed（opensoul services/embedding.py 的
  ollama provider 同款协议）本地 bge-m3（embedding 1024 维，实测可用）。

供应商链（"No silent downgrade"，卷二 #16，降级恒留痕）：
1. opensoul  （SOULMATE_OPENSOUL_EMBED_URL，默认 http://127.0.0.1:8090/api/gland/embed）
2. ollama    （SOULMATE_OLLAMA_EMBED_URL/MODEL，默认 http://localhost:11434/api/embed + bge-m3）
3. openai    （EMBEDDING_BASE_URL/EMBEDDING_API_KEY/EMBEDDING_MODEL，OpenAI 兼容 /embeddings）
4. hash      （本地确定性字符 n-gram 哈希向量——离线兜底，纯 stdlib 永不失败）

口径纪律：一次批量内向量同源（sticky provider——不同供应商向量空间不同，混算
余弦=假数据；进程内粘住首个成功的供应商，缓存键含供应商+模型）。失败契约对齐
opensoul services/embedding：永不 raise，全链失败→hash 向量+degraded_reason。

缓存：data/skill_embed_cache.json（sha256(tag|text) → 向量），原子写。
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
import urllib.request
from pathlib import Path

logger = logging.getLogger("agent.skill_semantic")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
EMBED_CACHE_PATH = os.environ.get(
    "SOULMATE_SKILL_EMBED_CACHE",
    os.path.join(DATA_DIR, "skill_embed_cache.json"),
)

OPENSOUL_EMBED_URL = os.environ.get(
    "SOULMATE_OPENSOUL_EMBED_URL", "http://127.0.0.1:8090/api/gland/embed"
)
OLLAMA_EMBED_URL = os.environ.get(
    "SOULMATE_OLLAMA_EMBED_URL", "http://localhost:11434/api/embed"
)
OLLAMA_EMBED_MODEL = os.environ.get("SOULMATE_OLLAMA_EMBED_MODEL", "bge-m3")
# 单次 HTTP 尝试超时 + 整批墙钟预算（对齐 opensoul EMBEDDING_* 口径，量级收紧）
EMBED_ATTEMPT_TIMEOUT = 5.0
MAX_BATCH = 32

HASH_DIM = 256


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；任一为空/维度不一/零向量返回 0.0（不猜，防跨口径混算）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / math.sqrt(na * nb)


class EmbedResult:
    """一次批量嵌入的结果：向量 + 实际供应商 + 降级留痕（None=未降级）。"""

    __slots__ = ("vectors", "provider_used", "degraded_reason", "from_cache")

    def __init__(
        self,
        vectors: list[list[float]],
        provider_used: str,
        degraded_reason: str | None = None,
        from_cache: int = 0,
    ):
        self.vectors = vectors
        self.provider_used = provider_used
        self.degraded_reason = degraded_reason
        self.from_cache = from_cache


class SkillEmbedder:
    """供应商链嵌入器：opensoul → ollama → openai 兼容 → 本地哈希。永不 raise。"""

    def __init__(
        self,
        cache_path: str | None = None,
        opensoul_url: str | None = None,
        ollama_url: str | None = None,
        ollama_model: str | None = None,
        openai_base: str | None = None,
        openai_key: str | None = None,
        openai_model: str | None = None,
        timeout: float = EMBED_ATTEMPT_TIMEOUT,
    ):
        self.cache_path = (
            cache_path
            or os.environ.get("SOULMATE_SKILL_EMBED_CACHE")
            or EMBED_CACHE_PATH
        )
        self.opensoul_url = opensoul_url or OPENSOUL_EMBED_URL
        self.ollama_url = ollama_url or OLLAMA_EMBED_URL
        self.ollama_model = ollama_model or OLLAMA_EMBED_MODEL
        self.openai_base = (
            openai_base if openai_base is not None else os.environ.get("EMBEDDING_BASE_URL", "")
        )
        self.openai_key = (
            openai_key if openai_key is not None else os.environ.get("EMBEDDING_API_KEY", "")
        )
        self.openai_model = (
            openai_model
            if openai_model is not None
            else os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
        )
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cache: dict[str, list[float]] | None = None
        # sticky provider：进程内粘住首个成功供应商（不同向量空间禁止混算余弦）
        self._provider_tag: str | None = None
        self.stats = {
            "embed_calls": 0,
            "embed_texts": 0,
            "cache_hits": 0,
            "degrades": 0,
            "failures": 0,
        }

    # ── 缓存 ──────────────────────────────────────────────

    @staticmethod
    def _cache_key(provider_tag: str, text: str) -> str:
        return hashlib.sha256(f"{provider_tag}|{text}".encode("utf-8")).hexdigest()

    def _load_cache(self) -> dict[str, list[float]]:
        if self._cache is None:
            try:
                with open(self.cache_path, encoding="utf-8") as fp:
                    data = json.load(fp)
                self._cache = {
                    k: v for k, v in data.get("vectors", {}).items() if isinstance(v, list)
                }
            except Exception:
                self._cache = {}
        return self._cache

    def _save_cache(self) -> None:
        if self._cache is None:
            return
        try:
            Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
            tmp = f"{self.cache_path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump({"version": 2, "vectors": self._cache}, fp)
            os.replace(tmp, self.cache_path)
        except Exception as e:  # 缓存写失败不影响主链路
            logger.debug("embed cache save failed: %s", e)

    # ── HTTP（每个供应商失败返回 None，不抛） ───────────────

    def _http_post_json(self, url: str, payload: dict, headers: dict) -> dict | None:
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json", **headers},
                method="POST",
            )
            # 本机地址绕过环境代理（urllib 会把 127.0.0.1 也交给 http_proxy
            # 拿到 502——实测踩坑；非本机地址保留环境代理，外网 API 可能需要）
            host = urllib.parse.urlsplit(url).hostname or ""
            if host in ("127.0.0.1", "localhost", "::1"):
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            else:
                opener = urllib.request.build_opener()
            with opener.open(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.debug("embed http failed %s: %s", url, e)
            return None

    @staticmethod
    def _check_vecs(vecs, n: int) -> list[list[float]] | None:
        if isinstance(vecs, list) and len(vecs) == n and all(
            isinstance(v, list) and v for v in vecs
        ):
            return vecs
        return None

    def _embed_opensoul(self, texts: list[str]) -> list[list[float]] | None:
        data = self._http_post_json(self.opensoul_url, {"texts": texts}, {})
        return self._check_vecs(data.get("embeddings") if data else None, len(texts))

    def _embed_ollama(self, texts: list[str]) -> list[list[float]] | None:
        # opensoul services/embedding.py 的 ollama provider 同款协议（POST /api/embed）
        data = self._http_post_json(
            self.ollama_url, {"model": self.ollama_model, "input": texts}, {}
        )
        return self._check_vecs(data.get("embeddings") if data else None, len(texts))

    def _embed_openai(self, texts: list[str]) -> list[list[float]] | None:
        if not self.openai_base:
            return None
        headers = {}
        if self.openai_key:
            headers["Authorization"] = f"Bearer {self.openai_key}"
        data = self._http_post_json(
            f"{self.openai_base.rstrip('/')}/embeddings",
            {"input": texts, "model": self.openai_model},
            headers,
        )
        items = data.get("data") if data else None
        if not isinstance(items, list) or len(items) != len(texts):
            return None
        return self._check_vecs([it.get("embedding") for it in items], len(texts))

    def _embed_hash(self, texts: list[str]) -> list[list[float]]:
        """确定性字符 n-gram 哈希向量（离线兜底）：中文按字 bigram，英文按词。"""
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * HASH_DIM
            t = (text or "").lower()
            grams: list[str] = []
            for i in range(len(t) - 1):
                grams.append(t[i:i + 2])
            grams.extend(t.split())
            for g in grams:
                h = int.from_bytes(
                    hashlib.md5(g.encode("utf-8")).digest()[:4], "big"
                ) % HASH_DIM
                vec[h] += 1.0
            out.append(vec)
        return out

    # ── 供应商链 ──────────────────────────────────────────

    def _online_chain(self) -> list[tuple[str, object]]:
        """(tag, callable) 有序链；sticky 供应商排最前（口径一致优先）。"""
        chain = [
            ("opensoul", self._embed_opensoul),
            (f"ollama:{self.ollama_model}", self._embed_ollama),
            (f"openai:{self.openai_model}", self._embed_openai),
        ]
        if self._provider_tag:
            chain.sort(key=lambda c: 0 if c[0] == self._provider_tag else 1)
        return chain

    # ── 主入口 ────────────────────────────────────────────

    def _chain_embed(self, missing_texts: list[str]) -> tuple[list[list[float]], str, list[str]]:
        """走在线链（sticky 优先）→ 全败 hash 兜底。返回 (向量, 实际tag, tried)。"""
        got: list[list[float]] | None = None
        tried: list[str] = []
        for chain_tag, fn in self._online_chain():
            batch_all: list[list[float]] | None = None
            for chunk_start in range(0, len(missing_texts), MAX_BATCH):
                chunk = missing_texts[chunk_start:chunk_start + MAX_BATCH]
                batch = fn(chunk)
                if batch is None:
                    batch_all = None
                    break
                batch_all = (batch_all or []) + batch
            if batch_all is not None:
                return batch_all, chain_tag, tried
            tried.append(chain_tag)
        return self._embed_hash(missing_texts), "hash", tried

    def embed(self, texts: list[str]) -> EmbedResult:
        """批量嵌入。缓存优先（sticky 口径内）；未命中走供应商链；全链失败→hash。

        口径纪律：不同供应商向量空间不同，禁止混算余弦——cache 只在 sticky
        口径内读写；口径切换时整批重算（宁可重嵌入，不出假相似度）。
        永不 raise。降级语义（#16 No silent downgrade）：provider_used 恒为实际
        出向量的层，degraded_reason 仅在未走首选供应商时非 None。
        """
        texts = [t if isinstance(t, str) else str(t) for t in (texts or [])]
        if not texts:
            return EmbedResult([], "none")
        with self._lock:
            cache = self._load_cache()
        self.stats["embed_calls"] += 1
        self.stats["embed_texts"] += len(texts)

        # 1) sticky 口径内查缓存（无 sticky=首跑：整批走链建立口径）
        tag = self._provider_tag
        vectors: list[list[float] | None] = [None] * len(texts)
        if tag is not None:
            for i, t in enumerate(texts):
                vectors[i] = cache.get(self._cache_key(tag, t))
        missing_idx = [i for i, v in enumerate(vectors) if v is None]
        self.stats["cache_hits"] += len(texts) - len(missing_idx)
        if not missing_idx:
            return EmbedResult(
                [v for v in vectors if v is not None], tag or "none", None,
                from_cache=len(texts),
            )

        # 2) 缺失走链
        missing_texts = [texts[i] for i in missing_idx]
        got, used_tag, tried = self._chain_embed(missing_texts)

        # 3) 口径切换守卫：缓存里的旧口径向量与新口径向量不混算——整批重算
        if tag is not None and used_tag != tag and len(missing_idx) != len(texts):
            missing_texts = list(texts)
            missing_idx = list(range(len(texts)))
            if used_tag == "hash":
                got = self._embed_hash(missing_texts)
            else:
                got, used_tag, tried = self._chain_embed(missing_texts)

        degraded_reason: str | None = None
        if used_tag == "hash":
            self.stats["degrades"] += 1
            degraded_reason = "all_online_failed:" + "+".join(tried)
        elif tried:
            self.stats["degrades"] += 1
            degraded_reason = "fell_back_past:" + "+".join(tried)
        if used_tag != "hash":
            self._provider_tag = used_tag
            with self._lock:
                cache = self._load_cache()
                for i, text in zip(missing_idx, missing_texts):
                    cache[self._cache_key(used_tag, text)] = got[i]
                self._save_cache()
        for j, i in enumerate(missing_idx):
            vectors[i] = got[j]

        return EmbedResult(
            [v for v in vectors if v is not None],
            used_tag,
            degraded_reason,
            from_cache=len(texts) - len(missing_idx),
        )

    def snapshot(self) -> dict:
        snap = dict(self.stats)
        snap["provider_tag"] = self._provider_tag
        return snap


_EMBEDDER: SkillEmbedder | None = None


def get_embedder() -> SkillEmbedder:
    """进程级单例（skill_matcher lazy-init；测试可自行构造隔离实例）。"""
    global _EMBEDDER
    if _EMBEDDER is None:
        _EMBEDDER = SkillEmbedder()
    return _EMBEDDER
