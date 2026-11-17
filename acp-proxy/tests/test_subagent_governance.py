# -*- coding: utf-8 -*-
"""Pi #14 子代理治理运行时基线——治理面单测。

三件套逐项：①并发乘数×2 超出排队 ②轮数上限统一施加 ③runChildTask 沿用限制。
"""
import asyncio

import pytest

from agent.subagent_governance import (
    DEFAULT_MAX_TURNS,
    SubagentGovernor,
    SubagentQueueFull,
    SubagentQueueTimeout,
    TurnBudget,
    TurnLimitExceeded,
)


def run(coro):
    return asyncio.run(coro)


class TestTurnBudget:
    def test_unified_limit(self):
        b = TurnBudget(max_turns=3)
        b.tick()
        b.tick(2)
        assert b.exceeded and b.remaining == 0
        with pytest.raises(TurnLimitExceeded, match="轮数上限已达"):
            b.tick()

    def test_child_inherits_remaining(self):
        """runChildTask 沿用限制：子预算=父剩余且不超过统一上限。"""
        parent = TurnBudget(max_turns=100)
        parent.tick(97)
        child = parent.child()
        assert child.max_turns == 3            # 沿用父剩余
        assert child.max_turns <= parent.max_turns  # 不超过统一上限
        child.tick(3)
        with pytest.raises(TurnLimitExceeded):
            child.tick()

    def test_child_of_exhausted_parent_rejected(self):
        parent = TurnBudget(max_turns=1)
        parent.tick()
        child = parent.child()
        with pytest.raises(TurnLimitExceeded):
            child.tick()

    def test_default_is_100(self):
        assert TurnBudget().max_turns == DEFAULT_MAX_TURNS == 100

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            TurnBudget(max_turns=-1)


class TestConcurrencyAndQueue:
    def test_capacity_is_base_times_two(self):
        """①并发乘数×2。"""
        gov = SubagentGovernor(base_concurrency=4)
        assert gov.capacity == 8
        assert gov.multiplier == 2

    def test_overflow_queues_fifo(self):
        """①超出排队：容量外的子任务排队 FIFO，全部最终执行且不超并发。"""
        async def main():
            gov = SubagentGovernor(base_concurrency=1, queue_timeout_s=5.0)
            active = 0
            peak = 0
            order: list[str] = []

            async def work(i):
                nonlocal active, peak
                async with gov.slot(f"t{i}"):
                    active += 1
                    peak = max(peak, active)
                    await asyncio.sleep(0.01)
                    active -= 1
                    order.append(f"t{i}")

            await asyncio.gather(*(work(i) for i in range(6)))
            return gov, peak, order

        gov, peak, order = run(main())
        assert peak <= 2                       # base 1 × 2
        assert sorted(order) == [f"t{i}" for i in range(6)]
        assert gov.stats()["queued_total"] == 4   # 6 任务、容量 2 → 4 真排队（立即获槽不计）

    def test_queue_full_rejected_explicitly(self):
        """①队满显式报因，不静默丢弃（容量=base×2=2 占满后再入队即拒）。"""
        async def main():
            gov = SubagentGovernor(base_concurrency=1, max_queue=1,
                                   queue_timeout_s=5.0)
            blocker = asyncio.Event()

            async def hold(tag):
                async with gov.slot(tag):
                    await blocker.wait()

            holders = [asyncio.create_task(hold(f"h{i}")) for i in range(2)]
            await asyncio.sleep(0.02)          # 两 holder 占满 capacity=2

            async def queued():
                async with gov.slot("queued"):
                    pass

            q = asyncio.create_task(queued())
            await asyncio.sleep(0.02)          # queued 排队中（waiting=1 = max_queue）
            with pytest.raises(SubagentQueueFull, match="拒绝入队"):
                async with gov.slot("rejected"):
                    pass
            blocker.set()
            await asyncio.gather(*holders, q)

        run(main())

    def test_queue_timeout_explicit(self):
        """①排队超时显式报因。"""
        async def main():
            gov = SubagentGovernor(base_concurrency=1, queue_timeout_s=0.05)
            blocker = asyncio.Event()

            async def hold(tag):
                async with gov.slot(tag):
                    await blocker.wait()

            holders = [asyncio.create_task(hold(f"h{i}")) for i in range(2)]
            await asyncio.sleep(0.02)          # 两 holder 占满 capacity=2
            with pytest.raises(SubagentQueueTimeout, match="显式放弃"):
                async with gov.slot("waiter"):
                    pass
            assert gov.stats()["timed_out"] == 1
            blocker.set()
            await asyncio.gather(*holders)

        run(main())


class TestRunChildInheritance:
    def test_run_child_consumes_unified_budget(self):
        """③runChildTask 沿用：同 governor 排队/并发 + 统一轮数上限。"""
        async def main():
            gov = SubagentGovernor(base_concurrency=2, max_turns=2)

            async def fn(x):
                return x * 2

            assert await gov.run_child(fn, child_id="c1", turns=1, x=21) == 42
            assert await gov.run_child(fn, child_id="c2", turns=1, x=1) == 2
            with pytest.raises(TurnLimitExceeded):
                await gov.run_child(fn, child_id="c3", turns=1, x=1)
            return gov

        gov = run(main())
        assert gov.budget.used == 2
        assert gov.stats()["turns"]["remaining"] == 0

    def test_run_child_uses_slots(self):
        async def main():
            gov = SubagentGovernor(base_concurrency=1)
            peak = 0
            active = 0

            async def fn():
                nonlocal peak, active
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
                return True

            await asyncio.gather(*(gov.run_child(fn, child_id=f"c{i}", turns=1)
                                   for i in range(5)))
            return peak

        assert run(main()) <= 2
