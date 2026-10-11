#!/usr/bin/env python3
"""turn_timing_report.py — 开发类回合耗时四段归因（周期#67 切片 12.3）

单一真源 = opensoul.db trajectory_events（真实回合落账）：
  - user_input 行：created_at=回合开始，end_at=回合结束（duration_ms=回合总时长）
  - llm_call 带 end_at：plan_step/chat_turn 步骤跨度（created_at=步开始）
  - llm_call 无 end_at：轮内 LLM 往返（created_at=轮结束，start=created_at-duration_ms）
  - tool_call 行：工具执行点（duration_ms 目前恒 0——工具执行时长未落账，如实报缺口）

四段归因（用户/路线图 12.3 口径）：
  ① LLM 往返（含首包）= 轮内 llm_call 时长合计 + 每轮明细
  ② 注入审计往返 = journal 同窗口"注入审计/clarify/terminate"判词行扫描（有则计数+归入空洞解释）
  ③ 工具执行 = tool_call 行（时长未落账→用相邻轮间隙做上界代理，如实标注）
  ④ 回复回收 = 回合结束 - 最后一个事件结束
  ⑤ 未归属空洞 = 上述四段之外 >2s 的静默区间（含 journal 传输错误/重试行做归因解释）

用法：
  python turn_timing_report.py om-25b400c46a99 [om-xxx ...]
  python turn_timing_report.py --json /tmp/turn_timing.json om-xxx
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = str(Path.home() / "opensoul" / "data" / "opensoul.db")

_GAP_MIN_S = 2.0  # 小于 2s 的零头不算空洞


def _ts(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _fmt(dt: datetime) -> str:
    return dt.astimezone().strftime("%H:%M:%S.%f")[:-3]


def _journal_errors(start: datetime, end: datetime) -> list[str]:
    """扫描回合窗口内 journal 的错误/重试/审计判词行（去重，含时间）。"""
    since = start.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    until = (end + timedelta(seconds=2)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    try:
        out = subprocess.run(
            ["journalctl", "--user", "--since", since, "--until", until,
             "--no-pager", "-o", "short-iso"],
            capture_output=True, text=True, errors="replace", timeout=30,
        ).stdout
    except Exception as e:  # noqa: BLE001 — journal 不可用如实降级
        return [f"(journal 不可用: {type(e).__name__})"]
    pat = re.compile(
        r"传输错误|WARNING|ERROR|重试|注入审计|clarify|terminate|watchdog|路径越界",
        re.IGNORECASE)
    seen: set[str] = set()
    hits: list[str] = []
    for line in out.splitlines():
        if "soulmate" not in line:
            continue
        # 只认 SoulMate 链路日志（剔除 paperclipai 等外部进程捎带"soulmate"字样的行）
        if not re.search(r"\[(soulmate|acp-agent|acp\.|task\]|turn_watchdog|utils\.|agent\.)", line):
            continue
        m = re.search(r"(\d{2}:\d{2}:\d{2}),\d+", line)
        ts = m.group(1) if m else "?"
        # 剥掉 logger 前缀噪音，只留消息体
        body = line.split("stderr: ", 1)[-1].strip()
        body = re.sub(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+ \[[^\]]+\] \w+: ", "", body)
        if not pat.search(body):
            continue
        key = f"{ts} {body[:110]}"
        if key in seen:
            continue
        seen.add(key)
        hits.append(key)
    return hits


def report(session_id: str, conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT created_at, end_at, event_type, content, duration_ms FROM trajectory_events "
        "WHERE session_id=? ORDER BY created_at", (session_id,)).fetchall()
    turn = next((r for r in rows if r[2] == "user_input" and r[1]), None)
    if turn is None:
        return {"session_id": session_id, "error": "无 user_input 跨度行"}
    t_start, t_end = _ts(turn[0]), _ts(turn[1])
    total_s = turn[4] / 1000.0

    steps = []          # 步骤跨度（带 end_at 的 llm_call）
    rounds = []         # 轮内 LLM 往返（无 end_at 的 llm_call）
    tools = []          # 工具执行点
    for c0, c1, etype, content, dur in rows:
        if etype == "user_input":
            continue
        start = _ts(c0) - timedelta(milliseconds=dur) if etype == "llm_call" and not c1 else None
        if etype == "llm_call" and c1:
            steps.append({"start": _ts(c0), "end": _ts(c1), "dur_s": round(dur / 1000, 2),
                          "label": (content or "")[:40]})
        elif etype == "llm_call":
            rounds.append({"start": start, "end": _ts(c0), "dur_s": round(dur / 1000, 2),
                           "preview": (content or "")[:30]})
        elif etype == "tool_call":
            tools.append({"ts": _ts(c0), "name": (content or "")[:20],
                          "dur_s": round(dur / 1000, 2)})

    llm_total = round(sum(r["dur_s"] for r in rounds), 2)
    tool_instrumented = round(sum(t["dur_s"] for t in tools), 2)

    # 空洞 = 回合窗口内未被 [轮跨度 ∪ 步骤跨度] 覆盖的区间
    covered = sorted(
        [r["start"] for r in rounds] + [s["start"] for s in steps],
    )
    spans = sorted(
        [(r["start"], r["end"]) for r in rounds] + [(s["start"], s["end"]) for s in steps],
    )
    gaps = []
    cur = t_start
    for s, e in spans:
        if (s - cur).total_seconds() > _GAP_MIN_S:
            gaps.append({"from": _fmt(cur), "to": _fmt(s),
                         "dur_s": round((s - cur).total_seconds(), 2)})
        cur = max(cur, e)
    if (t_end - cur).total_seconds() > _GAP_MIN_S:
        gaps.append({"from": _fmt(cur), "to": _fmt(t_end),
                     "dur_s": round((t_end - cur).total_seconds(), 2)})

    last_event_end = max([r["end"] for r in rounds] + [s["end"] for s in steps] + [t_start])
    reply_recovery = round((t_end - last_event_end).total_seconds(), 2)

    journal = _journal_errors(t_start, t_end)

    return {
        "session_id": session_id,
        "turn": f"{_fmt(t_start)} → {_fmt(t_end)}",
        "total_s": round(total_s, 2),
        "plan_overhead_s": round((steps[0]["start"] - t_start).total_seconds(), 2) if steps else None,
        "llm_total_s": llm_total,
        "llm_rounds": [{"t": _fmt(r["start"]), "dur_s": r["dur_s"], "preview": r["preview"]}
                       for r in rounds],
        "steps": [{"t": _fmt(s["start"]), "dur_s": s["dur_s"], "label": s["label"]} for s in steps],
        "tools": [{"t": _fmt(t["ts"]), "name": t["name"], "dur_s": t["dur_s"]} for t in tools],
        "tool_instrumented_s": tool_instrumented,
        "tool_timing_gap": "tool_call.duration_ms 恒 0（执行时长未落账）" if tools and tool_instrumented == 0 else "",
        "reply_recovery_s": reply_recovery,
        "gaps": gaps,
        "gap_total_s": round(sum(g["dur_s"] for g in gaps), 2),
        "journal_signals": journal,
    }


def main() -> None:
    argv = sys.argv[1:]
    json_path = ""
    if "--json" in argv:
        idx = argv.index("--json")
        json_path = argv[idx + 1]
        argv = argv[:idx] + argv[idx + 2:]
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print("用法: turn_timing_report.py [--json out.json] <session_id> [...]")
        sys.exit(1)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    reports = []
    for sid in args:
        rep = report(sid, conn)
        reports.append(rep)
        print(f"\n=== {sid} ===")
        if "error" in rep:
            print(" ", rep["error"])
            continue
        print(f"  回合: {rep['turn']}  总时长 {rep['total_s']}s")
        print(f"  ① 规划/预步开销: {rep['plan_overhead_s']}s")
        print(f"  ② LLM 往返合计: {rep['llm_total_s']}s（{len(rep['llm_rounds'])} 轮）")
        for r in rep["llm_rounds"]:
            print(f"     - {r['t']}  {r['dur_s']:>7}s  {r['preview']!r}")
        print(f"  ③ 工具执行: {len(rep['tools'])} 次，落账 {rep['tool_instrumented_s']}s"
              + (f"（{rep['tool_timing_gap']}）" if rep["tool_timing_gap"] else ""))
        for t in rep["tools"]:
            print(f"     - {t['t']}  {t['name']}  {t['dur_s']}s")
        print(f"  ④ 回复回收: {rep['reply_recovery_s']}s")
        print(f"  ⑤ 未归属空洞: 合计 {rep['gap_total_s']}s（{len(rep['gaps'])} 段）")
        for g in rep["gaps"]:
            print(f"     - {g['from']} → {g['to']}  {g['dur_s']}s")
        print("  journal 信号:")
        for j in rep["journal_signals"][:12]:
            print(f"     {j}")
    if json_path:
        Path(json_path).write_text(json.dumps(reports, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
        print(f"\nJSON: {json_path}")


if __name__ == "__main__":
    main()
