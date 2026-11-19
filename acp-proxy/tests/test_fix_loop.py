# -*- coding: utf-8 -*-
"""P1 测试驱动自主修复循环：四道验证闸门（测试红不交付）。

覆盖（拔高口径：对标 Claude Code/RooCode 级自主修复循环 + OMH 验证闸门）：
1. 复现闸：基线就绿 = 拒伪修复（没有红测试不许"修"）
2. 定向绿闸：修复后仍红 → 打回 + 三段式策略升级
   （normal→change_strategy→minimum→stop 诚实失败，绝不死循环、绝不假称完成）
3. 回归闸：定向绿但回归新增红（对比基线红名单）→ 打回回炉；预存红不阻塞
4. 报告闸：只有双绿出 fixed；失败报告诚实 failed + 复跑验收步骤
5. run() 全自主驱动（fix_fn 回调闭环）
6. 状态持久化 save/load（data/fix-loops/）
7. 端到端：真仓库真测试（tmp 库里真跑 pytest 子进程）走完整循环
"""
import json
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.fix_loop import (  # noqa: E402
    TestDrivenFixLoop,
    parse_failed_ids,
    PHASE_REPRODUCE,
    PHASE_FIX,
    PHASE_DONE,
    PHASE_FAILED,
)
from agent.soulmate_agent import SoulMateAgent  # noqa: E402


def _loop(tmp_path, runner, **kw):
    return TestDrivenFixLoop(
        tmp_path, test_cmd="pytest -q", repro_test="tests/test_bug.py::test_x",
        bug="数字格式化 off-by-one", runner=runner,
        state_dir=tmp_path / "fixstate", **kw)


def test_reproduce_gate_refuses_green_baseline(tmp_path):
    """复现闸：复现测试基线就绿 → 拒伪修复。"""
    loop = _loop(tmp_path, lambda cmd, cwd, timeout_s=600:
                 {"exit_code": 0, "output": "1 passed"})
    res = loop.start()
    assert res["ok"] is False and res["gate"] == "reproduce"
    assert "拒伪修复" in res["message"]
    assert loop.state.phase == PHASE_REPRODUCE  # 未获准进修复


def test_target_gate_blocks_red_and_escalates_strategy(tmp_path):
    """定向绿闸：永远红 → 策略升级 normal→change_strategy→minimum→诚实 stop。"""
    loop = _loop(tmp_path, lambda cmd, cwd, timeout_s=600:
                 {"exit_code": 1, "output": "FAILED tests/test_bug.py::test_x - assert 1 == 2"},
                 max_rounds=10)
    assert loop.start()["ok"] is True
    strategies = []
    for _ in range(8):
        r = loop.check()
        strategies.append(r["strategy"])
        if loop.state.phase == PHASE_FAILED:
            break
    assert loop.state.phase == PHASE_FAILED, "永不绿必须诚实失败（禁死循环/禁假称完成）"
    assert strategies[:2] == ["normal", "normal"]
    assert "change_strategy" in strategies and "minimum" in strategies
    rep = loop.report()
    assert rep["status"] == "failed" and rep["ok"] is False
    assert "复跑验收步骤" in rep["report"]


def test_regression_gate_blocks_new_failures(tmp_path):
    """回归闸：定向绿但回归新增红 → 打回；比对基线红名单（预存红不阻塞）。"""
    state = {"n": 0}

    def runner(cmd, cwd, timeout_s=600):
        state["n"] += 1
        if state["n"] == 1:
            # 基线回归：既有红 old_test（预存失败）
            return {"exit_code": 1, "output": "FAILED tests/test_old.py::test_old\n1 failed"}
        if state["n"] == 2:
            # 基线复现：红
            return {"exit_code": 1, "output": "FAILED tests/test_bug.py::test_x\n1 failed"}
        if state["n"] == 3:
            # 第1轮修复后定向：绿
            return {"exit_code": 0, "output": "1 passed"}
        if state["n"] == 4:
            # 第1轮回归：新增红（修复破坏别处）
            return {"exit_code": 1,
                    "output": "FAILED tests/test_bug.py::test_x\nFAILED tests/test_other.py::test_broken_new\n2 failed"}
        if state["n"] == 5:
            # 第2轮定向：绿
            return {"exit_code": 0, "output": "1 passed"}
        # 第2轮回归：只剩基线既有红 → 通过
        return {"exit_code": 1, "output": "FAILED tests/test_old.py::test_old\n1 failed"}

    loop = _loop(tmp_path, runner)
    assert loop.start()["ok"] is True
    assert loop.state.baseline_failed == ["tests/test_old.py::test_old"]
    r1 = loop.check()
    assert r1["ok"] is False and r1["gate"] == "regression"
    assert "test_broken_new" in json.dumps(r1.get("new_failed", []))
    r2 = loop.check()
    assert r2["ok"] is True and loop.state.phase == PHASE_DONE
    rep = loop.report()
    assert rep["status"] == "fixed"
    assert "tests/test_old.py::test_old" in rep["report"]  # 预存红如实列出


def test_report_gate_never_fixed_on_red(tmp_path):
    """报告闸：状态被绕改成 done 但历史末轮是红 → 报告照样降级 failed。"""
    loop = _loop(tmp_path, lambda cmd, cwd, timeout_s=600:
                 {"exit_code": 1, "output": "FAILED tests/test_bug.py::test_x"})
    loop.start()
    loop.check()
    loop.state.phase = PHASE_DONE   # 恶意/意外绕改
    rep = loop.report()
    assert rep["status"] == "failed", "报告闸自查：历史末轮红绝不写 fixed"


