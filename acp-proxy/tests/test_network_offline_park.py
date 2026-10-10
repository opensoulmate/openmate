# -*- coding: utf-8 -*-
"""网络离线三态专项测试（kilocode SessionNetwork移植——网络恢复专项）。

调研来源：kilocode source session/network.ts（三态blocked/resumed/aborted +
3URL竞速探测 + 3s轮询 + 10s宽限自动续跑 + MCP重连）；SUMMARY §五 cortex
「LLM重试策略（Retry-After三格式+5xx重试+离线三态）」第三件套。

覆盖：
1. 错误链分类单元（kilocode chain()/code()/disconnected()/message()移植）：
   errno/gaierror/消息模式/异常链/ExceptionGroup/TimeoutError
2. 探测竞速（check()/probe()）：status<500语义/任一通即在线/全败才离线/异常=离线
3. maybe_park三态：非离线不park/端点级故障不park/离线→恢复=resumed（含恢复回调）/
   离线不恢复=aborted/取消/宽限期取消
4. LLMEngine集成（fake传输）：断网park后恢复续跑/aborted回落原"[LLM错误: …]"
   可见失败契约（不吞）/重试记账不变（"语义不变只加等待"）
5. MCP重连（kilocode reply()）：只重连error态/服务不可达绝不抛出/部分失败记账
6. 接线钉：llm_engine三路消费maybe_park + app.py端点 + kilocode常量逐值一致
"""
import asyncio
import errno
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json

import httpx
import pytest

from agent.llm_engine import LLMEngine
from utils import network_state
from utils.network_state import (
    ABORTED,
    NOT_OFFLINE,
    RESUMED,
    chain,
    disconnected,
    error_code,
    network_message,
)

HERE = Path(__file__).parent.parent


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


# ── fixtures：状态清零 + 快速park常量 + 可控probe ────────────────────────


@pytest.fixture
def clean(monkeypatch, tmp_path):
    """模块级状态清零（_waits/_stats/_restore_callbacks）防测试互相污染；
    状态目录重定向tmp（park快照/拒绝标志不落data/network_park真实目录）"""
    monkeypatch.setenv("NETWORK_PARK_STATE_DIR", str(tmp_path))
    network_state._waits.clear()
    for k in network_state._stats:
        network_state._stats[k] = 0
    saved = list(network_state._restore_callbacks)
    network_state._restore_callbacks.clear()
    yield
    network_state._waits.clear()
    for k in network_state._stats:
        network_state._stats[k] = 0
    network_state._restore_callbacks[:] = saved


def _fast_parks(monkeypatch, hook=None):
    """park常量调小+_sleep seam假实现（记录时长，可挂hook）——不真睡"""
    monkeypatch.setattr(network_state, "POLL_S", 0.01)
    monkeypatch.setattr(network_state, "RESUME_GRACE_S", 0.02)
    monkeypatch.setattr(network_state, "MAX_WAIT_S", 0.2)
    sleeps: list[float] = []

    async def _fake_sleep(delay):
        sleeps.append(float(delay))
        if hook:
            hook(delay)

    monkeypatch.setattr(network_state, "_sleep", _fake_sleep)
    return sleeps


def _stub_probe(monkeypatch, results):
    """probe_online顺序桩：按序返回，用完重复最后一个；记录调用次数"""
    seq = list(results)
    calls = {"n": 0}

    async def _fake_probe():
        idx = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return bool(seq[idx])

    monkeypatch.setattr(network_state, "probe_online", _fake_probe)
    return calls


# ════════════════════════════════════════════════════════════════
# 1. 错误链分类（kilocode chain()/code()/disconnected()/message()）
# ════════════════════════════════════════════════════════════════


