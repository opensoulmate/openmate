# -*- coding: utf-8 -*-
"""路线图 13.3 · 注入审计 LLM 往返开销分布基线（可复跑探针）。

单一真源 = journal '[infection-guard] LLM审计完成 verdict=X 耗时=Nms tools=a,b
（累计 … 均值 … 最大 …）' 行（infection_guard._record_audit_ms 每次判词往返恰好
一行，含 timeout/error/unavailable 失败路径——失败往返是最贵的开销形态）。

用法：
    python audit_cost_report.py                    # 默认看 24 小时内
    python audit_cost_report.py --since "6 hours ago"
    python audit_cost_report.py --json             # 机器可读

输出=耗时分布（count/mean/p50/p95/max/min/total）+按 verdict 分布+按工具名归因
（一批多工具共享一次往返，按批内每个工具名各记一次归因——"每工具审计耗时"口径）。
无数据时报 None（禁把"没跑过"当 0ms）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys

# 生产者逐字契约（infection_guard._record_audit_ms）——生产者改措辞探针即红
AUDIT_RE = re.compile(
    r"\[infection-guard\] LLM审计完成 verdict=(\S+) 耗时=(\d+)ms tools=(\S*?)"
    r"（累计 \d+ 次 均值 \d+ms 最大 \d+ms）")
UNITS = ("acp-proxy-a", "acp-proxy-b", "acp-proxy")


def parse_audit_lines(lines) -> list[dict]:
    """纯函数：从 journal 行解析判词往返样本（可单测）。

    去重纪律（prescreen_hitrate_report 同款实测坑）：子进程 stderr 被 ws_acp 中继
    双份记账，同一事件计两次会把样本翻倍——按 "stderr: " 后内层日志行为唯一键去重。
    """
    samples: list[dict] = []
    seen: set[str] = set()
    for line in lines or []:
        inner = line.split("stderr: ", 1)[1] if "stderr: " in line else line
        if inner in seen:
            continue
        m = AUDIT_RE.search(line)
        if not m:
            continue
        seen.add(inner)
        verdict, ms_raw, tools_raw = m.group(1), m.group(2), m.group(3)
        tools = [t for t in tools_raw.split(",") if t and t != "-"]
        samples.append({"verdict": verdict, "ms": int(ms_raw), "tools": tools})
    return samples


def _pct(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def summarize(samples: list[dict]) -> dict:
    """耗时分布汇总（纯函数）：整体+按 verdict+按工具名归因。无数据耗时报 None。"""
    if not samples:
        return {
            "count": 0,
            "ms": {k: None for k in ("mean", "p50", "p95", "max", "min", "total")},
            "by_verdict": {}, "by_tool": {},
        }
    durs = sorted(float(s["ms"]) for s in samples)
    by_verdict: dict[str, dict] = {}
    by_tool: dict[str, dict] = {}
    for s in samples:
        v = by_verdict.setdefault(s["verdict"], {"count": 0, "total_ms": 0})
        v["count"] += 1
        v["total_ms"] += s["ms"]
        for name in s["tools"]:  # 一批多工具共享往返：各记一次归因
            t = by_tool.setdefault(name, {"count": 0, "total_ms": 0})
            t["count"] += 1
            t["total_ms"] += s["ms"]
    for d in (by_verdict, by_tool):
        for v in d.values():
            v["mean_ms"] = round(v["total_ms"] / v["count"], 1)
    return {
        "count": len(samples),
        "ms": {
            "mean": round(sum(durs) / len(durs), 1),
            "p50": round(_pct(durs, 0.50), 1),
            "p95": round(_pct(durs, 0.95), 1),
            "max": round(durs[-1], 1),
            "min": round(durs[0], 1),
            "total": round(sum(durs), 1),
        },
        "by_verdict": by_verdict,
        "by_tool": by_tool,
    }


def read_journal(since: str) -> tuple[list[dict], str]:
    """journalctl 扫描双实例服务日志；失败如实报因。"""
    cmd = ["journalctl", "--user", "--no-pager", "--since", since]
    for u in UNITS:
        cmd += ["-u", f"{u}.service"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=30)
    except Exception as exc:
        return [], f"journalctl 调用失败: {exc}"
    if proc.returncode != 0:
        return [], f"journalctl 退出码 {proc.returncode}: {proc.stderr[:200]}"
    return parse_audit_lines(proc.stdout.splitlines()), ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="注入审计 LLM 往返开销分布基线")
    ap.add_argument("--since", default="24 hours ago")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)
    samples, err = read_journal(args.since)
    if err:
        print(f"⚠️ {err}", file=sys.stderr)
        return 1
    summary = summarize(samples)
    if args.as_json:
        print(json.dumps({"since": args.since, **summary}, ensure_ascii=False, indent=2))
        return 0
    print(f"== 注入审计 LLM 往返开销分布（since={args.since}） ==")
    if not samples:
        print("无判词往返样本（None——无数据不等于 0ms）")
        return 0
    ms = summary["ms"]
    print(f"往返次数: {summary['count']}  总耗时: {ms['total']}ms")
    print(f"均值 {ms['mean']}ms  p50 {ms['p50']}ms  p95 {ms['p95']}ms  "
          f"最大 {ms['max']}ms  最小 {ms['min']}ms")
    print("-- 按 verdict --")
    for v, d in sorted(summary["by_verdict"].items()):
        print(f"  {v}: {d['count']} 次  合计 {d['total_ms']}ms  均值 {d['mean_ms']}ms")
    if summary["by_tool"]:
        print("-- 按工具归因（一批多工具共享往返，各记一次） --")
        for name, d in sorted(summary["by_tool"].items(),
                              key=lambda kv: -kv[1]["total_ms"]):
            print(f"  {name}: {d['count']} 次  合计 {d['total_ms']}ms  均值 {d['mean_ms']}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
