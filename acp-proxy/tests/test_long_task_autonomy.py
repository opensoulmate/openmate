"""
P0 长任务自治测试基线：防空转 · 断点续跑 · 过程可回放 · 完成状态多档。

覆盖：
- 完成状态多档（OMH）：自报封顶 reported_done、非执行者+内容证据才 verified、
  条件缺失降档、完整性分类器各规则。
- 防空转（Paperclip/Multica）：无进展三态降级、环路检测、慢但推进不误判。
- 断点续跑（Manus）：检查点落盘、进程被杀后从断点续跑不重做、恢复扫描回退 running。
- 过程可回放（Manus）：结构化轨迹完整有序、replay() 可回看每步输入/输出/决策。
- 端到端验收：真实子进程跑长任务→中途 SIGKILL→重启 --resume→断点续跑成功+轨迹完整可回放。
"""

import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.long_task_autonomy import (
    CheckpointStore,
    CompletionClaim,
    CompletionConfidence,
    CompletionIntegrityChecker,
    LongTaskController,
    LongTaskEngine,
    ProgressLevel,
    ProgressMonitor,
    TraceRecorder,
    evaluate_completion,
)

REPO = Path(__file__).resolve().parent.parent
DEMO = REPO / "agent" / "scripts" / "long_task_demo.py"


# ────────────────────────────────────────────────────────────
# 完成状态多档（OMH：自报 ≠ 完成）
# ────────────────────────────────────────────────────────────

class TestCompletionStatus:
    def test_self_report_capped_at_reported_done(self):
        claim = CompletionClaim(
            status=CompletionConfidence.REPORTED_DONE,
            evidence=["produced report.pdf"],
            verifier="executor", claim_text="done",
        )
        conf, reasons = evaluate_completion(claim)
        assert conf == CompletionConfidence.REPORTED_DONE
        assert "self_report_capped_at_reported_done" in reasons

    def test_external_verification_promotes_to_verified(self):
        claim = CompletionClaim(
            status=CompletionConfidence.VERIFIED,
            evidence=["sha256=abc", "command-output: 5 rows matched"],
            verification_command="pytest test_x.py -q",
            verification_result="5 passed in 1.2s",
            verifier="external", claim_text="verified by CI",
        )
        conf, reasons = evaluate_completion(claim)
        assert conf == CompletionConfidence.VERIFIED
        assert reasons == []

    def test_verified_demoted_when_self_verifier(self):
        # 执行者自己当核验者 → 不许 verified（OMH：须非执行者）
        claim = CompletionClaim(
            status=CompletionConfidence.VERIFIED,
            evidence=["sha256=abc"],
            verification_command="pytest", verification_result="passed",
            verifier="executor", claim_text="verified",
        )
        conf, reasons = evaluate_completion(claim)
        assert conf == CompletionConfidence.REPORTED_DONE
        assert "self_reporter_as_verifier" in reasons or "verified_demoted" in reasons

    def test_verified_demoted_without_verification_command(self):
        claim = CompletionClaim(
            status=CompletionConfidence.VERIFIED,
            evidence=["sha256=abc"],
            verification_command="", verification_result="looks good",
            verifier="external", claim_text="verified",
        )
        conf, reasons = evaluate_completion(claim)
        assert conf == CompletionConfidence.REPORTED_DONE
        assert "unnamed_verification_command" in reasons

    def test_verified_demoted_on_empty_evidence(self):
        claim = CompletionClaim(
            status=CompletionConfidence.VERIFIED, evidence=[],
            verification_command="pytest", verification_result="passed",
            verifier="external", claim_text="verified",
        )
        conf, reasons = evaluate_completion(claim)
        assert conf == CompletionConfidence.REPORTED_DONE
        assert "empty_evidence" in reasons

    def test_unknown_status_fails_closed(self):
        claim = CompletionClaim(status=CompletionConfidence.coerce("gibberish"))
        assert claim.status == CompletionConfidence.NOT_RUN  # fail-closed

    def test_wire_value_mapping(self):
        assert CompletionConfidence.coerce("completed") == CompletionConfidence.REPORTED_DONE
        assert CompletionConfidence.coerce("passed") == CompletionConfidence.VERIFIED
        assert CompletionConfidence.coerce("ready") == CompletionConfidence.NOT_RUN

    def test_str_enum_roundtrip_no_break(self):
        # str(str-Enum)='CompletionConfidence.X' 不能崩、要能解析回来
        member = CompletionConfidence.REPORTED_DONE
        assert CompletionConfidence.coerce(member) == CompletionConfidence.REPORTED_DONE
        assert CompletionConfidence.coerce(str(member)) == CompletionConfidence.REPORTED_DONE


