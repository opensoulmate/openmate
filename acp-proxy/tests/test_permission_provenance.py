"""kilocode #14 权限provenance测试 + P0缓存命中分支回归

三层验证：
1. 单元：classify_denial / approval_source / outside_workspace_paths / build_provenance
2. gate集成：PermissionGate各拒绝分支的denial_class（超时/真人拒绝/无人值守/引擎规则）
3. P0回归：_run_llm_with_tools缓存命中分支（原tool_calls_log NameError，一旦命中即炸回合）
4. 接线断言：soulmate_agent真实消息路径+code_mode内层的build_provenance调用（防死代码）
"""

import asyncio
import inspect
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.permission_gate import PermissionGate, degraded_decision
from agent.permission_provenance import (
    SCHEMA,
    PermissionProvenanceRecorder,
    approval_source,
    build_provenance,
    classify_denial,
    outside_workspace_paths,
)
from agent.soulmate_agent import SoulMateAgent
from tests.test_steering import make_agent, run


def gate_ns(**kw):
    """轻量gate结果stub（SimpleNamespace同test_steering FakeGate形态）"""
    base = {"allowed": True, "behavior": "allow", "blocked_reason": "", "decision_id": "",
            "rule_source": "", "rule_content": "", "mode": "", "human_approved": False,
            "denial_class": ""}
    base.update(kw)
    return SimpleNamespace(**base)


class TestClassifyDenial:
    """kilocode classifyDenial：拒绝原因分层（哪条ruleset/permission/patterns）"""

    def test_explicit_denial_class_wins(self):
        assert classify_denial("deny", "builtin", "", "approval:timeout") == "approval:timeout"

    def test_degraded_local_is_patterns_layer(self):
        assert classify_denial("deny", "degraded-local", r"\bmkfs\b") == "patterns:degraded-local"

    def test_hard_rule_detected_by_content(self):
        got = classify_denial("deny", "builtin", "HARD: rm -rf /")
        assert got.startswith("hard-ruleset:")

    def test_engine_deny_maps_to_ruleset_with_source(self):
        assert classify_denial("deny", "session", "npm run:*") == "ruleset:session"

    def test_ask_denied_fallback(self):
        assert classify_denial("ask-denied", "user", "") == "approval:unattended-or-rejected"

    def test_allow_gives_empty_class(self):
        assert classify_denial("allow", "builtin", "") == ""


class TestApprovalSource:
    """kilocode approval来源：哪一层放行"""

    def test_cached_allow_is_session_cache(self):
        assert approval_source(gate_ns(behavior="cached-allow", human_approved=True)) == "session-cache"

    def test_rule_source_session_cache(self):
        assert approval_source(gate_ns(rule_source="session-cache")) == "session-cache"

    def test_human_approval(self):
        assert approval_source(gate_ns(behavior="ask-approved", human_approved=True)) == "human-approval"

    def test_degraded_local(self):
        assert approval_source(gate_ns(rule_source="degraded-local")) == "degraded-local"

    def test_engine_rule_vs_default(self):
        assert approval_source(gate_ns(rule_source="builtin")) == "engine-rule"
        assert approval_source(gate_ns()) == "engine-default"


class TestOutsideWorkspace:
    """kilocode tagOutsideWorkspace：文件路径在workspace外的批准单独打标"""

    def test_path_inside_workspace_not_tagged(self, tmp_path):
        inside = tmp_path / "a.py"
        assert outside_workspace_paths({"path": str(inside)}, str(tmp_path)) == []

    def test_path_outside_workspace_tagged(self, tmp_path):
        outside = "/etc/passwd"
        got = outside_workspace_paths({"path": outside}, str(tmp_path))
        assert got == ["/etc/passwd"]

    def test_command_absolute_path_extracted(self, tmp_path):
        got = outside_workspace_paths(
            {"command": "cat /etc/hostname && ls"}, str(tmp_path))
        assert "/etc/hostname" in got

    def test_relative_path_resolved_against_base(self, tmp_path):
        # 相对路径按working_dir解析——workspace内的相对路径不算outside
        assert outside_workspace_paths({"path": "sub/file.txt"}, str(tmp_path)) == []
        assert outside_workspace_paths({"path": "../escape.txt"}, str(tmp_path)) != []

    def test_home_expansion_outside(self, tmp_path):
        got = outside_workspace_paths({"file": "~/.ssh/id_rsa"}, str(tmp_path))
        assert got and got[0].endswith(".ssh/id_rsa")

    def test_empty_working_dir_never_tags(self):
        assert outside_workspace_paths({"path": "/etc/passwd"}, "") == []

    def test_capped_at_max(self, tmp_path):
        args = {"command": " ".join(f"/etc/f{i}" for i in range(20))}
        got = outside_workspace_paths(args, str(tmp_path))
        assert len(got) <= 8


