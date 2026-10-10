# -*- coding: utf-8 -*-
"""LLM三路会话面×模型降级有序链接线专项（2026-09-30 CowAgent chat_fallback）。

调研来源：SUMMARY.md §五 cortex缺口「模型降级有序链（fallback+限流立即切备胎）
| CowAgent chat fallback链 | P0」；62bde6c1轮已把链能力接进非流式核心
（utils/llm_transport），本轮接进llm_engine三路会话面（chat_stream/
chat_stream_with_tools/chat）——真实回复主路径此前429/5xx只能同端点干等。

覆盖（CowAgent语义逐条，utils.model_fallback.try_advance/fail + llm_engine三路）：
1. switch_now：429+有备胎立即切换、零等待；429没备胎干等（原行为回归）
2. 同link重试优先：5xx先退避重试，重试已放弃才换link；切换进来的link只给1发
3. 不可重试状态（401/402等）也换link，链上没备胎才fail-fast
4. 链耗尽报错列出**所有**试过的模型；单link失败=原契约逐字不变（无链零变化）
5. sticky：切换后续写轮停在应答link不弹回primary
6. has_output守卫：已流出/已累积内容永不切换（防双份回复/tool_calls状态污染）
7. 接线钉：三路×7个try_advance决策点+chain.fail记账+递归携带_chain
"""

import asyncio
import json

import httpx
import pytest

from agent.llm_engine import LLMEngine
from utils.model_fallback import FallbackChain, FallbackLink, load_fallback_chain

PRIMARY = "https://primary.invalid/v1"
BACKUP1 = "https://backup1.invalid/v1"
BACKUP2 = "https://backup2.invalid/v1"


def run(coro):
    return asyncio.run(coro)


# ────────────────────────────── 测试替身 ──────────────────────────────


class _FakeResp:
    """流式/非流式两用假响应：chunks支持中途抛异常（has_output守卫场景）"""

    def __init__(self, status=200, body=b"", chunks=None, json_data=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._chunks = list(chunks) if chunks is not None else ([body] if body else [])
        self._json = json_data
        self.text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        for c in self._chunks:
            if isinstance(c, BaseException):
                raise c
            yield c

    async def aclose(self):
        pass

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text or "{}")


def _sse_text(text, finish="stop", usage=None):
    ev = {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}
    if usage:
        ev["usage"] = usage
    return _FakeResp(chunks=[
        b"data: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n",
        b"data: [DONE]\n\n",
    ])


def _sse_tools(name="exec", args='{"cmd": "ls"}'):
    ev = {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": "call-1", "type": "function",
        "function": {"name": name, "arguments": args},
    }]}, "finish_reason": "tool_calls"}]}
    return _FakeResp(chunks=[
        b"data: " + json.dumps(ev).encode() + b"\n\n",
        b"data: [DONE]\n\n",
    ])


def _err(status, body=b"boom", headers=None):
    return _FakeResp(status=status, body=body, headers=headers)


def _json_ok(content="OK", finish="stop", usage=None):
    data = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    if usage:
        data["usage"] = usage
    return _FakeResp(json_data=data)


def _install(monkeypatch, responses):
    """安装fake client（记录每次请求的端点/鉴权/模型）；响应队列末位重复"""
    calls = []
    state = {"i": 0}

    class _C:
        def __init__(self, base_url="", headers=None, **kw):
            self.base_url = base_url
            self.headers = dict(headers or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aclose(self):
            pass

        def _next(self):
            idx = min(state["i"], len(responses) - 1)
            state["i"] += 1
            return responses[idx]

        def _record(self, payload):
            calls.append({
                "base_url": str(self.base_url),
                "headers": dict(self.headers),
                "model": (payload or {}).get("model"),
            })

        def build_request(self, method, url, **kw):
            return kw.get("json") or {}

        async def send(self, req, **kw):
            self._record(req)
            item = self._next()
            if isinstance(item, BaseException):
                raise item
            return item

        async def post(self, url, **kw):
            self._record(kw.get("json"))
            item = self._next()
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _C)
    return calls


def _nosleep(sleeps):
    async def _f(delay):
        sleeps.append(delay)
    return _f


