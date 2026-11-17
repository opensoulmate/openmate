# -*- coding: utf-8 -*-
"""Pi 取长补短 #6——LLM 网关补三件事：错误归一 / 用量统计入账 / 请求排队。

- 错误归一：error_codes.vendor_compat 边界归一 → 结构化码+可重试标记挂
  ModelGatewayError/TokenUsage（决策只看码；不抄 piRetryErrorNormalizer 文案正则）；
- 用量入账：网关计量 → agent.cost_tracker.CostTracker 持久台账（成本可观测地基，
  best-effort 台账故障不破坏调用流但必须可见）；
- 请求排队：RequestQueue 并发节流+有界排队+排队超时显式报因（治高峰超时）。

通用网关能力（非投标专用）。
"""
import asyncio
from types import SimpleNamespace

import pytest

from error_codes import ErrorCode
from model_gateway import gateway as gw
from model_gateway.gateway import (
    CostTrackerAccountant,
    ModelGateway,
    ModelGatewayError,
)
from model_gateway.queue import RequestQueue, RequestQueueError


def run(coro):
    return asyncio.run(coro)


# ── 测试替身 ─────────────────────────────────────────────────────

class FakeStatusError(Exception):
    """OpenAI SDK 风格状态错误（带 status_code/body）"""

    def __init__(self, status, body, message=""):
        super().__init__(message or str(body))
        self.status_code = status
        self.body = body
        self.message = message or str(body)


def ok_response(prompt=10, completion=5):
    return SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion,
                              total_tokens=prompt + completion),
        choices=[])


class FakeCompletions:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        b = self.behavior
        if asyncio.iscoroutinefunction(b):
            return await b(kwargs)
        return b(kwargs)


class FakeOpenAI:
    def __init__(self, behavior):
        self.chat = SimpleNamespace(completions=FakeCompletions(behavior))


def make_gateway(monkeypatch, behavior, **gw_kwargs):
    fake = FakeOpenAI(behavior)
    monkeypatch.setattr(gw, "AsyncOpenAI", lambda **kw: fake)
    g = ModelGateway(usage_accountant=gw_kwargs.pop("usage_accountant", None),
                     **gw_kwargs)
    g.register_model("openai", "m-test", "http://fake/v1", api_key="k")
    return g, fake


class RecordingAccountant:
    def __init__(self, fail=False):
        self.records = []
        self.fail = fail

    def record_usage(self, usage):
        if self.fail:
            raise RuntimeError("ledger down")
        self.records.append(usage)


# ── ① RequestQueue 排队与并发节流 ────────────────────────────────

