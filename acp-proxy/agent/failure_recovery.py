# -*- coding: utf-8 -*-
"""工具失败恢复（路线图 2.2）——失败自动重试/换路径（最多2次）+ 失败原因入日志。

设计（对标 tenacity 重试 + LangGraph 工具失败策略，但按"恢复动作"建模）：
1. 工具执行结果失败（result_ok=False，read_turn.result_ok 单一真源）→ 进入恢复：
   - 第1次恢复 = 同路重试（同工具同参数，赌瞬时错误：网络/超时/限流/内部错误）
   - 第2次恢复 = 换路径（ALTERNATE_PATHS 换工具/换参数实现同一目标）
   - 两次都失败 → 带失败原因+已尝试路径回注给模型（不静默）
2. 安全边界：只有 IDEMPOTENT_TOOLS（幂等只读）允许自动重放。写类工具
   （write_file/terminal/execute_code/memory_write…）绝不自动重放——副作用
   可能翻倍（如 curl POST），失败原因入日志+回注提示，由模型决定下一步。
3. 权限/参数校验/资源不存在类失败不重试（重试无意义），只记录+提示。
4. 每次失败原因写入 JSONL 日志（data/tool_failure_log.jsonl，默认路径可被
   SOULMATE_FAILURE_LOG_PATH 覆盖，测试隔离用）——"失败原因入日志"可观测可查。
"""

import os
import json
import time
import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from agent.read_turn import result_ok
from agent.tool_errors import ErrorCategory

logger = logging.getLogger("acp-agent.failure-recovery")

# 每次恢复动作上限：1次同路重试 + 1次换路径 = 最多2次
MAX_RECOVERY_ATTEMPTS = 2
# 每次重放执行的兜底超时（exec_fn 自身另有子进程超时）
REPLAY_TIMEOUT_S = 60.0

# 幂等只读工具白名单——只有这些允许自动重放（重试无副作用）
IDEMPOTENT_TOOLS = frozenset({
    "read_file", "read_file_segment", "search_files", "list_files",
    "web_search", "web_extract",
    "vision_analyze", "read_image",
    "sense_ocr", "sense_analyze_image", "sense_transcribe_audio",
    "search_chat_history", "profile_read", "memory_read",
    "read_mcp_resource", "list_mcp_resources", "check_evolution_status",
})

# 重试有意义的错误类别（瞬时错误）；权限/校验/不存在重试无意义
RETRYABLE_CATEGORIES = frozenset({
    ErrorCategory.NETWORK, ErrorCategory.TIMEOUT,
    ErrorCategory.RATE_LIMIT, ErrorCategory.INTERNAL, ErrorCategory.UNKNOWN,
})

# 换路径映射：工具 → [(替代工具, 参数变换)]——同一目标的不同实现路径
def _read_file_to_segment(args: dict) -> dict:
    offset = int(args.get("offset", 1) or 1)
    limit = int(args.get("limit", 100) or 100)
    return {
        "path": args.get("path", ""),
        "start_line": offset,
        "end_line": offset + limit - 1,
    }


def _segment_to_read_file(args: dict) -> dict:
    start = int(args.get("start_line", 1) or 1)
    end = int(args.get("end_line", 200) or 200)
    return {
        "path": args.get("path", ""),
        "offset": start,
        "limit": max(end - start + 1, 1),
    }


ALTERNATE_PATHS: dict[str, list[tuple[str, Callable[[dict], dict]]]] = {
    "read_file": [("read_file_segment", _read_file_to_segment)],
    "read_file_segment": [("read_file", _segment_to_read_file)],
}


def classify_failure(text: str) -> ErrorCategory:
    """按失败结果文本分类（与 ToolErrorHandler._categorize_error 同族，输入是
    字符串而非异常——工具的软失败以字符串报出，不走异常路径）。"""
    t = str(text or "").lower()
    if any(k in t for k in ("permission", "denied", "eacces", "权限", "授权")):
        return ErrorCategory.PERMISSION
    if any(k in t for k in ("timeout", "timed out", "超时")):
        return ErrorCategory.TIMEOUT
    if any(k in t for k in ("not found", "enoent", "404", "不存在", "找不到")):
        return ErrorCategory.NOT_FOUND
    if ("rate" in t and "limit" in t) or "429" in t or "限流" in t:
        return ErrorCategory.RATE_LIMIT
    if any(k in t for k in ("参数", "invalid", "validation", "不完整", "schema")):
        return ErrorCategory.VALIDATION
    if any(k in t for k in ("connection", "network", "dns", "网络", "连接")):
        return ErrorCategory.NETWORK
    if any(k in t for k in ("internal", "500", "502", "503", "内部错误")):
        return ErrorCategory.INTERNAL
    return ErrorCategory.UNKNOWN


@dataclass
class RecoveryOutcome:
    """一次失败恢复的结果（含审计轨迹）。"""
    result: str                          # 最终回注给模型的结果
    recovered: bool = False              # 是否恢复成功
    actions: list[str] = field(default_factory=list)  # 已尝试的恢复动作
    category: ErrorCategory = ErrorCategory.UNKNOWN
    attempts: int = 0                    # 实际重放次数（不含首次失败执行）


