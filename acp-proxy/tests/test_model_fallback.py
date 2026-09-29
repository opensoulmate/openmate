# -*- coding: utf-8 -*-
"""utils.model_fallback + llm_transport降级链专项（2026-09-29 CowAgent chat_fallback移植）。

调研来源：SUMMARY.md §五 cortex缺口「模型降级有序链（fallback+限流立即切备胎）
| CowAgent chat fallback链 | P0」（38-CowAgent-source-supplement3.md #12"完全没有"）；
源码对照 zhayujie/CowAgent bridge/agent_bridge.py + agent/protocol/agent_stream.py。

覆盖（CowAgent语义逐条）：
1. 链规范化（fallback_config）：半截link丢弃/重复link丢弃（含primary模型名比对）/
   无凭据link丢弃/免鉴权本地provider保留
2. 环绕预算（fallback_available/use_fallback）：走完环绕第二遍、预算耗尽拒推进、
   sticky停在应答link、reset回链头
3. 传输行走链（_call_llm_stream失败处理）：primary全重试预算→备胎只给1发/
   429有备胎立即切换不干等/429没备胎干等（原行为回归）/不可重试也换link/
   传输错误耗尽换link/链耗尽报错列出所有试过的模型
4. sticky跨续写轮（use_fallback stickiness）：切到备胎后续写轮不弹回primary；
   sticky起始link保持全重试预算（CowAgent外部调用_on_fallback=False语义）
5. 接线钉：complete_with_protection自动构建链（真实路径零改动接线）+ 单一真源
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from utils import llm_transport, model_fallback
from utils.llm_errors import LLMCallError
from utils.model_fallback import (
    MAX_PASSES,
    FallbackChain,
    FallbackLink,
    chain_failure_message,
    load_fallback_chain,
    normalize_chain,
    parse_chain_spec,
)

HERE = Path(__file__).resolve().parent.parent


def run(coro):
    return asyncio.run(coro)


# ────────────────────────────── 测试替身 ──────────────────────────────

def _resp(status=200, text="", content=None, finish="stop", headers=None):
    class R:
        status_code = status

        def __init__(self):
            self.text = text
            self.headers = headers or {}
            if content is not None and not text:
                self.text = json.dumps({
                    "choices": [{"message": {"content": content}, "finish_reason": finish}]
                })

        def json(self):
            return json.loads(self.text)

    return R()


class _FakeClient:
    """按序返回响应/抛异常的假client（耗尽后抛错暴露意外调用），记录URL与payload"""

    def __init__(self, responses):
        self.queue = list(responses)
        self.calls = []
        self.payloads = []

    async def post(self, url, headers=None, json=None):
        self.calls.append(url)
        self.payloads.append({"url": url, "headers": headers, "json": json})
        if not self.queue:
            raise AssertionError("超出预期的LLM调用")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _nosleep(sleeps):
    async def _f(delay):
        sleeps.append(delay)
    return _f


PRIMARY = "https://primary.invalid/v1"
BACKUP1 = "https://backup1.invalid/v1"
BACKUP2 = "https://backup2.invalid/v1"


def _link(model="m-backup1", base_url=BACKUP1, provider="qwen", api_key="sk-b1"):
    return FallbackLink(
        provider=provider, model=model, base_url=base_url, api_key=api_key
    )


def _chain(*links):
    return FallbackChain(links=list(links))


def _post(client, **kw):
    args = dict(
        base_url=PRIMARY,
        headers={"Content-Type": "application/json", "Authorization": "Bearer sk-p"},
        model="m-primary",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=100,
        strand_id="t",
    )
    args.update(kw)
    return llm_transport.post_chat_completion(client, **args)


def _complete(client, **kw):
    args = dict(
        base_url=PRIMARY,
        headers={"Content-Type": "application/json"},
        model="m-primary",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=100,
        rounds=1,
        strand_id="t",
    )
    args.update(kw)
    return llm_transport.complete_with_protection(client, **args)


def _url_counts(client):
    out = {}
    for u in client.calls:
        out[u] = out.get(u, 0) + 1
    return out


# ══════════════════════ 1. 链配置解析/规范化 ══════════════════════

class TestParseChainSpec:
    def test_compact_format(self):
        items = parse_chain_spec("qwen:qwen-turbo, deepseek:deepseek-chat")
        assert items == [
            {"provider": "qwen", "model": "qwen-turbo"},
            {"provider": "deepseek", "model": "deepseek-chat"},
        ]

    def test_model_may_contain_colon(self):
        items = parse_chain_spec("ollama:qwen3:8b")
        assert items == [{"provider": "ollama", "model": "qwen3:8b"}]

    def test_json_format_cowagent_native(self):
        items = parse_chain_spec('[{"provider": "openai", "model": "gpt-4o-mini"}]')
        assert items == [{"provider": "openai", "model": "gpt-4o-mini"}]

    def test_bad_json_is_empty_not_crash(self):
        assert parse_chain_spec("[not json") == []

    def test_empty_and_garbage(self):
        assert parse_chain_spec("") == []
        assert parse_chain_spec("no-colon-item") == []


class TestNormalizeChain:
    @staticmethod
    def _resolver(provider):
        table = {
            "qwen": {"base_url": "https://qwen.invalid/v1", "api_key": "sk-q", "requires_auth": True},
            "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "", "requires_auth": False},
            "ghost": {"base_url": "", "api_key": "", "requires_auth": True},
            "nokey": {"base_url": "https://nokey.invalid/v1", "api_key": "", "requires_auth": True},
        }
        return table.get(provider, {"base_url": "", "api_key": "", "requires_auth": True})

    def test_half_link_dropped(self):
        links = normalize_chain(
            [{"provider": "qwen", "model": ""}, {"model": "x"}, {"provider": "qwen", "model": "ok"}],
            resolver=self._resolver,
        )
        assert [l.model for l in links] == ["ok"]

    def test_duplicate_of_primary_dropped_model_name_match(self):
        # primary无provider钉死时按模型名单独比对（CowAgent primary_model_only）
        links = normalize_chain(
            [{"provider": "qwen", "model": "m-primary"}, {"provider": "qwen", "model": "other"}],
            primary_model="m-primary",
            resolver=self._resolver,
        )
        assert [l.model for l in links] == ["other"]

    def test_duplicate_link_dropped(self):
        links = normalize_chain(
            [{"provider": "qwen", "model": "a"}, {"provider": "qwen", "model": "a"}],
            resolver=self._resolver,
        )
        assert [l.model for l in links] == ["a"]

    def test_unresolvable_provider_dropped(self):
        links = normalize_chain(
            [{"provider": "ghost", "model": "g"}, {"provider": "nokey", "model": "n"}],
            resolver=self._resolver,
        )
        assert links == []

    def test_local_provider_without_key_kept(self):
        # 免鉴权本地provider（ollama）无key合法（CowAgent"half a link"只看provider+model）
        links = normalize_chain([{"provider": "ollama", "model": "qwen3:8b"}], resolver=self._resolver)
        assert [l.label() for l in links] == ["ollama/qwen3:8b"]

    def test_load_from_env_value(self):
        ch = load_fallback_chain(
            primary_model="m-primary",
            env_value="qwen:qwen-turbo,ollama:qwen3:8b",
            resolver=self._resolver,
        )
        assert [l.label() for l in ch.links] == ["qwen/qwen-turbo", "ollama/qwen3:8b"]
        assert ch.available()  # 链存在即有预算


# ══════════════════════ 2. 环绕预算/sticky/reset ══════════════════════

class TestChainWalk:
    def test_wrap_around_second_pass(self):
        ch = _chain(_link("m1", BACKUP1), _link("m2", BACKUP2))
        order = [ch.advance().model for _ in range(4)]
        # CowAgent：走完环绕第二遍（depth % len），MAX_PASSES=2
        assert order == ["m1", "m2", "m1", "m2"]
        assert MAX_PASSES == 2
        assert not ch.available()

    def test_over_advance_raises_not_silent(self):
        ch = _chain(_link("m1", BACKUP1))
        ch.advance(); ch.advance()
        with pytest.raises(RuntimeError):
            ch.advance()

    def test_empty_chain_no_budget(self):
        ch = FallbackChain(links=[])
        assert not ch.available() and not ch

    def test_sticky_current_and_reset(self):
        ch = _chain(_link("m1", BACKUP1))
        assert ch.current is None
        got = ch.advance()
        assert ch.current is got and got.model == "m1"
        ch.reset()
        assert ch.current is None and ch.depth == 0 and ch.available()

    def test_failure_message_lists_all_tried(self):
        msg = chain_failure_message(["m-primary: HTTP 504", "qwen/m1: HTTP 500"])
        assert "m-primary" in msg and "qwen/m1" in msg and "2" in msg


# ══════════════════════ 3. 传输行走链 ══════════════════════

class TestFallbackWalk:
    def test_primary_exhausted_switches_to_backup(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([_resp(504, text="gateway timeout")] * 3 + [_resp(200, content="ok")])
        data = run(_post(c, chain=_chain(_link())))
        assert data["choices"][0]["message"]["content"] == "ok"
        counts = _url_counts(c)
        # primary=MAX_ATTEMPTS全预算3发；备胎link只给1发（CowAgent link_retries=0）
        assert counts == {f"{PRIMARY}/chat/completions": 3, f"{BACKUP1}/chat/completions": 1}
        assert sleeps == [2.0, 4.0]  # primary重试退避照旧；切换不睡

    def test_one_attempt_per_backup_link_then_wrap_then_exhaust(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([_resp(500, text="boom")] * 10)
        with pytest.raises(LLMCallError) as ei:
            run(_post(c, chain=_chain(_link("m1", BACKUP1), _link("m2", BACKUP2))))
        counts = _url_counts(c)
        # primary 3发 + 备胎每link 1发 × 环绕2遍（m1,m2,m1,m2）= 3+4 = 7发
        assert counts == {
            f"{PRIMARY}/chat/completions": 3,
            f"{BACKUP1}/chat/completions": 2,
            f"{BACKUP2}/chat/completions": 2,
        }
        msg = str(ei.value)
        # CowAgent语义3：报错列出所有试过的模型，不只最后一个
        assert "m-primary" in msg and "qwen/m1" in msg and "qwen/m2" in msg

    def test_429_with_backup_switches_immediately_no_wait(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([
            _resp(429, text="rate limited", headers={"retry-after": "30"}),
            _resp(200, content="ok"),
        ])
        data = run(_post(c, chain=_chain(_link())))
        assert data["choices"][0]["message"]["content"] == "ok"
        # CowAgent switch_now："限流有备胎立即切换"——1发就切，0等待（不烧30s）
        assert _url_counts(c)[f"{PRIMARY}/chat/completions"] == 1
        assert sleeps == []

    def test_429_without_chain_waits_out_unchanged(self, monkeypatch):
        # 回归钉：无链=原行为"没备胎干等"（退避重试，不换模型）
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([_resp(429, text="rate limited")] * 3)
        with pytest.raises(LLMCallError):
            run(_post(c))
        assert len(c.calls) == 3 and sleeps == [2.0, 4.0]

    def test_non_retryable_advances_to_backup(self, monkeypatch):
        # CowAgent："Retries are exhausted (or the error was never retryable)"
        # → 401/402等fail-fast也换link（provider级故障，重试同端点无益）
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([
            _resp(402, text="insufficient balance"),
            _resp(200, content="ok"),
        ])
        data = run(_post(c, chain=_chain(_link())))
        assert data["choices"][0]["message"]["content"] == "ok"
        assert _url_counts(c)[f"{PRIMARY}/chat/completions"] == 1 and sleeps == []

    def test_transport_error_exhausted_advances(self, monkeypatch):
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        parks = []
        async def _park(exc, cancel_event=None):
            parks.append(type(exc).__name__)
            return "not_offline"
        monkeypatch.setattr(llm_transport.network_state, "maybe_park", _park)
        c = _FakeClient([httpx.ConnectError("refused")] * 3 + [_resp(200, content="ok")])
        data = run(_post(c, chain=_chain(_link())))
        assert data["choices"][0]["message"]["content"] == "ok"
        assert len(parks) == 3  # park接线在链路径上依然生效
        assert sleeps == [2.0, 4.0]

    def test_no_chain_transport_error_raw_unchanged(self, monkeypatch):
        # 回归钉：无链时传输错误原样抛原始httpx异常（原契约逐字不变）
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        c = _FakeClient([httpx.ConnectError("refused")] * 3)
        with pytest.raises(httpx.ConnectError):
            run(_post(c))
        assert len(c.calls) == 3

    def test_link_payload_carries_link_model_and_auth(self, monkeypatch):
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep([]))
        c = _FakeClient([
            _resp(500, text="boom"),
            _resp(500, text="boom"),
            _resp(500, text="boom"),
            _resp(200, content="ok"),
        ])
        run(_post(c, chain=_chain(_link("m-backup1", BACKUP1, api_key="sk-b1"))))
        last = c.payloads[-1]
        assert last["json"]["model"] == "m-backup1"  # 备胎发自己的模型
        assert last["headers"]["Authorization"] == "Bearer sk-b1"  # 备胎带自己的凭据
        assert c.payloads[0]["json"]["model"] == "m-primary"


# ══════════════════════ 4. sticky跨续写轮 ══════════════════════

class TestStickyAcrossRounds:
    def test_backup_sticky_across_continuation_rounds(self, monkeypatch):
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep([]))
        c = _FakeClient([
            _resp(429, text="rate limited"),                       # primary：立即切
            _resp(200, content="PART1-", finish="length"),          # 备胎应答（截断）
            _resp(200, content="PART2", finish="stop"),             # 续写轮：仍走备胎
        ])
        out = run(_complete(c, rounds=2, chain=_chain(_link())))
        assert out == "PART1-PART2"
        counts = _url_counts(c)
        # CowAgent sticky："the run stays on whichever link answered"——
        # 续写轮不弹回primary
        assert counts == {f"{PRIMARY}/chat/completions": 1, f"{BACKUP1}/chat/completions": 2}

    def test_sticky_start_link_keeps_full_retry_budget(self, monkeypatch):
        # CowAgent外部调用_on_fallback=False：sticky起始link保持全重试预算
        sleeps = []
        monkeypatch.setattr(llm_transport.asyncio, "sleep", _nosleep(sleeps))
        ch = _chain(_link())
        ch.advance()  # 模拟上一轮已切换到备胎（sticky）
        c = _FakeClient([
            _resp(500, text="boom"),   # 备胎：第1发
            _resp(500, text="boom"),   # 备胎：第2发（全预算重试，不是1发）
            _resp(200, content="ok"),  # 备胎：第3发成功
        ])
        data = run(_post(c, chain=ch))
        assert data["choices"][0]["message"]["content"] == "ok"
        assert _url_counts(c) == {f"{BACKUP1}/chat/completions": 3}
        assert sleeps == [2.0, 4.0]


# ══════════════════════ 5. 接线钉 ══════════════════════

class TestWiringPins:
    def test_complete_auto_loads_chain_on_real_path(self, monkeypatch):
        # 零改动接线钉：complete_with_protection自动从LLM_FALLBACK_CHAIN构建链
        # （dna/gateway两条真实调用路径不需要任何改动即获得降级能力）
        seen = {}

        def _loader(primary_model="", primary_provider="", env_value=None, resolver=None):
            seen["primary_model"] = primary_model
            return FallbackChain(links=[])

        monkeypatch.setattr(llm_transport, "load_fallback_chain", _loader)
        c = _FakeClient([_resp(200, content="ok")])
        run(_complete(c))
        assert seen["primary_model"] == "m-primary"

    def test_single_source_no_duplicated_chain_impl(self):
        # 单一真源钉：walk/报错文案/链状态全在utils.model_fallback，llm_transport只消费
        assert llm_transport.FallbackChain is model_fallback.FallbackChain
        assert llm_transport.chain_failure_message is model_fallback.chain_failure_message
        assert llm_transport.load_fallback_chain is model_fallback.load_fallback_chain

    def test_transport_walk_lives_only_in_llm_transport(self):
        # 链行走只在llm_transport.post_chat_completion（禁止调用方各自手搓walk）
        src = (HERE / "dna_evolution.py").read_text(encoding="utf-8")
        assert "chain.advance" not in src and "FallbackChain" not in src
        src2 = (HERE / "utils" / "llm_gateway.py").read_text(encoding="utf-8")
        assert "chain.advance" not in src2 and "FallbackChain" not in src2

    def test_env_opt_in_default_empty(self, monkeypatch):
        # opt-in钉：未配置=空链（CowAgent "an empty chain disables the switch"）
        monkeypatch.delenv("LLM_FALLBACK_CHAIN", raising=False)
        ch = load_fallback_chain(primary_model="m-primary")
        assert ch.links == [] and not ch.available()
