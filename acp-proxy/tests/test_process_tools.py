# -*- coding: utf-8 -*-
"""路线图 9.2 process 后台进程管理工具测试。

覆盖：start/list/poll/log/kill/write/submit/close 全动作 + 真实子进程 e2e /
禁硬杀长任务（到点只报状态）/ 会话断开不杀（生命周期归系统）/ stdin 跨重启
失联显式报因 / PID 复用守卫 / 日志截断自报 / 校验拒绝 / 注册表跨实例持久 +
接线护栏（6 点）+ __new__ 真实 dispatch 烟测 + lazy-init 构造。
"""
import os
import sqlite3
import time
from pathlib import Path

import pytest

from agent.process_tools import ProcessTools, _proc_starttime


def _tools(tmp_path) -> ProcessTools:
    return ProcessTools(
        db_path=str(tmp_path / "proc.db"),
        log_dir=str(tmp_path / "logs"))


def _wait_status(tools, proc_id, want, timeout=8.0):
    """轮询等待状态（真实子进程异步收尾，避免 flaky 立即断言）。"""
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        out = tools.run("poll", {"proc_id": proc_id})
        last = out
        if want in out:
            return out
        time.sleep(0.1)
    raise AssertionError(f"状态未在{timeout}s内变为{want!r}: {last}")


def _start(tools, command, **kw):
    out = tools.run("start", {"command": command, **kw})
    assert "🚀 后台进程已启动" in out, out
    proc_id = [w for w in out.split() if w.startswith("proc_id=")][0].split("=", 1)[1]
    return proc_id


# ── start / poll / log / list（真实子进程 e2e）────────────────────────

def test_start_poll_log_e2e(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "echo hello-proc-marker")
    out = _wait_status(tools, pid, "exited")
    assert "code=0" in out
    log = tools.run("log", {"proc_id": pid})
    assert "hello-proc-marker" in log
    assert "exited code=0" in log
    listed = tools.run("list", {})
    assert pid in listed
    assert "⚪ exited (code=0)" in listed


