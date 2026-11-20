# -*- coding: utf-8 -*-
"""MEM-Semantic SoulMate 本地记忆语义检索契约测试。

覆盖：
1. 非字面重合查询语义命中（「上线发布方式」↔「蓝绿部署策略」零 token 重合）——
   本 P1 核心回归钉（全文匹配盲区）
2. 跨实例（新会话）语义回忆：同库新引擎实例=新会话，语义查询命中旧事实
3. 语义通道缺席（EMBEDDING 未配置）=旧行为字节级保持（三因子排序零扰动）
4. retrieve() 接线护栏：语义 boost 在真实排序键、旧字符串契约在场
5. retrieve_pack 召回包：over_budget 留痕 + truncated + 双预算如实
6. 写入时自动向量化 + 召回期自愈回填
"""
import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolate_embedding_env(monkeypatch):
    """测试面钉死语义通道环境（防生产 EMBEDDING_* 泄进测试打真模型/真网络）：
    需要语义行为的用例一律显式注入 fake_embed（NEST_DB 隔离同纪律）。"""
    monkeypatch.delenv("EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

from agent.memory_hybrid import keyword_relevance  # noqa: E402
from agent.memory_retrieval import (  # noqa: E402
    MemoryImportance,
    MemoryRetrievalEngine,
    _semantic_boost,
)

# 概念向量表：同概念不同词=同向量（确定性假 embedding，零网络）
CONCEPTS = {
    "部署": [1.0, 0.0, 0.0],
    "发布": [1.0, 0.0, 0.0],
    "上线": [1.0, 0.0, 0.0],
    "保存": [0.0, 1.0, 0.0],
    "数据": [0.0, 1.0, 0.0],
    "测试": [0.0, 0.0, 1.0],
    "验收": [0.0, 0.0, 1.0],
}


def fake_embed(texts):
    out = []
    for t in texts:
        v = [0.0, 0.0, 0.0]
        hit = False
        for word, vec in CONCEPTS.items():
            if word in t:
                v = [a + b for a, b in zip(v, vec)]
                hit = True
        out.append(v if hit else [0.1, 0.1, 0.1])
    return out


# ── 1/2. 非字面查询语义命中 + 跨实例回忆 ───────────────────────────

def test_cross_instance_semantic_recall_non_literal(tmp_path):
    """核心回归钉：新会话（新实例）里非字面重合查询语义命中旧事实。"""
    db = str(tmp_path / "m.db")
    session_a = MemoryRetrievalEngine(db_path=db, embed_fn=fake_embed)
    session_a.store("决定采用蓝绿部署策略", importance=MemoryImportance.HIGH)
    session_a.store("回归测试覆盖清单需要补充", importance=MemoryImportance.HIGH)
    del session_a  # 会话A结束

    session_b = MemoryRetrievalEngine(db_path=db, embed_fn=fake_embed)  # 新会话
    query = "上次说的上线发布方式是什么"
    # 字面通道盲区自证：零 token 重合
    assert keyword_relevance(query, "决定采用蓝绿部署策略") == 0.0
    assert session_b.retrieve(query, limit=1)[0].content == "决定采用蓝绿部署策略"


def test_semantic_boost_orders_correctly(tmp_path):
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"), embed_fn=fake_embed)
    eng.store("决定采用蓝绿部署策略", importance=MemoryImportance.HIGH)
    eng.store("明天下午开评审会", importance=MemoryImportance.HIGH)
    results = eng.retrieve("上次说的上线发布方式是什么", limit=2)
    assert results[0].content == "决定采用蓝绿部署策略"


def test_semantic_boost_pure_function():
    assert _semantic_boost({}, "m1") == 0.0  # 无向量=0.0 退化三因子
    assert abs(_semantic_boost({"m1": 1.0}, "m1") - 1.0 / 3.0) < 1e-9
    assert _semantic_boost({"m1": -0.5}, "m1") == 0.0  # 负相似度钳 0


# ── 3. 语义通道缺席=旧行为字节级保持 ──────────────────────────────

def test_disabled_semantic_keeps_legacy_ordering(tmp_path):
    """embed_fn=None 且 EMBEDDING 未配置=语义通道缺席，排序=纯三因子（旧行为）。"""
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"))
    rel = eng.store("python 部署 测试 数据 全流程", importance=MemoryImportance.LOW)
    imp = eng.store("测试", importance=MemoryImportance.CRITICAL)
    assert rel is not None and imp is not None
    results = eng.retrieve("python 部署 测试 数据", limit=5)
    assert results[0].content == "python 部署 测试 数据 全流程"  # 旧回归钉不变
    assert imp.relevance_score > rel.relevance_score


# ── 4. 接线护栏 ───────────────────────────────────────────────────

def test_wiring_semantic_boost_in_retrieve_sort_key():
    src = inspect.getsource(MemoryRetrievalEngine.retrieve)
    assert "three_factor_score(e, query)" in src  # 旧契约字符串在场
    assert "_semantic_boost(sims, e.memory_id)" in src  # 语义因子真实接线
    assert "e.relevance_score" in src
    assert "min_importance.value" in src


# ── 5. 召回包=OpenSoul 薄客户端契约（收敛版 2026-11-20：检索能力唯一正主=
# opensoul；本地混合排名实现已迁移 opensoul tests/test_memory_hybrid.py，
# 本节只钉客户端契约：委托形参/适配/降级诚实/禁本地排名兜底）──────────

class _FakeResp:
    def __init__(self, body: dict):
        self._data = json.dumps(body).encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def _clear_hybrid_cache():
    import agent.memory_hybrid as mh

    mh._CACHE.clear()
    yield
    mh._CACHE.clear()


def test_retrieve_pack_delegates_and_adapts(tmp_path, monkeypatch, _clear_hybrid_cache):
    import urllib.request

    import agent.memory_hybrid as mh

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["url"] = req.full_url
        return _FakeResp(
            {
                "pack": {
                    "pack_version": "memory_recall_pack/1",
                    "query": captured["payload"]["query"],
                    "included": [
                        {
                            "memory_id": "ltm_x",
                            "content": "决定采用蓝绿部署策略",
                            "score": 0.05,
                            "semantic": 0.9,
                            "keyword": 0.0,
                            "reason": "rrf_fused(semantic+literal)",
                        }
                    ],
                    "excluded": [],
                    "truncated": False,
                    "budget": {"max_records": 6, "max_chars": 1200, "used_chars": 10},
                    "channels": {"semantic": "ok"},
                    "degraded": False,
                    "reason": None,
                    "record_count": 1,
                    "candidates": 1,
                    "unranked": 0,
                    "intent": "default",
                    "trace": {"rerank": "off"},
                },
                "results": [],
                "count": 1,
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"), embed_fn=fake_embed)
    pack = eng.retrieve_pack("上次说的上线发布方式是什么", limit=3)
    assert captured["url"].endswith("/api/hippo/ltm/search")
    assert captured["payload"]["mode"] == "hybrid"  # 召回包=OpenSoul hybrid 真源
    assert pack["pack_version"] == "memory_recall_pack/1"
    assert pack["included"][0]["content"] == "决定采用蓝绿部署策略"
    assert pack["degraded"] is False
    assert pack["trace"]["rerank"] == "off"  # OpenSoul pack 原样透传


def test_retrieve_pack_degraded_honest_when_opensoul_down(
    tmp_path, monkeypatch, _clear_hybrid_cache
):
    """OpenSoul 不可达=如实降级空包；禁本地平行排名兜底（收敛铁律）。"""
    import urllib.error
    import urllib.request

    def boom(req, timeout=None):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"), embed_fn=fake_embed)
    eng.store("决定采用蓝绿部署策略", importance=MemoryImportance.HIGH)  # 本地暂存在
    pack = eng.retrieve_pack("上次说的上线发布方式是什么", limit=3)
    assert pack["degraded"] is True
    assert "opensoul unavailable" in (pack["reason"] or "")
    assert pack["included"] == []  # 不冒充本地排名结果
    assert pack["channels"] == {"opensoul": "unavailable"}


def test_hybrid_retrieve_compat_delegates_ignoring_rows(monkeypatch, _clear_hybrid_cache):
    import urllib.request

    from agent.memory_hybrid import hybrid_retrieve

    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["payload"] = json.loads(req.data.decode("utf-8"))
        return _FakeResp({"pack": {"pack_version": "memory_recall_pack/1", "included": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    pack = hybrid_retrieve(
        "部署流程",
        [{"memory_id": "local1", "content": "本地行被忽略"}],  # rows 弃用忽略
        vectors={"local1": [1.0]},
        embed_query_fn=fake_embed,
        limit=4,
        max_chars=100,
    )
    assert seen["payload"]["limit"] == 4
    assert seen["payload"]["max_chars"] == 100
    assert pack.degraded is False


def test_opensoul_cache_short_circuits_repeat(monkeypatch, _clear_hybrid_cache):
    import urllib.request

    import agent.memory_hybrid as mh

    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        return _FakeResp({"pack": {"pack_version": "memory_recall_pack/1", "included": []}})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    mh.search_opensoul("缓存测试查询甲", limit=3)
    mh.search_opensoul("缓存测试查询甲", limit=3)
    assert calls["n"] == 1  # 薄缓存（会话暂存）：30s 内同查询不重打


# ── 6. 写入时自动向量化 + 自愈 ─────────────────────────────────────

def test_store_auto_vectorizes_and_self_heals(tmp_path):
    calls = {"n": 0}

    def flaky(texts):
        calls["n"] += 1
        if calls["n"] == 1:
            return [[] for _ in texts]  # 写入期失败（空向量=不落索引）
        return fake_embed(texts)

    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"), embed_fn=flaky)
    mem = eng.store("决定采用蓝绿部署策略", importance=MemoryImportance.HIGH)
    assert mem is not None
    assert eng._vector_index.count() == 0  # 写入期空向量不落索引
    results = eng.retrieve("上次说的上线发布方式是什么", limit=1)
    assert results[0].content == "决定采用蓝绿部署策略"  # 检索期自愈
    assert eng._vector_index.count() == 1


def test_store_auto_vectorizes_on_success(tmp_path):
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"), embed_fn=fake_embed)
    mem = eng.store("决定采用蓝绿部署策略")
    assert mem is not None
    assert eng._vector_index.count() == 1
    assert eng._vector_index.is_fresh(mem.memory_id, "决定采用蓝绿部署策略")
