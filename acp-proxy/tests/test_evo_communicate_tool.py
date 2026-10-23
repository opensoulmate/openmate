# -*- coding: utf-8 -*-
"""evo_communicate（Agent↔Agent A2A 转达工具）真实路径回归测试。

来源：evo 目标 2026-10-23 07:55【探针A2A桥】「所有新增工具必须带真实路径回归测试」——
evo_communicate 是该目标落地时最新的新增工具（commit cc619d42 五登记点），此前只有
手工 e2e 记录、零回归测试。本文件=新增工具回归测试的参照形态（后续新增工具照此配齐）：
真实工具循环路径（_run_llm_with_tools → 主链分发 → _evo_communicate_dispatch →
httpx→:8093/rpc/a2a 信封）+ 回执原话透传（转达不冒名，每 agent 独立铁律）+ 失败可见 +
五登记点接线（写了≠接线了）。
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.soulmate_agent import SoulMateAgent  # noqa: E402
from tests.test_loop_guard_wiring import _tc, _wire_agent  # noqa: E402
from tests.test_steering import run  # noqa: E402
from tests.test_trajectory_rounds import FakeTrajRecorder  # noqa: E402

A2A_URL = "http://127.0.0.1:8093/rpc/a2a"


class FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _stub_post(monkeypatch, payload=None, exc=None):
    """替身传输层：捕获 A2A 信封（网络隔离，测试可重复），业务路径全部真实。"""
    calls = []

    def fake_post(url, json=None, timeout=None, **kw):  # noqa: A002
        calls.append({"url": url, "json": json, "timeout": timeout})
        if exc is not None:
            raise exc
        return FakeResp(payload if payload is not None else {
            "jsonrpc": "2.0", "id": 1,
            "result": {"task_id": "t-1",
                       "result_data": {"reply": "evo: 目标已收"}}})

    monkeypatch.setattr("agent.soulmate_agent.httpx.post", fake_post)
    return calls


# ════════════════════════════════════════════════════════════════
# 1. 真实工具循环路径：LLM 工具调用 → 主链分发 → A2A 信封（用户怎么用就怎么测）
# ════════════════════════════════════════════════════════════════

def test_tool_call_real_path_envelope_and_reply(tmp_path, monkeypatch):
    calls = _stub_post(monkeypatch)
    agent = _wire_agent(tmp_path, [
        [_tc(name="evo_communicate",
             args={"action": "set_goal", "content": "回复正文不得混入执行旁白"},
             tc_id="t1")],
        ["好的，已转达 evo。"],
    ])
    agent._traj_recorder = FakeTrajRecorder()
    resp, tools = run(agent._run_llm_with_tools(
        [{"role": "user", "content": "告诉evo：回复正文不得混入执行旁白"}],
        "s1", traj_seg_id="seg-1"))
    # 一次工具调用=恰好一次 A2A delegate（不多发不漏发）
    assert len(calls) == 1, f"必须发一次 a2a/task/delegate，实际 {len(calls)}"
    req = calls[0]["json"]
    assert calls[0]["url"] == A2A_URL
    assert req["method"] == "a2a/task/delegate"
    p = req["params"]
    assert p["target_agent"] == "evo"
    assert p["task_type"] == "set_goal"
    assert p["message"] == "回复正文不得混入执行旁白", "目标原话逐字转达"
    assert p["trace_id"].startswith("soulmate-evo-"), "可溯源 trace_id"
    # 工具账本（真源）里能看到 evo 原话回执（转达不冒名）
    evos = [t for t in tools if t["name"] == "evo_communicate"]
    assert evos, "evo_communicate 必须进工具账本"
    assert "evo: 目标已收" in evos[0]["result_preview"]
    assert resp == "好的，已转达 evo。"


# ════════════════════════════════════════════════════════════════
# 2. 回执透传（转达不冒名）+ 失败可见（错误/不可达/无应答）
# ════════════════════════════════════════════════════════════════

def test_reply_passthrough_verbatim(tmp_path, monkeypatch):
    _stub_post(monkeypatch, payload={
        "result": {"task_id": "t-2", "result_data": {"reply": "（evo 原话）进度：3/5"}}})
    agent = _wire_agent(tmp_path, [])
    out = agent._evo_communicate_dispatch({"action": "query_status"})
    assert out == "（evo 原话）进度：3/5", "回执=evo 原话逐字透传，不改写不冒名"


def test_default_action_and_empty_content(tmp_path, monkeypatch):
    calls = _stub_post(monkeypatch)
    agent = _wire_agent(tmp_path, [])
    agent._evo_communicate_dispatch({})
    assert calls[0]["json"]["params"]["task_type"] == "query_status"
    assert calls[0]["json"]["params"]["message"] == "query_status"


def test_error_paths_visible(tmp_path, monkeypatch):
    agent = _wire_agent(tmp_path, [])
    _stub_post(monkeypatch, payload={"error": {"code": -32601, "message": "unknown"}})
    out = agent._evo_communicate_dispatch({"action": "explain", "content": "x"})
    assert "evo 通道错误" in out, "A2A 错误必须可见"
    _stub_post(monkeypatch, exc=RuntimeError("conn refused"))
    out = agent._evo_communicate_dispatch({"action": "query_status"})
    assert "evo 通道不可达" in out, "通道故障必须可见（禁静默空回复）"
    _stub_post(monkeypatch, payload={"result": {"task_id": "t-3", "result_data": {}}})
    out = agent._evo_communicate_dispatch({"action": "query_status"})
    assert "evo 无应答" in out and "t-3" in out, "无回执如实报（带 task_id 可溯源）"


# ════════════════════════════════════════════════════════════════
# 3. 五登记点接线（写了≠接线了：schema/目录/双分发/方法体）
# ════════════════════════════════════════════════════════════════

class TestRegistrationWiring:
    def test_five_registration_points(self):
        run_src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        code_src = inspect.getsource(SoulMateAgent._code_mode_tool_call)
        file_src = Path(inspect.getsourcefile(SoulMateAgent)).read_text(encoding="utf-8")
        assert '"name": "evo_communicate"' in run_src            # ① schema 登记
        assert '"enum": ["set_goal", "query_status", "explain"]' in run_src
        assert 'func_name == "evo_communicate"' in code_src       # ② 批内分发
        assert run_src.count('func_name == "evo_communicate"') == 1  # ③ 主链分发（恰一处）
        assert "def _evo_communicate_dispatch" in file_src        # ④ 方法体
        assert "- evo_communicate: 与进化协调者 evo 对话" in file_src  # ⑤ 目录（system prompt）
