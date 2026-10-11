# -*- coding: utf-8 -*-
"""
13.1 流式路径 stall 停顿治理（utils.stall_guard 流式扩面，周期#77）——注入故障真实验收。

背景（12.3 事故族最后已知缺口）：非流式 chat() 停顿已由 stall_guard 封死（周期#69），
流式 chat_stream / chat_stream_with_tools 的消费端挂起与之同族——响应头/每个 chunk
之间挂死时 httpx read=120 超时在 websockets serve 循环里不开火，回合无界等待。

验收标准（路线图 13.1）：
1. 等响应头挂死 → 有界中止，零输出可重试 → 重试恢复，调用完成
2. 消费中 chunk 间隙挂死（已流出 partial 输出）→ 有界中止，has_output=True 不重试
   （partial 输出/has_output 重试规则零改动），失败文案 "[LLM响应超时]" 逐字不动
3. tool_calls 路径同族：args 已累积=has_output，停顿不重试不污染状态
4. 涓流零误杀负控制（必测）：chunk 间隙 < stall 的慢生成照常收完整流
5. 涓流假进展（间隙都小但从不收口）→ 流式总时长兜底（stream deadline）有界中止
6. 错误契约零变化：httpx.ReadTimeout → classify_failure=read_timeout /
   is_transient_error=True，既有 except httpx.TransportError 原样接住
7. 接线护栏：chat_stream / chat_stream_with_tools 真实走守卫 + 重试规则未被改动
8. SOULMATE_LLM_STALL_GUARD=off 旁路回原行为
"""
import asyncio
import http.server
import inspect
import json
import threading
import time

import httpx
import pytest

import agent.llm_engine as llm_engine
from agent.llm_engine import LLMEngine
from utils.llm_retry import classify_failure, is_transient_error
from utils.stall_guard import (
    DEFAULT_STREAM_DEADLINE_S,
    aiter_with_stall_guard,
    open_stream_with_stall_guard,
    stall_guard_enabled,
    stream_deadline_s,
)


# ── SSE 载荷构造（OpenAI 标准流式形态）────────────────────────────────
def _sse(obj) -> bytes:
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


def _ev_content(text: str) -> bytes:
    return _sse({"choices": [{"delta": {"content": text}}]})


def _ev_finish(reason: str = "stop") -> bytes:
    return _sse({"choices": [{"delta": {}, "finish_reason": reason}]})


DONE = b"data: [DONE]\n\n"


def _sse_tool_stream() -> list[bytes]:
    """标准 tool_calls 增量流：id/name 先行、arguments 增量拼接、finish=tool_calls"""
    return [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "read_file", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\": \"a.txt\"}"}}]}}]}),
        _ev_finish("tool_calls"),
        DONE,
    ]


