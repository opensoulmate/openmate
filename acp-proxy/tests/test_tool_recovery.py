"""路线图 2.2 失败恢复测试——工具报错自动重试/换路径（最多2次）+失败原因入日志。

覆盖：失败四态判定 / 恢复计划确定性映射与2次封顶 / 路径修正换路径 /
SQLite失败日志持久化（env覆盖）/ _tool_recovery_execute 集成（fake执行器：
一次失败→重试成功；连败→exhausted；BLOCKED绝不重试；无方案→no_plan）/
soulmate_agent 接线断言。
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.tool_recovery import (  # noqa: E402
    MAX_RECOVERY_ATTEMPTS,
    FailureKind,
    ToolRecoveryManager,
    classify_result,
)


# ── 失败判定 ─────────────────────────────────────────────────────

class TestClassify:
    def test_ok_normal_output(self):
        assert classify_result("1|hello world") == FailureKind.OK

    def test_ok_empty(self):
        assert classify_result("") == FailureKind.OK

    def test_blocked_never_error(self):
        # 权限拦截不是工具失败——绝不进入恢复（重试=绕过权限引擎）
        assert classify_result("[被拦截:ask] 需要人工确认") == FailureKind.BLOCKED

    def test_transient_timeout(self):
        assert classify_result("错误: 执行超时（10秒）") == FailureKind.TRANSIENT
        assert classify_result("[CODE_MODE] 工具执行超时: read_file") == FailureKind.TRANSIENT

    def test_transient_network(self):
        assert classify_result("Error: Connection reset by peer") == FailureKind.TRANSIENT
        assert classify_result("错误: 网络不可达") == FailureKind.TRANSIENT

    def test_recoverable_not_found(self):
        assert classify_result("错误: 文件不存在或为空") == FailureKind.RECOVERABLE
        assert classify_result("错误: sed: read x.txt: No such file or directory") \
            == FailureKind.RECOVERABLE

    def test_permanent_validation(self):
        assert classify_result("错误: write_file 参数不完整（path='')") == FailureKind.PERMANENT
        assert classify_result("错误: terminal command 不能为空") == FailureKind.PERMANENT

    def test_code_mode_exception_is_failure(self):
        # read_turn.result_ok 漏网形态——[CODE_MODE] 工具执行异常 必须判失败
        kind = classify_result("[CODE_MODE] 工具执行异常: read_file: boom")
        assert kind != FailureKind.OK


# ── 恢复计划 ─────────────────────────────────────────────────────

class TestPlanSteps:
    def test_transient_read_file_retry_then_fallback(self):
        mgr = ToolRecoveryManager(db_path=":memory:")
        steps = mgr.plan_steps(
            "read_file", {"path": "/tmp/a.txt", "offset": 1, "limit": 10},
            "错误: Connection reset by peer", FailureKind.TRANSIENT,
        )
        assert len(steps) <= MAX_RECOVERY_ATTEMPTS
        assert steps[0].action == "retry"
        assert steps[0].tool_name == "read_file"
        assert steps[1].action == "fallback"
        assert steps[1].tool_name == "read_file_segment"
        assert steps[1].func_args["path"] == "/tmp/a.txt"

    def test_not_found_path_fix_locates_real_file(self, tmp_path):
        real = tmp_path / "sub" / "report.md"
        real.parent.mkdir()
        real.write_text("hello")
        mgr = ToolRecoveryManager(db_path=":memory:")
        steps = mgr.plan_steps(
            "read_file", {"path": "/wrong/dir/report.md"},
            "错误: 文件不存在或为空", FailureKind.RECOVERABLE,
            search_roots=[str(tmp_path)],
        )
        assert steps and steps[0].action == "fallback"
        assert steps[0].func_args["path"] == str(real)
        assert "路径修正" in steps[0].note

    def test_not_found_no_match_falls_to_segment(self):
        mgr = ToolRecoveryManager(db_path=":memory:")
        steps = mgr.plan_steps(
            "read_file", {"path": "/nope/zzz-missing.txt"},
            "错误: 文件不存在或为空", FailureKind.RECOVERABLE,
            search_roots=[],
        )
        assert steps and steps[0].tool_name == "read_file_segment"

    def test_read_file_segment_fallback_to_read_file(self):
        mgr = ToolRecoveryManager(db_path=":memory:")
        steps = mgr.plan_steps(
            "read_file_segment", {"path": "/tmp/a.txt", "start_line": 5, "end_line": 20},
            "错误: 文件不存在或为空", FailureKind.RECOVERABLE,
        )
        assert steps and steps[0].tool_name == "read_file"
        assert steps[0].func_args["offset"] == 5
        assert steps[0].func_args["limit"] == 16

    def test_search_files_not_found_re_roots(self):
        mgr = ToolRecoveryManager(db_path=":memory:")
        steps = mgr.plan_steps(
            "search_files", {"pattern": "x", "path": "/gone"},
            "错误: 路径不存在: /gone", FailureKind.RECOVERABLE,
            search_roots=["/home/x"],
        )
        assert steps and steps[0].func_args["path"] == "/home/x"

    def test_terminal_and_write_never_auto_rerun(self):
        # 副作用工具禁自动重跑（写操作/命令执行）
        mgr = ToolRecoveryManager(db_path=":memory:")
        assert mgr.plan_steps(
            "terminal", {"command": "rm x"}, "错误: timeout", FailureKind.TRANSIENT) == []
        assert mgr.plan_steps(
            "write_file", {"path": "a", "content": "b"},
            "写入失败: disk full", FailureKind.PERMANENT) == []
        assert mgr.plan_steps(
            "execute_code", {"code": "1/0"},
            "错误: ZeroDivisionError", FailureKind.PERMANENT) == []

    def test_permanent_gets_no_steps(self):
        mgr = ToolRecoveryManager(db_path=":memory:")
        assert mgr.plan_steps(
            "read_file", {"path": ""}, "错误: 参数不完整", FailureKind.PERMANENT) == []

    def test_plan_capped_at_two(self):
        mgr = ToolRecoveryManager(db_path=":memory:")
        steps = mgr.plan_steps(
            "web_extract", {"urls": ["http://x"]},
            "Error: timeout", FailureKind.TRANSIENT)
        assert len(steps) <= MAX_RECOVERY_ATTEMPTS
        assert all(s.action == "retry" for s in steps)


# ── 失败日志持久化 ───────────────────────────────────────────────

class TestFailureLog:
    def test_record_and_recent(self, tmp_path):
        db = tmp_path / "failures.db"
        mgr = ToolRecoveryManager(db_path=str(db))
        mgr.record("sess1", "read_file", "transient", "错误: 超时",
                   action="retry", attempt=1, outcome="recovered",
                   note="瞬态失败原路重试", func_args={"path": "/a"})
        mgr.record("sess1", "read_file", "transient", "错误: 超时",
                   action="fallback", attempt=2, outcome="failed",
                   func_args={"path": "/a"})
        rows = mgr.recent(10)
        assert len(rows) == 2
        assert rows[0]["outcome"] == "failed"  # 倒序
        assert rows[1]["outcome"] == "recovered"
        assert rows[1]["error_first_line"] == "错误: 超时"
        stats = mgr.stats()
        assert stats["total_failures"] == 2
        assert stats["by_tool"]["read_file"] == 2

    def test_env_override_path(self, tmp_path, monkeypatch):
        db = tmp_path / "env_failures.db"
        monkeypatch.setenv("TOOL_FAILURE_DB_PATH", str(db))
        mgr = ToolRecoveryManager()
        mgr.record("s", "t", "transient", "错误: x",
                   action="retry", attempt=1, outcome="failed")
        assert db.exists()
        assert mgr.recent(1)[0]["tool_name"] == "t"

    def test_record_failure_never_raises(self):
        mgr = ToolRecoveryManager(db_path="/proc/nonexistent-dir/x.db")
        mgr.record("s", "t", "transient", "错误: x",
                   action="retry", attempt=1, outcome="failed")  # 不抛异常
        assert mgr.recent(5) == []


# ── _tool_recovery_execute 集成（fake执行器）─────────────────────

class _FakeAgent:
    """最小 stub：_tool_recovery_execute 只依赖 _tool_recovery/
    _code_mode_tool_call/_session_cwds/_project_root。"""

    def __init__(self, outcomes, db_path):
        self._tool_recovery = ToolRecoveryManager(db_path=db_path)
        self._session_cwds = {"s1": "/tmp"}
        self._project_root = "/tmp"
        self.calls = []
        self._outcomes = list(outcomes)

    async def _code_mode_tool_call(self, session_id, func_name, func_args, cwd):
        self.calls.append((func_name, tuple(sorted(func_args.items()))))
        return self._outcomes.pop(0) if self._outcomes else "错误: 仍然失败"


def _run(coro):
    # asyncio.run 每次全新事件循环——get_event_loop 在混跑时可能无当前循环
    return asyncio.run(coro)


def _load_recovery_fn():
    from agent.soulmate_agent import SoulMateAgent
    return SoulMateAgent._tool_recovery_execute


class TestRecoveryExecute:
    def test_retry_recovers_on_second_attempt(self, tmp_path):
        fn = _load_recovery_fn()
        fake = _FakeAgent(["1|重试成功内容"], str(tmp_path / "f.db"))
        out = _run(fn(fake, "s1", "web_search", {"query": "x"},
                      "", "错误: Connection reset by peer"))
        assert "重试成功内容" in out
        assert len(fake.calls) == 1  # 只重试1次就成功
        rows = fake._tool_recovery.recent(10)
        assert rows[0]["outcome"] == "recovered"

    def test_exhausted_logs_and_returns_error(self, tmp_path):
        fn = _load_recovery_fn()
        fake = _FakeAgent([], str(tmp_path / "f.db"))  # 每次都失败
        out = _run(fn(fake, "s1", "read_file", {"path": "/tmp/a.txt", "offset": 1, "limit": 5},
                      "", "错误: Connection reset by peer"))
        assert classify_result(out) != FailureKind.OK  # 最终错误如实回注
        assert len(fake.calls) <= MAX_RECOVERY_ATTEMPTS  # 2次封顶
        outcomes = [r["outcome"] for r in fake._tool_recovery.recent(20)]
        assert "exhausted" in outcomes

    def test_blocked_passthrough_no_retry(self, tmp_path):
        fn = _load_recovery_fn()
        fake = _FakeAgent([], str(tmp_path / "f.db"))
        blocked = "[被拦截:ask] 需要人工确认"
        out = _run(fn(fake, "s1", "terminal", {"command": "sudo ls"}, "", blocked))
        assert out == blocked  # 原样返回
        assert fake.calls == []  # 绝不重试（不绕权限引擎）
        assert fake._tool_recovery.recent(5) == []  # 拦截非失败，不记失败日志

    def test_no_plan_logs_no_plan(self, tmp_path):
        fn = _load_recovery_fn()
        fake = _FakeAgent([], str(tmp_path / "f.db"))
        err = "错误: write_file 参数不完整（path='')"
        out = _run(fn(fake, "s1", "write_file", {"path": "", "content": ""}, "", err))
        assert out == err
        assert fake.calls == []
        rows = fake._tool_recovery.recent(5)
        assert rows[0]["outcome"] == "no_plan"
        assert rows[0]["action"] == "none"

    def test_ok_result_short_circuits(self, tmp_path):
        fn = _load_recovery_fn()
        fake = _FakeAgent([], str(tmp_path / "f.db"))
        out = _run(fn(fake, "s1", "read_file", {"path": "/a"}, "", "1|正常内容"))
        assert out == "1|正常内容"
        assert fake.calls == []


# ── soulmate_agent 接线断言（"写了≠接线了"）─────────────────────

class TestWiring:
    def test_agent_source_wired(self):
        src = (Path(__file__).resolve().parent.parent
               / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")
        assert "from agent.tool_recovery import" in src
        assert "self._tool_recovery = ToolRecoveryManager()" in src
        # 主循环收口 + code-mode批内两处都接
        assert src.count("_tool_recovery_execute(") >= 3  # 定义1 + 调用2
        # 主循环调用在 tool_results.append 之前
        main_call = src.index("result = await self._tool_recovery_execute(")
        tail = src[main_call:main_call + 600]
        assert "tool_results.append({" in tail  # 收口后就近入结果（比对全文首匹配会命中更早分支）
        # code-mode批内调用紧跟 _code_mode_tool_call
        cm_call = src.index("_r = await self._tool_recovery_execute(")
        assert cm_call > src.index("async def _cm_dispatch")

    def test_agent_init_has_recovery(self):
        from agent.soulmate_agent import SoulMateAgent
        assert hasattr(SoulMateAgent, "_tool_recovery_execute")
