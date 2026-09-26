"""ws_chat._get_llm_config 收口 utils.llm_config.resolve_llm_config 单一真源契约测试。

背景（销账00:10遗留#4，两轮标注"下轮P1候选"）：
ws_chat._get_llm_config 曾自扫 opensoul/.env + 环境变量，与 resolve_llm_config
双入口并存，三处语义偏差：
① 容错：旧实现在 try 外 int()，坏的 LLM_MAX_TOKENS 直接抛 ValueError 炸代理；
   且 .env 里坏值行会中断其后所有键的解析（try 包整个循环）。
② 覆盖面：旧实现只读 opensoul/.env；单一真源还覆盖 acp-proxy/.env 与 MIMO_* 层。
③ 观测：旧实现无 key_source（"key从哪来"不可见，401排查靠猜）。

本文件钉住四件事：
① ws_chat 配置解析委托 resolve_llm_config（行为等价单一真源）；
② MIMO_*（进化strand专用覆写）绝不泄漏到用户侧 agent 代理；
③ 坏值容错不炸（旧崩溃路径的回归钉）；
④ 接线护栏：ws_chat 禁再自扫环境变量/自解析 .env，且 _get_llm_config 在
   run_agent_proxy 真实路径被调用。
"""

import os

import pytest

import utils.llm_config as llm_config
import ws_chat
from utils.llm_config import resolve_llm_config

