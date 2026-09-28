# -*- coding: utf-8 -*-
"""utils.llm_transport 传输核心专项（2026-09-29合并轮——多轮遗留"传输合并"销账）。

背景：utils.llm_gateway._call_with_protection与dna_evolution._call_llm曾各持一条
逐字重复的httpx传输（~70行重试环×2）+两套续写/omission防护循环（dna内联续写文案
与llm_safety漂移）。本轮收口为 utils.llm_transport 单一传输核心：
- post_chat_completion：单发层（llm_retry重试环+离线park+LLMCallError契约）
- complete_with_protection：防护层（Aider续写+Gemini omission+空输出契约+上限）

覆盖：
1. 单发层行为：Retry-After三格式退避/5xx耗尽可见失败/凭证级fail-fast/
   瞬时传输错误重试/park接线/非200带status的LLMCallError
2. 防护层行为：length续写（文案=single source build_continuation_messages）/
   omission重发（error_hint文案）/续写中断partial_content不丢/200空输出判失败/
   rounds轮数上限不空转/output_cap截断
3. 接线钉：POST+重试环全库只在llm_transport与llm_engine（流式）各一份；
   dna/gateway委托在场且零内联环；dna旧内联续写文案副本绝迹
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from utils import llm_transport
from utils.llm_errors import LLMCallError
from utils.llm_safety import build_continuation_messages

HERE = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


# ────────────────────────────── 测试替身 ──────────────────────────────

def _resp(status=200, text="", content=None, finish="stop", headers=None):
    class R:
        status_code = status

        def __init__(self):
            self.text = text
            self.headers = headers or {}
            if content is not None and not text:
                self.text = json.dumps({
                    "choices": [{"message": {"content": content}, "finish_reason": finish}]
                })

        def json(self):
            return json.loads(self.text)

    return R()


class _FakeClient:
    """按序返回响应/抛异常的假client（耗尽后抛错暴露意外调用），并捕获payload"""

    def __init__(self, responses):
        self.queue = list(responses)
        self.calls = 0
        self.payloads = []

    async def post(self, url, headers=None, json=None):
        self.calls += 1
        self.payloads.append({"url": url, "headers": headers, "json": json})
        if not self.queue:
            raise AssertionError("超出预期的LLM调用")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _nosleep(sleeps):
    async def _f(delay):
        sleeps.append(delay)
    return _f


def _parks(parks):
    async def _f(exc, cancel_event=None):
        parks.append(type(exc).__name__)
        return "not_offline"
    return _f


def _post(client, **kw):
    args = dict(
        base_url="https://example.invalid/v1",
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-test"},
        model="m-test",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=100,
        strand_id="t",
    )
    args.update(kw)
    return llm_transport.post_chat_completion(client, **args)


def _complete(client, **kw):
    args = dict(
        base_url="https://example.invalid/v1",
        headers={"Content-Type": "application/json"},
        model="m-test",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=100,
        rounds=1,
        strand_id="t",
    )
    args.update(kw)
    return llm_transport.complete_with_protection(client, **args)


# ══════════════════════ 1. 单发层 post_chat_completion ══════════════════════

class TestPostChatCompletion:
    def test_429_listens_retry_after_ms(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([
            _resp(429, text="rate limited", headers={"retry-after-ms": "300"}),
            _resp(200, content="ok"),
        ])
        data = run(_post(c))
        assert data["choices"][0]["message"]["content"] == "ok"
        assert c.calls == 2 and sleeps == [0.3]

    def test_5xx_exhausted_visible_failure(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([_resp(504, text="gateway timeout")] * 3)
        with pytest.raises(LLMCallError) as ei:
            run(_post(c))
        assert c.calls == 3 and sleeps == [2.0, 4.0]
        assert "504" in str(ei.value)

    def test_402_fail_fast_single_call(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([_resp(402, text="insufficient balance")])
        with pytest.raises(LLMCallError):
            run(_post(c))
        assert c.calls == 1 and sleeps == []

    def test_transient_transport_error_retried(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([httpx.ConnectError("refused"), _resp(200, content="ok")])
        assert run(_post(c))["choices"][0]["message"]["content"] == "ok"
        assert c.calls == 2 and sleeps == [2.0]

    def test_park_wired_on_transport_error(self, monkeypatch):
        parks = []
        monkeypatch.setattr(llm_transport.network_state, "maybe_park", _parks(parks))
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([httpx.ReadError("reset"), _resp(200, content="ok")])
        run(_post(c))
        assert parks == ["ReadError"]

    def test_non_200_error_carries_status(self, monkeypatch):
        c = _FakeClient([_resp(400, text="bad request")])
        with pytest.raises(LLMCallError) as ei:
            run(_post(c))
        assert "400" in str(ei.value)


# ══════════════════════ 2. 防护层 complete_with_protection ══════════════════════

class TestCompleteWithProtection:
    def test_length_continuation_joins_parts(self):
        c = _FakeClient([
            _resp(200, content="PART1-", finish="length"),
            _resp(200, content="PART2", finish="stop"),
        ])
        assert run(_complete(c, rounds=2)) == "PART1-PART2"
        # 续写请求携带canonical续写文案（单一真源llm_safety）
        cont = c.payloads[1]["json"]["messages"]
        assert cont[-1]["content"] == build_continuation_messages(
            [{"role": "user", "content": "hi"}], "PART1-"
        )[-1]["content"]

    def test_omission_reissues_with_error_hint(self):
        c = _FakeClient([
            _resp(200, content="code\n(rest of methods ...)", finish="stop"),
            _resp(200, content="full code", finish="stop"),
        ])
        assert run(_complete(c, rounds=2)) == "full code"
        hint_msg = c.payloads[1]["json"]["messages"][-1]["content"]
        assert "Your previous output had issues" in hint_msg

    def test_mid_continuation_failure_keeps_partial(self):
        c = _FakeClient([
            _resp(200, content="PART-", finish="length"),
            _resp(400, text="bad request"),
        ])
        with pytest.raises(LLMCallError) as ei:
            run(_complete(c, rounds=3))
        assert ei.value.partial_content == "PART-"

    def test_empty_200_is_failure(self):
        c = _FakeClient([_resp(200, content="")])
        with pytest.raises(LLMCallError) as ei:
            run(_complete(c))
        assert "空内容" in str(ei.value)

    def test_rounds_bound_no_infinite_loop(self):
        c = _FakeClient([
            _resp(200, content="x", finish="length"),
            _resp(200, content="y", finish="length"),
        ])
        out = run(_complete(c, rounds=2))
        assert out == "xy" and c.calls == 2  # rounds封顶，不空转

    def test_output_cap_truncates_explicit(self):
        c = _FakeClient([_resp(200, content="A" * 100, finish="stop")])
        assert run(_complete(c, output_cap=10)) == "A" * 10

    def test_non_llm_exception_wrapped_with_partial(self):
        c = _FakeClient([
            _resp(200, content="HALF", finish="length"),
            ValueError("json decode boom"),
        ])
        with pytest.raises(LLMCallError) as ei:
            run(_complete(c, rounds=3))
        assert "LLM调用异常" in str(ei.value)
        assert ei.value.partial_content == "HALF"

    def test_success_returns_full_content_not_empty(self):
        c = _FakeClient([_resp(200, content="done", finish="stop")])
        assert run(_complete(c)) == "done"


# ══════════════════════ 3. 接线钉（防再长出第二条传输） ══════════════════════

class TestWiringPins:
    def _code(self, rel):
        return "\n".join(
            l for l in (HERE / rel).read_text().splitlines()
            if not l.strip().startswith("#")
        )

    def test_single_post_retry_loop_per_transport_kind(self):
        """非流式POST+重试环全库只在llm_transport一份（llm_engine=流式传输除外）"""
        hits = []
        for p in (HERE / "utils").glob("*.py"):
            if "for _http_retry" in self._code(f"utils/{p.name}"):
                hits.append(f"utils/{p.name}")
        for name in ("dna_evolution.py",):
            if "for _http_retry" in self._code(name):
                hits.append(name)
        assert hits == ["utils/llm_transport.py"], f"发现第二条内联传输: {hits}"

    def test_callers_have_no_inline_post_loop(self):
        for rel in ("dna_evolution.py", "utils/llm_gateway.py"):
            code = self._code(rel)
            assert "chat/completions" not in code, f"{rel}不得再自拼chat/completions端点"
            assert "llm_transport.complete_with_protection(" in code

    def test_dna_inline_continuation_text_gone(self):
        """dna旧内联续写文案副本绝迹（曾与llm_safety漂移，现统一build_continuation_messages）"""
        code = self._code("dna_evolution.py")
        assert "Continue from where you left off" not in code
        assert "Output contains omission placeholder" not in code

    def test_transport_core_imports_single_sources(self):
        src = (HERE / "utils" / "llm_transport.py").read_text()
        for sym in ("from utils.llm_retry import", "from utils.llm_safety import",
                    "from utils import network_state", "from utils.llm_errors import"):
            assert sym in src, f"llm_transport必须消费单一真源 {sym}"

    def test_gateway_and_dna_still_reexport_contract(self):
        """兼容面不动：dna/gateway的LLMCallError再导出与pattern别名保持同一对象"""
        import dna_evolution
        import utils.llm_gateway as llm_gateway
        import utils.llm_safety as llm_safety
        assert dna_evolution.LLMCallError is LLMCallError
        assert llm_gateway.LLMCallError is LLMCallError
        assert llm_gateway._OMISSION_PATTERNS is llm_safety.OMISSION_PATTERNS
        assert dna_evolution.DNAStrand._OMISSION_PATTERNS is llm_safety.OMISSION_PATTERNS
