# -*- coding: utf-8 -*-
"""run_guard.py — 防跑飞三件套（Pi 集成 #7：无进展看门狗 + 轮数上限 + 空转诊断）。

来源：《取长补短清单》#7——OpenBidKit 防跑飞三件套（①无进展看门狗 8min，
ask-user 等待期暂停计时 ②每提示词轮数上限（子代理统一 100） ③空转诊断元
代理：同阶段同提示词重复即判空转 → extractSessionRecord 摘录会话执行记录 →
诊断给出"继续/收尾/停止"结论）。Pi 本体"愿赌服输烧 token"不设防，SoulMate
内建（能力层，纯 Python 独立可测）：

- `RunGuard`：turn 记账 + **无进展看门狗**（距上次进展超过 no_progress_timeout
  判 stop，默认 480s=Pi 易标 8 分钟；暂停/恢复计时支持 ask-user 等待期）+
  **轮数上限**（max_turns 默认 100，同子代理口径）；
- **空转诊断**：同一 (工具名, 参数签名) 重复达阈值判空转 → `diagnose()` 给
  结构化结论（continue / wrap_up / stop），`extract_session_record()` 产出
  会话摘录（JSONL 执行记录，诊断子任务/元代理的输入——程序侧确定性启发，
  LLM 诊断可消费同一摘录）。

与既有件的分工（不重复建轮）：loop_guard 管"重复检测+渐进干预"（文本/工具
重复签名、Jaccard），turn_watchdog 管"回合进度心跳+软预算可见"，termination
管"终止条件词汇单一真源"；本模块管**跑飞处决面**（无进展时长/轮数硬顶/空转
结论），verdict 词汇复用 termination 常量。禁硬杀用户任务：verdict=stop 只出
结论与收尾建议，处决由编排层显式决定。
独立可测（tests/test_guard_tools.py）。
"""
from __future__ import annotations

import hashlib
import json
import time

from agent import termination

__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_NO_PROGRESS_S",
    "DEFAULT_STALL_THRESHOLD",
    "VERDICT_OK",
    "VERDICT_WARN",
    "VERDICT_STOP",
    "RunGuard",
]

DEFAULT_MAX_TURNS = 100          # Pi：每提示词轮数上限（子代理统一 100）
DEFAULT_NO_PROGRESS_S = 480.0    # Pi 易标：无进展看门狗 8 分钟
DEFAULT_STALL_THRESHOLD = 3      # 同签名重复 N 次判空转

VERDICT_OK = "ok"
VERDICT_WARN = "warn"
VERDICT_STOP = "stop"

REC_CONTINUE = "continue"
REC_WRAP_UP = "wrap_up"
REC_STOP = "stop"


