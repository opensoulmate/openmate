# -*- coding: utf-8 -*-
"""LLM重试策略单一真源专项（2026-09-28 P0——SUMMARY §五 cortex缺口销账）

问题（本轮修复对象）：kilocode retry.ts的Retry-After三格式+5xx无条件重试是
P0缺口，接线前llm_engine三条传输路径重试各自为政且有洞：
- chat()（judge/计划/摘要/标题的非流式真实路径）：**零HTTP重试**——瞬时429/5xx
  直接"[LLM错误: …]"整体失败
- chat_stream()（soulmate真实回复主路径）：**零重试**——同上
- chat_stream_with_tools()：固定2^n退避（不听Retry-After）、可重试集
  (400, 429, 500, 502, 503)把400当可重试却漏501/504等其余5xx

覆盖：
1. 策略单元（utils.llm_retry）：Retry-After三格式/大小写不敏感/非法值/退避
   有头听头无头封顶30s/5xx无条件/瞬时错误判定
2. chat()集成（fake传输）：429听头后成功/5xx耗尽契约/401 fail-fast只1发/
   瞬时传输错误重试/400不重试（回归钉）/504重试（回归钉）
3. chat_stream()集成：429重试/401 fail-fast/零输出ReadTimeout重试/
   已输出ReadTimeout不重试（双份回复防御钉）/耗尽可见失败
4. with_tools集成：Retry-After被消费（退避时长断言）/504重试（回归钉）/
   400 fail-fast（行为变更钉）/已输出不重试
5. 接线钉：llm_engine三路消费llm_retry策略、旧固定退避与硬编码状态集绝迹
"""
import asyncio
import json
import sys
import time
from email.utils import formatdate
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from agent.llm_engine import LLMEngine
from utils import llm_retry
from utils.llm_retry import (
    MAX_ATTEMPTS,
    RETRY_INITIAL_DELAY_MS,
    RETRY_MAX_DELAY_NO_HEADERS_MS,
    backoff_ms,
    is_retryable_status,
    is_transient_error,
    parse_retry_after_ms,
    plan_error_retry,
    plan_status_retry,
)

HERE = Path(__file__).parent.parent


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


def _engine():
    return LLMEngine(api_key="k", base_url="http://fake/v1", model="mimo-test")


def _patch_sleep(monkeypatch):
    """拦截asyncio.sleep：记录等待时长但不真睡（快测+可断言退避值）"""
    waits: list[float] = []

    async def _fake_sleep(delay):
        waits.append(float(delay))

    monkeypatch.setattr("agent.llm_engine.asyncio.sleep", _fake_sleep)
    return waits


# ════════════════════════════════════════════════════════════════
# 1. 策略单元（kilocode retry.ts逐点对应）
# ════════════════════════════════════════════════════════════════


class TestRetryAfterParsing:
    def test_format1_retry_after_ms_header(self):
        assert parse_retry_after_ms({"retry-after-ms": "1500"}) == 1500.0

    def test_format1_case_insensitive(self):
        assert parse_retry_after_ms({"Retry-After-MS": "800"}) == 800.0

    def test_format2_seconds(self):
        assert parse_retry_after_ms({"retry-after": "3"}) == 3000.0

    def test_format2_fractional_seconds(self):
        assert parse_retry_after_ms({"retry-after": "0.5"}) == 500.0

    def test_format3_http_date(self):
        hdr = {"retry-after": formatdate(time.time() + 30, usegmt=True)}
        ms = parse_retry_after_ms(hdr)
        assert ms is not None
        assert 25000 <= ms <= 30100  # ≈30s（解析−now）

    def test_http_date_in_past_returns_none(self):
        hdr = {"retry-after": formatdate(time.time() - 60, usegmt=True)}
        assert parse_retry_after_ms(hdr) is None

    def test_garbage_value_returns_none(self):
        assert parse_retry_after_ms({"retry-after": "soon", "retry-after-ms": "abc"}) is None

    def test_missing_headers_returns_none(self):
        assert parse_retry_after_ms(None) is None
        assert parse_retry_after_ms({}) is None

    def test_ms_header_wins_over_seconds(self):
        # 三格式优先序：retry-after-ms → retry-after秒数 → HTTP date
        ms = parse_retry_after_ms({"retry-after-ms": "100", "retry-after": "9"})
        assert ms == 100.0

    def test_negative_rejected(self):
        assert parse_retry_after_ms({"retry-after-ms": "-5"}) is None
        assert parse_retry_after_ms({"retry-after": "-1"}) is None