async def _nopark(exc, cancel_event=None):
    return None


def _prep(monkeypatch, responses, chain=None):
    calls = _install(monkeypatch, responses)
    sleeps = []
    monkeypatch.setattr("agent.llm_engine.asyncio.sleep", _nosleep(sleeps))
    monkeypatch.setattr("agent.llm_engine.network_state.maybe_park", _nopark)
    if chain is not None:
        monkeypatch.setattr(
            "agent.llm_engine.load_fallback_chain", lambda **kw: chain
        )
    return calls, sleeps


def _engine():
    return LLMEngine(
        api_key="sk-p", base_url=PRIMARY, model="m-primary",
        system_prompt="sys",
    )


def _link(model="m-backup1", base_url=BACKUP1, provider="qwen", api_key="sk-b1"):
    return FallbackLink(
        provider=provider, model=model, base_url=base_url, api_key=api_key
    )


def _chain(*links):
    return FallbackChain(links=list(links), primary_label="m-primary")


async def _stream(engine, **kw):
    out = []
    async for x in engine.chat_stream(
        [{"role": "user", "content": "hi"}], **kw
    ):
        out.append(x)
    return out


async def _stream_tools(engine, tools=None, **kw):
    out = []
    async for x in engine.chat_stream_with_tools(
        [{"role": "user", "content": "hi"}], tools, **kw
    ):
        out.append(x)
    return out


# ══════════════════════ 1. 链切换决策/记账（try_advance/fail） ══════════════════════


class TestChainSwitchDecision:
    def test_switch_now_429_even_when_retry_planned(self):
        # CowAgent语义4：429+有备胎=立即切，不等Retry-After
        ch = _chain(_link())
        assert ch.try_advance("LLM HTTP 429: rl", retry_planned=True, status=429) is True
        assert ch.current.model == "m-backup1"
        assert ch.spent == ["m-primary: LLM HTTP 429: rl"]

    def test_no_switch_while_same_link_retry_planned(self):
        # 5xx瞬时故障先同link退避重试（llm_transport同语义），不急着换link
        ch = _chain(_link())
        assert ch.try_advance("LLM HTTP 500: oops", retry_planned=True, status=500) is False
        assert ch.current is None
        assert ch.spent == []

    def test_switch_when_retry_given_up(self):
        ch = _chain(_link())
        assert ch.try_advance("LLM HTTP 500: oops", retry_planned=False, status=500) is True
        assert ch.current.model == "m-backup1"

    def test_non_retryable_status_still_switches(self):
        # llm_transport"402不可重试也换link"同款：任何link失败都换
        ch = _chain(_link())
        assert ch.try_advance("LLM HTTP 402: no credit", retry_planned=False, status=402) is True

    def test_never_switch_with_output(self):
        # has_output守卫：已流出/已累积内容再重发=双份回复/状态污染
        ch = _chain(_link())
        assert ch.try_advance("x", retry_planned=False, status=429, has_output=True) is False
        assert ch.current is None

    def test_never_switch_on_empty_chain(self):
        # opt-in空链=行为不变
        ch = FallbackChain(links=[])
        assert ch.try_advance("x", retry_planned=False, status=429) is False
        assert ch.spent == []

    def test_wrap_around_budget_then_exhaust(self):
        ch = _chain(_link("m1"), _link("m2", BACKUP2))
        order = []
        for i in range(5):
            ok = ch.try_advance(f"e{i}", retry_planned=False, status=500)
            order.append(ok)
        assert order == [True, True, True, True, False]
        assert ch.current is not None and ch.current.model == "m2"
        assert len(ch.spent) == 4  # 4次切换各记一条；耗尽那次由fail()记

    def test_fail_single_returns_default_unchanged(self):
        # 单link失败=原错误契约逐字不变（无链行为零变化）
        ch = _chain(_link())
        assert ch.fail("LLM HTTP 500: x", "LLM API error 500: x") == "LLM API error 500: x"
        assert ch.spent == ["m-primary: LLM HTTP 500: x"]

    def test_fail_multi_lists_all_tried_models(self):
        # CowAgent语义3：列出所有试过的模型与各自错误
        ch = _chain(_link())
        ch.try_advance("LLM HTTP 500: a", retry_planned=False, status=500)
        msg = ch.fail("LLM HTTP 500: b", "default")
        assert "主模型与全部兜底模型均调用失败（2个模型全部试过）" in msg
        assert "m-primary: LLM HTTP 500: a" in msg
        assert "qwen/m-backup1: LLM HTTP 500: b" in msg

    def test_current_label_primary_then_link(self):
        ch = _chain(_link())
        assert ch.current_label() == "m-primary"
        ch.advance()
        assert ch.current_label() == "qwen/m-backup1"

    def test_load_chain_sets_primary_label(self):
        ch = load_fallback_chain(primary_model="m-primary", env_value="")
        assert ch.primary_label == "m-primary"
        assert not ch.links  # opt-in：未配置=空链


