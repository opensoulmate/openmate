# -*- coding: utf-8 -*-
"""P1 Code Mode工具批量化 — goose code_execution+kilocode code-mode两方定案

来源：SUMMARY.md §五 cortex差距表"Code Mode工具批量化（N次调用批成1个execute）|
goose code_execution+kilocode code-mode（两方定案）| P1"；
63-goose-source-supplement6.md #4（"N次工具调用批成1个execute脚本 | P0省钱省轮次"）+
#5 tool_graph + #6 "hung script不能楔住所有会话"工程教训。

验证三层：
1. CodeModeExecutor：N→1批量化契约/参数规范化/受限builtins/调用上限熔断/
   批级超时+cancel阻断后续副作用/单次调用超时标记/脚本异常部分结果显式可见
2. SoulMateAgent._code_mode_tool_call：内层调用逐条过permission gate（deny→真实
   副作用不发生）/放行→builtin同款语义真实执行/MCP fallback
3. 真实消息路径E2E：_run_llm_with_tools收到batch_execute tool_call→脚本内真实调用
   read_file+terminal→工具结果含[CODE_MODE]→gate.calls含内层工具（接线实证）
"""
import asyncio
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.code_mode import (
    CodeModeCallLimit,
    CodeModeCancelled,
    CodeModeExecutor,
    CodeModeResult,
    _reclaim_hung_thread,
)
from agent.sandbox_policy import SandboxPolicy
from agent.soulmate_agent import SoulMateAgent
from tests.test_steering import make_agent, run


class RecordingGate:
    """可配置权限门禁：记录每次check调用（含Code Mode内层调用）"""
    def __init__(self, allow=True, deny_names=()):
        self.allow = allow
        self.deny_names = set(deny_names)
        self.calls = []

    async def check(self, session_id, tool_name, tool_args, **kw):
        self.calls.append((tool_name, tool_args))
        allowed = self.allow and tool_name not in self.deny_names
        return SimpleNamespace(
            allowed=allowed,
            behavior="allow" if allowed else "deny",
            blocked_reason="blocked by recording gate",
            rule_source="test",
            mode="default",
        )


def _fake_dispatch(calls: list):
    async def dispatch(name, args):
        calls.append((name, args))
        return f"r:{name}"
    return dispatch


def _executor(tmp_path=None, **kw):
    return CodeModeExecutor(**kw)


# ════════════════════════════════════════════════════════════════
# 1. CodeModeExecutor — N→1批量化契约
# ════════════════════════════════════════════════════════════════

class TestExecutorBatching:
    def test_n_calls_one_execute_contract(self):
        """3次工具调用在1次execute()内完成——N→1契约核心"""
        calls = []
        ex = CodeModeExecutor()
        script = (
            "a = read_file(path='/x1.py')\n"
            "b = search_files(pattern='def foo')\n"
            "c = terminal(command='ls')\n"
            "result = '|'.join([a, b, c])\n"
        )
        res = run(ex.execute(script, ["read_file", "search_files", "terminal"],
                              _fake_dispatch(calls)))
        assert res.ok is True
        assert len(calls) == 3
        assert res.calls_dispatched == 3
        assert len(res.call_log) == 3
        assert res.output == "r:read_file|r:search_files|r:terminal"
        assert [c[0] for c in calls] == ["read_file", "search_files", "terminal"]
        assert calls[0][1] == {"path": "/x1.py"}
        assert calls[1][1] == {"pattern": "def foo"}
        assert calls[2][1] == {"command": "ls"}
        # 全部ok + call_log可观测字段齐全（goose tool_graph轻量版）
        for e in res.call_log:
            assert e["ok"] is True
            assert e["blocked"] is False
            assert e["duration_ms"] >= 0
            assert e["result_len"] > 0

    def test_kwargs_and_single_dict_positional(self):
        calls = []
        ex = CodeModeExecutor()
        script = (
            "a = read_file({'path': '/dict-arg'})\n"
            "b = read_file(path='/kw-arg')\n"
            "result = a + b\n"
        )
        res = run(ex.execute(script, ["read_file"], _fake_dispatch(calls)))
        assert res.ok is True
        assert calls[0][1] == {"path": "/dict-arg"}
        assert calls[1][1] == {"path": "/kw-arg"}

    def test_non_dict_positional_rejected_visibly(self):
        calls = []
        ex = CodeModeExecutor()
        res = run(ex.execute("read_file('/x')", ["read_file"], _fake_dispatch(calls)))
        assert res.ok is False
        assert "关键字" in res.error
        assert res.calls_dispatched == 0  # 参数不合法的调用不dispatch（无副作用）

    def test_stdout_captured_when_no_result_var(self):
        calls = []
        ex = CodeModeExecutor()
        script = "print('hello-from-print')\nread_file(path='/x')\n"
        res = run(ex.execute(script, ["read_file"], _fake_dispatch(calls)))
        assert res.ok is True
        assert res.output == "hello-from-print"
        assert len(calls) == 1

    def test_result_var_wins_over_stdout(self):
        ex = CodeModeExecutor()
        script = "print('noise')\nresult = 'final-value'\n"
        res = run(ex.execute(script, [], _fake_dispatch([])))
        assert res.output == "final-value"


