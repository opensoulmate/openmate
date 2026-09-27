# -*- coding: utf-8 -*-
"""流式纯文本截断自动续写专项（2026-09-28 P2——上轮遗留#2点名候选）

问题（本轮修复对象）：chat()已具备Aider续写+omission防护（上轮17851c7b），
但**流式**真实传输路径chat_stream/chat_stream_with_tools的纯文本
finish_reason=length截断仍静默半截收尾——soulmate真实回复、engine
_stream_to_client、discussion_engine全走流式，用户看到的是被腰斩的回答
且无任何标记（AIHawk"截断必须显式标记"违反）。

方案（与chat()同一策略单一真源utils.llm_safety）：
- finish_reason=length纯文本截断→build_continuation_messages按段续写，
  各段流式无缝拼接（递归chat_stream*，段语义：assistant只放本段防重复入history）
- 次数用尽(MAX_CONTINUATION)/空段→显式截断标记"\n\n[输出因max_tokens截断…]"
- 已取消(cancel_event)→不续写不标记
- with_tools的tool_calls截断仍走_truncated标记调用方决策（契约不变，回归钉）
- with_tools续写段不发射中间usage（防消费方同一逻辑回合双重backfill）

覆盖：
1. chat_stream行为（fake SSE传输）：stop不变/length续写拼流+续写请求形状/
   深度有界/空段不续写/取消不续写/无[DONE]流耗尽路径同样续写
2. chat_stream_with_tools行为：纯文本续写+usage只发一次/tool_calls截断
   _truncated不续写（回归）/usage先于tool_calls发射顺序（回归）/深度用尽标记
3. 消费方契约：按engine._agent_loop消费模式拼装完整回复
4. 接线钉：两方法源码必须真实调用build_continuation_messages/check_finish_reason/
   MAX_CONTINUATION（防再断线回死路径）+续写文案不在llm_engine（单一真源）
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.llm_engine import LLMEngine
from utils.llm_safety import MAX_CONTINUATION

HERE = Path(__file__).parent.parent


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# fake流式传输：send(stream=True)返回可编排SSE事件序列，捕获每次payload
# 事件形态：dict→data:{json}；"[DONE]"→data:[DONE]；callable→执行（可设cancel）
# ════════════════════════════════════════════════════════════════

def _sse(data: str) -> bytes:
    return f"data: {data}\n\n".encode()


def _delta_chunk(text=None, finish_reason=None, usage=None, tool_calls=None):
    delta = {}
    if text is not None:
        delta["content"] = text
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    obj = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
    if usage is not None:
        obj["usage"] = usage
    return obj


class _FakeStreamResp:
    def __init__(self, events, status_code=200, body=b""):
        self.status_code = status_code
        self._events = events
        self._body = body

    async def aiter_bytes(self):
        for ev in self._events:
            if callable(ev):
                ev()
                continue
            if ev == "[DONE]":
                yield _sse("[DONE]")
            else:
                yield _sse(json.dumps(ev, ensure_ascii=False))

    async def aread(self):
        return self._body

    async def aclose(self):
        pass


def _install_stream_client(monkeypatch, responses: list) -> list:
    """安装fake流式client；返回payload记录列表（深快照防事后篡改）"""
    payloads: list[dict] = []
    state = {"calls": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def build_request(self, method, url, **kw):
            return kw.get("json") or {}

        async def send(self, req, **kw):
            payloads.append(json.loads(json.dumps(req)))
            idx = min(state["calls"], len(responses) - 1)
            state["calls"] += 1
            r = responses[idx]
            return r() if callable(r) else r

    monkeypatch.setattr("agent.llm_engine.httpx.AsyncClient", _FakeClient)
    return payloads


def _engine():
    return LLMEngine(api_key="k", base_url="http://fake/v1", model="mimo-test")


def _run(coro):
    return asyncio.run(coro)


async def _collect_stream(engine, monkeypatch, responses, messages=None,
                          cancel_event=None, system_prompt=None):
    payloads = _install_stream_client(monkeypatch, responses)
    out = []
    async for delta in engine.chat_stream(
        messages or [{"role": "user", "content": "hi"}],
        cancel_event, system_prompt,
    ):
        out.append(delta)
    return out, payloads


async def _collect_stream_tools(engine, monkeypatch, responses, messages=None,
                                tools=None):
    payloads = _install_stream_client(monkeypatch, responses)
    out = []
    async for item in engine.chat_stream_with_tools(
        messages or [{"role": "user", "content": "hi"}],
        tools=tools,
    ):
        out.append(item)
    return out, payloads


_CONT_MSG = "Continue from where you left off. Do not repeat any content already provided."


# ════════════════════════════════════════════════════════════════
# 守护1：chat_stream续写行为（fake SSE传输真实调用chat_stream）
# ════════════════════════════════════════════════════════════════

class TestChatStreamContinuation:
    def test_stop_single_request_unchanged(self, monkeypatch):
        """finish_reason=stop：单发、无续写、无标记（回归钉）"""
        deltas, payloads = _run(_collect_stream(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk("hello "),
                _delta_chunk("world"),
                _delta_chunk(None, finish_reason="stop"),
                {"usage": {"prompt_tokens": 3, "completion_tokens": 2}},
                "[DONE]",
            ]),
        ]))
        assert deltas == ["hello ", "world"]
        assert len(payloads) == 1
        assert payloads[0]["messages"][0]["role"] == "system"
        assert payloads[0]["messages"][1] == {"role": "user", "content": "hi"}

    def test_length_truncation_continues_seamlessly(self, monkeypatch):
        """finish_reason=length：按段续写，delta无缝拼接，续写请求形状=段语义"""
        deltas, payloads = _run(_collect_stream(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk('{"action": "'),
                _delta_chunk(None, finish_reason="length"),
                "[DONE]",
            ]),
            _FakeStreamResp([
                _delta_chunk('continue", "reason": "继续"}'),
                _delta_chunk(None, finish_reason="stop"),
                {"usage": {"prompt_tokens": 9, "completion_tokens": 4}},
                "[DONE]",
            ]),
        ]))
        assert "".join(deltas) == '{"action": "continue", "reason": "继续"}'
        assert len(payloads) == 2
        # 续写请求：原始user + assistant本段 + user续写指令（段语义，不重复入history）
        msgs2 = payloads[1]["messages"]
        assert msgs2[1] == {"role": "user", "content": "hi"}
        assert msgs2[2] == {"role": "assistant", "content": '{"action": "'}
        assert msgs2[3] == {"role": "user", "content": _CONT_MSG}
        # system注入契约保留
        assert msgs2[0]["role"] == "system"
        # 无截断标记（续写补齐=完整输出）
        assert not any("截断" in d for d in deltas)

    def test_depth_bounded_max_continuation_plus_one(self, monkeypatch):
        """一直length：请求总数=MAX_CONTINUATION+1，末尾显式截断标记"""
        seg = lambda i: _FakeStreamResp([
            _delta_chunk(f"seg{i}"),
            _delta_chunk(None, finish_reason="length"),
            "[DONE]",
        ])
        deltas, payloads = _run(_collect_stream(_engine(), monkeypatch, [
            seg(1), seg(2), seg(3), seg(4),
        ]))
        assert len(payloads) == MAX_CONTINUATION + 1
        text = "".join(deltas)
        assert "seg1" in text and "seg4" in text
        # 次数用尽必须显式标记（AIHawk：截断必须显式标记，不静默半截）
        assert deltas[-1] == "\n\n[输出因max_tokens截断，续写次数已用尽]"

    def test_empty_segment_no_continuation_but_marked(self, monkeypatch):
        """length但零文本（纯reasoning等）：不发无意义续写，仍显式标记"""
        deltas, payloads = _run(_collect_stream(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk(None, finish_reason="length"),
                "[DONE]",
            ]),
        ]))
        assert len(payloads) == 1
        assert deltas == ["\n\n[输出因max_tokens截断，续写次数已用尽]"]

    def test_cancel_skips_continuation_and_marker(self, monkeypatch):
        """cancel_event：流中止即返回，不续写不标记（用户主动停止≠截断）"""
        ev = asyncio.Event()
        ev.set()
        deltas, payloads = _run(_collect_stream(
            _engine(), monkeypatch,
            [_FakeStreamResp([
                _delta_chunk("never"),
                _delta_chunk(None, finish_reason="length"),
                "[DONE]",
            ])],
            cancel_event=ev,
        ))
        # 取消在首个chunk消费前生效：零delta、不续写、无标记
        assert deltas == []
        assert len(payloads) == 1

    def test_stream_exhaustion_without_done_also_continues(self, monkeypatch):
        """无[DONE]非标准provider：流耗尽收尾路径同样触发续写（两收尾路径同真源）"""
        deltas, payloads = _run(_collect_stream(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk("前半"),
                _delta_chunk(None, finish_reason="length"),
            ]),
            _FakeStreamResp([
                _delta_chunk("后半"),
                _delta_chunk(None, finish_reason="stop"),
            ]),
        ]))
        assert "".join(deltas) == "前半后半"
        assert len(payloads) == 2

    def test_multi_segment_chain_accumulates(self, monkeypatch):
        """多段链式续写：第3段的续写请求带上前两段的完整段链"""
        seg = lambda i, fr: _FakeStreamResp([
            _delta_chunk(f"P{i}"),
            _delta_chunk(None, finish_reason=fr),
            "[DONE]",
        ])
        deltas, payloads = _run(_collect_stream(_engine(), monkeypatch, [
            seg(1, "length"), seg(2, "length"), seg(3, "stop"),
        ]))
        assert "".join(deltas) == "P1P2P3"
        assert len(payloads) == 3
        # 第3发请求：assistant只放第2段（段语义），不是P1P2拼接（防重复）
        msgs3 = payloads[2]["messages"]
        assert msgs3[-2] == {"role": "assistant", "content": "P2"}
        assert msgs3[-1] == {"role": "user", "content": _CONT_MSG}
        msgs2 = payloads[1]["messages"]
        assert msgs2[-2] == {"role": "assistant", "content": "P1"}


# ════════════════════════════════════════════════════════════════
# 守护2：chat_stream_with_tools——纯文本续写 + tool_calls契约回归
# ════════════════════════════════════════════════════════════════

class TestChatStreamWithToolsContinuation:
    def test_pure_text_length_continues(self, monkeypatch):
        """纯文本length：续写拼接 + usage只发一次（最终段）+ 无tool_calls"""
        items, payloads = _run(_collect_stream_tools(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk("回答上半"),
                _delta_chunk(None, finish_reason="length"),
                {"usage": {"prompt_tokens": 5, "completion_tokens": 2}},
                "[DONE]",
            ]),
            _FakeStreamResp([
                _delta_chunk("下半补齐"),
                _delta_chunk(None, finish_reason="stop"),
                {"usage": {"prompt_tokens": 9, "completion_tokens": 3}},
                "[DONE]",
            ]),
        ], tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}]))
        texts = [i for i in items if isinstance(i, str)]
        assert "".join(texts) == "回答上半下半补齐"
        assert len(payloads) == 2
        # usage契约：续写段不发射中间usage（防同一逻辑回合双重backfill）
        usage_items = [i for i in items if isinstance(i, dict) and "usage" in i]
        assert len(usage_items) == 1
        assert usage_items[0]["usage"]["prompt_tokens"] == 9
        assert not any(isinstance(i, dict) and "tool_calls" in i for i in items)
        # 续写请求保留tools（模型可在续写段发起工具调用）
        assert payloads[1].get("tools")
        assert payloads[1]["messages"][-2]["role"] == "assistant"
        assert payloads[1]["messages"][-1] == {"role": "user", "content": _CONT_MSG}

    def test_tool_calls_length_truncation_keeps_truncated_contract(self, monkeypatch):
        """tool_calls截断：仍走_truncated标记+不递归续写（调用方决策契约不变）"""
        tc = [{"index": 0, "id": "call_1", "type": "function",
               "function": {"name": "write_file", "arguments": '{"path": "a.py", "content": "x'}}]
        items, payloads = _run(_collect_stream_tools(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk(None, tool_calls=tc),
                _delta_chunk(None, finish_reason="length"),
                "[DONE]",
            ]),
        ], tools=[{"type": "function", "function": {"name": "write_file", "parameters": {}}}]))
        assert len(payloads) == 1  # 不续写
        tc_items = [i for i in items if isinstance(i, dict) and "tool_calls" in i]
        assert len(tc_items) == 1
        assert tc_items[0].get("_truncated") is True

    def test_complete_tool_calls_unchanged(self, monkeypatch):
        """完整tool_calls：单发、无_truncated、无续写（回归钉）"""
        tc = [{"index": 0, "id": "call_1", "type": "function",
               "function": {"name": "t", "arguments": '{"a": 1}'}}]
        items, payloads = _run(_collect_stream_tools(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk(None, tool_calls=tc),
                _delta_chunk(None, finish_reason="tool_calls"),
                "[DONE]",
            ]),
        ], tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}]))
        assert len(payloads) == 1
        tc_items = [i for i in items if isinstance(i, dict) and "tool_calls" in i]
        assert tc_items[0].get("_truncated") is None

    def test_usage_emitted_before_tool_calls_order(self, monkeypatch):
        """发射顺序回归：usage先于tool_calls（消费方收tool_calls即break）"""
        tc = [{"index": 0, "id": "c1", "type": "function",
               "function": {"name": "t", "arguments": "{}"}}]
        items, payloads = _run(_collect_stream_tools(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk(None, tool_calls=tc),
                _delta_chunk(None, finish_reason="tool_calls"),
                {"usage": {"prompt_tokens": 7, "completion_tokens": 1}},
                "[DONE]",
            ]),
        ], tools=[{"type": "function", "function": {"name": "t", "parameters": {}}}]))
        kinds = ["usage" if (isinstance(i, dict) and "usage" in i)
                 else "tool_calls" if (isinstance(i, dict) and "tool_calls" in i)
                 else "text" for i in items]
        assert kinds.index("usage") < kinds.index("tool_calls")

    def test_depth_exhausted_marker_and_single_usage(self, monkeypatch):
        """一直length：MAX_CONTINUATION+1发后显式标记；usage仍只发一次"""
        seg = lambda i: _FakeStreamResp([
            _delta_chunk(f"S{i}"),
            _delta_chunk(None, finish_reason="length"),
            {"usage": {"prompt_tokens": i, "completion_tokens": 1}},
            "[DONE]",
        ])
        items, payloads = _run(_collect_stream_tools(_engine(), monkeypatch, [
            seg(1), seg(2), seg(3), seg(4),
        ]))
        assert len(payloads) == MAX_CONTINUATION + 1
        texts = [i for i in items if isinstance(i, str)]
        assert texts[-1] == "\n\n[输出因max_tokens截断，续写次数已用尽]"
        usage_items = [i for i in items if isinstance(i, dict) and "usage" in i]
        assert len(usage_items) == 1

    def test_cancel_skips_continuation(self, monkeypatch):
        """with_tools取消：不续写不标记"""
        ev = asyncio.Event()
        ev.set()
        payloads = _install_stream_client(monkeypatch, [
            _FakeStreamResp([
                _delta_chunk("x"),
                _delta_chunk(None, finish_reason="length"),
                "[DONE]",
            ]),
        ])

        async def _go():
            out = []
            async for item in _engine().chat_stream_with_tools(
                [{"role": "user", "content": "hi"}], cancel_event=ev,
            ):
                out.append(item)
            return out

        items = _run(_go())
        assert items == []
        assert len(payloads) == 1


# ════════════════════════════════════════════════════════════════
# 守护3：消费方契约（engine._agent_loop消费模式拼装完整回复）
# ════════════════════════════════════════════════════════════════

class TestConsumerContract:
    def test_agent_loop_consumption_assembles_full_reply(self, monkeypatch):
        """按engine._agent_loop的消费模式（str→text_parts，dict tool_calls→break）
        拼装——续写段在消费方无感拼接成完整回复"""
        items, payloads = _run(_collect_stream_tools(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk("第一段回复"),
                _delta_chunk(None, finish_reason="length"),
                {"usage": {"prompt_tokens": 4, "completion_tokens": 2}},
                "[DONE]",
            ]),
            _FakeStreamResp([
                _delta_chunk("，第二段续写完成。"),
                _delta_chunk(None, finish_reason="stop"),
                {"usage": {"prompt_tokens": 8, "completion_tokens": 2}},
                "[DONE]",
            ]),
        ]))
        text_parts: list[str] = []
        tool_calls = None
        for item in items:
            if isinstance(item, str):
                text_parts.append(item)
            elif isinstance(item, dict) and "tool_calls" in item:
                tool_calls = item["tool_calls"]
        full = "".join(text_parts)
        assert full == "第一段回复，第二段续写完成。"
        assert tool_calls is None
        assert not any("截断" in p for p in text_parts)

    def test_stream_to_client_consumption_full_response(self, monkeypatch):
        """engine._stream_to_client消费模式（full_response += delta）同样完整"""
        deltas, payloads = _run(_collect_stream(_engine(), monkeypatch, [
            _FakeStreamResp([
                _delta_chunk("前"),
                _delta_chunk(None, finish_reason="length"),
                "[DONE]",
            ]),
            _FakeStreamResp([
                _delta_chunk("后"),
                _delta_chunk(None, finish_reason="stop"),
                "[DONE]",
            ]),
        ]))
        full_response = ""
        for d in deltas:
            full_response += d
        assert full_response == "前后"
        assert len(payloads) == 2


# ════════════════════════════════════════════════════════════════
# 守护4：接线钉（防再断线回死路径）+ 单一真源
# ════════════════════════════════════════════════════════════════

class TestWiringPins:
    def _method_src(self, name: str) -> str:
        src = _src("agent/llm_engine.py")
        start = src.index(f"async def {name}(")
        nxt = src.find("\n    async def ", start + 10)
        nxt2 = src.find("\n    def ", start + 10)
        ends = [e for e in (nxt, nxt2) if e != -1]
        return src[start:min(ends) if ends else len(src)]

    def test_chat_stream_wired_to_single_source(self):
        ms = self._method_src("chat_stream")
        assert "build_continuation_messages(" in ms
        assert "check_finish_reason(" in ms
        assert "MAX_CONTINUATION" in ms

    def test_chat_stream_with_tools_wired_to_single_source(self):
        ms = self._method_src("chat_stream_with_tools")
        assert "build_continuation_messages(" in ms
        assert "check_finish_reason(" in ms
        assert "MAX_CONTINUATION" in ms

    def test_continuation_depth_bounded_in_both_methods(self):
        for name in ("chat_stream", "chat_stream_with_tools"):
            ms = self._method_src(name)
            assert "_continuation_depth=_continuation_depth + 1" in ms
            assert "_continuation_depth < MAX_CONTINUATION" in ms

    def test_continuation_prompt_single_source_no_copy(self):
        """续写/重发文案唯一权威=utils.llm_safety（防镜像漂移，上轮已销账三镜像）"""
        src = _src("agent/llm_engine.py")
        assert "Continue from where you left off" not in src
        assert "from utils.llm_safety import" in src or (
            "build_continuation_messages" in src.split("class ")[0]
        )

    def test_tool_truncated_marker_only_in_with_tools(self):
        """_truncated契约仍在with_tools（本轮不改），chat_stream不引入tool语义"""
        assert "_truncated" in self._method_src("chat_stream_with_tools")
        assert "_truncated" not in self._method_src("chat_stream")

    def test_marker_text_is_explicit(self):
        """显式截断标记存在且两方法一致（AIHawk：截断必须显式标记）"""
        src = _src("agent/llm_engine.py")
        assert src.count("[输出因max_tokens截断，续写次数已用尽]") == 2
