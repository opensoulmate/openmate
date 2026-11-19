"""`.agents/skills`标准层运行时接线测试（五方定案：goose/ChatDev2.0/FastGPT/OpenHands/Warp）

覆盖：标准层扫描（flat+category嵌套+dot目录跳过+fail-closed校验）、
list_skills合并去重（标准层优先）、search_skills标准层检索（英文+CJK-aware中文）、
std: id只读保护区（update/delete拒绝+record_usage不落盘）、
content preview截断显式标记、TTL缓存、soulmate注入gate策略is_injectable_trigger、
本地JSON技能CRUD回归（形状兼容）。
"""
import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from skill_manager import (  # noqa: E402
    STANDARD_SKILL_CONTENT_LIMIT,
    SkillManager,
    is_injectable_trigger,
)


def _write_skill(base: Path, rel: str, name: str, description: str, body: str, extra_fm: str = ""):
    d = base / rel
    d.mkdir(parents=True, exist_ok=True)
    fm = f"---\nname: {name}\ndescription: {description}\n{extra_fm}---\n\n"
    (d / "SKILL.md").write_text(fm + body, encoding="utf-8")
    return d


@pytest.fixture
def mgr(tmp_path):
    std_dir = tmp_path / "agents-standard"
    std_dir.mkdir()
    json_dir = tmp_path / "json-skills"
    m = SkillManager(skills_dir=str(json_dir), standard_dirs=[("agents-test", str(std_dir))])
    return m, std_dir, json_dir


# ── 扫描 ─────────────────────────────────────────────────────


class TestStandardScan:
    def test_scan_flat_standard_skill(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "mail-skill", "mail-skill", "操作邮件、管理收件箱", "## 用法\n发送邮件")
        skills = m.scan_standard_skills(force_refresh=True)
        assert len(skills) == 1
        s = skills[0]
        assert s["id"] == "std:agents-test:mail-skill"
        assert s["name"] == "mail-skill"
        assert s["source"] == "agents-test"
        assert s["standard"] == "agents"
        assert s["read_only"] is True
        assert "SKILL.md路径" in s["content"]
        assert "发送邮件" in s["content"]

    def test_scan_category_nested(self, mgr):
        """shared-skills/hermes同构：category/skill/SKILL.md两层结构可扫描"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "category-a/nested-skill", "nested-skill", "嵌套技能描述", "body")
        skills = m.scan_standard_skills(force_refresh=True)
        assert [s["name"] for s in skills] == ["nested-skill"]

    def test_scan_skips_invalid_missing_description(self, mgr):
        """fail-closed：缺description的条目不进技能面（agno typed-error语义）"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "good-skill", "good-skill", "有效描述", "body")
        d = std_dir / "bad-skill"
        d.mkdir()
        (d / "SKILL.md").write_text("---\nname: bad-skill\n---\n无描述", encoding="utf-8")
        skills = m.scan_standard_skills(force_refresh=True)
        assert [s["name"] for s in skills] == ["good-skill"]

    def test_scan_skips_missing_skill_md_and_dot_dirs(self, mgr):
        m, std_dir, _ = mgr
        (std_dir / "no-md-dir").mkdir()
        _write_skill(std_dir, ".staging-123/inner", "staging-skill", "staging", "body")
        _write_skill(std_dir, ".backup-x/inner2", "backup-skill", "backup", "body")
        skills = m.scan_standard_skills(force_refresh=True)
        assert skills == []

    def test_scan_unreadable_base_dir_skipped(self, tmp_path):
        m = SkillManager(
            skills_dir=str(tmp_path / "json"),
            standard_dirs=[("missing", str(tmp_path / "not-exist")), ("bad", "/root/no-access-xyz")],
        )
        skills = m.scan_standard_skills(force_refresh=True)
        assert isinstance(skills, list)  # 不抛异常，目录不可达安全跳过

    def test_scan_cache_ttl_and_force_refresh(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "first-skill", "first-skill", "第一", "body")
        assert len(m.scan_standard_skills(force_refresh=True)) == 1
        # 新增技能后：TTL内读缓存（看不到），force_refresh可见
        _write_skill(std_dir, "second-skill", "second-skill", "第二", "body")
        assert len(m.scan_standard_skills()) == 1
        assert len(m.scan_standard_skills(force_refresh=True)) == 2
        # 缓存过期后自动重扫
        m._std_cache_ts = time.monotonic() - 61
        assert len(m.scan_standard_skills()) == 2


