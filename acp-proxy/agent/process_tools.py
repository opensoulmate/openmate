"""process 后台进程管理工具（路线图 9.2）。

对标 Hermes process 语义的有界子集：start/list/poll/log/kill/write/submit/close。
后台任务**可见可管**（用户极度重视可观测性）：

- start = 后台启动 shell 命令（stdout/stderr 落日志文件），返回进程句柄 proc_id
- list/poll = 注册表 + 真实进程状态（running/exited），PID 复用有守卫（starttime 比对）
- log = 日志尾部（行数/字节双限，截断自报）
- kill = 显式停止：默认 SIGTERM（进程组），force=true 才 SIGKILL
- write/submit/close = 进程 stdin 写入（submit=写入+回车）、关闭 stdin

铁律对齐：
- **禁硬杀长任务**：本工具没有任何"到点 SIGKILL"路径——超时只报状态不杀
  （与 turn_watchdog 软预算/观察窗哲学一致）；kill 仅是显式意图，默认还是 SIGTERM。
- **子进程生命周期归系统（观察窗哲学）**：start_new_session=True 脱离会话进程组，
  会话断开/代理重启不杀后台进程；注册表 SQLite 持久（~/.hermes/soulmate/memory/
  processes.db），重启后 list/poll/log 照常可用（SOULMATE_PROCESS_DB_PATH /
  SOULMATE_PROCESS_LOG_DIR 可覆盖存储位置）。
- 显式报因禁静默：空 command/未知 action/未知 proc_id/stdin 不可用（重启后旧
  进程无句柄）/日志缺失都明确返回原因。

write/submit 只对**本代理进程启动且 stdin 未关闭**的进程可用（stdin 句柄在内存里，
重启即失联——如实报"stdin 不可用"，不假装成功）。
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import time
import uuid

DEFAULT_DB_PATH = os.path.expanduser(
    "~/.hermes/soulmate/memory/processes.db")
DEFAULT_LOG_DIR = os.path.expanduser("~/.hermes/soulmate/processes")

_DB_TIMEOUT_S = 30
_MAX_LOG_CHARS = 20000        # 单次 log 返回字节上限（超出截尾自报）
_MAX_LOG_LINES_DEFAULT = 200  # 默认返回行数
_MAX_LOG_LINES = 2000
_KILL_WAIT_S = 3.0            # kill 后等待退出的有界时长（不硬杀）

ACTIONS = ("start", "list", "poll", "log", "kill", "write", "submit", "close")


def _proc_starttime(pid: int):
    """读 /proc/<pid>/stat 第22字段 starttime（PID 复用守卫用）。失败返回 None。"""
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8",
                  errors="replace") as f:
            data = f.read()
        # comm 字段可含空格/括号——取最后一个 ')' 之后再分割
        tail = data[data.rfind(")") + 1:].split()
        return int(tail[19])  # ')' 后第20个字段 = stat 第22字段
    except Exception:
        return None


class ProcessTools:
    """后台进程注册表 + 生命周期管理（确定性操作，无 LLM 参与）。"""

    def __init__(self, db_path: str | None = None,
                 log_dir: str | None = None):
        self._db_path = (
            db_path or os.environ.get("SOULMATE_PROCESS_DB_PATH") or
            DEFAULT_DB_PATH)
        self._log_dir = (
            log_dir or os.environ.get("SOULMATE_PROCESS_LOG_DIR") or
            DEFAULT_LOG_DIR)
        self._procs: dict = {}   # proc_id -> Popen（本代理进程启动的才有）
        self._stdins: dict = {}  # proc_id -> stdin 句柄（write/submit 可用）
        os.makedirs(self._log_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    # ── 存储 ─────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._db_path, timeout=_DB_TIMEOUT_S)
        db.row_factory = sqlite3.Row
        return db

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS processes ("
                "proc_id TEXT PRIMARY KEY, pid INTEGER, starttime TEXT, "
                "command TEXT, cwd TEXT, log_path TEXT, "
                "started_at_ms INTEGER, exit_code INTEGER, "
                "session_id TEXT, stdin_open INTEGER DEFAULT 0)"
            )
            db.commit()

    def _row(self, proc_id: str):
        with self._connect() as db:
            cur = db.execute(
                "SELECT * FROM processes WHERE proc_id=?", (proc_id,))
            return cur.fetchone()

    def _rows(self, limit: int = 50):
        with self._connect() as db:
            cur = db.execute(
                "SELECT * FROM processes ORDER BY started_at_ms DESC LIMIT ?",
                (limit,))
            return cur.fetchall()

    def _set_exit(self, proc_id: str, code) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE processes SET exit_code=?, stdin_open=0 "
                "WHERE proc_id=? AND exit_code IS NULL", (code, proc_id))
            db.commit()

    # ── 状态判定（真实进程状态，PID 复用守卫）────────────────────

    def _status(self, row) -> tuple:
        """返回 (status, exit_code)。status ∈ running/exited。

        - 本代理进程启动的：Popen.poll() 权威（顺手落 exit_code）
        - 重启后无句柄的：/proc 存活 + starttime 一致才算 running；
          starttime 不一致 = PID 已复用，原进程已退出（如实报，不误判）
        """
        proc_id = row["proc_id"]
        popen = self._procs.get(proc_id)
        if popen is not None:
            rc = popen.poll()
            if rc is None:
                return "running", None
            self._set_exit(proc_id, rc)
            return "exited", rc
        pid = row["pid"]
        stored = row["starttime"]
        alive = os.path.isdir(f"/proc/{pid}") if pid else False
        if alive:
            current = _proc_starttime(pid)
            if stored is not None and str(current) == str(stored):
                return "running", None
            # PID 复用 / 无法核对 → 不把陌生进程当自己的
            return "exited", row["exit_code"]
        return "exited", row["exit_code"]

    def _fmt_row(self, row, with_command: bool = True) -> str:
        status, code = self._status(row)
        elapsed_ms = int(time.time() * 1000) - (row["started_at_ms"] or 0)
        elapsed = f"{elapsed_ms / 1000:.1f}s"
        if status == "running":
            state = "🟢 running"
        elif code is not None:
            state = f"⚪ exited (code={code})"
        else:
            state = "⚪ exited (code不可知：进程非本代理进程所启动/已重启)"
        line = (f"{row['proc_id']} | {state} | pid={row['pid']} | "
                f"elapsed={elapsed}")
        if with_command:
            cmd = row["command"] or ""
            if len(cmd) > 80:
                cmd = cmd[:80] + f"…（截断，全文{len(row['command'])}字）"
            line += f" | {cmd}"
        return line

    # ── 动作 ─────────────────────────────────────────────────────

    def run(self, action: str, params: dict, session_id: str = "") -> str:
        action = str(action or "").strip().lower()
        if action not in ACTIONS:
            return (f"错误: 未知 action: {action or '(空)'}；"
                    f"可用: {', '.join(ACTIONS)}")
        if action == "start":
            return self._start(params, session_id)
        if action == "list":
            return self._list()
        if action == "poll":
            return self._poll(params)
        if action == "log":
            return self._log(params)
        if action == "kill":
            return self._kill(params)
        if action in ("write", "submit"):
            return self._write(params, newline=(action == "submit"))
        return self._close(params)

    def _start(self, params: dict, session_id: str) -> str:
        command = str(params.get("command", "") or "").strip()
        if not command:
            return "错误: command 参数不能为空"
        cwd = str(params.get("cwd", "") or "").strip()
        if cwd:
            cwd = os.path.realpath(os.path.expanduser(cwd))
            if not os.path.isdir(cwd):
                return f"错误: cwd 不存在或不是目录: {cwd}"
        else:
            cwd = None

        proc_id = f"proc-{uuid.uuid4().hex[:8]}"
        log_path = os.path.join(self._log_dir, f"{proc_id}.log")
        logf = open(log_path, "ab")
        try:
            popen = subprocess.Popen(
                command, shell=True, cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=logf, stderr=subprocess.STDOUT,
                start_new_session=True,  # 脱离会话进程组：生命周期归系统
            )
        except Exception as e:
            logf.close()
            return f"错误: 后台进程启动失败: {e}"
        logf.close()

        self._procs[proc_id] = popen
        self._stdins[proc_id] = popen.stdin
        with self._connect() as db:
            db.execute(
                "INSERT INTO processes (proc_id, pid, starttime, command, "
                "cwd, log_path, started_at_ms, exit_code, session_id, "
                "stdin_open) VALUES (?,?,?,?,?,?,?,?,?,1)",
                (proc_id, popen.pid,
                 str(_proc_starttime(popen.pid) or ""), command, cwd or "",
                 log_path, int(time.time() * 1000), None,
                 str(session_id or "")))
            db.commit()
        return (f"🚀 后台进程已启动: proc_id={proc_id} pid={popen.pid}\n"
                f"命令: {command}\n"
                f"日志: {log_path}\n"
                f"跟进: process action=poll proc_id={proc_id} 查状态 / "
                f"action=log 看输出；不随会话断开而终止")

    def _list(self) -> str:
        rows = self._rows()
        if not rows:
            return "（暂无后台进程记录）"
        lines = [f"后台进程 {len(rows)} 个（最近50条）："]
        lines += [self._fmt_row(r) for r in rows]
        return "\n".join(lines)

    def _poll(self, params: dict) -> str:
        proc_id = str(params.get("proc_id", "") or "").strip()
        if not proc_id:
            # 无 proc_id = 全量状态（与 list 同源，但只报状态不带命令全文）
            rows = self._rows()
            if not rows:
                return "（暂无后台进程记录）"
            return "\n".join(self._fmt_row(r) for r in rows)
        row = self._row(proc_id)
        if row is None:
            return f"错误: 未知 proc_id: {proc_id}"
        return self._fmt_row(row)

    def _log(self, params: dict) -> str:
        proc_id = str(params.get("proc_id", "") or "").strip()
        if not proc_id:
            return "错误: proc_id 参数不能为空"
        row = self._row(proc_id)
        if row is None:
            return f"错误: 未知 proc_id: {proc_id}"
        log_path = row["log_path"]
        if not log_path or not os.path.exists(log_path):
            return f"错误: 日志文件不存在: {log_path}"
        try:
            limit = int(params.get("limit", _MAX_LOG_LINES_DEFAULT))
        except Exception:
            limit = _MAX_LOG_LINES_DEFAULT
        limit = max(1, min(limit, _MAX_LOG_LINES))
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        truncated_char = False
        if len(text) > _MAX_LOG_CHARS:
            text = text[-_MAX_LOG_CHARS:]
            truncated_char = True
        lines = text.splitlines()
        truncated_line = len(lines) > limit
        tail = lines[-limit:]
        out = []
        status, code = self._status(row)
        state = "running" if status == "running" else (
            f"exited code={code}" if code is not None else
            "exited code=不可知")
        out.append(f"📜 {proc_id} 日志（状态 {state}，"
                   f"共{len(lines)}行，返回{len(tail)}行）：")
        if truncated_char or truncated_line:
            out.append(f"⚠️ 日志过长已截断自报（仅显示末尾 "
                       f"{len(tail)} 行{'/字节上限' if truncated_char else ''}）")
        out.extend(tail)
        return "\n".join(out)

    def _kill(self, params: dict) -> str:
        proc_id = str(params.get("proc_id", "") or "").strip()
        if not proc_id:
            return "错误: proc_id 参数不能为空"
        row = self._row(proc_id)
        if row is None:
            return f"错误: 未知 proc_id: {proc_id}"
        status, code = self._status(row)
        if status != "running":
            return (f"⚪ 进程已退出（{proc_id}"
                    f"{'' if code is None else f' code={code}'}），无需 kill")
        force = str(params.get("force", "")).lower() in ("1", "true", "yes", "on") \
            if not isinstance(params.get("force"), bool) else params.get("force")
        sig = signal.SIGKILL if force else signal.SIGTERM
        sig_name = "SIGKILL" if force else "SIGTERM"
        pid = row["pid"]
        try:
            # start_new_session=True → pgid == pid，杀进程组收编子进程
            os.killpg(pid, sig)
        except ProcessLookupError:
            self._set_exit(proc_id, None)
            return f"⚪ 进程 {proc_id} 已退出（kill 时已不存在）"
        except Exception as e:
            return f"错误: kill 失败: {e}"
        # 有界等待退出（不硬杀：SIGTERM 后最多等 _KILL_WAIT_S 报状态）
        deadline = time.time() + _KILL_WAIT_S
        while time.time() < deadline:
            st, _ = self._status(row)
            if st != "running":
                break
            time.sleep(0.1)
        st, code2 = self._status(row)
        self._stdins.pop(proc_id, None)
        if st == "running":
            return (f"🛑 已发送 {sig_name} 至 {proc_id} (pid={pid})，"
                    f"{_KILL_WAIT_S:.0f}s 内仍在运行（不硬杀，可再查 poll）")
        return (f"🛑 已停止 {proc_id} (pid={pid})，信号 {sig_name}"
                f"{'' if code2 is None else f'，退出码 {code2}'}")

    def _write(self, params: dict, newline: bool) -> str:
        verb = "submit" if newline else "write"
        proc_id = str(params.get("proc_id", "") or "").strip()
        if not proc_id:
            return "错误: proc_id 参数不能为空"
        data = params.get("data", None)
        if data is None:
            return f"错误: {verb} 需要 data 参数"
        row = self._row(proc_id)
        if row is None:
            return f"错误: 未知 proc_id: {proc_id}"
        status, _ = self._status(row)
        if status != "running":
            return f"错误: 进程已退出，无法 {verb}: {proc_id}"
        stdin = self._stdins.get(proc_id)
        if stdin is None or stdin.closed:
            return (f"错误: stdin 不可用（进程非本代理进程启动/代理已重启/"
                    f"已 close），无法 {verb}: {proc_id}")
        try:
            payload = str(data) + ("\n" if newline else "")
            stdin.write(payload.encode("utf-8", errors="replace"))
            stdin.flush()
        except Exception as e:
            return f"错误: {verb} 写入失败: {e}"
        return f"✅ 已{('写入并回车' if newline else '写入')}{len(str(data))}字到 {proc_id} 的 stdin"

    def _close(self, params: dict) -> str:
        proc_id = str(params.get("proc_id", "") or "").strip()
        if not proc_id:
            return "错误: proc_id 参数不能为空"
        row = self._row(proc_id)
        if row is None:
            return f"错误: 未知 proc_id: {proc_id}"
        stdin = self._stdins.pop(proc_id, None)
        if stdin is None:
            return f"⚪ stdin 已不可用/已关闭: {proc_id}"
        try:
            stdin.close()
        except Exception as e:
            return f"错误: close 失败: {e}"
        with self._connect() as db:
            db.execute(
                "UPDATE processes SET stdin_open=0 WHERE proc_id=?",
                (proc_id,))
            db.commit()
        return f"✅ 已关闭 {proc_id} 的 stdin（进程读到 EOF 自行收尾）"
