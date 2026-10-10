"""路线图 6.2 技能检索与加载：按任务自动匹配并注入。

覆盖：触发词闸门（纯描述/tag 不注入）/任务级精排/短消息与文件请求跳过/
预算感知加载（单技能截断显式标记+总预算丢弃说明）/环境变量覆盖/
真实 SkillManager 检索→匹配→渲染管道（含 saved 层）/soulmate_agent 接线护栏。
"""

import inspect

import pytest

from skill_manager import SkillManager
from agent.skill_matcher import SkillMatcher, get_skill_matcher
from agent.skill_saver import SkillSaver


def sk(name, triggers, description="", content="步骤内容", code_template="", tags=None):
    return {
        "id": f"id-{name}", "name": name, "triggers": triggers,
        "description": description, "content": content,
        "code_template": code_template, "tags": tags or [],
    }


# ── 匹配：触发词闸门 + 任务级精排 ─────────────────────────────

class TestMatch:
    def test_description_only_dropped(self):
        """纯 description 命中不注入（既有语义：只保留触发词匹配的技能）"""
        m = SkillMatcher()
        out = m.match("帮我处理一个很长的表格任务", [sk("表", [], description="表格处理")])
        assert out == []

    def test_trigger_gate_pass(self):
        m = SkillMatcher()
        out = m.match("请帮我搜索收件箱里的邮件并整理", [sk("mail", ["收件箱", "邮件"])])
        assert len(out) == 1
        assert out[0]["_hit_triggers"]

    def test_generic_cjk_trigger_not_injectable(self):
        """通用词触发词（操作/处理…）不进注入闸门"""
        m = SkillMatcher()
        assert m.match("请帮我操作一下这个文件内容看看", [sk("g", ["操作"])]) == []

    def test_ranking_trigger_quality_first(self):
        """精确/子串命中排在弱命中之前"""
        m = SkillMatcher()
        weak = sk("weak", ["发票"], description="")
        strong = sk("strong", ["发票OCR"], description="")
        out = m.match("帮我做发票OCR识别这张扫描件", [weak, strong])
        assert [s["name"] for s in out] == ["strong", "weak"]
        assert out[0]["_match_score"] > out[1]["_match_score"]

    def test_limit_and_stable_order(self):
        m = SkillMatcher()
        cands = [sk(f"s{i}", ["邮件"]) for i in range(5)]
        out = m.match("这是一封邮件的处理任务请开始执行", cands, limit=2)
        assert [s["name"] for s in out] == ["s0", "s1"]  # 同分按输入顺序稳定

    def test_empty_and_bad_input(self):
        m = SkillMatcher()
        assert m.match("", [sk("a", ["邮件"])]) == []
        assert m.match("   ", [sk("a", ["邮件"])]) == []
        assert m.match("这是一条足够长的任务文本", []) == []
        assert m.match("这是一条足够长的任务文本", ["not-a-dict"]) == []

    def test_does_not_mutate_candidates(self):
        cand = sk("a", ["邮件"])
        m = SkillMatcher()
        out = m.match("这是一封邮件的处理任务请开始执行", [cand])
        assert "_match_score" in out[0] and "_match_score" not in cand


# ── 加载：预算感知渲染 ────────────────────────────────────────

class TestRender:
    def test_short_skill_byte_identical_to_old_format(self):
        """未超预算时注入正文与既有格式逐字一致（归因/内容零变化）"""
        m = SkillMatcher()
        s = sk("demo", ["x"], content="技能正文ABC", code_template="code()")
        bodies, note = m.render_bodies([s])
        assert note == ""
        assert bodies == [("\n### demo\n技能正文ABC\n```\ncode()\n```\n", s)]

    def test_long_content_truncated_explicit_marker(self):
        m = SkillMatcher(per_skill_tokens=300, budget_tokens=10000)
        s = sk("big", ["x"], content="长内容" * 2000, )
        bodies, _ = m.render_bodies([s])
        body = bodies[0][0]
        assert "技能内容过长已截断" in body  # 显式标记，禁静默
        assert "read_file" in body
        assert "长内容" * 2000 not in body

    def test_total_budget_drops_tail_with_note(self):
        m = SkillMatcher(per_skill_tokens=500, budget_tokens=400)
        s1 = sk("first", ["x"], content="内容A" * 30)
        s2 = sk("second", ["x"], content="内容B" * 300)
        bodies, note = m.render_bodies([s1, s2])
        names = [s["name"] for _, s in bodies]
        assert names == ["first"]  # second 超总预算被丢
        assert "second" in note and "未注入" in note  # 丢弃显式说明

    def test_env_override_per_skill_tokens(self, monkeypatch):
        monkeypatch.setenv("SOULMATE_SKILL_PER_TOKENS", "300")
        m = SkillMatcher()
        assert m.per_skill_tokens == 300
        s = sk("big", ["x"], content="长内容" * 1000)
        bodies, _ = m.render_bodies([s])
        assert "已截断" in bodies[0][0]

    def test_render_item_failure_degrades_not_crash(self):
        class Boom(dict):
            def get(self, *a, **k):
                raise RuntimeError("boom")

        m = SkillMatcher()
        good = sk("good", ["x"])
        bodies, note = m.render_bodies([Boom(), good])
        assert [s["name"] for _, s in bodies] == ["good"]
        assert "boom" not in note.lower()


