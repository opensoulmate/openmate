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

模型降级有序链（2026-09-29，CowAgent chat_fallback移植，utils.model_fallback）：
单link重试耗尽/不可重试→推进到链下一个备胎link（每备胎只给一次尝试）、429有备胎
立即切换不干等、走完环绕第二遍、链耗尽报错列出所有试过的模型。sticky：一个
complete_with_protection调用（含续写轮）内停在应答的link上。链=opt-in
（LLM_FALLBACK_CHAIN未设=空链=行为与接线前逐字一致）。
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
from utils.stall_guard import send_with_stall_guard
from utils.model_fallback import (
    FallbackChain,
    chain_failure_message,
    load_fallback_chain,
)

logger = logging.getLogger(__name__)

__all__ = ["post_chat_completion", "complete_with_protection"]


# ── 传输故障可观测（路线图11.3）：分类入 tool_failures.db ──────────────────
def _fail_detail(strand_id: str, info) -> str:
    """失败描述（带 strand 归因；key 不进描述）。"""
    text = (
        f"{info.__class__.__name__}: {info}"
        if isinstance(info, BaseException)
        else str(info)
    )
    return f"[{strand_id}] {text}" if strand_id else text


def _record_failure_safe(path: str, source, action: str, attempt: int,
                         outcome: str, detail: str = "") -> bool:
    """传输故障记账 best-effort 化（阻断记账异常反噬 LLM 主链路，记账单一真源）。

    - record_llm_failure(path, source, action, attempt, outcome, detail) 原样转发
      （tool_name=llm:{path}、kind=classify_failure(source)、action/attempt/outcome
      逐字段保真）——周期#69 修复 evo 混入 WIP（345440e4）的参数错位：曾把 source
      当 path、action/outcome 压成单个 code，致 llm:dna 记账错标 llm:empty_output、
      九态分类全落 unknown、动作/尝试数丢失（test_llm_transport_accounting 6 例红）。
    - **不在此层做幂等去重**（周期#69 撤除 WIP 去重集）：本函数收不到请求身份
      （path 非请求 ID），按 path:action:attempt:outcome 去重会把**不同请求**的
      同形失败误并成一行=审计丢行，且模块级缓存跨测试/跨库误抑制（实证"no such
      table"）。请求级幂等归持有真实请求身份的调用方（如 dna 的 request_id=uuid）。
    - record_llm_failure(...) 放在 try/except 内，except 分支仅 logger.warning
      （best-effort），绝不 raise、绝不吞掉主流程异常。
    - 返回 True=已记账 / False=记账降级。

    与 transport 业务路径解耦：本函数自身不改变 post_chat_completion /
    complete_with_protection 的返回值、异常类型、重试与 park 策略。
    """
    try:
        from agent.llm_engine import record_llm_failure
        record_llm_failure(path, source, action, attempt, outcome, detail)
        return True
    except Exception as exc:
        logger.warning(
            'llm failure accounting degraded (best-effort): %s', exc, exc_info=True
        )
        return False


def _record_failure(path: str, source, action: str, attempt: int,
                    outcome: str, detail: str = "") -> None:
    """传输故障分类记账（11.3 补链）：进化引擎/llm_gateway 的 LLM 调用失败可审计。

    复用 agent.llm_engine.record_llm_failure 单一真源（kind=utils.llm_retry
    .classify_failure 九态分类，落 tool_failures.db）；lazy import 使 utils 层
    不持有对 agent 层的模块级依赖（记账器居 agent 层是 10.1 既有形态，不搬）。
    旁路纪律（同 10.1）：只记账，绝不冒泡、绝不阻塞 LLM 链路。
    """
    try:
        _record_failure_safe(
            path=str(path or ""),
            source=source,
            action=str(action or ""),
            attempt=int(attempt or 1),
            outcome=str(outcome or ""),
            detail=detail,
        )
    except Exception as exc:
        logger.warning(
            'llm failure accounting degraded (best-effort): %s', exc, exc_info=True
        )


class _LinkFailure(Exception):
    """内部信号：当前link（primary或备胎）本轮失败。

    cause = 无链时按原契约抛出的异常（LLMCallError / 原始httpx异常——原契约
    原样，接线前后零变化）；summary = 链耗尽报错的spent记账文案
    （"provider/model: 错误"，CowAgent语义3列出所有试过的模型）。
    """

    def __init__(self, cause: BaseException, summary: str):
        super().__init__(str(cause))
        self.cause = cause
        self.summary = summary