# ── frontmatter解析与触发词 ──────────────────────────────────


class TestParseAndTriggers:
    def test_block_scalar_description(self, mgr):
        m, std_dir, _ = mgr
        d = std_dir / "block-skill"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: block-skill\ndescription: |\n  多行描述第一段\n  多行描述第二段\n---\nbody",
            encoding="utf-8",
        )
        skills = m.scan_standard_skills(force_refresh=True)
        assert skills[0]["description"].startswith("多行描述第一段")

    def test_frontmatter_triggers_parsed(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "trig-skill", "trig-skill", "描述", "body",
                     extra_fm="triggers: [发票, invoice]\n")
        s = m.scan_standard_skills(force_refresh=True)[0]
        assert "发票" in s["triggers"]
        assert "invoice" in s["triggers"]

    def test_derived_triggers_name_and_cjk(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "agently-mail", "agently-mail", "通过CLI操作邮件：管理收件箱、下载附件", "body")
        s = m.scan_standard_skills(force_refresh=True)[0]
        trig = s["triggers"]
        assert "agently" in trig and "mail" in trig  # name分段
        assert "收件箱" in trig  # 中文3字滑窗（可通过注入gate）
        assert "邮件" in trig  # 中文2字词参与检索打分
        assert len(trig) <= 40

    def test_content_truncation_explicit_marker(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "big-skill", "big-skill", "大技能", "x" * (STANDARD_SKILL_CONTENT_LIMIT + 500))
        s = m.scan_standard_skills(force_refresh=True)[0]
        assert "[TRUNCATED" in s["content"]
        assert str(STANDARD_SKILL_CONTENT_LIMIT) in s["content"]
        assert s["skill_md"] in s["content"]  # 完整内容可read_file读回

    def test_short_body_no_truncation_marker(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "small-skill", "small-skill", "小技能", "短正文")
        s = m.scan_standard_skills(force_refresh=True)[0]
        assert "[TRUNCATED" not in s["content"]
        assert "短正文" in s["content"]


# ── list_skills 合并去重 ─────────────────────────────────────


