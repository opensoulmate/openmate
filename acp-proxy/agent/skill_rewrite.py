"""路线图 13.5 技能进化语义改写 —— LLM 辅助 + 确定性零丢失保真门。

6.3 诚实边界销账：步骤/触发词的语义改写此前"需 LLM 或用户拍板，未做"。
本模块做**确定性可验证的那部分**：

- LLM 只允许 **步骤措辞改写**（数量不变、语义载体零丢失）与 **触发词增补**
  （只增不删不改）——保真门确定性校验，任一违反即整体拒绝（拒绝时技能文件
  逐字不动），拒绝原因显式记录。
- **语义取舍**（删/并/拆步骤、删/改触发词、改描述/验证/坑）一律拒绝，
  报"需用户拍板"——这类改动不做、不猜、不擅自取舍（13.5 契约）。
- LLM 不可用/超时/输出不可解析 → 显式跳过报因，绝不静默、绝不编造。

保真门（fidelity_check，确定性不走 LLM）：
1. 步骤数量必须一致（合并/拆分=取舍 → 拒绝）
2. 每步语义载体 token（引号内容/反引号/路径/命令flag/数字/常量名/扩展名）
   必须原样出现在改写后同序步骤里（措辞可变，命令与参数不可变）
3. 触发词是原集合超集（只增不删），新增词过 is_injectable_trigger 有效性门槛
4. 写回后回读复验（load_skill→parse_sections 重走保真门 + 坑/验证逐字一致），
   回读失败则还原原文并报拒绝——产出用不同路径回读验证，不信生成时自报

确定性原则：解析/校验/门槛全确定性；LLM 只产出候选，通过保真门才落盘。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger("acp-proxy.skill_rewrite")

# 环境开关（默认开启；置 0 关闭语义改写）
ENV_ENABLED = "SOULMATE_SKILL_REWRITE"
# 同一技能两次语义改写的最小间隔（秒）——LLM 成本有界，默认 24h
ENV_INTERVAL_S = "SOULMATE_SKILL_REWRITE_INTERVAL_S"
# 单次 LLM 改写往返超时（秒）
ENV_TIMEOUT_S = "SOULMATE_SKILL_REWRITE_TIMEOUT_S"
DEFAULT_INTERVAL_S = 86400.0
DEFAULT_TIMEOUT_S = 20.0

# 新增触发词长度上限（防把整句请求当触发词塞进来）
_TRIGGER_MAX_CHARS = 40

# 语义载体 token：命令/路径/引号内容/数字/常量——改写不允许丢失的硬约束
_HARD_TOKEN_RES: tuple[re.Pattern, ...] = (
    re.compile(r"`[^`\n]+`"),                                   # `code`
    re.compile(r"[\"'「][^\"'\n「」]{1,120}[\"'」]"),            # "..." '...' 「...」
    re.compile(r"(?:~|\.{1,2})?/[\w.\-\[\]*?]+(?:/[\w.\-\[\]*?]+)*"),  # 路径
    re.compile(r"--?[A-Za-z][\w\-]*"),                          # 命令 flag
    re.compile(r"\b[\w\-*]+\.[A-Za-z0-9]{1,6}\b"),              # 扩展名/glob（*.py、SKILL.md）
    re.compile(r"\b\d[\d.:]*\b"),                               # 数字/端口/版本
    re.compile(r"\b[A-Z][A-Z0-9_]{3,}\b"),                      # 常量/环境变量名
)


def _norm(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def hard_tokens(text: str) -> list[str]:
    """提取文本的语义载体 token（保真门比对用，确定性）"""
    out: list[str] = []
    seen: set[str] = set()
    for rx in _HARD_TOKEN_RES:
        for m in rx.finditer(str(text or "")):
            tok = _norm(m.group(0))
            if tok and tok not in seen:
                seen.add(tok)
                out.append(tok)
    return out


def fidelity_check(
    old_steps: list[str],
    new_steps: list[str],
    old_triggers: list[str],
    new_triggers: list[str],
) -> tuple[bool, list[str]]:
    """零丢失保真门（确定性）。返回 (ok, reasons)。

    - 步骤数量一致：合并/拆分是语义取舍 → 拒绝
    - 每步语义载体 token 全部原样保留（措辞可变，命令/路径/数字不可变）
    - 触发词只增不删（超集），新增项过 is_injectable_trigger
    """
    reasons: list[str] = []
    old_steps = [str(s or "").strip() for s in (old_steps or [])]
    new_steps = [str(s or "").strip() for s in (new_steps or [])]
    if len(old_steps) != len(new_steps):
        reasons.append(
            f"步骤数量变化 {len(old_steps)}→{len(new_steps)}（合并/拆分=语义取舍，需用户拍板）"
        )
    else:
        for i, (old_s, new_s) in enumerate(zip(old_steps, new_steps), 1):
            if not new_s:
                reasons.append(f"步骤{i}改写后为空")
                continue
            new_l = new_s.lower()
            for tok in hard_tokens(old_s):
                if tok not in new_l:
                    reasons.append(f"步骤{i}丢失语义载体「{tok}」")
    old_t = {_norm(t) for t in (old_triggers or []) if str(t or "").strip()}
    new_t = {_norm(t) for t in (new_triggers or []) if str(t or "").strip()}
    for lost in sorted(old_t - new_t):
        reasons.append(f"触发词被删除「{lost}」（删除=语义取舍，需用户拍板）")
    if not reasons:
        try:
            from skill_manager import is_injectable_trigger
        except Exception:
            is_injectable_trigger = None  # 模块不可用时退化为长度门槛
        for t in new_t - old_t:
            raw = str(t).strip()
            if len(raw) > _TRIGGER_MAX_CHARS:
                reasons.append(f"新增触发词过长（>{_TRIGGER_MAX_CHARS}字）「{raw[:40]}…」")
                continue
            if is_injectable_trigger is not None and not is_injectable_trigger(raw):
                reasons.append(f"新增触发词无效（过短/通用词）「{raw}」")
    return (not reasons, reasons)


def build_rewrite_prompt(
    name: str,
    description: str,
    triggers: list[str],
    steps: list[str],
    user_text: str = "",
    failures: Optional[list] = None,
) -> str:
    """确定性改写提示词（要求纯 JSON 输出）"""
    trig = "\n".join(f"- {t}" for t in triggers)
    step = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    fail = ""
    if failures:
        fail = "\n".join(f"- {f}" for f in failures[:5])
        fail = f"\n## 本回合执行失败记录（供参考）\n{fail}\n"
    user_s = str(user_text or "").strip()[:2000]
    return f"""你是技能文档编辑。请对下面技能的"步骤"做措辞改写（更清晰可执行），并按用户真实用语补充"触发词"。