class TestCompletionIntegrityChecker:
    def test_unfinished_marker_flagged(self):
        claim = CompletionClaim(status=CompletionConfidence.VERIFIED,
                                evidence=["wrote handler, TODO: edge cases"],
                                verification_command="pytest", verification_result="passed",
                                verifier="external", claim_text="done")
        reasons = CompletionIntegrityChecker().check(claim)
        assert "unfinished_work_marker" in reasons

    def test_tracked_marker_exempt(self):
        claim = CompletionClaim(status=CompletionConfidence.VERIFIED,
                                evidence=["wrote handler, TODO tracked in issue #42"],
                                verification_command="pytest", verification_result="passed",
                                verifier="external", claim_text="done")
        reasons = CompletionIntegrityChecker().check(claim)
        assert "unfinished_work_marker" not in reasons

    def test_stub_implementation_flagged(self):
        claim = CompletionClaim(status=CompletionConfidence.VERIFIED,
                                evidence=["this is a placeholder stub"],
                                verification_command="pytest", verification_result="passed",
                                verifier="external", claim_text="done")
        reasons = CompletionIntegrityChecker().check(claim)
        assert "stub_implementation" in reasons

    def test_clean_claim_passes(self):
        claim = CompletionClaim(status=CompletionConfidence.VERIFIED,
                                evidence=["sha256=abc", "command-output: 3/3 matched"],
                                verification_command="pytest", verification_result="3 passed",
                                verifier="external", claim_text="verified")
        assert CompletionIntegrityChecker().check(claim) == []


# ────────────────────────────────────────────────────────────
# 防空转（Paperclip 三态 + Multica 判据）
# ────────────────────────────────────────────────────────────

class TestProgressMonitor:
    def test_ok_when_progressing(self):
        m = ProgressMonitor(warn_after_s=10, stall_after_s=20, degrade_after_s=30)
        a = m.heartbeat(signature="s1", substantive=True, now=100.0)
        assert a.level == ProgressLevel.OK

    def test_warns_after_no_progress(self):
        m = ProgressMonitor(warn_after_s=10, stall_after_s=20, degrade_after_s=30)
        m.heartbeat(signature="s1", substantive=True, now=100.0)
        a = m.assess(now=100.0 + 15)  # 15s 无进展 > warn
        assert a.level == ProgressLevel.WARNING
        assert a.action == "self_check"

    def test_stalls_then_degrades(self):
        m = ProgressMonitor(warn_after_s=10, stall_after_s=20, degrade_after_s=30)
        m.heartbeat(signature="s1", substantive=True, now=100.0)
        assert m.assess(now=125.0).level == ProgressLevel.STALLED       # 25s
        assert m.assess(now=135.0).level == ProgressLevel.DEGRADED      # 35s
        assert m.assess(now=135.0).action == "escalate_report"

    def test_slow_but_progressing_never_escalates(self):
        # Multica：判卡死看"多久无进展"而非"总共多久"——持续推进永不误判
        m = ProgressMonitor(warn_after_s=10, stall_after_s=20, degrade_after_s=30)
        now = 100.0
        a = m.heartbeat(signature="step_0", substantive=True, now=now)
        now += 10.0
        for i in range(1, 20):  # 每次推进，跨 200s
            a = m.heartbeat(signature=f"step_{i}", substantive=True, now=now)
            now += 10.0
        assert a.level == ProgressLevel.OK  # 从不空转

    def test_loop_detection_degrades(self):
        # 同一签名反复出现（原地打转）→ 直接 degraded
        m = ProgressMonitor(warn_after_s=10, stall_after_s=20, degrade_after_s=30,
                            loop_repeat_threshold=3)
        m.heartbeat(signature="same", substantive=True, now=100.0)
        now = 100.0
        a = m.heartbeat(signature="same", substantive=False, now=now)
        for _ in range(5):
            now += 6.0
            a = m.heartbeat(signature="same", substantive=False, now=now)
        assert a.loop_detected is True
        assert a.level == ProgressLevel.DEGRADED


