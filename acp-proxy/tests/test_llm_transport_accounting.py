# -*- coding: utf-8 -*-
"""
11.3 LLM 故障分类记账补链 —— utils/llm_transport → dna/gateway 调用路径（路线图阶段11）

10.1 已给 agent.llm_engine 三路（chat/chat_stream/chat_stream_with_tools）接了
record_llm_failure（tool_failures.db 九态分类）；但 utils/llm_transport（进化引擎
dna_evolution._call_llm 与 llm_gateway 的公共传输收口）此前零记账——进化引擎的
LLM 调用失败不可审计。本测试钉住补链：

1. 状态码失败（429/503）→ llm_retry/llm_give_up 行 + 九态分类（rate_limit/server_error）
2. 传输层失败（incomplete chunked read 实证形态）→ protocol_error 分类行
3. 降级链换 link 成功 → 上游失败仍留审计行（可审计≠只记最终失败）
4. HTTP 200 空输出（dna "静默返空"历史形态）→ 可见记账行
5. 旁路纪律：记账器炸了绝不阻塞 LLM 链路
6. 接线钉：path 归因（llm:dna / llm:gateway）在真实调用点

真实本地 HTTP 服务器注入故障（非 mock），走 utils.llm_transport 真实 httpx 传输。
"""
import asyncio
import http.server
import json
import sqlite3
import threading

import httpx
import pytest

import agent.llm_engine as llm_engine
from utils import network_state
from utils.llm_errors import LLMCallError
from utils.llm_transport import complete_with_protection, post_chat_completion
from utils.model_fallback import FallbackChain, FallbackLink


