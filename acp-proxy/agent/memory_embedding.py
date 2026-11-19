"""SoulMate 记忆语义检索·embedding 客户端（MEM-Semantic，全 Python 标准库+httpx）。

复用 opensoul 既有 embedding 基建的**配置口径**（src/services/embedding.py 的
EMBEDDING_* 环境变量逐字同款）：provider=openai（OpenAI 兼容 /embeddings）或
ollama（POST /api/embed）；同一模型（如 bge-m3）→ 同一向量空间——SoulMate 本地
记忆与 opensoul LTM 的向量可互比。

诚实边界（opensoul get_embeddings_batch 失败契约逐字同族）：
- 永不 raise、永不挂死——超时/异常/未配置一律降级为空向量 + 可见日志；
- 空向量=失败（调用方跳过向量索引写入，关键词通道兜底）；
- 未配置（EMBEDDING_BASE_URL 缺省）=embedding_enabled() False，整条语义通道
  如实缺席——测试面零网络确定性（缺省即关）。
"""

import logging
import os

logger = logging.getLogger("acp-proxy.memory-embedding")

SUPPORTED_PROVIDERS = ("openai", "ollama")
ATTEMPT_TIMEOUT = 15.0
MAX_RETRIES = 2
RETRY_BASE_DELAY = 0.5


def _config() -> dict:
    return {
        "provider": (os.environ.get("EMBEDDING_PROVIDER") or "openai").strip().lower(),
        "base_url": (os.environ.get("EMBEDDING_BASE_URL") or "").strip(),
        "model": (os.environ.get("EMBEDDING_MODEL") or "").strip(),
        "api_key": (os.environ.get("EMBEDDING_API_KEY") or "").strip(),
    }


def embedding_enabled() -> bool:
    """语义通道可用性：EMBEDDING_BASE_URL 在场且 provider 合法才开（缺省即关）。"""
    cfg = _config()
    return bool(cfg["base_url"]) and cfg["provider"] in SUPPORTED_PROVIDERS


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量向量化（失败契约：永不 raise、空向量降级 + 可见日志）。"""
    if not texts:
        return []
    if not embedding_enabled():
        logger.debug("memory embedding disabled (EMBEDDING_BASE_URL unset)")
        return [[] for _ in texts]
    cfg = _config()
    if cfg["provider"] not in SUPPORTED_PROVIDERS:
        logger.warning(
            "memory embedding degraded: unsupported provider %r", cfg["provider"]
        )
        return [[] for _ in texts]
    try:
        import httpx
    except Exception as exc:  # httpx 缺席=如实降级
        logger.warning("memory embedding degraded: httpx unavailable (%s)", exc)
        return [[] for _ in texts]

    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            with httpx.Client(timeout=ATTEMPT_TIMEOUT) as client:
                if cfg["provider"] == "ollama":
                    resp = client.post(
                        f"{cfg['base_url'].rstrip('/')}/api/embed",
                        json={"model": cfg["model"], "input": list(texts)},
                    )
                    resp.raise_for_status()
                    embeddings = resp.json().get("embeddings")
                    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
                        raise RuntimeError(
                            f"ollama /api/embed unexpected response: "
                            f"{type(embeddings).__name__} for {len(texts)} texts"
                        )
                    return [list(v) for v in embeddings]
                # openai 兼容 /embeddings
                headers = {}
                if cfg["api_key"]:
                    headers["Authorization"] = f"Bearer {cfg['api_key']}"
                payload: dict = {"input": list(texts), "model": cfg["model"]}
                resp = client.post(
                    f"{cfg['base_url'].rstrip('/')}/embeddings",
                    headers=headers,
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()["data"]
                return [
                    list(item["embedding"])
                    for item in sorted(data, key=lambda x: x["index"])
                ]
        except Exception as exc:  # httpx.HTTPStatusError/RequestError/形状不符
            last_exc = exc
            logger.warning(
                "memory embedding attempt %d/%d failed: %s",
                attempt + 1,
                MAX_RETRIES,
                exc,
            )
    logger.warning(
        "memory embedding degraded to empty vectors (%d texts): %s",
        len(texts),
        last_exc,
    )
    return [[] for _ in texts]


def embed_text(text: str) -> list[float]:
    """单条向量化（空向量=失败，调用方归口语义通道 degraded）。"""
    vecs = embed_texts([text])
    return vecs[0] if vecs else []
