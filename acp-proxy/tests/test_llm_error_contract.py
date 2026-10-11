# -*- coding: utf-8 -*-
"""LLM传输错误契约统一 + 旁路传输并入重试/离线park单一真源（2026-09-29收口）。

背景（多轮dev-report遗留项"先统一错误契约再合并传输"）：接线前LLM传输层三种
失败形态并存——dna_evolution抛LLMCallError、llm_engine返回"[LLM错误: …]"、
utils.llm_gateway**静默返空串/半截**（mem0 §1.1反模式）；且dna的_retry_delay
副本（只认retry-after秒数、传输错误零重试）与llm_gateway零HTTP重试都没消费
utils.llm_retry策略单一真源，也没有离线park。

本文件钉住四件事：
① 错误契约单一真源：utils.llm_errors.LLMCallError，dna/gateway同一对象，
   失败=raise（partial_content=半截），"空串=失败"第三态废除；
② dna/gateway重试消费utils.llm_retry：Retry-After三格式/5xx无条件/瞬时传输
   重试/请求凭证级fail-fast，旧_retry_delay副本绝迹；
③ 传输错误路径接utils.network_state.maybe_park（断网park等恢复续跑）；
④ 会话面契约不动：agent.llm_engine 仍以"[LLM错误: …]"可见字符串呈现失败。

（2026-09-29合并轮补记：②④的"传输本体"已随两并行传输合并移入
utils/llm_transport 单一传输核心——dna/gateway改为委托complete_with_protection，
行为断言①②③全部原样通过=合并语义零变化；接线钉④同步改为钉新拓扑。）
"""

import asyncio
import inspect
from pathlib import Path

import httpx
import pytest

import dna_evolution
import utils.llm_gateway as llm_gateway
from dna_evolution import DNAStrand, LLMCallError, StrandRole
from utils.llm_errors import LLMCallError as LLMCallErrorCanonical
from utils.llm_retry import backoff_ms

HERE = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


# ────────────────────────────── 测试替身 ──────────────────────────────


def _resp(status=200, text="", content=None, finish="stop", headers=None):
    import json as _json

    class R:
        status_code = status

        def __init__(self):
            self.text = text
            self.headers = headers or {}
            if content is not None and not text:
                self.text = _json.dumps({
                    "choices": [{"message": {"content": content}, "finish_reason": finish}]
                })

        def json(self):
            return _json.loads(self.text)

    return R()


def _fake_client_factory(responses):
    """按序返回响应/抛异常的假httpx.AsyncClient工厂（耗尽后抛错暴露意外调用）"""
    queue = list(responses)
    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            calls["n"] += 1
            if not queue:
                raise AssertionError("超出预期的LLM调用")
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    _FakeClient.calls = calls
    return _FakeClient


def _nosleep_factory(sleeps):
    async def _nosleep(delay):
        sleeps.append(delay)

    return _nosleep


def _strand(tmp_path):
    (tmp_path / "acp-proxy" / "data").mkdir(parents=True, exist_ok=True)
    return DNAStrand(
        strand_id="strand_contract",
        role=StrandRole.PRIMARY,
        repo_root=str(tmp_path),
        llm_base_url="https://example.invalid/v1",
        llm_api_key="sk-test",
        llm_model="m-test",
    )


def _gateway_config():
    return llm_gateway.ModelConfig(
        name="t-contract", base_url="https://example.invalid/v1",
        api_key="sk-test", model="m-test",
    )


# ══════════════════════ ① 错误契约单一真源 ══════════════════════