def _make_handler(state):
    class StreamStallHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # 静音
            pass

        def _pick(self):
            with state["lock"]:
                idx = min(state["i"], len(state["script"]) - 1)
                state["i"] += 1
                state["calls"].append(idx)
                return dict(state["script"][idx])

        def _begin_sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

        def _write_chunk(self, data: bytes):
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()

        def _end_chunks(self):
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            self.close_connection = True

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            spec = self._pick()
            kind = spec["kind"]
            try:
                if kind == "sse_ok":
                    self._begin_sse()
                    for ev in (_ev_content("hel"), _ev_content("lo"),
                               _ev_finish(), DONE):
                        self._write_chunk(ev)
                    self._end_chunks()
                elif kind == "sse_slow_head":
                    # 合法慢 TTFB（首 token 时长 < stall）：不该误杀
                    time.sleep(spec.get("secs", 0.3))
                    self._begin_sse()
                    for ev in (_ev_content("slow-ok"), _ev_finish(), DONE):
                        self._write_chunk(ev)
                    self._end_chunks()
                elif kind == "sse_stall_mid":
                    # 消费中挂死：partial 输出已流出后 chunk 间隙零字节（事故形态B同族）
                    self._begin_sse()
                    self._write_chunk(_ev_content("PART"))
                    self._write_chunk(_ev_content("IAL"))
                    time.sleep(spec.get("secs", 5.0))
                    self.close_connection = True
                elif kind == "sse_tool_stall_mid":
                    # tool_calls args 已累积后挂死（has_output 同族语义）
                    self._begin_sse()
                    for ev in _sse_tool_stream()[:2]:
                        self._write_chunk(ev)
                    time.sleep(spec.get("secs", 5.0))
                    self.close_connection = True
                elif kind == "sse_tool_ok":
                    self._begin_sse()
                    for ev in _sse_tool_stream():
                        self._write_chunk(ev)
                    self._end_chunks()
                elif kind == "hang_head":
                    # 响应头之前长时间零字节（事故形态A：等响应头挂死）
                    time.sleep(spec.get("secs", 5.0))
                    self.close_connection = True
                elif kind == "sse_trickle":
                    # 涓流：每个 gap 秒一个 chunk（gap < stall 不该误杀）
                    n = spec.get("n", 10)
                    gap = spec.get("gap", 0.1)
                    self._begin_sse()
                    for i in range(n):
                        self._write_chunk(_ev_content("c%d" % i))
                        time.sleep(gap)
                    self._write_chunk(_ev_finish())
                    self._write_chunk(DONE)
                    self._end_chunks()
                elif kind == "sse_trickle_forever":
                    # 永久涓流：间隙都小但从不收口（stream deadline 兜底用例）
                    self._begin_sse()
                    for _i in range(spec.get("n", 200)):
                        self._write_chunk(_ev_content("t"))
                        time.sleep(spec.get("gap", 0.15))
            except (BrokenPipeError, ConnectionResetError, OSError):
                # 对端中止读取（守卫开火）属预期路径，静默
                self.close_connection = True

    return StreamStallHandler


class _Server:
    def __init__(self, script):
        self.state = {"script": script, "calls": [], "i": 0,
                      "lock": threading.Lock()}
        handler = _make_handler(self.state)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def n_calls(self):
        with self.state["lock"]:
            return len(self.state["calls"])


@pytest.fixture()
def fast_env(monkeypatch, tmp_path):
    """测试期小阈值 + 退避封顶 + 失败记账落临时库（不污染真实库）"""
    monkeypatch.setenv("SOULMATE_LLM_STALL_TIMEOUT_S", "0.5")
    monkeypatch.setenv("SOULMATE_LLM_STREAM_DEADLINE_S", "8")
    monkeypatch.setenv("SOULMATE_LLM_RETRY_MAX_WAIT_S", "0.1")
    monkeypatch.setenv("TOOL_FAILURE_DB_PATH", str(tmp_path / "tf.db"))
    old = llm_engine._llm_fail_log
    llm_engine._llm_fail_log = None
    yield
    llm_engine._llm_fail_log = old


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


def _make_engine(srv) -> LLMEngine:
    return LLMEngine(api_key="sk-test",
                     base_url=f"http://127.0.0.1:{srv.port}/v1",
                     model="m-test")


async def _collect_stream(eng, **kw):
    parts = []
    async for chunk in eng.chat_stream([{"role": "user", "content": "hi"}], **kw):
        parts.append(chunk)
    return parts


async def _collect_stream_tools(eng, **kw):
    parts = []
    async for chunk in eng.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function",
                    "function": {"name": "read_file", "parameters": {}}}],
            **kw):
        parts.append(chunk)
    return parts


# ══════════════ 1/2. 两类停顿形态：有界中止 + 重试规则 ══════════════

