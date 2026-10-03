# -*- coding: utf-8 -*-
"""kilocode supplement3 #4 readTurn快照diff + #6 toolSummary 测试。

调研来源：kilocode-source-supplement3.md
- #4（kilo-memory ports.ts）："readTurn提取user文本+assistant输出+快照diff
  （本轮改动了哪些文件进记忆）——记忆看得到文件diff=能记住'改了什么'"
- #6（MemoryRedact）："工具摘要只留command/file/pattern/query+exit code+
  error brief(220字符)"——工具全量参数绝不进记忆

覆盖：
1. summarize_args：白名单参数轮廓 + 220字符截断 + 非白名单键（content/code）绝不进
2. result_ok：失败前缀启发式（头部判定，正文出现'错误'不算失败）
3. diff_line_stats：行级+/-统计 / old=None增量未知
4. TurnReadView：toolSummary行格式 / 文件快照diff聚合 / 显式截断标注（AIHawk）/
   per-session隔离 / clear / fail-safe绝不抛出
5. MemoryDigestCollector集成：快照diff+工具动作随digest进记忆 / 无内容时空段不进
6. 接线断言：soulmate真实消息路径 record/record_file_change/render+aclose_turn/
   clear 逐项在场（防死接线）
"""
import asyncio
import inspect
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.read_turn import (
    ARG_VALUE_MAX,
    DIFF_MAX_BYTES,
    RECENT_TRACE_MAX,
    TRACE_BRIEF_MAX,
    TurnReadView,
    diff_line_stats,
    render_trace,
    result_ok,
    summarize_args,
)
from agent.turn_lifecycle import (
    CLOSE_COMPLETED,
    CLOSE_INTERRUPTED,
    MemoryDigestCollector,
    TurnLifecycleBus,
)
from agent.soulmate_agent import SoulMateAgent


def run(coro):
    return asyncio.run(coro)


class FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data if data is not None else {}

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, resp=None):
        self.calls = []
        self._resp = resp or FakeResp()

    async def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        return self._resp


# ════════════════════════════════════════════════════════════════
# 1. summarize_args（kilocode #6 MemoryRedact toolSummary规格）
# ════════════════════════════════════════════════════════════════

class TestSummarizeArgs:
    def test_whitelist_only(self):
        """content/code/prompt等大参数绝不进记忆轮廓（MemoryRedact核心）"""
        s = summarize_args("write_file", {
            "path": "/tmp/a.py", "content": "SECRET_BODY " * 100, "code": "print(1)",
        })
        assert "path=/tmp/a.py" in s
        assert "SECRET_BODY" not in s
        assert "print(1)" not in s
        assert "content=" not in s and "code=" not in s

    def test_all_whitelist_keys_kept(self):
        s = summarize_args("terminal", {"command": "ls", "file": "f", "pattern": "p",
                                        "query": "q", "url": "u"})
        for kv in ("command=ls", "file=f", "pattern=p", "query=q", "url=u"):
            assert kv in s

    def test_value_truncated_at_220(self):
        long_val = "x" * 500
        s = summarize_args("terminal", {"command": long_val})
        assert len(s) < 300
        assert "…" in s
        assert "x" * (ARG_VALUE_MAX + 1) not in s

    def test_empty_values_skipped(self):
        assert summarize_args("terminal", {"command": "", "path": ""}) == ""
        assert summarize_args("terminal", None) == ""


# ════════════════════════════════════════════════════════════════
# 2. result_ok 启发式
# ════════════════════════════════════════════════════════════════

