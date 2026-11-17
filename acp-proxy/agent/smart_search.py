# -*- coding: utf-8 -*-
"""SoulMate 智能搜索编排（下载中心搜索面的语义智能层，2026-11-17）。

用户钉死：**搜索=SoulMate 智能搜索（语义相关性，非字面匹配）**。本模块是
SoulMate 侧的智能编排，把「帮我找XX」的自然语言一路带到可入任务的精选候选：

    自然语言意图理解 → 查询改写（可多路查询）→ 后端 /search 取多源候选
    → LLM 语义相关性筛选与排序（判定「跟用户要的东西真相关」，丢弃科普页/
      不相干项/同名不同物）→ 呈现精选结果（有歧义先问「哪一部/哪个版本」）
    → 候选可直接 download_add 入下载任务

分层铁律（对齐 download_tools.py 的单一真源约定）：
- 搜索/下载业务逻辑（抓源/适配器/缓存/逐源降级）只在 opensoul
  src/api/download_search.py；本模块只做**编排**（查询改写/语义筛选排序/
  呈现决策），候选一律来自后端 REST /search（经 fetch 注入，转发归
  download_tools.DownloadTools.search_candidates），绝不自抓源。底层候选源
  （bing_magnet 等）的字面相关性过滤在源侧，本层把它当黑盒源用。
- **确定性过滤=免费地板**：词面匹配（查询词∩标题，NFKC+大小写不敏感）永远
  先跑、零成本可复现；LLM 未配置/超时/输出坏 JSON 时降级只用它（宁缺毋滥丢
  词面全不相干项）。LLM 在场时地板退居取窗排序（地板分高者优先进入 LLM 语义
  筛选窗，跨语言/同义标题不因词面 0 分被硬丢）。
- LLM 层任何失败必须可见（notes 显式报因），绝不静默吞，也绝不因 LLM 缺席
  退回纯字面堆砌的原始候选。

接口（download_tools.torrent_search 消费）：
    engine = SmartSearchEngine(llm_fn=None, fetch=...)
    result = await engine.run("最新有什么好的科幻电影帮我找一下", limit=20)
    result.items       # 精选候选 = 后端行 + _smart{score,reason,layer,kind} + _add{url,extract_code}
    result.dropped     # [{title, reason, layer}] 被丢弃候选（如实列因）
    result.clarify     # 歧义追问（非空=先问「哪一部/哪个版本」再加任务）
    result.queries     # 实际执行的多路查询（改写可观测）
    result.llm_used    # False=只跑了免费地板（LLM 缺席/失败的确定性降级路径）
    result.notes       # 降级/说明文案（显式报因禁静默）
    result.source_errors / result.fatal  # 后端逐源降级 / 整面失败报因

llm_fn: async (messages: list[dict]) -> str —— SoulMate 注入
        agent.soulmate_agent.SoulMateAgent._smart_search_llm（真实
        LLMEngine.chat + 辅助 LLM 落账），测试注入桩；None=免费地板模式
        （零 LLM 依赖）。
fetch:  async (query, category, limit) -> (rows, source_errors, err)
        —— download_tools.DownloadTools.search_candidates（REST 转发仍归它）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

MAX_QUERIES = 3          # 改写查询路数上限（多路召回，不放量打爆源）
LLM_WINDOW = 40          # 进入 LLM 语义筛选的候选窗上限（控 token）
REWRITE_TIMEOUT_S = 30.0
SEMANTIC_TIMEOUT_S = 60.0
MAX_SHOWN_CANDIDATES = 40  # 语义筛选提示词里单条候选标题截断（控 token）

_FETCH_T = Callable[..., Awaitable[tuple[list, list, str]]]
_LLM_T = Callable[[list], Awaitable[str]]

# ─── 确定性免费地板（词面相关性下限，零 LLM 成本）───────────────────────────

_ASCII_WORD_RE = re.compile(r"[a-z0-9]+")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


def _norm(text: str) -> str:
    """NFKC（全角→半角）+ casefold + 空白折叠（子串匹配的基础）。"""
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def _query_keys(query: str) -> set[str]:
    """查询 → 词面匹配键集：ASCII 词（len>=2）+ CJK 连续段整段与全部 2-gram。

    2-gram 让「三体的有声书」也能命中「三体全集有声小说」（三体/有声）；
    地板语义是「任意键命中即保留」（宁多勿漏），真正的语义判别交给 LLM 层。
    """
    q = _norm(query)
    keys = {w for w in _ASCII_WORD_RE.findall(q) if len(w) >= 2}
    for run in _CJK_RUN_RE.findall(q):
        if len(run) >= 2:
            keys.add(run)
            keys.update(run[i : i + 2] for i in range(len(run) - 1))
    return keys


def _floor_score(row: dict, keys: set[str]) -> int:
    """候选词面得分 = 标题命中的查询键个数（0=词面全不相干）。"""
    if not keys:
        return 1  # 无键可判 → 不筛（地板没长眼睛时不冒充有）
    title = _norm(str(row.get("title") or row.get("name") or ""))
    return sum(1 for k in keys if k in title)


# ─── LLM JSON 容错解析 ──────────────────────────────────────────────────────


def _llm_json(text: Any):
    """LLM 输出 → dict/list/None。去代码围栏、截首个 JSON 块；失败 None 不猜。"""
    if not isinstance(text, str):
        return None
    t = text.strip()
    if not t or t.startswith("[LLM错误"):
        return None
    m = re.search(r"[\{\[].*[\}\]]", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001 — 坏 JSON 走确定性降级，不抛
        return None


def _add_of(row: dict) -> dict:
    """候选 → download_add 可直接消费的入任务参数（契约 url|magnet|share_url）。"""
    share_url = str(row.get("share_url") or row.get("shareUrl") or "")
    if share_url:
        d = {"url": share_url}
        code = str(row.get("extract_code") or row.get("pwd") or "")
        if code:
            d["extract_code"] = code
        return d
    magnet = str(row.get("magnet") or "")
    if magnet:
        return {"url": magnet}
    url = str(row.get("url") or row.get("torrentUrl") or row.get("torrent_url")
              or row.get("torrent") or "")
    return {"url": url} if url else {}


# ─── 提示词（语义层单一真源；改动=改判定标准，须同步验收）──────────────────

_REWRITE_SYSTEM = (
    "你是下载资源搜索的查询规划器。把用户的找资源诉求理解成意图，并改写成发给"
    "资源搜索后端的关键词查询。\n"
    "规则：\n"
    "- 改写 1-3 条查询，每条只放 1-4 个最可能出现在资源标题里的硬限定关键词"
    "（作品名/作者/版本/媒体形态/年份，如「科幻电影」「三体 有声书」「三体 "
    "有声小说」），词与词之间用空格分隔。后端做「标题必须全部包含这些词」的"
    "字面过滤——宁可多路短查询，绝不要一条长查询（词越多几乎越搜不到）。"
    "去掉口语废话（帮我/找一下/最新有什么好的）。\n"
    "- 不同写法/别名/全半角可多路覆盖（如「三体的有声书」→「三体 有声书」"
    "「三体 有声小说」「三体 广播剧」）。\n"
    "- 若诉求指向多个明显不同的目标（哪一部/哪个版本没说清），clarify 写一句"
    "中文追问（「你指的是哪一部/哪个版本？」风格）；否则空字符串。\n"
    "- 只输出 JSON，不要任何其它文字：\n"
    '{"intent":"一句话意图","category_hint":"video|audio|books|software|game|other",'
    '"queries":["查询1","查询2"],"clarify":""}'
)

_SEMANTIC_SYSTEM = (
    "你是资源搜索结果的语义相关性筛选器。用户想找资源下载，逐条判定候选是否"
    "「跟用户要的东西真相关」：\n"
    "- relevant（保留）：指向用户要的作品/版本/媒体形态。kind=resource 是可下载"
    "资源（磁力/种子/网盘分享/直链）；kind=web 是主题相关网页（介绍/收听/推荐页），"
    "不是直接资源但确实关于用户要的东西。\n"
    "- irrelevant（丢弃）：科普/百科页（如「XX是什么含义」「XX_百度百科」）、"
    "广告站、同名不同物、题材不相干项——它们不是用户要的东西本身。\n"
    "- 每个候选编号必须在 relevant 或 irrelevant 中恰好出现一次（全覆盖，不许"
    "漏判）；score=0-100 相关度，reason ≤20 字中文。\n"
    "- 若 relevant 里存在多个不同作品/版本且用户没说清要哪部/哪版，clarify 写"
    "一句「哪一部/哪个版本」风格的中文追问；否则空字符串。\n"
    "- 只输出 JSON，不要任何其它文字：\n"
    '{"relevant":[{"i":1,"score":95,"kind":"resource","reason":"..."}],'
    '"irrelevant":[{"i":2,"reason":"..."}],"clarify":""}'
)


@dataclass
class SmartPlan:
    """①意图理解+查询改写 的产物。"""

    intent: str = ""
    category_hint: str = ""
    queries: list = field(default_factory=list)
    clarify: str = ""


@dataclass
class SmartResult:
    """整条智能链的产物（download_tools 渲染层消费）。"""

    items: list = field(default_factory=list)       # 精选候选（后端行+_smart+_add）
    dropped: list = field(default_factory=list)     # [{title, reason, layer}]
    queries: list = field(default_factory=list)     # 实际执行的多路查询
    intent: str = ""
    clarify: str = ""
    llm_used: bool = False
    notes: list = field(default_factory=list)
    source_errors: list = field(default_factory=list)   # [{source, error}]
    fatal: str = ""                                  # 整面失败报因（非空且无结果时原样上抛）


class SmartSearchEngine:
    """SoulMate 智能搜索编排（意图→改写→取候选→免费地板→LLM 语义筛选→精选）。"""

    def __init__(self, llm_fn: _LLM_T | None = None, fetch: _FETCH_T | None = None):
        self.llm_fn = llm_fn
        self.fetch = fetch

    # ── ① 意图理解 + 查询改写（多路查询）────────────────────────────

    async def _rewrite(self, query: str, category: str | None) -> tuple[SmartPlan, str]:
        llm = self.llm_fn
        if llm is None:
            return SmartPlan(queries=[query]), ""
        user = f"用户原话: {query}"
        if category:
            user += f"\n分类线索: {category}"
        messages = [{"role": "system", "content": _REWRITE_SYSTEM},
                    {"role": "user", "content": user}]
        try:
            text = await asyncio.wait_for(llm(messages), REWRITE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 — 降级原词单路，失败可见
            return (SmartPlan(queries=[query]),
                    f"查询改写 LLM 不可用（{type(e).__name__}: {e}）"
                    "——按原词单路查询（免费地板降级）")
        data = _llm_json(text)
        if not isinstance(data, dict):
            snippet = (str(text or "")[:60]).replace("\n", " ")
            return (SmartPlan(queries=[query]),
                    f"查询改写 LLM 输出非 JSON（{snippet}）"
                    "——按原词单路查询（免费地板降级）")
        queries: list = []
        for q in data.get("queries") or []:
            q = str(q or "").strip()
            if q and q not in queries:
                queries.append(q)
        plan = SmartPlan(
            intent=str(data.get("intent") or "").strip(),
            category_hint=str(data.get("category_hint") or "").strip(),
            queries=(queries or [query])[:MAX_QUERIES],
            clarify=str(data.get("clarify") or "").strip(),
        )
        return plan, ""

    # ── ② 多路取候选（后端 /search，逐路并发）────────────────────────

    async def _fetch_all(self, queries: list, category: str | None, limit: int):
        """→ (merged_rows, source_errors, fatal)。按查询声明序合并去重保序。"""

        async def one(q: str):
            try:
                rows, errs, err = await self.fetch(q, category, limit)
            except Exception as e:  # noqa: BLE001 — 单路失败不拖垮其它路
                return [], [], f"{type(e).__name__}: {e}"
            return list(rows or []), [e for e in (errs or []) if isinstance(e, dict)], err or ""

        results = await asyncio.gather(*[one(q) for q in queries])
        merged: list = []
        seen: set = set()
        src_errors: list = []
        err_seen: set = set()
        fatals: list = []
        for rows, errs, err in results:
            for r in rows:
                sig = (r.get("title"), r.get("url") or r.get("magnet")
                       or r.get("share_url"))
                if sig in seen:
                    continue
                seen.add(sig)
                merged.append(r)
            for e in errs:  # 多路查询的同源同错去重（同一源挂只报一次）
                sig = (e.get("source"), e.get("error"))
                if sig in err_seen:
                    continue
                err_seen.add(sig)
                src_errors.append(e)
            if err:
                fatals.append(err)
        return merged, src_errors, (fatals[0] if fatals else "")

    # ── ③ LLM 语义相关性筛选与排序 ─────────────────────────────────

    def _window(self, rows: list) -> list:
        """地板分高者优先进 LLM 筛选窗（稳定排序，控 token）。"""
        scored = sorted(rows, key=lambda r: -r.get("_floor", 0))
        return scored[:LLM_WINDOW]

    async def _semantic(self, query: str, plan: SmartPlan, window: list):
        """→ (relevant, irrelevant, clarify, note)；失败 → (None, None, "", note)。"""
        llm = self.llm_fn
        if llm is None:
            return None, None, "", "LLM 语义筛选未配置——已降级免费地板（确定性词面过滤）"
        lines = []
        for i, r in enumerate(window, 1):
            title = str(r.get("title") or r.get("name") or "(无标题)")
            title = title[:MAX_SHOWN_CANDIDATES]
            src = str(r.get("source") or "?")
            kind_hint = "（标注仅相关网页）" if r.get("web_only") else ""
            lines.append(f"{i}. {title} | 源 {src}{kind_hint}")
        user = (f"用户想下载的资源: {query}\n"
                + (f"意图: {plan.intent}\n" if plan.intent else "")
                + f"候选（编号. 标题 | 源）:\n" + "\n".join(lines))
        messages = [{"role": "system", "content": _SEMANTIC_SYSTEM},
                    {"role": "user", "content": user}]
        try:
            text = await asyncio.wait_for(llm(messages), SEMANTIC_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001
            return None, None, "", (f"LLM 语义筛选不可用（{type(e).__name__}: {e}）"
                                    "——已降级免费地板（确定性词面过滤）")
        data = _llm_json(text)
        if not isinstance(data, dict):
            snippet = (str(text or "")[:60]).replace("\n", " ")
            return None, None, "", (f"LLM 语义筛选输出非 JSON（{snippet}）"
                                    "——已降级免费地板（确定性词面过滤）")
        relevant, seen = [], set()
        for v in data.get("relevant") or []:
            if not isinstance(v, dict):
                continue
            raw_i = v.get("i")
            if raw_i is None:
                continue
            try:
                idx = int(raw_i)
            except Exception:  # noqa: BLE001
                continue
            if not 1 <= idx <= len(window) or idx in seen:
                continue
            seen.add(idx)
            try:
                score = float(v.get("score", 50))
            except Exception:  # noqa: BLE001
                score = 50.0
            kind = str(v.get("kind") or "").strip().lower()
            if kind not in ("resource", "web"):
                kind = "web" if window[idx - 1].get("web_only") else "resource"
            relevant.append({"i": idx, "score": max(0.0, min(100.0, score)),
                             "reason": str(v.get("reason") or "").strip(),
                             "kind": kind})
        irrelevant = []
        for v in data.get("irrelevant") or []:
            if not isinstance(v, dict):
                continue
            raw_i = v.get("i")
            if raw_i is None:
                continue
            try:
                idx = int(raw_i)
            except Exception:  # noqa: BLE001
                continue
            if not 1 <= idx <= len(window) or idx in seen:
                continue
            seen.add(idx)
            irrelevant.append({"i": idx,
                               "reason": str(v.get("reason") or "").strip()})
        clarify = str(data.get("clarify") or "").strip()
        return relevant, irrelevant, clarify, ""

    # ── 主链 ─────────────────────────────────────────────────────────

    async def run(self, query: str, category: str | None = None,
                  limit: int = 20) -> SmartResult:
        query = str(query or "").strip()
        limit = max(1, min(int(limit or 20), 100))
        res = SmartResult()
        if not query:
            res.fatal = "错误: query 参数不能为空"
            return res

        # ① 意图理解 + 查询改写（多路查询）
        plan, note = await self._rewrite(query, category)
        if note:
            res.notes.append(note)
        res.intent = plan.intent
        res.queries = plan.queries
        res.clarify = plan.clarify

        # ② 多路取候选（后端 /search）
        rows, res.source_errors, res.fatal = await self._fetch_all(
            plan.queries, category, limit)
        if not rows:
            return res

        # ③ 免费地板：词面打分（永远先跑，零 LLM 成本）
        keys = set()
        for q in [query] + list(plan.queries):
            keys |= _query_keys(q)
        for r in rows:
            r["_floor"] = _floor_score(r, keys)

        kept_floor = [r for r in rows if r["_floor"] > 0]
        floor_dropped = [{"title": str(r.get("title") or "(无标题)"),
                          "reason": "词面全不相干（免费地板过滤）",
                          "layer": "floor"} for r in rows if r["_floor"] <= 0]
        if not kept_floor and rows:
            # 地板词面零命中：过滤器没长眼睛时不冒充有——放宽为原始候选并如实标注
            kept_floor = list(rows)
            floor_dropped = []
            res.notes.append("确定性地板词面零命中（查询与标题无词面交集）"
                             "——已放宽为原始候选（未过滤，仅供参考）")

        # ④ LLM 语义相关性筛选与排序（缺席/失败 → 免费地板路径）
        relevant = irrelevant = None
        clarify = ""
        window = self._window(kept_floor)
        if self.llm_fn is not None and kept_floor:
            relevant, irrelevant, clarify, note = await self._semantic(
                query, plan, window)
            if note:
                res.notes.append(note)

        if relevant is not None:
            res.llm_used = True
            in_window = {}
            for v in relevant:
                in_window[v["i"]] = v
            for v in irrelevant:
                in_window[v["i"]] = None  # 语义层判丢
            dropped = []
            kept = []
            for i, row in enumerate(window, 1):
                v = in_window.get(i, "unjudged")
                if v == "unjudged":
                    dropped.append({"title": str(row.get("title") or "(无标题)"),
                                    "reason": "语义层未确认相关", "layer": "llm"})
                elif v is None:
                    dropped.append({"title": str(row.get("title") or "(无标题)"),
                                    "reason": next(
                                        (x["reason"] for x in (irrelevant or [])
                                         if x["i"] == i), "语义层判定不相关"),
                                    "layer": "llm"})
                else:
                    row["_smart"] = {"score": v["score"], "reason": v["reason"],
                                     "layer": "llm", "kind": v["kind"]}
                    row["_add"] = _add_of(row)
                    kept.append(row)
            # 窗外行（地板分低，未进 LLM 筛选）如实记因，不冒充语义判定
            in_ids = {id(r) for r in window}
            over = [r for r in kept_floor if id(r) not in in_ids]
            res.dropped = dropped + [
                {"title": str(r.get("title") or "(无标题)"),
                 "reason": "超出语义筛选窗（词面分低未评）", "layer": "llm"}
                for r in over]
            res.dropped.extend(floor_dropped)
            kept.sort(key=lambda r: -(r.get("_smart") or {}).get("score", 0))
            # 不截断：精选全量入 res.items，展示上限由渲染层「N 个结果（展示前 M 条）」如实自报
            res.items = kept
            if clarify:
                res.clarify = clarify
        else:
            # 免费地板路径（LLM 缺席/失败）：词面过滤 + 稳定排序
            for r in kept_floor:
                r["_smart"] = {"score": r["_floor"],
                               "reason": f"词面命中 {r['_floor']} 个查询词（免费地板）",
                               "layer": "floor",
                               "kind": "web" if r.get("web_only") else "resource"}
                r["_add"] = _add_of(r)
            kept_floor.sort(key=lambda r: -r["_floor"])  # 稳定排序，同分保序
            res.items = kept_floor
            res.dropped = floor_dropped
        return res
