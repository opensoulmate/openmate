"""model_router 多提供商路由面配置收口单一真源 + fail-closed-lite 契约测试。

背景（销账00:10遗留#4第二批，live实锤）：model_router.PROVIDER_DEFAULTS 曾在
**import期**直接 os.environ.get 读提供商凭据：
① key 只写在 .env 文件（opensoul/.env LLM_API_KEY=51字符真实存在）时解析成 ""
   → live curl /api/model-router/config 九个模型 has_api_key 全 false
   → 选中的模型不带鉴权 → 静默401（与DNA进化strand/llm_gateway同款根因）；
② 极端兜底（全tier无可用模型）再抄一遍 os.environ.get("LLM_*")+硬编码
   "mimo-v2.5-pro" 兜底模型名——违反 llm_config 调用方纪律；
③ 全tier缺key时旧实现"返回第一个（让调用方报错）"=照样把无鉴权请求发出去。

本文件钉住：
① 提供商凭据/端点/覆盖项全部走 utils.llm_config.resolve_env_value 单一真源
   （.env文件优先于进程环境；别名先者优先）；
② 极端兜底走 resolve_llm_config(include_mimo_overrides=False)——不跟随MIMO_*
   进化覆写、不硬编码模型名、配置不完整 fail-closed 抛 RuntimeError；
③ requires_auth 提供商缺key不参选（绝不静默发无鉴权请求），ollama免key合法；
④ 接线护栏：model_router.py 禁 env 自扫/import os（防回潮）。
"""

from pathlib import Path

import pytest

import utils.llm_config as llm_config
from model_router import ModelRouter, resolve_provider_config
from utils.llm_config import read_merged_env_files, resolve_env_value

HERE = Path(__file__).resolve().parent.parent

_ENV_VARS = (
    "MIMO_API_KEY", "MIMO_BASE_URL", "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "CLAUDE_API_KEY", "ANTHROPIC_API_KEY",
    "CLAUDE_BASE_URL", "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
    "QWEN_API_KEY", "DASHSCOPE_API_KEY", "QWEN_BASE_URL",
    "OLLAMA_API_KEY", "OLLAMA_BASE_URL",
    "MODEL_ROUTER_COST", "MODEL_ROUTER_BALANCE", "MODEL_ROUTER_INTELLIGENCE",
    "MODEL_MIMO_V2_5_PRO_API_KEY", "MODEL_MIMO_V2_5_PRO_BASE_URL",
    "AGENT_TESTBOT_MODEL",
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setattr(
        llm_config, "_default_env_files", lambda: [str(tmp_path / "none.env")]
    )
    yield


def _env_file(tmp_path, text, name="env"):
    f = tmp_path / name
    f.write_text(text)
    return f


# ─────────────────── resolve_env_value 通用件语义 ───────────────────

class TestResolveEnvValue:
    def test_file_beats_env_same_name(self, monkeypatch, tmp_path):
        """同一name内部：.env文件优先于进程环境（文件优先合同）"""
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(tmp_path, "DEEPSEEK_API_KEY=sk-file\n"))],
        )
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
        v, src = resolve_env_value(["DEEPSEEK_API_KEY"])
        assert v == "sk-file"
        assert src == "file:DEEPSEEK_API_KEY"

    def test_specific_alias_beats_generic(self, monkeypatch, tmp_path):
        """names之间先者优先（专用别名>通用别名）——与resolve_llm_config别名语义一致"""
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(tmp_path, "LLM_API_KEY=sk-generic\n"))],
        )
        monkeypatch.setenv("MIMO_API_KEY", "sk-specific")
        v, src = resolve_env_value(["MIMO_API_KEY", "LLM_API_KEY"])
        assert v == "sk-specific"
        assert src == "env:MIMO_API_KEY"

    def test_missing_returns_default_with_empty_source(self):
        v, src = resolve_env_value(["NO_SUCH_KEY_ANYWHERE"], default="dflt")
        assert v == "dflt"
        assert src == ""

    def test_merged_files_first_wins(self, tmp_path):
        f1 = _env_file(tmp_path, "LLM_MODEL=m-first\n", "a.env")
        f2 = _env_file(tmp_path, "LLM_MODEL=m-second\n", "b.env")
        merged = read_merged_env_files([str(f1), str(f2)])
        assert merged["LLM_MODEL"] == "m-first"


