"""DNA进化strand 错误风暴熔断（agno §1.4）+ 非LLM失败通用记账 契约测试。

背景（2026-09-27 00:10轮遗留#2/#3销账）：
① streak计数已入状态面但**未接熔断机制**——provider余额402等系统性故障
   时每小时空转烧LLM调用，且同一故障被记成N条独立失败污染进化样本
   （agno §1.4"前storm_window个attempt全部error且error_type相同→停止"）。
② _evolve的非LLM异常只logger.error不留账本——"跑了但炸了"在history/
   状态面不可见。

本文件钉住：
① streak≥窗口→熔断开闸暂停自动进化调度（run()调度门），冷却半开试探；
   成功复位/半开失败再开闸冷却重新计数；手动reset-breaker人工通道。
② 非LLM失败按失败记账（history带error键、last_error状态面），与LLM失败
   （llm_error键）分账，失败必须可见。
③ 熔断状态持久化（重启不重置风暴计数）+ 接线护栏。
"""

import asyncio
import json
import time

import pytest

import dna_evolution
from dna_evolution import (
    DNAStrand,
    EvolutionStage,
    LLMCallError,
    LLM_BREAKER_COOLDOWN_S,
    LLM_BREAKER_STORM_WINDOW,
    StrandRole,
)


def run(coro):
    return asyncio.run(coro)


def _resp(status=200, text="", content=None, finish="stop"):
    class R:
        status_code = status
        headers = {}

        def __init__(self):
            self.text = text or (json.dumps({
                "choices": [{"message": {"content": content or ""}, "finish_reason": finish}]
            }) if content is not None else text)

        def json(self):
            return json.loads(self.text)

    return R()


def _fake_client_factory(responses):
    queue = list(responses)

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            if not queue:
                raise AssertionError("超出预期的LLM调用")
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    return _FakeClient


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


def _failing_call_llm(s, msg="LLM HTTP 402: Insufficient account balance"):
    async def _boom(prompt):
        raise LLMCallError(msg)

    s._call_llm = _boom


def _spin_run_loop(s, settle=0.25):
    """真实跑一轮 run() 主循环（首个迭代同步段必达调度门判定点）。"""
    async def _go():
        task = asyncio.create_task(s.run("strand_b"))
        await asyncio.sleep(settle)  # 让主循环跑完首个迭代（末尾sleep(5)挂起）
        s._running = False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_go())


# ─────────────── ① 熔断开闸：streak≥窗口=错误风暴 ───────────────

class TestBreakerTrip:
    def test_below_window_does_not_trip(self, tmp_path):
        s = _strand(tmp_path)
        _failing_call_llm(s)
        run(s._evolve())
        run(s._evolve())
        assert s._llm_failure_streak == 2
        assert s._breaker_open is False
        assert s.get_status()["breaker"]["state"] == "closed"

    def test_streak_reaches_window_trips_breaker(self, tmp_path):
        s = _strand(tmp_path)
        _failing_call_llm(s)
        for _ in range(LLM_BREAKER_STORM_WINDOW):
            run(s._evolve())
        assert s._llm_failure_streak == LLM_BREAKER_STORM_WINDOW
        assert s._breaker_open is True
        st = s.get_status()["breaker"]
        assert st["state"] == "open"
        assert st["gating"] is True
        assert st["cooldown_remaining_s"] > 0
        assert st["storm_window"] == LLM_BREAKER_STORM_WINDOW
        # 开闸事件留痕（失败必须可见）
        log = s._log_file.read_text()
        assert "熔断开启" in log

    def test_retrip_refreshes_cooldown_but_logs_once(self, tmp_path):
        """已在拦截态再失败：冷却重新计数，但不重复记开闸事件"""
        s = _strand(tmp_path)
        _failing_call_llm(s)
        for _ in range(LLM_BREAKER_STORM_WINDOW):
            run(s._evolve())
        first_opened = s._breaker_opened_at
        run(s._evolve())  # 半开前又失败一次
        assert s._breaker_open is True
        assert s._breaker_opened_at >= first_opened  # 冷却重新计数
        log = s._log_file.read_text()
        assert log.count("熔断开启") == 1  # 开闸事件只记一次

    def test_generic_failures_do_not_trip_llm_breaker(self, tmp_path):
        """非LLM失败不进LLM风暴熔断（不同故障域，分账）"""
        s = _strand(tmp_path)

        async def _boom():
            raise ValueError("non-llm boom")

        s._reflect = _boom
        for _ in range(5):
            run(s._evolve())
        assert s._breaker_open is False
        assert s._llm_failure_streak == 0


