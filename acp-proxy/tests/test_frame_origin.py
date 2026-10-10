# -*- coding: utf-8 -*-
"""⚡8.5c-⑤ 采集侧帧分类 — WS 回复采集按帧来源过滤 测试。

覆盖：
1. 心跳快照/软预算提醒 → heartbeat（真实 TurnWatchdog 输出钉死，含常量真源）
2. 需求自检报告 → self_check（真实 SelfVerifier.format_report 输出钉死，
   含补做后缀形态）
3. 治理横幅（blocked/unsupported 两态）/可用性更正声明 → audit_notice
   （真实 report_evidence banner 输出钉死）
4. 纯横幅帧整帧丢弃；横幅+正文同帧只剥横幅块保留正文（banner+空行+body 契约）
5. 回答帧不误伤：正文中间含标记字样/管线进度行按设计归回答帧
6. split_frames 混合帧：只留回答+正文、计数可观测、空帧忽略
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.frame_origin import (
    ANSWER,
    KIND_AUDIT_NOTICE,
    KIND_HEARTBEAT,
    KIND_SELF_CHECK,
    origin_of,
    split_frames,
)
from agent.self_verify import REPORT_HEADER, SelfVerifier, VerifyItem, VerifyReport
from agent.turn_watchdog import SNAPSHOT_PREFIX, SOFT_WARN_PREFIX, TurnWatchdog
import agent.report_evidence as re_ev


# ── 1. 心跳帧（真实生产者输出）────────────────────────────────────

def test_heartbeat_snapshot_classified():
    wd = TurnWatchdog("s1")
    wd.beat(round_index=2, max_rounds=15, tools_executed=3, last_tool="schedule_task")
    text = wd.status_text()
    assert text.startswith(SNAPSHOT_PREFIX)  # 常量=生产者输出真源
    assert origin_of(text) == KIND_HEARTBEAT


def test_heartbeat_soft_warn_classified():
    async def main():
        msgs = []

        async def notify(t):
            msgs.append(t)

        wd = TurnWatchdog("s1", notify=notify, heartbeat_s=999,
                          soft_budget_s=1, max_lifetime_s=999)
        wd._started_at = time.monotonic() - 5
        await wd._fire_once(5.0)
        return msgs

    msgs = asyncio.run(main())
    assert msgs and msgs[0].startswith(SOFT_WARN_PREFIX)  # 常量=生产者输出真源
    assert all(origin_of(m) == KIND_HEARTBEAT for m in msgs)


# ── 2. 需求自检报告（真实生产者输出）──────────────────────────────

def _sample_report() -> VerifyReport:
    return VerifyReport(
        items=[
            VerifyItem("创建 dev_hello.py", "met", 1.0, "write_file 执行", False),
            VerifyItem("运行它", "uncertain", 0.5, "", False),
        ],
        coverage=0.5,
    )


def test_self_check_report_classified():
    text = SelfVerifier().format_report(_sample_report())
    assert text.startswith(REPORT_HEADER)  # 常量=生产者输出真源
    assert origin_of(text) == KIND_SELF_CHECK


def test_self_check_report_with_remediation_suffix_classified():
    text = SelfVerifier().format_report(_sample_report()) + "\n（未覆盖项已补做1轮并复检）"
    assert origin_of(text) == KIND_SELF_CHECK


# ── 3. 治理横幅/可用性更正（真实生产者输出）────────────────────────

def test_gov_banner_unsupported_classified():
    a = re_ev.EvidenceAssessment(
        action_required=True, claims=True, verdict="unsupported")
    banner = re_ev.governance_banner(a)
    assert banner and origin_of(banner) == KIND_AUDIT_NOTICE


def test_gov_banner_blocked_classified():
    log = [{"function": {"name": "terminal", "arguments": "{}"},
            "result_preview": "OpenSoul权限引擎要求人工确认", "permission": "deny"}]
    a = re_ev.assess("帮我运行一个命令", "任务执行报告：已完成。", log)
    assert a.verdict == "blocked"
    banner = re_ev.governance_banner(a)
    assert "⛔" in banner and origin_of(banner) == KIND_AUDIT_NOTICE


def test_availability_banner_classified():
    av = re_ev.assess_availability(
        "message_send 运行时未注入，无法发送", {"message_send"}, [])
    assert av.false_claims
    banner = re_ev.availability_banner(av)
    assert banner and origin_of(banner) == KIND_AUDIT_NOTICE
    # 生产者独立推送形态："\n" + banner（带前导空白仍判帧首）
    assert origin_of("\n" + banner) == KIND_AUDIT_NOTICE


# ── 4. 横幅帧拆分（纯横幅丢弃 / 横幅+正文保正文）───────────────────

def test_pure_banner_frame_dropped():
    av = re_ev.assess_availability(
        "message_send 运行时未注入，无法发送", {"message_send"}, [])
    banner = re_ev.availability_banner(av)
    reply, counts = split_frames(["\n" + banner])
    assert reply == ""
    assert counts == {KIND_AUDIT_NOTICE: 1}


def test_banner_plus_body_keeps_body():
    av = re_ev.assess_availability(
        "message_send 运行时未注入，无法发送", {"message_send"}, [])
    banner = re_ev.availability_banner(av)
    reply, counts = split_frames([banner + "\n\n这是正文回答。"])
    assert reply == "这是正文回答。"
    assert counts == {KIND_AUDIT_NOTICE: 1}


# ── 5. 回答帧不误伤 ──────────────────────────────────────────────

def test_answer_with_marker_text_mid_body_not_filtered():
    assert origin_of("回答里提到 ⏳ 回合进行中 是心跳格式") == ANSWER
    assert origin_of("回答里引用 🔍 需求自检 的原文") == ANSWER
    # 管线进度行按设计归回答帧（只过滤 roadmap 点名的心跳/自检/横幅）
    assert origin_of("📋 任务拆解：\n1. xxx") == ANSWER
    assert origin_of("🛑 [循环检测强制停止] xxx") == ANSWER


# ── 6. split_frames 混合帧 ───────────────────────────────────────

def test_split_frames_mixed_counts():
    wd = TurnWatchdog("s1")
    hb = wd.status_text()
    sc = SelfVerifier().format_report(_sample_report())
    reply, counts = split_frames(["这是回答", hb, sc, "，继续回答", ""])
    assert reply == "这是回答，继续回答"
    assert counts == {KIND_HEARTBEAT: 1, KIND_SELF_CHECK: 1}


def test_split_frames_empty_input():
    reply, counts = split_frames([])
    assert reply == "" and counts == {}
    reply, counts = split_frames(["", "   ", "\n"])
    assert reply == "" and counts == {}
