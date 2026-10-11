# -*- coding: utf-8 -*-
"""路线图 15.2 工具执行时长落账测试（trajectory_events tool_call duration_ms 全 0 盲段销账）

覆盖：
1. _tool_child_events 透传：账本条目 duration_ms → tool_call 事件列（旧条目缺省 0）
2. record_round / record_llm_call 真实落账路径批量事件带 duration_ms
3. 真实 _run_llm_with_tools E2E：真实工具执行（terminal echo）→ 账本条目 duration_ms>0；
   缓存命中条目带键（如实近 0）；权限拦截条目不计时（无执行=不编造时长）
4. turn_timing_report ③工具执行段独立列：tool_exec_s/计时覆盖/batch: 子调用不重复计/
   工具计时跨度计入空洞覆盖（⑤收窄）/未计时行诚实标注
5. 接线护栏（源码断言落账代码在真实路径）
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.soulmate_agent as sma  # noqa: E402
from agent.soulmate_agent import SoulMateAgent  # noqa: E402
from agent.trajectory_recorder import TrajectoryRecorder  # noqa: E402
from turn_timing_report import report as timing_report  # noqa: E402
from tests.test_aux_llm_ledger import _timing_conn  # noqa: E402
from tests.test_loop_guard_wiring import _tc, _wire_agent  # noqa: E402
from tests.test_steering import run  # noqa: E402


# ════════════════════════════════════════════════════════════════
# 1. _tool_child_events — duration_ms 透传
# ════════════════════════════════════════════════════════════════

class TestToolChildEventsDuration:
    def _events(self, tools):
        rec = TrajectoryRecorder.__new__(TrajectoryRecorder)
        return rec._tool_child_events(tools, "span1")

    def test_duration_passed_through(self):
        ev = self._events([{"name": "terminal", "arguments": {"command": "ls"},
                            "result_preview": "ok", "permission": "allow",
                            "duration_ms": 123.4}])
        assert ev[0]["duration_ms"] == 123.4

    def test_missing_duration_defaults_zero(self):
        """旧条目/拦截条目无 duration_ms → 如实 0，不编造"""
        ev = self._events([{"name": "terminal", "arguments": {}, "result_preview": "ok"}])
        assert ev[0]["duration_ms"] == 0.0

    def test_batch_child_passthrough(self):
        ev = self._events([{"name": "batch:read_file", "arguments": "{}",
                            "result_preview": "ok 12ms len=3", "duration_ms": 12.0}])
        assert ev[0]["duration_ms"] == 12.0

    def test_existing_shape_untouched(self):
        """既有字段形状零回归（metadata/status 契约）"""
        ev = self._events([{"name": "terminal", "arguments": {"command": "ls"},
                            "result_preview": "ok", "permission": "allow",
                            "duration_ms": 5.0}])
        assert ev[0]["event_type"] == "tool_call"
        assert ev[0]["status"] == "ok"
        assert ev[0]["metadata"]["name"] == "terminal"
        assert ev[0]["parent_event_id"] == "span1"


# ════════════════════════════════════════════════════════════════
# 2. record_round / record_llm_call 落账路径批量事件带 duration_ms
# ════════════════════════════════════════════════════════════════

class _CaptureRec(TrajectoryRecorder):
    def __init__(self):
        self.posts = []

    async def _post(self, path: str, payload: dict, allow_404: bool = False) -> dict | None:  # noqa: ARG002
        self.posts.append((path, payload))
        return {"id": "ev1"}


class TestRecorderPassthrough:
    def test_record_round_batch_events_carry_duration(self):
        rec = _CaptureRec()
        run(rec.record_round("s1", "seg1", round_index=1, content="hi",
                             duration_ms=3000,
                             tools=[{"name": "terminal", "arguments": {},
                                     "result_preview": "ok", "permission": "allow",
                                     "duration_ms": 456.7}]))
        batch = [p for p in rec.posts if p[0].endswith("/events/batch")]
        assert batch, "record_round 必须落 tool_call 子事件批量"
        evs = batch[0][1]["events"]
        assert evs[0]["duration_ms"] == 456.7

    def test_record_llm_call_batch_events_carry_duration(self):
        rec = _CaptureRec()
        run(rec.record_llm_call("s1", "root1", content="chat_turn", duration_ms=9000,
                                tools=[{"name": "read_file", "arguments": {},
                                        "result_preview": "ok", "permission": "allow",
                                        "duration_ms": 88.0}]))
        batch = [p for p in rec.posts if p[0].endswith("/events/batch")]
        assert batch, "record_llm_call 必须落 tool_call 子事件批量"
        assert batch[0][1]["events"][0]["duration_ms"] == 88.0


# ════════════════════════════════════════════════════════════════
# 3. 真实 _run_llm_with_tools E2E — 执行点计时进账本条目
# ════════════════════════════════════════════════════════════════

class TestExecutionTimingE2E:
    def test_real_tool_execution_lands_duration(self, tmp_path):
        """真实执行（terminal echo 子进程）→ 账本条目 duration_ms>0"""
        call = _tc(args={"command": "echo SOULMATE_DUR_OK"}, tc_id="dur1")
        agent = _wire_agent(tmp_path, [[call], ["done"]])
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1", user_text="go"))
        executed = [t for t in tools if t.get("name") == "terminal"]
        assert executed, f"terminal 执行条目缺失: {tools}"
        assert executed[0].get("duration_ms", 0) > 0, "执行时长必须落账（>0）"
        assert "SOULMATE_DUR_OK" in executed[0].get("result_preview", "")

    def test_cached_path_carries_duration_key(self, tmp_path):
        """缓存命中条目带键（如实近 0）——键存在=观察面统一形状"""
        call = _tc(name="read_file", args={"path": "/tmp/nope"}, tc_id="dur2")
        agent = _wire_agent(tmp_path, [[call], ["done"]])
        _cache = MagicMock()
        _cache.get.return_value = "cached content"
        agent._tool_cache = _cache
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1", user_text="go"))
        cached = [t for t in tools if t.get("cached")]
        assert cached, f"缓存命中条目缺失: {tools}"
        assert "duration_ms" in cached[0]
        assert cached[0]["duration_ms"] >= 0

    def test_blocked_entry_has_no_madeup_duration(self, tmp_path):
        """权限拦截=无执行 → 不编造时长（缺省 0），诚实边界"""
        from tests.test_loop_guard_wiring import FakeGateDeny
        call = _tc(args={"command": "echo hi"}, tc_id="dur3")
        agent = _wire_agent(tmp_path, [[call], ["done"]], gate=FakeGateDeny())
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1", user_text="go"))
        blocked = [t for t in tools if t.get("name") == "terminal"]
        assert blocked
        assert blocked[0].get("duration_ms", 0) == 0, "拦截条目不得冒充执行时长"


# ════════════════════════════════════════════════════════════════
# 4. turn_timing_report — ③工具执行段独立列
# ════════════════════════════════════════════════════════════════

_T0 = "2026-10-11T21:00:00.000000+08:00"
_T1 = "2026-10-11T21:00:01.000000+08:00"
_T5 = "2026-10-11T21:00:05.000000+08:00"
_T6 = "2026-10-11T21:00:06.000000+08:00"
_T10 = "2026-10-11T21:00:10.000000+08:00"


class TestTimingReportToolSegment:
    def test_tool_exec_column_and_metering(self):
        rows = [
            ("s1", _T0, _T10, "user_input", "目标", 10000),
            ("s1", _T5, None, "tool_call", "terminal", 4000),
        ]
        rep = timing_report("s1", _timing_conn(rows))
        assert rep["tool_exec_s"] == 4.0
        assert rep["tool_exec_calls"] == 1
        assert rep["tool_exec_metered"] == "1/1"
        assert rep["tool_timing_gap"] == ""

    def test_batch_children_not_double_counted(self):
        """batch: 子调用时长含于 batch_execute 行——明细可见、汇总不重复计"""
        rows = [
            ("s1", _T0, _T10, "user_input", "目标", 10000),
            ("s1", _T5, None, "tool_call", "batch_execute", 3000),
            ("s1", _T5, None, "tool_call", "batch:read_file", 1000),
            ("s1", _T5, None, "tool_call", "batch:terminal", 1500),
        ]
        rep = timing_report("s1", _timing_conn(rows))
        assert rep["tool_exec_s"] == 3.0, "批内子调用不得重复计入工具段合计"
        assert rep["tool_batch_child_s"] == 2.5
        assert rep["tool_exec_metered"] == "3/3"

    def test_tool_span_narrows_gaps(self):
        """工具计时跨度计入覆盖 → ⑤未归属空洞收窄（15.2 验收）"""
        base = [
            ("s1", _T0, _T10, "user_input", "目标", 10000),
            ("s1", _T5, None, "tool_call", "terminal", 0),
        ]
        metered = [
            ("s1", _T0, _T10, "user_input", "目标", 10000),
            ("s1", _T5, None, "tool_call", "terminal", 4000),  # 覆盖 T1→T5
        ]
        rep0 = timing_report("s1", _timing_conn(base))
        rep1 = timing_report("s1", _timing_conn(metered))
        assert rep0["gap_total_s"] == 10.0, "未计时基线：全程空洞"
        assert rep1["gap_total_s"] == 5.0, f"计时跨度应覆盖 T1→T5，实际 {rep1['gaps']}"

    def test_all_zero_rows_report_honestly(self):
        rows = [
            ("s1", _T0, _T10, "user_input", "目标", 10000),
            ("s1", _T5, None, "tool_call", "terminal", 0),
            ("s1", _T6, None, "tool_call", "read_file", 0),
        ]
        rep = timing_report("s1", _timing_conn(rows))
        assert rep["tool_exec_s"] == 0.0
        assert "全 0" in rep["tool_timing_gap"]

    def test_partial_metering_reported_per_row(self):
        rows = [
            ("s1", _T0, _T10, "user_input", "目标", 10000),
            ("s1", _T5, None, "tool_call", "terminal", 2000),
            ("s1", _T6, None, "tool_call", "read_file", 0),
        ]
        rep = timing_report("s1", _timing_conn(rows))
        assert rep["tool_exec_s"] == 2.0
        assert rep["tool_exec_metered"] == "1/2"
        assert "1/2 行" in rep["tool_timing_gap"]

    def test_existing_columns_untouched(self):
        """既有归因仪表字段零回归（14.4 aux 测试消费面）"""
        rows = [
            ("s1", _T0, _T10, "user_input", "目标", 10000),
            ("s1", _T5, None, "llm_call", "chat_turn", 3000),
        ]
        rep = timing_report("s1", _timing_conn(rows))
        assert rep["llm_total_s"] == 3.0
        assert rep["aux_total_s"] == 0.0
        assert rep["tool_instrumented_s"] == 0.0  # 兼容旧字段
        assert rep["reply_recovery_s"] == 5.0


# ════════════════════════════════════════════════════════════════
# 5. 接线护栏（源码断言=真实路径落账代码在位）
# ════════════════════════════════════════════════════════════════

class TestWiringPins:
    def test_soulmate_agent_ledger_sites(self):
        src = Path(sma.__file__).read_text(encoding="utf-8")
        # 主执行路径 + 缓存路径两处执行点计时
        assert src.count('"duration_ms": (time.time() - tool_start) * 1000') == 2
        # batch 批内子调用自身时长
        assert '"duration_ms": float(_e.get("duration_ms", 0.0) or 0.0)' in src

    def test_recorder_passthrough_site(self):
        import agent.trajectory_recorder as tr
        src = Path(tr.__file__).read_text(encoding="utf-8")
        assert '"duration_ms": float(t.get("duration_ms", 0.0) or 0.0)' in src

    def test_report_consumes_duration_column(self):
        import turn_timing_report as ttr
        src = Path(ttr.__file__).read_text(encoding="utf-8")
        assert '"tool_exec_s"' in src
        assert 'startswith("batch:")' in src
