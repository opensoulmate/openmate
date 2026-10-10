"""路线图 6.1 技能保存：把成功流程写成标准 SKILL.md（触发条件/步骤/坑）

覆盖：渲染格式（frontmatter+固定分区）/校验拒绝/upsert去重与revision/原子写/
CJK slug/skill_manager saved层检索命中/单写入者守卫/soulmate_agent四点接线+
真实dispatch烟测。
"""

import os
from pathlib import Path

import pytest

from skill_manager import SkillManager
from agent.skill_saver import SkillSaver, slugify


def make_saver(tmp_path) -> SkillSaver:
    return SkillSaver(library_dir=str(tmp_path / "lib"))


FLOW = dict(
    name="发票OCR识别入库",
    description="扫描件发票OCR识别后分类入库报销",
    triggers=["发票", "报销", "OCR"],
    steps=["读取扫描件PDF", "ocrmypdf识别文本", "按类别入库并回写金额"],
    pitfalls=["扫描件PDF无文本层，直接read_file得空", "必须ocrmypdf+vision兜底"],
    verify="sqlite查入库行且金额合计与发票一致",
    category="finance",
)


# ── 渲染与落盘 ─────────────────────────────────────────────────


def test_render_standard_layout():
    md = SkillSaver.render_skill_md(
        name="X", description="D", triggers=["a", "b"], steps=["s1", "s2"],
        pitfalls=["p1"], verify="v1",
    )
    # frontmatter
    assert md.startswith("---\n")
    assert "name: X" in md
    assert "description: D" in md
    assert "triggers: a, b" in md
    # 固定分区（路线图6.1：触发条件/步骤/坑）
    assert "## 触发条件" in md
    assert "## 步骤" in md
    assert "## 坑" in md
    assert "## 验证" in md
    assert "1. s1" in md and "2. s2" in md
    assert "- p1" in md


def test_save_and_parse_roundtrip(tmp_path):
    saver = make_saver(tmp_path)
    result = saver.save_flow(**FLOW)
    assert result["created"] is True and result["revision"] == 1
    md_path = Path(result["skill_md"])
    assert md_path.exists()
    assert md_path.read_text(encoding="utf-8").endswith("\n")

    meta, body = SkillManager._parse_standard_skill_md(md_path)
    assert meta["name"] == "发票OCR识别入库"
    assert meta["description"] == "扫描件发票OCR识别后分类入库报销"
    trig = [t.strip() for t in meta["triggers"].split(",")]
    assert trig == ["发票", "报销", "ocr"]
    assert "## 触发条件" in body and "## 步骤" in body and "## 坑" in body
    assert "ocrmypdf识别文本" in body
    assert "直接read_file得空" in body


def test_no_tmp_leftover_and_load_skill(tmp_path):
    saver = make_saver(tmp_path)
    result = saver.save_flow(**FLOW)
    skill_dir = Path(result["skill_md"]).parent
    assert list(skill_dir.glob("*.tmp*")) == []  # 原子写不留半截文件
    loaded = saver.load_skill(result["slug"])
    assert loaded is not None and loaded["revision"] == 1
    assert loaded["name"] == FLOW["name"]
    assert "步骤" in loaded["body"]


def test_list_saved_sorted_roundtrip(tmp_path):
    saver = make_saver(tmp_path)
    saver.save_flow(**FLOW)
    saver.save_flow(
        name="部署检查", description="部署后健康检查", triggers=["部署"],
        steps=["curl health"], category="ops",
    )
    slugs = [s["slug"] for s in saver.list_saved()]
    assert slugs == sorted(slugs)
    assert len(slugs) == 2


# ── 校验拒绝（显式报因，不静默吞） ─────────────────────────────


