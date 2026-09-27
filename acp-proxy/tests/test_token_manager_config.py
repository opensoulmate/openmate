"""utils.token_manager 模型限额收口 utils.llm_config 单一真源契约测试。

背景（销账00:10遗留#4第一批，上轮 ws_chat 收口的续篇）：
token_manager 曾在 import 期 `int(os.environ.get("LLM_MAX_TOKENS", "65536"))`
自扫环境变量，三处问题：
① 崩溃面：坏的 LLM_MAX_TOKENS → ValueError 在 import 期抛 → import
   utils.token_manager 失败 → agent/llm_engine/soulmate_agent/context_budget 全灭；
② 覆盖面：只读进程环境，.env 文件里的 LLM_MAX_TOKENS/LLM_CONTEXT_WINDOW/
   LLM_INPUT_LIMIT 被无视（与 resolve_llm_config"文件>env"语义漂移）；
③ probe_model_capabilities 内部又一遍自扫 env + int()（同样崩溃面）。

本文件钉住：收口委托、文件优先于env、坏值容错（旧崩溃回归钉）、MIMO_* 调用面
隔离、probe 不再自扫 env、接线护栏（token_manager/app.py/llm_engine 禁自扫 LLM_*）。
"""

import importlib
from pathlib import Path

import pytest

import utils.llm_config as llm_config
from utils import token_manager

HERE = Path(__file__).resolve().parent.parent

