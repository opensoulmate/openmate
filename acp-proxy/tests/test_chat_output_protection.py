# -*- coding: utf-8 -*-
"""chat()输出防护接线专项（2026-09-28 P1——上轮遗留#5点名下轮优先）

问题（本轮修复对象）：LLMEngine.chat()是soulmate真实消息路径的非流式传输真源
（_judge_task_continuation_llm/_llm_plan_call/标题生成/上下文压缩全走它），但
Aider续写（finish_reason=length）+ Gemini CLI omission防护只存在于
utils.llm_gateway._call_with_protection与utils.llm_safety.safe_llm_call两个
**零生产调用点**的死路径里——chat()真实传输裸奔：max_tokens截断静默半截输出
（judge JSON半截=解析失败兜底"new"、计划/摘要半截即废），omission占位符照单全收。

覆盖：
1. chat()防护行为（fake传输）：stop单发不变/length续写拼接/omission重发/
   次数有界不空转/失败契约"[LLM错误: …]"不变/usage回填保留
2. 真实路径集成：SoulMateAgent._judge_task_continuation_llm半截JSON→续写补齐→
   真裁决"continue"（无防护时json.loads必炸走兜底"new"=用户可感知语义差）
3. 单一真源镜像收口：llm_safety/llm_gateway/dna_evolution三处omission pattern
   同一对象（旧三副本漂移实锤：dna 7条 vs 9条缺2条），副本绝迹
4. 接线钉：chat()源码必须引用check_omission/build_continuation_messages/
   check_finish_reason（防再断线回死路径）
"""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.llm_engine import LLMEngine
from utils import llm_safety
from utils.llm_safety import MAX_CONTINUATION

HERE = Path(__file__).parent.parent


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════════
# fake非流式传输：post()返回可编排响应序列，捕获每次payload
# ════════════════════════════════════════════════════════════════

class _FakeResp:
    def __init__(self, content=None, finish_reason="stop", status_code=200,
                 body="", usage=None):
        self.status_code = status_code
        self._body = body
        self._data = {
            "choices": [{
                "message": {"content": content},
                "finish_reason": finish_reason,
            }],
        }
        if usage is not None:
            self._data["usage"] = usage

    def json(self):
        return self._data

    @property
    def text(self):
        return self._body


def _install_client(monkeypatch, responses: list) -> list:
    """安装fake client；返回payload记录列表（深快照，防事后篡改）"""
    payloads: list[dict] = []
    state = {"calls": 0}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            # 注意：kw里的json是payload——不能命名参数为json（会遮蔽json模块）
            payload = kw.get("json") or {}
            payloads.append(json.loads(json.dumps(payload)))
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


async def _chat(engine, monkeypatch, responses, messages=None):
    payloads = _install_client(monkeypatch, responses)
    out = await engine.chat(messages or [{"role": "user", "content": "hi"}])
    return out, payloads


# ════════════════════════════════════════════════════════════════
# 守护1：chat()防护行为（fake传输真实调用chat，非只验import）
# ════════════════════════════════════════════════════════════════

class TestChatOutputProtection:
    def test_normal_stop_single_call_unchanged(self, monkeypatch):
        """finish_reason=stop：单发返回，行为与接线前一致（回归钉）"""
        out, payloads = _run(_chat(_engine(), monkeypatch, [
            _FakeResp(content="hello world"),
        ]))
        assert out == "hello world"
        assert len(payloads) == 1
        # system注入契约保留
        assert payloads[0]["messages"][0]["role"] == "system"

    def test_length_truncation_auto_continues(self, monkeypatch):
        """finish_reason=length：Aider续写，各段拼接（此前静默返回半截）"""
        out, payloads = _run(_chat(_engine(), monkeypatch, [
            _FakeResp(content="AAA", finish_reason="length"),
            _FakeResp(content="BBB", finish_reason="stop"),
        ]))
        assert out == "AAABBB"
        assert len(payloads) == 2
        # 续写消息形状：assistant=本段 + user=续写指令（单一真源文案）
        tail = payloads[1]["messages"]
        assert tail[-2] == {"role": "assistant", "content": "AAA"}
        assert tail[-1]["role"] == "user"
        assert "Continue from where you left off" in tail[-1]["content"]
        # 段语义防重复：第2发不得把已入history的"AAA"再并进assistant消息
        assert all(m.get("content") != "AAABBB" for m in tail)

    def test_omission_placeholder_regenerates(self, monkeypatch):
        """omission占位符（Gemini CLI）：整体重新生成，不带旧脏输出返回"""
        out, payloads = _run(_chat(_engine(), monkeypatch, [
            _FakeResp(content="code\n[...]\nmore"),
            _FakeResp(content="clean complete output"),
        ]))
        assert out == "clean complete output"
        assert len(payloads) == 2
        tail = payloads[1]["messages"]
        assert tail[-2] == {"role": "assistant", "content": "code\n[...]\nmore"}
        assert "Your previous output had issues" in tail[-1]["content"]

    def test_continuation_exhaustion_bounded(self, monkeypatch):
        """永远截断：请求次数有界（MAX_CONTINUATION+1），不无限空转"""
        out, payloads = _run(_chat(_engine(), monkeypatch, [
            _FakeResp(content="X", finish_reason="length"),
        ]))
        assert len(payloads) == MAX_CONTINUATION + 1
        assert out == "X" * (MAX_CONTINUATION + 1)

    def test_error_contract_preserved(self, monkeypatch):
        """非200：失败契约"[LLM错误: …]"不变（失败必须可见）"""
        out, _ = _run(_chat(_engine(), monkeypatch, [
            _FakeResp(status_code=402, body="insufficient balance"),
        ]))
        assert out.startswith("[LLM错误:")
        assert "402" in out

    def test_usage_backfill_on_each_attempt(self, monkeypatch):
        """P1 provider usage回填契约保留：每次尝试都写last_usage"""
        engine = _engine()
        out, _ = _run(_chat(engine, monkeypatch, [
            _FakeResp(content="A", finish_reason="length",
                      usage={"prompt_tokens": 10, "completion_tokens": 1}),
            _FakeResp(content="B", usage={"prompt_tokens": 12, "completion_tokens": 2}),
        ]))
        assert out == "AB"
        assert engine.last_usage == {"prompt_tokens": 12, "completion_tokens": 2}


