#!/usr/bin/env python3
"""
长任务自治·验收演示脚本（真实可复跑）——kill→resume→replay。

用法（三态）：
  # 跑一个 N 步长任务（每步真睡+写执行台账），可被外部 kill
  python3 agent/scripts/long_task_demo.py --task-id demo1 --steps 6 --step-delay 0.4 --exec-log /tmp/demo1.exec

  # 进程被杀/中断后从检查点续跑（不从头来）
  python3 agent/scripts/long_task_demo.py --task-id demo1 --resume --exec-log /tmp/demo1.exec

  # 回放完整轨迹（Manus 式时间线）
  python3 agent/scripts/long_task_demo.py --task-id demo1 --replay

验收断言由 tests/test_long_task_autonomy.py 驱动：中途 SIGKILL 子进程→重启 --resume→
(1) 已完成步不重做（exec-log 每个 step_id 恰好一次）(2) 从断点续跑非从头 (3) 轨迹完整可回放。
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# 让脚本可独立运行：把 acp-proxy 根加入 sys.path
_HERE = Path(__file__).resolve()
_ACPPROXY = _HERE.parent.parent.parent  # .../acp-proxy
if str(_ACPPROXY) not in sys.path:
    sys.path.insert(0, str(_ACPPROXY))

from agent.long_task_autonomy import (  # noqa: E402
    CheckpointStore,
    LongTaskEngine,
    CompletionConfidence,
)


def build_steps(n: int) -> list[dict]:
    return [
        {"id": f"step_{i}", "description": f"执行长任务步骤 {i+1}/{n}",
         "input": {"index": i, "payload": f"workload-{i}"}}
        for i in range(n)
    ]


def make_runner(exec_log: Path, step_delay: float, verify_step: int):
    """每步：真睡（模拟长活）→ 写执行台账（证明是否重做）→ 返回结果。

    verify_step 那一步额外演示'非执行者核验'→ verified；其余自报→ reported_done。
    """
    def runner(step):
        time.sleep(step_delay)  # 真实长活（可被 kill 的窗口）
        # 做"工作"：把 step_id 记入共享执行台账（只在完成后写，kill 在睡眠中则不写）
        with open(exec_log, "a", encoding="utf-8") as f:
            f.write(f"{step.step_id}\n")
        out = {"produced": f"artifact-{step.step_id}", "bytes": 128 + step.seq}

        idx = int(step.input_data.get("index", -1))
        if idx == verify_step:
            # 演示完成状态多档：非执行者 + 具名核验命令 + 内容级证据 → verified
            return {
                "success": True, "output": out, "confidence": "verified",
                "decision": "external verifier checked artifact hash and command output",
                "evidence": [f"sha256={hash(str(out))}", f"command-output: verified artifact-{step.step_id} present"],
                "verification_command": f"test -f artifact-{step.step_id}",
                "verification_result": "exit=0 artifact present and hash matches",
                "verifier": "external",
            }
        # 自报完成（无核验）→ 封顶 reported_done
        return {
            "success": True, "output": out,
            "decision": "executor reports step complete (self-report, unchecked)",
            "evidence": [f"produced artifact-{step.step_id}"],
            "verifier": "executor",
        }
    return runner


def hash(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode()).hexdigest()[:12]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--data-dir", default=str(_ACPPROXY / "data"))
    ap.add_argument("--steps", type=int, default=6)
    ap.add_argument("--step-delay", type=float, default=0.4)
    ap.add_argument("--exec-log", default="/tmp/long_task_demo.exec")
    ap.add_argument("--verify-step", type=int, default=1)  # 第几步做外部核验
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--replay", action="store_true")
    args = ap.parse_args()

    os.environ["ACP_PROXY_DATA_DIR"] = args.data_dir
    os.environ["ACP_LONG_TASK_DB"] = str(Path(args.data_dir) / "long_task_autonomy.db")

    # 重新加载模块级常量（DB_PATH 在 import 时固化）
    import agent.long_task_autonomy as lta
    lta.DATA_DIR = Path(args.data_dir)
    lta.DB_PATH = Path(args.data_dir) / "long_task_autonomy.db"

    store = CheckpointStore(db_path=lta.DB_PATH)
    engine = LongTaskEngine(args.task_id, goal=f"演示长任务 {args.task_id}", store=store)

    if args.replay:
        # 回放：找到该 task 的任意 run
        run = store.find_resumable(args.task_id)
        if run is None:
            # 取最近一个 run（含已完成）
            with store._conn() as c:
                r = c.execute("SELECT run_id FROM long_task_runs WHERE task_id=? ORDER BY created_at DESC LIMIT 1",
                              (args.task_id,)).fetchone()
            if not r:
                print(f"no run found for {args.task_id}")
                return 2
            run = store.load_run(r["run_id"])
        engine.run = run
        print(engine.replay())
        print("\n--- completion report ---")
        print(json.dumps(engine.completion_report(), ensure_ascii=False, indent=2))
        return 0

    exec_log = Path(args.exec_log)
    runner = make_runner(exec_log, args.step_delay, args.verify_step)

    if args.resume:
        engine.resume()
        assert engine.run is not None
        done_before = len(engine.run.completed_steps())
        print(f"[resume] run={engine.run.run_id} 已完成 {done_before} 步，从断点续跑")
    else:
        engine.start(build_steps(args.steps))
        assert engine.run is not None
        done_before = 0
        print(f"[start] run={engine.run.run_id} 共 {args.steps} 步")

    final = engine.run_steps(runner)
    print(f"[done] status={final.status} confidence={final.confidence.value}")
    print("--- completion report ---")
    print(json.dumps(engine.completion_report(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