# ─────────────────── 提供商凭据单一真源（live bug 回归钉） ───────────────────

class TestProviderConfigSingleSource:
    def test_router_sees_env_file_keys(self, monkeypatch, tmp_path):
        """回归钉（live实锤）：key只写在.env时旧实现九模型has_api_key全false→静默401"""
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(tmp_path, "LLM_API_KEY=sk-file-51chars\n"))],
        )
        router = ModelRouter()
        mimo_models = [
            m for tier in router._model_table.values() for m in tier
            if m.provider == "mimo"
        ]
        assert mimo_models, "默认表应含mimo模型"
        for m in mimo_models:
            assert m.api_key == "sk-file-51chars"
            assert m.key_source == "file:LLM_API_KEY"
        cfg = router.get_config()
        assert cfg["model_table"]["balance"][0]["has_api_key"] is True
        assert cfg["model_table"]["balance"][0]["key_source"] == "file:LLM_API_KEY"

    def test_file_wins_over_env_for_provider_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(tmp_path, "DEEPSEEK_API_KEY=sk-file\n"))],
        )
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env")
        p = resolve_provider_config("deepseek")
        assert p["api_key"] == "sk-file"
        assert p["key_source"] == "file:DEEPSEEK_API_KEY"

    def test_per_model_override_beats_provider_key(self, monkeypatch, tmp_path):
        """MODEL_<MODEL名>_API_KEY 每模型覆盖优先级最高（既有语义保留）"""
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(tmp_path, "LLM_API_KEY=sk-provider\n"))],
        )
        monkeypatch.setenv("MODEL_MIMO_V2_5_PRO_API_KEY", "sk-model")
        router = ModelRouter()
        balance = {m.name: m for m in router._model_table["balance"]}
        assert balance["mimo-v2.5-pro"].api_key == "sk-model"
        assert balance["mimo-v2.5-pro"].key_source == "env:MODEL_MIMO_V2_5_PRO_API_KEY"
        assert balance["deepseek-chat"].api_key == ""  # 未配置的不受影响

    def test_lazy_resolution_not_import_time(self, monkeypatch, tmp_path):
        """import期捕获已废：模块import后才设置的配置能被新建router看见"""
        router1 = ModelRouter()
        assert router1._model_table["balance"][0].api_key == ""
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(tmp_path, "LLM_API_KEY=sk-late\n"))],
        )
        router2 = ModelRouter()
        assert router2._model_table["balance"][0].api_key == "sk-late"

    def test_no_key_leaves_provider_unkeyed(self):
        p = resolve_provider_config("openai")
        assert p["api_key"] == ""
        assert p["key_source"] == ""
        assert p["requires_auth"] is True


# ─────────────────── fail-closed-lite 选择语义 ───────────────────

