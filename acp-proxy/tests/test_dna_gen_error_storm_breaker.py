"""DNA进化strand 通用（非LLM）失败同错风暴熔断 契约测试（02:26遗留#2销账）。

背景：上轮已实现LLM错误风暴熔断，但**通用（非LLM）失败的同错风暴未熔断**——
pipeline/import类错误循环出现时每周期仍重试烧预算。本轮补上，且与LLM域
**分域独立窗口**（遗留#2原话："注意别把LLM窗口和通用窗口混算"）。

本文件钉住：
① 同签名连续失败≥GEN窗口→gen熔断开闸；签名一变重计（不同故障不误熔断）；
② 分域隔离：LLM风暴只开LLM闸、通用风暴只开gen闸，互不污染计数/窗口/闸门；
③ 调度门双域拦截（gen开闸→run()主循环抑制自动进化）+对照组放行；
④ 半开语义同款（冷却放行试探，失败再开闸）+周期跑通复位gen域（不碰LLM域）；
⑤ 持久化（重启不重置）+ 人工通道 reset_breaker(domain=...) + 接线护栏。
"""

import asyncio
import json
import time

import pytest

import dna_evolution
from dna_evolution import (
    DNAStrand,
    EvolutionStage,
    GEN_BREAKER_COOLDOWN_S,
    GEN_BREAKER_STORM_WINDOW,
    LLMCallError,
    StrandRole,
)


def run(coro):
    return asyncio.run(coro)


def _strand(tmp_path, strand_id="strand_a"):
    (tmp_path / "acp-proxy" / "data").mkdir(parents=True, exist_ok=True)
    return DNAStrand(
        strand_id=strand_id,
        role=StrandRole.PRIMARY,
        repo_root=str(tmp_path),
        llm_base_url="https://example.invalid/v1",
        llm_api_key="sk-test",
        llm_model="m-test",
    )


def _failing_reflect(s, exc_factory=lambda: ValueError("pipeline exploded")):
    """让 _evolve 在reflect阶段抛非LLM异常（进 except Exception 分支）。"""
    calls = []

    async def _boom():
        calls.append(1)
        raise exc_factory()

    s._reflect = _boom
    return calls


def _ok_evolve_stubs(s):
    """让 _evolve 完整走通不抛异常（成功路径=通用风暴复位点）。"""

    async def _ok(prompt):
        if "改进方案" in prompt:
            return '{"improvements": []}'
        if "教训" in prompt:
            return '{"lesson": "x", "pattern": "y"}'
        return '{"failure_patterns": [], "success_patterns": [], "improvements": [], "goal_progress": {}}'

    s._call_llm = _ok


def _spin_run_loop(s, settle=0.25):
    """真实跑一轮 run() 主循环（首个迭代同步段必达调度门判定点）。"""

    async def _go():
        task = asyncio.create_task(s.run("strand_b"))
        await asyncio.sleep(settle)
        s._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_go())


# ─────────────── ① 同签名风暴开闸（gen域） ───────────────

