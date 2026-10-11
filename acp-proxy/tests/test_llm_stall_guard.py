# -*- coding: utf-8 -*-
"""
12.4① LLM 传输无流停顿检测（utils.stall_guard，周期#69）——注入故障真实验收。

事故形态逐字对齐（2026-10-11 10:19 journal + tool_failures.db llm:chat|protocol_error）：
上游响应头已到后**长时间零字节**（挂死约285s），httpx read=120 超时未开火
（websockets serve 循环超时盲区），对端 FIN 才见错、重试一发 8s 恢复。

验收标准（路线图 12.4①）：
1. 等响应头停顿 → 有界中止本次尝试（不等对端），上层重试恢复 → 调用完成
2. 响应体停顿（头已到、body 零字节） → 同上，报因文案区分"未收到响应头/响应体无新数据"
3. 细碎涓流（间隙 < stall）→ **不误杀**，照常收完整 body（零回归关键负控制）
4. 涓流永不停但间隙都小 → 总时长兜底（attempt deadline）有界中止
5. 错误契约零变化：httpx.ReadTimeout（TransportError 家族）→
   classify_failure=read_timeout / is_transient_error=True，既有重试环原样接住
6. 正常响应契约逐字一致（status/headers/text/json 全可用）
7. 接线护栏：llm_engine.chat() 与 llm_transport._post_single_link 真实走守卫
8. SOULMATE_LLM_STALL_GUARD=off 旁路回原行为
"""
import asyncio
import gzip
import http.server
import inspect
import json
import threading
import time

import httpx
import pytest

import agent.llm_engine as llm_engine
from agent.llm_engine import LLMEngine
from utils import llm_transport
from utils.llm_retry import classify_failure, is_transient_error
from utils.stall_guard import (
    DEFAULT_ATTEMPT_DEADLINE_S,
    DEFAULT_STALL_TIMEOUT_S,
    attempt_deadline_s,
    send_with_stall_guard,
    stall_guard_enabled,
    stall_timeout_s,
)

OK_JSON = {"choices": [{"message": {"content": "ok-resp"}, "finish_reason": "stop"}]}
OK_BODY = json.dumps(OK_JSON).encode()


def _make_handler(state):
    class StallHandler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # 静音
            pass

        def _pick(self):
            with state["lock"]:
                idx = min(state["i"], len(state["script"]) - 1)
                state["i"] += 1
                state["calls"].append(idx)
                return dict(state["script"][idx])

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            spec = self._pick()
            kind = spec["kind"]
            try:
                if kind == "ok":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(OK_BODY)))
                    self.end_headers()
                    self.wfile.write(OK_BODY)
                elif kind == "gzip_ok":
                    # 真实生产形态：MiFE/token-plan 回 content-encoding: gzip
                    gz = gzip.compress(OK_BODY)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Encoding", "gzip")
                    self.send_header("Content-Length", str(len(gz)))
                    self.end_headers()
                    self.wfile.write(gz)
                elif kind == "slow_ok":
                    # 延迟后回完整响应（合法慢 TTFB，不该误杀）
                    time.sleep(spec.get("secs", 0.3))
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(OK_BODY)))
                    self.end_headers()
                    self.wfile.write(OK_BODY)
                elif kind == "error500":
                    body = b"LLM HTTP 500 detail"
                    self.send_response(500)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif kind == "hang_head":
                    # 响应头之前长时间零字节（事故形态A：等响应头挂死）
                    time.sleep(spec.get("secs", 5.0))
                    self.close_connection = True
                elif kind == "hang_body":
                    # 头已到、body 零字节（事故形态B：incomplete body 挂死）
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "100")
                    self.end_headers()
                    self.wfile.write(b"{}")
                    self.wfile.flush()
                    time.sleep(spec.get("secs", 5.0))
                    self.close_connection = True
                elif kind == "trickle":
                    # 细碎涓流：n 字节、每 gap 秒 1 字节（间隙 < stall 不该误杀）
                    n = spec.get("n", 10)
                    gap = spec.get("gap", 0.1)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(n))
                    self.end_headers()
                    for _i in range(n):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(gap)
                elif kind == "trickle_forever":
                    # 永久涓流：间隙都小但从不收口（总时长兜底的用例）
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    for _i in range(spec.get("n", 200)):
                        self.wfile.write(b"x")
                        self.wfile.flush()
                        time.sleep(spec.get("gap", 0.15))
            except (BrokenPipeError, ConnectionResetError, OSError):
                # 对端中止读取（守卫开火）属预期路径，静默
                self.close_connection = True

    return StallHandler


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
    monkeypatch.setenv("SOULMATE_LLM_ATTEMPT_DEADLINE_S", "8")
    monkeypatch.setenv("SOULMATE_LLM_RETRY_MAX_WAIT_S", "0.1")
    monkeypatch.setenv("TOOL_FAILURE_DB_PATH", str(tmp_path / "tf.db"))
    old = llm_engine._llm_fail_log
    llm_engine._llm_fail_log = None
    yield
    llm_engine._llm_fail_log = old


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


