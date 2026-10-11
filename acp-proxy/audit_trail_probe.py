# -*- coding: utf-8 -*-
"""路线图 12.6 · 轨迹/审计闭环抽查（可复跑探针）。

对 tool_failures.db（失败/恢复/LLM传输记账单一落点）做确定性抽查核对（不写库）：

1. **失败分类行完整性**：kind/action/outcome/attempt 值域核对——
   工具行 kind∈FailureKind 五态、llm: 行 kind∈九态(+unknown 显式列出=分类缺口)、
   action∈{none,retry,fallback,give_up,llm_retry,llm_give_up}（值域外=记账参数
   错位形态，周期#69 WIP 实证 'retry_scheduled'/'failed' 落进 action 列）。
2. **无孤儿行**：收尾行（outcome=exhausted）必须在同一 session_id+tool_name 组内
   有先行恢复尝试行（action∈{retry,fallback} 且 id 更小），收尾无过程=孤儿行；
   llm 记账契约配对核（llm_retry↔retry_scheduled、llm_give_up↔failed）。
3. **llm:dna 归因不串线**：tool_name 必须是 llm:<已知path>（已知面之外=归因串线，
   如 WIP 错位实证 'llm:[Errno -2] Name or service not known'）；llm:dna 行必须带
   [strand_a|strand_b] 归因（11.3 线程化契约）。
4. **llm:dna 真实降级审计行闭环**（周期#61 遗留③）：真实 llm:dna 行存在与否如实报。
5. **ts 单位核**（周期#69③）：毫秒 UTC（≥1e12）与秒单位旧规行分别计数如实报
   （全局时间规范=毫秒存储，存量旧规行不改写历史）。
6. **11.5 溯源降级真实判词触发观察**：opensoul.db trajectory_events 里
   provenance_pass_with_trace / self_heal_pass_with_trace 留痕计数
   （0 = 尚未自然触发，如实报"未触发"，不假造观察结论）。
   ⚠️列名容缺：扫描列取 PRAGMA 实存列（content/arguments_json/metadata_json），
   轨迹账本 metadata 落 metadata_json 列——早期版本只扫 content/arguments_json
   （后者根本不存在）对 [infection-guard] 伪条目是盲的（周期#74 修正）。
7. **判词依据落账抽查**（12.6①）：infection 非 ok 判词的 [infection-guard]
   verdict_basis 落账行核对——真实依据行 / "判词未附依据（纯标签）"诚实标记行
   分别计数；有拦截行（permission infection:terminate|clarify）却无任何
   verdict_basis 落账行的会话=修复前历史缺口，如实计数不改写历史。

用法：
    python audit_trail_probe.py                       # 默认真实库
    python audit_trail_probe.py --db /tmp/x.db       # 指定库
    python audit_trail_probe.py --json                # 机器可读输出

抽查发现=审计发现（如实列出），探针自身跑通与否用 exit code 表达：
0=抽查完成（无论发现多少），1=库不可读等探针故障。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path.home() / ".hermes" / "soulmate" / "memory" / "tool_failures.db"
DEFAULT_EVENTS_DB = Path.home() / "opensoul" / "data" / "opensoul.db"

# ── 值域（与生产代码单一真源对齐，改这里先改生产再改探针） ──────────────
TOOL_KINDS = {"ok", "blocked", "transient", "recoverable", "permanent"}
LLM_KINDS = {
    "rate_limit", "timeout_status", "server_error", "client_error",
    "protocol_error", "read_timeout", "connect_error", "network_error",
    "transient_message", "unknown",
}
ACTIONS = {"none", "retry", "fallback", "give_up", "llm_retry", "llm_give_up"}
# llm 记账契约配对（action ↔ outcome，10.1/11.3 语义）
LLM_ACTION_OUTCOME = {
    "llm_retry": {"retry_scheduled"},
    "llm_give_up": {"failed"},
}
KNOWN_LLM_PATHS = {"chat", "chat_stream", "chat_stream_with_tools", "dna", "gateway"}
STRAND_RE = re.compile(r"\[(strand_[ab])\]")
RECOVERY_ACTIONS = {"retry", "fallback"}

# ts 单位判据：毫秒 UTC（2026 年约 1.79e12）远大于秒值；1e10 为分界保守值
TS_MS_MIN = 10 ** 10

# 判词依据空值判据（12.6①）：verdict_basis 键后跟空字符串。容忍三种落库形态——
# Python repr `'verdict_basis': ''` / JSON `"verdict_basis": ""` / JSON 内再转义
# `\"verdict_basis\": \"\"`（周期#72 实证 arguments 落库为 repr 非 JSON）；每个引号
# 是「可选反斜杠+引号」单元（转义形态引号前各有反斜杠）
_EMPTY_BASIS_RE = re.compile(r"""verdict_basis\\?['"]:\s*(?:\\?['"]){2}""")


