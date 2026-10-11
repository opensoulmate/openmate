# -*- coding: utf-8 -*-
"""路线图17.1 音频输入多模态直路由——"语音直接路由多模态大模型，不走whisper→文本两步"
（用户原要求；MiMo token plan input_audio 可行性查实=周期#98）。

实测provider坑（2026-10-12真实MiMo探针）：reasoning模型在音频输入回合偶发把答案
只写 reasoning_content、content 留空——旧消费面直接丢弃 reasoning_content → 用户
看空回复。故本切片两件套：

① attach_audio_parts：音频附件挂进目标用户消息转 OpenAI input_audio 内容块直发
   （锚点定位=用户文本"[附件已保存到: path]"注记；找不到绝不乱挂）；
② reasoning→content 回退：content 为空且无 tool_calls 时收下 thinking 文本
   （content 正常时不掺、工具轮不掺）。

测试面：
1. attach_audio_parts 单元（锚点/格式映射/兜底/幂等/不动原表）
2. reasoning回退（真实socket SSE/JSON故障面，非mock）
3. _resolve_model 对列表content容噪（复杂度评分不吃音频块）
4. agent接线（真实 _prompt_inner 简单路径：提取→落盘→锚点→wire 消息实证）
5. aux自动随行（plan/label面 fallback_last）与 infection_audit 排除
"""

import asyncio
import base64
import http.server
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402

import agent.soulmate_agent as sa  # noqa: E402
from agent.llm_engine import LLMEngine, attach_audio_parts  # noqa: E402
from tests.test_plan_execution import build_env, run_prompt  # noqa: E402

B64 = base64.b64encode(b"RIFFfake-wav-bytes").decode()
AUDIO_PART = {"path": "/tmp/att/voice.wav", "mime": "audio/wav", "data": B64}


# ════════════════ 1. attach_audio_parts 单元 ════════════════

class TestAttachAudioParts:
    def _msgs(self):
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "你好\n[附件已保存到: /tmp/att/voice.wav]"},
            {"role": "assistant", "content": "收到"},
        ]

    def test_anchor_targeting_and_block_shape(self):
        out = attach_audio_parts(self._msgs(), [AUDIO_PART])
        content = out[1]["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "你好\n[附件已保存到: /tmp/att/voice.wav]"}
        blk = content[1]
        assert blk["type"] == "input_audio"
        assert blk["input_audio"]["data"] == f"data:audio/wav;base64,{B64}"
        assert blk["input_audio"]["format"] == "wav"

    def test_original_list_not_mutated(self):
        msgs = self._msgs()
        out = attach_audio_parts(msgs, [AUDIO_PART])
        assert out is not msgs
        assert isinstance(msgs[1]["content"], str)

    def test_data_url_prefix_not_doubled(self):
        part = {**AUDIO_PART, "data": f"data:audio/wav;base64,{B64}"}
        out = attach_audio_parts(self._msgs(), [part])
        blk = out[1]["content"][1]
        assert blk["input_audio"]["data"] == f"data:audio/wav;base64,{B64}"

    def test_mime_format_mapping_mp3_m4a(self):
        msgs = self._msgs()
        for mime, fmt in (("audio/mpeg", "mp3"), ("audio/mp4", "m4a"), ("audio/flac", "flac")):
            part = {**AUDIO_PART, "mime": mime}
            out = attach_audio_parts(msgs, [part])
            assert out[1]["content"][1]["input_audio"]["format"] == fmt, mime

    def test_unknown_mime_skipped_explicitly(self):
        """未映射格式跳过直发（显式报因禁静默），无块=消息原样"""
        part = {**AUDIO_PART, "mime": "audio/x-unknown-codec"}
        out = attach_audio_parts(self._msgs(), [part])
        assert isinstance(out[1]["content"], str)

    def test_empty_data_skipped(self):
        out = attach_audio_parts(self._msgs(), [{**AUDIO_PART, "data": ""}])
        assert isinstance(out[1]["content"], str)

    def test_no_anchor_no_fallback_untouched(self):
        msgs = [{"role": "user", "content": "无关消息"}]
        out = attach_audio_parts(msgs, [AUDIO_PART])
        assert isinstance(out[0]["content"], str)

    def test_fallback_last_wraps_last_user(self):
        msgs = [{"role": "user", "content": "标题提示词内联用户原话"},
                {"role": "user", "content": "另一条"}]
        out = attach_audio_parts(msgs, [AUDIO_PART], fallback_last=True)
        assert isinstance(out[1]["content"], list)
        assert isinstance(out[0]["content"], str)

    def test_assistant_with_anchor_not_wrapped(self):
        msgs = [{"role": "assistant", "content": "[附件已保存到: /tmp/att/voice.wav]"},
                {"role": "user", "content": "真实用户消息"}]
        out = attach_audio_parts(msgs, [AUDIO_PART], fallback_last=True)
        # assistant带锚点不作目标；fallback_last挂最后一条user
        assert isinstance(out[0]["content"], str)
        assert isinstance(out[1]["content"], list)

    def test_idempotent_on_already_attached(self):
        once = attach_audio_parts(self._msgs(), [AUDIO_PART])
        twice = attach_audio_parts(once, [AUDIO_PART])
        assert isinstance(twice[1]["content"], list)
        assert len(twice[1]["content"]) == 2  # 不重复包装

    def test_none_and_empty_passthrough(self):
        msgs = self._msgs()
        assert attach_audio_parts(msgs, None) is msgs
        assert attach_audio_parts(msgs, []) is msgs

    def test_multiple_audio_blocks_one_message(self):
        parts = [AUDIO_PART, {**AUDIO_PART, "path": "/tmp/att/voice2.wav"}]
        out = attach_audio_parts(self._msgs(), parts)
        assert len(out[1]["content"]) == 3  # text + 2块


# ════════════════ 2. reasoning→content 回退（真实socket） ════════════════

def _sse(*objs, done=True):
    body = "".join(f"data: {json.dumps(o)}\n\n" for o in objs)
    if done:
        body += "data: [DONE]\n\n"
    return body


def _make_handler(state):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            n = len(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            with state["lock"]:
                idx = state["i"]
                state["i"] += 1
                state["req_bytes"].append(n)
            kind, body = state["script"][idx]
            payload = body.encode()
            self.send_response(200)
            if kind == "sse":
                self.send_header("Content-Type", "text/event-stream")
            else:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return H


class _Server:
    def __init__(self, script):
        self.state = {"script": script, "i": 0, "req_bytes": [], "lock": threading.Lock()}
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self.state))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _engine(port):
    return LLMEngine(api_key="sk-test-key", base_url=f"http://127.0.0.1:{port}/v1", model="m-test")


