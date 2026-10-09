"""工具失败恢复（路线图 2.2）——工具报错自动重试/换路径（最多2次）+ 失败原因入日志

对标：ToolErrorHandler（tool_errors.py）已有错误分类+doom loop+内存日志，但
**不重试、不换路径、不持久化**。本模块补上失败恢复三件事：

1. **失败判定**：复用 read_turn.result_ok 族的结构化失败标记，另补
   `[CODE_MODE] 工具执行异常`（result_ok 漏网形态）。三态：
   - BLOCKED（权限/沙箱拦截）→ **绝不重试**（重试=绕过权限引擎，铁律）
   - TRANSIENT（超时/网络/限流/内部错误）→ 可原路重试
   - RECOVERABLE（文件不存在/路径不对）→ 直接换路径
   - PERMANENT（参数不完整/校验失败）→ 不自动恢复，错误回注 LLM
2. **恢复计划（最多2次）**：确定性映射，不走 LLM——
   - TRANSIENT + 只读/无副作用工具 → 第1步原路重试；仍有第2步预算时接换路径
   - RECOVERABLE → 第1步即换路径（原路重试无意义）
   - 换路径映射：read_file↔read_file_segment 互备；read_file 文件不存在→按
     basename 定位真实路径后重读（路径修正）；search_files 路径不存在→回 cwd
   - 写操作/terminal/execute_code **不自动重跑**（副作用风险），只记日志
3. **失败原因入日志**：SQLite 持久化（默认
   ~/.hermes/soulmate/memory/tool_failures.db，TOOL_FAILURE_DB_PATH 覆盖，
   与 experience_sink 同目录约定），每次失败/恢复动作一行，可审计复盘。
"""

from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("acp-proxy.tool-recovery")

DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~/.hermes/soulmate/memory"), "tool_failures.db"
)

# 路线图2.2："自动重试/换路径（最多2次）"——恢复动作总数封顶
MAX_RECOVERY_ATTEMPTS = 2

# 原路重试安全集合（只读/无副作用——写操作/terminal/execute_code 禁自动重跑）
SAFE_RETRY_TOOLS = frozenset({
    "read_file", "read_file_segment", "search_files", "list_files",
    "web_search", "web_extract", "search_chat_history",
    "memory_read", "profile_read", "list_mcp_resources", "read_mcp_resource",
})

# 路径修正定位时的目录跳过项（防递归爆扫）与访问上限
_WALK_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__", ".next",
    ".cache", "dist", "build", ".npm", ".cargo", ".rustup",
})
_WALK_MAX_ENTRIES = 50_000