# ── 真实 SkillManager 管道（检索→匹配→渲染） ──────────────────

def _mgr(tmp_path):
    std_dir = tmp_path / "agents-standard"
    std_dir.mkdir()
    return SkillManager(
        skills_dir=str(tmp_path / "json-skills"),
        standard_dirs=[("agents-test", str(std_dir))],
        saved_dir=str(tmp_path / "saved"),
    )


class TestPipeline:
    def test_search_match_render_roundtrip(self, tmp_path):
        m = _mgr(tmp_path)
        m.create_skill(
            name="发票OCR入库", description="扫描件发票OCR识别后分类入库",
            triggers=["发票", "OCR"], content="1. ocrmypdf\n2. 入库",
            code_template="ocrmypdf f.pdf", tags=["finance"],
        )
        raw = m.search_skills("帮我把这张发票做OCR识别", limit=3)
        matched = SkillMatcher().match("帮我把这张发票做OCR识别", raw, limit=3)
        assert [s["name"] for s in matched] == ["发票OCR入库"]
        bodies, note = SkillMatcher().render_bodies(matched)
        assert note == ""
        assert "1. ocrmypdf" in bodies[0][0]

    def test_saved_layer_skill_matched(self, tmp_path):
        """6.1 保存的技能进 saved 层后按任务命中并可注入（6.2 全链路）"""
        m = _mgr(tmp_path)
        saver = SkillSaver(library_dir=str(tmp_path / "saved"))
        saver.save_flow(
            name="发票OCR识别入库", description="扫描件发票OCR识别后分类入库报销",
            triggers=["发票", "报销", "OCR"],
            steps=["读取扫描件PDF", "ocrmypdf识别文本"],
            pitfalls=["扫描件无文本层"], verify="sqlite查行", category="finance",
        )
        raw = m.search_skills("帮我报销这张发票", limit=3)
        matched = SkillMatcher().match("帮我报销这张发票", raw, limit=3)
        assert matched and matched[0]["name"] == "发票OCR识别入库"
        assert matched[0].get("read_only") is False and matched[0].get("revision") == 1
        bodies, _ = SkillMatcher().render_bodies(matched)
        assert "## 步骤" in bodies[0][0]  # SKILL.md 正文完整注入


# ── soulmate_agent 接线护栏 ───────────────────────────────────

class TestWiring:
    def test_match_method_wired_in_agent_source(self):
        from agent import soulmate_agent as sa
        # 匹配接线在调用方 _prompt_inner（matched_skills 传入 _run_llm_with_tools）
        src = inspect.getsource(sa.SoulMateAgent._prompt_inner)
        assert "self._match_skills_for_task(user_text)" in src  # 匹配接线
        run_src = inspect.getsource(sa.SoulMateAgent._run_llm_with_tools)
        assert "render_bodies(" in run_src  # 注入渲染接线

    def test_agent_match_short_msg_and_file_req_skip(self):
        from agent.soulmate_agent import SoulMateAgent

        class StubMgr:
            def search_skills(self, query, limit=3):
                return [sk("mail", ["收件箱", "邮件"])]

        agent = SoulMateAgent.__new__(SoulMateAgent)
        agent._skill_manager = StubMgr()
        # 正常任务：命中
        out = agent._match_skills_for_task("请帮我搜索收件箱里的邮件并整理归档")
        assert [s["name"] for s in out] == ["mail"]
        # 短消息：跳过
        assert agent._match_skills_for_task("你好啊") == []
        # 文件发送请求：走 MEDIA 路径跳过技能
        assert agent._match_skills_for_task("把 report.pdf 文件发给我下载") == []

    def test_singleton(self):
        assert get_skill_matcher() is get_skill_matcher()
