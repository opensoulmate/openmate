# -*- coding: utf-8 -*-
"""LLM重试策略单一真源 — kilocode retry.ts移植（2026-09-28 P0）

调研来源：SUMMARY.md §五 cortex缺口「LLM重试策略（Retry-After三格式+5xx重试+离线三态）
| kilocode retry.ts | P0」；kilocode-source-supplement6 §2：
- **Retry-After三格式**：retry-after-ms毫秒头→retry-after秒数→HTTP date格式
  （Date.parse−now），全部cap到i32上限；**无头时才fallback指数退避（2s×2^n）
  且封顶30s**——有头听头，比盲目退避对限流API友好得多
- **5xx无条件重试**：即使SDK没标isRetryable，status>=500一律重试
  （注释即理由："transient server failures"）
- **不可重试的精确排除**：ContextOverflowError/凭证级错误重试没用，fail-fast
- **limit参数防无限重试**（kilocode_change加的，上游opencode没有）

接线前llm_engine三条传输路径重试各自为政且有洞（2026-09-28盘点）：
- chat()（非流式真实路径，judge/计划/摘要/标题全走它）：**零HTTP重试**——
  瞬时429/5xx直接"[LLM错误: …]"整体失败
- chat_stream()（真实回复主路径）：**零重试**——同上
- chat_stream_with_tools()：固定2^n退避（不听Retry-After）、可重试集
  (400, 429, 500, 502, 503)把400当可重试（请求级错误重试无益）却漏501/504等其余5xx

本模块是唯一权威：状态分类/退避时长/瞬时错误判定/尝试上限全部在此，
llm_engine三路消费，不再各自复制策略（延续utils.llm_safety输出防护单一真源范式）。

未移植部分（如实记录）：kilocode离线handler三态（retry/blocked/aborted，探测+轮询
+事件~120行）是turn层语义，归入网络恢复专项，本模块只管"调用失败怎么重试"。
"""
import math
import os
import time
from email.utils import parsedate_to_datetime

import httpx

# ── 常量（kilocode retry.ts逐值对应；10.1韧性治理处显式标注偏差） ──────
RETRY_INITIAL_DELAY_MS = 2000  # RETRY_INITIAL_DELAY
RETRY_BACKOFF_FACTOR = 2  # RETRY_BACKOFF_FACTOR
# 10.1韧性治理（2026-10-11，周期#54上游降级实证）：退避上限拉长30s→60s。
# 同时MAX_ATTEMPTS 3→5（kilocode默认3，此处显式偏差）：持续降级期3发2s+4s
# 共6s即耗尽→"[LLM错误 429]"掐断真实回合（周期#54实录）；5发无头总退避
# 2+4+8+16=30s，仍有限防无限重试（kilocode limit语义保留）。
RETRY_MAX_DELAY_NO_HEADERS_MS = 60_000  # RETRY_MAX_DELAY_NO_HEADERS（无头退避封顶，10.1拉长）
RETRY_MAX_DELAY_MS = 2_147_483_647  # RETRY_MAX_DELAY（i32上限，parse层cap；实际等待另见max_wait_ms）
MAX_ATTEMPTS = 5  # 10.1偏差：kilocode默认3→5（持续降级耐性，仍有限）
# 10.1新增：单次重试等待硬上限（"禁静默空转"铁律）。kilocode对有头等待只cap
# i32（≈24.8天）——上游一个`Retry-After: 86400`就让回合静默挂一天，恰是
# 10.1验收禁止的形态。有头听头语义保留（≤上限的头值原样生效），超上限截到
# 上限后照常重试（有界耐性）；SOULMATE_LLM_RETRY_MAX_WAIT_S可配（测试注入故障用）。
RETRY_MAX_WAIT_MS = 60_000

# 九态分类码全集（12.6：预分类 code 串透传判据，改分类面先改这里）
_LLM_KIND_CODES = frozenset({
    "rate_limit", "timeout_status", "server_error", "client_error",
    "protocol_error", "read_timeout", "connect_error", "network_error",
    "transient_message", "unknown",
})

# kilocode TRANSIENT_MESSAGES：纯文案匹配兜底（跨平台错误串，httpx类型判定之外的补充）
TRANSIENT_MESSAGES = (
    "load failed",
    "network connection was lost",
    "network request failed",
    "failed to fetch",
    "econnreset",
    "econnrefused",
    "etimedout",
    "socket hang up",
)


def _cap_ms(ms: float) -> float:
    """cap到i32上限（kilocode cap()同款）"""
    return min(float(ms), RETRY_MAX_DELAY_MS)


