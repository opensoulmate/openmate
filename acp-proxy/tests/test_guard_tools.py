# -*- coding: utf-8 -*-
"""Pi 取长补短落地——防跑飞三件套（run_guard）/ task_complete 免总结
（summary_control）/ 三段式修复循环（repair_loop）/ 工具面（guard_tools）。

通用任务治理能力（长文档/长流水线/子代理全场景复用，非投标专用）。
"""
import json

import pytest

from agent import repair_loop, run_guard, summary_control
from agent.guard_tools import GuardTools, tool_defs


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


# ── run_guard 防跑飞三件套 ──────────────────────────────────────────

def test_run_guard_max_turns_trip():
    g = run_guard.RunGuard(max_turns=3, now=FakeClock())
    for i in range(2):
        assert g.record_turn("read_file", {"path": f"/f{i}"})["verdict"] == "ok"
    chk = g.record_turn("read_file", {"path": "/f2"})
    assert chk["verdict"] == "stop" and chk["reason"] == "max_turns"


def test_run_guard_no_progress_watchdog_with_pause():
    clock = FakeClock()
    g = run_guard.RunGuard(no_progress_timeout=480, now=clock)
    g.record_turn("bash", {"cmd": "build"})          # 进展
    clock.advance(300)
    assert g.check()["verdict"] == "ok"
    g.pause()                                        # ask-user 等待期暂停
    clock.advance(1000)
    assert g.check()["verdict"] == "ok"              # 暂停期不计
    g.resume()
    clock.advance(200)                               # 恢复后再 200s 无进展
    chk = g.check()
    assert chk["verdict"] == "stop" and chk["reason"] == "no_progress"
    g.record_progress()
    assert g.check()["verdict"] == "ok"              # 标进展重置


def test_run_guard_stall_diagnosis_and_record():
    g = run_guard.RunGuard(stall_threshold=3, now=FakeClock())
    for _ in range(3):
        g.record_turn("write_file", {"path": "/same", "content": "x"})
    d = g.diagnose()
    assert d["stalled"] is True
    assert d["recommendation"] in ("wrap_up", "stop")
    chk = g.check()
    assert chk["reason"] == "stalled"
    # 会话摘录（诊断子任务输入）：JSONL 逐行可解析
    rec = g.extract_session_record()
    rows = [json.loads(ln) for ln in rec.splitlines()]
    assert len(rows) == 3 and rows[0]["tool"] == "write_file"
    # 重复后出现新签名 → 不再判空转
    g.record_turn("send_file", {"path": "/out"})
    assert g.diagnose()["stalled"] is False


def test_run_guard_validates_args():
    with pytest.raises(ValueError):
        run_guard.RunGuard(max_turns=0)
    with pytest.raises(ValueError):
        run_guard.RunGuard(no_progress_timeout=0)
    with pytest.raises(ValueError):
        run_guard.RunGuard(stall_threshold=1)


# ── summary_control task_complete 免总结 ────────────────────────────

def test_summary_control_batch_terminate_semantics():
    sc = summary_control.SummaryControl()
    assert sc.batch_terminate() is False                     # 空批不提前收
    sc.record({"task_complete": True, "action": "compile"}, "产物A")
    sc.record({"task_complete": True}, "产物B")
    assert sc.batch_terminate() is True                      # 整批全 true
    d = sc.consume()
    assert d["terminate"] is True and d["count"] == 2
    assert "产物A" in d["final_text"] and "产物B" in d["final_text"]
    assert sc.batch_terminate() is False                     # consume 后复位

    # 混批（一个没带）→ 不整批 → 不提前收（防半批胡写交差）
    sc.start_batch()
    sc.record({"task_complete": True}, "A")
    sc.record({"action": "read"}, "B")
    assert sc.batch_terminate() is False

    # 新批显式重置
    sc.start_batch()
    sc.record({"task_complete": "true"}, "C")                # 真值宽容
    assert sc.batch_terminate() is True


def test_summary_control_param_and_strip():
    schema = summary_control.param_schema()
    assert schema["task_complete"]["type"] == "boolean"
    args = {"action": "compile", "task_complete": True}
    assert summary_control.strip_meta(args) == {"action": "compile"}
    assert args["task_complete"] is True                     # 原字典不动


# ── repair_loop 三段式修复 ──────────────────────────────────────────

def _val(ids, blocking=(), ok=False):
    return {"ok": ok,
            "blocking": list(blocking),
            "issues": [{"id": i, "severity":
                        "blocking" if i in blocking else "minor",
                        "detail": f"问题 {i}"} for i in ids]}


