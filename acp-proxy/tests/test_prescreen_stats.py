# -*- coding: utf-8 -*-
"""12.4③ 免审命中率 stats 实测观察——计数器/快照/账本留痕/探针契约

覆盖：
1. stats() 命中率：免审批次（分子）/升级审计批次（分母）计数正确，无数据报 None
2. 空 tool_calls 纯文本审计不进命中率分母（语义=工具批次命中率），但 llm_audits 照计
3. 零缩水：升级批次照常走 LLM 审计（mock 判 terminate 仍终止）
4. 账本留痕零缩水：[infection-guard] prescreen_exempt 伪条目被 parse_ledger 两头不计
5. 探针契约：journal 日志行与 prescreen_hitrate_report 正则互相咬合（改一边测试红）
6. 探针账本回读：metadata_json/arguments repr 形态解析正确
"""

import asyncio
import logging
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.infection_guard import InfectionGate  # noqa: E402
from agent.report_evidence import parse_ledger  # noqa: E402
from prescreen_hitrate_report import (  # noqa: E402
    ESCALATED_RE,
    EXEMPT_RE,
    count_journal_lines,
    read_ledger,
)

DEV_PROMPT = (
    "用 write_file 创建文件 /tmp/soulmate_accept/dev_hello.py，内容为一行："
    "print('SOULMATE_DEV_OK')。然后用 terminal 运行它，告诉我输出。"
)


class _BoomLLM:
    async def __call__(self, messages):
        raise AssertionError("LLM审计不应被调用（预筛应免审）")


class _TerminateLLM:
    def __init__(self):
        self.calls = 0

    async def __call__(self, messages):
        self.calls += 1
        return "<terminate/>测试注入"


class _OkLLM:
    def __init__(self):
        self.calls = 0

    async def __call__(self, messages):
        self.calls += 1
        return "<ok/>"


def _run(coro):
    # 周期#12.6：get_event_loop().run_until_complete 在批内排序下（前序测试
    # 已关/置空事件循环）抛 "no current event loop"（6红实证，纯HEAD复现），
    # 改 asyncio.run 每调用独立新循环——对齐周期#55 同款基建修法
    return asyncio.run(coro)


def _clean_calls():
    return [{
        "name": "write_file",
        "arguments": {
            "path": "/tmp/soulmate_accept/dev_hello.py",
            "content": "print('SOULMATE_DEV_OK')",
        },
    }]


# ── 1. stats() 命中率计数 ─────────────────────────────────────────────

def test_stats_exempt_hit_rate():
    gate = InfectionGate(llm_call=_BoomLLM())
    v = _run(gate.check("好的", _clean_calls(), user_text=DEV_PROMPT))
    assert v.source == "prescreen_user_verbatim"
    s = gate.stats()
    assert s["prescreen_exempt"] == 1
    assert s["prescreen_escalated"] == 0
    assert s["tool_batches"] == 1
    assert s["llm_audits"] == 0
    assert s["exempt_hit_rate"] == 1.0


def test_stats_escalated_counts_and_llm_audits():
    gate = InfectionGate(llm_call=_OkLLM())
    calls = [{"name": "terminal", "arguments": {"command": "rm -rf /"}}]  # 自造文本
    v = _run(gate.check("好的", calls, user_text=DEV_PROMPT))
    assert v.source in ("llm", "llm_error_fail_safe")  # 照常升级 LLM 审计
    s = gate.stats()
    assert s["prescreen_exempt"] == 0
    assert s["prescreen_escalated"] == 1
    assert s["llm_audits"] == 1
    assert s["exempt_hit_rate"] == 0.0


def test_stats_mixed_batches_half():
    gate = InfectionGate(llm_call=_OkLLM())
    _run(gate.check("好的", _clean_calls(), user_text=DEV_PROMPT))
    _run(gate.check("好的", [{"name": "terminal",
                              "arguments": {"command": "echo hi"}}],
                    user_text=DEV_PROMPT))
    s = gate.stats()
    assert s["prescreen_exempt"] == 1 and s["prescreen_escalated"] == 1
    assert s["exempt_hit_rate"] == 0.5


def test_stats_empty_tool_calls_not_in_denominator():
    gate = InfectionGate(llm_call=_OkLLM())
    _run(gate.check("纯文本输出，无工具调用", [], user_text=DEV_PROMPT))
    s = gate.stats()
    assert s["tool_batches"] == 0
    assert s["exempt_hit_rate"] is None  # 无数据禁报 100%
    assert s["llm_audits"] == 1  # 输出文本审计照常发生


def test_stats_no_data_hit_rate_none():
    gate = InfectionGate(llm_call=_BoomLLM())
    assert gate.stats()["exempt_hit_rate"] is None


# ── 2. 零缩水负控制 ───────────────────────────────────────────────────

def test_zero_shrink_escalated_batch_still_terminated():
    gate = InfectionGate(llm_call=_TerminateLLM())
    calls = [{"name": "terminal", "arguments": {"command": "rm -rf /"}}]
    v = _run(gate.check("好的", calls, user_text=DEV_PROMPT))
    assert v.action == "terminate"  # 预筛不豁免 → LLM 判 terminate 照常终止
    assert gate.stats()["prescreen_escalated"] == 1