class TestErrorContractSingleSource:
    def test_canonical_class_shape(self):
        assert issubclass(LLMCallErrorCanonical, RuntimeError)
        assert LLMCallErrorCanonical("x").partial_content == ""

    def test_dna_reexports_same_object(self):
        """dna_evolution.LLMCallError 是兼容再导出，必须=同一类对象"""
        assert dna_evolution.LLMCallError is LLMCallErrorCanonical
        assert LLMCallError is LLMCallErrorCanonical

    def test_gateway_uses_same_object(self):
        assert llm_gateway.LLMCallError is LLMCallErrorCanonical

    def test_dna_no_local_class_copy(self):
        src = (HERE / "dna_evolution.py").read_text()
        assert "class LLMCallError" not in src, "dna_evolution不得再定义本地LLMCallError副本"

    def test_gateway_failure_is_exception_not_empty_string(self):
        """gateway静默返空串第三态废除：配置缺失=raise，绝不return ''"""
        cfg_bad = llm_gateway.ModelConfig(
            name="t-empty", base_url="https://example.invalid/v1",
            api_key="", model="m-test",  # 空key fail-closed
        )
        llm_gateway.register_model(cfg_bad)
        with pytest.raises(LLMCallError):
            run(llm_gateway.llm_call([{"role": "user", "content": "hi"}], model="t-empty"))

    def test_session_surface_contract_untouched(self):
        """会话面（llm_engine）失败仍是"[LLM错误: …]"可见字符串契约，本轮不动"""
        src = (HERE / "agent" / "llm_engine.py").read_text()
        assert "[LLM错误" in src


# ══════════════════════ ② dna重试消费llm_retry ══════════════════════


class TestDnaRetryPolicyWiring:
    def test_429_listens_retry_after_ms_header(self, tmp_path, monkeypatch):
        """Retry-After三格式之一：retry-after-ms毫秒头→退避0.3s（非盲目2s）"""
        sleeps = []
        monkeypatch.setattr(dna_evolution.asyncio, "sleep", _nosleep_factory(sleeps))
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", _fake_client_factory([
            _resp(429, text="rate limited", headers={"retry-after-ms": "300"}),
            _resp(200, content="恢复后的回答"),
        ]))
        s = _strand(tmp_path)
        assert run(s._call_llm("hi")) == "恢复后的回答"
        assert sleeps == [0.3]

    def test_500_no_header_exponential_backoff(self, tmp_path, monkeypatch):
        sleeps = []
        monkeypatch.setattr(dna_evolution.asyncio, "sleep", _nosleep_factory(sleeps))
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", _fake_client_factory([
            _resp(500, text="boom"),
            _resp(200, content="ok"),
        ]))
        s = _strand(tmp_path)
        assert run(s._call_llm("hi")) == "ok"
        assert sleeps == [2.0]  # 无头→2s×2^n（kilocode语义）

    def test_5xx_exhausted_visible_failure(self, tmp_path, monkeypatch):
        sleeps = []
        monkeypatch.setattr(dna_evolution.asyncio, "sleep", _nosleep_factory(sleeps))
        fc = _fake_client_factory([_resp(504, text="gateway timeout")] * 5)
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", fc)
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError) as ei:
            run(s._call_llm("hi"))
        assert fc.calls["n"] == 5
        assert sleeps == [2.0, 4.0, 8.0, 16.0]
        assert "504" in str(ei.value)

    def test_402_fail_fast_single_call(self, tmp_path, monkeypatch):
        """凭证/余额级错误重试无益：fail-fast只1发"""
        sleeps = []
        monkeypatch.setattr(dna_evolution.asyncio, "sleep", _nosleep_factory(sleeps))
        fc = _fake_client_factory([_resp(402, text="insufficient balance")])
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", fc)
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError):
            run(s._call_llm("hi"))
        assert fc.calls["n"] == 1
        assert sleeps == []

    def test_transient_transport_error_retried_then_success(self, tmp_path, monkeypatch):
        """接线前：瞬时ConnectError一次就判失败——现在重试自愈"""
        sleeps = []
        monkeypatch.setattr(dna_evolution.asyncio, "sleep", _nosleep_factory(sleeps))
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", _fake_client_factory([
            httpx.ConnectError("connection refused"),
            _resp(200, content="重试后成功"),
        ]))
        s = _strand(tmp_path)
        assert run(s._call_llm("hi")) == "重试后成功"
        assert sleeps == [2.0]

    def test_non_transient_exception_not_retried(self, tmp_path, monkeypatch):
        sleeps = []
        monkeypatch.setattr(dna_evolution.asyncio, "sleep", _nosleep_factory(sleeps))
        fc = _fake_client_factory([ValueError("json decode boom")])
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", fc)
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError):
            run(s._call_llm("hi"))
        assert fc.calls["n"] == 1
        assert sleeps == []

    def test_transport_error_parks_on_disconnect(self, tmp_path, monkeypatch):
        """断网类传输错误→maybe_park接线在场（语义不变只加等待）"""
        parks = []

        async def _fake_park(exc, cancel_event=None):
            parks.append(type(exc).__name__)
            return "not_offline"

        monkeypatch.setattr(dna_evolution.network_state, "maybe_park", _fake_park)
        sleeps = []
        monkeypatch.setattr(dna_evolution.asyncio, "sleep", _nosleep_factory(sleeps))
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", _fake_client_factory([
            httpx.ConnectError("name resolution failed"),
            _resp(200, content="park后恢复"),
        ]))
        s = _strand(tmp_path)
        assert run(s._call_llm("hi")) == "park后恢复"
        assert parks == ["ConnectError"]


