"""路线图 6.3 技能进化：用执行结果反哺修正技能内容

覆盖：事件缓冲（note/drain/隔离/上限）/反馈落库去重与统计/pending队列/
apply合并（保字段+revision递增+原子替换）/去重不产生空revision/单次条数
上限/24h冷却/技能不存在与slug错配拒绝/resolve_slug/渲染模板两态/
soulmate_agent接线护栏+_skill_evolve_dispatch真实烟测+_skill_feedback_flush
归因烟测（含无命中技能drain不残留）。
"""

from pathlib import Path

import pytest

from agent.skill_evolver import (
    SkillEvolver,
    MAX_EVENTS_PER_SESSION,
    _parse_sections,
)
from agent.skill_saver import SkillSaver, slugify


FLOW = dict(
    name="发票OCR识别入库",
    description="扫描件发票OCR识别后分类入库报销",
    triggers=["发票", "报销", "OCR"],
    steps=["读取扫描件PDF", "ocrmypdf识别文本", "按类别入库并回写金额"],
    pitfalls=["扫描件PDF无文本层，直接read_file得空"],
    verify="sqlite查入库行且金额合计与发票一致",
    category="finance",
)


def make_evolver(tmp_path) -> SkillEvolver:
    return SkillEvolver(
        db_path=str(tmp_path / "feedback.db"),
        library_dir=str(tmp_path / "lib"),
    )


def make_skill(evolver: SkillEvolver, **overrides) -> str:
    args = dict(FLOW)
    args.update(overrides)
    res = evolver._saver.save_flow(**args)
    return res["slug"]


# ── 渲染模板 ─────────────────────────────────────────────────


def test_render_amendment_two_templates():
    assert "曾被遗漏" in SkillEvolver.render_amendment("self_verify", "unmet", "导出CSV")
    line = SkillEvolver.render_amendment("read_file", "file_not_found", "no such file")
    assert "read_file" in line and "file_not_found" in line and "no such file" in line


# ── 事件缓冲 ─────────────────────────────────────────────────


def test_note_drain_session_isolation(tmp_path):
    ev = make_evolver(tmp_path)
    ev.note_event("s1", "read_file", "file_not_found", "err a")
    ev.note_event("s2", "terminal", "timeout", "err b")
    assert [e["tool"] for e in ev.drain_events("s1")] == ["read_file"]
    assert [e["tool"] for e in ev.drain_events("s2")] == ["terminal"]
    assert ev.drain_events("s1") == []  # drain 清空


def test_note_event_skips_blank_and_caps(tmp_path):
    ev = make_evolver(tmp_path)
    ev.note_event("s", "read_file", "k", "   ")  # 空 detail 不计
    ev.note_event("s", "", "k", "d")  # 空 tool 不计
    for i in range(MAX_EVENTS_PER_SESSION + 5):
        ev.note_event("s", "t", "k", f"d{i}")
    events = ev.drain_events("s")
    assert len(events) == MAX_EVENTS_PER_SESSION
    assert events[-1]["detail"] == f"d{MAX_EVENTS_PER_SESSION + 4}"  # 留最新


# ── 反馈落库与查询 ───────────────────────────────────────────


