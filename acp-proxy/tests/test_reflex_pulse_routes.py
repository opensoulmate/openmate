# -*- coding: utf-8 -*-
"""⚡❤️ /api/reflex/* 与 /api/pulse/* 只读观察端点测试（真数据实证）。

写了≠接线了：端点必须真读 ReflexHub.stats()/recent() 与
PulseLoop.status()/recent_beats()——发真事件立刻在事件流上屏、真一拍心跳
的自检结果出现在清单里。
"""
import asyncio

import pytest

import routes.pulse as pulse_routes
import routes.reflex as reflex_routes
from agent.reflex import EVENT_TOOL_RETURN, reset_reflex


@pytest.fixture(autouse=True)
def _fresh_reflex():
    reset_reflex()
    yield
    reset_reflex()


class TestReflexRoutes:
    def test_stats_returns_hub_stats(self):
        st = asyncio.run(reflex_routes.reflex_stats())
        assert st["total"] == 0
        assert "rules" in st and "avg_latency_ms" in st

    def test_real_event_appears_in_event_stream(self):
        """发一个真事件（工具返回钩子）→ /api/reflex/events 事件流立刻上屏。"""
        from agent.reflex import get_reflex
        resp = get_reflex().on_tool_return(tool_name="read_file", ok=True,
                                           result_preview="hello")
        out = asyncio.run(reflex_routes.reflex_events(limit=50))
        assert out["total"] == 1
        kinds = [e["kind"] for e in out["events"]]
        assert EVENT_TOOL_RETURN in kinds
        ev = next(e for e in out["events"] if e["kind"] == EVENT_TOOL_RETURN)
        assert ev["verdict"] == resp.verdict
        assert ev["latency_ms"] >= 0.0
        assert ev["ts"] > 0.0   # 事件流必须带时间戳（面板时间列）

    def test_events_limit_is_read_only(self):
        """只读铁律：拉事件流不改变计数。"""
        from agent.reflex import get_reflex
        get_reflex().on_error(error="boom", where="test")
        before = asyncio.run(reflex_routes.reflex_stats())["total"]
        asyncio.run(reflex_routes.reflex_events(limit=10))
        asyncio.run(reflex_routes.reflex_events(limit=10))
        after = asyncio.run(reflex_routes.reflex_stats())["total"]
        assert before == after


class TestPulseRoutes:
    def test_status_exposes_observation_surface(self):
        st = asyncio.run(pulse_routes.pulse_status())
        for key in ("beats", "missed_beats", "avg_drift_ms", "max_drift_ms",
                    "checks", "reaped_total", "interval_s", "tasks"):
            assert key in st, f"status 缺观察面字段: {key}"

    def test_real_beat_self_check_results_on_screen(self):
        """真心跳一拍（同步核心）→ /api/pulse/checks 五类目真结果上屏、
        /api/pulse/beats 心跳流含该拍。"""
        pl = pulse_routes.get_pulse()
        report = pl.beat()          # 真一拍：五类目真检查真跑
        assert report["beat"] == pl.beats
        assert len(report["checks"]) == 5   # 五类目清单
        checks = asyncio.run(pulse_routes.pulse_checks())
        assert len(checks["checks"]) == 5
        assert checks["last_beat"] == report["beat"]
        for c in checks["checks"]:
            assert c["name"]
            assert c["category"]
            assert isinstance(c["ok"], bool)   # 真结果：每项都有 ok 判定
            assert c["detail"] is not None
        beats = asyncio.run(pulse_routes.pulse_beats(limit=5))
        assert beats["beats_total"] == pl.beats
        assert any(b["beat"] == report["beat"] for b in beats["beats"])

    def test_beats_and_status_read_only(self):
        pl = pulse_routes.get_pulse()
        pl.beat()
        before = asyncio.run(pulse_routes.pulse_status())["beats"]
        asyncio.run(pulse_routes.pulse_beats(limit=5))
        after = asyncio.run(pulse_routes.pulse_status())["beats"]
        assert before == after   # 只读：观察面不代发心跳
