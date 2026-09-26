# -*- coding: utf-8 -*-
"""WakeupExecutor（ScheduleWakeup执行侧闭环）接线测试。

调研来源：claude-code SDK ScheduleWakeup——runtime在delay到点后**重新唤醒agent跑一轮**
（10-claude-code-source.md #4）。上一轮P0-4只落"登记意图+可观测"，执行侧缺口
（dev-reports遗留#1"下轮P1首选"）由本模块闭环：
due轮询 → opensoul claim CAS领取（at-most-once，双实例不双跑）→ 无客户端唤醒回合
→ fire-result回执（失败释放重试/上限EXHAUSTED）。

解决"写了≠接线了"：
- GET due / POST claim / POST fire-result 三段HTTP必须真实发出（FakeHttp捕获断言）
- fire失败/超时必须回执ok=False（释放重试），禁止假装成功
- claim CAS失败（409）绝不fire（对端实例已领——at-most-once的另一半）
- refusal（会话消失）必须转异常→失败回执，不给消失的会话造僵尸回合
无网络依赖：httpx.AsyncClient整体替身 + fire_fn注入。
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.wakeup_executor import (
    WakeupExecutor,
    build_wakeup_prompt,
    default_headless_fire,
)


def run(coro):
    return asyncio.run(coro)


EV = {
    "event_id": 7,
    "agent_id": "soulmate",
    "session_id": "om-test1",
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
    """opensoul API替身：due→claim→fire-result三段全捕获"""

    def __init__(self, due=None, claim_status=200, ack_status=200, get_raises=None):
        self.due = list(due or [])
        self.claim_status = claim_status
        self.ack_status = ack_status
        self.get_raises = get_raises
        self.get_urls = []
        self.claim_posts = []
        self.ack_posts = []

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
        self.s.get_urls.append(url)
        if self.s.get_raises is not None:
            raise self.s.get_raises
        return FakeResp(200, {"count": len(self.s.due), "due": self.s.due})

    async def post(self, url, json=None, **kw):
        if url.endswith("/wakeup/claim"):
            self.s.claim_posts.append((url, json))
            return FakeResp(self.s.claim_status, {"claimed": True, "event": json})
        if url.endswith("/wakeup/fire-result"):
            self.s.ack_posts.append((url, json))
            return FakeResp(self.s.ack_status, {"state": "fired"})
        raise AssertionError(f"unexpected POST {url}")


def _wire(monkeypatch, server):
    monkeypatch.setattr(
        "agent.wakeup_executor.httpx.AsyncClient", server.factory, raising=True
    )


def _executor(server, fire_fn, monkeypatch, **kw):
    _wire(monkeypatch, server)
    return WakeupExecutor(
        opensoul_url="http://opensoul.test",
        poll_interval_s=0.01,
        fire_fn=fire_fn,
        **kw,
    )


# ════════════════════════════════════════════════════════════════
# ① 主链路：due → claim → fire → ack(ok=True)
# ════════════════════════════════════════════════════════════════

class TestHappyPath:
    def test_fire_and_ack_ok(self, monkeypatch):
        server = FakeHttp(due=[EV])
        fired = []

        async def fire_fn(ev):
            fired.append(ev)

        ex = _executor(server, fire_fn, monkeypatch)
        res = run(ex.poll_once())
        assert res == {"due": 1, "fired": 1}
        assert fired == [EV]
        # claim真的POST了opensoul
        assert server.claim_posts[0][0] == "http://opensoul.test/api/will/wakeup/claim"
        assert server.claim_posts[0][1] == {"event_id": 7}
        # ack真的POST了且ok=True
        assert server.ack_posts[0][1] == {"event_id": 7, "ok": True, "error": ""}
        st = ex.snapshot()
        assert st["claimed"] == 1 and st["fire_ok"] == 1 and st["fire_failed"] == 0

    def test_due_endpoint_queried(self, monkeypatch):
        server = FakeHttp(due=[])

        async def fire_fn(ev):
            raise AssertionError("must not fire")

        ex = _executor(server, fire_fn, monkeypatch)
        run(ex.poll_once())
        assert server.get_urls == ["http://opensoul.test/api/will/wakeup/due"]


# ════════════════════════════════════════════════════════════════
# ② at-most-once：claim CAS失败绝不fire（对端实例已领）
# ════════════════════════════════════════════════════════════════

class TestClaimCas:
    def test_claim_lost_skips_fire_and_ack(self, monkeypatch):
        server = FakeHttp(due=[EV], claim_status=409)
        fired = []

        async def fire_fn(ev):
            fired.append(ev)

        ex = _executor(server, fire_fn, monkeypatch)
        res = run(ex.poll_once())
        assert res == {"due": 1, "fired": 0}
        assert fired == []
        assert server.ack_posts == []
        assert ex.snapshot()["claim_lost"] == 1

    def test_claim_http_error_counted_not_fired(self, monkeypatch):
        server = FakeHttp(due=[EV], claim_status=500)
        fired = []

        async def fire_fn(ev):
            fired.append(ev)

        ex = _executor(server, fire_fn, monkeypatch)
        run(ex.poll_once())
        assert fired == []
        st = ex.snapshot()
        assert st["claim_errors"] == 1 and st["claimed"] == 0


# ════════════════════════════════════════════════════════════════
# ③ 失败必须可见：fire失败/超时 → ack ok=False（释放重试）
# ════════════════════════════════════════════════════════════════

class TestFireFailure:
    def test_fire_exception_acks_not_ok(self, monkeypatch):
        server = FakeHttp(due=[EV])

        async def fire_fn(ev):
            raise RuntimeError("LLM炸了")

        ex = _executor(server, fire_fn, monkeypatch)
        res = run(ex.poll_once())
        assert res == {"due": 1, "fired": 0}
        ack = server.ack_posts[0][1]
        assert ack["event_id"] == 7 and ack["ok"] is False
        assert "RuntimeError: LLM炸了" in ack["error"]
        st = ex.snapshot()
        assert st["fire_failed"] == 1 and st["fire_ok"] == 0

    def test_fire_timeout_acks_not_ok(self, monkeypatch):
        server = FakeHttp(due=[EV])

        async def fire_fn(ev):
            await asyncio.sleep(1.0)

        ex = _executor(server, fire_fn, monkeypatch, fire_timeout_s=0.05)
        run(ex.poll_once())
        ack = server.ack_posts[0][1]
        assert ack["ok"] is False
        assert "fire timeout after 0.05s" in ack["error"]

    def test_ack_http_error_counted_but_fire_still_recorded(self, monkeypatch):
        server = FakeHttp(due=[EV], ack_status=500)

        async def fire_fn(ev):
            return None

        ex = _executor(server, fire_fn, monkeypatch)
        run(ex.poll_once())
        st = ex.snapshot()
        assert st["fire_ok"] == 1 and st["ack_errors"] == 1


# ════════════════════════════════════════════════════════════════
# ④ 默认执行体：refusal（会话消失）必须转异常，不造僵尸回合
# ════════════════════════════════════════════════════════════════

class FakeAgent:
    def __init__(self, stop_reason):
        self._stop_reason = stop_reason
        self.calls = []
        self.origin_log = []

    def set_turn_origin(self, session_id, origin):
        self.origin_log.append(("set", session_id, origin))

    def clear_turn_origin(self, session_id):
        self.origin_log.append(("clear", session_id))

    async def prompt(self, prompt, session_id, **kw):
        self.calls.append((prompt, session_id))
        return type("R", (), {"stop_reason": self._stop_reason})()


class TestDefaultFire:
    def test_refusal_raises(self, monkeypatch):
        agent = FakeAgent("refusal")
        monkeypatch.setattr(
            "agent.wakeup_executor._get_headless_agent", lambda: agent, raising=True
        )
        coro = default_headless_fire(EV)
        try:
            run(coro)
            raise AssertionError("refusal必须抛异常")
        except RuntimeError as e:
            assert "拒绝给消失的会话造回合" in str(e)

    def test_success_returns_and_targets_session(self, monkeypatch):
        agent = FakeAgent("end_turn")
        monkeypatch.setattr(
            "agent.wakeup_executor._get_headless_agent", lambda: agent, raising=True
        )
        res = run(default_headless_fire(EV))
        assert res == {"stop_reason": "end_turn"}
        prompt, sid = agent.calls[0]
        assert sid == "om-test1"
        assert "ScheduleWakeup 到点唤醒" in prompt[0]["text"]

    def test_prompt_contract(self):
        text = build_wakeup_prompt(EV)
        assert "检查CI是否转绿" in text
        assert "schedule_wakeup" in text
        assert "stop=true" in text
        assert "noop如实自报" in text
        assert "非用户消息" in text


# ════════════════════════════════════════════════════════════════
# ⑤ 循环与可观测
# ════════════════════════════════════════════════════════════════

class TestLoopAndSnapshot:
    def test_loop_polls_until_stopped(self, monkeypatch):
        server = FakeHttp(due=[])

        async def fire_fn(ev):
            return None

        ex = _executor(server, fire_fn, monkeypatch)

        async def scenario():
            await ex.start()
            await asyncio.sleep(0.08)
            await ex.stop()

        run(scenario())
        assert ex.snapshot()["polls"] >= 1
        assert ex.snapshot()["running"] is False

    def test_snapshot_shape(self, monkeypatch):
        server = FakeHttp(due=[])
        ex = _executor(server, (lambda ev: None), monkeypatch)
        st = ex.snapshot()
        for key in (
            "running", "opensoul_url", "poll_interval_s", "fire_timeout_s",
            "polls", "due_seen", "claimed", "claim_lost", "claim_errors",
            "fire_ok", "fire_failed", "ack_errors",
            "last_poll_at", "last_fire_at", "last_error",
        ):
            assert key in st, key
        assert st["opensoul_url"] == "http://opensoul.test"

    def test_poll_error_recorded_not_crashed(self, monkeypatch):
        server = FakeHttp(due=[], get_raises=RuntimeError("opensoul挂了"))

        async def fire_fn(ev):
            return None

        ex = _executor(server, fire_fn, monkeypatch)

        async def scenario():
            await ex.start()
            await asyncio.sleep(0.05)
            await ex.stop()

        run(scenario())  # 不抛=循环吞掉异常继续
        assert "opensoul挂了" in ex.snapshot()["last_error"]
