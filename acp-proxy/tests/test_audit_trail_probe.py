# -*- coding: utf-8 -*-
"""12.6 轨迹/审计闭环抽查探针测试（audit_trail_probe + ③④修复面）。

覆盖：分类行完整性（值域/unknown 缺口）、孤儿行（step工具名-收尾工具名
note 连接口径）、llm 归因串线、ts 毫秒单位（③）、dna 死包装对齐（④）、
classify_failure 码串透传+RequestError 分类缺口、探针端到端真实库回读。
"""
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit_trail_probe import (  # noqa: E402
    check_classification,
    check_llm_attribution,
    check_orphans,
    check_provenance_trace,
    check_ts_units,
    run_probe,
)
from agent.tool_recovery import ToolRecoveryManager  # noqa: E402
from utils.llm_retry import classify_failure  # noqa: E402


def _row(id=1, ts=1791699348841.0, session_id="s", tool_name="read_file",
         kind="recoverable", action="give_up", attempt=1, outcome="exhausted",
         error_first_line="err", note=""):
    return {
        "id": id, "ts": ts, "session_id": session_id, "tool_name": tool_name,
        "kind": kind, "action": action, "attempt": attempt,
        "outcome": outcome, "error_first_line": error_first_line, "note": note,
    }


# ── 失败分类行完整性 ──────────────────────────────────────────
class TestClassification:
    def test_valid_rows_pass(self):
        rows = [
            _row(kind="recoverable", action="fallback", outcome="failed"),
            _row(id=2, tool_name="llm:dna", kind="network_error",
                 action="llm_give_up", outcome="failed"),
        ]
        r = check_classification(rows)
        assert r["ok"] and not r["invalid_rows"] and not r["unknown_kind_rows"]

    def test_wip_misaligned_action_flagged(self):
        # 周期#69 WIP 错位实证形态：outcome 值落进 action 列
        rows = [_row(tool_name="llm:empty_output", kind="unknown",
                     action="failed", outcome="failed")]
        r = check_classification(rows)
        assert not r["ok"]
        assert any("action" in p for it in r["invalid_rows"] for p in it["problems"])

    def test_kind_domain_split_tool_vs_llm(self):
        # 工具行落 llm 九态=越界；llm 行落工具五态=越界
        rows = [
            _row(kind="rate_limit", action="none", outcome="no_plan"),
            _row(id=2, tool_name="llm:chat", kind="permanent",
                 action="llm_give_up", outcome="failed"),
        ]
        r = check_classification(rows)
        assert len(r["invalid_rows"]) == 2

    def test_unknown_kind_reported_as_gap(self):
        rows = [_row(tool_name="llm:dna", kind="unknown",
                     action="llm_give_up", outcome="failed")]
        r = check_classification(rows)
        assert r["ok"]  # 值域合法（unknown 在九态内）
        assert len(r["unknown_kind_rows"]) == 1  # 但作为分类缺口列出

    def test_negative_attempt_flagged(self):
        r = check_classification([_row(attempt=-1, action="none",
                                       outcome="no_plan")])
        assert not r["ok"]


# ── 无孤儿行 ──────────────────────────────────────────────────
class TestOrphans:
    def test_step_toolname_linkage_by_note(self):
        # 真实行形态：尝试行 tool_name=step 工具（read_file_segment），
        # 收尾行 tool_name=原工具（read_file），note 提及原工具名=连接
        rows = [
            _row(id=1, tool_name="read_file_segment", action="fallback",
                 attempt=1, outcome="failed",
                 note="换路径: read_file → read_file_segment"),
            _row(id=2, tool_name="read_file", action="give_up",
                 attempt=1, outcome="exhausted", note="2次恢复均失败"),
        ]
        r = check_orphans(rows)
        assert r["ok"] and not r["orphan_rows"]

    def test_exhausted_without_process_is_orphan(self):
        r = check_orphans([_row(id=5, action="give_up", outcome="exhausted")])
        assert not r["ok"] and len(r["orphan_rows"]) == 1

    def test_empty_toolname_not_wildcard_match(self):
        # 空 tool_name 不得用 "" in note 通配吞掉任意先行行
        rows = [
            _row(id=1, tool_name="something_else", action="retry",
                 attempt=1, outcome="failed", note="瞬态失败原路重试"),
            _row(id=2, tool_name="", action="give_up", outcome="exhausted"),
        ]
        r = check_orphans(rows)
        assert len(r["orphan_rows"]) == 1

    def test_llm_action_outcome_pairing(self):
        rows = [_row(tool_name="llm:chat", action="llm_retry",
                     outcome="failed")]  # llm_retry 必须配 retry_scheduled
        r = check_orphans(rows)
        assert len(r["llm_pairing_violations"]) == 1

    def test_cross_session_not_linked(self):
        rows = [
            _row(id=1, session_id="a", action="retry", attempt=1,
                 outcome="failed", note="瞬态失败原路重试"),
            _row(id=2, session_id="b", action="give_up", outcome="exhausted"),
        ]
        assert len(check_orphans(rows)["orphan_rows"]) == 1