class TestGenBreakerTrip:
    def test_same_signature_below_window_does_not_trip(self, tmp_path):
        s = _strand(tmp_path)
        _failing_reflect(s)
        run(s._evolve())
        run(s._evolve())
        assert s._gen_failure_streak == 2
        assert s._gen_breaker_open is False
        assert s.get_status()["gen_breaker"]["state"] == "closed"

    def test_same_signature_reaches_window_trips_gen_breaker(self, tmp_path):
        s = _strand(tmp_path)
        _failing_reflect(s)
        for _ in range(GEN_BREAKER_STORM_WINDOW):
            run(s._evolve())
        assert s._gen_failure_streak == GEN_BREAKER_STORM_WINDOW
        assert s._gen_breaker_open is True
        st = s.get_status()["gen_breaker"]
        assert st["state"] == "open"
        assert st["gating"] is True
        assert st["cooldown_remaining_s"] > 0
        assert st["storm_window"] == GEN_BREAKER_STORM_WINDOW
        assert st["domain"] == "gen"
        # 开闸事件留痕（失败必须可见）
        log = s._log_file.read_text()
        assert "通用错误风暴熔断开启" in log
        # 分域：gen风暴绝不碰LLM闸/LLM计数
        assert s._breaker_open is False
        assert s._llm_failure_streak == 0
        assert s.get_status()["breaker"]["state"] == "closed"

    def test_signature_change_resets_streak_no_false_trip(self, tmp_path):
        """不同故障交错（签名一变重计）：永不达到窗口，不误熔断"""
        s = _strand(tmp_path)
        kinds = [ValueError, TypeError, RuntimeError, ValueError, TypeError, RuntimeError]

        async def _alternating():
            raise kinds[len(s._history)]()

        s._reflect = _alternating
        for _ in kinds:
            run(s._evolve())
        assert s._gen_failure_streak == 1  # 每次签名都变→重计
        assert s._gen_breaker_open is False
        assert s.get_status()["gen_breaker"]["state"] == "closed"

    def test_error_signature_ignores_message_uses_stage_and_type(self, tmp_path):
        """同根因不同消息（路径各异的FileNotFoundError）仍算同签名"""
        s = _strand(tmp_path)
        assert s._error_signature(ValueError("a"), "reflect") == "reflect:ValueError"
        assert s._error_signature(ValueError("totally different msg"), "reflect") == "reflect:ValueError"
        assert s._error_signature(ValueError("a"), "plan") != s._error_signature(ValueError("a"), "reflect")
        assert s._error_signature(KeyError("a"), "reflect") != s._error_signature(ValueError("a"), "reflect")

    def test_history_carries_error_signature(self, tmp_path):
        s = _strand(tmp_path)
        _failing_reflect(s)
        run(s._evolve())
        entry = s._history[-1]
        assert entry["error_signature"] == "reflect:ValueError"
        assert entry["success"] is False
        assert "llm_error" not in entry  # 与LLM失败分账（既有契约）
        assert s.get_status()["gen_error_signature"] == "reflect:ValueError"


# ─────────────── ② 分域隔离：两域窗口/计数不混算 ───────────────

