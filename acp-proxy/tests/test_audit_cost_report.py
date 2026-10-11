# -*- coding: utf-8 -*-
"""13.3 注入审计 LLM 往返开销基线——计时/统计/探针契约

覆盖：
1. 成功往返计时落 stats（last/mean/max/total）+ journal 行含 verdict/耗时/tools
2. 失败路径照计时（timeout/error/unavailable 各一形态）——失败往返是最贵开销
3. 耗时列表有界（≤500，防长命进程内存无界增长）
4. 生产者-消费者契约：_record_audit_ms 真实输出被 audit_cost_report.parse_audit_lines
   解析回读（改一边测试红）+ stderr 中继双份去重
5. summarize 分布数学（mean/p50/p95/max/min/total）+ 按 verdict/按工具归因 + 空数据 None
6. 接线护栏：check() 真实路径经 _llm_audit 计时（stats.llm_audit_last_ms 非 None）
"""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.infection_guard import InfectionGate  # noqa: E402
from audit_cost_report import (  # noqa: E402
    AUDIT_RE,
    parse_audit_lines,
    summarize,
)


def _run(coro):
    return asyncio.run(coro)


def _calls():
    # 参数含用户明文之外自造文本（路径+内容均不在 _sample_user_text）→ 预筛升级 → 真走 LLM 审计
    return [{
        "name": "write_file",
        "arguments": {"path": "/tmp/x.py", "content": "print('SOULMATE_DEV_OK')"},
    }]


class _SlowOkLLM:
    def __init__(self, delay=0.05):
        self.delay = delay

    async def __call__(self, messages):
        await asyncio.sleep(self.delay)
        return "<ok/>"


class _HangingLLM:
    async def __call__(self, messages):
        await asyncio.sleep(5)
        return "<ok/>"


class _BoomLLM:
    async def __call__(self, messages):
        raise RuntimeError("connection reset")


class _TaggedFailLLM:
    async def __call__(self, messages):
        return "[LLM错误: 402 余额不足]"


def _sample_user_text():
    return "帮我写个测试脚本看看输出"


# ── 1. 成功往返计时 ─────────────────────────────────────────────────
def test_success_timing_recorded_in_stats():
    gate = InfectionGate(llm_call=_SlowOkLLM(0.05))
    v = _run(gate.check(output_text="写入 x.py", tool_calls=_calls(),
                        user_text=_sample_user_text()))
    assert v.action == "ok"
    s = gate.stats()
    assert s["llm_audits"] == 1
    assert s["llm_audit_last_ms"] is not None and s["llm_audit_last_ms"] >= 40
    assert s["llm_audit_mean_ms"] == s["llm_audit_max_ms"] == s["llm_audit_last_ms"]
    assert s["llm_audit_total_ms"] == s["llm_audit_last_ms"]


def test_journal_line_shape_ok(caplog):
    gate = InfectionGate(llm_call=_SlowOkLLM(0.02))
    with caplog.at_level(logging.INFO, logger="infection_guard"):
        _run(gate.check(output_text="写入 x.py", tool_calls=_calls(),
                        user_text=_sample_user_text()))
    line = next(r.getMessage() for r in caplog.records
                if "LLM审计完成" in r.getMessage())
    assert "verdict=ok" in line
    assert "耗时=" in line and "ms tools=write_file" in line
    assert "（累计 1 次 均值 " in line
    assert AUDIT_RE.search(line), "探针正则必须能解析生产者行"


# ── 2. 失败路径照计时 ───────────────────────────────────────────────
def test_timeout_path_recorded(caplog):
    gate = InfectionGate(llm_call=_HangingLLM(), audit_timeout=0.05)
    with caplog.at_level(logging.INFO, logger="infection_guard"):
        v = _run(gate.check(output_text="x", tool_calls=_calls(),
                            user_text=_sample_user_text()))
    assert v.source == "llm_error_fail_safe"
    s = gate.stats()
    assert s["llm_audit_last_ms"] is not None and s["llm_audit_last_ms"] >= 40
    line = next(r.getMessage() for r in caplog.records
                if "LLM审计完成" in r.getMessage())
    assert "verdict=timeout" in line


def test_error_path_recorded():
    gate = InfectionGate(llm_call=_BoomLLM())
    v = _run(gate.check(output_text="x", tool_calls=_calls(),
                        user_text=_sample_user_text()))
    assert v.source == "llm_error_fail_safe"
    assert gate.stats()["llm_audit_last_ms"] is not None


def test_unavailable_path_recorded(caplog):
    gate = InfectionGate(llm_call=_TaggedFailLLM())
    with caplog.at_level(logging.INFO, logger="infection_guard"):
        v = _run(gate.check(output_text="x", tool_calls=_calls(),
                            user_text=_sample_user_text()))
    assert v.source == "llm_error_fail_safe"
    line = next(r.getMessage() for r in caplog.records
                if "LLM审计完成" in r.getMessage())
    assert "verdict=unavailable" in line
    assert gate.stats()["llm_audit_last_ms"] is not None


