"""6.2 技能检索与加载（按任务自动匹配并注入）测试。

覆盖：检索gate语义保留（短消息/文件请求/触发词命中/纯描述不命中）、步骤级
重新匹配+合并去重、注入段字节契约（token归因锁定）、预算截断自报、gene推荐
载荷内容加载（描述/模板/成功率/相关性，字段容缺）、接线护栏、__new__烟测。
"""

import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skill_loader import (  # noqa: E402
    DEFAULT_BUDGET_TOKENS,
    SkillLoader,
    _TRUNCATED_MARK,
)


def fake_manager(skills_by_query=None, all_skills=None):
    """Fake SkillManager：search_skills 按query返回预置列表。"""
    skills_by_query = skills_by_query or {}
    return SimpleNamespace(
        search_skills=lambda q, limit=5: (skills_by_query.get(q) or all_skills or [])[:limit]
    )


SKILL_PDF = {
    "id": "s-pdf", "name": "pdf-skill", "content": "用read_file解析PDF",
    "triggers": ["pdf", "PDF解析"], "code_template": "read_file(path)",
}
SKILL_XLSX = {
    "id": "s-xlsx", "name": "excel-skill", "content": "表格处理流程",
    "triggers": ["excel", "表格"], "code_template": "",
}


# ── 检索：gate 语义（与历史内联实现逐条一致） ──────────────────

class TestMatchForTask:
    def test_short_message_no_match(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[SKILL_PDF]))
        assert loader.match_for_task("你好") == []
        assert loader.match_for_task("帮我pdf") == []  # ≤10字

    def test_file_request_skipped(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[SKILL_PDF]))
        assert loader.match_for_task("把report.pdf发给我，谢谢配合") == []

    def test_trigger_match_with_reason(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[SKILL_PDF]))
        hits = loader.match_for_task("请帮我解析这份PDF文档的内容并总结要点")
        assert [h["name"] for h in hits] == ["pdf-skill"]
        assert hits[0]["match_reason"].startswith("trigger:")
        # 原技能dict不被污染（match_reason在副本上）
        assert "match_reason" not in SKILL_PDF

    def test_description_only_not_matched(self):
        """纯描述匹配忽略（pitfall 30 防误触发语义保留）。"""
        skill = {"id": "s1", "name": "n1", "content": "c",
                 "triggers": ["门禁系统运维"], "description": "文档总结要点"}
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[skill]))
        # query命中description但触发词不在文本→不注入
        assert loader.match_for_task("请帮我总结这份文档的要点内容") == []

    def test_generic_cjk_trigger_not_injectable(self):
        """通用词触发词（如"使用"）不参与注入gate。"""
        skill = {"id": "s1", "name": "n1", "content": "c", "triggers": ["使用"]}
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[skill]))
        assert loader.match_for_task("请使用这个功能帮我处理一下这个问题") == []

    def test_no_hit_empty(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[SKILL_PDF]))
        assert loader.match_for_task("今天天气怎么样，帮我查一下北京的天气情况") == []


class TestMatchForStep:
    def test_step_hits_first_then_base_dedup_cap(self):
        step_skill = {"id": "s-xlsx", "name": "excel-skill", "content": "x", "triggers": ["excel"]}
        loader = SkillLoader(
            skill_manager=fake_manager(all_skills=[step_skill]), max_skills=3,
        )
        base = [dict(SKILL_PDF), dict(SKILL_XLSX)]  # xlsx重复
        merged = loader.match_for_step("用excel整理数据表", base)
        names = [m["name"] for m in merged]
        # 步骤命中在前，base随后，同名去重只留一份
        assert names == ["excel-skill", "pdf-skill"]

    def test_step_no_length_gate(self):
        """步骤文本天然短（"解析PDF"6字）也要能命中——不受回合级≤10字门槛限制。"""
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[SKILL_PDF]))
        merged = loader.match_for_step("解析PDF", [])
        assert [m["name"] for m in merged] == ["pdf-skill"]

    def test_step_file_request_skipped(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[SKILL_PDF]))
        assert loader.match_for_step("把report.pdf发给我", []) == []

    def test_step_merge_keeps_base_when_no_step_hit(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[]))
        merged = loader.match_for_step("无关步骤", [dict(SKILL_PDF)])
        assert [m["name"] for m in merged] == ["pdf-skill"]

    def test_step_merge_caps_max_skills(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[]), max_skills=2)
        base = [{"id": f"s{i}", "name": f"n{i}", "triggers": []} for i in range(5)]
        assert len(loader.match_for_step("任意步骤", base)) == 2


# ── 加载：本地技能注入段（字节契约 + 预算） ───────────────────