class TestBackoff:
    def test_no_headers_exponential_capped_30s(self):
        # 无头：2s×2^n封顶30s（kilocode RETRY_MAX_DELAY_NO_HEADERS）
        assert backoff_ms(1) == RETRY_INITIAL_DELAY_MS
        assert backoff_ms(2) == 4000.0
        assert backoff_ms(3) == 8000.0
        assert backoff_ms(10) == RETRY_MAX_DELAY_NO_HEADERS_MS
        assert backoff_ms(50) == RETRY_MAX_DELAY_NO_HEADERS_MS

    def test_headers_with_value_listen_to_header(self):
        # 有头听头：比盲目退避对限流API友好（调研结论原句）
        assert backoff_ms(1, {"retry-after-ms": "250"}) == 250.0
        assert backoff_ms(2, {"retry-after": "7"}) == 7000.0

    def test_headers_without_parseable_value_kilocode_semantics(self):
        # 有头但无可解析值：退避只cap i32（kilocode delay()语义，不封30s）
        val = backoff_ms(10, {"x-other": "1"})
        assert val > RETRY_MAX_DELAY_NO_HEADERS_MS  # 不受30s封顶
        assert val == RETRY_INITIAL_DELAY_MS * (2 ** 9)

    def test_cap_i32(self):
        huge = backoff_ms(1, {"retry-after-ms": "99999999999"})
        assert huge == 2_147_483_647


class TestStatusAndErrorClassification:
    def test_retryable_status_table(self):
        for s in (408, 429, 500, 501, 502, 503, 504, 599):
            assert is_retryable_status(s), s
        for s in (400, 401, 402, 403, 404, 418, 422):
            assert not is_retryable_status(s), s

    def test_5xx_unconditional(self):
        # kilocode注释即理由："transient server failures"，SDK没标也重试
        assert is_retryable_status(500) and is_retryable_status(504)

    def test_transient_httpx_transport_errors(self):
        assert is_transient_error(httpx.ConnectError("all failed"))
        assert is_transient_error(httpx.ReadTimeout("timed out"))
        assert is_transient_error(httpx.RemoteProtocolError("server disconnected"))

    def test_transient_message_match(self):
        # kilocode TRANSIENT_MESSAGES文案兜底
        assert is_transient_error(RuntimeError("socket hang up"))
        assert is_transient_error(RuntimeError("ECONNRESET"))
        assert is_transient_error(ValueError("failed to fetch"))

    def test_non_transient(self):
        assert not is_transient_error(ValueError("bad json"))
        assert not is_transient_error(RuntimeError("LLM API error 400: bad request"))


# ════════════════════════════════════════════════════════════════
# fake传输（chat()非流式 + 流式），可编排响应序列/异常，捕获payload与请求数
# ════════════════════════════════════════════════════════════════


