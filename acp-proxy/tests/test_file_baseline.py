# -*- coding: utf-8 -*-
"""Pi 取长补短 #8——受保护文件基线（manifest+sha256 副本、settle 前还原）。

项目有文件截断/误改历史痛点；工作区工具纪律：任务输入文件登记基线，Agent 只许
动产物——settle 前 restore() 还原被改动/删除的受保护输入。整合面：
- edit_safety.py（既有编辑安全模块）扩 FileBaseline；
- guard_tools 治理工具组第三件 file_baseline（GUARD_TOOL_NAMES 自动双路分发）；
- settle 收尾边界（#2）内置钩子双相自动还原（before_settle + agent_settled）。

通用工作区保护能力（非投标专用）。
"""
import json

import pytest

from agent.edit_safety import (
    MANIFEST_NAME,
    FileBaseline,
    _safe_ns,
    baseline_restore_hook,
)
from agent.guard_tools import TOOL_NAMES, GuardTools, tool_defs
from tests.test_loop_guard_wiring import _wire_agent
from tests.test_steering import run


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    f1 = ws / "input.txt"
    f1.write_text("原始输入 v1", encoding="utf-8")
    f2 = ws / "template.md"
    f2.write_text("# 模板", encoding="utf-8")
    return ws


@pytest.fixture()
def baseline(tmp_path, workspace):
    return FileBaseline(baseline_dir=tmp_path / "baseline_store")


# ── ① FileBaseline 核心 ──────────────────────────────────────────

class TestRegister:
    def test_register_stores_manifest_and_copy(self, baseline, workspace):
        r = baseline.register([str(workspace / "input.txt"), str(workspace / "template.md")])
        assert len(r["registered"]) == 2 and r["errors"] == []
        assert baseline.manifest_path.exists()
        manifest = json.loads(baseline.manifest_path.read_text(encoding="utf-8"))
        key = str((workspace / "input.txt").resolve())
        assert manifest["files"][key]["sha256"]
        assert manifest["files"][key]["group"] == "input"
        # 内容副本落 files/<sha256>（工作区外）
        stored = baseline.baseline_dir / manifest["files"][key]["stored"]
        assert stored.read_text(encoding="utf-8") == "原始输入 v1"

    def test_register_idempotent_and_re_register_on_change(self, baseline, workspace):
        p = workspace / "input.txt"
        r1 = baseline.register([str(p)])
        r2 = baseline.register([str(p)])
        assert r2["registered"] == [] and r2["unchanged"] == [str(p.resolve())]
        p.write_text("改了", encoding="utf-8")
        r3 = baseline.register([str(p)])              # 内容变=重新登记新基线
        assert r3["registered"] == [str(p.resolve())]

    def test_register_missing_file_visible(self, baseline, tmp_path):
        r = baseline.register([str(tmp_path / "ghost.txt")])
        assert r["registered"] == []
        assert r["errors"] and "文件不存在" in r["errors"][0]["error"]

    def test_register_accepts_single_path(self, baseline, workspace):
        r = baseline.register(str(workspace / "input.txt"))
        assert r["total"] == 1

    def test_manifest_survives_reload(self, tmp_path, workspace):
        store = tmp_path / "baseline_store"
        b1 = FileBaseline(baseline_dir=store)
        b1.register([str(workspace / "input.txt")])
        b2 = FileBaseline(baseline_dir=store)          # 重启后加载同一 manifest
        assert b2.check()["total"] == 1 and b2.check()["ok"] is True


class TestCheck:
    def test_unchanged_ok(self, baseline, workspace):
        baseline.register([str(workspace / "input.txt")])
        c = baseline.check()
        assert c == {"ok": True, "matched": 1, "modified": [], "deleted": [], "total": 1}

    def test_modified_detected(self, baseline, workspace):
        p = workspace / "input.txt"
        baseline.register([str(p)])
        p.write_text("Agent 动了输入", encoding="utf-8")
        c = baseline.check()
        assert c["ok"] is False and c["modified"] == [str(p.resolve())]

    def test_deleted_detected(self, baseline, workspace):
        p = workspace / "input.txt"
        baseline.register([str(p)])
        p.unlink()
        c = baseline.check()
        assert c["ok"] is False and c["deleted"] == [str(p.resolve())]

    def test_group_filter(self, baseline, workspace):
        baseline.register([str(workspace / "input.txt")], group="input")
        baseline.register([str(workspace / "template.md")], group="template")
        (workspace / "input.txt").write_text("动了", encoding="utf-8")
        assert baseline.check(group="template")["ok"] is True
        assert baseline.check(group="input")["ok"] is False


