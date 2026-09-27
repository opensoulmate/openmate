"""DNA进化strand 熔断半开**严格单试探**（at-most-one probe）契约测试。

背景（多轮遗留"半开语义非严格单试探"销账）：旧实现的"半开"只是冷却过期后
`_breaker_is_open()`恒返回False——**冷却一过就放行一切**，每个调度周期都跑，
失败还要等streak重新爬到窗口（3次）才再开闸。docstring承诺的"放行一个探测
请求，成功则closed，失败则重新open"与实现不符：
  ① 不是单试探：冷却过期后N个周期连续放行（无probe in-flight概念）；
  ② 试探失败不立即再开闸：streak<窗口时失败只计数，闸门形同虚设。

本文件钉住修复后的契约：
① 试探准入唯一（_breaker_probe_start）：open且冷却已过且无在飞→标记一次；
   在飞期间其余调度继续拦截（_breaker_is_open返回True），第二次准入被拒。
② 试探裁决即时（_breaker_probe_resolve）：试探期**一次失败即重新开闸**
   （冷却重新计数），不等streak爬窗口；试探成功→复位（closed）。
③ 裁决粒度=周期级保守：周期没跑通=在飞域全部判失败（fail-closed）；
   非常规出口（取消）→_breaker_probe_release释放不裁决（无证据不误判）。
④ 只读判定：status查询/reset_breaker不得消费试探（监控轮询误当试探的回归钉）。
⑤ 分域：llm/gen各自独立的probe标记；人工reset作废在飞试探。
⑥ 状态面probe_in_flight可见（"我都不知道他们在干嘛"观测面）+ 接线护栏。
"""

import asyncio
import json
import time

import pytest