def load_rows(conn) -> list[dict]:
    cur = conn.execute(
        "SELECT id, ts, session_id, tool_name, kind, action, attempt, outcome,"
        " error_first_line, note FROM tool_failures ORDER BY id"
    )
    keys = ("id", "ts", "session_id", "tool_name", "kind", "action", "attempt",
            "outcome", "error_first_line", "note")
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def check_classification(rows: list[dict]) -> dict:
    """失败分类行完整性：值域核对 + kind=unknown 分类缺口如实列。"""
    invalid = []
    unknown_kind = []
    for r in rows:
        is_llm = str(r["tool_name"]).startswith("llm:")
        domain = LLM_KINDS if is_llm else TOOL_KINDS
        problems = []
        if not r["kind"] or r["kind"] not in domain:
            problems.append(f"kind={r['kind']!r} 越界")
        if not r["action"] or r["action"] not in ACTIONS:
            problems.append(f"action={r['action']!r} 越界")
        if not r["outcome"]:
            problems.append("outcome 为空")
        try:
            if int(r["attempt"]) < 0:
                problems.append(f"attempt={r['attempt']} 为负")
        except (TypeError, ValueError):
            problems.append(f"attempt={r['attempt']!r} 非整数")
        if problems:
            invalid.append({"id": r["id"], "tool_name": r["tool_name"],
                            "problems": problems})
        if is_llm and r["kind"] == "unknown":
            unknown_kind.append({"id": r["id"], "tool_name": r["tool_name"],
                                 "error": (r["error_first_line"] or "")[:80]})
    return {
        "checked": len(rows),
        "invalid_rows": invalid,
        "unknown_kind_rows": unknown_kind,
        "ok": not invalid,
    }


def check_orphans(rows: list[dict]) -> dict:
    """无孤儿行：收尾行必须有先行恢复尝试过程；llm action↔outcome 契约配对。

    连接口径（真实行形态实证）：尝试行以 step.tool_name 落行（如
    read_file_segment），收尾行以原工具名落行（read_file）——同组匹配按
    session_id +（tool_name 相等 或 note 提及收尾工具名，step.note 含
    "换路径: read_file → read_file_segment" 式原文）+ action∈{retry,fallback}。
    """
    orphans = []
    pairing = []
    for r in rows:
        if r["outcome"] == "exhausted":
            name = r["tool_name"] or ""
            prior = [
                g for g in rows
                if g["id"] < r["id"]
                and (g["session_id"] or "") == (r["session_id"] or "")
                and g["action"] in RECOVERY_ACTIONS
                and (g["tool_name"] == name or (name and name in (g["note"] or "")))
            ]
            if not prior:
                orphans.append({
                    "id": r["id"], "tool_name": r["tool_name"],
                    "reason": "收尾行(exhausted)无先行恢复尝试行",
                })
        if str(r["tool_name"]).startswith("llm:"):
            want = LLM_ACTION_OUTCOME.get(r["action"])
            if want is not None and r["outcome"] not in want:
                pairing.append({
                    "id": r["id"], "tool_name": r["tool_name"],
                    "reason": f"action={r['action']} 与 outcome={r['outcome']} 不配对",
                })
    return {"orphan_rows": orphans, "llm_pairing_violations": pairing,
            "ok": not orphans and not pairing}


def check_llm_attribution(rows: list[dict]) -> dict:
    """llm:dna 归因不串线：tool_name=llm:<已知path> + dna 行带 strand 归因。"""
    misrouted = []
    dna_rows = []
    dna_missing_strand = []
    for r in rows:
        name = str(r["tool_name"] or "")
        if not name.startswith("llm:"):
            continue
        path = name[4:]
        if path not in KNOWN_LLM_PATHS:
            misrouted.append({"id": r["id"], "tool_name": name,
                              "error": (r["error_first_line"] or "")[:80]})
            continue
        if path == "dna":
            dna_rows.append(r["id"])
            text = f"{r['error_first_line'] or ''} {r['note'] or ''}"
            if not STRAND_RE.search(text):
                dna_missing_strand.append({"id": r["id"],
                                           "error": text[:80]})
    return {
        "misrouted_rows": misrouted,
        "dna_rows": len(dna_rows),
        "dna_missing_strand": dna_missing_strand,
        "ok": not misrouted and not dna_missing_strand,
    }


