# -*- coding: utf-8 -*-
"""P2-10 fork 沙箱对照晋升门测试（蓝图 P2.4：fork 沙箱跑对照，再晋升主配置）"""

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent.fork_promote as fp  # noqa: E402
from agent.fork_promote import (  # noqa: E402
    KIND_PROMPT,
    KIND_SKILL_MD,
    compare_and_decide,
    render_skill_artifact,
)
from agent.skill_evolution import SkillEvolver  # noqa: E402
from agent.skill_rewrite import SkillRewriter  # noqa: E402
from agent.skill_saver import SkillSaver  # noqa: E402


def _skill_md(steps, triggers, pitfalls, verify="跑一遍确认输出"):
    return render_skill_artifact(
        name="测试技能", description="用于测试的技能",
        triggers=triggers, steps=steps, pitfalls=pitfalls,
        verify=verify, category="test",
    )


BASE_MD = _skill_md(
    steps=["第一步读取文件", "第二步写入结果"],
    triggers=["测试技能", "测试"],
    pitfalls=["原有坑：路径要绝对"],
)
CAND_ADD_MD = _skill_md(
    steps=["第一步读取文件", "第二步写入结果"],
    triggers=["测试技能", "测试"],
    pitfalls=["原有坑：路径要绝对", "read_file 可能报错「x」（recoverable）—— 重试"],
)
CAND_DROP_MD = _skill_md(
    steps=["第一步读取文件"],
    triggers=["测试技能"],
    pitfalls=["原有坑：路径要绝对"],
)


class CompareAndDecideTest(unittest.TestCase):
    """对照晋升裁决：双向语义（该晋的晋、该留的留）"""

    def test_promote_when_candidate_wins(self):
        v = compare_and_decide(KIND_SKILL_MD, BASE_MD, CAND_ADD_MD)
        self.assertTrue(v["promote"], v["reasons"])
        self.assertEqual(v["reasons"], [])
        self.assertTrue(v["baseline"]["ok"])
        self.assertTrue(v["candidate"]["ok"])
        self.assertTrue(v["pairwise"]["ok"])

    def test_keep_baseline_when_candidate_drops(self):
        v = compare_and_decide(KIND_SKILL_MD, BASE_MD, CAND_DROP_MD)
        self.assertFalse(v["promote"])
        self.assertTrue(any("零丢失" in r for r in v["reasons"]), v["reasons"])

    def test_keep_baseline_when_candidate_unparseable(self):
        v = compare_and_decide(KIND_SKILL_MD, BASE_MD, "完全不是技能格式的文本")
        self.assertFalse(v["promote"])
        self.assertTrue(any("未通过" in r or "劣于基线" in r or "阈值" in r
                            for r in v["reasons"]), v["reasons"])

    def test_baseline_failing_candidate_passing_promotes(self):
        # 对照方向：基线烂候选好 → 候选胜出即晋升（score 不低于基线）
        v = compare_and_decide(KIND_SKILL_MD, "基线无分区", CAND_ADD_MD)
        self.assertTrue(v["promote"], v["reasons"])
        self.assertFalse(v["baseline"]["ok"])

    def test_prompt_kind_promote_and_keep(self):
        v = compare_and_decide(KIND_PROMPT, "line one\nline two",
                               "line one\nline two\nline three")
        self.assertTrue(v["promote"], v["reasons"])
        v2 = compare_and_decide(KIND_PROMPT, "line one\nline two", "line one")
        self.assertFalse(v2["promote"])
        self.assertTrue(any("baseline_lines_retained" in r for r in v2["reasons"]))

    def test_unknown_kind_rejected(self):
        v = compare_and_decide("nonsense", "a", "b")
        self.assertFalse(v["promote"])
        self.assertTrue(any("未知 kind" in r for r in v["reasons"]))

    def test_sandbox_timeout_contained(self):
        # fork 隔离：沙箱超时只收编该路评测，主进程不挂、不晋升
        with mock.patch.object(fp, "SANDBOX_TIMEOUT_S", 0.01):
            v = compare_and_decide(KIND_SKILL_MD, BASE_MD, CAND_ADD_MD)
        self.assertFalse(v["promote"])
        self.assertTrue(any("timeout" in (v["candidate"].get("error") or "")
                            or "未通过" in r for r in v["reasons"]), v)

    def test_harness_crash_contained(self):
        # 评测崩溃在子进程内收编：候选按未通过处理，主进程完好
        with mock.patch.object(fp, "_harness_checks",
                               side_effect=RuntimeError("boom")):
            # _harness_checks 在子进程内跑，patch 不影响子进程；直接验证
            # harness 异常被收编的语义（子进程入口层）
            rc = fp._harness_main(KIND_SKILL_MD, "/nonexistent-artifact",
                                  os.path.join(tempfile.mkdtemp(), "r.json"))
        self.assertEqual(rc, 0)  # 崩溃收编为 ok=False JSON，不非零退出