class TestExecutorFailureVisibility:
    """mem0 §1.1失败必须可见：脚本异常→部分结果+traceback显式返回"""

    def test_script_exception_preserves_partial_log(self):
        calls = []
        ex = CodeModeExecutor()
        script = (
            "result = read_file(path='/x1')\n"
            "raise ValueError('boom-in-script')\n"
        )
        res = run(ex.execute(script, ["read_file"], _fake_dispatch(calls)))
        assert res.ok is False
        assert "ValueError" in res.error and "boom-in-script" in res.error
        assert len(res.call_log) == 1          # 异常前的调用记录保留
        assert res.output == "r:read_file"     # 异常前的result/partial可见
        assert res.call_log[0]["ok"] is True

    def test_import_blocked_visible(self):
        """受限builtins：__import__不注入——绕过权限层的import显式失败"""
        ex = CodeModeExecutor()
        res = run(ex.execute("import os\nresult = os.getcwd()",
                              ["read_file"], _fake_dispatch([])))
        assert res.ok is False
        assert res.error  # 错误显式可见，非静默
        res2 = run(ex.execute("__import__('subprocess')",
                               ["read_file"], _fake_dispatch([])))
        assert res2.ok is False
        assert "__import__" in res2.error

    def test_unknown_tool_name_not_injected(self):
        """脚本调用不在会话工具列表中的函数→NameError可见（不凭空造工具）"""
        ex = CodeModeExecutor()
        res = run(ex.execute("result = not_a_tool(x=1)",
                              ["read_file"], _fake_dispatch([])))
        assert res.ok is False
        assert "not_a_tool" in res.error

    def test_call_limit_circuit_breaker(self):
        """失控循环熔断（goose max_repetitions哲学）：到顶即停，错误显式"""
        calls = []
        ex = CodeModeExecutor(max_calls=3)
        script = (
            "for i in range(10):\n"
            "    terminal(command=f'cmd-{i}')\n"
            "result = 'unreachable'\n"
        )
        res = run(ex.execute(script, ["terminal"], _fake_dispatch(calls)))
        assert res.ok is False
        assert "上限3" in res.error
        assert res.calls_dispatched == 3   # 第4次被拦截，未dispatch
        assert len(calls) == 3
        assert len(res.call_log) == 3

    def test_batch_timeout_returns_partial_and_sets_cancel(self, monkeypatch):
        """挂死脚本不能楔住会话（goose pctx教训）：批级超时→部分结果返回"""
        monkeypatch.setattr("agent.code_mode.RECLAIM_GRACE_S", 0.1)  # 测试期快速回收
        calls = []
        ex = CodeModeExecutor(batch_timeout=1.0)
        script = (
            "result = read_file(path='/done-first')\n"
            "while True:\n"
            "    pass\n"
        )
        t0 = time.monotonic()
        res = run(ex.execute(script, ["read_file"], _fake_dispatch(calls)))
        elapsed = time.monotonic() - t0
        assert res.timed_out is True
        assert res.ok is False
        assert "CODE_MODE_TIMEOUT" in res.error
        assert len(res.call_log) == 1           # 超时前完成的调用可见
        assert res.output == "r:read_file"      # 部分结果不丢
        assert elapsed < 8.0                    # 有界退出（不是被脚本拖死）

    def test_cancel_flag_blocks_subsequent_stubs(self):
        """AbortOnDrop语义：cancel置位后stub直接拒绝，不再产生副作用"""
        calls = []
        ex = CodeModeExecutor()
        loop = asyncio.new_event_loop()
        try:
            state = {"call_log": [], "calls": 0, "cancel": threading.Event()}
            state["cancel"].set()
            ns = ex.build_namespace(["terminal"], _fake_dispatch(calls), loop, state)
            try:
                ns["terminal"](command="should-not-run")
                assert False, "cancelled stub应抛CodeModeCancelled"
            except CodeModeCancelled:
                pass
            assert calls == []  # 关键：副作用未发生
        finally:
            loop.close()

    def test_per_call_timeout_marker_script_continues(self):
        """单次调用挂死→超时标记，脚本继续（单次挂死不楔住整批）"""
        calls = []
        async def slow_dispatch(name, args):
            calls.append(name)
            if name == "slow_tool":
                await asyncio.sleep(2.0)
            return f"r:{name}"
        ex = CodeModeExecutor(call_timeout=0.3, batch_timeout=10.0)
        script = (
            "a = slow_tool(p=1)\n"
            "b = read_file(path='/x')\n"
            "result = a + '|' + b\n"
        )
        res = run(ex.execute(script, ["slow_tool", "read_file"], slow_dispatch))
        assert res.ok is True                      # 脚本本身跑完了
        assert "超时" in res.output                 # 超时标记进入脚本可见结果
        assert calls == ["slow_tool", "read_file"]  # 后续调用继续发生
        assert res.call_log[0]["error"] == "call_timeout"
        assert res.call_log[0]["ok"] is False
        assert res.call_log[1]["ok"] is True