# ══════════════════════ 2. chat_stream（真实回复主路径） ══════════════════════


class TestChatStreamFallback:
    def test_429_switch_now_zero_wait_backup_answers(self, monkeypatch):
        # 429带Retry-After-MS:30000：有备胎立即切=零等待（若干等=30s）
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [
            _err(429, b"rate limited", headers={"retry-after-ms": "30000"}),
            _sse_text("HELLO"),
        ], chain=ch)
        out = run(_stream(_engine()))
        assert out == ["HELLO"]
        assert len(calls) == 2  # primary 1发 + 备胎1发
        assert calls[0]["base_url"] == PRIMARY
        assert calls[1]["base_url"] == BACKUP1
        assert calls[1]["model"] == "m-backup1"
        # 鉴权整体替换：备胎用自己凭据，primary的key不泄漏
        assert calls[0]["headers"].get("Authorization") == "Bearer sk-p"
        assert calls[1]["headers"].get("Authorization") == "Bearer sk-b1"
        assert sleeps == []  # switch_now实锤：零等待

    def test_5xx_retries_primary_then_switches(self, monkeypatch):
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [
            _err(500), _err(500), _err(500), _err(500), _err(500), _sse_text("OK"),
        ], chain=ch)
        out = run(_stream(_engine()))
        assert out == ["OK"]
        assert len(calls) == 6  # primary全预算5发（2+4+8+16s退避）→备胎1发
        assert sleeps == [2.0, 4.0, 8.0, 16.0]
        assert calls[5]["base_url"] == BACKUP1

    def test_no_chain_behavior_byte_identical(self, monkeypatch):
        # 无链（opt-in未配置）：MAX_ATTEMPTS=5发干等+原错误契约逐字不变
        calls, sleeps = _prep(monkeypatch, [_err(429, b"rl")], chain=FallbackChain(links=[]))
        out = run(_stream(_engine()))
        assert out == ["\n[LLM错误: LLM API error 429: rl]"]
        assert len(calls) == 5
        assert sleeps == [2.0, 4.0, 8.0, 16.0]
        assert all(c["base_url"] == PRIMARY for c in calls)

    def test_chain_exhausted_lists_all_tried_models(self, monkeypatch):
        # 全500：primary 5发+4个link各1发（环绕两遍）=9发，报错列全部
        ch = _chain(_link("m-b1"), _link("m-b2", BACKUP2))
        calls, sleeps = _prep(monkeypatch, [_err(500, b"oops")], chain=ch)
        out = run(_stream(_engine()))
        assert len(calls) == 9
        assert sleeps == [2.0, 4.0, 8.0, 16.0]  # 只有primary干等；切换link零等待
        msg = out[-1]
        assert "主模型与全部兜底模型均调用失败（5个模型全部试过）" in msg
        assert "m-primary" in msg and "qwen/m-b1" in msg and "qwen/m-b2" in msg

    def test_sticky_continuation_stays_on_backup(self, monkeypatch):
        # 429切备胎→截断续写轮继续停备胎（不弹回primary）
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [
            _err(429, b"rl", headers={"retry-after-ms": "30000"}),
            _sse_text("PART1-", finish="length"),
            _sse_text("PART2"),
        ], chain=ch)
        out = run(_stream(_engine()))
        assert "".join(out) == "PART1-PART2"
        assert len(calls) == 3
        assert calls[0]["base_url"] == PRIMARY
        assert calls[1]["base_url"] == BACKUP1
        assert calls[2]["base_url"] == BACKUP1  # sticky实锤
        assert sleeps == []

    def test_output_guard_no_switch_on_midstream_error(self, monkeypatch):
        # 中途传输错误但已流出内容：不切换不重试（防双份回复），可见失败
        ch = _chain(_link())
        mid = _FakeResp(chunks=[
            b'data: {"choices": [{"delta": {"content": "HALF"}, "finish_reason": null}]}\n\n',
            httpx.ReadError("conn lost"),
        ])
        calls, sleeps = _prep(monkeypatch, [mid, _sse_text("DUPLICATE")], chain=ch)
        out = run(_stream(_engine()))
        assert out == ["HALF", "\n[LLM错误: conn lost]"]
        assert len(calls) == 1  # 没有第二次请求=没双份回复
        assert sleeps == []