@pytest.mark.parametrize("mutate,frag", [
    ({"name": ""}, "name"),
    ({"name": "   "}, "name"),
    ({"description": ""}, "description"),
    ({"triggers": []}, "triggers"),
    ({"triggers": ["", "  "]}, "triggers"),
    ({"steps": []}, "steps"),
    ({"steps": ["", None]}, "steps"),
])
def test_validation_errors(tmp_path, mutate, frag):
    saver = make_saver(tmp_path)
    kwargs = dict(FLOW)
    kwargs.update(mutate)
    with pytest.raises(ValueError) as ei:
        saver.save_flow(**kwargs)
    assert frag in str(ei.value)
    assert saver.list_saved() == []  # 校验失败零落盘


# ── upsert：禁静默覆盖 + revision 可审计 ────────────────────────


def test_dupe_guard_no_overwrite(tmp_path):
    saver = make_saver(tmp_path)
    saver.save_flow(**FLOW)
    second = dict(FLOW)
    second["steps"] = ["被悄悄换掉的步骤"]
    result = saver.save_flow(**second)  # overwrite=False 缺省
    assert result["exists"] is True
    assert result["created"] is False and result["updated"] is False
    assert result["revision"] == 1
    loaded = saver.load_skill(result["slug"])
    assert "被悄悄换掉的步骤" not in loaded["body"]  # 既有内容原样保留
    assert "ocrmypdf识别文本" in loaded["body"]


def test_overwrite_increments_revision(tmp_path):
    saver = make_saver(tmp_path)
    saver.save_flow(**FLOW)
    second = dict(FLOW)
    second["steps"] = ["新步骤一", "新步骤二"]
    result = saver.save_flow(**second, overwrite=True)
    assert result["updated"] is True and result["revision"] == 2
    loaded = saver.load_skill(result["slug"])
    assert loaded["revision"] == 2
    assert "新步骤一" in loaded["body"] and "ocrmypdf识别文本" not in loaded["body"]


def test_cjk_slug_stable():
    s1 = slugify("发票 OCR 识别/入库")
    s2 = slugify("发票 OCR 识别/入库")
    assert s1 == s2
    assert " " not in s1 and "/" not in s1
    assert "发票" in s1  # CJK 保留
    assert slugify("   ") .startswith("skill-")  # 空名回退 hash（确定性）


def test_multiline_description_survives_frontmatter(tmp_path):
    saver = make_saver(tmp_path)
    kwargs = dict(FLOW)
    kwargs["description"] = "第一行: 带冒号\n第二行换行的描述"
    result = saver.save_flow(**kwargs)
    meta, _ = SkillManager._parse_standard_skill_md(Path(result["skill_md"]))
    assert meta["description"] == "第一行: 带冒号 第二行换行的描述"


def test_pitfalls_optional_section(tmp_path):
    saver = make_saver(tmp_path)
    kwargs = dict(FLOW)
    kwargs["pitfalls"] = []
    result = saver.save_flow(**kwargs)
    body = saver.load_skill(result["slug"])["body"]
    assert "## 坑" not in body
    assert "## 步骤" in body


# ── skill_manager saved 层：保存即被检索命中 ────────────────────


def make_manager(tmp_path, lib_dir) -> SkillManager:
    return SkillManager(
        skills_dir=str(tmp_path / "json-skills"),
        standard_dirs=[],  # 隔离标准层，测试只看 saved 层
        saved_dir=str(lib_dir),
    )


def test_saved_skill_searchable(tmp_path):
    saver = make_saver(tmp_path)
    saver.save_flow(**FLOW)
    mgr = make_manager(tmp_path, saver.library_dir)
    hits = mgr.search_skills("帮我做发票报销", limit=5)
    assert hits, "保存的技能必须能被 search_skills 命中"
    hit = next(h for h in hits if h["id"].startswith("saved:"))
    assert hit["id"] == f"saved:{slugify(FLOW['name'])}"
    assert hit["read_only"] is False
    assert hit["skill_md"].endswith("SKILL.md")
    assert hit["revision"] == 1
    assert "[已保存技能]" in hit["content"]


