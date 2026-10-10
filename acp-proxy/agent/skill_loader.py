"""agent/skill_loader.py — 技能检索与加载（路线图 6.2：按任务自动匹配并注入）

补齐 6.1 诚实边界里"按任务自动匹配注入"的真实缺口（本轮勘察实证三处）：
1. gene /skill/recommend 推荐结果只注入"可用技能: 名字"——描述/模板/成败率全部
   丢弃，LLM 看到名字不知道怎么做（"检索到了但没加载"）；
2. 任务管线只按用户原话匹配一次技能，步骤文本（如"解析PDF"）命中不到的技能
   永远注入不进来（步骤级重新匹配缺位）；
3. 注入面无预算——技能正文多长都全量塞系统提示（超出即膨胀上下文）。

职责（确定性，不走 LLM）：
- match_for_task：回合级检索，集中既有 gate 语义（短消息不匹配 / 文件发送请求
  跳过 / 只认触发词命中忽略纯描述匹配——pitfall 30 防误触发语义逐条保留）；
- match_for_step：步骤级重新检索 + 与回合级结果合并去重（步骤命中在前——当前
  步骤相关性优先）；
- render_local_section：本地 SkillManager 技能注入段（字节契约与历史内联实现
  一致，token 归因逐项记账靠返回的 per-skill body）；
- render_recommended：gene 推荐载荷 → 技能内容加载注入段（描述/模板/成功率/
  相关性），格式对齐 opensoul skill_learner.get_context_prompt；
- 预算：SOULMATE_SKILL_BUDGET_TOKENS（默认 1500）内截断自报（"指标不许说谎"，
  小技能文本绝不改动）。

可观测：stats 计数（匹配/注入/截断/推荐加载）。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

logger = logging.getLogger("agent.skill_loader")

try:
    from utils.token_manager import estimate_tokens
except Exception:  # pragma: no cover - 测试环境无acp-proxy根时降级
    def estimate_tokens(text: str) -> int:
        return (len(text) + 1) // 2 if text else 0

DEFAULT_MAX_SKILLS = 3
DEFAULT_BUDGET_TOKENS = 1500
_TRUNCATED_MARK = "…(技能内容过长，已截断)"

# 文件发送请求识别（Hermes 范式：LLM 直接输出 MEDIA 标签，跳过技能注入）
_FILE_NAME_RE = re.compile(r"[\w\-]+\.\w{1,5}")
_SEND_VERB_RE = re.compile(r"发给我|发送|发给|给我|下载|send|download")


class SkillLoader:
    """按任务自动匹配并加载技能（检索 + 注入面渲染一体）。"""

    def __init__(
        self,
        skill_manager: Any = None,
        max_skills: int = DEFAULT_MAX_SKILLS,
        budget_tokens: int | None = None,
    ):
        self._skill_manager = skill_manager  # None → 延迟构造生产 SkillManager
        self.max_skills = max(1, int(max_skills))
        self.budget_tokens = (
            int(budget_tokens)
            if budget_tokens is not None
            else int(os.environ.get("SOULMATE_SKILL_BUDGET_TOKENS", str(DEFAULT_BUDGET_TOKENS)))
        )
        self.stats = {
            "task_matches": 0,
            "step_matches": 0,
            "injections": 0,
            "recommended_loaded": 0,
            "truncations": 0,
        }

    # ── 检索 ─────────────────────────────────────────────

    def _manager(self) -> Any:
        if self._skill_manager is None:
            from skill_manager import SkillManager  # 延迟导入防环
            self._skill_manager = SkillManager()
        return self._skill_manager

    @staticmethod
    def _is_file_request(text: str) -> bool:
        return bool(_FILE_NAME_RE.search(text) and _SEND_VERB_RE.search(text))

    @staticmethod
    def _trigger_gate(text: str, skills: list[dict]) -> list[dict]:
        """只保留触发词命中（is_injectable_trigger 单一真源），忽略纯描述匹配。

        命中条目带 match_reason（可观测：为什么注入这个技能）。
        """
        from skill_manager import is_injectable_trigger
        t_lower = text.lower()
        out: list[dict] = []
        for s in skills or []:
            reasons = [
                t for t in s.get("triggers", [])
                if t and t.lower() in t_lower and is_injectable_trigger(t)
            ]
            if reasons:
                item = dict(s)
                item["match_reason"] = f"trigger:{reasons[0]}"
                out.append(item)
        return out

    def match_for_task(self, task_text: str) -> list[dict]:
        """回合级技能检索（gate 语义与历史内联实现逐条一致）。

        1. 短消息（≤10 字）不匹配（pitfall 30 技能误触发）；
        2. 文件发送请求跳过（MEDIA 标签范式）；
        3. 只认触发词命中，忽略纯描述匹配。
        """
        text = task_text or ""
        if len(text) <= 10:
            return []
        if self._is_file_request(text):
            return []
        hits = self._trigger_gate(text, self._manager().search_skills(text, limit=self.max_skills))
        self.stats["task_matches"] += 1
        return hits

    def match_for_step(self, step_text: str, base_matches: list[dict] | None = None) -> list[dict]:
        """步骤级重新检索（6.2 补缺：步骤"解析PDF"可命中用户原话没触发的技能）。

        与回合级 base 合并去重：步骤命中在前（当前步骤相关性优先），base 随后；
        封顶 max_skills。步骤文本天然短，不做长度门槛；文件发送请求同款跳过。
        """
        text = step_text or ""
        step_hits: list[dict] = []
        if text and not self._is_file_request(text):
            step_hits = self._trigger_gate(
                text, self._manager().search_skills(text, limit=self.max_skills)
            )
        self.stats["step_matches"] += 1
        return self.merge_matches(step_hits, base_matches or [])

    def merge_matches(self, first: list[dict], second: list[dict]) -> list[dict]:
        """合并去重（按 id/name），first 在前，封顶 max_skills。"""
        merged: list[dict] = []
        seen: set[str] = set()
        for s in list(first) + list(second):
            key = str(s.get("id") or s.get("name") or "")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(s)
        return merged[: self.max_skills]

    # ── 加载（注入面渲染） ────────────────────────────────

    @staticmethod
    def _shrink_to_tokens(text: str, budget: int) -> str:
        """确定性收缩文本到 token 预算内（保头去尾，estimate_tokens 单一真源）。"""
        if budget <= 0 or not text:
            return ""
        cut = max(1, int(budget / 1.5))  # 中文≈1.5tok/字的保守起点
        cut = min(cut, len(text))
        while cut > 1 and estimate_tokens(text[:cut]) > budget:
            cut = max(1, int(cut * 0.8))
        return text[:cut]

    def render_local_section(self, skills: list[dict]) -> tuple[str, list[tuple[str, str]]]:
        """本地 SkillManager 技能 → 系统提示注入段。

        字节契约（test_token_attribution_wiring 锁定）：单技能体 =
        f"\\n### {name}\\n{content}\\n" + (f"```\\n{code_template}\\n```\\n")。
        返回 (section_text, [(name, skill_body)])；skill_body 供逐项 token 归因。
        预算内截断自报；小技能文本绝不改动。
        """
        if not skills:
            return "", []
        header = "\n\n## 相关技能（参考以下经验执行任务）\n"
        section = header
        bodies: list[tuple[str, str]] = []
        used = estimate_tokens(header)
        budget = max(200, self.budget_tokens)
        for skill in list(skills)[: self.max_skills]:
            name = str(skill.get("name", "unnamed_skill"))
            content = str(skill.get("content") or "")
            tail = ""
            if skill.get("code_template"):
                tail = f"```\n{skill['code_template']}\n```\n"
            body = f"\n### {name}\n{content}\n"
            cost = estimate_tokens(body) + estimate_tokens(tail)
            if used + cost > budget:
                head = f"\n### {name}\n"
                keep = self._shrink_to_tokens(
                    content, budget - used - estimate_tokens(head) - estimate_tokens("\n")
                )
                body = f"{head}{keep}{_TRUNCATED_MARK}\n"
                tail = ""
                self.stats["truncations"] += 1
            full = body + tail
            section += full
            bodies.append((name, full))
            used += estimate_tokens(full)
        self.stats["injections"] += 1
        return section, bodies

    def render_recommended(self, skills: list[dict]) -> str:
        """gene /skill/recommend 载荷 → 技能内容加载注入段（6.2"加载"补缺）。

        此前只注入"可用技能: 名字"；现在加载 描述/模板/成功率/相关性，格式对齐
        opensoul skill_learner.get_context_prompt。字段容缺（载荷缺 code_template
        就只注描述——不猜、不编造）；预算内截断自报。
        """
        if not skills:
            return ""
        lines = ["## 相关技能（从历史执行中学习）"]
        used = 0
        budget = max(200, self.budget_tokens)
        for s in list(skills)[: self.max_skills]:
            name = str(s.get("name", "") or "")
            block = f"### {name}\n描述: {s.get('description', '') or ''}"
            tpl = s.get("code_template") or ""
            if tpl:
                block += f"\n模板:\n```\n{tpl}\n```"
            rate_bits = [f"使用 {s.get('usage_count', 0)} 次"]
            sr = s.get("success_rate")
            if isinstance(sr, (int, float)):
                rate_bits.append(f"成功率 {sr:.0%}")
            score = s.get("relevance_score")
            if isinstance(score, (int, float)):
                rate_bits.append(f"相关性 {score:.2f}")
            block += "\n" + "，".join(rate_bits) + "\n"
            cost = estimate_tokens(block)
            if used + cost > budget:
                block = self._shrink_to_tokens(block, budget - used) + _TRUNCATED_MARK + "\n"
                self.stats["truncations"] += 1
            lines.append(block)
            used += estimate_tokens(block)
        self.stats["recommended_loaded"] += 1
        return "\n".join(lines) + "\n"

    def snapshot(self) -> dict:
        """观测快照（计数器，不含业务数据）。"""
        return dict(self.stats)


def get_skill_loader() -> SkillLoader:
    """进程级单例（soulmate_agent 用属性 lazy-init，此入口供脚本/测试）。"""
    global _LOADER
    if _LOADER is None:
        _LOADER = SkillLoader()
    return _LOADER


_LOADER: SkillLoader | None = None
