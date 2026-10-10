# -*- coding: utf-8 -*-
"""⚡8.5c-⑤ 采集侧帧分类 — WS 回复采集按帧来源（turn_origin/帧类型）过滤。

问题（8.5c-①遗留，周期#43 STYLE85C_1 实证）：回答帧与系统帧走同一个
agent_message_chunk 通道（ACP ContentChunk 无 metadata 字段，wire 上不携带
turn_origin）——turn_watchdog 心跳快照、self_verify 需求自检推送、report_evidence
治理横幅被采集侧并进回复文本："简短一句话"回答的合并视图被 ⏳ 快照/🔍 自检粘连
污染（周期#43：需求自检报告复述需求原文混入主答区 → 正确回答被误判负）。

帧分类（确定性帧首前缀判别；标记常量从生产者模块 import 单一真源——生产者改
措辞不改常量，测试即红）：
- heartbeat      turn_watchdog 心跳快照/软预算提醒（纯状态帧 → 整帧丢弃）
- self_check     self_verify 需求自检报告（纯报告帧 → 整帧丢弃）
- audit_notice   report_evidence 治理横幅/可用性更正声明，两类形态：
                 ①纯横幅帧（独立 session_update 推送）→ 整帧丢弃
                 ②横幅+正文同帧（amend_* 前置横幅后的非流式整发）→ 只剥横幅块
                 保留正文（banner + "\\n\\n" + body 的生产者契约，首个空行即分界）
其余 = 回答帧（回答文本、🛑 停止提示/轮次耗尽提示等 full_response 的组成部分，
属回答语义"失败必须可见"，不过滤；📋 任务拆解/🔧 步骤类管线进度行视为回答帧——
只认帧首前缀，正文中间出现同字样不误伤）。

⚡周期#47 引擎chrome横幅（轨迹采集同源粘连实证）：CLI 引擎（hermes acp 等）的
启动横幅/工具清单 ASCII 框线块与回答走同一 agent_message_chunk 通道——relay
trajectory 把它并进 llm_call content（真实库实证：content[:500] 全被
"╭─── Hermes Agent v0.15.2 … Available Tools …" 框线占满，真实回答被完全遮蔽）。
strip_engine_chrome 在合并文本开头剥这类 chrome 块（╭ 顶边到 ╰ 底边的框线块），
窄签名=块内须命中已知 chrome 标记（"Available Tools"/"Agent v"/"upstream"），
含框线的正文/ASCII 图不误伤；只剥开头，正文中间的框线不动。
"""
from __future__ import annotations

import re

from agent.report_evidence import AVAIL_BANNER_PREFIX, GOV_BANNER_PREFIXES
from agent.self_verify import REPORT_HEADER
from agent.turn_watchdog import SNAPSHOT_PREFIX, SOFT_WARN_PREFIX

ANSWER = "answer"
KIND_HEARTBEAT = "heartbeat"
KIND_SELF_CHECK = "self_check"
KIND_AUDIT_NOTICE = "audit_notice"
KIND_ENGINE_CHROME = "engine_chrome"

# 帧首前缀 → 帧来源（顺序即优先级；均为生产者输出契约的字面前缀）
_PREFIXES: dict[str, tuple[str, ...]] = {
    KIND_HEARTBEAT: (SNAPSHOT_PREFIX, SOFT_WARN_PREFIX),
    KIND_SELF_CHECK: (REPORT_HEADER,),
    KIND_AUDIT_NOTICE: (AVAIL_BANNER_PREFIX,) + tuple(GOV_BANNER_PREFIXES),
}

# audit_notice 横幅块与正文的分界（amend_response/amend_availability 契约）
_BANNER_BODY_SEP = "\n\n"


def origin_of(text: str) -> str:
    """帧来源分类：系统帧返回 kind，回答帧返回 "answer"。

    只判帧首（lstrip 前导空白后）——正文中间出现标记字样不误伤。
    """
    head = (text or "").lstrip()
    for kind, prefixes in _PREFIXES.items():
        if any(head.startswith(p) for p in prefixes):
            return kind
    return ANSWER


def _split_banner_frame(text: str) -> str | None:
    """audit_notice 帧拆横幅块与正文。

    纯横幅帧 → None（整帧丢弃）；横幅+正文同帧 → 返回正文（横幅块剥除）。
    """
    head = (text or "").lstrip()
    _banner, sep, body = head.partition(_BANNER_BODY_SEP)
    return body if sep else None


def split_frames(texts: list[str]) -> tuple[str, dict[str, int]]:
    """合并回复文本按帧来源过滤。

    返回 (回答文本, 系统帧计数 {kind: n})；计数是可观测性（剥了什么、剥了几帧
    一眼可查），空文本帧忽略不计。
    """
    kept: list[str] = []
    counts: dict[str, int] = {}
    for raw in texts or []:
        if not raw or not raw.strip():
            continue
        kind = origin_of(raw)
        if kind == ANSWER:
            kept.append(raw)
            continue
        if kind == KIND_AUDIT_NOTICE:
            body = _split_banner_frame(raw)
            if body and body.strip():
                kept.append(body)  # 横幅+正文同帧：只剥横幅块，正文归回答
        counts[kind] = counts.get(kind, 0) + 1
    return "".join(kept), counts


# 引擎chrome横幅（hermes acp 启动横幅/工具清单 ASCII 框线块，真实库实证形状）
_CHROME_TOP = "╭"
# 框线块内行首字符（顶边/侧边/分隔/底边 + 竖排框线变体）
_CHROME_LINE_RE = re.compile(r"^[│┃║├╠╰╚┼╬─━═⠿⣿⣀⣁⣠⣤⣴⣶⣷⣿⡀⡁⡂⡄⡅⡆⡇]")
# 窄签名：块内须命中已知chrome标记（防误杀含框线的正文/ASCII图）
_CHROME_MARKERS = ("Available Tools", "Agent v", "upstream")
# 底边行：╰───…───╯（其后可能粘着回答——chunk流无分隔符拼接）
_CHROME_BOTTOM_RE = re.compile(r"^[╰╚][─━═]*[╯╝]")


def strip_engine_chrome(text: str) -> tuple[str, int]:
    """剥合并文本开头的引擎chrome横幅块。返回 (正文, 剥除块数 0|1)。

    只认开头（lead前导空白之后以 ╭ 起），块边界= ╰ 底边行或首个非框线行；
    窄签名=块内命中 _CHROME_MARKERS 之一，否则原样返回（正文里的框线图不误伤）。
    """
    if not (text or "").strip():
        return text, 0
    lead = len(text) - len(text.lstrip())
    body = text[lead:]
    lines = body.split("\n")
    if not lines or not lines[0].startswith(_CHROME_TOP):
        return text, 0
    end = None
    glue_tail = ""
    for i in range(1, len(lines)):
        ln = lines[i]
        if ln.startswith("╰") or ln.startswith("╚"):
            end = i + 1
            # 底边行可能粘着回答（chunk流无分隔符拼接，实证形态）：
            # "╰───╯回答…" —— 边框后的尾巴归回答，不吞
            m = _CHROME_BOTTOM_RE.match(ln)
            if m:
                glue_tail = ln[m.end():]
            break
        if not _CHROME_LINE_RE.match(ln):
            end = i
            break
    if end is None:
        end = len(lines)
    block = "\n".join(lines[:end])
    if not any(m in block for m in _CHROME_MARKERS):
        return text, 0
    rest_lines = ([glue_tail] if glue_tail else []) + lines[end:]
    rest = "\n".join(rest_lines)
    return rest.lstrip("\n"), 1
