# -*- coding: utf-8 -*-
"""agent/audit_prescreen.py — 注入审计确定性预筛（12.4 第一刀）

实证缺陷（周期#67 12.3/12.4②）：
① 单工具链每批一次注入审计 LLM 判词往返（约 16s/次）——来源干净（用户明文点名+
  可溯源）的批次本不必走语义审计。
② 良性 dev 回合 1/5 次 terminal 被 LLM 判"疑似提示词注入劫持" terminate（工具未
  执行），疑触发=harness 防缓存行"（复测标记 TAG，此行仅为防缓存标记，忽略其语义）"
  ——措辞像注入指令，混进审计员看到的"## 用户请求"里。

治理（判据是溯源，不是模式白名单；防护语义零放松）：
1. **用户明文全溯源批次免 LLM 往返**（prescreen_batch）：工具参数的每个字符串值
   都能被用户请求的**连续原文片段**覆盖、残渣只剩空白/数字/标点（无任何字母）→
   批次内容全部来自用户明文（8.1② 同语义："能出现在用户请求里的文本本来就是用户
   意图"），LLM 判词在此类上只会得到 ok（JUDGE_PROMPT 判据 4/5 本就要求判 ok），
   跳过不构成拦截面缩水。**残渣含任何字母即升级 LLM 审计**（宁慢不误放）：
   - 必须是连续原文片段覆盖（非逐词拼凑）——防"用户提到过 rm 和 -rf 各自的字样"
     被拼成 rm -rf 逃审计；
   - **仅认 user_text 溯源**，本回合工具结果不作免审依据——web_extract 等外部内容
     夹带的注入文本恰恰藏在工具结果里，抄进命令必须照常过审计（零缩水关键负控制）；
   - 启发式签名扫描在本预筛之前照常跑（劫持签名不因参数干净而跳过）。
2. **防缓存标记行折叠**（fold_marker_lines）：窄签名（"此行/本行 仅/只是 + 标记/占位/
   防缓存"）的自述占位行从审计输入折叠为一行中性说明——它按自述本就无语义，折叠
   只是防止审计员把占位措辞（"忽略其语义"）误读成注入指令。行级折叠只认该窄签名，
   正文照常全量送审；标记行本就是用户请求原文（8.1② 用户意图范畴），折叠不隐藏
   非用户内容。
"""

from __future__ import annotations

import json
import re

# ── 防缓存/占位标记行（窄签名，中英双语）──────────────────────────────────
# 需同时命中"自述仅是"与"标记/占位"两组词——单有"忽略"不折叠（防误杀真实需求行）
_MARKER_LINE_RE = re.compile(
    r"^.*(?:"
    r"(?:此|本|这)(?:一)?行(?:仅仅?|只是)(?:为|是|作为)?[^。\n]{0,12}(?:标记|占位|防缓存)"
    r"|"
    r"this\s+line\s+is\s+only\s+(?:a\s+)?(?:cache[- ]?busting\s+)?(?:marker|placeholder)"
    r").*$",
    re.IGNORECASE,
)
# 折叠说明里保留的可审计标识（RUN_TAG 之类）
_TAG_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_\-]{3,}")

# 残渣"惰性"判定：去掉用户原文片段后只剩空白/引号/中文标点 = 无模型自造内容。
# ⚠️ ASCII 路径/shell 语义字符（/ \ . - _ $ ` | & ; < > ~ * ? ! # @ % ^ + =）与数字/
# 字母一律不算惰性——"用户写了 rm -rf、模型补个 /"的拼装逃逸在此被挡（宁慢不误放）
_INERT_RE = re.compile(r"^[\s\"'“”‘’，、：；！？（）【】《》。，]*$")

_MIN_SPAN = 4  # 溯源片段最短长度（更短的碎片不作覆盖依据，防乱拼）