class TestRestore:
    def test_restore_modified_and_deleted(self, baseline, workspace):
        p1, p2 = workspace / "input.txt", workspace / "template.md"
        baseline.register([str(p1), str(p2)])
        p1.write_text("被改坏了", encoding="utf-8")     # 改动
        p2.unlink()                                    # 删除
        r = baseline.restore()
        assert str(p1.resolve()) in r["restored_modified"]
        assert str(p2.resolve()) in r["restored_deleted"]
        assert p1.read_text(encoding="utf-8") == "原始输入 v1"   # 还原到基线
        assert p2.read_text(encoding="utf-8") == "# 模板"
        assert r["ok"] is True

    def test_restore_idempotent(self, baseline, workspace):
        p = workspace / "input.txt"
        baseline.register([str(p)])
        p.write_text("坏", encoding="utf-8")
        r1 = baseline.restore()
        r2 = baseline.restore()
        assert r1["restored_modified"] and not r2["restored_modified"]
        assert r2["unchanged"] == 1

    def test_restore_missing_copy_visible(self, baseline, workspace):
        p = workspace / "input.txt"
        baseline.register([str(p)])
        (baseline.baseline_dir / baseline._manifest["files"][str(p.resolve())]["stored"]).unlink()
        p.write_text("坏", encoding="utf-8")
        r = baseline.restore()
        assert r["errors"] and "副本缺失" in r["errors"][0]["error"]

    def test_restore_group_filter(self, baseline, workspace):
        p1, p2 = workspace / "input.txt", workspace / "template.md"
        baseline.register([str(p1)], group="input")
        baseline.register([str(p2)], group="template")
        p1.write_text("坏1", encoding="utf-8")
        p2.write_text("坏2", encoding="utf-8")
        r = baseline.restore(group="input")
        assert len(r["restored_modified"]) == 1
        assert p1.read_text(encoding="utf-8") == "原始输入 v1"
        assert p2.read_text(encoding="utf-8") == "坏2"          # 不在组内不动

    def test_status_shape(self, baseline, workspace):
        baseline.register([str(workspace / "input.txt")], group="input")
        s = baseline.status()
        assert s["total"] == 1 and s["groups"] == {"input": 1}
        assert s["check"]["ok"] is True and s["baseline_dir"]

    def test_unregister(self, baseline, workspace):
        p = workspace / "input.txt"
        baseline.register([str(p)])
        r = baseline.unregister(str(p))
        assert r["unregistered"] is True
        p.write_text("随便改", encoding="utf-8")
        assert baseline.check()["ok"] is True            # 解除登记后不再保护
        assert baseline.unregister(str(p))["unregistered"] is False


class TestSafeNs:
    def test_traversal_sanitized(self):
        assert _safe_ns("../../etc/passwd") == ".._.._etc_passwd"
        assert _safe_ns("") == "default"

    def test_default_baseline_dir_uses_safe_ns(self):
        b = FileBaseline(ns="sess/../x")
        assert "/" not in b.baseline_dir.name
        assert b.baseline_dir.name == "sess_.._x"      # 分隔符被消毒，不逃出基线根


# ── ② settle 挂点适配（#2×#8：settle 前还原）──────────────────────

class TestBaselineRestoreHook:
    def test_hook_restores_with_visible_reason(self, baseline, workspace):
        p = workspace / "input.txt"
        baseline.register([str(p)])
        p.write_text("被改", encoding="utf-8")
        out = baseline_restore_hook(baseline)({})
        assert out["continue"] is False
        assert "文件基线还原 1 个受保护输入" in out["reason"] and "input.txt" in out["reason"]
        assert p.read_text(encoding="utf-8") == "原始输入 v1"

    def test_hook_noop_when_clean(self, baseline, workspace):
        baseline.register([str(workspace / "input.txt")])
        assert baseline_restore_hook(baseline)({}) is None   # 静默 no-op 不记账

    def test_hook_lazy_provider(self, baseline, workspace):
        p = workspace / "input.txt"
        baseline.register([str(p)])
        p.write_text("被改", encoding="utf-8")
        box = {"b": None}
        hook = baseline_restore_hook(lambda ctx: box["b"])
        assert hook({}) is None                            # 未登记：延迟解析到 None
        box["b"] = baseline
        out = hook({})
        assert out and "还原 1 个" in out["reason"]

    def test_hook_provider_none(self):
        assert baseline_restore_hook(lambda ctx: None)({}) is None


# ── ③ 治理工具组第三件 file_baseline（工具面）────────────────────

