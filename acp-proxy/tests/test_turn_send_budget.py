# -*- coding: utf-8 -*-
"""续写×重试联合发送预算专项（2026-10-01 多轮挂账项销账）。

问题（本轮修复对象）：续写（最多MAX_CONTINUATION+1段）与HTTP重试（每段
MAX_ATTEMPTS发）在chat_stream/chat_stream_with_tools/chat()三路各自独立计数、
互不知情——4段×3发=理论12发/逻辑回合无联合上界，极端截断+限流一轮可狂发十余个
HTTP请求（latency与配额双杀）。降级链切换/环绕再叠乘上界更不可控。

方案（单一真源=utils.llm_safety.TurnSendBudget，kilocode双限封顶语义）：
- MAX_TURN_SENDS发硬顶/逻辑回合：每次真实HTTP发送前扣1（spend()），余额不足
  =不再发送，显式失败/显式截断标记（AIHawk"截断必须显式标记"），不静默不多发
- 预算对象随递归线程传递（与_chain同款）：重试/续写/降级链切换共享同一额度
- 决策点前置守卫（budget.remaining > 0）：预算已尽不再sleep干等后才发现发不了

覆盖：
1. TurnSendBudget单元语义（spend/remaining/耗尽不扣减/默认MAX_TURN_SENDS）
2. chat_stream：429×2+length极端序列发送数=硬顶、显式失败可见、已流出内容保留；
   预算截断标记与次数截断标记分流（旧标记契约不变）；普通回合单发零变化
3. chat_stream_with_tools：同款硬顶+预算标记分流；tool_calls回合零变化
4. chat()：硬顶+失败可见；预算用尽保留已累积输出（不吞成"[LLM错误:]"）；
   omission重发预算用尽同样保留
5. 接线钉：_budget=budget全递归线程/每发送点spend/7个链切换全守卫/标记计数/
   单一真源无本地副本
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.llm_engine import LLMEngine
from utils.llm_retry import MAX_ATTEMPTS
from utils.llm_safety import MAX_CONTINUATION, MAX_TURN_SENDS, TurnSendBudget
from utils.model_fallback import FallbackChain

HERE = Path(__file__).parent.parent


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


# ────────────────────────────── 测试替身 ──────────────────────────────


class _FakeResp:
    """流式/非流式两用假响应"""

    def __init__(self, status=200, body=b"", chunks=None, json_data=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._body = body
        self._chunks = list(chunks) if chunks is not None else ([body] if body else [])
        self._json = json_data
        self.text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)

    async def aread(self):
        return self._body

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass

    def json(self):
        if self._json is not None:
            return self._json
        return json.loads(self.text or "{}")


def _sse_text(text, finish="stop", usage=None):
    ev = {"choices": [{"delta": {"content": text}, "finish_reason": finish}]}
    if usage:
        ev["usage"] = usage
    return _FakeResp(chunks=[
        b"data: " + json.dumps(ev, ensure_ascii=False).encode() + b"\n\n",
        b"data: [DONE]\n\n",
    ])


def _sse_tools(name="exec", args='{"cmd": "ls"}'):
    ev = {"choices": [{"delta": {"tool_calls": [{
        "index": 0, "id": "call-1", "type": "function",
        "function": {"name": name, "arguments": args},
    }]}, "finish_reason": "tool_calls"}]}
    return _FakeResp(chunks=[
        b"data: " + json.dumps(ev).encode() + b"\n\n",
        b"data: [DONE]\n\n",
    ])


def _err(status, body=b"boom", headers=None):
    return _FakeResp(status=status, body=body, headers=headers)


def _json_ok(content="OK", finish="stop", usage=None):
    data = {"choices": [{"message": {"content": content}, "finish_reason": finish}]}
    if usage:
        data["usage"] = usage
    return _FakeResp(json_data=data)


def _install(monkeypatch, responses):
    """安装fake client（记录每次payload）；响应队列末位重复"""
    calls = []
    state = {"i": 0}

    class _C:
        def __init__(self, base_url="", headers=None, **kw):
            self.base_url = base_url
            self.headers = dict(headers or {})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aclose(self):
            pass

        def _next(self):
            idx = min(state["i"], len(responses) - 1)
            state["i"] += 1
            return responses[idx]

        def _record(self, payload):
            calls.append({"model": (payload or {}).get("model")})

        def build_request(self, method, url, **kw):
            return kw.get("json") or {}

        async def send(self, req, **kw):
            self._record(req)
            item = self._next()
            if isinstance(item, BaseException):
                raise item
            return item

        async def post(self, url, **kw):
            self._record(kw.get("json"))
            item = self._next()
            if isinstance(item, BaseException):
                raise item
            return item

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _C)
    return calls


def _prep(monkeypatch, responses):
    calls = _install(monkeypatch, responses)
    sleeps = []

    async def _nosleep(delay):
        sleeps.append(delay)

    async def _nopark(exc, cancel_event=None):
        return None

    monkeypatch.setattr("agent.llm_engine.asyncio.sleep", _nosleep)
    monkeypatch.setattr("agent.llm_engine.network_state.maybe_park", _nopark)
    monkeypatch.setattr(
        "agent.llm_engine.load_fallback_chain", lambda **kw: FallbackChain()
    )
    return calls, sleeps


def _engine():
    return LLMEngine(api_key="k", base_url="http://fake/v1", model="mimo-test")


_BUDGET_MARKER = "\n\n[输出因max_tokens截断，本回合发送预算已用尽]"
_DEPTH_MARKER = "\n\n[输出因max_tokens截断，续写次数已用尽]"


# ════════════════════════════════════════════════════════════════
# 守护1：TurnSendBudget单元语义
# ════════════════════════════════════════════════════════════════


class TestBudgetUnit:
    def test_default_limit_is_max_turn_sends(self):
        b = TurnSendBudget()
        assert b.limit == MAX_TURN_SENDS
        assert b.remaining == MAX_TURN_SENDS

    def test_spend_decrements_and_exhausts(self):
        b = TurnSendBudget(limit=2)
        assert b.spend() is True and b.remaining == 1
        assert b.spend() is True and b.remaining == 0
        assert b.spend() is False and b.remaining == 0  # 耗尽不扣减
        assert b.spent == 2

    def test_cap_is_meaningful_vs_theoretical(self):
        """硬顶必须真正小于理论最坏（4段×5发=20，10.1后），否则封顶形同虚设"""
        assert MAX_TURN_SENDS < (MAX_CONTINUATION + 1) * MAX_ATTEMPTS


# ════════════════════════════════════════════════════════════════
# 守护2：chat_stream联合封顶
# ════════════════════════════════════════════════════════════════


class TestChatStreamJointCap:
    def test_cap_bounds_retry_x_continuation_sends(self, monkeypatch):
        """429×2+length极端序列：理论12发，实际=硬顶，失败显式可见"""
        responses = [
            _err(429), _err(429), _sse_text("S1", finish="length"),
            _err(429), _err(429), _sse_text("S2", finish="length"),
            _err(429), _err(429), _sse_text("S3", finish="length"),
            _err(429), _err(429), _err(429),
        ]
        calls, sleeps = _prep(monkeypatch, responses)
        out = []
        async def _go():
            async for d in _engine().chat_stream([{"role": "user", "content": "hi"}]):
                out.append(d)
        run(_go())
        assert len(calls) == MAX_TURN_SENDS  # 硬顶封口，不发第13发（10.1后硬顶12）
        text = "".join(d for d in out if isinstance(d, str))
        assert "S1" in text and "S2" in text  # 已流出内容保留
        assert "[LLM错误:" in text  # 失败必须可见
        assert sleeps, "重试路径应有真实退避记录"

    def test_budget_marker_when_continuation_blocked(self, monkeypatch):
        """预算用尽挡在续写决策点：显式预算截断标记（区别于次数标记）"""
        calls, _ = _prep(monkeypatch, [
            _sse_text("S1", finish="length"),
            _sse_text("S2", finish="length"),
        ])
        out = []
        async def _go():
            async for d in _engine().chat_stream(
                [{"role": "user", "content": "hi"}],
                _budget=TurnSendBudget(limit=2),
            ):
                out.append(d)
        run(_go())
        assert len(calls) == 2
        assert out[-1] == _BUDGET_MARKER
        assert "".join(out) == "S1S2" + _BUDGET_MARKER

    def test_depth_marker_contract_unchanged(self, monkeypatch):
        """次数用尽（预算充足）：旧标记逐字不变（回归钉）"""
        calls, _ = _prep(monkeypatch, [_sse_text("S", finish="length")])
        out = []
        async def _go():
            async for d in _engine().chat_stream([{"role": "user", "content": "hi"}]):
                out.append(d)
        run(_go())
        assert len(calls) == MAX_CONTINUATION + 1
        assert out[-1] == _DEPTH_MARKER

    def test_normal_turn_single_send_unchanged(self, monkeypatch):
        calls, _ = _prep(monkeypatch, [_sse_text("hello", finish="stop")])
        out = []
        async def _go():
            async for d in _engine().chat_stream([{"role": "user", "content": "hi"}]):
                out.append(d)
        run(_go())
        assert len(calls) == 1
        assert out == ["hello"]

    def test_send_point_guard_is_explicit(self, monkeypatch):
        """发送点硬守卫：预算0发时直接可见失败，绝不发出请求"""
        calls, _ = _prep(monkeypatch, [_sse_text("X", finish="stop")])
        out = []
        async def _go():
            async for d in _engine().chat_stream(
                [{"role": "user", "content": "hi"}],
                _budget=TurnSendBudget(limit=0),
            ):
                out.append(d)
        run(_go())
        assert calls == []
        assert out == ["\n[LLM错误: 本回合HTTP发送预算已用尽（续写×重试联合封顶）]"]


# ════════════════════════════════════════════════════════════════
# 守护3：chat_stream_with_tools联合封顶
# ════════════════════════════════════════════════════════════════


class TestWithToolsJointCap:
    def test_cap_bounds_retry_x_continuation_sends(self, monkeypatch):
        responses = [
            _err(429), _err(429), _sse_text("S1", finish="length"),
            _err(429), _err(429), _sse_text("S2", finish="length"),
            _err(429), _err(429), _sse_text("S3", finish="length"),
            _err(429), _err(429), _err(429),
        ]
        calls, _ = _prep(monkeypatch, responses)
        out = []
        async def _go():
            async for d in _engine().chat_stream_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "exec"}}],
            ):
                out.append(d)
        run(_go())
        assert len(calls) == MAX_TURN_SENDS
        text = "".join(d for d in out if isinstance(d, str))
        assert "S1" in text and "S2" in text
        assert "[LLM错误:" in text
        assert not any(isinstance(d, dict) and "tool_calls" in d for d in out)

    def test_budget_marker_when_continuation_blocked(self, monkeypatch):
        calls, _ = _prep(monkeypatch, [
            _sse_text("S1", finish="length"),
            _sse_text("S2", finish="length"),
        ])
        out = []
        async def _go():
            async for d in _engine().chat_stream_with_tools(
                [{"role": "user", "content": "hi"}],
                _budget=TurnSendBudget(limit=2),
            ):
                out.append(d)
        run(_go())
        assert len(calls) == 2
        texts = [d for d in out if isinstance(d, str)]
        assert texts[-1] == _BUDGET_MARKER

    def test_tool_calls_turn_unchanged(self, monkeypatch):
        calls, _ = _prep(monkeypatch, [_sse_tools()])
        out = []
        async def _go():
            async for d in _engine().chat_stream_with_tools(
                [{"role": "user", "content": "hi"}],
                tools=[{"type": "function", "function": {"name": "exec"}}],
            ):
                out.append(d)
        run(_go())
        assert len(calls) == 1
        tc = [d for d in out if isinstance(d, dict) and "tool_calls" in d]
        assert tc and tc[0]["tool_calls"][0]["function"]["name"] == "exec"


# ════════════════════════════════════════════════════════════════
# 守护4：chat()联合封顶
# ════════════════════════════════════════════════════════════════


class TestChatJointCap:
    def test_cap_bounds_retry_x_continuation_sends(self, monkeypatch):
        responses = [
            _err(429), _err(429), _json_ok("S1", finish="length"),
            _err(429), _err(429), _json_ok("S2", finish="length"),
            _err(429), _err(429), _json_ok("S3", finish="length"),
            _err(429), _err(429), _err(429),
        ]
        calls, _ = _prep(monkeypatch, responses)
        result = run(_engine().chat([{"role": "user", "content": "hi"}]))
        assert len(calls) == MAX_TURN_SENDS
        assert result.startswith("[LLM错误:")  # 失败必须可见

    def test_budget_exhaustion_preserves_accumulated(self, monkeypatch):
        """续写预算用尽：返回已累积输出（不吞成"[LLM错误:]"）"""
        calls, _ = _prep(monkeypatch, [
            _json_ok("S1", finish="length"),
            _json_ok("S2", finish="length"),
            _json_ok("S3", finish="stop"),
        ])
        result = run(_engine().chat(
            [{"role": "user", "content": "hi"}],
            _budget=TurnSendBudget(limit=2),
        ))
        assert len(calls) == 2
        assert result == "S1S2"
        assert "[LLM错误" not in result

    def test_omission_regen_budget_exhaustion_preserves(self, monkeypatch):
        calls, _ = _prep(monkeypatch, [
            _json_ok("# ... existing code ...", finish="stop"),
        ])
        result = run(_engine().chat(
            [{"role": "user", "content": "hi"}],
            _budget=TurnSendBudget(limit=1),
        ))
        assert len(calls) == 1
        assert "existing code" in result
        assert "[LLM错误" not in result

    def test_send_point_guard_is_explicit(self, monkeypatch):
        calls, _ = _prep(monkeypatch, [_json_ok("X")])
        result = run(_engine().chat(
            [{"role": "user", "content": "hi"}],
            _budget=TurnSendBudget(limit=0),
        ))
        assert calls == []
        assert result == "[LLM错误: 本回合HTTP发送预算已用尽（续写×重试联合封顶）]"


# ════════════════════════════════════════════════════════════════
# 守护5：接线钉（防再断线回死路径）+ 单一真源
# ════════════════════════════════════════════════════════════════


class TestWiringPins:
    def test_budget_threaded_through_all_recursion(self):
        """10个递归调用点（chat_stream 6 + with_tools 4）全部线程预算"""
        assert _src("agent/llm_engine.py").count("_budget=budget") == 10

    def test_every_send_point_spends(self):
        """三路每真实HTTP发送点前都有spend()守卫"""
        src = _src("agent/llm_engine.py")
        assert src.count("budget.spend()") == 3
        assert "TurnSendBudget" in src

    def test_all_chain_switches_budget_gated(self):
        """7个降级链切换决策点全部先看预算（没额度不切、不空转）"""
        src = _src("agent/llm_engine.py")
        assert src.count("chain.try_advance(") == 7
        assert src.count("budget.remaining > 0 and chain.try_advance(") == 7

    def test_retry_gates_consult_budget(self):
        src = _src("agent/llm_engine.py")
        assert "if _wait is not None and budget.remaining > 0:" in src
        assert "if _wait is None or budget.remaining <= 0:" in src

    def test_markers_explicit_and_distinct(self):
        """次数标记2处逐字不变（回归钉）+ 预算标记2处（截断必须显式标记）"""
        src = _src("agent/llm_engine.py")
        assert src.count("[输出因max_tokens截断，续写次数已用尽]") == 2
        assert src.count("[输出因max_tokens截断，本回合发送预算已用尽]") == 2

    def test_single_source_no_local_copy(self):
        """预算类唯一权威=utils.llm_safety（防镜像漂移）"""
        src = _src("agent/llm_engine.py")
        assert "class TurnSendBudget" not in src
        imp = src.split("from utils.llm_safety import", 1)[1].split(")", 1)[0]
        assert "TurnSendBudget" in imp

    def test_legacy_flaws_still_gone(self):
        """旧反模式绝迹（回归钉，与test_llm_retry_policy同款）"""
        src = _src("agent/llm_engine.py")
        assert "backoff_ms(" not in src
        assert "range(3)" not in src
        assert "for _http_retry in range(MAX_ATTEMPTS)" in src
        assert "for _retry in range(MAX_ATTEMPTS)" in src
