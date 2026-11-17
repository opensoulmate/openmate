# -*- coding: utf-8 -*-
"""⚡Reflex 反射层测试 — 事件总线 + 反射弧（事件捕获→轻量规则/安全拦截→快速
回调，零 LLM 不进重型主循环，含异常熔断/权限拦截路径）。

系统性覆盖：真事件即时响应**实测延迟**（perf_counter 真量）× 零 LLM 源码断言 ×
异常熔断（连续失败开路/冷却/half-open）× 突发风暴护栏（显式丢弃/安全豁免）×
/ws_acp 事件源分类（用户动作/快捷键/窗口变化/Electron 上报/工具返回/报错）×
既有 event_bus 观测面整合 × 并发捕获（多线程风暴下不炸不丢账）。
"""
import asyncio
import ast
import json
import threading
import time
from pathlib import Path

import pytest

import agent.reflex as reflex
from agent.event_bus import EventBus
from agent.reflex import (
    ACTION_BLOCK,
    ACTION_CALLBACK,
    ACTION_ROUTE,
    EVENT_CIRCUIT_OPEN,
    EVENT_ERROR,
    EVENT_FRONTEND,
    EVENT_HOTKEY,
    EVENT_INTERRUPT,
    EVENT_MCP_CALL,
    EVENT_PERMISSION,
    EVENT_TOOL_RETURN,
    EVENT_USER_ACTION,
    EVENT_WINDOW_CHANGE,
    VERDICT_BLOCKED,
    VERDICT_DROPPED,
    VERDICT_HANDLED,
    VERDICT_PASS,
    VERDICT_ROUTED,
    ReflexHub,
    get_reflex,
    reset_reflex,
)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


@pytest.fixture(autouse=True)
def _fresh_hub():
    reset_reflex()
    yield
    reset_reflex()


# ═══════════════════════════════════════════════════════════════════
# 反射弧：捕获 → 轻量规则 → 回调/路由/拦截（来信号立刻反应）
# ═══════════════════════════════════════════════════════════════════

class TestReflexArc:
    def test_real_event_latency_measured(self):
        """真事件即时响应实测延迟：同步短路径，200 次捕获逐次计时（真量打印）。"""
        hub = ReflexHub()
        seen = []
        hub.add_rule("cb", EVENT_TOOL_RETURN,
                     lambda e: seen.append(e.payload.get("tool")),
                     action=ACTION_CALLBACK)
        lat = []
        resp = None
        for i in range(200):
            t0 = time.perf_counter()
            resp = hub.on_tool_return(session_id="s1", tool_name=f"t{i}", ok=True)
            lat.append((time.perf_counter() - t0) * 1000.0)
            assert resp.verdict == VERDICT_HANDLED
        avg, mx = sum(lat) / len(lat), max(lat)
        print(f"\n[实测] Reflex 捕获→回调延迟: avg={avg:.3f}ms max={mx:.3f}ms (n=200)")
        assert len(seen) == 200
        assert mx < 50.0, f"反射弧出现 {mx:.1f}ms 级延迟——疑似进了重路径"
        assert avg < 5.0
        # 响应自带逐次延迟量（可观测）
        assert resp is not None and resp.latency_ms >= 0.0

    def test_callback_route_block_three_terminals(self):
        hub = ReflexHub(with_default_rules=False)
        calls = []
        hub.add_rule("cb", EVENT_USER_ACTION, lambda e: calls.append("cb"),
                     action=ACTION_CALLBACK)
        hub.add_rule("rt", EVENT_INTERRUPT,
                     lambda e: {"target": "steering"}, action=ACTION_ROUTE)
        r1 = hub.emit(EVENT_USER_ACTION, text="hi")
        assert r1.verdict == VERDICT_HANDLED and calls == ["cb"]
        r2 = hub.emit(EVENT_INTERRUPT, text="/abort")
        assert r2.verdict == VERDICT_ROUTED
        r3 = hub.emit("nobody.matches", text="x")
        assert r3.verdict == VERDICT_PASS

    def test_abort_route_reuses_steering_semantics(self):
        """中断/abort 路由复用 steering.SteeringQueue.is_abort（不另立词汇）。"""
        hub = ReflexHub()
        r = hub.on_user_action(session_id="s1", text="/abort")
        assert r.verdict == VERDICT_ROUTED
        assert r.actions[0].result["target"] == "steering.abort"

    def test_block_short_circuits_first_block_wins(self):
        """安全拦截路径：首个拦截即终态，后续规则不再执行。"""
        hub = ReflexHub(with_default_rules=False)
        ran = []
        hub.add_rule("guard", EVENT_USER_ACTION, lambda e: {"reason": "危险动作"},
                     action=ACTION_BLOCK, priority=100)
        hub.add_rule("after", EVENT_USER_ACTION, lambda e: ran.append(1),
                     action=ACTION_CALLBACK, priority=0)
        r = hub.emit(EVENT_USER_ACTION, text="do it")
        assert r.verdict == VERDICT_BLOCKED and r.blocked
        assert r.reason == "危险动作"
        assert ran == [], "拦截后回调链必须短路"

    def test_permission_interception_path_default(self):
        """权限拦截路径（内置规则）：拦截事件即 blocked 终态，留痕可见。"""
        hub = ReflexHub()
        r = hub.on_permission_blocked(session_id="s1", tool_name="rm_rf",
                                      reason="blocked by gate", behavior="deny")
        assert r.verdict == VERDICT_BLOCKED
        assert "blocked by gate" in r.reason
        r2 = hub.on_sandbox_blocked(session_id="s1", tool_name="curl",
                                    reason="network restricted")
        assert r2.verdict == VERDICT_BLOCKED
        assert hub.stats()["blocked"] == 2

    def test_mcp_call_captured(self):
        hub = ReflexHub()
        r = hub.on_mcp_call(tool_name="server__tool", session_id="s1")
        assert r.event.kind == EVENT_MCP_CALL
        assert hub.stats()["by_kind"][EVENT_MCP_CALL] == 1