def test_start_cwd(tmp_path):
    tools = _tools(tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    pid = _start(tools, "pwd", cwd=str(work))
    _wait_status(tools, pid, "exited")
    assert str(work.resolve()) in tools.run("log", {"proc_id": pid})


def test_list_empty(tmp_path):
    assert "暂无后台进程记录" in _tools(tmp_path).run("list", {})


def test_poll_without_proc_id_lists_all(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "echo x")
    _wait_status(tools, pid, "exited")
    out = tools.run("poll", {})
    assert pid in out


# ── 禁硬杀长任务 + 显式 kill（默认 SIGTERM）───────────────────────────

def test_no_hard_kill_long_running(tmp_path):
    # 铁律：没有任何"到点 SIGKILL"路径——长任务只报状态不杀
    tools = _tools(tmp_path)
    pid = _start(tools, "sleep 30")
    time.sleep(1.0)  # 远超任何内部节拍：若存在自动杀这里就该变 exited
    out = tools.run("poll", {"proc_id": pid})
    assert "🟢 running" in out, out
    tools.run("kill", {"proc_id": pid})
    _wait_status(tools, pid, "exited")


def test_kill_default_sigterm_not_sigkill(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "sleep 30")
    out = tools.run("kill", {"proc_id": pid})
    assert "SIGTERM" in out and "SIGKILL" not in out
    assert "🛑 已停止" in out
    assert "exited" in tools.run("poll", {"proc_id": pid})


def test_kill_force_sigkill(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "sleep 30")
    out = tools.run("kill", {"proc_id": pid, "force": True})
    assert "SIGKILL" in out
    _wait_status(tools, pid, "exited")


def test_kill_process_group_reaps_children(tmp_path):
    # start_new_session=True → pgid=pid，kill 收编 shell 子进程
    tools = _tools(tmp_path)
    pid = _start(tools, "sleep 30 & sleep 30")
    out = tools.run("kill", {"proc_id": pid})
    assert "🛑" in out
    _wait_status(tools, pid, "exited")


def test_kill_exited_idempotent(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "echo bye")
    _wait_status(tools, pid, "exited")
    out = tools.run("kill", {"proc_id": pid})
    assert "已退出" in out and "无需 kill" in out


# ── write / submit / close（stdin 生命周期）───────────────────────────

def test_write_submit_close_stdin_e2e(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "cat")  # 回显 stdin→stdout→日志
    assert "已写入" in tools.run("write", {"proc_id": pid, "data": "abc"})
    assert "写入并回车" in tools.run("submit", {"proc_id": pid, "data": "def"})
    assert "已关闭" in tools.run("close", {"proc_id": pid})
    _wait_status(tools, pid, "exited")
    log = tools.run("log", {"proc_id": pid})
    assert "abc" in log and "def" in log


def test_stdin_unavailable_after_restart_explicit_error(tmp_path):
    # 重启语义：新实例无 stdin 句柄 → 显式报因，禁假装成功
    tools = _tools(tmp_path)
    pid = _start(tools, "cat")
    tools2 = _tools(tmp_path)  # 模拟代理重启后的实例
    out = tools2.run("submit", {"proc_id": pid, "data": "hi"})
    assert "stdin 不可用" in out
    tools.run("kill", {"proc_id": pid})


def test_close_twice_reports_unavailable(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "cat")
    assert "已关闭" in tools.run("close", {"proc_id": pid})
    assert "stdin 已不可用" in tools.run("close", {"proc_id": pid})
    tools.run("kill", {"proc_id": pid})


# ── PID 复用守卫 + 跨实例持久 ─────────────────────────────────────────

def test_pid_reuse_guard(tmp_path):
    # 存活的陌生 PID + starttime 不匹配 → 不把自己的状态安给它（报 exited）
    tools = _tools(tmp_path)
    with sqlite3.connect(tools._db_path) as db:
        db.execute(
            "INSERT INTO processes (proc_id, pid, starttime, command, cwd, "
            "log_path, started_at_ms, exit_code, session_id, stdin_open) "
            "VALUES ('proc-fake', ?, '999999', 'fake', '', '', ?, NULL, '', 0)",
            (os.getpid(), int(time.time() * 1000)))
        db.commit()
    out = tools.run("poll", {"proc_id": "proc-fake"})
    assert "exited" in out and "running" not in out
    # 对照组：starttime 一致 = 真 running（本进程自身）
    with sqlite3.connect(tools._db_path) as db:
        db.execute(
            "INSERT INTO processes (proc_id, pid, starttime, command, cwd, "
            "log_path, started_at_ms, exit_code, session_id, stdin_open) "
            "VALUES ('proc-real', ?, ?, 'self', '', '', ?, NULL, '', 0)",
            (os.getpid(), str(_proc_starttime(os.getpid())),
             int(time.time() * 1000)))
        db.commit()
    assert "🟢 running" in tools.run("poll", {"proc_id": "proc-real"})


def test_registry_persists_across_instances(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "echo persisted")
    _wait_status(tools, pid, "exited")
    tools2 = _tools(tmp_path)  # 重启后实例：注册表仍在
    out = tools2.run("list", {})
    assert pid in out and "code=0" in out  # exit_code 已持久化
    assert "persisted" in tools2.run("log", {"proc_id": pid})


def test_unknown_pid_in_registry_reports_exited_honestly(tmp_path):
    # 无句柄 + /proc 不存在 → exited（code 不可知如实标注）
    tools = _tools(tmp_path)
    pid = _start(tools, "echo done")
    _wait_status(tools, pid, "exited")
    tools2 = _tools(tmp_path)
    out = tools2.run("poll", {"proc_id": pid})
    assert "exited" in out


# ── log 截断自报 ─────────────────────────────────────────────────────

def test_log_char_truncation_self_report(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "seq 1 20000")
    _wait_status(tools, pid, "exited")
    out = tools.run("log", {"proc_id": pid, "limit": 5})
    assert "⚠️ 日志过长已截断自报" in out
    assert out.strip().splitlines()[-1] == "20000"


def test_log_line_limit_clamped(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "seq 1 100")
    _wait_status(tools, pid, "exited")
    out = tools.run("log", {"proc_id": pid, "limit": 3})
    lines = out.splitlines()
    assert "返回3行" in lines[0]
    assert lines[-3:] == ["98", "99", "100"]


def test_log_missing_file_explicit_error(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "echo gone")
    _wait_status(tools, pid, "exited")
    os.remove(tools._row(pid)["log_path"])
    assert "日志文件不存在" in tools.run("log", {"proc_id": pid})


# ── 校验拒绝（显式报因禁静默）────────────────────────────────────────

def test_empty_command_rejected(tmp_path):
    out = _tools(tmp_path).run("start", {"command": ""})
    assert out == "错误: command 参数不能为空"


def test_unknown_action_rejected(tmp_path):
    out = _tools(tmp_path).run("explode", {})
    assert "错误: 未知 action" in out and "start" in out


def test_unknown_proc_id_rejected(tmp_path):
    tools = _tools(tmp_path)
    for action, params in (("poll", {}), ("log", {}), ("kill", {}),
                           ("write", {"data": "x"}), ("close", {})):
        params = dict(params, proc_id="proc-nope")
        assert f"错误: 未知 proc_id: proc-nope" in tools.run(action, params)


def test_missing_proc_id_rejected(tmp_path):
    tools = _tools(tmp_path)
    assert "proc_id 参数不能为空" in tools.run("log", {})
    assert "proc_id 参数不能为空" in tools.run("kill", {})


def test_write_missing_data_rejected(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "cat")
    assert "需要 data 参数" in tools.run("write", {"proc_id": pid})
    tools.run("kill", {"proc_id": pid})


def test_write_to_exited_rejected(tmp_path):
    tools = _tools(tmp_path)
    pid = _start(tools, "echo bye")
    _wait_status(tools, pid, "exited")
    assert "进程已退出" in tools.run("submit", {"proc_id": pid, "data": "x"})


def test_bad_cwd_rejected(tmp_path):
    out = _tools(tmp_path).run(
        "start", {"command": "echo x", "cwd": str(tmp_path / "nope")})
    assert "cwd 不存在" in out


# ── 接线护栏 + dispatch 烟测 ─────────────────────────────────────────

def _src() -> str:
    return (Path(__file__).resolve().parent.parent
            / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_source_guards():
    src = _src()
    # 顶部 import
    assert "from agent.process_tools import ProcessTools" in src
    # 工具定义进 LLM 工具面
    assert '"name": "process"' in src
    assert "builtin_tools.extend(process_tool)" in src
    # 主循环 dispatch + code-mode 批内 dispatch 两处调用 + 共享入口定义
    assert src.count("self._process_dispatch(func_args, session_id)") == 2
    assert "def _process_dispatch" in src
    # 系统提示工具清单
    assert "- process: 后台进程管理" in src


def test_real_dispatch_smoke(tmp_path):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent._process_tools_inst = ProcessTools(
        db_path=str(tmp_path / "p.db"), log_dir=str(tmp_path / "logs"))
    out = agent._process_dispatch({"action": "start", "command": "echo smoke-ok"},
                                  "sess-1")
    assert "🚀 后台进程已启动" in out and "proc_id=proc-" in out
    pid = [w for w in out.split() if w.startswith("proc_id=")][0].split("=", 1)[1]
    assert pid in agent._process_dispatch({"action": "poll"}, "sess-1")
    # 校验失败走同一入口如实报因
    assert "command 参数不能为空" in agent._process_dispatch(
        {"action": "start", "command": ""}, "sess-1")


def test_lazy_init_construct(tmp_path, monkeypatch):
    # 无预置实例时 lazy-init 真实构造 ProcessTools（走环境变量隔离路径）
    monkeypatch.setenv("SOULMATE_PROCESS_DB_PATH", str(tmp_path / "lazy.db"))
    monkeypatch.setenv("SOULMATE_PROCESS_LOG_DIR", str(tmp_path / "lazy-logs"))
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    out = agent._process_dispatch({"action": "list"}, "s")
    assert "暂无后台进程记录" in out
    assert os.path.exists(tmp_path / "lazy.db")
