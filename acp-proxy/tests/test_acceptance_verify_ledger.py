"""路线图12.2（周期#66）：运维/调研 verify 升级 server_side——工具账本真源判据。

判据真源=opensoul.db trajectory_events（trajectory_recorder 真实回合已写入的
tool_call 轨迹账本，复用既有落账零新增落库负担）：
- 运维=体检结果落账（system_checkup 账本行 result_preview 含 8090+磁盘结论）
- 调研=检索调用账本+来源行（web_search 账本行 result_preview 含来源 URL）
用例（temp 库注入真实行形态，非 mock 验证器内部）：
1. 运维账本命中+回复辅证 → 过（server_side 形态）
2. 运维账本缺行（会话有其他工具行=missing）→ 不过（工具真没调不假过）
3. 运维账本整体无行（unavailable）→ 诚实降级回复辅证+mode_note 标注
4. 运维账本命中但回复缺事实 → 不过（双判据都要）
5. 调研来源行命中+回复事实 → 过
6. 调研 web_search 行无来源 URL → 不过
7. 会话隔离：他会话同工具行不计（陈旧行假阳性防线）
8. 被拦行（permission=sandbox-restricted）不计执行证据
9. 迟到落账：行晚于调用落库，有界轮询等到（竞态假阴性防线）
10. 接线护栏：运维/调研 ∈ SERVER_SIDE、main 注入 _VERIFY_CTX 会话上下文、
    降级时 verify_mode 不假称 server_side
"""
import inspect
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import acceptance_benchmark as bench  # noqa: E402
from acceptance_benchmark import verify_ops, verify_research  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_bench_globals():
    """测试期模块全局态隔离：TOOL_LEDGER_DB/_VERIFY_CTX/_LEDGER_* 用后即还原，
    防跨测试污染（离线调用 verify_* 的既有测试依赖空会话上下文=诚实降级）。"""
    saved = (bench.TOOL_LEDGER_DB, dict(bench._VERIFY_CTX),
             bench._LEDGER_WAIT_S, bench._LEDGER_POLL_S)
    yield
    (bench.TOOL_LEDGER_DB, ctx, bench._LEDGER_WAIT_S, bench._LEDGER_POLL_S) = saved
    bench._VERIFY_CTX.clear()
    bench._VERIFY_CTX.update(ctx)

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS trajectory_events ("
    "id INTEGER PRIMARY KEY, session_id TEXT, event_type TEXT, content TEXT,"
    "metadata_json TEXT, status TEXT, created_at TEXT)"
)

_OPS_PREVIEW = (
    "🩺 系统体检：✅ ok（4项：ok=4 warn=0 critical=0 unknown=0）\n\n"
    "[ports]\n  ✅ 8090（opensoul）: 本地监听中\n  ✅ 8092（acp-proxy-a）: 本地监听中\n\n"
    "[disk]\n  ✅ /: 已用82.1%（可用29G）"
)
_SRC_PREVIEW = (
    "- Welcome to Python.org: https://www.python.org/\n"
    "  The mission of the Python Software Foundation\n"
    "- Python Release Python 3.13.14 | Python.org: https://www.python.org/downloads/"
)
_GOOD_OPS_REPLY = "体检完成：8090 端口在监听，磁盘已用 82.1%。"
_GOOD_RESEARCH_REPLY = "Python 3.13 于 2024 年 10 月 7 日正式发布。"


def _make_db(tmp_path, rows):
    """rows: [(session_id, event_type, content, metadata_dict, status)]"""
    db = tmp_path / "ledger.db"
    conn = sqlite3.connect(db)
    conn.execute(_SCHEMA)
    for sid, etype, content, meta, status in rows:
        conn.execute(
            "INSERT INTO trajectory_events (session_id, event_type, content,"
            " metadata_json, status, created_at) VALUES (?,?,?,?,?,?)",
            (sid, etype, content, json.dumps(meta, ensure_ascii=False), status,
             "2026-10-11T00:00:00+00:00"))
    conn.commit()
    conn.close()
    return str(db)


def _ctx(monkey_db, sid, wait=0.0):
    bench.TOOL_LEDGER_DB = monkey_db
    bench._VERIFY_CTX["session_id"] = sid
    bench._VERIFY_CTX["mode_note"] = ""
    bench._LEDGER_WAIT_S = wait
    bench._LEDGER_POLL_S = 0.05


def _tool_row(name, preview, permission="allow"):
    return {"name": name, "arguments": "{}", "result_preview": preview,
            "permission": permission, "cached": False}


# ── 运维：体检结果落账 ────────────────────────────────────────────


def test_ops_ledger_hit_passes(tmp_path):
    db = _make_db(tmp_path, [
        ("om-t1", "tool_call", "system_checkup", _tool_row("system_checkup", _OPS_PREVIEW), "ok"),
    ])
    _ctx(db, "om-t1")
    ok, detail = verify_ops(_GOOD_OPS_REPLY)
    assert ok, detail
    assert "体检结果落账=命中" in detail and "回复含8090=是" in detail
    assert bench._VERIFY_CTX["mode_note"] == ""


def test_ops_ledger_missing_tool_fails(tmp_path):
    # 会话有其他工具行（=记账链路活着）但无 system_checkup → 工具真没调，不假过
    db = _make_db(tmp_path, [
        ("om-t2", "tool_call", "memory_read", _tool_row("memory_read", "记忆内容"), "ok"),
    ])
    _ctx(db, "om-t2")
    ok, detail = verify_ops(_GOOD_OPS_REPLY)
    assert not ok, detail
    assert "体检结果落账=未命中" in detail