class TestResultOk:
    def test_error_prefixes_fail(self):
        for text in ("错误: 文件不存在", "写入失败: perm", "[被拦截:deny] xx",
                     "执行超时（60秒）", "修改失败: x", "⚠️ 不可用"):
            assert not result_ok(text), text

    def test_marker_in_body_beyond_head_is_ok(self):
        # 正文（>60字符后）出现'错误'不算失败——只看头部
        assert result_ok("这是一段普通输出" + "。" * 80 + "错误二字在正文里")

    def test_normal_results_ok(self):
        assert result_ok("已写入 /tmp/a (10 字节)")
        assert result_ok("")
        assert result_ok("[CODE_MODE] ✅ 3次工具调用已合并为1轮执行")

    def test_content_with_marker_words_in_head_is_ok(self):
        # 结构化判定的核心收益：内容里带"错误/失败/超时/异常"字样的正常读取/
        # 搜索结果≠失败（旧60字符子串启发式会把它们全判成假失败→工具审计
        # success指标说谎+成功读取不进结果缓存+记忆工具行误标error）
        assert result_ok("错误处理.md\n1|def handle_error():\n2|    pass")
        assert result_ok("找到 3 个匹配: src/x.py:12: logger.error('错误')")
        assert result_ok("超时时间设置为 60 秒（见配置项 timeout）")
        assert result_ok("异常处理流程分为三步……" + "。" * 30)

    def test_machine_bracket_markers_fail(self):
        # soulmate_agent各分支机器标记（[MCP 工具调用超时]:735等，逐一grep核实）
        for text in ("[MCP 工具调用超时] web_search",
                     "[CODE_MODE] 工具执行超时: read_file",
                     "[MCP resource调用超时] read_resource",
                     "⚠️ wakeup调度不可用: conn refused"):
            assert not result_ok(text), text

    def test_timeout_report_fail_but_timeout_word_in_prose_ok(self):
        assert not result_ok("执行超时（60秒）")  # soulmate_agent.py:2284真实形态
        assert not result_ok("操作超时")
        assert result_ok("超时重试策略见下文")
        assert result_ok("设置 timeout=30 可避免超时")

    def test_heading_first_line_is_ok(self):
        # 首行恰为「常见错误：」类小节标题≠失败
        assert result_ok("常见错误：\n1. 忘记加引号")

    def test_long_path_edit_safety_fail_not_missed(self):
        # 旧60字符截断窗口的漏检面：长路径把「失败」推到60字符外=旧判假成功
        long_path = "/tmp/" + "a" * 80 + ".py"
        assert not result_ok(f"❌ 文件 `{long_path}` 写入失败: disk full")

    def test_first_line_window_semantics(self):
        # 判定窗口=结果首行（机器模板均首行报出）；行2+失败报出不判=显式残余
        assert result_ok("正常输出内容\n错误: boom")
        # 行中散文里的「错误:」引用不算失败（与turn_outcome同哲学）
        assert result_ok("这是一段" + "。" * 80 + "错误: 只是引用示例")

    def test_heading_filter_never_spares_machine_errors(self):
        # 标题过滤不误放真报错（动作动词前缀不含标题线索词）
        for text in ("解析错误: unexpected EOF", "未知错误: xxx",
                     "校验失败: checksum mismatch", "提取失败: empty"):
            assert not result_ok(text), text


# ════════════════════════════════════════════════════════════════
# 3. diff_line_stats（readTurn快照diff行级统计）
# ════════════════════════════════════════════════════════════════

class TestDiffLineStats:
    def test_identical(self):
        assert diff_line_stats("a\nb\n", "a\nb\n") == (0, 0)

    def test_pure_insert(self):
        added, removed = diff_line_stats("a\n", "a\nb\nc\n")
        assert added == 2 and removed == 0

    def test_pure_delete(self):
        added, removed = diff_line_stats("a\nb\nc\n", "a\n")
        assert added == 0 and removed == 2

    def test_replace(self):
        added, removed = diff_line_stats("a\nb\nc\n", "a\nX\nY\nc\n")
        assert added == 2 and removed == 1

    def test_new_file(self):
        added, removed = diff_line_stats("", "a\nb\n")
        assert added == 2 and removed == 0

    def test_unknown_delta_is_none_not_zero(self):
        """old不可得→None（增量未知），绝不假装0改动"""
        assert diff_line_stats(None, "a\n") is None
        assert diff_line_stats("a\n", None) is None


# ════════════════════════════════════════════════════════════════
# 4. TurnReadView
# ════════════════════════════════════════════════════════════════

