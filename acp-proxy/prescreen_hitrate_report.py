# -*- coding: utf-8 -*-
"""路线图 12.4③ · 注入审计确定性预筛免审命中率观察（可复跑探针）。

单一真源双通道交叉核对（不新建落库）：
1. **journal**（跨进程/跨重启真源）：'[infection-guard] 预筛免审（source）' 行 =
   免审批次（分子）；'[infection-guard] 预筛升级审计' 行 = 升级 LLM 审计批次（分母另一侧）。
2. **轨迹账本**（opensoul.db trajectory_events）：permission='infection:prescreen_exempt'
   的 [infection-guard] 伪条目 = 免审批次留痕（report_evidence.parse_ledger 两头不计，
   纯可观测）。两通道计数不一致时如实报差异，不静默取其一。

用法：
    python prescreen_hitrate_report.py                  # 默认看 24 小时内
    python prescreen_hitrate_report.py --since "6 hours ago"
    python prescreen_hitrate_report.py --no-journal     # 只查账本
    python prescreen_hitrate_report.py --no-db          # 只查 journal

命中率 = 免审批次 / (免审批次 + 升级审计批次)；两侧都为 0 时报 None（无数据，
禁把"没跑过"当 100%）。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

EXEMPT_RE = re.compile(r"\[infection-guard\] 预筛免审（([^）]*)）")
ESCALATED_RE = re.compile(r"\[infection-guard\] 预筛升级审计")
DEFAULT_DB = Path.home() / "opensoul" / "data" / "opensoul.db"
UNITS = ("acp-proxy-a", "acp-proxy-b", "acp-proxy")


def count_journal_lines(lines) -> dict:
    """纯函数：从 journal 行文本统计免审/升级计数（可单测）。

    去重纪律（实测发现）：子进程 stderr 会被 ws_acp 中继再记一份 journal
    （"[INFO] [soulmate] stderr: …" 与 "[acp-proxy.ws_acp] INFO: [soulmate] stderr: …"
    双份），同一事件计两次会把命中率样本翻倍——按 "stderr: " 后的内层日志行
    为唯一键去重（无 stderr 前缀的行按整行）。
    """
    exempt = 0
    escalated = 0
    by_source: dict[str, int] = {}
    seen: set[str] = set()
    for line in lines or []:
        inner = line.split("stderr: ", 1)[1] if "stderr: " in line else line
        if inner in seen:
            continue
        m = EXEMPT_RE.search(line)
        if m:
            seen.add(inner)
            exempt += 1
            src = (m.group(1) or "?").strip()
            by_source[src] = by_source.get(src, 0) + 1
            continue
        if ESCALATED_RE.search(line):
            seen.add(inner)
            escalated += 1
    return {"exempt": exempt, "escalated": escalated, "by_source": by_source}


def read_journal(since: str) -> tuple[dict, str]:
    """journalctl 扫描双实例服务日志；失败如实报因（返回 error 字符串）。"""
    cmd = ["journalctl", "--user", "--no-pager", "--since", since]
    for u in UNITS:
        cmd += ["-u", f"{u}.service"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=30)
    except Exception as exc:  # journalctl 不可用/超时
        return {}, f"journalctl 调用失败: {exc}"
    if proc.returncode != 0:
        return {}, f"journalctl 退出码 {proc.returncode}: {proc.stderr[:200]}"
    return count_journal_lines(proc.stdout.splitlines()), ""


def read_ledger(db_path: Path) -> tuple[dict, str]:
    """轨迹账本回读免审留痕行（permission=infection:prescreen_exempt）。"""
    if not db_path.exists():
        return {}, f"账本库不存在: {db_path}"
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            cur = conn.execute(
                "SELECT metadata_json FROM trajectory_events "
                "WHERE event_type='tool_call'")
            exempt = 0
            by_source: dict[str, int] = {}
            for (meta_raw,) in cur.fetchall():
                try:
                    meta = json.loads(meta_raw) if meta_raw else {}
                except Exception:
                    continue
                if str(meta.get("permission", "")) != "infection:prescreen_exempt":
                    continue
                exempt += 1
                # arguments 落库形态=str(dict)（Python repr，非JSON）——解析要兼容两种
                src = "unknown"
                args = meta.get("arguments")
                if isinstance(args, dict):
                    src = str(args.get("source", "unknown"))
                elif isinstance(args, str):
                    try:
                        src = str(json.loads(args).get("source", "unknown"))
                    except Exception:
                        m = re.search(r"['\"]source['\"]\s*:\s*['\"]([^'\"]+)['\"]", args)
                        if m:
                            src = m.group(1)
                by_source[src] = by_source.get(src, 0) + 1
            return {"exempt": exempt, "by_source": by_source}, ""
        finally:
            conn.close()
    except Exception as exc:
        return {}, f"账本读取失败: {exc}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="注入审计预筛免审命中率观察")
    ap.add_argument("--since", default="24 hours ago",
                    help="journal 时间窗（journalctl --since 语法），默认 24 hours ago")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="opensoul.db 路径")
    ap.add_argument("--no-journal", action="store_true", help="跳过 journal 通道")
    ap.add_argument("--no-db", action="store_true", help="跳过账本通道")
    args = ap.parse_args(argv)

    j_stats: dict = {}
    j_err = ""
    if not args.no_journal:
        j_stats, j_err = read_journal(args.since)
    d_stats: dict = {}
    d_err = ""
    if not args.no_db:
        d_stats, d_err = read_ledger(Path(args.db))

    print("== 12.4③ 注入审计预筛免审命中率 ==")
    if j_err:
        print(f"journal 通道: 不可用（{j_err}）")
    elif not args.no_journal:
        exempt, escalated = j_stats.get("exempt", 0), j_stats.get("escalated", 0)
        batches = exempt + escalated
        rate = (exempt / batches) if batches else None
        print(f"journal 通道（--since '{args.since}'）:")
        print(f"  免审批次={exempt} 升级审计批次={escalated} "
              f"工具批次={batches} 命中率={'%.1f%%' % (rate * 100) if rate is not None else 'None（无数据）'}")
        for src, n in sorted(j_stats.get("by_source", {}).items()):
            print(f"    免审来源 {src}: {n}")
    if d_err:
        print(f"账本通道: 不可用（{d_err}）")
    elif not args.no_db:
        print(f"账本通道（{args.db}，全量 tool_call 行）:")
        print(f"  infection:prescreen_exempt 留痕行={d_stats.get('exempt', 0)}")
        for src, n in sorted(d_stats.get("by_source", {}).items()):
            print(f"    免审来源 {src}: {n}")
    # 双通道交叉核对（都可用且计数不一致时如实报差异）
    if not j_err and not d_err and not args.no_journal and not args.no_db:
        if j_stats.get("exempt", 0) != d_stats.get("exempt", 0):
            print(f"⚠️ 双通道免审计数不一致（journal={j_stats.get('exempt', 0)} "
                  f"账本={d_stats.get('exempt', 0)}）——时间窗/进程边界差异，如实报告不取其一")
    return 0


if __name__ == "__main__":
    sys.exit(main())
