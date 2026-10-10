# -*- coding: utf-8 -*-
"""
10.1 LLM 传输韧性治理 —— 注入故障真实验收（路线图阶段10）

验收标准（路线图10.1原文）：注入故障模拟上游抖动，回合要么完成要么显式报因
（禁静默空转）。周期#54上游降级三态实证形态逐一对齐：
- 429 Too Many Requests（限流）
- 503 ServiceUnavailableError（服务端瞬时）
- RemoteProtocolError incomplete chunked read（连接半途断）

本测试起**真实本地HTTP服务器**注入上述故障，走 LLMEngine 真实 httpx 传输
（chat_stream_with_tools = soulmate 真实回复路径），断言：
1. 抖动后恢复 → 回合完成（产出真实内容）
2. 持续降级 → 显式 "[LLM错误" 报因（不静默、有界时间）
3. 上游 Retry-After: 3600 → 等待有硬上限（禁静默空转），回合照常推进
4. 传输故障分类入 tool_failures.db（rate_limit/server_error/protocol_error…）
"""
import asyncio
import http.server
import json
import os
import socket
import sqlite3
import threading
import time

import httpx
import pytest

import agent.llm_engine as llm_engine
from agent.llm_engine import LLMEngine, record_llm_failure
from utils.llm_retry import (
    MAX_ATTEMPTS,
    RETRY_MAX_WAIT_MS,
    backoff_ms,
    classify_failure,
    max_wait_ms,
)

# ── 故障注入HTTP服务器（真实socket，非mock） ─────────────────────────────

SSE_OK = (
    'data: {"choices":[{"delta":{"content":"hello"},"finish_reason":null}]}\n\n'
    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    "data: [DONE]\n\n"
)


def _make_handler(state):
    class FaultHandler(http.server.BaseHTTPRequestHandler):
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
                spec = dict(state["script"][idx])
            kind = spec["kind"]
            if kind == "status":
                payload = spec.get("body", "err").encode()
                self.send_response(spec["status"])
                for k, v in spec.get("headers", {}).items():
                    self.send_header(k, v)
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
            elif kind == "truncate":
                # 声明chunked编码但不发终止0-chunk直接断连 =
                # incomplete chunked read（周期#54 RemoteProtocolError实证形态）
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"5\r\nhello\r\n")  # 一个完整chunk但无终止chunk
                self.wfile.flush()
                self.close_connection = True

    return FaultHandler


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


def _engine(port):
    return LLMEngine(
        api_key="sk-test-key",
        base_url=f"http://127.0.0.1:{port}/v1",
        model="m-test",
    )


async def _collect(eng, timeout=60.0):
    parts = []
    async def _run():
        async for chunk in eng.chat_stream_with_tools(
            [{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "noop"}}],
        ):
            parts.append(chunk)
    await asyncio.wait_for(_run(), timeout=timeout)
    return parts


def _db_rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT tool_name, kind, action, attempt, outcome FROM tool_failures"
            " ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    return rows


@pytest.fixture()
def fail_db(tmp_path, monkeypatch):
    """tool_failures.db 指到临时库 + 重置lazy单例（每测试独立观测）"""
    db = tmp_path / "tf.db"
    monkeypatch.setenv("TOOL_FAILURE_DB_PATH", str(db))
    old = llm_engine._llm_fail_log
    llm_engine._llm_fail_log = None
    yield db
    llm_engine._llm_fail_log = old


# ════════════════ 1. 抖动→恢复：回合完成 ════════════════


