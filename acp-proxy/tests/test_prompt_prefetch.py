# -*- coding: utf-8 -*-
"""路线图⚡0.2 首条消息提速——new_session 预热引擎 + 微小预热 LLM 请求。

用户实测"第一条回复特别慢"（6s 引擎准备 + 27s 模型大首包）。验收方向：
新建会话→首条消息→首字回复显著提速（目标 ≤8s，实测值在周期日志如实记录）。

测试面：
1. 并行预取：同步/异步收集器混跑、并行而非串行（barrier 实证）；
2. 复用与新鲜度：TTL 内 take() 命中、超时回退空（fake clock）；
3. 旁路纪律：收集器/预热请求失败只计数不冒泡、其余源照常缓存；
4. prefill：带 base_system_prompt 调用、产物不进会话历史；
5. 接线护栏：SoulMateAgent lazy-init / new_session 调度 / _run_llm_with_tools 消费 /
   基础系统提示单一真源（prefill 与主链路同前缀）。
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.prompt_prefetch import PromptPrefetcher
from agent.soulmate_agent import SoulMateAgent

SID = "om-prefetch"


def run(coro):
    return asyncio.run(coro)


# ── 1. 并行预取 ───────────────────────────────────────────────


def test_gatherers_mixed_sync_async_cached():
    async def agather(sid):
        await asyncio.sleep(0)
        return "mcp-tools"

    pf = PromptPrefetcher(
        gatherers={
            "pref_context": lambda sid: "偏好X",
            "memory_md": lambda sid: "记忆Y",
            "mcp_tools": agather,
        }
    )
    parts = run(pf.warm(SID))
    assert parts == {"pref_context": "偏好X", "memory_md": "记忆Y", "mcp_tools": "mcp-tools"}
    assert pf.take(SID) == parts
    assert pf.stats["warm_runs"] == 1
    assert pf.stats["cache_hits"] == 1


def test_gatherers_run_in_parallel_barrier():
    """barrier 实证：两个收集器互相等待对方进入——串行执行会死等到超时。"""
    entered = {"a": False, "b": False}

    def make(name, other):
        async def g(sid):
            entered[name] = True
            for _ in range(200):  # 最多等 2s（0.01×200）
                if entered[other]:
                    return name
                await asyncio.sleep(0.01)
            raise AssertionError(f"gatherer {name} 疑似串行执行（对方未进入）")
        return g

    pf = PromptPrefetcher(gatherers={"a": make("a", "b"), "b": make("b", "a")})
    parts = run(asyncio.wait_for(pf.warm(SID), timeout=5))
    assert parts == {"a": "a", "b": "b"}


# ── 2. 复用与新鲜度 ───────────────────────────────────────────


def test_take_ttl_expiry_fallback_empty():
    fake_now = {"t": 1000.0}
    pf = PromptPrefetcher(
        gatherers={"pref_context": lambda sid: "X"},
        ttl_s=60.0,
        clock=lambda: fake_now["t"],
    )
    run(pf.warm(SID))
    assert pf.take(SID) == {"pref_context": "X"}
    fake_now["t"] += 59.0
    assert pf.take(SID) == {"pref_context": "X"}  # TTL 内仍复用
    fake_now["t"] += 2.0
    assert pf.take(SID) == {}  # 超时回退现场计算
    assert pf.stats["cache_hits"] == 2


def test_take_unknown_session_empty_and_invalidate():
    pf = PromptPrefetcher(gatherers={"pref_context": lambda sid: "X"})
    assert pf.take("om-nope") == {}
    run(pf.warm(SID))
    pf.invalidate(SID)
    assert pf.take(SID) == {}


def test_take_is_copy_not_alias():
    pf = PromptPrefetcher(gatherers={"pref_context": lambda sid: "x"})
    run(pf.warm(SID))
    got = pf.take(SID)
    got["pref_context"] = "dirty"
    got["injected"] = 1
    assert pf.take(SID) == {"pref_context": "x"}  # 顶层copy：改写/新增键不回写缓存


# ── 3. 旁路纪律：失败只计数不冒泡 ─────────────────────────────


def test_gatherer_failure_isolated_others_cached():
    def boom(sid):
        raise RuntimeError("画像库挂了")

    async def agood(sid):
        return "ok"

    pf = PromptPrefetcher(gatherers={"bad": boom, "good": agood, "pref_context": lambda sid: "X"})
    parts = run(pf.warm(SID))  # 不抛
    assert parts == {"good": "ok", "pref_context": "X"}
    assert pf.stats["warm_failures"] == 1


async def _async_gatherer_raises(sid):
    raise RuntimeError("async源挂了")


def test_async_gatherer_failure_isolated():
    pf = PromptPrefetcher(gatherers={"a": _async_gatherer_raises, "b": lambda sid: "ok"})
    parts = run(pf.warm(SID))
    assert parts == {"b": "ok"}
    assert pf.stats["warm_failures"] == 1


# ── 4. prefill 微小预热 LLM 请求 ──────────────────────────────


def test_prefill_called_with_base_prompt_and_isolated():
    seen = {}

    async def prefill(sid, base):
        seen["sid"] = sid
        seen["base"] = base

    pf = PromptPrefetcher(gatherers={"pref_context": lambda sid: "X"}, prefill_fn=prefill)
    run(pf.warm(SID, base_system_prompt="你是SoulMate..."))
    assert seen == {"sid": SID, "base": "你是SoulMate..."}
    assert pf.stats["prefill_runs"] == 1
    assert pf.stats["prefill_failures"] == 0
    # prefill 产物不进缓存（不污染会话/上下文面）
    assert pf.take(SID) == {"pref_context": "X"}


def test_prefill_failure_counted_not_raised():
    async def prefill(sid, base):
        raise RuntimeError("LLM 预热超时")

    pf = PromptPrefetcher(gatherers={"pref_context": lambda sid: "X"}, prefill_fn=prefill)
    parts = run(pf.warm(SID, base_system_prompt="base"))  # 不抛
    assert parts == {"pref_context": "X"}
    assert pf.stats["prefill_runs"] == 1
    assert pf.stats["prefill_failures"] == 1


def test_prefill_parallel_with_gatherers():
    """prefill 与收集器并行：prefill 等收集器进入才返回，超时即证明串行。"""
    entered = {"g": False}

    async def prefill(sid, base):
        for _ in range(200):
            if entered["g"]:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("prefill 疑似与收集器串行执行")

    async def g(sid):
        entered["g"] = True
        return "v"

    pf = PromptPrefetcher(gatherers={"g": g}, prefill_fn=prefill)
    run(asyncio.wait_for(pf.warm(SID, base_system_prompt="b"), timeout=5))
    assert pf.stats["prefill_failures"] == 0


def test_schedule_warm_fire_and_forget():
    done = {}

    async def g(sid):
        return "v"

    pf = PromptPrefetcher(gatherers={"g": g})

    async def main():
        task = pf.schedule_warm(SID, base_system_prompt="b")
        await task  # 测试里 join；生产里 fire-and-forget
        done["ok"] = True

    run(main())
    assert done.get("ok") and pf.take(SID) == {"g": "v"}


# ── 5. 接线护栏 ───────────────────────────────────────────────


class _StubPref:
    def get_context_prompt(self):
        return "画像-偏好"


class _StubMem:
    def load_prompt_section(self):
        return "MEMORY-内容"


class _StubRefl:
    def get_improvement_context(self, sid):
        return f"改进-{sid}"


class _StubEngine:
    def __init__(self):
        self.calls = []

    async def chat_stream_with_tools(self, messages=None, tools=None, system_prompt=None, **kw):
        self.calls.append({"messages": messages, "tools": tools, "system_prompt": system_prompt})
        yield "你好"  # 首 chunk 即暖完前缀


def _bare_agent() -> Any:
    a: Any = SoulMateAgent.__new__(SoulMateAgent)
    a._pref_learner = _StubPref()
    a._persistent_memory = _StubMem()
    a._reflection_engine = _StubRefl()
    a.llm_engine = _StubEngine()
    a._project_root = "/home/climbing/openmate"
    a._session_cwds = {SID: "/home/climbing/openmate"}

    async def _no_mcp():
        return []

    a._fetch_mcp_tools = _no_mcp
    return a


def test_agent_lazy_init_and_prefill_warm_real_path():
    a = _bare_agent()
    pf = a._prompt_prefetcher()
    assert a._prompt_prefetcher() is pf  # lazy-init 单例
    parts = run(pf.warm(SID, base_system_prompt="你是SoulMate..."))
    # 真实收集器产出（三源来自 agent 自己的组件）
    assert parts["pref_context"] == "画像-偏好"
    assert parts["memory_md"] == "MEMORY-内容"
    assert parts["improvement"] == f"改进-{SID}"
    # prefill 走 llm_engine.chat_stream_with_tools：同系统提示前缀+回复丢弃
    assert a.llm_engine.calls and a.llm_engine.calls[0]["system_prompt"] == "你是SoulMate..."
    assert a.llm_engine.calls[0]["messages"] == [{"role": "user", "content": "你好"}]


def test_agent_schedule_prefetch_uses_base_prompt_single_source():
    a = _bare_agent()

    async def main():
        a._schedule_prefetch(SID, "/home/climbing/openmate")
        await a._prefetcher_inst._tasks[SID]  # 生产里 fire-and-forget，测试里 join

    run(main())
    # 预热 prefill 的系统提示 == 主链路 _build_base_system_prompt 同一真源
    base = a._build_base_system_prompt("/home/climbing/openmate")
    assert a.llm_engine.calls[0]["system_prompt"] == base
    assert "你是SoulMate" in base


def test_agent_schedule_prefetch_never_raises():
    """旁路纪律：预热器构建失败也绝不阻塞 new_session。"""
    a = SoulMateAgent.__new__(SoulMateAgent)  # 裸实例（无 _pref_learner 等）
    a._project_root = "/home/climbing/openmate"
    a._session_cwds = {SID: "/home/climbing/openmate"}

    async def main():
        a._schedule_prefetch(SID)  # 不抛（缺组件等一切异常被旁路吞掉）

    run(main())


def test_wiring_source_guards():
    """接线护栏（写了≠接线了）：new_session 调度 + 主链路消费 + 同前缀真源。"""
    src = Path(__file__).parent.parent / "agent" / "soulmate_agent.py"
    text = src.read_text(encoding="utf-8")
    # new_session 创建路径末尾调度预热（⚡0.2）
    assert "self._schedule_prefetch(sid, cwd)" in text
    assert text.count("self._schedule_prefetch(sid, cwd)") == 1
    # _run_llm_with_tools 消费预热缓存（三源）
    assert '_prewarm.get("pref_context")' in text
    assert '_prewarm.get("memory_md")' in text
    assert '_prewarm.get("improvement")' in text
    # 基础系统提示单一真源：主链路调用 + 方法定义共存（prefill 同前缀的前提）
    assert "system_prompt = self._build_base_system_prompt(cwd)" in text
    assert "def _build_base_system_prompt(self, cwd: str) -> str:" in text
    # 预热请求不落库不进会话历史：prefill 只调 chat_stream_with_tools，无 _save_message
    pf_src = (Path(__file__).parent.parent / "agent" / "prompt_prefetch.py").read_text(encoding="utf-8")
    assert "_save_message" not in pf_src
    assert "asyncio.gather" in pf_src  # 并行预取
