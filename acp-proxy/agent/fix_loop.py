# -*- coding: utf-8 -*-
"""fix_loop.py — 测试驱动自主修复循环（验证闸门版）。

来源：P1-代码工程能力「测试驱动的自主修复循环」，拔高标准对标 Claude Code /
RooCode 级自主修复循环 + OMH 的验证闸门哲学。流程（环内自校验）：

    复现（红测试）→ 修 → 定向测试绿 → 回归全量 → 报告

四道验证闸门（测试红不交付）：
1. **复现闸**：修复前必须拿到一个真实红的复现测试——基线就绿 = 没有可修的
   bug，拒伪修复（"修了个寂寞"）。
2. **定向绿闸**：每次修复尝试后跑复现测试；仍红 = 不可交付，把失败输出喂回
   修复者继续，并经 RepairLoop 三段式升级策略（normal → change_strategy →
   minimum），绝不死循环（stalled 超限 → 如实 stop 报失败，禁假称完成）。
3. **回归闸**：定向绿后跑回归全量；**对比基线**（开跑前抓的既有红名单）——
   新增红 = 修复破坏了别处，回炉重修（回归失败作为 blocking issue 进策略机）。
   既有红不阻塞（如实列在报告"预存失败"里），新红零容忍。
4. **报告闸**：只有 1-3 全过才出 `status=fixed` 的交付报告；任何放弃路径
   出 `status=failed` 的诚实失败报告（含失败原因/轮次/策略轨迹）。

纯 Python 确定性状态机：测试执行可注入（runner），真跑 subprocess 也可用
假跑 stub 测——独立可测（tests/test_fix_loop.py）。状态落
data/fix-loops/<id>.json，中断可续。

用法（SoulMate 工具面 fix_loop 的能力层）：
    loop = TestDrivenFixLoop(repo, test_cmd="pytest tests/ -q",
                             repro_test="tests/test_bug.py::test_x")
    loop.start()          # 基线 + 复现闸（必须红）
    # ……agent 修代码……
    loop.check()          # 定向绿闸 + 回归闸（自动推进/打回）
    loop.report()         # 报告闸（fixed / failed）
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from agent.repair_loop import RepairLoop, ACTION_ACCEPT, ACTION_STOP

__all__ = [
    "TestDrivenFixLoop",
    "FixLoopState",
    "PHASE_REPRODUCE",
    "PHASE_FIX",
    "PHASE_REGRESSION",
    "PHASE_DONE",
    "PHASE_FAILED",
    "default_runner",
]

PHASE_REPRODUCE = "reproduce"
PHASE_FIX = "fix"
PHASE_REGRESSION = "regression"   # 定向已绿，等回归
PHASE_DONE = "done"
PHASE_FAILED = "failed"

_STATE_ROOT = Path(__file__).resolve().parent.parent / "data" / "fix-loops"

# pytest 输出的失败/通过计数行
_PYTEST_SUMMARY_RE = re.compile(r"(\d+) failed")
_PYTEST_PASS_RE = re.compile(r"(\d+) passed")
_PYTEST_COLLECT_ERR_RE = re.compile(r"(\d+) error")


def default_runner(cmd: str, cwd: str, timeout_s: int = 600) -> dict:
    """真实测试执行器：subprocess 跑 cmd，返回 {exit_code, output}。"""
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True,
            errors="replace", timeout=timeout_s,
        )
        return {"exit_code": proc.returncode, "output": (proc.stdout + proc.stderr)[-8000:]}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output": f"[超时 {timeout_s}s] {cmd}"}
    except OSError as e:
        return {"exit_code": -1, "output": f"[执行失败] {e}"}


def parse_failed_ids(output: str) -> set[str]:
    """从 pytest 输出抽失败测试 id（FAILED tests/xx.py::test_y — ... 行）。"""
    ids: set[str] = set()
    for line in output.splitlines():
        m = re.match(r"^(?:FAILED|ERROR)\s+(\S+)", line.strip())
        if m:
            ids.add(m.group(1).split(" ")[0])
    return ids


@dataclass
class FixLoopState:
    loop_id: str = ""
    repo: str = ""
    bug: str = ""
    test_cmd: str = ""
    repro_test: str = ""
    phase: str = PHASE_REPRODUCE
    rounds: int = 0
    max_rounds: int = 8
    baseline_failed: list = field(default_factory=list)   # 基线既有红（失败测试id）
    repro_baseline_red: bool = False
    strategy: str = "normal"
    history: list = field(default_factory=list)           # 每轮记录
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "loop_id": self.loop_id, "repo": self.repo, "bug": self.bug,
            "test_cmd": self.test_cmd, "repro_test": self.repro_test,
            "phase": self.phase, "rounds": self.rounds, "max_rounds": self.max_rounds,
            "baseline_failed": self.baseline_failed,
            "repro_baseline_red": self.repro_baseline_red,
            "strategy": self.strategy, "history": self.history,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FixLoopState":
        st = cls()
        for k in ("loop_id", "repo", "bug", "test_cmd", "repro_test", "phase",
                  "strategy"):
            if k in d:
                setattr(st, k, d[k])
        for k in ("rounds", "max_rounds"):
            if k in d:
                setattr(st, k, int(d[k]))
        for k in ("baseline_failed", "history"):
            if isinstance(d.get(k), list):
                setattr(st, k, list(d[k]))
        st.repro_baseline_red = bool(d.get("repro_baseline_red"))
        st.created_at = float(d.get("created_at") or 0)
        st.updated_at = float(d.get("updated_at") or 0)
        return st


class TestDrivenFixLoop:
    """测试驱动自主修复循环（四道验证闸门，测试红不交付）。"""

    def __init__(
        self,
        repo: str | os.PathLike,
        test_cmd: str,
        repro_test: str = "",
        bug: str = "",
        regress_cmd: str = "",
        max_rounds: int = 8,
        runner=None,
        state_dir: str | os.PathLike | None = None,
        loop_id: str = "",
    ):
        self.repo = str(Path(repo).resolve())
        self.test_cmd = str(test_cmd or "").strip()
        self.repro_test = str(repro_test or "").strip()
        # 回归命令缺省 = 全量测试命令（定向跑法= test_cmd + repro_test 选择器）
        self.regress_cmd = str(regress_cmd or "").strip() or self.test_cmd
        self.runner = runner or default_runner
        self.state_dir = Path(state_dir) if state_dir else _STATE_ROOT
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state = FixLoopState(
            loop_id=loop_id or uuid.uuid4().hex[:12],
            repo=self.repo, bug=str(bug or ""), test_cmd=self.test_cmd,
            repro_test=self.repro_test, max_rounds=max(1, int(max_rounds)),
            created_at=time.time(),
        )
        # 三段式策略机（normal → change_strategy → minimum），stalled 判定复用
        self._repair = RepairLoop(goal=str(bug or ""), max_stall=2)

    # ── 持久化 ─────────────────────────────────────────────────────

    @property
    def _state_path(self) -> Path:
        return self.state_dir / f"{self.state.loop_id}.json"

    def save(self) -> None:
        self.state.updated_at = time.time()
        self._state_path.write_text(
            json.dumps(self.state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")

    @classmethod
    def load(cls, loop_id: str, state_dir: str | os.PathLike | None = None,
             runner=None) -> "TestDrivenFixLoop":
        d = Path(state_dir) if state_dir else _STATE_ROOT
        path = d / f"{loop_id}.json"
        st = FixLoopState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        loop = cls(
            repo=st.repo, test_cmd=st.test_cmd, repro_test=st.repro_test,
            bug=st.bug, max_rounds=st.max_rounds, runner=runner,
            state_dir=d, loop_id=st.loop_id,
        )
        loop.state = st
        return loop

    # ── 测试执行（定向 / 回归） ────────────────────────────────────

    def _run_repro(self) -> dict:
        """定向跑复现测试（test_cmd + repro 选择器拼接）。"""
        if self.repro_test and self.repro_test not in self.test_cmd:
            cmd = f"{self.test_cmd} {self.repro_test}"
        else:
            cmd = self.test_cmd
        return self.runner(cmd, self.repo)

    def _run_regression(self) -> dict:
        return self.runner(self.regress_cmd, self.repo)

    # ── 闸门1：复现（必须红） ──────────────────────────────────────

    def start(self) -> dict:
        """基线 + 复现闸。复现测试基线就绿 → 拒伪修复（没有 bug 可修）。"""
        if not self.test_cmd:
            raise ValueError("test_cmd 不能为空（测试驱动修复必须有测试命令）")
        # 基线回归：抓既有红名单（预存失败不阻塞，但必须如实进报告）
        reg = self._run_regression()
        self.state.baseline_failed = sorted(parse_failed_ids(reg["output"]))
        repro = self._run_repro()
        repro_red = repro["exit_code"] != 0
        self.state.repro_baseline_red = repro_red
        self.state.phase = PHASE_FIX if repro_red else PHASE_REPRODUCE
        self.state.history.append({
            "round": 0, "phase": "start",
            "baseline_exit": reg["exit_code"],
            "baseline_failed": self.state.baseline_failed,
            "repro_exit": repro["exit_code"],
            "repro_red": repro_red,
            "repro_output_tail": repro["output"][-2000:],
        })
        self.save()
        if not repro_red:
            return {
                "ok": False, "phase": self.state.phase,
                "gate": "reproduce",
                "message": (
                    "复现闸拒绝：复现测试在修复前就是绿的——没有红测试 = 没有可修的 "
                    "bug，拒伪修复。请换一个真正失败的复现测试（或先写一个能暴露 bug 的）。"
                ),
                "repro_output": repro["output"][-2000:],
                "baseline_failed": self.state.baseline_failed,
            }
        return {
            "ok": True, "phase": self.state.phase, "gate": "reproduce",
            "message": (
                f"复现闸通过：复现测试确认红（exit={repro['exit_code']}）。"
                f"基线既有红 {len(self.state.baseline_failed)} 个（预存失败，回归闸对照用）。"
                "现在可以开始修复；修完调用 check() 过定向绿闸。"
            ),
            "baseline_failed": self.state.baseline_failed,
            "repro_output": repro["output"][-2000:],
        }

    # ── 闸门2/3：定向绿 + 回归全量 ────────────────────────────────

    def check(self) -> dict:
        """每轮修复后验收：定向绿闸 → 回归闸 → 推进/打回/停。

        - 定向仍红：打回 + RepairLoop 策略升级（normal→change_strategy→minimum），
          绝不交付；stalled 超限且 minimum 无进展 → phase=failed（诚实失败）。
        - 定向绿 + 回归新增红：回归闸打回（回归失败作为 blocking issue 回炉）。
        - 定向绿 + 回归对照基线无新增红：phase=done，可出交付报告。
        """
        if self.state.phase in (PHASE_DONE, PHASE_FAILED):
            return self.status()
        if self.state.phase != PHASE_FIX:
            return {
                "ok": False, "gate": "reproduce",
                "message": "尚未通过复现闸（或未 start()），不能进修复验收。",
            }
        self.state.rounds += 1
        repro = self._run_repro()
        repro_red = repro["exit_code"] != 0
        record = {
            "round": self.state.rounds, "phase": "check",
            "repro_exit": repro["exit_code"], "repro_red": repro_red,
            "repro_output_tail": repro["output"][-2000:],
        }

        if repro_red:
            # 定向绿闸拒绝：测试红不交付
            # issue id 跨轮稳定（"repro-red"）：RepairLoop 的 stalled 判定看
            # "同一问题是否被修掉"，id 带轮次号会让每轮都算"有进展"永不升级
            decision = self._escalate(
                issue_id="repro-red",
                detail=f"复现测试仍红（第{self.state.rounds}轮, exit={repro['exit_code']}）",
                output=repro["output"],
            )
            record["decision"] = decision
            self.state.history.append(record)
            self.save()
            return {
                "ok": False, "gate": "target_green",
                "phase": self.state.phase,
                "message": (
                    f"定向绿闸拒绝（第{self.state.rounds}轮）：测试红不交付。"
                    f"{decision['reason']} 策略={decision['mode']}。"
                ),
                "fix_prompt": decision.get("prompt") or "",
                "strategy": decision["mode"],
                "repro_output": repro["output"][-2000:],
            }

        # 定向绿 → 回归闸
        reg = self._run_regression()
        new_failed = sorted(parse_failed_ids(reg["output"]) - set(self.state.baseline_failed))
        record["regress_exit"] = reg["exit_code"]
        record["new_failed"] = new_failed
        if reg["exit_code"] != 0 and new_failed:
            # 回归闸拒绝：修复破坏了别处 → 回炉（blocking issue 进策略机）
            decision = self._escalate(
                issue_id="regression-break",
                detail=(f"回归新增红（第{self.state.rounds}轮）"
                        f"{len(new_failed)} 个: {', '.join(new_failed[:5])}"),
                output=reg["output"],
            )
            record["decision"] = decision
            self.state.history.append(record)
            self.save()
            return {
                "ok": False, "gate": "regression",
                "phase": self.state.phase,
                "message": (
                    f"回归闸拒绝（第{self.state.rounds}轮）：定向绿但回归新增红 "
                    f"{len(new_failed)} 个（{', '.join(new_failed[:5])}）——修复破坏了别处，"
                    f"回炉重修。策略={decision['mode']}。"
                ),
                "fix_prompt": decision.get("prompt") or "",
                "strategy": decision["mode"],
                "new_failed": new_failed,
            }

        # 全闸通过
        self.state.phase = PHASE_DONE
        record["decision"] = {"action": ACTION_ACCEPT}
        self.state.history.append(record)
        self.save()
        return {
            "ok": True, "gate": "regression",
            "phase": self.state.phase,
            "message": (
                f"定向绿闸 + 回归闸通过（第{self.state.rounds}轮）：复现测试绿，"
                f"回归无新增红（基线既有红 {len(self.state.baseline_failed)} 个照旧）。"
                "可以出交付报告 report()。"
            ),
            "baseline_failed": self.state.baseline_failed,
        }

    def _escalate(self, issue_id: str, detail: str, output: str) -> dict:
        """RepairLoop 三段式策略升级；stop = 诚实失败收尾。"""
        validation = {
            "ok": False,
            "blocking": [issue_id],
            "issues": [{"id": issue_id, "severity": "blocking", "detail": detail}],
        }
        decision = self._repair.step(validation)
        self.state.strategy = decision["mode"]
        if decision["action"] == ACTION_STOP or self.state.rounds >= self.state.max_rounds:
            self.state.phase = PHASE_FAILED
            decision = dict(decision)
            decision["reason"] = (
                decision.get("reason", "") +
                f"（轮次上限 {self.state.max_rounds}）" if self.state.rounds >= self.state.max_rounds
                else decision.get("reason", "")
            )
        return decision

    # ── 闸门4：报告 ───────────────────────────────────────────────

    def report(self) -> dict:
        """交付报告（fixed）或诚实失败报告（failed）。测试红绝不写 fixed。"""
        st = self.state
        fixed = st.phase == PHASE_DONE
        if fixed:
            # 报告闸自查：历史里最后一轮必须是双绿（防状态被绕改）
            last = st.history[-1] if st.history else {}
            if last.get("repro_red") or last.get("new_failed"):
                fixed = False
        lines = [
            f"# 测试驱动修复报告（loop {st.loop_id}）",
            "",
            f"- 仓库: {st.repo}",
            f"- Bug: {st.bug or '（未描述）'}",
            f"- 复现测试: {st.repro_test or st.test_cmd}",
            f"- 结论: {'✅ fixed（定向绿 + 回归无新增红）' if fixed else '❌ failed（未交付，测试红/回归红如实报告）'}",
            f"- 修复轮次: {st.rounds}（策略轨迹: {' → '.join(h.get('decision', {}).get('mode', 'normal') for h in st.history if h.get('decision')) or '无'}）",
            f"- 基线既有红（预存失败，不属本次修复范围）: {len(st.baseline_failed)} 个",
        ]
        for fid in st.baseline_failed[:10]:
            lines.append(f"  - {fid}")
        if not fixed:
            last = st.history[-1] if st.history else {}
            lines += ["", "## 失败原因（诚实报告，禁假称完成）",
                      f"- 最后一轮: {json.dumps({k: v for k, v in last.items() if k != 'repro_output_tail'}, ensure_ascii=False)[:500]}"]
        lines += ["", "## 复跑验收步骤", f"1. cd {st.repo}",
                  f"2. {st.test_cmd} {st.repro_test}".strip(),
                  f"3. {self.regress_cmd}  # 回归全量对照基线"]
        report = {
            "ok": fixed, "status": "fixed" if fixed else "failed",
            "loop_id": st.loop_id, "rounds": st.rounds,
            "baseline_failed": st.baseline_failed,
            "report": "\n".join(lines),
        }
        self.state.history.append({"round": st.rounds, "phase": "report",
                                   "status": report["status"]})
        self.save()
        return report

    # ── 状态 / 自主驱动 ───────────────────────────────────────────

    def status(self) -> dict:
        st = self.state
        return {
            "ok": st.phase == PHASE_DONE,
            "phase": st.phase, "rounds": st.rounds,
            "max_rounds": st.max_rounds, "strategy": st.strategy,
            "baseline_failed": st.baseline_failed,
            "history_len": len(st.history),
            "loop_id": st.loop_id,
        }

    def run(self, fix_fn) -> dict:
        """全自主驱动：fix_fn(ctx) 改代码（LLM/agent 调用点），循环内自校验。

        ctx: {"round", "strategy", "phase", "feedback"(上一轮失败输出),
              "bug", "repro_test"}。fix_fn 返回后即验收；四闸语义同上。
        """
        started = self.start()
        if not started.get("ok"):
            return {"ok": False, "gate": "reproduce", **started}
        feedback = started.get("repro_output", "")
        while self.state.phase == PHASE_FIX:
            ctx = {
                "round": self.state.rounds + 1,
                "strategy": self.state.strategy,
                "phase": self.state.phase,
                "feedback": feedback,
                "bug": self.state.bug,
                "repro_test": self.state.repro_test,
            }
            fix_fn(ctx)
            result = self.check()
            feedback = result.get("repro_output") or json.dumps(
                result.get("new_failed", []), ensure_ascii=False)
            if result.get("ok"):
                break
            if self.state.phase == PHASE_FAILED:
                # 诚实失败收尾：出 failed 报告（含失败原因/策略轨迹/复跑步骤），
                # 绝不返回看似可交付的中间态
                rep = self.report()
                rep["gate"] = result.get("gate")
                return rep
        return self.report()