def check_ts_units(rows: list[dict]) -> dict:
    """ts 单位核（周期#69③）：毫秒/秒混存如实计数。"""
    ms_rows = 0
    sec_rows = 0
    for r in rows:
        try:
            ts = float(r["ts"] or 0)
        except (TypeError, ValueError):
            continue
        if ts >= TS_MS_MIN:
            ms_rows += 1
        elif ts > 0:
            sec_rows += 1
    return {"ms_rows": ms_rows, "sec_rows": sec_rows,
            "ok": sec_rows == 0, "note": "存量秒单位行不改写历史，新行毫秒UTC"}


def _traj_text_columns(conn) -> list[str]:
    """轨迹账本可扫文本列（列名容缺）：真实列是 metadata_json（工具账本 metadata
    落点），历史上探针误扫不存在的 arguments_json 对 [infection-guard] 伪条目全盲。"""
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(trajectory_events)")}
    except sqlite3.OperationalError:
        return []
    return [c for c in ("content", "arguments_json", "metadata_json") if c in cols]


def _count_traj_rows(conn, cols: list[str], needle: str) -> int:
    if not cols:
        return 0
    where = " OR ".join(f"{c} LIKE ?" for c in cols)
    cur = conn.execute(
        f"SELECT COUNT(*) FROM trajectory_events WHERE {where}",
        [f"%{needle}%"] * len(cols))
    return cur.fetchone()[0]


def check_provenance_trace(events_db: Path) -> dict:
    """11.5 溯源降级真实判词触发观察：留痕行自然触发计数（0=未触发如实报）。"""
    out = {"provenance_rows": 0, "self_heal_rows": 0, "available": False,
           "note": ""}
    if not Path(events_db).exists():
        out["note"] = f"轨迹库不存在: {events_db}"
        return out
    try:
        conn = sqlite3.connect(str(events_db))
        try:
            cols = _traj_text_columns(conn)
            total = _count_traj_rows(conn, cols, "pass_with_trace")
            prov = _count_traj_rows(conn, cols, "provenance_pass_with_trace")
        finally:
            conn.close()
        out["available"] = True
        out["provenance_rows"] = prov
        out["self_heal_rows"] = max(0, total - prov)
        out["note"] = (f"自然触发观察中（探针可复跑，扫描列={'+'.join(cols) or '无'}）"
                       if total
                       else "尚未自然触发（0 行=如实报，非缺陷）")
    except Exception as e:  # 探针自身故障显式报
        out["note"] = f"轨迹库读取失败: {e}"
    return out


def check_verdict_basis(events_db: Path) -> dict:
    """12.6① 判词依据落账抽查：非 ok 判词的 verdict_basis 落账行核对（只读）。"""
    out = {"basis_rows": 0, "with_basis_rows": 0, "no_basis_marker_rows": 0,
           "intercept_rows": 0, "intercept_sessions_without_basis": 0,
           "empty_basis_rows": 0, "available": False, "note": ""}
    if not Path(events_db).exists():
        out["note"] = f"轨迹库不存在: {events_db}"
        return out
    try:
        conn = sqlite3.connect(str(events_db))
        try:
            cols = _traj_text_columns(conn)
            basis = _count_traj_rows(conn, cols, "verdict_basis")
            marker = _count_traj_rows(conn, cols, "判词未附依据")
            intercept = (_count_traj_rows(conn, cols, "infection:terminate")
                         + _count_traj_rows(conn, cols, "infection:clarify"))
            # 有拦截行却无任何判词落账行的会话（修复前历史账，如实计数）
            gap = 0
            if basis == 0:
                gap = intercept  # 无落账行时按拦截行计（行口径如实报）
            # 空依据行：verdict_basis 键在但值为空（正常路径必落真实依据或诚实
            # 标记，0=健康）。arguments 落库形态有三种（Python repr / JSON /
            # JSON 里再转义），LIKE 配不过来——取行后 Python 正则判空。
            empty = 0
            for c in cols:
                try:
                    cur = conn.execute(
                        f"SELECT {c} FROM trajectory_events"
                        f" WHERE {c} LIKE '%verdict_basis%'")
                    for (cell,) in cur.fetchall():
                        if _EMPTY_BASIS_RE.search(cell or ""):
                            empty += 1
                except sqlite3.OperationalError:
                    continue
        finally:
            conn.close()
        out["available"] = True
        out["basis_rows"] = basis
        out["no_basis_marker_rows"] = marker
        out["empty_basis_rows"] = min(empty, basis)
        out["with_basis_rows"] = max(0, basis - marker - out["empty_basis_rows"])
        out["intercept_rows"] = intercept
        out["intercept_sessions_without_basis"] = gap
        out["note"] = (
            "判词依据落账观察中（探针可复跑）"
            if basis else
            "尚无 verdict_basis 落账行（修复前历史拦截行不改写，如实报）")
    except Exception as e:  # 探针自身故障显式报
        out["note"] = f"轨迹库读取失败: {e}"
    return out


