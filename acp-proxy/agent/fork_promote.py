"""fork 沙箱对照晋升门（蓝图 P2.4 / 对账 P2-10 "fork 沙箱对照晋升"）。

语义（蓝图原文：对「自我进化后的技能/prompt」在 fork 沙箱跑对照，再晋升主配置）：
1. **fork**：基线/候选两个变体各自物化进独立沙箱目录（tempfile.mkdtemp），
   并在**独立子进程**里跑同一套确定性评测（`python -m agent.fork_promote --harness`）——
   候选崩溃/挂死只污染自己这路，主进程与基线不受影响（超时即判失败收编）。
2. **对照**：同一评测双跑 + 评分对比（candidate.score >= baseline.score）
   + 成对零丢失断言（步骤/触发词/坑只增不减、验证保留）。
3. **晋升主配置**：候选全部通过且不低于基线才返回 promote=True（调用方随后
   才走 SkillSaver.save_flow 单一写入源落盘）；否则 keep baseline，主配置不动。

确定性原则：评测/裁决全确定性不走 LLM。行为级对照（EvalCase 数据集 +
LLM judge）为升级路径（eval_pipeline.py 已有数据集原语），不在本门内硬造。

诚实边界（如实声明，不粉饰）：
- 对照为**结构级**同构评测（可解析/分区完整/零丢失），不是行为回放；
- 子进程失败/超时一律按"候选未通过"处理（fail-closed at promotion
  boundary——晋升门的判据就是评测本身，评测不可用时不晋升，不是静默放行）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional

ACPPROXY_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ACPPROXY_ROOT not in sys.path:
    sys.path.insert(0, ACPPROXY_ROOT)

KIND_SKILL_MD = "skill_md"
KIND_PROMPT = "prompt"
KINDS = (KIND_SKILL_MD, KIND_PROMPT)

PASS_THRESHOLD = float(os.environ.get("SOULMATE_FORK_PROMOTE_THRESHOLD", "0.7"))
SANDBOX_TIMEOUT_S = float(os.environ.get("SOULMATE_FORK_SANDBOX_TIMEOUT_S", "20"))


@dataclass
class VariantReport:
    """单变体（基线或候选）在 fork 沙箱里的评测报告"""

    ok: bool
    score: float
    checks: list = field(default_factory=list)  # [{name, passed, detail}]
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "score": round(self.score, 4),
            "checks": self.checks,
            "error": self.error,
        }


# ── 渲染（技能变体物化用；与 SkillSaver 同一渲染真源） ──────────────


def render_skill_artifact(
    name: str,
    description: str,
    triggers: list,
    steps: list,
    pitfalls: list | None = None,
    verify: str = "",
    category: str = "general",
) -> str:
    """候选/基线技能变体 → 标准 SKILL.md 文本（SkillSaver.render_skill_md 单一真源）。"""
    from agent.skill_saver import SkillSaver

    return SkillSaver.render_skill_md(
        name=name,
        description=description,
        triggers=triggers,
        steps=steps,
        pitfalls=pitfalls,
        verify=verify,
        category=category,
    )


# ── 确定性评测（harness，子进程内执行） ─────────────────────────────


def _harness_checks(kind: str, content: str) -> list:
    """单变体绝对质量检查（确定性）。返回 [{name, passed, detail}]。"""
    checks: list = []
    text = str(content or "")

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": str(detail)[:200]})

    if kind == KIND_SKILL_MD:
        from agent.skill_evolution import parse_sections

        sections = parse_sections(text)
        add("parseable_steps", bool(sections["steps"]),
            f"steps={len(sections['steps'])}")
        add("steps_nonempty", all(s.strip() for s in sections["steps"]),
            "步骤条目均非空" if sections["steps"] else "无步骤")
        add("triggers_present", bool(sections["triggers"]),
            f"triggers={len(sections['triggers'])}")
        add("sections_marked", ("## 步骤" in text) and ("## 触发条件" in text),
            "步骤/触发条件分区标题存在")
    elif kind == KIND_PROMPT:
        stripped = text.strip()
        add("nonblank", bool(stripped), f"len={len(stripped)}")
        add("no_control_chars", not any(ord(c) < 9 or 13 < ord(c) < 32 for c in text),
            "无异常控制字符")
        fences = text.count("```")
        add("fences_balanced", fences % 2 == 0, f"code_fences={fences}")
        add("length_bounds", 1 <= len(stripped) <= 20000, f"chars={len(stripped)}")
    else:
        add("known_kind", False, f"未知 kind: {kind}")
    return checks


def _pairwise_checks(kind: str, baseline_content: str, candidate_content: str) -> list:
    """成对零丢失断言（确定性，基线语义载体在候选中不得丢失）。"""
    checks: list = []

    def add(name: str, passed: bool, detail: str = "") -> None:
        checks.append({"name": name, "passed": bool(passed), "detail": str(detail)[:200]})

    if kind == KIND_SKILL_MD:
        from agent.skill_evolution import parse_sections

        base = parse_sections(baseline_content)
        cand = parse_sections(candidate_content)

        def retained(base_list: list, cand_list: list) -> bool:
            seq = list(cand_list)
            for item in base_list:
                if item in seq:
                    seq.remove(item)
                else:
                    return False
            return True

        add("steps_lossless", retained(base["steps"], cand["steps"]),
            f"baseline_steps={len(base['steps'])} candidate_steps={len(cand['steps'])}")
        add("triggers_superset", retained(base["triggers"], cand["triggers"]),
            f"baseline={len(base['triggers'])} candidate={len(cand['triggers'])}")
        add("pitfalls_superset", retained(base["pitfalls"], cand["pitfalls"]),
            f"baseline={len(base['pitfalls'])} candidate={len(cand['pitfalls'])}")
        base_verify = (base["verify"] or "").strip()
        cand_verify = (cand["verify"] or "").strip()
        add("verify_preserved", (not base_verify) or (base_verify in cand_verify),
            "基线验证段保留" if base_verify else "基线无验证段")
    elif kind == KIND_PROMPT:
        base_lines = [l.strip() for l in str(baseline_content or "").splitlines() if l.strip()]
        cand_text = str(candidate_content or "")
        missing = [l for l in base_lines if l not in cand_text]
        add("baseline_lines_retained", not missing,
            f"missing={len(missing)}/{len(base_lines)}")
    else:
        add("known_kind", False, f"未知 kind: {kind}")
    return checks


def _score_of(checks: list) -> float:
    if not checks:
        return 0.0
    return sum(1.0 for c in checks if c.get("passed")) / len(checks)


# ── fork 沙箱执行（独立进程，崩溃/超时收编） ─────────────────────────


def _run_in_sandbox(kind: str, content: str, timeout_s: Optional[float] = None) -> VariantReport:
    """把变体物化进独立沙箱目录，在独立子进程跑 harness（真 fork 隔离）。"""
    timeout = float(timeout_s if timeout_s is not None else SANDBOX_TIMEOUT_S)
    root = tempfile.mkdtemp(prefix="fork_promote_")
    try:
        artifact_path = os.path.join(root, "artifact.md")
        result_path = os.path.join(root, "result.json")
        with open(artifact_path, "w", encoding="utf-8") as f:
            f.write(str(content or ""))
        env = dict(os.environ)
        env["PYTHONPATH"] = ACPPROXY_ROOT + os.pathsep + env.get("PYTHONPATH", "")
        cmd = [
            sys.executable, "-m", "agent.fork_promote",
            "--harness", kind,
            "--artifact", artifact_path,
            "--result", result_path,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, errors="replace",
                timeout=timeout, cwd=root, env=env,
            )
        except subprocess.TimeoutExpired:
            return VariantReport(
                ok=False, score=0.0,
                error=f"sandbox timeout (>{timeout:g}s)——按候选未通过收编",
            )
        except Exception as e:  # fork 不可用：评测不可用 → 不晋升（不静默放行）
            return VariantReport(ok=False, score=0.0, error=f"sandbox spawn failed: {e}")
        if proc.returncode != 0:
            return VariantReport(
                ok=False, score=0.0,
                error=f"harness exit={proc.returncode}: {(proc.stderr or '')[-200:]}",
            )
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return VariantReport(ok=False, score=0.0, error=f"result unreadable: {e}")
        checks = list(data.get("checks") or [])
        return VariantReport(
            ok=bool(data.get("ok")),
            score=float(data.get("score") or 0.0),
            checks=checks,
            error=str(data.get("error") or ""),
        )
    finally:
        import shutil

        shutil.rmtree(root, ignore_errors=True)


# ── 对照晋升裁决（主入口） ─────────────────────────────────────────


def compare_and_decide(kind: str, baseline_content: str, candidate_content: str) -> dict:
    """fork 沙箱对照晋升裁决。

    返回 {"promote": bool, "reasons": [...], "baseline": {...}, "candidate": {...},
          "pairwise": {...}, "kind": str}。
    晋升条件（全部满足才 promote=True）：
      1. 候选沙箱评测 ok（绝对质量检查全过）
      2. 候选 score >= PASS_THRESHOLD
      3. 候选 score >= 基线 score（对照不劣于基线）
      4. 成对零丢失断言全过
    任一不满足 → promote=False + 显式 reasons，主配置不动。
    """
    if kind not in KINDS:
        return {
            "promote": False,
            "kind": kind,
            "reasons": [f"未知 kind: {kind}（合法: {', '.join(KINDS)}）"],
            "baseline": {}, "candidate": {}, "pairwise": {},
        }
    baseline = _run_in_sandbox(kind, baseline_content)
    candidate = _run_in_sandbox(kind, candidate_content)
    pairwise = _pairwise_checks(kind, baseline_content, candidate_content)
    pairwise_ok = all(c["passed"] for c in pairwise)

    reasons: list = []
    if not candidate.ok:
        reasons.append("候选沙箱评测未通过" + (f": {candidate.error}" if candidate.error else ""))
    if candidate.score < PASS_THRESHOLD:
        reasons.append(f"候选评分 {candidate.score:.2f} < 阈值 {PASS_THRESHOLD:.2f}")
    if candidate.score < baseline.score:
        reasons.append(
            f"对照劣于基线（候选 {candidate.score:.2f} < 基线 {baseline.score:.2f}）"
        )
    if not pairwise_ok:
        failed = [c["name"] for c in pairwise if not c["passed"]]
        reasons.append("零丢失断言失败: " + ", ".join(failed))

    return {
        "promote": not reasons,
        "kind": kind,
        "reasons": reasons,
        "baseline": baseline.to_dict(),
        "candidate": candidate.to_dict(),
        "pairwise": {"ok": pairwise_ok, "checks": pairwise},
    }


# ── 子进程 harness 入口（python -m agent.fork_promote --harness ...） ──


def _harness_main(kind: str, artifact_path: str, result_path: str) -> int:
    try:
        with open(artifact_path, "r", encoding="utf-8") as f:
            content = f.read()
        checks = _harness_checks(kind, content)
        score = _score_of(checks)
        out = {"ok": all(c["passed"] for c in checks), "score": score, "checks": checks}
    except Exception as e:  # 评测崩溃在子进程内收编，不冒泡到主进程
        out = {"ok": False, "score": 0.0, "checks": [], "error": f"harness crash: {e}"}
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--harness", default="")
    parser.add_argument("--artifact", default="")
    parser.add_argument("--result", default="")
    args = parser.parse_args()
    if args.harness:
        sys.exit(_harness_main(args.harness, args.artifact, args.result))
    parser.error("需要 --harness")
