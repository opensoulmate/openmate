# -*- coding: utf-8 -*-
"""⚡8.5c② 可用性误报治理测试（8.2 "说了≠做了"的镜像："做了/有，说没有"）。

覆盖：
① 声称抽取（注册类/可用性类/无名类各形态 + 结果类宾语排除）
② assess_availability 两级判据（registration=工具面证伪 / availability=执行账本证伪）
③ 被拦截的执行不构成"可用"证据（反向支持模型说法，不更正）
④ amend_availability/banner 更正声明（原文保留、无误报原文返回）
⑤ soulmate_agent 接线护栏（工具面真源+_active_tool_names+先于自检）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.report_evidence import (
    amend_availability,
    assess_availability,
    availability_banner,
    find_availability_claims,
)


def _exec(name, permission="allow"):
    return {"name": name, "arguments": "{}", "result_preview": "ok", "permission": permission}


# ── ① 声称抽取 ──────────────────────────────────────────────────

def test_extract_registration_forms():
    cases = [
        ("我没有 web_search 工具，无法完成搜索。", "web_search"),
        ("message_send 运行时未注入，我拒绝发送。", "message_send"),
        ("缺少 schedule_list 工具。", "schedule_list"),
        ("不存在 terminal 工具。", "terminal"),
        ("write_file 不在工具列表中。", "write_file"),
        ("patch 未注入。", "patch"),
        ("环境里没有 vision_analyze", "vision_analyze"),  # run-A 实证裸形态
    ]
    for text, name in cases:
        claims = find_availability_claims(text)
        reg = [c for c in claims if c.kind == "registration" and c.tool_name == name]
        assert reg, f"未抽取到注册类声称: {text!r} -> {claims}"


def test_extract_availability_forms():
    cases = [
        ("web_search 工具不可用，请稍后再试。", "web_search"),
        ("我无法使用 message_send。", "message_send"),
        ("无法调用 todo 工具。", "todo"),
    ]
    for text, name in cases:
        claims = find_availability_claims(text)
        av = [c for c in claims if c.kind == "availability" and c.tool_name == name]
        assert av, f"未抽取到可用性声称: {text!r} -> {claims}"


def test_extract_generic_form():
    claims = find_availability_claims("很抱歉，工具未注入，我什么也做不了。")
    assert any(c.kind == "generic" for c in claims), claims


def test_result_ish_object_not_a_claim():
    """"没有 web_search 的结果"是结果缺失，不是可用性声称——禁误纠。"""
    assert find_availability_claims("没有 web_search 的结果可供引用。") == []
    assert find_availability_claims("这里没有 terminal 输出。") == []


def test_non_tool_context_injection_word_not_generic_claim():
    """"参数没有注入到请求里"不含"工具"——不是无名类可用性声称。"""
    assert find_availability_claims("参数没有注入到请求里，导致 400。") == []


def test_named_claim_suppresses_generic_noise():
    claims = find_availability_claims("message_send 未注入，工具未注入。")
    assert all(c.kind != "generic" for c in claims), claims


# ── ② 两级判据 ──────────────────────────────────────────────────

def test_registration_claim_falsified_by_registry():
    """注册类声称：工具面在册即证伪（无须执行过）。"""
    a = assess_availability(
        "message_send 运行时未注入，我拒绝发送。",
        {"message_send", "web_search"}, None,
    )
    assert len(a.false_claims) == 1
    assert a.false_claims[0].tool_name == "message_send"
    assert a.false_claims[0].proof == "registry"


def test_registration_claim_falsified_by_execution_without_registry():
    """工具面真源缺失但账本有成功执行——照样证伪（执行=最强可用证据）。"""
    a = assess_availability(
        "没有 web_search 工具。",
        None, [_exec("web_search")],
    )
    assert len(a.false_claims) == 1
    assert a.false_claims[0].proof == "executed"


def test_unknown_tool_not_falsified():
    """声称的工具不在任何真源里（模型说的是真话或瞎编）——不更正。"""
    a = assess_availability(
        "没有 super_tool 工具。",
        {"web_search"}, None,
    )
    assert a.claims and a.false_claims == []


def test_availability_claim_only_falsified_by_execution():
    """可用性类（模糊）：仅"真实执行成功"证伪——在册没试过不武断更正。"""
    in_reg_uncalled = assess_availability(
        "web_search 工具不可用。", {"web_search"}, None)
    assert in_reg_uncalled.false_claims == []

    executed = assess_availability(
        "web_search 工具不可用。", {"web_search"}, [_exec("web_search")])
    assert len(executed.false_claims) == 1
    assert executed.false_claims[0].proof == "executed"


def test_blocked_call_supports_model_claim_not_falsified():
    """被拦截的调用证明"确实用不了"——反向支持模型说法，禁更正。"""
    a = assess_availability(
        "message_send 无法使用。",
        {"message_send"}, [_exec("message_send", permission="deny")],
    )
    assert a.false_claims == []


def test_generic_claim_falsified_by_any_execution():
    a = assess_availability(
        "工具未注入。", {"web_search"}, [_exec("terminal")])
    assert len(a.false_claims) == 1 and a.false_claims[0].kind == "generic"

    none_run = assess_availability("工具未注入。", {"web_search"}, None)
    assert none_run.false_claims == []


def test_no_source_no_correction():
    """无工具面真源且无账本——一律不更正（诚实边界：没证据不动手）。"""
    a = assess_availability("message_send 未注入。", None, None)
    assert a.false_claims == []


# ── ③④ 更正声明 ────────────────────────────────────────────────

def test_amend_prepends_banner_and_preserves_original():
    resp = "message_send 运行时未注入，我拒绝发送。"
    a = assess_availability(resp, {"message_send"}, None)
    out = amend_availability(resp, a)
    assert out.startswith("⚠️ **工具可用性更正**")
    assert resp in out  # 原文保留（可审计，不删改模型原话）
    assert "message_send" in availability_banner(a)
    assert "已注入" in availability_banner(a) or "真实调用执行成功" in availability_banner(a)


def test_amend_no_false_claim_passthrough():
    resp = "今天天气不错。"
    a = assess_availability(resp, {"web_search"}, None)
    assert amend_availability(resp, a) == resp
    assert availability_banner(a) == ""


def test_amend_executed_proof_wording():
    a = assess_availability(
        "web_search 工具不可用。", None, [_exec("web_search")])
    banner = availability_banner(a)
    assert "真实调用执行成功" in banner


# ── ⑤ soulmate_agent 接线护栏 ──────────────────────────────────

def _agent_source() -> str:
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "agent", "soulmate_agent.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_wiring_availability_check_uses_tool_face_truth_source():
    src = _agent_source()
    assert "report_evidence.assess_availability(" in src
    assert 'getattr(self, "_active_tool_names", None)' in src
    assert "report_evidence.amend_availability(full_response, _avail)" in src
    assert "report_evidence.availability_banner(_avail)" in src


def test_wiring_availability_before_self_verify_and_after_evidence():
    src = _agent_source()
    avail = src.find("report_evidence.assess_availability(")
    verify = src.find("self._self_verifier.verify(user_text, full_response, tool_calls_log)")
    evidence = src.find("report_evidence.assess(user_text, full_response, tool_calls_log)")
    assert avail > 0, "可用性核验收口缺失"
    assert evidence > 0 and avail > evidence, "可用性核验须在证据治理之后"
    assert verify > avail, "可用性核验须先于自检（自检看更正后文本）"