class TestExecutorNamespaceSafety:
    def test_protected_names_not_shadowed_and_invalid_skipped(self):
        ex = CodeModeExecutor()
        state = {"call_log": [], "calls": 0, "cancel": threading.Event()}
        ns = ex.build_namespace(
            ["print", "read_file", "bad-name", "123abc", "tool_names"],
            _fake_dispatch([]), None, state)
        assert "print" not in ns                       # ns层不注入（不遮蔽）
        assert ns["__builtins__"]["print"] is print   # 内建语义经__builtins__保留
        assert callable(ns["read_file"])         # 正常注入
        assert "bad-name" not in ns              # 非法标识符跳过
        assert "123abc" not in ns
        assert ns["tool_names"] == ["read_file"]  # 注入清单可观测（仅合法名）

    def test_no_dunder_escape_hatches(self):
        ex = CodeModeExecutor()
        state = {"call_log": [], "calls": 0, "cancel": threading.Event()}
        ns = ex.build_namespace(["read_file"], _fake_dispatch([]), None, state)
        bi = ns["__builtins__"]
        assert "__import__" not in bi
        assert "open" not in bi          # 文件IO走工具（过gate），不给脚本直通
        assert "exec" not in bi and "eval" not in bi
        assert "read_file" not in bi  # stub在ns层不在builtins层


