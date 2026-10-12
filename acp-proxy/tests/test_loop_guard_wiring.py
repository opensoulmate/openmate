"""P0 cortex循环guard真实路径接线测试

解决"写了≠接线了"：opensoul cortex/loop_guard.py（ag2+goose+Khoj+anything-llm+
DeerFlow五源合抄）实现完整且自带25个单测，但真实ACP工具循环（:8092 ws→
SoulMateAgent._run_llm_with_tools，OpenMate聊天页实际使用路径）零消费方——
模型重复调同一工具会烧完MAX_ROUNDS=15无任何干预。

本轮改动：
1. loop_guard移植到acp-proxy/agent/loop_guard.py（独立进程/venv，按tool_output_handler先例持有副本）
2. soulmate_agent真实循环接线三级渐进（DeerFlow）：
   - WARN：Khoj模式注入"[LoopGuard警告]"用户消息，本轮工具照常执行
   - INTERVENE：合成拦截结果、不执行工具（open-webui三态：拒绝=合成错误结果，loop不断）
   - FORCE_STOP：断循环+用户可见回复标记（mem0：失败必须可见）
   - wiring级升级：同任务第2次INTERVENE→FORCE_STOP（agent侧guard cooldown=0防冷却期放行）
3. MAX_ROUNDS耗尽显式提示（for-else），不再静默返回残缺结果
4. 次级bug修复：read_file工具 proc.stdout.split("\\n") 字面量转义错误（行号全坏）
"""
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.loop_guard import LoopGuard, LoopSeverity
from agent.soulmate_agent import SoulMateAgent
from tests.test_steering import FakeLLM, make_agent, run


def _tc(name="terminal", args=None, tc_id="tc1"):
    """构造OpenAI格式tool_calls chunk步骤"""
    return {"tool_calls": [{
        "id": tc_id,
        "function": {"name": name, "arguments": json.dumps(args if args is not None else {})},
    }]}


