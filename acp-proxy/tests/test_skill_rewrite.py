# -*- coding: utf-8 -*-
"""路线图 13.5 技能进化语义改写：LLM 辅助 + 确定性零丢失保真门测试"""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.skill_evolution import SkillEvolver  # noqa: E402
from agent.skill_rewrite import (  # noqa: E402
    SkillRewriter,
    build_rewrite_prompt,
    fidelity_check,
    hard_tokens,
    parse_proposal,
)
from agent.skill_saver import SkillSaver  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


class FidelityGateTest(unittest.TestCase):
    """零丢失保真门（确定性）"""

    def test_hard_tokens_extract(self):
        toks = hard_tokens('运行 `npm build` 后写入 /tmp/out.json，端口 3000，常量 MAX_ROUNDS')
        self.assertIn("`npm build`", toks)
        self.assertIn("/tmp/out.json", toks)
        self.assertIn("3000", toks)
        self.assertIn("max_rounds", toks)

    def test_paraphrase_with_tokens_preserved_passes(self):
        old = ["读取 /tmp/data.json 然后执行 `npm build`"]
        new = ["先读取 /tmp/data.json 文件内容，再执行 `npm build` 命令"]
        ok, reasons = fidelity_check(old, new, ["导出"], ["导出", "生成报表"])
        self.assertTrue(ok, reasons)

    def test_step_count_change_rejected(self):
        # 合并/拆分 = 语义取舍 → 拒绝
        ok, reasons = fidelity_check(
            ["步骤一", "步骤二"], ["步骤一 步骤二"], ["t"], ["t"]
        )
        self.assertFalse(ok)
        self.assertTrue(any("取舍" in r for r in reasons))

    def test_hard_token_lost_rejected(self):
        ok, reasons = fidelity_check(
            ["写入 /tmp/out.json"], ["写入输出文件"], ["t"], ["t"]
        )
        self.assertFalse(ok)
        self.assertTrue(any("丢失语义载体" in r for r in reasons))

    def test_trigger_deletion_rejected(self):
        ok, reasons = fidelity_check(
            ["步骤"], ["步骤"], ["触发甲", "触发乙"], ["触发甲"]
        )
        self.assertFalse(ok)
        self.assertTrue(any("删除" in r for r in reasons))

    def test_new_trigger_too_short_rejected(self):
        ok, reasons = fidelity_check(
            ["步骤"], ["步骤"], ["触发甲"], ["触发甲", "ab"]
        )
        self.assertFalse(ok)
        self.assertTrue(any("无效" in r for r in reasons))

    def test_new_trigger_too_long_rejected(self):
        ok, reasons = fidelity_check(
            ["步骤"], ["步骤"], ["触发甲"], ["触发甲", "这" * 41]
        )
        self.assertFalse(ok)
        self.assertTrue(any("过长" in r for r in reasons))

    def test_empty_step_rejected(self):
        ok, reasons = fidelity_check(["步骤"], [""], ["t"], ["t"])
        self.assertFalse(ok)


class ParseProposalTest(unittest.TestCase):
    def test_plain_json(self):
        steps, adds, err = parse_proposal('{"steps": ["a"], "trigger_additions": ["b"]}')
        self.assertEqual(err, "")
        self.assertEqual(steps, ["a"])
        self.assertEqual(adds, ["b"])

    def test_fenced_json_with_explanation(self):
        raw = '好的，改写如下：\n```json\n{"steps": ["s1"], "trigger_additions": []}\n```\n完成。'
        steps, adds, err = parse_proposal(raw)
        self.assertEqual(err, "")
        self.assertEqual(steps, ["s1"])

    def test_no_change_marker(self):
        steps, adds, err = parse_proposal('{"steps": [], "trigger_additions": []}')
        self.assertEqual(err, "")
        self.assertEqual(steps, [])
        self.assertEqual(adds, [])

    def test_garbage_rejected(self):
        steps, adds, err = parse_proposal("我觉得这个技能挺好的")
        self.assertEqual(steps, [])
        self.assertNotEqual(err, "")

    def test_wrong_type_rejected(self):
        steps, adds, err = parse_proposal('{"steps": "不是数组", "trigger_additions": []}')
        self.assertEqual(steps, [])
        self.assertNotEqual(err, "")


class SkillRewriterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self._tmp.name, "library")
        self.saver = SkillSaver(library_dir=self.lib)
        self.rewriter = SkillRewriter(saver=self.saver, timeout_s=5)
        saved = self.saver.save_flow(
            name="测试技能",
            description="用于测试的技能",
            triggers=["测试技能"],
            steps=["第一步读取 /tmp/data.json 文件", "第二步执行 `npm build`"],
            pitfalls=["原有坑：路径要绝对"],
            verify="跑一遍确认输出",
            category="test",
        )
        self.slug = saved["slug"]
        self.skill = self.saver.load_skill(self.slug)

    def tearDown(self):
        self._tmp.cleanup()

    def _llm(self, payload):
        async def fake(prompt):
            return json.dumps(payload, ensure_ascii=False)

        return fake

    def test_rewrite_applied_zero_loss(self):
        payload = {
            "steps": [
                "先读取 /tmp/data.json 文件内容",
                "随后执行 `npm build` 命令完成构建",
            ],
            "trigger_additions": ["生成测试报表"],
        }
        out = _run(self.rewriter.rewrite(self.skill, user_text="帮我生成测试报表", llm=self._llm(payload)))
        self.assertEqual(out.action, "rewritten", out.reason)
        self.assertEqual(out.changed_steps, 2)
        self.assertEqual(out.added_triggers, ["生成测试报表"])
        back = self.saver.load_skill(self.slug)
        self.assertIn("随后执行 `npm build` 命令完成构建", back["body"])
        # name/description/坑/验证逐字不动（取舍类不在此通道）
        self.assertEqual(back["name"], "测试技能")
        self.assertEqual(back["description"], "用于测试的技能")
        self.assertIn("原有坑：路径要绝对", back["body"])
        self.assertIn("跑一遍确认输出", back["body"])
        self.assertEqual(back["revision"], 2)

    def test_rewrite_rejected_file_untouched(self):
        payload = {
            "steps": ["第一步读取文件", "第二步执行构建"],  # 丢 /tmp/data.json 与 `npm build`
            "trigger_additions": [],
        }
        before = Path(self.skill["skill_md"]).read_text(encoding="utf-8")
        out = _run(self.rewriter.rewrite(self.skill, llm=self._llm(payload)))
        self.assertEqual(out.action, "rejected")
        self.assertIn("保真门", out.reason)
        after = Path(self.skill["skill_md"]).read_text(encoding="utf-8")
        self.assertEqual(before, after)  # 零丢失承诺：拒绝=原文逐字不动

    def test_semantic_tradeoff_rejected_needs_user(self):
        # 删触发词=语义取舍 → 拒绝并报"需用户拍板"
        payload = {"steps": [], "trigger_additions": []}
        out = _run(self.rewriter.rewrite(self.skill, llm=self._llm(payload)))
        self.assertEqual(out.action, "skipped")  # 空提案=无需改写

        payload2 = {
            "steps": [
                "第一步读取 /tmp/data.json 文件",
                "第二步执行 `npm build`",
            ],
            "trigger_additions": ["新触发"],
        }
        # 直接用 gate 验证删除触发词被拒（rewrite 通道不会发出删除提案，
        # 这里验证保真门对"伪造的删除结果"同样拒绝）
        ok, reasons = fidelity_check(
            ["s"], ["s"], ["测试技能"], ["新触发"]
        )
        self.assertFalse(ok)
        self.assertTrue(any("需用户拍板" in r for r in reasons))

    def test_llm_error_string_skipped(self):
        async def failing(prompt):
            return "[LLM错误: 401 Invalid API Key]"

        out = _run(self.rewriter.rewrite(self.skill, llm=failing))
        self.assertEqual(out.action, "skipped")
        self.assertIn("LLM 不可用", out.reason)

    def test_llm_timeout_skipped(self):
        async def slow(prompt):
            await asyncio.sleep(1.0)
            return "{}"

        rewriter = SkillRewriter(saver=self.saver, timeout_s=0.1)
        out = _run(rewriter.rewrite(self.skill, llm=slow))
        self.assertEqual(out.action, "skipped")
        self.assertIn("超时", out.reason)

    def test_no_llm_skipped(self):
        out = _run(self.rewriter.rewrite(self.skill, llm=None))
        self.assertEqual(out.action, "skipped")
        self.assertIn("LLM 不可用", out.reason)

    def test_non_rendered_body_skipped(self):
        skill = dict(self.skill)
        skill["body"] = "随便写的正文，没有标准分区"
        out = _run(self.rewriter.rewrite(skill, llm=self._llm({"steps": [], "trigger_additions": []})))
        self.assertEqual(out.action, "skipped")
        self.assertIn("非SkillSaver渲染格式", out.reason)

    def test_readback_failure_restores_original(self):
        # 回读复验路径：写回后 load_skill 返回被篡改内容 → 拒绝+还原原文
        real_load = SkillSaver.load_skill

        def corrupting_load(saver_self, slug):
            skill = real_load(saver_self, slug)
            if skill is not None and getattr(saver_self, "_saved_once", False):
                skill["body"] = skill["body"].replace("/tmp/data.json", "被篡改")
            return skill

        payload = {
            "steps": [
                "第一步读取 /tmp/data.json 文件",
                "第二步执行 `npm build` 命令",
            ],
            "trigger_additions": [],
        }

        async def fake(prompt):
            # 真实写回一次后开始篡改回读
            self.saver._saved_once = True
            return json.dumps(payload, ensure_ascii=False)

        SkillSaver.load_skill = corrupting_load
        try:
            out = _run(self.rewriter.rewrite(self.skill, llm=fake))
        finally:
            SkillSaver.load_skill = real_load
        self.assertEqual(out.action, "rejected")
        self.assertIn("回读复验失败", out.reason)
        # 还原后磁盘原文仍含语义载体
        after = Path(self.skill["skill_md"]).read_text(encoding="utf-8")
        self.assertIn("/tmp/data.json", after)

    def test_prompt_contains_hard_rules(self):
        prompt = build_rewrite_prompt(
            name="n", description="d", triggers=["t"], steps=["s1"],
            user_text="帮我做X",
        )
        self.assertIn("纯 JSON", prompt)
        self.assertIn("禁止删除/合并/拆分步骤", prompt)
        self.assertIn("帮我做X", prompt)


