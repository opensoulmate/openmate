"""
Token管理器 — 启动时探测模型能力，缓存结果，不重复计算

配置收口（2026-09-27轮，00:10遗留#4第一批）：模型限额一律走
utils.llm_config.resolve_llm_config 单一真源（文件>进程环境 + 坏值容错不炸），
禁止在本模块自扫 os.environ 读 LLM_*。旧实现在 import 期 int() 解析坏的
LLM_MAX_TOKENS 直接 ValueError → import utils.token_manager 失败 → agent
子系统（llm_engine/soulmate_agent/context_budget）全灭；且只读进程环境，
.env 文件里的限额被无视（与单一真源"文件>env"语义漂移）。

调用面：本模块服务**用户侧 agent 运行时**（soulmate_agent/context_budget/
llm_engine 真实消息路径）→ include_mimo_overrides=False（MIMO_* 是进化strand
专用覆写，用户流量绝不跟随）。未来进化侧调用点必须显式传 include_mimo_overrides=True。
"""
import os
import json
import logging
import httpx

from utils.llm_config import parse_int, resolve_llm_config

logger = logging.getLogger(__name__)

# 限额默认值（未配置时的回退，与 resolve_llm_config 同源）
DEFAULT_MAX_OUTPUT_TOKENS = 65536
DEFAULT_CONTEXT_WINDOW = 131072
DEFAULT_INPUT_LIMIT = 0
DEFAULT_TOOL_RESULT_MAX_CHARS = 8000

# 缓存的模型能力（启动时探测一次）——由 reload_model_cache() 从单一真源填充
_model_cache: dict = {}

# 工具返回内容截断上限（坏值容错不炸，旧 int() 崩溃面同款教训）
TOOL_RESULT_MAX_CHARS = parse_int(
    os.environ.get("TOOL_RESULT_MAX_CHARS", ""), DEFAULT_TOOL_RESULT_MAX_CHARS
)


def reload_model_cache(*, include_mimo_overrides: bool = False, env_files=None) -> dict:
    """（重新）从单一真源解析模型限额并回填缓存。

    import 期自动调用一次；配置变更/测试可显式重载。reload 把 probed 重置为
    False（配置变了旧探测结果作废）。
    """
    cfg = resolve_llm_config(
        env_files=env_files, include_mimo_overrides=include_mimo_overrides
    )
    _model_cache.update({
        "max_output_tokens": cfg["max_tokens"],
        "context_window": cfg["context_window"],
        # 模型独立输入限额（kilocode model.limit.input，supplement3 #17双限额）：
        # 0=未声明（单窗口模型）；双限额模型（如"1M输入/32k输出"分离限额）在此声明，
        # 上下文预算即走"输入限额优先"分支（agent.context_budget.usable_input）。
        "input_limit": cfg["input_limit"],
        "probed": False,
    })
    return _model_cache


reload_model_cache()


def get_max_output_tokens() -> int:
    """获取模型最大输出token数（缓存值）"""
    return _model_cache["max_output_tokens"]


def get_context_window() -> int:
    """获取模型上下文窗口（缓存值）"""
    return _model_cache["context_window"]


def get_input_limit() -> int:
    """获取模型独立输入限额（kilocode model.limit.input语义，0=未声明单窗口模型）"""
    return _model_cache["input_limit"]


def probe_model_capabilities(base_url: str, api_key: str, model: str,
                             max_tokens: int | None = None) -> dict:
    """
    启动时探测模型能力，只调用一次。
    发一个小请求，用max_tokens=1触发，从usage中获取信息。
    同时尝试不同的max_tokens值探测上限。
    """
    if _model_cache["probed"]:
        return _model_cache

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        # 测试1: 基本连通性 + 获取completion_tokens
        resp = httpx.post(f"{base_url}/chat/completions", headers=headers, json={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
            "stream": False,
        }, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            usage = data.get("usage", {})
            logger.info(f"[TokenProbe] 连通成功, usage={usage}")
        else:
            logger.warning(f"[TokenProbe] 连通失败: {resp.status_code}")
            return _model_cache
    except Exception as e:
        logger.warning(f"[TokenProbe] 探测失败: {e}")
        return _model_cache

    # 测试2: 探测max_tokens上限（二分法）
    # 限额以调用方显式传入/单一真源缓存为准（旧实现此处又自扫 env 且 int() 坏值即炸）
    configured_max = max_tokens if max_tokens is not None else _model_cache["max_output_tokens"]
    _model_cache["max_output_tokens"] = configured_max
    _model_cache["probed"] = True
    logger.info(f"[TokenProbe] 缓存: max_output_tokens={configured_max}, context_window={_model_cache['context_window']}")
    return _model_cache


def truncate_tool_result(result: str, max_chars: int = 0) -> str:
    """截断工具返回内容，保留头尾"""
    if not max_chars:
        max_chars = TOOL_RESULT_MAX_CHARS
    if not result or len(result) <= max_chars:
        return result
    head = result[:max_chars * 2 // 3]
    tail = result[-max_chars // 3:]
    return f"{head}\n\n... [内容过长，已截断 {len(result)}→{max_chars} 字符] ...\n\n{tail}"


def estimate_tokens(text: str) -> int:
    """粗略估算token数：中文1.5/字，其他0.4/字符"""
    if not text:
        return 0
    chinese = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return int(chinese * 1.5 + (len(text) - chinese) * 0.4)