class TestStreamStall:
    def test_head_stall_retries_and_recovers(self, fast_env):
        """等响应头挂死：零输出可重试 → 重试恢复，完整输出无错误标记"""
        srv = _Server([{"kind": "hang_head", "secs": 5.0}, {"kind": "sse_ok"}])
        try:
            eng = _make_engine(srv)
            t0 = time.monotonic()
            parts = _run(_collect_stream(eng))
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        text = "".join(p for p in parts if isinstance(p, str))
        assert text == "hello"
        assert "[LLM响应超时]" not in text
        assert elapsed < 3.0, f"头前停顿未被有界中止: {elapsed:.1f}s"
        assert srv.n_calls >= 2  # 首发被中止 + 重试真实发出

    def test_mid_stall_after_partial_no_retry_contract(self, fast_env):
        """消费中挂死（partial 已流出）：has_output=True 不重试，"[LLM响应超时]" 逐字"""
        srv = _Server([{"kind": "sse_stall_mid", "secs": 5.0}])
        try:
            eng = _make_engine(srv)
            t0 = time.monotonic()
            parts = _run(_collect_stream(eng))
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        text = "".join(p for p in parts if isinstance(p, str))
        assert text.startswith("PARTIAL"), f"partial 输出丢失: {text!r}"
        assert text.endswith("\n[LLM响应超时]"), f"失败文案契约被改: {text!r}"
        assert elapsed < 3.0, f"消费中停顿未被有界中止: {elapsed:.1f}s"
        assert srv.n_calls == 1  # has_output=True → 零重试（防双份回复）

    def test_tool_stream_mid_stall_no_state_pollution(self, fast_env):
        """tool_calls args 已累积=has_output：停顿不重试、不吐半成品 tool_calls"""
        srv = _Server([{"kind": "sse_tool_stall_mid", "secs": 5.0}])
        try:
            eng = _make_engine(srv)
            parts = _run(_collect_stream_tools(eng))
        finally:
            srv.close()
        assert srv.n_calls == 1  # 已累积 tool_calls → 零重试
        assert not any(isinstance(p, dict) and "tool_calls" in p for p in parts), \
            "停顿失败路径吐出半成品 tool_calls"
        text = "".join(p for p in parts if isinstance(p, str))
        assert text.endswith("\n[LLM响应超时]")

    def test_tool_stream_head_stall_recovers(self, fast_env):
        """tool 路径等响应头挂死：重试恢复，tool_calls 完整产出"""
        srv = _Server([{"kind": "hang_head", "secs": 5.0}, {"kind": "sse_tool_ok"}])
        try:
            eng = _make_engine(srv)
            parts = _run(_collect_stream_tools(eng))
        finally:
            srv.close()
        tc_payloads = [p for p in parts if isinstance(p, dict) and "tool_calls" in p]
        assert len(tc_payloads) == 1
        tc = tc_payloads[0]["tool_calls"][0]
        assert tc["function"]["name"] == "read_file"
        assert json.loads(tc["function"]["arguments"]) == {"path": "a.txt"}
        assert srv.n_calls >= 2


# ══════════════ 4. 涓流零误杀负控制（必测）══════════════

class TestNoFalseKill:
    def test_stream_trickle_within_stall_completes(self, fast_env):
        """chunk 间隙 0.1s < stall 0.5s、总时长 1s > stall：照常收完整流，零误杀"""
        srv = _Server([{"kind": "sse_trickle", "n": 10, "gap": 0.1}])
        try:
            eng = _make_engine(srv)
            t0 = time.monotonic()
            parts = _run(_collect_stream(eng))
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        text = "".join(p for p in parts if isinstance(p, str))
        assert text == "".join("c%d" % i for i in range(10)), f"涓流内容缺失: {text!r}"
        assert "[LLM响应超时]" not in text and "[LLM错误" not in text
        assert elapsed > 0.5, "样本总时长应超过 stall（证明按间隙计时非总计时）"

    def test_slow_first_token_within_stall_completes(self, fast_env):
        """TTFB 0.3s < stall 0.5s 的合法慢首 token：照常完成"""
        srv = _Server([{"kind": "sse_slow_head", "secs": 0.3}])
        try:
            eng = _make_engine(srv)
            parts = _run(_collect_stream(eng))
        finally:
            srv.close()
        text = "".join(p for p in parts if isinstance(p, str))
        assert text == "slow-ok"


# ══════════════ 5. 流式总时长兜底：涓流假进展有界 ══════════════

class TestStreamDeadline:
    def test_deadline_caps_endless_trickle(self, fast_env, monkeypatch):
        """间隙 0.15s < stall 0.5s 但从不收口：stream deadline 1s 兜底中止"""
        monkeypatch.setenv("SOULMATE_LLM_STREAM_DEADLINE_S", "1")
        srv = _Server([{"kind": "sse_trickle_forever", "n": 200, "gap": 0.15}])
        try:
            eng = _make_engine(srv)
            t0 = time.monotonic()
            parts = _run(_collect_stream(eng))
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        text = "".join(p for p in parts if isinstance(p, str))
        assert text.endswith("\n[LLM响应超时]")
        assert elapsed < 4.0, f"涓流假进展未被兜底: {elapsed:.1f}s"