## 技能
名称: {name}
描述: {description}
触发词:
{trig}
步骤:
{step}
{fail}
## 本回合用户请求（触发词增补的用语依据）
{user_s}

## 硬性规则（违反即被拒绝）
1. 输出**纯 JSON**，格式：{{"steps": [...], "trigger_additions": [...]}}
2. steps 数量必须与原步骤**完全一致**（只改措辞，禁止删除/合并/拆分步骤）
3. 每个步骤中的命令、路径、引号内容、参数 flag、数字必须**原样保留**
4. trigger_additions 只**新增**触发词（禁止修改/删除现有触发词）；每条 ≤{_TRIGGER_MAX_CHARS}字；
   依据用户请求用语提炼，没有值得新增的就给 []
5. 无需任何改写时输出：{{"steps": [], "trigger_additions": []}}
只输出 JSON，不要解释。"""


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_proposal(text: str) -> tuple[list[str], list[str], str]:
    """从 LLM 输出解析改写提案（容错：剥代码围栏/扫描首个平衡花括号块）。

    返回 (steps, trigger_additions, err)；err 非空=不可解析（显式报因禁编造）。
    """
    raw = str(text or "").strip()
    if not raw:
        return [], [], "LLM 输出为空"
    candidates: list[str] = []
    m = _JSON_FENCE_RE.search(raw)
    if m:
        candidates.append(m.group(1))
    # 花括号平衡扫描（容 JSON 前后带解释文本）
    start = raw.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(raw[start:i + 1])
                    break
        start = raw.find("{", start + 1)
        if len(candidates) > 5:
            break
    for cand in candidates:
        try:
            data = json.loads(cand)
        except (TypeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        steps = data.get("steps")
        adds = data.get("trigger_additions", [])
        if steps is None:
            continue
        if not isinstance(steps, list) or not all(isinstance(s, str) for s in steps):
            return [], [], "steps 字段不是字符串数组"
        if not isinstance(adds, list) or not all(isinstance(s, str) for s in adds):
            return [], [], "trigger_additions 字段不是字符串数组"
        return [s.strip() for s in steps], [s.strip() for s in adds], ""
    return [], [], "LLM 输出中找不到合法 JSON 提案"


@dataclass
class RewriteOutcome:
    """一次语义改写尝试的结果（改没改都可观测）"""

    skill_id: str
    slug: str
    action: str  # rewritten | rejected | skipped
    reason: str = ""
    changed_steps: int = 0
    added_triggers: list[str] = field(default_factory=list)
    revision_before: int = 0
    revision_after: int = 0


class SkillRewriter:
    """LLM 辅助语义改写执行器：提案 → 保真门 → 原子写回 → 回读复验。

    只改步骤措辞与触发词增补；name/description/坑/验证逐字不动（取舍类
    改动被保真门/字段不动约束拒绝，报"需用户拍板"）。
    """

    def __init__(
        self,
        saver=None,
        timeout_s: Optional[float] = None,
    ):
        self._saver = saver  # lazy：None 时用 get_skill_saver()
        self.timeout_s = float(
            timeout_s if timeout_s is not None
            else os.environ.get(ENV_TIMEOUT_S, DEFAULT_TIMEOUT_S)
        )

    # ── 保真门辅助 ──────────────────────────────────────────

    @staticmethod
    def _triggers_of(skill: dict, sections: dict) -> list[str]:
        trig = list(sections.get("triggers") or [])
        if not trig:
            trig = [t.strip() for t in str((skill or {}).get("triggers") or "").split(",") if t.strip()]
        return trig

    # ── 主入口 ─────────────────────────────────────────────

    async def rewrite(
        self,
        skill: dict,
        user_text: str = "",
        failures: Optional[list] = None,
        llm: Optional[Callable] = None,
    ) -> RewriteOutcome:
        """对单个技能做一次语义改写尝试（调用方负责频控/审计）。"""
        skill_id = str((skill or {}).get("id") or "")
        slug = str((skill or {}).get("slug") or "")
        if llm is None:
            return RewriteOutcome(skill_id, slug, "skipped", "LLM 不可用（语义改写需 LLM 或用户拍板）")
        saver = self._saver
        if saver is None:
            from agent.skill_saver import get_skill_saver

            saver = get_skill_saver()
            self._saver = saver
        existing = skill
        if not existing.get("body"):
            existing = saver.load_skill(slug)
        if not existing:
            return RewriteOutcome(skill_id, slug, "skipped", "技能文件不存在")
        from agent.skill_evolution import parse_sections

        sections = parse_sections(existing.get("body") or "")
        if not sections["steps"]:
            return RewriteOutcome(
                skill_id, slug, "skipped", "正文非SkillSaver渲染格式（步骤解析为空），不改写"
            )
        old_steps = list(sections["steps"])
        old_triggers = self._triggers_of(existing, sections)
        old_pitfalls = list(sections["pitfalls"])
        old_verify = sections["verify"]
        rev_before = int(existing.get("revision", 1) or 1)

        prompt = build_rewrite_prompt(
            name=str(existing.get("name") or slug),
            description=str(existing.get("description") or ""),
            triggers=old_triggers,
            steps=old_steps,
            user_text=user_text,
            failures=failures,
        )
        try:
            raw = await asyncio.wait_for(llm(prompt), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            return RewriteOutcome(
                skill_id, slug, "skipped", f"LLM 改写超时（>{self.timeout_s:.0f}s）", revision_before=rev_before
            )
        except Exception as e:
            return RewriteOutcome(
                skill_id, slug, "skipped", f"LLM 调用失败: {e}", revision_before=rev_before
            )
        raw_s = str(raw or "")
        if raw_s.startswith("[LLM错误"):
            return RewriteOutcome(
                skill_id, slug, "skipped", f"LLM 不可用: {raw_s[:120]}", revision_before=rev_before
            )
        new_steps, add_triggers, err = parse_proposal(raw_s)
        if err:
            return RewriteOutcome(
                skill_id, slug, "rejected", f"提案不可解析: {err}", revision_before=rev_before
            )
        if not new_steps and not add_triggers:
            return RewriteOutcome(
                skill_id, slug, "skipped", "LLM 判定无需改写", revision_before=rev_before
            )
        # 提案缺 steps 但有触发词增补 → 步骤原样（零改写合法形态）
        if not new_steps:
            new_steps = list(old_steps)
        new_triggers = list(old_triggers) + [t for t in add_triggers if t]
        ok, reasons = fidelity_check(old_steps, new_steps, old_triggers, new_triggers)
        if not ok:
            return RewriteOutcome(
                skill_id, slug, "rejected",
                "保真门拒绝（零丢失断言失败）: " + "; ".join(reasons[:6]),
                revision_before=rev_before,
            )

        # 字段不动约束：description/坑/验证逐字透传（取舍类改动不在此通道）
        # ── P2-10 fork 沙箱对照晋升门（蓝图 P2.4）：语义改写候选晋升主配置前 fork 对照 ──
        try:
            from agent.fork_promote import compare_and_decide, render_skill_artifact

            candidate_md = render_skill_artifact(
                name=existing.get("name") or slug,
                description=existing.get("description") or "",
                triggers=new_triggers,
                steps=new_steps,
                pitfalls=old_pitfalls,
                verify=old_verify,
                category=existing.get("category") or "general",
            )
            verdict = compare_and_decide("skill_md", existing.get("body") or "", candidate_md)
        except Exception as e:
            verdict = {"promote": False, "reasons": [f"对照晋升门异常: {e}"]}
        if not verdict.get("promote"):
            return RewriteOutcome(
                skill_id, slug, "rejected",
                "fork沙箱对照未晋升（主配置不动）: "
                + "; ".join(str(r) for r in (verdict.get("reasons") or []))[:300],
                revision_before=rev_before,
            )
        try:
            saved = saver.save_flow(
                name=existing.get("name") or slug,
                description=existing.get("description") or "",
                triggers=new_triggers,
                steps=new_steps,
                pitfalls=old_pitfalls,
                verify=old_verify,
                category=existing.get("category") or "general",
                overwrite=True,
            )
        except Exception as e:
            logger.warning(f"[skill-rewrite] save failed for {slug}: {e}")
            return RewriteOutcome(
                skill_id, slug, "rejected", f"写回失败: {e}", revision_before=rev_before
            )
        rev_after = int(saved.get("revision", rev_before + 1) or rev_before + 1)

        # 回读复验（不同路径：load_skill→parse_sections 重走保真门）
        try:
            back = saver.load_skill(slug) or {}
            back_sections = parse_sections(back.get("body") or "")
            back_triggers = self._triggers_of(back, back_sections)
            back_ok, back_reasons = fidelity_check(
                old_steps, back_sections["steps"], old_triggers, back_triggers
            )
            pitfalls_same = list(back_sections["pitfalls"]) == old_pitfalls
            verify_same = back_sections["verify"] == old_verify
            if not (back_ok and pitfalls_same and verify_same):
                why = "; ".join(back_reasons[:4]) if not back_ok else "坑/验证被改动"
                # 回读失败：还原原文（零丢失承诺优先于本次改写）
                saver.save_flow(
                    name=existing.get("name") or slug,
                    description=existing.get("description") or "",
                    triggers=old_triggers,
                    steps=old_steps,
                    pitfalls=old_pitfalls,
                    verify=old_verify,
                    category=existing.get("category") or "general",
                    overwrite=True,
                )
                return RewriteOutcome(
                    skill_id, slug, "rejected",
                    f"回读复验失败已还原原文: {why}", revision_before=rev_before, revision_after=rev_before,
                )
        except Exception as e:
            return RewriteOutcome(
                skill_id, slug, "rejected",
                f"回读复验异常已尝试还原: {e}", revision_before=rev_before, revision_after=rev_before,
            )

        changed = sum(1 for a, b in zip(old_steps, new_steps) if _norm(a) != _norm(b))
        added_only = [t for t in add_triggers if _norm(t) not in {_norm(x) for x in old_triggers}]
        return RewriteOutcome(
            skill_id, slug, "rewritten",
            f"改写{changed}步+新增{len(added_only)}触发词",
            changed_steps=changed,
            added_triggers=added_only,
            revision_before=rev_before,
            revision_after=rev_after,
        )