def run(coro):
    return asyncio.run(coro)


REASONING_ONLY = [
    ("sse", _sse(
        {"choices": [{"delta": {"reasoning_content": "嗯，苹果香蕉橘子。"}}]},
        {"choices": [{"delta": {"reasoning_content": "一二三。"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    )),
]

CONTENT_WITH_REASONING = [
    ("sse", _sse(
        {"choices": [{"delta": {"reasoning_content": "thinking..."}}]},
        {"choices": [{"delta": {"content": "正常答案"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]},
    )),
]


class TestReasoningFallbackStream:
    def test_reasoning_only_falls_back(self):
        """MiMo音频实测形态：content空+reasoning有答案→回退收下为回复"""
        srv = _Server(REASONING_ONLY)
        try:
            eng = _engine(srv.port)
            chunks = run(_collect(eng.chat_stream_with_tools(
                messages=[{"role": "user", "content": "hi"}])))
            text = "".join(c for c in chunks if isinstance(c, str))
            assert "苹果香蕉橘子" in text and "一二三" in text
            assert not any(isinstance(c, dict) and "tool_calls" in c for c in chunks)
        finally:
            srv.close()

    def test_content_not_duplicated_by_reasoning(self):
        """content正常时thinking不掺（语义面不变回归钉）"""
        srv = _Server([("sse", _sse(
            {"choices": [{"delta": {"reasoning_content": "thinking..."}}]},
            {"choices": [{"delta": {"content": "正常答案"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ))])
        try:
            eng = _engine(srv.port)
            chunks = run(_collect(eng.chat_stream_with_tools(
                messages=[{"role": "user", "content": "hi"}])))
            text = "".join(c for c in chunks if isinstance(c, str))
            assert text == "正常答案"
            assert "thinking" not in text
        finally:
            srv.close()

    def test_tool_call_round_no_reasoning_spill(self):
        """工具轮：thinking≠可见输出，绝不掺进回复（回退只救纯文本空回复）"""
        tc = {"tool_calls": [{"index": 0, "id": "tc1", "type": "function",
                              "function": {"name": "read_file", "arguments": "{}"}}]}
        srv = _Server([("sse", _sse(
            {"choices": [{"delta": {"reasoning_content": "我来调工具"}}]},
            {"choices": [{"delta": {"tool_calls": [tc["tool_calls"][0]]},
                          "finish_reason": "tool_calls"}]},
        ))])
        try:
            eng = _engine(srv.port)
            chunks = run(_collect(eng.chat_stream_with_tools(
                messages=[{"role": "user", "content": "hi"}], tools=[{"type": "function"}])))
            text = "".join(c for c in chunks if isinstance(c, str))
            assert "我来调工具" not in text
            assert any(isinstance(c, dict) and c.get("tool_calls") for c in chunks)
        finally:
            srv.close()

    def test_plain_chat_stream_reasoning_fallback(self):
        srv = _Server(REASONING_ONLY)
        try:
            eng = _engine(srv.port)
            chunks = run(_collect(eng.chat_stream(messages=[{"role": "user", "content": "hi"}])))
            text = "".join(c for c in chunks if isinstance(c, str))
            assert "苹果香蕉橘子" in text
        finally:
            srv.close()

    def test_plain_chat_stream_content_not_duplicated(self):
        srv = _Server([("sse", _sse(
            {"choices": [{"delta": {"reasoning_content": "thinking..."}}]},
            {"choices": [{"delta": {"content": "正常答案"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ))])
        try:
            eng = _engine(srv.port)
            chunks = run(_collect(eng.chat_stream(messages=[{"role": "user", "content": "hi"}])))
            assert "".join(chunks) == "正常答案"
        finally:
            srv.close()


class TestReasoningFallbackChat:
    def test_chat_empty_content_falls_back(self):
        body = json.dumps({"choices": [{"message": {"content": "", "reasoning_content": "答案在思考里"},
                                        "finish_reason": "stop"}]})
        srv = _Server([("chat", body)])
        try:
            eng = _engine(srv.port)
            out = run(eng.chat([{"role": "user", "content": "hi"}]))
            assert out == "答案在思考里"
        finally:
            srv.close()

    def test_chat_content_wins_over_reasoning(self):
        body = json.dumps({"choices": [{"message": {"content": "正文", "reasoning_content": "thinking"},
                                        "finish_reason": "stop"}]})
        srv = _Server([("chat", body)])
        try:
            eng = _engine(srv.port)
            out = run(eng.chat([{"role": "user", "content": "hi"}]))
            assert out == "正文"
        finally:
            srv.close()


async def _collect(agen):
    out = []
    async for c in agen:
        out.append(c)
    return out


# ════════════════ 3. _resolve_model 列表content容噪 ════════════════

class TestResolveModelListContent:
    def test_list_content_does_not_crash_scorer(self):
        eng = _engine(0)
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "正常文本"},
            {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64,xx", "format": "wav"}},
        ]}]
        model, base_url, key = eng._resolve_model(msgs)  # 不抛异常即为过
        assert model == "m-test"


# ════════════════ 4. agent接线：真实 _prompt_inner 简单路径 ════════════════

_REPLY = "听到啦，你说的是苹果香蕉橘子。"


def _build_audio_env(tmp_path, monkeypatch, prompt_blocks):
    agent, _ = build_env(
        tmp_path, monkeypatch,
        steps=[("step_1", "A")],
        llm_scripts=[[_REPLY]],
        llm_call_responses=[],
    )
    agent._tracer = MagicMock()
    agent._token_analyzer = MagicMock()
    agent._cost_tracker = MagicMock()
    from pathlib import Path as _P
    agent._opensoul_root = _P(tmp_path)  # 附件落盘根（build_env不设）
    return agent


class TestAgentWiring:
    def test_dict_audio_block_reaches_llm_wire(self, tmp_path, monkeypatch):
        """前端/WS形态dict块：提取→落盘→锚点→wire消息含input_audio（真实路径）"""
        agent = _build_audio_env(tmp_path, monkeypatch, None)
        blocks = [
            {"type": "audio", "data": B64, "mimeType": "audio/wav"},
            {"type": "text", "text": "这段音频说了什么？"},
        ]
        run(agent._prompt_inner(blocks, "s1"))
        # 服务端副作用①：附件真实落盘
        att_dir = Path(tmp_path) / "data" / "attachments" / "s1"
        saved = list(att_dir.glob("*"))
        assert saved and saved[0].stat().st_size > 0
        # 服务端副作用②：真实LLM请求面含 input_audio 内容块（锚点定位目标消息）
        wire = agent.llm_engine.seen_messages[0]
        user_contents = [m.get("content") for m in wire if m.get("role") == "user"]
        parts_contents = [c for c in user_contents if isinstance(c, list)]
        assert parts_contents, "wire消息未见内容块形态"
        audio_blocks = [b for c in parts_contents for b in c
                        if isinstance(b, dict) and b.get("type") == "input_audio"]
        assert audio_blocks, "input_audio块未进LLM请求"
        assert audio_blocks[0]["input_audio"]["data"].startswith("data:audio/wav;base64,")
        # 锚点：text部分含"[附件已保存到: <落盘路径>]"
        text_parts = [b.get("text", "") for c in parts_contents for b in c
                      if isinstance(b, dict) and b.get("type") == "text"]
        assert any(str(saved[0]) in t for t in text_parts), (text_parts, saved)
        # 回复落会话
        joined = " ".join(str(m.get("content", "")) for m in agent.sessions["s1"]["messages"])
        assert _REPLY in joined

    def test_sdk_object_audio_block_reached(self, tmp_path, monkeypatch):
        """SDK Pydantic对象形态（AudioContentBlock：.data/.mime_type）不被误标image"""
        agent = _build_audio_env(tmp_path, monkeypatch, None)
        blocks = [
            SimpleNamespace(type="audio", data=B64, mime_type="audio/wav"),
            SimpleNamespace(type="text", text="听一下"),
        ]
        run(agent._prompt_inner(blocks, "s1"))
        wire = agent.llm_engine.seen_messages[0]
        flat = [b for m in wire for b in (m.get("content") if isinstance(m.get("content"), list) else [])
                if isinstance(b, dict)]
        audio_blocks = [b for b in flat if b.get("type") == "input_audio"]
        assert audio_blocks, "SDK对象形态音频未进wire"
        # 落库attachments元数据mime如实（type=file卡渲染，mime=audio/*）
        import sqlite3
        db = sqlite3.connect(str(agent._db_path))
        row = db.execute(
            "SELECT attachments FROM agent_messages WHERE session_id='s1' AND attachments IS NOT NULL"
        ).fetchone()
        db.close()
        assert row and "audio/wav" in row[0]

    def test_text_only_turn_untouched(self, tmp_path, monkeypatch):
        """纯文本回归钉：wire消息不被内容块化（行为逐字不变）"""
        agent = _build_audio_env(tmp_path, monkeypatch, None)
        run(agent._prompt_inner([{"type": "text", "text": "你好"}], "s1"))
        for m in agent.llm_engine.seen_messages[0]:
            assert isinstance(m.get("content", ""), (str, type(None)))


# ════════════════ 5. aux随行直发 + infection_audit排除 ════════════════

class _FakeChat:
    def __init__(self):
        self.seen = []

    async def chat(self, messages=None, **kw):
        self.seen.append(messages)
        return "ok"


class TestAuxAudioFollow:
    def _agent(self, tmp_path):
        from tests.test_steering import make_agent
        agent = make_agent(tmp_path)
        agent.llm_engine = _FakeChat()
        return agent

    def test_plan_aux_fallback_last_attach(self, tmp_path):
        agent = self._agent(tmp_path)
        sa._TURN_AUDIO.set([AUDIO_PART])
        out = run(agent._aux_llm_chat("plan", [{"role": "user", "content": "规划一下"}]))
        assert out == "ok"
        content = agent.llm_engine.seen[0][0]["content"]
        assert isinstance(content, list)
        assert any(b.get("type") == "input_audio" for b in content)

    def test_infection_audit_excluded(self, tmp_path):
        """审计员判注入面，掺音频徒增噪声——显式排除回归钉"""
        agent = self._agent(tmp_path)
        sa._TURN_AUDIO.set([AUDIO_PART])
        run(agent._aux_llm_chat("infection_audit", [{"role": "user", "content": "审计prompt"}]))
        content = agent.llm_engine.seen[0][0]["content"]
        assert isinstance(content, str)

    def test_no_turn_audio_untouched(self, tmp_path):
        agent = self._agent(tmp_path)
        sa._TURN_AUDIO.set([])
        run(agent._aux_llm_chat("title", [{"role": "user", "content": "起标题"}]))
        assert isinstance(agent.llm_engine.seen[0][0]["content"], str)