# ══════════════ 6. 错误契约零变化 ══════════════

class TestErrorContract:
    def test_stream_stall_is_transient_read_timeout(self, fast_env):
        """守卫抛出的错与非流式同族：TransportError 家族 / read_timeout / transient"""
        async def go():
            async def gen():
                yield b"x"
                await asyncio.sleep(5)
                yield b"y"
            out = []
            async for item in aiter_with_stall_guard(gen(), stall_s=0.2):
                out.append(item)
            return out
        with pytest.raises(httpx.ReadTimeout) as ei:
            _run(go())
        err = ei.value
        assert "响应体无新数据" in str(err)
        assert isinstance(err, httpx.TransportError)
        assert is_transient_error(err) is True
        assert classify_failure(err) == "read_timeout"

    def test_head_stall_wording(self, fast_env):
        async def go():
            await open_stream_with_stall_guard(
                asyncio.sleep(5, result="resp"), stall_s=0.2)
        with pytest.raises(httpx.ReadTimeout) as ei:
            _run(go())
        assert "未收到响应头" in str(ei.value)

    def test_inner_exception_passes_through(self, fast_env):
        """内层迭代器自身异常（如 RemoteProtocolError）原样透传不被吞"""
        async def go():
            async def gen():
                yield b"x"
                raise httpx.RemoteProtocolError("peer closed")
            async for _item in aiter_with_stall_guard(gen(), stall_s=5):
                pass
        with pytest.raises(httpx.RemoteProtocolError):
            _run(go())


# ══════════════ 7. 接线护栏 ══════════════

class TestWiring:
    def test_streaming_paths_use_guard(self):
        s1 = inspect.getsource(LLMEngine.chat_stream)
        s2 = inspect.getsource(LLMEngine.chat_stream_with_tools)
        assert "aiter_with_stall_guard" in s1 and "aiter_with_stall_guard" in s2
        assert "open_stream_with_stall_guard" in s1
        assert "open_stream_with_stall_guard" in s2

    def test_partial_output_retry_rule_untouched(self):
        """partial 输出/has_output 重试规则耦合不动（契约约束）"""
        s1 = inspect.getsource(LLMEngine.chat_stream)
        s2 = inspect.getsource(LLMEngine.chat_stream_with_tools)
        assert "has_output=bool(_segment_parts)" in s1
        assert "has_output=_has_out" in s2

    def test_failure_wording_untouched(self):
        """既有失败文案逐字不动"""
        s1 = inspect.getsource(LLMEngine.chat_stream)
        s2 = inspect.getsource(LLMEngine.chat_stream_with_tools)
        assert '[LLM响应超时]' in s1 and '[LLM响应超时]' in s2


# ══════════════ 8. 配置与旁路 ══════════════

class TestConfig:
    def test_stream_deadline_default_and_env(self, monkeypatch):
        monkeypatch.delenv("SOULMATE_LLM_STREAM_DEADLINE_S", raising=False)
        assert stream_deadline_s() == DEFAULT_STREAM_DEADLINE_S == 900.0
        monkeypatch.setenv("SOULMATE_LLM_STREAM_DEADLINE_S", "33")
        assert stream_deadline_s() == 33.0

    def test_guard_disabled_bypasses_stream(self, fast_env, monkeypatch):
        """off 旁路：stall 极小也不中止（原样迭代）"""
        monkeypatch.setenv("SOULMATE_LLM_STALL_GUARD", "off")
        assert stall_guard_enabled() is False

        async def go():
            async def gen():
                for i in range(5):
                    yield b"x"
                    await asyncio.sleep(0.1)
            return [x async for x in aiter_with_stall_guard(gen(), stall_s=0.05)]
        items = _run(go())
        assert items == [b"x"] * 5  # 0.05s 阈值下涓流若走守卫必被中止——旁路实证
