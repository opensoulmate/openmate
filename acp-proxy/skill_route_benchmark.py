#!/usr/bin/env python3
"""skill_route_benchmark.py — P0 技能自动命中率基准（对标 OMH 路由器质量门）

在黄金任务集上量化"同类任务第二次自动命中"能力，对比三套系统：
- S0 线上现状：触发词 gate（改造前 skill_loader._trigger_gate 同语义复刻）；
- S1 混合排序无反馈闭环：触发+BM25+语义打分，但不记账（空账本）；
- S2 全链路（本次交付）：混合排序 + 证据放行 + 命中反馈闭环。

黄金集口径（对齐 OMH tests/test_routing_quality.py + ROUTING_PRECISION_CASES）：
- 8 个任务类 × run1（学技能+记账）+ 3 条 run2 换说法（24 条）；
- 8 条负对照=普通问题（OMH"普通问题不许被误路由"同口径）；
- 质量门：run2 自动命中 100%（黄金集基线）+ 负对照误放行 0。

对比口径声明：OMH 未公开命中率数字（其质量门为黄金集 100% 基线+负对照零误
路由），本基准同口径达成即 ≥OMH 同类水平；top-1/top-3/MRR 为 OMH 未公开的
额外量化细分。语义层用 opensoul /api/gland/embed（查实可用，2026-11-19），
不可达时 hash 兜底并如实记 degraded（No silent downgrade）。

用法（可复跑）：
    cd ~/openmate/acp-proxy && /usr/bin/python3 skill_route_benchmark.py
产物：data/skill_route_benchmark.json（含逐查询明细）+ stdout 对比表。
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 沙箱：全部运行时数据隔离到 data/skill_route_benchmark/（导入前设 env）
SANDBOX = ROOT / "data" / "skill_route_benchmark"
os.environ.setdefault("SOULMATE_SKILL_LEDGER", str(SANDBOX / "ledger.json"))
os.environ.setdefault("SOULMATE_SKILL_LOAD_RECEIPTS", str(SANDBOX / "receipts.jsonl"))
os.environ.setdefault("SOULMATE_SKILL_EMBED_CACHE", str(SANDBOX / "embed_cache.json"))

from agent.skill_feedback import SkillFeedbackLedger  # noqa: E402
from agent.skill_loader import SkillLoader  # noqa: E402
from agent.skill_matcher import SkillMatcher  # noqa: E402
from agent.skill_semantic import SkillEmbedder  # noqa: E402
from skill_manager import SkillManager, is_injectable_trigger  # noqa: E402

# ── 黄金任务集 ──────────────────────────────────────────────
# run1 → try_learn_skill 学技能 + 命中记账；run2…4 = 同类任务换说法（计分）。
GOLDEN = [
    {
        "class": "发票报销",
        "run1": "帮我把这个季度的发票报销单整理一下，填好提交给财务",
        "run2": [
            "请将本季度发票整理成报销单并交给财务部门",
            "把三季度那些发票做成报销单，我要报给财务",
            "财务要报销单，帮我从这些发票里整理出来",
        ],
    },
    {
        "class": "Excel表格处理",
        "run1": "帮我把销售数据整理成Excel表格并按月统计总额",
        "run2": [
            "请把这些销售数据做成excel表，每月一个汇总",
            "把销售数字整理到电子表格里算月度合计",
            "我需要一份按月汇总的销售excel表",
        ],
    },
    {
        "class": "PDF文档解析",
        "run1": "帮我解析这份PDF合同的内容并总结关键条款",
        "run2": [
            "请提取这份pdf文件里的文字并概括要点",
            "把这份PDF文档读出来，总结一下里面的重要条款",
            "分析下这份PDF里的合同条款都写了什么",
        ],
    },
    {
        "class": "服务器日志排查",
        "run1": "帮我查一下这台服务器日志里的报错原因",
        "run2": [
            "服务器又出错了，看看日志是什么问题",
            "帮我分析这台机器的日志错误信息",
            "查查服务器上日志里的error是怎么回事",
        ],
    },
    {
        "class": "网页信息监控",
        "run1": "帮我监控这个商品页面的价格变化",
        "run2": [
            "盯一下这个商品网页的价格，变了告诉我",
            "帮我盯着这个页面的价格波动情况",
            "监控这个商品页什么时候降价",
        ],
    },
    {
        "class": "图片压缩",
        "run1": "帮我把这批图片压缩到200KB以内",
        "run2": [
            "把这些照片压小一点，别超过200KB",
            "图片太大了，帮我压缩一下",
            "把这批图片压到200KB以下再给我",
        ],
    },
    {
        "class": "数据备份",
        "run1": "帮我把数据库每天凌晨备份到对象存储",
        "run2": [
            "给这个数据库配置每日自动备份",
            "每天半夜把数据库备份到云端存储",
            "帮我设置数据库的定时备份任务",
        ],
    },
    {
        "class": "邮件发送",
        "run1": "帮我把这份周报邮件发给项目组所有人",
        "run2": [
            "把周报用邮件发给项目组成员",
            "帮我群发这封周报给组里同事",
            "把这份周报邮件发出去给项目组",
        ],
    },
]

# 负对照：普通问题（OMH ROUTING_PRECISION_CASES 同口径——不许被误路由）
NEGATIVES = [
    "今天天气怎么样，帮我查一下北京的天气情况",
    "帮我写一段生日祝福语送给朋友",
    "现在几点了，今天是星期几",
    "推荐几本好看的科幻小说",
    "帮我把这句话翻译成英文：今天开会取消了",
    "讲个笑话放松一下吧",
    "Python的列表和元组有什么区别",
    "帮我取一个好听的英文名字",
]

# 每类任务的真实工具轮廓（auto-learned 技能名=工具链；跨类同链名是常态，
# 技能可见性由 list_skills 的 (name, description) 去重语义保障）
FAKE_RESPONSE = "已处理完成：\n1. 汇总了数据\n2. 生成结果\n3. 完成交付"

TOOL_PROFILES = {
    "发票报销": ["read_file", "image_tools", "write_file", "submit"],
    "Excel表格处理": ["read_file", "terminal", "write_file"],
    "PDF文档解析": ["read_file", "docgen_tools", "write_file"],
    "服务器日志排查": ["terminal", "read_file", "search_files"],
    "网页信息监控": ["browser_exec", "terminal", "web_extract"],
    "图片压缩": ["image_compress", "terminal", "write_file"],
    "数据备份": ["terminal", "write_file", "guard_tools"],
    "邮件发送": ["read_file", "media_channel", "terminal"],
}


def tool_calls_for(task_class: str) -> list[dict]:
    return [
        {"function": {"name": name, "arguments": {"command": name, "path": "x"}}}
        for name in TOOL_PROFILES[task_class]
    ]


def legacy_admit(query: str, mgr: SkillManager) -> list[str]:
    """S0 线上现状复刻：search_skills + 触发词子串 gate（改造前语义）。"""
    if len(query) <= 10:
        return []
    t_lower = query.lower()
    hits = []
    for s in mgr.search_skills(query, limit=3):
        reasons = [
            t for t in s.get("triggers", [])
            if t and t.lower() in t_lower and is_injectable_trigger(t)
        ]
        if reasons:
            hits.append(str(s.get("id", "")))
    return hits


def main() -> int:
    t0 = time.time()
    SANDBOX.mkdir(parents=True, exist_ok=True)
    mgr = SkillManager(
        skills_dir=str(SANDBOX / "skills"), standard_dirs=[], saved_dir=str(SANDBOX / "saved")
    )
    ledger = SkillFeedbackLedger()
    embedder = SkillEmbedder()  # opensoul 首选，降级留痕
    matcher = SkillMatcher(skill_manager=mgr, ledger=ledger, embedder=embedder)
    loader = SkillLoader(skill_manager=mgr, feedback=ledger, matcher=matcher)

    # ── run1：8 个任务类学技能 + 命中记账（真实管线）─────────
    skill_by_class: dict[str, str] = {}
    for g in GOLDEN:
        learned = asyncio.run(
            mgr.try_learn_skill(
                user_text=g["run1"],
                assistant_response=FAKE_RESPONSE,
                tool_calls=tool_calls_for(g["class"]),
            )
        )
        if not learned:
            print(f"[FATAL] run1 未学到技能: {g['class']}")
            return 1
        ledger.record_hit(learned["id"], g["run1"], outcome="useful",
                          skill_name=learned["name"])
        skill_by_class[g["class"]] = learned["id"]

    # ── run2 计分：S0 / S1（空账本对照）/ S2（全链路）────────
    empty_ledger = SkillFeedbackLedger(path=str(SANDBOX / "empty_ledger.json"))
    matcher_no_fb = SkillMatcher(
        skill_manager=mgr, ledger=empty_ledger, embedder=embedder, top_k=3)
    loader_no_fb = SkillLoader(skill_manager=mgr, feedback=empty_ledger, matcher=matcher_no_fb)

    rows = []
    s0_hit = s1_top3 = s1_admit = s2_hit = s2_top1 = 0
    total = 0
    rr_sum = 0.0
    paths: dict[str, int] = {}
    for g in GOLDEN:
        target = skill_by_class[g["class"]]
        for q in g["run2"]:
            total += 1
            # S0
            s0_ok = target in legacy_admit(q, mgr)
            s0_hit += int(s0_ok)
            # S1：混合排序（空账本→无证据→放行面与S0同，测排序质量）
            ranked_no_fb = matcher_no_fb.rank(q)
            rank_ids = [r.get("id") for r in ranked_no_fb]
            top3_ok = target in rank_ids[:3]
            s1_top3 += int(top3_ok)
            s1_admit += int(any(h.get("id") == target for h in loader_no_fb.match_for_task(q)))
            # S2：全链路
            ranked = matcher.rank(q)
            rank_ids2 = [r.get("id") for r in ranked]
            hits = matcher.admit(q, ranked)
            hit_ids = [h.get("id") for h in hits]
            s2_ok = target in hit_ids
            s2_hit += int(s2_ok)
            s2_top1 += int(bool(hit_ids) and hit_ids[0] == target)
            if target in rank_ids2:
                rr_sum += 1.0 / (rank_ids2.index(target) + 1)
            reason = next(
                (h.get("match_reason", "") for h in hits if h.get("id") == target), "")
            if reason:
                paths[reason.split(":")[0]] = paths.get(reason.split(":")[0], 0) + 1
            rows.append({
                "class": g["class"], "query": q,
                "s0_legacy_hit": s0_ok, "s1_top3": top3_ok, "s2_admitted": s2_ok,
                "s2_reason": reason, "s2_top1": bool(hit_ids) and hit_ids[0] == target,
            })

    # ── 负对照（S2 全链路误放行必须 0）──────────────────────
    neg_rows = []
    misroutes = 0
    for q in NEGATIVES:
        hits = loader.match_for_task(q)
        mis = bool(hits)
        misroutes += int(mis)
        neg_rows.append({"query": q, "misrouted": mis,
                         "reasons": [h.get("match_reason") for h in hits]})

    embed_snap = embedder.snapshot()
    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "elapsed_s": round(time.time() - t0, 2),
        "comparison_scope": (
            "OMH未公开命中率数字；其质量门=黄金集100%基线+负对照零误路由"
            "（tests/test_routing_quality.py + ROUTING_PRECISION_CASES）。"
            "本基准同口径达成即≥OMH同类水平；top-1/top-3/MRR为额外量化细分。"
        ),
        "golden_size": total,
        "negative_size": len(NEGATIVES),
        "S0_legacy_trigger_gate": {"admit_rate": round(s0_hit / total, 4)},
        "S1_hybrid_rank_no_feedback": {
            "top3_recall": round(s1_top3 / total, 4),
            "admit_rate": round(s1_admit / total, 4),
        },
        "S2_full_loop": {
            "admit_rate": round(s2_hit / total, 4),
            "top1_rate": round(s2_top1 / total, 4),
            "top3_recall": round(s1_top3 / total, 4),
            "mrr": round(rr_sum / total, 4),
            "admit_paths": paths,
        },
        "negative_misroute_rate": round(misroutes / len(NEGATIVES), 4),
        "quality_gate": {
            "golden_100pct": s2_hit == total,
            "negative_zero_misroute": misroutes == 0,
        },
        "semantic_provider": {
            "embed_calls": embed_snap.get("embed_calls"),
            "cache_hits": embed_snap.get("cache_hits"),
            "degrades": embed_snap.get("degrades"),
        },
        "rows": rows,
        "negatives": neg_rows,
    }
    out_path = SANDBOX / "skill_route_benchmark.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    # ── stdout 对比表 ─────────────────────────────────────
    print("\n=== P0 技能自动命中率基准（黄金集 %d 条 + 负对照 %d 条）==="
          % (total, len(NEGATIVES)))
    print("| 系统 | 自动命中率 | top-1 | top-3 | MRR | 负对照误放行 |")
    print("|---|---|---|---|---|---|")
    print("| S0 线上现状（触发词gate） | %.1f%% | - | - | - | - |" % (100 * s0_hit / total))
    print("| S1 混合排序（无反馈闭环） | %.1f%% | - | %.1f%% | - | - |"
          % (100 * s1_admit / total, 100 * s1_top3 / total))
    print("| S2 全链路（混合+证据放行+反馈闭环） | %.1f%% | %.1f%% | %.1f%% | %.3f | %d/%d |"
          % (100 * s2_hit / total, 100 * s2_top1 / total, 100 * s1_top3 / total,
             rr_sum / total, misroutes, len(NEGATIVES)))
    print("\n放行路径分布: %s" % paths)
    print("语义供应商: calls=%s cache_hits=%s degrades=%s（降级留痕，No silent downgrade）"
          % (embed_snap.get("embed_calls"), embed_snap.get("cache_hits"),
             embed_snap.get("degrades")))
    gate = result["quality_gate"]
    print("质量门（OMH 同口径）: 黄金集100%%=%s  负对照零误放行=%s"
          % (gate["golden_100pct"], gate["negative_zero_misroute"]))
    print("明细: %s" % out_path)
    ok = gate["golden_100pct"] and gate["negative_zero_misroute"]
    print("\n%s" % ("PASS" if ok else "FAIL（见明细 rows/negatives）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
