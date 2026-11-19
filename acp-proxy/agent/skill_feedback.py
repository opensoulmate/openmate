"""agent/skill_feedback.py — 技能命中记账与反馈闭环（P0 技能自动命中③）

对标 OMH 路由的"情况语句"（situations："the plain phrases a user in that situation
would type"）+ 卷二 #17 召回包留痕理念：每次技能命中按真实结局记账三态——
命中（hits）/有用（useful）/没用（not_useful），并把任务原话沉淀为该技能的
情况语句（situations）。这些语句就是"同类任务第二次"的命中锚：run2 换说法也
能经锚词共享/语义相似命中 run1 学到的技能（近零摸索）。

三态口径（对齐 _report_skill_usage"诚实优先于记账"）：
- useful：回合成功（turn_outcome_success 单一真源）；
- not_useful：回合真实失败（结构化失败标记）；
- unknown：结局不可判（基础设施失败/中断收尾）——计 hit 但不稀释成败率。

反馈如何提升二次命中率：boost_for() 把 (useful-not_useful) 映射为打分加权
（Laplace 平滑，界 [-0.25, +0.35]），situations 供 skill_matcher 锚词/语义命中。
持久化 data/skill_hit_ledger.json（原子写 tmp+os.replace，线程锁）；
路径可经 SOULMATE_SKILL_LEDGER 覆盖（测试隔离）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("agent.skill_feedback")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
LEDGER_PATH = os.environ.get(
    "SOULMATE_SKILL_LEDGER", os.path.join(DATA_DIR, "skill_hit_ledger.json")
)

OUTCOME_USEFUL = "useful"
OUTCOME_NOT_USEFUL = "not_useful"
OUTCOME_UNKNOWN = "unknown"

MAX_SITUATIONS_PER_SKILL = 20
MAX_SITUATION_LEN = 80

# 情况语句锚词的通用停用表（"帮我/请/一下"这类虚词不是锚——负对照防线：
# 天气类闲聊与PDF任务共享"帮我"绝不许命中）。与 skill_manager._CJK_GENERIC_TRIGGERS
# 同哲学但面向任务短语。
_GENERIC_ANCHOR_TOKENS = frozenset({
    "帮我", "给我", "请问", "一下", "这个", "那个", "什么", "怎么", "如何", "可以",
    "需要", "帮我查", "看看", "告诉", "的说", "这些", "那些", "还是", "不是",
    "the", "and", "for", "you", "please", "help", "with", "this", "that",
    "what", "how", "can", "need", "want",
})

_TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def ngram_tokenize(text: str) -> list[str]:
    """轻量分词（skill_matcher 同源单一真源）：英文 ≥3 字符词 + 中文 2/3 字滑窗。

    中文最大连续段直接当词会导致换说法永不重叠（"整理成Excel表格"整段一个
    token），滑窗 n-gram 近似分词是锚词共享的前提。
    """
    t = (text or "").lower()
    tokens = _TOKEN_RE.findall(t)
    for run in _CJK_RUN_RE.findall(t):
        if len(run) == 1:
            continue
        if len(run) <= 3:
            tokens.append(run)
        for n in (2, 3):
            for i in range(0, len(run) - n + 1):
                tokens.append(run[i:i + n])
    return tokens


def anchor_tokens(text: str) -> list[str]:
    """任务短语的锚词：n-gram 分词后去通用停用词（"帮我/一下"不是锚）。"""
    out: list[str] = []
    seen: set[str] = set()
    for tok in ngram_tokenize(text or ""):
        if tok in _GENERIC_ANCHOR_TOKENS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def situation_phrase(task_text: str) -> str:
    """任务原话 → 情况语句（压空白、截长，保留原词序——锚词来自原文）。"""
    t = re.sub(r"\s+", " ", (task_text or "").strip())
    return t[:MAX_SITUATION_LEN]


class SkillFeedbackLedger:
    """技能命中账本：三态记账 + 情况语句沉淀 + 打分增益。"""

    def __init__(self, path: str | None = None):
        self.path = path or LEDGER_PATH
        self._lock = threading.Lock()
        self._data: dict | None = None

    # ── 持久化 ────────────────────────────────────────────

    def _load(self) -> dict:
        if self._data is None:
            try:
                with open(self.path, encoding="utf-8") as fp:
                    data = json.load(fp)
                if not isinstance(data, dict) or "skills" not in data:
                    raise ValueError("bad ledger shape")
            except FileNotFoundError:
                data = {"version": 1, "skills": {}}
            except Exception as e:
                logger.warning("skill hit ledger unreadable (%s), starting fresh", e)
                data = {"version": 1, "skills": {}}
            self._data = data
        return self._data

    def _save(self) -> None:
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            tmp = f"{self.path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(self._data, fp, ensure_ascii=False, indent=1)
            os.replace(tmp, self.path)
        except Exception as e:  # 记账失败不阻塞回合收尾（旁路纪律）
            logger.debug("skill hit ledger save failed: %s", e)

    # ── 记账 ──────────────────────────────────────────────

    def record_hit(
        self,
        skill_id: str,
        task_text: str,
        outcome: str = OUTCOME_UNKNOWN,
        skill_name: str = "",
    ) -> dict:
        """记一次技能命中：hits+1、按 outcome 计入有用/没用、沉淀情况语句。

        outcome 只认三态枚举，其余一律按 unknown（fail-closed，不猜）。
        返回本次记账后的技能账目（副本）。
        """
        if not skill_id:
            return {}
        if outcome not in (OUTCOME_USEFUL, OUTCOME_NOT_USEFUL, OUTCOME_UNKNOWN):
            outcome = OUTCOME_UNKNOWN
        with self._lock:
            data = self._load()
            entry = data["skills"].setdefault(
                skill_id,
                {
                    "name": skill_name or "",
                    "hits": 0,
                    "useful": 0,
                    "not_useful": 0,
                    "unknown": 0,
                    "situations": [],
                    "last_outcome": None,
                    "updated_at": None,
                },
            )
            if skill_name and not entry.get("name"):
                entry["name"] = skill_name
            entry["hits"] += 1
            entry[outcome] += 1
            entry["last_outcome"] = outcome
            entry["updated_at"] = datetime.now(timezone.utc).isoformat()
            phrase = situation_phrase(task_text)
            if phrase:
                situations: list[str] = entry["situations"]
                if phrase not in situations:
                    situations.append(phrase)
                    if len(situations) > MAX_SITUATIONS_PER_SKILL:
                        del situations[0]
            self._save()
            return dict(entry)

    # ── 查询（打分/命中用） ─────────────────────────────────

    def entry_for(self, skill_id: str) -> dict:
        with self._lock:
            return dict(self._load()["skills"].get(skill_id, {}))

    def situations_for(self, skill_id: str) -> list[str]:
        with self._lock:
            return list(self._load()["skills"].get(skill_id, {}).get("situations", []))

    def boost_for(self, skill_id: str) -> float:
        """反馈增益 ∈ [-0.25, +0.35]：Laplace 平滑 (useful-not_useful)/(hits+2)。

        有用越多越先注入、没用多的压后——反馈直接抬二次命中率（命中目标top-k）。
        无账目=0（不猜）。unknown 不进分子分母的成败率，但占 hits（诚实计数）。
        """
        entry = self.entry_for(skill_id)
        hits = int(entry.get("hits", 0))
        if hits <= 0:
            return 0.0
        judged = int(entry.get("useful", 0)) + int(entry.get("not_useful", 0))
        if judged <= 0:
            return 0.0
        raw = (int(entry.get("useful", 0)) - int(entry.get("not_useful", 0))) / (hits + 2)
        return max(-0.25, min(0.35, raw * 2.0))

    def has_evidence(self, skill_id: str) -> bool:
        """技能是否有真实任务证据（≥1 次命中 + ≥1 条情况语句）。

        语义放行的前置条件（OMH "Ranking is not admission"：入候选还要过锚词
        共享+前置条件）——无证据技能绝不语义放行，负对照防线。
        """
        entry = self.entry_for(skill_id)
        return int(entry.get("hits", 0)) > 0 and bool(entry.get("situations"))

    def anchor_overlap(self, skill_id: str, task_text: str) -> list[str]:
        """任务锚词与该技能情况语句锚词的交集（命中即返回交集词，可观测）。"""
        task_anchors = set(anchor_tokens(task_text))
        if not task_anchors:
            return []
        shared: list[str] = []
        for phrase in self.situations_for(skill_id):
            for tok in anchor_tokens(phrase):
                if tok in task_anchors and tok not in shared:
                    shared.append(tok)
        return shared

    def snapshot(self) -> dict:
        """观测快照：技能数/总命中/成败计数（不含业务正文）。"""
        with self._lock:
            data = self._load()
        skills = data.get("skills", {})
        return {
            "skills_tracked": len(skills),
            "hits_total": sum(int(e.get("hits", 0)) for e in skills.values()),
            "useful_total": sum(int(e.get("useful", 0)) for e in skills.values()),
            "not_useful_total": sum(int(e.get("not_useful", 0)) for e in skills.values()),
            "unknown_total": sum(int(e.get("unknown", 0)) for e in skills.values()),
        }


_LEDGER: SkillFeedbackLedger | None = None


def get_ledger() -> SkillFeedbackLedger:
    """进程级单例（soulmate_agent lazy-init；测试可自行构造隔离实例）。"""
    global _LEDGER
    if _LEDGER is None:
        _LEDGER = SkillFeedbackLedger()
    return _LEDGER