# ══════════════════════ ③ gateway重试+失败可见 ══════════════════════


class TestGatewayRetryAndContract:
    def test_429_listens_retry_after_header(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_gateway.asyncio, "sleep", _nosleep_factory(sleeps))
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _fake_client_factory([
            _resp(429, text="rate limited", headers={"retry-after-ms": "500"}),
            _resp(200, content="hello"),
        ]))
        out = run(llm_gateway._call_with_protection(
            config=_gateway_config(), messages=[{"role": "user", "content": "hi"}],
            temperature=0.7, max_tokens=100, max_continuation=0, strand_id="t",
        ))
        assert out == "hello"
        assert sleeps == [0.5]

    def test_401_fail_fast_raises_visible(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_gateway.asyncio, "sleep", _nosleep_factory(sleeps))
        fc = _fake_client_factory([_resp(401, text="invalid api key")])
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", fc)
        with pytest.raises(LLMCallError) as ei:
            run(llm_gateway._call_with_protection(
                config=_gateway_config(), messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=100, max_continuation=0, strand_id="t",
            ))
        assert fc.calls["n"] == 1
        assert "401" in str(ei.value)

    def test_mid_continuation_failure_keeps_partial(self, monkeypatch):
        """半截输出不丢（partial_content），但失败仍判失败（与dna同契约）"""
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _fake_client_factory([
            _resp(200, content="PART-", finish="length"),
            _resp(400, text="bad request"),
        ]))
        with pytest.raises(LLMCallError) as ei:
            run(llm_gateway._call_with_protection(
                config=_gateway_config(), messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=100, max_continuation=3, strand_id="t",
            ))
        assert ei.value.partial_content == "PART-"

    def test_empty_200_is_failure(self, monkeypatch):
        """"空串=失败"第三态废除：200空输出=LLMCallError，不许伪装成功"""
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _fake_client_factory([
            _resp(200, content=""),
        ]))
        with pytest.raises(LLMCallError) as ei:
            run(llm_gateway._call_with_protection(
                config=_gateway_config(), messages=[{"role": "user", "content": "hi"}],
                temperature=0.7, max_tokens=100, max_continuation=0, strand_id="t",
            ))
        assert "空内容" in str(ei.value)

    def test_transient_transport_error_retried_and_parked(self, monkeypatch):
        parks = []

        async def _fake_park(exc, cancel_event=None):
            parks.append(type(exc).__name__)
            return "not_offline"

        monkeypatch.setattr(llm_gateway.network_state, "maybe_park", _fake_park)
        sleeps = []
        monkeypatch.setattr(llm_gateway.asyncio, "sleep", _nosleep_factory(sleeps))
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _fake_client_factory([
            httpx.ReadError("connection reset"),
            _resp(200, content="recovered"),
        ]))
        out = run(llm_gateway._call_with_protection(
            config=_gateway_config(), messages=[{"role": "user", "content": "hi"}],
            temperature=0.7, max_tokens=100, max_continuation=0, strand_id="t",
        ))
        assert out == "recovered"
        assert parks == ["ReadError"]
        assert sleeps == [2.0]


