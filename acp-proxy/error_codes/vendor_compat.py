# -*- coding: utf-8 -*-
"""vendor_compat.py — 供应商错误文案兼容层（Pi 取长补短 #15 第二件）。

来源：OpenBidKit 集成 Pi 的「集成税」实证——Pi 自动重试靠**正则匹配错误文案**、
上下文溢出判定同样靠文案，OpenBidKit 被迫写 piRetryErrorNormalizer 把真实供应商
错误改写成 Pi 认得的格式（piRetryErrorNormalizer 模式）。

SoulMate 抄功能**不抄机制**（《取长补短清单》明确不抄②）：供应商错误文案/payload
只在**边界归一一次** → 结构化错误码（error_codes LLM供应商域 -322xx）+ retryable
判定；之后重试/降级/记账决策只看码，不再做文案正则（脆弱耦合绝迹）。

归一入口 `normalize()`，输入三形态（HTTP status / 供应商错误 payload / 传输异常），
输出统一 `NormalizedError`：
- `code`     error_codes.ErrorCode（LLM供应商域），归一目标
- `kind`     utils.llm_retry 九态分类码（tool_failures.db 记账口径，既有分类复用
             不重造——classify_failure 透传）
- `retryable` 结构化判据（error_codes.is_retryable(code)：rate_limit/timeout/
             server/network/empty_output 可重试，auth/quota/invalid/content_filter
             不可重试）

payload 识别面（一次性文本归一，之后不再看文案）：OpenAI 兼容
`{"error":{"type","code","message"}}`、Anthropic `{"type":"error","error":{"type"}}`、
裸 `{"message"}` / 字符串、以及 type/code 里的常见语义词
（rate_limit / quota / auth / api_key / context_length / content_filter /
overloaded / timeout / model_not_found …）。

独立可测（tests/test_deps_lock.py::TestVendorCompat）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from error_codes import ErrorCode, make_error, is_retryable as _code_retryable

__all__ = [
    "NormalizedError",
    "normalize",
    "TOKEN_TO_CODE",
]

# ── 文案/payload token → 结构化码（唯一一次文案消费面）─────────────
# key = 归一 token（小写、非字母数字→下划线折叠）；命中即定码，之后决策只看码。
TOKEN_TO_CODE: dict[str, int] = {
    # 限流/超时/服务端（可重试族）
    "rate_limit": ErrorCode.LLM_RATE_LIMITED,
    "rate_limit_error": ErrorCode.LLM_RATE_LIMITED,
    "rate_limit_exceeded": ErrorCode.LLM_RATE_LIMITED,
    "too_many_requests": ErrorCode.LLM_RATE_LIMITED,
    "throttling": ErrorCode.LLM_RATE_LIMITED,
    "throttling_error": ErrorCode.LLM_RATE_LIMITED,
    "timeout": ErrorCode.LLM_TIMEOUT,
    "timeout_error": ErrorCode.LLM_TIMEOUT,
    "request_timeout": ErrorCode.LLM_TIMEOUT,
    "time_limit_exceeded": ErrorCode.LLM_TIMEOUT,
    "overloaded": ErrorCode.LLM_SERVER_ERROR,
    "overloaded_error": ErrorCode.LLM_SERVER_ERROR,
    "server_error": ErrorCode.LLM_SERVER_ERROR,
    "api_error": ErrorCode.LLM_SERVER_ERROR,
    "internal_server_error": ErrorCode.LLM_SERVER_ERROR,
    "service_unavailable": ErrorCode.LLM_SERVER_ERROR,
    "model_not_found": ErrorCode.LLM_INVALID_REQUEST,  # 换模型才能成功→请求级
    "network_error": ErrorCode.LLM_NETWORK_ERROR,
    "connection_error": ErrorCode.LLM_NETWORK_ERROR,
    # 凭证/额度（不可重试族）
    "authentication_error": ErrorCode.LLM_AUTH_FAILED,
    "authentication_error_type": ErrorCode.LLM_AUTH_FAILED,
    "invalid_api_key": ErrorCode.LLM_AUTH_FAILED,
    "invalid_api_key_error": ErrorCode.LLM_AUTH_FAILED,
    "unauthorized": ErrorCode.LLM_AUTH_FAILED,
    "permission_error": ErrorCode.LLM_AUTH_FAILED,
    "insufficient_quota": ErrorCode.LLM_QUOTA_EXCEEDED,
    "quota_exceeded": ErrorCode.LLM_QUOTA_EXCEEDED,
    "billing_hard_limit_reached": ErrorCode.LLM_QUOTA_EXCEEDED,
    "account_deactivated": ErrorCode.LLM_QUOTA_EXCEEDED,
    # 请求级（改请求才能成功）
    "invalid_request_error": ErrorCode.LLM_INVALID_REQUEST,
    "invalid_request": ErrorCode.LLM_INVALID_REQUEST,
    "invalid_parameters": ErrorCode.LLM_INVALID_REQUEST,
    "context_length_exceeded": ErrorCode.LLM_INVALID_REQUEST,
    "context_length_exceeded_error": ErrorCode.LLM_INVALID_REQUEST,
    "string_above_max_length": ErrorCode.LLM_INVALID_REQUEST,
    # 内容过滤
    "content_filter": ErrorCode.LLM_CONTENT_FILTERED,
    "content_filter_error": ErrorCode.LLM_CONTENT_FILTERED,
    "content_policy_violation": ErrorCode.LLM_CONTENT_FILTERED,
    "responsibleaipolicyviolation": ErrorCode.LLM_CONTENT_FILTERED,
}

# kind 复用 utils.llm_retry 九态分类码口径（tool_failures.db 记账），不重造分类
_CODE_TO_KIND = {
    ErrorCode.LLM_RATE_LIMITED: "rate_limit",
    ErrorCode.LLM_TIMEOUT: "timeout_status",
    ErrorCode.LLM_SERVER_ERROR: "server_error",
    ErrorCode.LLM_NETWORK_ERROR: "network_error",
    ErrorCode.LLM_AUTH_FAILED: "client_error",
    ErrorCode.LLM_QUOTA_EXCEEDED: "client_error",
    ErrorCode.LLM_INVALID_REQUEST: "client_error",
    ErrorCode.LLM_EMPTY_OUTPUT: "empty_output",
    ErrorCode.LLM_CONTENT_FILTERED: "client_error",
    ErrorCode.LLM_UNKNOWN: "unknown",
}

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class NormalizedError:
    """供应商错误归一结果（结构化，之后的决策面只消费本对象）。"""

    code: int                     # error_codes.ErrorCode（LLM供应商域）
    kind: str                     # utils.llm_retry 九态分类码（记账口径）
    retryable: bool               # 结构化重试判据（error_codes.is_retryable）
    message: str                  # 归一后短文案（对外展示）
    provider: str = ""            # 来源供应商标注（openai/anthropic/…，可空）
    raw: str = ""                 # 原始文案（保留供人工排查，决策不看它）
    data: dict[str, Any] = field(default_factory=dict)

    def to_error(self):
        """OpenSoulError 标准结构（error_codes 既有契约）。"""
        err = make_error(self.code, **(self.data or {}))
        return err

    def to_dict(self) -> dict:
        return {
            "code": self.code, "kind": self.kind, "retryable": self.retryable,
            "message": self.message, "provider": self.provider, "raw": self.raw,
            "data": dict(self.data),
        }


def _token(text: str) -> str:
    return _TOKEN_RE.sub("_", str(text or "").strip().lower()).strip("_")


def _from_payload(payload) -> tuple[int | None, str, str]:
    """供应商错误 payload → (候选码|None, raw 文案, 语义 token)。

    OpenAI 兼容 {"error":{"type","code","message"}} / Anthropic
    {"type":"error","error":{"type","message"}} / 裸 {"message"} / 字符串。
    """
    if payload is None:
        return None, "", ""
    if isinstance(payload, str):
        raw = payload
        inner: dict = {}
    elif not isinstance(payload, dict):
        raw = str(payload)
        inner = {}
    else:
        inner = payload
        if isinstance(payload.get("error"), dict):
            inner = payload["error"]
        raw = str(inner.get("message") or payload.get("message") or "") or str(payload)
    # 语义 token 优先取 code（供应商更具体的码，如 invalid_api_key 优于
    # invalid_request_error），再 type/error_type/status，最后裸 message 归一
    for key in ("code", "type", "error_type", "status"):
        v = inner.get(key)
        if isinstance(v, str) and v.strip():
            t = _token(v)
            if t in TOKEN_TO_CODE:
                return TOKEN_TO_CODE[t], raw, t
    t = _token(raw)
    if t in TOKEN_TO_CODE:
        return TOKEN_TO_CODE[t], raw, t
    # message 语义词兜底（如 "Rate limit reached…"）——词序列窗口匹配，
    # 仍是边界一次性归一（之后决策只看码）
    words = re.findall(r"[a-z0-9]+", raw.lower())
    for tok in sorted(TOKEN_TO_CODE, key=lambda k: -len(k.split("_"))):
        phrase = tok.split("_")
        n = len(phrase)
        for i in range(len(words) - n + 1):
            if words[i:i + n] == phrase:
                return TOKEN_TO_CODE[tok], raw, tok
    return None, raw, t


def normalize(provider: str = "", *, status: int | None = None,
              payload=None, exc: BaseException | None = None,
              headers=None, empty_output: bool = False) -> NormalizedError:
    """供应商错误归一（piRetryErrorNormalizer 同功能，机制换成结构化码）。

    判定优先级（确定性）：
    1. exc 传输异常 → 复用 utils.llm_retry.classify_failure 九态分类（既有口径）；
    2. payload 语义 token 命中 TOKEN_TO_CODE → 定码（文案只看这一次）；
    3. HTTP status → 既有 is_retryable_status/classify_failure 口径映射；
    4. empty_output（HTTP 200 空输出独立失败形态）；
    5. 其余 → LLM_UNKNOWN（不猜、不自动重试，人工看 raw）。
    """
    raw = ""
    token = ""
    code: int | None = None
    kind = "unknown"

    if exc is not None:
        # 传输层：分类口径复用 utils.llm_retry（九态单一真源），不重造
        try:
            from utils.llm_retry import classify_failure, is_transient_error
            kind = classify_failure(exc)
            if is_transient_error(exc):
                code = ErrorCode.LLM_NETWORK_ERROR
            raw = f"{type(exc).__name__}: {exc}"
        except Exception:  # pragma: no cover — llm_retry 不可用时显式降级
            raw = f"{type(exc).__name__}: {exc}"
    if code is None and payload is not None:
        code, raw, token = _from_payload(payload)
    if code is None and status is not None:
        s = int(status)
        if s == 429:
            code = ErrorCode.LLM_RATE_LIMITED
        elif s == 408:
            code = ErrorCode.LLM_TIMEOUT
        elif s in (401, 403):
            code = ErrorCode.LLM_AUTH_FAILED
        elif s == 402:
            code = ErrorCode.LLM_QUOTA_EXCEEDED
        elif s == 400:
            code = ErrorCode.LLM_INVALID_REQUEST
        elif s >= 500:
            code = ErrorCode.LLM_SERVER_ERROR
        raw = raw or f"HTTP {s}"
    if code is None and empty_output:
        code = ErrorCode.LLM_EMPTY_OUTPUT
    if code is None:
        code = ErrorCode.LLM_UNKNOWN
        raw = raw or "(无错误详情)"

    kind = kind if kind != "unknown" else _CODE_TO_KIND.get(code, "unknown")
    info_msg = make_error(code).message
    return NormalizedError(
        code=code,
        kind=kind,
        retryable=_code_retryable(code),
        message=info_msg,
        provider=str(provider or ""),
        raw=raw,
        data={"token": token} if token else {},
    )
