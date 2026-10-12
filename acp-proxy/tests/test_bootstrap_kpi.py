"""P2.3 自举 KPI 四联整合测试（bootstrap_kpi）。

合成库按生产 schema 建表（trajectory_events / perm_decision_audit / reflections），
每个 KPI 有正例+空样本诚实降级负例+串味防污染负例；soulmate 压缩点落点接线钉。
"""

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.bootstrap_kpi import compute_kpis  # noqa: E402

_TRAJ_SCHEMA = """
CREATE TABLE trajectory_events (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, parent_event_id TEXT,
    event_type TEXT NOT NULL, agent_id TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
    token_usage INTEGER NOT NULL DEFAULT 0, duration_ms REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ok', created_at TEXT NOT NULL, end_at TEXT
)
"""

_PERM_SCHEMA = """
CREATE TABLE perm_decision_audit (
    decision_id TEXT PRIMARY KEY, session_id TEXT DEFAULT '', tool_name TEXT NOT NULL,
    tool_input_digest TEXT DEFAULT '', tool_input_preview TEXT DEFAULT '',
    behavior TEXT NOT NULL, decision_reason TEXT DEFAULT '', message TEXT DEFAULT '',
    rule_id TEXT DEFAULT '', rule_source TEXT DEFAULT '', rule_content TEXT DEFAULT '',
    requires_human INTEGER DEFAULT 0, risk TEXT DEFAULT 'medium', mode TEXT DEFAULT '',
    outcome TEXT DEFAULT '', outcome_comment TEXT DEFAULT '',
    created_at REAL NOT NULL, outcome_at REAL
)
"""

_REFL_SCHEMA = """
CREATE TABLE reflections (
    reflection_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    task_description TEXT NOT NULL, result_summary TEXT DEFAULT '',
    quality_score REAL DEFAULT 0.5, strengths TEXT DEFAULT '[]',
    weaknesses TEXT DEFAULT '[]', improvement_suggestions TEXT DEFAULT '[]',
    error_patterns TEXT DEFAULT '[]', created_at REAL NOT NULL, applied INTEGER DEFAULT 0
)
"""


def _mk_traj(path: Path, turns: list[dict], extra_events: list[dict] | None = None) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(_TRAJ_SCHEMA)
    for i, t in enumerate(turns):
        meta = {"kind": "turn_root", "chars": 10}
        if t.get("termination"):
            meta["termination"] = t["termination"]
        conn.execute(
            "INSERT INTO trajectory_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"e{i}", t.get("sid", "s1"), None, "user_input", "soulmate",
                "turn", json.dumps(meta), 0, 0.0, t.get("status", "ok"),
                t["created_at"], t.get("end_at", t["created_at"]),
            ),
        )
    for j, e in enumerate(extra_events or []):
        conn.execute(
            "INSERT INTO trajectory_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"x{j}", e.get("sid", "s1"), None, e.get("event_type", "custom"), "soulmate",
                e.get("content", ""), json.dumps(e.get("metadata", {})), 0, 0.0,
                "ok", e["created_at"], e.get("end_at"),
            ),
        )
    conn.commit()
    conn.close()
    return path


def _mk_perm(path: Path, rows: list[dict]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(_PERM_SCHEMA)
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO perm_decision_audit (decision_id, tool_name, behavior,"
            " requires_human, created_at) VALUES (?,?,?,?,?)",
            (f"d{i}", r.get("tool", "terminal"), r.get("behavior", "ask"),
             r.get("requires_human", 1), r.get("created_at", 1000.0 + i)),
        )
    conn.commit()
    conn.close()
    return path


def _mk_refl(path: Path, rows: list[dict]) -> Path:
    conn = sqlite3.connect(path)
    conn.execute(_REFL_SCHEMA)
    for i, r in enumerate(rows):
        conn.execute(
            "INSERT INTO reflections VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                f"r{i}", r.get("sid", "s1"), r.get("task", "edit"),
                r.get("summary", ""), r.get("quality", 0.5), "[]", "[]", "[]", "[]",
                r.get("created_at", 1000.0 + i), r.get("applied", 0),
            ),
        )
    conn.commit()
    conn.close()
    return path


# ── 1. 任务成功率 ──────────────────────────────────────────────────


def test_task_success_rate_and_termination_breakdown(tmp_path):
    traj = _mk_traj(
        tmp_path / "t.db",
        [
            {"created_at": "2026-01-01T00:00:01"},                          # ok 正常
            {"created_at": "2026-01-01T00:00:02"},                          # ok 正常
            {"created_at": "2026-01-01T00:00:03", "termination": "max_turns"},  # 不算成功
            {"created_at": "2026-01-01T00:00:04", "status": "error"},       # 不算成功
        ],
    )
    k = compute_kpis(db_path=traj, permission_db=tmp_path / "none.db",
                     reflections_db=tmp_path / "none.db")
    ts = k["task_success"]
    assert ts["available"] and ts["closed_turns"] == 4
    assert ts["success_turns"] == 2 and ts["rate"] == 0.5
    assert ts["termination_counts"] == {"(normal)": 3, "max_turns": 1}
    # 保守语义：error 行 termination 空→(normal) 计数但 status≠ok 不算成功


