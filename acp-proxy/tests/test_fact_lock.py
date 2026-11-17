"""C1 全局事实锁（fact_lock.py）：结构化事实表 + 跨章节一致性校验器。

覆盖：schema 校验（缺字段/重复 key/别名）/ prompt 注入块 / 一致文档通过 /
冲突点名（章节+行号+原文摘录）/ 多值点名 / 锁值被架空（missing）/ 值在 key
前（"90日历天的工期"）/ 别名重叠去重（合同工期⊇工期）/ 并列断言不串窗
（工期与质保期互不干扰）/ 文本值断言（系统全称：X）/ 全角数字归一。
"""

import pytest

from agent.fact_lock import (
    build_lock, check_document, format_report, prompt_block, validate_lock,
)

_FACTS = [
    {"key": "工期", "value": "90日历天", "scope": "全文",
     "source": "用户确认 2026-11-17", "confirmed_by": "张工",
     "aliases": ["合同工期", "服务期限"]},
    {"key": "质保期", "value": "3年", "scope": "全文",
     "source": "用户确认", "confirmed_by": "张工"},
    {"key": "系统全称", "value": "智慧园区综合管理平台", "scope": "全文",
     "source": "用户确认", "confirmed_by": "李经理", "aliases": ["系统名称"]},
]


def _lock():
    return build_lock([dict(f) for f in _FACTS])


# ── schema 校验 ─────────────────────────────────────────────────────

def test_validate_lock_required_fields():
    errs = validate_lock({"locked_facts": [{"key": "工期", "value": "90天"}]})
    assert any("confirmed_by" in e for e in errs)
    assert any("scope" in e for e in errs)
    with pytest.raises(ValueError, match="事实锁非法"):
        build_lock([{"key": "x", "value": "y"}])


def test_validate_lock_duplicate_key_and_alias_shape():
    errs = validate_lock({"locked_facts": [
        {"key": "工期", "value": "1", "scope": "s", "source": "src",
         "confirmed_by": "a"},
        {"key": "工期", "value": "2", "scope": "s", "source": "src",
         "confirmed_by": "a"},
    ]})
    assert any("重复" in e for e in errs)
    errs2 = validate_lock({"locked_facts": [
        {"key": "k", "value": "v", "scope": "s", "source": "src",
         "confirmed_by": "a", "aliases": [""]}]})
    assert any("aliases" in e for e in errs2)


# ── prompt 注入块 ───────────────────────────────────────────────────

def test_prompt_block_readonly_and_full_facts():
    block = prompt_block(_lock())
    assert "【全局事实锁·只读】" in block
    assert "90日历天" in block and "合同工期" in block
    assert "智慧园区综合管理平台" in block
    assert "确认人：张工" in block
    assert "只准出自本锁" in block          # 承诺类数字纪律


# ── 一致性校验器 ────────────────────────────────────────────────────

def test_consistent_document_passes():
    chapters = [
        {"id": "ch1", "title": "概述",
         "text": "本项目工期为90日历天。系统全称：智慧园区综合管理平台。"},
        {"id": "ch2", "title": "实施方案",
         "text": "合同工期90日历天，质保期为3年。\n90日历天的工期安排见附件。"},
        {"id": "ch3", "title": "服务承诺",
         "text": "质保期为3年，服务期限90日历天。"},
    ]
    r = check_document(_lock(), chapters)
    assert r["ok"] is True and r["issues"] == []
    assert "通过" in format_report(r)


def test_conflict_reported_with_location():
    chapters = [
        {"id": "ch1", "title": "概述", "text": "本项目工期为90日历天。"},
        {"id": "ch2", "title": "实施方案", "text": "本节概述。\n合同工期120日历天。"},
    ]
    r = check_document(_lock(), chapters)
    assert r["ok"] is False
    conflicts = [i for i in r["issues"] if i["type"] == "conflict"]
    assert conflicts and conflicts[0]["key"] == "工期"
    assert conflicts[0]["locked_value"] == "90日历天"
    assert "120日历天" in conflicts[0]["found"]
    assert conflicts[0]["chapter_id"] == "ch2"
    assert conflicts[0]["line"] == 2                # 定位到行
    assert "120" in conflicts[0]["excerpt"]         # 原文摘录
    report = format_report(r)
    assert "矛盾点名" in report and "ch2" in report and "第2行" in report


def test_multi_value_and_missing():
    chapters = [
        {"id": "ch1", "title": "概述", "text": "质保期3年。"},
        {"id": "ch2", "title": "承诺", "text": "质保期1年。"},
    ]
    r = check_document(_lock(), chapters)
    types = {i["type"] for i in r["issues"]}
    assert "multi_value" in types                   # 3年 vs 1年 多值点名
    assert "conflict" in types
    # 工期/系统全称 全文未提 → 不产生 missing（无断言不判）
    assert not any(i["type"] == "missing" and i["key"] == "工期" for i in r["issues"])


def test_missing_when_value_never_appears():
    chapters = [{"id": "ch1", "title": "概述", "text": "质保期为5年，质保期5年。"}]
    r = check_document(_lock(), chapters)
    missing = [i for i in r["issues"] if i["type"] == "missing"]
    assert missing and missing[0]["key"] == "质保期"
    assert r["ok"] is False        # 同时有 conflict（5年≠3年）


def test_value_before_key_and_alias_overlap_dedup():
    chapters = [{"id": "ch1", "title": "概述",
                 "text": "90日历天的工期安排如下。\n合同工期90日历天。"}]
    r = check_document(_lock(), chapters)
    assert r["ok"] is True and r["issues"] == []     # 值在 key 前 + 别名重叠不重复判


def test_parallel_assertions_do_not_cross_windows():
    chapters = [{"id": "ch1", "title": "概述",
                 "text": "工期90日历天，质保期为3年。"}]
    r = check_document(_lock(), chapters)
    assert r["ok"] is True                          # 互不串窗


def test_text_fact_assertion():
    chapters = [{"id": "ch1", "title": "概述",
                 "text": "系统名称是数字孪生平台。"}]
    r = check_document(_lock(), chapters)
    conflicts = [i for i in r["issues"] if i["type"] == "conflict"]
    assert conflicts and conflicts[0]["key"] == "系统全称"
    # 无断言标记的提及不判
    r2 = check_document(_lock(), [{"id": "c", "title": "t",
                                   "text": "系统全称的命名规则如下。"}])
    assert r2["issues"] == [] or all(
        i["type"] != "conflict" for i in r2["issues"])


def test_fullwidth_digits_normalized():
    chapters = [{"id": "ch1", "title": "概述",
                 "text": "本项目工期为９０日历天。"}]
    r = check_document(_lock(), chapters)
    assert r["ok"] is True                          # 全角 90 归一命中锁值
