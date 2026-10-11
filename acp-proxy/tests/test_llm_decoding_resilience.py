# -*- coding: utf-8 -*-
"""
14.5 上游传输错误韧性（路线图阶段14）

两件（路线图14.5原文）：
1. httpx.DecodingError（incorrect header check）解压错误——核实 llm_engine 重试链
   覆盖该异常类 + 请求禁透明压缩（Accept-Encoding: identity 根因消除）
2. 错误文本不再进会话标题（"[LLM错误: …]"污染，14.2 附带观察实证）

覆盖口径（三面）：
- 分类面：is_transient_error 认 DecodingError（重发新响应可自愈=瞬时）
- 传输面：chat()/chat_stream()/chat_stream_with_tools/_post_single_link 重试环
  catch 面覆盖 DecodingError（真实socket故障注入，坏gzip体→重试→恢复）
- 消费面：is_llm_error_text + _generate_session_title 错误文本不进标题
"""
import asyncio
import http.server
import json
import threading

import httpx
import pytest

import agent.llm_engine as llm_engine
import agent.soulmate_agent as sma
from agent.llm_engine import LLMEngine
from utils.llm_retry import is_llm_error_text, is_transient_error
from utils.model_fallback import FallbackLink

# ── 故障注入HTTP服务器（真实socket，非mock） ─────────────────────────────

