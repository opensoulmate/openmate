# -*- coding: utf-8 -*-
"""纯产物型回答（表格/JSON/列表）的自验覆盖核对——2026-10-23 周期#664 真实缺陷回归。

实证病灶：用户"请用 markdown 表格对比苹果和香蕉的三个维度…"→ 回复=一张合法表格
（内容词元 6/6 全命中），但需求侧 bigram 词元含大量跨词边界噪声（格对/比苹/蕉的）
与指令脚手架词（请用/直接/输出/不要）→ score 0.222 < 0.25 → 误判 unmet →
补做轮把整个任务重答一遍 → full_response + "\\n\\n" + fix_resp 追加 →
用户看到同一张表出现两遍（"重复的话"家族根因之一）。

期望行为：纯产物型回答不判 unmet（不触发补做）；真正未完成的需求仍然 unmet。
"""
from agent.self_verify import SelfVerifier, _tokens, _structure_tokens

TABLE_REPLY = (
    "| 维度 | 苹果 | 香蕉 |\n|------|------|------|\n"
    "| 价格 | 约5~10元/斤，中等偏便宜 | 约3~6元/斤，更实惠 |\n"
    "| 口感 | 脆甜多汁、清脆爽口 | 软糯香甜、绵密细腻 |\n"
    "| 营养 | 富含膳食纤维和维C，热量较低 | 富含钾和维B6，热量较高，快速供能 |"
)
TABLE_REQ = "请用 markdown 表格对比苹果和香蕉的三个维度（价格/口感/营养），直接输出一个表格，不要其他内容。"


def test_pure_table_reply_not_unmet():
    """真实案例：纯表格回答不得判 unmet（判 unmet = 触发补做 = 回复双份）。"""
    report = SelfVerifier().verify(TABLE_REQ, TABLE_REPLY, [])
    assert len(report.items) == 1
    assert report.items[0].status != "unmet", report.items[0]
    assert report.missing_requirements() == []


def test_table_structure_counts_as_format_evidence():
    """请求点名"表格"、回复交付 markdown 表格 = 格式词有证据（结构桥接）。"""
    toks = _structure_tokens(TABLE_REPLY)
    assert "表格" in toks and "markdown" in toks
    report = SelfVerifier().verify(TABLE_REQ, TABLE_REPLY, [])
    assert "表格" in report.items[0].evidence or "结构" in report.items[0].evidence


def test_weak_bigram_not_in_tokens():
    """含停用字的弱 bigram（蕉的/请用/个维）= 跨词边界/脚手架噪声，不进词元面。"""
    toks = _tokens(TABLE_REQ)
    assert "蕉的" not in toks
    assert "请用" not in toks
    assert "个维" not in toks
    # 实义 bigram 保留（"苹果"的"果"在历史停用字集内（"如果"整词入库），
    # 对称过滤下需求/证据两侧同丢，不影响命中率——此处只验保留面）
    assert "香蕉" in toks and "维度" in toks and "表格" in toks


def test_genuinely_unmet_still_unmet():
    """回归：真缺项仍然 unmet（补做语义不丢）。"""
    report = SelfVerifier().verify(
        "1. 修复登录bug 2. 生成月报",
        "登录bug已修复，测试通过",
        tool_calls_log=[{"function": {"name": "patch", "arguments": "{}"}}],
    )
    missing = report.missing_requirements()
    assert len(missing) == 1 and "生成月报" in missing[0]


def test_empty_reply_unmet():
    """回归：空回复对动作类需求仍然 unmet。"""
    report = SelfVerifier().verify(TABLE_REQ, "", [])
    assert report.missing_requirements() != []


def test_json_structure_evidence():
    """JSON 交付形态同款结构桥接。"""
    assert "json" in _structure_tokens('{"a": 1, "b": [2, 3]}')
