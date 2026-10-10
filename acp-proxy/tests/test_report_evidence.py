# -*- coding: utf-8 -*-
"""⚡8.2 短路合成报告治理测试。

覆盖：
① 账本解析（真实执行 vs 被拦截 vs 伪条目）
② assess 四态判定（no-claim/grounded/blocked/unsupported）
③ amend_response 报告降级+治理声明（禁报"完成"、被拦截报原因）
④ 语义缓存准入（执行类请求不缓存不回放——run2 1.38s 短路路径封死）
⑤ SelfVerifier 自检以工具执行证据为前提（自称"已完成"不是证据）
⑥ soulmate_agent 接线护栏（缓存门/落库门/治理收口三处）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.report_evidence import (
    assess,
    amend_response,
    claims_completion,
    governance_banner,
    is_cacheable_request,
    parse_ledger,
)
from agent.self_verify import SelfVerifier


# ── ① 账本解析 ──────────────────────────────────────────────────

def test_parse_ledger_nested_shape_no_permission_counts_executed():
    """LLM 原始 {"function": {...}} 形态（无门禁信息）= 已执行，不虚构拦截。"""
    executed, blocked = parse_ledger([{"function": {"name": "write_file", "arguments": "{}"}}])
    assert len(executed) == 1 and executed[0].name == "write_file"
    assert blocked == []


def test_parse_ledger_executed_permissions():
    for perm in ("allow", "ask-approved", "degraded-allow", "cached-allow"):
        executed, blocked = parse_ledger([
            {"name": "terminal", "arguments": "{}", "result_preview": "ok", "permission": perm},
        ])
        assert len(executed) == 1, perm
        assert blocked == [], perm


def test_parse_ledger_blocked_permissions():
    for perm, reason in (
        ("deny", "OpenSoul权限引擎拒绝"),
        ("ask-denied", "用户拒绝"),
        ("sandbox-restricted", "沙箱受限"),
        ("loop_guard:intervene", "循环干预"),
        ("infection:terminate", "注入审计终止"),
    ):
        executed, blocked = parse_ledger([
            {"name": "write_file", "arguments": "{}",
             "result_preview": reason, "permission": perm},
        ])
        assert executed == [], perm
        assert len(blocked) == 1 and blocked[0].name == "write_file", perm
        assert blocked[0].result_preview == reason


def test_parse_ledger_pass_with_trace_and_pseudo_entries():
    """pass_with_trace=放行执行（计证据）；"[infection-guard]"伪条目两头不计。"""
    executed, blocked = parse_ledger([
        {"name": "memory_append", "arguments": "{}", "result_preview": "ok",
         "permission": "infection:pass_with_trace"},
        {"name": "[infection-guard]", "arguments": {}, "result_preview": "trace",
         "permission": "infection:pass_with_trace"},
    ])
    assert [e.name for e in executed] == ["memory_append"]
    assert blocked == []


def test_parse_ledger_unknown_permission_not_evidence():
    executed, blocked = parse_ledger([
        {"name": "x", "arguments": "", "result_preview": "", "permission": "weird-unknown"},
    ])
    assert executed == [] and blocked == []


# ── ② assess 四态 ───────────────────────────────────────────────

REPORT = "## 任务执行报告\n\n**目标**: 创建文件\n\n✅ **步骤1**: 创建 dev_hello.py"


def test_assess_no_claim_informational():
    a = assess("什么是语义缓存？", "语义缓存是一种……", [])
    assert a.verdict == "no-claim"


def test_assess_grounded_with_executed_tool():
    a = assess(
        "帮我创建 dev_hello.py",
        REPORT,
        [{"name": "write_file", "arguments": "{}", "result_preview": "ok", "permission": "allow"}],
    )
    assert a.verdict == "grounded"
    assert a.grounded


def test_assess_unsupported_zero_tools_r2_short_circuit():
    """R2：零工具执行却合成"任务执行报告" = unsupported（run2 1.38s 形态）。"""
    a = assess("帮我创建 dev_hello.py", REPORT, [])
    assert a.verdict == "unsupported"
    assert a.claims and a.action_required
    assert a.executed == [] and a.blocked == []


def test_assess_blocked_all_tools_blocked():
    a = assess(
        "帮我创建 dev_hello.py",
        REPORT,
        [{"name": "write_file", "arguments": "{}",
          "result_preview": "注入审计终止", "permission": "infection:terminate"}],
    )
    assert a.verdict == "blocked"
    assert len(a.blocked) == 1


def test_assess_informational_claim_not_gated():
    """非动作请求里的口头"成功"字样不触发治理。"""
    a = assess("怎么才能成功？", "想要成功，需要坚持和方法。", [])
    assert a.verdict == "no-claim"


# ── ③ amend_response / governance_banner ────────────────────────

def test_amend_demotes_heading_and_states_no_evidence():
    a = assess("帮我创建 dev_hello.py", REPORT, [])
    out = amend_response(REPORT, a)
    assert "## 任务执行报告（⚠️ 未经执行验证）" in out
    assert "不构成完成证明" in out
    assert "无成功工具执行记录" in out
    # 原始标题已被降级（禁报裸"任务执行报告"）
    assert "## 任务执行报告\n" not in out


def test_amend_blocked_reports_reason_not_done():
    a = assess(
        "帮我创建 dev_hello.py",
        REPORT,
        [{"name": "write_file", "arguments": "{}",
          "result_preview": "注入审计终止：疑似验收标记", "permission": "infection:terminate"}],
    )
    out = amend_response(REPORT, a)
    assert "被拦截" in out
    assert "write_file" in out
    assert "注入审计终止：疑似验收标记" in out  # 被拦截+原因
    assert "## 任务执行报告（⚠️ 未经执行验证）" in out


def test_amend_grounded_and_no_claim_unchanged():
    g = assess("帮我创建 dev_hello.py", REPORT,
               [{"name": "write_file", "arguments": "{}", "permission": "allow"}])
    assert amend_response(REPORT, g) == REPORT
    n = assess("什么是Python？", "Python 是一门语言。", [])
    assert amend_response("Python 是一门语言。", n) == "Python 是一门语言。"


def test_governance_banner_empty_when_grounded():
    g = assess("帮我创建 x.py", REPORT,
               [{"name": "write_file", "arguments": "{}", "permission": "allow"}])
    assert governance_banner(g) == ""


# ── ④ 语义缓存准入 ──────────────────────────────────────────────

def test_cache_gate_blocks_execution_requests():
    for q in (
        "帮我创建 dev_hello.py 写入 print hello",
        "修复登录页面的bug",
        "把 report.xlsx 生成一下",
        "删除/tmp下的临时文件",
        "安装 playwright 并运行测试",
        "更新配置文件里的端口",
    ):
        assert not is_cacheable_request(q), q


def test_cache_gate_allows_informational_requests():
    for q in (
        "什么是语义缓存？",
        "为什么Python的GIL会影响多线程？",
        "介绍一下ACP协议",
        "你好",
        "怎么理解上下文工程？",
    ):
        assert is_cacheable_request(q), q


def test_claims_completion_markers():
    assert claims_completion(REPORT)
    assert claims_completion("已完成数据同步脚本的编写")
    assert not claims_completion("我建议先看一下日志再判断")


# ── ⑤ SelfVerifier 自检以工具执行证据为前提 ─────────────────────

def test_self_verify_self_claim_is_not_evidence():
    """R2 漏洞封死：回复自称"已完成"但零工具执行 → 证据不足，不给 met。"""
    v = SelfVerifier()
    report = v.verify(
        "帮我创建 dev_hello.py 写入 print hello",
        "## 任务执行报告\n\n已完成 dev_hello.py 写入 print hello 的创建，内容写入成功。",
        tool_calls_log=[],
    )
    assert len(report.items) == 1
    assert report.items[0].status == "uncertain", report.items[0]
    assert not report.ok


def test_self_verify_blocked_tools_reported_with_reason():
    """工具被拦 → 报"被拦截+原因"，不计执行证据。"""
    v = SelfVerifier()
    report = v.verify(
        "帮我创建 dev_hello.py 写入 print hello",
        "## 任务执行报告\n\n已完成 dev_hello.py 写入 print hello 的创建。",
        tool_calls_log=[
            {"name": "write_file", "arguments": "{}",
             "result_preview": "注入审计终止", "permission": "infection:terminate"},
        ],
    )
    assert report.items[0].status == "uncertain"
    assert "⛔被拦截" in report.items[0].evidence
    assert "write_file" in report.items[0].evidence
    assert "注入审计终止" in report.items[0].evidence


def test_self_verify_executed_tool_still_met():
    v = SelfVerifier()
    report = v.verify(
        "帮我创建 dev_hello.py 写入 print hello",
        "已创建 dev_hello.py，写入 print hello。",
        tool_calls_log=[
            {"name": "write_file", "arguments": "{}", "result_preview": "ok",
             "permission": "allow"},
        ],
    )
    assert report.items[0].status == "met", report.items[0]


# ── ⑥ soulmate_agent 接线护栏 ───────────────────────────────────

def _agent_source() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "agent", "soulmate_agent.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_wiring_semantic_cache_gate_on_get_and_put():
    src = _agent_source()
    assert "report_evidence.is_cacheable_request(user_text)" in src
    # get 侧：执行类请求不读缓存
    assert "cached = self._semantic_cache.get(user_text) if _cacheable else None" in src
    # put 侧：执行类回复不入缓存
    assert "and report_evidence.is_cacheable_request(user_text)" in src


def test_wiring_evidence_gate_before_self_verify():
    src = _agent_source()
    gate = src.find("report_evidence.assess(user_text, full_response, tool_calls_log)")
    verify = src.find("self._self_verifier.verify(user_text, full_response, tool_calls_log)")
    assert gate > 0, "证据治理收口缺失"
    assert verify > gate, "证据治理必须先于自检（自检看治理后文本）"
    assert "report_evidence.amend_response(full_response, _evidence)" in src
    assert "report_evidence.governance_banner(_evidence)" in src