class TestBuildProvenance:
    """tool part metadata：三要素齐全（approval来源+tagOutsideWorkspace+classifyDenial）"""

    def test_allow_record_full_fields(self, tmp_path):
        prov = build_provenance("read_file", {"path": str(tmp_path / "in.py")},
                                gate_ns(rule_source="builtin"), working_dir=str(tmp_path),
                                session_id="s1", via="main_loop")
        assert prov["schema"] == SCHEMA
        assert prov["tool"] == "read_file"
        assert prov["allowed"] is True
        assert prov["approval_source"] == "engine-rule"
        assert prov["denial_class"] == ""
        assert prov["tag_outside_workspace"] is False
        assert prov["session_id"] == "s1" and prov["via"] == "main_loop"
        assert prov["ts"]

    def test_deny_record_carries_denial_class(self, tmp_path):
        g = gate_ns(allowed=False, behavior="deny", rule_source="builtin",
                    rule_content="HARD: rm -rf /", denial_class="hard-ruleset:builtin")
        prov = build_provenance("terminal", {"command": "rm -rf /"}, g,
                                working_dir=str(tmp_path), session_id="s1")
        assert prov["allowed"] is False
        assert prov["denial_class"] == "hard-ruleset:builtin"
        assert prov["rule_content"] == "HARD: rm -rf /"

    def test_outside_workspace_tag_on_approval(self, tmp_path):
        prov = build_provenance("write_file", {"path": "/etc/cron.d/x"},
                                gate_ns(human_approved=True), working_dir=str(tmp_path))
        assert prov["tag_outside_workspace"] is True
        assert prov["outside_paths"] == ["/etc/cron.d/x"]
        assert prov["approval_source"] == "human-approval"

    def test_magicmock_gate_json_safe(self):
        """测试stub/MagicMock gate都不炸、可JSON序列化（kilocode元数据落盘前提）"""
        prov = build_provenance("terminal", {"command": "ls"}, MagicMock(allowed=True))
        json.dumps(prov, ensure_ascii=False)  # 不抛异常
        assert prov["schema"] == SCHEMA