def parse_retry_after_ms(headers) -> float | None:
    """Retry-After三格式解析（kilocode delay()头解析部分逐序对应）：
    1. retry-after-ms毫秒头
    2. retry-after秒数（float可解析）
    3. retry-after HTTP date格式（parsedate_to_datetime − now，只取未来时刻）

    headers：httpx.Headers或任意有items()的映射（大小写不敏感）。
    无可解析值返回None（调用方走指数退避）。
    """
    if headers is None:
        return None
    try:
        items = headers.items()
    except AttributeError:
        return None
    norm = {str(k).lower(): v for k, v in items}

    # 格式1：retry-after-ms毫秒头
    raw_ms = norm.get("retry-after-ms")
    if raw_ms is not None:
        try:
            val = float(str(raw_ms).strip())
            if math.isnan(val) or val < 0:
                raise ValueError
            return _cap_ms(val)
        except (TypeError, ValueError):
            pass

    raw = norm.get("retry-after")
    if raw is not None:
        s = str(raw).strip()
        # 格式2：秒数
        try:
            sec = float(s)
            if math.isnan(sec) or sec < 0:
                raise ValueError
            return _cap_ms(math.ceil(sec * 1000))
        except (TypeError, ValueError):
            pass
        # 格式3：HTTP date（Date.parse − now，只认未来时刻——kilocode同款）
        try:
            delta_ms = (parsedate_to_datetime(s).timestamp() - time.time()) * 1000
            if delta_ms > 0:
                return _cap_ms(math.ceil(delta_ms))
        except Exception:
            pass
    return None


def max_wait_ms() -> float:
    """单次重试等待硬上限毫秒（10.1"禁静默空转"）。

    SOULMATE_LLM_RETRY_MAX_WAIT_S>0 时覆盖（注入故障测试用小值实证有界）；
    缺省 RETRY_MAX_WAIT_MS=60s。所有实际 sleep 的等待都不得超过此值。
    """
    try:
        val = float(os.environ.get("SOULMATE_LLM_RETRY_MAX_WAIT_S", "") or 0)
        if val > 0:
            return val * 1000.0
    except (TypeError, ValueError):
        pass
    return RETRY_MAX_WAIT_MS


def backoff_ms(attempt: int, headers=None) -> float:
    """第attempt次失败后的等待毫秒（attempt从1开始，kilocode delay(attempt, error)同款）。

    有头听头：头里有可解析Retry-After→用头里的值；有头但无可解析值→指数退避；
    **无头**→指数退避2s×2^n且封顶RETRY_MAX_DELAY_NO_HEADERS_MS。
    10.1收口：无论哪路，实际等待一律 ≤ max_wait_ms()（有头超上限截到上限后照常
    重试；kilocode原语义对有头只cap i32≈24.8天=静默空转隐患，此处显式偏差）。
    """
    exp = RETRY_INITIAL_DELAY_MS * (RETRY_BACKOFF_FACTOR ** max(int(attempt) - 1, 0))
    cap = max_wait_ms()
    if headers is not None:
        parsed = parse_retry_after_ms(headers)
        if parsed is not None:
            return min(_cap_ms(parsed), cap)
        return min(_cap_ms(exp), cap)
    return min(_cap_ms(min(exp, RETRY_MAX_DELAY_NO_HEADERS_MS)), cap)


def is_retryable_status(status: int) -> bool:
    """HTTP状态可重试判定（kilocode retryable()）：
    429/408限流超时可重试；**5xx无条件可重试**（"transient server failures"，
    SDK没标也重试）；其余（400/401/402/403/404等请求/凭证级）重试无益，fail-fast。
    """
    status = int(status)
    if status in (408, 429):
        return True
    return status >= 500


def is_transient_error(exc: BaseException) -> bool:
    """瞬时传输错误判定（网络层，重试有望自愈）：
    httpx.TransportError家族（ConnectError/ReadTimeout/RemoteProtocolError等）
    无条件瞬时 + kilocode TRANSIENT_MESSAGES文案匹配兜底。
    """
    if isinstance(exc, httpx.TransportError):
        return True
    msg = str(exc).lower()
    return any(m in msg for m in TRANSIENT_MESSAGES)


# ── 重试决策合并（三路重试环单一真源，2026-09-29） ──────────────────
# 接线前chat()/chat_stream()/chat_stream_with_tools()三路各自内联组合
# 「可重试判定 ∧ 剩余次数 → backoff_ms → sleep」同一段策略（三处复制粘贴，
# 漂移即行为分叉）。本组plan_*把"失败后要不要重试、等多久"这一决策收口为
# 纯函数单点——三路只剩各自的流式解析差异。此单点同时是模型降级有序链
# （utils.model_fallback）未来接入流式的挂载点：switch_now（429有备胎立即切）
# 只需在此决策里加一条分支，不必再改三处。
# 纯函数无副作用、无I/O，仅组合上方已移植的kilocode原语（is_retryable_status
# /is_transient_error/backoff_ms/MAX_ATTEMPTS），行为面与旧内联逐字等价。