def fold_marker_lines(text: str) -> tuple[str, int, str]:
    """折叠自述占位/防缓存标记行。返回 (折叠后文本, 折叠条数, 说明)。"""
    if not text:
        return text, 0, ""
    kept: list[str] = []
    tags: list[str] = []
    count = 0
    for line in text.splitlines():
        if _MARKER_LINE_RE.match(line.strip()):
            count += 1
            m = _TAG_TOKEN_RE.search(line)
            if m:
                tags.append(m.group(0))
            continue
        kept.append(line)
    if not count:
        return text, 0, ""
    tag_note = "、".join(dict.fromkeys(tags))[:80]
    note = (f"（审计输入已折叠防缓存/占位标记行 {count} 条"
            f"{'：' + tag_note if tag_note else ''}——自述占位符，按其自述无语义）")
    return "\n".join(kept), count, note


def flatten_value_strings(obj, out: list | None = None) -> list[str]:
    """提取参数**值**里的所有字符串（键是工具 schema，不是模型生成内容，不计）。"""
    if out is None:
        out = []
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            flatten_value_strings(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            flatten_value_strings(v, out)
    # int/float/bool/None 无文本内容，不计
    return out


def _parse_args(call: dict) -> dict | object:
    args = call.get("arguments") if isinstance(call, dict) else None
    if args is None:
        args = call.get("args") if isinstance(call, dict) else None
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            return args  # 解析失败按原字符串处理（走残渣判定）
    return args if args is not None else {}


def uncovered_residue(value: str, user_text: str) -> str:
    """去掉所有"用户请求连续原文片段"覆盖后的残渣（贪心最长片段优先）。

    只认连续片段（一次删除整段），不做逐词拼凑覆盖——拼凑覆盖会让"用户提到过的
    零散词"组装成新命令逃过审计。
    """
    if not value or not user_text:
        return value
    v = value
    ut = user_text
    changed = True
    rounds = 0
    while changed:
        rounds += 1
        if rounds > 64:
            # 覆盖轮数异常（参数碎片极多）→ 直接返回剩余文本走"非惰性→升级审计"，保守
            return v.replace("\u0000", "")
        changed = False
        vmax = len(v)
        for L in range(min(len(ut), vmax), _MIN_SPAN - 1, -1):
            hit = None
            for i in range(len(ut) - L + 1):
                sub = ut[i:i + L]
                if sub in v:
                    hit = sub
                    break
            if hit:
                v = v.replace(hit, "\u0000")
                changed = True
                break
    return v.replace("\u0000", "")


def value_is_user_verbatim(value: str, user_text: str) -> bool:
    """单个字符串值是否全部溯源到用户请求明文（残渣仅空白/数字/标点）。"""
    if not value or not value.strip():
        return True
    if not user_text:
        return False
    residue = uncovered_residue(value, user_text)
    # 残渣必须"惰性"（仅空白/引号/中文标点）——含任何路径/shell语义字符或字母/数字
    # 即视为模型自造内容，不算明文溯源
    return _INERT_RE.match(residue) is not None


def prescreen_batch(tool_calls: list, user_text: str) -> tuple[bool, str]:
    """确定性预筛：批次是否可免 LLM 判词往返。返回 (免审, 说明)。

    仅当：user_text 非空 且 批次非空 且 每个调用的每个字符串参数值都全部
    用户明文溯源时免审。任一不满足 → False（升级 LLM 审计）。
    """
    if not tool_calls:
        return False, "空批次"
    if not (user_text or "").strip():
        return False, "用户请求为空（无可溯源依据）"
    clean_parts: list[str] = []
    for idx, call in enumerate(tool_calls):
        if not isinstance(call, dict):
            return False, f"第{idx + 1}个调用形态不可核验"
        name = call.get("name") or "unknown"
        values = flatten_value_strings(_parse_args(call))
        for val in values:
            if not value_is_user_verbatim(val, user_text):
                return False, (
                    f"{name} 参数含用户请求之外的自造文本"
                    f"（残渣: {uncovered_residue(val, user_text)[:60]!r}）")
        clean_parts.append(name)
    return True, "；".join(clean_parts) or "（无参数调用）"