_ENV_VARS = (
    "MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "MIMO_MAX_TOKENS",
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_MAX_TOKENS",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """清掉进程环境里的LLM变量+把默认env文件指向不存在的路径（真实opensoul/.env不得泄漏进测试）。"""
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(
        llm_config, "_default_env_files", lambda: [str(tmp_path / "none.env")]
    )


class TestWsChatDelegatesToSingleSource:
    def test_file_config_used_when_no_env(self, tmp_path, monkeypatch):
        f1 = tmp_path / "opensoul.env"
        f1.write_text(
            "# comment\nLLM_API_KEY=sk-file1\nLLM_BASE_URL=https://file1/v1\n"
            "LLM_MODEL=m-file1\nLLM_MAX_TOKENS=1234\n"
        )
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f1)])
        monkeypatch.setenv("LLM_API_KEY", "sk-env")  # 文件优先于环境变量
        cfg = ws_chat._get_llm_config()
        assert cfg["api_key"] == "sk-file1"
        assert cfg["base_url"] == "https://file1/v1"
        assert cfg["model"] == "m-file1"
        assert cfg["max_tokens"] == 1234
        assert cfg["key_source"] == "file"  # 观测面随单一真源透出

    def test_parity_with_resolve_llm_config_no_mimo(self, tmp_path, monkeypatch):
        """行为等价钉：ws_chat结果 == resolve_llm_config(include_mimo_overrides=False)"""
        f1 = tmp_path / "a.env"
        f1.write_text("LLM_API_KEY=sk-a\nLLM_BASE_URL=https://a/v1\nLLM_MODEL=m-a\n")
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f1)])
        monkeypatch.setenv("LLM_API_KEY", "sk-env")
        monkeypatch.setenv("LLM_MODEL", "m-env")
        assert ws_chat._get_llm_config() == resolve_llm_config(
            include_mimo_overrides=False
        )

    def test_env_var_fallback_when_no_file(self, monkeypatch):
        monkeypatch.setenv("LLM_API_KEY", "sk-env")
        monkeypatch.setenv("LLM_BASE_URL", "https://env/v1")
        monkeypatch.setenv("LLM_MODEL", "m-env")
        cfg = ws_chat._get_llm_config()
        assert cfg["api_key"] == "sk-env"
        assert cfg["base_url"] == "https://env/v1"
        assert cfg["model"] == "m-env"
        assert cfg["key_source"] == "env"

    def test_missing_key_is_empty_not_invented(self):
        cfg = ws_chat._get_llm_config()
        assert cfg["api_key"] == ""
        assert cfg["key_source"] == ""


class TestMimoOverrideDoesNotLeakToUserChat:
    def test_user_chat_ignores_evolution_override(self, tmp_path, monkeypatch):
        """MIMO_* 是进化strand专用覆写：用户侧代理必须仍走系统LLM配置。"""
        f1 = tmp_path / "a.env"
        f1.write_text(
            "LLM_API_KEY=sk-sys\nLLM_BASE_URL=https://sys/v1\nLLM_MODEL=m-sys\n"
        )
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f1)])
        monkeypatch.setenv("MIMO_API_KEY", "sk-evo")
        monkeypatch.setenv("MIMO_BASE_URL", "https://evo/v1")
        monkeypatch.setenv("MIMO_MODEL", "m-evo")

        chat_cfg = ws_chat._get_llm_config()
        assert chat_cfg["api_key"] == "sk-sys"
        assert chat_cfg["base_url"] == "https://sys/v1"
        assert chat_cfg["model"] == "m-sys"
        assert chat_cfg["key_source"] == "file"  # 非 mimo_env

        # 对照：进化侧默认含覆写（同一真源，两种调用面）
        evo_cfg = resolve_llm_config()
        assert evo_cfg["api_key"] == "sk-evo"
        assert evo_cfg["key_source"] == "mimo_env"

    def test_no_file_chat_ignores_mimo_falls_to_env(self, monkeypatch):
        monkeypatch.setenv("MIMO_API_KEY", "sk-evo")
        monkeypatch.setenv("LLM_API_KEY", "sk-env")
        cfg = ws_chat._get_llm_config()
        assert cfg["api_key"] == "sk-env"
        assert cfg["key_source"] == "env"


class TestBadValueTolerance:
    def test_bad_max_tokens_env_does_not_crash(self, monkeypatch):
        """回归钉：旧实现在 try 外 int(os.environ...)，坏值直接炸 run_agent_proxy。"""
        monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")
        cfg = ws_chat._get_llm_config()
        assert cfg["max_tokens"] == 65536

    def test_bad_max_tokens_file_line_does_not_lose_other_keys(self, tmp_path, monkeypatch):
        """回归钉：旧实现 try 包整个循环，坏值行后所有键丢失。"""
        f1 = tmp_path / "a.env"
        f1.write_text(
            "LLM_API_KEY=sk-keep\nLLM_MAX_TOKENS=oops\nLLM_MODEL=m-keep\n"
        )
        monkeypatch.setattr(llm_config, "_default_env_files", lambda: [str(f1)])
        cfg = ws_chat._get_llm_config()
        assert cfg["api_key"] == "sk-keep"
        assert cfg["model"] == "m-keep"
        assert cfg["max_tokens"] == 65536


class TestWiringGuardrail:
    HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_ws_chat_has_no_self_env_scanning(self):
        """ws_chat 禁再自扫环境变量/自解析 .env（单一真源收口护栏）。"""
        src = open(os.path.join(self.HERE, "ws_chat.py")).read()
        assert 'startswith("LLM_API_KEY=")' not in src, "ws_chat 重新自解析 .env"
        assert 'os.environ.get("LLM_API_KEY"' not in src, "ws_chat 重新自扫环境变量"
        assert "resolve_llm_config(include_mimo_overrides=False)" in src, (
            "ws_chat 未按约定调用面委托单一真源"
        )

    def test_get_llm_config_called_on_real_agent_proxy_path(self):
        """接线证据：_get_llm_config 在 run_agent_proxy 真实执行路径被调用。"""
        src = open(os.path.join(self.HERE, "ws_chat.py")).read()
        assert "llm_cfg = _get_llm_config()" in src
        fn_start = src.index("async def run_agent_proxy")
        fn_end = src.index("async def forward_to_agent_engine")
        assert "llm_cfg = _get_llm_config()" in src[fn_start:fn_end], (
            "run_agent_proxy 内未调用 _get_llm_config"
        )
        assert "describe(llm_cfg)" in src[fn_start:fn_end], (
            "run_agent_proxy 内未透出配置观测（key_source）"
        )