class TestRequestQueue:
    def test_concurrency_throttled(self):
        async def main():
            q = RequestQueue(max_concurrent=2, queue_timeout=5)
            state = {"live": 0, "peak": 0}

            async def worker():
                async with q.slot("m") as info:
                    state["live"] += 1
                    state["peak"] = max(state["peak"], state["live"])
                    await asyncio.sleep(0.02)
                    state["live"] -= 1
                return info

            results = await asyncio.gather(*[worker() for _ in range(6)])
            assert state["peak"] <= 2                       # 并发节流实证
            assert all(r["queue_wait_ms"] >= 0 for r in results)
            assert any(r["queue_wait_ms"] > 0 for r in results)  # 有人排队等待
        run(main())

    def test_queue_timeout_visible(self):
        async def main():
            q = RequestQueue(max_concurrent=1, queue_timeout=0.05)
            hold = asyncio.Event()

            async def holder():
                async with q.slot("m"):
                    await hold.wait()

            t = asyncio.ensure_future(holder())
            await asyncio.sleep(0.01)
            with pytest.raises(RequestQueueError) as ei:
                async with q.slot("m"):
                    pass
            assert "排队超时" in str(ei.value)
            assert ei.value.retryable is True
            hold.set()
            await t
        run(main())

    def test_depth_full_rejects_immediately(self):
        async def main():
            q = RequestQueue(max_concurrent=1, max_depth=1, queue_timeout=5)
            hold = asyncio.Event()

            async def holder():
                async with q.slot("m"):
                    await hold.wait()

            t = asyncio.ensure_future(holder())
            await asyncio.sleep(0.01)

            async def waiter():
                async with q.slot("m"):
                    pass

            w = asyncio.ensure_future(waiter())             # 占满唯一排队位
            await asyncio.sleep(0.01)
            with pytest.raises(RequestQueueError) as ei:
                async with q.slot("m"):                     # 超深度立即拒绝
                    pass
            assert "队列已满" in str(ei.value)
            hold.set()
            await asyncio.gather(t, w)
        run(main())

    def test_slot_released_on_exception(self):
        async def main():
            q = RequestQueue(max_concurrent=1)
            with pytest.raises(RuntimeError):
                async with q.slot("m"):
                    raise RuntimeError("boom")
            assert q.stats()["in_flight"] == 0
            async with q.slot("m"):                          # 位已释放可复用
                pass
        run(main())

    def test_stats_shape(self):
        q = RequestQueue(max_concurrent=3, max_depth=9, queue_timeout=7)
        s = q.stats()
        assert s == {"max_concurrent": 3, "max_depth": 9, "queue_timeout_s": 7.0,
                     "in_flight": 0, "waiting": 0, "peak_in_flight": 0,
                     "peak_waiting": 0, "total_acquired": 0, "total_timeouts": 0,
                     "total_rejected": 0, "avg_wait_ms": 0.0}

    def test_params_validation(self):
        with pytest.raises(ValueError):
            RequestQueue(max_concurrent=0)
        with pytest.raises(ValueError):
            RequestQueue(max_depth=-1)
        with pytest.raises(ValueError):
            RequestQueue(queue_timeout=0)


# ── ② 错误归一（vendor_compat → 网关错误面）──────────────────────

class TestErrorNormalization:
    def test_rate_limit_retryable_marked(self, monkeypatch):
        def boom(kwargs):
            raise FakeStatusError(429, {"error": {
                "message": "Rate limit reached for requests",
                "type": "rate_limit_error"}})

        g, _ = make_gateway(monkeypatch, boom)
        with pytest.raises(ModelGatewayError) as ei:
            run(g.chat_completion(messages=[{"role": "user", "content": "hi"}],
                                  model="m-test"))
        err = ei.value
        assert err.retryable is True                        # 可重试错误标记
        assert err.normalized.code == ErrorCode.LLM_RATE_LIMITED
        assert err.normalized.kind == "rate_limit"
        # 失败计量携带归一字段
        rec = g.get_recent_usage(1)[0]
        assert rec["error_code"] == ErrorCode.LLM_RATE_LIMITED
        assert rec["retryable"] is True and rec["success"] is False

    def test_auth_failure_not_retryable(self, monkeypatch):
        def boom(kwargs):
            raise FakeStatusError(401, {"error": {
                "message": "Incorrect API key", "code": "invalid_api_key"}})

        g, _ = make_gateway(monkeypatch, boom)
        with pytest.raises(ModelGatewayError) as ei:
            run(g.chat_completion(messages=[], model="m-test"))
        assert ei.value.retryable is False
        assert ei.value.normalized.code == ErrorCode.LLM_AUTH_FAILED

    def test_transport_exception_network_code(self, monkeypatch):
        import httpx

        def boom(kwargs):
            raise httpx.ConnectError("connection refused")

        g, _ = make_gateway(monkeypatch, boom)
        with pytest.raises(ModelGatewayError) as ei:
            run(g.chat_completion(messages=[], model="m-test"))
        assert ei.value.normalized.code == ErrorCode.LLM_NETWORK_ERROR
        assert ei.value.retryable is True

    def test_plain_exception_unknown_not_retryable(self, monkeypatch):
        def boom(kwargs):
            raise RuntimeError("blip")

        g, _ = make_gateway(monkeypatch, boom)
        with pytest.raises(ModelGatewayError) as ei:
            run(g.chat_completion(messages=[], model="m-test"))
        assert ei.value.normalized.code == ErrorCode.LLM_UNKNOWN
        assert ei.value.retryable is False                  # 未知错误不猜不自动重试

    def test_error_message_kept_for_humans(self, monkeypatch):
        def boom(kwargs):
            raise FakeStatusError(429, {"error": {"type": "rate_limit_error",
                                                  "message": "Rate limit reached"}})

        g, _ = make_gateway(monkeypatch, boom)
        with pytest.raises(ModelGatewayError):
            run(g.chat_completion(messages=[], model="m-test"))
        rec = g.get_recent_usage(1)[0]
        assert "Rate limit" in rec["error_message"]         # 原文保留供排查


