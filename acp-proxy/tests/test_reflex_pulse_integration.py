# -*- coding: utf-8 -*-
"""⚡❤️ Reflex+Pulse 与既有体系整合测试（跨模块 × 故障降级 × 并发）。

"写了≠接线了"：钩子必须活在**真实工具循环**（_run_llm_with_tools 真实执行
read_file）与**真实 /ws_acp 事件源路径**（ws_acp._reflex_observe 真报文）上；
Pulse 空闲语义必须活用 **run_guard 空闲语义**（idle_seconds/check verdict/pause）
而非另造定义。含真实延迟实测、真实 run_guard→Pulse→Reflex 链路实证、反射层故障
不破坏宿主循环的降级实证、并发捕获。
"""
import asyncio
import json
import threading
import time
from types import SimpleNamespace

import pytest

import ws_acp
from agent.guard_tools import GuardTools
from agent.reflex import (
    EVENT_ERROR,
    EVENT_PERMISSION,
    EVENT_TOOL_RETURN,
    ReflexHub,
    get_reflex,
    reset_reflex,
)
from agent.pulse import PulseLoop
from tests.test_plan_tool_ledger import _wire_tool_side
from tests.test_steering import FakeLLM, make_agent, run


class FakeClock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, s):
        self.t += s


def _tool_call(name, args, tc_id="t1"):
    return {"tool_calls": [{"id": tc_id,
                            "function": {"name": name,
                                         "arguments": json.dumps(args)}}]}


@pytest.fixture(autouse=True)
def _fresh_hub():
    reset_reflex()
    yield
    reset_reflex()


# ═══════════════════════════════════════════════════════════════════
# 工具返回钩子：真实工具循环实证（写了≠接线了）
# ═══════════════════════════════════════════════════════════════════

class TestToolReturnHookRealLoop:
    def test_real_tool_loop_fires_tool_return_event(self, tmp_path):
        """真事件即时响应实测延迟：真实 read_file 执行→工具返回钩子→反射弧。"""
        f = tmp_path / "data.txt"
        f.write_text("alpha\nbeta\n")
        agent = make_agent(tmp_path)
        _wire_tool_side(agent, tmp_path)   # 补工具执行侧依赖（放行门+缓存+审计）
        agent.llm_engine = FakeLLM([
            [_tool_call("read_file", {"path": str(f), "limit": 5})],
            ["完成"],
        ])
        t0 = time.perf_counter()
        resp, tools = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "读文件"}], "s1"))
        hook_latency = (time.perf_counter() - t0) * 1000.0
        assert resp == "完成"
        hub = agent._reflex_hub()
        events = [e for e in hub.recent(50) if e["kind"] == EVENT_TOOL_RETURN]
        assert events, "真实工具返回必须被反射层即时捕获"
        ev = next(e for e in events if "read_file" in json.dumps(e))
        print(f"\n[实测] 真实工具循环→Reflex 捕获链路: 轮次总耗时 {hook_latency:.1f}ms, "
              f"事件={ev['kind']}/{ev['verdict']}")
        assert ev["kind"] == EVENT_TOOL_RETURN

    def test_permission_block_hook_real_loop(self, tmp_path):
        """权限拦截路径活在真实工具循环：deny → reflex permission.check blocked。"""
        agent = make_agent(tmp_path)   # FakeGate(allow=False) 默认
        agent.llm_engine = FakeLLM([
            [_tool_call("read_file", {"path": "/etc/hostname"})],
            ["被拦"],
        ])
        run(agent._run_llm_with_tools([{"role": "user", "content": "越权"}], "s1"))
        hub = agent._reflex_hub()
        perms = [e for e in hub.recent(50) if e["kind"] == EVENT_PERMISSION]
        assert perms, "权限拦截必须被反射层即时捕获"
        assert perms[0]["verdict"] == "blocked"     # 安全拦截短路终态

    def test_broken_reflex_rule_degrades_not_breaks_tool_loop(self, tmp_path):
        """故障降级（跨模块）：反射规则炸掉→熔断开路，真实工具循环照常出结果。"""
        f = tmp_path / "x.txt"
        f.write_text("ok\n")
        hub = get_reflex()
        hub.add_rule("boom", EVENT_TOOL_RETURN,
                     lambda e: 1 / 0, action="callback",
                     max_failures=1, cooldown_s=60)
        agent = make_agent(tmp_path)
        _wire_tool_side(agent, tmp_path)
        agent.llm_engine = FakeLLM([
            [_tool_call("read_file", {"path": str(f), "limit": 3})],
            ["完成"],
        ])
        resp, _ = run(agent._run_llm_with_tools(
            [{"role": "user", "content": "干活"}], "s1"))
        assert resp == "完成", "反射层故障绝不破坏宿主会话流"
        rule = next(r for r in hub.stats()["rules"] if r["name"] == "boom")
        assert rule["circuit"] == "open"            # 异常熔断已开路
        assert any(e["kind"] == "circuit.open" for e in hub.recent(50))


