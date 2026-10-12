"""20.2 反射产生源测试——三确定性时刻真写 SQLite reflections 行 + fail-safe 旁路
+ 接线钉（源码级断言三产生源在真实路径）+ bootstrap_kpi 消费面可观测对照。
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.reflection_engine import ReflectionEngine  # noqa: E402
from agent import reflection_sources  # noqa: E402
from agent.self_verify import VerifyItem, VerifyReport  # noqa: E402


def _rows(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT * FROM reflections ORDER BY created_at").fetchall()]


def _engine(tmp_path):
    return ReflectionEngine(db_path=str(tmp_path / "reflections.db"))


# ── 产生源①：自验收尾出结论 ──────────────────────────────────────

def test_self_verify_conclusion_writes_real_row(tmp_path):
    eng = _engine(tmp_path)
    report = VerifyReport(items=[
        VerifyItem(requirement="创建文件 dev.py", status="met", score=1.0),
        VerifyItem(requirement="运行并给出输出", status="unmet", score=0.0),
        VerifyItem(requirement="报告文件大小", status="uncertain", score=0.5),
    ], coverage=0.33)
    ok = reflection_sources.from_self_verify(
        eng, "sess-1", "1)创建文件 2)运行 3)报告大小", report, remediated=True)
    assert ok is True
    rows = _rows(eng.db_path)
    assert len(rows) == 1
    r = rows[0]
    assert r["session_id"] == "sess-1"
    assert abs(r["quality_score"] - 0.33) < 1e-6  # coverage 真值非硬造
    assert "remediated=True" in r["result_summary"]
    import json
    weaknesses = json.loads(r["weaknesses"])
    assert any("运行并给出输出" in w for w in weaknesses)
    assert any("报告文件大小" in w for w in weaknesses)  # uncertain 进待证
    patterns = json.loads(r["error_patterns"])
    assert any(p.startswith("self_verify_missing:") for p in patterns)


def test_self_verify_empty_items_no_row(tmp_path):
    eng = _engine(tmp_path)
    assert reflection_sources.from_self_verify(
        eng, "s", "hi", VerifyReport(items=[], coverage=1.0)) is False
    assert _rows(eng.db_path) == []


def test_self_verify_full_coverage_strengths_only(tmp_path):
    eng = _engine(tmp_path)
    report = VerifyReport(items=[
        VerifyItem(requirement="做A", status="met", score=1.0),
        VerifyItem(requirement="做B", status="met", score=1.0),
    ], coverage=1.0)
    assert reflection_sources.from_self_verify(eng, "s", "做A做B", report) is True
    r = _rows(eng.db_path)[0]
    import json
    assert json.loads(r["weaknesses"]) == []
    assert r["quality_score"] == 1.0


# ── 产生源②：补做自愈完成 ────────────────────────────────────────

def test_remediation_success_and_failure_two_states(tmp_path):
    eng = _engine(tmp_path)
    assert reflection_sources.from_remediation(
        eng, "s", "refusal-remedy", "web_search", True, user_text="查新闻") is True
    assert reflection_sources.from_remediation(
        eng, "s", "refusal-remedy", "web_search", False, user_text="查新闻") is True
    rows = _rows(eng.db_path)
    assert len(rows) == 2
    ok_row, fail_row = rows
    assert ok_row["quality_score"] == 0.8
    assert fail_row["quality_score"] == 0.3
    import json
    assert any("自愈成功" in s for s in json.loads(ok_row["strengths"]))
    assert any("仍未执行" in w for w in json.loads(fail_row["weaknesses"]))
    assert any(p.startswith("remediation_failed:") for p in json.loads(fail_row["error_patterns"]))


# ── 产生源③：工具失败恢复成功 ────────────────────────────────────

def test_tool_recovery_writes_row(tmp_path):
    eng = _engine(tmp_path)
    assert reflection_sources.from_tool_recovery(
        eng, "s", "read_file", attempt=1, action="换路径",
        kind="not_found", first_result="错误: 文件不存在") is True
    r = _rows(eng.db_path)[0]
    import json
    assert any("read_file 第1次换路径" in s for s in json.loads(r["strengths"]))
    assert any(p == "tool_failure:read_file:not_found" for p in json.loads(r["error_patterns"]))
    assert r["quality_score"] == 0.8


# ── fail-safe 旁路 ───────────────────────────────────────────────

class _Boom:
    def record_reflection(self, **kw):
        raise RuntimeError("db exploded")


def test_record_safely_never_raises():
    assert reflection_sources.record_safely(
        _Boom(), session_id="s", task_description="t", result_summary="r",
        quality_score=0.5) is False
    # 三个产生源在引擎爆炸时同样不冒泡
    assert reflection_sources.from_tool_recovery(
        _Boom(), "s", "x", 1, "重试", "err") is False
    assert reflection_sources.from_remediation(
        _Boom(), "s", "refusal-remedy", "x", True) is False
    assert reflection_sources.from_self_verify(
        _Boom(), "s", "t", VerifyReport(items=[
            VerifyItem("a", "met", 1.0), VerifyItem("b", "unmet", 0.0)],
            coverage=0.5)) is False


def test_record_safely_none_engine_lazy_init(monkeypatch, tmp_path):
    stub = _engine(tmp_path)
    import agent.reflection_sources as rs
    monkeypatch.setattr(rs, "__name__", rs.__name__)  # no-op 保持模块态
    # engine=None → lazy-init ReflectionEngine（打桩到临时库，不碰真实库）
    import agent.reflection_engine as re_mod
    monkeypatch.setattr(re_mod, "ReflectionEngine", lambda *a, **k: stub)
    assert reflection_sources.record_safely(
        None, session_id="s", task_description="t", result_summary="r",
        quality_score=0.5) is True
    assert len(_rows(stub.db_path)) == 1


# ── 接线钉（源码级：三产生源在真实调用路径，写了≠接线了）──────────

def _func_body(src: str, header: str) -> str:
    i = src.index(header)
    j = src.find("\n    def ", i + 1)
    j = src.find("\n    async def ", i + 1) if j == -1 else j
    return src[i:j if j != -1 else len(src)]


def test_wiring_three_sources_in_soulmate_agent():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text()
    assert "from agent import reflection_sources" in src
    # ① 自验结论：在 _prompt_inner 的结论块内
    assert "reflection_sources.from_self_verify(" in src
    # ② 拒做补做收口：成功/未自愈两态都接
    assert src.count("reflection_sources.from_remediation(") == 2
    # ③ 工具失败恢复：在 _tool_recovery_execute 函数体内（recovered 分支）
    body = _func_body(src, "async def _tool_recovery_execute")
    assert "reflection_sources.from_tool_recovery(" in body
    assert "outcome=\"recovered\"" in body


def test_bootstrap_kpi_consumes_source_rows(tmp_path):
    """消费面对照：产生源写入行后 bootstrap_kpi 反射 KPI 可观测（不再恒 available=False）。"""
    from agent.bootstrap_kpi import compute_kpis
    eng = _engine(tmp_path)
    reflection_sources.from_tool_recovery(eng, "s", "x", 1, "重试", "err")
    reflection_sources.from_remediation(eng, "s", "refusal-remedy", "y", False)
    k = compute_kpis(db_path=tmp_path / "none.db",
                     permission_db=tmp_path / "none.db",
                     reflections_db=eng.db_path)["edit_reflection"]
    assert k["available"] is True
    assert k["count"] == 2
    assert k["avg_quality"] is not None
    assert k["note"] == ""
