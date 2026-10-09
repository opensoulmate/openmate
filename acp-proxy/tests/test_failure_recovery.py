# -*- coding: utf-8 -*-
"""路线图 2.2 失败恢复——契约测试。

覆盖：
1. 同路重试成功（第1次恢复即成功，动作=retry）
2. 换路径成功（重试失败→read_file→read_file_segment，参数变换正确）
3. 两次都失败→带失败原因+已尝试轨迹回注（首行失败标记保留，不谎报成功）
4. 非幂等只读工具（write_file/terminal）绝不自动重放，只记录+提示
5. 权限/校验/不存在类失败不重试（重试无意义）
6. 最多2次恢复：换路径只试第一个替代，重放计数封顶
7. 失败原因入日志：JSONL 记录字段齐全（ts/session/tool/error/category/actions/outcome）
8. 重放异常/超时收敛成失败文本不外抛
9. 接线护栏：soulmate_agent 真实路径含 FailureRecoveryManager 初始化+recover 调用
"""
import sys
import json
import asyncio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.failure_recovery import (  # noqa: E402
    ALTERNATE_PATHS,
    FailureRecoveryManager,
    IDEMPOTENT_TOOLS,
    MAX_RECOVERY_ATTEMPTS,
    classify_failure,
)
from agent.tool_errors import ErrorCategory  # noqa: E402

FAIL = "错误: 连接超时 (connection timeout)"
OK_RESULT = "文件内容第1行\n第2行"


def run(coro):
    return asyncio.run(coro)


def make_mgr(tmp_path):
    return FailureRecoveryManager(log_path=str(tmp_path / "fail_log.jsonl"))


def read_log(tmp_path):
    p = tmp_path / "fail_log.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


# ── 1. 同路重试成功 ──────────────────────────────────────────────

def test_retry_same_path_succeeds(tmp_path):
    mgr = make_mgr(tmp_path)
    calls = []

    async def exec_fn(tool, args):
        # 契约：first_result=已发生的首次失败，exec_fn 只负责重放
        calls.append((tool, dict(args)))
        return OK_RESULT

    result = run(mgr.recover("s1", "web_search", {"q": "x"}, FAIL, exec_fn))
    assert OK_RESULT in result
    assert "同路重试成功" in result
    assert len(calls) == 1 and calls[0] == ("web_search", {"q": "x"})
    assert mgr.stats()["recovered"] == 1


# ── 2. 换路径成功（含参数变换） ─────────────────────────────────

def test_switch_path_after_retry_fails(tmp_path):
    mgr = make_mgr(tmp_path)
    calls = []

    async def exec_fn(tool, args):
        calls.append((tool, dict(args)))
        if tool == "read_file":
            return FAIL
        return OK_RESULT  # read_file_segment 成功

    result = run(mgr.recover(
        "s1", "read_file", {"path": "/tmp/a.txt", "offset": 10, "limit": 20},
        FAIL, exec_fn))
    assert OK_RESULT in result
    assert "换路径 `read_file_segment` 成功" in result
    assert [c[0] for c in calls] == ["read_file", "read_file_segment"]
    # 参数变换：offset=10, limit=20 → start_line=10, end_line=29
    assert calls[1][1] == {"path": "/tmp/a.txt", "start_line": 10, "end_line": 29}
    assert mgr.stats()["recovered"] == 1


# ── 3. 两次都失败→带原因回注 ────────────────────────────────────

def test_exhausted_reports_failure_reason(tmp_path):
    mgr = make_mgr(tmp_path)
    calls = []

    async def exec_fn(tool, args):
        calls.append(tool)
        return FAIL

    result = run(mgr.recover("s1", "read_file", {"path": "/tmp/a.txt"}, FAIL, exec_fn))
    # 首行失败标记保留（result_ok 仍为 False，不谎报成功）
    from agent.read_turn import result_ok
    assert not result_ok(result)
    assert FAIL.split("\n")[0] in result
    assert "[失败恢复] 已尝试: retry→switch:read_file_segment" in result
    assert "均失败" in result and "失败原因" in result
    assert len(calls) == 2  # 最多2次重放
    assert mgr.stats()["replays"] == 2


# ── 4. 非幂等工具不自动重放 ─────────────────────────────────────

def test_non_idempotent_no_auto_replay(tmp_path):
    for tool in ("write_file", "terminal", "execute_code", "memory_write"):
        mgr = make_mgr(tmp_path)
        calls = []

        async def exec_fn(_t, _a):
            calls.append(_t)
            return OK_RESULT

        result = run(mgr.recover("s1", tool, {"path": "/tmp/x"}, FAIL, exec_fn))
        assert calls == [], f"{tool} 不应自动重放"
        assert "未自动重试" in result and "失败原因已入日志" in result
        assert tool not in IDEMPOTENT_TOOLS