class TestZeroLLM:
    def test_reflex_pulse_modules_have_zero_llm_imports(self):
        """② 铁律：反射/心跳层零 LLM 调用——源码导入面无任何 LLM/HTTP 客户端。"""
        banned = {"llm_engine", "openai", "anthropic", "httpx", "requests",
                  "aiohttp", "urllib3"}
        for mod_path in (Path(reflex.__file__), Path(reflex.__file__).with_name("pulse.py")):
            tree = ast.parse(mod_path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
                    imported.update(a.name.split(".")[0] for a in node.names)
            assert not (imported & banned), f"{mod_path.name} 引入了重型依赖: {imported & banned}"


# ═══════════════════════════════════════════════════════════════════
# 异常熔断（③ 高优先突发信号之一）
# ═══════════════════════════════════════════════════════════════════

class TestCircuitBreaker:
    def _hub_with_failing_rule(self, clock):
        hub = ReflexHub(now=clock)
        hub.remove_rule("permission_intercept")
        hub.add_rule("boom", EVENT_TOOL_RETURN,
                     lambda e: (_ for _ in ()).throw(RuntimeError("handler炸")),
                     action=ACTION_CALLBACK, max_failures=3, cooldown_s=60)
        return hub

    def test_handler_exception_opens_circuit_after_threshold(self):
        clock = FakeClock()
        hub = self._hub_with_failing_rule(clock)
        for i in range(3):
            r = hub.emit(EVENT_TOOL_RETURN, tool="t")
            assert r.verdict in (VERDICT_PASS, VERDICT_HANDLED)  # 失败回调不外溢
        # 熔断已开路：后续捕获跳过该规则（fail-fast）
        r = hub.emit(EVENT_TOOL_RETURN, tool="t")
        assert any(a.circuit == "open" for a in r.actions)
        assert any("circuit-open" in a.error for a in r.actions)
        stats = hub.stats()
        rule = next(x for x in stats["rules"] if x["name"] == "boom")
        assert rule["circuit"] == "open" and rule["circuit_trips"] == 1

    def test_circuit_half_open_probe_then_close(self):
        clock = FakeClock()
        hub = ReflexHub(now=clock)
        hub.remove_rule("permission_intercept")
        state = {"ok": False}

        def flaky(e):
            if not state["ok"]:
                raise RuntimeError("still broken")
            return "recovered"

        hub.add_rule("flaky", EVENT_TOOL_RETURN, flaky,
                     action=ACTION_CALLBACK, max_failures=1, cooldown_s=60)
        hub.emit(EVENT_TOOL_RETURN, tool="t")           # fail → open
        r = hub.emit(EVENT_TOOL_RETURN, tool="t")       # 冷却期跳过
        assert any(a.circuit == "open" for a in r.actions)
        clock.advance(61)                                # 过冷却
        state["ok"] = True
        r = hub.emit(EVENT_TOOL_RETURN, tool="t")       # half-open 试探成功
        assert any(a.circuit == "half_open" and a.ok for a in r.actions)
        r = hub.emit(EVENT_TOOL_RETURN, tool="t")
        assert all(a.circuit == "" for a in r.actions)   # 已闭合
        rule = next(x for x in hub.stats()["rules"] if x["name"] == "flaky")
        assert rule["circuit"] == "closed"

    def test_emit_never_raises_broken_handler(self):
        hub = ReflexHub()
        hub.add_rule("broken", EVENT_TOOL_RETURN, lambda e: 1 / 0,
                     action=ACTION_CALLBACK)
        for _ in range(10):
            r = hub.emit(EVENT_TOOL_RETURN, tool="t")
            assert r is not None   # 宿主循环绝不被反射层炸掉

    def test_error_storm_trips_circuit_record(self):
        """同签名报错风暴（≥5/窗）→ 异常熔断记录（自我观察，不递归）。"""
        hub = ReflexHub()
        for _ in range(5):
            hub.on_error(error="LLM 超时 X", where="llm")
        kinds = hub.stats()["by_kind"]
        assert kinds.get(EVENT_CIRCUIT_OPEN, 0) >= 1
        assert hub.stats()["error_storms"] >= 1
        recent = hub.recent(50)
        assert any(e["kind"] == EVENT_CIRCUIT_OPEN for e in recent)


# ═══════════════════════════════════════════════════════════════════
# 突发风暴护栏（③ 高优先突发信号：显式丢弃 / 安全类豁免）
# ═══════════════════════════════════════════════════════════════════

class TestStormGuard:
    def test_burst_dropped_explicitly_counted(self):
        hub = ReflexHub(storm_window_s=10, storm_max=10)
        for i in range(30):
            hub.emit(EVENT_USER_ACTION, text=f"flood{i}")
        st = hub.stats()
        assert st["dropped"] == 20
        assert st["dropped_by_kind"][EVENT_USER_ACTION] == 20
        assert st["total"] == 30          # 丢弃也记账（截断必须显式标记）

    def test_safety_kinds_never_dropped(self):
        hub = ReflexHub(storm_window_s=10, storm_max=5)
        for _ in range(30):
            r = hub.on_permission_blocked(tool_name="x", reason="deny")
            assert r.verdict == VERDICT_BLOCKED
            assert not r.dropped
        assert hub.stats()["dropped"] == 0


# ═══════════════════════════════════════════════════════════════════
# /ws_acp 事件源分类（① 外部事件/用户动作/Electron 上报/工具返回/报错）
# ═══════════════════════════════════════════════════════════════════

class TestWsEventSource:
    def test_in_user_prompt(self):
        hub = ReflexHub()
        raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "session/prompt",
                          "params": {"sessionId": "s1",
                                     "prompt": [{"type": "text", "text": "你好"}]}})
        r = hub.capture_ws_message("in", raw)
        assert r.event.kind == EVENT_USER_ACTION
        assert "你好" in r.event.text

    def test_in_cancel_is_interrupt_critical(self):
        hub = ReflexHub()
        raw = json.dumps({"method": "session/cancel", "params": {"sessionId": "s1"}})
        r = hub.capture_ws_message("in", raw)
        assert r.event.kind == EVENT_INTERRUPT
        from agent.event_bus import EventPriority
        assert int(r.event.priority) == int(EventPriority.CRITICAL)

    def test_in_abort_text_routes_steering(self):
        hub = ReflexHub()
        raw = json.dumps({"method": "session/prompt",
                          "params": {"prompt": [{"type": "text", "text": "/abort"}]}})
        r = hub.capture_ws_message("in", raw)
        assert r.event.kind == EVENT_INTERRUPT
        assert r.verdict == VERDICT_ROUTED

    def test_in_electron_reported_event(self):
        hub = ReflexHub()
        raw = json.dumps({"method": "ext/notification",
                          "params": {"kind": "telemetry", "cpu": 0.4}})
        r = hub.capture_ws_message("in", raw)
        assert r.event.kind == EVENT_FRONTEND

    def test_in_window_change_and_hotkey(self):
        hub = ReflexHub()
        r1 = hub.capture_ws_message("in", json.dumps(
            {"method": "session/update", "params": {"kind": "window_focus"}}))
        assert r1.event.kind == EVENT_WINDOW_CHANGE
        r2 = hub.capture_ws_message("in", json.dumps(
            {"method": "session/update", "params": {"kind": "hotkey", "key": "ctrl+k"}}))
        assert r2.event.kind == EVENT_HOTKEY

    def test_out_tool_call_update_is_tool_return(self):
        hub = ReflexHub()
        raw = json.dumps({"jsonrpc": "2.0", "method": "session/update",
                          "params": {"sessionUpdate": "tool_call_update",
                                     "sessionId": "s1"}})
        r = hub.capture_ws_message("out", raw)
        assert r.event.kind == EVENT_TOOL_RETURN

    def test_out_jsonrpc_error_is_error(self):
        hub = ReflexHub()
        raw = json.dumps({"jsonrpc": "2.0", "id": 9,
                          "error": {"code": -32603, "message": "boom"}})
        r = hub.capture_ws_message("out", raw)
        assert r.event.kind == EVENT_ERROR

    def test_non_json_and_ping_ignored_safely(self):
        hub = ReflexHub()
        assert hub.capture_ws_message("in", "not json at all") is None
        assert hub.capture_ws_message("in", json.dumps({"method": "ping"})) is not None

    def test_real_latency_ws_capture(self):
        """真事件（真实 JSON-RPC 报文）即时响应实测延迟。"""
        hub = ReflexHub()
        raw = json.dumps({"method": "session/prompt",
                          "params": {"prompt": [{"type": "text", "text": "hi"}]}})
        lat = []
        for _ in range(100):
            t0 = time.perf_counter()
            hub.capture_ws_message("in", raw)
            lat.append((time.perf_counter() - t0) * 1000.0)
        print(f"\n[实测] ws_acp 事件源捕获延迟: avg={sum(lat)/len(lat):.3f}ms "
              f"max={max(lat):.3f}ms (n=100)")
        assert max(lat) < 50.0


