"""DNA进化 LLM配置解析 + 失败必须可见（mem0 §1.1）契约测试。

背景（2026-09-26 live实锤，禁止重犯）：
进化strand每小时向 token-plan-cn.xiaomimimo.com 发**无鉴权**请求
（MIMO_API_KEY从未设置）→ 401 Invalid API Key → _call_llm静默返回"" →
_reflect/_plan空 → "Learned: 规划0项改进" → cycle success=pending——
LLM故障被伪装成"进化没发现可改进项"（"但是一直没有进化啊"的隐形死因）。

本文件钉住三件事：
① 配置解析走 utils/llm_config.resolve_llm_config 单一真源（调用点禁硬编码
   provider URL/模型名/自扫环境变量）；
② _call_llm 失败必须抛 LLMCallError（HTTP非200/空输出/传输异常），绝不静默返空；
③ _evolve 的 LLM 失败必须按**失败**记账（history带llm_error、streak计数、
   get_status可见），与"LLM成功但无改进"可区分——失败≠无改进。
"""

import asyncio
import json

import pytest

import dna_evolution
from dna_evolution import DNAStrand, EvolutionStage, LLMCallError, StrandRole
from utils.llm_config import describe, resolve_llm_config


def run(coro):
    return asyncio.run(coro)


# ─────────────────────────── ① 配置解析 ───────────────────────────