def test_record_outcome_dedupe_and_stats(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    r1 = ev.record_outcome(slug, success=False, events=[
        {"tool": "read_file", "kind": "file_not_found", "detail": "no such file"},
        {"tool": "read_file", "kind": "file_not_found", "detail": "no such file"},
    ])
    assert r1["recorded"] == 1 and r1["deduped"] == 1
    r2 = ev.record_outcome(slug, success=True, events=[
        {"tool": "read_file", "kind": "file_not_found", "detail": "no such file"},
    ])
    assert r2["recorded"] == 0 and r2["deduped"] == 1
    st = ev.stats(slug)[slug]
    assert st == {"feedback": 1, "applied": 0, "pending": 1, "times_seen": 3}


def test_record_outcome_empty_events_not_recorded(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    r = ev.record_outcome(slug, success=True, events=[])
    assert r["recorded"] == 0
    assert ev.stats() == {}
    with pytest.raises(ValueError):
        ev.record_outcome("", success=True, events=[{"tool": "t", "kind": "k", "detail": "d"}])


def test_pending_amendments_text(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    ev.record_outcome(slug, success=False, events=[
        {"tool": "terminal", "kind": "timeout", "detail": "cmd hung"},
        {"tool": "self_verify", "kind": "unmet", "detail": "导出CSV"},
    ])
    texts = ev.pending_amendments(slug)
    assert len(texts) == 2
    assert any("terminal" in t for t in texts)
    assert any("曾被遗漏" in t and "导出CSV" in t for t in texts)


# ── apply 合并 ───────────────────────────────────────────────


def test_apply_merges_pitfalls_preserves_fields(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    ev.record_outcome(slug, success=False, events=[
        {"tool": "read_file", "kind": "file_not_found", "detail": "no such file"},
    ])
    res = ev.apply(slug)
    assert res["updated"] is True and res["revision"] == 2 and len(res["added"]) == 1

    skill = ev._saver.load_skill(slug)
    assert skill["revision"] == 2
    assert skill["name"] == FLOW["name"]
    assert skill["description"] == FLOW["description"]
    secs = _parse_sections(skill["body"])
    assert secs["triggers"] == [t.lower() for t in FLOW["triggers"]]
    assert secs["steps"] == FLOW["steps"]
    assert secs["verify"] == FLOW["verify"]
    assert secs["pitfalls"][0] == FLOW["pitfalls"][0]  # 旧坑保留
    assert any("read_file" in p for p in secs["pitfalls"][1:])  # 新坑并入
    # apply 后 pending 清空
    assert ev.pending_rows(slug) == []
    # 二次 apply = no_pending
    assert ev.apply(slug)["reason"] == "no_pending"


def test_apply_dedupe_no_empty_revision(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    # 与既有坑互为子集 → 去重，不改文件
    ev.record_outcome(slug, success=False, events=[
        {"tool": "read_file", "kind": "file_not_found",
         "detail": FLOW["pitfalls"][0]},  # 模板文本含既有坑为子集
    ])
    md_before = (Path(ev.library_dir) / slug / "SKILL.md").read_text(encoding="utf-8")
    res = ev.apply(slug)
    assert res["updated"] is False and res["reason"] == "all_duplicated"
    md_after = (Path(ev.library_dir) / slug / "SKILL.md").read_text(encoding="utf-8")
    assert md_after == md_before  # 零改动（不产生空 revision）
    assert ev.pending_rows(slug) == []  # 重复项已消化不再排队


def test_apply_max_items_cap(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    ev.record_outcome(slug, success=False, events=[
        {"tool": f"tool{i}", "kind": "err", "detail": f"detail {i}"} for i in range(5)
    ])
    res = ev.apply(slug, max_items=2)
    assert res["updated"] is True and len(res["added"]) == 2
    assert len(ev.pending_rows(slug)) == 3  # 剩余继续排队


def test_apply_cooldown_blocks_revision_churn(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    ev.record_outcome(slug, success=False, events=[
        {"tool": f"tool{i}", "kind": "err", "detail": f"detail {i}"} for i in range(6)
    ])
    for _ in range(5):
        r = ev.apply(slug, max_items=1)
        assert r["updated"] is True
    r6 = ev.apply(slug, max_items=1)
    assert r6["updated"] is False and r6["reason"] == "cooldown"
    assert r6["applied_24h"] == 5 and r6["limit_24h"] == 5
    assert len(ev.pending_rows(slug)) == 1  # 冷却期不吞反馈


def test_apply_rejects_unknown_and_slug_mismatch(tmp_path):
    ev = make_evolver(tmp_path)
    with pytest.raises(ValueError):
        ev.apply("no-such-skill")
    # name→slug 与目录不一致 → 拒绝写错目录
    slug = make_skill(ev)
    bad_md = Path(ev.library_dir) / slug / "SKILL.md"
    text = bad_md.read_text(encoding="utf-8").replace(
        f"name: {FLOW['name']}", "name: 另一个名字")
    bad_md.write_text(text, encoding="utf-8")
    ev.record_outcome(slug, success=False, events=[
        {"tool": "t", "kind": "k", "detail": "d"},
    ])
    with pytest.raises(ValueError):
        ev.apply(slug)


def test_apply_rejects_unparsable_body(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    bad_md = Path(ev.library_dir) / slug / "SKILL.md"
    bad_md.write_text("---\nname: x\n---\n\n# x\n\n无分区内容\n", encoding="utf-8")
    ev.record_outcome(slug, success=False, events=[
        {"tool": "t", "kind": "k", "detail": "d"},
    ])
    with pytest.raises(ValueError):
        ev.apply(slug)


def test_resolve_slug_accepts_name(tmp_path):
    ev = make_evolver(tmp_path)
    slug = make_skill(ev)
    assert ev.resolve_slug(FLOW["name"]) == slug
    assert ev.resolve_slug(slug) == slug
    assert ev.resolve_slug("不存在的技能") == "不存在的技能"


# ── soulmate_agent 接线护栏 + 真实烟测 ───────────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "skill_evolve"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "skill_evolve"') >= 2
    # 分发helper + 反哺收口 + lazy-init + 顶部import
    assert "def _skill_evolve_dispatch" in src
    assert "def _skill_feedback_flush" in src
    assert "_get_skill_evolver" in src
    assert "from agent.skill_evolver import" in src
    # 系统提示工具清单
    assert "skill_evolve: 技能进化" in src
    # 反哺源①工具失败采集 + 回合收口调用
    assert "note_event(" in src
    assert "_skill_feedback_flush(session_id, matched_skills, verify_report)" in src


def test_skill_evolve_dispatch_smoke(tmp_path, monkeypatch):
    """__new__构造+lazy-init真实dispatch烟测：pending/apply/stats/错误四态。"""
    import agent.soulmate_agent as sa

    ev = make_evolver(tmp_path)
    monkeypatch.setattr(sa, "_get_skill_evolver", lambda: ev)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__
    slug = make_skill(ev)

    # 错误态：未知 action / 空 slug
    assert agent._skill_evolve_dispatch({"action": "bogus", "slug": slug}).startswith("错误:")
    assert agent._skill_evolve_dispatch({"action": "pending", "slug": ""}).startswith("错误:")

    # 空 pending 显式说明
    out = agent._skill_evolve_dispatch({"action": "pending", "slug": slug})
    assert "暂无待应用修正" in out

    # 记反馈 → pending 可见（支持技能名解析 slug）
    ev.record_outcome(slug, success=False, events=[
        {"tool": "terminal", "kind": "timeout", "detail": "cmd hung"},
    ])
    out = agent._skill_evolve_dispatch({"action": "pending", "slug": FLOW["name"]})
    assert "待应用修正 1 条" in out and "terminal" in out and "命中1次" in out

    # apply → revision 2 + 新坑条目 + SKILL.md 路径
    out = agent._skill_evolve_dispatch({"action": "apply", "slug": slug})
    assert "已进化技能" in out and "revision 2" in out and "SKILL.md" in out

    # stats 形状
    out = agent._skill_evolve_dispatch({"action": "stats"})
    assert "技能反馈统计" in out and slug in out and "已应用1" in out

    # 不存在技能 → 错误
    out = agent._skill_evolve_dispatch({"action": "apply", "slug": "no-such"})
    assert out.startswith("错误:")


def test_skill_feedback_flush_attribution(tmp_path, monkeypatch):
    """回合收尾反哺归因：事件+自验遗漏 → 只落命中的 saved 技能；无命中drain不残留。"""
    import agent.soulmate_agent as sa

    ev = make_evolver(tmp_path)
    monkeypatch.setattr(sa, "_get_skill_evolver", lambda: ev)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    slug = make_skill(ev)

    class FakeReport:
        def missing_requirements(self):
            return ["导出CSV"]

    ev.note_event("sess-1", "read_file", "file_not_found", "no such file")
    agent._skill_feedback_flush(
        "sess-1",
        [{"id": f"saved:{slug}", "name": FLOW["name"]},
         {"id": "std:other", "name": "只读标准技能"}],  # std 条目不反哺
        FakeReport(),
    )
    assert ev.drain_events("sess-1") == []  # 已 drain
    rows = ev.pending_rows(slug)
    assert len(rows) == 2  # 工具失败 + unmet 各一
    kinds = {r["kind"] for r in rows}
    assert kinds == {"file_not_found", "unmet"}
    assert ev.stats("other") == {}  # std 前缀技能不落库

    # 无命中 saved 技能：事件照样 drain，不残留归因到下回合
    ev.note_event("sess-2", "terminal", "timeout", "cmd hung")
    agent._skill_feedback_flush("sess-2", [{"id": "std:other", "name": "x"}], None)
    assert ev.drain_events("sess-2") == []

    # 无事件无 unmet → 不落库
    agent._skill_feedback_flush(
        "sess-3", [{"id": f"saved:{slug}", "name": FLOW["name"]}], None)
    assert len(ev.pending_rows(slug)) == 2


def test_tool_recovery_notes_failure_events(tmp_path, monkeypatch):
    """反哺源①接线：_tool_recovery_execute 失败路径记事件，BLOCKED 不记。"""
    import asyncio
    import agent.soulmate_agent as sa

    ev = make_evolver(tmp_path)
    monkeypatch.setattr(sa, "_get_skill_evolver", lambda: ev)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)

    async def fake_tool_call(session_id, tool_name, func_args, cwd):
        return "错误: 文件不存在或为空: /nope"

    agent._code_mode_tool_call = fake_tool_call

    # 失败（RECOVERABLE）→ 记事件（恢复走 fake 执行器仍失败，走 no_plan/exhausted）
    out = asyncio.run(agent._tool_recovery_execute(
        "sess-t", "read_file", {"path": "/nope"}, "/tmp", "错误: 文件不存在或为空: /nope"))
    assert "文件不存在" in out
    events = ev.drain_events("sess-t")
    assert len(events) == 1 and events[0]["tool"] == "read_file"
    assert events[0]["kind"] == "recoverable"

    # BLOCKED → 不记（权限拦截不是技能内容问题）
    asyncio.run(agent._tool_recovery_execute(
        "sess-t", "terminal", {"command": "rm -rf /"}, "/tmp", "[被拦截] 权限引擎拦截"))
    assert ev.drain_events("sess-t") == []