# ── llm 归因串线 ──────────────────────────────────────────────
class TestLLMAttribution:
    def test_known_paths_pass(self):
        rows = [
            _row(tool_name="llm:chat", kind="protocol_error",
                 action="llm_retry", outcome="retry_scheduled"),
            _row(id=2, tool_name="llm:dna", kind="network_error",
                 action="llm_give_up", outcome="failed",
                 error_first_line="[strand_b] DecodingError: x"),
        ]
        r = check_llm_attribution(rows)
        assert r["ok"] and r["dna_rows"] == 1

    def test_misrouted_tool_name_flagged(self):
        # WIP 错位实证形态（真实库 id=115）：异常串当 path
        rows = [_row(tool_name="llm:[Errno -2] Name or service not known",
                     kind="unknown", action="llm_retry",
                     outcome="retry_scheduled")]
        r = check_llm_attribution(rows)
        assert len(r["misrouted_rows"]) == 1

    def test_dna_missing_strand_flagged(self):
        rows = [_row(tool_name="llm:dna", kind="unknown",
                     action="llm_give_up", outcome="failed",
                     error_first_line="DecodingError: no strand here")]
        r = check_llm_attribution(rows)
        assert len(r["dna_missing_strand"]) == 1

    def test_non_llm_rows_ignored(self):
        assert check_llm_attribution([_row()])["ok"]


# ── ③ ts 毫秒单位 ────────────────────────────────────────────
class TestTsUnits:
    def test_ms_and_sec_census(self):
        rows = [_row(ts=1791699348841), _row(id=2, ts=1791699348.84)]
        r = check_ts_units(rows)
        assert r["ms_rows"] == 1 and r["sec_rows"] == 1 and not r["ok"]

    def test_record_writes_ms(self, tmp_path):
        mgr = ToolRecoveryManager(db_path=str(tmp_path / "tf.db"))
        mgr.record("s", "read_file", "recoverable", "错误：x",
                   action="none", attempt=0, outcome="no_plan")
        conn = sqlite3.connect(str(tmp_path / "tf.db"))
        ts = conn.execute("SELECT ts FROM tool_failures").fetchone()[0]
        conn.close()
        assert float(ts) >= 10 ** 10  # 毫秒 UTC，非秒
        assert abs(float(ts) - time.time() * 1000) < 60_000


# ── ④ 死包装对齐 + classify 透传 ──────────────────────────────
class TestDnaWrapperAligned:
    def test_delegates_with_correct_signature(self, monkeypatch):
        import dna_evolution
        captured = {}

        def fake(path, source, action, attempt, outcome, detail=""):
            captured.update(path=path, source=source, action=action,
                            attempt=attempt, outcome=outcome, detail=detail)
            return True

        monkeypatch.setattr(
            "utils.llm_transport._record_failure_safe", fake)
        dedup: set = set()
        dna_evolution._record_llm_failure_safe(
            "strand_a", "rate_limit", "上游429",
            request_id="req-1", _dedup=dedup)
        assert captured["path"] == "dna"
        assert captured["source"] == "rate_limit"
        assert captured["action"] == "llm_give_up"
        assert captured["outcome"] == "failed"
        assert captured["detail"].startswith("[strand_a]")
        # 同请求同码只记一次
        dna_evolution._record_llm_failure_safe(
            "strand_a", "rate_limit", "上游429",
            request_id="req-1", _dedup=dedup)
        assert len(dedup) == 1

    def test_no_typeerror_swallowed(self, monkeypatch):
        # 对齐前 kwargs 形态会 TypeError 被吞=记账静默丢失；对齐后真实落账
        import dna_evolution
        calls = []

        def fake(**kwargs):
            calls.append(kwargs)
            return True

        monkeypatch.setattr(
            "utils.llm_transport._record_failure_safe", fake)
        dna_evolution._record_llm_failure_safe(
            "strand_b", "server_error", "503", _dedup=set())
        assert len(calls) == 1 and calls[0]["path"] == "dna"

    def test_classify_code_passthrough(self):
        assert classify_failure("rate_limit") == "rate_limit"
        assert classify_failure("protocol_error") == "protocol_error"
        assert classify_failure("not_a_code") == "unknown"

    def test_classify_empty_output_gap_closed(self):
        """周期#88：empty_output 预分类串码透传（此前不在码集→落 unknown 分类缺口，
        探针实证 id=142 新行缺分类）"""
        assert classify_failure("empty_output") == "empty_output"
        assert classify_failure("empty_output") != "unknown"

    def test_classify_decoding_error_gap_closed(self):
        import httpx
        err = httpx.DecodingError("Error -3 while decompressing data")
        assert classify_failure(err) == "network_error"
        # 非 httpx 的通用异常仍落 unknown（零缩水）
        assert classify_failure(ValueError("x")) == "unknown"