def plan_status_retry(status: int, attempt: int, headers=None) -> float | None:
    """HTTP状态失败后的重试决策：返回等待秒数（应重试）或None（应放弃）。

    组合三要素（kilocode retry.ts语义）：is_retryable_status（429/408/5xx可重试，
    400/401/402/403/404请求/凭证级fail-fast）∧ attempt未耗尽 → backoff_ms
    （有头听头Retry-After三格式，无头指数退避封顶30s）。

    Args:
        status: HTTP状态码
        attempt: 0基，本次是第attempt次尝试（attempt=0=首发，失败后算第1次重试决策）
        headers: 响应头（可选，供Retry-After解析；None走指数退避）

    Returns:
        应等待的秒数（>0，调用方sleep后重试）或None（不可重试/次数耗尽，调用方失败）。
    """
    if attempt < MAX_ATTEMPTS - 1 and is_retryable_status(status):
        return backoff_ms(attempt + 1, headers) / 1000.0
    return None


def plan_error_retry(
    exc: BaseException, attempt: int, has_output: bool = False
) -> float | None:
    """传输失败后的重试决策：返回等待秒数（应重试）或None（应放弃）。

    组合：is_transient_error（httpx.TransportError家族/TRANSIENT_MESSAGES瞬时可自愈）
    ∧ attempt未耗尽 ∧ 本attempt零输出 → backoff_ms（无头指数退避封顶30s）。

    Args:
        exc: 捕获到的传输异常
        attempt: 0基，本次是第attempt次尝试
        has_output: 本attempt是否已流出文本/已累积tool_calls。True则永不重试
            （已流出内容再重试=同一回复发两遍/tool_calls状态串attempt污染）。

    Returns:
        应等待的秒数或None（不重试/次数耗尽/已有输出，调用方走原失败契约）。
    """
    if not has_output and attempt < MAX_ATTEMPTS - 1 and is_transient_error(exc):
        return backoff_ms(attempt + 1) / 1000.0
    return None


# ── 传输故障分类（路线图10.1：分类入tool_failures.db可观测） ────────────
def classify_failure(source) -> str:
    """传输/状态故障确定性分类（不走LLM），产出tool_failures.db的kind列。

    source：HTTP状态码(int)、捕获的异常对象、或已分类九态码串（透传保真——
    12.6：预分类 code 经此落库不再落 unknown）。
    分类面（周期#54上游降级三态实证对齐）：
    - 429 → rate_limit（限流）；408 → timeout_status
    - 5xx → server_error（服务端瞬时故障，含503 ServiceUnavailableError实证形态）
    - 其余4xx/3xx → client_error（请求/凭证级，重试无益）
    - httpx.RemoteProtocolError → protocol_error（含incomplete chunked read
      实证形态——周期#54直探针 RemoteProtocolError: peer closed connection…）
    - httpx.ReadTimeout → read_timeout；httpx.ConnectError → connect_error
    - 其余httpx.TransportError → network_error
    - 其余httpx.RequestError → network_error（12.6 补：DecodingError 等
      请求族异常——实证 4 行 llm:dna "Error -3 while decompressing" 曾落 unknown
      分类缺口，请求族传输损坏归 network_error）
    - TRANSIENT_MESSAGES文案命中 → transient_message（跨平台兜底）
    - 其余 → unknown
    """
    if isinstance(source, bool):  # bool是int子类，先排除
        return "unknown"
    if isinstance(source, str) and source in _LLM_KIND_CODES:
        return source  # 已分类九态码串透传（record 调用方预分类场景）
    if isinstance(source, int):
        s = int(source)
        if s == 429:
            return "rate_limit"
        if s == 408:
            return "timeout_status"
        if s >= 500:
            return "server_error"
        return "client_error"
    if isinstance(source, BaseException):
        if isinstance(source, httpx.ReadTimeout):
            return "read_timeout"
        if isinstance(source, httpx.ConnectError):
            return "connect_error"
        if isinstance(source, httpx.RemoteProtocolError):
            return "protocol_error"
        if isinstance(source, httpx.TransportError):
            return "network_error"
        if isinstance(source, httpx.RequestError):
            # 12.6 补：DecodingError 等请求族异常（非 TransportError 兄弟类）——
            # 实证 4 行 llm:dna "Error -3 while decompressing" 落 unknown 分类缺口
            return "network_error"
        msg = str(source).lower()
        if any(m in msg for m in TRANSIENT_MESSAGES):
            return "transient_message"
        return "unknown"
    return "unknown"