class TestListMerge:
    def test_standard_and_json_merged(self, mgr):
        m, std_dir, json_dir = mgr
        _write_skill(std_dir, "std-skill", "std-skill", "标准技能", "body")
        m.create_skill(name="json-skill", description="本地技能", triggers=["json"], content="c")
        names = {s["name"] for s in m.list_skills()}
        assert {"std-skill", "json-skill"} <= names

    def test_standard_wins_name_collision(self, mgr):
        """重名时标准层优先（五方定案目录=事实标准）"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "dup-skill", "dup-skill", "标准层版本", "std-body")
        m.create_skill(name="dup-skill", description="JSON层版本", triggers=["dup"], content="json-body")
        skills = m.list_skills()
        dups = [s for s in skills if s["name"] == "dup-skill"]
        assert len(dups) == 1
        assert dups[0]["standard"] == "agents"
        assert dups[0]["id"].startswith("std:")

    def test_cross_label_standard_dedupe(self, mgr, tmp_path):
        """标准层跨label同名去重：agents-global先于shared（目录顺序优先）"""
        std2 = tmp_path / "agents-shared-std"
        _write_skill(mgr[1], "dup-skill", "dup-skill", "全局层版本", "global-body")
        _write_skill(std2, "dup-skill", "dup-skill", "shared层版本", "shared-body")
        m = SkillManager(
            skills_dir=str(tmp_path / "json2"),
            standard_dirs=[("agents-global", str(mgr[1])), ("shared", str(std2))],
        )
        skills = [s for s in m.list_skills() if s["name"] == "dup-skill"]
        assert len(skills) == 1
        assert skills[0]["id"] == "std:agents-global:dup-skill"

    def test_sort_by_use_count(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "std-skill", "std-skill", "标准", "body")
        created = m.create_skill(name="hot-skill", description="热", triggers=["hot"], content="c")
        m.record_usage(created["id"])
        skills = m.list_skills()
        names = [s["name"] for s in skills]
        assert names.index("hot-skill") < names.index("std-skill")  # use_count降序

    def test_local_same_name_different_task_both_visible(self, mgr):
        """P0 二次命中缺口回归：auto-learned 同名（工具链）不同任务技能不可再被吞。

        按 name 吞掉 → 第二个任务学到的技能永远检索不到；按 (name, description)
        折叠：同名不同任务=两个都可见，同名同描述=重复学习复本折叠。
        """
        m, _, _ = mgr
        m.create_skill(name="terminal→read_file", description="从交互中自动学习: 报销发票",
                       triggers=["发票"], content="c1")
        m.create_skill(name="terminal→read_file", description="从交互中自动学习: 备份数据库",
                       triggers=["备份"], content="c2")
        names = [s["name"] for s in m.list_skills() if s["name"] == "terminal→read_file"]
        assert len(names) == 2

    def test_local_same_name_same_description_collapsed(self, mgr):
        """同名同描述=重复学习的复本，折叠只留一份。"""
        m, _, _ = mgr
        m.create_skill(name="n", description="d", triggers=["t"], content="c")
        m.create_skill(name="n", description="d", triggers=["t"], content="c")
        assert len([s for s in m.list_skills() if s["name"] == "n"]) == 1


# ── search_skills 标准层检索 ─────────────────────────────────


class TestStandardSearch:
    def test_search_by_english_name_token(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "browser-skill", "browser-skill", "浏览器自动化操作网页", "body")
        results = m.search_skills("browser automation", limit=5)
        assert any(s["name"] == "browser-skill" for s in results)

    def test_search_chinese_two_char_trigger(self, mgr):
        """中文2字词检索：修复前len>4门槛使'邮件'永远打不中触发词"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "mail-skill", "mail-skill", "通过CLI操作邮件：管理收件箱", "body")
        results = m.search_skills("帮我处理邮件", limit=5)
        assert any(s["name"] == "mail-skill" for s in results)

    def test_search_chinese_description_token(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "bid-skill", "bid-skill", "招标文件分析与投标文件编制", "body")
        results = m.search_skills("投标文件", limit=5)
        assert any(s["name"] == "bid-skill" for s in results)

    def test_generic_word_not_injectable_but_exact_still_scores(self, mgr):
        """通用2字词（操作/管理）不走子串+10路径；整句=触发词的精确匹配仍+15"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "generic-skill", "generic-skill", "一些描述", "body",
                     extra_fm="triggers: [操作]\n")
        # query含"操作"但不等于触发词 → 通用词不产生触发分；desc无命中 → 不返回
        assert m.search_skills("文件操作指南", limit=5) == []
        # 整个query精确等于触发词 → path1 +15 仍然生效
        results = m.search_skills("操作", limit=5)
        assert any(s["name"] == "generic-skill" for s in results)


# ── std: id 只读保护区 ───────────────────────────────────────


class TestStdReadOnly:
    def _std_skill(self, mgr):
        m, std_dir, _ = mgr
        _write_skill(std_dir, "ro-skill", "ro-skill", "只读技能", "body")
        return m, std_dir, m.scan_standard_skills(force_refresh=True)[0]

    def test_get_skill_std_id(self, mgr):
        m, _, s = self._std_skill(mgr)
        got = m.get_skill(s["id"])
        assert got is not None and got["name"] == "ro-skill"
        assert m.get_skill("std:agents-test:nonexistent") is None

    def test_update_std_refused(self, mgr):
        m, _, s = self._std_skill(mgr)
        assert m.update_skill(s["id"], name="hacked") is None
        assert m.get_skill(s["id"])["name"] == "ro-skill"  # 未被修改

    def test_delete_std_refused(self, mgr):
        m, std_dir, s = self._std_skill(mgr)
        assert m.delete_skill(s["id"]) is False
        assert (std_dir / "ro-skill" / "SKILL.md").exists()  # 目录原封不动

    def test_record_usage_std_noop_no_files(self, mgr):
        m, _, s = self._std_skill(mgr)
        m.record_usage(s["id"])
        json_files = list(Path(m.skills_dir).glob("*.json")) if os.path.exists(m.skills_dir) else []
        assert json_files == []  # 不在JSON层落状态文件
        assert m.get_skill(s["id"])["use_count"] == 0

    def test_delete_std_id_no_path_traversal_side_effect(self, mgr):
        """std: id走拒绝分支，绝不拼路径rmdir"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "victim", "victim", "受害者", "body")
        assert m.delete_skill("std:agents-test:../../victim") is False
        assert (std_dir / "victim" / "SKILL.md").exists()