# ─────────────── ② 调度门：熔断期间抑制自动进化 ───────────────

class TestSchedulingGate:
    def test_run_loop_suppresses_evolution_while_open(self, tmp_path):
        s = _strand(tmp_path)
        called = []

        async def _fake_evolve():
            called.append(1)

        s._evolve = _fake_evolve
        s._breaker_trip("test storm")  # 开闸（冷却新鲜→拦截）
        _spin_run_loop(s)
        assert called == []  # 自动进化被抑制，没烧预算
        assert s._breaker_suppressed == 1  # 抑制次数可见计数
        log = s._log_file.read_text()
        assert "熔断抑制" in log
        assert s.get_status()["breaker"]["suppressed_cycles"] == 1

    def test_run_loop_runs_evolution_when_closed(self, tmp_path):
        """对照组：熔断关闭时调度门放行（证明门不是死代码恒抑制）"""
        s = _strand(tmp_path)
        called = []

        async def _fake_evolve():
            called.append(1)

        s._evolve = _fake_evolve
        _spin_run_loop(s)
        assert called == [1]
        assert s._breaker_suppressed == 0

    def test_manual_evolve_bypasses_gate(self, tmp_path):
        """手动POST /trigger走_evolve直调——人工意图不受熔断拦截（job_queue.requeue同款）"""
        s = _strand(tmp_path)
        s._breaker_trip("test storm")
        assert s._breaker_is_open() is True

        async def _ok(prompt):
            if "改进方案" in prompt:
                return '{"improvements": []}'
            if "教训" in prompt:
                return '{"lesson": "x", "pattern": "y"}'
            return '{"failure_patterns": [], "success_patterns": [], "improvements": [], "goal_progress": {}}'

        s._call_llm = _ok
        run(s._evolve())
        assert s._cycle_count == 1  # 熔断开着照样执行了
        assert len(s._history) == 1


# ─────────────── ③ 半开语义 + 复位 ───────────────

class TestBreakerHalfOpenAndReset:
    def test_half_open_after_cooldown_allows_probe(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        # 冷却过期：半开，放行一次试探
        s._breaker_opened_at = time.time() - LLM_BREAKER_COOLDOWN_S - 1
        assert s._breaker_is_open() is False  # 调度门放行
        st = s.get_status()["breaker"]
        assert st["state"] == "half_open"
        assert st["gating"] is False

    def test_half_open_probe_failure_reopens_with_fresh_cooldown(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        s._breaker_opened_at = time.time() - LLM_BREAKER_COOLDOWN_S - 1  # 半开
        s._llm_failure_streak = LLM_BREAKER_STORM_WINDOW  # 开闸前已达窗口（真实前置态）
        _failing_call_llm(s)
        run(s._evolve())  # 试探失败（streak=窗口+1）
        assert s._breaker_is_open() is True  # 重新开闸
        st = s.get_status()["breaker"]
        assert st["state"] == "open"
        assert st["cooldown_remaining_s"] > LLM_BREAKER_COOLDOWN_S - 5  # 冷却重新计数

    def test_llm_success_closes_breaker(self, tmp_path, monkeypatch):
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        s._llm_failure_streak = 7
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient",
                            _fake_client_factory([_resp(200, content="hello")]))
        assert run(s._call_llm("hi")) == "hello"
        assert s._llm_failure_streak == 0  # 既有契约
        assert s._breaker_open is False  # LLM恢复=风暴结束，自动复位
        assert s.get_status()["breaker"]["state"] == "closed"

    def test_reset_breaker_manual_channel(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        status = s.reset_breaker()
        assert s._breaker_open is False
        assert status["breaker"]["state"] == "closed"
        assert "breaker" in status  # reset返回完整状态面
        log = s._log_file.read_text()
        assert "人工复位熔断" in log

    def test_successful_evolve_closes_breaker(self, tmp_path, monkeypatch):
        """半开试探成功（完整_evolve走通，打真实_call_llm）→熔断复位"""
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        s._breaker_opened_at = time.time() - LLM_BREAKER_COOLDOWN_S - 1  # 半开
        # 走真实_call_llm（_breaker_close在其成功路径内，stub掉_call_llm会绕开复位）
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient", _fake_client_factory([
            _resp(200, content='{"failure_patterns": [], "success_patterns": [], "improvements": [], "goal_progress": {}}'),
            _resp(200, content='{"improvements": []}'),
        ]))
        run(s._evolve())
        assert s._breaker_open is False