# ── 5. 权限/校验/不存在类不重试 ─────────────────────────────────

def test_semantic_failures_no_retry(tmp_path):
    cases = [
        ("读取失败: Permission denied", ErrorCategory.PERMISSION),
        ("错误: write_file 参数不完整（path=''）", ErrorCategory.VALIDATION),
        ("错误: 文件不存在或为空", ErrorCategory.NOT_FOUND),
    ]
    for msg, expect_cat in cases:
        mgr = make_mgr(tmp_path)
        calls = []

        async def exec_fn(_t, _a):
            calls.append(_t)
            return OK_RESULT

        result = run(mgr.recover("s1", "read_file", {"path": "/tmp/a"}, msg, exec_fn))
        assert calls == [], f"{msg} 不应重试"
        assert "未自动重试" in result
        assert classify_failure(msg) == expect_cat


# ── 6. 最多2次恢复封顶 ──────────────────────────────────────────

def test_max_two_attempts_cap():
    assert MAX_RECOVERY_ATTEMPTS == 2
    # 每个工具的换路径候选只试第一个（break），总重放 ≤ 2
    for tool, alts in ALTERNATE_PATHS.items():
        assert len(alts) >= 1


def test_no_alternate_tool_stops_after_retry(tmp_path):
    mgr = make_mgr(tmp_path)
    calls = []

    async def exec_fn(tool, args):
        calls.append(tool)
        return FAIL

    result = run(mgr.recover("s1", "web_search", {"q": "x"}, FAIL, exec_fn))
    assert len(calls) == 1  # 无换路径候选：只同路重试1次
    assert "已尝试: retry，均失败" in result


# ── 7. 失败原因入日志（JSONL） ──────────────────────────────────

def test_failure_log_jsonl(tmp_path):
    mgr = make_mgr(tmp_path)
    calls = []

    async def exec_fn(tool, args):
        calls.append(tool)
        return FAIL

    run(mgr.recover("s1", "read_file", {"path": "/tmp/a"}, FAIL, exec_fn))
    run(mgr.recover("s2", "write_file", {"path": "/tmp/b"}, "写入失败: disk full", exec_fn))
    records = read_log(tmp_path)
    assert len(records) == 2
    r1 = records[0]
    assert r1["session_id"] == "s1" and r1["tool"] == "read_file"
    assert "timeout" in r1["error"] or "超时" in r1["error"]
    assert r1["category"] == "timeout"
    assert r1["actions"] == ["retry", "switch:read_file_segment"]
    assert r1["outcome"] == "exhausted"
    assert r1["ts"] > 0
    r2 = records[1]
    assert r2["outcome"] == "reported" and r2["tool"] == "write_file"


# ── 8. 重放异常/超时不外抛 ──────────────────────────────────────

def test_replay_exception_converges(tmp_path):
    mgr = make_mgr(tmp_path)

    async def exec_fn(tool, args):
        raise RuntimeError("boom")

    result = run(mgr.recover("s1", "web_search", {"q": "x"}, FAIL, exec_fn))
    assert "失败恢复重放异常" in result
    assert "boom" in result

    async def exec_hang(tool, args):
        await asyncio.sleep(999)
        return "never"

    import agent.failure_recovery as fr
    old = fr.REPLAY_TIMEOUT_S
    fr.REPLAY_TIMEOUT_S = 0.05
    try:
        result = run(mgr.recover("s1", "web_search", {"q": "x"}, FAIL, exec_hang))
        assert "失败恢复重放超时" in result
    finally:
        fr.REPLAY_TIMEOUT_S = old


# ── 9. 分类器 ───────────────────────────────────────────────────

def test_classify_failure():
    assert classify_failure("错误: rate limit exceeded 429") == ErrorCategory.RATE_LIMIT
    assert classify_failure("错误: 网络连接失败") == ErrorCategory.NETWORK
    assert classify_failure("错误: 操作超时") == ErrorCategory.TIMEOUT
    assert classify_failure("内部错误: 500") == ErrorCategory.INTERNAL
    assert classify_failure("随便一个失败") == ErrorCategory.UNKNOWN


# ── 10. 接线护栏（写了≠接线了） ─────────────────────────────────

def test_wiring_in_soulmate_agent():
    src = (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text(
        encoding="utf-8")
    assert "from agent.failure_recovery import FailureRecoveryManager" in src
    assert "self._failure_recovery = FailureRecoveryManager()" in src
    assert "await self._failure_recovery.recover(" in src
    # 失败判定走 result_ok 单一真源
    assert "if not result_ok(result):" in src
