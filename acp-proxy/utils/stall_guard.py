# -*- coding: utf-8 -*-
"""LLM 非流式传输无流停顿检测（路线图 12.4①，周期#69）。

背景（真实事故逐字取证，2026-10-11 10:19 acp-proxy-a journal + tool_failures.db）：
step 自省 LLM 调用（llm_engine.chat()，非流式）上游传输挂死——响应头已到
（chunked+gzip，MiFE 网关）后**约 285 秒零字节**，httpx read=120 超时**未开火**
（httpx/anyio 在 websockets serve 循环里的已知超时盲区，_make_client docstring
同族问题），直到对端 FIN 才见 RemoteProtocolError（incomplete chunked read），
重试一发 8s 即成功。tool_failures.db 实证行：llm:chat|protocol_error|llm_retry。
"挂死约293s 才见错，重试本可 8s 恢复"——缺的是**不依赖 httpx 超时机制**的
独立停顿看门狗。

本模块：send_with_stall_guard() 用 asyncio.wait_for 对**每个字节间隙**独立计时
（等响应头 + 响应体每个 chunk 前），任何间隙超过 stall_timeout_s() 即中止本次
尝试并抛 httpx.ReadTimeout（TransportError 家族 → 既有 is_transient_error /
classify_failure=read_timeout / 重试环零改动原样接住，契约零变化）；
attempt_deadline_s() 另设单次请求总时长兜底（细碎涓流、间隙都小于 stall 但
总不收口的形态）。两者都以"收到字节=进展"为准，**绝不对正常慢响应误杀**：
间隙 < stall 的完整慢响应照常完成（回归测试钉死）。

流式路径（13.1，周期#77 同源扩面）：chat_stream / chat_stream_with_tools 的
消费端挂起与非流式同族盲区（12.3 事故族最后已知缺口）——open_stream_with_stall_guard
守"等响应头"，aiter_with_stall_guard 守"每个 chunk 间隙"，同一套 stall/deadline
计时语义（_await_with_stall 单源）；停顿抛 httpx.ReadTimeout 落进流式路径既有的
except httpx.TransportError（partial 输出/has_output 重试规则零改动原样生效，
错误文案逐字不动）。

语义边界（诚实记录）：
- 非流式 chat/completions 的 TTFB=整个生成时长（服务端生成完才发头）。默认
  stall=60s 对 chat() 实测单次调用时长（0.03~16s，周期#67 五样本+判词 16s）
  留 3.75 倍余量；若真实负载出现生成 >60s 的合法调用，调 SOULMATE_LLM_STALL_TIMEOUT_S
  即可（≤120s 与 httpx read 超时对齐，>120s 的调用今天本来就会 ReadTimeout）。
- 流式路径的 TTFB=首 token 时长（短），生成期"收到字节=进展"：间隙 < stall 的
  慢涓流生成照常收完整流（负控制必测）；流式总时长兜底默认 900s（stream_deadline_s，
  比非流式 240s 宽松——连续出字的长生成属合法），涓流假进展仍是有界失败。

环境变量（均可运行时读取，测试注入用）：
- SOULMATE_LLM_STALL_TIMEOUT_S  停顿阈值秒（默认 60；任何字节间隙超过即中止本次尝试）
- SOULMATE_LLM_ATTEMPT_DEADLINE_S  单请求总时长上限秒（默认 240，对齐 8.3 回合软预算）
- SOULMATE_LLM_STREAM_DEADLINE_S  流式单次生成总时长上限秒（默认 900）
- SOULMATE_LLM_STALL_GUARD=off  整体旁路（退回 client.request 原行为，排障用）
"""
import asyncio
import os

import httpx

__all__ = [
    "stall_timeout_s",
    "attempt_deadline_s",
    "stream_deadline_s",
    "stall_guard_enabled",
    "send_with_stall_guard",
    "open_stream_with_stall_guard",
    "aiter_with_stall_guard",
]

# 默认值取值依据：
# - STALL 60s：chat() 单次调用实测 0.03~16s（周期#67 四段计时），3.75 倍余量；
#   且 >120s（httpx read 超时）的调用今天必然失败，故 stall≤120 不引入新误杀面。
# - DEADLINE 240s：对齐 turn_watchdog 软预算（8.3），单次非流式请求总时长
#   超过回合软预算本身已属病态（含涓流假进展兜底）。
# - STREAM_DEADLINE 900s：流式生成"连续出字=合法进展"，长文档生成可达数分钟，
#   默认比非流式宽松 3.75 倍；涓流假进展（间隙<stall 但从不收口）由它兜底有界。
DEFAULT_STALL_TIMEOUT_S = 60.0
DEFAULT_ATTEMPT_DEADLINE_S = 240.0
DEFAULT_STREAM_DEADLINE_S = 900.0


