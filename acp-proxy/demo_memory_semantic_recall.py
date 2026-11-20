#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SoulMate 记忆基准·收敛版（2026-11-20 用户纠偏「记忆系统应该是opensoul」）。

基准真身已**迁移** OpenSoul（记忆系统唯一正主，检索能力归 opensoul 一家）：
    cd ~/opensoul
    PYTHONPATH=. .venv/bin/python scripts/e2e_memory_semantic_recall.py

那是权威基准面：22 组扩集查询（8 非字面回归钉 + 5 时间类 + 5 实体类 +
4 别名类）hit@3 / MRR@3 抄前抄后对比 + 召回包预算裁剪 + 写入审计演示。

本脚本保留为 SoulMate 薄客户端冒烟（迁移优先于删除）：
    python3 demo_memory_semantic_recall.py --smoke   # 走统一 API 打 OpenSoul
不可达=SKIP 退出 0（如实跳过，不产本地排名冒充——本地混合排名实现已随
镜像收敛迁移 opensoul/src/hippo/memory_hybrid.py）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

SMOKE_QUERIES = [
    "上次说的上线发布方式是什么",
    "监控设备成本多少",
    "王工建议改用千兆交换机",
]


def run_smoke() -> bool:
    from agent.memory_hybrid import search_opensoul

    print("═══ SoulMate 薄客户端冒烟（检索统一走 OpenSoul /api/hippo/ltm/search）═══")
    ok_any = False
    for q in SMOKE_QUERIES:
        pack = search_opensoul(q, limit=3, use_cache=False)
        if pack.degraded:
            print(f"[SKIP] {q} → {pack.reason}")
            continue
        ok_any = True
        print(f"[OK] {q} → {pack.record_count} 条（intent={pack.intent}）")
        for item in pack.included[:3]:
            print(f"     - {item['content'][:40]}... reason={item['reason']}")
    if not ok_any:
        print("\nSKIP：OpenSoul 服务不可达——权威基准请跑 opensoul "
              "scripts/e2e_memory_semantic_recall.py（如实跳过，不冒充本地结果）")
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", help="打 OpenSoul 薄客户端冒烟")
    args = parser.parse_args()
    if args.smoke:
        sys.exit(0 if run_smoke() else 1)
    print(__doc__)
    sys.exit(0)