def test_repair_loop_three_stage_progression_to_stop():
    """阻塞项持续不修：normal→change-strategy→minimum→stop（失败必须可见）。"""
    r = repair_loop.RepairLoop(goal="成稿验收")
    d1 = r.step(_val(["a"], blocking=["a"]))
    assert d1["action"] == "repair" and d1["mode"] == "normal"
    assert "逐条修复" in d1["prompt"]
    # 三轮同问题（无进展）→ 换方法
    for _ in range(2):
        d = r.step(_val(["a"], blocking=["a"]))
    assert d["mode"] == "change_strategy"
    assert "换一种方法" in d["prompt"]
    for _ in range(3):
        d = r.step(_val(["a"], blocking=["a"]))
    assert d["mode"] == "minimum"
    assert "只修复阻塞项" in d["prompt"]
    for _ in range(3):
        d = r.step(_val(["a"], blocking=["a"]))
    assert d["action"] == "stop"                            # minimum 仍失败


def test_repair_loop_stalled_accept_with_legacy():
    """stalled≥2 且无阻塞 → 带遗留接受（不烧 token 死磕）。"""
    r = repair_loop.RepairLoop()
    r.step(_val(["a"], blocking=["a"]))
    d2 = r.step(_val(["b", "c"]))                           # 进展（阻塞已修）
    assert d2["action"] == "repair"
    d3 = r.step(_val(["b", "c"]))                           # 无进展 1
    assert d3["action"] == "repair"
    d4 = r.step(_val(["b", "c"]))                           # 无进展 2 → 接受
    assert d4["action"] == "accept"
    assert "遗留" in d4["reason"]


def test_repair_loop_accept_and_string_issues():
    r = repair_loop.RepairLoop()
    assert r.step({"ok": True})["action"] == "accept"
    r2 = repair_loop.RepairLoop()
    d = r2.step({"ok": False, "issues": ["格式不统一", "缺附录"]})
    assert d["action"] == "repair"
    assert "格式不统一" in d["prompt"]
    with pytest.raises(ValueError):
        r2.step("not a dict")
    with pytest.raises(ValueError):
        r2.build_prompt("bogus", [])


# ── guard_tools 工具面 ──────────────────────────────────────────────

def test_guard_tools_defs_shape():
    defs = tool_defs()
    names = [d["function"]["name"] for d in defs]
    assert names == ["run_guard", "repair_loop"]
    for d in defs:
        props = d["function"]["parameters"]["properties"]
        assert "task_complete" in props                      # 免总结参数面
        assert "action" in props


def test_guard_tools_run_guard_actions():
    t = GuardTools(now=FakeClock())
    out = t.run("run_guard", {"action": "configure", "max_turns": 5})
    assert "轮数上限 5" in out
    out = t.run("run_guard", {"action": "beat", "tool": "docx_compile",
                              "args": {"filename": "x"}, "task_complete": True})
    assert "verdict=ok" in out                               # 元参数被剔除不炸
    t.run("run_guard", {"action": "beat", "tool": "docx_compile",
                        "args": {"filename": "x"}})
    t.run("run_guard", {"action": "beat", "tool": "docx_compile",
                        "args": {"filename": "x"}})
    out = t.run("run_guard", {"action": "diagnose"})
    assert "空转诊断" in out
    out = t.run("run_guard", {"action": "extract"})
    assert "会话摘录" in out
    out = t.run("run_guard", {"action": "status"})
    assert "max_turns" in out
    out = t.run("run_guard", {"action": "pause"})
    assert "暂停" in out
    assert "❌" in t.run("run_guard", {"action": "bogus"})


def test_guard_tools_repair_loop_actions():
    t = GuardTools()
    out = t.run("repair_loop", {"action": "start", "goal": "课件成稿"})
    assert "normal→change-strategy→minimum" in out
    out = t.run("repair_loop", {"action": "step", "validation": {
        "ok": False, "blocking": ["a"],
        "issues": [{"id": "a", "severity": "blocking", "detail": "工期矛盾"}]}})
    assert "repair" in out and "修复指令·normal" in out
    out = t.run("repair_loop", {"action": "step", "validation": {
        "ok": False, "issues": [{"id": "a", "detail": "工期矛盾"}]}})
    assert "accept" in out or "repair" in out
    out = t.run("repair_loop", {"action": "status"})
    assert "stall_count" in out
    assert "❌" in t.run("repair_loop", {"action": "step"})
    assert "❌" in t.run("nope", {})
    # 会话隔离：另一会话状态独立
    out = t.run("repair_loop", {"action": "status"}, session_id="other")
    assert json.loads(out.split("\n", 1)[1])["rounds"] == 0