SSE_OK = (
    'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)

CHAT_OK_BODY = json.dumps({
    "choices": [{"message": {"content": "ok-title"}, "finish_reason": "stop"}],
    "usage": {},
})


def _make_handler(state):
    class DecodingHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # 静音
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            with state["lock"]:
                idx = min(state["i"], len(state["script"]) - 1)
                state["i"] += 1
                state["calls"].append(idx)
                # 请求头留证（Accept-Encoding: identity 接线钉）
                state["req_headers"].append({k.lower(): v for k, v in self.headers.items()})
                spec = dict(state["script"][idx])
            kind = spec["kind"]
            if kind == "badgzip":
                # 声明 content-encoding: gzip 却发非 gzip 体（14.2 实证形态：
                # "Error -3 while decompressing data: incorrect header check"）
                # ——模拟不合规上游/透明代理重压缩损坏，客户端解码必炸
                payload = b"definitely-not-gzip-payload"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif kind == "chat_ok":
                payload = CHAT_OK_BODY.encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif kind == "sse":
                payload = spec.get("body", SSE_OK).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

    return DecodingHandler


class _Server:
    def __init__(self, script):
        self.state = {"script": script, "calls": [], "req_headers": [], "i": 0,
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

    @property
    def req_headers(self):
        with self.state["lock"]:
            return list(self.state["req_headers"])


def _engine(port):
    return LLMEngine(
        api_key="sk-test-key",
        base_url=f"http://127.0.0.1:{port}/v1",
        model="m-test",
    )


# ════════════════ 1. 分类面：DecodingError=瞬时可重试 ════════════════


class TestTransientClassification:
    def test_decoding_error_is_transient(self):
        """DecodingError（压缩体损坏）=瞬时请求族异常，重发可自愈"""
        assert is_transient_error(httpx.DecodingError("incorrect header check"))

    def test_transport_error_still_transient(self):
        """既有面不缩水：TransportError 家族照旧瞬时"""
        assert is_transient_error(httpx.ReadTimeout("x"))
        assert is_transient_error(httpx.RemoteProtocolError("peer closed"))

    def test_generic_error_not_transient(self):
        """负控制：普通异常不因本切片被误判瞬时"""
        assert not is_transient_error(ValueError("boom"))
        assert not is_transient_error(RuntimeError("LLM API error: 500"))


# ════════════════ 2. 传输面：重试环覆盖 DecodingError ════════════════


class TestRetryCoversDecodingError:
    def test_chat_recovers_after_bad_gzip(self, monkeypatch):
        """chat()（标题/规划/判词真实路径）：坏gzip响应→DecodingError→重试→恢复

        修复前：DecodingError 不在 except httpx.TransportError 面内 → 直接落
        外层 except Exception 返回"[LLM错误: …]"，零重试（14.2 解压错误实证形态）。
        """
        monkeypatch.setattr("utils.llm_retry.backoff_ms", lambda attempt, headers=None: 10)
        srv = _Server([
            {"kind": "badgzip"},
            {"kind": "chat_ok"},
        ])
        try:
            eng = _engine(srv.port)
            out = asyncio.run(eng.chat([{"role": "user", "content": "hi"}]))
        finally:
            srv.close()
        assert out == "ok-title", f"重试后应恢复产出，实际={out!r}"
        assert srv.n_calls == 2, "坏gzip一次+重试一次=2次请求"
        # 禁透明压缩接线钉：请求携带 Accept-Encoding: identity
        for h in srv.req_headers:
            assert h.get("accept-encoding") == "identity"

    def test_stream_recovers_after_bad_gzip(self, monkeypatch):
        """chat_stream()（simple chat 真实路径）：坏gzip→DecodingError→重试→恢复"""
        monkeypatch.setattr("utils.llm_retry.backoff_ms", lambda attempt, headers=None: 10)
        srv = _Server([
            {"kind": "badgzip"},
            {"kind": "sse"},
        ])
        try:
            eng = _engine(srv.port)

            async def _collect():
                parts = []
                async for chunk in eng.chat_stream([{"role": "user", "content": "hi"}]):
                    parts.append(chunk)
                return parts

            out = "".join(asyncio.run(_collect()))
        finally:
            srv.close()
        assert "hello" in out, f"重试后应产出真实内容，实际={out!r}"
        assert srv.n_calls == 2
        for h in srv.req_headers:
            assert h.get("accept-encoding") == "identity"

    def test_persistent_bad_gzip_fails_visibly(self, monkeypatch):
        """负控制：持续坏gzip→有界重试后显式"[LLM错误"（失败契约逐字不变）"""
        monkeypatch.setattr("utils.llm_retry.backoff_ms", lambda attempt, headers=None: 10)
        srv = _Server([{"kind": "badgzip"}])
        try:
            eng = _engine(srv.port)
            out = asyncio.run(eng.chat([{"role": "user", "content": "hi"}]))
        finally:
            srv.close()
        assert out.startswith("[LLM错误"), f"失败必须可见，实际={out!r}"
        assert srv.n_calls <= 5, "重试有界（MAX_ATTEMPTS），禁无限重发"


# ════════════════ 3. 禁透明压缩接线钉 ════════════════


class TestNoTransparentCompression:
    def test_make_client_identity(self):
        eng = LLMEngine(api_key="sk-test", base_url="http://127.0.0.1:1/v1", model="m")
        client = eng._make_client()
        try:
            assert client.headers.get("accept-encoding") == "identity"
            assert client.headers.get("authorization") == "Bearer sk-test"
        finally:
            asyncio.run(client.aclose())

    def test_fallback_link_identity(self):
        """_apply_link 整体替换 client.headers——link 缺此头即降级链压缩复活"""
        link = FallbackLink(provider="p", model="m", base_url="http://x/v1", api_key="k")
        h = link.headers()
        assert h["Accept-Encoding"] == "identity"
        assert h["Authorization"] == "Bearer k"

    def test_llm_transport_merges_identity(self, monkeypatch):
        """utils/llm_transport._post_single_link 发送面缺省禁压缩，调用方显式值优先"""
        monkeypatch.setenv("SOULMATE_LLM_STALL_GUARD", "off")
        captured = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}

        class _FakeClient:
            async def request(self, method, url, headers=None, json=None):
                captured["headers"] = dict(headers or {})
                return _Resp()

        import utils.llm_transport as lt

        async def _run():
            return await lt._post_single_link(
                _FakeClient(),
                base_url="http://x/v1",
                headers={"Authorization": "Bearer k"},
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.7,
                max_tokens=100,
                attempts=1,
                strand_id="t",
            )

        out = asyncio.run(_run())
        assert out["choices"][0]["message"]["content"] == "ok"
        assert captured["headers"]["Accept-Encoding"] == "identity"
        assert captured["headers"]["Authorization"] == "Bearer k"


# ════════════════ 4. 消费面：错误文本禁进会话标题 ════════════════


class TestErrorTextContract:
    def test_error_prefixes_detected(self):
        assert is_llm_error_text("[LLM错误: Error -3 while decompressing data]")
        assert is_llm_error_text("[LLM响应超时]")
        assert is_llm_error_text("  [LLM错误: x]  ")  # strip 容忍

    def test_normal_text_not_flagged(self):
        assert not is_llm_error_text("创建并运行Python文件")
        assert not is_llm_error_text("")
        assert not is_llm_error_text(None)
        # 负控制：正文里提到错误字样不误判（只认契约前缀）
        assert not is_llm_error_text("排查 [LLM错误: x] 的处理流程")


class TestTitleGuard:
    def _agent(self, llm_reply):
        ag = sma.SoulMateAgent.__new__(sma.SoulMateAgent)
        ag.sessions = {"sid-title-guard": {"title": "临时标题", "messages": []}}

        async def _fake_aux(label, messages, **kwargs):
            return llm_reply

        ag._aux_llm_chat = _fake_aux
        return ag

    def test_error_text_never_becomes_title(self):
        """14.2 实证污染形态："[LLM错误: …]"绝不写进会话标题，保留临时标题"""
        ag = self._agent("[LLM错误: Error -3 while decompressing data: incorrect header check]")
        asyncio.run(ag._generate_session_title("sid-title-guard", "帮我创建并运行一个Python文件"))
        assert ag.sessions["sid-title-guard"]["title"] == "临时标题"

    def test_timeout_text_never_becomes_title(self):
        ag = self._agent("[LLM响应超时]")
        asyncio.run(ag._generate_session_title("sid-title-guard", "你好"))
        assert ag.sessions["sid-title-guard"]["title"] == "临时标题"

    def test_normal_title_sets_and_syncs(self):
        """正控制：正常标题照常写入+同步OpenSoul（PATCH载荷断言，不打真实服务）"""
        ag = self._agent("  测试标题  ")
        patched = {}

        class _FakeResp:
            status_code = 200

        class _FakeClient:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def patch(self, url, json=None, timeout=None):
                patched["url"] = url
                patched["json"] = json

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(httpx, "AsyncClient", _FakeClient)
            asyncio.run(ag._generate_session_title("sid-title-guard", "帮我测试"))
        assert ag.sessions["sid-title-guard"]["title"] == "测试标题"
        assert patched["json"] == {"title": "测试标题"}
        assert "sid-title-guard" in patched["url"]
