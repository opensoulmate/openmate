"""agent/内嵌soulmate引擎 LLM配置收口单一真源 契约测试（2026-09-27轮）。

背景（销账遗留#2）：agent/llm_engine.py:78-80、agent/start.py:34-36、
agent/wakeup_executor.py:194-198 三处各自 `os.environ.get("LLM_*",
os.environ.get("OPENAI_*", 默认))` 自扫——与全系统 resolve_llm_config 单一真源
双源漂移：①只经 load_dotenv 见 acp-proxy/.env，不见 opensoul/.env 主配置；
②同一兜底槽位两个硬编码模型名（deepseek-r1:latest vs qwen3:8b）；③key缺失
静默发无鉴权请求（DNA进化strand 401事故同款根因）；④无key_source观测。

本文件钉住调用面契约（utils/llm_config.resolve_agent_llm_config docstring）：
① 显式参数 > ② 宿主注入OPENAI_* > ③ 静态单一真源（文件>env）> ④ 裸机本地
Ollama兜底；MIMO_* 进化覆写绝不跟随；接线护栏禁三文件回潮自扫。
"""

import os

import pytest

from utils.llm_config import (
    AGENT_LOCAL_BASE_URL,
    AGENT_LOCAL_MODEL,
    describe,
    resolve_agent_llm_config,
)

_ENV_KEYS = (
    "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "MIMO_API_KEY", "MIMO_BASE_URL", "MIMO_MODEL",
)


@pytest.fixture()
def clean_env(monkeypatch):
    """清空LLM_/OPENAI_/MIMO_配置env，测试只看注入的tmp文件与显式注入。"""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def _patch_files(monkeypatch, tmp_path, *contents: str) -> list[str]:
    """把单一真源的默认env文件列表替换成tmp文件（先者优先=第一个文件）。"""
    paths = []
    for i, c in enumerate(contents):
        p = tmp_path / f"f{i}.env"
        p.write_text(c)
        paths.append(str(p))
    monkeypatch.setattr("utils.llm_config._default_env_files", lambda: paths)
    return paths


# ─────────────────── ① 调用面契约：四层优先级 ───────────────────


