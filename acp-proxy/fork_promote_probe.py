# -*- coding: utf-8 -*-
"""P2-10 fork 沙箱对照晋升 真跑终验探针（蓝图 P2.4）。

四层实证（全部真实代码路径，无 mock 生产件）：
  A. 门语义层：compare_and_decide 真实 fork 沙箱（独立子进程）双向对照
     —— 候选胜出晋升 / 候选劣于基线不晋 / prompt 类双向。
  B. 真实晋升主配置（技能坑沉淀漏斗）：SkillEvolver.evolve_from_turn
     （生产同款入口）→ 真实 SkillSaver 落盘可写技能库 → 文件回读。
  C. 真实晋升主配置（语义改写漏斗）：SkillRewriter.rewrite（真实保真门+
     真实对照门+真实落盘；LLM 为注入输入源，如实标注）。
  D. 真实漏斗内的 fail-closed 否决：沙箱超时（评测不可用）→ 漏斗 kept_baseline
     主配置不动（通过真实 evolve_from_turn，非 mock）。

产物：evidence/p2-10-fork-promote-evidence.json（探针自报+服务端文件真源对照）。
探针技能用完即删（测试产物清理归零），删除记录入证据。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

ACPPROXY_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ACPPROXY_ROOT))

import agent.fork_promote as fp  # noqa: E402
from agent.fork_promote import compare_and_decide, render_skill_artifact  # noqa: E402
from agent.skill_evolution import SkillEvolver  # noqa: E402
from agent.skill_rewrite import SkillRewriter  # noqa: E402
from agent.skill_saver import SkillSaver  # noqa: E402

EVIDENCE_PATH = (
    ACPPROXY_ROOT.parent
    / "docs" / "agent-architecture-mimo" / "synthesis" / "evidence"
    / "p2-10-fork-promote-evidence.json"
)
RUN_TAG = f"P210_{int(time.time())}"
PROBE_SKILL_NAME = "P2-10晋升探针"


def _check(results: list, name: str, ok: bool, detail: str = "") -> None:
    results.append({"name": name, "ok": bool(ok), "detail": str(detail)[:400]})
    print(f"  [{'✅' if ok else '❌'}] {name}: {str(detail)[:160]}")


def main() -> int:
    results: list = []
    extra: dict = {"run_tag": RUN_TAG}
    lib = str(ACPPROXY_ROOT / "data" / "fork_promote_probe_library")
    db = str(ACPPROXY_ROOT / "data" / "fork_promote_probe.db")
    shutil.rmtree(lib, ignore_errors=True)
    if os.path.exists(db):
        os.remove(db)
    saver = SkillSaver(library_dir=lib)

    base_md = render_skill_artifact(
        name=PROBE_SKILL_NAME, description="P2-10 终验探针技能",
        triggers=["p210探针", "晋升探针"],
        steps=["第一步读取 /tmp/p210_probe.txt", "第二步写入结果到 /tmp/p210_out.txt"],
        pitfalls=["原有坑：路径要绝对"], verify="跑一遍确认输出", category="test",
    )
    cand_win_md = render_skill_artifact(
        name=PROBE_SKILL_NAME, description="P2-10 终验探针技能",
        triggers=["p210探针", "晋升探针"],
        steps=["第一步读取 /tmp/p210_probe.txt", "第二步写入结果到 /tmp/p210_out.txt"],
        pitfalls=["原有坑：路径要绝对",
                  "read_file 可能报错「文件不存在」（recoverable）—— 重试/换路径后可成功"],
        verify="跑一遍确认输出", category="test",
    )
    cand_lose_md = render_skill_artifact(
        name=PROBE_SKILL_NAME, description="P2-10 终验探针技能",
        triggers=["p210探针"],
        steps=["第一步读取 /tmp/p210_probe.txt"],
        pitfalls=["原有坑：路径要绝对"], verify="跑一遍确认输出", category="test",
    )

    # ── A. 门语义层（真实 fork 沙箱子进程）────────────────────
    print("A. 门语义层：真实 fork 沙箱对照（基线/候选独立子进程双跑）")
    v_win = compare_and_decide("skill_md", base_md, cand_win_md)
    _check(results, "A1 候选胜出→晋升", v_win["promote"] and not v_win["reasons"],
           f"promote={v_win['promote']} baseline_score={v_win['baseline']['score']} "
           f"candidate_score={v_win['candidate']['score']}")
    _check(results, "A1b 沙箱独立评测落痕",
           bool(v_win["baseline"]["checks"]) and bool(v_win["candidate"]["checks"]),
           f"baseline_checks={len(v_win['baseline']['checks'])} "
           f"candidate_checks={len(v_win['candidate']['checks'])}")
    v_lose = compare_and_decide("skill_md", base_md, cand_lose_md)
    _check(results, "A2 候选劣于基线→不晋", (not v_lose["promote"])
           and any("零丢失" in r for r in v_lose["reasons"]),
           f"promote={v_lose['promote']} reasons={v_lose['reasons'][:2]}")
    v_pw = compare_and_decide("prompt", "line one\nline two",
                              "line one\nline two\nline three")
    v_pw2 = compare_and_decide("prompt", "line one\nline two", "line one")
    _check(results, "A3 prompt 类双向", v_pw["promote"] and not v_pw2["promote"],
           f"win={v_pw['promote']} lose={v_pw2['promote']} "
           f"lose_reasons={v_pw2['reasons'][:1]}")
    extra["gate_verdicts"] = {"win": v_win, "lose": v_lose,
                             "prompt_win": v_pw, "prompt_lose": v_pw2}

    # ── B. 真实晋升主配置（坑沉淀漏斗，生产同款入口）──────────
    print("B. 真实晋升主配置：SkillEvolver.evolve_from_turn（真实落盘可写库）")
    saved = saver.save_flow(
        name=PROBE_SKILL_NAME, description="P2-10 终验探针技能",
        triggers=["p210探针", "晋升探针"],
        steps=["第一步读取 /tmp/p210_probe.txt", "第二步写入结果到 /tmp/p210_out.txt"],
        pitfalls=["原有坑：路径要绝对"], verify="跑一遍确认输出", category="test",
    )
    evolver = SkillEvolver(saver=saver, db_path=db, max_new_per_turn=2, max_pitfalls=12)
    fail_entry = {
        "name": "read_file", "arguments": "{}",
        "result_preview": "错误: 文件不存在或为空: /tmp/not-there", "permission": "allow",
    }
    r = evolver.evolve_from_turn(
        skills=[{"id": f"saved:{saved['slug']}"}],
        tool_calls_log=[fail_entry], session_id="p210_probe",
    )[0]
    reloaded = saver.load_skill(saved["slug"])
    _check(results, "B1 坑沉淀经对照门晋升主配置",
           r.action == "evolved" and reloaded is not None
           and "read_file 可能报错" in reloaded["body"] and reloaded["revision"] == 2,
           f"action={r.action} revision={reloaded and reloaded['revision']} "
           f"file={reloaded and reloaded['skill_md']} 含新坑="
           f"{bool(reloaded and 'read_file 可能报错' in reloaded['body'])}")
    extra["funnel_promotion"] = {
        "action": r.action, "revision_before": r.revision_before,
        "revision_after": r.revision_after, "added": r.added,
        "file": reloaded and reloaded["skill_md"],
        "body_contains_new_pitfall": bool(
            reloaded and "read_file 可能报错" in reloaded["body"]),
    }

    # ── C. 真实语义改写漏斗晋升（LLM=注入输入源，门/落盘全真实）──
    print("C. 真实晋升主配置：SkillRewriter.rewrite（真实保真门+对照门+落盘）")

    async def fake_llm(_prompt):
        return ('{"steps": ["第一步读取 /tmp/p210_probe.txt（措辞改写）",'
                ' "第二步写入结果到 /tmp/p210_out.txt"],'
                ' "trigger_additions": ["p210改写探针"]}')

    rewriter = SkillRewriter(saver=saver)
    skill = saver.load_skill(saved["slug"])
    skill["id"] = f"saved:{saved['slug']}"
    out = asyncio.run(rewriter.rewrite(
        skill, user_text="p210改写探针场景", llm=fake_llm))
    back = saver.load_skill(saved["slug"])
    _check(results, "C1 语义改写经对照门晋升主配置",
           out.action == "rewritten" and back is not None
           and "措辞改写" in back["body"] and "p210改写探针" in back["body"],
           f"action={out.action} reason={out.reason[:80]} "
           f"含改写={bool(back and '措辞改写' in back['body'])} "
           f"含新触发词={bool(back and 'p210改写探针' in back['body'])}")
    extra["rewrite_promotion"] = {
        "action": out.action, "revision_after": out.revision_after,
        "added_triggers": out.added_triggers,
        "body_contains_rewording": bool(back and "措辞改写" in back["body"]),
    }

    # ── D. 真实漏斗内 fail-closed 否决（沙箱超时=评测不可用→不晋升）──
    print("D. 真实漏斗否决：沙箱超时收编 → kept_baseline 主配置不动")
    before = saver.load_skill(saved["slug"])
    before_rev, before_body = before["revision"], before["body"]
    old_timeout = fp.SANDBOX_TIMEOUT_S
    fp.SANDBOX_TIMEOUT_S = 0.01
    try:
        r2 = evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[{
                "name": "terminal", "arguments": "{}",
                "result_preview": "错误: 执行超时（120s）", "permission": "allow",
            }],
            session_id="p210_probe_failclosed",
        )[0]
    finally:
        fp.SANDBOX_TIMEOUT_S = old_timeout
    after = saver.load_skill(saved["slug"])
    _check(results, "D1 沙箱超时→kept_baseline 主配置不动",
           r2.action == "kept_baseline" and after["revision"] == before_rev
           and after["body"] == before_body,
           f"action={r2.action} reason={r2.reason[:100]} "
           f"revision {before_rev}→{after['revision']}")
    extra["failclosed_denial"] = {
        "action": r2.action, "reason": r2.reason[:300],
        "revision_unchanged": after["revision"] == before_rev,
        "body_unchanged": after["body"] == before_body,
    }

    # ── 清理（测试产物清理归零）─────────────────────────────
    final_body = saver.load_skill(saved["slug"])["body"]
    shutil.rmtree(lib, ignore_errors=True)
    if os.path.exists(db):
        os.remove(db)
    cleaned = (not os.path.exists(lib)) and (not os.path.exists(db))
    _check(results, "E 清理归零", cleaned, f"library/db removed={cleaned}")
    extra["cleanup"] = {
        "probe_library_removed": cleaned,
        "final_body_for_record": final_body[:600],
    }

    ok_all = all(c["ok"] for c in results)
    payload = {
        "probe": "p2-10-fork-sandbox-compare-promote",
        "run_tag": RUN_TAG,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "passed": ok_all,
        "total": len(results),
        "checks": results,
        **extra,
    }
    tmp_copy = Path("/tmp/p2-10-fork-promote-evidence.json")
    tmp_copy.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"\n{'PASS' if ok_all else 'FAIL'} {sum(c['ok'] for c in results)}"
          f"/{len(results)}  evidence={EVIDENCE_PATH}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
