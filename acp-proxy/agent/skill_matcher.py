"""路线图 6.2 技能检索与加载：按任务自动匹配 + 预算感知注入。

与 skill_manager.search_skills 的分工：
- search_skills = 候选召回（triggers/tags/description 打分取 top-N）
- SkillMatcher.match = 任务级精排 + 触发词闸门（纯描述/tag 命中不注入，
  保持既有"只保留触发词匹配的技能"语义；触发词策略复用
  skill_manager.is_injectable_trigger 单一真源）
- SkillMatcher.render_bodies = 注入块渲染（预算感知加载：单技能/总预算
  双上限，超长显式截断标记，禁静默截断——AIHawk SHOWN/SENT 原则）。

确定性实现，不走 LLM；所有阈值可经环境变量覆盖（测试可调）。
"""

from __future__ import annotations

import logging
import os
import re

from skill_manager import is_injectable_trigger
from utils.token_manager import estimate_tokens

logger = logging.getLogger(__name__)

# 预算（tokens）：SOULMATE_SKILL_PER_TOKENS 单技能上限、
# SOULMATE_SKILL_BUDGET_TOKENS 全部技能注入总预算
DEFAULT_PER_SKILL_TOKENS = 1500
DEFAULT_BUDGET_TOKENS = 3000


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _hit_triggers(task_lower: str, task_tokens: set[str], triggers: list) -> tuple[float, list[str]]:
    """触发词命中打分（对齐 search_skills 权重：精确15/子串10/词token 5）。

    返回 (score, hit_list)；hit_list 为空 = 触发词闸门未通过。
    """
    score = 0.0
    hits: list[str] = []
    for trigger in triggers or []:
        t_lower = str(trigger or "").lower().strip()
        if not t_lower or not is_injectable_trigger(t_lower):
            continue
        if t_lower == task_lower:
            score += 15.0
            hits.append(t_lower)
        elif t_lower in task_lower or task_lower in t_lower:
            # 子串命中 + 特异性加成（触发词越长越具体：发票OCR > 发票）
            score += 10.0 + 0.5 * min(len(t_lower), 20)
            hits.append(t_lower)
        elif t_lower in task_tokens:
            score += 5.0 + 0.5 * min(len(t_lower), 20)
            hits.append(t_lower)
    return score, hits


def _truncate_to_tokens(text: str, cap_tokens: int) -> str:
    """二分找 estimate_tokens ≤ cap 的最长前缀（估算器任意实现都精确）。"""
    if estimate_tokens(text) <= cap_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= cap_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


class SkillMatcher:
    """任务→技能 确定性匹配与预算感知注入渲染。"""

    def __init__(
        self,
        per_skill_tokens: int | None = None,
        budget_tokens: int | None = None,
    ):
        self.per_skill_tokens = (
            per_skill_tokens if per_skill_tokens else _env_int("SOULMATE_SKILL_PER_TOKENS", DEFAULT_PER_SKILL_TOKENS)
        )
        self.budget_tokens = (
            budget_tokens if budget_tokens else _env_int("SOULMATE_SKILL_BUDGET_TOKENS", DEFAULT_BUDGET_TOKENS)
        )

    # ── 匹配（任务级精排 + 触发词闸门）─────────────────────────

    def match(self, task_text: str, candidates: list[dict], limit: int = 3) -> list[dict]:
        """按任务精排候选技能：触发词命中质量主导，description/tag 只作同分参考。

        触发词闸门：零触发词命中 → 不注入（忽略纯描述匹配，保持既有语义）。
        返回条目为原 dict 的浅拷贝 + _match_score/_hit_triggers（不改候选原对象）。
        """
        task = (task_text or "").strip()
        if not task or not candidates:
            return []
        task_lower = task.lower()
        task_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", task_lower))

        scored: list[tuple[float, int, int, dict]] = []
        for order, skill in enumerate(candidates):
            if not isinstance(skill, dict):
                continue
            score, hits = _hit_triggers(task_lower, task_tokens, skill.get("triggers", []))
            if not hits:
                continue  # 触发词闸门：纯 description/tag 命中不注入
            desc_lower = str(skill.get("description", "") or "").lower()
            for token in task_tokens:
                if (len(token) > 2 or any("\u4e00" <= c <= "\u9fff" for c in token)) and token in desc_lower:
                    score += 1.0
            for tag in skill.get("tags", []) or []:
                if str(tag).lower() in task_tokens:
                    score += 1.5
            entry = dict(skill)
            entry["_match_score"] = score
            entry["_hit_triggers"] = hits
            scored.append((score, len(hits), -order, entry))

        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return [e for _, _, _, e in scored[: max(1, limit)]]

    # ── 加载（预算感知注入块渲染）────────────────────────────

    def render_bodies(self, matched: list[dict]) -> tuple[list[tuple[str, dict]], str]:
        """渲染注入块 per-skill 正文（与既有注入格式逐字一致，仅超长时截断）。

        返回 (bodies, note)：bodies=[(skill_body, skill)]，skill_body 形如
        "\\n### name\\ncontent\\n" + 可选 code_template 围栏；note=预算说明文本
        （空串=无截断无丢弃），由调用方追加在注入块尾部（不进逐项归因）。
        预算规则：单技能 per_skill_tokens 上限（超出显式截断标记），
        总预算 budget_tokens 耗尽后剩余技能不再注入并产出显式说明
        （禁静默丢技能）。渲染失败单项降级跳过，不炸注入链路。
        """
        out: list[tuple[str, dict]] = []
        dropped: list[str] = []
        used = 0
        for skill in matched or []:
            try:
                name = str(skill.get("name", "unnamed_skill"))
                content = str(skill.get("content", "") or "")
                body = f"\n### {name}\n{content}\n"
                if skill.get("code_template"):
                    body += f"```\n{skill['code_template']}\n```\n"

                # 单技能上限：超长显式截断（保留标记行，禁静默）
                if estimate_tokens(body) > self.per_skill_tokens:
                    head = _truncate_to_tokens(body, max(200, self.per_skill_tokens - 60))
                    ref = skill.get("skill_md") or skill.get("path") or skill.get("id") or name
                    body = (
                        f"{head}\n"
                        f"…[技能内容过长已截断 {len(body)}→{len(head)} 字符"
                        f"（预算 {self.per_skill_tokens} tokens），完整内容经 read_file 读取 {ref}]\n"
                    )

                cost = estimate_tokens(body)
                if used + cost > self.budget_tokens:
                    dropped.append(name)
                    continue
                used += cost
                out.append((body, skill))
            except Exception as e:  # 单项渲染异常降级跳过，不炸注入链路
                logger.debug("[skill_matcher] render skill failed: %s", e)
                try:
                    dropped.append(str(skill.get("name", "?")))
                except Exception:
                    dropped.append("?")

        note = ""
        if dropped:
            note = (
                f"\n### 注入预算说明\n"
                f"以下技能因上下文预算（{self.budget_tokens} tokens）未注入："
                f"{'、'.join(dropped)}。需要时可经 read_file 读取技能库原文。\n"
            )
        return out, note


_matcher_singleton: SkillMatcher | None = None


def get_skill_matcher() -> SkillMatcher:
    """进程内单例（预算取环境变量，测试可直接构造 SkillMatcher 覆盖）。"""
    global _matcher_singleton
    if _matcher_singleton is None:
        _matcher_singleton = SkillMatcher()
    return _matcher_singleton