class TestGuardToolsBaseline:
    def test_tool_names_and_defs(self):
        assert "file_baseline" in TOOL_NAMES
        names = [d["function"]["name"] for d in tool_defs()]
        # Pi #11 后治理组四件套（json_validation 验收校验器入列）
        assert names == ["run_guard", "repair_loop", "file_baseline", "json_validation"]

    def _tools(self, tmp_path):
        return GuardTools(baseline_root=tmp_path / "baseline_root")

    def test_tool_register_check_restore_flow(self, tmp_path, workspace):
        gt = self._tools(tmp_path)
        p = str(workspace / "input.txt")
        out = gt.run("file_baseline", {"action": "register", "paths": [p]}, "s1")
        assert "已登记 1 个受保护文件" in out
        out = gt.run("file_baseline", {"action": "check"}, "s1")
        assert "基线比对通过" in out
        (workspace / "input.txt").write_text("改了", encoding="utf-8")
        out = gt.run("file_baseline", {"action": "check"}, "s1")
        assert "被改动" in out and "input.txt" in out
        out = gt.run("file_baseline", {"action": "restore"}, "s1")
        assert "已还原（改动）" in out
        assert (workspace / "input.txt").read_text(encoding="utf-8") == "原始输入 v1"

    def test_tool_status_and_unregister(self, tmp_path, workspace):
        gt = self._tools(tmp_path)
        p = str(workspace / "input.txt")
        gt.run("file_baseline", {"action": "register", "paths": [p], "group": "tpl"}, "s1")
        out = gt.run("file_baseline", {"action": "status"}, "s1")
        assert '"total": 1' in out and '"tpl": 1' in out
        out = gt.run("file_baseline", {"action": "unregister", "path": p}, "s1")
        assert "已解除登记" in out

    def test_tool_param_errors_visible(self, tmp_path):
        gt = self._tools(tmp_path)
        assert "参数错误" in gt.run("file_baseline", {"action": "register"}, "s1")
        assert "参数错误" in gt.run("file_baseline", {"action": "unregister"}, "s1")
        assert "未知 action" in gt.run("file_baseline", {"action": "explode"}, "s1")

    def test_restore_baseline_noop_without_state(self, tmp_path):
        gt = self._tools(tmp_path)
        assert gt.restore_baseline("never-used") is None   # 不凭空建状态
        assert gt.baseline_for("never-used") is None


# ── ④ settle 集成（#2 内置钩子：settle 前自动还原）────────────────

class TestSettleIntegration:
    def test_settle_gate_auto_restores(self, tmp_path, workspace):
        """settle 前自动还原被改动的受保护输入（内置钩子，无注册也生效）。"""
        agent = _wire_agent(tmp_path, [])
        gt = GuardTools(baseline_root=tmp_path / "baseline_root")
        agent._guard_tools_inst = gt
        p = workspace / "input.txt"
        gt.run("file_baseline", {"action": "register", "paths": [str(p)]}, "s1")
        p.write_text("Agent 偷改输入", encoding="utf-8")

        gate = agent._settle_gate_for("s1")
        out = gate.before_settle({})
        assert out["settled"] is True                     # 还原不是续轮
        assert "文件基线还原" in out["reason"]            # 还原可见禁静默
        assert p.read_text(encoding="utf-8") == "原始输入 v1"

    def test_settled_phase_double_insurance(self, tmp_path, workspace):
        """终态 agent_settled 兜底还原（硬终止路径绕过 before_settle 也能保）。"""
        agent = _wire_agent(tmp_path, [])
        gt = GuardTools(baseline_root=tmp_path / "baseline_root")
        agent._guard_tools_inst = gt
        p = workspace / "input.txt"
        gt.run("file_baseline", {"action": "register", "paths": [str(p)]}, "s1")
        p.write_text("改坏", encoding="utf-8")

        gate = agent._settle_gate_for("s1")
        gate.mark_settled({"session_id": "s1"})           # 终态钩子还原
        assert p.read_text(encoding="utf-8") == "原始输入 v1"

    def test_no_baseline_zero_behavior(self, tmp_path):
        agent = _wire_agent(tmp_path, [])
        agent._guard_tools_inst = GuardTools(baseline_root=tmp_path / "b")
        gate = agent._settle_gate_for("s1")
        out = gate.before_settle({})
        assert out["settled"] is True and out["reason"] == ""   # 零登记零行为

    def test_end_to_end_via_real_loop(self, tmp_path, workspace):
        """真实工具循环：回合收尾 settle 自动还原被改动的输入（E2E）。"""
        agent = _wire_agent(tmp_path, [["处理完成"]])
        gt = GuardTools(baseline_root=tmp_path / "baseline_root")
        agent._guard_tools_inst = gt
        p = workspace / "input.txt"
        gt.run("file_baseline", {"action": "register", "paths": [str(p)]}, "s1")
        p.write_text("循环里被改坏", encoding="utf-8")

        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "go"}], "s1"))
        assert "处理完成" in resp
        assert p.read_text(encoding="utf-8") == "原始输入 v1"   # settle 前还原了

    def test_wiring_pins(self):
        import inspect
        import agent.soulmate_agent as sma
        src = inspect.getsource(sma.SoulMateAgent)
        assert 'gate.register(baseline_hook, name="baseline_restore")' in src
        assert 'gate.register_settled(baseline_hook, name="baseline_restore")' in src
        assert "self._guard_tools_inst" in src
