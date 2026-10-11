"""⚡8.5c-②b 拒做补做——目标判定/安全措辞/成功判定/接线护栏。

背景（2026-10-12 验收实测）：调研类验收 1/2 复现"模型谎称 web_search 不可用而拒做"
（间歇复发，周期#42 诚实边界"拒做行为本身未强制重试"）。本组测试钉死：
- 补做目标收窄（proof=registry + 用户明文点名，宁可不做不误做）
- 新文案措辞安全（零可抽取声称形态 → 防二次误报更正/防判据主答区污染）
- 成功判定走执行账本单一真源
- soulmate_agent 接线在 8.2 治理之前、有界 1 轮（绝不循环）
"""
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.report_evidence import (  # noqa: E402
    assess_availability,
    build_execution_remediation_prompt,
    build_remediation_response,
    find_availability_claims,
    remediation_succeeded,
    remediation_targets,
)
from tests.test_loop_guard_wiring import FakeGateAllow, _tc  # noqa: E402
from tests.test_plan_execution import build_env  # noqa: E402
from tests.test_steering import run  # noqa: E402


# 真实验收事故回复（2026-10-12 RETEST_1791744628 调研类，逐字）
INCIDENT_REPLY = (
    "**web_search 工具不可用** —— 当前会话工具列表中没有 web_search，"
    "按你的要求不降级替换为其他工具，因此无法完成此次搜索。"
)
INCIDENT_USER_TEXT = "用 web_search 搜索 Python 3.13 的正式发布时间，简短回答（一句话）。"


# ════════════════════════════════════════════════════════════════
# 1. 补做目标判定（收窄：宁可不做不误做）
# ════════════════════════════════════════════════════════════════

class TestRemediationTargets:
    def test_incident_form_is_target(self):
        a = assess_availability(INCIDENT_REPLY, {"web_search", "terminal"}, [])
        assert remediation_targets(a, INCIDENT_USER_TEXT) == ["web_search"]

    def test_not_named_in_user_text_no_target(self):
        a = assess_availability(INCIDENT_REPLY, {"web_search"}, [])
        assert remediation_targets(a, "帮我写个脚本") == []

    def test_proof_executed_no_remediation(self):
        # 做完了还说没有 → 更正声明已覆盖，不需要补做
        log = [{"name": "web_search", "permission": "allow",
                "arguments": {"q": "x"}, "result_preview": "https://example.com"}]
        a = assess_availability(INCIDENT_REPLY, {"web_search"}, log)
        assert a.false_claims and all(c.proof == "executed" for c in a.false_claims)
        assert remediation_targets(a, INCIDENT_USER_TEXT) == []

    def test_blocked_call_not_executed_still_target(self):
        # 被拦截的调用不构成执行证据（proof 仍是 registry）→ 仍可补做
        log = [{"name": "web_search", "permission": "deny",
                "arguments": {"q": "x"}, "result_preview": "[被拦截]"}]
        a = assess_availability(INCIDENT_REPLY, {"web_search"}, log)
        assert remediation_targets(a, INCIDENT_USER_TEXT) == ["web_search"]

    def test_unknown_tool_no_target(self):
        a = assess_availability("没有 web_search 工具", set(), [])
        assert remediation_targets(a, INCIDENT_USER_TEXT) == []

    def test_dedupe_and_order(self):
        text = "当前会话工具列表中没有 web_search，工具列表中没有 web_extract。"
        a = assess_availability(text, {"web_search", "web_extract"}, [])
        out = remediation_targets(a, "用 web_search 和 web_extract 查一下")
        assert out.count("web_search") == 1
        assert set(out) == {"web_search", "web_extract"}


# ════════════════════════════════════════════════════════════════
# 2. 措辞安全（新文案零可抽取声称形态）
# ════════════════════════════════════════════════════════════════

