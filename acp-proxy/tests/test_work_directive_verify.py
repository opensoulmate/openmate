# -*- coding: utf-8 -*-
"""周期#670 · 执行方式约束假 unmet 根修契约测试。

根因（真实 WS 长回合探针 om-72539c270140 实证，2026-10-23 09:13）：
self_verify.extract_requirements 把"执行方式约束"行（"请勿拆分子任务、勿做任务规划，
直接在一个回合里连续调用工具完成"类——约束的是"怎么干活"，不是交付物）拆成需求项；
回复措辞天然不含约束原文 → 词元核对必然假 unmet → 补做 1 轮 → 回复正文混入
"补充内容为空——该补做项未包含任何新的待执行内容（条件不满足）…"元叙事。
journal 实证：`[self-verify] items=3, coverage=0.0,
missing=['【单一连续任务，请勿拆分子任务、勿做任务规划，直接在一个回合里连续调用工具完成】'],
remediated=True`。

修复契约（与条件子句/感知类元指令同哲学：确定性词元验不了"遵守约束"）：
  1. is_work_directive 判定确定性：剥【…】标签后以否定式祈使头开头（请勿/勿/禁止/
     严禁/不得/不要/别/无需/不用）＋含工作方式词（拆分/规划/合并/调用/回合/…）＋
     不含交付物动词（生成/写/删除/…）；
  2. 执行方式约束项 unmet 封顶 uncertain——不进补做清单、不计覆盖率分母、不判 ok；
  3. 实质约束（"请不要删除我的配置文件"）与含交付物需求（"禁止合并输出，请生成报告"
     /"依次执行命令（禁止合并）"）不受影响，照常核对（不过度抑制）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.self_verify import (  # noqa: E402
    SelfVerifier,
    extract_requirements,
    is_work_directive,
)

# 真实探针 prompt 原文（/tmp/_c670_narr_longturn.py 发送形态，逐字钉死）
_PROBE_PROMPT = (
    "【单一连续任务，请勿拆分子任务、勿做任务规划，直接在一个回合里连续调用工具完成】\n"
    "请用 terminal 工具依次执行以下 12 条命令（一次工具调用只执行一条，"
    "禁止合并多条命令、禁止使用 batch_execute，每条执行完再执行下一条）："
    "echo TURN1、echo TURN2、echo TURN3、echo TURN4、echo TURN5、echo TURN6、"
    "echo TURN7、echo TURN8、echo TURN9、echo TURN10、echo TURN11、echo TURN12。\n"
    "全部执行完后，用一句话简短汇报共执行了几条命令。"
)
_DIRECTIVE_LINE = "【单一连续任务，请勿拆分子任务、勿做任务规划，直接在一个回合里连续调用工具完成】"
# 真实回复（探针 reply_head 原文，含补做元叙事形态）
_PROBE_REPLY = (
    "共执行了 12 条命令（TURN1 至 TURN12），全部成功。"
    "补充内容为空——该补做项未包含任何新的待执行内容（条件不满足），"
    "且所列任务的 12 条命令与一句话汇报均已完成，故实际需执行 0 项。"
)
# 交付项被覆盖的回复（terminal 工具依次执行 12 条命令…+一句话汇报）
_PROBE_REPLY_MET = (
    "用 terminal 工具依次执行了以下 12 条命令：echo TURN1、echo TURN2、echo TURN3、"
    "echo TURN4、echo TURN5、echo TURN6、echo TURN7、echo TURN8、echo TURN9、"
    "echo TURN10、echo TURN11、echo TURN12。全部执行完后，一句话汇报共执行了 12 条命令。"
)
_TERMINAL_LEDGER = [
    {"function": {"name": "terminal", "arguments": '{"command": "echo TURN%d"}' % i}}
    for i in range(1, 13)
]


def _verifier():
    return SelfVerifier()


# ── 1. is_work_directive 判定 ─────────────────────────────────────

def test_directive_detection_positive():
    for text in (
        _DIRECTIVE_LINE,
        "禁止合并多条命令、禁止使用 batch_execute，每条执行完再执行下一条",
        "请勿拆分子任务、勿做任务规划",
        "直接在一个回合里连续调用工具完成，不要分步执行",
    ):
        assert is_work_directive(text), text


def test_directive_detection_negative():
    # 实质约束（负向但含交付物动词/无工作方式词）＝真实需求，照常核对
    for text in (
        "请不要删除我的配置文件，保持原样，只做只读检查就好",
        "禁止合并输出，请生成一份完整的测试报告并说明结论",
        "请用 terminal 工具依次执行以下 2 条命令（禁止合并多条命令）：echo a、echo b。",
        "把任务拆分成3步并写出计划",
    ):
        assert not is_work_directive(text), text


# ── 2. 真实探针形态：约束行不进补做清单 ──────────────────────────

def test_real_probe_prompt_items_pinned():
    items = extract_requirements(_PROBE_PROMPT)
    assert _DIRECTIVE_LINE in items, items
    assert len(items) == 3, items  # 约束行＋执行12条命令＋一句话汇报


def test_real_probe_directive_not_remediated_real_reply():
    # 真实回复（含补做元叙事）路径：修复前 missing 含约束行 → 补做；修复后零补做
    report = _verifier().verify(_PROBE_PROMPT, _PROBE_REPLY, tool_calls_log=_TERMINAL_LEDGER)
    assert report.missing_requirements() == [], [
        (i.requirement, i.status) for i in report.items]
    d = [i for i in report.items if i.directive]
    assert len(d) == 1 and d[0].requirement == _DIRECTIVE_LINE
    assert d[0].status != "unmet"


def test_real_probe_met_reply_no_missing_and_coverage_full():
    report = _verifier().verify(
        _PROBE_PROMPT, _PROBE_REPLY_MET, tool_calls_log=_TERMINAL_LEDGER)
    assert report.missing_requirements() == []
    # 约束项不计覆盖率分母：两个可验交付项全 met → coverage 1.0
    assert report.coverage == 1.0, [(i.requirement, i.status, i.score) for i in report.items]


# ── 3. 纯约束项封顶 uncertain（不进补做/不计覆盖）────────────────

def test_pure_constraint_item_capped_uncertain():
    prompt = "禁止合并多条命令、禁止使用 batch_execute，每条执行完再执行下一条"
    report = _verifier().verify(prompt, "已完成。", tool_calls_log=[])
    assert len(report.items) == 1
    it = report.items[0]
    assert it.directive is True
    assert it.status == "uncertain"
    assert report.missing_requirements() == []
    assert "执行方式约束" in it.evidence
    assert report.coverage == 1.0  # 分母不含约束项


# ── 4. 不过面抑制：实质需求照常核对进补做 ────────────────────────

def test_real_negative_requirement_still_verified():
    prompt = "请不要删除我的配置文件，保持原样，只做只读检查就好"
    report = _verifier().verify(prompt, "随便闲聊两句。", tool_calls_log=[])
    assert len(report.items) == 1
    assert report.items[0].directive is False
    assert report.missing_requirements() == [prompt]


def test_deliverable_with_embedded_constraint_still_verified():
    prompt = "请用 terminal 工具依次执行以下 2 条命令（禁止合并多条命令）：echo a、echo b。"
    report = _verifier().verify(prompt, "闲聊。", tool_calls_log=[])
    assert len(report.items) == 1
    assert report.items[0].directive is False
    assert report.missing_requirements() == [prompt]


def test_deliverable_verb_rescues_constraint_head():
    prompt = "禁止合并输出，请生成一份完整的测试报告并说明结论"
    assert not is_work_directive(prompt)
