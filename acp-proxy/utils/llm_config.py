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

调用面（2026-09-27收口）：
- DNA进化/后台调用：resolve_llm_config()（含 MIMO_* 覆写层）
- ws_chat 用户侧agent代理 / utils.token_manager 模型限额 / utils.llm_gateway 默认模型：
  resolve_llm_config(include_mimo_overrides=False)
  —— MIMO_* 是进化专用覆写，用户/业务流量绝不跟随（运维把进化指向另一套LLM时，
     用户会话仍走系统LLM配置）。
- model_router 多提供商路由面：resolve_env_value()（逐提供商凭据/端点，多别名）
  + 极端兜底 resolve_llm_config(include_mimo_overrides=False)（系统默认LLM单一真源）。
  注意：model_router 的 MIMO_API_KEY = mimo 提供商凭据（与 OPENAI_API_KEY 同级的
  历史命名），不是"进化覆写"语义——两语义并存，见 model_router 模块docstring。
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


def read_merged_env_files(env_files: list[str] | None = None) -> dict:
    """合并 .env 文件配置（先者优先：opensoul/.env → acp-proxy/.env）。"""
    files = _default_env_files() if env_files is None else list(env_files)
    merged: dict = {}
    for p in files:
        for k, v in _read_env_file(p).items():
            merged.setdefault(k, v)
    return merged


def resolve_env_value(
    names: list[str],
    *,
    env_files: list[str] | None = None,
    default: str = "",
) -> tuple[str, str]:
    """按 names 顺序解析单个配置值——多别名/多提供商配置的通用单一真源件。

    语义（两个独立优先级轴，禁止混淆）：
    1. names 之间：先者优先（专用别名在前，通用兜底别名在后）；
    2. 同一 name 内部：.env 文件优先于进程环境（与 resolve_llm_config 同款
       "文件优先"合同——.env 是运维管理的配置真源，进程环境可能不含key）。

    返回 (value, source)；source ∈ {"file:NAME", "env:NAME", ""}——key从哪来可观测，
    key 本身绝不进日志。找不到返回 (default, "")。
    """
    merged = read_merged_env_files(env_files)
    for name in names:
        v = merged.get(name, "").strip()
        if v:
            return v, f"file:{name}"
        v = os.environ.get(name, "").strip()
        if v:
            return v, f"env:{name}"
    return default, ""


def parse_int(raw: str, default: int) -> int:
    """安全int解析：空值/坏值回退default不抛。

    历史教训：token_manager 曾在 import 期 int(os.environ.get("LLM_MAX_TOKENS"))
    ——坏值直接 ValueError 炸掉整个模块 import（agent 子系统全灭）。
    一切限额解析走本函数。
    """
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_llm_config(
    env_files: list[str] | None = None,
    *,
    include_mimo_overrides: bool = True,
) -> dict:
    """解析LLM配置，返回 {api_key, base_url, model, max_tokens, context_window,
    input_limit, key_source}。

    key_source ∈ {"mimo_env", "env", "file", ""}（观测面可据此判key从哪来；
    key本身绝不进日志/状态面，只报key_source与key长度）。

    include_mimo_overrides=False：跳过 MIMO_* 覆写层（ws_chat 用户侧代理用，
    见模块docstring"调用面"）。
    """
    files = _default_env_files() if env_files is None else list(env_files)
    file_cfg: dict = {}
    for p in files:
        for k, v in _read_env_file(p).items():
            file_cfg.setdefault(k, v)  # 先者优先（opensoul/.env 在前）

    def _override(name: str) -> str:
        return os.environ.get(name, "").strip() if include_mimo_overrides else ""

    mimo_key = _override("MIMO_API_KEY")
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
        _override("MIMO_BASE_URL")
        or file_cfg.get("LLM_BASE_URL", "").strip()
        or os.environ.get("LLM_BASE_URL", "").strip()
    )
    model = (
        _override("MIMO_MODEL")
        or file_cfg.get("LLM_MODEL", "").strip()
        or os.environ.get("LLM_MODEL", "").strip()
    )
    raw_max_tokens = (
        _override("MIMO_MAX_TOKENS")
        or file_cfg.get("LLM_MAX_TOKENS", "").strip()
        or os.environ.get("LLM_MAX_TOKENS", "").strip()
    )
    max_tokens = parse_int(raw_max_tokens, 65536)

    # 模型限额（token_manager 域，2026-09-27收口）：与上面同款"文件>env+坏值容错"。
    # LLM_CONTEXT_WINDOW 默认131072；LLM_INPUT_LIMIT=0=未声明单窗口模型
    # （kilocode model.limit.input 双限额语义，见 utils/token_manager 注释）。
    def _limit(name: str, default: int) -> int:
        raw = file_cfg.get(name, "").strip() or os.environ.get(name, "").strip()
        return parse_int(raw, default)

    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "max_tokens": max_tokens,
        "context_window": _limit("LLM_CONTEXT_WINDOW", 131072),
        "input_limit": _limit("LLM_INPUT_LIMIT", 0),
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