class TestDisconnectClassification:
    def test_oserror_errno_refused(self):
        err = httpx.ConnectError("boom")
        err.__cause__ = OSError(errno.ECONNREFUSED, "Connection refused")
        assert error_code(err) == "ECONNREFUSED"
        assert disconnected(err)
        assert network_message(err) == "Connection refused"

    def test_gaierror_dns(self):
        err = OSError("wrap")
        err.__cause__ = socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
        assert error_code(err) == "EAI_AGAIN"
        assert disconnected(err)
        assert network_message(err) == "DNS lookup failed"

    def test_explicit_code_attr(self):
        # kilocode code()：第一个code字符串字段
        class E(Exception):
            code = "ECONNRESET"

        assert error_code(E("x")) == "ECONNRESET"
        assert disconnected(E("x"))
        assert network_message(E("x")) == "Connection reset by server"

    def test_message_patterns_case_insensitive(self):
        assert disconnected(RuntimeError("Socket Hang Up"))
        assert disconnected(RuntimeError("Failed to Fetch"))
        assert disconnected(RuntimeError("network is unreachable"))
        assert disconnected(RuntimeError("All connection attempts failed"))
        assert disconnected(RuntimeError("name resolution failed"))

    def test_timeout_error_is_disconnect(self):
        # kilocode：AbortSignal.timeout()的TimeoutError同判离线
        assert disconnected(TimeoutError("Request timed out"))
        assert disconnected(asyncio.TimeoutError())

    def test_non_disconnect(self):
        assert not disconnected(ValueError("bad json"))
        assert not disconnected(RuntimeError("LLM API error 400: bad request"))
        # kilocode msgs()不含裸"timed out"（ReadTimeout是慢不是断）
        assert not disconnected(httpx.ReadTimeout("timed out"))

    def test_chain_walks_cause_and_group(self):
        inner = OSError(errno.ENETUNREACH, "Network is unreachable")
        outer = ValueError("wrap")
        outer.__cause__ = inner
        assert disconnected(outer)  # 经__cause__链命中
        grp = ExceptionGroup("g", [inner])
        assert disconnected(grp)  # ExceptionGroup子异常命中
        # 防环：自引用__cause__不死循环
        a = RuntimeError("a")
        a.__cause__ = a
        assert chain(a) == [a]

    def test_message_fallback(self):
        assert network_message(RuntimeError("failed to fetch")) == "Network request failed"
        assert network_message(ValueError("weird")) == "Network connection failed"


# ════════════════════════════════════════════════════════════════
# 2. 探测竞速（kilocode check()/probe()）
# ════════════════════════════════════════════════════════════════


class _ProbeResp:
    def __init__(self, status):
        self.status_code = status


class _ProbeClient:
    """_PROBE_CLIENT_CLS替身：head返回可控状态码或抛错"""

    plan = {}

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def head(self, url, **kw):
        r = _ProbeClient.plan.get(url, "raise")
        if r == "raise":
            raise httpx.ConnectError("All connection attempts failed")
        return _ProbeResp(r)


class TestProbeRacing:
    def test_status_under_500_is_online(self, monkeypatch):
        # kilocode check()语义钉：status<500即在线（404=服务在=网络通）
        monkeypatch.setattr(network_state, "_PROBE_CLIENT_CLS", _ProbeClient)
        _ProbeClient.plan = {"https://x": 200}
        assert asyncio.run(network_state._probe_one("https://x")) is True
        _ProbeClient.plan = {"https://x": 404}
        assert asyncio.run(network_state._probe_one("https://x")) is True
        _ProbeClient.plan = {"https://x": 500}
        assert asyncio.run(network_state._probe_one("https://x")) is False
        _ProbeClient.plan = {"https://x": "raise"}
        assert asyncio.run(network_state._probe_one("https://x")) is False

    def test_any_url_online_is_online(self, monkeypatch):
        # 竞速语义：任一通即在线（kilocode Promise.any）
        seen = []

        async def _fake_one(url):
            seen.append(url)
            return "b" in url

        monkeypatch.setattr(network_state, "_probe_one", _fake_one)
        monkeypatch.setattr(network_state, "PROBE_URLS", ("https://a", "https://b", "https://c"))
        assert asyncio.run(network_state.probe_online()) is True

    def test_all_fail_is_offline(self, monkeypatch):
        seen = []

        async def _fake_one(url):
            seen.append(url)
            return False

        monkeypatch.setattr(network_state, "_probe_one", _fake_one)
        monkeypatch.setattr(network_state, "PROBE_URLS", ("https://a", "https://b", "https://c"))
        assert asyncio.run(network_state.probe_online()) is False
        assert set(seen) == {"https://a", "https://b", "https://c"}  # 三URL都试了

    def test_three_probe_urls_pinned(self):
        # kilocode是3URL竞速；本地化URL但数量/机制不变
        assert len(network_state.PROBE_URLS) == 3
        assert network_state.PROBE_TIMEOUT_S == 5.0  # kilocode PROBE_MS=5000


# ════════════════════════════════════════════════════════════════
# 3. maybe_park三态（kilocode ask()/watch()/restore()/resume()）
# ════════════════════════════════════════════════════════════════