# ── 3. 有界 ────────────────────────────────────────────────────────
def test_durations_list_bounded():
    gate = InfectionGate(llm_call=_SlowOkLLM(0.001))
    for i in range(505):
        gate._record_audit_ms(float(i), "ok", _calls())
    assert len(gate.audit_durations_ms) == 500
    assert gate.audit_durations_ms[-1] == 504.0  # 保留最新


# ── 4. 生产者-消费者契约 ───────────────────────────────────────────
def test_producer_consumer_roundtrip(caplog):
    gate = InfectionGate(llm_call=_SlowOkLLM(0.02))
    with caplog.at_level(logging.INFO, logger="infection_guard"):
        _run(gate.check(output_text="写入 x.py", tool_calls=_calls(),
                        user_text=_sample_user_text()))
    line = next(r.getMessage() for r in caplog.records
                if "LLM审计完成" in r.getMessage())
    samples = parse_audit_lines([line])
    assert len(samples) == 1
    assert samples[0]["verdict"] == "ok"
    assert samples[0]["tools"] == ["write_file"]
    assert samples[0]["ms"] >= 10


def test_parse_dedup_stderr_relayed_lines():
    gate = InfectionGate(llm_call=None)
    gate._record_audit_ms(1234.0, "clarify",
                          [{"name": "terminal"}, {"name": "write_file"}])
    # 手工构造同内容行（模拟 ws_acp stderr 中继双份）+一份带 stderr 前缀
    body = ("[infection-guard] LLM审计完成 verdict=clarify 耗时=1234ms "
            "tools=terminal,write_file（累计 1 次 均值 1234ms 最大 1234ms）")
    dup = f"10月 11 17:00:00 host python[1]: [INFO] [soulmate] stderr: {body}"
    samples = parse_audit_lines([body, dup, body])
    assert len(samples) == 1
    assert samples[0]["ms"] == 1234
    assert samples[0]["tools"] == ["terminal", "write_file"]


def test_parse_ignores_unrelated_lines():
    assert parse_audit_lines([
        "[infection-guard] 预筛免审（user_verbatim）: xxx",
        "随便一行日志",
    ]) == []


# ── 5. summarize 分布数学 ──────────────────────────────────────────
def test_summarize_distribution_math():
    samples = [
        {"verdict": "ok", "ms": 100, "tools": ["write_file"]},
        {"verdict": "ok", "ms": 200, "tools": ["terminal", "write_file"]},
        {"verdict": "clarify", "ms": 300, "tools": ["terminal"]},
        {"verdict": "timeout", "ms": 400, "tools": []},
    ]
    s = summarize(samples)
    assert s["count"] == 4
    assert s["ms"]["total"] == 1000.0
    assert s["ms"]["mean"] == 250.0
    assert s["ms"]["max"] == 400.0 and s["ms"]["min"] == 100.0
    assert s["ms"]["p50"] in (200.0, 300.0)  # 四样本取中位邻域
    assert s["by_verdict"]["ok"]["count"] == 2
    assert s["by_verdict"]["timeout"]["total_ms"] == 400
    # 按工具归因：write_file 出现 2 次（100+200），terminal 2 次（200+300）
    assert s["by_tool"]["write_file"]["count"] == 2
    assert s["by_tool"]["write_file"]["total_ms"] == 300
    assert s["by_tool"]["terminal"]["total_ms"] == 500


def test_summarize_empty_is_none():
    s = summarize([])
    assert s["count"] == 0
    assert s["ms"]["mean"] is None and s["ms"]["total"] is None
    assert s["by_verdict"] == {} and s["by_tool"] == {}


# ── 6. 接线护栏 ────────────────────────────────────────────────────
def test_check_real_path_times_audit():
    """check() 真实路径（非直接调 _llm_audit）也必须落计时。"""
    gate = InfectionGate(llm_call=_SlowOkLLM(0.03))
    assert gate.stats()["llm_audit_last_ms"] is None  # 前置：未审计无数据
    _run(gate.check(output_text="写入 x.py", tool_calls=_calls(),
                    user_text=_sample_user_text()))
    s = gate.stats()
    assert s["llm_audit_last_ms"] is not None
    assert s["llm_audit_total_ms"] == s["llm_audit_last_ms"]


def test_exempt_batch_costs_zero_roundtrip():
    """预筛免审批次零 LLM 往返=零计时样本（开销治理语义：免审省的就是这个）。"""
    gate = InfectionGate(llm_call=_SlowOkLLM(0.05))
    user_text = "把 print('SOULMATE_DEV_OK') 写入 /tmp/x.py"
    v = _run(gate.check(
        output_text="", tool_calls=_calls(), user_text=user_text))
    assert v.source == "prescreen_user_verbatim"
    assert gate.stats()["llm_audits"] == 0
    assert gate.stats()["llm_audit_last_ms"] is None