# ── ③ 用量统计入账（网关计量 → 持久台账）─────────────────────────

class TestUsageAccounting:
    def test_success_and_failure_accounted(self, monkeypatch):
        acct = RecordingAccountant()

        def behavior(kwargs):
            if kwargs.get("messages"):
                return ok_response()
            raise RuntimeError("x")

        g, _ = make_gateway(monkeypatch, behavior, usage_accountant=acct)
        run(g.chat_completion(messages=[{"role": "user", "content": "hi"}],
                              model="m-test"))
        with pytest.raises(ModelGatewayError):
            run(g.chat_completion(messages=[], model="m-test"))
        assert len(acct.records) == 2                       # 成败都入账
        assert acct.records[0].input_tokens == 10
        assert acct.records[0].queue_wait_ms >= 0

    def test_cost_tracker_ledger_rows(self, monkeypatch, tmp_path):
        from agent.cost_tracker import CostTracker
        tracker = CostTracker(db_path=str(tmp_path / "costs.db"))
        fake = FakeOpenAI(lambda kw: ok_response(100, 50))
        monkeypatch.setattr(gw, "AsyncOpenAI", lambda **kw: fake)
        g = ModelGateway(usage_accountant=CostTrackerAccountant(tracker))
        g.register_model("openai", "m-test", "http://fake/v1", api_key="k",
                         cost_per_1k_input=1.0, cost_per_1k_output=2.0)
        run(g.chat_completion(messages=[{"role": "user", "content": "hi"}],
                              model="m-test"))
        assert tracker.get_daily_cost() > 0                 # 真实台账落账（端点单价精算）
        assert tracker.get_model_breakdown(days=1)["m-test"]["calls"] == 1

    def test_accountant_failure_does_not_break_call(self, monkeypatch):
        acct = RecordingAccountant(fail=True)               # 台账故障
        g, _ = make_gateway(monkeypatch, lambda kw: ok_response(),
                            usage_accountant=acct)
        resp = run(g.chat_completion(messages=[{"role": "user", "content": "hi"}],
                                     model="m-test"))
        assert resp is not None                             # 调用流不受影响
        assert g.get_recent_usage(1)[0]["success"] is True  # 计量仍在环形缓冲

    def test_none_accountant_zero_ledger(self, monkeypatch):
        g, _ = make_gateway(monkeypatch, lambda kw: ok_response(),
                            usage_accountant=None)
        run(g.chat_completion(messages=[], model="m-test"))
        assert g._usage_accountant is None                  # 零入账=零行为变化

    def test_auto_accountant_lazy_creates_tracker(self, monkeypatch):
        created = []

        class FakeTracker:
            def record_usage(self, **kw):
                created.append(kw)

        import agent.cost_tracker as ct
        monkeypatch.setattr(ct, "CostTracker", lambda *a, **k: FakeTracker())
        g, _ = make_gateway(monkeypatch, lambda kw: ok_response(),
                            usage_accountant="auto")
        run(g.chat_completion(messages=[], model="m-test"))
        run(g.chat_completion(messages=[], model="m-test"))
        assert len(created) == 2
        assert created[0]["request_type"] == "gateway"      # 与会话路径区分口径
        assert created[0]["custom_cost"] is not None        # 网关端点单价精算入账

    def test_callable_accountant_supported(self, monkeypatch):
        seen = []
        g, _ = make_gateway(monkeypatch, lambda kw: ok_response(),
                            usage_accountant=lambda u: seen.append(u))
        run(g.chat_completion(messages=[], model="m-test"))
        assert len(seen) == 1