class TestTurnReadView:
    def test_tool_line_format(self):
        rv = TurnReadView()
        rv.record_tool("s1", "terminal", {"command": "ls"}, ok=True)
        line = rv.render("s1")["tool_actions"]
        assert line == "Tool terminal completed | command=ls | exit=0"

    def test_failed_tool_has_error_brief(self):
        rv = TurnReadView()
        rv.record_tool("s1", "read_file", {"path": "/nope"}, ok=False, error="错误: " + "e" * 500)
        line = rv.render("s1")["tool_actions"]
        assert "exit=1" in line and "error=" in line
        assert "e" * (ARG_VALUE_MAX + 10) not in line  # brief截断220级

    def test_via_tag_for_code_mode(self):
        rv = TurnReadView()
        rv.record_tool("s1", "patch", {"path": "/a"}, ok=True, via="code_mode")
        assert "via=code_mode" in rv.render("s1")["tool_actions"]

    def test_tool_cap_with_explicit_marker(self):
        """超上限显式标注'另有N条'——绝不静默丢弃（AIHawk铁律）"""
        rv = TurnReadView(max_tool_lines=3)
        for i in range(6):
            rv.record_tool("s1", f"t{i}", {"command": str(i)})
        ta = rv.render("s1")["tool_actions"]
        lines = ta.splitlines()
        assert len(lines) == 4  # 3条+1条溢出标注
        assert lines[-1] == "…(另有3条工具调用未列出)"

    def test_file_change_new_file(self):
        rv = TurnReadView()
        rv.record_file_change("s1", "/tmp/new.py", "write", "", "a\nb\nc\n")
        fc = rv.render("s1")["file_changes"]
        assert "/tmp/new.py" in fc and "新建" in fc and "+3/-0行" in fc

    def test_file_change_patch_delta(self):
        rv = TurnReadView()
        rv.record_file_change("s1", "/a.py", "patch", "a\nb\nc\n", "a\nX\n")
        fc = rv.render("s1")["file_changes"]
        assert "+1/-2行" in fc and "patch x1" in fc

    def test_same_file_aggregated(self):
        rv = TurnReadView()
        rv.record_file_change("s1", "/a.py", "write", "", "a\n")
        rv.record_file_change("s1", "/a.py", "patch", "a\n", "a\nb\n")
        fc = rv.render("s1")["file_changes"]
        assert "x2" in fc and "+2/-0行" in fc
        assert "write/patch" in fc

    def test_unknown_delta_explicit(self):
        rv = TurnReadView()
        rv.record_file_change("s1", "/big.py", "write", None, "x\n")
        fc = rv.render("s1")["file_changes"]
        assert "增量未知" in fc and "+0/-0行" in fc

    def test_file_cap_with_explicit_marker(self):
        rv = TurnReadView(max_files=2)
        for i in range(4):
            rv.record_file_change("s1", f"/f{i}", "write", "", "x\n")
        fc = rv.render("s1")["file_changes"]
        assert "…(另有2个文件改动未列出)" in fc
        assert "/f2" not in fc

    def test_session_isolation_and_clear(self):
        rv = TurnReadView()
        rv.record_tool("s1", "terminal", {"command": "ls"})
        rv.record_tool("s2", "terminal", {"command": "pwd"})
        assert "command=ls" in rv.render("s1")["tool_actions"]
        assert "command=pwd" in rv.render("s2")["tool_actions"]
        assert rv.render("s3") == {"file_changes": "", "tool_actions": ""}
        rv.clear("s1")
        assert rv.render("s1") == {"file_changes": "", "tool_actions": ""}
        assert "command=pwd" in rv.render("s2")["tool_actions"]  # clear不误伤

    def test_fail_safe_never_raises(self):
        rv = TurnReadView()
        rv.record_tool("s1", None, "not-a-dict", ok=True)  # 类型失真不炸
        rv.record_file_change("s1", None, None, 123, 456)
        out = rv.render("s1")
        assert isinstance(out, dict)
        rv.clear("s1")

    def test_result_ok_used_in_tool_line(self):
        rv = TurnReadView()
        rv.record_tool("s1", "read_file", {"path": "/x"}, ok=result_ok("错误: 不存在"),
                       error="" if result_ok("错误: 不存在") else "错误: 不存在")
        assert "exit=1" in rv.render("s1")["tool_actions"]