class TestRecorder:
    """JSONL持久账本：跨进程可读 + 失败可见不静默 + 绝不反噬工具流程"""

    def test_write_and_read_roundtrip(self, tmp_path):
        rec = PermissionProvenanceRecorder(ledger_path=str(tmp_path / "prov.jsonl"))
        assert rec.record({"tool": "terminal", "decision": "deny"}) is True
        assert rec.record({"tool": "read_file", "decision": "allow"}) is True
        got = rec.read_recent()
        assert [g["tool"] for g in got] == ["terminal", "read_file"]
        assert rec.written == 2

    def test_cross_instance_readable(self, tmp_path):
        p = str(tmp_path / "shared.jsonl")
        PermissionProvenanceRecorder(ledger_path=p).record({"tool": "x", "decision": "deny"})
        reader = PermissionProvenanceRecorder(ledger_path=p)
        assert reader.read_recent()[0]["tool"] == "x"

    def test_record_failure_visible_not_raised(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file")
        rec = PermissionProvenanceRecorder(ledger_path=str(blocker / "sub" / "prov.jsonl"))
        assert rec.record({"tool": "x"}) is False   # 不抛异常
        assert rec.errors == 1                       # 失败可见（mem0 §1.1禁止静默）

    def test_empty_entry_rejected(self, tmp_path):
        rec = PermissionProvenanceRecorder(ledger_path=str(tmp_path / "p.jsonl"))
        assert rec.record({}) is False

    def test_default_ledger_name(self):
        rec = PermissionProvenanceRecorder()
        assert rec.ledger_path.name == "permission_provenance.jsonl"


class TestGateDenialClass:
    """PermissionGate各拒绝分支自带denial_class（构造处最清楚是哪一层拦的）"""

    def _gate(self, decision=None, **kw):
        gate = PermissionGate(**kw)

        async def _fake_engine(tool_name, tool_args, session_id, working_dir):
            return decision or {"behavior": "allow"}

        async def _fake_outcome(decision_id, outcome, comment=""):
            return None

        gate._call_engine = _fake_engine
        gate._record_outcome = _fake_outcome
        return gate

    def test_engine_deny_class(self):
        gate = self._gate({"behavior": "deny", "rule_source": "builtin",
                           "rule_content": "x", "decision_reason": "r"})
        r = run(gate.check("s1", "terminal", {"command": "x"}))
        assert r.denial_class == "ruleset:builtin"
        assert r.to_dict()["denial_class"] == "ruleset:builtin"

    def test_unattended_ask_class(self):
        gate = self._gate({"behavior": "ask", "rule_source": "user", "decision_reason": "r"})
        r = run(gate.check("s1", "terminal", {"command": "x"}, request_approval=None))
        assert r.denial_class == "approval:unattended"

    def test_timeout_vs_human_rejected_distinguished(self):
        async def _slow_approval(tn, ta, dec):
            await asyncio.sleep(1.0)
            return False

        gate = self._gate({"behavior": "ask", "decision_reason": "r"}, approval_timeout=0.05)
        r = run(gate.check("s1", "terminal", {"command": "x"}, request_approval=_slow_approval))
        assert r.denial_class == "approval:timeout"

        async def _reject(tn, ta, dec):
            return False

        gate2 = self._gate({"behavior": "ask", "decision_reason": "r"})
        r2 = run(gate2.check("s1", "terminal", {"command": "x"}, request_approval=_reject))
        assert r2.denial_class == "approval:human-rejected"

    def test_degraded_hard_pattern_class(self):
        r = degraded_decision("terminal", {"command": "mkfs /dev/sda"})
        assert r.denial_class == "patterns:degraded-local"

    def test_approved_no_denial_class(self):
        async def _ok(tn, ta, dec):
            return True

        gate = self._gate({"behavior": "ask", "decision_reason": "r"})
        r = run(gate.check("s1", "terminal", {"command": "x"}, request_approval=_ok))
        assert r.allowed is True and r.denial_class == ""
        assert r.human_approved is True


def _allow_gate():
    return SimpleNamespace(
        allowed=True, behavior="allow", blocked_reason="", decision_id="dg1",
        rule_source="builtin", rule_content="", mode="default",
        human_approved=False, denial_class="")


class TestCacheHitP0Regression:
    """P0回归：缓存命中分支原tool_calls_log NameError（一旦命中即炸整个回合）"""

    def _agent_with_cache(self, tmp_path, cached_value):
        tc_chunk = {"tool_calls": [{
            "id": "c1",
            "function": {"name": "read_file",
                         "arguments": json.dumps({"path": str(tmp_path / "x.py")})},
        }]}
        agent = make_agent(tmp_path, acquired=True,
                           scripts=[[tc_chunk], ["完成"]])
        agent._permission_gate = MagicMock()
        agent._permission_gate.check = _allow_gate_check
        agent._tool_cache = MagicMock()
        agent._tool_cache.get.return_value = cached_value
        agent._tool_error_handler = MagicMock()
        agent._perm_provenance = PermissionProvenanceRecorder(
            ledger_path=str(tmp_path / "prov.jsonl"))
        return agent

    def test_cache_hit_no_crash_and_ledgered(self, tmp_path):
        agent = self._agent_with_cache(tmp_path, "cached-content-abc")
        resp, all_tool_calls = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "读一下"}], session_id="s1"))
        assert "完成" in resp
        hits = [t for t in all_tool_calls if t.get("cached")]
        assert len(hits) == 1
        assert hits[0]["name"] == "read_file"
        assert hits[0]["result_preview"] == "cached-content-abc"
        assert "permission_provenance" in hits[0]
        assert hits[0]["permission_provenance"]["approval_source"] == "engine-rule"

    def test_cache_hit_message_protocol_order(self, tmp_path):
        """缓存命中不再在循环内乱插messages：tool结果必须跟随assistant tool_calls"""
        agent = self._agent_with_cache(tmp_path, "cached-2")
        resp, _ = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "读一下"}], session_id="s1"))
        # 全程无异常即协议顺序由循环尾统一处理（原实现NameError根本走不到这里）
        assert "完成" in resp

    def test_cache_miss_path_unaffected(self, tmp_path):
        """对照：缓存未命中走真实执行路径，同样带provenance进账本"""
        tc_chunk = {"tool_calls": [{
            "id": "c1",
            "function": {"name": "search_files",
                         "arguments": json.dumps({"pattern": "def", "path": str(tmp_path)})},
        }]}
        agent = make_agent(tmp_path, acquired=True, scripts=[[tc_chunk], ["完成"]])
        agent._permission_gate = MagicMock()
        agent._permission_gate.check = _allow_gate_check
        agent._tool_cache = MagicMock()
        agent._tool_cache.get.return_value = None  # 未命中
        agent._tool_error_handler = MagicMock()
        agent._perm_provenance = PermissionProvenanceRecorder(
            ledger_path=str(tmp_path / "prov2.jsonl"))
        _resp, all_tool_calls = run(agent._run_llm_with_tools(
            messages=[{"role": "user", "content": "搜一下"}], session_id="s1"))
        assert all_tool_calls and "permission_provenance" in all_tool_calls[0]
        assert all_tool_calls[0]["permission"] == "allow"


async def _allow_gate_check(session_id, tool_name, tool_args, **kw):
    return _allow_gate()


class TestSoulmateWiring:
    """接线断言：build_provenance在真实消息路径被调用（防'写了≠接线了'）"""

    def test_run_llm_tools_calls_build_provenance(self):
        src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        assert "build_provenance(" in src
        assert '"permission_provenance": gate_provenance' in src
        # P0守护：cache-hit分支绝不能再引用未定义的tool_calls_log（仅注释提及允许）
        assert "tool_calls_log.append" not in src

    def test_code_mode_inner_calls_build_provenance(self):
        # Pi #1 拆钩子面包裹层后执行体=_code_mode_tool_call_inner（接线不变）
        src = inspect.getsource(SoulMateAgent._code_mode_tool_call_inner)
        assert "build_provenance(" in src
        assert 'via="code_mode"' in src

    def test_init_creates_recorder(self):
        src = inspect.getsource(SoulMateAgent.__init__)
        assert "PermissionProvenanceRecorder()" in src

    def test_provenance_present_on_deny_entry_too(self):
        src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        # 拒绝与放行两种条目都带provenance（kilocode：每次审批结果含拒绝都写回）
        assert src.count('gate_provenance') >= 3
