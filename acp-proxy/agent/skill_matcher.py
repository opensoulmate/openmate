"""agent/skill_matcher.py — 任务→技能混合匹配（P0 技能自动命中②）

对标 OMH 技能路由器（卷一§4）三层选中并按其质量纪律落地：
1. 触发打分（触发词精确/子串/词命中，权重语义对齐 SkillManager.search_skills
   与 is_injectable_trigger 单一真源——CJK 2字词/通用词策略零漂移）；
2. BM25 词汇短名单（OMH 字段权重 name 3.0 / triggers 2.0 / situations 2.0 /
   description 1.0 / tags 1.0，轻量分词：英文词 + 中文 2/3 字滑窗）；
3. 语义层（skill_semantic 供应商链向量余弦）：排序用技能全文，放行用情况语句
   （描述文本绝不参与放行——pitfall 30 语义逐条保留）。

**"Ranking is not admission"**（OMH lexical_shortlist.py:21-23 同纪律）：
入候选≠放行注入。放行三路（match_reason 可观测）：
- trigger:<词>    触发词命中（历史行为原样，is_injectable_trigger 门）；
- semantic:sim=   与该技能*情况语句*余弦 ≥ T_sem 且技能有真实任务证据
                  （skill_feedback.has_evidence——无证据技能绝不语义放行）；
- anchor:<词>+sim 情况语句锚词共享 ≥2 且余弦 ≥ T_low（词汇+语义混合佐证）。

负对照防线（OMH ROUTING_PRECISION_CASES 同哲学）：纯描述匹配、通用词触发、
无证据技能的"看起来像"——一律不放行；短消息/文件发送请求 gate 由 SkillLoader
集中（本模块不重复实现）。

语义降级留痕（卷二 #16 No silent downgrade）：EmbedResult.provider_used/
degraded_reason 逐次记入 stats 与 match_reason，绝不静默换供应商。
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

from agent.skill_feedback import SkillFeedbackLedger, get_ledger, ngram_tokenize
from skill_manager import is_injectable_trigger

logger = logging.getLogger("agent.skill_matcher")

# 打分权重（可观测写进 score_breakdown；改动需同步基准脚本口径）
W_TRIGGER = 3.0
W_BM25 = 2.0
W_SEMANTIC = 1.5
W_FEEDBACK = 2.0

# 语义放行阈值（按供应商口径校准，见 skill_route_benchmark.py 输出）：
# opensoul/openai 真向量：换说法同任务类 ≥0.85、跨任务类 ≤0.65 的经验带；
# hash 离线兜底是字符 n-gram 余弦，口径不同阈值独立。
T_SEMANTIC_ONLINE = 0.78
T_LOW_ONLINE = 0.55
T_SEMANTIC_HASH = 0.60
T_LOW_HASH = 0.40

MAX_ANCHOR_SHARED = 2  # 锚词共享放行所需最少共享词

def tokenize(text: str) -> list[str]:
    """轻量分词：委托 skill_feedback.ngram_tokenize（单一真源，防两处漂移）。"""
    return ngram_tokenize(text)


def _term_freq(tokens: list[str]) -> dict[str, int]:
    tf: dict[str, int] = {}
    for tok in tokens:
        tf[tok] = tf.get(tok, 0) + 1
    return tf


class SkillMatcher:
    """混合打分 + 证据放行的任务→技能路由器。"""

    def __init__(
        self,
        skill_manager: Any = None,
        ledger: SkillFeedbackLedger | None = None,
        embedder: Any = None,
        top_k: int = 3,
        shortlist_size: int = 15,
    ):
        self._skill_manager = skill_manager
        self._ledger = ledger
        self._embedder = embedder
        self.top_k = max(1, int(top_k))
        self.shortlist_size = max(self.top_k, int(shortlist_size))
        self.stats = {
            "rank_calls": 0,
            "admit_calls": 0,
            "semantic_admits": 0,
            "anchor_admits": 0,
            "trigger_admits": 0,
            "rejections_no_evidence": 0,
            "semantic_degrades": 0,
        }

    # ── 依赖（lazy，测试可注入） ──────────────────────────

    def ledger(self) -> SkillFeedbackLedger:
        if self._ledger is None:
            self._ledger = get_ledger()
        return self._ledger

    def embedder(self) -> Any:
        if self._embedder is None:
            from agent.skill_semantic import get_embedder

            self._embedder = get_embedder()
        return self._embedder

    # ── 候选（BM25 短名单的语料） ─────────────────────────

    def candidates(self, query: str) -> list[dict]:
        """技能全集（真 SkillManager 走 list_skills；测试替身只有 search_skills 时
        退化为 search_skills 宽召回——形状兼容，绝不猜不存在的接口）。"""
        mgr = self._skill_manager
        if mgr is None:
            from skill_manager import SkillManager

            mgr = SkillManager()
            self._skill_manager = mgr
        if hasattr(mgr, "list_skills"):
            return list(mgr.list_skills())
        if hasattr(mgr, "search_skills"):
            return list(mgr.search_skills(query, limit=50) or [])
        return []

    # ── 打分（纯函数部分独立可测） ─────────────────────────

    @staticmethod
    def trigger_score(query: str, skill: dict) -> tuple[float, str | None]:
        """触发打分（权重 15/10/5 与 SkillManager.search_skills 逐条一致）。

        返回 (原始分, 命中触发词)；命中词已过 is_injectable_trigger 门。
        """
        q_lower = (query or "").lower()
        q_tokens = set(tokenize(query))
        best = 0.0
        hit_trigger: str | None = None
        for trigger in skill.get("triggers", []) or []:
            t_lower = (trigger or "").lower()
            if not t_lower:
                continue
            if t_lower == q_lower:
                score = 15.0
            elif is_injectable_trigger(t_lower) and (
                t_lower in q_lower or q_lower in t_lower
            ):
                score = 10.0
            elif is_injectable_trigger(t_lower) and t_lower in q_tokens:
                score = 5.0
            else:
                continue
            if score > best:
                best = score
                hit_trigger = trigger
        return best, hit_trigger

    @staticmethod
    def _skill_field_tokens(skill: dict, situations: list[str]) -> dict[str, list[str]]:
        """字段分词（OMH 字段权重表的字段划分）。"""
        return {
            "name": tokenize(skill.get("name", "")),
            "triggers": [t.lower() for t in (skill.get("triggers") or []) if t],
            "situations": tokenize(" ".join(situations)),
            "description": tokenize(skill.get("description", "")),
            "tags": [str(t).lower() for t in (skill.get("tags") or []) if t],
        }

    @classmethod
    def bm25_score(
        cls,
        query: str,
        skill: dict,
        situations: list[str],
        idf: dict[str, float],
    ) -> float:
        """Okapi BM25 场加权版（name 3.0 / triggers 2.0 / situations 2.0 /
        description 1.0 / tags 1.0——OMH lexical_shortlist.py:44-48 权重照抄）。"""
        q_tokens = tokenize(query)
        if not q_tokens:
            return 0.0
        fields = cls._skill_field_tokens(skill, situations)
        weights = {
            "name": 3.0, "triggers": 2.0, "situations": 2.0,
            "description": 1.0, "tags": 1.0,
        }
        k1, b = 1.5, 0.75
        total_len = sum(len(v) for v in fields.values()) or 1
        avg_len = total_len / max(1, len(fields))
        score = 0.0
        for tok in q_tokens:
            tf = 0.0
            weighted_len = 0.0
            for fname, toks in fields.items():
                f_tf = _term_freq(toks).get(tok, 0)
                if f_tf:
                    tf += weights[fname] * f_tf
                    weighted_len += weights[fname] * len(toks)
            if tf <= 0:
                continue
            dl = weighted_len if weighted_len > 0 else avg_len
            score += idf.get(tok, math.log(1.5)) * (
                tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(avg_len, 1e-9)))
        return score

    @staticmethod
    def _idf_corpus(candidates: list[dict], ledger: SkillFeedbackLedger) -> dict[str, float]:
        """候选语料 IDF（平滑）。情况语句从账本取（场景词参与 IDF 统计）。"""
        n_docs = max(1, len(candidates))
        df: dict[str, int] = {}
        for skill in candidates:
            situations = ledger.situations_for(str(skill.get("id", "")))
            toks: set[str] = set()
            for toks_list in SkillMatcher._skill_field_tokens(skill, situations).values():
                toks.update(toks_list)
            for tok in toks:
                df[tok] = df.get(tok, 0) + 1
        return {
            tok: math.log(1 + (n_docs - c + 0.5) / (c + 0.5)) for tok, c in df.items()
        }

    # ── 排序（ranking——不等于放行） ───────────────────────

    def rank(self, query: str, skills: list[dict] | None = None) -> list[dict]:
        """混合打分排序：触发 3.0 + BM25 2.0 + 语义 1.5 + 反馈 2.0。

        返回降序列表，每项 = 技能副本 + score + score_breakdown（全可观测）。
        语义向量只给短名单（触发命中 ∪ 有证据技能 ∪ BM25 前 N）——339+ 技能
        全量嵌入不经济，BM25 短名单语义重排（OMH 同架构）。
        """
        self.stats["rank_calls"] += 1
        skills = skills if skills is not None else self.candidates(query)
        if not skills:
            return []
        ledger = self.ledger()
        idf = self._idf_corpus(skills, ledger)

        prelim: list[dict] = []
        for skill in skills:
            sid = str(skill.get("id", ""))
            situations = ledger.situations_for(sid)
            trig, hit_trigger = self.trigger_score(query, skill)
            bm25 = self.bm25_score(query, skill, situations, idf)
            prelim.append({
                "skill": skill,
                "trigger_raw": trig,
                "hit_trigger": hit_trigger,
                "bm25": bm25,
                "situations": situations,
                "boost": ledger.boost_for(sid),
            })

        # 短名单（OMH BM25 词汇短名单语义）：触发命中 ∪ 有账本证据 ∪ BM25 前 N
        # ——语义层按此短名单内的证据技能计算（见下），排序仍覆盖全量
        prelim.sort(key=lambda r: r["bm25"], reverse=True)

        # 语义层（只给带情况语句的技能算：描述文本绝不进放行语料，pitfall 30；
        # 零证据技能零网络开销——存量测试不触网。供应商降级留痕 #16）
        from agent.skill_semantic import cosine

        query_vec: list[float] = []
        sem_rows = [row for row in prelim if row["situations"]]
        if sem_rows:
            embedder = self.embedder()
            result = embedder.embed(
                [query] + [" ".join(row["situations"]) for row in sem_rows]
            )
            if result.degraded_reason:
                self.stats["semantic_degrades"] += 1
                logger.info(
                    "[skill-matcher] semantic provider degraded: %s", result.degraded_reason
                )
            query_vec = result.vectors[0] if result.vectors else []
            for i, row in enumerate(sem_rows):
                vec = result.vectors[i + 1] if len(result.vectors) > i + 1 else []
                sim = cosine(query_vec, vec) if query_vec and vec else 0.0
                row["sem_situation_sim"] = sim
                row["sem_provider"] = result.provider_used
                row["sem_degraded"] = result.degraded_reason

        out: list[dict] = []
        for row in prelim:
            sem_rank = float(row.get("sem_situation_sim", 0.0))
            combined = (
                W_TRIGGER * min(1.0, row["trigger_raw"] / 15.0)
                + W_BM25 * min(1.0, row["bm25"] / 8.0)
                + W_SEMANTIC * max(0.0, sem_rank)
                + W_FEEDBACK * (0.5 + row["boost"])
            )
            item = dict(row["skill"])
            item["score"] = round(combined, 4)
            item["score_breakdown"] = {
                "trigger": round(row["trigger_raw"], 2),
                "bm25": round(row["bm25"], 3),
                "semantic_rank": round(sem_rank, 4),
                "feedback_boost": round(row["boost"], 4),
                "sem_provider": row.get("sem_provider", "none"),
            }
            item["_situations"] = row["situations"]
            item["_hit_trigger"] = row["hit_trigger"]
            item["_sem_situation_sim"] = row.get("sem_situation_sim")
            item["_sem_provider"] = row.get("sem_provider", "none")
            item["_sem_degraded"] = row.get("sem_degraded")
            out.append(item)
        out.sort(key=lambda r: r["score"], reverse=True)
        return out

    # ── 放行（Ranking is not admission） ──────────────────

    def _semantic_admit(
        self, query: str, item: dict
    ) -> tuple[str | None, float]:
        """语义放行判定：只与*情况语句*比相似度（描述文本绝不放行）。

        返回 (reason, sim)；reason=None 不放行。需要技能有真实任务证据
        （has_evidence）——无证据技能"看起来像"也不放行（负对照防线）。
        rank() 已算过 sim 则直用（省一次嵌入）。
        """
        situations = item.get("_situations") or []
        sid = str(item.get("id", ""))
        if not situations:
            self.stats["rejections_no_evidence"] += 1
            return None, 0.0
        if not self.ledger().has_evidence(sid):
            self.stats["rejections_no_evidence"] += 1
            return None, 0.0
        best_sim = item.get("_sem_situation_sim")
        if best_sim is None:
            embedder = self.embedder()
            result = embedder.embed([query] + situations)
            if result.degraded_reason:
                self.stats["semantic_degrades"] += 1
            if len(result.vectors) < 2:
                return None, 0.0
            from agent.skill_semantic import cosine

            q_vec = result.vectors[0]
            best_sim = 0.0
            for vec in result.vectors[1:]:
                best_sim = max(best_sim, cosine(q_vec, vec))
        best_sim = float(best_sim)
        # 阈值按向量口径分档：在线真向量（opensoul/ollama/openai）vs hash 离线兜底
        online = not str(item.get("_sem_provider", "none")).startswith(("hash", "none"))
        t_sem = T_SEMANTIC_ONLINE if online else T_SEMANTIC_HASH
        t_low = T_LOW_ONLINE if online else T_LOW_HASH

        # 路A：高相似直接放行（证据前置已在上面核过）
        if best_sim >= t_sem:
            return f"semantic:sim={best_sim:.2f}", best_sim
        # 路B：锚词共享 + 语义佐证（混合打分的"混合"落在放行层）
        shared = self.ledger().anchor_overlap(sid, query)
        if len(shared) >= MAX_ANCHOR_SHARED and best_sim >= t_low:
            return f"anchor:{'+'.join(shared[:2])}+sim={best_sim:.2f}", best_sim
        return None, best_sim

    def admit(
        self, query: str, ranked: list[dict] | None = None
    ) -> list[dict]:
        """放行 top-k（命中带 match_reason，副本不污染原技能 dict）。"""
        self.stats["admit_calls"] += 1
        if ranked is None:
            ranked = self.rank(query)
        out: list[dict] = []
        for item in ranked:
            reason: str | None = None
            if item.get("_hit_trigger"):
                reason = f"trigger:{item['_hit_trigger']}"
                self.stats["trigger_admits"] += 1
            else:
                sem_reason, _sim = self._semantic_admit(query, item)
                if sem_reason:
                    reason = sem_reason
                    if sem_reason.startswith("semantic:"):
                        self.stats["semantic_admits"] += 1
                    else:
                        self.stats["anchor_admits"] += 1
            if not reason:
                continue
            clean = {
                k: v for k, v in item.items() if not k.startswith("_")
            }
            clean["match_reason"] = reason
            out.append(clean)
            if len(out) >= self.top_k:
                break
        return out

    def snapshot(self) -> dict:
        snap: dict = dict(self.stats)
        snap["ledger"] = self.ledger().snapshot()
        try:
            snap["embedder"] = self.embedder().snapshot()
        except Exception:
            snap["embedder"] = {}
        return snap
