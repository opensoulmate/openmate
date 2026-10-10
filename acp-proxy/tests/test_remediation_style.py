# -*- coding: utf-8 -*-
"""⚡8.5c-① 回复风格治理——补做元叙事根因修复契约测试。

根因（周期#41 实证）：任务类回答尾部固定带"补做结果/逐项说明"元叙事，
违反验收 prompt"简短回答一句话"。两层根因：
  A. self_verify 把条件子句（"如果X就Y"）拆成独立需求项，短答覆盖不到
     → unmet → 触发补做；
  B. build_remediation_prompt 旧措辞"完成后逐项简要说明结果"直接命令模型
     产出"逐项说明"式复盘。

修复契约：
  1. 条件子句判定确定性（如果/一旦/的话/当…时/If…）；
  2. 条件项 unmet 封顶 uncertain——只提醒不补做，missing_requirements() 恒不含条件项；
  3. 补做提示词禁"补做结果/逐项说明"复盘标题，只要补充内容本身；
  4. 非条件项的真实缺口照常进补做清单（不过度抑制）；
  5. 接线护栏：soulmate_agent 真实补做路径仍走 missing_requirements + build_remediation_prompt。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.self_verify import (  # noqa: E402
    SelfVerifier,
    extract_requirements,
    is_conditional,
    is_self_negating,
)
from acceptance_benchmark import verify_research  # noqa: E402


def _verifier():
    return SelfVerifier()


# 验收 harness 真实任务类 prompt（acceptance_benchmark.py"调研"类原文）
_ACCEPT_PROMPT = (
    "用 web_search 搜索 Python 3.13 的正式发布时间，简短回答（一句话）。"
    "如果搜索工具不可用，明确说明不可用。"
)
# harness 真实发送形态（prompt + 防缓存标记行——周期#43 实证的"固定带"元叙事主根因：
# 标记行被拆成需求项且必然 unmet → 每轮必补做）
_ACCEPT_PROMPT_TAGGED = (
    _ACCEPT_PROMPT + "\n（复测标记 RETEST_123，此行仅为防缓存标记，忽略其语义）"
)
_SHORT_ANSWER = "Python 3.13 于 2024 年 10 月 7 日正式发布。"
_SEARCH_LEDGER = [{"function": {"name": "web_search", "arguments": '{"query": "Python 3.13 release"}'}}]


# ── 1. 条件子句判定 ─────────────────────────────────────────────

def test_conditional_detection_positive():
    for text in (
        "如果搜索工具不可用，明确说明不可用",
        "一旦失败就回滚",
        "查不到的话如实说明",
        "当端口被占用时换一个端口",
        "If the tool is unavailable, say so",
    ):
        assert is_conditional(text), text


def test_conditional_detection_negative():
    for text in (
        "用 web_search 搜索 Python 3.13 的正式发布时间",
        "修复登录bug并生成月报",
        "整理若干实验数据",  # "若干"不是条件词
    ):
        assert not is_conditional(text), text


# ── 2. 自否定元指令不算需求（根因C治理："固定带"主根因） ─────────

def test_self_negating_detection():
    for text in (
        "（复测标记 RETEST_123，此行仅为防缓存标记，忽略其语义）",
        "此行只是模板占位",
        "（本行可忽略）",
        "Ignore this line",
    ):
        assert is_self_negating(text), text


def test_self_negating_not_over_filtered():
    for text in (
        "对比时忽略大小写差异",  # 真实需求："忽略"是任务内容
        "生成月报并写明数据来源",
        "此行只是第一部分，请继续完成第二部分",  # "此行只是"+非标记/占位=真实需求
    ):
        assert not is_self_negating(text), text


def test_marker_line_never_requirement():
    """真实用户路径（harness 发送形态）：防缓存标记行不得成为需求项。"""
    reqs = extract_requirements(_ACCEPT_PROMPT_TAGGED)
    assert len(reqs) == 2, reqs
    assert all("复测标记" not in r for r in reqs), reqs

    report = _verifier().verify(
        _ACCEPT_PROMPT_TAGGED, _SHORT_ANSWER, tool_calls_log=_SEARCH_LEDGER
    )
    assert report.missing_requirements() == [], [
        (i.requirement, i.status) for i in report.items
    ]


def test_legit_ignore_requirement_still_checked():
    reqs = extract_requirements("帮我生成月度报表用于对比分析，对比时忽略大小写差异")
    assert any("忽略大小写" in r for r in reqs), reqs


# ── 3. 条件项不进补做清单（根因A治理） ──────────────────────────

def test_acceptance_prompt_short_answer_no_remediation():
    """真实用户路径：验收 prompt + 一句话短答 + 真实工具账本 → 不得触发补做。"""
    v = _verifier()
    reqs = extract_requirements(_ACCEPT_PROMPT)
    assert len(reqs) == 2, reqs
    assert is_conditional(reqs[1]), reqs[1]

    report = v.verify(_ACCEPT_PROMPT, _SHORT_ANSWER, tool_calls_log=_SEARCH_LEDGER)
    assert report.missing_requirements() == [], [
        (i.requirement, i.status) for i in report.items
    ]
    cond_items = [i for i in report.items if i.conditional]
    assert len(cond_items) == 1, [(i.requirement, i.conditional) for i in report.items]


def test_conditional_item_unmet_capped_uncertain():
    """条件项零证据覆盖时封顶 uncertain（只提醒），绝不进补做。"""
    v = _verifier()
    report = v.verify(
        "1. 生成月报 2. 如果数据库连不上就写明原因",
        "月报已生成并保存到 /tmp/report.md",
        tool_calls_log=[{"function": {"name": "write_file", "arguments": "{}"}}],
    )
    cond = [i for i in report.items if i.conditional]
    assert len(cond) == 1, [(i.requirement, i.conditional) for i in report.items]
    assert cond[0].status == "uncertain", cond[0]
    assert "条件项" in cond[0].evidence, cond[0].evidence
    assert report.missing_requirements() == [], report.missing_requirements()


# ── 4. 补做提示词禁元叙事（根因B治理） ──────────────────────────

def test_remediation_prompt_forbids_meta_narration():
    prompt = _verifier().build_remediation_prompt(["生成月报"])
    assert "逐项简要说明结果" not in prompt, prompt  # 旧措辞=元叙事根因，必须消失
    assert "补做结果" in prompt and "不要写" in prompt, prompt  # 明确禁复盘标题
    assert "逐项说明" in prompt, prompt  # 禁令必须点名该形态
    assert "生成月报" in prompt


# ── 5. 不过度抑制：真实缺口照常补做 ─────────────────────────────

def test_non_conditional_unmet_still_remediated():
    v = _verifier()
    report = v.verify(
        "1. 修复登录bug 2. 生成月报",
        "登录bug已修复，测试通过",
        tool_calls_log=[{"function": {"name": "patch", "arguments": "{}"}}],
    )
    missing = report.missing_requirements()
    assert len(missing) == 1 and "生成月报" in missing[0], missing


# ── 6. 报告渲染：条件项标注"仅提醒" ─────────────────────────────

def test_report_labels_conditional_item():
    v = _verifier()
    report = v.verify(
        "1. 生成月报 2. 如果数据库连不上就写明原因",
        "月报已生成并保存到 /tmp/report.md",
        tool_calls_log=[{"function": {"name": "write_file", "arguments": "{}"}}],
    )
    text = v.format_report(report)
    assert "（条件项，仅提醒）" in text, text
    assert "（未覆盖）" not in text, text  # 条件项封顶 uncertain，报告不得称"未覆盖"


# ── 7. harness 判据边界：元叙事区需求原文复述 ≠ 模型自报（STYLE85C_1 实证） ──

# 周期#43 真实 run（tag=STYLE85C_1）采集到的合并回复原文（模型主答正确，
# 需求自检报告被 harness 并帧混入，复述的需求原文"如果搜索工具不可用…"误触发判负）
_MERGED_REPLY = (
    "**Python 3.13 于 2024 年 10 月 7 日正式发布**（web_search 可用，已搜索确认）。"
    "🔍 需求自检（对照原始需求逐条核对）： "
    "⚠️ 1. 用 web_search 搜索 Python 3.13 的正式发布时间，简短回答（一句话） "
    "✅ 2. 如果搜索工具不可用，明确说明不可用（条件项，仅提醒）— 覆盖 1/2"
)


def test_verify_research_meta_echo_not_self_report():
    ok, reason = verify_research(_MERGED_REPLY)
    assert ok, reason  # 需求自检区复述的"不可用"不是模型自报


def test_verify_research_real_claim_still_fails():
    ok, reason = verify_research("搜索工具不可用，无法确认 Python 3.13 的发布时间（2024）")
    assert not ok, reason


# ── 8. 接线护栏：soulmate_agent 真实补做路径 ─────────────────────

def test_wiring_agent_remediation_path_intact():
    src = (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text(
        encoding="utf-8"
    )
    assert "missing_requirements()" in src
    assert "build_remediation_prompt(" in src
    # 治理生效面在 self_verify 单一真源，agent 侧不得再出现旧元叙事措辞
    assert "逐项简要说明结果" not in src