# ══════════════════════ 3. chat_stream_with_tools（工具调用面） ══════════════════════


class TestWithToolsFallback:
    def test_429_switch_tool_calls_from_backup(self, monkeypatch):
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [
            _err(429, b"rl", headers={"retry-after-ms": "30000"}),
            _sse_tools("exec", '{"cmd": "ls"}'),
        ], chain=ch)
        out = run(_stream_tools(_engine(), tools=[{"type": "function"}]))
        tc = [x for x in out if isinstance(x, dict) and "tool_calls" in x]
        assert len(tc) == 1
        fn = tc[0]["tool_calls"][0]["function"]
        assert fn["name"] == "exec" and fn["arguments"] == '{"cmd": "ls"}'
        assert len(calls) == 2 and calls[1]["base_url"] == BACKUP1
        assert sleeps == []

    def test_non_retryable_switches_then_chain_fail(self, monkeypatch):
        # 401不可重试也换link；链走完（含环绕）报错列全部
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [_err(401, b"bad key")], chain=ch)
        out = run(_stream_tools(_engine(), tools=[{"type": "function"}]))
        assert len(calls) == 3  # primary 1发 + 备胎1发 + 环绕1发（各link只给1发）
        assert sleeps == []
        msg = out[-1]
        assert "主模型与全部兜底模型均调用失败（3个模型全部试过）" in msg
        assert "bad key" in msg

    def test_switched_link_single_shot_no_backoff(self, monkeypatch):
        # 切换进来的link只给1发（CowAgent link_retries=0）：不退避干等
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [_err(500, b"oops")], chain=ch)
        out = run(_stream_tools(_engine(), tools=[{"type": "function"}]))
        assert len(calls) == 7  # primary 5发 + 备胎1发 + 环绕1发
        assert sleeps == [2.0, 4.0, 8.0, 16.0]  # 只有primary的同link重试在等
        assert "主模型与全部兜底模型均调用失败" in out[-1]

    def test_no_chain_429_identical_contract(self, monkeypatch):
        calls, sleeps = _prep(monkeypatch, [_err(429, b"rl")], chain=FallbackChain(links=[]))
        out = run(_stream_tools(_engine(), tools=[{"type": "function"}]))
        assert out == ["\n[LLM错误: LLM API error 429: rl]"]
        assert len(calls) == 5 and sleeps == [2.0, 4.0, 8.0, 16.0]


# ══════════════════════ 4. chat()（非流式judge/计划/摘要面） ══════════════════════


