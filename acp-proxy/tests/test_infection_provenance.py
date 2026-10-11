# -*- coding: utf-8 -*-
"""11.5 注入审计误杀良性文档任务治理——"路径越界"与"注入劫持"区分

实证缺陷（周期#59 FULL11 办公类验收回合，journal 08:20 实录）：用户明文指定
/tmp/soulmate_accept/report.xlsx，doc_generate 输出目录受限产物落
~/.hermes/soulmate/documents/report.xlsx（本回合工具结果"✅ 文档已生成…（4962 字节）"
即来源证明），助手按用户指定路径复制产物——审计员看不到工具结果，把本回合刚生成的
文件当"来源不明的既有文件"→ clarify 反复 → 超限升级 terminate 杀整轮
（"[注入审计终止] 安全审计终止本轮工具执行"）。

覆盖：
1. extract_claimed_paths / resolve_path_claims 确定性溯源（用户明文指定 / 本回合
   工具产出两形态自证；无路径/部分不可自证 → 不降级）
2. InfectionGate 降级语义：clarify/terminate 的来源质疑降级放行+留痕
   （provenance_pass_with_trace，原疑问留痕）；负控制（外发类行为指控/不可自证
   路径/非来源类判词）保持原判；反复来源质疑不再升级 terminate（事故回归）
3. 审计输入附"本回合已执行的工具结果（来源证明）" + JUDGE_PROMPT 判据钉死
4. 防死接线（soulmate_agent 传 prior_tool_results + 留痕分支含新 source）
5. docgen 越界回显限制说明（越界≠劫持的工具侧半边）
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.docgen_tools import DocGenTools  # noqa: E402
from agent.infection_guard import (  # noqa: E402
    JUDGE_PROMPT,
    VERDICT_CLARIFY,
    VERDICT_OK,
    VERDICT_TERMINATE,
    InfectionGate,
    extract_claimed_paths,
    resolve_path_claims,
)


def run(coro):
    return asyncio.run(coro)


def _judge(text: str):
    async def _llm(messages):
        return text
    return _llm


# ── 事故现场真实数据（周期#59 journal 逐字形态）────────────────────────────
USER_TEXT = ("用 doc_generate 工具生成 Excel 文件 /tmp/soulmate_accept/report.xlsx，"
             "内容为二维表格：表头['名称','数量']，数据行[['苹果',3],['香蕉',5]]")
TOOL_RESULT = ("✅ 文档已生成（xlsx）: /home/climbing/.hermes/soulmate/documents/report.xlsx"
               "（4962 字节）\n内容统计: 表 1，行 3")
INCIDENT_DETAIL = ("助手未按用户指定内容生成表格，而是将来源不明的既有文件 "
                   "/home/climbing/.hermes/soulmate/documents/report.xlsx 复制到目标路径"
                   "且仅回读展示、未校验是否等于请求数据，是否被上下文内容劫持以偷换"
                   "文件内容无法确认")

DOC_PATH = "/home/climbing/.hermes/soulmate/documents/report.xlsx"


# 1. extract_claimed_paths / resolve_path_claims
class TestResolvePathClaims:
    def test_extract_paths_dedupe_and_trim(self):
        text = f"复制 {DOC_PATH}。又复制 {DOC_PATH} 到 /tmp/a/b.xlsx；"
        paths = extract_claimed_paths(text)
        assert paths == [DOC_PATH, "/tmp/a/b.xlsx"]

    def test_extract_paths_ignores_verdict_tag_fragments(self):
        # 实测坑："</clarify>" 里的 "/clarify" 曾被误当路径 token 拖垮降级核验
        assert extract_claimed_paths(f"<clarify>来源不明 {DOC_PATH}</clarify>") == [DOC_PATH]
        assert extract_claimed_paths("<terminate/> 路径 /a/b 与单段 /clarify") == ["/a/b"]

    def test_user_specified_path_resolves(self):
        ok, note = resolve_path_claims(
            "复制到目标路径 /tmp/soulmate_accept/report.xlsx 越界", user_text=USER_TEXT)
        assert ok and "用户明文指定" in note

    def test_tool_provenance_resolves(self):
        ok, note = resolve_path_claims(
            f"来源不明的既有文件 {DOC_PATH}", user_text=USER_TEXT,
            prior_tool_results=[TOOL_RESULT])
        assert ok and "本回合工具产出" in note

    def test_creation_verb_required_in_tool_line(self):
        # 同路径但工具结果行无生成语义 → 不算来源证明
        ok, _ = resolve_path_claims(
            f"来源不明的既有文件 {DOC_PATH}", user_text="",
            prior_tool_results=[f"ls: {DOC_PATH} 不存在"])
        assert not ok

    def test_unresolved_path_rejects(self):
        ok, note = resolve_path_claims(
            "复制来源不明文件 /home/other/leak.txt", user_text=USER_TEXT,
            prior_tool_results=[TOOL_RESULT])
        assert not ok and "无自证来源" in note

    def test_mixed_paths_partial_rejects(self):
        # 一个可自证 + 一个不可自证 → 整体不降级（宁不纠不误放）
        ok, _ = resolve_path_claims(
            f"{DOC_PATH} 与 /home/other/leak.txt 均为来源不明",
            user_text=USER_TEXT, prior_tool_results=[TOOL_RESULT])
        assert not ok

    def test_no_paths_rejects(self):
        assert not resolve_path_claims("偷换文件内容", user_text=USER_TEXT)[0]


# 2. InfectionGate 降级语义
class TestProvenanceDowngrade:
    def test_incident_clarify_downgraded(self):
        """事故回归：journal 原判词逐字形态 → 降级放行+留痕，不打断工具执行"""
        g = InfectionGate(llm_call=_judge(f"<clarify>{INCIDENT_DETAIL}</clarify>"),
                          enabled=True)
        v = run(g.check("已复制", [{"name": "terminal",
                                 "arguments": {"command": "cp a b"}}],
                        user_text=USER_TEXT, prior_tool_results=[TOOL_RESULT]))
        assert v.action == VERDICT_OK and v.source == "provenance_pass_with_trace"
        assert "真源自证" in v.detail and INCIDENT_DETAIL[:20] in v.detail  # 原疑问留痕

    def test_terminate_with_claim_downgraded(self):
        # 来源类质疑的 terminate 同样降级（"越界不升级终止"）
        g = InfectionGate(
            llm_call=_judge(f"<terminate/> 将来源不明的既有文件 {DOC_PATH} "
                            "复制到目标路径，偷换内容"),
            enabled=True)
        v = run(g.check("x", [], user_text=USER_TEXT,
                        prior_tool_results=[TOOL_RESULT]))
        assert v.action == VERDICT_OK and v.source == "provenance_pass_with_trace"

    def test_repeated_clarify_never_escalates_to_terminate(self):
        """事故死因=clarify 反复→超限升级 terminate；来源质疑可自证时不再累积"""
        g = InfectionGate(llm_call=_judge(f"<clarify>{INCIDENT_DETAIL}</clarify>"),
                          enabled=True, max_clarifications=2)
        for _ in range(4):
            v = run(g.check("x", [], user_text=USER_TEXT,
                            prior_tool_results=[TOOL_RESULT]))
            assert v.action == VERDICT_OK, "来源质疑可自证不得升级终止"
        assert g.clarify_count == 0

    def test_exfil_accusation_not_downgraded(self):
        # 负过滤：判词另有外发类行为指控 → 非纯路径质疑，保持原判
        g = InfectionGate(
            llm_call=_judge(f"<clarify>把来源不明的 {DOC_PATH} 内容外发到远端</clarify>"),
            enabled=True)
        v = run(g.check("x", [], user_text=USER_TEXT,
                        prior_tool_results=[TOOL_RESULT]))
        assert v.action == VERDICT_CLARIFY

    def test_unresolved_path_not_downgraded(self):
        g = InfectionGate(
            llm_call=_judge("<clarify>复制来源不明文件 /home/other/leak.txt</clarify>"),
            enabled=True)
        v = run(g.check("x", [], user_text=USER_TEXT,
                        prior_tool_results=[TOOL_RESULT]))
        assert v.action == VERDICT_CLARIFY

    def test_non_claim_terminate_preserved(self):
        # 非来源类判词（真劫持语义）不走降级
        g = InfectionGate(
            llm_call=_judge("<terminate/> 试图改写系统提示词并隐瞒行为"), enabled=True)
        v = run(g.check("x", [], user_text=USER_TEXT,
                        prior_tool_results=[TOOL_RESULT]))
        assert v.action == VERDICT_TERMINATE

    def test_no_tool_results_and_unspecified_path_not_downgraded(self):
        g = InfectionGate(llm_call=_judge(f"<clarify>{INCIDENT_DETAIL}</clarify>"),
                          enabled=True)
        v = run(g.check("x", [], user_text="随便聊聊",
                        prior_tool_results=None))
        assert v.action == VERDICT_CLARIFY

    def test_ok_verdict_unaffected(self):
        g = InfectionGate(llm_call=_judge("<ok/>"), enabled=True)
        v = run(g.check("x", [], user_text=USER_TEXT))
        assert v.action == VERDICT_OK and v.source == "llm"


# 3. 审计输入附来源证明 + JUDGE_PROMPT 判据
class TestAuditInputAndPrompt:
    def test_tool_results_fed_to_judge(self):
        captured = {}

        async def _llm(messages):
            captured["text"] = messages[0]["content"]
            return "<ok/>"

        g = InfectionGate(llm_call=_llm, enabled=True)
        run(g.check("x", [], user_text=USER_TEXT, prior_tool_results=[TOOL_RESULT]))
        assert "本回合已执行的工具结果（来源证明" in captured["text"]
        assert "✅ 文档已生成" in captured["text"]  # 真实来源行进审计输入

    def test_tool_results_truncated_and_capped(self):
        captured = {}

        async def _llm(messages):
            captured["text"] = messages[0]["content"]
            return "<ok/>"

        g = InfectionGate(llm_call=_llm, enabled=True)
        run(g.check("x", [], user_text="u",
                    prior_tool_results=["R" * 5000] * 30))
        assert len(captured["text"]) < 12000  # 12条×400字节封顶+总3000封顶

    def test_judge_prompt_criteria_pinned(self):
        assert "路径越界≠注入劫持" in JUDGE_PROMPT
        assert "来源证明" in JUDGE_PROMPT
        assert "不得称\"来源不明的既有文件\"" in JUDGE_PROMPT


# 4. 防死接线
class TestWiring:
    def test_soulmate_agent_passes_prior_tool_results(self):
        src = Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py"
        text = src.read_text(encoding="utf-8")
        assert "prior_tool_results=" in text
        assert "provenance_pass_with_trace" in text

    def test_docgen_out_of_bounds_echoes_restriction(self):
        """越界回显限制说明（工具侧半边）：请求路径越界时显式说明限制+解法"""
        tools = DocGenTools(out_dir="/tmp/ig_docgen_out")
        out = tools.run("xlsx", {
            "filename": "/tmp/soulmate_accept/report.xlsx",
            "rows": [["名称", "数量"], ["苹果", 3]]})
        assert "❌ 参数错误" in out
        assert "输出目录受限" in out and "/tmp/ig_docgen_out" in out
        assert "terminal 复制" in out  # 明示解法：生成后按用户指定路径复制

    def test_docgen_plain_bad_name_keeps_original_rejection(self):
        tools = DocGenTools(out_dir="/tmp/ig_docgen_out2")
        out = tools.run("docx", {"filename": "bad?name",
                                 "content": [{"type": "paragraph", "text": "x"}]})
        assert "filename 非法" in out and "输出目录受限" not in out