class TestResolveAgentLLMConfig:
    def test_explicit_params_win_over_everything(self, monkeypatch, tmp_path, clean_env):
        """① 显式参数最高优先（盖过注入与文件），key_source=param。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-inject")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://inject/v1")
        monkeypatch.setenv("OPENAI_MODEL", "m-inject")
        cfg = resolve_agent_llm_config(api_key="sk-param", base_url="https://param/v1", model="m-param")
        assert cfg["api_key"] == "sk-param"
        assert cfg["base_url"] == "https://param/v1"
        assert cfg["model"] == "m-param"
        assert cfg["key_source"] == "param"

    def test_injected_openai_env_beats_file(self, monkeypatch, tmp_path, clean_env):
        """② 宿主注入契约回归钉：OPENAI_* 注入必须盖过静态文件。

        旧实现 load_dotenv 把文件合并进env，静态配置反向遮蔽 ws_chat 注入——
        契约失效。本测试钉死"运行时注入 > 静态文件"。
        """
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-inject")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://inject/v1")
        monkeypatch.setenv("OPENAI_MODEL", "m-inject")
        cfg = resolve_agent_llm_config()
        assert cfg["api_key"] == "sk-inject"
        assert cfg["base_url"] == "https://inject/v1"
        assert cfg["model"] == "m-inject"
        assert cfg["key_source"] == "openai_env"

    def test_static_file_single_source_beats_llm_env(self, monkeypatch, tmp_path, clean_env):
        """③ 静态单一真源：文件优先于 LLM_* 进程env（与 resolve_llm_config 同款合同）。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        monkeypatch.setenv("LLM_API_KEY", "sk-env")
        monkeypatch.setenv("LLM_MODEL", "m-env")
        cfg = resolve_agent_llm_config()
        assert cfg["api_key"] == "sk-file"
        assert cfg["model"] == "m-file"
        assert cfg["key_source"] == "file"

    def test_first_env_file_wins(self, monkeypatch, tmp_path, clean_env):
        """opensoul/.env 主配置在前先者优先——修复旧实现只读 acp-proxy/.env 的源漂移回归钉。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-first\nLLM_MODEL=m-first\n",
                     "LLM_API_KEY=sk-second\nLLM_MODEL=m-second\n")
        cfg = resolve_agent_llm_config()
        assert cfg["api_key"] == "sk-first"
        assert cfg["model"] == "m-first"

    def test_env_fallback_when_no_file(self, monkeypatch, tmp_path, clean_env):
        """无文件时 LLM_* 进程env兜底，key_source=env。"""
        _patch_files(monkeypatch, tmp_path)  # 空文件列表
        monkeypatch.setenv("LLM_API_KEY", "sk-env")
        monkeypatch.setenv("LLM_BASE_URL", "https://env/v1")
        monkeypatch.setenv("LLM_MODEL", "m-env")
        cfg = resolve_agent_llm_config()
        assert cfg["api_key"] == "sk-env"
        assert cfg["base_url"] == "https://env/v1"
        assert cfg["key_source"] == "env"

    def test_local_ollama_fallback_when_all_missing(self, monkeypatch, tmp_path, clean_env):
        """④ 全空→裸机本地Ollama兜底（deliberate保留），key_source=""如实为空。"""
        _patch_files(monkeypatch, tmp_path)
        cfg = resolve_agent_llm_config()
        assert cfg["base_url"] == AGENT_LOCAL_BASE_URL == "http://localhost:11434/v1"
        assert cfg["model"] == AGENT_LOCAL_MODEL
        assert cfg["api_key"] == ""
        assert cfg["key_source"] == ""

    def test_model_default_unified_no_drift(self, monkeypatch, tmp_path, clean_env):
        """双源漂移销账回归钉：兜底模型名全库统一，deepseek-r1:latest 绝迹。"""
        assert AGENT_LOCAL_MODEL == "qwen3:8b"  # 多数派定案（start/wakeup旧值）
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for fname in ("agent/llm_engine.py", "agent/start.py", "agent/wakeup_executor.py"):
            src = open(os.path.join(here, fname)).read()
            # 只禁"硬编码赋值字面量"（注释里提教训原文不在此列）
            assert '"deepseek-r1:latest"' not in src, f"{fname} 仍残留旧硬编码兜底模型名"
            assert '"qwen3:8b"' not in src, f"{fname} 不得自持兜底模型名（统一走 AGENT_LOCAL_MODEL）"

    def test_mimo_overrides_not_followed(self, monkeypatch, tmp_path, clean_env):
        """MIMO_* 进化覆写绝不泄漏到 agent 运行时调用面。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_MODEL=m-file\n")
        monkeypatch.setenv("MIMO_API_KEY", "sk-mimo")
        monkeypatch.setenv("MIMO_MODEL", "m-mimo")
        cfg = resolve_agent_llm_config()
        assert cfg["api_key"] == "sk-file"
        assert cfg["model"] == "m-file"
        assert cfg["key_source"] == "file"

    def test_partial_explicit_params_fall_through(self, monkeypatch, tmp_path, clean_env):
        """逐字段独立解析：只传model时api_key/base_url仍走后续层（discussion_engine调用面）。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        cfg = resolve_agent_llm_config(model="m-only")
        assert cfg["model"] == "m-only"
        assert cfg["api_key"] == "sk-file"
        assert cfg["base_url"] == "https://file/v1"
        assert cfg["key_source"] == "file"

    def test_describe_never_leaks_key(self, monkeypatch, tmp_path, clean_env):
        """key原文绝不进日志/状态面。"""
        _patch_files(monkeypatch, tmp_path, "LLM_API_KEY=SUPERSECRET123\n")
        cfg = resolve_agent_llm_config()
        assert cfg["api_key"] == "SUPERSECRET123"  # 解析层如实返回
        d = describe(cfg)
        assert "SUPERSECRET123" not in d
        assert "key_source=file" in d


# ─────────────────── ② 接线：LLMEngine/start 真实调用路径 ───────────────────


class TestWiring:
    def test_llm_engine_bare_init_uses_single_source(self, monkeypatch, tmp_path, clean_env):
        """裸 LLMEngine()（acp_server.py:116 真实调用面）走单一真源，key_source可观测。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        from agent.llm_engine import LLMEngine

        engine = LLMEngine()
        assert engine.api_key == "sk-file"
        assert engine.base_url == "https://file/v1"
        assert engine.model == "m-file"
        assert engine.key_source == "file"

    def test_llm_engine_explicit_params_still_win(self, monkeypatch, tmp_path, clean_env):
        """显式参数优先级不回归（既有测试/调用方依赖：LLMEngine(api_key=..., ...)）。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_MODEL=m-file\n")
        from agent.llm_engine import LLMEngine

        engine = LLMEngine(api_key="k", base_url="http://fake/v1", model="mimo-test")
        assert (engine.api_key, engine.base_url, engine.model) == ("k", "http://fake/v1", "mimo-test")
        assert engine.key_source == "param"

    def test_llm_engine_injected_openai_env_path(self, monkeypatch, tmp_path, clean_env):
        """宿主注入OPENAI_*路径在LLMEngine构造点真实生效（ws_acp子进程env注入契约）。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-inject")
        monkeypatch.setenv("OPENAI_MODEL", "m-inject")
        from agent.llm_engine import LLMEngine

        engine = LLMEngine()
        assert engine.api_key == "sk-inject"
        assert engine.model == "m-inject"
        assert engine.key_source == "openai_env"

    def test_start_load_llm_config_delegates(self, monkeypatch, tmp_path, clean_env):
        """agent/start.load_llm_config 委托单一真源且返回形状不变（LLMEngine(**cfg)兼容）。"""
        _patch_files(monkeypatch, tmp_path,
                     "LLM_API_KEY=sk-file\nLLM_BASE_URL=https://file/v1\nLLM_MODEL=m-file\n")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        from agent.start import load_llm_config

        cfg = load_llm_config()
        assert set(cfg.keys()) == {"api_key", "base_url", "model"}  # 返回形状钉
        assert cfg["api_key"] == "sk-file"
        assert cfg["model"] == "m-file"

    def test_no_env_self_scan_in_agent_files(self):
        """接线护栏：三处旧自扫点禁回潮，必须走 resolve_agent_llm_config。"""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for fname in ("agent/llm_engine.py", "agent/start.py", "agent/wakeup_executor.py"):
            src = open(os.path.join(here, fname)).read()
            for name in ("LLM_API_KEY", "OPENAI_API_KEY", "LLM_BASE_URL",
                         "OPENAI_BASE_URL", "LLM_MODEL", "OPENAI_MODEL"):
                assert f'os.environ.get("{name}"' not in src, f"{fname} 重新自扫 {name}"
            assert "resolve_agent_llm_config" in src, f"{fname} 未接 agent 调用面单一真源"

    def test_soulmate_model_label_prefers_engine(self):
        """归因标签（soulmate_agent token归因）优先用引擎已解析model，env只兜底。"""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        src = open(os.path.join(here, "agent/soulmate_agent.py")).read()
        assert 'getattr(self.llm_engine, "model", "")' in src
        assert src.index('getattr(self.llm_engine, "model", "")') < src.index(
            'os.environ.get("LLM_MODEL", "")'
        ), "engine model 必须先于 env 自扫"