# ══════════════ 1/2. 两类停顿形态：有界中止 + 重试恢复 ══════════════

class TestStallAbortAndRecover:
    def test_head_stall_aborts_fast_and_retry_recovers(self, fast_env):
        """等响应头挂死：不等对端（5s），stall 0.5s 中止→重试→完成"""
        srv = _Server([
            {"kind": "hang_head", "secs": 5.0},
            {"kind": "ok"},
        ])
        try:
            async def go():
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{srv.port}", timeout=httpx.Timeout(120)
                ) as client:
                    return await llm_transport.post_chat_completion(
                        client, base_url=f"http://127.0.0.1:{srv.port}",
                        headers={}, model="m", messages=[{"role": "user", "content": "hi"}],
                        temperature=0.1, max_tokens=16, strand_id="test",
                    )
            t0 = time.monotonic()
            data = _run(go())
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        assert data["choices"][0]["message"]["content"] == "ok-resp"
        assert elapsed < 3.0, f"停顿未被有界中止: {elapsed:.1f}s"
        assert srv.n_calls >= 2  # 首发被中止 + 重试真实发出

    def test_body_stall_aborts_with_body_wording(self, fast_env):
        """响应体停顿（头已到）：报因文案区分「响应体无新数据」"""
        srv = _Server([{"kind": "hang_body", "secs": 5.0}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1},
                    )
            t0 = time.monotonic()
            with pytest.raises(httpx.ReadTimeout) as ei:
                _run(go())
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        assert "响应体无新数据" in str(ei.value)
        assert elapsed < 3.0

    def test_head_stall_wording(self, fast_env):
        srv = _Server([{"kind": "hang_head", "secs": 5.0}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1},
                    )
            with pytest.raises(httpx.ReadTimeout) as ei:
                _run(go())
        finally:
            srv.close()
        assert "未收到响应头" in str(ei.value)

    def test_chat_e2e_incident_path_recovers(self, fast_env):
        """真实事故路径 llm_engine.chat()：停顿→重试→完成（e2e）"""
        srv = _Server([
            {"kind": "hang_head", "secs": 5.0},
            {"kind": "ok"},
        ])
        try:
            eng = LLMEngine(api_key="sk-test", base_url=f"http://127.0.0.1:{srv.port}/v1",
                            model="m-test")
            t0 = time.monotonic()
            out = _run(eng.chat([{"role": "user", "content": "hi"}]))
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        assert out == "ok-resp"
        assert elapsed < 3.0, f"chat() 停顿未被有界中止: {elapsed:.1f}s"


# ══════════════ 3. 零回归负控制：慢/涓流响应不误杀 ══════════════

class TestNoFalseAbort:
    def test_trickle_within_stall_completes(self, fast_env):
        """间隙 0.1s < stall 0.5s 的涓流：照常收完整 body，零误杀"""
        srv = _Server([{"kind": "trickle", "n": 10, "gap": 0.1}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1},
                    )
            resp = _run(go())
        finally:
            srv.close()
        assert resp.status_code == 200
        assert resp.content == b"x" * 10

    def test_slow_ttfb_within_stall_completes(self, fast_env):
        """TTFB 0.3s < stall 2.0s 的合法慢响应：照常完成（不误杀慢生成）"""
        srv = _Server([{"kind": "slow_ok", "secs": 0.3}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1}, stall_s=2.0,
                    )
            resp = _run(go())
        finally:
            srv.close()
        assert resp.json() == OK_JSON


# ══════════════ 4. 总时长兜底：涓流假进展有界 ══════════════

class TestAttemptDeadline:
    def test_deadline_caps_endless_trickle(self, fast_env):
        """间隙 0.15s < stall 0.5s 但从不收口：deadline 1s 兜底中止"""
        srv = _Server([{"kind": "trickle_forever", "n": 200, "gap": 0.15}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1}, deadline_s=1.0,
                    )
            t0 = time.monotonic()
            with pytest.raises(httpx.ReadTimeout) as ei:
                _run(go())
            elapsed = time.monotonic() - t0
        finally:
            srv.close()
        assert "总时长超上限" in str(ei.value)
        assert elapsed < 3.5


# ══════════════ 5. 错误契约零变化 ══════════════