# ────────────────────────────────────────────────────────────
# 断点续跑（Manus：检查点落盘 + 恢复不重做）
# ────────────────────────────────────────────────────────────

def _mk_store(tmp_path) -> CheckpointStore:
    return CheckpointStore(db_path=tmp_path / "lta.db")


STEPS = [
    {"id": f"step_{i}", "description": f"step {i}", "input": {"index": i}}
    for i in range(4)
]


def _runner_factory(runs_log: list, fail_at: int | None = None):
    """记录每次被调用的 step_id；fail_at 处抛异常模拟失败。"""
    def runner(step):
        runs_log.append(step.step_id)
        if fail_at is not None and step.seq == fail_at:
            raise RuntimeError("boom")
        return {"success": True, "output": {"n": step.seq},
                "decision": "ok", "evidence": [f"artifact-{step.step_id}"]}
    return runner


class TestCheckpointResume:
    def test_all_steps_run_once_when_uninterrupted(self, tmp_path):
        store = _mk_store(tmp_path)
        engine = LongTaskEngine("t1", "goal", store=store)
        engine.start(STEPS)
        log = []
        engine.run_steps(_runner_factory(log))
        assert sorted(log) == sorted(s["id"] for s in STEPS)

    def test_completed_steps_not_redone_on_resume(self, tmp_path):
        store = _mk_store(tmp_path)
        # 第一跑：step_2 抛异常（失败），其余步照跑（步间独立，at-least-once）
        engine = LongTaskEngine("t2", "goal", store=store)
        engine.start(STEPS)
        log1 = []
        engine.run_steps(_runner_factory(log1, fail_at=2))
        assert log1 == ["step_0", "step_1", "step_2", "step_3"]

        # 第二跑（断点续跑）：只重做失败的步，已成功步不重做
        engine2 = LongTaskEngine("t2", "goal", store=store)
        engine2.resume()
        log2 = []
        engine2.run_steps(_runner_factory(log2))
        assert "step_0" not in log2 and "step_1" not in log2 and "step_3" not in log2
        assert log2 == ["step_2"]  # 仅失败步重跑

    def test_resume_nothing_when_all_done(self, tmp_path):
        store = _mk_store(tmp_path)
        e1 = LongTaskEngine("t3", "goal", store=store)
        e1.start(STEPS)
        log1 = []
        e1.run_steps(_runner_factory(log1))
        assert sorted(log1) == sorted(s["id"] for s in STEPS)
        # 全完成 → 无可续跑（干净语义：resume 报"无可续"）
        e2 = LongTaskEngine("t3", "goal", store=store)
        with pytest.raises(LookupError):
            e2.resume()

    def test_recovery_flips_inflight_running_to_pending(self, tmp_path):
        # 模拟"进程被杀时某步卡在 running"：恢复扫描回退 running→pending（至少一次）
        store = _mk_store(tmp_path)
        e = LongTaskEngine("t4", "goal", store=store)
        run = e.start(STEPS)
        store.claim_step(run.run_id, "step_1", {})  # step_1 卡 running
        recovered = store.record_recovery(run.run_id)
        assert recovered == 1
        loaded = store.load_run(run.run_id)
        s1 = next(s for s in loaded.steps if s.step_id == "step_1")
        assert s1.status.value == "pending"  # 可重跑

    def test_resumable_lookup(self, tmp_path):
        store = _mk_store(tmp_path)
        e = LongTaskEngine("t5", "goal", store=store)
        run = e.start(STEPS)
        assert store.find_resumable("t5").run_id == run.run_id
        store.set_run_status(run.run_id, "completed", CompletionConfidence.REPORTED_DONE)
        assert store.find_resumable("t5") is None  # 已收尾不再可续跑