class TestChatFallback:
    def test_429_switch_now_usage_backfill(self, monkeypatch):
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [
            _err(429, b"rl", headers={"retry-after-ms": "30000"}),
            _json_ok("OK", usage={"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}),
        ], chain=ch)
        eng = _engine()
        assert run(eng.chat([{"role": "user", "content": "hi"}])) == "OK"
        assert len(calls) == 2 and sleeps == []
        assert calls[1]["base_url"] == BACKUP1
        assert calls[1]["model"] == "m-backup1"  # _apply_link改写payload模型
        assert calls[1]["headers"].get("Authorization") == "Bearer sk-b1"
        assert eng.last_usage["total_tokens"] == 10  # 备胎usage照样回填

    def test_no_chain_5xx_identical_contract(self, monkeypatch):
        calls, sleeps = _prep(monkeypatch, [_err(500, b"boom")], chain=FallbackChain(links=[]))
        out = run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "[LLM错误: LLM API error 500: boom]"
        assert len(calls) == 5 and sleeps == [2.0, 4.0, 8.0, 16.0]

    def test_chain_exhausted_lists_all(self, monkeypatch):
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [_err(500, b"oops")], chain=ch)
        out = run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert len(calls) == 7  # primary 5发 + 备胎1发 + 环绕1发
        assert "主模型与全部兜底模型均调用失败（3个模型全部试过）" in out

    def test_sticky_continuation_rounds(self, monkeypatch):
        # 切备胎后续写轮停在备胎（payload模型/端点都不弹回primary）
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [
            _err(429, b"rl", headers={"retry-after-ms": "30000"}),
            _json_ok("PART1", finish="length"),
            _json_ok("PART2"),
        ], chain=ch)
        out = run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "PART1PART2"
        assert [c["base_url"] for c in calls] == [PRIMARY, BACKUP1, BACKUP1]
        assert [c["model"] for c in calls][2] == "m-backup1"
        assert sleeps == []

    def test_transport_error_switches_after_give_up(self, monkeypatch):
        # 瞬时传输错误重试耗尽才换link；备胎应答
        ch = _chain(_link())
        calls, sleeps = _prep(monkeypatch, [
            httpx.ConnectError("refused"), httpx.ConnectError("refused"),
            httpx.ConnectError("refused"), httpx.ConnectError("refused"),
            httpx.ConnectError("refused"), _json_ok("OK"),
        ], chain=ch)
        out = run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "OK"
        assert len(calls) == 6 and calls[5]["base_url"] == BACKUP1
        assert sleeps == [2.0, 4.0, 8.0, 16.0]


# ══════════════════════ 5. 接线钉 ══════════════════════


class TestEngineFallbackWiring:
    def _src(self):
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent / "agent" / "llm_engine.py").read_text()

    def test_seven_switch_decision_points(self):
        src = self._src()
        # 三路×7个失败决策点全部过chain.try_advance单点（chat_stream 2、
        # with_tools 3、chat 2）
        assert src.count("chain.try_advance(") == 7
        assert src.count("chain.fail(") == 7

    def test_chain_threaded_through_all_recursions(self):
        src = self._src()
        # 10处递归/循环携带_chain（chat_stream 6：stream_options/状态切换/状态重试/
        # 续写/传输切换/传输重试；with_tools 4：不可重试切换/状态切换/续写/传输切换）
        assert src.count("_chain=chain") == 10

    def test_switched_links_get_single_shot(self):
        src = self._src()
        # 5处切换递归传_retry_count=MAX_ATTEMPTS-1=预算见底=1发（CowAgent
        # link_retries=0）：chat_stream 2 + with_tools 3
        assert src.count("_retry_count=MAX_ATTEMPTS - 1") == 5

    def test_retry_decision_single_source_intact(self):
        src = self._src()
        # plan_*单一真源消费点仍是三处（既有接线钉不破）
        assert src.count("plan_status_retry(") == 3
        assert src.count("plan_error_retry(") == 3
        assert "for _http_retry in range(MAX_ATTEMPTS)" in src
        assert "for _retry in range(MAX_ATTEMPTS)" in src

    def test_old_catch_retry_dead_branch_gone(self):
        code = "\n".join(
            l for l in self._src().splitlines() if not l.strip().startswith("#")
        )
        assert 'if "LLM API error" in str(e) and _retry' not in code

    def test_chain_construction_at_all_entries(self):
        src = self._src()
        assert src.count("load_fallback_chain(primary_model=resolved_model)") == 3

    def test_apply_link_replaces_auth_wholesale(self):
        # 鉴权整体替换（防primary key泄漏给备胎）是代码级契约
        src = self._src()
        assert "client.headers = link.headers()" in src
        assert "client.base_url = link.base_url" in src