class TestFormatResult:
    def _res(self, **kw):
        base = dict(ok=True, output="OUT", call_log=[], error="", timed_out=False,
                    duration_ms=12, calls_dispatched=0)
        base.update(kw)
        return CodeModeResult(**base)

    def test_success_head_and_log_lines(self):
        res = self._res(call_log=[
            {"name": "read_file", "args_preview": '{"path": "/a"}', "ok": True,
             "blocked": False, "duration_ms": 5, "result_len": 100, "error": ""},
            {"name": "terminal", "args_preview": '{"command": "rm -rf /"}', "ok": True,
             "blocked": True, "duration_ms": 1, "result_len": 30, "error": ""},
        ], calls_dispatched=2)
        text = CodeModeExecutor.format_result(res)
        assert "2次工具调用已合并为1轮执行" in text
        assert "1. read_file(" in text and "ok" in text
        assert "拦截(权限)" in text
        assert "OUT" in text

    def test_timeout_and_error_heads(self):
        t = CodeModeExecutor.format_result(
            self._res(ok=False, timed_out=True, call_log=[], error="CODE_MODE_TIMEOUT"))
        assert "超时取消" in t
        e = CodeModeExecutor.format_result(
            self._res(ok=False, error="脚本异常: X", call_log=[]))
        assert "批执行异常" in e and "脚本异常: X" in e
        empty = CodeModeExecutor.format_result(self._res(call_log=[]))
        assert "未调用任何工具" in empty

    def test_failed_call_error_shown(self):
        res = self._res(call_log=[
            {"name": "web_search", "args_preview": "{}", "ok": False, "blocked": False,
             "duration_ms": 3, "result_len": 0, "error": "call_timeout"}])
        text = CodeModeExecutor.format_result(res)
        assert "失败(call_timeout)" in text


# ════════════════════════════════════════════════════════════════
# 2. SoulMateAgent._code_mode_tool_call — 内层权限gate + builtin同款语义
# ════════════════════════════════════════════════════════════════

def _bare_agent(gate: RecordingGate) -> SoulMateAgent:
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent._permission_gate = gate
    agent._output_handler = None
    # kilocode supplement3 #15：每agent独立store（默认不受限）
    agent._sandbox_policy = SandboxPolicy(
        store_path=str(Path(tempfile.mkdtemp()) / "sandbox_policy.json"))

    async def _mcp(name, args):
        return f"mcp:{name}"
    agent._call_mcp_tool = _mcp
    return agent