_ENV_VARS = (
    "MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL", "MIMO_MAX_TOKENS",
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_MAX_TOKENS",
    "LLM_CONTEXT_WINDOW", "LLM_INPUT_LIMIT", "TOOL_RESULT_MAX_CHARS",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """清进程环境LLM变量+默认env文件指向不存在路径（真实.env不得泄漏进测试）；
    测试跑完恢复缓存到默认值，避免污染其他测试模块。"""
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(
        llm_config, "_default_env_files", lambda: [str(tmp_path / "none.env")]
    )
    yield
    token_manager.reload_model_cache(env_files=[str(tmp_path / "none.env")])


class TestReloadCacheFromSingleSource:
    def test_file_values_honored_and_file_beats_env(self, tmp_path, monkeypatch):
        """文件优先+覆盖面收口：旧实现只读env，文件限额全被无视"""
        f = tmp_path / "env"
        f.write_text(
            "LLM_MAX_TOKENS=1234\nLLM_CONTEXT_WINDOW=200000\nLLM_INPUT_LIMIT=100000\n"
        )
        monkeypatch.setenv("LLM_MAX_TOKENS", "999")  # 文件优先于env
        cache = token_manager.reload_model_cache(env_files=[str(f)])
        assert cache["max_output_tokens"] == 1234
        assert cache["context_window"] == 200000
        assert cache["input_limit"] == 100000
        assert token_manager.get_max_output_tokens() == 1234
        assert token_manager.get_context_window() == 200000
        assert token_manager.get_input_limit() == 100000

    def test_env_fallback_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_CONTEXT_WINDOW", "99999")
        cache = token_manager.reload_model_cache(env_files=[str(tmp_path / "none.env")])
        assert cache["context_window"] == 99999

    def test_defaults_when_fully_unset(self, tmp_path):
        cache = token_manager.reload_model_cache(env_files=[str(tmp_path / "none.env")])
        assert cache["max_output_tokens"] == 65536
        assert cache["context_window"] == 131072
        assert cache["input_limit"] == 0

    def test_bad_values_fall_back_no_crash(self, tmp_path, monkeypatch):
        """旧崩溃回归钉：int('oops') 在 import 期抛 ValueError 炸模块"""
        monkeypatch.setenv("LLM_MAX_TOKENS", "oops")
        f = tmp_path / "env"
        f.write_text("LLM_CONTEXT_WINDOW=bad\nLLM_INPUT_LIMIT=x\n")
        cache = token_manager.reload_model_cache(env_files=[str(f)])  # 不得抛
        assert cache["max_output_tokens"] == 65536
        assert cache["context_window"] == 131072
        assert cache["input_limit"] == 0

    def test_reload_resets_probed(self, tmp_path):
        cache = token_manager.reload_model_cache(env_files=[str(tmp_path / "none.env")])
        cache["probed"] = True
        token_manager.reload_model_cache(env_files=[str(tmp_path / "none.env")])
        assert cache["probed"] is False  # 配置重载后旧探测作废


class TestMimoOverrideFace:
    def test_user_runtime_face_ignores_mimo(self, tmp_path, monkeypatch):
        """调用面语义：token_manager 服务用户侧运行时 → MIMO_*（进化覆写）绝不跟随"""
        monkeypatch.setenv("MIMO_MAX_TOKENS", "999")
        cache = token_manager.reload_model_cache(env_files=[str(tmp_path / "none.env")])
        assert cache["max_output_tokens"] == 65536  # 不是999

    def test_explicit_opt_in_takes_mimo(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIMO_MAX_TOKENS", "999")
        cache = token_manager.reload_model_cache(
            include_mimo_overrides=True, env_files=[str(tmp_path / "none.env")]
        )
        assert cache["max_output_tokens"] == 999


class TestProbeNoEnvRescan:
    def test_probe_uses_param_not_env(self, monkeypatch, tmp_path):
        """回归钉：旧 probe 内 int(os.environ.get('LLM_MAX_TOKENS')) 在坏env值时炸"""
        monkeypatch.setenv("LLM_MAX_TOKENS", "not-a-number")

        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = 200

                def json(self):
                    return {"usage": {}}

            return R()

        monkeypatch.setattr(token_manager.httpx, "post", fake_post)
        cache = token_manager.reload_model_cache(env_files=[str(tmp_path / "none.env")])
        out = token_manager.probe_model_capabilities(
            "https://x/v1", "k", "m", max_tokens=12345
        )
        assert out["probed"] is True
        assert out["max_output_tokens"] == 12345
        assert cache["max_output_tokens"] == 12345

    def test_probe_falls_back_to_cache_without_param(self, monkeypatch, tmp_path):
        def fake_post(url, headers=None, json=None, timeout=None):
            class R:
                status_code = 200

                def json(self):
                    return {"usage": {}}

            return R()

        monkeypatch.setattr(token_manager.httpx, "post", fake_post)
        f = tmp_path / "env"
        f.write_text("LLM_MAX_TOKENS=4321\n")
        token_manager.reload_model_cache(env_files=[str(f)])
        out = token_manager.probe_model_capabilities("https://x/v1", "k", "m")
        assert out["max_output_tokens"] == 4321


class TestToolResultCapSafe:
    def test_bad_value_no_import_crash(self, monkeypatch):
        """TOOL_RESULT_MAX_CHARS 坏值不得炸 import（旧 int() 同款崩溃面）"""
        monkeypatch.setenv("TOOL_RESULT_MAX_CHARS", "oops")
        try:
            importlib.reload(token_manager)
            assert token_manager.TOOL_RESULT_MAX_CHARS == 8000
        finally:
            monkeypatch.delenv("TOOL_RESULT_MAX_CHARS", raising=False)
            importlib.reload(token_manager)


class TestWiring:
    """接线护栏（防死代码/防回潮）：真实消费点禁再自扫 LLM_*"""

    def test_token_manager_delegates_single_source(self):
        src = (HERE / "utils" / "token_manager.py").read_text()
        assert "resolve_llm_config" in src
        assert "reload_model_cache()" in src  # import 期真实回填
        assert 'os.environ.get("LLM_MAX_TOKENS"' not in src
        assert 'os.environ.get("LLM_CONTEXT_WINDOW"' not in src
        assert 'os.environ.get("LLM_INPUT_LIMIT"' not in src
        assert "int(os.environ.get" not in src  # 旧崩溃模式绝迹

    def test_app_probe_uses_single_source(self):
        src = (HERE / "app.py").read_text()
        assert "probe_model_capabilities(" in src
        assert "probe_cfg = resolve_llm_config()" in src
        assert 'os.environ.get("LLM_BASE_URL"' not in src
        assert 'os.environ.get("LLM_API_KEY"' not in src
        assert 'os.environ.get("LLM_MODEL"' not in src

    def test_llm_engine_max_tokens_single_source(self):
        src = (HERE / "agent" / "llm_engine.py").read_text()
        assert 'os.environ.get("LLM_MAX_TOKENS"' not in src
        assert src.count("get_max_output_tokens()") >= 3  # 173/293/506 三处同源

    def test_consumer_paths_reference_token_manager(self):
        """token_manager 的真实消费点（soulmate真实消息路径）仍然接线"""
        for rel in ("agent/llm_engine.py", "agent/context_budget.py",
                    "agent/soulmate_agent.py"):
            src = (HERE / rel).read_text()
            assert "token_manager" in src or "get_max_output_tokens" in src, rel
