"""P0 技能自动命中端到端验收（同一类任务跑两次，第二次自动命中+注入+验证）。

真实场景：发票报销任务。run1 无技能→执行→try_learn_skill 学到技能→命中记账
（任务原话沉淀为情况语句）；run2 同类任务换说法→SkillLoader.match_for_task 自动
命中（semantic/anchor 证据路）→render_local_section 注入上下文→装载验证收据
all_loaded→run2 再记账（情况语句增长）。

对照组断言：
- 空账本（无反馈闭环）时 run2 不命中——反馈闭环是二次命中率的机制本体；
- 负对照（天气闲聊）不命中——pitfall 30 防误触发语义保留；
- 接线护栏：soulmate_agent.py 真实聊天路径含记账/装载验证调用（"写了≠接线了"）。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent.skill_load_verify as slv  # noqa: E402
import agent.soulmate_agent as sa  # noqa: E402
from agent.skill_feedback import SkillFeedbackLedger  # noqa: E402
from agent.skill_loader import SkillLoader  # noqa: E402
from agent.skill_matcher import SkillMatcher  # noqa: E402
from agent.skill_semantic import SkillEmbedder  # noqa: E402
from skill_manager import SkillManager  # noqa: E402

TASK1 = "帮我把这个季度的发票报销单整理一下，填好提交给财务"
TASK2 = "请将本季度发票整理成报销单并交给财务部门"  # 同类任务·换说法
NEGATIVE = "今天天气怎么样，帮我查一下北京的天气情况"

FAKE_TOOL_CALLS = [
    {"function": {"name": "terminal", "arguments": {"command": "ls invoices/"}}},
    {"function": {"name": "read_file", "arguments": {"path": "invoices/q3.pdf"}}},
    {"function": {"name": "write_file", "arguments": {"path": "baoxiao.md", "content": "x"}}},
]
FAKE_RESPONSE = "已整理完成：\n1. 汇总了本季度发票\n2. 生成报销单\n3. 提交财务"


def make_env(tmp_path):
    """隔离环境：临时技能库/账本/嵌入缓存/收据（不碰真实运行数据）。"""
    mgr = SkillManager(
        skills_dir=str(tmp_path / "skills"),
        standard_dirs=[],
        saved_dir=str(tmp_path / "saved"),
    )
    ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
    # 嵌入器：opensoul 指向不可达地址+无 openai 配置 → hash 离线兜底（确定性、不触网）
    embedder = SkillEmbedder(
        cache_path=str(tmp_path / "embed_cache.json"),
        opensoul_url="http://127.0.0.1:1/unreachable",
        openai_base="",
    )
    matcher = SkillMatcher(skill_manager=mgr, ledger=ledger, embedder=embedder)
    loader = SkillLoader(skill_manager=mgr, feedback=ledger, matcher=matcher)
    return mgr, ledger, loader


def bare_agent(mgr, ledger, loader):
    """真实 SoulMateAgent 方法（__new__ 裸实例，注入隔离依赖）。"""
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    agent._skill_manager = mgr
    agent._skill_feedback_inst = ledger
    agent._skill_loader_inst = loader
    return agent


def run1_learn_and_record(mgr, agent):
    learned = asyncio.run(
        mgr.try_learn_skill(
            user_text=TASK1,
            assistant_response=FAKE_RESPONSE,
            tool_calls=FAKE_TOOL_CALLS,
        )
    )
    assert learned is not None, "run1 应从成功执行中学到技能"
    ok = agent._record_skill_hits([learned], TASK1, FAKE_RESPONSE)
    assert ok == 1
    return learned


class TestSecondRunAutoHit:
    def test_full_loop(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            "SOULMATE_SKILL_LOAD_RECEIPTS", str(tmp_path / "receipts.jsonl"))
        mgr, ledger, loader = make_env(tmp_path)
        agent = bare_agent(mgr, ledger, loader)

        # ── run1：无技能 → 学到 → 记账 ─────────────────────
        learned = run1_learn_and_record(mgr, agent)
        assert ledger.situations_for(learned["id"]), "run1 任务原话应沉淀为情况语句"

        # ── run2：换说法 → 自动命中（近零摸索）──────────────
        hits = loader.match_for_task(TASK2)
        assert hits, "run2 同类任务必须自动命中技能"
        assert hits[0]["id"] == learned["id"]
        assert hits[0]["match_reason"].split(":")[0] in ("semantic", "anchor", "trigger")

        # 注入上下文（字节契约渲染）+ 装载验证（写了≠接线了）
        section, bodies = loader.render_local_section(hits)
        assert learned["name"] in section
        overall = agent._verify_skill_loads(hits, section, bodies, "sess-e2e")
        assert overall == "all_loaded"

        # 收据落盘且可读回
        receipts = slv.read_receipts(path=str(tmp_path / "receipts.jsonl"))
        assert receipts and receipts[-1]["overall"] == "all_loaded"
        assert receipts[-1]["skills"][0]["skill_id"] == learned["id"]

        # run2 再记账：情况语句库增长（run3+ 更稳）
        agent._record_skill_hits(hits, TASK2, FAKE_RESPONSE)
        situations = ledger.situations_for(learned["id"])
        assert len(situations) >= 2

    def test_cold_loader_still_hits(self, tmp_path):
        """全新 SkillLoader 实例（冷启动）也命中——命中靠数据不靠进程状态。"""
        mgr, ledger, loader = make_env(tmp_path)
        agent = bare_agent(mgr, ledger, loader)
        learned = run1_learn_and_record(mgr, agent)

        _, ledger2, loader2 = make_env(tmp_path)
        loader2._feedback = ledger  # 同一账本文件，不同实例
        loader2._matcher_inst = SkillMatcher(
            skill_manager=mgr, ledger=ledger, embedder=loader.matcher().embedder())
        hits = loader2.match_for_task(TASK2)
        assert hits and hits[0]["id"] == learned["id"]

    def test_without_feedback_loop_second_run_misses(self, tmp_path):
        """对照组：空账本（无反馈闭环）run2 不命中——闭环是二次命中的机制本体。"""
        mgr, ledger, loader = make_env(tmp_path)
        agent = bare_agent(mgr, ledger, loader)
        learned = run1_learn_and_record(mgr, agent)

        empty_ledger = SkillFeedbackLedger(path=str(tmp_path / "empty.json"))
        loader._feedback = empty_ledger
        loader._matcher_inst = SkillMatcher(
            skill_manager=mgr, ledger=empty_ledger,
            embedder=loader.matcher().embedder())
        assert loader.match_for_task(TASK2) == []

    def test_negative_control_no_misroute(self, tmp_path):
        """负对照：天气闲聊绝不命中发票技能（pitfall 30 防误触发保留）。"""
        mgr, ledger, loader = make_env(tmp_path)
        agent = bare_agent(mgr, ledger, loader)
        learned = run1_learn_and_record(mgr, agent)
        assert loader.match_for_task(NEGATIVE) == []
        assert loader.match_for_task(NEGATIVE) == []  # 重复也不许积累误命中


class TestWiring:
    def test_soulmate_agent_real_path_wired(self):
        src = (Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py").read_text(
            encoding="utf-8")
        # 命中记账接线（回合收尾，matched_skills 消费）
        assert "self._record_skill_hits(matched_skills, user_text, full_response)" in src
        # 装载验证接线（注入点）
        assert "self._verify_skill_loads(matched_skills, _skill_section, _skill_bodies, session_id)" in src
        # run1 学到技能即记账（闭环起点）
        assert "self._record_skill_hits([_learned_skill], user_text, full_response)" in src
        # 检索单一真源（既有接线不动）
        assert "self._skill_loader().match_for_task(user_text)" in src

    def test_loader_routes_through_matcher(self):
        src = (Path(__file__).resolve().parent.parent / "agent" / "skill_loader.py").read_text(
            encoding="utf-8")
        assert "self.matcher().admit(text)" in src
        assert "from agent.skill_matcher import SkillMatcher" in src
