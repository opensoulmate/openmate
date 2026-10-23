#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""15.5 stall_guard flaky 复跑器 — 留证日志路径随周期隔离（周期#682 实装）。

背景（周期#681 工具失误，#597 覆盖事故家族第 2 例）：flaky 复跑命令的留证日志路径
硬编码 /tmp/_c680_flaky_* → #681 运行直接覆盖 #680 原件（不可复原）。教训固化为本脚本：
留证文件名进脚本变量（argv tag），调用侧不再手写路径——「仅改调用侧文件名不够」。

用法：
    python tests/flaky_run_stall_guard.py c682
    → /tmp/_c682_flaky_{batch,durations,single1-3,singleX1-3}.log + /tmp/_c682_flaky_run.log

覆盖 8 发（15.5 三面）：
    1) batch   32 例（test_llm_stall_guard.py + test_llm_stream_stall.py 全量）
    2) durations 同批 + --durations=32（读 max call 时长对 3.0s 上限）
    3) single  3x1 test_head_stall_aborts_fast_and_retry_recovers
    4) singleX 3x1 test_head_stall_retries_and_recovers
"""
import os
import subprocess
import sys

PY = os.path.expanduser("~/opensoul/.venv/bin/python")
ACPPROXY = os.path.expanduser("~/openmate/acp-proxy")
SINGLE = "tests/test_llm_stall_guard.py::TestStallAbortAndRecover::test_head_stall_aborts_fast_and_retry_recovers"
SINGLE_X = "tests/test_llm_stream_stall.py::TestStreamStall::test_head_stall_retries_and_recovers"
BATCH_TARGETS = ["tests/test_llm_stall_guard.py", "tests/test_llm_stream_stall.py"]


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print("usage: flaky_run_stall_guard.py <tag>  (e.g. c682)", file=sys.stderr)
        return 2
    tag = sys.argv[1].strip()
    if "/" in tag or tag.startswith("."):
        print("invalid tag", file=sys.stderr)
        return 2

    def log_path(kind: str) -> str:
        # 留证文件名 = 脚本变量随周期隔离（唯一真源，调用侧禁手写路径）
        return f"/tmp/_{tag}_flaky_{kind}.log"

    run_log = log_path("run")
    jobs = [
        ("batch", BATCH_TARGETS + ["-q"]),
        ("durations", BATCH_TARGETS + ["-q", "--durations=32"]),
    ]
    for i in (1, 2, 3):
        jobs.append((f"single{i}", [SINGLE, "-q"]))
        jobs.append((f"singleX{i}", [SINGLE_X, "-q"]))

    lines = []
    for kind, args in jobs:
        out = log_path(kind)
        cmd = [PY, "-m", "pytest"] + args
        with open(out, "w", encoding="utf-8") as f:
            proc = subprocess.run(cmd, cwd=ACPPROXY, stdout=f, stderr=subprocess.STDOUT)
        lines.append(f"{kind.upper() if kind.startswith('single') else kind.upper()}_EXIT={proc.returncode}")
        print(lines[-1], flush=True)
    lines.append("FLAKY_ALL_DONE")
    with open(run_log, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("FLAKY_ALL_DONE")
    return 0 if all(l.endswith("_EXIT=0") for l in lines[:-1]) else 1


if __name__ == "__main__":
    sys.exit(main())