import dna_evolution
from dna_evolution import (
    DNAStrand,
    GEN_BREAKER_COOLDOWN_S,
    GEN_BREAKER_STORM_WINDOW,
    LLMCallError,
    LLM_BREAKER_COOLDOWN_S,
    LLM_BREAKER_STORM_WINDOW,
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


def _failing_call_llm(s, msg="LLM HTTP 402: Insufficient account balance"):
    async def _boom(prompt):
        raise LLMCallError(msg)

    s._call_llm = _boom


def _failing_reflect(s, exc_factory=lambda: ValueError("pipeline exploded")):
    async def _boom():
        raise exc_factory()

    s._reflect = _boom


def _ok_evolve_stubs(s):
    """让 _evolve 完整走通不抛异常（成功路径=试探裁决成功点）。"""

    async def _ok(prompt):
        if "改进方案" in prompt:
            return '{"improvements": []}'
        if "教训" in prompt:
            return '{"lesson": "x", "pattern": "y"}'
        return '{"failure_patterns": [], "success_patterns": [], "improvements": [], "goal_progress": {}}'

    s._call_llm = _ok


def _expire_cooldown(s, domain="llm"):
    """把该域熔断拨到半开（冷却已过、无试探在飞）。"""
    if domain == "llm":
        s._breaker_opened_at = time.time() - LLM_BREAKER_COOLDOWN_S - 1
    else:
        s._gen_breaker_opened_at = time.time() - GEN_BREAKER_COOLDOWN_S - 1


# ─────────────── ① 试探准入唯一（at-most-one） ───────────────

class TestProbeAdmission:
    def test_probe_start_admits_exactly_one(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        assert s._breaker_probe_start() == ["llm"]  # 第一次=本轮即试探
        assert s._breaker_probe_start() == []  # 在飞期间拒绝第二个试探
        assert s._breaker_probe_start() == []
        assert s._probe_in_flight["llm"] is True

    def test_probe_in_flight_blocks_gate(self, tmp_path):
        """严格单试探：试探在飞期间其余调度继续拦截（gate=唯一判定点）"""
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        assert s._breaker_is_open() is False  # 未开始试探：放行（下一轮即试探）
        s._breaker_probe_start()
        assert s._breaker_is_open() is True  # 在飞：拦截其余调度

    def test_probe_start_ignores_closed_and_fresh_cooldown(self, tmp_path):
        s = _strand(tmp_path)
        assert s._breaker_probe_start() == []  # closed域不标记（正常周期）
        s._breaker_trip("storm")  # 冷却新鲜
        assert s._breaker_probe_start() == []  # 冷却未过不标记（仍拦截）
        assert s._probe_in_flight == {"llm": False, "gen": False}

    def test_both_domains_probe_in_one_cycle(self, tmp_path):
        """两域都半开：一个周期同时充当两域试探"""
        s = _strand(tmp_path)
        s._breaker_trip("llm storm")
        s._breaker_trip("gen storm", domain="gen")
        _expire_cooldown(s, "llm")
        _expire_cooldown(s, "gen")
        assert sorted(s._breaker_probe_start()) == ["gen", "llm"]
        assert s._probe_in_flight == {"llm": True, "gen": True}


# ─────────────── ② 试探裁决即时（修复核心） ───────────────

class TestProbeAdjudication:
    def test_probe_failure_retrips_below_streak_window(self, tmp_path):
        """THE修复：试探期一次失败即重新开闸——不等streak爬到窗口。

        旧实现此处streak=1<窗口=3，不trip，冷却过期后每个周期继续放行；
        新实现=严格单试探：试探失败立即再开闸（冷却重新计数）。
        """
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        assert s._llm_failure_streak == 0
        _failing_call_llm(s)
        run(s._evolve())  # 试探周期：LLM失败（streak=1，远低于窗口3）
        assert s._llm_failure_streak == 1 < LLM_BREAKER_STORM_WINDOW
        assert s._breaker_open is True  # 已重新开闸（不是靠streak窗口）
        st = s.get_status()["breaker"]
        assert st["state"] == "open"
        assert st["cooldown_remaining_s"] > LLM_BREAKER_COOLDOWN_S - 5  # 冷却重计
        assert st["probe_in_flight"] is False  # 已裁决，标记清空
        log = s._log_file.read_text()
        assert "半开试探失败" in log  # 失败必须可见

    def test_probe_success_closes_breaker(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        _ok_evolve_stubs(s)
        run(s._evolve())  # 试探周期完整跑通=试探成功
        assert s._breaker_open is False
        st = s.get_status()["breaker"]
        assert st["state"] == "closed"
        assert st["probe_in_flight"] is False
        assert s._probe_in_flight == {"llm": False, "gen": False}

    def test_gen_probe_failure_retrips_gen_only(self, tmp_path):
        """分域裁决：gen半开试探失败只再开gen闸，llm闸（closed）不受影响"""
        s = _strand(tmp_path)
        s._gen_failure_streak = 1  # 远低于GEN窗口（旧实现此处不会trip）
        s._breaker_trip("gen storm", domain="gen")
        _expire_cooldown(s, "gen")
        _failing_reflect(s)
        run(s._evolve())
        assert s._gen_breaker_open is True  # 一次失败即再开闸（严格单试探）
        assert s._breaker_open is False  # llm域不被误伤
        st = s.get_status()["gen_breaker"]
        assert st["state"] == "open"
        assert st["cooldown_remaining_s"] > GEN_BREAKER_COOLDOWN_S - 5

    def test_probe_failure_fails_all_in_flight_domains(self, tmp_path):
        """裁决粒度=周期级保守：周期没跑通=在飞域全部判失败（fail-closed）"""
        s = _strand(tmp_path)
        s._breaker_trip("llm storm")
        s._breaker_trip("gen storm", domain="gen")
        _expire_cooldown(s, "llm")
        _expire_cooldown(s, "gen")
        assert sorted(s._breaker_probe_start()) == ["gen", "llm"]
        s._breaker_probe_resolve(ok=False, why="cycle died")
        assert s._breaker_open is True and s._gen_breaker_open is True
        assert s._probe_in_flight == {"llm": False, "gen": False}

    def test_closed_state_single_failure_still_not_trip(self, tmp_path):
        """回归钉：closed态普通失败不走试探裁决，仍按streak窗口（不得误开闸）"""
        s = _strand(tmp_path)
        _failing_call_llm(s)
        run(s._evolve())
        assert s._breaker_open is False
        assert s._llm_failure_streak == 1
        assert s._probe_in_flight == {"llm": False, "gen": False}


# ─────────────── ③ 只读判定（监控不消费试探） ───────────────

class TestReadOnlyJudgement:
    def test_status_query_does_not_consume_probe(self, tmp_path):
        """回归钉：get_status/reset查询不得把监控轮询误当试探"""
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        for _ in range(5):
            s.get_status()  # 监控反复轮询
        assert s._probe_in_flight["llm"] is False  # 未被消费
        assert s._breaker_probe_start() == ["llm"]  # 试探额度仍在

    def test_status_sees_half_open_while_probe_in_flight(self, tmp_path):
        """观测面：在飞时state=half_open+probe_in_flight=True+gating=True"""
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        s._breaker_probe_start()
        st = s.get_status()["breaker"]
        assert st["state"] == "half_open"
        assert st["probe_in_flight"] is True
        assert st["gating"] is True  # 在飞=其余调度被拦

    def test_status_exposes_probe_key_both_domains(self, tmp_path):
        s = _strand(tmp_path)
        st = s.get_status()
        assert st["breaker"]["probe_in_flight"] is False
        assert st["gen_breaker"]["probe_in_flight"] is False


# ─────────────── ④ 释放/人工通道 ───────────────

class TestProbeReleaseAndManual:
    def test_release_on_abnormal_exit_never_judges(self, tmp_path):
        """非常规出口（取消/BaseException）：释放不裁决——不误开闸也不误复位"""
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        s._breaker_probe_start()
        s._breaker_probe_release()
        assert s._probe_in_flight == {"llm": False, "gen": False}
        assert s._breaker_open is True  # 闸保持原状（未被误判）
        st = s.get_status()["breaker"]
        assert st["state"] == "half_open"  # 仍可再放行一次试探
        assert st["gating"] is False
        log = s._log_file.read_text()
        assert "试探标记释放" in log  # 释放留痕

    def test_reset_breaker_vetoes_in_flight_probe(self, tmp_path):
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)
        s._breaker_probe_start()
        s.reset_breaker()
        assert s._probe_in_flight["llm"] is False  # 人工复位=在飞试探作废
        assert s._breaker_open is False

    def test_evolve_abnormal_exit_releases_probe(self, tmp_path):
        """_evolve非常规出口（BaseException）→finally释放，不卡死闸门"""
        s = _strand(tmp_path)
        s._breaker_trip("storm")
        _expire_cooldown(s)

        async def _cancel():
            raise asyncio.CancelledError()

        s._reflect = _cancel
        with pytest.raises(asyncio.CancelledError):
            run(s._evolve())
        assert s._probe_in_flight == {"llm": False, "gen": False}  # 未卡死
        assert s._breaker_open is True  # 无裁决=闸不变


# ─────────────── ⑤ 接线护栏（写了≠接线了） ───────────────

class TestWiringGuards:
    def test_evolve_wired_to_probe_lifecycle(self):
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "dna_evolution.py")).read()
        # 准入点在_evolve周期开头（手动trigger同路径）
        assert "self._breaker_probe_start()" in src
        # 裁决点：成功路径1处 + 两类失败路径各1处
        assert 'self._breaker_probe_resolve(ok=True, why="cycle_completed")' in src
        assert src.count("self._breaker_probe_resolve(ok=False") == 2
        # 非常规出口finally释放
        assert "self._breaker_probe_release()" in src
        assert "finally:" in src
        # 状态面可见
        assert '"probe_in_flight"' in src
        # 旧调度门接线不回归（wiring guard沿用）
        assert "should_evolve and self._breaker_is_open(now)" in src

    def test_probe_state_not_persisted(self):
        """进程内存态（不持久化）语义钉：_save_state不得写probe键（重启=重新放行一次试探）"""
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "dna_evolution.py")).read()
        save_block = src.split("def _save_state(self):", 1)[1].split("def _save_goals", 1)[0]
        assert "probe_in_flight" not in save_block
