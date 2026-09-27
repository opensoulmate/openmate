# -*- coding: utf-8 -*-
"""P0 三因子检索契约测试（generative-agents recency+importance+relevance）

调研来源：69-generative-agents-source.md #1（"recency=0.99^i + importance=
poignancy 1-10 + relevance=cos_sim，三者normalize到[0,1]后等权相加取top"）
+ SUMMARY.md P0-6 "三因子检索（generative-agents，40行可移植）——hippo只有
时间衰减"。修复目标：agent/memory_retrieval.py retrieve() 排序此前与 query
完全无关（高重要性答非所问记忆挤掉真正相关记忆）。

覆盖：
1. relevance 因子（token 覆盖度 + CJK bigram）与三因子等权公式
2. retrieve() 排序修复回归钉：相关但低重要性 > 不相关但高重要性（旧实现相反）
3. recency tie-break / query 为空退化旧启发式
4. fallback（FTS失败→LIKE）路径补齐过滤条件（旧路径丢 type/importance/session 过滤）
5. 接线护栏：三因子函数在 retrieve() 真实排序键 + soulmate_agent 真实消息路径调用点
"""
import inspect
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.memory_retrieval import (  # noqa: E402
    MemoryEntry,
    MemoryImportance,
    MemoryRetrievalEngine,
    MemoryType,
    query_relevance,
    three_factor_score,
)


def _entry(content, importance=MemoryImportance.NORMAL, created_at=None, decay_rate=0.01):
    return MemoryEntry(
        memory_id="mem_" + str(abs(hash(content)) % 10**8),
        memory_type=MemoryType.EPISODIC,
        content=content,
        importance=importance,
        created_at=created_at if created_at is not None else time.time(),
    )


# ── 1. relevance 因子 ──────────────────────────────────────────────

def test_query_relevance_full_coverage():
    assert query_relevance("python deploy test", "python deploy test 全流程") == 1.0


def test_query_relevance_partial_coverage():
    score = query_relevance("python deploy test data", "python deploy 流程")
    assert abs(score - 0.5) < 1e-9  # 只覆盖 python/deploy 两个token


def test_query_relevance_empty_query_or_content_is_zero():
    assert query_relevance("", "anything") == 0.0
    assert query_relevance("query", "") == 0.0
    assert query_relevance("   ", "anything") == 0.0


def test_query_relevance_cjk_bigram():
    # query "部署服务" bigrams={部署,署服,服务}；"服务部署流程"={服务,部署,署流,流程}
    score = query_relevance("部署服务", "服务部署流程")
    assert 0.6 < score < 0.7  # 命中 部署/服务 → 2/3
    assert query_relevance("部署服务", "天气预报") == 0.0


# ── 2. 三因子等权公式 ─────────────────────────────────────────────

def test_three_factor_equal_weight_formula():
    now = time.time()
    e = _entry("python deploy", importance=MemoryImportance.CRITICAL, created_at=now)
    # recency=1/(1+0)=1.0, importance=5/5=1.0, relevance=query_relevance=1.0
    expected = (1.0 + 1.0 + 1.0) / 3.0
    assert abs(three_factor_score(e, "python deploy", now=now) - expected) < 1e-9


def test_three_factor_recency_decay_in_range():
    now = time.time()
    old = _entry("same content here", created_at=now - 3600 * 100)  # 100小时前
    fresh = _entry("same content here", created_at=now)
    s_old = three_factor_score(old, "same content here", now=now)
    s_fresh = three_factor_score(fresh, "same content here", now=now)
    assert 0.0 < s_old < s_fresh <= 1.0  # 新近度因子单调且归一在(0,1]


def test_three_factor_relevance_zero_when_no_match():
    now = time.time()
    e = _entry("天气不错", importance=MemoryImportance.CRITICAL, created_at=now)
    # recency=1.0, importance=1.0, relevance=0.0 → (1+1+0)/3
    assert abs(three_factor_score(e, "python deploy", now=now) - 2.0 / 3.0) < 1e-9