def test_ops_ledger_unavailable_degrades(tmp_path):
    db = _make_db(tmp_path, [])  # 会话整体零行=记账链路未写入
    _ctx(db, "om-t3")
    ok, detail = verify_ops(_GOOD_OPS_REPLY)
    assert ok, detail  # 诚实降级回复辅证
    assert "降级回复辅证" in detail
    assert bench._VERIFY_CTX["mode_note"] == "ledger_unavailable"


def test_ops_reply_still_required(tmp_path):
    db = _make_db(tmp_path, [
        ("om-t4", "tool_call", "system_checkup", _tool_row("system_checkup", _OPS_PREVIEW), "ok"),
    ])
    _ctx(db, "om-t4")
    ok, detail = verify_ops("体检完成，一切正常。")  # 回复缺 8090/磁盘事实
    assert not ok, detail


# ── 调研：检索调用账本+来源行 ────────────────────────────────────


def test_research_ledger_source_line_passes(tmp_path):
    db = _make_db(tmp_path, [
        ("om-t5", "tool_call", "web_search", _tool_row("web_search", _SRC_PREVIEW), "ok"),
    ])
    _ctx(db, "om-t5")
    ok, detail = verify_research(_GOOD_RESEARCH_REPLY)
    assert ok, detail
    assert "检索调用账本+来源行=命中" in detail


def test_research_no_source_line_fails(tmp_path):
    db = _make_db(tmp_path, [
        ("om-t6", "tool_call", "web_search", _tool_row("web_search", "无结果"), "ok"),
    ])
    _ctx(db, "om-t6")
    ok, detail = verify_research(_GOOD_RESEARCH_REPLY)
    assert not ok, detail
    assert "来源行=未命中" in detail


def test_research_session_isolation(tmp_path):
    # 他会话的同工具行=陈旧行，不许给本会话借证据（陈旧行假阳性防线）：
    # 账本回读必须是 0 命中；本会话整体无行=unavailable 诚实降级，绝不"命中"。
    db = _make_db(tmp_path, [
        ("om-other", "tool_call", "web_search", _tool_row("web_search", _SRC_PREVIEW), "ok"),
    ])
    _ctx(db, "om-t7")
    hits, state = bench._tool_ledger("web_search")
    assert hits == [] and state == "unavailable", (hits, state)
    ok, detail = verify_research(_GOOD_RESEARCH_REPLY)
    assert "来源行=命中" not in detail  # 他会话行不构成命中


def test_blocked_row_not_executed(tmp_path):
    db = _make_db(tmp_path, [
        ("om-t8", "tool_call", "system_checkup",
         _tool_row("system_checkup", _OPS_PREVIEW, permission="sandbox-restricted"), "ok"),
    ])
    _ctx(db, "om-t8")
    ok, detail = verify_ops(_GOOD_OPS_REPLY)
    assert not ok, detail  # 被拦行不计执行证据（missing 语义）


def test_late_landing_row_recovered_by_poll(tmp_path):
    db = _make_db(tmp_path, [])  # 先空库，后台线程晚落账

    def _late_insert():
        time.sleep(0.2)
        conn = sqlite3.connect(db)
        conn.execute(_SCHEMA)
        conn.execute(
            "INSERT INTO trajectory_events (session_id, event_type, content,"
            " metadata_json, status, created_at) VALUES (?,?,?,?,?,?)",
            ("om-t9", "tool_call", "system_checkup",
             json.dumps(_tool_row("system_checkup", _OPS_PREVIEW), ensure_ascii=False),
             "ok", "2026-10-11T00:00:00+00:00"))
        conn.commit()
        conn.close()

    th = threading.Thread(target=_late_insert)
    th.start()
    _ctx(db, "om-t9", wait=3.0)
    ok, detail = verify_ops(_GOOD_OPS_REPLY)
    th.join()
    assert ok, detail  # 有界轮询等到迟到落账，不误判 missing
    assert "体检结果落账=命中" in detail


# ── 接线护栏 ─────────────────────────────────────────────────────


def test_server_side_membership():
    assert "运维" in bench.SERVER_SIDE and "调研" in bench.SERVER_SIDE
    # 12.2 完成后 10 类全部服务端判据
    assert bench.SERVER_SIDE == {t["category"] for t in bench.TASKS}


def test_main_wires_session_ctx_and_degraded_mode():
    src = inspect.getsource(bench.main)
    assert '_VERIFY_CTX["session_id"] = sid' in src, "main 未注入会话上下文"
    assert 'mode_note' in src, "main 未处理账本判据降级标注"
    # 降级时 verify_mode 不假称 server_side（两处 mode 计算点都要处理）
    assert src.count('mode_note') >= 2


def test_verifiers_reset_mode_note(tmp_path):
    db = _make_db(tmp_path, [
        ("om-t10", "tool_call", "system_checkup", _tool_row("system_checkup", _OPS_PREVIEW), "ok"),
    ])
    _ctx(db, "om-t10")
    bench._VERIFY_CTX["mode_note"] = "stale"
    verify_ops(_GOOD_OPS_REPLY)
    assert bench._VERIFY_CTX["mode_note"] == ""  # 命中路径复位陈旧降级标记