def run_probe(db_path: Path, events_db: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    rows = load_rows(conn)
    conn.close()
    report = {
        "db": str(db_path),
        "rows": len(rows),
        "classification": check_classification(rows),
        "orphans": check_orphans(rows),
        "llm_attribution": check_llm_attribution(rows),
        "ts_units": check_ts_units(rows),
        "dna_closure": {
            "real_dna_rows": sum(
                1 for r in rows
                if str(r["tool_name"]) == "llm:dna"),
            "note": "周期#61遗留③：真实降级审计行存在=闭环",
        },
        "provenance_observation": check_provenance_trace(events_db),
        "verdict_basis": check_verdict_basis(events_db),
    }
    report["dna_closure"]["closed"] = report["dna_closure"]["real_dna_rows"] > 0
    return report


def format_report(rep: dict) -> str:
    lines = [f"# 轨迹/审计闭环抽查（tool_failures.db: {rep['db']}，共{rep['rows']}行）"]
    c = rep["classification"]
    mark = "✅" if c["ok"] else "❌"
    lines.append(f"{mark} 失败分类行完整性：核{c['checked']}行，值域越界 {len(c['invalid_rows'])}，"
                 f"kind=unknown 分类缺口 {len(c['unknown_kind_rows'])}")
    for it in c["invalid_rows"][:10]:
        lines.append(f"   · id={it['id']} {it['tool_name']}: {'; '.join(it['problems'])}")
    for it in c["unknown_kind_rows"][:10]:
        lines.append(f"   · id={it['id']} {it['tool_name']}: {it['error']}")
    o = rep["orphans"]
    mark = "✅" if o["ok"] else "❌"
    lines.append(f"{mark} 无孤儿行：孤儿 {len(o['orphan_rows'])}，llm 契约配对违例 {len(o['llm_pairing_violations'])}")
    for it in (o["orphan_rows"] + o["llm_pairing_violations"])[:10]:
        lines.append(f"   · id={it['id']} {it['tool_name']}: {it['reason']}")
    a = rep["llm_attribution"]
    mark = "✅" if a["ok"] else "❌"
    lines.append(f"{mark} llm 归因串线：越界 tool_name {len(a['misrouted_rows'])}，"
                 f"llm:dna 行 {a['dna_rows']}（缺 strand 归因 {len(a['dna_missing_strand'])}）")
    for it in a["misrouted_rows"][:10]:
        lines.append(f"   · id={it['id']} {it['tool_name']}: {it['error']}")
    for it in a["dna_missing_strand"][:10]:
        lines.append(f"   · id={it['id']} 缺strand: {it['error']}")
    t = rep["ts_units"]
    mark = "✅" if t["ok"] else "⚠️"
    lines.append(f"{mark} ts 单位：毫秒行 {t['ms_rows']} / 秒单位旧行 {t['sec_rows']}（{t['note']}）")
    d = rep["dna_closure"]
    mark = "✅" if d["closed"] else "⚠️"
    lines.append(f"{mark} llm:dna 真实降级审计闭环（周期#61遗留③）：真实行 {d['real_dna_rows']}（{d['note']}）")
    p = rep["provenance_observation"]
    lines.append(f"ℹ️ 11.5 溯源降级留痕观察：provenance {p['provenance_rows']} / "
                 f"self_heal {p['self_heal_rows']} — {p['note']}")
    b = rep.get("verdict_basis", {})
    mark = "✅" if not b.get("empty_basis_rows") else "❌"
    lines.append(f"{mark} 判词依据落账（12.6①）：落账行 {b.get('basis_rows', 0)}"
                 f"（真实依据 {b.get('with_basis_rows', 0)} / 诚实标记 "
                 f"{b.get('no_basis_marker_rows', 0)}），空依据行 "
                 f"{b.get('empty_basis_rows', 0)}，拦截行 {b.get('intercept_rows', 0)}"
                 f"（无落账历史缺口 {b.get('intercept_sessions_without_basis', 0)}）"
                 f" — {b.get('note', '')}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="轨迹/审计闭环抽查探针（只读）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help="tool_failures.db 路径")
    ap.add_argument("--events-db", default=str(DEFAULT_EVENTS_DB),
                    help="opensoul.db 轨迹库路径")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        print(f"❌ 库不可读: {db}", file=sys.stderr)
        return 1
    try:
        rep = run_probe(db, Path(args.events_db))
    except Exception as e:
        print(f"❌ 探针故障: {e}", file=sys.stderr)
        return 1
    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json
          else format_report(rep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