class TestMaybeParkThreeStates:
    def test_non_disconnect_returns_not_offline(self, monkeypatch, clean):
        calls = _stub_probe(monkeypatch, [True])
        _fast_parks(monkeypatch)
        res = asyncio.run(network_state.maybe_park(ValueError("bad json")))
        assert res == NOT_OFFLINE
        assert calls["n"] == 0  # 非断连类错误连探测都不做
        assert network_state.list_waits() == []

    def test_disconnect_but_online_is_endpoint_fault(self, monkeypatch, clean):
        # 适配#2：探测在线=端点级故障≠全局断网，不park走原有退避
        calls = _stub_probe(monkeypatch, [True])
        _fast_parks(monkeypatch)
        # 断连类判定走消息模式（"connection timed out"∈kilocode msgs表）
        res = asyncio.run(network_state.maybe_park(httpx.ConnectError("Connection timed out")))
        assert res == NOT_OFFLINE
        assert calls["n"] == 1
        assert network_state.list_waits() == []
        assert network_state.stats()["total_parks"] == 0

    def test_offline_then_restored_resumes(self, monkeypatch, clean):
        # 三态主路径：blocked → 轮询 → 恢复 → 宽限 → resumed（retry续跑）
        _fast_parks(monkeypatch)
        calls = _stub_probe(monkeypatch, [False, False, False, True])  # 初始+2轮询离线，第3轮询恢复
        restored = []
        network_state.register_restore_callback(lambda: restored.append(1))
        res = asyncio.run(network_state.maybe_park(httpx.ConnectError("All connection attempts failed")))
        assert res == RESUMED
        assert calls["n"] >= 3
        waits = network_state.list_waits()
        assert len(waits) == 1 and waits[0]["state"] == RESUMED and waits[0]["restored"] is not None
        assert restored == [1]  # 恢复回调真实执行（kilocode reply()）
        assert network_state.stats()["resumed"] == 1

    def test_offline_forever_aborts(self, monkeypatch, clean):
        # 适配#3：有界park——kilocode无界等人拒绝，服务端超时→aborted
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])
        res = asyncio.run(network_state.maybe_park(httpx.ConnectError("All connection attempts failed")))
        assert res == ABORTED
        waits = network_state.list_waits()
        assert len(waits) == 1 and waits[0]["state"] == ABORTED
        assert "timeout" in waits[0]["error"]
        assert network_state.stats()["aborted"] == 1

    def test_cancel_event_aborts(self, monkeypatch, clean):
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])
        ev = asyncio.Event()
        ev.set()
        res = asyncio.run(network_state.maybe_park(httpx.ConnectError("All connection attempts failed"), ev))
        assert res == ABORTED
        assert network_state.list_waits()[0]["error"] == "cancelled"

    def test_cancel_during_grace_aborts(self, monkeypatch, clean):
        # 宽限期=人工拒绝窗口（kilocode RESUME_MS语义）：窗口内取消→aborted
        ev = asyncio.Event()

        def _hook(delay):
            if abs(delay - 0.02) < 1e-9:  # RESUME_GRACE_S被调用=已进入宽限
                ev.set()

        _fast_parks(monkeypatch, hook=_hook)
        _stub_probe(monkeypatch, [False, True])
        res = asyncio.run(network_state.maybe_park(httpx.ConnectError("All connection attempts failed"), ev))
        assert res == ABORTED
        assert network_state.list_waits()[0]["error"] == "cancelled during grace"

    def test_restore_callback_failure_does_not_block_resume(self, monkeypatch, clean):
        # kilocode reply()：恢复回调失败只记日志，续跑照常
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])
        done = []

        def _boom():
            raise RuntimeError("callback boom")

        network_state.register_restore_callback(_boom)
        network_state.register_restore_callback(lambda: done.append(1))
        res = asyncio.run(network_state.maybe_park(httpx.ConnectError("All connection attempts failed")))
        assert res == RESUMED
        assert done == [1]

    def test_stats_and_list_shape(self, monkeypatch, clean):
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])
        asyncio.run(network_state.maybe_park(httpx.ConnectError("All connection attempts failed")))
        st = network_state.stats()
        assert st["total_parks"] == 1 and st["resumed"] == 1 and st["pending"] == 0
        for key in ("probe_urls", "poll_s", "max_wait_s", "resume_grace_s"):
            assert key in st
        w = network_state.list_waits()[0]
        for key in ("id", "message", "state", "created", "restored", "error"):
            assert key in w


# ════════════════════════════════════════════════════════════════
# 4. LLMEngine集成（fake传输——park真实接进三条传输路径）
# ════════════════════════════════════════════════════════════════


def _engine():
    return LLMEngine(api_key="k", base_url="http://fake/v1", model="mimo-test")


