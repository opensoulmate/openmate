# -*- coding: utf-8 -*-
"""⚡0.2 首条消息提速——new_session 预热引擎 + 微小预热 LLM 请求（路线图紧急插队 0.2）。

用户实测"第一条回复特别慢"（6s 引擎准备 + 27s 模型大首包）。方案（路线图拍板）：
a) new_session 时即预热引擎：记忆检索/画像/工具装配等上下文源**并行**预取并缓存，
   用户打字时间自然覆盖引擎准备——首条消息直接吃缓存；
b) 会话创建时发一次微小预热 LLM 请求（同系统提示前缀+同工具面，回复丢弃）暖
   服务端前缀缓存，真正首条消息打到热前缀。

纪律（与 0.1 同款"旁路绝不破坏主链路"）：
- 预热是旁路：任一环节失败只记日志+计数，绝不抛出、绝不阻塞 new_session / prompt；
- 预热产物不进会话历史、不落库（不污染消息面）；
- 缓存带 TTL（默认 120s）：首条消息吃新鲜缓存，超时回退现场计算（新鲜度优先，
  现场计算路径零变化——缓存只是快路径）。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

DEFAULT_TTL_S = 120.0

# 预热收集器：session_id -> 产物（str/None/任意）。同步/异步都支持——
# 同步源是 SQLite/文件毫秒级直接跑，HTTP 源（MCP 工具装配）是 async。
Gatherer = Callable[[str], Any]
# 微小预热 LLM 请求：(session_id, base_system_prompt) -> None（回复丢弃）
PrefillFn = Callable[[str, str], Awaitable[None]]


class PromptPrefetcher:
    """会话创建期预热器：上下文源并行预取 + 微小预热 LLM 请求，产物按 TTL 复用。"""

    def __init__(
        self,
        *,
        gatherers: dict[str, Gatherer] | None = None,
        prefill_fn: PrefillFn | None = None,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.time,
    ):
        self._gatherers: dict[str, Gatherer] = dict(gatherers or {})
        self._prefill_fn = prefill_fn
        self._ttl_s = float(ttl_s)
        self._clock = clock
        self._cache: dict[str, dict[str, Any]] = {}
        self._ts: dict[str, float] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        # 可观测性计数（用户极度重视可观测性）
        self.stats = {
            "warm_runs": 0,
            "warm_failures": 0,
            "prefill_runs": 0,
            "prefill_failures": 0,
            "cache_hits": 0,
        }

    # ── 配置面 ────────────────────────────────────────────────
    def add_gatherer(self, name: str, fn: Gatherer) -> None:
        self._gatherers[name] = fn

    # ── 预热 ──────────────────────────────────────────────────
    async def warm(self, session_id: str, *, base_system_prompt: str = "") -> dict:
        """并行预取全部上下文源 + 并行发微小预热 LLM 请求。返回本次产物 dict。

        任何收集器/预热请求失败都不抛出（旁路纪律）：失败源缺席于产物，
        其余源照常缓存。
        """
        self.stats["warm_runs"] += 1
        results: list[tuple[str, Any]] = []

        async def _one(name: str, fn: Gatherer):
            try:
                v = fn(session_id)
                if inspect.isawaitable(v):
                    v = await v
                return name, v, None
            except Exception as e:  # 旁路：失败只计数，不冒泡
                self.stats["warm_failures"] += 1
                logger.warning("[prefetch] gatherer %s failed (non-fatal): %s", name, e)
                return name, None, e

        coros: list = [_one(n, f) for n, f in self._gatherers.items()]
        if self._prefill_fn is not None:
            coros.append(_one("__prefill__", lambda _sid: self._run_prefill(session_id, base_system_prompt)))
        outcomes = await asyncio.gather(*coros)

        parts: dict[str, Any] = {}
        for name, value, err in outcomes:
            if name == "__prefill__":
                continue
            if err is None:
                parts[name] = value
        self._cache[session_id] = parts
        self._ts[session_id] = self._clock()
        logger.info(
            "[prefetch] warm done session=%s parts=%s prefill=%s",
            session_id, sorted(parts.keys()), self.stats["prefill_runs"],
        )
        return parts

    async def _run_prefill(self, session_id: str, base_system_prompt: str) -> None:
        self.stats["prefill_runs"] += 1
        try:
            if self._prefill_fn is None:
                return
            await self._prefill_fn(session_id, base_system_prompt)
        except Exception as e:  # 预热请求失败只计数（不影响会话主链路）
            self.stats["prefill_failures"] += 1
            logger.warning("[prefetch] prefill warm failed (non-fatal): %s", e)

    def schedule_warm(self, session_id: str, *, base_system_prompt: str = "") -> "asyncio.Task":
        """fire-and-forget 调度预热（new_session 用）；任务句柄存留便于测试 join。"""
        task = asyncio.create_task(
            self.warm(session_id, base_system_prompt=base_system_prompt)
        )
        self._tasks[session_id] = task

        def _cleanup(_t, sid=session_id):
            self._tasks.pop(sid, None)

        task.add_done_callback(_cleanup)
        return task

    # ── 复用 ──────────────────────────────────────────────────
    def take(self, session_id: str, *, ttl_s: float | None = None) -> dict:
        """取预热产物（TTL 内有效）。只读不删：TTL 内多条消息都可复用；
        未预热/超时返回 {}——调用方回退现场计算，行为与预热前逐字一致。

        值契约：产物值应为不可变值（str/None 等）——顶层 dict 是 copy，
        收集器若返回可变共享对象（list/dict）则调用方不得原地改写。"""
        ts = self._ts.get(session_id)
        if ts is None:
            return {}
        limit = self._ttl_s if ttl_s is None else float(ttl_s)
        if (self._clock() - ts) > limit:
            return {}
        self.stats["cache_hits"] += 1
        return dict(self._cache.get(session_id) or {})

    def invalidate(self, session_id: str) -> None:
        """显式作废某会话预热缓存（预热产物可能过期时调用）。"""
        self._cache.pop(session_id, None)
        self._ts.pop(session_id, None)
