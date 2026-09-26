# -*- coding: utf-8 -*-
"""WakeupExecutor 活动流（snapshot().recent）测试——monitoring面板"tail日志流"数据面。

本轮改进：executor的可观测面此前只有聚合计数器（polls/claimed/fire_ok...），
"它刚才干了什么"要翻journal日志；snapshot().recent 环形缓冲（容量20，最新在前）
把每个 claim/fire/ack 动作留成结构化记录，monitoring页Wakeup面板直接渲染。

断言面：
- 每类动作（claimed/claim_lost/claim_error/fire_ok/fire_failed/ack_error/poll_error）
  都留痕，且最新在前；
- 环形缓冲有界（20）防增长；
- snapshot() 可JSON序列化（真HTTP端点 /api/agent/wakeup-executor 的返回体）。
无网络依赖：httpx.AsyncClient整体替身 + fire_fn注入（与test_wakeup_executor.py同款）。
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.wakeup_executor import WakeupExecutor


def run(coro):
    return asyncio.run(coro)


EV = {
    "event_id": 7,
    "agent_id": "soulmate",
    "session_id": "om-obs1",
    "reason": "检查CI是否转绿",
    "noop": False,
    "streak": 0,
}


class FakeResp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class FakeHttp:
    def __init__(self, due=None, claim_status=200, ack_status=200, get_raises=None):
        self.due = list(due or [])
        self.claim_status = claim_status
        self.ack_status = ack_status
        self.get_raises = get_raises

    def factory(self, **kw):
        return FakeClient(self)


class FakeClient:
    def __init__(self, server: FakeHttp):
        self.s = server

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        if self.s.get_raises is not None:
            raise self.s.get_raises
        return FakeResp(200, {"count": len(self.s.due), "due": self.s.due})

    async def post(self, url, json=None, **kw):
        if url.endswith("/wakeup/claim"):
            return FakeResp(self.s.claim_status, {"claimed": True, "event": json})
        if url.endswith("/wakeup/fire-result"):
            return FakeResp(self.s.ack_status, {"state": "fired"})
        raise AssertionError(f"unexpected POST {url}")


def _executor(server, fire_fn, monkeypatch, **kw):
    monkeypatch.setattr(
        "agent.wakeup_executor.httpx.AsyncClient", server.factory, raising=True
    )
    return WakeupExecutor(
        opensoul_url="http://opensoul.test",
        poll_interval_s=0.01,
        fire_fn=fire_fn,
        request_timeout_s=1.0,
        fire_timeout_s=1.0,
        **kw,
    )


async def _ok_fire(ev):
    return {"stop_reason": "end_turn"}


class TestRecentActivity:
    def test_snapshot_has_recent_key_initially_empty(self, monkeypatch):
        ex = _executor(FakeHttp(), _ok_fire, monkeypatch)
        snap = ex.snapshot()
        assert snap["recent"] == []
        json.dumps(snap)  # 真端点返回体必须可序列化

    def test_fire_ok_records_claimed_then_fire_ok_newest_first(self, monkeypatch):
        server = FakeHttp(due=[EV])
        ex = _executor(server, _ok_fire, monkeypatch)
        assert run(ex.poll_once())["fired"] == 1
        recent = ex.snapshot()["recent"]
        # 最新在前：fire_ok 在 claimed 之前
        assert [a["kind"] for a in recent] == ["fire_ok", "claimed"]
        fire_entry, claim_entry = recent[0], recent[1]
        assert fire_entry["event_id"] == 7 and "om-obs1" in fire_entry["detail"]
        assert claim_entry["event_id"] == 7 and "检查CI是否转绿" in claim_entry["detail"]
        for a in recent:
            assert isinstance(a["ts"], float) and a["ts"] > 0

    def test_fire_failure_records_fire_failed_with_error(self, monkeypatch):
        async def _bad_fire(ev):
            raise RuntimeError("唤醒回合LLM级失败: [LLM错误: 402]")

        server = FakeHttp(due=[EV])
        ex = _executor(server, _bad_fire, monkeypatch)
        assert run(ex.poll_once())["fired"] == 0
        recent = ex.snapshot()["recent"]
        assert recent[0]["kind"] == "fire_failed"
        assert "LLM级失败" in recent[0]["detail"]
        assert recent[1]["kind"] == "claimed"

    def test_claim_lost_409_recorded_and_no_fire(self, monkeypatch):
        async def _must_not_run(ev):
            raise AssertionError("claim败北后绝不fire")

        server = FakeHttp(due=[EV], claim_status=409)
        ex = _executor(server, _must_not_run, monkeypatch)
        run(ex.poll_once())
        recent = ex.snapshot()["recent"]
        assert [a["kind"] for a in recent] == ["claim_lost"]
        assert recent[0]["event_id"] == 7

    def test_claim_http_error_recorded(self, monkeypatch):
        server = FakeHttp(due=[EV], claim_status=500)
        ex = _executor(server, _ok_fire, monkeypatch)
        run(ex.poll_once())
        recent = ex.snapshot()["recent"]
        assert recent[0]["kind"] == "claim_error"
        assert "HTTP 500" in recent[0]["detail"]

    def test_ack_error_recorded_after_fire(self, monkeypatch):
        server = FakeHttp(due=[EV], ack_status=500)
        ex = _executor(server, _ok_fire, monkeypatch)
        run(ex.poll_once())
        kinds = [a["kind"] for a in ex.snapshot()["recent"]]
        # 真实时序（最新在前）：ack失败发生在fire计数留痕之前→fire_ok比ack_error新
        assert kinds[:2] == ["fire_ok", "ack_error"]
        assert "claimed" in kinds

    def test_poll_error_recorded_in_loop(self, monkeypatch):
        server = FakeHttp(get_raises=RuntimeError("ConnectError: refused"))

        async def _go():
            ex = _executor(server, _ok_fire, monkeypatch)
            await ex.start()
            await asyncio.sleep(0.08)
            await ex.stop()
            return ex.snapshot()

        snap = run(_go())
        kinds = [a["kind"] for a in snap["recent"]]
        assert "poll_error" in kinds
        entry = next(a for a in snap["recent"] if a["kind"] == "poll_error")
        assert "refused" in entry["detail"]

    def test_ring_buffer_bounded_at_20_newest_first(self, monkeypatch):
        async def _many():
            server = FakeHttp(due=[dict(EV, event_id=i) for i in range(30)])
            ex = _executor(server, _ok_fire, monkeypatch)
            await ex.poll_once()
            return ex.snapshot()

        snap = run(_many())
        recent = snap["recent"]
        assert len(recent) == 20  # 60条动作（30claimed+30fire_ok）截到20
        # 最新在前：30个事件按顺序fire，最后fire的是id=29
        assert recent[0]["kind"] == "fire_ok" and recent[0]["event_id"] == 29
        assert all(
            recent[i]["ts"] >= recent[i + 1]["ts"] for i in range(len(recent) - 1)
        )
