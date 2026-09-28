# -*- coding: utf-8 -*-
"""LLM HTTP传输核心 — 两条并行非流式传输的公共收口（2026-09-29合并轮）。

背景（多轮dev-report遗留"传输合并"）：utils.llm_gateway._call_with_protection与
dna_evolution._call_llm曾各持一条**逐字重复**的httpx传输（~70行重试环×2）+ 两套
续写/omission防护循环（dna还内联了一份与llm_safety漂移的续写文案副本）。错误契约
（utils.llm_errors）与重试/park策略（utils.llm_retry/utils.network_state）虽已同源，
传输本体仍是两条并行实现——修一处漏一处的漂移面。

本模块收口两层：
1. `post_chat_completion`（单发层）：chat/completions单次请求 +
   utils.llm_retry重试环（429/408/5xx听Retry-After三格式、瞬时传输错误重试、
   400/401/402等请求/凭证级fail-fast）+ utils.network_state离线park +
   utils.llm_errors.LLMCallError契约（失败=raise，绝不静默）。
2. `complete_with_protection`（防护层）：finish_reason=length续写（Aider方案）
   + omission占位符重发（Gemini CLI方案）+ 200空输出同判失败 + 输出上限，
   续写文案单一真源=utils.llm_safety.build_continuation_messages（旧dna内联
   英文文案副本已并入，禁止再复制）。

消费方（禁止再各自手搓httpx POST环）：
- utils.llm_gateway._call_with_protection（业务/调用面）
- dna_evolution.DNAStrand._call_llm（进化strand真实每小时流量）
会话面（agent.llm_engine 流式传输）不在合并范围（SSE流式+usage回填语义不同，
其重试环仍按 tests/test_llm_retry_policy.py 接线钉留在chat()方法体内）。
"""

import asyncio
import logging

import httpx

from utils import network_state
from utils.llm_errors import LLMCallError
from utils.llm_retry import (
    MAX_ATTEMPTS,
    backoff_ms,
    is_retryable_status,
    is_transient_error,
)
from utils.llm_safety import build_continuation_messages, check_omission

logger = logging.getLogger(__name__)

__all__ = ["post_chat_completion", "complete_with_protection"]


async def post_chat_completion(
    client,
    *,
    base_url: str,
    headers: dict,
    model: str,
    messages: list,
    temperature: float,
    max_tokens: int,
    strand_id: str = "",
) -> dict:
    """单发chat/completions（带重试/park/错误契约）——传输层单一真源。

    返回：HTTP 200的resp.json()解析结果（choices等由调用方按需取）。
    失败：raise LLMCallError（"LLM HTTP {status}: {body}"）——重试耗尽/不可重试
    状态/传输错误最终失败时均可见失败，禁止静默返回空/半截。
    """
    resp = None
    for _http_retry in range(MAX_ATTEMPTS):
        try:
            resp = await client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": messages,
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
            f"[{strand_id}] LLM HTTP {resp.status_code}, "
            f"retry {_http_retry+1}/{MAX_ATTEMPTS} in {_wait:.1f}s"
        )
        await asyncio.sleep(_wait)

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp else "no-response"
        body = resp.text[:200] if resp else ""
        logger.error(f"[{strand_id}] LLM call failed: {status} {body}")
        raise LLMCallError(f"LLM HTTP {status}: {body}")
    return resp.json()


async def complete_with_protection(
    client,
    *,
    base_url: str,
    headers: dict,
    model: str,
    messages: list,
    temperature: float,
    max_tokens: int,
    rounds: int,
    strand_id: str = "",
    output_cap: int | None = None,
) -> str:
    """带防护的完整LLM调用（续写/omission/空输出契约/输出上限）。

    rounds = 总请求轮数（含首发与续写轮）；output_cap = 输出字符上限（None=不限）。
    返回：完整输出文本。
    失败：raise LLMCallError（partial_content=续写中断时的半截输出，不丢，调用方可取；
    HTTP 200空输出同判失败——"空串=失败"第三态废除，契约见utils.llm_errors）。
    """
    full_content = ""
    current_messages = list(messages)
    try:
        for _round in range(rounds):
            try:
                data = await post_chat_completion(
                    client,
                    base_url=base_url,
                    headers=headers,
                    model=model,
                    messages=current_messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    strand_id=strand_id,
                )
            except LLMCallError as e:
                # 半截输出不丢（partial_content），但失败仍判失败（统一契约）
                if not getattr(e, "partial_content", ""):
                    e.partial_content = full_content
                raise

            choice = data.get("choices", [{}])[0]
            content = choice.get("message", {}).get("content", "")
            finish_reason = choice.get("finish_reason", "stop")
            full_content += content

            # 检查1: omission占位符（Gemini CLI方案）——重发文案单一真源llm_safety
            omission = check_omission(full_content)
            if omission:
                logger.warning(f"[{strand_id}] {omission}")
                current_messages = build_continuation_messages(
                    current_messages, full_content, error_hint=omission
                )
                full_content = ""  # 重置，让LLM重新完整输出
                continue

            # 检查2: finish_reason=length（Aider续写方案）
            if finish_reason == "length":
                logger.warning(
                    f"[{strand_id}] LLM output truncated (finish_reason=length), "
                    f"attempt {_round+1}/{rounds}"
                )
                current_messages = build_continuation_messages(
                    current_messages, full_content
                )
                continue

            # 正常结束
            break
    except LLMCallError:
        raise
    except Exception as e:
        logger.error(f"[{strand_id}] LLM call exception: {e}")
        err = LLMCallError(f"LLM调用异常: {e}")
        err.partial_content = full_content
        raise err from e

    if not full_content.strip():
        # 200但空输出：对反思/规划/审查一律无意义，同判失败（不许伪装"无改进"）
        raise LLMCallError("LLM返回空内容（HTTP 200但无输出）")

    # 输出字符数限制（调用方指定上限时）
    if output_cap and len(full_content) > output_cap:
        logger.warning(
            f"[{strand_id}] Output truncated to {output_cap} chars "
            f"(was {len(full_content)})"
        )
        full_content = full_content[:output_cap]

    return full_content