class _FakeResp:
    def __init__(self, content=None, finish_reason="stop", status_code=200,
                 body="", usage=None, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers if headers is not None else {}
        self._data = {
            "choices": [{
                "message": {"content": content},
                "finish_reason": finish_reason,
            }],
        }
        if usage is not None:
            self._data["usage"] = usage

    def json(self):
        return self._data

    @property
    def text(self):
        return self._body


def _install_post_client(monkeypatch, responses: list) -> dict:
    """fake非流式client；返回 {"payloads": [...], "calls": N} 记录"""
    state = {"payloads": [], "calls": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            state["payloads"].append(json.loads(json.dumps(kw.get("json") or {})))
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            r = responses[idx]
            if callable(r):
                return r()
            return r

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _FakeClient)
    return state


def _sse(data: str) -> bytes:
    return f"data: {data}\n\n".encode()


def _delta_chunk(text=None, finish_reason=None, usage=None, tool_calls=None):
    delta = {}
    if text is not None:
        delta["content"] = text
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    obj = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
    if usage is not None:
        obj["usage"] = usage
    return obj


class _FakeStreamResp:
    def __init__(self, events, status_code=200, body=b"", headers=None):
        self.status_code = status_code
        self._events = events
        self._body = body
        self.headers = headers if headers is not None else {}

    async def aiter_bytes(self):
        for ev in self._events:
            if callable(ev):
                ev()
                continue
            if ev == "[DONE]":
                yield _sse("[DONE]")
            else:
                yield _sse(json.dumps(ev, ensure_ascii=False))

    async def aread(self):
        return self._body

    async def aclose(self):
        pass


def _install_stream_client(monkeypatch, responses: list) -> dict:
    state = {"payloads": [], "calls": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def build_request(self, method, url, **kw):
            return kw.get("json") or {}

        async def send(self, req, **kw):
            state["payloads"].append(json.loads(json.dumps(req)))
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            r = responses[idx]
            return r() if callable(r) else r

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _FakeClient)
    return state


# ════════════════════════════════════════════════════════════════
# 2. chat()集成（此前零HTTP重试——本轮P0修复对象）
# ════════════════════════════════════════════════════════════════


class TestChatRetry:
    def test_429_listen_retry_after_then_success(self, monkeypatch):
        waits = _patch_sleep(monkeypatch)
        state = _install_post_client(monkeypatch, [
            _FakeResp(status_code=429, body="rate limited",
                      headers={"retry-after-ms": "500"}),
            _FakeResp(content="恢复后的回答"),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "恢复后的回答"
        assert state["calls"] == 2  # 重试真实发生
        assert waits == [0.5]  # 有头听头：Retry-After 500ms被消费

    def test_5xx_exhaustion_visible_failure(self, monkeypatch):
        _patch_sleep(monkeypatch)
        state = _install_post_client(monkeypatch, [
            _FakeResp(status_code=500, body="boom",
                      headers={"retry-after-ms": "1"}),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out.startswith("[LLM错误:") and "500" in out  # 失败契约不变
        assert state["calls"] == MAX_ATTEMPTS  # 恰好3发，不无限

    def test_401_fail_fast_single_call(self, monkeypatch):
        waits = _patch_sleep(monkeypatch)
        state = _install_post_client(monkeypatch, [
            _FakeResp(status_code=401, body="bad key"),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert "[LLM错误:" in out
        assert state["calls"] == 1  # 凭证级错误不烧3次调用
        assert waits == []

    def test_400_not_retried_regression(self, monkeypatch):
        # 行为修正钉：400请求级错误重试无益（旧with_tools硬编码把400当可重试）
        _patch_sleep(monkeypatch)
        state = _install_post_client(monkeypatch, [
            _FakeResp(status_code=400, body="bad request"),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert "[LLM错误:" in out
        assert state["calls"] == 1

    def test_504_retried_regression(self, monkeypatch):
        # 回归钉：5xx无条件重试——旧(400,429,500,502,503)硬编码漏504
        _patch_sleep(monkeypatch)
        state = _install_post_client(monkeypatch, [
            _FakeResp(status_code=504, body="gateway timeout",
                      headers={"retry-after-ms": "1"}),
            _FakeResp(content="好的"),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "好的"
        assert state["calls"] == 2

    def test_transient_transport_error_retried(self, monkeypatch):
        waits = _patch_sleep(monkeypatch)

        def _raise():
            raise httpx.ConnectError("All connection attempts failed")

        state = _install_post_client(monkeypatch, [
            _raise,
            _FakeResp(content="网络恢复后的回答"),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "网络恢复后的回答"
        assert state["calls"] == 2
        assert waits == [2.0]  # 无头指数退避2s×2^0

    def test_transport_error_exhaustion_visible(self, monkeypatch):
        _patch_sleep(monkeypatch)

        def _raise():
            raise httpx.ReadTimeout("timed out")

        state = _install_post_client(monkeypatch, [_raise])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out.startswith("[LLM错误:")
        assert state["calls"] == MAX_ATTEMPTS

    def test_non_transport_error_not_retried(self, monkeypatch):
        _patch_sleep(monkeypatch)

        def _raise():
            raise ValueError("bad json")

        state = _install_post_client(monkeypatch, [_raise])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert "[LLM错误:" in out
        assert state["calls"] == 1  # 非瞬时错误不重试


# ════════════════════════════════════════════════════════════════
# 3. chat_stream()集成（此前零重试——本轮P0修复对象）
# ════════════════════════════════════════════════════════════════


async def _collect(engine, responses, monkeypatch, tools=None, messages=None):
    state = _install_stream_client(monkeypatch, responses)
    out = []
    if tools is None:
        gen = engine.chat_stream(messages or [{"role": "user", "content": "hi"}])
    else:
        gen = engine.chat_stream_with_tools(
            messages or [{"role": "user", "content": "hi"}], tools
        )
    async for item in gen:
        out.append(item)
    return out, state


class TestChatStreamRetry:
    def test_429_retry_then_success(self, monkeypatch):
        waits = _patch_sleep(monkeypatch)
        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([], status_code=429, body=b"rate limited",
                            headers={"retry-after-ms": "500"}),
            _FakeStreamResp([_delta_chunk("你好"), "[DONE]"]),
        ], monkeypatch))
        assert out == ["你好"]
        assert state["calls"] == 2
        assert waits == [0.5]  # 有头听头

    def test_401_fail_fast_single_call(self, monkeypatch):
        _patch_sleep(monkeypatch)
        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([], status_code=401, body=b"bad key"),
        ], monkeypatch))
        assert any(isinstance(x, str) and "[LLM错误:" in x for x in out)
        assert state["calls"] == 1

    def test_zero_output_readtimeout_retried(self, monkeypatch):
        _patch_sleep(monkeypatch)

        def _raise():
            raise httpx.ReadTimeout("timed out")

        out, state = asyncio.run(_collect(_engine(), [
            _raise,
            _FakeStreamResp([_delta_chunk("恢复后的回答"), "[DONE]"]),
        ], monkeypatch))
        assert out == ["恢复后的回答"]
        assert state["calls"] == 2

    def test_partial_output_readtimeout_not_retried(self, monkeypatch):
        # 双份回复防御钉：已流出内容后超时绝不重试（重试=同一回复发两遍）
        waits = _patch_sleep(monkeypatch)

        def _raise():
            raise httpx.ReadTimeout("timed out")

        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([_delta_chunk("前半段"), _raise,
                             _delta_chunk("never")]),
        ], monkeypatch))
        assert out[0] == "前半段"
        assert any("响应超时" in x for x in out if isinstance(x, str))
        assert state["calls"] == 1  # 不重试
        assert waits == []

    def test_exhaustion_visible_failure(self, monkeypatch):
        _patch_sleep(monkeypatch)
        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([], status_code=503, body=b"unavailable",
                            headers={"retry-after-ms": "1"}),
        ], monkeypatch))
        assert any(isinstance(x, str) and "[LLM错误:" in x for x in out)
        assert state["calls"] == MAX_ATTEMPTS


# ════════════════════════════════════════════════════════════════
# 4. chat_stream_with_tools()集成
# ════════════════════════════════════════════════════════════════

_TOOLS = [{"type": "function", "function": {
    "name": "read_file", "parameters": {"type": "object", "properties": {}}}}]


class TestWithToolsRetry:
    def test_504_retried_regression(self, monkeypatch):
        # 回归钉：5xx无条件重试（旧硬编码(400,429,500,502,503)漏504）
        _patch_sleep(monkeypatch)
        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([], status_code=504, body=b"gateway timeout",
                            headers={"retry-after-ms": "1"}),
            _FakeStreamResp([_delta_chunk("工具回答"), "[DONE]"]),
        ], monkeypatch, tools=_TOOLS))
        assert "工具回答" in out
        assert state["calls"] == 2

    def test_retry_after_header_consumed(self, monkeypatch):
        # 退避时长断言：Retry-After: 0.5s被真实消费（旧固定2**_retry=1s/2s）
        waits = _patch_sleep(monkeypatch)
        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([], status_code=429, body=b"rate limited",
                            headers={"retry-after": "0.5"}),
            _FakeStreamResp([_delta_chunk("ok"), "[DONE]"]),
        ], monkeypatch, tools=_TOOLS))
        assert "ok" in out
        assert state["calls"] == 2
        assert waits and abs(waits[0] - 0.5) < 1e-6

    def test_400_fail_fast_single_call(self, monkeypatch):
        # 行为修正钉：400不再重试（旧硬编码把400当可重试烧3次调用）
        _patch_sleep(monkeypatch)
        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([], status_code=400, body=b"bad request"),
        ], monkeypatch, tools=_TOOLS))
        assert any(isinstance(x, str) and "[LLM错误:" in x for x in out)
        assert state["calls"] == 1

    def test_partial_output_not_retried(self, monkeypatch):
        # tool_calls/文本已累积后传输错误不重试（防状态串attempt污染）
        _patch_sleep(monkeypatch)

        def _raise():
            raise httpx.ReadTimeout("timed out")

        out, state = asyncio.run(_collect(_engine(), [
            _FakeStreamResp([_delta_chunk("已经流出的部分"), _raise]),
        ], monkeypatch, tools=_TOOLS))
        assert any("响应超时" in x for x in out if isinstance(x, str))
        assert state["calls"] == 1


