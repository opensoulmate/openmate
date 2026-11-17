# -*- coding: utf-8 -*-
"""fact_lock.py — C1 全局事实锁（Global Truth Lock），SoulMate 能力层纯函数库。

通用长文写作能力（可研报告/申报书/解决方案/合同/白皮书/课程讲义/标书全场景）：
写多章节长文前先生成**只读《全局事实锁》**（核心数字锁——工期/质保/响应时效/
人数/金额；技术路线锁——框架/语言/部署架构；命名实体锁——甲方简称/系统全称/
自称口径），人工确认（confirmed_by）后注入写作 System Prompt 顶部；生成后
**跨章节一致性校验器**逐章核对锁值出现且一致，矛盾即点名（章节+行号+原文）。

头号死穴（走量 SaaS"上下文失忆症"）：第 2 章质保 3 年 vs 第 6 章 1 年 →
前后打架。本模块提供确定性校验（无 LLM 参与）：
- conflict    锁点名附近出现与锁值不同的断言值（硬错误，定位到行）
- multi_value 同一 key 全文绑定了 ≥2 个不同值（硬错误，列全部出处）
- missing     key 在全文被提及但锁值从未随 key 出现（警告级——值被架空）

事实锁工件 schema（对齐路线图 C1 落地规格）：
{"locked_facts": [{"key", "value", "scope", "source", "confirmed_by", "aliases"?}]}

纯函数、无 I/O、无 LLM；独立可测（tests/test_fact_lock.py）。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = [
    "REQUIRED_FIELDS",
    "validate_lock",
    "build_lock",
    "prompt_block",
    "check_document",
]

REQUIRED_FIELDS = ("key", "value", "scope", "source", "confirmed_by")

# 数值+单位断言（单位按长词优先；None=无单位）
_NUM_UNIT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*"
    r"(日历天|个月|小时|分钟|万元|亿元|%|折|天|年|月|周|人|次|台|套|项|个|元|万|亿|分)?")
# 文本值断言标记（"系统全称：X"/"为X"/"是X"）——无标记的 key 提及不构成断言
_ASSERT_MARK_RE = re.compile(r"[:：=]|为|是|叫")
# 锁点名后的观察窗：到句读/并列分隔/括注为止，上限 40 字——
# 保证窗口内数值只绑定当前 key（"工期90日历天，质保期为3年"互不串门）
_WINDOW_END_RE = re.compile(r"[。；;！!？?\n，,、（(【［]")
_BEFORE_CHARS = 12          # 值在 key 前（"90日历天的工期"）的回看窗口
_AFTER_MAX_CHARS = 40


# ── 工件与校验 ───────────────────────────────────────────────────────

def validate_lock(lock) -> list[str]:
    """校验事实锁工件，返回错误清单（空=合法）。"""
    errs: list[str] = []
    if not isinstance(lock, dict):
        return ["lock 必须是对象 {locked_facts: [...]}"]
    facts = lock.get("locked_facts")
    if not isinstance(facts, list) or not facts:
        return ["lock.locked_facts 需要非空列表"]
    seen: set[str] = set()
    for i, f in enumerate(facts):
        if not isinstance(f, dict):
            errs.append(f"locked_facts[{i}] 必须是对象")
            continue
        for k in REQUIRED_FIELDS:
            if not str(f.get(k, "") or "").strip():
                errs.append(f"locked_facts[{i}] 缺必填字段 {k}"
                            f"（{'/'.join(REQUIRED_FIELDS)}）")
        key = str(f.get("key", "") or "").strip()
        if key:
            if key in seen:
                errs.append(f"locked_facts[{i}] key 重复: {key!r}")
            seen.add(key)
        aliases = f.get("aliases")
        if aliases is not None and (
                not isinstance(aliases, list)
                or not all(isinstance(a, str) and a.strip() for a in aliases)):
            errs.append(f"locked_facts[{i}].aliases 须为非空字符串列表")
    return errs


def build_lock(facts: list) -> dict:
    """从事实列表构建事实锁工件（校验不过抛 ValueError）。"""
    lock = {"locked_facts": [dict(f) for f in facts or []]}
    errs = validate_lock(lock)
    if errs:
        raise ValueError("事实锁非法：" + "；".join(errs))
    return lock


def prompt_block(lock: dict) -> str:
    """生成《全局事实锁》只读块——注入写作 System Prompt 顶部。"""
    errs = validate_lock(lock)
    if errs:
        raise ValueError("事实锁非法：" + "；".join(errs))
    lines = [
        "【全局事实锁·只读】以下事实已人工确认，全文任何章节必须逐字一致引用，"
        "禁止改写、禁止另立口径；与锁冲突的内容一律以锁为准，发现无法兼容时停下报冲突：",
    ]
    for f in lock["locked_facts"]:
        aliases = f.get("aliases") or []
        alias_txt = f"（又称：{'/'.join(aliases)}）" if aliases else ""
        lines.append(
            f"- 【{f['key']}】{f['value']}{alias_txt}"
            f"　｜范围：{f['scope']}｜来源：{f['source']}｜确认人：{f['confirmed_by']}")
    lines.append(
        "写作纪律：承诺类数字（工期/质保/报价/人数/响应时效等）只准出自本锁；"
        "本锁之外的数字/事实无来源不写（缺料登记留白，禁编造）。")
    return "\n".join(lines)


# ── 跨章节一致性校验器 ───────────────────────────────────────────────

def _normalize_text(s: str) -> str:
    """1:1 归一（保偏移）：全角数字/点→半角。定位/摘录必须走原文偏移。"""
    return "".join(
        chr(ord(ch) - 0xFEE0) if ("０" <= ch <= "９" or ch == "．") else ch
        for ch in s)


def _squash(s: str) -> str:
    """包含性匹配用归一：1:1 归一后去全部空白。"""
    return re.sub(r"\s+", "", _normalize_text(s))


def _parse_value(value: str):
    """锁值解析：(数值, 单位) 或 None（非数值文本值）。"""
    m = _NUM_UNIT_RE.fullmatch(_squash(value))
    if not m:
        return None
    try:
        num = Decimal(m.group(1))
    except InvalidOperation:
        return None
    return num, (m.group(2) or "")


def _iter_key_mentions(text: str, keys: list[str]):
    """逐 key（含别名）枚举出现位置（1:1 归一文本偏移 = 原文偏移）。

    同位重叠去重保留最长（"合同工期"与内嵌"工期"只算一次断言）。
    """
    norm = _normalize_text(text)
    found: list[tuple[int, int, str]] = []
    for key in keys:
        nk = _squash(key)
        if not nk:
            continue
        start = 0
        while True:
            pos = norm.find(nk, start)
            if pos < 0:
                break
            found.append((pos, pos + len(nk), key))
            start = pos + max(len(nk), 1)
    found.sort(key=lambda t: (t[0], -(t[1] - t[0])))
    last_end = -1
    for s, e, k in found:
        if s < last_end:
            continue
        last_end = e
        yield s, e, k


def _extract_tokens(window: str):
    """窗口内取全部 数值[+单位] 断言（如 90日历天 / 13356305.2元 / 13%）。"""
    toks = []
    for m in _NUM_UNIT_RE.finditer(_normalize_text(window)):
        try:
            num = Decimal(m.group(1))
        except InvalidOperation:
            continue
        toks.append((num, m.group(2) or "", m.group(0)))
    return toks


def _token_matches(tok, value_parsed) -> bool:
    if value_parsed is None:      # 文本值：窗口含该值即命中
        return False
    vnum, vunit = value_parsed
    num, unit, _ = tok
    return num == vnum and unit == vunit


def check_document(lock: dict, chapters: list) -> dict:
    """跨章节一致性校验：锁值出现且一致，矛盾即点名（章节+行号+原文摘录）。

    chapters: [{"id", "title", "text"}]（按文档顺序）
    返回 {"ok": bool, "issues": [...], "checked_facts": N, "checked_chapters": M}
    issue: {"type": conflict|multi_value|missing, "level": error|warning,
            "key", "locked_value", "chapter_id", "title", "line", "excerpt",
            "found"?/values"?}
    """
    errs = validate_lock(lock)
    if errs:
        raise ValueError("事实锁非法：" + "；".join(errs))
    if not isinstance(chapters, list) or not chapters:
        raise ValueError("chapters 需要非空列表 [{id, title, text}]")
    for i, ch in enumerate(chapters):
        if not isinstance(ch, dict) or "text" not in ch:
            raise ValueError(f"chapters[{i}] 必须是对象且含 text")

    issues: list[dict] = []
    for fact in lock["locked_facts"]:
        key = fact["key"]
        value = fact["value"]
        keys = [key] + list(fact.get("aliases") or [])
        value_parsed = _parse_value(value)
        matched_locs: list[dict] = []
        seen_values: dict[str, dict] = {}   # 归一值 → {"display", "locs": [...]}

        for ch in chapters:
            text = str(ch.get("text", "") or "")
            ch_id = str(ch.get("id", "") or "")
            ch_title = str(ch.get("title", "") or "")
            for start, end, used_key in _iter_key_mentions(text, keys):
                # 值在 key 前（"90日历天的工期"）/ 后（"工期为90日历天"）双窗，
                # 前窗回切到最近句读（防"…120日历天。质保期…"串窗）
                raw_before = text[max(0, start - _BEFORE_CHARS):start]
                cut_b = None
                for m_b in _WINDOW_END_RE.finditer(raw_before):
                    cut_b = m_b
                before = raw_before[cut_b.end():] if cut_b else raw_before
                after_end = min(len(text), end + _AFTER_MAX_CHARS)
                cut = _WINDOW_END_RE.search(text, end)
                if cut:
                    after_end = min(after_end, cut.start())
                after = text[end:after_end]
                tokens = _extract_tokens(before) + _extract_tokens(after)
                excerpt_src = (before + text[start:end] + after).strip()
                excerpt = excerpt_src if len(excerpt_src) <= 60 else excerpt_src[:57] + "…"
                line = text[:start].count("\n") + 1
                loc = {"chapter_id": ch_id, "title": ch_title,
                       "line": line, "excerpt": excerpt}
                if value_parsed is None:
                    # 文本值：仅"key：X/为X/是X"式断言参与校验
                    if not _ASSERT_MARK_RE.search(after):
                        continue
                    window = _squash(after)
                    if _squash(value) in window:
                        matched_locs.append(dict(loc))
                        entry = seen_values.setdefault(
                            _squash(value), {"display": value, "locs": []})
                        entry["locs"].append(dict(loc))
                    else:
                        issues.append({
                            "type": "conflict", "level": "error",
                            "key": key, "used_key": used_key,
                            "locked_value": value,
                            "found": [after.strip()[:30]],
                            **loc,
                        })
                    continue
                if not tokens:
                    continue        # key 提及但无数值断言（"工期见商务部分"）→ 不判
                hit = False
                for tok in tokens:
                    display = tok[2]
                    entry = seen_values.setdefault(
                        _squash(display), {"display": display, "locs": []})
                    entry["locs"].append(dict(loc))
                    if _token_matches(tok, value_parsed):
                        hit = True
                if hit:
                    matched_locs.append(dict(loc))
                else:
                    issues.append({
                        "type": "conflict", "level": "error",
                        "key": key, "used_key": used_key,
                        "locked_value": value,
                        "found": [t[2] for t in tokens],
                        **loc,
                    })

        # multi_value：同一 key 全文 ≥2 个不同值（含锁值自身算 1 个）
        distinct = list(seen_values.values())
        values_in_doc = {_squash(value)} if matched_locs else set()
        values_in_doc.update(_squash(v["display"]) for v in distinct)
        if len(values_in_doc) > 1:
            issues.append({
                "type": "multi_value", "level": "error",
                "key": key, "locked_value": value,
                "values": [
                    {"value": v["display"], "locs": v["locs"][:5]}
                    for v in distinct],
            })
        # missing：key 全文被提及（有数值断言）但锁值从未随 key 出现
        if not matched_locs and distinct:
            issues.append({
                "type": "missing", "level": "warning",
                "key": key, "locked_value": value,
                "hint": f"全文提及 {key} 处均未出现锁值（锁值被架空/未写入正文）",
                "chapter_id": "", "title": "", "line": 0, "excerpt": "",
            })

    ok = not any(i["level"] == "error" for i in issues)
    return {
        "ok": ok,
        "issues": issues,
        "checked_facts": len(lock["locked_facts"]),
        "checked_chapters": len(chapters),
    }


def format_report(result: dict) -> str:
    """人读校验报告：冲突即点名（含定位）。"""
    if result.get("ok") and not result.get("issues"):
        return (f"✅ 事实锁一致性通过：{result['checked_facts']} 条锁值 × "
                f"{result['checked_chapters']} 章节，无冲突")
    lines = []
    if result.get("ok"):
        lines.append("⚠️ 事实锁校验：有警告（无硬冲突）")
    else:
        lines.append("❌ 事实锁一致性校验未过——矛盾点名如下：")
    for i in result.get("issues", []):
        loc = ""
        if i.get("line"):
            loc = f"｜{i.get('chapter_id') or '-'}《{i.get('title') or '-'}》第{i['line']}行"
        if i["type"] == "conflict":
            lines.append(
                f"  [冲突] {i['key']} 锁值「{i['locked_value']}」{loc}"
                f" 实际断言 {i['found']}｜原文：{i['excerpt']}")
        elif i["type"] == "multi_value":
            vals = "；".join(
                f"{v['value']}（{len(v['locs'])} 处，首处 "
                f"{v['locs'][0].get('chapter_id', '')}第{v['locs'][0].get('line', 0)}行）"
                for v in i.get("values", []))
            lines.append(f"  [多值] {i['key']} 全文出现多个值：{vals}（锁值「{i['locked_value']}」）")
        else:
            lines.append(f"  [缺失] {i['key']}：{i.get('hint', '')}")
    return "\n".join(lines)