class TestJitterRideOut:
    def test_429_503_then_success_completes(self, fail_db):
        """429(Retry-After)+503抖动后恢复→回合完成产出真实内容（真实socket）"""
        srv = _Server([
            {"kind": "status", "status": 429, "body": "rate limited",
             "headers": {"retry-after-ms": "20"}},
            {"kind": "status", "status": 503, "body": "unavailable"},
            {"kind": "sse"},
        ])
        try:
            eng = _engine(srv.port)
            out = asyncio.run(_collect(eng))
        finally:
            srv.close()
        joined = "".join(out)
        assert "hello" in joined          # 回合完成，真实产出
        assert "[LLM错误" not in joined   # 非显式报因形态
        assert srv.n_calls == 3          # 429→503→200，重试真实穿过两次故障
        rows = _db_rows(fail_db)
        kinds = [r[1] for r in rows if r[2] == "llm_retry"]
        assert "rate_limit" in kinds     # 传输故障分类可观测
        assert "server_error" in kinds

    def test_incomplete_chunked_read_then_success_completes(self, fail_db):
        """incomplete chunked read（周期#54实证形态）→瞬时传输错误重试→完成"""
        srv = _Server([{"kind": "truncate"}, {"kind": "sse"}])
        try:
            eng = _engine(srv.port)
            out = asyncio.run(_collect(eng))
        finally:
            srv.close()
        joined = "".join(out)
        assert "hello" in joined
        assert srv.n_calls == 2
        rows = _db_rows(fail_db)
        retry_kinds = [r[1] for r in rows if r[2] == "llm_retry"]
        assert "protocol_error" in retry_kinds


# ════════════════ 2. 持续降级：显式报因（禁静默空转） ════════════════


class TestSustainedDegradationExplicitFailure:
    def test_sustained_429_fails_visible_bounded(self, fail_db):
        """持续429：MAX_ATTEMPTS次有界重试后显式"[LLM错误"报因（不静默）"""
        srv = _Server([
            {"kind": "status", "status": 429, "body": "rate limited",
             "headers": {"retry-after-ms": "30"}},
        ])
        t0 = time.monotonic()
        try:
            eng = _engine(srv.port)
            out = asyncio.run(_collect(eng))
        finally:
            srv.close()
        elapsed = time.monotonic() - t0
        joined = "".join(out)
        assert "[LLM错误" in joined       # 显式报因，失败必须可见
        assert "429" in joined
        assert srv.n_calls == MAX_ATTEMPTS  # 5发不无限，到点放弃
        assert elapsed < 30.0            # 有界时间（wait均0.03s级）
        rows = _db_rows(fail_db)
        gives = [r for r in rows if r[2] == "llm_give_up"]
        assert gives and gives[-1][1] == "rate_limit"
        assert gives[-1][4] == "failed"
        retries = [r for r in rows if r[2] == "llm_retry"]
        assert len(retries) == MAX_ATTEMPTS - 1  # 每次重试都有分类记账

    def test_budget_never_infinite(self):
        """重试永远有界（kilocode limit语义保留）"""
        assert 3 <= MAX_ATTEMPTS <= 10


# ════════════════ 3. Retry-After 硬上限（禁静默空转） ════════════════


class TestRetryAfterBounded:
    def test_huge_retry_after_does_not_sleep_forever(self, fail_db, monkeypatch):
        """上游 Retry-After: 3600 → 等待截到硬上限（0.2s注入），回合照常完成"""
        monkeypatch.setenv("SOULMATE_LLM_RETRY_MAX_WAIT_S", "0.2")
        srv = _Server([
            {"kind": "status", "status": 429, "body": "rate limited",
             "headers": {"retry-after": "3600"}},
            {"kind": "sse"},
        ])
        t0 = time.monotonic()
        try:
            eng = _engine(srv.port)
            out = asyncio.run(_collect(eng))
        finally:
            srv.close()
        elapsed = time.monotonic() - t0
        joined = "".join(out)
        assert "hello" in joined
        # 10.1修复前：这里会 asyncio.sleep(3600) 静默空转一小时
        assert elapsed < 5.0

    def test_backoff_hard_bound_default(self):
        """缺省60s硬上限：有头超上限截到上限（kilocode原i32≈24.8天已收口）"""
        assert max_wait_ms() == RETRY_MAX_WAIT_MS
        assert backoff_ms(1, {"retry-after": "3600"}) == RETRY_MAX_WAIT_MS
        assert backoff_ms(1, {"retry-after-ms": "99999999999"}) == RETRY_MAX_WAIT_MS
        # ≤上限的头值原样生效（有头听头语义保留）
        assert backoff_ms(1, {"retry-after": "30"}) == 30000.0
        assert backoff_ms(1, {"retry-after-ms": "250"}) == 250.0

    def test_backoff_env_override(self, monkeypatch):
        monkeypatch.setenv("SOULMATE_LLM_RETRY_MAX_WAIT_S", "0.25")
        assert max_wait_ms() == 250.0
        assert backoff_ms(1, {"retry-after": "3600"}) == 250.0