def test_task_success_open_turn_not_counted(tmp_path):
    traj = _mk_traj(
        tmp_path / "t.db",
        [
            {"created_at": "2026-01-01T00:00:01"},
            {"created_at": "2026-01-01T00:00:02", "end_at": None},  # 未收口
        ],
    )
    ts = compute_kpis(db_path=traj, permission_db=tmp_path / "n.db",
                      reflections_db=tmp_path / "n.db")["task_success"]
    assert ts["total_turns"] == 2 and ts["closed_turns"] == 1 and ts["open_turns"] == 1
    assert ts["rate"] == 1.0  # 分母只算已收口


def test_task_success_empty_honest(tmp_path):
    traj = _mk_traj(tmp_path / "t.db", [])
    ts = compute_kpis(db_path=traj, permission_db=tmp_path / "n.db",
                      reflections_db=tmp_path / "n.db")["task_success"]
    assert ts["available"] is False and ts["rate"] is None and ts["note"]


# ── 2. 人工干预率 ──────────────────────────────────────────────────


def test_human_intervention_rate(tmp_path):
    traj = _mk_traj(
        tmp_path / "t.db",
        [
            {"created_at": "2026-01-01T00:00:01"},
            {"created_at": "2026-01-01T00:00:02", "termination": "external_cancel"},
        ],
    )
    perm = _mk_perm(tmp_path / "p.db", [{}, {}, {"requires_human": 0}])
    hi = compute_kpis(db_path=traj, permission_db=perm,
                      reflections_db=tmp_path / "n.db")["human_intervention"]
    assert hi["ask_events"] == 2 and hi["external_cancel_turns"] == 1
    assert hi["interventions"] == 3 and hi["turns"] == 2
    assert hi["rate"] == 1.5  # 每回合干预次数语义（文档明示）


def test_human_intervention_no_sample_honest(tmp_path):
    traj = _mk_traj(tmp_path / "t.db", [{"created_at": "2026-01-01T00:00:01"}])
    perm = _mk_perm(tmp_path / "p.db", [])
    hi = compute_kpis(db_path=traj, permission_db=perm,
                      reflections_db=tmp_path / "n.db")["human_intervention"]
    assert hi["available"] and hi["interventions"] == 0 and hi["rate"] == 0.0


# ── 3. edit 反射次数 ──────────────────────────────────────────────


def test_edit_reflection_counts(tmp_path):
    refl = _mk_refl(tmp_path / "r.db", [{"quality": 0.8, "applied": 1},
                                        {"quality": 0.4, "applied": 0}])
    er = compute_kpis(db_path=tmp_path / "n.db", permission_db=tmp_path / "n.db",
                      reflections_db=refl)["edit_reflection"]
    assert er["available"] and er["count"] == 2 and er["applied"] == 1
    assert er["avg_quality"] == 0.6


def test_edit_reflection_empty_honest(tmp_path):
    refl = _mk_refl(tmp_path / "r.db", [])
    er = compute_kpis(db_path=tmp_path / "n.db", permission_db=tmp_path / "n.db",
                      reflections_db=refl)["edit_reflection"]
    assert er["available"] is False and er["count"] == 0 and er["note"]


# ── 4. compact 后质量 ─────────────────────────────────────────────


def test_compact_quality_post_vs_pre(tmp_path):
    # s1: compact 发生在第 2/3 回合之间；pre 2 回合（1 成功 1 失败）、post 2 回合全成功
    traj = _mk_traj(
        tmp_path / "t.db",
        [
            {"created_at": "2026-01-01T00:00:01"},
            {"created_at": "2026-01-01T00:00:02", "status": "error"},
            {"created_at": "2026-01-01T00:00:04"},
            {"created_at": "2026-01-01T00:00:05", "termination": "external_cancel"},
            {"created_at": "2026-01-01T00:00:06"},  # s2 不受影响
        ],
        extra_events=[
            {"sid": "s1", "metadata": {"kind": "compact", "before_count": 30,
                                        "after_count": 8},
             "created_at": "2026-01-01T00:00:03"},
        ],
    )
    # s2 的回合归 s2
    conn = sqlite3.connect(traj)
    conn.execute("UPDATE trajectory_events SET session_id='s2' WHERE id='e4'")
    conn.commit()
    conn.close()
    cq = compute_kpis(db_path=traj, permission_db=tmp_path / "n.db",
                      reflections_db=tmp_path / "n.db")["compact_quality"]
    assert cq["available"] and cq["compact_events"] == 1
    assert cq["post_compact_turns"] == 2 and cq["pre_compact_success_rate"] == 0.5
    assert cq["post_compact_success_rate"] == 0.5  # 1 ok + 1 external_cancel（非成功）
    assert cq["avg_before_count"] == 30.0 and cq["avg_after_count"] == 8.0