# ════════════════════════════════════════════════════════════════
# 5. 接线钉（防再断线回各自为政的旧策略）
# ════════════════════════════════════════════════════════════════


class TestWiring:
    def test_llm_engine_imports_llm_retry(self):
        src = _src("agent/llm_engine.py")
        assert "from utils.llm_retry import" in src
        for sym in ("MAX_ATTEMPTS", "backoff_ms", "is_retryable_status", "is_transient_error"):
            assert sym in src

    def test_three_paths_consume_policy(self):
        src = _src("agent/llm_engine.py")
        # chat()：HTTP重试环在chat方法体内（_http_retry是chat特有变量）
        assert "for _http_retry in range(MAX_ATTEMPTS)" in src
        # chat_stream()：递归重试计数+_retry_count递增
        assert "_retry_count=_retry_count + 1" in src
        # with_tools()：for _retry环消费同一MAX_ATTEMPTS
        assert "for _retry in range(MAX_ATTEMPTS)" in src

    def test_old_flawed_policy_gone(self):
        src = _src("agent/llm_engine.py")
        assert "(400, 429, 500, 502, 503)" not in src  # 旧硬编码状态集绝迹
        assert "2 ** _retry" not in src  # 旧固定指数退避绝迹
        assert "range(3)" not in src  # 旧硬编码次数绝迹

    def test_constants_match_kilocode(self):
        # kilocode retry.ts逐值：RETRY_INITIAL_DELAY=2000 / FACTOR=2 /
        # RETRY_MAX_DELAY_NO_HEADERS=30_000 / RETRY_MAX_DELAY=i32上限
        assert llm_retry.RETRY_INITIAL_DELAY_MS == 2000
        assert llm_retry.RETRY_BACKOFF_FACTOR == 2
        assert llm_retry.RETRY_MAX_DELAY_NO_HEADERS_MS == 30_000
        assert llm_retry.RETRY_MAX_DELAY_MS == 2_147_483_647
        assert llm_retry.MAX_ATTEMPTS == 3