# ════════════════ 4. 传输故障分类（tool_failures.db 可观测） ════════════════


class TestClassifyFailure:
    def test_status_kinds(self):
        assert classify_failure(429) == "rate_limit"
        assert classify_failure(408) == "timeout_status"
        assert classify_failure(503) == "server_error"
        assert classify_failure(500) == "server_error"
        assert classify_failure(401) == "client_error"
        assert classify_failure(404) == "client_error"

    def test_exception_kinds(self):
        assert classify_failure(httpx.ReadTimeout("t")) == "read_timeout"
        assert classify_failure(httpx.ConnectError("c")) == "connect_error"
        assert classify_failure(
            httpx.RemoteProtocolError("peer closed connection without sending complete message body")
        ) == "protocol_error"
        assert classify_failure(httpx.WriteTimeout("w")) == "network_error"
        assert classify_failure(RuntimeError("socket hang up")) == "transient_message"
        assert classify_failure(ValueError("boom")) == "unknown"
        assert classify_failure(True) == "unknown"

    def test_record_writes_classified_row(self, fail_db):
        record_llm_failure("chat", 429, "llm_give_up", 5, "failed", "LLM HTTP 429")
        rows = _db_rows(fail_db)
        assert rows == [("llm:chat", "rate_limit", "llm_give_up", 5, "failed")]

    def test_record_never_raises_without_db(self, fail_db):
        """旁路纪律：记账失败绝不阻塞LLM链路"""
        monkeypatch = pytest.MonkeyPatch()
        try:
            monkeypatch.setattr(llm_engine, "_llm_fail_log", _Boom(), raising=False)
            record_llm_failure("chat", 503, "llm_retry", 1, "retry_scheduled")  # 不抛
        finally:
            monkeypatch.undo()


class _Boom:
    def record(self, *a, **kw):
        raise RuntimeError("db down")


# ════════════════ 5. 接线钉 ════════════════


class TestWiring:
    def test_three_paths_record(self):
        src = open("agent/llm_engine.py", encoding="utf-8").read()
        assert src.count('record_llm_failure("chat_stream"') >= 4   # 重试+放弃双接
        assert src.count('record_llm_failure("chat_stream_with_tools"') >= 5
        assert src.count('record_llm_failure("chat"') >= 4

    def test_waits_hard_bounded_single_source(self):
        src = open("utils/llm_retry.py", encoding="utf-8").read()
        # 所有实际等待出口都过 max_wait_ms 上限（禁静默空转单一真源）
        assert src.count("cap = max_wait_ms()") == 1
        assert "min(_cap_ms(parsed), cap)" in src

    def test_chat_stream_path_jitter_also_completes(self, fail_db):
        """chat_stream（另一条真实路径）同款抖动韧性"""
        srv = _Server([
            {"kind": "status", "status": 429, "body": "rl",
             "headers": {"retry-after-ms": "20"}},
            {"kind": "sse"},
        ])
        try:
            eng = _engine(srv.port)
            parts = []

            async def _run():
                async for c in eng.chat_stream([{"role": "user", "content": "hi"}]):
                    parts.append(c)

            asyncio.run(asyncio.wait_for(_run(), 30))
        finally:
            srv.close()
        assert "hello" in "".join(parts)
        assert srv.n_calls == 2
