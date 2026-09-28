"""
LLM Gateway — 统一调用入口 + 统一防护

使用方式：
    from utils.llm_gateway import llm_call
    result = await llm_call(messages, model="default")

架构：
    业务代码 ──→ llm_call() ──→ 大模型A
                  ├── 防护逻辑     ├── 大模型B
                  ├── 路由逻辑     ├── 大模型C
                  └── 重试逻辑     └── ...

防护机制（全部封装在llm_call内部）：
1. finish_reason检测+自动续写（Aider方案）
2. omission占位符检测+重新生成（Gemini CLI方案）
3. 输出字符数限制（Cline方案）
4. 多模型路由+降级
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from utils.llm_config import resolve_llm_config
# 防护策略单一真源=utils.llm_safety（2026-09-28收口）：旧本文件自有9条pattern副本
# +内联续写/重发文案，与llm_safety/dna_evolution三处镜像漂移（dna 7条缺2条），
# 现统一引用llm_safety的pattern与消息构建器，禁止再复制。
from utils.llm_safety import (
    OMISSION_PATTERNS as _OMISSION_PATTERNS,
    build_continuation_messages,
    check_omission as _check_omission,
)

# LLM传输错误契约+重试策略+离线park 单一真源（2026-09-29收口）：本模块曾对
# 失败静默返回""/半截（return full_content），上游无法区分"LLM挂了"与"LLM说
# 没有可改进项"。现统一（契约见utils/llm_errors.py）：失败=raise LLMCallError
# （partial_content=半截输出）、HTTP 200空输出同判失败——"空串=失败"第三态废除；
# 重试策略/离线park与agent.llm_engine、dna_evolution同源import，禁止再复制。
from utils import network_state
from utils.llm_errors import LLMCallError
from utils.llm_retry import (
    MAX_ATTEMPTS,
    backoff_ms,
    is_retryable_status,
    is_transient_error,
)

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────

MAX_CONTINUATION = 3  # 最大续写次数
MAX_OUTPUT_TOKENS = 4096  # 单次输出token上限
MAX_TOOL_OUTPUT_CHARS = 8000  # 工具输出字符上限


# ── 模型配置 ──────────────────────────────────────────

@dataclass
class ModelConfig:
    """模型配置"""
    name: str
    base_url: str
    api_key: str
    model: str
    max_tokens: int = MAX_OUTPUT_TOKENS
    temperature: float = 0.7
    timeout: float = 120.0
    priority: int = 0  # 优先级，越小越优先
    enabled: bool = True


# 默认模型配置——统一走 utils.llm_config 单一真源（2026-09-27收口，00:10遗留#4第一批）。
# 调用面：业务/用户侧流量 → include_mimo_overrides=False（MIMO_* 进化覆写不跟随）。
# 旧实现自扫 os.environ（key 只写在 .env 时拿到空key→可选鉴权头→静默401）且硬编码
# provider URL/模型名兜底（https://api.mimo.ai/v1 + "mimo-v2"）——两条均违反
# llm_config 调用方纪律，已移除：base_url/model 缺失=配置错误，由 llm_call
# fail-closed 显式报错拒绝调用，不许瞎猜默认值、不许发无鉴权请求。
def _default_model_config() -> ModelConfig:
    cfg = resolve_llm_config(include_mimo_overrides=False)
    return ModelConfig(
        name="mimo",
        base_url=cfg["base_url"],
        api_key=cfg["api_key"],
        model=cfg["model"],
        priority=0,
    )


DEFAULT_MODELS = [_default_model_config()]

# 模型注册表
_model_registry: dict[str, ModelConfig] = {}


def register_model(config: ModelConfig):
    """注册模型配置"""
    _model_registry[config.name] = config


def get_model(name: str = "default") -> ModelConfig | None:
    """获取模型配置"""
    if name == "default":
        # 返回优先级最高的可用模型
        available = [m for m in _model_registry.values() if m.enabled]
        if available:
            return min(available, key=lambda m: m.priority)
        return None
    return _model_registry.get(name)


def refresh_default_model() -> ModelConfig:
    """（重新）从单一真源解析默认模型并注册（配置变更/测试可显式重载）。"""
    cfg_m = _default_model_config()
    register_model(cfg_m)
    return cfg_m


# 初始化默认模型
for config in DEFAULT_MODELS:
    register_model(config)


# ── Omission占位符检测（Gemini CLI方案）────────────────
# pattern与check_omission已收口 utils.llm_safety 单一真源（见顶部import），
# 此处保留 _check_omission 名称为既有调用面/测试兼容。


# ── 统一调用入口 ──────────────────────────────────────

async def llm_call(
    messages: list[dict],
    model: str = "default",
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_continuation: int = MAX_CONTINUATION,
    strand_id: str = "",
) -> str:
    """统一LLM调用入口 — 一处防护，全局调用
    
    自动完成：
    1. finish_reason检测+自动续写（Aider方案）
    2. omission占位符检测+重新生成（Gemini CLI方案）
    3. 输出字符数限制（Cline方案）
    4. 多模型路由+降级
    5. HTTP重试（utils.llm_retry单一真源）+断网park（utils.network_state）
    
    返回：LLM输出文本（已保证安全）。
    失败：raise LLMCallError（partial_content=半截输出；HTTP 200空输出同判
    失败）——"静默返空串"第三态废除（契约见utils/llm_errors.py）。
    """
    # 获取模型配置
    config = get_model(model)
    if not config:
        logger.error(f"[{strand_id}] No available model: {model}")
        # 失败必须可见（mem0 §1.1）：raise而非静默返空串——"空串=失败"第三态废除
        raise LLMCallError(f"No available model: {model}")
    if not config.api_key or not config.base_url:
        # fail-closed：缺key/base_url 一律拒绝调用（llm_config 调用方纪律：
        # "key缺失要显式告警（401早知道），不得静默发出无鉴权请求"）。
        # 旧实现把鉴权头做成可选（if config.api_key:）→ 空key照发请求→静默401。
        logger.error(
            f"[{strand_id}] LLM配置缺失，拒绝调用: "
            f"base_url={config.base_url or '<unset>'}, "
            f"model={config.model or '<unset>'}, key_len={len(config.api_key or '')}"
        )
        raise LLMCallError(
            f"[{strand_id}] LLM配置缺失，拒绝调用: "
            f"base_url={config.base_url or '<unset>'}, "
            f"model={config.model or '<unset>'}, key_len={len(config.api_key or '')}"
        )
    
    # 参数覆盖
    temp = temperature if temperature is not None else config.temperature
    tokens = max_tokens if max_tokens is not None else config.max_tokens
    
    # 调用（带防护）
    result = await _call_with_protection(
        config=config,
        messages=messages,
        temperature=temp,
        max_tokens=tokens,
        max_continuation=max_continuation,
        strand_id=strand_id,
    )
    
    return result


async def _call_with_protection(
    config: ModelConfig,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    max_continuation: int,
    strand_id: str,
) -> str:
    """带防护的LLM调用"""
    full_content = ""
    current_messages = list(messages)
    
    headers = {"Content-Type": "application/json"}
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(config.timeout)) as client:
            for attempt in range(max_continuation + 1):
                try:
                    # HTTP重试（策略单一真源utils.llm_retry，kilocode retry.ts）：
                    # 429/408/5xx听Retry-After三格式可重试、瞬时传输错误重试、
                    # 400/401/402等请求/凭证级fail-fast——接线前本传输零HTTP重试
                    resp = None
                    for _http_retry in range(MAX_ATTEMPTS):
                        try:
                            resp = await client.post(
                                f"{config.base_url}/chat/completions",
                                headers=headers,
                                json={
                                    "model": config.model,
                                    "messages": current_messages,
                                    "temperature": temperature,
                                    "max_tokens": max_tokens,
                                },
                            )
                        except httpx.TransportError as e:
                            # 网络离线三态（utils.network_state，kilocode SessionNetwork）：
                            # 全局断连→park等恢复自动续跑；非离线/恢复后走下方重试记账
                            await network_state.maybe_park(e)
                            if _http_retry < MAX_ATTEMPTS - 1 and is_transient_error(e):
                                _wait = backoff_ms(_http_retry + 1) / 1000.0
                                logger.warning(
                                    f"[{strand_id}] 传输错误{e.__class__.__name__}，"
                                    f"{_wait:.1f}s后重试 ({_http_retry+1}/{MAX_ATTEMPTS})"
                                )
                                await asyncio.sleep(_wait)
                                continue
                            raise
                        if resp.status_code == 200 or not is_retryable_status(resp.status_code):
                            break
                        if _http_retry >= MAX_ATTEMPTS - 1:
                            break
                        # 有头听头：Retry-After三格式（ms头/秒数/HTTP date），无头才指数退避
                        _wait = backoff_ms(
                            _http_retry + 1, getattr(resp, "headers", None)
                        ) / 1000.0
                        logger.warning(
                            f"[{strand_id}] LLM API {resp.status_code}，{_wait:.1f}s后重试 "
                            f"({_http_retry+1}/{MAX_ATTEMPTS})"
                        )
                        await asyncio.sleep(_wait)

                    if resp is None or resp.status_code != 200:
                        status = resp.status_code if resp else "no-response"
                        body = resp.text[:200] if resp else ""
                        logger.error(
                            f"[{strand_id}] LLM call failed: {status} {body}"
                        )
                        # 失败必须可见（mem0 §1.1）：raise而非静默返半截——
                        # 已生成部分挂partial_content不丢，调用方可取
                        err = LLMCallError(f"LLM HTTP {status}: {body}")
                        err.partial_content = full_content
                        raise err
                    
                    data = resp.json()
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")
                    finish_reason = choice.get("finish_reason", "stop")
                    
                    full_content += content
                    
                    # 检查1: omission占位符
                    omission = _check_omission(full_content)
                    if omission:
                        logger.warning(f"[{strand_id}] {omission}")
                        # 重发文案单一真源=llm_safety.build_continuation_messages
                        current_messages = build_continuation_messages(
                            current_messages, full_content, error_hint=omission
                        )
                        full_content = ""  # 重置，让LLM重新完整输出
                        continue
                    
                    # 检查2: finish_reason
                    if finish_reason == "length":
                        logger.warning(
                            f"[{strand_id}] LLM output truncated (finish_reason=length), "
                            f"attempt {attempt+1}/{max_continuation}"
                        )
                        # 续写文案单一真源=llm_safety.build_continuation_messages
                        current_messages = build_continuation_messages(
                            current_messages, full_content
                        )
                        continue
                    
                    # 正常结束
                    break
                    
                except LLMCallError:
                    raise
                except Exception as e:
                    # 失败必须可见：旧实现log后break静默返半截/空串——改为带
                    # partial_content的LLMCallError（与dna_evolution同契约）
                    logger.error(f"[{strand_id}] LLM call exception: {e}")
                    err = LLMCallError(f"LLM调用异常: {e}")
                    err.partial_content = full_content
                    raise err from e
    
    except LLMCallError:
        raise
    except Exception as e:
        logger.error(f"[{strand_id}] LLM client exception: {e}")
        err = LLMCallError(f"LLM调用异常: {e}")
        err.partial_content = full_content
        raise err from e
    
    if not full_content.strip():
        # 200但空输出：与dna_evolution同判失败（"空串=失败"第三态废除，契约统一）
        raise LLMCallError("LLM返回空内容（HTTP 200但无输出）")
    
    # 输出字符数限制
    if len(full_content) > MAX_TOOL_OUTPUT_CHARS:
        logger.warning(
            f"[{strand_id}] Output truncated to {MAX_TOOL_OUTPUT_CHARS} chars "
            f"(was {len(full_content)})"
        )
        full_content = full_content[:MAX_TOOL_OUTPUT_CHARS]
    
    return full_content


# ── 便捷函数 ──────────────────────────────────────────

async def llm_call_simple(prompt: str, model: str = "default", strand_id: str = "") -> str:
    """简单调用：单条消息"""
    return await llm_call(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        strand_id=strand_id,
    )


async def llm_call_with_system(
    system: str, user: str, model: str = "default", strand_id: str = ""
) -> str:
    """带系统提示的调用"""
    return await llm_call(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        model=model,
        strand_id=strand_id,
    )