def _env_float(name: str, default: float) -> float:
    try:
        val = float(os.environ.get(name, "") or 0)
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return default


def stall_timeout_s() -> float:
    """无流停顿阈值秒（字节间隙上限）。SOULMATE_LLM_STALL_TIMEOUT_S 覆盖。"""
    return _env_float("SOULMATE_LLM_STALL_TIMEOUT_S", DEFAULT_STALL_TIMEOUT_S)


def attempt_deadline_s() -> float:
    """单次请求总时长上限秒。SOULMATE_LLM_ATTEMPT_DEADLINE_S 覆盖。"""
    return _env_float("SOULMATE_LLM_ATTEMPT_DEADLINE_S", DEFAULT_ATTEMPT_DEADLINE_S)


def stream_deadline_s() -> float:
    """流式单次生成总时长上限秒。SOULMATE_LLM_STREAM_DEADLINE_S 覆盖。

    流式生成"连续出字=合法进展"，长文档生成可达数分钟，默认 900s 比非流式
    240s 宽松；涓流假进展（间隙<stall 但从不收口）由它兜底有界失败。
    """
    return _env_float("SOULMATE_LLM_STREAM_DEADLINE_S", DEFAULT_STREAM_DEADLINE_S)


def stall_guard_enabled() -> bool:
    """SOULMATE_LLM_STALL_GUARD=off/0/false 时旁路（排障用），默认启用。"""
    raw = (os.environ.get("SOULMATE_LLM_STALL_GUARD", "") or "").strip().lower()
    return raw not in ("off", "0", "false", "no")


async def _anext_or_none(aiter):
    """安全取下一个 chunk：StopAsyncIteration 转 None（避免经 Task/等待层传播
    StopAsyncIteration 的边角语义）。"""
    try:
        return await aiter.__anext__()
    except StopAsyncIteration:
        return None


async def _await_with_stall(awaitable, stall: float, remaining_fn, deadline_msg: str,
                            stall_msg_fmt: str):
    """单个等待点的停顿计时：超 stall=停顿；总预算耗尽=总时长超限。

    两个超时都转成 httpx.ReadTimeout（TransportError 家族）——既有重试环/
    is_transient_error/classify_failure 零改动接住，错误契约零变化。
    """
    budget = min(stall, remaining_fn())
    if budget <= 0:
        raise httpx.ReadTimeout(deadline_msg)
    try:
        return await asyncio.wait_for(awaitable, timeout=budget)
    except asyncio.TimeoutError:
        if remaining_fn() <= 0:
            raise httpx.ReadTimeout(deadline_msg) from None
        raise httpx.ReadTimeout(stall_msg_fmt) from None


async def send_with_stall_guard(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict | None = None,
    json: object = None,
    stall_s: float | None = None,
    deadline_s: float | None = None,
) -> httpx.Response:
    """带无流停顿检测的单次 HTTP 请求（非流式语义：返回已读全 body 的 Response）。

    与 client.post() 返回契约一致（resp.status_code/headers/text/json 可用）；
    差别只在：任何字节间隙（等响应头/等响应体 chunk）超过 stall_s 即中止本次
    尝试并 raise httpx.ReadTimeout，或总时长超 deadline_s 同样中止——
    "要么完成要么显式报因"，禁静默空转。
    """
    stall = stall_s if stall_s is not None else stall_timeout_s()
    deadline = deadline_s if deadline_s is not None else attempt_deadline_s()

    if not stall_guard_enabled():
        # 旁路：原行为逐字（非流式整读）
        return await client.request(method, url, headers=headers, json=json)

    if not (hasattr(client, "build_request") and hasattr(client, "send")):
        # 非 httpx 客户端（测试替身只有 post() 鸭子类型）：走原 post 契约，
        # 逐字保留接线前调用形态（headers=None 不传参），不破坏既有替身测试
        kwargs = {}
        if headers is not None:
            kwargs["headers"] = headers
        if json is not None:
            kwargs["json"] = json
        return await client.post(url, **kwargs)

    loop = asyncio.get_running_loop()
    t_end = loop.time() + deadline

    def _remaining() -> float:
        return t_end - loop.time()

    deadline_msg = (
        f"LLM请求总时长超上限 {deadline:.0f}s（无流停顿检测总预算兜底）"
    )
    stall_head_msg = f"LLM传输停顿：{stall:.0f}s 未收到响应头（无流停顿检测）"
    stall_body_msg = f"LLM传输停顿：{stall:.0f}s 响应体无新数据（无流停顿检测）"

    req = client.build_request(method, url, headers=headers, json=json)
    resp = None
    try:
        resp = await _await_with_stall(
            client.send(req, stream=True), stall, _remaining,
            deadline_msg, stall_head_msg,
        )
        if not isinstance(resp, httpx.Response):
            # 测试替身响应（鸭子类型，非 httpx.Response）：保持原契约原样整读返回，
            # 不做字节级守卫也不重包装（重包装会丢替身的 json()/text 语义）
            if hasattr(resp, "aread"):
                await resp.aread()
            return resp
        chunks: list[bytes] = []
        # aiter_raw 收**原始（未解码）字节**——必须与重建 Response 保留的
        # content-encoding 头一致：aiter_bytes 产出的是已解码字节，配 gzip 头重建
        # 会被二次解码 → DecodingError "incorrect header check"（真实生产实证
        # 2026-10-11 16:09 journal：token-plan gzip 响应下 chat() 全部报
        # "[LLM错误: Error -3 while decompressing...]"，纯文本假服务器测试未覆盖）
        aiter = resp.aiter_raw()
        while True:
            chunk = await _await_with_stall(
                _anext_or_none(aiter), stall, _remaining,
                deadline_msg, stall_body_msg,
            )
            if chunk is None:
                break
            chunks.append(chunk)
        return httpx.Response(
            resp.status_code,
            headers=resp.headers,
            content=b"".join(chunks),
            request=req,
        )
    finally:
        if resp is not None:
            try:
                await resp.aclose()
            except Exception:  # noqa: BLE001 — 收尾尽力而为，不冒泡
                pass