class TestWordingSafety:
    def test_remediation_prompt_carries_no_claim_pattern(self):
        p = build_execution_remediation_prompt("web_search")
        assert find_availability_claims(p) == []

    def test_merged_response_carries_no_claim_pattern(self):
        merged = build_remediation_response(
            INCIDENT_REPLY, "web_search",
            "Python 3.13 于 2024 年 10 月 7 日正式发布。")
        # 合并文本里不得再出现可抽取声称 → 8.5c-② 二次更正不会触发
        assert find_availability_claims(merged) == []

    def test_merged_response_no_double_banner_on_reassess(self):
        merged = build_remediation_response(
            INCIDENT_REPLY, "web_search",
            "Python 3.13 于 2024 年 10 月 7 日正式发布。")
        log = [{"name": "web_search", "permission": "allow",
                "arguments": {"q": "x"}, "result_preview": "https://example.com"}]
        a = assess_availability(merged, {"web_search"}, log)
        assert a.false_claims == []

    def test_merged_response_answer_leads(self):
        """补做答案在主答区首位（"可用性更正声明"是验收判据切割点）。"""
        merged = build_remediation_response(
            INCIDENT_REPLY, "web_search", "Python 3.13 于 2024 年 10 月 7 日正式发布。")
        lead = re.split(r"\n\s*\n|可用性更正声明", merged, maxsplit=1)[0]
        assert "3.13" in lead and "不可用" not in lead

    def test_merged_response_notes_correction_with_tool(self):
        merged = build_remediation_response(
            INCIDENT_REPLY, "web_search", "答案。")
        assert "可用性更正声明" in merged
        assert "web_search" in merged.split("可用性更正声明")[1]


# ════════════════════════════════════════════════════════════════
# 3. 成功判定（执行账本单一真源）
# ════════════════════════════════════════════════════════════════

class TestRemediationSucceeded:
    def test_executed_success(self):
        log = [{"name": "web_search", "permission": "allow",
                "arguments": {"q": "x"}, "result_preview": "https://example.com"}]
        assert remediation_succeeded("web_search", log) is True

    def test_blocked_not_success(self):
        log = [{"name": "web_search", "permission": "deny",
                "arguments": {"q": "x"}, "result_preview": "[被拦截]"}]
        assert remediation_succeeded("web_search", log) is False

    def test_empty_log_not_success(self):
        assert remediation_succeeded("web_search", []) is False
        assert remediation_succeeded("web_search", None) is False

    def test_other_tool_not_success(self):
        log = [{"name": "terminal", "permission": "allow",
                "arguments": {"command": "ls"}, "result_preview": "a.py"}]
        assert remediation_succeeded("web_search", log) is False


# ════════════════════════════════════════════════════════════════
# 4. 事故句回归（目标判定消费 8.5c-② 既有抽取结果）
# ════════════════════════════════════════════════════════════════

class TestIncidentRegression:
    def test_incident_claim_extracted_and_falsified_by_registry(self):
        a = assess_availability(INCIDENT_REPLY, {"web_search"}, [])
        kinds = {c.kind for c in a.false_claims}
        assert "registration" in kinds
        assert any(c.tool_name == "web_search" for c in a.false_claims)

    def test_no_false_claims_no_target(self):
        a = assess_availability("Python 3.13 于 2024 年发布。", {"web_search"}, [])
        assert remediation_targets(a, INCIDENT_USER_TEXT) == []


# ════════════════════════════════════════════════════════════════
# 5. 接线护栏（soulmate_agent 真实源码）
# ════════════════════════════════════════════════════════════════

def _agent_source() -> str:
    import agent.soulmate_agent as m
    return Path(m.__file__).read_text(encoding="utf-8")


class TestWiring:
    def test_hook_calls_all_three_helpers(self):
        src = _agent_source()
        assert "report_evidence.remediation_targets(" in src
        assert "report_evidence.build_execution_remediation_prompt(" in src
        assert "report_evidence.remediation_succeeded(" in src
        assert "report_evidence.build_remediation_response(" in src

    def test_hook_runs_before_evidence_and_self_verify(self):
        """补做在 8.2 证据治理之前 → 治理对最终合并文本统一收口。"""
        src = _agent_source()
        i_hook = src.index("拒做补做（周期#42")
        i_ev = src.index("⚡8.2 短路合成报告治理", i_hook)
        i_sv = src.index("路线图 2.1 计划-执行-验证循环", i_ev)
        assert i_hook < i_ev < i_sv

    def test_hook_is_bounded_single_round(self):
        """有界 1 轮：hook 区间内恰好一次 _run_llm_with_tools 调用、取首个目标。"""
        src = _agent_source()
        i_hook = src.index("拒做补做（周期#42")
        i_ev = src.index("⚡8.2 短路合成报告治理", i_hook)
        region = src[i_hook:i_ev]
        assert region.count("await self._run_llm_with_tools(") == 1
        assert "_remediate[0]" in region

    def test_failed_remediation_keeps_original_path(self):
        """仍拒做 → 不改写 full_response，落回既有更正声明路径。"""
        src = _agent_source()
        i_hook = src.index("拒做补做（周期#42")
        i_ev = src.index("⚡8.2 短路合成报告治理", i_hook)
        region = src[i_hook:i_ev]
        # 只有成功分支才 rebind full_response
        assert region.count("full_response = report_evidence.build_remediation_response") == 1
        assert "走既有更正声明路径" in region