class TestResolveLLMConfig:
    def test_file_config_used_when_no_env(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MIMO_API_KEY", raising=False)
        monkeypatch.delenv("MIMO_BASE_URL", raising=False)
        monkeypatch.delenv("MIMO_MODEL", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        f1 = tmp_path / "opensoul.env"
        f1.write_text("# comment\nLLM_API_KEY=sk-file1\nLLM_BASE_URL=https://file1/v1\nLLM_MODEL=m-file1\nLLM_MAX_TOKENS=1234\n")
        cfg = resolve_llm_config(env_files=[str(f1), str(tmp_path / "missing.env")])
        assert cfg["api_key"] == "sk-file1"
        assert cfg["base_url"] == "https://file1/v1"
        assert cfg["model"] == "m-file1"
        assert cfg["max_tokens"] == 1234
        assert cfg["key_source"] == "file"

    def test_first_env_file_wins(self, tmp_path, monkeypatch):
        for v in ("MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"):
            monkeypatch.delenv(v, raising=False)
        f1 = tmp_path / "a.env"
        f1.write_text("LLM_API_KEY=sk-first\nLLM_BASE_URL=https://first/v1\n")
        f2 = tmp_path / "b.env"
        f2.write_text("LLM_API_KEY=sk-second\nLLM_BASE_URL=https://second/v1\n")
        cfg = resolve_llm_config(env_files=[str(f1), str(f2)])
        assert cfg["api_key"] == "sk-first"
        assert cfg["base_url"] == "https://first/v1"

    def test_mimo_env_is_explicit_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIMO_API_KEY", "sk-mimo")
        monkeypatch.setenv("MIMO_BASE_URL", "https://mimo/v1")
        monkeypatch.setenv("MIMO_MODEL", "m-mimo")
        f1 = tmp_path / "a.env"
        f1.write_text("LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        cfg = resolve_llm_config(env_files=[str(f1)])
        assert cfg["api_key"] == "sk-mimo"
        assert cfg["base_url"] == "https://mimo/v1"
        assert cfg["model"] == "m-mimo"
        assert cfg["key_source"] == "mimo_env"

    def test_env_var_fallback_when_no_file(self, tmp_path, monkeypatch):
        for v in ("MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL"):
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv("LLM_API_KEY", "sk-env")
        monkeypatch.setenv("LLM_BASE_URL", "https://env/v1")
        monkeypatch.setenv("LLM_MODEL", "m-env")
        cfg = resolve_llm_config(env_files=[str(tmp_path / "none.env")])
        assert cfg["api_key"] == "sk-env"
        assert cfg["key_source"] == "env"

    def test_missing_key_is_empty_not_invented(self, tmp_path, monkeypatch):
        for v in ("MIMO_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(v, raising=False)
        cfg = resolve_llm_config(env_files=[str(tmp_path / "none.env")])
        assert cfg["api_key"] == ""
        assert cfg["key_source"] == ""

    def test_bad_max_tokens_defaults(self, tmp_path, monkeypatch):
        for v in ("MIMO_API_KEY", "LLM_API_KEY", "MIMO_MAX_TOKENS", "LLM_MAX_TOKENS"):
            monkeypatch.delenv(v, raising=False)
        f1 = tmp_path / "a.env"
        f1.write_text("LLM_API_KEY=sk\nLLM_MAX_TOKENS=not-a-number\n")
        cfg = resolve_llm_config(env_files=[str(f1)])
        assert cfg["max_tokens"] == 65536

    def test_describe_never_leaks_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIMO_API_KEY", "sk-SUPERSECRET-VALUE")
        cfg = resolve_llm_config(env_files=[str(tmp_path / "none.env")])
        d = describe(cfg)
        assert "SUPERSECRET" not in d
        assert "key_source=mimo_env" in d

    def test_wiring_callers_use_single_source(self):
        """接线护栏：app.py / dna_strand_runner.py / ws_chat.py 禁硬编码provider URL/模型名，
        必须走resolve_llm_config（ws_chat走include_mimo_overrides=False，见test_ws_chat_llm_config.py；
        model_router另有resolve_env_value提供商凭据路径，见test_model_router_config.py）"""
        import os
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for fname in ("app.py", "dna_strand_runner.py", "ws_chat.py",
                      "utils/token_manager.py", "utils/llm_gateway.py",
                      "model_router.py"):
            src = open(os.path.join(here, fname)).read()
            # 只禁"硬编码赋值"（注释里提教训原文不在此列）
            assert 'llm_base_url="https://token-plan-cn' not in src, f"{fname} 仍硬编码provider URL"
            assert 'llm_model="xiaomi/' not in src, f"{fname} 仍硬编码模型名"
            assert "resolve_llm_config" in src, f"{fname} 未接统一配置解析"


# ─────────────────────── ② _call_llm 失败必须抛 ───────────────────────


def _resp(status=200, text="", content=None, finish="stop"):
    class R:
        status_code = status
        headers = {}

        def __init__(self):
            self.text = text or (json.dumps({
                "choices": [{"message": {"content": content or ""}, "finish_reason": finish}]
            }) if content is not None else text)

        def json(self):
            return json.loads(self.text)

    return R()


def _fake_client_factory(responses):
    """按序返回响应的假httpx.AsyncClient工厂（responses耗尽后继续抛错以暴露意外调用）"""
    queue = list(responses)

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            if not queue:
                raise AssertionError("超出预期的LLM调用")
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    return _FakeClient


def _strand(tmp_path):
    (tmp_path / "acp-proxy" / "data").mkdir(parents=True, exist_ok=True)
    return DNAStrand(
        strand_id="strand_a",
        role=StrandRole.PRIMARY,
        repo_root=str(tmp_path),
        llm_base_url="https://example.invalid/v1",
        llm_api_key="sk-test",
        llm_model="m-test",
    )


class TestCallLLMFailureVisible:
    def test_http_401_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient",
                            _fake_client_factory([_resp(401, text='{"error": "Invalid API Key"}')]))
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError) as ei:
            run(s._call_llm("hi"))
        assert "401" in str(ei.value)
        assert s._llm_failure_streak == 0  # streak在_evolve层记账，_call_llm只负责报错

    def test_http_402_raises_not_empty_string(self, tmp_path, monkeypatch):
        """402余额不足=失败，绝不静默返""让上游记成'无改进'"""
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient",
                            _fake_client_factory([_resp(402, text='{"error": "Insufficient account balance"}')]))
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError):
            run(s._call_llm("hi"))

    def test_http_200_empty_content_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient",
                            _fake_client_factory([_resp(200, content="")]))
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError) as ei:
            run(s._call_llm("hi"))
        assert "空内容" in str(ei.value)

    def test_success_returns_content_and_resets_streak(self, tmp_path, monkeypatch):
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient",
                            _fake_client_factory([_resp(200, content="hello")]))
        s = _strand(tmp_path)
        s._llm_failure_streak = 5
        assert run(s._call_llm("hi")) == "hello"
        assert s._llm_failure_streak == 0

    def test_transport_exception_wrapped(self, tmp_path, monkeypatch):
        import httpx as _httpx
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient",
                            _fake_client_factory([_httpx.ConnectError("connection refused")]))
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError):
            run(s._call_llm("hi"))

    def test_partial_content_kept_on_mid_continuation_failure(self, tmp_path, monkeypatch):
        """续写中断：半截输出不丢（partial_content），但仍判失败"""
        monkeypatch.setattr(dna_evolution.httpx, "AsyncClient",
                            _fake_client_factory([
                                _resp(200, content="PART-", finish="length"),
                                _resp(400, text='{"error": "bad request"}'),
                            ]))
        s = _strand(tmp_path)
        with pytest.raises(LLMCallError) as ei:
            run(s._call_llm("hi"))
        assert ei.value.partial_content == "PART-"