# ════════════════════════════════════════════════════════════════
# 守护2：真实路径集成——judge半截JSON续写补齐（用户可感知语义差）
# ════════════════════════════════════════════════════════════════

class TestJudgePathIntegration:
    def test_truncated_judge_json_completed_by_continuation(self, monkeypatch):
        """_judge_task_continuation_llm真实调用：半截JSON→续写补齐→真裁决continue
        （无防护时json.loads('{"action": "cont')必炸→兜底"new"=旧任务被误完成）"""
        from agent.soulmate_agent import SoulMateAgent

        engine = _engine()
        agent = SoulMateAgent.__new__(SoulMateAgent)
        agent.llm_engine = engine
        task = SimpleNamespace(goal="部署服务器")

        # 半截JSON被max_tokens切断的provider响应，续写段补齐
        out, payloads = _run(_chat(engine, monkeypatch, [
            _FakeResp(content='{"action": "cont', finish_reason="length"),
            _FakeResp(content='inue", "reason": "同一部署任务继续"}'),
        ], messages=[{"role": "user", "content": "占位"}]))

        # 先证fake传输可用，再走真实judge方法（同一engine同一fake编排）
        _install_client(monkeypatch, [
            _FakeResp(content='{"action": "cont', finish_reason="length"),
            _FakeResp(content='inue", "reason": "同一部署任务继续"}'),
        ])
        action = _run(agent._judge_task_continuation_llm(task, "把配置也部署了"))
        assert action == "continue"
        # 防护真实在场：无防护版本只会发1次请求且json必炸（此处2次请求=续写发生）
        assert out == '{"action": "continue", "reason": "同一部署任务继续"}'


# ════════════════════════════════════════════════════════════════
# 守护3：单一真源镜像收口（旧三副本漂移：dna 7条 vs 9条缺2条）
# ════════════════════════════════════════════════════════════════

class TestSingleSourcePatterns:
    def test_pattern_count_nine(self):
        assert len(llm_safety.OMISSION_PATTERNS) == 9

    def test_llm_gateway_delegates_to_llm_safety(self):
        from utils import llm_gateway
        assert llm_gateway._OMISSION_PATTERNS is llm_safety.OMISSION_PATTERNS
        assert llm_gateway._check_omission is llm_safety.check_omission

    def test_dna_evolution_delegates_to_llm_safety(self):
        from dna_evolution import DNAStrand
        assert DNAStrand._OMISSION_PATTERNS is llm_safety.OMISSION_PATTERNS

    def test_no_local_pattern_copies_remain(self):
        """pattern副本绝迹：llm_gateway/dna_evolution源码不得再有re.compile占位符副本"""
        for rel in ("utils/llm_gateway.py", "dna_evolution.py"):
            src = "\n".join(
                l for l in _src(rel).splitlines() if not l.strip().startswith("#")
            )
            assert "rest of methods" not in src, f"{rel} 残留omission pattern副本"
            assert "existing code" not in src, f"{rel} 残留omission pattern副本"


# ════════════════════════════════════════════════════════════════
# 守护4：接线钉——chat()必须引用防护（防再断线回死路径）
# ════════════════════════════════════════════════════════════════

class TestWiring:
    def test_chat_wired_to_protection(self):
        src = _src("agent/llm_engine.py")
        assert "from utils.llm_safety import" in src
        # chat方法体内真实调用三件套（不是只import）
        chat_src = src.split("async def chat(", 1)[1].split("def _build_messages", 1)[0]
        assert "check_omission(" in chat_src
        assert "check_finish_reason(" in chat_src
        assert "build_continuation_messages(" in chat_src
        assert "MAX_CONTINUATION" in chat_src

    def test_protection_not_stream_path_only(self):
        """防护必须在chat()（非流式真实传输），不是只在chat_stream"""
        src = _src("agent/llm_engine.py")
        chat_src = src.split("async def chat(", 1)[1].split("def _build_messages", 1)[0]
        assert "for attempt in range(MAX_CONTINUATION + 1)" in chat_src
