"""utils.llm_gateway 默认模型配置收口单一真源 + fail-closed 契约测试。

背景（销账00:10遗留#4第一批）：llm_gateway.DEFAULT_MODELS 曾自扫 os.environ 且
硬编码 provider URL/模型名兜底（"https://api.mimo.ai/v1" + "mimo-v2"）：
① key 只写在 .env 文件时 → api_key="" → _call_with_protection 把鉴权头做成
   可选（if config.api_key:）→ 静默发出无鉴权请求 → 401（与DNA进化strand
   硬编码401事故同款根因）；
② 硬编码兜底违反 llm_config 调用方纪律（"禁止在调用点硬编码 provider URL/模型名"）。

本文件钉住：默认模型走单一真源（文件优先）、MIMO_* 不泄漏（业务/用户侧调用面）、
缺 key/base_url fail-closed 拒绝调用（零HTTP请求）、有配置时真实请求带鉴权头。
"""

import asyncio
from pathlib import Path

import pytest

import utils.llm_config as llm_config
from utils import llm_gateway
from utils.llm_errors import LLMCallError

HERE = Path(__file__).resolve().parent.parent

_ENV_VARS = (
    "MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "MIMO_MAX_TOKENS",
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_MAX_TOKENS",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(
        llm_config, "_default_env_files", lambda: [str(tmp_path / "none.env")]
    )
    yield
    llm_gateway.refresh_default_model()


class _FakeResp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """记录真实出站请求（正向路径证明：有配置时请求真的带鉴权头发出）"""

    captured: dict = {}

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, headers=None, json=None):
        _FakeClient.captured = {"url": url, "headers": headers, "json": json}
        return _FakeResp(
            200,
            {"choices": [{"message": {"content": "hello world"},
                          "finish_reason": "stop"}]},
        )


class _BoomClient:
    """缺配置时 HTTP 层一旦被碰到即失败（fail-closed = 零请求）"""

    def __init__(self, *args, **kwargs):
        raise AssertionError("缺配置时不得发起HTTP请求")


class TestDefaultModelFromSingleSource:
    def test_file_config_used(self, monkeypatch, tmp_path):
        """文件优先收口：旧实现只读env+硬编码URL兜底，.env里的key/URL全被无视"""
        f = tmp_path / "env"
        f.write_text(
            "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n"
        )
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f)])
        monkeypatch.setenv("LLM_API_KEY", "sk-env")  # 文件优先于env
        m = llm_gateway.refresh_default_model()
        assert m.api_key == "sk-file"
        assert m.base_url == "https://file/v1"
        assert m.model == "m-file"
        assert llm_gateway.get_model("default").model == "m-file"

    def test_mimo_override_not_leaked(self, monkeypatch, tmp_path):
        """调用面语义：业务/用户侧流量不跟随 MIMO_*（进化strand专用覆写）"""
        f = tmp_path / "env"
        f.write_text(
            "LLM_API_KEY=sk-sys\nLLM_BASE_URL=https://sys/v1\nLLM_MODEL=m-sys\n"
        )
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f)])
        monkeypatch.setenv("MIMO_API_KEY", "sk-mimo")
        monkeypatch.setenv("MIMO_BASE_URL", "https://mimo/v1")
        monkeypatch.setenv("MIMO_MODEL", "m-mimo")
        m = llm_gateway.refresh_default_model()
        assert m.api_key == "sk-sys"
        assert m.base_url == "https://sys/v1"
        assert m.model == "m-sys"


class TestFailClosedNoUnauthedRequest:
    def test_missing_key_refuses_without_http(self, monkeypatch, tmp_path):
        """旧实现：空key照样发请求（鉴权头可选）→ 静默401。现在 fail-closed
        且失败可见（2026-09-29错误契约收口：LLMCallError，静默返""第三态废除）"""
        f = tmp_path / "env"
        f.write_text("LLM_BASE_URL=https://x/v1\nLLM_MODEL=m-x\n")  # 无key
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f)])
        llm_gateway.refresh_default_model()
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _BoomClient)
        with pytest.raises(LLMCallError):
            asyncio.run(llm_gateway.llm_call([{"role": "user", "content": "hi"}]))

    def test_missing_base_url_refuses_without_http(self, monkeypatch, tmp_path):
        f = tmp_path / "env"
        f.write_text("LLM_API_KEY=sk-x\nLLM_MODEL=m-x\n")  # 无base_url
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f)])
        llm_gateway.refresh_default_model()
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _BoomClient)
        with pytest.raises(LLMCallError):
            asyncio.run(llm_gateway.llm_call([{"role": "user", "content": "hi"}]))


class TestRealRequestCarriesAuth:
    def test_auth_header_and_target(self, monkeypatch, tmp_path):
        """正向路径：配置齐全时请求真实发出且带 Bearer 鉴权头（防恒拒绝死代码）"""
        f = tmp_path / "env"
        f.write_text(
            "LLM_API_KEY=sk-test\nLLM_BASE_URL=https://llm.example/v1\nLLM_MODEL=m-test\n"
        )
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f)])
        llm_gateway.refresh_default_model()
        _FakeClient.captured = {}
        monkeypatch.setattr(llm_gateway.httpx, "AsyncClient", _FakeClient)
        out = asyncio.run(llm_gateway.llm_call([{"role": "user", "content": "hi"}]))
        assert out == "hello world"
        assert _FakeClient.captured["headers"]["Authorization"] == "Bearer sk-test"
        assert _FakeClient.captured["url"] == "https://llm.example/v1/chat/completions"
        assert _FakeClient.captured["json"]["model"] == "m-test"


class TestWiring:
    """接线护栏（防回潮）"""

    def test_no_hardcoded_provider_or_env_scan(self):
        src = (HERE / "utils" / "llm_gateway.py").read_text()
        # 只禁"硬编码赋值"（注释里提教训原文不在此列，同test_dna_llm_config_failure惯例）
        assert '="https://api.mimo.ai/v1"' not in src
        assert '="mimo-v2"' not in src
        assert 'os.environ.get("LLM_' not in src
        assert "resolve_llm_config" in src
        assert "refresh_default_model" in src

    def test_convenience_wrappers_delegate_to_llm_call(self):
        src = (HERE / "utils" / "llm_gateway.py").read_text()
        assert "async def llm_call_simple" in src
        assert "async def llm_call_with_system" in src
        assert src.count("return await llm_call(") >= 2