# ── ④ 网关接线（排队×计量×归一 全链路）───────────────────────────

class TestGatewayWiring:
    def test_parallel_calls_throttled_and_measured(self, monkeypatch):
        async def slow(kwargs):
            await asyncio.sleep(0.02)
            return ok_response()

        g, fake = make_gateway(monkeypatch, slow,
                               max_concurrent=1, queue_timeout=5)

        async def main():
            await asyncio.gather(*[
                g.chat_completion(messages=[{"role": "user", "content": str(i)}],
                                  model="m-test") for i in range(3)])

        run(main())
        assert len(fake.chat.completions.calls) == 3
        recs = g.get_recent_usage(3)
        assert all(r["success"] for r in recs)
        waits = sorted(r["queue_wait_ms"] for r in recs)
        assert waits[0] < waits[-1] and waits[-1] > 0       # 串行化产生排队等待
        assert g._queue.stats()["peak_in_flight"] == 1      # 并发上限实证

    def test_queue_full_error_bypasses_failure_accounting(self, monkeypatch):
        acct = RecordingAccountant()

        async def slow(kwargs):
            await asyncio.sleep(0.2)
            return ok_response()

        g, _ = make_gateway(monkeypatch, slow, usage_accountant=acct,
                            max_concurrent=1, max_queue_depth=0, queue_timeout=5)

        async def main():
            t = asyncio.ensure_future(g.chat_completion(
                messages=[{"role": "user", "content": "a"}], model="m-test"))
            await asyncio.sleep(0.02)
            with pytest.raises(RequestQueueError):
                await g.chat_completion(
                    messages=[{"role": "user", "content": "b"}], model="m-test")
            await t

        run(main())
        assert len(acct.records) == 1                       # 排队拒绝≠调用失败计量

    def test_stream_usage_and_normalization(self, monkeypatch):
        def stream_ok(kwargs):
            async def gen():
                for w in ("你", "好"):
                    yield SimpleNamespace(
                        choices=[SimpleNamespace(
                            delta=SimpleNamespace(content=w))],
                        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2))
            return gen()

        acct = RecordingAccountant()
        g, _ = make_gateway(monkeypatch, stream_ok, usage_accountant=acct)
        out = "".join(run(_collect(g.chat_completion_stream(
            messages=[{"role": "user", "content": "hi"}], model="m-test"))))
        assert out == "你好"
        assert len(acct.records) == 1                       # 流式也入账
        assert acct.records[0].input_tokens == 3

    def test_stream_error_normalized(self, monkeypatch):
        def stream_boom(kwargs):
            async def gen():
                yield SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="x"))],
                    usage=None)
                raise FakeStatusError(503, {"error": {"type": "server_error",
                                                      "message": "overloaded"}})
            return gen()

        g, _ = make_gateway(monkeypatch, stream_boom)

        async def main():
            chunks = []
            with pytest.raises(ModelGatewayError) as ei:
                async for c in g.chat_completion_stream(
                        messages=[{"role": "user", "content": "hi"}],
                        model="m-test"):
                    chunks.append(c)
            return ei.value, chunks

        err, chunks = run(main())
        assert chunks == ["x"]
        assert err.normalized.code == ErrorCode.LLM_SERVER_ERROR
        assert err.retryable is True


async def _collect(gen):
    return [c async for c in gen]