def test_run_autonomous_with_fix_fn(tmp_path):
    """run() 全自主：fix_fn 第2轮修好 → 双绿 → fixed 报告。"""
    state = {"n": 0}

    def runner(cmd, cwd, timeout_s=600):
        state["n"] += 1
        if state["n"] <= 3:  # 基线回归+基线复现+第1轮定向=红
            return {"exit_code": 1, "output": "FAILED tests/test_bug.py::test_x"}
        return {"exit_code": 0, "output": "5 passed"}

    fixes = []

    def fix_fn(ctx):
        fixes.append(ctx["strategy"])

    loop = _loop(tmp_path, runner)
    rep = loop.run(fix_fn)
    assert rep["status"] == "fixed"
    assert len(fixes) == 2 and fixes[0] == "normal"
    assert rep["rounds"] == 2


def test_run_autonomous_reports_honest_failure(tmp_path):
    """run() 全自主：永不绿 → failed 报告（不是假称完成）。"""
    loop = _loop(tmp_path, lambda cmd, cwd, timeout_s=600:
                 {"exit_code": 1, "output": "FAILED tests/test_bug.py::test_x"},
                 max_rounds=6)
    rep = loop.run(lambda ctx: None)
    assert rep["status"] == "failed"
    assert "禁假称完成" in rep["report"] or "failed" in rep["report"]


def test_state_persistence_roundtrip(tmp_path):
    loop = _loop(tmp_path, lambda cmd, cwd, timeout_s=600:
                 {"exit_code": 1, "output": "FAILED tests/test_bug.py::test_x"})
    loop.start()
    loop.check()
    loaded = TestDrivenFixLoop.load(loop.state.loop_id,
                                    state_dir=tmp_path / "fixstate")
    assert loaded.state.rounds == loop.state.rounds
    assert loaded.state.phase == PHASE_FIX
    assert loaded.state.baseline_failed == loop.state.baseline_failed


def test_parse_failed_ids():
    out = ("FAILED tests/a.py::test_1 - boom\n"
           "ERROR tests/b.py::test_2\n"
           "1 failed, 1 error")
    assert parse_failed_ids(out) == {"tests/a.py::test_1", "tests/b.py::test_2"}


def test_end_to_end_real_pytest(tmp_path):
    """端到端真刀：真仓库 + 真 pytest 子进程，预存失败测试走完整循环。

    小库 calc.py 有真 bug（median 对偶数长度取错元素），预存红测试
    test_bug.py::test_median_even 复现它；第1轮改错地方（定向仍红被拒），
    第2轮真修好（定向绿 + 回归对照基线无新增红 → fixed）。
    """
    (tmp_path / "calc.py").write_text(textwrap.dedent('''\
        def median(nums):
            s = sorted(nums)
            n = len(s)
            return s[n // 2]  # BUG: 偶数长度应取中间两元素均值
    '''))
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bug.py").write_text(textwrap.dedent('''\
        from calc import median

        def test_median_even():
            assert median([1, 2, 3, 4]) == 2.5

        def test_median_odd():
            assert median([3, 1, 2]) == 2
    '''))
    (tmp_path / "tests" / "test_other.py").write_text(textwrap.dedent('''\
        from calc import median

        def test_median_single():
            assert median([7]) == 7
    '''))
    loop = TestDrivenFixLoop(
        tmp_path, test_cmd=f"{sys.executable} -m pytest -q",
        repro_test="tests/test_bug.py::test_median_even",
        bug="median 偶数长度取错元素",
        state_dir=tmp_path / "fixstate")
    res = loop.start()
    assert res["ok"] is True and "复现闸通过" in res["message"]

    # 第1轮：改错地方（只加 docstring）→ 定向仍红 → 被拒
    calc = tmp_path / "calc.py"
    calc.write_text('"""stats helpers"""\n' + calc.read_text())
    r1 = loop.check()
    assert r1["ok"] is False and r1["gate"] == "target_green"

    # 第2轮：真修（偶数取中间两元素均值）→ 定向绿 + 回归绿 → done
    calc.write_text(textwrap.dedent('''\
        def median(nums):
            s = sorted(nums)
            n = len(s)
            if n % 2 == 0:
                return (s[n // 2 - 1] + s[n // 2]) / 2
            return s[n // 2]
    '''))
    r2 = loop.check()
    assert r2["ok"] is True and loop.state.phase == PHASE_DONE
    rep = loop.report()
    assert rep["status"] == "fixed"
    assert "test_median_even" in rep["report"]


# ── 工具面端到端（写了≠接线了）──────────────────────────────────

def test_fix_loop_dispatch_end_to_end(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b  # BUG\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_bug.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n")
    ag = SoulMateAgent.__new__(SoulMateAgent)
    ag._project_root = str(tmp_path)
    out = ag._fix_loop_dispatch({
        "action": "start", "repo_path": str(tmp_path),
        "test_cmd": f"{sys.executable} -m pytest -q",
        "repro_test": "tests/test_bug.py::test_add", "bug": "add 减法写错",
    })
    data = json.loads(out)
    assert data["ok"] is True and data["loop_id"]
    loop_id = data["loop_id"]
    # 修好后 check
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    out = ag._fix_loop_dispatch({"action": "check", "loop_id": loop_id})
    data = json.loads(out)
    assert data["ok"] is True and data["phase"] == PHASE_DONE
    out = ag._fix_loop_dispatch({"action": "report", "loop_id": loop_id})
    data = json.loads(out)
    assert data["status"] == "fixed"
    out = ag._fix_loop_dispatch({"action": "check", "loop_id": "nonexistent"})
    assert "未找到修复循环" in out