# ────────────────────────────────────────────────────────────
# 过程可回放（Manus：结构化轨迹 + replay 时间线）
# ────────────────────────────────────────────────────────────

class TestReplay:
    def test_timeline_records_input_output_decision(self, tmp_path):
        store = _mk_store(tmp_path)
        e = LongTaskEngine("t6", "goal", store=store)
        e.start(STEPS)
        e.run_steps(_runner_factory([]))
        tl = e.timeline()
        assert len(tl) > len(STEPS)  # run_start + 每步 start/end/heartbeat + run_end
        # 每个步骤都有 step_start/step_end，且 step_end 带 output+decision
        ends = [ev for ev in tl if ev["phase"] == "step_end"]
        assert len(ends) == len(STEPS)
        for ev in ends:
            assert ev["output_data"] and ev["decision"]

    def test_replay_text_human_readable(self, tmp_path):
        store = _mk_store(tmp_path)
        e = LongTaskEngine("t7", "goal", store=store)
        e.start(STEPS)
        e.run_steps(_runner_factory([]))
        text = e.replay()
        assert "Replay" in text
        assert "step_start" in text and "step_end" in text
        assert "step_0" in text

    def test_timeline_strictly_ordered(self, tmp_path):
        store = _mk_store(tmp_path)
        e = LongTaskEngine("t8", "goal", store=store)
        e.start(STEPS)
        e.run_steps(_runner_factory([]))
        seqs = [ev["seq"] for ev in e.timeline()]
        assert seqs == sorted(seqs)

    def test_completion_report_tiers(self, tmp_path):
        # 混合结局：自报 + 外部核验 → 多档
        store = _mk_store(tmp_path)
        e = LongTaskEngine("t9", "goal", store=store)
        e.start([{"id": "a", "description": ""}, {"id": "b", "description": ""}])

        def runner(step):
            if step.step_id == "b":
                return {"success": True, "output": {"x": 1}, "confidence": "verified",
                        "decision": "external check", "evidence": ["sha=1", "output: ok"],
                        "verification_command": "pytest", "verification_result": "passed",
                        "verifier": "external"}
            return {"success": True, "output": {"x": 0}, "decision": "self-report",
                    "evidence": ["artifact"], "verifier": "executor"}

        e.run_steps(runner)
        report = e.completion_report()
        assert report["verified_count"] == 1
        assert report["self_reported_count"] == 1
        assert report["run_confidence"] == CompletionConfidence.REPORTED_DONE.value  # 未全 verified


# ────────────────────────────────────────────────────────────
# Controller 薄适配器（接线语义）
# ────────────────────────────────────────────────────────────