class TestDomainIsolation:
    def test_llm_storm_does_not_touch_gen_breaker(self, tmp_path):
        s = _strand(tmp_path)

        async def _boom(prompt):
            raise LLMCallError("LLM HTTP 402: Insufficient account balance")

        s._call_llm = _boom
        for _ in range(3):
            run(s._evolve())
        assert s._llm_failure_streak == 3
        assert s._breaker_open is True  # LLM闸开了
        assert s._gen_breaker_open is False  # gen闸不受影响
        assert s._gen_failure_streak == 0
        assert s.get_status()["gen_breaker"]["state"] == "closed"

    def test_gen_storm_does_not_touch_llm_breaker(self, tmp_path):
        s = _strand(tmp_path)
        _failing_reflect(s)
        for _ in range(GEN_BREAKER_STORM_WINDOW + 1):
            run(s._evolve())
        assert s._gen_breaker_open is True
        assert s._breaker_open is False
        assert s._llm_failure_streak == 0

    def test_both_domains_can_be_open_independently(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("llm storm", domain="llm")
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._breaker_trip("gen storm", domain="gen")
        assert s._breaker_is_open(domain="llm") is True
        assert s._breaker_is_open(domain="gen") is True
        s._breaker_close("llm_success", domain="llm")
        assert s._breaker_is_open(domain="llm") is False  # 关llm闸
        assert s._breaker_is_open(domain="gen") is True   # gen闸不受影响


# ─────────────── ③ 调度门：gen开闸→抑制自动进化 ───────────────

class TestGenSchedulingGate:
    def test_run_loop_suppresses_evolution_while_gen_open(self, tmp_path):
        s = _strand(tmp_path)
        called = []

        async def _fake_evolve():
            called.append(1)

        s._evolve = _fake_evolve
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._breaker_trip("gen storm", domain="gen")  # 开闸（冷却新鲜→拦截）
        _spin_run_loop(s)
        assert called == []  # 自动进化被抑制，没烧预算
        assert s._gen_breaker_suppressed == 1  # gen域独立计数
        assert s._breaker_suppressed == 0  # 不污染LLM域抑制计数
        log = s._log_file.read_text()
        assert "通用错误风暴" in log and "熔断抑制" in log
        assert s.get_status()["gen_breaker"]["suppressed_cycles"] == 1

    def test_run_loop_runs_evolution_when_gen_closed(self, tmp_path):
        """对照组：gen熔断关闭时调度门放行（证明门不是死代码恒抑制）"""
        s = _strand(tmp_path)
        called = []

        async def _fake_evolve():
            called.append(1)

        s._evolve = _fake_evolve
        _spin_run_loop(s)
        assert called == [1]
        assert s._gen_breaker_suppressed == 0

    def test_manual_evolve_bypasses_gen_gate(self, tmp_path):
        """手动POST /trigger走_evolve直调——人工意图不受熔断拦截（同LLM域语义）"""
        s = _strand(tmp_path)
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._breaker_trip("gen storm", domain="gen")
        assert s._breaker_is_open(domain="gen") is True
        _ok_evolve_stubs(s)
        run(s._evolve())
        assert s._cycle_count == 1  # 熔断开着照样执行了
        assert len(s._history) == 1


# ─────────────── ④ 半开语义 + 周期复位 + 人工通道 ───────────────

class TestGenHalfOpenAndReset:
    def test_half_open_after_cooldown_allows_probe(self, tmp_path):
        s = _strand(tmp_path)
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._breaker_trip("gen storm", domain="gen")
        # 冷却过期：半开，放行一次试探
        s._gen_breaker_opened_at = time.time() - GEN_BREAKER_COOLDOWN_S - 1
        assert s._breaker_is_open(domain="gen") is False  # 调度门放行
        st = s.get_status()["gen_breaker"]
        assert st["state"] == "half_open"
        assert st["gating"] is False

    def test_half_open_probe_failure_reopens_with_fresh_cooldown(self, tmp_path):
        s = _strand(tmp_path)
        _failing_reflect(s)
        for _ in range(GEN_BREAKER_STORM_WINDOW):
            run(s._evolve())
        s._gen_breaker_opened_at = time.time() - GEN_BREAKER_COOLDOWN_S - 1  # 半开
        run(s._evolve())  # 试探失败（同签名streak=窗口+1）→再开闸
        assert s._breaker_is_open(domain="gen") is True
        st = s.get_status()["gen_breaker"]
        assert st["state"] == "open"
        assert st["cooldown_remaining_s"] > GEN_BREAKER_COOLDOWN_S - 5  # 冷却重新计数

    def test_cycle_success_resets_gen_streak_and_closes_gen_breaker(self, tmp_path):
        """周期完整跑通=通用风暴结束：只关gen闸，不碰LLM闸（分域）"""
        s = _strand(tmp_path)
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._gen_error_signature = "reflect:ValueError"
        s._breaker_trip("gen storm", domain="gen")
        s._breaker_trip("llm storm", domain="llm")  # LLM闸也开着（应保持）
        _ok_evolve_stubs(s)
        run(s._evolve())
        assert s._gen_failure_streak == 0
        assert s._gen_error_signature == ""
        assert s._breaker_is_open(domain="gen") is False  # gen闸复位
        assert s._breaker_is_open(domain="llm") is True  # LLM闸不被误关

    def test_reset_breaker_domain_gen(self, tmp_path):
        s = _strand(tmp_path)
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._breaker_trip("gen storm", domain="gen")
        status = s.reset_breaker(domain="gen")
        assert s._gen_breaker_open is False
        assert status["gen_breaker"]["state"] == "closed"
        assert s._gen_failure_streak == GEN_BREAKER_STORM_WINDOW  # 复位熔断不清账本
        log = s._log_file.read_text()
        assert "人工复位熔断" in log

    def test_reset_breaker_domain_all_resets_both(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("llm storm", domain="llm")
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._breaker_trip("gen storm", domain="gen")
        status = s.reset_breaker(domain="all")
        assert status["breaker"]["state"] == "closed"
        assert status["gen_breaker"]["state"] == "closed"

    def test_reset_breaker_rejects_unknown_domain(self, tmp_path):
        s = _strand(tmp_path)
        with pytest.raises(ValueError):
            s.reset_breaker(domain="bogus")

    def test_reset_breaker_default_domain_is_llm_backward_compat(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("llm storm", domain="llm")
        s._gen_failure_streak = GEN_BREAKER_STORM_WINDOW
        s._breaker_trip("gen storm", domain="gen")
        s.reset_breaker()  # 无参=只复位LLM域（向后兼容既有调用方）
        assert s._breaker_open is False
        assert s._gen_breaker_open is True


# ─────────────── ⑤ 持久化：重启不重置风暴计数 ───────────────

class TestGenBreakerPersistence:
    def test_gen_breaker_state_survives_restart(self, tmp_path):
        s = _strand(tmp_path)
        _failing_reflect(s)
        for _ in range(GEN_BREAKER_STORM_WINDOW):
            run(s._evolve())
        s._gen_breaker_suppressed = 2
        s._save_state()

        s2 = _strand(tmp_path)  # 模拟重启（重新加载state）
        assert s2._gen_breaker_open is True
        assert s2._gen_failure_streak == GEN_BREAKER_STORM_WINDOW
        assert s2._gen_error_signature == "reflect:ValueError"
        assert s2._gen_breaker_suppressed == 2
        assert s2._breaker_is_open(domain="gen") is True

    def test_legacy_state_file_without_gen_breaker_keys(self, tmp_path):
        """老state文件（无gen_breaker键）加载不炸，默认关闭"""
        data_dir = tmp_path / "acp-proxy" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "dna_state_strand_a.json").write_text(json.dumps({
            "strand_id": "strand_a", "cycle_count": 3,
            "memory": [], "history": [], "observations": [],
            "breaker": {"open": False, "opened_at": 0.0, "suppressed": 0},
        }))
        s = _strand(tmp_path)
        assert s._gen_breaker_open is False
        assert s._gen_failure_streak == 0
        assert s._gen_error_signature == ""
        assert s.get_status()["gen_breaker"]["state"] == "closed"


# ─────────────── ⑥ 状态面形状 + 接线护栏（写了≠接线了） ───────────────

class TestStatusAndWiringGuards:
    def test_get_status_exposes_gen_breaker_shape(self, tmp_path):
        s = _strand(tmp_path)
        st = s.get_status()
        assert "gen_breaker" in st and "gen_failure_streak" in st and "gen_error_signature" in st
        gb = st["gen_breaker"]
        for key in ("domain", "state", "open", "gating", "opened_at",
                    "cooldown_remaining_s", "suppressed_cycles", "storm_window", "cooldown_s"):
            assert key in gb
        assert gb["storm_window"] == GEN_BREAKER_STORM_WINDOW
        assert gb["cooldown_s"] == GEN_BREAKER_COOLDOWN_S
        # LLM域既有形状不回归
        assert st["breaker"]["storm_window"] == dna_evolution.LLM_BREAKER_STORM_WINDOW

    def test_run_loop_gate_wired_for_gen_domain(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "dna_evolution.py")).read()
        # 调度门真实检查gen域（run()主循环分支内）
        assert 'should_evolve and self._breaker_is_open(now, domain="gen")' in src
        assert "self._gen_breaker_suppressed += 1" in src
        # 同签名计数+开闸在except Exception分支内
        assert "self._error_signature(e, cycle.stage.value)" in src
        assert 'f"连续{self._gen_failure_streak}次同签名通用失败' in src
        # 成功路径分域复位
        assert '_breaker_close("cycle_success", domain="gen")' in src
        # 分域常量存在
        assert "GEN_BREAKER_STORM_WINDOW" in src and "GEN_BREAKER_COOLDOWN_S" in src

    def test_reset_breaker_endpoint_wired_for_domain(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "routes", "evolution.py")).read()
        assert '"gen_breaker_storm_window"' in src  # 配置面可见
        assert 'strand.reset_breaker(domain=domain)' in src
        assert '"gen_breaker"' in src
