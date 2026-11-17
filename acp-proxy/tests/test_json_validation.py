# -*- coding: utf-8 -*-
"""Pi 取长补短 #11——json-validation 预置 Schema 工具（四阶段校验/路径越界/
错误回传）。

整合面：agent/output_validator.py（既有解析→schema 校验→修复三段）扩第四段
路径越界 + 预置 Schema 注册表 + 结构化错误回传（归一成 repair_loop.step 的
验收输入形态——验收→修复天然对接 #3）。工具面：guard_tools 治理组第四件。

通用 JSON 产物验收能力（非投标专用）。
"""
import json

import pytest

from agent.guard_tools import TOOL_NAMES, GuardTools, tool_defs
from agent.output_validator import SCHEMA_PRESETS, OutputValidator, _looks_like_path
from agent import repair_loop


@pytest.fixture(autouse=True)
def _clean_presets():
    SCHEMA_PRESETS.clear()
    yield
    SCHEMA_PRESETS.clear()


SCHEMA = {
    "type": "object",
    "required": ["name", "qty"],
    "properties": {
        "name": {"type": "string"},
        "qty": {"type": "number", "minimum": 1},
        "status": {"type": "string", "enum": ["draft", "final"]},
    },
}


# ── ① 四阶段校验核心 ─────────────────────────────────────────────

class TestFourStages:
    def test_stage1_parse_failure(self):
        v = OutputValidator()
        r = v.validate_artifact("完全不是 JSON 的文本", schema=SCHEMA)
        assert r["ok"] is False and r["stage"] == "parse"
        assert r["blocking"] == ["stage1-parse"]

    def test_stage1_extracts_json_block(self):
        v = OutputValidator()
        r = v.validate_artifact(
            '前言\n```json\n{"name": "x", "qty": 2}\n```\n后记', schema=SCHEMA)
        assert r["ok"] is True and r["data"] == {"name": "x", "qty": 2}

    def test_stage2_missing_required_and_type(self):
        v = OutputValidator()
        r = v.validate_artifact('{"qty": "不是数字"}', schema=SCHEMA)
        assert r["ok"] is False
        details = " ".join(i["detail"] for i in r["issues"])
        assert "Missing required field: name" in details
        assert "Expected number" in details
        assert all(i["severity"] == "blocking" for i in r["issues"][:2])

    def test_stage2_enum_violation(self):
        v = OutputValidator()
        r = v.validate_artifact(
            '{"name": "x", "qty": 1, "status": "oops"}', schema=SCHEMA)
        assert r["ok"] is False and "not in enum" in r["issues"][0]["detail"]

    def test_stage2_min_max_are_minor(self):
        v = OutputValidator()
        r = v.validate_artifact('{"name": "x", "qty": 0}', schema=SCHEMA)
        assert r["ok"] is True                       # min/max 是建议不阻塞
        assert r["issues"] and r["issues"][0]["severity"] == "minor"

    def test_stage3_path_bounds_violations(self, tmp_path):
        v = OutputValidator()
        data = json.dumps({"out": "/etc/passwd", "rel": "../../escape.txt",
                           "ok": "产物/报告.md", "note": "普通散文不含路径"})
        r = v.validate_artifact(data, schema={"type": "object"},
                                base_path=str(tmp_path))
        assert r["ok"] is False
        vals = [b["value"] for b in r["path_bounds"]["violations"]]
        assert "/etc/passwd" in vals and "../../escape.txt" in vals
        assert "产物/报告.md" not in vals and "普通散文不含路径" not in vals

    def test_stage3_safe_relative_inside_base(self, tmp_path):
        v = OutputValidator()
        r = v.validate_artifact('{"p": "sub/dir/file.md"}', schema={"type": "object"},
                                base_path=str(tmp_path))
        assert r["ok"] is True and not r["path_bounds"]["violations"]

    def test_stage3_skipped_without_base_visible(self):
        v = OutputValidator()
        r = v.validate_artifact('{"p": "/etc/passwd"}', schema={"type": "object"})
        assert r["ok"] is True and r["path_bounds"]["skipped"] is True   # 跳过可见

    def test_stage4_repair_loop_shape(self):
        v = OutputValidator()
        r = v.validate_artifact("{}", schema=SCHEMA)
        assert set(r) >= {"ok", "blocking", "issues", "stage", "data",
                          "presets_used", "path_bounds"}
        assert all(set(i) == {"id", "severity", "detail"} for i in r["issues"])

    def test_ok_case(self):
        v = OutputValidator()
        r = v.validate_artifact('{"name": "x", "qty": 3, "status": "final"}',
                                schema=SCHEMA)
        assert r["ok"] is True and r["issues"] == [] and r["stage"] == "done"


class TestLooksLikePath:
    def test_prose_never_flagged(self):
        assert not _looks_like_path("普通文本")
        assert not _looks_like_path("和/或的关系")
        assert not _looks_like_path("rate limit reached")

    def test_path_shapes_flagged(self):
        assert _looks_like_path("/etc/passwd")
        assert _looks_like_path("./x.txt")
        assert _looks_like_path("../x.txt")
        assert _looks_like_path("a/../b")
        assert _looks_like_path("C:\\win\\path")
        assert _looks_like_path("~/home/file")