class FailureKind(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"          # 权限/沙箱拦截——非失败，绝不重试
    TRANSIENT = "transient"      # 瞬态（超时/网络/限流/内部）→ 可原路重试
    RECOVERABLE = "recoverable"  # 路径不对/文件不存在 → 换路径
    PERMANENT = "permanent"      # 参数/校验类 → 不自动恢复，回注 LLM


@dataclass
class RecoveryStep:
    """一步恢复动作（retry=原路重试 / fallback=换路径）"""
    action: str
    tool_name: str
    func_args: dict
    note: str = ""


# ── 失败形态识别（结构化标记优先，内容启发式兜底）────────────────────
# BLOCKED：权限引擎拦截（soulmate_agent 各门禁分支模板）
_BLOCKED_MARKERS = ("[被拦截", "被拦截:")
# TRANSIENT 线索（超时/网络/限流/服务端内部错误）
_TRANSIENT_RES = (
    "超时", "timeout", "timed out",
    "connection", "connect", "网络", "network", "econnreset", "econnrefused",
    "temporary failure", "temporarily unavailable", "unreachable",
    "429", "502", "503", "504", "rate limit", "限流", "too many requests",
)
# RECOVERABLE 线索（资源不存在/路径不对）
_RECOVERABLE_RES = (
    "文件不存在或为空", "no such file", "not found", "404",
    "不存在", "未找到", "找不到", "enoent",
)
# PERMANENT 线索（参数/校验——重跑同参只会再失败）
_PERMANENT_RES = (
    "参数不完整", "不能为空", "需要 script 参数", "需要 server 和 uri",
    "参数格式", "invalid", "validation", "schema",
)
# 机器失败标记（result_ok 族之外的补网：[CODE_MODE] 工具执行异常 不在
# read_turn._RESULT_FAIL_MARKERS 里——首行"异常:"不匹配其"错误/失败:"正则）
_EXTRA_FAIL_MARKERS = (
    "[CODE_MODE] 工具执行异常",
    "工具执行异常",
    "写入失败",
)


def _first_line(result) -> str:
    return str(result or "").lstrip().split("\n", 1)[0]


def classify_result(result: str) -> FailureKind:
    """工具结果三态/四态判定。OK/BLOCKED 外 = 失败，按可恢复性细分。"""
    text = str(result or "")
    line = _first_line(result)
    if not line:
        return FailureKind.OK
    if any(m in line for m in _BLOCKED_MARKERS):
        return FailureKind.BLOCKED
    # 复用 read_turn.result_ok 单一真源（import 局部化避免循环依赖——
    # read_turn 不依赖本模块）
    from agent.read_turn import result_ok
    failed = (not result_ok(text)) or any(m in line for m in _EXTRA_FAIL_MARKERS)
    if not failed:
        return FailureKind.OK
    low = line.lower()
    # 判定顺序：参数校验（重跑必败）> 资源不存在（换路径）> 瞬态（可重试）
    if any(k in low or k in line for k in _PERMANENT_RES):
        return FailureKind.PERMANENT
    if any(k in low or k in line for k in _RECOVERABLE_RES):
        return FailureKind.RECOVERABLE
    if any(k in low or k in line for k in _TRANSIENT_RES):
        return FailureKind.TRANSIENT
    return FailureKind.PERMANENT


def _locate_by_basename(basename: str, search_roots: list[str]) -> Optional[str]:
    """文件不存在时按 basename 定位真实路径（换路径的路径修正）。"""
    if not basename:
        return None
    visited = 0
    for root in search_roots:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            visited += 1
            if visited > _WALK_MAX_ENTRIES:
                return None
            dirnames[:] = [d for d in dirnames if d not in _WALK_SKIP_DIRS]
            if basename in filenames:
                return os.path.join(dirpath, basename)
    return None


class ToolRecoveryManager:
    """失败恢复编排 + 失败原因持久化日志。"""

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = (
            db_path
            or os.environ.get("TOOL_FAILURE_DB_PATH")
            or DEFAULT_DB_PATH
        )

    # ── 失败判定 ─────────────────────────────────────────────
    def classify(self, result: str) -> FailureKind:
        return classify_result(result)

    # ── 恢复计划（最多2步，确定性，不走LLM）──────────────────
    def plan_steps(
        self,
        tool_name: str,
        func_args: dict,
        result: str,
        kind: Optional[FailureKind] = None,
        search_roots: Optional[list[str]] = None,
    ) -> list[RecoveryStep]:
        if kind is None:
            kind = self.classify(result)
        if kind in (FailureKind.OK, FailureKind.BLOCKED, FailureKind.PERMANENT):
            return []
        steps: list[RecoveryStep] = []
        args = dict(func_args or {})

        # 第1步预算：瞬态+只读工具 → 原路重试
        if kind == FailureKind.TRANSIENT and tool_name in SAFE_RETRY_TOOLS:
            steps.append(RecoveryStep(
                action="retry", tool_name=tool_name, func_args=dict(args),
                note="瞬态失败原路重试",
            ))

        # 换路径映射（第2步预算；RECOVERABLE 直接从这步开始）
        fallback = self._fallback_step(tool_name, args, result, search_roots)
        if fallback is not None:
            steps.append(fallback)

        return steps[:MAX_RECOVERY_ATTEMPTS]

    def _fallback_step(
        self,
        tool_name: str,
        args: dict,
        result: str,
        search_roots: Optional[list[str]],
    ) -> Optional[RecoveryStep]:
        line = _first_line(result)
        low = line.lower()
        not_found = any(k in low or k in line for k in _RECOVERABLE_RES)

        if tool_name == "read_file":
            # 路径修正优先：文件不存在→按 basename 定位真实路径
            if not_found:
                base = os.path.basename(str(args.get("path", "")))
                hit = _locate_by_basename(base, search_roots or [])
                if hit:
                    new_args = dict(args)
                    new_args["path"] = hit
                    return RecoveryStep(
                        action="fallback", tool_name="read_file",
                        func_args=new_args,
                        note=f"路径修正: {args.get('path')} → {hit}",
                    )
            # 兜底：换 read_file_segment 同路径重读
            seg_args = {
                "path": args.get("path", ""),
                "start_line": int(args.get("offset", 1) or 1),
                "end_line": int(args.get("offset", 1) or 1)
                + int(args.get("limit", 100) or 100) - 1,
            }
            return RecoveryStep(
                action="fallback", tool_name="read_file_segment",
                func_args=seg_args, note="换路径: read_file → read_file_segment",
            )

        if tool_name == "read_file_segment":
            alt = {
                "path": args.get("path", ""),
                "offset": int(args.get("start_line", 1) or 1),
                "limit": max(
                    1,
                    int(args.get("end_line", 200) or 200)
                    - int(args.get("start_line", 1) or 1) + 1,
                ),
            }
            return RecoveryStep(
                action="fallback", tool_name="read_file", func_args=alt,
                note="换路径: read_file_segment → read_file",
            )

        if tool_name == "search_files" and not_found:
            new_args = dict(args)
            new_args["path"] = (search_roots or ["."])[0]
            return RecoveryStep(
                action="fallback", tool_name="search_files", func_args=new_args,
                note=f"换路径: 搜索根回退 {new_args['path']}",
            )

        # web_search/web_extract/search_chat_history/memory_read 等：
        # 无确定性换路径方案；terminal/write_file/patch/execute_code：
        # 副作用风险禁自动重跑——均不返回步骤（只记日志）
        return None

    # ── 失败原因持久化日志 ────────────────────────────────────
    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS tool_failures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                session_id TEXT,
                tool_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                action TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                error_first_line TEXT,
                note TEXT,
                args_json TEXT
            )"""
        )
        return conn

    def record(
        self,
        session_id: str,
        tool_name: str,
        kind: str,
        error: str,
        action: str,
        attempt: int,
        outcome: str,
        note: str = "",
        func_args: Optional[dict] = None,
    ) -> None:
        """记录一行失败/恢复动作（失败原因入日志——路线图2.2）。"""
        try:
            args_json = json.dumps(
                {k: str(v)[:200] for k, v in (func_args or {}).items()},
                ensure_ascii=False,
            )
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO tool_failures (ts, session_id, tool_name, kind,"
                    " action, attempt, outcome, error_first_line, note, args_json)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        time.time(), session_id or "", tool_name, str(kind),
                        action, int(attempt), outcome,
                        _first_line(error)[:300], note[:300], args_json,
                    ),
                )
        except Exception as e:  # 日志失败不阻塞工具链路
            logger.warning(f"[tool-recovery] 失败日志写入失败(非致命): {e}")

    def recent(self, limit: int = 20) -> list[dict]:
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT ts, session_id, tool_name, kind, action, attempt,"
                    " outcome, error_first_line, note FROM tool_failures"
                    " ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        except Exception as e:
            logger.warning(f"[tool-recovery] 失败日志读取失败: {e}")
            return []
        keys = ("ts", "session_id", "tool_name", "kind", "action",
                "attempt", "outcome", "error_first_line", "note")
        return [dict(zip(keys, r)) for r in rows]

    def stats(self) -> dict:
        try:
            with self._conn() as conn:
                total = conn.execute(
                    "SELECT COUNT(*) FROM tool_failures").fetchone()[0]
                by_outcome = dict(conn.execute(
                    "SELECT outcome, COUNT(*) FROM tool_failures"
                    " GROUP BY outcome").fetchall())
                by_tool = dict(conn.execute(
                    "SELECT tool_name, COUNT(*) FROM tool_failures"
                    " GROUP BY tool_name ORDER BY COUNT(*) DESC LIMIT 10"
                ).fetchall())
        except Exception as e:
            logger.warning(f"[tool-recovery] 失败日志统计失败: {e}")
            return {}
        return {
            "total_failures": total,
            "by_outcome": by_outcome,
            "by_tool": by_tool,
        }