# ── 故障注入HTTP服务器（真实socket，非mock） ─────────────────────────────


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
                entry = state["script"][idx]
            kind = entry[0]
            if kind == "ok":
                body = json.dumps({
                    "choices": [
                        {"message": {"content": entry[1]}, "finish_reason": "stop"}
                    ]
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif kind == "status":
                code = entry[1]
                headers = entry[2] if len(entry) > 2 else {}
                body = b"err"
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif kind == "close":
                # incomplete chunked read（周期#54 实证形态）：半截chunked响应后断连
                # ⚠️ 必须置 close_connection=True，否则 socketserver 复用连接读下一请求、
                # FIN 不达客户端（实测变 ReadTimeout 而非 RemoteProtocolError）
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                self.wfile.write(b"5\r\nhello")  # 无终止chunk
                self.wfile.flush()
                self.connection.close()
                self.close_connection = True
            else:
                raise AssertionError(f"unknown entry {entry!r}")

    return FaultHandler


class _Server:
    def __init__(self, script):
        self.state = {"i": 0, "script": list(script), "lock": threading.Lock()}
        handler = _make_handler(self.state)
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fault_server():
    servers = []

    def make(script):
        srv = _Server(script)
        servers.append(srv)
        return srv

    yield make
    for srv in servers:
        srv.stop()


@pytest.fixture
def fail_db(tmp_path, monkeypatch):
    """tool_failures.db 指到临时库 + 重置lazy单例 + 重试等待压到50ms（快测）"""
    db = tmp_path / "tf.db"
    monkeypatch.setenv("TOOL_FAILURE_DB_PATH", str(db))
    monkeypatch.setenv("SOULMATE_LLM_RETRY_MAX_WAIT_S", "0.05")
    old = llm_engine._llm_fail_log
    llm_engine._llm_fail_log = None
    yield db
    llm_engine._llm_fail_log = old


@pytest.fixture
def no_park(monkeypatch):
    """传输错误不进离线park（park是10.1既有能力与本切片正交，测试保持确定性）"""

    async def _not_offline(exc, cancel_event=None):
        return network_state.NOT_OFFLINE

    monkeypatch.setattr(network_state, "maybe_park", _not_offline)


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(
            "SELECT tool_name, kind, action, attempt, outcome, error_first_line"
            " FROM tool_failures ORDER BY id"
        ).fetchall()
    finally:
        conn.close()


def _call(base_url, *, path="dna", strand_id="strand-x", chain=None, rounds=3):
    async def _run():
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
            return await complete_with_protection(
                client,
                base_url=base_url,
                headers={"Content-Type": "application/json"},
                model="m",
                messages=[{"role": "user", "content": "hi"}],
                temperature=0.0,
                max_tokens=64,
                rounds=rounds,
                strand_id=strand_id,
                chain=chain,
                path=path,
            )

    return asyncio.run(_run())


# ════════════════ 1. 状态码失败：九态分类入账 ════════════════


class TestStatusAccounting:
    def test_retry_scheduled_recorded_then_success(self, fault_server, fail_db):
        """429抖动→恢复：llm_retry 行（rate_limit）+ 调用成功"""
        srv = fault_server([
            ("status", 429, {"retry-after-ms": "10"}),
            ("ok", "hi"),
        ])
        assert _call(srv.base_url) == "hi"
        rows = _rows(fail_db)
        assert ("llm:dna", "rate_limit", "llm_retry", 1, "retry_scheduled") in [
            r[:5] for r in rows
        ]
        # strand 归因进 error 行
        assert any("strand-x" in (r[5] or "") for r in rows)

    def test_sustained_503_give_up_recorded(self, fault_server, fail_db):
        """持续503→显式失败，give_up 行（server_error）+ 重试行均在"""
        srv = fault_server([("status", 503)])
        with pytest.raises(LLMCallError):
            _call(srv.base_url)
        rows = [r[:5] for r in _rows(fail_db)]
        assert any(
            r[0] == "llm:dna" and r[1] == "server_error" and r[2] == "llm_give_up"
            and r[4] == "failed" for r in rows
        )
        assert any(r[2] == "llm_retry" and r[1] == "server_error" for r in rows)


# ════════════════ 2. 传输层失败：protocol_error 分类 ════════════════


class TestTransportErrorAccounting:
    def test_incomplete_chunked_read_classified(
        self, fault_server, fail_db, no_park
    ):
        """incomplete chunked read（周期#54实证形态）→ protocol_error 记账"""
        srv = fault_server([("close",)])
        with pytest.raises(LLMCallError):
            _call(srv.base_url)
        rows = [r[:5] for r in _rows(fail_db)]
        assert any(r[1] == "protocol_error" and r[2] == "llm_give_up" for r in rows)
        assert any(r[1] == "protocol_error" and r[2] == "llm_retry" for r in rows)


# ════════════════ 3. 降级链：上游失败仍留审计行 ════════════════


class TestFallbackChainAccounting:
    def test_upstream_failure_audited_even_when_backup_answers(
        self, fault_server, fail_db
    ):
        """primary 429 有备胎立即切换→备胎成功：调用成功且上游失败行仍在（可审计）"""
        primary = fault_server([("status", 429, {"retry-after-ms": "5"})])
        backup = fault_server([("ok", "from-backup")])
        chain = FallbackChain(
            links=[
                FallbackLink(
                    provider="backup", model="b", base_url=backup.base_url
                )
            ]
        )
        assert _call(primary.base_url, chain=chain) == "from-backup"
        rows = [r[:5] for r in _rows(fail_db)]
        assert any(
            r[0] == "llm:dna" and r[1] == "rate_limit" and r[2] == "llm_give_up"
            for r in rows
        ), f"上游失败未留审计行: {rows}"


# ════════════════ 4. 空输出（dna "静默返空"历史形态） ════════════════


class TestEmptyOutputAccounting:
    def test_empty_output_recorded(self, fault_server, fail_db):
        """HTTP 200 空内容→失败必须可见：记账行含'空内容'"""
        srv = fault_server([("ok", "")])
        with pytest.raises(LLMCallError) as ei:
            _call(srv.base_url)
        assert "空内容" in str(ei.value)
        rows = _rows(fail_db)
        assert any(
            r[0] == "llm:dna" and r[2] == "llm_give_up" and "空内容" in (r[5] or "")
            for r in rows
        ), rows


# ════════════════ 5. 旁路纪律 ════════════════


class _Boom:
    def record(self, *a, **kw):
        raise RuntimeError("db down")


class TestBypassDiscipline:
    def test_recorder_boom_never_blocks_llm_chain(self, fault_server, fail_db):
        """记账器炸了：调用契约不变（成功照常成功，失败照常原契约失败）"""
        llm_engine._llm_fail_log = _Boom()
        srv = fault_server([("ok", "still-works")])
        assert _call(srv.base_url) == "still-works"

        srv2 = fault_server([("status", 500)])
        with pytest.raises(LLMCallError):
            _call(srv2.base_url)


# ════════════════ 6. 接线钉 ════════════════


class TestWiring:
    def test_transport_records_at_retry_and_giveup(self):
        src = open("utils/llm_transport.py", encoding="utf-8").read()
        # 重试+放弃双接（状态/传输两分支 + 防护层两出口）
        assert src.count("_record_failure(") >= 5

    def test_callers_pass_path_attribution(self):
        dna = open("dna_evolution.py", encoding="utf-8").read()
        gw = open("utils/llm_gateway.py", encoding="utf-8").read()
        assert 'path="dna"' in dna          # 进化引擎路径归因 llm:dna
        assert 'path="gateway"' in gw       # 业务/调用面归因 llm:gateway

    def test_gateway_path_records_under_its_own_name(self, fault_server, fail_db):
        """llm_gateway 调用面走同一记账链，tool_name=llm:gateway"""
        srv = fault_server([("status", 429, {"retry-after-ms": "5"}), ("ok", "gw")])
        assert _call(srv.base_url, path="gateway") == "gw"
        rows = [r[:5] for r in _rows(fail_db)]
        assert any(r[0] == "llm:gateway" and r[1] == "rate_limit" for r in rows)
        assert not any(r[0] == "llm:dna" for r in rows)