def _patch_llm_sleep(monkeypatch):
    waits: list[float] = []

    async def _fake_sleep(delay):
        waits.append(float(delay))

    monkeypatch.setattr("agent.llm_engine.asyncio.sleep", _fake_sleep)
    return waits


class _FakeResp:
    def __init__(self, content=None, status_code=200, body=""):
        self.status_code = status_code
        self._body = body
        self.headers = {}
        self._data = {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]}

    def json(self):
        return self._data

    @property
    def text(self):
        return self._body


def _install_post_client(monkeypatch, responses: list) -> dict:
    state = {"calls": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            r = responses[idx]
            return r() if callable(r) else r

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _FakeClient)
    return state


def _sse(data: str) -> bytes:
    return f"data: {data}\n\n".encode()


def _delta_chunk(text=None, finish_reason="stop"):
    return {"choices": [{"delta": {"content": text}, "finish_reason": finish_reason}]}


class _FakeStreamResp:
    def __init__(self, events, status_code=200):
        self.status_code = status_code
        self._events = events
        self.headers = {}

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
        return b""

    async def aclose(self):
        pass


def _install_stream_client(monkeypatch, responses: list) -> dict:
    state = {"calls": 0}

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
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            r = responses[idx]
            return r() if callable(r) else r

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _FakeClient)
    return state


class TestLLMEngineIntegration:
    def test_chat_disconnect_park_then_resume_then_success(self, monkeypatch, clean):
        # 断网→park等恢复→自动续跑→第二次调用成功（重试记账不变，语义不变只加等待）
        _patch_llm_sleep(monkeypatch)
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])  # 初始离线，第1轮询恢复
        done = []
        network_state.register_restore_callback(lambda: done.append(1))

        def _raise():
            raise httpx.ConnectError("All connection attempts failed")

        state = _install_post_client(monkeypatch, [
            _raise,
            _FakeResp(content="断网恢复后的回答"),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "断网恢复后的回答"
        assert state["calls"] == 2
        assert network_state.list_waits()[0]["state"] == RESUMED
        assert done == [1]  # 恢复回调在真实传输路径被触发（MCP重连挂载点）

    def test_chat_offline_abort_falls_back_to_visible_failure(self, monkeypatch, clean):
        # aborted回落原失败契约："[LLM错误: …]"可见失败不吞（mem0）
        _patch_llm_sleep(monkeypatch)
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])  # 一直离线

        def _raise():
            raise httpx.ConnectError("All connection attempts failed")

        state = _install_post_client(monkeypatch, [_raise])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out.startswith("[LLM错误:")
        assert network_state.list_waits()[0]["state"] == ABORTED
        assert state["calls"] == 5  # MAX_ATTEMPTS=5重试记账（10.1拉长）

    def test_chat_stream_disconnect_park_then_resume(self, monkeypatch, clean):
        _patch_llm_sleep(monkeypatch)
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])

        def _raise():
            raise httpx.ConnectError("socket hang up")

        state = _install_stream_client(monkeypatch, [
            _raise,
            _FakeStreamResp([_delta_chunk("流式恢复回答"), "[DONE]"]),
        ])
        out = []

        async def _run():
            async for x in _engine().chat_stream([{"role": "user", "content": "hi"}]):
                out.append(x)

        asyncio.run(_run())
        assert out == ["流式恢复回答"]
        assert state["calls"] == 2
        assert network_state.list_waits()[0]["state"] == RESUMED

    def test_with_tools_disconnect_park_then_resume(self, monkeypatch, clean):
        _patch_llm_sleep(monkeypatch)
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])

        def _raise():
            raise httpx.ConnectError("connection terminated")

        state = _install_stream_client(monkeypatch, [
            _raise,
            _FakeStreamResp([_delta_chunk("工具路径恢复"), "[DONE]"]),
        ])
        tools = [{"type": "function", "function": {
            "name": "read_file", "parameters": {"type": "object", "properties": {}}}}]
        out = []

        async def _run():
            async for x in _engine().chat_stream_with_tools(
                [{"role": "user", "content": "hi"}], tools
            ):
                out.append(x)

        asyncio.run(_run())
        assert "工具路径恢复" in out
        assert state["calls"] == 2
        assert network_state.list_waits()[0]["state"] == RESUMED

    def test_endpoint_fault_does_not_park(self, monkeypatch, clean):
        # 探测在线（端点级故障）：不park不建wait记录，原退避重试照旧
        _patch_llm_sleep(monkeypatch)
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [True])

        def _raise():
            raise httpx.ConnectError("All connection attempts failed")

        state = _install_post_client(monkeypatch, [
            _raise,
            _FakeResp(content="好的"),
        ])
        out = asyncio.run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert out == "好的"
        assert network_state.list_waits() == []
        assert network_state.stats()["total_parks"] == 0