# ══════════════════════ ④ 接线钉（防再断线回死路径） ══════════════════════


class TestWiringPins:
    def _src(self, rel):
        return (HERE / rel).read_text()

    def _code(self, rel):
        """去掉整行注释后的源码（防接线钉误命中'旧副本已删除'类留痕注释）"""
        return "\n".join(
            l for l in self._src(rel).splitlines() if not l.strip().startswith("#")
        )

    def test_transport_core_consumes_policy_symbols(self):
        """策略消费点已随2026-09-29传输合并移入 utils/llm_transport 单一传输核心"""
        src = self._src("utils/llm_transport.py")
        for sym in ("backoff_ms(", "is_retryable_status(", "is_transient_error(",
                    "maybe_park(", "MAX_ATTEMPTS"):
            assert sym in src, f"llm_transport缺少策略消费点 {sym}"

    def test_callers_delegate_to_transport_core(self):
        """dna/gateway必须委托complete_with_protection，不得再各持一条并行传输"""
        for rel in ("dna_evolution.py", "utils/llm_gateway.py"):
            src = self._src(rel)
            assert "llm_transport.complete_with_protection(" in src, \
                f"{rel}必须委托传输核心"
            code = self._code(rel)
            assert "for _http_retry" not in code, \
                f"{rel}不得再内联HTTP重试环（合并前的两条并行传输已收口）"

    def test_old_retry_copy_gone(self):
        """旧_retry_delay副本绝迹（曾只认retry-after秒数一种格式）"""
        src = self._code("dna_evolution.py")
        assert "def _retry_delay" not in src
        assert "self._RETRY_MAX" not in src
        assert "_RETRY_BASE_DELAY" not in src

    def test_policy_single_source_imports(self):
        """重试策略消费面：llm_engine（流式传输）与llm_transport（非流式核心）直连
        utils.llm_retry；dna/gateway经llm_transport委托消费——三面不再有第二份策略实现"""
        assert "from utils.llm_retry import" in self._src("agent/llm_engine.py")
        assert "from utils.llm_retry import" in self._src("utils/llm_transport.py")
        for rel in ("dna_evolution.py", "utils/llm_gateway.py"):
            assert "from utils import llm_transport" in self._src(rel), \
                f"{rel}必须import传输核心"

    def test_no_silent_failure_return(self):
        """"失败return full_content"静默契约绝迹（llm_safety.safe_llm_call旧反模式已删）：
        成功return全库只在传输核心与dna的成功出口各一处"""
        assert self._code("utils/llm_transport.py").count("return full_content") == 1
        assert self._code("dna_evolution.py").count("return full_content") == 1
        assert "return full_content" not in self._code("utils/llm_safety.py"), \
            "llm_safety残留静默返回半截旧契约（safe_llm_call）"
        assert "return full_content" not in self._code("utils/llm_gateway.py")
        assert "async def safe_llm_call" not in self._code("utils/llm_safety.py")

    def test_canonical_module_defines_exactly_one_class(self):
        src = self._src("utils/llm_errors.py")
        assert src.count("class LLMCallError") == 1
        assert "partial_content" in src

    def test_park_call_shape_matches_llm_engine(self):
        """park调用形态与llm_engine同款（except 传输/解码异常分支内）"""
        # 锚定真实 handler 语句——裸类名会被注释文字误命中（13.1 流式注释曾抢锚致
        # 假红）；14.5 catch 面扩到 DecodingError（瞬时请求族异常进同一重试环），
        # 钉随规格更新
        _ANCHOR = "except (httpx.TransportError, httpx.DecodingError) as e:"
        for rel in ("utils/llm_transport.py", "agent/llm_engine.py"):
            src = self._src(rel)
            assert _ANCHOR in src
            idx = src.find(_ANCHOR)
            assert "maybe_park(" in src[idx:idx + 400], f"{rel} park必须在传输异常分支内"