class TestInnerDispatchGate:
    def test_deny_blocks_real_side_effect(self):
        """deny→[被拦截]文本且真实副作用不发生（批量化不绕过权限引擎）"""
        import tempfile, os
        probe = Path(tempfile.mkdtemp()) / "deny_probe.txt"
        gate = RecordingGate(allow=False)
        agent = _bare_agent(gate)
        out = run(agent._code_mode_tool_call(
            "s1", "terminal", {"command": f"touch {probe}"}, "/tmp"))
        assert out.startswith("[被拦截:deny]")
        assert "recording gate" in out
        assert not probe.exists()          # 关键：副作用未发生
        assert gate.calls == [("terminal", {"command": f"touch {probe}"})]

    def test_selective_deny_only_blocks_named_tool(self):
        gate = RecordingGate(allow=True, deny_names=("terminal",))
        agent = _bare_agent(gate)
        out_deny = run(agent._code_mode_tool_call(
            "s1", "terminal", {"command": "echo x"}, "/tmp"))
        out_allow = run(agent._code_mode_tool_call(
            "s1", "search_files", {"pattern": "def _cm_probe_none_", "path": "/tmp",
                                   "target": "content"}, "/tmp"))
        assert out_deny.startswith("[被拦截")
        assert not out_allow.startswith("[被拦截")
        assert ("terminal",) and len(gate.calls) == 2  # 两次都过gate被记录

    def test_allow_terminal_real_execution(self):
        gate = RecordingGate(allow=True)
        agent = _bare_agent(gate)
        out = run(agent._code_mode_tool_call(
            "s1", "terminal", {"command": "echo cm-live-marker-42"}, "/tmp"))
        assert "cm-live-marker-42" in out
        assert gate.calls[0][0] == "terminal"

    def test_read_file_semantics_match_inline(self):
        gate = RecordingGate(allow=True)
        agent = _bare_agent(gate)
        import tempfile
        d = Path(tempfile.mkdtemp())
        f = d / "probe.txt"
        f.write_text("alpha\nbeta\n", encoding="utf-8")
        out = run(agent._code_mode_tool_call(
            "s1", "read_file", {"path": str(f)}, str(d)))
        assert "1|alpha" in out and "2|beta" in out

    def test_write_patch_roundtrip(self):
        gate = RecordingGate(allow=True)
        agent = _bare_agent(gate)
        import tempfile
        d = Path(tempfile.mkdtemp())
        f = d / "rt.txt"
        out_w = run(agent._code_mode_tool_call(
            "s1", "write_file", {"path": str(f), "content": "hello world"}, str(d)))
        assert "已写入" in out_w
        out_p = run(agent._code_mode_tool_call(
            "s1", "patch", {"path": str(f), "old_string": "world",
                            "new_string": "code-mode"}, str(d)))
        assert "已修改" in out_p
        assert f.read_text(encoding="utf-8") == "hello code-mode"

    def test_search_files_content_grep(self):
        gate = RecordingGate(allow=True)
        agent = _bare_agent(gate)
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "hit.py").write_text("def cm_unique_probe_fn():\n    pass\n", encoding="utf-8")
        out = run(agent._code_mode_tool_call(
            "s1", "search_files",
            {"pattern": "cm_unique_probe_fn", "path": str(d), "target": "content"}, str(d)))
        assert "cm_unique_probe_fn" in out and "hit.py" in out

    def test_execute_code_runs_and_reports_exit_code(self):
        gate = RecordingGate(allow=True)
        agent = _bare_agent(gate)
        out = run(agent._code_mode_tool_call(
            "s1", "execute_code", {"code": "print('exec-probe-9')"}, "/tmp"))
        assert "exec-probe-9" in out
        out2 = run(agent._code_mode_tool_call(
            "s1", "execute_code", {"code": "import sys; sys.exit(3)"}, "/tmp"))
        assert "[exit code: 3]" in out2

    def test_mcp_fallback_for_unknown_tools(self):
        gate = RecordingGate(allow=True)
        agent = _bare_agent(gate)
        out = run(agent._code_mode_tool_call(
            "s1", "some_server__some_tool", {"q": 1}, "/tmp"))
        assert out == "mcp:some_server__some_tool"
        assert gate.calls[0][0] == "some_server__some_tool"  # MCP调用同样过gate

    def test_gate_failure_returns_visible_error(self):
        """gate自身异常→fail-closed：直接路径向上抛（与主循环gate语义一致，不静默
        放行）；经executor脚本路径→stub捕获转为可见[CODE_MODE]工具调用异常文本，
        被gate故障影响的调用不执行、批不崩"""
        class BrokenGate:
            async def check(self, *a, **kw):
                raise RuntimeError("gate backend down")
        agent = _bare_agent(BrokenGate())  # type: ignore[arg-type]
        # ① 直接路径：gate异常不被吞（fail-closed，绝非fail-open放行）
        try:
            run(agent._code_mode_tool_call("s1", "terminal", {"command": "echo x"}, "/tmp"))
            assert False, "gate异常应向上抛而非放行"
        except RuntimeError as e:
            assert "gate backend down" in str(e)
        # ② executor脚本路径：异常变可见文本，脚本级不崩
        ex = CodeModeExecutor()

        async def _dispatch_broken(name, args):
            return await agent._code_mode_tool_call("s1", name, args, "/tmp")
        res = run(ex.execute("result = terminal(command='echo x')",
                             ["terminal"], _dispatch_broken))
        assert res.ok is True  # 脚本级不崩
        assert "工具调用异常" in res.output
        assert "gate backend down" in res.output
        assert res.call_log[0]["ok"] is False
        assert "gate backend down" in res.call_log[0]["error"]


# ════════════════════════════════════════════════════════════════
# 3. 真实消息路径E2E — _run_llm_with_tools × batch_execute接线
# ════════════════════════════════════════════════════════════════