# ── 3. retrieve() 排序修复回归钉 ──────────────────────────────────

def test_relevance_beats_irrelevant_importance(tmp_path):
    """核心回归钉：相关低重要性记忆排在不相关高重要性记忆之前（旧实现相反）。"""
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"))
    rel = eng.store("python 部署 测试 数据 全流程", importance=MemoryImportance.LOW)
    imp = eng.store("测试", importance=MemoryImportance.CRITICAL)

    results = eng.retrieve("python 部署 测试 数据", limit=5)
    contents = [r.content for r in results]
    assert contents[0] == "python 部署 测试 数据 全流程"
    assert "测试" in contents
    # 旧启发式下高重要性记忆分数更高（证明排序确实翻转了，不是恒等变换）
    assert imp.relevance_score > rel.relevance_score


def test_recency_tiebreak_same_relevance_and_importance(tmp_path):
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"))
    now = time.time()
    old = eng.store("alpha topic 关键词", importance=MemoryImportance.NORMAL)
    new = eng.store("alpha topic 关键词", importance=MemoryImportance.NORMAL)
    # 改写 created_at：old 记忆100小时前，new 记忆刚创建
    import sqlite3
    with sqlite3.connect(eng.db_path) as conn:
        conn.execute("UPDATE memories SET created_at = ? WHERE memory_id = ?",
                     (now - 3600 * 100, old.memory_id))
        conn.commit()

    results = eng.retrieve("alpha topic 关键词", limit=5)
    assert [r.memory_id for r in results[:2]] == [new.memory_id, old.memory_id]


def test_empty_query_falls_back_to_legacy_heuristic(tmp_path):
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"))
    low = eng.store("low importance 内容", importance=MemoryImportance.LOW)
    high = eng.store("high importance 内容", importance=MemoryImportance.CRITICAL)
    results = eng.retrieve("", limit=5)
    assert [r.memory_id for r in results[:2]] == [high.memory_id, low.memory_id]


# ── 4. fallback（FTS失败→LIKE）路径过滤补齐 ───────────────────────

def test_fallback_like_path_respects_filters(tmp_path):
    """query="(" → keywords为空→FTS MATCH "(" 报错→LIKE fallback；
    旧fallback丢type/importance/session过滤，此处钉住补齐后的行为。"""
    eng = MemoryRetrievalEngine(db_path=str(tmp_path / "m.db"))
    wanted = eng.store("包含(括号)的episodic记忆", memory_type=MemoryType.EPISODIC,
                       importance=MemoryImportance.HIGH, session_id="s1")
    eng.store("包含(括号)的semantic记忆", memory_type=MemoryType.SEMANTIC,
              importance=MemoryImportance.HIGH, session_id="s1")
    eng.store("包含(括号)的低重要性记忆", memory_type=MemoryType.EPISODIC,
              importance=MemoryImportance.TRIVIAL, session_id="s1")
    eng.store("包含(括号)的他会话记忆", memory_type=MemoryType.EPISODIC,
              importance=MemoryImportance.HIGH, session_id="s2")

    results = eng.retrieve("(", memory_type=MemoryType.EPISODIC, session_id="s1",
                           min_importance=MemoryImportance.LOW, limit=10)
    assert [r.memory_id for r in results] == [wanted.memory_id]


# ── 5. 接线护栏 ───────────────────────────────────────────────────

def test_wiring_three_factor_in_retrieve_sort_key():
    src = inspect.getsource(MemoryRetrievalEngine.retrieve)
    assert "three_factor_score(e, query)" in src  # 真实排序键
    assert "e.relevance_score" in src  # query为空退化路径
    assert "min_importance.value" in src  # fallback过滤补齐在场


def test_wiring_real_message_path_calls_retrieve():
    """soulmate_agent 真实消息路径（prompt记忆注入）调用 retrieve(user_text)。"""
    agent_src = (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text()
    assert "self._memory_engine.retrieve(user_text, limit=3)" in agent_src