# ── 探针端到端 + 11.5 观察面 ──────────────────────────────────
class TestProbeE2E:
    def _make_db(self, tmp_path) -> Path:
        db = tmp_path / "tf.db"
        mgr = ToolRecoveryManager(db_path=str(db))
        mgr.record("s", "read_file_segment", "recoverable",
                   "错误：溢出文件不存在", action="fallback", attempt=1,
                   outcome="failed", note="换路径: read_file → read_file_segment")
        mgr.record("s", "read_file", "recoverable", "错误：溢出文件不存在",
                   action="give_up", attempt=1, outcome="exhausted",
                   note="2次恢复均失败")
        return db

    def test_run_probe_on_real_writer_db(self, tmp_path):
        db = self._make_db(tmp_path)
        rep = run_probe(db, tmp_path / "no-events.db")
        assert rep["rows"] == 2
        assert rep["classification"]["ok"]
        assert rep["orphans"]["ok"]
        assert rep["llm_attribution"]["ok"]
        assert rep["ts_units"]["ok"] and rep["ts_units"]["ms_rows"] == 2
        assert not rep["dna_closure"]["closed"]  # 无 llm:dna 行如实报未闭环
        assert not rep["provenance_observation"]["available"]

    def test_provenance_observation_counts(self, tmp_path):
        ev = tmp_path / "opensoul.db"
        conn = sqlite3.connect(str(ev))
        conn.execute(
            "CREATE TABLE trajectory_events (id INTEGER PRIMARY KEY,"
            " content TEXT, arguments_json TEXT)")
        conn.execute(
            "INSERT INTO trajectory_events (content, arguments_json) VALUES"
            " ('[infection-guard]', '{\"action\": \"pass_with_trace\","
            " \"source\": \"provenance_pass_with_trace\"}')")
        conn.execute(
            "INSERT INTO trajectory_events (content, arguments_json) VALUES"
            " ('[infection-guard]', '{\"action\": \"pass_with_trace\","
            " \"source\": \"self_heal_pass_with_trace\"}')")
        conn.commit()
        conn.close()
        r = check_provenance_trace(ev)
        assert r["available"] and r["provenance_rows"] == 1
        assert r["self_heal_rows"] == 1

    def test_provenance_zero_reported_honestly(self, tmp_path):
        ev = tmp_path / "opensoul.db"
        conn = sqlite3.connect(str(ev))
        conn.execute("CREATE TABLE trajectory_events (id INTEGER PRIMARY KEY,"
                     " content TEXT, arguments_json TEXT)")
        conn.commit()
        conn.close()
        r = check_provenance_trace(ev)
        assert r["provenance_rows"] == 0
        assert "尚未自然触发" in r["note"]

    def test_cli_json(self, tmp_path, capsys):
        from audit_trail_probe import main
        db = self._make_db(tmp_path)
        rc = main(["--db", str(db), "--events-db", str(tmp_path / "x.db"),
                   "--json"])
        assert rc == 0
        assert '"rows": 2' in capsys.readouterr().out

    def test_cli_missing_db_exits_1(self, capsys):
        from audit_trail_probe import main
        assert main(["--db", "/nonexistent/tf.db"]) == 1