# ════════════════════════════════════════════════════════════════
# 5. MemoryDigestCollector集成（readTurn输入→digest载荷）
# ════════════════════════════════════════════════════════════════

class TestDigestIntegration:
    def _close(self, meta, reason=CLOSE_COMPLETED):
        bus = TurnLifecycleBus()
        client = FakeClient()
        bus.subscribe("turn.close", MemoryDigestCollector(
            client_factory=lambda: client))
        bus.open_turn("s1")
        run(bus.aclose_turn("s1", reason, **meta))
        return client

    def test_file_changes_and_tools_in_digest(self):
        client = self._close({
            "user_text": "改一下配置文件，加一行调试日志" + "，这是一个足够长的用户指令" * 3,
            "full_response": "已完成修改",
            "file_changes": "- /tmp/cfg.py: patch x1 +1/-0行",
            "tool_actions": "Tool patch completed | path=/tmp/cfg.py | exit=0",
        })
        content = client.calls[0]["json"]["content"]
        assert "本轮文件改动（快照diff）" in content
        assert "/tmp/cfg.py" in content
        assert "工具动作（toolSummary）" in content
        assert "Tool patch completed" in content

    def test_no_sections_when_empty(self):
        # should_digest长度门：user_text>50（turn_lifecycle既有语义，fixture必须过门）
        client = self._close({
            "user_text": "你好" * 30, "full_response": "好",
            "file_changes": "", "tool_actions": "",
        })
        content = client.calls[0]["json"]["content"]
        assert "本轮文件改动" not in content and "工具动作" not in content
        assert content.startswith("用户: ")

    def test_partial_sections(self):
        client = self._close({
            "user_text": "u" * 60, "full_response": "r" * 120,
            "tool_actions": "Tool terminal completed | command=ls | exit=0",
        })
        content = client.calls[0]["json"]["content"]
        assert "工具动作" in content and "本轮文件改动" not in content

    def test_interrupted_turn_not_digested_even_with_changes(self):
        """不完整turn即使有文件改动也不进记忆（kilocode #7核心语义不回退）"""
        client = self._close({
            "user_text": "x" * 60, "full_response": "y" * 120,
            "file_changes": "- /a.py: write x1 +1/-0行",
        }, reason=CLOSE_INTERRUPTED)
        assert client.calls == []


# ════════════════════════════════════════════════════════════════
# 6. 接线断言（防死接线——写了≠接线了）
# ════════════════════════════════════════════════════════════════

class TestWiring:
    def test_soulmate_loop_records_tools(self):
        src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        assert src.count("record_tool(") >= 3, "主循环3条记录路径（blocked/cached/正常）必须全在"
        assert src.count("record_file_change(") >= 2, "write_file/patch分支必须记录快照diff"

    def test_code_mode_records(self):
        src = inspect.getsource(SoulMateAgent._code_mode_tool_call)
        assert src.count("record_file_change(") >= 2, "code_mode内层write_file/patch同样记录"
        src2 = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        assert 'via="code_mode"' in src2, "批内调用轮廓必须标注来源"

    def test_close_passes_read_view_and_clears(self):
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert "file_changes=" in src and "tool_actions=" in src, "aclose_turn必须带readTurn两段"
        assert "self._turn_read_view.render(session_id)" in src
        assert src.count("self._turn_read_view.clear(session_id)") >= 2, (
            "正常收尾+cached收尾都要clear防跨turn泄漏")

    def test_error_close_clears(self):
        src = inspect.getsource(SoulMateAgent.prompt)
        assert "self._turn_read_view.clear(session_id)" in src

    def test_write_file_reads_prev_for_diff(self):
        src = inspect.getsource(SoulMateAgent._run_llm_with_tools) + \
            inspect.getsource(SoulMateAgent._code_mode_tool_call)
        assert src.count("_read_prev_for_diff(") >= 2

    def test_digest_collector_consumes_sections(self):
        src = inspect.getsource(MemoryDigestCollector.__call__)
        assert "file_changes" in src and "tool_actions" in src
        assert "本轮文件改动" in src and "工具动作" in src

    def test_init_creates_read_view(self):
        src = inspect.getsource(SoulMateAgent.__init__)
        assert "TurnReadView()" in src

    def test_lazy_property_for_partial_constructs(self):
        """__new__测试双实例也必须能记录（lazy构造，不炸工具循环）"""
        agent = SoulMateAgent.__new__(SoulMateAgent)
        agent._turn_read_view.record_tool("sx", "terminal", {"command": "ls"})
        assert "command=ls" in agent._turn_read_view.render("sx")["tool_actions"]