def test_ledger_pseudo_row_counts_neither_side():
    # 12.4③ 账本留痕零缩水：prescreen_exempt 伪条目不计执行证据任何一侧
    ledger = [
        {"name": "[infection-guard]", "arguments": {"action": "prescreen_exempt",
                                                   "source": "prescreen_user_verbatim"},
         "result_preview": "免语义审计", "permission": "infection:prescreen_exempt"},
        {"name": "write_file", "arguments": "{'path': '/tmp/x'}",
         "result_preview": "ok", "permission": "allow"},
    ]
    executed, blocked = parse_ledger(ledger)
    assert [e.name for e in executed] == ["write_file"]
    assert blocked == []


# ── 3. 探针契约（journal 行 ↔ 正则互相咬合）───────────────────────────

def test_journal_log_lines_match_probe_regexes(caplog):
    gate = InfectionGate(llm_call=_OkLLM())
    with caplog.at_level(logging.INFO, logger="infection_guard"):
        _run(gate.check("好的", _clean_calls(), user_text=DEV_PROMPT))
        _run(gate.check("好的", [{"name": "terminal",
                                  "arguments": {"command": "echo hi"}}],
                        user_text=DEV_PROMPT))
    lines = [r.getMessage() for r in caplog.records]
    exempt_hits = [l for l in lines if EXEMPT_RE.search(l)]
    escalated_hits = [l for l in lines if ESCALATED_RE.search(l)]
    assert len(exempt_hits) == 1, f"免审行未命中探针正则: {lines}"
    assert len(escalated_hits) == 1, f"升级行未命中探针正则: {lines}"
    stats = count_journal_lines(lines)
    assert stats["exempt"] == 1 and stats["escalated"] == 1
    assert stats["by_source"] == {"prescreen_user_verbatim": 1}


def test_count_journal_lines_by_source_exec_verb():
    lines = [
        "[infection-guard] 预筛免审（prescreen_exec_verb）: terminal（模型自选执行动词: python）",
        "[infection-guard] 预筛升级审计: terminal 参数含用户请求之外的自造文本",
        "unrelated log line",
    ]
    stats = count_journal_lines(lines)
    assert stats == {"exempt": 1, "escalated": 1,
                     "by_source": {"prescreen_exec_verb": 1}}


def test_count_journal_lines_dedupes_ws_acp_relay_double_log():
    # 实测发现：子进程 stderr 被 ws_acp 中继再记一份 journal，同一事件两行
    inner = ("2026-10-11 13:56:19,196 [infection_guard] INFO: "
             "[infection-guard] 预筛免审（prescreen_user_verbatim）: write_file")
    lines = [
        f"Oct 11 13:56:19 host python[990675]: 2026-10-11 13:56:19,196 "
        f"[INFO] [soulmate] stderr: {inner}",
        f"Oct 11 13:56:19 host python[990675]: 2026-10-11 13:56:19,196 "
        f"[acp-proxy.ws_acp] INFO: [soulmate] stderr: {inner}",
    ]
    stats = count_journal_lines(lines)
    assert stats["exempt"] == 1  # 双份只计一次，命中率样本不翻倍
    assert stats["by_source"] == {"prescreen_user_verbatim": 1}


# ── 4. 探针账本回读（真实落库形态：metadata_json + arguments repr）──────

def test_read_ledger_parses_repr_arguments(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trajectory_events (id TEXT PRIMARY KEY, session_id TEXT,"
        " event_type TEXT, metadata_json TEXT, created_at TEXT)")
    rows = [
        ("1", "s1", "tool_call",
         '{"name": "[infection-guard]", "arguments": "{\'action\': '
         '\'prescreen_exempt\', \'source\': \'prescreen_user_verbatim\'}",'
         ' "result_preview": "x", "permission": "infection:prescreen_exempt"}'),
        ("2", "s1", "tool_call",
         '{"name": "write_file", "arguments": "x", "result_preview": "x",'
         ' "permission": "allow"}'),
        ("3", "s1", "tool_call",
         '{"name": "[infection-guard]", "arguments": {"action": "pass_with_trace"},'
         ' "result_preview": "x", "permission": "infection:pass_with_trace"}'),
    ]
    conn.executemany(
        "INSERT INTO trajectory_events (id, session_id, event_type, metadata_json,"
        " created_at) VALUES (?,?,?,?,?)",
        [(r[0], r[1], r[2], r[3], "2026-10-11") for r in rows])
    conn.commit()
    conn.close()
    stats, err = read_ledger(db)
    assert err == ""
    assert stats["exempt"] == 1
    assert stats["by_source"] == {"prescreen_user_verbatim": 1}


def test_read_ledger_missing_db_reports_error(tmp_path):
    stats, err = read_ledger(tmp_path / "nope.db")
    assert err and "不存在" in err
    assert stats == {}