# ═══════════════════════════════════════════════════════════════════
# 既有件整合：event_bus 观测面（旁路转发，勿平行造轮子）
# ═══════════════════════════════════════════════════════════════════

class TestEventBusIntegration:
    def test_events_forwarded_to_existing_event_bus(self):
        async def scenario():
            bus = EventBus()
            hub = ReflexHub()
            hub.attach_event_bus(bus)
            hub.on_tool_return(session_id="s1", tool_name="read_file", ok=True)
            hub.on_error(error="boom", where="test")
            await asyncio.sleep(0)   # 让 fire-and-forget 转发落地
            return bus.get_history(limit=50)

        history = asyncio.run(scenario())
        kinds = {h["event_type"] for h in history}
        assert "reflex.tool.return" in kinds
        assert "reflex.error" in kinds

    def test_no_loop_forward_skipped_but_history_kept(self):
        hub = ReflexHub()
        hub.attach_event_bus(EventBus())   # 无运行中事件循环
        hub.on_tool_return(tool_name="t")
        assert hub.recent(5)               # 本层历史照常留痕


# ═══════════════════════════════════════════════════════════════════
# 并发（系统性维度：多线程风暴下捕获不炸、计数不丢账）
# ═══════════════════════════════════════════════════════════════════

class TestConcurrency:
    def test_threaded_storm_no_loss_no_crash(self):
        hub = ReflexHub(storm_max=10 ** 9)   # 关风暴护栏，专测账目完整性
        errors = []

        def worker(n):
            try:
                for i in range(50):
                    hub.on_tool_return(tool_name=f"t{n}-{i}")
                    hub.on_error(error=f"e{n}-{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert hub.stats()["total"] == 8 * 50 * 2

    def test_rules_mutable_during_capture(self):
        hub = ReflexHub()
        stop = False

        def churn():
            i = 0
            while not stop:
                hub.add_rule(f"r{i % 5}", EVENT_TOOL_RETURN,
                             lambda e: None, action=ACTION_CALLBACK)
                i += 1

        t = threading.Thread(target=churn)
        t.start()
        try:
            for _ in range(300):
                hub.emit(EVENT_TOOL_RETURN, tool="t")
        finally:
            stop = True
            t.join(timeout=5)