# ─────────────── ④ 非LLM失败通用记账（遗留#3） ───────────────

class TestGenericFailureAccounting:
    def test_non_llm_exception_recorded_as_failure(self, tmp_path):
        s = _strand(tmp_path)

        async def _boom():
            raise ValueError("pipeline exploded")

        s._reflect = _boom
        run(s._evolve())

        assert s._cycle_count == 1
        assert len(s._history) == 1
        entry = s._history[0]
        assert entry["success"] is False
        assert "error" in entry and "pipeline exploded" in entry["error"]
        assert "llm_error" not in entry  # 与LLM失败分账
        assert entry["stage"] == EvolutionStage.REFLECT.value
        # 状态面可见（与last_llm_error分账）
        assert "pipeline exploded" in s._last_error
        assert s._last_error_at
        assert s._last_llm_error == ""  # 非LLM失败不污染LLM账本
        st = s.get_status()
        assert "pipeline exploded" in st["last_error"]
        assert st["last_error_at"]
        # cycle.error留痕
        cycle_err = s._cycles[-1].error or ""
        assert "pipeline exploded" in cycle_err
        assert cycle_err.startswith("[reflect]")
        # 反伪装：绝不出现"无改进"话术
        blob = json.dumps({"history": s._history, "memory": s._memory}, ensure_ascii=False)
        assert "规划0项改进" not in blob

    def test_generic_failure_accounting_accumulates_visibly(self, tmp_path):
        s = _strand(tmp_path)

        async def _ok_reflect():
            return ["insight"]

        async def _boom(reflections):
            raise RuntimeError("kaboom")

        s._reflect = _ok_reflect
        s._plan = _boom
        run(s._evolve())
        run(s._evolve())
        assert len(s._history) == 2
        assert all("error" in h and h["success"] is False for h in s._history)
        assert all(h["stage"] == EvolutionStage.PLAN.value for h in s._history)
        assert s._llm_failure_streak == 0  # 不进LLM风暴计数


# ─────────────── ⑤ 持久化：重启不重置风暴计数 ───────────────

class TestBreakerPersistence:
    def test_breaker_state_survives_restart(self, tmp_path):
        s = _strand(tmp_path)
        _failing_call_llm(s)
        for _ in range(LLM_BREAKER_STORM_WINDOW):
            run(s._evolve())
        s._breaker_suppressed = 2
        s._save_state()

        s2 = _strand(tmp_path)  # 模拟重启（重新加载state）
        assert s2._breaker_open is True
        assert s2._llm_failure_streak == LLM_BREAKER_STORM_WINDOW
        assert s2._breaker_suppressed == 2
        assert s2._breaker_is_open() is True
        assert "402" in s2._last_llm_error

    def test_legacy_state_file_without_breaker_keys(self, tmp_path):
        """老state文件（无breaker键）加载不炸，默认关闭"""
        data_dir = tmp_path / "acp-proxy" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "dna_state_strand_a.json").write_text(json.dumps({
            "strand_id": "strand_a", "cycle_count": 3,
            "memory": [], "history": [], "observations": [],
        }))
        s = _strand(tmp_path)
        assert s._breaker_open is False
        assert s._llm_failure_streak == 0
        assert s.get_status()["breaker"]["state"] == "closed"


# ─────────────── ⑥ 接线护栏（写了≠接线了） ───────────────

class TestWiringGuards:
    def test_run_loop_has_scheduling_gate(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "dna_evolution.py")).read()
        # 调度门真实存在于run()主循环分支
        assert "should_evolve and self._breaker_is_open(now)" in src
        assert "self._breaker_suppressed += 1" in src
        # LLM失败达窗口→开闸（真实except分支内）
        assert "self._breaker_trip(" in src
        assert "LLM_BREAKER_STORM_WINDOW" in src
        # LLM成功→复位（_call_llm成功路径内）
        assert '_breaker_close("llm_success")' in src
        # 通用失败记账在except Exception分支
        assert 'cycle.error = f"[{cycle.stage.value}]' in src

    def test_reset_breaker_endpoint_wired(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "routes", "evolution.py")).read()
        assert '"/strands/{strand_id}/reset-breaker"' in src
        assert "strand.reset_breaker()" in src
        assert "llm_breaker_storm_window" in src  # 配置面可见
