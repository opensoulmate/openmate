"""统一LLM配置解析 — DNA进化/后台调用的单一真源（2026-09-26轮）。

背景（live实锤的教训，禁止重犯）：DNA进化strand曾把 provider URL 和模型名硬编码成
`https://token-plan-cn.xiaomimimo.com/v1` + `xiaomi/mimo-v2.5-pro`，key 用
`os.environ.get("MIMO_API_KEY", "")`（该变量在 systemd 服务里从未设置）→
请求**不带鉴权头** → 每小时 401 Invalid API Key 空转；且失败被静默记成
"规划0项改进"（LLM故障伪装成"进化没发现可改进项"）。

配置优先级（高→低，先者优先）：
1. MIMO_API_KEY / MIMO_BASE_URL / MIMO_MODEL / MIMO_MAX_TOKENS
   —— 进化strand专用覆写（运维显式把进化指向另一套LLM时才设置）
2. opensoul/.env → acp-proxy/.env 文件
   —— 与 ws_chat._get_llm_config 同款"文件优先"语义（进程环境可能不含key，
      .env 是运维管理的配置真源；两个文件先者优先）
3. LLM_API_KEY / LLM_BASE_URL / LLM_MODEL / LLM_MAX_TOKENS 进程环境变量兜底

调用方纪律：禁止在调用点硬编码 provider URL/模型名/自扫环境变量——一律走
resolve_llm_config()；key 缺失要显式告警（401早知道），不得静默发出无鉴权请求。
"""

from __future__ import annotations

import os


def _read_env_file(path: str) -> dict:
    """解析 .env 文件（KEY=VALUE，#注释），失败返回空dict不抛。"""
    out: dict = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def _default_env_files() -> list[str]:
    """opensoul/.env（主配置）→ acp-proxy/.env（本地覆盖备选）"""
    opensoul_root = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [
        os.path.join(opensoul_root, ".env"),
        os.path.join(here, ".env"),
    ]


def resolve_llm_config(env_files: list[str] | None = None) -> dict:
    """解析LLM配置，返回 {api_key, base_url, model, max_tokens, key_source}。

    key_source ∈ {"mimo_env", "env", "file", ""}（观测面可据此判key从哪来；
    key本身绝不进日志/状态面，只报key_source与key长度）。
    """
    files = _default_env_files() if env_files is None else list(env_files)
    file_cfg: dict = {}
    for p in files:
        for k, v in _read_env_file(p).items():
            file_cfg.setdefault(k, v)  # 先者优先（opensoul/.env 在前）

    mimo_key = os.environ.get("MIMO_API_KEY", "").strip()
    env_key = os.environ.get("LLM_API_KEY", "").strip()
    file_key = file_cfg.get("LLM_API_KEY", "").strip()

    if mimo_key:
        api_key, key_source = mimo_key, "mimo_env"
    elif file_key:
        api_key, key_source = file_key, "file"
    elif env_key:
        api_key, key_source = env_key, "env"
    else:
        api_key, key_source = "", ""

    base_url = (
        os.environ.get("MIMO_BASE_URL", "").strip()
        or file_cfg.get("LLM_BASE_URL", "").strip()
        or os.environ.get("LLM_BASE_URL", "").strip()
    )
    model = (
        os.environ.get("MIMO_MODEL", "").strip()
        or file_cfg.get("LLM_MODEL", "").strip()
        or os.environ.get("LLM_MODEL", "").strip()
    )
    raw_max_tokens = (
        os.environ.get("MIMO_MAX_TOKENS", "").strip()
        or file_cfg.get("LLM_MAX_TOKENS", "").strip()
        or os.environ.get("LLM_MAX_TOKENS", "").strip()
    )
    try:
        max_tokens = int(raw_max_tokens) if raw_max_tokens else 65536
    except ValueError:
        max_tokens = 65536

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "max_tokens": max_tokens,
        "key_source": key_source,
    }


def describe(cfg: dict) -> str:
    """安全的配置摘要（日志/状态面用）——绝不含key原文。"""
    key_len = len(cfg.get("api_key") or "")
    return (
        f"base_url={cfg.get('base_url') or '<unset>'}, "
        f"model={cfg.get('model') or '<unset>'}, "
        f"key_source={cfg.get('key_source') or '<unset>'}, key_len={key_len}"
    )