# ════════════════════════════════════════════════════════════════
# 6. 重试决策合并（2026-09-29）：plan_status_retry/plan_error_retry单一真源
#    ——三路重试环（chat/chat_stream/chat_stream_with_tools）此前三处内联组合
#    「可重试∧次数→backoff→sleep」，现收口到utils.llm_retry纯函数单点，
#    行为面与旧内联逐字等价（下方等价性测试逐点对照）。
# ════════════════════════════════════════════════════════════════


class TestPlanRetryDecision:
    """plan_status_retry/plan_error_retry：三路重试决策合并单一真源的单元契约"""

    def test_status_retry_429_listens_header(self):
        # 可重试+未耗尽+有头→返回头里的等待秒数（等价旧 backoff_ms(a+1,头)/1000）
        assert plan_status_retry(429, 0, {"retry-after-ms": "500"}) == 0.5

    def test_status_retry_5xx_exponential_no_header(self):
        # 可重试+未耗尽+无头→指数退避2s×2^0=2s（等价旧 backoff_ms(a+1)/1000）
        assert plan_status_retry(500, 0, None) == 2.0
        assert plan_status_retry(503, 1, None) == 4.0

    def test_status_retry_exhausted_returns_none(self):
        # attempt=MAX_ATTEMPTS-1=2（第3次尝试）耗尽→None（调用方走失败契约）
        assert plan_status_retry(429, MAX_ATTEMPTS - 1, {"retry-after-ms": "1"}) is None

    def test_status_retry_non_retryable_returns_none(self):
        # 401/402/403/404请求/凭证级→None（fail-fast，等价旧 is_retryable_status短路）
        for s in (400, 401, 402, 403, 404):
            assert plan_status_retry(s, 0, None) is None

    def test_error_retry_transient_zero_output(self):
        # 瞬时传输错误+零输出+未耗尽→指数退避2s（等价旧 is_transient∧not output→backoff）
        assert plan_error_retry(httpx.ConnectError("x"), 0) == 2.0

    def test_error_retry_has_output_never_retries(self):
        # 已流出内容（has_output=True）→None 永不重试（双份回复防线，与旧内联一致）
        assert plan_error_retry(httpx.ConnectError("x"), 0, has_output=True) is None

    def test_error_retry_non_transient_returns_none(self):
        # 非瞬时错误（重试不会自愈）→None（等价旧 is_transient_error短路）
        assert plan_error_retry(ValueError("boom"), 0) is None

    def test_error_retry_exhausted_returns_none(self):
        assert plan_error_retry(httpx.ReadTimeout("t"), MAX_ATTEMPTS - 1) is None


class TestRetryDecisionMergeWiring:
    """接线钉：三路重试环的决策确已收口到plan_*单一真源（防再分叉回各自为政）"""

    def test_three_paths_consume_plan_status(self):
        src = _src("agent/llm_engine.py")
        # 三处状态失败决策（chat_stream/with_tools/chat）全部调用plan_status_retry
        assert src.count("plan_status_retry(") == 3

    def test_three_paths_consume_plan_error(self):
        src = _src("agent/llm_engine.py")
        # 三处传输失败决策（chat_stream/with_tools/chat）全部调用plan_error_retry
        assert src.count("plan_error_retry(") == 3

    def test_inline_decision_composition_gone(self):
        src = _src("agent/llm_engine.py")
        # 旧手搓决策绝迹：engine不再内联组合「∧is_transient_error(...)」或手算backoff
        assert "and is_transient_error(" not in src
        assert "backoff_ms(" not in src