# ─────────────── ③ _evolve 失败记账：失败≠无改进 ───────────────


class TestEvolveFailureAccounting:
    def test_llm_failure_recorded_as_failure_not_no_improvement(self, tmp_path, monkeypatch):
        s = _strand(tmp_path)

        async def _boom(prompt):
            raise LLMCallError("LLM HTTP 401: Invalid API Key")

        s._call_llm = _boom
        run(s._evolve())

        assert s._llm_failure_streak == 1
        assert "401" in s._last_llm_error
        assert s._last_llm_error_at
        assert s._cycle_count == 1
        assert len(s._history) == 1
        entry = s._history[0]
        assert entry["success"] is False
        assert "llm_error" in entry and "401" in entry["llm_error"]
        assert entry["stage"] == EvolutionStage.REFLECT.value
        # 关键反伪装断言：绝不出现"规划0项改进"这类"无改进"话术
        blob = json.dumps({"history": s._history, "memory": s._memory}, ensure_ascii=False)
        assert "规划0项改进" not in blob
        assert s._memory == []  # 失败不产教训
        # 状态面可见
        st = s.get_status()
        assert st["llm_failure_streak"] == 1
        assert "401" in st["last_llm_error"]
        assert st["last_llm_error_at"]

    def test_llm_failure_streak_accumulates(self, tmp_path):
        s = _strand(tmp_path)

        async def _boom(prompt):
            raise LLMCallError("LLM HTTP 402: insufficient balance")

        s._call_llm = _boom
        run(s._evolve())
        run(s._evolve())
        assert s._llm_failure_streak == 2
        assert s._cycle_count == 2
        assert all("llm_error" in h for h in s._history)

    def test_genuine_no_improvement_has_no_llm_error(self, tmp_path):
        """LLM成功但真无改进：记为'0项改进'但**无llm_error键**——与失败可区分"""
        s = _strand(tmp_path)

        async def _ok(prompt):
            if "改进方案" in prompt:
                return '{"improvements": []}'
            if "教训" in prompt:
                return '{"lesson": "x", "pattern": "y"}'
            return '{"failure_patterns": [], "success_patterns": [], "improvements": [], "goal_progress": {}}'

        s._call_llm = _ok
        run(s._evolve())

        assert len(s._history) == 1
        entry = s._history[0]
        assert "llm_error" not in entry  # 成功路径不带失败标记
        assert entry["improvements_count"] == 0
        assert s._llm_failure_streak == 0

    def test_extract_lesson_llm_failure_keeps_lesson_body(self, tmp_path):
        """教训提炼的LLM失败不影响已成文教训，且不冒充周期失败"""
        s = _strand(tmp_path)
        s.observe("failure", "test observation")
        from dna_evolution import EvolutionCycle
        cycle = EvolutionCycle(
            cycle_id="c1", strand_id="strand_a",
            stage=EvolutionStage.LEARN, trigger="t",
            plan={"improvements": []},
        )
        s._cycles.extend([cycle, cycle])  # 满足≥2周期才走LLM提炼分支（真正打到被测代码）

        async def _boom(prompt):
            raise LLMCallError("LLM HTTP 500: boom")

        s._call_llm = _boom
        lesson = run(s._extract_lesson(cycle))
        assert lesson is not None
        assert "insight" not in lesson  # 提炼失败留空，主体保留