class TestWiringEndToEnd:
    def test_static_wiring_present(self):
        """接线静态证据：import/工具schema/分发分支/内层方法全在源码中"""
        src = (Path(__file__).parent.parent / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")
        assert "from agent.code_mode import CodeModeExecutor" in src
        assert '"name": "batch_execute"' in src
        assert 'elif func_name == "batch_execute":' in src
        assert "async def _code_mode_tool_call" in src
        assert "CodeModeExecutor.format_result(_cm_res)" in src
        assert "batch:" in src  # call_log进all_tool_calls（tool_graph可观测）

    def test_batch_execute_real_message_path(self):
        """E2E：LLM请求batch_execute→脚本内真实read_file+terminal→结果回注消息历史；
        gate.calls证明batch_execute本体+内层工具逐条过权限门禁（非死代码）"""
        import tempfile
        d = Path(tempfile.mkdtemp())
        target = d / "cm_target.txt"
        target.write_text("cm-file-marker-7788\n", encoding="utf-8")
        script = (
            f"a = read_file(path={str(target)!r})\n"
            "b = terminal(command='echo cm-wired-echo')\n"
            "result = a + '||' + b\n"
        )
        tc_chunk = {"tool_calls": [{
            "id": "cm1",
            "function": {
                "name": "batch_execute",
                "arguments": json.dumps({"script": script}),
            },
        }]}
        agent = make_agent(d, acquired=True, scripts=[[tc_chunk], ["任务完成"]])
        gate = RecordingGate(allow=True)
        agent._permission_gate = gate
        agent._tool_cache = MagicMock(**{"get.return_value": None})
        agent._tool_error_handler = MagicMock()
        agent._session_cwds = {"s1": str(d)}

        resp, all_tool_calls = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "批量读取"}],
            session_id="s1",
        ))

        # ① 任务正常走完（batch一轮→模型收结果→纯文本结束）
        assert resp == "任务完成"
        # ② round2的messages里有batch工具结果（真实消息路径回注）
        round2_msgs = agent.llm_engine.seen_messages[1]
        tool_msgs = [m for m in round2_msgs if m.get("role") == "tool"]
        assert len(tool_msgs) == 1
        cm_text = tool_msgs[0]["content"]
        assert "[CODE_MODE]" in cm_text
        assert "2次工具调用已合并为1轮执行" in cm_text
        assert "cm-file-marker-7788" in cm_text      # read_file真实执行
        assert "cm-wired-echo" in cm_text            # terminal真实执行
        assert "1. read_file(" in cm_text and "2. terminal(" in cm_text
        # ③ 权限gate逐层记录：batch_execute本体 + 内层每个工具（接线核心证据）
        gate_names = [c[0] for c in gate.calls]
        assert gate_names[0] == "batch_execute"
        assert "read_file" in gate_names and "terminal" in gate_names
        assert len(gate.calls) == 3
        # ④ tool_graph可观测：批内每步进轨迹账本
        batch_entries = [t for t in all_tool_calls if t["name"].startswith("batch:")]
        assert {t["name"] for t in batch_entries} == {"batch:read_file", "batch:terminal"}
        assert all("ok" in t["result_preview"] for t in batch_entries)
        # ⑤ batch_execute本体也进账本
        assert any(t["name"] == "batch_execute" for t in all_tool_calls)

    def test_batch_deny_inner_visible_in_result(self):
        """gate只放行batch_execute本体、deny全部内层工具→脚本结果全[被拦截]且不崩"""
        tc_chunk = {"tool_calls": [{
            "id": "cm2",
            "function": {
                "name": "batch_execute",
                "arguments": json.dumps({
                    "script": "result = terminal(command='echo never') + search_files(pattern='x')",
                }),
            },
        }]}
        agent = make_agent(None if False else Path("/tmp"), acquired=True,
                           scripts=[[tc_chunk], ["done"]])
        gate = RecordingGate(allow=True, deny_names=("terminal", "search_files"))
        agent._permission_gate = gate
        agent._tool_cache = MagicMock(**{"get.return_value": None})
        agent._tool_error_handler = MagicMock()
        agent._session_cwds = {"s1": "/tmp"}
        resp, all_tool_calls = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "go"}], session_id="s1"))
        assert resp == "done"
        cm_text = [m for m in agent.llm_engine.seen_messages[1]
                   if m.get("role") == "tool"][0]["content"]
        assert "[CODE_MODE]" in cm_text
        assert cm_text.count("[被拦截:deny]") == 2
        assert "拦截(权限)" in cm_text
        # 内层deny仍被gate记录（审计完整）
        assert [c[0] for c in gate.calls] == ["batch_execute", "terminal", "search_files"]

    def test_batch_missing_script_error_visible(self):
        tc_chunk = {"tool_calls": [{
            "id": "cm3",
            "function": {"name": "batch_execute", "arguments": json.dumps({"script": "  "})},
        }]}
        agent = make_agent(Path("/tmp"), acquired=True, scripts=[[tc_chunk], ["ok"]])
        agent._permission_gate = RecordingGate(allow=True)
        agent._tool_cache = MagicMock(**{"get.return_value": None})
        agent._tool_error_handler = MagicMock()
        agent._session_cwds = {"s1": "/tmp"}
        resp, _ = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "go"}], session_id="s1"))
        assert resp == "ok"
        cm_text = [m for m in agent.llm_engine.seen_messages[1]
                   if m.get("role") == "tool"][0]["content"]
        assert "batch_execute 需要 script 参数" in cm_text