def test_compact_kind_must_match_not_string_contamination(tmp_path):
    # metadata 里含 "compact" 字样但 kind≠compact 的事件不得计入（LIKE 粗筛串味负例）
    traj = _mk_traj(
        tmp_path / "t.db",
        [{"created_at": "2026-01-01T00:00:01"}],
        extra_events=[
            {"metadata": {"kind": "termination_condition", "action": "compact_soft"},
             "created_at": "2026-01-01T00:00:02"},
        ],
    )
    cq = compute_kpis(db_path=traj, permission_db=tmp_path / "n.db",
                      reflections_db=tmp_path / "n.db")["compact_quality"]
    assert cq["available"] is False and cq["compact_events"] == 0


def test_compact_empty_honest(tmp_path):
    traj = _mk_traj(tmp_path / "t.db", [{"created_at": "2026-01-01T00:00:01"}])
    cq = compute_kpis(db_path=traj, permission_db=tmp_path / "n.db",
                      reflections_db=tmp_path / "n.db")["compact_quality"]
    assert cq["available"] is False and cq["note"] and cq["post_compact_success_rate"] is None


# ── 5. 窗口过滤 / 容噪 / 汇总 ─────────────────────────────────────


def test_since_window_filters_turns_and_compacts(tmp_path):
    traj = _mk_traj(
        tmp_path / "t.db",
        [
            {"created_at": "2026-01-01T00:00:01"},
            {"created_at": "2026-03-01T00:00:01", "status": "error"},
        ],
        extra_events=[
            {"metadata": {"kind": "compact", "before_count": 3, "after_count": 1},
             "created_at": "2026-01-01T00:00:05"},  # 窗口外
        ],
    )
    k = compute_kpis(db_path=traj, permission_db=tmp_path / "n.db",
                     reflections_db=tmp_path / "n.db", since="2026-02-01T00:00:00")
    assert k["task_success"]["closed_turns"] == 1  # 只剩 3 月的 error 回合
    assert k["compact_quality"]["compact_events"] == 0


def test_malformed_metadata_and_missing_db_tolerated(tmp_path):
    traj = _mk_traj(tmp_path / "t.db", [{"created_at": "2026-01-01T00:00:01"}])
    conn = sqlite3.connect(traj)
    conn.execute(
        "INSERT INTO trajectory_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("bad", "s1", None, "user_input", "soulmate", "", "{not-json", 0, 0.0,
         "ok", "2026-01-01T00:00:09", "2026-01-01T00:00:09"),
    )
    conn.commit()
    conn.close()
    k = compute_kpis(db_path=traj, permission_db=tmp_path / "missing.db",
                     reflections_db=tmp_path / "missing.db")
    assert k["task_success"]["closed_turns"] == 1  # 坏 JSON 行被精筛掉，不炸不冒充
    assert k["human_intervention"]["available"]  # 缺库=0 行不崩


def test_four_kpi_dict_shape_single_surface(tmp_path):
    k = compute_kpis(db_path=tmp_path / "missing.db", permission_db=tmp_path / "missing.db",
                     reflections_db=tmp_path / "missing.db")
    assert set(k) >= {"task_success", "human_intervention", "edit_reflection",
                      "compact_quality", "sources", "generated_at"}
    for key in ("task_success", "human_intervention", "edit_reflection", "compact_quality"):
        assert "source" in k[key] and "note" in k[key] and "available" in k[key]


# ── 6. 接线钉：soulmate 压缩点必须落 compact 事件 ─────────────────


def test_soulmate_compress_site_logs_compact_event():
    src = Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py"
    text = src.read_text(encoding="utf-8")
    # 压缩点调用 log_event(kind="compact") 且带 before/after 计数（KPI 数据源接线）
    assert 'kind="compact"' in text, "压缩点未落 compact 事件（KPI 数据源断线）"
    assert '"before_count": _before_count' in text and '"after_count": len(compressed)' in text
    # 事件必须发在 compress 成功分支内（避免失败也记账）
    idx_compress = text.index("compressed = self._ctx_compressor.compress(messages)")
    idx_log = text.index('kind="compact"', idx_compress)
    assert 0 < idx_log - idx_compress < 800


def test_app_exposes_bootstrap_kpi_endpoint():
    src = Path(__file__).resolve().parent.parent / "app.py"
    text = src.read_text(encoding="utf-8")
    assert '@app.get("/api/agent/bootstrap-kpi")' in text
    assert "compute_kpis" in text
