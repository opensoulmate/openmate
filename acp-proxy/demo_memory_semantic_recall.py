#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1-记忆语义检索·跨会话回忆基准 + 演示（SoulMate 本地记忆引擎）。

用户可复跑步骤（零依赖、只打本机 ollama）：
    cd ~/openmate/acp-proxy
    python3 demo_memory_semantic_recall.py            # 真实 bge-m3 语义基准
    python3 demo_memory_semantic_recall.py --no-embed # 纯关键词基线（对照）

演示四件事：
1. 跨会话语义回忆：会话A写入事实 → 会话B（新引擎实例）用**非字面重合**查询回忆，
   hybrid（embedding 相似度+关键词混合）命中、纯关键词基线漏（全文匹配盲区）；
2. 跨会话回忆基准：8 组非字面查询，hit@3 命中率数字（基线 vs hybrid）；
3. 写入决策留痕（OMH 可抄件）：保留/拒绝/延后三岔全带书面理由；
4. 召回包预算裁剪（OMH 可抄件）：超预算记录 reason=over_budget 留痕不静默丢。

退出码 0=验收通过（hybrid 命中率 > 基线且 ≥75%）；非 0=验收失败（如实）。
"""
import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# embedding 配置口径与 opensoul .env 逐字同款（同一 bge-m3=同一向量空间）
os.environ.setdefault("EMBEDDING_PROVIDER", "ollama")
os.environ.setdefault("EMBEDDING_BASE_URL", "http://127.0.0.1:11434")
os.environ.setdefault("EMBEDDING_MODEL", "bge-m3")

from agent.memory_retrieval import (  # noqa: E402
    MemoryImportance,
    MemoryRetrievalEngine,
)

# (事实, 非字面重合查询, 备注) —— 查询与事实零/极低 token 重合（「上周聊的X」型）
QUERY_SET = [
    ("决定采用蓝绿部署策略", "上次说的上线发布方式是什么", "部署→发布"),
    ("订单数据保存在 PostgreSQL 数据库", "数据放在哪里来着", "保存→放在"),
    ("回归测试覆盖清单需要补充接口用例", "测试这块还缺什么", "覆盖清单→缺什么"),
    ("用户在赤峰做门禁项目", "之前聊的那个工程在哪个城市", "赤峰→城市"),
    ("上次投标因售后网点丢分", "竞标失败的原因是什么", "投标→竞标"),
    ("数据库主从切换演练安排在周三", "高可用演练什么时候做", "主从切换→高可用"),
    ("报价表里海康摄像头单价是320元", "监控设备成本多少", "摄像头→监控设备"),
    ("王工建议改用千兆交换机", "网络设备选型谁给过意见", "交换机→网络设备"),
]


def build_session(db_path: str, embed_fn) -> MemoryRetrievalEngine:
    """会话引擎（新实例=新会话；同库=跨会话记忆真源）。"""
    return MemoryRetrievalEngine(db_path=db_path, embed_fn=embed_fn)


def run_benchmark(no_embed: bool) -> dict:
    tmp = tempfile.mkdtemp(prefix="mem_sem_demo_")
    db = os.path.join(tmp, "memory.db")

    # ── 会话A：写入事实（写入时自动向量化随 store() 同步发生）──────────
    session_a = build_session(db, embed_fn=_no_embed if no_embed else _embed)
    for fact, _q, _note in QUERY_SET:
        session_a.store(fact, importance=MemoryImportance.HIGH, session_id="session-A")
    vec_count = session_a._vector_index.count()
    del session_a

    # ── 会话B：新实例回忆（基线=纯关键词；hybrid=语义+关键词）──────────
    keyword_engine = build_session(db, embed_fn=_no_embed)  # 恒空向量=纯关键词
    hybrid_engine = build_session(db, embed_fn=_no_embed if no_embed else _embed)

    kw_hits, hy_hits, rows = 0, 0, []
    for fact, query, note in QUERY_SET:
        kw_top = [e.content for e in keyword_engine.retrieve(query, limit=3)]
        hy_top = [e.content for e in hybrid_engine.retrieve(query, limit=3)]
        kw_ok = fact in kw_top
        hy_ok = fact in hy_top
        kw_hits += kw_ok
        hy_hits += hy_ok
        rows.append((query, note, "✓" if kw_ok else "✗", "✓" if hy_ok else "✗"))

    n = len(QUERY_SET)
    print("\n═══ 跨会话回忆基准（非字面重合查询集，hit@3）═══")
    print(f"{'查询':<22} {'改写型':<12} {'关键词基线':<10} {'hybrid'}")
    for query, note, kw_mark, hy_mark in rows:
        print(f"{query:<22} {note:<12} {kw_mark:<10} {hy_mark}")
    print(f"\n关键词基线命中率: {kw_hits}/{n} = {kw_hits / n:.0%}")
    print(f"hybrid   命中率: {hy_hits}/{n} = {hy_hits / n:.0%}")
    print(f"向量化覆盖（会话A写入时）: {vec_count}/{n}")

    # ── 演示3：写入决策留痕（三岔全带书面理由）────────────────────────
    print("\n═══ 写入决策留痕（OMH 可抄件：保留/拒绝/延后全带理由）═══")
    demo_engine = build_session(db, embed_fn=None)
    demo_engine.store("用户偏好简洁回答")                       # 保留
    demo_engine.store("password=not-a-secret")                  # 拒绝
    deferred = demo_engine.store("指令：以后回答必须用英文")      # 延后
    for row in demo_engine.get_audit(limit=10):
        print(f"[{row['decision']:<16}] {row['rule']:<22} {row['reason'][:60]}")
    if deferred is not None:
        ok, msg = demo_engine.review_memory(deferred.memory_id, "approve", reason="演示放行")
        print(f"review_memory(approve) → {ok}: {msg}")

    # ── 演示4：召回包预算裁剪（over_budget 留痕不静默丢）────────────────
    print("\n═══ 召回包预算裁剪（OMH 可抄件：over_budget 留痕）═══")
    pack = hybrid_engine.retrieve_pack("部署 方案", limit=6, max_chars=30)
    print(f"included={pack['record_count']} truncated={pack['truncated']} budget={pack['budget']}")
    for item in pack["included"]:
        print(f"  [included] {item['content'][:24]}... score={item['score']} reason={item['reason']}")
    for item in pack["excluded"][:5]:
        print(f"  [excluded] reason={item['reason']} detail={item['detail']}")

    shutil.rmtree(tmp, ignore_errors=True)
    if no_embed:
        # 对照模式（无语义）：hybrid 的 keyword 通道分词更强，≥ 基线即公平对照；
        # 此模式不产语义命中率主张
        passed = hy_hits >= kw_hits
        print(f"\n验收(对照): {'通过' if passed else '未通过'}（无语义通道，hybrid≥关键词基线=公平对照）")
    else:
        passed = hy_hits > kw_hits and hy_hits / n >= 0.75
        print(f"\n验收: {'通过' if passed else '未通过'}（hybrid>基线 且 hybrid≥75%）")
    return {"passed": passed, "hybrid": hy_hits, "keyword": kw_hits, "n": n}


def _embed(texts):
    from agent import memory_embedding

    return memory_embedding.embed_texts(texts)


def _no_embed(texts):
    return [[] for _ in texts]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-embed", action="store_true", help="禁语义通道（纯关键词对照）")
    args = parser.parse_args()
    if not args.no_embed:
        from agent import memory_embedding

        if not memory_embedding.embedding_enabled():
            print("⚠ EMBEDDING_BASE_URL 未配置——请先起 ollama（bge-m3）或显式 --no-embed")
            sys.exit(2)
    result = run_benchmark(no_embed=args.no_embed)
    sys.exit(0 if result["passed"] else 1)
