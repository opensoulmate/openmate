# -*- coding: utf-8 -*-
"""P0-4 ScheduleWakeup（noop自报+streak折叠）acp-proxy工具接线测试。

调研来源：claude-code SDK ScheduleWakeup（feature-matrix/10-claude-code-source.md #4）：
agent自主定下次唤醒延迟（钳到[60,3600]）+reason必填+noop自报+连续noop折叠streak+
stop结束循环——"用户不知道agent在干嘛"的教科书答案（SUMMARY.md P0-4价值最高项）。

分工边界：agent/steering.py ActivityStore已有turn级**推断式**noop（本轮无工具无文本），
本工具是**声明式**自报（agent明说"这次检查没进展"+自定下次唤醒时刻），状态落opensoul
will/wakeup（SQLite持久化+折叠视图API）。

解决"写了≠接线了"：
- 工具schema必须真实进入LLM工具面（CapturingLLM捕获tools参数断言）
- dispatch必须真实POST到opensoul /api/will/wakeup/schedule（FakeAsyncClient捕获断言）
- 失败必须可见：opensoul不可用/非200 → 显式⚠️错误文本进tool result
无网络依赖：httpx.AsyncClient整体替身。
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.soulmate_agent import SoulMateAgent
from tests.test_steering import FakeGate, make_agent, run


def _tc(name, args, tc_id="tc1"):
    return {"tool_calls": [{
        "id": tc_id,
        "function": {"name": name, "arguments": json.dumps(args)},
    }]}


class CapturingLLM:
    """脚本化LLM并捕获每轮tools参数（断言schedule_wakeup真实进入工具面）"""

    def __init__(self, scripts):
        self.scripts = list(scripts)
        self.seen_tools = []
        self.seen_messages = []

    def chat_stream_with_tools(self, messages=None, tools=None, system_prompt=None, **kw):
        self.seen_tools.append(list(tools or []))
        self.seen_messages.append([dict(m) for m in (messages or [])])
        step = self.scripts.pop(0) if self.scripts else []

        async def gen():
            for item in step:
                yield item
        return gen()


class FakeResp:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.text = text or json.dumps(self._body, ensure_ascii=False)

    def json(self):
        return self._body


class FakeAsyncClient:
    """httpx.AsyncClient替身：捕获POST，按脚本返回/抛错"""
    calls = []          # [(url, payload)]
    next_resp = None    # FakeResp 或 Exception
    fail_with = None

    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, **kw):
        FakeAsyncClient.calls.append((url, json))
        if FakeAsyncClient.fail_with is not None:
            raise FakeAsyncClient.fail_with
        return FakeAsyncClient.next_resp or FakeResp(200, {"ok": True})


def _wire(tmp_path, scripts, monkeypatch, resp=None, fail_with=None):
    FakeAsyncClient.calls = []
    FakeAsyncClient.next_resp = resp
    FakeAsyncClient.fail_with = fail_with
    monkeypatch.setattr(
        "agent.soulmate_agent.httpx.AsyncClient", FakeAsyncClient, raising=True)
    agent = make_agent(tmp_path, scripts=scripts)
    agent._permission_gate = FakeGate(allow=True)
    agent.llm_engine = CapturingLLM(scripts)
    return agent


def _tool_names(agent) -> set:
    names = set()
    for tools in agent.llm_engine.seen_tools:
        for t in tools:
            fn = t.get("function") or {}
            if fn.get("name"):
                names.add(fn["name"])
    return names


# ════════════════════════════════════════════════════════════════
# ① 工具面接线：schedule_wakeup 必须真实出现在 LLM tools 参数里
# ════════════════════════════════════════════════════════════════


class TestToolSurface:
    def test_tool_exposed_in_llm_toolset(self, tmp_path, monkeypatch):
        agent = _wire(tmp_path, [["done"]], monkeypatch)
        run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        assert "schedule_wakeup" in _tool_names(agent)

    def test_tool_schema_claude_code_contract(self, tmp_path, monkeypatch):
        agent = _wire(tmp_path, [["done"]], monkeypatch)
        run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        spec = None
        for tools in agent.llm_engine.seen_tools:
            for t in tools:
                if (t.get("function") or {}).get("name") == "schedule_wakeup":
                    spec = t["function"]
        assert spec is not None
        props = spec["parameters"]["properties"]
        # claude-code字段面：reason/noop/delay_seconds/stop；reason+noop必填
        assert set(props) == {"reason", "noop", "delay_seconds", "stop"}
        assert set(spec["parameters"]["required"]) == {"reason", "noop"}
        # 语义必须写进工具文档（agent才知道怎么用）：钳制区间+noop+stop
        desc = spec["description"]
        assert "60,3600" in desc.replace("[", "").replace("]", "") or "[60,3600]" in desc
        assert "noop" in desc and "折叠" in desc and "stop" in desc


# ════════════════════════════════════════════════════════════════
# ② dispatch接线：真实POST opensoul + 结果语义回给模型
# ════════════════════════════════════════════════════════════════


class TestDispatch:
    def test_posts_to_opensoul_wakeup_schedule(self, tmp_path, monkeypatch):
        resp = FakeResp(200, {
            "streak": 2, "folded": True, "noop": True,
            "requested_delay_s": 5, "delay_s": 60, "clamped": True,
            "next_wakeup_at": 123.0, "active": True,
        })
        call = _tc("schedule_wakeup", {
            "reason": "检查构建队列无新任务", "noop": True, "delay_seconds": 5,
        })
        agent = _wire(tmp_path, [[call], ["ok"]], monkeypatch, resp=resp)
        resp_obj, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        assert FakeAsyncClient.calls, "dispatch必须真实发起HTTP调用"
        url, payload = FakeAsyncClient.calls[0]
        assert url.endswith("/api/will/wakeup/schedule")
        assert payload["reason"] == "检查构建队列无新任务"
        assert payload["noop"] is True and payload["delay_seconds"] == 5
        assert payload["agent_id"] == "soulmate" and payload["session_id"] == "s1"

    def test_success_result_visible_to_model(self, tmp_path, monkeypatch):
        resp = FakeResp(200, {
            "streak": 2, "folded": True, "noop": True,
            "requested_delay_s": 5, "delay_s": 60, "clamped": True,
            "next_wakeup_at": 123.0,
        })
        call = _tc("schedule_wakeup", {"reason": "r", "noop": True, "delay_seconds": 5})
        agent = _wire(tmp_path, [[call], ["ok"]], monkeypatch, resp=resp)
        _, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        wu = [t for t in tools if t["name"] == "schedule_wakeup"]
        assert wu, "all_tool_calls必须记录schedule_wakeup"
        preview = wu[0]["result_preview"]
        assert "唤醒已调度" in preview
        assert "已钳制" in preview          # 钳制必须显式可见
        assert "streak=2" in preview
        assert "折叠区" in preview           # 连续空转进入折叠区提示

    def test_stop_note_visible(self, tmp_path, monkeypatch):
        resp = FakeResp(200, {
            "streak": 0, "folded": False, "noop": False,
            "requested_delay_s": 60, "delay_s": 60, "clamped": False,
            "next_wakeup_at": None, "stop": True,
        })
        call = _tc("schedule_wakeup", {"reason": "任务完成", "noop": False, "stop": True})
        agent = _wire(tmp_path, [[call], ["ok"]], monkeypatch, resp=resp)
        _, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        wu = [t for t in tools if t["name"] == "schedule_wakeup"][0]
        assert "唤醒循环已结束" in wu["result_preview"]


# ════════════════════════════════════════════════════════════════
# ③ 失败必须可见（mem0 §1.1）：opensoul不可用/非200 → 显式错误
# ════════════════════════════════════════════════════════════════


class TestFailureVisible:
    def test_opensoul_down_returns_visible_error(self, tmp_path, monkeypatch):
        call = _tc("schedule_wakeup", {"reason": "r", "noop": True})
        agent = _wire(
            tmp_path, [[call], ["ok"]], monkeypatch,
            fail_with=RuntimeError("connection refused"))
        _, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        wu = [t for t in tools if t["name"] == "schedule_wakeup"][0]
        assert "⚠️ wakeup调度不可用" in wu["result_preview"]

    def test_non_200_returns_visible_error(self, tmp_path, monkeypatch):
        call = _tc("schedule_wakeup", {"reason": "", "noop": True})
        agent = _wire(
            tmp_path, [[call], ["ok"]], monkeypatch,
            resp=FakeResp(400, {}, text="reason必填"))
        _, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        wu = [t for t in tools if t["name"] == "schedule_wakeup"][0]
        assert "schedule_wakeup被拒绝(400)" in wu["result_preview"]

    def test_no_silent_success_on_failure(self, tmp_path, monkeypatch):
        """失败路径绝不允许出现"唤醒已调度"字样（防止假成功）"""
        call = _tc("schedule_wakeup", {"reason": "r", "noop": True})
        agent = _wire(tmp_path, [[call], ["ok"]], monkeypatch, fail_with=OSError("x"))
        _, tools = run(agent._run_llm_with_tools([{"role": "user", "content": "go"}], "s1"))
        wu = [t for t in tools if t["name"] == "schedule_wakeup"][0]
        assert "唤醒已调度" not in wu["result_preview"]


# ════════════════════════════════════════════════════════════════
# ④ 源码级接线证据（反向断言防脱敏破坏）
# ════════════════════════════════════════════════════════════════


class TestSourceWiring:
    def _src(self) -> str:
        return (Path(__file__).parent.parent / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")

    def test_tool_and_dispatch_present(self):
        text = self._src()
        assert '"name": "schedule_wakeup"' in text            # 工具schema
        assert 'elif func_name == "schedule_wakeup":' in text  # dispatch分支
        assert "/api/will/wakeup/schedule" in text             # opensoul端点
        assert "builtin_tools.append(wakeup_tool)" in text     # 进入工具面

    def test_tool_not_trapped_in_dead_branch(self):
        """dispatch分支必须在主工具循环elif链内（batch_execute同链）"""
        text = self._src()
        i_wu = text.index('elif func_name == "schedule_wakeup":')
        i_batch = text.index('elif func_name == "batch_execute":')
        assert i_wu < i_batch, "schedule_wakeup必须与batch_execute同一条主循环elif链"