class SkillEvolutionWiringTest(unittest.TestCase):
    """接线钉：_evolve_one 晋升主配置前必须过 fork 对照门"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self._tmp.name, "library")
        self.saver = SkillSaver(library_dir=self.lib)
        self.evolver = SkillEvolver(
            saver=self.saver,
            db_path=os.path.join(self._tmp.name, "e.db"),
            max_new_per_turn=2, max_pitfalls=12,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _save_skill(self):
        return self.saver.save_flow(
            name="测试技能", description="用于测试的技能",
            triggers=["测试技能", "测试"],
            steps=["第一步读取文件", "第二步写入结果"],
            pitfalls=["原有坑：路径要绝对"],
            verify="跑一遍确认输出", category="test",
        )

    @staticmethod
    def _fail_entry():
        return {
            "name": "read_file", "arguments": "{}",
            "result_preview": "错误: 文件不存在或为空: /tmp/not-there",
            "permission": "allow",
        }

    def test_gate_denied_keeps_baseline(self):
        saved = self._save_skill()
        deny = {"promote": False, "reasons": ["对照劣于基线（候选 0.50 < 基线 1.00）"]}
        with mock.patch("agent.fork_promote.compare_and_decide", return_value=deny):
            results = self.evolver.evolve_from_turn(
                skills=[{"id": f"saved:{saved['slug']}"}],
                tool_calls_log=[self._fail_entry()],
            )
        self.assertEqual(results[0].action, "kept_baseline")
        self.assertIn("未晋升", results[0].reason)
        # 主配置不动：revision 未涨、坑未增
        reloaded = self.saver.load_skill(saved["slug"])
        self.assertEqual(reloaded["revision"], 1)
        self.assertNotIn("read_file 可能报错", reloaded["body"])

    def test_gate_passed_promotes_via_real_sandbox(self):
        saved = self._save_skill()
        results = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[self._fail_entry()],
        )
        self.assertEqual(results[0].action, "evolved", results[0].reason)
        reloaded = self.saver.load_skill(saved["slug"])
        self.assertEqual(reloaded["revision"], 2)
        self.assertIn("read_file 可能报错", reloaded["body"])

    def test_wiring_guard_source(self):
        root = Path(__file__).resolve().parents[1]
        evo_src = (root / "agent" / "skill_evolution.py").read_text(encoding="utf-8")
        rew_src = (root / "agent" / "skill_rewrite.py").read_text(encoding="utf-8")
        self.assertIn("compare_and_decide", evo_src)
        self.assertIn("compare_and_decide", rew_src)
        # 裁决必须在 save_flow 之前（晋升门语义）
        self.assertLess(evo_src.index("compare_and_decide"),
                        evo_src.index("saved = saver.save_flow"))
        self.assertLess(rew_src.index("compare_and_decide"),
                        rew_src.index("saved = saver.save_flow"))


class SkillRewriteWiringTest(unittest.TestCase):
    """接线钉：语义改写候选晋升主配置前必须过 fork 对照门"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self._tmp.name, "library")
        self.saver = SkillSaver(library_dir=self.lib)
        self.saved = self.saver.save_flow(
            name="测试技能", description="用于测试的技能",
            triggers=["测试技能", "测试"],
            steps=["第一步读取文件", "第二步写入结果"],
            pitfalls=["原有坑：路径要绝对"],
            verify="跑一遍确认输出", category="test",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_gate_denied_rewrite_rejected(self):
        async def fake_llm(_prompt):
            return ('{"steps": ["第一步读取文件（改写措辞）", "第二步写入结果"],'
                    ' "trigger_additions": ["新增触发词"]}')

        rewriter = SkillRewriter(saver=self.saver)
        skill = self.saver.load_skill(self.saved["slug"])
        skill["id"] = f"saved:{self.saved['slug']}"
        deny = {"promote": False, "reasons": ["候选沙箱评测未通过"]}
        with mock.patch("agent.fork_promote.compare_and_decide", return_value=deny):
            out = asyncio.run(rewriter.rewrite(skill, user_text="新增触发词", llm=fake_llm))
        self.assertEqual(out.action, "rejected")
        self.assertIn("未晋升", out.reason)
        reloaded = self.saver.load_skill(self.saved["slug"])
        self.assertEqual(reloaded["revision"], 1)
        self.assertNotIn("改写措辞", reloaded["body"])

    def test_gate_passed_rewrite_promotes(self):
        async def fake_llm(_prompt):
            return ('{"steps": ["第一步读取文件（改写措辞）", "第二步写入结果"],'
                    ' "trigger_additions": ["新增触发词"]}')

        rewriter = SkillRewriter(saver=self.saver)
        skill = self.saver.load_skill(self.saved["slug"])
        skill["id"] = f"saved:{self.saved['slug']}"
        out = asyncio.run(rewriter.rewrite(skill, user_text="新增触发词", llm=fake_llm))
        self.assertEqual(out.action, "rewritten", out.reason)
        reloaded = self.saver.load_skill(self.saved["slug"])
        self.assertIn("改写措辞", reloaded["body"])


if __name__ == "__main__":
    unittest.main()