# ════════════════════════════════════════════════════════════════
# 5. MCP重连（kilocode reply()：恢复后重连failed Server）
# ════════════════════════════════════════════════════════════════


class _MCPResp:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class _MCPClient:
    """mcp-client(:8094)HTTP替身：get返回server清单，post记录connect调用"""

    servers: list = []
    post_log: list = []
    get_raises = False
    post_status = 200

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if _MCPClient.get_raises:
            raise httpx.ConnectError("mcp-client down")
        return _MCPResp({"servers": _MCPClient.servers})

    async def post(self, url, **kw):
        _MCPClient.post_log.append(url)
        return _MCPResp({}, status_code=_MCPClient.post_status)


class TestMCPReconnect:
    def _setup(self, monkeypatch):
        _MCPClient.servers = []
        _MCPClient.post_log = []
        _MCPClient.get_raises = False
        _MCPClient.post_status = 200
        monkeypatch.setattr(network_state, "_PROBE_CLIENT_CLS", _MCPClient)

    def test_only_error_servers_reconnected(self, monkeypatch):
        # kilocode reply()：status!=="failed"不动（disconnected可能是人为断开）
        self._setup(monkeypatch)
        _MCPClient.servers = [
            {"id": "a", "status": "error"},
            {"id": "b", "status": "connected"},
            {"id": "c", "status": "disconnected"},
        ]
        res = asyncio.run(network_state.reconnect_failed_mcp_servers())
        assert res["checked"] == 3
        assert res["reconnected"] == 1
        assert _MCPClient.post_log == ["http://127.0.0.1:8094/api/mcp/servers/a/connect"]

    def test_unreachable_mcp_client_never_raises(self, monkeypatch):
        # kilocode log.error语义：重连失败绝不反噬LLM续跑主流程
        self._setup(monkeypatch)
        _MCPClient.get_raises = True
        res = asyncio.run(network_state.reconnect_failed_mcp_servers())
        assert res["reconnected"] == 0
        assert res["errors"]

    def test_partial_failure_recorded(self, monkeypatch):
        self._setup(monkeypatch)
        _MCPClient.servers = [{"id": "a", "status": "error"}, {"id": "b", "status": "error"}]
        _MCPClient.post_status = 500
        res = asyncio.run(network_state.reconnect_failed_mcp_servers())
        assert res["reconnected"] == 0
        assert len(res["errors"]) == 2  # 逐条记账


# ════════════════════════════════════════════════════════════════
# 6. 接线钉（防死接线：新符号必须在真实运行时路径被调用）
# ════════════════════════════════════════════════════════════════


class TestWiringPins:
    def test_llm_engine_three_paths_consume_maybe_park(self):
        src = _src("agent/llm_engine.py")
        assert src.count("network_state.maybe_park") == 3  # chat/chat_stream/with_tools
        # 三处都在except httpx.TransportError分支内（真实传输错误路径）
        assert src.count("except httpx.TransportError as e:") == 3

    def test_app_exposes_network_status_endpoint(self):
        src = _src("app.py")
        assert "/api/network/status" in src
        assert "register_restore_callback" in src  # MCP重连挂载
        assert "reconnect_failed_mcp_servers" in src

    def test_kilocode_constants_pinned(self):
        # 逐值对应network.ts：POLL_MS=3000/PROBE_MS=5000/RESUME_MS=10000
        assert network_state.POLL_S == 3.0
        assert network_state.PROBE_TIMEOUT_S == 5.0
        assert network_state.RESUME_GRACE_S == 10.0

    def test_existing_retry_contract_untouched(self):
        # "语义不变只加等待"钉：原有重试策略消费与失败契约保持在场
        src = _src("agent/llm_engine.py")
        assert "from utils.llm_retry import" in src
        assert "is_retryable_status" in src and "backoff_ms" in src
        assert "[LLM错误:" in src and "[LLM响应超时]" in src

    def test_network_state_docstring_records_adaptations(self):
        # 移植适配必须留痕（URL本地化/先探测再park/有界park/MCP跨进程）
        src = _src("utils/network_state.py")
        for kw in ("kilocode", "Promise.any", "RESUME_MS", "本地化", "有界park"):
            assert kw in src