# ════════════════════════════════════════════════════════════════
# 7. 行为级：_read_prev_for_diff 真实文件读取
# ════════════════════════════════════════════════════════════════

class TestReadPrevForDiff:
    def test_missing_file_is_empty(self):
        assert SoulMateAgent._read_prev_for_diff(None, "/no/such/file-xyz") == ""

    def test_real_file_content(self):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("a\nb\n")
            path = f.name
        try:
            assert SoulMateAgent._read_prev_for_diff(None, path) == "a\nb\n"
        finally:
            os.unlink(path)

    def test_huge_file_is_none(self, monkeypatch):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("x")
            path = f.name
        try:
            monkeypatch.setattr("os.path.getsize", lambda p: DIFF_MAX_BYTES + 1)
            assert SoulMateAgent._read_prev_for_diff(None, path) is None
        finally:
            monkeypatch.undo()
            os.unlink(path)


# ════════════════════════════════════════════════════════════════
# 8. render_trace（kilocode #4第4输入源：recent 8轮trace，ports.ts trace()）
# ════════════════════════════════════════════════════════════════

class TestRenderTrace:
    def test_kilocode_format(self):
        """原文格式逐字：`User: ${body}` / `Assistant: ${body}` / join("\\n\\n")"""
        out = render_trace([
            {"role": "user", "content": "帮我看看配置"},
            {"role": "assistant", "content": "已查看，配置正常"},
        ])
        assert out == "User: 帮我看看配置\n\nAssistant: 已查看，配置正常"

    def test_tuple_entries_accepted(self):
        assert render_trace([("user", "你好"), ("assistant", "好的")]) == \
            "User: 你好\n\nAssistant: 好的"

    def test_slice_last_8(self):
        """kilocode .slice(-max)：只留最后8条"""
        entries = [{"role": "user", "content": f"msg{i}"} for i in range(10)]
        out = render_trace(entries)
        assert RECENT_TRACE_MAX == 8
        assert out.count("User: ") == 8
        assert "msg0" not in out and "msg1" not in out
        assert "msg2" in out and "msg9" in out

    def test_max_entries_override(self):
        entries = [{"role": "user", "content": f"m{i}"} for i in range(5)]
        assert render_trace(entries, max_entries=2) == "User: m3\n\nUser: m4"

    def test_skip_tool_role_and_empty_body(self):
        out = render_trace([
            {"role": "tool", "content": "Tool write_file completed"},
            {"role": "user", "content": "   "},
            {"role": "assistant", "content": None},
            {"role": "user", "content": "真实发言"},
        ])
        assert out == "User: 真实发言"

    def test_skip_error_and_summary_assistant(self):
        """kilocode trace()：summary===true || error 的assistant条目return []"""
        out = render_trace([
            {"role": "assistant", "content": "boom", "error": "timeout"},
            {"role": "assistant", "content": "branch note", "summary": True},
            {"role": "assistant", "content": "正常回复"},
        ])
        assert out == "Assistant: 正常回复"

    def test_skip_synthetic_and_ignored(self):
        """kilocode text()：!part.synthetic && !part.ignored"""
        out = render_trace([
            {"role": "user", "content": "[用户插话] 顺手改个日志", "synthetic": True},
            {"role": "user", "content": "记忆注入", "ignored": True},
            {"role": "user", "content": "真实发言"},
        ])
        assert out == "User: 真实发言"

    def test_brief_220_explicit_marker(self):
        """有意偏离：body过220 brief+显式'…'（kilocode hidden() brief(220)同源）"""
        assert TRACE_BRIEF_MAX == 220
        out = render_trace([{"role": "user", "content": "x" * 500}])
        assert out == "User: " + "x" * 220 + "…"

    def test_parts_list_content(self):
        out = render_trace([{"role": "user", "content": [
            {"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"},
        ]}])
        assert out == "User: 第一段\n第二段"

    def test_fail_safe_never_raises(self):
        assert render_trace(None) == ""
        assert render_trace([object(), "junk", {"role": "user"}]) == ""
        assert render_trace([{"role": 123, "content": {"weird": 1}}]) == ""