def test_saved_layer_cache_and_force_refresh(tmp_path):
    saver = make_saver(tmp_path)
    mgr = make_manager(tmp_path, saver.library_dir)
    assert mgr.scan_saved_skills() == []
    saver.save_flow(**FLOW)
    assert mgr.scan_saved_skills() == []  # 60s TTL 缓存仍返回旧快照
    fresh = mgr.scan_saved_skills(force_refresh=True)
    assert len(fresh) == 1  # 强刷可见（skill_save dispatch 保存后即刷）
    assert mgr.get_skill(fresh[0]["id"]) is not None


def test_saved_id_write_guards_single_writer(tmp_path):
    saver = make_saver(tmp_path)
    result = saver.save_flow(**FLOW)
    mgr = make_manager(tmp_path, saver.library_dir)
    sid = f"saved:{result['slug']}"
    assert mgr.update_skill(sid, name="篡改") is None  # 单写入者：SkillManager不写saved库
    assert mgr.delete_skill(sid) is False
    assert Path(result["skill_md"]).exists()  # 文件原样在
    mgr.record_usage(sid)  # 不记账但不炸
    assert saver.load_skill(result["slug"])["name"] == FLOW["name"]


# ── soulmate_agent 四点接线 + 真实dispatch烟测 ─────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "skill_save"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "skill_save"') >= 2
    # 分发helper + lazy-init + 顶部import
    assert "def _skill_save_dispatch" in src
    assert "_get_skill_saver" in src
    assert "from agent.skill_saver import" in src
    # 系统提示工具清单
    assert "skill_save: 技能保存" in src


def test_skill_save_dispatch_smoke(tmp_path, monkeypatch):
    """__new__构造+lazy-init真实dispatch烟测：保存/去重守卫/覆盖/校验错误四态。"""
    import agent.soulmate_agent as sa

    saver = make_saver(tmp_path)
    monkeypatch.setattr(sa, "_get_skill_saver", lambda: saver)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = agent._skill_save_dispatch({
        "name": FLOW["name"], "description": FLOW["description"],
        "triggers": FLOW["triggers"], "steps": FLOW["steps"],
        "pitfalls": FLOW["pitfalls"], "verify": FLOW["verify"],
    })
    assert "已保存技能" in out and "SKILL.md" in out
    assert (Path(saver.library_dir) / slugify(FLOW["name"]) / "SKILL.md").exists()

    # 去重守卫：不覆盖 + 显式提示
    out2 = agent._skill_save_dispatch({
        "name": FLOW["name"], "description": FLOW["description"],
        "triggers": FLOW["triggers"], "steps": ["x"],
    })
    assert "已存在同名技能" in out2 and "overwrite=true" in out2

    # 覆盖更新
    out3 = agent._skill_save_dispatch({
        "name": FLOW["name"], "description": FLOW["description"],
        "triggers": FLOW["triggers"], "steps": ["x"], "overwrite": True,
    })
    assert "已更新技能" in out3 and "revision 2" in out3

    # 校验错误显式报因
    out4 = agent._skill_save_dispatch({"name": "", "description": "d",
                                      "triggers": ["t"], "steps": ["s"]})
    assert out4.startswith("错误:")


def test_dispatch_refreshes_saved_cache(tmp_path, monkeypatch):
    """保存后强刷 saved 层缓存——保存即被检索命中（60s TTL不藏新技能）。"""
    import agent.soulmate_agent as sa

    saver = make_saver(tmp_path)
    mgr = make_manager(tmp_path, saver.library_dir)
    monkeypatch.setattr(sa, "_get_skill_saver", lambda: saver)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    agent._skill_manager = mgr

    assert mgr.scan_saved_skills() == []  # 预热缓存
    agent._skill_save_dispatch({
        "name": FLOW["name"], "description": FLOW["description"],
        "triggers": FLOW["triggers"], "steps": FLOW["steps"],
    })
    assert len(mgr.scan_saved_skills()) == 1  # dispatch 已 force_refresh，无需再刷