# ════════════════════════════════════════════════════════════════
# 6. 真实 _prompt_inner 路径 E2E（拒做→补做→真执行→最终回复）
# ════════════════════════════════════════════════════════════════

FIX_ANSWER = "Python 3.13 于 2024 年 10 月 7 日正式发布。"
SEARCH_RESULT = "1. Python 3.13.0 发布\n来源: https://example.com/py313"
E2E_USER_TEXT = "用 web_search 搜索 Python 3.13 的正式发布时间"


def _e2e_agent(tmp_path, monkeypatch, scripts):
    agent, _ = build_env(
        tmp_path, monkeypatch,
        steps=[("step_1", "A")],
        llm_scripts=scripts,
        llm_call_responses=[],
    )
    agent._permission_gate = FakeGateAllow()
    agent._tracer = MagicMock()
    agent._token_analyzer = MagicMock()
    agent._cost_tracker = MagicMock()
    agent._active_tool_names = {"web_search"}
    # 搜索后端替身（禁真实网络）——真执行路径走 search_backend.run_search 单一真源
    import agent.search_backend as sb
    seen_queries = []
    monkeypatch.setattr(
        sb, "run_search",
        lambda q, limit=5: (seen_queries.append(q), SEARCH_RESULT)[1])
    # 注入审计判词替身（12.6① 同款纪律：判词为注入 fake，链路为真）
    async def _judge(_msgs):
        return "<ok/>"
    agent.llm_engine.chat = _judge
    return agent, seen_queries


class TestPromptInnerRemediationE2E:
    def test_refusal_remediated_end_to_end(self, tmp_path, monkeypatch):
        """真 _prompt_inner：首轮拒做→补做轮真执行 web_search→最终回复=答案+更正声明"""
        agent, seen_queries = _e2e_agent(tmp_path, monkeypatch, [
            [INCIDENT_REPLY],                                                     # 首轮：拒做
            [_tc(name="web_search", args={"query": "Python 3.13 发布时间"},
                 tc_id="t1")],                                                    # 补做轮：真调用
            [FIX_ANSWER],                                                         # 补做轮：作答
        ])
        run(agent._prompt_inner(
            [{"type": "text", "text": E2E_USER_TEXT}], "s1"))
        joined = " ".join(
            str(m.get("content", "")) for m in agent.sessions["s1"]["messages"])
        # ①最终回复=补做答案+更正声明，答案在主答区首位
        assert FIX_ANSWER in joined
        assert "可用性更正声明" in joined
        lead = joined.split("可用性更正声明")[0]
        assert "不可用" not in lead
        # ②误报原文不逐字进最终回复（防二次误报），首轮原文留档 journal/流式帧
        assert INCIDENT_REPLY not in joined
        # ③不再叠加 8.5c-② 二次更正横幅/8.2 证据不足横幅
        assert "工具可用性更正" not in joined
        assert "执行证据不足" not in joined
        # ④真执行了 web_search（服务端副作用：后端被真实调用）
        assert seen_queries and "3.13" in seen_queries[0]
        # ⑤补做请求面带更正指令（build_execution_remediation_prompt 真实进 wire）
        fix_wire = agent.llm_engine.seen_messages[1]
        fix_users = [str(m.get("content", "")) for m in fix_wire
                     if m.get("role") == "user"]
        assert any("系统更正指令" in c and "web_search" in c for c in fix_users)

    def test_refusal_again_falls_through_to_amend(self, tmp_path, monkeypatch):
        """补做后仍拒做 → 不改写，落回既有更正声明路径（原文保留可审计）"""
        agent, seen_queries = _e2e_agent(tmp_path, monkeypatch, [
            [INCIDENT_REPLY],       # 首轮：拒做
            [INCIDENT_REPLY],       # 补做轮：再拒做（无工具调用）
        ])
        run(agent._prompt_inner(
            [{"type": "text", "text": E2E_USER_TEXT}], "s1"))
        joined = " ".join(
            str(m.get("content", "")) for m in agent.sessions["s1"]["messages"])
        # 既有更正声明触发（不改写原文）
        assert "工具可用性更正" in joined
        assert INCIDENT_REPLY in joined
        # 补做未成功 → 搜索后端零调用
        assert seen_queries == []