class RunGuard:
    """防跑飞三件套（无进展看门狗 / 轮数上限 / 空转诊断）。"""

    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS,
                 no_progress_timeout: float = DEFAULT_NO_PROGRESS_S,
                 stall_threshold: int = DEFAULT_STALL_THRESHOLD,
                 now=None):
        self.max_turns = int(max_turns)
        self.no_progress_timeout = float(no_progress_timeout)
        self.stall_threshold = int(stall_threshold)
        if self.max_turns < 1:
            raise ValueError(f"max_turns 需 ≥1，收到 {max_turns}")
        if self.no_progress_timeout <= 0:
            raise ValueError(f"no_progress_timeout 需 >0，收到 {no_progress_timeout}")
        if self.stall_threshold < 2:
            raise ValueError(f"stall_threshold 需 ≥2，收到 {stall_threshold}")
        self._now = now or time.time
        self._t0 = self._now()
        self._last_progress = self._t0
        self._paused_at: float | None = None
        self._turns: list[dict] = []
        self._signatures: dict[str, int] = {}

    # ── 记账 ─────────────────────────────────────────────────────────

    def record_turn(self, tool_name: str = "", args=None, *,
                    progress: bool = True) -> dict:
        """记一轮（一次工具调用/一步执行）。progress=是否构成实质进展。"""
        args = args if args is not None else {}
        sig = _signature(tool_name, args)
        self._signatures[sig] = self._signatures.get(sig, 0) + 1
        entry = {
            "turn": len(self._turns) + 1,
            "ts": round(self._now() - self._t0, 3),
            "tool": str(tool_name or ""),
            "sig": sig,
            "args_preview": json.dumps(args, ensure_ascii=False,
                                       default=str)[:120],
            "progress": bool(progress),
        }
        self._turns.append(entry)
        if progress:
            self.record_progress()
        return self.check()

    def record_progress(self) -> None:
        """标记实质进展（无进展看门狗从现在重新计时；暂停期调用顺带恢复）。"""
        if self._paused_at is not None:
            self._paused_at = None   # 进展即恢复计时
        self._last_progress = self._now()

    def pause(self) -> None:
        """暂停无进展计时（ask-user 等待用户期间，Pi 同款语义）。"""
        if self._paused_at is None:
            self._paused_at = self._now()

    def resume(self) -> None:
        """恢复计时（暂停时长不计入无进展时长）。"""
        if self._paused_at is not None:
            self._last_progress += self._now() - self._paused_at
            self._paused_at = None

    # ── 处决面（verdict 只出结论，不硬杀） ────────────────────────────

    def check(self) -> dict:
        """{"verdict": ok|warn|stop, "reason", "detail"}。"""
        turns = len(self._turns)
        if turns >= self.max_turns:
            return {"verdict": VERDICT_STOP,
                    "reason": termination.TERM_MAX_TURNS,
                    "detail": {"turns": turns, "max_turns": self.max_turns}}
        idle = self.idle_seconds()
        if idle >= self.no_progress_timeout:
            return {"verdict": VERDICT_STOP,
                    "reason": "no_progress",
                    "detail": {"idle_seconds": round(idle, 1),
                               "timeout": self.no_progress_timeout,
                               "turns": turns}}
        diag = self.diagnose()
        if diag["stalled"]:
            return {"verdict": VERDICT_STOP if diag["recommendation"] == REC_STOP
                    else VERDICT_WARN,
                    "reason": "stalled",
                    "detail": diag}
        return {"verdict": VERDICT_OK, "reason": "",
                "detail": {"turns": turns,
                           "idle_seconds": round(idle, 1)}}

    def idle_seconds(self) -> float:
        """无进展时长（暂停中冻结在暂停点）。"""
        if self._paused_at is not None:
            return self._paused_at - self._last_progress
        return self._now() - self._last_progress

    # ── 空转诊断（元代理输入/确定性启发双用） ─────────────────────────

    def diagnose(self) -> dict:
        """空转诊断：{"stalled", "repeat_signatures", "recommendation", "reason"}。

        启发（确定性）：某签名重复达阈值且其后无新签名 → 空转；有产物
        （turn 数 > 重复数）→ 建议 wrap_up（收尾交付已有产物），否则 stop。
        """
        repeats = {sig: n for sig, n in self._signatures.items()
                   if n >= self.stall_threshold}
        stalled = bool(repeats)
        if not stalled:
            return {"stalled": False, "repeat_signatures": {},
                    "recommendation": REC_CONTINUE, "reason": ""}
        novel_after_repeat = self._novel_after_repeat(repeats)
        if not novel_after_repeat and len(self._turns) > max(repeats.values()):
            rec, reason = REC_WRAP_UP, "空转但已有执行产物，建议收尾交付现有产物"
        elif not novel_after_repeat:
            rec, reason = REC_STOP, "空转且无新进展，建议停止并如实报告"
        else:
            rec, reason = REC_CONTINUE, "重复调用后仍有新签名，观察中"
        return {
            "stalled": not novel_after_repeat,
            "repeat_signatures": repeats,
            "recommendation": rec,
            "reason": reason,
        }

    def _novel_after_repeat(self, repeats: dict) -> bool:
        """最后一个重复签名位置之后是否出现过新签名。"""
        last_repeat_i = -1
        for i, t in enumerate(self._turns):
            if t["sig"] in repeats:
                last_repeat_i = i
        return any(t["sig"] not in repeats
                   for t in self._turns[last_repeat_i + 1:])

    def extract_session_record(self, limit: int = 20) -> str:
        """会话摘录（JSONL 执行记录）——诊断子任务/元代理的输入。

        Pi extractSessionRecord 同款语义：从执行记录摘取 turn/tool/args/进展，
        有界（默认最近 20 条），JSONL 逐行可解析。
        """
        rows = self._turns[-max(1, int(limit)):]
        return "\n".join(json.dumps(r, ensure_ascii=False) for r in rows)

    def snapshot(self) -> dict:
        """状态快照（工具面 status 用）。"""
        return {
            "turns": len(self._turns),
            "max_turns": self.max_turns,
            "idle_seconds": round(self.idle_seconds(), 1),
            "no_progress_timeout": self.no_progress_timeout,
            "paused": self._paused_at is not None,
            "signatures": len(self._signatures),
            "check": self.check(),
        }


def _signature(tool_name: str, args) -> str:
    raw = json.dumps({"tool": str(tool_name or ""), "args": args},
                     ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
