# -*- coding: utf-8 -*-
"""8.3 长回合治理 — 回合级进度/超时可见（禁静默）。

验收#7实录（2026-10-10）：schedule 3 工具链回合 >240s，用户只见 spinner 不知死活，
harness 到点放弃后连终态落库都没有 = 静默。根因两半：
- 耗时本身：每工具调用一次注入审计 LLM 往返 + R1 审计误杀触发的失败/重试轮
  （R1 已在周期#31修复；审计 LLM 开销优化另测另做）；
- **过程不可见**（本模块治理）：长回合中途无任何进度/超时信号。

设计（旁路纪律对标 0.1 prompt_prefetch）：
- 后台 asyncio 任务周期心跳（默认 60s，SOULMATE_TURN_HEARTBEAT_S 可配）把回合快照
  （已用时/轮次/工具数/最近工具）推给客户端+journal——不依赖主循环轮次边界，
  LLM 单轮慢（大首包 27s 级）也照发。
- 软预算（默认 240s，SOULMATE_TURN_SOFT_BUDGET_S）超限发一次 ⚠️ 提醒，
  **绝不硬杀**：用户铁律"任务禁硬超时杀（超时零事件只提醒）"。
- 旁路纪律：notify 抛错只计数（notify_failures）不冒泡不阻塞主链路。
- 生命周期：start()/stop() 幂等；stop 即 cancel 不等 join；max_lifetime 自过期
  （防孤儿心跳变噪音）；一个会话同时最多一个看门狗（soulmate_agent 顶掉旧的）。
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger("acp.turn_watchdog")

DEFAULT_HEARTBEAT_S = 60.0
DEFAULT_SOFT_BUDGET_S = 240.0
# 自过期=软预算4倍：回合真跑16分钟级看门狗退出（此后进度由loop-guard轮次上限兜）
DEFAULT_MAX_LIFETIME_S = 960.0

_SNAPSHOT_FIELDS = ("round_index", "max_rounds", "tools_executed", "last_tool")

# 消息前缀常量（frame_origin 采集侧帧分类的单一真源——改措辞须同步语义）
SNAPSHOT_PREFIX = "⏳ 回合进行中"
SOFT_WARN_PREFIX = "⚠️ 回合已超过软预算"


@dataclass
class TurnSnapshot:
    """回合进度快照（心跳消息的数据源）"""

    round_index: int = 0
    max_rounds: int = 0
    tools_executed: int = 0
    last_tool: str = ""


class TurnWatchdog:
    """回合级看门狗：周期心跳推进度，软预算超限提醒，绝不硬杀。"""

    def __init__(
        self,
        session_id: str,
        notify: Callable[[str], Awaitable[None]] | None = None,
        heartbeat_s: float | None = None,
        soft_budget_s: float | None = None,
        max_lifetime_s: float | None = None,
    ):
        self.session_id = session_id
        self._notify = notify
        self.heartbeat_s = float(
            heartbeat_s if heartbeat_s is not None
            else os.environ.get("SOULMATE_TURN_HEARTBEAT_S", DEFAULT_HEARTBEAT_S)
        )
        self.soft_budget_s = float(
            soft_budget_s if soft_budget_s is not None
            else os.environ.get("SOULMATE_TURN_SOFT_BUDGET_S", DEFAULT_SOFT_BUDGET_S)
        )
        self.max_lifetime_s = float(
            max_lifetime_s if max_lifetime_s is not None
            else os.environ.get("SOULMATE_TURN_MAX_LIFETIME_S", DEFAULT_MAX_LIFETIME_S)
        )
        self.snapshot = TurnSnapshot()
        self._task: asyncio.Task | None = None
        self._started_at: float | None = None
        self._stopped = False
        self.beats_sent = 0
        self.notify_failures = 0
        self.soft_warned = False

    # ── 生命周期 ────────────────────────────────────────────────
    def start(self) -> None:
        """启动心跳任务（幂等：已在跑不重建、不重置时钟）"""
        if self.running:
            return
        self._stopped = False
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(
            self._run(), name=f"turn-watchdog-{self.session_id[:8]}")

    def stop(self) -> None:
        """停止心跳（幂等；cancel 即可不等 join）"""
        self._stopped = True
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def elapsed(self) -> float:
        if self._started_at is None:
            return 0.0
        return time.monotonic() - self._started_at

    # ── 进度上报（主循环各节点调用）────────────────────────────────
    def beat(self, **fields) -> None:
        """更新回合快照字段（只认 TurnSnapshot 已知字段，拼错响亮报错不静默丢）"""
        for key, value in fields.items():
            if key not in _SNAPSHOT_FIELDS:
                raise ValueError(f"未知快照字段: {key}（合法: {_SNAPSHOT_FIELDS}）")
            setattr(self.snapshot, key, value)

    def status_text(self) -> str:
        snap = self.snapshot
        if snap.max_rounds:
            round_part = f"第 {snap.round_index}/{snap.max_rounds} 轮"
        else:
            round_part = f"第 {snap.round_index} 轮"
        tool_part = f"已执行工具 {snap.tools_executed} 个"
        if snap.last_tool:
            tool_part += f"（最近：{snap.last_tool}）"
        return (
            f"{SNAPSHOT_PREFIX}：已用 {self.elapsed():.0f}s｜{round_part}｜{tool_part}")

    # ── 心跳循环 ────────────────────────────────────────────────
    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.heartbeat_s)
                if self._stopped:
                    return
                elapsed = self.elapsed()
                if elapsed >= self.max_lifetime_s:
                    logger.info(
                        f"[turn-watchdog] session={self.session_id} 自过期退出 "
                        f"elapsed={elapsed:.0f}s max={self.max_lifetime_s:.0f}s")
                    return
                await self._fire_once(elapsed)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 旁路纪律：看门狗自身异常绝不冒泡
            self.notify_failures += 1
            logger.debug(f"[turn-watchdog] run aborted (non-fatal): {e}")

    async def _fire_once(self, elapsed: float) -> None:
        msg = self.status_text()
        if elapsed >= self.soft_budget_s and not self.soft_warned:
            self.soft_warned = True
            msg = (
                f"{SOFT_WARN_PREFIX} {self.soft_budget_s:.0f}s（长回合），"
                f"继续执行不中断。{msg}")
        self.beats_sent += 1
        logger.info(f"[turn-watchdog] session={self.session_id} {msg}")
        if self._notify is not None:
            try:
                await self._notify(msg)
            except Exception as e:
                self.notify_failures += 1
                logger.debug(f"[turn-watchdog] notify failed (non-fatal): {e}")