class EvolveSemanticsTest(unittest.TestCase):
    """SkillEvolver.evolve_semantics：频控/名额/审计/接线"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self._tmp.name, "library")
        self.db = os.path.join(self._tmp.name, "evolution.db")
        self.saver = SkillSaver(library_dir=self.lib)
        self.evolver = SkillEvolver(
            saver=self.saver, db_path=self.db,
            rewrite_min_interval_s=3600, max_rewrite_per_turn=1,
        )
        self.slugs = []
        for name in ("技能甲", "技能乙"):
            saved = self.saver.save_flow(
                name=name,
                description=f"{name}描述",
                triggers=[name],
                steps=[f"第一步读取 /tmp/{name}.json 文件", f"第二步执行 `build-{name}`"],
                pitfalls=["原有坑"],
                verify="确认输出",
                category="test",
            )
            self.slugs.append(saved["slug"])
        self.calls = 0

    def tearDown(self):
        self._tmp.cleanup()

    def _skills(self):
        out = []
        for slug in self.slugs:
            s = self.saver.load_skill(slug)
            s["id"] = f"saved:{slug}"
            out.append(s)
        return out

    def _llm(self, payload=None):
        payload = payload or {
            "steps": [
                "先第一步读取 /tmp/技能甲.json 文件内容",
                "随后第二步执行 `build-技能甲` 命令",
            ],
            "trigger_additions": ["新增触发词"],
        }

        async def fake(prompt):
            self.calls += 1
            return json.dumps(payload, ensure_ascii=False)

        return fake

    def test_budget_one_per_turn(self):
        out = _run(self.evolver.evolve_semantics(
            self._skills(), user_text="做X", llm=self._llm(), session_id="s1"
        ))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].action, "rewritten", out[0].reason)
        self.assertEqual(out[1].action, "skipped")
        self.assertIn("名额已用", out[1].reason)
        self.assertEqual(self.calls, 1)  # 名额=1，LLM 只被调一次

    def test_rate_limit_second_call_skipped(self):
        _run(self.evolver.evolve_semantics(
            self._skills()[:1], llm=self._llm(), session_id="s1"
        ))
        out2 = _run(self.evolver.evolve_semantics(
            self._skills()[:1], llm=self._llm(), session_id="s2"
        ))
        self.assertEqual(out2[0].action, "skipped")
        self.assertIn("频控", out2[0].reason)
        self.assertEqual(self.calls, 1)  # 频控生效：LLM 未被再次调用

    def test_audit_row_recorded(self):
        _run(self.evolver.evolve_semantics(
            self._skills()[:1], llm=self._llm(), session_id="sess-9"
        ))
        rows = self.evolver.recent()
        self.assertTrue(any(r["action"] == "rewritten" for r in rows), rows)
        row = next(r for r in rows if r["action"] == "rewritten")
        self.assertEqual(row["session_id"], "sess-9")
        self.assertIn("新增触发词", row["added"])

    def test_rejection_recorded_in_audit(self):
        payload = {"steps": ["丢了语义载体"], "trigger_additions": []}
        out = _run(self.evolver.evolve_semantics(
            self._skills()[:1], llm=self._llm(payload), session_id="s1"
        ))
        self.assertEqual(out[0].action, "rejected")
        self.assertTrue(any(r["action"] == "rejected" for r in self.evolver.recent()))

    def test_env_disable(self):
        os.environ["SOULMATE_SKILL_REWRITE"] = "0"
        try:
            out = _run(self.evolver.evolve_semantics(
                self._skills(), llm=self._llm(), session_id="s1"
            ))
        finally:
            os.environ.pop("SOULMATE_SKILL_REWRITE", None)
        self.assertEqual(out, [])
        self.assertEqual(self.calls, 0)

    def test_standard_layer_skill_skipped(self):
        skills = [{"id": "std:某标准技能", "slug": ""}]
        out = _run(self.evolver.evolve_semantics(skills, llm=self._llm()))
        self.assertEqual(out, [])  # 只读层：单写入者纪律，连尝试都不做
        self.assertEqual(self.calls, 0)

    def test_wiring_guard_soulmate_agent(self):
        # 接线护栏：soulmate_agent 回合收尾真实调用 evolve_semantics（写了≠接线了）
        src = Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py"
        text = src.read_text(encoding="utf-8")
        self.assertIn("await _sem_evolver.evolve_semantics(", text)
        self.assertIn("Skill semantic rewrite skipped", text)


if __name__ == "__main__":
    unittest.main()