async def _post_single_link(
    client,
    *,
    base_url: str,
    headers: dict,
    model: str,
    messages: list,
    temperature: float,
    max_tokens: int,
    attempts: int,
    strand_id: str,
    switch_now: bool = False,
    path: str = "transport",
) -> dict:
    """单link传输环（post_chat_completion的内层）。

    attempts = 本link的HTTP尝试上限（primary=MAX_ATTEMPTS全预算；备胎link=1
    ——CowAgent link_retries=0："链存在的意义是换provider，不是等这个provider"）。
    switch_now = 429限流且链上有备胎→跳过等待立即失败换link（CowAgent
    "限流有备胎立即切换、没备胎干等"）。
    成功=HTTP 200的resp.json()；失败=raise _LinkFailure（cause按原契约）。
    """
    resp = None
    for _http_retry in range(attempts):
        try:
            # 无流停顿检测（12.4①，utils.stall_guard）：与 llm_engine.chat() 同源
            # 事故面（挂死 285s 零字节而 httpx read 超时未开火）——独立看门狗按
            # 字节间隙计时中止本次尝试，上层重试环照常重试
            resp = await send_with_stall_guard(
                client,
                "POST",
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
            if _http_retry < attempts - 1 and is_transient_error(e):
                _record_failure(
                    path, e, "llm_retry", _http_retry + 1, "retry_scheduled",
                    _fail_detail(strand_id, e),
                )
                _wait = backoff_ms(_http_retry + 1) / 1000.0
                logger.warning(
                    f"[{strand_id}] 传输错误{e.__class__.__name__}，"
                    f"{_wait:.1f}s后重试 ({_http_retry+1}/{attempts})"
                )
                await asyncio.sleep(_wait)
                continue
            _record_failure(
                path, e, "llm_give_up", _http_retry + 1, "failed",
                _fail_detail(strand_id, e),
            )
            raise _LinkFailure(e, f"{e.__class__.__name__}: {e}") from e
        if resp.status_code == 200:
            return resp.json()
        if not is_retryable_status(resp.status_code):
            break
        if switch_now and resp.status_code == 429:
            # CowAgent switch_now：限流+有备胎=备胎是不同provider自带配额，
            # 立即切换能现在就回答，不烧30-60s干等
            logger.warning(
                f"[{strand_id}] LLM HTTP 429 限流且链上有备胎：立即切换不等待"
            )
            break
        if _http_retry >= attempts - 1:
            break
        # 有头听头：Retry-After三格式（ms头/秒数/HTTP date），无头才指数退避
        _wait = backoff_ms(
            _http_retry + 1, getattr(resp, "headers", None)
        ) / 1000.0
        _record_failure(
            path, resp.status_code, "llm_retry", _http_retry + 1, "retry_scheduled",
            _fail_detail(strand_id, f"LLM HTTP {resp.status_code}"),
        )
        logger.warning(
            f"[{strand_id}] LLM HTTP {resp.status_code}, "
            f"retry {_http_retry+1}/{attempts} in {_wait:.1f}s"
        )
        await asyncio.sleep(_wait)

    status = resp.status_code if resp else "no-response"
    body = resp.text[:200] if resp else ""
    logger.error(f"[{strand_id}] LLM call failed: {status} {body}")
    _record_failure(
        path, resp.status_code if resp else "no-response", "llm_give_up",
        attempts, "failed",
        _fail_detail(strand_id, f"LLM HTTP {status}: {body}"),
    )
    raise _LinkFailure(
        LLMCallError(f"LLM HTTP {status}: {body}"), f"LLM HTTP {status}: {body}"
    )


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
    chain: FallbackChain | None = None,
    path: str = "transport",
) -> dict:
    """单发chat/completions（带重试/park/错误契约/降级链）——传输层单一真源。

    返回：HTTP 200的resp.json()解析结果（choices等由调用方按需取）。
    失败：raise LLMCallError（"LLM HTTP {status}: {body}"）——重试耗尽/不可重试
    状态/传输错误最终失败时均可见失败，禁止静默返回空/半截。

    chain（CowAgent模型降级有序链，utils.model_fallback）：
    - 无链（默认）：行为与接线前逐字一致（primary全重试预算，原契约原样raise）
    - 有链：起始link失败→推进备胎link（call内切换进来的link只给1发——CowAgent
      _on_fallback link_retries=0）；429+有备胎立即切换；走完环绕第二遍
      （MAX_PASSES=2）；链耗尽raise LLMCallError列出**所有**试过的模型。
      传输错误的原始异常只在无链/单link时原样抛（原契约），链路径统一
      LLMCallError携带全部spent。
    - sticky（CowAgent use_fallback）：链已切换过（chain.current）则本call起始
      就在应答的link上不弹回primary；起始link=全重试预算（CowAgent外部调用
      _on_fallback=False语义）。
    """
    spent: list[str] = []
    if chain is not None and chain.current is not None:
        # CowAgent sticky："the run stays on whichever link answered"
        cur = chain.current
        link_base, link_headers, link_model = cur.base_url, cur.headers(), cur.model
        link_label = cur.label()
    else:
        link_base, link_headers, link_model = base_url, headers, model
        link_label = model or "primary"
    is_start_link = True
    while True:
        try:
            return await _post_single_link(
                client,
                base_url=link_base,
                headers=link_headers,
                model=link_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                # CowAgent：起始link=全重试预算；call内切换进来的link只给1发
                attempts=MAX_ATTEMPTS if is_start_link else 1,
                strand_id=strand_id,
                switch_now=(
                    is_start_link and chain is not None and chain.available()
                ),
                path=path,
            )
        except _LinkFailure as f:
            spent.append(f"{link_label}: {f.summary}")
            if chain is None or not chain.available():
                if len(spent) > 1:
                    # CowAgent语义3：链耗尽报错列出所有试过的模型——
                    # 只报最后一个="那个模型坏了"，实际每个都试过都失败
                    logger.error(
                        f"[{strand_id}] 降级链耗尽，{len(spent)}个模型全部失败"
                    )
                    raise LLMCallError(chain_failure_message(spent)) from f.cause
                raise f.cause  # 无链：原契约原样（LLMCallError/原始httpx异常）
            link = chain.advance()
            link_base = link.base_url
            link_headers = link.headers()
            link_model = link.model
            link_label = link.label()
            is_start_link = False


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
    chain: FallbackChain | None = None,
    path: str = "transport",
) -> str:
    """带防护的完整LLM调用（续写/omission/空输出契约/输出上限/降级链）。

    rounds = 总请求轮数（含首发与续写轮）；output_cap = 输出字符上限（None=不限）。
    chain = 模型降级有序链（utils.model_fallback）；None=自动从 LLM_FALLBACK_CHAIN
    构建（opt-in，未配置=空链=行为不变）。**sticky**（CowAgent use_fallback）：
    链对象跨续写轮保持——切到备胎就停在应答的备胎上，续写轮不弹回primary。
    返回：完整输出文本。
    失败：raise LLMCallError（partial_content=续写中断时的半截输出，不丢，调用方可取；
    HTTP 200空输出同判失败——"空串=失败"第三态废除，契约见utils.llm_errors）。
    """
    if chain is None:
        # CowAgent sticky语义：一个逻辑调用=一个run，链状态在run内保持，
        # 每个新调用新建链=下个run从primary重新走链（reset_fallback等价）
        chain = load_fallback_chain(primary_model=model)
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
                    chain=chain,
                    path=path,
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
        if not isinstance(e, httpx.TransportError):
            # 传输错误已在 _post_single_link 放弃点记账（防双行重复）；
            # 此处记防护层自身异常（JSON解析/防护循环等）
            _record_failure(
                path, e, "llm_give_up", 1, "failed", _fail_detail(strand_id, e)
            )
        err = LLMCallError(f"LLM调用异常: {e}")
        err.partial_content = full_content
        raise err from e

    if not full_content.strip():
        # 200但空输出：对反思/规划/审查一律无意义，同判失败（不许伪装"无改进"）
        _record_failure(
            path, "empty_output", "llm_give_up", rounds, "failed",
            _fail_detail(strand_id, "LLM返回空内容（HTTP 200但无输出）"),
        )
        raise LLMCallError("LLM返回空内容（HTTP 200但无输出）")

    # 输出字符数限制（调用方指定上限时）
    if output_cap and len(full_content) > output_cap:
        logger.warning(
            f"[{strand_id}] Output truncated to {output_cap} chars "
            f"(was {len(full_content)})"
        )
        full_content = full_content[:output_cap]

    return full_content