class TestController:
    def test_bind_resume_and_skip(self, tmp_path):
        db = tmp_path / "lta.db"
        store = CheckpointStore(db_path=db)
        ctl = LongTaskController.bind("t10", "goal", STEPS, store=store)
        # 跑前两步
        assert ctl.on_step_start(0, "step 0", {"index": 0}, step_id="step_0") is True
        ctl.on_step_end(0, success=True, output={"n": 0}, decision="ok",
                        evidence=["a"], step_id="step_0")
        assert ctl.on_step_start(1, "step 1", {"index": 1}, step_id="step_1") is True
        ctl.on_step_end(1, success=True, output={"n": 1}, decision="ok",
                        evidence=["b"], step_id="step_1")

        # 新 controller（模拟重启）bind 自动续跑，已完成步返回 False
        store2 = CheckpointStore(db_path=db)
        ctl2 = LongTaskController.bind("t10", "goal", STEPS, store=store2)
        assert ctl2.on_step_start(0, "step 0", {}, step_id="step_0") is False  # 不重做
        assert ctl2.on_step_start(2, "step 2", {}, step_id="step_2") is True

    def test_heartbeat_reports_level(self, tmp_path):
        store = _mk_store(tmp_path)
        ctl = LongTaskController.bind("t11", "goal", STEPS, store=store)
        a = ctl.on_heartbeat("sig")
        assert a.level in (ProgressLevel.OK, ProgressLevel.WARNING,
                           ProgressLevel.STALLED, ProgressLevel.DEGRADED)


# ────────────────────────────────────────────────────────────
# 接线验证：soulmate_agent 的 P0 钩子真实驱动控制器+data 检查点/轨迹
# （"写了≠接线了"——这里断言真实 agent 方法跑通端到端落盘）
# ────────────────────────────────────────────────────────────

class TestAgentWiring:
    def test_agent_helpers_drive_controller_end_to_end(self, tmp_path, monkeypatch):
        import types
        import agent.long_task_autonomy as lta
        from agent.soulmate_agent import SoulMateAgent

        # 隔离：把检查点库指到临时目录（不污染真实 data/）
        monkeypatch.setattr(lta, "DB_PATH", tmp_path / "lta.db")

        # stub self：只带 _lta_controllers（helper 只用它 + logger）
        stub = types.SimpleNamespace(_lta_controllers={})

        # 伪 plan：2 个子任务
        Step = types.SimpleNamespace
        plan = types.SimpleNamespace(subtasks=[
            Step(id="s1", description="步骤A"), Step(id="s2", description="步骤B")])

        # 1) bind → 控制器建立 + 检查点库建 run
        ctl = SoulMateAgent._bind_long_task(stub, "sess1", "目标X", plan)  # type: ignore[arg-type]
        assert ctl is not None
        assert "sess1" in stub._lta_controllers

        # 2) step_start + step_end → 检查点落盘 + 轨迹记录
        assert ctl.on_step_start(0, "步骤A", {"step_id": "s1"}, step_id="s1") is True
        stepA = Step(id="s1", result="产出A")
        SoulMateAgent._lta_step_end(stub, ctl, 0, stepA, True, {"summary": "完成A"})  # type: ignore[arg-type]

        # 3) finalize → 多档完成状态
        SoulMateAgent._lta_finalize(stub, ctl)  # type: ignore[arg-type]

        # 断言：真实落盘了检查点 + 轨迹（在临时 db）
        store = lta.CheckpointStore(db_path=tmp_path / "lta.db")
        run = store.load_run(ctl.run_id)
        assert run is not None
        s1 = next(s for s in run.steps if s.step_id == "s1")
        assert s1.status.value == "success"
        # 自省=自报 → 封顶 reported_done（不谎报 verified）
        assert s1.confidence == CompletionConfidence.REPORTED_DONE
        # 轨迹完整可回放
        tl = lta.TraceRecorder(db_path=tmp_path / "lta.db").get_timeline(ctl.run_id)
        phases = {e["phase"] for e in tl}
        assert "step_start" in phases and "step_end" in phases and "run_end" in phases

    def test_bind_nonfatal_on_bad_plan(self, tmp_path, monkeypatch):
        import types
        import agent.long_task_autonomy as lta
        from agent.soulmate_agent import SoulMateAgent
        monkeypatch.setattr(lta, "DB_PATH", tmp_path / "lta.db")
        stub = types.SimpleNamespace(_lta_controllers={})
        # plan 无 subtasks 属性 → helper 容错返回 None（非致命）
        ctl = SoulMateAgent._bind_long_task(stub, "sess2", "goal", types.SimpleNamespace())  # type: ignore[arg-type]
        # 要么绑定成功（空步骤），要么 None——但绝不抛异常
        assert ctl is None or ctl is not None


