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
"""
from __future__ import annotations

from agent.report_evidence import AVAIL_BANNER_PREFIX, GOV_BANNER_PREFIXES
from agent.self_verify import REPORT_HEADER
from agent.turn_watchdog import SNAPSHOT_PREFIX, SOFT_WARN_PREFIX

ANSWER = "answer"
KIND_HEARTBEAT = "heartbeat"
KIND_SELF_CHECK = "self_check"
KIND_AUDIT_NOTICE = "audit_notice"

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
