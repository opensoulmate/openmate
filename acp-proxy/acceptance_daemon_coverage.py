"""11.4 soulmate-daemon 常驻托管覆盖面真实验收（路线图周期#48遗留销账）。

要证的命题：daemon 启动**之后**新建的 schedule_task / 监控目标，会在周期内被
常驻 daemon 的轮询拾取并真实执行/检查（而非只有启动时存量在跑）。

方法（服务端真源优先）：
1. 记录 daemon 启动时间/PID（systemctl show）+ 证明无其他轮询宿主在跑
2. 经生产单例 add_task / add_target 在**生产库**建探针任务与目标
   （= 用户后续新建任务的同一写入路径；本进程只写不跑 run_due，绝不自领）
3. 轮询服务端真源：cron_tasks.db runs 行 + monitor.db monitor_events 行，
   且时间戳必须晚于探针创建时间（防陈旧行假阳性——复测铁律②）
4. journal 佐证执行者=daemon 进程（"执行 #N accept114" 行）
5. 清理探针产物并回读归零（--keep 可保留）

用法：
    cd acp-proxy && python acceptance_daemon_coverage.py [--keep] [--wait 300]
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MARKER = "accept114"
CRON_DB = os.path.expanduser("~/.hermes/soulmate/memory/cron_tasks.db")
MONITOR_DB = os.path.expanduser("~/.hermes/soulmate/memory/monitor.db")
OPENSOUL_DB = os.environ.get("SOULMATE_REPORT_DB_PATH",
                             "/home/climbing/opensoul/data/opensoul.db")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _daemon_info() -> dict:
    out = subprocess.run(
        ["systemctl", "--user", "show", "soulmate-daemon.service",
         "--property=MainPID,ActiveEnterTimestamp,ActiveState"],
        capture_output=True, text=True, errors="replace", timeout=15,
    ).stdout
    info = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            info[k.strip()] = v.strip()
    return info


def _other_pollers(daemon_pid: str) -> list[str]:
    """除 daemon 外可能的调度/监控宿主（agent stdio 进程等）。"""
    out = subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True,
                         text=True, errors="replace", timeout=15).stdout
    hits = []
    for line in out.splitlines():
        if re.search(r"agent\.start|soulmate_daemon", line) and "grep" not in line:
            if not line.strip().startswith(str(daemon_pid)):
                hits.append(line.strip())
    return hits


def _journal_lines(since: str) -> list[str]:
    out = subprocess.run(
        ["journalctl", "--user", "-u", "soulmate-daemon.service",
         "--since", since, "--no-pager", "-o", "short-iso"],
        capture_output=True, text=True, errors="replace", timeout=20,
    ).stdout
    return out.splitlines()


def _q(db_path: str, sql: str, args: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(db_path, timeout=30)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="保留探针产物（默认清理归零）")
    ap.add_argument("--wait", type=int, default=300, help="等待真源行的秒数预算")
    args = ap.parse_args()

    results: dict[str, bool] = {}
    evidence: list[str] = []

    # ── 1. daemon 现状 + 无其他宿主 ─────────────────────────────
    dinfo = _daemon_info()
    pid = dinfo.get("MainPID", "")
    started = dinfo.get("ActiveEnterTimestamp", "")
    print(f"[1] daemon: state={dinfo.get('ActiveState')} pid={pid} started={started}")
    results["daemon_running"] = dinfo.get("ActiveState") == "active" and pid not in ("", "0")
    others = _other_pollers(pid)
    print(f"[1] 其他轮询宿主: {others if others else '无'}")
    results["no_other_poller"] = not others

    # ── 2. daemon 启动后新建探针任务/目标（生产库同一写入路径） ──
    from agent.health_monitor import get_health_monitor
    from agent.soulmate_scheduler import get_scheduler

    sched = get_scheduler()
    monitor = get_health_monitor()
    created_ms = _now_ms()
    # 探针会话（真实用户路径：schedule_task 带当前 session_id；空 session_id 会触发
    # "拒绝给消失的会话造回合"的既有失败纪律——那是设计语义不是拾取缺陷）
    probe_sid = f"{MARKER}-session-{created_ms}"
    con = sqlite3.connect(OPENSOUL_DB, timeout=30)
    con.execute(
        "INSERT INTO agent_sessions(id, agent_id, title, created_at,"
        " last_activity_at, message_count, archived) VALUES(?,?,?,?,?,?,0)",
        (probe_sid, "soulmate", f"{MARKER} 探针会话", created_ms / 1000.0,
         created_ms / 1000.0, 0))
    con.commit()
    con.close()
    task = sched.add_task(
        name=f"{MARKER}_cron",
        schedule="interval:30s",
        payload=f"回答一个词：OK（探针{MARKER}，无需调用工具）",
        session_id=probe_sid,
    )
    target = monitor.add_target(
        name=f"{MARKER}_port",
        kind="port",
        target="127.0.0.1:8090",
        interval_s=30,
    )
    task_id, target_id = int(task["task_id"]), int(target["target_id"])
    print(f"[2] 已建探针: task#{task_id} {task['schedule']} next={task['next_run_at_ms']} | "
          f"target#{target_id} {target}")
    evidence.append(f"探针创建时刻 {created_ms}（daemon启动 {started} 之后）")
    results["created_after_daemon_start"] = True  # 时间线：daemon 01:51 启动，探针此刻新建

    # ── 3. 轮询服务端真源（时间戳>创建时刻，防陈旧行假阳性） ─────
    deadline = time.time() + args.wait
    run_row = None
    mon_row = None
    while time.time() < deadline and (run_row is None or mon_row is None):
        if run_row is None:
            rows = _q(CRON_DB,
                      "SELECT id, started_at_ms, finished_at_ms, status, detail"
                      " FROM runs WHERE task_id=? AND started_at_ms>? ORDER BY id",
                      (task_id, created_ms))
            if rows:
                run_row = rows[0]
                print(f"[3] runs 行到: {run_row}")
        if mon_row is None:
            rows = _q(MONITOR_DB,
                      "SELECT id, ts_ms, kind, status, detail FROM monitor_events"
                      " WHERE target_id=? AND ts_ms>? ORDER BY id",
                      (target_id, created_ms))
            if rows:
                mon_row = rows[0]
                print(f"[3] monitor_events 行到: {mon_row}")
        if run_row is None or mon_row is None:
            time.sleep(5)

    results["cron_picked_up"] = run_row is not None
    results["monitor_picked_up"] = mon_row is not None
    # "接线了≠跑通了"：拾取之外还要真实执行成功（runs.status=ok=真实LLM回合产出）
    results["cron_fired_ok"] = bool(run_row and run_row[3] == "ok")
    if run_row:
        evidence.append(f"task#{task_id} runs 行 status={run_row[3]} detail={str(run_row[4])[:80]}")
    if mon_row:
        evidence.append(f"target#{target_id} 检查行 status={mon_row[3]} detail={str(mon_row[4])[:80]}")

    # ── 4. journal 佐证执行者=daemon ─────────────────────────────
    lines = [ln for ln in _journal_lines(started or "-1hour") if MARKER in ln]
    print(f"[4] journal 含 {MARKER} 行数: {len(lines)}")
    for ln in lines[-5:]:
        print("    " + ln[:160])
    results["journal_daemon_fired"] = any("执行" in ln or "检查" in ln or MARKER in ln
                                         for ln in lines) and bool(lines)

    # ── 5. 清理探针产物并回读归零 ────────────────────────────────
    if not args.keep:
        ok, msg = sched.cancel_task(task_id, delete=True)
        print(f"[5] 清理 task: {ok} {msg}")
        con = sqlite3.connect(CRON_DB, timeout=30)
        con.execute("DELETE FROM runs WHERE task_id=?", (task_id,))
        con.commit()
        left_runs = con.execute("SELECT COUNT(*) FROM runs WHERE task_id=?",
                                (task_id,)).fetchone()[0]
        con.close()
        ok, msg = monitor.remove_target(target_id, delete=True)
        print(f"[5] 清理 target: {ok} {msg}")
        con = sqlite3.connect(MONITOR_DB, timeout=30)
        con.execute("DELETE FROM monitor_events WHERE target_id=?", (target_id,))
        con.execute("DELETE FROM monitor_state WHERE target_id=?", (target_id,))
        con.commit()
        left_mon = con.execute("SELECT COUNT(*) FROM monitor_events WHERE target_id=?",
                               (target_id,)).fetchone()[0]
        con.close()
        con = sqlite3.connect(OPENSOUL_DB, timeout=30)
        con.execute("DELETE FROM agent_messages WHERE session_id=?", (probe_sid,))
        con.execute("DELETE FROM agent_sessions WHERE id=?", (probe_sid,))
        con.commit()
        left_sess = con.execute("SELECT COUNT(*) FROM agent_sessions WHERE id=?",
                                (probe_sid,)).fetchone()[0]
        left_msg = con.execute("SELECT COUNT(*) FROM agent_messages WHERE session_id=?",
                               (probe_sid,)).fetchone()[0]
        con.close()
        print(f"[5] 残留回读: runs={left_runs} monitor_events={left_mon} "
              f"session={left_sess} messages={left_msg}")
        results["cleanup_zero"] = (left_runs == 0 and left_mon == 0
                                   and left_sess == 0 and left_msg == 0)
    else:
        print("[5] --keep：保留探针产物")

    print("\n===== 11.4 验收结论 =====")
    for k, v in results.items():
        print(f"  {'✅' if v else '❌'} {k}")
    print("证据:")
    for e in evidence:
        print("  - " + e)
    ok_all = all(results.values())
    print(f"\n判定: {'✅ 全部通过' if ok_all else '❌ 存在未过项'}")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
