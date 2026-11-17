# -*- coding: utf-8 -*-
"""RequestQueue — LLM 请求排队与并发节流（Pi 取长补短 #6 第三件）。

来源：OpenBidKit 本地 OpenAI 代理（agentOpenAiProxy）「请求排队与并发节流」——
高峰排队等空位而不是打爆上游（治高峰超时）。SoulMate 落在 LLM 网关调度面
（与 circuit_breaker 熔断、round-robin 负载均衡同层，包内协作者先例）：

- 并发节流：全局信号量 max_concurrent（同时在飞的 LLM 请求上限）；
- 有界排队：等待者超 max_depth 直接拒绝（RequestQueueError，禁无限堆积）；
- 排队超时：等空位超 queue_timeout 显式报因（RequestQueueError，失败必须可见，
  排队饱和是瞬时故障 retryable=True）；
- 计量：slot() 产出 queue_wait_ms（排队等待耗时，挂进 TokenUsage 可观测），
  stats() 给深度/峰值/超时计数（高峰行为可观测）。

纯 asyncio 确定性实现（无 I/O），独立可测（tests/test_model_gateway_increments.py）。
"""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager

logger = logging.getLogger("model_gateway.queue")

__all__ = ["RequestQueue", "RequestQueueError"]

DEFAULT_MAX_CONCURRENT = 8      # 并发上限（默认；env LLM_GATEWAY_MAX_CONCURRENT 可调）
DEFAULT_MAX_DEPTH = 32          # 排队深度上限（超出即拒绝，禁无限堆积）
DEFAULT_QUEUE_TIMEOUT_S = 30.0  # 排队等待上限（超出显式报因）


class RequestQueueError(Exception):
    """排队失败（饱和/超时）——瞬时故障，retryable=True。"""
    retryable = True


class RequestQueue:
    """LLM 请求排队 + 并发节流（信号量 + 有界等待 + 计量）。"""

    def __init__(self, max_concurrent: int = DEFAULT_MAX_CONCURRENT,
                 max_depth: int = DEFAULT_MAX_DEPTH,
                 queue_timeout: float = DEFAULT_QUEUE_TIMEOUT_S,
                 now=None):
        self.max_concurrent = int(max_concurrent)
        self.max_depth = int(max_depth)
        self.queue_timeout = float(queue_timeout)
        if self.max_concurrent < 1:
            raise ValueError(f"max_concurrent 需 ≥1，收到 {max_concurrent}")
        if self.max_depth < 0:
            raise ValueError(f"max_depth 需 ≥0，收到 {max_depth}")
        if self.queue_timeout <= 0:
            raise ValueError(f"queue_timeout 需 >0，收到 {queue_timeout}")
        self._now = now or time.monotonic
        self._sem = asyncio.Semaphore(self.max_concurrent)
        self._waiting = 0
        self._in_flight = 0
        self._peak_waiting = 0
        self._peak_in_flight = 0
        self._total_acquired = 0
        self._total_timeouts = 0
        self._total_rejected = 0
        self._wait_ms_sum = 0.0

    @asynccontextmanager
    async def slot(self, model: str = ""):
        """申请执行位（排队→拿位→释放）。产出 {"queue_wait_ms": float}。

        满载时 FIFO 等待（asyncio 信号量公平性）；等待超 queue_timeout →
        RequestQueueError 显式报因；排队深度超 max_depth → 立即拒绝。
        """
        if (self._in_flight >= self.max_concurrent
                and self._waiting >= self.max_depth):
            # 全占满且排队位也满：立即拒绝（max_depth=0=不允许排队）
            self._total_rejected += 1
            raise RequestQueueError(
                f"LLM 请求队列已满（在飞 {self._in_flight}/{self.max_concurrent}，"
                f"排队 {self._waiting}/{self.max_depth}，"
                f"model={model or '-'}）——高峰限流拒绝，稍后重试")
        t0 = self._now()
        self._waiting += 1
        self._peak_waiting = max(self._peak_waiting, self._waiting)
        try:
            try:
                await asyncio.wait_for(self._sem.acquire(),
                                       timeout=self.queue_timeout)
            except asyncio.TimeoutError:
                self._total_timeouts += 1
                raise RequestQueueError(
                    f"LLM 请求排队超时（等待 {self.queue_timeout:.1f}s 无空位，"
                    f"model={model or '-'}）——高峰饱和，稍后重试") from None
        finally:
            self._waiting -= 1
        wait_ms = (self._now() - t0) * 1000.0
        self._wait_ms_sum += wait_ms
        self._total_acquired += 1
        self._in_flight += 1
        self._peak_in_flight = max(self._peak_in_flight, self._in_flight)
        try:
            yield {"queue_wait_ms": round(wait_ms, 3)}
        finally:
            self._in_flight -= 1
            self._sem.release()

    def stats(self) -> dict:
        """排队面可观测（高峰行为诊断）。"""
        return {
            "max_concurrent": self.max_concurrent,
            "max_depth": self.max_depth,
            "queue_timeout_s": self.queue_timeout,
            "in_flight": self._in_flight,
            "waiting": self._waiting,
            "peak_in_flight": self._peak_in_flight,
            "peak_waiting": self._peak_waiting,
            "total_acquired": self._total_acquired,
            "total_timeouts": self._total_timeouts,
            "total_rejected": self._total_rejected,
            "avg_wait_ms": (round(self._wait_ms_sum / self._total_acquired, 3)
                            if self._total_acquired else 0.0),
        }