async def open_stream_with_stall_guard(
    send_awaitable,
    *,
    stall_s: float | None = None,
    deadline_s: float | None = None,
):
    """流式请求"等响应头"的停顿计时（send_with_stall_guard 头部等待同源语义）。

    流式路径 client.send(req, stream=True) 在响应头到达时即返回，但与非流式同族
    盲区：websockets serve 循环里 httpx read 超时不开火时，头前挂死同样无界。
    间隙 > stall_s → 抛 httpx.ReadTimeout（既有 except httpx.TransportError 原样
    接住，重试/失败契约零变化）；deadline_s 为本次流式生成总预算起点（调用方传
    同一 remaining 语义即可，此处独立起表=头+体各得一份预算，上界仍是有界）。
    """
    if not stall_guard_enabled():
        return await send_awaitable
    stall = stall_s if stall_s is not None else stall_timeout_s()
    deadline = deadline_s if deadline_s is not None else stream_deadline_s()
    loop = asyncio.get_running_loop()
    t_end = loop.time() + deadline

    def _remaining() -> float:
        return t_end - loop.time()

    deadline_msg = f"LLM请求总时长超上限 {deadline:.0f}s（无流停顿检测总预算兜底）"
    stall_head_msg = f"LLM传输停顿：{stall:.0f}s 未收到响应头（无流停顿检测）"
    return await _await_with_stall(
        send_awaitable, stall, _remaining, deadline_msg, stall_head_msg,
    )


async def aiter_with_stall_guard(
    aiter,
    *,
    stall_s: float | None = None,
    deadline_s: float | None = None,
):
    """流式消费侧 chunk 间隙停顿看门狗（13.1）：逐 chunk 套 _await_with_stall 单源计时。

    - 每个 chunk 前的间隙 > stall_s → 抛 httpx.ReadTimeout（"响应体无新数据"）；
    - 总时长 > deadline_s（涓流假进展：间隙都小但从不收口）→ 抛 httpx.ReadTimeout
      （"总时长超上限"）；
    - 间隙 < stall_s 的慢涓流生成照常逐 chunk 产出（零误杀负控制）；
    - 内层迭代器自身抛出的异常（RemoteProtocolError 等）原样透传不吞；
    - SOULMATE_LLM_STALL_GUARD=off 时旁路为原样迭代。

    抛出的 ReadTimeout 属 TransportError 家族：流式路径既有 except httpx.TransportError
    原样接住——partial 输出/has_output 重试规则零改动（plan_error_retry 语义不变），
    失败文案走既有 "[LLM响应超时]" 契约逐字不动。
    """
    if not stall_guard_enabled():
        async for item in aiter:
            yield item
        return
    stall = stall_s if stall_s is not None else stall_timeout_s()
    deadline = deadline_s if deadline_s is not None else stream_deadline_s()
    loop = asyncio.get_running_loop()
    t_end = loop.time() + deadline

    def _remaining() -> float:
        return t_end - loop.time()

    deadline_msg = f"LLM请求总时长超上限 {deadline:.0f}s（无流停顿检测总预算兜底）"
    stall_body_msg = f"LLM传输停顿：{stall:.0f}s 响应体无新数据（无流停顿检测）"
    while True:
        item = await _await_with_stall(
            _anext_or_none(aiter), stall, _remaining, deadline_msg, stall_body_msg,
        )
        if item is None:
            return
        yield item
