"""自举 KPI（蓝图 P2.3）— 四联指标整合单一真源

任务成功率 / 人工干预率 / edit 反射次数 / compact 后质量

四联 KPI 之前分散在各数据面（benchmark 指标面、trajectory 账本、权限审计、
reflection 库、压缩日志），"有没有变好"没有一个整合面可读。本模块把四个
指标聚合到一个只读计算面：全部从真实落库数据计算，绝不估算/伪造；无样本
时诚实降级（available=False + note 明说原因），不拿 0 冒充"表现良好"。

数据源（逐件真实落库面，非探针自报）：
1. 任务成功率   ← opensoul.db trajectory_events
   回合根 = event_type='user_input' + metadata.kind='turn_root'（start_turn 落点）。
   成功语义（保守且显式）：end_turn status='ok' 且 termination=''（正常完成）。
   其余终止（max_turns/token_budget/external_cancel/user_idle）单列
   termination_counts 透明呈现，不混入成功也不冒充失败率。
2. 人工干预率   ← immune/permission.db perm_decision_audit(requires_human=1)
   + 回合根 termination='external_cancel'（用户手动打断）。
   语义：每回合需要"真人出手"（审批或打断）的比例 = interventions / turns。
3. edit 反射次数 ← ~/.hermes/soulmate/reflection/reflections.db reflections
   （ReflectionEngine.record_reflection 落点）。计数+应用数+平均质量分。
   当前生产库 0 行 = 未接入产生源，如实报 available=False。
4. compact 后质量 ← trajectory_events custom 事件 kind='compact'
   （soulmate_agent 压缩点落点，含 before/after 消息数）。
   质量语义：compact 之后回合的成功率（与 KPI1 同一成功定义），对照
   compact 之前回合的成功率——压缩不伤会话质量才是好 compact（蓝图
   "compact 失败不得破坏会话"同源）。0 样本诚实降级。

只读模块：不建表不写库不改任何生产状态；SQLite 全程 mode=ro + timeout。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("acp-proxy.bootstrap-kpi")

# 成功语义单一真源：正常完成（status=ok 且无终止条件接管）
_SUCCESS_TERMINATION = ""

# 数据源缺省路径（与 soulmate_agent._db_path / ReflectionEngine 同源解析）
_OPENSOUL_DB = Path.home() / "opensoul" / "data" / "opensoul.db"
_PERMISSION_DB = Path.home() / ".hermes" / "opensoul" / "immune" / "permission.db"
_REFLECTION_DB = Path.home() / ".hermes" / "soulmate" / "reflection" / "reflections.db"


def _norm_since(since: float | str | None) -> str | None:
    """since 归一为 ISO 前缀（trajectory created_at 是 ISO 文本；权限/反射是
    epoch REAL——各查询内部自行换算）。返回 None = 不限窗。"""
    if since is None or since == "" or since == 0:
        return None
    if isinstance(since, (int, float)):
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(float(since)))
    s = str(since).strip()
    return s or None


def _iso_to_epoch(iso_prefix: str) -> float:
    try:
        return time.mktime(time.strptime(iso_prefix[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return 0.0


def _rows(db_path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    """只读查询（mode=ro）；库缺失/损坏返回 [] 不冒泡（fire-safe 只读面）。"""
    if not db_path.exists():
        return []
    try:
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            return list(conn.execute(sql, params).fetchall())
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 — 只读聚合面不因单库损坏整体崩
        logger.warning("[bootstrap-kpi] 查询 %s 失败: %s", db_path, e)
        return []


def _turn_roots(db_path: Path, since_iso: str | None) -> list[dict]:
    """回合根行：user_input + kind=turn_root（start_turn 唯一落点）。"""
    sql = (
        "SELECT session_id, status, metadata_json, created_at, end_at FROM trajectory_events "
        "WHERE event_type='user_input' AND metadata_json LIKE '%\"turn_root\"%'"
    )
    params: tuple = ()
    if since_iso:
        sql += " AND created_at >= ?"
        params = (since_iso,)
    out = []
    for sid, status, meta_json, created_at, end_at in _rows(db_path, sql, params):
        try:
            meta = json.loads(meta_json or "{}")
        except Exception:
            meta = {}
        if meta.get("kind") != "turn_root":
            continue  # LIKE 粗筛后按真源字段精筛
        out.append(
            {
                "session_id": sid,
                "status": status or "",
                "termination": str(meta.get("termination", "") or ""),
                "created_at": created_at or "",
                "end_at": end_at or "",
            }
        )
    return out


def _task_success(turn_roots: list[dict]) -> dict[str, Any]:
    total = len(turn_roots)
    closed = [t for t in turn_roots if t["end_at"]]
    open_turns = total - len(closed)
    termination_counts: dict[str, int] = {}
    success = 0
    for t in closed:
        term = t["termination"]
        termination_counts[term or "(normal)"] = termination_counts.get(term or "(normal)", 0) + 1
        if t["status"] == "ok" and term == _SUCCESS_TERMINATION:
            success += 1
    return {
        "available": len(closed) > 0,
        "total_turns": total,
        "closed_turns": len(closed),
        "open_turns": open_turns,
        "success_turns": success,
        "rate": (success / len(closed)) if closed else None,
        "termination_counts": termination_counts,
        "source": "opensoul.db trajectory_events (turn_root)",
        "success_semantics": "已收口回合中 end_turn status=ok 且 termination 空（正常完成）；未收口回合不计入分母",
        "note": "" if closed else "窗口内无已收口回合根样本",
    }


def _human_intervention(
    db_path: Path, perm_db: Path, turn_roots: list[dict], since_iso: str | None
) -> dict[str, Any]:
    turns = len(turn_roots)
    # 审批类干预：强制 ask 到达真人通道的决策行
    sql = "SELECT COUNT(*) FROM perm_decision_audit WHERE requires_human=1"
    params: tuple = ()
    if since_iso:
        sql += " AND created_at >= ?"
        params = (_iso_to_epoch(since_iso),)
    ask_rows = _rows(perm_db, sql, params)
    ask_events = int(ask_rows[0][0]) if ask_rows else 0
    # 打断类干预：用户 external_cancel 终止的回合
    cancel_turns = sum(1 for t in turn_roots if t["termination"] == "external_cancel")
    interventions = ask_events + cancel_turns
    available = turns > 0 or ask_events > 0
    return {
        "available": available,
        "turns": turns,
        "ask_events": ask_events,
        "external_cancel_turns": cancel_turns,
        "interventions": interventions,
        "rate": (interventions / turns) if turns else None,
        "source": "perm_decision_audit(requires_human) + turn_root(termination=external_cancel)",
        "note": "" if available else "窗口内无回合且无审批行",
    }


def _edit_reflection(refl_db: Path, since_iso: str | None) -> dict[str, Any]:
    sql = "SELECT COUNT(*), SUM(applied), AVG(quality_score) FROM reflections"
    params: tuple = ()
    if since_iso:
        sql += " WHERE created_at >= ?"
        params = (_iso_to_epoch(since_iso),)
    rows = _rows(refl_db, sql, params)
    if not rows or rows[0][0] == 0:
        return {
            "available": False,
            "count": 0,
            "applied": 0,
            "avg_quality": None,
            "source": "~/.hermes/soulmate/reflection/reflections.db",
            "note": "反射库 0 行（窗口内无产生源触发：自验结论/补做收口/失败恢复均未发生）",
        }
    count, applied, avg_q = rows[0]
    return {
        "available": True,
        "count": int(count or 0),
        "applied": int(applied or 0),
        "avg_quality": round(float(avg_q), 3) if avg_q is not None else None,
        "source": "~/.hermes/soulmate/reflection/reflections.db",
        "note": "",
    }


def _compact_producer_status(src_path: Path | None = None) -> dict[str, Any]:
    """compact 产生源接线状态探测（21.1 消歧：区分"未触发"与"未接入"）。

    确定性源码检视（soulmate_agent 压缩点），不猜不冒充：
    - wired     = 压缩点既有 should_compress 判定又有 kind="compact" 落账；
    - not_wired = 缺任一（无判定=压缩触发未接线；有判定无落账=KPI 数据源断线）；
    - unknown   = 源码不可读（诚实降级，不冒充任一态）。
    """
    path = src_path or (Path(__file__).resolve().parent / "soulmate_agent.py")
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # 不可读=不可探测，如实报 unknown
        return {"producer_status": "unknown",
                "producer_evidence": f"源码不可读: {type(e).__name__}"}
    has_trigger = "should_compress(" in src
    has_ledger = 'kind="compact"' in src or "kind='compact'" in src
    if has_trigger and has_ledger:
        return {"producer_status": "wired",
                "producer_evidence":
                    f"{path.name} 压缩点含 should_compress 判定+kind=compact 落账"}
    if not has_trigger:
        return {"producer_status": "not_wired",
                "producer_evidence":
                    f"{path.name} 无 should_compress 判定（压缩触发未接线）"}
    return {"producer_status": "not_wired",
            "producer_evidence":
                f"{path.name} 有压缩判定但无 kind=compact 落账（KPI 数据源断线）"}


def _compact_threshold_context() -> dict[str, Any]:
    """压缩触发门槛语境（阈值核账透明化：0 事件须可对照门槛解读）。

    读 ContextCompressor 构造缺省（与 soulmate_agent._ctx_compressor 同源
    `from agent.context_compression import ContextCompressor`）；import/构造
    失败诚实降级 trigger_estimate_tokens=None，不猜数。
    """
    try:
        from agent.context_compression import ContextCompressor
        c = ContextCompressor()
        return {
            "max_tokens": c.max_tokens,
            "compression_threshold": c.compression_threshold,
            "trigger_estimate_tokens": int(c.max_tokens * c.compression_threshold),
        }
    except Exception as e:
        return {"max_tokens": None, "compression_threshold": None,
                "trigger_estimate_tokens": None,
                "threshold_note": f"门槛不可读: {type(e).__name__}"}


def _compact_quality(db_path: Path, since_iso: str | None) -> dict[str, Any]:
    sql = (
        "SELECT session_id, metadata_json, created_at FROM trajectory_events "
        "WHERE event_type='custom' AND metadata_json LIKE '%\"compact\"%'"
    )
    params: tuple = ()
    if since_iso:
        sql += " AND created_at >= ?"
        params = (since_iso,)
    compacts = []
    for sid, meta_json, created_at in _rows(db_path, sql, params):
        try:
            meta = json.loads(meta_json or "{}")
        except Exception:
            meta = {}
        if meta.get("kind") != "compact":
            continue  # LIKE 粗筛后按 kind 精筛（防 termination_condition 等串味）
        compacts.append({"session_id": sid, "meta": meta, "created_at": created_at or ""})

    if not compacts:
        # 21.1 消歧：0 事件区分"产生源未接入（数据源断线）"与"已接线未触发（阈值未到）"
        prod = _compact_producer_status()
        thr = _compact_threshold_context()
        if prod["producer_status"] == "wired":
            note = ("窗口内无 compact 事件（产生源已接线：窗口内未触发压缩——"
                    f"估算门槛约 {thr.get('trigger_estimate_tokens')} token）")
        elif prod["producer_status"] == "not_wired":
            note = (f"窗口内无 compact 事件（产生源未接入：{prod['producer_evidence']}）")
        else:
            note = (f"窗口内无 compact 事件（产生源接线状态无法探测："
                    f"{prod['producer_evidence']}）")
        return {
            "available": False,
            "compact_events": 0,
            "post_compact_turns": 0,
            "post_compact_success_rate": None,
            "pre_compact_success_rate": None,
            "avg_before_count": None,
            "avg_after_count": None,
            **prod,
            **thr,
            "source": "trajectory_events custom kind=compact",
            "note": note,
        }

    # 逐 compact 事件切分同会话回合（按 created_at 文本序可比 ISO）
    turns = _turn_roots(db_path, None)
    post, pre = [], []
    before_counts, after_counts = [], []
    for c in compacts:
        meta = c["meta"]
        if meta.get("before_count") is not None:
            before_counts.append(int(meta["before_count"]))
        if meta.get("after_count") is not None:
            after_counts.append(int(meta["after_count"]))
        for t in turns:
            if t["session_id"] != c["session_id"] or not t["end_at"]:
                continue  # 只算已收口回合（未收口无结局，不冒充成功/失败）
            ok = t["status"] == "ok" and t["termination"] == _SUCCESS_TERMINATION
            if t["created_at"] >= c["created_at"]:
                post.append(ok)
            else:
                pre.append(ok)

    def _rate(flags: list[bool]) -> float | None:
        return (sum(flags) / len(flags)) if flags else None

    return {
        "available": True,
        "compact_events": len(compacts),
        "post_compact_turns": len(post),
        "post_compact_success_rate": _rate(post),
        "pre_compact_success_rate": _rate(pre),
        "avg_before_count": round(sum(before_counts) / len(before_counts), 1) if before_counts else None,
        "avg_after_count": round(sum(after_counts) / len(after_counts), 1) if after_counts else None,
        "producer_status": "wired",  # 有真实 compact 事件落账=产生源实证在工作
        "producer_evidence": f"窗口内 {len(compacts)} 条 kind=compact 事件真实落账",
        **_compact_threshold_context(),
        "source": "trajectory_events custom kind=compact + 同会话回合结局",
        "note": "",
    }


def compute_kpis(
    db_path: str | Path = "",
    permission_db: str | Path = "",
    reflections_db: str | Path = "",
    since: float | str | None = None,
) -> dict[str, Any]:
    """四联 KPI 一次算齐（全部只读、fire-safe、无样本诚实降级）。

    since：窗口起点（epoch 秒或 ISO 前缀），None=全量历史。
    """
    db = Path(db_path) if db_path else _OPENSOUL_DB
    pdb = Path(permission_db) if permission_db else _PERMISSION_DB
    rdb = Path(reflections_db) if reflections_db else _REFLECTION_DB
    since_iso = _norm_since(since)

    turns = _turn_roots(db, since_iso)
    return {
        "generated_at": time.time(),
        "since": since,
        "task_success": _task_success(turns),
        "human_intervention": _human_intervention(db, pdb, turns, since_iso),
        "edit_reflection": _edit_reflection(rdb, since_iso),
        "compact_quality": _compact_quality(db, since_iso),
        "sources": {
            "trajectory_db": str(db),
            "permission_db": str(pdb),
            "reflections_db": str(rdb),
        },
    }