class TestFailClosedLiteSelection:
    def test_keyless_auth_models_not_selected(self, monkeypatch):
        """缺key的requires_auth模型不参选——落到tier内下一个有key的（绝不无鉴权外发）"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        router = ModelRouter()
        sel = router.select_model("帮我分析一下", mode="cost")
        # cost tier三个模型全缺key → 不参选 → 回退balance → deepseek-chat
        assert sel.model == "deepseek-chat"
        assert sel.api_key == "sk-ds"
        assert "回退到balance" in sel.reason

    def test_ollama_exempt_from_key(self, monkeypatch):
        """本地provider（ollama）免key合法"""
        monkeypatch.setenv("MODEL_ROUTER_COST", "ollama:llama3")
        router = ModelRouter()
        sel = router.select_model("hi", mode="cost")
        assert sel.model == "llama3"
        assert sel.api_key == ""

    def test_all_keyless_raises_no_unauthed_config(self):
        """全tier缺key且系统默认LLM不完整 → RuntimeError（fail-closed，绝不返回无鉴权配置）"""
        router = ModelRouter()
        with pytest.raises(RuntimeError, match="系统默认LLM配置不完整"):
            router.select_model("hi", mode="cost")

    def test_agent_override_file_first(self, monkeypatch, tmp_path):
        """AGENT_<ID>_MODEL 专属覆盖也走单一真源（文件优先）"""
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [
                str(_env_file(tmp_path, "AGENT_TESTBOT_MODEL=deepseek:deepseek-chat\n"))
            ],
        )
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
        router = ModelRouter()
        sel = router.select_model("hi", mode="balance", agent_id="testbot")
        assert sel.model == "deepseek-chat"


# ─────────────────── 极端兜底：系统默认LLM单一真源 ───────────────────

class TestExtremeFallbackSingleSource:
    def _force_all_tiers_unusable(self, monkeypatch):
        monkeypatch.setenv("MODEL_ROUTER_COST", "claude:none1")
        monkeypatch.setenv("MODEL_ROUTER_BALANCE", "claude:none2")
        monkeypatch.setenv("MODEL_ROUTER_INTELLIGENCE", "claude:none3")

    def test_fallback_uses_llm_config_file_first(self, monkeypatch, tmp_path):
        self._force_all_tiers_unusable(monkeypatch)
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(
                tmp_path,
                "LLM_API_KEY=sk-sys\nLLM_BASE_URL=https://sys/v1\nLLM_MODEL=m-sys\n",
            ))],
        )
        router = ModelRouter()
        sel = router.select_model("hi", mode="auto")
        assert sel.model == "m-sys"
        assert sel.base_url == "https://sys/v1"
        assert sel.api_key == "sk-sys"
        assert sel.provider == "default"
        # 兜底路径key_source用resolve_llm_config词表（file/env/mimo_env/""）；
        # provider路径用resolve_env_value词表（file:NAME/env:NAME/""）——两真源各自词表如实透传
        assert sel.key_source == "file"

    def test_mimo_override_not_leaked_to_fallback(self, monkeypatch, tmp_path):
        """调用面语义：系统默认LLM兜底不跟随MIMO_*进化覆写（与llm_gateway同款）"""
        self._force_all_tiers_unusable(monkeypatch)
        monkeypatch.setattr(
            llm_config, "_default_env_files",
            lambda: [str(_env_file(
                tmp_path,
                "LLM_API_KEY=sk-sys\nLLM_BASE_URL=https://sys/v1\nLLM_MODEL=m-sys\n",
            ))],
        )
        monkeypatch.setenv("MIMO_API_KEY", "sk-evil")
        monkeypatch.setenv("MIMO_MODEL", "m-evil")
        router = ModelRouter()
        sel = router.select_model("hi", mode="auto")
        assert sel.model == "m-sys"
        assert sel.api_key == "sk-sys"


# ─────────────────── 接线护栏（防回潮） ───────────────────

class TestWiring:
    def test_no_env_self_scan_or_hardcode(self):
        src = (HERE / "model_router.py").read_text()
        # 只禁运行时代码形态（注释里提教训原文不在此列，同test_dna_llm_config_failure惯例）
        assert "os.environ.get(" not in src
        assert "import os" not in src
        # 旧import期env快照表已绝迹（注释里提教训原文不在此列）
        assert "PROVIDER_DEFAULTS: dict" not in src
        assert "resolve_env_value" in src
        assert "resolve_llm_config" in src
        assert "resolve_provider_config" in src
        # 硬编码兜底模型名已绝迹（教训原文注释除外——旧值mimo-v2.5-pro不再作为兜底赋值）
        assert 'name=os.environ.get("LLM_MODEL"' not in src

    def test_llm_config_exports_generic_resolver(self):
        src = (HERE / "utils" / "llm_config.py").read_text()
        assert "def resolve_env_value(" in src
        assert "def read_merged_env_files(" in src