class TestHungThreadReclaim:
    """周期#83：批超时被放弃的 worker 线程到点回收（GIL 楔死另一半）。

    实证背景：`while True: pass` 残留线程持 GIL 自旋，同进程其他线程
    单次 syscall 的 GIL 再获取都要等切换间隔——0.14s 的目录 walk 实测变
    60s+，整套回归被楔成"挂死"。
    """

    def test_hung_spinner_reclaimed_after_grace(self, monkeypatch):
        monkeypatch.setattr("agent.code_mode.RECLAIM_GRACE_S", 0.2)
        ex = CodeModeExecutor(batch_timeout=0.5)
        script = "while True:\n    pass\n"
        res = run(ex.execute(script, [], lambda n, a: asyncio.sleep(0)))
        assert res.timed_out is True  # 超时语义不变
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            alive = [t for t in threading.enumerate()
                     if t.name == "code-mode-exec" and t.is_alive()]
            if not alive:
                break
            time.sleep(0.1)
        alive = [t for t in threading.enumerate()
                 if t.name == "code-mode-exec" and t.is_alive()]
        assert alive == []  # 宽限期后残留自旋线程被回收

    def test_reclaim_noop_on_not_started_thread(self):
        # 线程从未启动（ident=None）：回收是安全 no-op，不抛异常
        th = threading.Thread(target=lambda: None, name="code-mode-exec")
        _reclaim_hung_thread(th, grace=0.05)
        time.sleep(0.2)  # Timer 到点执行 _fire，走 ident is None 分支

    def test_reclaim_survives_oneshot_exit(self):
        # 线程已自行退出：_fire 检查 is_alive 短路，不注入不抛异常
        th = threading.Thread(target=lambda: None, name="code-mode-exec")
        th.start()
        th.join(timeout=2.0)
        assert not th.is_alive()
        _reclaim_hung_thread(th, grace=0.05)
        time.sleep(0.2)

    def test_wiring_timeout_path_schedules_reclaim(self):
        src = (Path(__file__).parent.parent / "agent" / "code_mode.py").read_text(
            encoding="utf-8")
        # TimeoutError 分支必须紧随 cancel 置位后调度回收
        i = src.index("except asyncio.TimeoutError:")
        seg = src[i:i + 400]
        assert "state[\"cancel\"].set()" in seg
        assert "_reclaim_hung_thread(worker, grace=RECLAIM_GRACE_S)" in seg