# ── 注入gate策略 ─────────────────────────────────────────────


class TestInjectableTrigger:
    def test_english_len_gt2_pass(self):
        assert is_injectable_trigger("browser") is True
        assert is_injectable_trigger("mail") is True

    def test_english_short_fail(self):
        assert is_injectable_trigger("ab") is False
        assert is_injectable_trigger("db") is False

    def test_chinese_domain_word_pass(self):
        assert is_injectable_trigger("邮件") is True
        assert is_injectable_trigger("发票") is True
        assert is_injectable_trigger("收件箱") is True

    def test_chinese_generic_word_fail(self):
        assert is_injectable_trigger("操作") is False
        assert is_injectable_trigger("管理") is False
        assert is_injectable_trigger("使用") is False

    def test_empty_safe(self):
        assert is_injectable_trigger("") is False


# ── 注入路径模拟（soulmate gate形状兼容） ────────────────────


class TestSoulmateGateShape:
    def test_matched_skill_shape_for_injection(self, mgr):
        """soulmate_agent:638注入需要skill['name']+skill['content']，标准层形状兼容"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "mail-skill", "mail-skill", "通过CLI操作邮件：管理收件箱", "## 步骤\n1. 发送")
        raw = m.search_skills("请帮我搜索收件箱里的邮件", limit=3)
        user_text = "请帮我搜索收件箱里的邮件"
        matched = [s for s in raw if any(
            t.lower() in user_text.lower() and is_injectable_trigger(t)
            for t in s.get("triggers", [])
        )]
        assert any(s["name"] == "mail-skill" for s in matched)
        for s in matched:
            assert isinstance(s["name"], str) and s["name"]
            assert isinstance(s["content"], str) and s["content"]
            assert s.get("code_template") == ""  # 标准层无code_template → 注入分支跳过

    def test_record_usage_on_matched_std_id_safe(self, mgr):
        """soulmate_agent:2596对每个matched skill调record_usage——std id必须安全"""
        m, std_dir, _ = mgr
        _write_skill(std_dir, "mail-skill", "mail-skill", "管理收件箱", "body")
        matched = m.search_skills("收件箱", limit=3)
        for s in matched:
            m.record_usage(s["id"])  # 不抛异常、不落盘
        assert True


# ── 本地JSON层回归（既有契约不变） ───────────────────────────


class TestJsonRegression:
    def test_crud_roundtrip(self, mgr):
        m, _, _ = mgr
        created = m.create_skill(name="t", description="d", triggers=["发票真伪"], content="c")
        got = m.get_skill(created["id"])
        assert got["name"] == "t"
        m.update_skill(created["id"], description="d2")
        assert m.get_skill(created["id"])["description"] == "d2"
        m.record_usage(created["id"])
        assert m.get_skill(created["id"])["use_count"] == 1
        assert m.delete_skill(created["id"]) is True
        assert m.get_skill(created["id"]) is None

    def test_search_json_skill_chinese_trigger(self, mgr):
        """JSON层中文触发词同享CJK-aware检索"""
        m, _, _ = mgr
        m.create_skill(name="inv", description="发票处理", triggers=["发票"], content="c")
        results = m.search_skills("帮我查发票", limit=5)
        assert any(s["name"] == "inv" for s in results)

    def test_json_file_corrupt_skipped(self, mgr):
        m, _, json_dir = mgr
        m.create_skill(name="ok", description="d", triggers=["ok"], content="c")
        with open(os.path.join(json_dir, "bad.json"), "w") as fp:
            fp.write("{not valid json")
        names = [s["name"] for s in m.list_skills()]
        assert "ok" in names  # 坏文件跳过不影响其余
