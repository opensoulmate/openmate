# -*- coding: utf-8 -*-
"""llm_engine流式usage发射专项（2026-09-21 01:15轮遗留#2下轮专项核对闭环）

live复现证据（token-plan xiaomi/mimo-v2.5-pro）：
- raw单遍SSE消费：provider每次都发trailing usage chunk（finish_reason之后）
- 真实LLMEngine.chat_stream_with_tools：usage发射率 0/5 ——旧实现break出
  aiter_bytes后二次"尾部排空"，httpx流不可二次迭代（StreamConsumed），异常被
  debug级吞掉 → OpenAI标准usage位置（finish之后）结构性丢失 → backfill零样本
  → 校准样本永远积累不起来。

旧测试mock（_FakeStreamResp）aiter_bytes可二次调用（list续弹），mock与真实
httpx行为分歧掩盖了死代码。本文件用严格mock（二次迭代即raise）钉住真实语义。

覆盖：
1. 严格流下finish_reason后trailing usage仍被发射（tools路径+plain路径）
2. usage先于tool_calls的顺序约束（消费方soulmate_agent见tool_calls即break）
3. stream_options.include_usage进入payload（OpenAI标准流式usage保障）
4. provider不支持stream_options时永久回退且不影响后续请求
5. 429/5xx重试真实生效（旧break退出循环且零yield=空响应死路径；旧client
   aclose后复用=重试必败隐性死路径）
6. chat_stream plain路径last_usage回填（此前全程不消费usage）
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from agent.llm_engine import LLMEngine


# ════════════════════════════════════════════════════════════════
# 严格mock：aiter_bytes二次调用即raise（模拟真实httpx StreamConsumed）
# ════════════════════════════════════════════════════════════════

class _StrictStreamResp:
    status_code = 200

    def __init__(self, payload_bytes: list[bytes]):
        self._payloads = list(payload_bytes)
        self._consumed = False

    async def aiter_bytes(self):
        if self._consumed:
            # 真实httpx语义：同一响应的流不可二次迭代
            raise httpx.StreamConsumed("Attempted to stream the response content more than once.")
        self._consumed = True
        while self._payloads:
            yield self._payloads.pop(0)

    async def aclose(self):
        pass


class _ErrResp:
    def __init__(self, status_code: int, body: bytes):
        self.status_code = status_code
        self._body = body

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        yield b""

    async def aclose(self):
        pass


def _sse(data: dict | str) -> bytes:
    if isinstance(data, str):
        return f"data: {data}\n\n".encode()
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def _install_client(monkeypatch, responses: list) -> list:
    """安装捕获payload的fake client；返回payload记录列表"""
    payloads: list[dict] = []
    state = {"calls": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def build_request(self, *a, **kw):
            # 快照而非引用：engine在stream_options回退时会pop同一dict，
            # 引用捕获会让"第一次请求带了什么"被事后篡改
            payloads.append(json.loads(json.dumps(kw.get("json", {}))))
            return object()

        async def send(self, req, stream=False):
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            r = responses[idx]
            return r() if callable(r) else r

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _FakeClient)
    return payloads


async def _collect_tools(engine, monkeypatch, responses):
    payloads = _install_client(monkeypatch, responses)
    out = []
    async for c in engine.chat_stream_with_tools(
        messages=[{"role": "user", "content": "hi"}], tools=None
    ):
        out.append(c)
    return out, payloads


async def _collect_plain(engine, monkeypatch, responses):
    payloads = _install_client(monkeypatch, responses)
    out = []
    async for c in engine.chat_stream(
        messages=[{"role": "user", "content": "hi"}]
    ):
        out.append(c)
    return out, payloads


def _engine():
    return LLMEngine(api_key="k", base_url="http://fake/v1", model="mimo-test")


def _run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════
# 1. 严格流：finish_reason后trailing usage不丢失（核心回归）
# ════════════════════════════════════════════════════════════════

class TestStrictStreamTrailingUsage:
    def test_trailing_usage_after_stop_finish(self, monkeypatch):
        """纯文本回复：finish_reason=stop后trailing usage chunk必须发射
        （真实httpx单遍语义——旧实现在此形态下usage必然丢失，live 0/5）"""
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "OK"}, "index": 0}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 4121, "completion_tokens": 8}}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        out = _run(_collect_tools(engine, monkeypatch, [resp]))
        chunks, _ = out
        usage_chunks = [c for c in chunks if isinstance(c, dict) and "usage" in c]
        assert usage_chunks, "严格流（不可二次迭代）下trailing usage必须被单遍消费捕获"
        assert usage_chunks[0]["usage"]["prompt_tokens"] == 4121
        assert engine.last_usage == {"prompt_tokens": 4121, "completion_tokens": 8}
        assert "OK" in chunks  # 文本路径不受影响

    def test_trailing_usage_after_tool_calls_finish_order(self, monkeypatch):
        """工具调用回复：usage仍必须先于tool_calls发射（soulmate消费方协议）"""
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "tc1", "type": "function",
                 "function": {"name": "read_file", "arguments": "{}"}}]},
                "finish_reason": "tool_calls", "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 5555, "completion_tokens": 3}}),
            _sse("[DONE]"),
        ])
        chunks, _ = _run(_collect_tools(_engine(), monkeypatch, [resp]))
        usage_idx = next(i for i, c in enumerate(chunks)
                         if isinstance(c, dict) and "usage" in c)
        tc_idx = next(i for i, c in enumerate(chunks)
                      if isinstance(c, dict) and "tool_calls" in c)
        assert usage_idx < tc_idx
        assert chunks[usage_idx]["usage"]["prompt_tokens"] == 5555

    def test_usage_chunk_in_same_network_read_as_finish(self, monkeypatch):
        """usage与finish_reason/DONE在同一network read（buffer残留形态）：
        tail模式继续解析缓冲区，不依赖后续网络数据"""
        # 所有SSE行一次性到达（单字节块），finish后buffer内还有usage+[DONE]
        one_blob = (b"data: {\"choices\": [{\"delta\": {\"content\": \"hi\"}, \"index\": 0}]}\n\n"
                    b"data: {\"choices\": [{\"delta\": {}, \"finish_reason\": \"stop\", \"index\": 0}]}\n\n"
                    b"data: {\"choices\": [], \"usage\": {\"prompt_tokens\": 777, \"completion_tokens\": 2}}\n\n"
                    b"data: [DONE]\n\n")
        resp = _StrictStreamResp([one_blob])
        engine = _engine()
        chunks, _ = _run(_collect_tools(engine, monkeypatch, [resp]))
        usage_chunks = [c for c in chunks if isinstance(c, dict) and "usage" in c]
        assert usage_chunks and usage_chunks[0]["usage"]["prompt_tokens"] == 777
        assert engine.last_usage["prompt_tokens"] == 777

    def test_stream_exhausted_without_done_still_emits(self, monkeypatch):
        """非标准provider：流耗尽无[DONE]，已捕获usage照样发射"""
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "x"}, "index": 0}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 321}}),
            # 无[DONE]，流直接结束
        ])
        chunks, _ = _run(_collect_tools(_engine(), monkeypatch, [resp]))
        assert any(isinstance(c, dict) and c.get("usage", {}).get("prompt_tokens") == 321
                   for c in chunks)

    def test_no_usage_provider_path_unchanged(self, monkeypatch):
        """无usage的provider：不发射usage dict，文本完整，last_usage=None"""
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "纯"}, "index": 0}]}),
            _sse({"choices": [{"delta": {"content": "文本"}, "index": 0}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        chunks, _ = _run(_collect_tools(engine, monkeypatch, [resp]))
        assert [c for c in chunks if isinstance(c, str)] == ["纯", "文本"]
        assert not any(isinstance(c, dict) and "usage" in c for c in chunks)
        assert engine.last_usage is None


# ════════════════════════════════════════════════════════════════
# 2. stream_options.include_usage（OpenAI标准流式usage保障）
# ════════════════════════════════════════════════════════════════

class TestStreamOptions:
    def test_stream_options_in_tools_payload(self, monkeypatch):
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "ok"}, "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 10}}),
            _sse("[DONE]"),
        ])
        _, payloads = _run(_collect_tools(_engine(), monkeypatch, [resp]))
        assert payloads[0].get("stream_options") == {"include_usage": True}

    def test_stream_options_in_plain_payload(self, monkeypatch):
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "ok"}, "index": 0}]}),
            _sse("[DONE]"),
        ])
        _, payloads = _run(_collect_plain(_engine(), monkeypatch, [resp]))
        assert payloads[0].get("stream_options") == {"include_usage": True}

    def test_unsupported_stream_options_fallback_tools_path(self, monkeypatch):
        """provider 400拒绝stream_options：回退后重试，第二次payload不再携带，
        回退是永久的（_stream_options_ok=False），usage链路不受影响"""
        err = _ErrResp(400, b'{"error": "Unknown parameter: \'stream_options\'."}')
        ok = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "ok"}, "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 55}}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        chunks, payloads = _run(_collect_tools(engine, monkeypatch, [err, ok]))
        assert engine._stream_options_ok is False
        assert payloads[0].get("stream_options") == {"include_usage": True}
        assert len(payloads) >= 2, "回退后必须真实重试"
        assert "stream_options" not in payloads[1]
        assert any(isinstance(c, dict) and c.get("usage", {}).get("prompt_tokens") == 55
                   for c in chunks)

    def test_unsupported_stream_options_fallback_plain_path(self, monkeypatch):
        err = _ErrResp(400, b'{"error": "stream_options not supported"}')
        ok = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "ok"}, "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 66}}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        chunks, payloads = _run(_collect_plain(engine, monkeypatch, [err, ok]))
        assert engine._stream_options_ok is False
        assert "stream_options" not in payloads[1]
        assert chunks == ["ok"]  # 递归重试的文本正常透传
        assert engine.last_usage == {"prompt_tokens": 66}


# ════════════════════════════════════════════════════════════════
# 3. 可重试HTTP状态：重试真实生效（旧break=空响应死路径）
# ════════════════════════════════════════════════════════════════

class TestRetryReal:
    def test_429_then_success_retries_with_fresh_stream(self, monkeypatch):
        """429后必须真重试并产出完整响应（旧实现break出循环零yield=静默空白；
        旧实现复用aclosed client重试必败）"""
        err = _ErrResp(429, b'{"error": "rate limited"}')
        ok = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "恢复"}, "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 88}}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        chunks, payloads = _run(_collect_tools(engine, monkeypatch, [err, ok]))
        assert len(payloads) >= 2, "429必须触发重试"
        assert any(isinstance(c, str) and "恢复" in c for c in chunks), \
            "重试成功后文本必须到达消费方（不是空响应）"
        assert any(isinstance(c, dict) and c.get("usage", {}).get("prompt_tokens") == 88
                   for c in chunks)

    def test_500_exhausts_attempts_then_visible_error(self, monkeypatch):
        """持续500：MAX_ATTEMPTS=5次尝试后报错信息可见（mem0：失败必须可见，禁止静默空白；
        10.1韧性治理后重试预算3→5，本钉随之重推导）"""
        err = lambda: _ErrResp(500, b'{"error": "boom"}')
        engine = _engine()
        chunks, payloads = _run(_collect_tools(engine, monkeypatch, [err()]))
        assert len(payloads) == 5, f"应尝试5次（10.1 MAX_ATTEMPTS=5），实际{len(payloads)}"
        assert any(isinstance(c, str) and "LLM错误" in c for c in chunks)

    def test_non_retryable_401_fails_fast(self, monkeypatch):
        err = _ErrResp(401, b'{"error": "bad key"}')
        engine = _engine()
        chunks, payloads = _run(_collect_tools(engine, monkeypatch, [err]))
        assert len(payloads) == 1, "401不可重试，必须fail-fast"
        assert any(isinstance(c, str) and "LLM错误" in c for c in chunks)


# ════════════════════════════════════════════════════════════════
# 4. chat_stream plain路径usage回填（此前完全不消费usage）
# ════════════════════════════════════════════════════════════════

class TestPlainPathUsage:
    def test_plain_stream_captures_trailing_usage(self, monkeypatch):
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "你好"}, "index": 0}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 4321, "completion_tokens": 8}}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        chunks, _ = _run(_collect_plain(engine, monkeypatch, [resp]))
        # 文本协议不破坏：plain路径消费方（engine.py）按str迭代
        assert chunks == ["你好"]
        assert engine.last_usage == {"prompt_tokens": 4321, "completion_tokens": 8}

    def test_plain_stream_usage_in_finish_chunk(self, monkeypatch):
        resp = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "答"}, "index": 0}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}],
                  "usage": {"prompt_tokens": 253, "completion_tokens": 12}}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        chunks, _ = _run(_collect_plain(engine, monkeypatch, [resp]))
        assert chunks == ["答"]
        assert engine.last_usage == {"prompt_tokens": 253, "completion_tokens": 12}

    def test_plain_stream_no_usage_sets_none(self, monkeypatch):
        """无usageprovider：last_usage置None（本轮调用没有usage，不能残留上轮旧值）"""
        resp1 = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "a"}, "index": 0}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 100}}),
            _sse("[DONE]"),
        ])
        resp2 = _StrictStreamResp([
            _sse({"choices": [{"delta": {"content": "b"}, "index": 0}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}),
            _sse("[DONE]"),
        ])
        engine = _engine()
        _run(_collect_plain(engine, monkeypatch, [resp1]))
        assert engine.last_usage == {"prompt_tokens": 100}
        _run(_collect_plain(engine, monkeypatch, [resp2]))
        assert engine.last_usage is None
