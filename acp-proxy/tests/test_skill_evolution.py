# -*- coding: utf-8 -*-
"""路线图 6.3 技能进化：执行结果反哺技能内容（确定性坑沉淀）测试"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.skill_evolution import (  # noqa: E402
    EvolutionResult,
    SkillEvolver,
    parse_sections,
)
from agent.skill_saver import SkillSaver  # noqa: E402
from agent.tool_recovery import FailureKind, classify_result  # noqa: E402


def _entry(name, result, permission="allow"):
    return {
        "name": name,
        "arguments": "{}",
        "result_preview": result,
        "permission": permission,
    }


OK_RESULT = "读取成功: hello world"
FAIL_MISSING = "错误: 文件不存在或为空: /tmp/not-there"
FAIL_TIMEOUT = "错误: 执行超时（120s）"


class SkillEvolutionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.lib = os.path.join(self._tmp.name, "library")
        self.db = os.path.join(self._tmp.name, "evolution.db")
        self.saver = SkillSaver(library_dir=self.lib)
        self.evolver = SkillEvolver(
            saver=self.saver, db_path=self.db, max_new_per_turn=2, max_pitfalls=12
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _save_skill(self, name="测试技能", pitfalls=None):
        return self.saver.save_flow(
            name=name,
            description="用于测试的技能",
            triggers=["测试技能", "测试"],
            steps=["第一步读取文件", "第二步写入结果"],
            pitfalls=pitfalls or ["原有坑：路径要绝对"],
            verify="跑一遍确认输出",
            category="test",
        )

    # ── 基础闭环 ───────────────────────────────────────────

    def test_pitfall_promoted_on_failure(self):
        saved = self._save_skill()
        results = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}", "name": "测试技能"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
            session_id="s1",
        )
        self.assertEqual(len(results), 1)
        r = results[0]
        self.assertEqual(r.action, "evolved")
        self.assertEqual(r.revision_before, 1)
        self.assertEqual(r.revision_after, 2)
        self.assertEqual(len(r.added), 1)
        self.assertIn("read_file", r.added[0])
        self.assertIn("recoverable", r.added[0])
        # 内容真的写回了技能文件
        reloaded = self.saver.load_skill(saved["slug"])
        self.assertIn("read_file", reloaded["body"])
        self.assertIn("原有坑", reloaded["body"])
        self.assertEqual(reloaded["revision"], 2)

    def test_no_failure_no_change(self):
        saved = self._save_skill()
        results = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[_entry("read_file", OK_RESULT, "allow")],
        )
        self.assertEqual(results[0].action, "no_change")
        self.assertEqual(self.saver.load_skill(saved["slug"])["revision"], 1)

    def test_blocked_not_failure(self):
        saved = self._save_skill()
        results = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[
                _entry("terminal", "[被拦截] 权限引擎拒绝", "deny"),
                _entry("read_file", "", "loop_guard:warn"),
            ],
        )
        # 拦截/留痕伪条目都不算技能缺陷证据
        self.assertEqual(results[0].action, "no_change")

    # ── 去重与上限 ─────────────────────────────────────────

    def test_dedup_same_signature(self):
        saved = self._save_skill()
        skill = [{"id": f"saved:{saved['slug']}"}]
        log = [_entry("read_file", FAIL_MISSING, "allow")]
        r1 = self.evolver.evolve_from_turn(skills=skill, tool_calls_log=log)
        self.assertEqual(r1[0].action, "evolved")
        r2 = self.evolver.evolve_from_turn(skills=skill, tool_calls_log=log)
        self.assertEqual(r2[0].action, "no_change")
        self.assertIn("去重", r2[0].reason)
        self.assertEqual(self.saver.load_skill(saved["slug"])["revision"], 2)

    def test_max_new_per_turn(self):
        saved = self._save_skill()
        log = [
            _entry("read_file", "错误: 文件不存在或为空: /tmp/a", "allow"),
            _entry("terminal", "错误: 执行超时（120s）", "allow"),
            _entry("write_file", "错误: 写入失败: disk full", "allow"),
        ]
        r = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}], tool_calls_log=log
        )
        self.assertEqual(r[0].action, "evolved")
        self.assertEqual(len(r[0].added), 2)  # max_new_per_turn=2

    def test_max_pitfalls_cap(self):
        existing = [f"旧坑{i}：占位内容" for i in range(12)]
        saved = self._save_skill(pitfalls=existing)
        r = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
        )
        self.assertEqual(r[0].action, "no_change")
        self.assertIn("上限", r[0].reason)

    # ── 恢复语义 ───────────────────────────────────────────

    def test_recovered_vs_unrecovered(self):
        saved = self._save_skill()
        log_recovered = [
            _entry("read_file", FAIL_MISSING, "allow"),
            _entry("read_file", OK_RESULT, "allow"),
        ]
        r = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}], tool_calls_log=log_recovered
        )
        self.assertIn("重试/换路径后可成功", r[0].added[0])

        saved2 = self._save_skill(name="测试技能二")
        r2 = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved2['slug']}"}],
            tool_calls_log=[_entry("terminal", FAIL_TIMEOUT, "allow")],
        )
        self.assertIn("本次未恢复", r2[0].added[0])
        self.assertIn("transient", r2[0].added[0])

    # ── 边界与诚实语义 ─────────────────────────────────────

    def test_std_skill_skipped(self):
        results = self.evolver.evolve_from_turn(
            skills=[{"id": "std:some-standard", "name": "标准技能"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
        )
        self.assertEqual(results[0].action, "skipped")
        self.assertIn("单写入者", results[0].reason)

    def test_missing_skill_file_skipped(self):
        r = self.evolver.evolve_from_turn(
            skills=[{"id": "saved:not-exist"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
        )
        self.assertEqual(r[0].action, "skipped")

    def test_unparseable_body_skipped(self):
        saved = self._save_skill()
        md_path = Path(self.lib) / saved["slug"] / "SKILL.md"
        md_path.write_text("---\nname: 测试技能\ndescription: x\n---\n\n正文没有分区\n", encoding="utf-8")
        r = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
        )
        self.assertEqual(r[0].action, "skipped")
        self.assertIn("不改写", r[0].reason)

    def test_empty_skills_noop(self):
        self.assertEqual(self.evolver.evolve_from_turn(skills=[], tool_calls_log=[]), [])
        self.assertEqual(self.evolver.evolve_from_turn(skills=None), [])

    # ── 内容保真（禁丢步骤/触发词/描述）──────────────────

    def test_content_preserved_after_evolve(self):
        saved = self._save_skill()
        self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
        )
        reloaded = self.saver.load_skill(saved["slug"])
        sections = parse_sections(reloaded["body"])
        self.assertEqual(sections["steps"], ["第一步读取文件", "第二步写入结果"])
        self.assertEqual(sections["verify"], "跑一遍确认输出")
        self.assertIn("原有坑：路径要绝对", sections["pitfalls"])
        self.assertEqual(len(sections["pitfalls"]), 2)
        self.assertIn("测试技能", str(reloaded["triggers"]))
        self.assertEqual(reloaded["description"], "用于测试的技能")

    # ── 审计 ───────────────────────────────────────────────

    def test_audit_rows_recorded(self):
        saved = self._save_skill()
        self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
            verify_report=type("V", (), {"ok": False, "items": [1, 2]})(),
            session_id="sess-x",
        )
        rows = self.evolver.recent(10)
        self.assertTrue(rows)
        row = rows[0]
        self.assertEqual(row["session_id"], "sess-x")
        self.assertEqual(row["action"], "evolved")
        self.assertEqual(row["slug"], saved["slug"])
        self.assertEqual(row["revision_after"], 2)
        self.assertTrue(row["added"])

    def test_verify_outcome_noted_on_no_change(self):
        saved = self._save_skill()
        r = self.evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[_entry("read_file", OK_RESULT, "allow")],
            verify_report=type("V", (), {"ok": False, "items": [1]})(),
        )
        self.assertEqual(r[0].action, "no_change")
        self.assertIn("verify", r[0].reason)
        rows = self.evolver.recent(5)
        self.assertIn("verify", rows[0]["reason"])

    def test_audit_write_failure_nonfatal(self):
        # db 路径不可写 → 进化照常完成，审计失败不冒泡
        evolver = SkillEvolver(
            saver=self.saver, db_path="/proc/definitely/not/writable/x.db"
        )
        saved = self._save_skill()
        r = evolver.evolve_from_turn(
            skills=[{"id": f"saved:{saved['slug']}"}],
            tool_calls_log=[_entry("read_file", FAIL_MISSING, "allow")],
        )
        self.assertEqual(r[0].action, "evolved")
        self.assertEqual(evolver.recent(5), [])

    # ── 单元：解析与签名 ───────────────────────────────────

    def test_parse_sections_roundtrip(self):
        md = SkillSaver.render_skill_md(
            name="n", description="d", triggers=["t1", "t2"],
            steps=["s1", "s2"], pitfalls=["p1"], verify="v", category="c",
        )
        body = md.split("---", 2)[2]
        s = parse_sections(body)
        self.assertEqual(s["triggers"], ["t1", "t2"])
        self.assertEqual(s["steps"], ["s1", "s2"])
        self.assertEqual(s["pitfalls"], ["p1"])
        self.assertEqual(s["verify"], "v")

    def test_failure_kind_single_source(self):
        self.assertEqual(classify_result(FAIL_MISSING), FailureKind.RECOVERABLE)
        self.assertEqual(classify_result(FAIL_TIMEOUT), FailureKind.TRANSIENT)
        self.assertEqual(classify_result(OK_RESULT), FailureKind.OK)

    def test_evolution_result_shape(self):
        r = EvolutionResult("id", "slug", "evolved")
        self.assertEqual(r.added, [])
        self.assertEqual(r.revision_after, 0)


class WiringGuardTest(unittest.TestCase):
    """接线护栏：写了≠接线了——源码里必须有回合收尾钩子"""

    def test_soulmate_agent_calls_evolver(self):
        src = Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py"
        text = src.read_text(encoding="utf-8", errors="replace")
        self.assertIn("from agent.skill_evolution import SkillEvolver", text)
        self.assertIn("evolve_from_turn", text)

    def test_hook_after_record_usage(self):
        src = Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py"
        text = src.read_text(encoding="utf-8", errors="replace")
        i_usage = text.find("record_usage(skill[\"id\"])")
        i_hook = text.find("evolve_from_turn")
        self.assertGreater(i_usage, 0)
        self.assertGreater(i_hook, i_usage)


if __name__ == "__main__":
    unittest.main()