# ═══════════════════════════════════════════════════════════════════
# /ws_acp 事件源接线：ws_acp._reflex_observe 真报文实证
# ═══════════════════════════════════════════════════════════════════

class TestWsAcPEventSourceWiring:
    def test_ws_observe_in_and_out_real_payloads(self):
        hub = get_reflex()
        # in：真实客户端报文（用户动作）
        ws_acp._reflex_observe("in", json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "session/prompt",
            "params": {"sessionId": "s1",
                       "prompt": [{"type": "text", "text": "帮我查天气"}]}}))
        # in：前端 Electron 上报
        ws_acp._reflex_observe("in", json.dumps({
            "method": "ext/notification",
            "params": {"kind": "window_focus", "win": "main"}}))
        # out：真实代理报文（工具返回更新/报错）
        ws_acp._reflex_observe("out", json.dumps({
            "method": "session/update",
            "params": {"sessionUpdate": "tool_call_update", "sessionId": "s1"}}))
        ws_acp._reflex_observe("out", json.dumps({
            "jsonrpc": "2.0", "id": 2,
            "error": {"code": -32603, "message": "LLM 502"}}))
        st = hub.stats()["by_kind"]
        assert st.get("user.action", 0) == 1
        assert st.get("frontend.event", 0) + st.get("frontend.window", 0) == 1
        assert st.get("tool.return", 0) >= 1
        assert st.get("error", 0) == 1

    def test_ws_observe_never_raises_on_garbage(self):
        ws_acp._reflex_observe("in", "}{ 不是JSON")
        ws_acp._reflex_observe("out", None)
        ws_acp._reflex_observe("in", json.dumps({"method": "ping"}))


# ═══════════════════════════════════════════════════════════════════
# 用户动作事件源：真实 prompt() 路径实证
# ═══════════════════════════════════════════════════════════════════

class TestPromptUserActionHook:
    def test_real_prompt_fires_user_action_event(self, tmp_path):
        agent = make_agent(tmp_path, acquired=False)   # writer-fence 未获取→排队
        run(agent.prompt([SimpleNamespace(text="帮我把config也查了")], "s1"))
        hub = agent._reflex_hub()
        events = [e for e in hub.recent(50) if e["kind"] == "user.action"]
        assert events and "帮我把config也查了" in events[0]["text"]

    def test_real_prompt_abort_text_routes_steering_semantics(self, tmp_path):
        agent = make_agent(tmp_path, acquired=False)
        run(agent.prompt([SimpleNamespace(text="/abort")], "s1"))
        hub = agent._reflex_hub()
        events = [e for e in hub.recent(50) if e["kind"] == "user.interrupt"]
        assert events and events[0]["verdict"] == "routed"


# ═══════════════════════════════════════════════════════════════════
# Pulse 接 run_guard 空闲语义（活用既有件，勿平行造轮子）
# ═══════════════════════════════════════════════════════════════════