# ════════════════════════════════════════════════════════════════
# 9. digest集成：recent_trace段（第4输入源→记忆载荷）
# ════════════════════════════════════════════════════════════════

class TestDigestRecentTrace:
    def _close(self, meta, reason=CLOSE_COMPLETED):
        bus = TurnLifecycleBus()
        client = FakeClient()
        bus.subscribe("turn.close", MemoryDigestCollector(
            client_factory=lambda: client))
        bus.open_turn("s1")
        run(bus.aclose_turn("s1", reason, **meta))
        return client

    def test_recent_trace_in_digest(self):
        client = self._close({
            "user_text": "u" * 60, "full_response": "r" * 120,
            "recent_trace": "User: 之前的问题\n\nAssistant: 之前的回答",
        })
        content = client.calls[0]["json"]["content"]
        assert "近期对话（recent trace）" in content
        assert "User: 之前的问题" in content
        assert "Assistant: 之前的回答" in content

    def test_trace_segment_order(self):
        """readTurn输入序 user/assistant/recent→近期对话段在文件改动/工具动作之前"""
        client = self._close({
            "user_text": "u" * 60, "full_response": "r" * 120,
            "recent_trace": "User: q",
            "file_changes": "- /a.py: write x1 +1/-0行",
            "tool_actions": "Tool write_file completed | exit=0",
        })
        content = client.calls[0]["json"]["content"]
        assert content.index("近期对话") < content.index("本轮文件改动") < content.index("工具动作")

    def test_no_trace_section_when_empty(self):
        client = self._close({
            "user_text": "你好" * 30, "full_response": "好",
            "recent_trace": "",
        })
        content = client.calls[0]["json"]["content"]
        assert "近期对话" not in content
        assert "\n\nUser: " not in content  # trace行形态绝迹
        assert content.startswith("用户: ")

    def test_interrupted_turn_trace_not_digested(self):
        client = self._close({
            "user_text": "x" * 60, "full_response": "y" * 120,
            "recent_trace": "User: q\n\nAssistant: a",
        }, reason=CLOSE_INTERRUPTED)
        assert client.calls == []


# ════════════════════════════════════════════════════════════════
# 10. 接线断言（第4输入源防死接线）
# ════════════════════════════════════════════════════════════════

class TestRecentTraceWiring:
    def test_prompt_inner_passes_recent_trace(self):
        src = inspect.getsource(SoulMateAgent._prompt_inner)
        assert "recent_trace=" in src and "render_trace(" in src
        assert "_recent_trace_entries(" in src

    def test_collector_consumes_recent_trace(self):
        src = inspect.getsource(MemoryDigestCollector.__call__)
        assert "recent_trace" in src and "近期对话" in src

    def test_trace_source_is_agent_messages(self):
        src = inspect.getsource(SoulMateAgent._recent_trace_entries)
        assert "agent_messages" in src and "LIMIT" in src

    def test_import_wired(self):
        """import接线：render_trace必须真的进了soulmate_agent命名空间"""
        import agent.soulmate_agent as smod
        assert getattr(smod, "render_trace", None) is render_trace
