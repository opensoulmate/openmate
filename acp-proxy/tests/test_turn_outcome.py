"""回合结局判定单一真源 turn_outcome_success — 回归测试

背景（06:47轮遗留#2销账）：回合结局启发式 `"错误" not in text and "error" not in
text.lower()` 是任意子串匹配——正常回答讨论error/报错/失败案例即被误判失败：
① 技能usage回执success=False → success_rate被无辜稀释（指标说谎）
② 能力评估accuracy按0.3记分（指标说谎）
③ 技能提取被整轮跳过（成功回合学不到东西="不进化"的度量假象之一）
④ 语义缓存拒绝缓存含"error"字样的优质回复

修法（llm_outcome_status同哲学，见trajectory_recorder.turn_outcome_success）：
只认结构化失败标记（行首「错误:/X失败:」前缀、tool_errors/edit_safety固定
格式、[MCP工具调用失败]/[CODE_MODE]批量执行失败等机器生成标记），不认
"错误/error"任意子串。工具结果侧结局统一走read_turn.result_ok（首部标记）。

三层锁死：
① turn_outcome_success 单元判定（真实机器生成报错形态逐一对）
② _report_skill_usage 回执载荷（误判修复+失败照旧诚实）
③ _prompt_inner 真实路径（extract门+能力评估accuracy）+ 工具审计/缓存真实路径
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from agent.trajectory_recorder import llm_outcome_status, turn_outcome_success
from agent.tool_output_handler import ToolOutputHandler
from tests.test_plan_execution import build_env, run_prompt
from tests.test_skill_usage_receipt import FakeResp
from tests.test_steering import make_agent, run
from agent import turn_lifecycle


# ════════════════════════════════════════════════════════════════
# ① turn_outcome_success 单元判定
# ════════════════════════════════════════════════════════════════

class TestTurnOutcomeSuccess:
    def test_english_error_midtext_is_success(self):
        """"error"字样出现在正常讨论里≠失败（旧启发式的头号误伤）"""
        assert turn_outcome_success(
            "Python error handling 分三种模式：try/except、context manager、返回码。"
            "处理 error 时注意不要吞掉原始堆栈。")

    def test_chinese_error_midtext_is_success(self):
        """"报错/错误信息/失败案例"出现在正文里≠失败"""
        assert turn_outcome_success(
            "这个报错的原因是参数缺失。上面的错误信息说明配置有误，"
            "我们先看错误日志。如果任务失败可以重试，失败案例见下文。")

    def test_empty_and_none_is_success(self):
        assert turn_outcome_success("") and turn_outcome_success(None)

    def test_structured_error_prefix(self):
        """「错误: …」行首前缀（session_recall.py:577等工具分支兜底形态）"""
        assert not turn_outcome_success("错误: 文件不存在")
        assert not turn_outcome_success("错误：溢出文件不存在 — /tmp/spill/x.txt")

    def test_x_failure_prefix(self):
        """「写入失败:/执行失败:/搜索失败:」等X失败: 前缀（soulmate各工具兜底）"""
        for text in ("写入失败: permission denied", "执行失败: boom",
                     "搜索失败: connection reset", "clarify失败: timeout"):
            assert not turn_outcome_success(text), text

    def test_prose_error_report_leading_line(self):
        """行首「执行遇到错误：…」类失败汇报仍是失败（保留既有语义）"""
        assert not turn_outcome_success("执行遇到错误：工具没有返回结果")

    def test_tool_errors_format(self):
        """tool_errors.to_model_message 固定格式「工具 `X` 调用失败 (第N次尝试)」"""
        assert not turn_outcome_success(
            "🌐 工具 `read_file` 调用失败 (第1次尝试)\n"
            "错误类别: not_found\n错误信息: xxx\n建议: 检查路径是否正确")

    def test_edit_safety_format(self):
        """edit_safety.py:68「❌ 文件 `path` 写入失败: …」/「❌ 改进失败: …」"""
        assert not turn_outcome_success("❌ 文件 `/tmp/x.py` 写入失败: disk full")
        assert not turn_outcome_success("❌ 改进失败: 模型未给出修复")

    def test_machine_markers(self):
        """[MCP 工具调用失败]/[CODE_MODE] 批量执行失败/[被拦截 等机器生成标记"""
        assert not turn_outcome_success("[MCP 工具调用失败] HTTP 500: upstream")
        assert not turn_outcome_success("[CODE_MODE] 批量执行失败: NameError")
        assert not turn_outcome_success("🛡️ [被拦截] 注入审计终止")

    def test_infra_llm_failure_still_failure(self):
        """LLM基础设施失败（llm_outcome_status=error）保持失败判定"""
        assert llm_outcome_status("推理错误: LLM API error 402") == "error"
        assert not turn_outcome_success("推理错误: LLM API error 402")
        assert not turn_outcome_success("正常回答\n[LLM错误: 402]")

    def test_english_error_line_prefix(self):
        """行首「Error: …」「Exception: …」=结构化报错前缀"""
        assert not turn_outcome_success("Error: file not found")
        assert not turn_outcome_success("exception: connection refused")

    def test_structured_error_line_inside_long_reply(self):
        """长回复中间夹一行结构化报错=失败（行锚定不受位置影响）"""
        assert not turn_outcome_success(
            "步骤一完成。\n错误: read_file执行失败 — no such file\n请检查路径后重试。")

    def test_normal_multiline_reply_is_success(self):
        assert turn_outcome_success(
            "## 任务执行报告\n\n**目标**: 分析数据\n\n"
            "✅ **步骤1**: 读取文件\n   结果: 正常\n\n"
            "总结：数据里的错误类型分布如下（仅为统计描述）。")


# ════════════════════════════════════════════════════════════════
# ①-bis 小节标题误伤面收敛（「常见错误：」类行首标题≠失败汇报）
# ════════════════════════════════════════════════════════════════

class TestHeadingFalsePositives:
    def test_common_errors_heading_is_success(self):
        """「常见错误：」小节标题曾把整回合误判成失败（残余误伤面销账）——
        技能提取被挡在门外+usage回执记FAIL+缓存拒收，全是假信号"""
        assert turn_outcome_success(
            "## 常见错误：\n1. 忘记加引号\n2. 路径写错\n按上述步骤排查即可。")

    def test_various_heading_forms_are_success(self):
        for text in ("典型失败：汇总如下。", "几种常见错误与失败：略。",
                     "其他错误：见附录。", "关于失败的说明：略。"):
            assert turn_outcome_success(text), text

    def test_heading_filter_never_spares_machine_errors(self):
        """标题过滤不误放真报错——动作动词前缀不含标题线索词"""
        for text in ("写入失败: permission denied", "解析错误: unexpected EOF",
                     "未知错误: xxx", "校验失败: checksum mismatch",
                     "执行遇到错误：工具没有返回结果"):
            assert not turn_outcome_success(text), text


# ════════════════════════════════════════════════════════════════
# ①-ter ⚠️警告≠❌失败细分（00:12轮遗留#3回合侧同源）
# ════════════════════════════════════════════════════════════════

class TestWarnLineOutcome:
    def test_warn_failure_line_is_failure(self):
        # 「⚠️ …不可用: …」类机器失败告警行=失败必须可见（此前漏判=假成功
        # →usage回执记success+语义缓存收失败回复）
        for text in ("⚠️ wakeup调度不可用: conn refused",
                     "任务中止。\n⚠️ 文件不存在: /tmp/x.py"):
            assert not turn_outcome_success(text), text

    def test_warn_degraded_warning_is_success(self):
        # 降级告警/信息提示≠失败
        for text in ("⚠️ 3个文件被跳过，其余成功",
                     "任务完成。\n⚠️ WARNING: disk 90%"):
            assert turn_outcome_success(text), text


# ════════════════════════════════════════════════════════════════
# ② _report_skill_usage 回执载荷（真实调用点）
# ════════════════════════════════════════════════════════════════

class _CaptureClient:
    """httpx.AsyncClient替身：/usage全捕获。"""

    def __init__(self):
        self.usage_posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None, **kw):
        if "/usage" in url:
            self.usage_posts.append((url, json))
            return FakeResp(200, {"updated": True})
        return FakeResp(200, {})


class TestReportUsageOutcome:
    def test_error_discussing_reply_receipted_success(self, tmp_path):
        """误判修复实锤：讨论error/报错的成功回合→success=True（旧启发式False）"""
        agent = make_agent(tmp_path)
        client = _CaptureClient()
        ok = run(agent._report_skill_usage(
            client, ["skill_a"],
            full_response="处理 error 时先看日志。这个报错的原因是超时，已重试成功。",
            close_reason=turn_lifecycle.CLOSE_COMPLETED))
        assert ok == 1
        assert client.usage_posts[0][1] == {"success": True}

    def test_english_error_reply_receipted_success(self, tmp_path):
        """英文error字样正文≠失败（旧启发式按"error"子串判False）"""
        agent = make_agent(tmp_path)
        client = _CaptureClient()
        ok = run(agent._report_skill_usage(
            client, ["skill_b"],
            full_response="Done. Note: unhandled error classes in Python are BaseException.",
            close_reason=turn_lifecycle.CLOSE_COMPLETED))
        assert ok == 1
        assert client.usage_posts[0][1] == {"success": True}

    def test_structured_failure_reply_receipted_failure(self, tmp_path):
        """结构化失败标记→success=False（成败诚实不回归）"""
        agent = make_agent(tmp_path)
        client = _CaptureClient()
        ok = run(agent._report_skill_usage(
            client, ["skill_c"],
            full_response="写入失败: permission denied",
            close_reason=turn_lifecycle.CLOSE_COMPLETED))
        assert ok == 1
        assert client.usage_posts[0][1] == {"success": False}


# ════════════════════════════════════════════════════════════════
# ③ _prompt_inner 真实路径：extract门 + 能力评估accuracy
# ════════════════════════════════════════════════════════════════

class _PostCapture:
    """httpx.AsyncClient替身：捕获全部POST（url, json）。"""

    posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, timeout=None, **kw):
        _PostCapture.posts.append((url, json))
        return FakeResp(200, {})

    async def get(self, url, timeout=None, **kw):
        return FakeResp(200, {})

    async def patch(self, url, json=None, timeout=None, **kw):
        return FakeResp(200, {})


_TOOL_CALL = {"tool_calls": [{
    "id": "tc1",
    "function": {"name": "read_file",
                 "arguments": json.dumps({"path": "/tmp/none.txt"})},
}]}


def _build_simple(tmp_path, monkeypatch, reply_text):
    """驱动 _prompt_inner 简单任务路径（真实 _run_llm_with_tools 工具轮）。

    空llm_call_responses→TaskPlanner.plan不续旧计划→简单路径（04:19轮教训：
    该路径撞缺_tracer被LLM error兜底吞，必须补_tracer/_token_analyzer/_cost_tracker）
    """
    agent, _ = build_env(
        tmp_path, monkeypatch,
        steps=[("step_1", "A")],
        llm_scripts=[[_TOOL_CALL], [reply_text]],
        llm_call_responses=[],
    )
    agent._tracer = MagicMock()
    agent._token_analyzer = MagicMock()
    agent._cost_tracker = MagicMock()
    monkeypatch.setattr(httpx, "AsyncClient", _PostCapture)
    return agent


class TestPromptInnerGates:
    def test_error_reply_still_extracted_and_scored_high(self, tmp_path, monkeypatch):
        """误判修复实锤：正文带"错误"字样的成功回合→extract照常上报+accuracy=0.8

        旧门 `"错误" not in full_response` 会跳过extract（成功回合学不到东西）
        并把accuracy记成0.3（指标说谎）。
        """
        _PostCapture.posts = []
        reply = "检查完毕，未发现错误，数据一切正常，error 处理逻辑已经覆盖。"
        agent = _build_simple(tmp_path, monkeypatch, reply)
        run_prompt(agent)
        # 真实简单路径跑通（LLM工具轮+最终回复落session）
        joined = " ".join(m.get("content", "") for m in agent.sessions["s1"]["messages"])
        assert reply in joined
        urls = [u for u, _ in _PostCapture.posts]
        assert any("/api/gene/skill/extract" in u for u in urls), urls
        cap = [p for u, p in _PostCapture.posts
               if "/api/benchmark/capability/evaluate" in u]
        assert cap, urls
        assert cap[0]["accuracy"] == 0.8
        assert cap[0]["details"]["success"] is True

    def test_structured_failure_reply_not_extracted(self, tmp_path, monkeypatch):
        """真实失败回合（结构化标记）→extract不上报+accuracy=0.3（不回归）"""
        _PostCapture.posts = []
        agent = _build_simple(tmp_path, monkeypatch, "写入失败: permission denied")
        run_prompt(agent)
        urls = [u for u, _ in _PostCapture.posts]
        assert not any("/api/gene/skill/extract" in u for u in urls), urls
        cap = [p for u, p in _PostCapture.posts
               if "/api/benchmark/capability/evaluate" in u]
        assert cap and cap[0]["accuracy"] == 0.3
        assert cap[0]["details"]["success"] is False


# ════════════════════════════════════════════════════════════════
# ④ 工具审计/缓存真实路径（result_ok单一真源）
# ════════════════════════════════════════════════════════════════

def _wire_tool_agent(tmp_path, scripts):
    """test_tool_output_wiring同款harness：真实read_file工具路径"""
    agent = make_agent(tmp_path, scripts=scripts)
    agent._permission_gate = MagicMock()
    agent._permission_gate.check = _allow
    agent._output_handler = ToolOutputHandler(char_threshold=8000)
    agent._tool_cache = MagicMock()
    agent._tool_cache.get.return_value = None
    agent._tool_auditor = MagicMock()
    agent._tool_error_handler = MagicMock()
    agent._todo_list = []
    return agent


async def _allow(session_id, tool_name, tool_args, **kw):
    return MagicMock(allowed=True)


class TestToolAuditAndCache:
    def test_read_result_with_error_word_audited_success(self, tmp_path):
        """内容里带"错误"字样的读取结果→审计success=True+可缓存（旧子串判False）"""
        f = tmp_path / "data.txt"
        f.write_text("正常数据行A\n" * 10 + "统计口径里提到错误分类的行\n", encoding="utf-8")
        step = {"tool_calls": [{
            "id": "tc1",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": str(f)})},
        }]}
        agent = _wire_tool_agent(tmp_path, [[step], ["done"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "read it"}], "s1"))
        assert agent._tool_auditor.audit_call.called
        kwargs = agent._tool_auditor.audit_call.call_args.kwargs
        assert kwargs["success"] is True
        assert "错误" in kwargs["result"]  # 前提：结果确实带"错误"字样
        assert agent._tool_cache.put.called

    def test_failed_read_result_audited_failure(self, tmp_path):
        """结构化失败结果（「错误: …」前缀）→success=False+不缓存（不回归）"""
        step = {"tool_calls": [{
            "id": "tc1",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": str(tmp_path / "none.txt")})},
        }]}
        agent = _wire_tool_agent(tmp_path, [[step], ["done"]])
        run(agent._run_llm_with_tools([{"role": "user", "content": "read it"}], "s1"))
        kwargs = agent._tool_auditor.audit_call.call_args.kwargs
        assert kwargs["success"] is False
        assert not agent._tool_cache.put.called