class TestRenderLocalSection:
    def test_byte_contract_with_template(self):
        """token归因锁定契约：单技能体文本逐字节一致。"""
        loader = SkillLoader(skill_manager=fake_manager())
        section, bodies = loader.render_local_section([SKILL_PDF])
        expected_body = "\n### pdf-skill\n用read_file解析PDF\n```\nread_file(path)\n```\n"
        assert bodies == [("pdf-skill", expected_body)]
        assert section == "\n\n## 相关技能（参考以下经验执行任务）\n" + expected_body

    def test_byte_contract_no_template(self):
        loader = SkillLoader(skill_manager=fake_manager())
        section, bodies = loader.render_local_section([SKILL_XLSX])
        expected_body = "\n### excel-skill\n表格处理流程\n"
        assert bodies == [("excel-skill", expected_body)]
        assert section.endswith(expected_body)

    def test_empty_no_section(self):
        loader = SkillLoader(skill_manager=fake_manager())
        assert loader.render_local_section([]) == ("", [])

    def test_budget_truncates_with_self_report(self):
        big = {"name": "big", "content": "内容" * 2000, "triggers": []}
        loader = SkillLoader(skill_manager=fake_manager(), budget_tokens=300)
        section, bodies = loader.render_local_section([big])
        assert _TRUNCATED_MARK in section
        assert loader.stats["truncations"] == 1
        # 截断自报文本必须真的比原文短（不是假标记）
        assert len(bodies[0][1]) < len(big["content"])

    def test_small_skill_untouched_under_budget(self):
        loader = SkillLoader(skill_manager=fake_manager(), budget_tokens=DEFAULT_BUDGET_TOKENS)
        section, bodies = loader.render_local_section([SKILL_PDF])
        assert _TRUNCATED_MARK not in section
        assert loader.stats["truncations"] == 0
        assert "用read_file解析PDF" in bodies[0][1]

    def test_caps_max_skills(self):
        loader = SkillLoader(skill_manager=fake_manager(), max_skills=2)
        skills = [{"name": f"s{i}", "content": "c", "triggers": []} for i in range(5)]
        _, bodies = loader.render_local_section(skills)
        assert len(bodies) == 2

    def test_missing_name_fallback(self):
        loader = SkillLoader(skill_manager=fake_manager())
        _, bodies = loader.render_local_section([{"content": "c"}])
        assert bodies[0][0] == "unnamed_skill"


# ── 加载：gene 推荐载荷内容加载 ──────────────────────────────

class TestRenderRecommended:
    def test_loads_content_fields(self):
        payload = [{
            "skill_id": "k1", "name": "发票报销流程", "description": "OCR识别发票并填单",
            "code_template": "ocrmypdf(x); submit(y)", "usage_count": 7,
            "success_rate": 0.86, "relevance_score": 0.72,
        }]
        loader = SkillLoader(skill_manager=fake_manager())
        out = loader.render_recommended(payload)
        assert "### 发票报销流程" in out
        assert "描述: OCR识别发票并填单" in out
        assert "```\\nocrmypdf(x); submit(y)\\n```".replace("\\n", "\n") in out
        assert "使用 7 次" in out
        assert "成功率 86%" in out
        assert "相关性 0.72" in out
        assert loader.stats["recommended_loaded"] == 1

    def test_missing_optional_fields_tolerated(self):
        """载荷缺code_template/success_rate不猜不编造，只注描述。"""
        loader = SkillLoader(skill_manager=fake_manager())
        out = loader.render_recommended([{"name": "n", "description": "d"}])
        assert "描述: d" in out
        assert "模板" not in out
        assert "成功率" not in out

    def test_empty_returns_empty(self):
        loader = SkillLoader(skill_manager=fake_manager())
        assert loader.render_recommended([]) == ""
        assert loader.stats["recommended_loaded"] == 0

    def test_budget_truncates_with_self_report(self):
        payload = [{"name": "big", "description": "很长的描述" * 1000, "usage_count": 1}]
        loader = SkillLoader(skill_manager=fake_manager(), budget_tokens=300)
        out = loader.render_recommended(payload)
        assert _TRUNCATED_MARK in out
        assert loader.stats["truncations"] == 1

    def test_caps_max_skills(self):
        loader = SkillLoader(skill_manager=fake_manager(), max_skills=2)
        payload = [{"name": f"s{i}", "description": "d"} for i in range(5)]
        out = loader.render_recommended(payload)
        assert out.count("### ") == 2


# ── 可观测 + 接线护栏 ───────────────────────────────────────

class TestObservabilityAndWiring:
    def test_stats_snapshot(self):
        loader = SkillLoader(skill_manager=fake_manager(all_skills=[SKILL_PDF]))
        loader.match_for_task("请帮我解析这份PDF文档的内容并总结要点")
        loader.render_local_section([SKILL_PDF])
        snap = loader.snapshot()
        assert snap["task_matches"] == 1
        assert snap["injections"] == 1

    def test_soulmate_agent_wiring(self):
        src = (Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py").read_text(
            encoding="utf-8")
        # 检索收口：回合级 gate 单一真源
        assert "self._skill_loader().match_for_task(user_text)" in src
        # 加载：注入段渲染 + gene推荐载荷内容注入（不再是"可用技能: 名字"）
        assert "render_local_section(matched_skills)" in src
        assert "render_recommended(skill_data[\"skills\"])" in src
        assert "可用技能: {', '.join(skill_names)}" not in src  # 旧名字-only注入代码已移除
        # 步骤级重新匹配：首试+重试两处消费step_skills
        assert "match_for_step(step.description, matched_skills)" in src
        assert src.count("matched_skills=step_skills") == 2
        # lazy-init入口
        assert "def _skill_loader(self)" in src
        assert "from agent.skill_loader import SkillLoader" in src

    def test_new_agent_lazy_loader_smoke(self):
        """__new__裸实例lazy-init烟测：检索/渲染真实跑通。"""
        import agent.soulmate_agent as sa

        agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__
        agent._skill_manager = fake_manager(all_skills=[SKILL_PDF])
        loader = agent._skill_loader()
        hits = loader.match_for_task("请帮我解析这份PDF文档的内容并总结要点")
        assert [h["name"] for h in hits] == ["pdf-skill"]
        section, bodies = loader.render_local_section(hits)
        assert section.startswith("\n\n## 相关技能")
        assert bodies[0][0] == "pdf-skill"
        # 推荐载荷渲染同样走同一loader
        out = loader.render_recommended([{"name": "n", "description": "d"}])
        assert "### n" in out
