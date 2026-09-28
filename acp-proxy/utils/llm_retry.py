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
import time
from email.utils import parsedate_to_datetime

import httpx

# ── 常量（kilocode retry.ts逐值对应） ──────────────────────────────────
RETRY_INITIAL_DELAY_MS = 2000  # RETRY_INITIAL_DELAY
RETRY_BACKOFF_FACTOR = 2  # RETRY_BACKOFF_FACTOR
RETRY_MAX_DELAY_NO_HEADERS_MS = 30_000  # RETRY_MAX_DELAY_NO_HEADERS（无头退避封顶）
RETRY_MAX_DELAY_MS = 2_147_483_647  # RETRY_MAX_DELAY（i32上限，setTimeout同款）
MAX_ATTEMPTS = 3  # kilocode retry() attempts默认3 = limit防无限重试

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


def backoff_ms(attempt: int, headers=None) -> float:
    """第attempt次失败后的等待毫秒（attempt从1开始，kilocode delay(attempt, error)同款）。

    有头听头：头里有可解析Retry-After→用头里的值（cap i32）；有头但无可解析值→
    指数退避只cap i32（kilocode语义）；**无头**→指数退避2s×2^n且封顶30s。
    """
    exp = RETRY_INITIAL_DELAY_MS * (RETRY_BACKOFF_FACTOR ** max(int(attempt) - 1, 0))
    if headers is not None:
        parsed = parse_retry_after_ms(headers)
        if parsed is not None:
            return _cap_ms(parsed)
        return _cap_ms(exp)
    return _cap_ms(min(exp, RETRY_MAX_DELAY_NO_HEADERS_MS))


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
