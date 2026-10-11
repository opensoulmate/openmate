"""路线图12.6-②（周期#75）：verify_dev 判据弱项——"用 terminal 运行它"运行账本判据。

弱项根因（S1 实证）：verify_dev 只查文件 marker，terminal 步骤被审计误杀（未执行）
仍判 pass = 假过。升级后判据=文件 marker + 运行账本（terminal/execute_code 执行行
args 引用 dev_hello 且 result_preview 含程序输出且非源码回显）+ 回复报告输出。

用例（temp 库注入真实行形态——行形状取自真实库 15 条 dev_hello 轨迹行实证）：
1. 真实形态 terminal 运行行 → 过（S1 修复正形态）
2. S1 回归：文件就绪但只有 write_file 行（terminal 步骤被杀）→ 不过（假过封死）
3. terminal 行被拦（permission=infection:terminate）→ 不过（被拦不计执行）
4. cat/echo 源码回显不算运行（print( 回显）→ 不过
5. execute_code 路径运行 → 过（真实执行路径容忍）
6. terminal 行跑的是无关命令（mkdir 空输出）→ 不过
7. 账本整体无行（unavailable）→ 诚实降级文件+回复、mode_note 标注
8. 运行命中但回复没报告输出 → 不过（双判据都要）
9. "terminal: <cmd>" 冒号前缀形态行 → 计入（CLI 轨迹行实证形态）
10. write_file 行 args 含 marker 不冒充运行（名字过滤）
11. _tool_ledger 多名字一次回读（terminal+execute_code 同批命中）
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import acceptance_benchmark as bench  # noqa: E402
from acceptance_benchmark import verify_dev  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_bench_globals():
    saved = (bench.TOOL_LEDGER_DB, dict(bench._VERIFY_CTX),
             bench._LEDGER_WAIT_S, bench._LEDGER_POLL_S, bench.WORKDIR)
    yield
    (bench.TOOL_LEDGER_DB, ctx, bench._LEDGER_WAIT_S, bench._LEDGER_POLL_S,
     bench.WORKDIR) = saved
    bench._VERIFY_CTX.clear()
    bench._VERIFY_CTX.update(ctx)


_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS trajectory_events ("
    "id INTEGER PRIMARY KEY, session_id TEXT, event_type TEXT, content TEXT,"
    "metadata_json TEXT, status TEXT, created_at TEXT)"
)


def _make_db(tmp_path, rows):
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


def _ctx(db, sid, wait=0.0):
    bench.TOOL_LEDGER_DB = db
    bench._VERIFY_CTX["session_id"] = sid
    bench._VERIFY_CTX["mode_note"] = ""
    bench._LEDGER_WAIT_S = wait
    bench._LEDGER_POLL_S = 0.05


def _workdir(tmp_path):
    wd = tmp_path / "work"
    wd.mkdir()
    (wd / "dev_hello.py").write_text("print('SOULMATE_DEV_OK')\n", encoding="utf-8")
    bench.WORKDIR = str(wd)


# 真实库实证行形态（om-* 会话 dev 类真实轨迹行）
def _terminal_row(result="SOULMATE_DEV_OK \n", name="terminal",
                  cmd="python3 /tmp/soulmate_accept/dev_hello.py",
                  permission="allow"):
    return {"name": name,
            "arguments": str({"command": cmd}),
            "result_preview": result,
            "permission": permission, "cached": False}


def _write_row():
    return {"name": "write_file",
            "arguments": str({"path": "/tmp/soulmate_accept/dev_hello.py",
                              "content": "print('SOULMATE_DEV_OK')\\n"}),
            "result_preview": "已写入 /tmp/soulmate_accept/dev_hello.py (25 字节)",
            "permission": "allow", "cached": False}


_GOOD_REPLY = "已完成：dev_hello.py 输出 SOULMATE_DEV_OK。"


def test_dev_real_run_shape_passes(tmp_path):
    _workdir(tmp_path)
    db = _make_db(tmp_path, [
        ("om-d1", "tool_call", "write_file", _write_row(), "ok"),
        ("om-d1", "tool_call", "terminal", _terminal_row(), "ok"),
    ])
    _ctx(db, "om-d1")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert ok, detail
    assert "marker=命中" in detail and "运行账本=命中" in detail
    assert bench._VERIFY_CTX["mode_note"] == ""


def test_dev_s1_regression_run_killed_fails(tmp_path):
    """S1 回归：terminal 步骤被审计误杀（账本无运行行），文件就绪+回复自称完成
    ——旧判据假过，新判据必须拒。"""
    _workdir(tmp_path)
    db = _make_db(tmp_path, [
        ("om-d2", "tool_call", "write_file", _write_row(), "ok"),
    ])
    _ctx(db, "om-d2")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert not ok, detail
    assert "运行账本=未命中" in detail


def test_dev_blocked_run_not_evidence(tmp_path):
    _workdir(tmp_path)
    row = _terminal_row(permission="infection:terminate")
    db = _make_db(tmp_path, [("om-d3", "tool_call", "terminal", row, "ok")])
    _ctx(db, "om-d3")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert not ok, detail  # 被拦行不计执行证据


def test_dev_source_echo_not_a_run(tmp_path):
    _workdir(tmp_path)
    row = _terminal_row(cmd="cat /tmp/soulmate_accept/dev_hello.py",
                        result="print('SOULMATE_DEV_OK')\n")
    db = _make_db(tmp_path, [("om-d4", "tool_call", "terminal", row, "ok")])
    _ctx(db, "om-d4")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert not ok, detail  # cat 源码回显不算运行
    assert "非源码回显=否" in detail


def test_dev_execute_code_path_accepted(tmp_path):
    _workdir(tmp_path)
    row = _terminal_row(
        name="execute_code",
        cmd="import subprocess; subprocess.run(['python3','/tmp/soulmate_accept/dev_hello.py'])")
    db = _make_db(tmp_path, [("om-d5", "tool_call", "execute_code", row, "ok")])
    _ctx(db, "om-d5")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert ok, detail


def test_dev_unrelated_terminal_run_fails(tmp_path):
    _workdir(tmp_path)
    row = _terminal_row(cmd="mkdir -p /tmp/soulmate_accept", result=" \n")
    db = _make_db(tmp_path, [("om-d6", "tool_call", "terminal", row, "ok")])
    _ctx(db, "om-d6")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert not ok, detail
    assert "引用 dev_hello 的执行行=0" in detail


def test_dev_ledger_unavailable_degrades(tmp_path):
    _workdir(tmp_path)
    db = _make_db(tmp_path, [])  # 会话整体零行=记账链路未写入
    _ctx(db, "om-d7")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert ok, detail  # 诚实降级文件+回复
    assert "降级文件marker+回复辅证" in detail
    assert bench._VERIFY_CTX["mode_note"] == "ledger_unavailable"


def test_dev_reply_output_still_required(tmp_path):
    _workdir(tmp_path)
    db = _make_db(tmp_path, [
        ("om-d8", "tool_call", "terminal", _terminal_row(), "ok"),
    ])
    _ctx(db, "om-d8")
    ok, detail = verify_dev("做完了，输出正确。")  # 回复没报告输出
    assert not ok, detail
    assert "回复报告输出=否" in detail


def test_dev_colon_prefix_name_counted(tmp_path):
    _workdir(tmp_path)
    row = _terminal_row(name="terminal: python3 /tmp/soulmate_accept/dev_hello.py",
                        cmd="")
    db = _make_db(tmp_path, [("om-d9", "tool_call", "terminal", row, "ok")])
    _ctx(db, "om-d9")
    ok, detail = verify_dev(_GOOD_REPLY)
    assert ok, detail


def test_write_file_row_cannot_fake_run(tmp_path):
    """write_file 行 args 含 marker（真实库实证形态）——名字过滤，不冒充运行。"""
    _workdir(tmp_path)
    db = _make_db(tmp_path, [("om-d10", "tool_call", "write_file", _write_row(), "ok")])
    _ctx(db, "om-d10")
    hits, state = bench._tool_ledger(("terminal", "execute_code"))
    assert hits == [] and state == "missing"


def test_tool_ledger_multi_names_one_read(tmp_path):
    db = _make_db(tmp_path, [
        ("om-d11", "tool_call", "terminal", _terminal_row(), "ok"),
        ("om-d11", "tool_call", "execute_code",
         _terminal_row(name="execute_code", cmd="python3 dev_hello.py"), "ok"),
    ])
    _ctx(db, "om-d11")
    hits, state = bench._tool_ledger(("terminal", "execute_code"))
    assert state == "ok"
    assert {h.name for h in hits} == {"terminal", "execute_code"}


def test_verify_dev_source_wire_guard():
    """接线护栏：verify_dev 必须消费运行账本（_DEV_RUN_TOOLS+_tool_ledger），
    不能退回只查文件 marker 的弱判据。"""
    import inspect
    src = inspect.getsource(verify_dev)
    assert "_tool_ledger(_DEV_RUN_TOOLS)" in src
    assert "_DEV_SRC_ECHO_RE" in src
    assert bench._DEV_RUN_TOOLS == ("terminal", "execute_code")
    # 判据门：文件 + 运行 + 回复 三项齐过
    assert "file_ok and run_ok and reply_ok" in src