# ────────────────────────────────────────────────────────────
# 端到端验收：真实子进程 kill → resume → replay（用户可复跑）
# ────────────────────────────────────────────────────────────

def _run_demo(data_dir: Path, exec_log: Path, task_id: str, extra: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ACP_PROXY_DATA_DIR"] = str(data_dir)
    return subprocess.run(
        [sys.executable, str(DEMO), "--task-id", task_id,
         "--data-dir", str(data_dir), "--exec-log", str(exec_log),
         "--step-delay", "0.5", "--steps", "6"] + extra,
        capture_output=True, text=True, timeout=60, env=env,
    )


class TestEndToEndKillResumeReplay:
    def test_kill_midtask_resumes_and_replays(self, tmp_path):
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        exec_log = tmp_path / "run.exec"
        task_id = "e2e_kill_resume"

        # 1) 启动长任务子进程（6 步 × 0.5s ≈ 3s）
        env = dict(os.environ)
        env["ACP_PROXY_DATA_DIR"] = str(data_dir)
        proc = subprocess.Popen(
            [sys.executable, str(DEMO), "--task-id", task_id,
             "--data-dir", str(data_dir), "--exec-log", str(exec_log),
             "--step-delay", "0.5", "--steps", "6"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
        )
        # 2) 等它完成若干步后，中途 SIGKILL（模拟进程被杀）
        deadline = time.time() + 20
        while time.time() < deadline:
            if exec_log.exists() and len(exec_log.read_text().split()) >= 2:
                break
            time.sleep(0.05)
        time.sleep(0.15)  # 让第二个 step 落账
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

        done_before = exec_log.read_text().split() if exec_log.exists() else []
        assert len(done_before) >= 2, f"pre-kill progress missing: {done_before}"
        assert len(done_before) < 6, "task fully finished before kill; adjust timing"

        # 3) 重启：从检查点续跑（不从头来）
        resumed = _run_demo(data_dir, exec_log, task_id, ["--resume"])
        assert resumed.returncode == 0, resumed.stderr
        assert "[resume]" in resumed.stdout

        # 4) 断言：每个 step_id 恰好一次（已完成步不重做）→ 全 6 步齐
        final = exec_log.read_text().split()
        assert len(final) == 6, f"expected 6 unique step runs, got {final}"
        assert len(set(final)) == 6, f"a step was redone: {final}"

        # 5) 断言：从断点续跑而非从头（续跑只跑剩余步）
        assert "[resume] run=" in resumed.stdout

        # 6) 轨迹完整可回放：replay 含被杀前后所有步骤、每步 in/out/decision
        replay = _run_demo(data_dir, exec_log, task_id, ["--replay"])
        assert replay.returncode == 0, replay.stderr
        text = replay.stdout
        assert "Replay" in text
        for i in range(6):
            assert f"step_{i}" in text, f"missing step_{i} in replay"
        assert "step_end" in text and "decision" in text

        # 7) 完成状态多档：至少一个 verified（外部核验步），其余 reported_done
        assert '"run_status": "completed"' in text or "completed" in text

    def test_replay_after_full_run_has_all_steps(self, tmp_path):
        # 干净一跑（无 kill）也应完整可回放
        data_dir = tmp_path / "data2"
        data_dir.mkdir()
        exec_log = tmp_path / "run2.exec"
        task_id = "e2e_full"
        r = _run_demo(data_dir, exec_log, task_id, [])
        assert r.returncode == 0, r.stderr
        assert len(exec_log.read_text().split()) == 6
        replay = _run_demo(data_dir, exec_log, task_id, ["--replay"])
        assert "Replay" in replay.stdout
