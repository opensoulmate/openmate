"""session收口（04:20轮遗留#2销账：session.status不再恒running）——acp-proxy侧。

三层语义中的①显式收口（实时）接线钉 + recorder.end_session公共面：
- ws_acp连接teardown（最终清理块）在traj_relay.close()（drain完回合事件）与
  子进程死透之后调traj_recorder.end_session(_end_sid)——顺序纪律钉死；
- TrajectoryRecorder.end_session：POST /api/trajectory/sessions/{sid}/end?status=…，
  404=正常no-op不计熔断（allow_404），never-raise。

opensoul侧（②闲置清扫③自愈重开）的真行为测试在
opensoul/tests/test_trajectory_session_lifecycle.py。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent.trajectory_recorder import TrajectoryRecorder

WS_ACP_SRC = Path(__file__).parent.parent / "ws_acp.py"


# ── 1. recorder.end_session 公共面 ────────────────────────────


class FakePoster:
    def __init__(self, ret=None, raise_exc=None):
        self.calls = []
        self._ret = ret
        self._raise = raise_exc

    async def __call__(self, path, payload, **kwargs):
        self.calls.append((path, payload, kwargs))
        if self._raise is not None:
            raise self._raise
        return self._ret


@pytest.mark.asyncio
async def test_end_session_posts_end_endpoint():
    fake = FakePoster(ret={"ended": True, "session_id": "s1", "status": "completed"})
    rec = TrajectoryRecorder()
    rec._post = fake
    ok = await rec.end_session("s1")
    assert ok is True
    assert len(fake.calls) == 1
    path, payload, kwargs = fake.calls[0]
    assert path == "/api/trajectory/sessions/s1/end?status=completed"
    assert payload == {}
    assert kwargs.get("allow_404") is True, "404必须走allow_404不计熔断"


@pytest.mark.asyncio
async def test_end_session_custom_status_in_query():
    fake = FakePoster(ret={"ended": True})
    rec = TrajectoryRecorder()
    rec._post = fake
    await rec.end_session("s2", status="error")
    assert fake.calls[0][0] == "/api/trajectory/sessions/s2/end?status=error"


@pytest.mark.asyncio
async def test_end_session_empty_sid_is_noop():
    fake = FakePoster(ret={"ended": True})
    rec = TrajectoryRecorder()
    rec._post = fake
    assert await rec.end_session("") is False
    assert fake.calls == [], "空sid不得发请求"


@pytest.mark.asyncio
async def test_end_session_not_found_returns_false():
    fake = FakePoster(ret=None)  # 404→None（allow_404路径）
    rec = TrajectoryRecorder()
    rec._post = fake
    assert await rec.end_session("ghost") is False


@pytest.mark.asyncio
async def test_end_session_never_raises():
    fake = FakePoster(raise_exc=RuntimeError("boom"))
    rec = TrajectoryRecorder()
    rec._post = fake
    assert await rec.end_session("s3") is False  # fail-safe兜底


# ── 2. _post allow_404：404不计熔断失败 ────────────────────────


class _FakeResp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text

    def json(self):
        return {}


class _FakeAsyncClient:
    """替换 httpx.AsyncClient 的最小替身（404场景）。"""

    status_code = 404

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        return _FakeResp(self.status_code)


@pytest.mark.asyncio
async def test_post_allow_404_does_not_trip_breaker(monkeypatch):
    import agent.trajectory_recorder as tr

    monkeypatch.setattr(tr.httpx, "AsyncClient", _FakeAsyncClient)
    rec = TrajectoryRecorder()
    assert await rec._post("/x", {}, allow_404=True) is None
    assert rec._consecutive_failures == 0, "404（allow_404）不得计熔断失败"
    assert not rec.is_paused, "404（allow_404）不得把合法写入打进冷却窗"


@pytest.mark.asyncio
async def test_post_404_without_allow_still_counts(monkeypatch):
    import agent.trajectory_recorder as tr

    monkeypatch.setattr(tr.httpx, "AsyncClient", _FakeAsyncClient)
    rec = TrajectoryRecorder()
    assert await rec._post("/x", {}) is None
    assert rec._consecutive_failures == 1, "普通404照旧计入失败风暴熔断"


# ── 3. ws_acp接线钉（写了≠接线了） ────────────────────────────


def test_ws_acp_session_end_wiring_pins():
    src = WS_ACP_SRC.read_text(encoding="utf-8")
    # 构造：session收口recorder（soulmate路由也用）
    assert re.search(r"traj_recorder\s*=\s*TrajectoryRecorder\(\)", src)
    # relay观察者共享同一recorder实例
    assert re.search(r"recorder=traj_recorder", src)
    # 调用点（调用语法而非注释提及）+ sid非空守卫
    assert re.search(r"_end_sid\s*=\s*acp_state\.get\(\"acp_sid\"\)\s*or\s*\"\"", src)
    assert re.search(r"traj_recorder\.end_session\(_end_sid, status=\"completed\"\)", src)


def test_ws_acp_session_end_happens_after_drain_and_proc_death():
    """顺序纪律：先traj_relay.close()（drain回合事件）+proc死透，后收口session。
    反了的话add_event自愈重开会把收口的session翻回running。"""
    src = WS_ACP_SRC.read_text(encoding="utf-8")
    drain_pos = src.index("await traj_relay.close()")
    end_pos = src.index("traj_recorder.end_session")
    proc_pos = src.index("await asyncio.wait_for(proc.wait(), timeout=5)")
    ws_close_pos = src.index('client_ws.close(code=1000, reason="session ended")')
    assert drain_pos < end_pos, "必须在relay队列drain之后收口"
    assert proc_pos < end_pos, "必须在子进程死透之后收口（soulmate写入方不再有在途事件）"
    assert end_pos < ws_close_pos, "收口必须在WebSocket关闭前完成（客户端感知close时已收口）"


def test_ws_acp_session_end_in_final_cleanup_block():
    """收口调用必须在最终清理块（restart循环之外），不是每次子进程重启都收口。"""
    src = WS_ACP_SRC.read_text(encoding="utf-8")
    cleanup_pos = src.index("# 最终清理")
    end_pos = src.index("traj_recorder.end_session")
    loop_break_pos = src.index('logger.error(f"[ACP] restart loop error: {e}")')
    assert cleanup_pos < end_pos
    assert loop_break_pos < cleanup_pos, "最终清理在restart循环break之后（结构钉）"


def test_ws_acp_session_end_never_blocks_teardown():
    """收口失败只debug日志（never-raise包try/except），不得阻塞清理路径。"""
    src = WS_ACP_SRC.read_text(encoding="utf-8")
    end_pos = src.index("traj_recorder.end_session")
    window = src[end_pos : end_pos + 500]
    assert "except Exception" in window, "end_session调用必须有try/except兜底"
