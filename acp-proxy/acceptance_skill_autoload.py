#!/usr/bin/env python3
"""acceptance_skill_autoload.py — P0 技能自动命中真实场景验收（可复跑）

真实场景：发票报销任务跑两次。run1 无技能→执行→自动学习→命中记账；
run2 同类任务换说法→断言技能自动命中 + 注入上下文 + 装载验证 all_loaded +
反馈再记账（情况语句增长）。另跑负对照（普通问题零误命中）与接线护栏。

走的全是生产链路组件：SkillManager.try_learn_skill / SkillLoader.match_for_task
（soulmate_agent 检索单一真源）/ render_local_section（注入段字节契约）/
SoulMateAgent._record_skill_hits + _verify_skill_loads（真实接线方法，__new__
裸实例注入隔离依赖）。语义层用生产供应商链（opensoul /api/gland/embed 首选，
实测可用；其 registry 抖动时 ollama bge-m3 / hash 依次兜底并留痕）。

沙箱：全部运行时数据写 data/skill_autoload_acceptance/（每次运行重置），
绝不碰生产 skills/、账本、收据。

用法（可复跑）：
    cd ~/openmate/acp-proxy && /usr/bin/python3 acceptance_skill_autoload.py
退出码 0=PASS / 1=FAIL。产物：data/skill_autoload_acceptance/（含收据/账本）。
"""

import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 沙箱 env（导入前设置——模块级路径常量在导入时读取）
SANDBOX = ROOT / "data" / "skill_autoload_acceptance"
os.environ["SOULMATE_SKILL_LEDGER"] = str(SANDBOX / "ledger.json")
os.environ["SOULMATE_SKILL_LOAD_RECEIPTS"] = str(SANDBOX / "skill_load_receipts.jsonl")
os.environ["SOULMATE_SKILL_EMBED_CACHE"] = str(SANDBOX / "embed_cache.json")

import agent.soulmate_agent as sa  # noqa: E402
from agent.skill_feedback import SkillFeedbackLedger  # noqa: E402
from agent.skill_loader import SkillLoader  # noqa: E402
from agent.skill_load_verify import read_receipts  # noqa: E402
from agent.skill_semantic import SkillEmbedder  # noqa: E402
from skill_manager import SkillManager  # noqa: E402

TASK1 = "帮我把这个季度的发票报销单整理一下，填好提交给财务"
TASK2 = "请将本季度发票整理成报销单并交给财务部门"  # 同类任务·换说法
NEGATIVE = "今天天气怎么样，帮我查一下北京的天气情况"
TOOL_CALLS = [
    {"function": {"name": "read_file", "arguments": {"path": "invoices/q3.pdf"}}},
    {"function": {"name": "image_tools", "arguments": {"command": "ocr"}}},
    {"function": {"name": "write_file", "arguments": {"path": "baoxiao.md", "content": "x"}}},
]
RESPONSE = "已整理完成：\n1. 汇总了本季度发票\n2. 生成报销单\n3. 提交财务"

checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    checks.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    t0 = time.time()
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)  # 幂等：每次运行重置沙箱
    SANDBOX.mkdir(parents=True)

    mgr = SkillManager(
        skills_dir=str(SANDBOX / "skills"), standard_dirs=[], saved_dir=str(SANDBOX / "saved")
    )
    ledger = SkillFeedbackLedger()
    embedder = SkillEmbedder()  # 生产供应商链（opensoul→ollama→openai→hash，降级留痕）
    loader = SkillLoader(skill_manager=mgr, feedback=ledger)  # matcher lazy-init 同账本

    # 真实 SoulMateAgent 方法（__new__ 裸实例 + 注入隔离依赖）
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    agent._skill_manager = mgr
    agent._skill_feedback_inst = ledger
    agent._skill_loader_inst = loader

    print("\n=== run1：首次任务（无技能→学习→记账）===")
    learned = asyncio.run(
        mgr.try_learn_skill(
            user_text=TASK1, assistant_response=RESPONSE, tool_calls=TOOL_CALLS))
    check("run1 从成功执行中学到技能", learned is not None,
          f"id={learned['id'][:8]} name={learned['name']}" if learned else "")
    if not learned:
        return report(t0)
    n = agent._record_skill_hits([learned], TASK1, RESPONSE)
    check("run1 命中记账（有用）+情况语句沉淀", n == 1 and bool(ledger.situations_for(learned["id"])),
          f"situations={ledger.situations_for(learned['id'])}")

    print("\n=== run2：同类任务换说法（自动命中=近零摸索）===")
    hits = loader.match_for_task(TASK2)
    hit_ids = [h.get("id") for h in hits]
    check("run2 技能自动命中（top-1）", bool(hit_ids) and hit_ids[0] == learned["id"],
          f"match_reason={hits[0]['match_reason'] if hits else '无命中'}")
    section, bodies = loader.render_local_section(hits)
    check("run2 技能注入任务上下文", learned["name"] in section,
          f"注入段 {len(section)} 字符")
    overall = agent._verify_skill_loads(hits, section, bodies, "acceptance-run2")
    check("run2 装载验证（写了≠接线了）", overall == "all_loaded", f"overall={overall}")
    receipts = read_receipts()
    check("装载收据落盘可读", bool(receipts) and receipts[-1]["overall"] == "all_loaded",
          f"共 {len(receipts)} 条收据")
    agent._record_skill_hits(hits, TASK2, RESPONSE)
    check("run2 反馈再记账（情况语句增长→run3+更稳）",
          len(ledger.situations_for(learned["id"])) >= 2,
          f"共 {len(ledger.situations_for(learned['id']))} 条情况语句")

    print("\n=== 负对照：普通问题零误命中（pitfall 30 防误触发）===")
    neg_hits = loader.match_for_task(NEGATIVE)
    check("天气闲聊不命中任何技能", neg_hits == [],
          f"hits={[h.get('name') for h in neg_hits]}")

    print("\n=== 接线护栏（写了≠接线了：真实聊天路径含全套调用）===")
    src = (ROOT / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")
    check("检索：match_for_task 收口", "self._skill_loader().match_for_task(user_text)" in src)
    check("注入+装载验证：render→verify", 
          "self._verify_skill_loads(matched_skills, _skill_section, _skill_bodies, session_id)" in src)
    check("记账：回合收尾 record_skill_hits",
          "self._record_skill_hits(matched_skills, user_text, full_response)" in src)
    check("闭环起点：学到技能即记账",
          "self._record_skill_hits([_learned_skill], user_text, full_response)" in src)

    return report(t0)


def report(t0: float) -> int:
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    ok = passed == total
    print(f"\n{'=' * 60}")
    print(f"结果：{passed}/{total} 通过 — {'PASS' if ok else 'FAIL'}（{time.time() - t0:.1f}s）")
    print(f"沙箱产物：{SANDBOX}（账本/收据/嵌入缓存，可检查）")
    print("\n复跑步骤：cd ~/openmate/acp-proxy && /usr/bin/python3 acceptance_skill_autoload.py")
    print("量化命中率基准：/usr/bin/python3 skill_route_benchmark.py（黄金集+负对照，OMH 同口径）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