class TestErrorContract:
    def test_stall_error_is_transient_read_timeout(self, fast_env):
        srv = _Server([{"kind": "hang_head", "secs": 5.0}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1},
                    )
            with pytest.raises(httpx.ReadTimeout) as ei:
                _run(go())
        finally:
            srv.close()
        err = ei.value
        assert isinstance(err, httpx.TransportError)   # 既有 except 原样接住
        assert is_transient_error(err) is True
        assert classify_failure(err) == "read_timeout"


# ══════════════ 6. 正常响应契约逐字一致 ══════════════

class TestResponseContract:
    def test_ok_response_json_identical(self, fast_env):
        srv = _Server([{"kind": "ok"}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1},
                    )
            resp = _run(go())
        finally:
            srv.close()
        assert resp.status_code == 200
        assert resp.json() == OK_JSON
        assert resp.content == OK_BODY
        assert "application/json" in resp.headers.get("content-type", "")

    def test_gzip_body_not_double_decoded(self, fast_env):
        """content-encoding: gzip 响应重建后正常解码（真实生产缺陷回归钉）：

        2026-10-11 16:09 journal 实证：send_with_stall_guard 收集 aiter_bytes 已解码
        字节却保留 gzip 头重建 Response → 二次解码 DecodingError "incorrect header
        check"，真实 chat() 全部报 "[LLM错误: Error -3 while decompressing...]"。
        修复=收集 aiter_raw 原始字节与头一致。纯文本假服务器此前未覆盖此形态。
        """
        srv = _Server([{"kind": "gzip_ok"}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1},
                    )
            resp = _run(go())
        finally:
            srv.close()
        assert resp.json() == OK_JSON  # 二次解码会在构造时直接抛 DecodingError
        assert resp.text == OK_BODY.decode()
        assert resp.content == OK_BODY

    def test_error_status_body_preserved(self, fast_env):
        """非 200 响应的 resp.text 保留（状态重试/失败契约要读 body 前 200 字）"""
        srv = _Server([{"kind": "error500"}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1},
                    )
            resp = _run(go())
        finally:
            srv.close()
        assert resp.status_code == 500
        assert resp.text == "LLM HTTP 500 detail"


# ══════════════ 7. 接线护栏 ══════════════

class TestWiring:
    def test_chat_uses_guard(self):
        src = inspect.getsource(LLMEngine.chat)
        assert "send_with_stall_guard" in src

    def test_llm_transport_uses_guard(self):
        src = inspect.getsource(llm_transport._post_single_link)
        assert "send_with_stall_guard" in src

    def test_streaming_paths_wired(self):
        """13.1（周期#77 另案）：流式路径已接同源守卫——此断言随另案更新（原钉"不动"）"""
        s1 = inspect.getsource(LLMEngine.chat_stream)
        s2 = inspect.getsource(LLMEngine.chat_stream_with_tools)
        assert "aiter_with_stall_guard" in s1
        assert "aiter_with_stall_guard" in s2
        assert "open_stream_with_stall_guard" in s1
        assert "open_stream_with_stall_guard" in s2


# ══════════════ 8. 配置与旁路 ══════════════

class TestConfig:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("SOULMATE_LLM_STALL_TIMEOUT_S", raising=False)
        monkeypatch.delenv("SOULMATE_LLM_ATTEMPT_DEADLINE_S", raising=False)
        assert stall_timeout_s() == DEFAULT_STALL_TIMEOUT_S == 60.0
        assert attempt_deadline_s() == DEFAULT_ATTEMPT_DEADLINE_S == 240.0

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("SOULMATE_LLM_STALL_TIMEOUT_S", "7.5")
        monkeypatch.setenv("SOULMATE_LLM_ATTEMPT_DEADLINE_S", "33")
        assert stall_timeout_s() == 7.5
        assert attempt_deadline_s() == 33.0

    def test_guard_disabled_bypasses(self, fast_env, monkeypatch):
        """off 旁路：stall 极小也不中止（走 client.request 原行为）"""
        monkeypatch.setenv("SOULMATE_LLM_STALL_GUARD", "off")
        assert stall_guard_enabled() is False
        srv = _Server([{"kind": "trickle", "n": 5, "gap": 0.1}])
        try:
            async def go():
                async with httpx.AsyncClient(timeout=httpx.Timeout(120)) as client:
                    return await send_with_stall_guard(
                        client, "POST", f"http://127.0.0.1:{srv.port}/v1/chat/completions",
                        json={"x": 1}, stall_s=0.05,
                    )
            resp = _run(go())
        finally:
            srv.close()
        assert resp.content == b"x" * 5  # 0.05s 阈值下涓流若走守卫必被中止——旁路实证