class TestPulseRunGuardIntegration:
    def _agent_with_guard(self, tmp_path, clock, npt=1000):
        agent = make_agent(tmp_path)
        gt = GuardTools(now=clock)
        agent._guard_tools_inst = gt
        gt.run("run_guard", {"action": "configure",
                             "no_progress_timeout": npt,
                             "max_turns": 100}, "s1")
        return agent, gt

    def test_pulse_wake_uses_run_guard_idle_real(self, tmp_path):
        """空闲唤醒实测（跨模块）：真 RunGuard 无进展时长驱动 Pulse 唤醒。"""
        clock = FakeClock()
        agent, gt = self._agent_with_guard(tmp_path, clock)
        clock.advance(500)                       # 真 run_guard 空闲 500s
        wakes = []
        agent._pulse_wake_sink = lambda sid, reason: wakes.append((sid, reason))
        pl = agent._pulse_loop()
        assert [c.name for c in pl.checks()] == [
            "backend_services", "connection_state", "task_queue",
            "unfinished_work_orders", "memory_consistency"]
        report = pl.beat()
        print(f"\n[实测] run_guard(idle={gt.guard_for('s1').idle_seconds():.0f}s)"
              f"→Pulse 空闲唤醒: {wakes}")
        assert wakes == [("s1", "idle_keepalive")]
        assert report["wakes"][0]["session_id"] == "s1"

    def test_pulse_wake_suppressed_while_waiting_user(self, tmp_path):
        """ask-user 等待期（run_guard pause）不唤醒——空闲语义同源 run_guard。"""
        clock = FakeClock()
        agent, gt = self._agent_with_guard(tmp_path, clock)
        clock.advance(500)
        gt.run("run_guard", {"action": "pause"}, "s1")   # 等用户作答
        wakes = []
        agent._pulse_wake_sink = lambda sid, reason: wakes.append(reason)
        report = agent._pulse_loop().beat()
        assert wakes == []
        assert report["suppressed"][0]["reason"].startswith("waiting_user")

    def test_run_guard_stop_verdict_flows_to_reflex(self, tmp_path):
        """超时可见链路：run_guard verdict=stop → Pulse finding → Reflex 留痕。"""
        clock = FakeClock()
        agent, gt = self._agent_with_guard(tmp_path, clock, npt=100)
        clock.advance(300)                        # 无进展 300s → verdict=stop
        assert gt.guard_for("s1").check()["verdict"] == "stop"
        agent._pulse_loop().beat()
        hub = agent._reflex_hub()
        errs = [e for e in hub.recent(50) if e["kind"] == EVENT_ERROR]
        assert any("run_guard.no_progress" in json.dumps(e) for e in errs), \
            "run_guard 超时结论必须经 Pulse finding 落 Reflex 留痕（不硬杀）"


# ═══════════════════════════════════════════════════════════════════
# 裸实例零感知 + 生命周期接线 + 并发
# ═══════════════════════════════════════════════════════════════════

class TestBareInstanceAndLifecycle:
    def test_bare_instance_reflex_pulse_zero_perception(self):
        """__new__ 裸实例零感知：懒初始化不炸，五类目自检清单成形。"""
        from agent.soulmate_agent import SoulMateAgent
        agent = SoulMateAgent.__new__(SoulMateAgent)
        hub = agent._reflex_hub()
        assert isinstance(hub, ReflexHub)
        pl = agent._pulse_loop()
        assert [c.name for c in pl.checks()] == [
            "backend_services", "connection_state", "task_queue",
            "unfinished_work_orders", "memory_consistency"]
        # 裸实例 beat 也不炸（缺属性=自检发现，不是崩溃）
        report = pl.beat()
        assert isinstance(report["checks"], list) and len(report["checks"]) == 5

    def test_initialize_starts_pulse_heartbeat(self, tmp_path):
        """生命周期接线：ACP initialize 即起后台心跳（软停收尾）。"""
        agent = make_agent(tmp_path)

        async def scenario():
            resp = await agent.initialize(1)
            pl = agent._pulse_loop()
            assert pl._running, "initialize 必须起 Pulse 后台心跳"
            await asyncio.sleep(0.05)
            await pl.stop(timeout_s=2.0)
            return resp

        resp = run(scenario())
        assert resp is not None
        assert not agent._pulse_loop()._running


class TestConcurrencyCrossModule:
    def test_ws_capture_while_pulse_beats(self):
        """并发（跨模块）：ws 事件流 × 心跳拍 × 线程风暴同时跑，账目不丢不炸。"""
        hub = get_reflex()
        pulse = PulseLoop(interval_s=0.01)
        errors = []

        def ws_storm():
            try:
                for i in range(40):
                    ws_acp._reflex_observe("in", json.dumps({
                        "method": "session/prompt",
                        "params": {"prompt": [{"type": "text", "text": f"m{i}"}]}}))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=ws_storm) for _ in range(4)]

        async def scenario():
            for t in threads:
                t.start()
            for _ in range(20):
                pulse.beat()
                await asyncio.sleep(0)
            for t in threads:
                t.join()

        run(scenario())
        assert not errors
        assert hub.stats()["by_kind"].get("user.action", 0) == 160