class FailureRecoveryManager:
    """工具失败恢复管理器（会话级共享一个实例即可，无重资源）。"""

    def __init__(self, log_path: str | None = None):
        self._log_path = Path(
            log_path
            or os.environ.get("SOULMATE_FAILURE_LOG_PATH")
            or (Path(__file__).resolve().parent.parent / "data" / "tool_failure_log.jsonl")
        )
        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        self._stats = {"failures": 0, "replays": 0, "recovered": 0, "reported": 0}

    # ── 失败原因入日志（JSONL，append-only） ──────────────────────
    def _log_failure(
        self,
        session_id: str,
        tool_name: str,
        args: dict,
        error: str,
        category: ErrorCategory,
        actions: list[str],
        outcome: str,
        replay_errors: list[str] | None = None,
    ) -> None:
        record = {
            "ts": time.time(),
            "session_id": session_id,
            "tool": tool_name,
            "args_preview": json.dumps(args, ensure_ascii=False)[:300],
            "error": str(error)[:500],
            "category": category.value,
            "actions": actions,
            "outcome": outcome,
        }
        if replay_errors:
            record["replay_errors"] = [str(e)[:200] for e in replay_errors]
        try:
            with open(self._log_path, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            # 日志写失败不许打断任务循环，但必须可见
            logger.warning(f"[failure-recovery] 失败日志写入失败(非致命): {e}")
        logger.warning(
            f"[failure-recovery] session={session_id} tool={tool_name} "
            f"category={category.value} outcome={outcome} actions={actions} "
            f"error={str(error)[:200]}")

    # ── 主入口 ──────────────────────────────────────────────────
    async def recover(
        self,
        session_id: str,
        tool_name: str,
        args: dict,
        first_result: str,
        exec_fn: Callable[[str, dict], Awaitable[str]],
    ) -> str:
        """对一次失败的工具调用做恢复。

        exec_fn(tool, args) -> str：重放执行器（必须已过权限门禁）。
        返回：恢复成功=新结果；失败=首行保留原失败标记的结果 + 恢复轨迹注记。
        """
        self._stats["failures"] += 1
        category = classify_failure(first_result)
        actions: list[str] = []
        outcome = RecoveryOutcome(
            result=first_result, category=category)

        # 权限/校验/不存在：重试无意义，只记录+提示
        auto_replay_allowed = (
            tool_name in IDEMPOTENT_TOOLS
            and category in RETRYABLE_CATEGORIES
        )
        if not auto_replay_allowed:
            reason = (
                f"类别{category.value}重试无意义" if tool_name in IDEMPOTENT_TOOLS
                else f"非幂等只读工具不自动重放（防副作用翻倍）"
            )
            note = (
                f"\n\n[失败恢复] {reason}，未自动重试。失败原因已入日志。"
                "请换参数/换工具重新发起，或向用户说明。"
            )
            self._log_failure(
                session_id, tool_name, args, first_result,
                category, actions, outcome="reported")
            self._stats["reported"] += 1
            return first_result + note

        # 第1次恢复：同路重试（同工具同参数）
        actions.append("retry")
        outcome.attempts += 1
        self._stats["replays"] += 1
        replay_errors: list[str] = []
        r1 = await self._replay(exec_fn, tool_name, args)
        if result_ok(r1):
            actions_mark = "→".join(actions)
            self._log_failure(
                session_id, tool_name, args, first_result,
                category, actions, outcome="recovered_retry")
            self._stats["recovered"] += 1
            return r1 + f"\n\n[失败恢复] 首次失败后同路重试成功（{actions_mark}）"
        replay_errors.append(str(r1).split("\n", 1)[0])

        # 第2次恢复：换路径（换工具/换参数实现同一目标）
        for alt_tool, transform in ALTERNATE_PATHS.get(tool_name, []):
            actions.append(f"switch:{alt_tool}")
            outcome.attempts += 1
            self._stats["replays"] += 1
            alt_args = transform(args)
            r2 = await self._replay(exec_fn, alt_tool, alt_args)
            if result_ok(r2):
                self._log_failure(
                    session_id, tool_name, args, first_result,
                    category, actions, outcome="recovered_switch")
                self._stats["recovered"] += 1
                return (
                    f"{r2}\n\n[失败恢复] `{tool_name}` 失败后换路径 "
                    f"`{alt_tool}` 成功（{'→'.join(actions)}）"
                )
            replay_errors.append(str(r2).split("\n", 1)[0])
            break  # 最多2次恢复：换路径只试第一个替代

        # 两次都失败（或无换路径可用）：带失败原因+已尝试轨迹回注
        attempts_mark = f"（重放{outcome.attempts}次）" if outcome.attempts else ""
        replay_mark = (
            f" 重放报错: {'；'.join(replay_errors)}。" if replay_errors else ""
        )
        note = (
            f"\n\n[失败恢复] 已尝试: {'→'.join(actions) if actions else '(无)'}，"
            f"均失败{attempts_mark}。失败原因（{category.value}）已入日志。{replay_mark}"
            "请勿重复相同调用，换参数/换工具/向用户求助。"
        )
        self._log_failure(
            session_id, tool_name, args, first_result,
            category, actions, outcome="exhausted",
            replay_errors=replay_errors)
        return first_result + note

    async def _replay(
        self,
        exec_fn: Callable[[str, dict], Awaitable[str]],
        tool_name: str,
        args: dict,
    ) -> str:
        """单次重放（兜底超时，重放自身异常也收敛成失败文本不外抛）。"""
        try:
            return await asyncio.wait_for(
                exec_fn(tool_name, args), timeout=REPLAY_TIMEOUT_S)
        except asyncio.TimeoutError:
            return f"错误: 失败恢复重放超时（{REPLAY_TIMEOUT_S}s）"
        except Exception as e:
            return f"错误: 失败恢复重放异常: {e}"

    def stats(self) -> dict:
        return dict(self._stats)