# ── ② 预置 Schema（插件任务配置映射面）───────────────────────────

class TestPresets:
    def test_register_get_list(self):
        v = OutputValidator()
        v.register_preset("ledger", SCHEMA)
        assert v.get_preset("ledger") == SCHEMA
        assert v.list_presets() == ["ledger"]
        assert v.get_preset("ghost") is None

    def test_validate_with_preset(self):
        v = OutputValidator()
        v.register_preset("ledger", SCHEMA)
        r = v.validate_artifact('{"name": "x", "qty": 1}', preset="ledger")
        assert r["ok"] is True and r["presets_used"] == "ledger"

    def test_unknown_preset_visible(self):
        v = OutputValidator()
        r = v.validate_artifact("{}", preset="ghost")
        assert r["ok"] is False and "预置 Schema 不存在" in r["issues"][0]["detail"]

    def test_preset_validation_errors(self):
        v = OutputValidator()
        with pytest.raises(ValueError):
            v.register_preset("", SCHEMA)
        with pytest.raises(ValueError):
            v.register_preset("x", "not-a-dict")

    def test_missing_schema_visible(self):
        v = OutputValidator()
        r = v.validate_artifact("{}")
        assert r["ok"] is False and "缺 schema" in r["issues"][0]["detail"]


# ── ③ 验收→修复成环（#3 repair_loop 天然对接）────────────────────

class TestRepairLoopIntegration:
    def test_report_feeds_repair_loop_step(self):
        v = OutputValidator()
        repair = repair_loop.RepairLoop(goal="交台账")
        r = v.validate_artifact('{"qty": 1}', schema=SCHEMA)   # 缺 name
        assert r["ok"] is False
        decision = repair.step({"ok": r["ok"], "blocking": r["blocking"],
                                "issues": r["issues"]})
        assert decision["action"] == "repair"
        assert "Missing required field: name" in decision["prompt"]

    def test_passing_report_accepted(self):
        v = OutputValidator()
        repair = repair_loop.RepairLoop()
        r = v.validate_artifact('{"name": "x", "qty": 1}', schema=SCHEMA)
        decision = repair.step({"ok": r["ok"], "blocking": r["blocking"],
                                "issues": r["issues"]})
        assert decision["action"] == "accept"


# ── ④ 工具面（guard_tools 治理组第四件）──────────────────────────

class TestToolSurface:
    def test_tool_names_and_defs(self):
        assert "json_validation" in TOOL_NAMES
        names = [d["function"]["name"] for d in tool_defs()]
        assert names == ["run_guard", "repair_loop", "file_baseline", "json_validation"]

    def test_tool_validate_pass(self, tmp_path):
        gt = GuardTools()
        out = gt.run("json_validation", {
            "action": "validate",
            "data": '{"name": "x", "qty": 2}',
            "schema": SCHEMA,
        }, "s1")
        assert "✅ 校验通过" in out
        machine = json.loads(out.split("validation）：\n", 1)[1])
        assert machine["ok"] is True

    def test_tool_validate_fail_feeds_repair(self, tmp_path):
        gt = GuardTools()
        out = gt.run("json_validation", {
            "action": "validate", "data": "{}", "schema": SCHEMA,
        }, "s1")
        assert "⚠️ 校验不过" in out and "Missing required field" in out
        machine = json.loads(out.split("validation）：\n", 1)[1])
        # 机器面直接喂 repair_loop（成环实证）
        decision = repair_loop.RepairLoop().step(machine)
        assert decision["action"] == "repair"

    def test_tool_preset_flow(self):
        gt = GuardTools()
        assert "已登记" in gt.run("json_validation", {
            "action": "register_preset", "name": "ledger", "schema": SCHEMA}, "s1")
        assert "ledger" in gt.run("json_validation", {"action": "list_presets"}, "s1")
        out = gt.run("json_validation", {
            "action": "validate", "data": '{"name": "x", "qty": 1}',
            "preset": "ledger"}, "s1")
        assert "✅ 校验通过" in out and "预置 ledger" in out

    def test_tool_path_bounds_message(self, tmp_path):
        gt = GuardTools()
        out = gt.run("json_validation", {
            "action": "validate",
            "data": json.dumps({"p": "/etc/passwd"}),
            "schema": {"type": "object"},
            "base_path": str(tmp_path),
        }, "s1")
        assert "路径越界" in out and "/etc/passwd" in out

    def test_tool_param_errors_visible(self):
        gt = GuardTools()
        assert "参数错误" in gt.run("json_validation", {"action": "validate"}, "s1")
        assert "参数错误" in gt.run("json_validation", {"action": "register_preset"}, "s1")
        assert "未知 action" in gt.run("json_validation", {"action": "boom"}, "s1")
