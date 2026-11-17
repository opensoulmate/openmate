# -*- coding: utf-8 -*-
"""子代理治理运行时基线（Pi #14，③流程引擎并行节点的运行时基线语义）。

三件事（与 Pi agentService.cjs 子代理治理对齐，机制用本栈自建）：
1. **并发乘数×2 超出排队**：同父并发 = 基础并发 × 2，超限进 FIFO 有界队列
   （排队超时/队满显式报因，绝不静默丢弃——与 model_gateway.queue 的
   RequestQueue 同一排队纪律，但资源面不同：这里是子任务执行槽，不是 LLM 请求）。
2. **轮数上限统一施加**：全部子任务共享 TurnBudget（默认 100，即 Pi
   CHILD_AGENT_MAX_TURNS_PER_PROMPT 语义），超限显式报因不硬杀。
3. **runChildTask 沿用限制**：治理面内部子任务（run_child）继承同一 governor
   与父剩余轮数预算——子任务不得绕开上限。

用法（流程引擎 parallel 节点 / 任何子任务编排）：
    gov = SubagentGovernor(base_concurrency=4)
    async with gov.slot("branch-a"):
        ...  # 并发超出 base*2 时自动排队
    result = await gov.run_child(coro_fn, child_id="diag")   # 沿用同一套限制
"""
from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")

# Pi：子代理统一轮数上限（CHILD_AGENT_MAX_TURNS_PER_PROMPT=100）
DEFAULT_MAX_TURNS = 100
DEFAULT_MULTIPLIER = 2          # 并发乘数×2
DEFAULT_MAX_QUEUE = 32          # 有界排队上限
DEFAULT_QUEUE_TIMEOUT_S = 30.0  # 排队超时（显式报因）


class SubagentQueueFull(RuntimeError):
    """排队已满（显式报因，不静默丢弃）"""


class SubagentQueueTimeout(RuntimeError):
    """排队等待超时（显式报因）"""


class TurnLimitExceeded(RuntimeError):
    """轮数上限已达（统一施加，显式报因不硬杀）"""


class TurnBudget:
    """统一轮数预算：全部子任务共享同一上限；runChildTask 继承父剩余。"""

    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS):
        if max_turns < 0:
            raise ValueError("max_turns 不能为负")
        self.max_turns = int(max_turns)
        self.used = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_turns - self.used)

    @property
    def exceeded(self) -> bool:
        return self.remaining <= 0

    def tick(self, n: int = 1) -> None:
        """消耗 n 轮；超限抛 TurnLimitExceeded（显式报因，调用方决定收尾）。"""
        if n <= 0:
            return
        if self.remaining < n:
            self.used = self.max_turns
            raise TurnLimitExceeded(
                f"轮数上限已达：{self.used}/{self.max_turns}（统一施加，收尾交付已有产物）")
        self.used += n

    def child(self) -> "TurnBudget":
        """runChildTask 沿用限制：子预算=父剩余，且不超过统一上限（父耗尽→子 0 轮即拒）。"""
        return TurnBudget(max_turns=min(self.max_turns, self.remaining))

    def stats(self) -> dict[str, Any]:
        return {"max_turns": self.max_turns, "used": self.used,
                "remaining": self.remaining}


@dataclass
class _SlotState:
    in_flight: int = 0
    waiting: int = 0
    queued_total: int = 0
    timed_out: int = 0


class SubagentGovernor:
    """子代理治理面：并发乘数×2 + 超出排队 + 统一轮数 + 子任务沿用。"""

    def __init__(self, base_concurrency: int = 4, *,
                 multiplier: int = DEFAULT_MULTIPLIER,
                 max_queue: int = DEFAULT_MAX_QUEUE,
                 queue_timeout_s: float = DEFAULT_QUEUE_TIMEOUT_S,
                 max_turns: int = DEFAULT_MAX_TURNS):
        if base_concurrency <= 0:
            raise ValueError("base_concurrency 必须为正数")
        if multiplier <= 0:
            raise ValueError("multiplier 必须为正数")
        self.base_concurrency = int(base_concurrency)
        self.multiplier = int(multiplier)
        self.capacity = self.base_concurrency * self.multiplier   # 并发乘数×2
        self.max_queue = int(max_queue)
        self.queue_timeout_s = float(queue_timeout_s)
        self.budget = TurnBudget(max_turns=max_turns)              # 统一轮数上限
        self._sem = asyncio.Semaphore(self.capacity)
        self._state = _SlotState()

    # ── 执行槽（超出排队）────────────────────────────────────────

    @asynccontextmanager
    async def slot(self, child_id: str = ""):
        """占用一个子任务执行槽；并发满时 FIFO 排队（有界+超时显式报因）。
        只有真正排队等待的任务计入 waiting（立即获槽不计），队满判定不误伤。"""
        if self._sem.locked():
            # 容量已满：本任务须排队 → 有界队列判定
            if self._state.waiting >= self.max_queue:
                raise SubagentQueueFull(
                    f"子任务排队已满（waiting={self._state.waiting} >= "
                    f"max_queue={self.max_queue}）：{child_id or '<unnamed>'} 拒绝入队")
            self._state.waiting += 1
            self._state.queued_total += 1
            try:
                try:
                    await asyncio.wait_for(self._sem.acquire(),
                                           timeout=self.queue_timeout_s)
                except asyncio.TimeoutError as e:
                    self._state.timed_out += 1
                    raise SubagentQueueTimeout(
                        f"子任务排队超时（{self.queue_timeout_s:.1f}s）："
                        f"{child_id or '<unnamed>'} 显式放弃，不静默丢弃") from e
            finally:
                self._state.waiting -= 1
        else:
            await self._sem.acquire()      # 立即获槽（acquire 不让出，无竞态）
        self._state.in_flight += 1
        try:
            yield
        finally:
            self._state.in_flight -= 1
            self._sem.release()

    # ── runChildTask 沿用限制 ─────────────────────────────────────

    async def run_child(self, fn: Callable[..., Awaitable[T]], *,
                        child_id: str = "", turns: int = 1,
                        **kwargs: Any) -> T:
        """治理面内部子任务：继承同一 governor（排队/并发）与统一轮数上限。
        fn 返回 awaitable；turns 为该子任务消耗的轮数。"""
        async with self.slot(child_id):
            self.budget.tick(turns)     # 统一轮数上限（超限 TurnLimitExceeded）
            return await fn(**kwargs)

    # ── 观测 ───────────────────────────────────────────────────

    def stats(self) -> dict[str, Any]:
        return {
            "base_concurrency": self.base_concurrency,
            "multiplier": self.multiplier,
            "capacity": self.capacity,
            "in_flight": self._state.in_flight,
            "waiting": self._state.waiting,
            "queued_total": self._state.queued_total,
            "timed_out": self._state.timed_out,
            "turns": self.budget.stats(),
        }