class FakeGateAllow:
    async def check(self, session_id, tool_name, tool_args, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(allowed=True, behavior="allow", blocked_reason="",
                               rule_source="test", mode="default")


class FakeGateDeny:
    async def check(self, session_id, tool_name, tool_args, **kw):
        from types import SimpleNamespace
        return SimpleNamespace(allowed=False, behavior="deny", blocked_reason="blocked by test gate",
                               rule_source="test", mode="default")


def _wire_agent(tmp_path, scripts, gate=None):
    """复用test_steering的make_agent骨架，补齐工具执行路径所需属性"""
    agent = make_agent(tmp_path, scripts=scripts)
    agent._permission_gate = gate if gate is not None else FakeGateAllow()
    agent._tool_cache = MagicMock()
    agent._tool_cache.get.return_value = None
    agent._tool_error_handler = MagicMock()
    agent._todo_list = []
    return agent


class TestLoopGuardModule:
    """移植后的guard模块行为（opensoul原版语义保持）"""

    def test_three_consecutive_warns_five_intervenes(self):
        g = LoopGuard(cooldown_seconds=0)
        call = {"name": "terminal", "arguments": {"command": "ls"}}
        r1 = g.check(tool_calls=[call])
        r2 = g.check(tool_calls=[call])
        r3 = g.check(tool_calls=[call])
        r4 = g.check(tool_calls=[call])
        r5 = g.check(tool_calls=[call])
        assert r1.severity == LoopSeverity.OK
        assert r2.severity == LoopSeverity.OK
        assert r3.severity == LoopSeverity.WARN and r3.is_looping  # ag2 threshold=3
        assert r4.severity == LoopSeverity.OK  # WARN已flagged，不重复告警
        assert r5.severity == LoopSeverity.INTERVENE  # goose max_repetitions=5

    def test_different_args_not_flagged(self):
        g = LoopGuard(cooldown_seconds=0)
        for i in range(6):
            r = g.check(tool_calls=[{"name": "terminal", "arguments": {"command": f"echo {i}"}}])
            assert r.severity == LoopSeverity.OK  # 参数不同=不是连续重复

    def test_openai_format_accepted(self):
        g = LoopGuard(cooldown_seconds=0)
        call = {"function": {"name": "read_file", "arguments": '{"path":"/x"}'}}
        for _ in range(3):
            r = g.check(tool_calls=[call])
        assert r.severity == LoopSeverity.WARN
        assert r.detection_type.value == "tool_repetition"


class TestSourceWiring:
    """源码级接线证据（写了≠接线了的反向断言）"""

    def _src(self) -> str:
        return (Path(__file__).parent.parent / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")

    def test_import_init_helper_call_sites_present(self):
        text = self._src()
        assert "from agent.loop_guard import LoopGuard" in text          # import
        assert "self._loop_guards" in text                                # __init__
        assert "def _loop_guard_for" in text                              # helper
        assert "guard = self._loop_guard_for(session_id)" in text         # 循环内取实例
        assert "loop_result = guard.check(tool_calls=[" in text           # 真实调用点
        assert 'f"loop_guard:{lg_severity}"' in text                      # trajectory可观测字段

    def test_three_severity_paths_present(self):
        text = self._src()
        assert "[LoopGuard警告]" in text                    # WARN注入
        assert "loop-guard INTERVENE" in text               # INTERVENE合成结果
        assert "循环检测强制停止" in text                     # FORCE_STOP用户可见
        assert "已达工具调用软上限" in text                  # 零推理收口可见（推理优先终止）
        assert "推理优先" in text                            # 推理续期机制在源码可见

    def test_read_file_escape_bug_fixed(self):
        """字面量\\n转义bug：split按两字符'\\n'切分永远切不开真实换行（行号全坏）"""
        text = self._src()
        fixed = 'split("' + "\\" + 'n")'          # 修复后：单反斜杠n（Python换行转义）
        buggy = 'split("' + "\\" + "\\" + 'n")'   # 修复前：双反斜杠n（字面量）
        assert fixed in text
        assert buggy not in text


class TestRealLoopWiring:
    """真实运行路径（_run_llm_with_tools）端到端：FakeLLM脚本化产出重复tool_calls"""

    def test_warn_injects_message_and_tools_still_run(self, tmp_path):
        """第3次连续重复→WARN→警告注入下一轮上下文，本轮工具照常执行（Khoj语义）"""
        call = _tc(args={"command": "echo hi"})
        agent = _wire_agent(tmp_path, [[call], [call], [call], ["final text"]])
        resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        seen = agent.llm_engine.seen_messages
        assert len(seen) == 4  # 3个工具轮 + 1个文本收尾轮
        warn_msgs = [m for m in seen[3]
                     if m.get("role") == "user" and "[LoopGuard警告]" in str(m.get("content", ""))]
        assert warn_msgs, "第4轮上下文中必须存在注入的LoopGuard警告消息"
        # WARN轮工具照常执行：trajectory记录中前3轮是真实执行（非loop_guard拦截）
        exec_entries = [t for t in tools if not str(t.get("permission", "")).startswith("loop_guard:")]
        loop_entries = [t for t in tools if str(t.get("permission", "")).startswith("loop_guard:")]
        assert len(loop_entries) >= 1      # WARN也进trajectory可观测
        assert len(exec_entries) >= 3      # 3轮工具真实执行（gate allow + echo）

    def test_intervene_and_force_stop_escalation(self, tmp_path):
        """第5次重复→INTERVENE合成拦截不执行；第6次→wiring级升级FORCE_STOP断循环"""
        counter = Path(tempfile.mkdtemp()) / "exec_counter.txt"
        call = _tc(args={"command": f"echo executed >> {counter}"})
        scripts = [[call] for _ in range(6)] + [["never consumed"]]
        agent = _wire_agent(tmp_path, scripts)
        resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        # FORCE_STOP：用户可见 + 循环提前终止（script未耗尽）
        assert "循环检测强制停止" in resp
        assert len(agent.llm_engine.scripts) == 1
        # 执行计数：只有r1-r4真实执行；r5 INTERVENE拦截、r6 FORCE_STOP均未执行
        executed = len(counter.read_text().strip().splitlines())
        assert executed == 4
        # INTERVENE合成结果进入messages（open-webui三态：拒绝=合成错误结果，loop不断）
        intervene_msgs = [m for m in agent.llm_engine.seen_messages[5]
                          if m.get("role") == "tool" and "loop-guard INTERVENE" in str(m.get("content", ""))]
        assert intervene_msgs, "第6轮LLM收到的messages必须包含第5轮的INTERVENE合成拦截结果"
        # trajectory可观测：拦截事件带loop_guard标记
        loop_entries = [t for t in tools if str(t.get("permission", "")).startswith("loop_guard:")]
        assert len(loop_entries) >= 2

    def test_max_rounds_exhaustion_visible(self, tmp_path):
        """轮次耗尽必须显式告知用户（mem0：失败必须可见），不再静默返回残缺结果"""
        call_a = _tc(args={"command": "echo a"}, tc_id="a")
        call_b = _tc(args={"command": "echo b"}, tc_id="b")
        # 交替参数：不触发连续重复（Khoj非连续组合重复仅WARN一次，不拦截）
        scripts = [[call_a if i % 2 == 0 else call_b] for i in range(15)]
        agent = _wire_agent(tmp_path, scripts, gate=FakeGateDeny())
        resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        assert "已达工具调用软上限(15轮)" in resp
        assert "请发送'继续'" in resp
        assert len(agent.llm_engine.seen_messages) == 15  # 恰好15轮，无16次LLM调用

    def test_guard_reset_between_tasks(self, tmp_path):
        """guard per-task reset：同session第二个任务从干净窗口开始，不背第一任务的旧账"""
        call = _tc(args={"command": "echo x"})
        agent = _wire_agent(tmp_path,
                            [[call], [call], ["task1 done"],   # task1: 2次重复(consec=2)无告警
                             [call], ["task2 done"]])          # task2: 若不reset则consec=3→误WARN
        run(agent._run_llm_with_tools([{"role": "user", "content": "t1"}], "s1"))
        run(agent._run_llm_with_tools([{"role": "user", "content": "t2"}], "s1"))
        task2_first_round = agent.llm_engine.seen_messages[3]
        assert not any("[LoopGuard警告]" in str(m.get("content", ""))
                       for m in task2_first_round), "任务边界必须reset guard窗口"

    def test_read_file_line_numbers_correct(self, tmp_path):
        """字面量\\n bug修复实证：read_file工具结果带正确行号（修复前整个文件只有'1|'前缀）"""
        f = tmp_path / "three_lines.txt"
        f.write_text("alpha\nbeta\ngamma")
        call = _tc(name="read_file", args={"path": str(f), "offset": 1, "limit": 50}, tc_id="rf")
        agent = _wire_agent(tmp_path, [[call], []])
        resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "read it"}], "s1"))
        seen = agent.llm_engine.seen_messages[1]
        tool_msgs = [m for m in seen if m.get("role") == "tool"]
        assert tool_msgs
        content = tool_msgs[0]["content"]
        assert "2|beta" in content
        assert "3|gamma" in content

    def test_guard_acquisition_failure_fails_loud(self, tmp_path):
        """guard获取失败→fail-loud：核心安全件获取失败不静默跑无防护循环（设计意图锁定）"""
        import pytest
        call = _tc(args={"command": "echo ok"})
        agent = _wire_agent(tmp_path, [[call], ["text"]])
        agent._loop_guard_for = lambda sid: (_ for _ in ()).throw(RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))

    def test_loop_events_visible_in_trajectory_log(self, tmp_path):
        """可观测性：拦截事件进入all_tool_calls（prompt()落trajectory，可查'为什么没执行'）"""
        call = _tc(args={"command": "echo repeat"})
        scripts = [[call] for _ in range(6)] + [["end"]]
        agent = _wire_agent(tmp_path, scripts)
        resp, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        for entry in tools:
            if str(entry.get("permission", "")).startswith("loop_guard:"):
                assert entry["name"] == "terminal"
                assert entry["result_preview"]  # 拦截原因可见
                break
        else:
            raise AssertionError("trajectory记录中未找到loop_guard拦截事件")
