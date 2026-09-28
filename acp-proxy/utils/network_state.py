# -*- coding: utf-8 -*-
"""网络离线三态 — kilocode SessionNetwork移植（网络恢复专项）

调研来源：SUMMARY.md §五 cortex缺口「LLM重试策略（Retry-After三格式+5xx重试+
离线三态）| kilocode retry.ts | P0」的第三件套（前两件=utils/llm_retry.py已由
上轮242b5e8c完成）；kilocode source session/network.ts（418行）逐点对照移植。

kilocode SessionNetwork语义（network.ts）：
- **三态**：blocked（离线park等待）→ resumed（网络恢复后retry自动续跑）/
  aborted（人工拒绝或放弃）。turn遇到disconnected类错误不立即失败，而是park
- **3URL竞速探测**（probe()=Promise.any）：任一URL HEAD返回status<500即在线
- **3s轮询**（POLL_MS=3000）：watch()循环探测，恢复即restore
- **10s宽限自动续跑**（RESUME_MS=10000）：restore后留10s人工拒绝窗口，无人
  拒绝则自动reply续跑（"断网→等一会→自动接着跑"）
- **MCP重连**（reply()）：恢复后把failed的远程MCP Server重新connect

移植适配（如实记录，非照抄处）：
1. **探测URL本地化**：kilocode原URL（kilo.ai/example.com/cloudflare）在大陆
   网络两个不可达（实测example.com超时），保留"3URL竞速+任一通即在线"机制，
   URL换为大陆可达的连通性检查点（baidu/miui generate_204/captive.apple），
   可用NETWORK_PROBE_URLS环境变量覆盖
2. **先探测再park**：kilocode对任何disconnected错误直接park；本实现先做一次
   探测，探测说在线=端点级故障（对方服务挂了≠断网），走原有退避重试不park——
   防止把单个API故障误判为全局断网把turn挂起
3. **有界park**：kilocode无界等待直到人工拒绝；服务端turn无人值守，设
   NETWORK_OFFLINE_MAX_WAIT上限（默认30s），超时→aborted→保持既有
   "[LLM错误: …]"可见失败契约（mem0：失败必须可见，不吞）
4. **MCP重连走HTTP**：kilocode是同进程Effect服务；本项目mcp-client是独立
   systemd服务(:8094)，恢复回调通过其HTTP API（/api/mcp/status +
   POST /api/mcp/servers/{id}/connect）重连status=="error"的Server
   （kilocode只重连failed，对应语义）

接线点：agent/llm_engine.py三条传输路径的except httpx.TransportError分支
（chat()/chat_stream()/chat_stream_with_tools()）——传输错误先过maybe_park，
离线则park等恢复再走原有重试；app.py /api/network/status暴露park状态可观测。
"""
import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("acp-agent.network")

# ── 测试/运行时seam（防测试替身穿透）────────────────────────────────────
# probe用import时捕获的httpx.AsyncClient类引用：llm_engine的存量测试会把
# httpx.AsyncClient整体替换成fake client（无head方法），若probe动态解析同名属性
# 会被测试替身骗成"离线"，污染既有测试的退避时长断言（waits==[2.0]）。捕获后
# probe永远走真实网络类，测试替身只影响被测传输路径。
_PROBE_CLIENT_CLS = httpx.AsyncClient
# 睡眠seam同理：llm_engine存量测试monkeypatch asyncio.sleep记录时长，park的
# 轮询睡眠不应混入该记录——捕获真实sleep，park节奏与被测退避解耦。
_sleep = asyncio.sleep

# ── 常量（kilocode network.ts逐值对应，URL本地化见模块docstring适配#1）────
POLL_S = 3.0  # POLL_MS=3000：离线轮询探测间隔
PROBE_TIMEOUT_S = 5.0  # PROBE_MS=5000：单URL探测超时
RESUME_GRACE_S = 10.0  # RESUME_MS=10000：恢复后宽限（人工拒绝窗口）再自动续跑
# kilocode无界park+人工拒绝；服务端有界park（适配#3），env可调
MAX_WAIT_S = float(os.environ.get("NETWORK_OFFLINE_MAX_WAIT", "30"))
PROBE_URLS = tuple(
    u.strip()
    for u in os.environ.get(
        "NETWORK_PROBE_URLS",
        "https://www.baidu.com,"
        "https://connect.rom.miui.com/generate_204,"
        "https://captive.apple.com/hotspot-detect.html",
    ).split(",")
    if u.strip()
)
MCP_CLIENT_BASE = os.environ.get("MCP_CLIENT_BASE", "http://127.0.0.1:8094")

# 三态（kilocode retry/blocked/aborted）：wait记录的生命周期状态
BLOCKED = "blocked"  # 离线park等待中
RESUMED = "resumed"  # 网络恢复，retry自动续跑
ABORTED = "aborted"  # 放弃（超时/人工拒绝/取消）
NOT_OFFLINE = "not_offline"  # maybe_park返回值：非全局离线，走原有退避重试

# kilocode network.ts codes集合（Node错误码）+ Python errno对应补充
DISCONNECT_CODES = frozenset({
    "ECONNRESET", "ECONNREFUSED", "ENOTFOUND", "EAI_AGAIN", "ETIMEDOUT",
    "ENETUNREACH", "EHOSTUNREACH", "ENETDOWN",
    # Python errno.errorcode名字（socket/OSError链上提取）
    "EHOSTDOWN", "ENETRESET", "EPIPE",
    # kilocode undici错误码（跨实现保留）
    "UND_ERR_CONNECT_TIMEOUT", "UND_ERR_HEADERS_TIMEOUT", "UND_ERR_SOCKET",
    "ERR_SOCKET_CONNECTION_TIMEOUT",
})

# kilocode network.ts msgs()文案匹配（跨平台错误串兜底）+ Python特有补充
DISCONNECT_MESSAGES = (
    # kilocode原文
    "load failed",
    "failed to fetch",
    "fetch failed",
    "network connection was lost",
    "network is unreachable",
    "socket connection",
    "socket hang up",
    "connection timed out",
    "connection terminated",
    "connect timeout",
    # Python/httpx大陆网络实测常见形态（适配#4补充，语义同类）
    "name resolution",
    "temporary failure in name resolution",
    "no route to host",
    "connect call failed",
    "all connection attempts failed",
)

# kilocode network.ts message()友好文案表
_CODE_MESSAGES = {
    "ECONNRESET": "Connection reset by server",
    "ECONNREFUSED": "Connection refused",
    "ENOTFOUND": "Host not found",
    "EAI_AGAIN": "DNS lookup failed",
    "ETIMEDOUT": "Connection timed out",
    "ENETUNREACH": "Network is unreachable",
    "EHOSTUNREACH": "Host is unreachable",
    "ENETDOWN": "Network is down",
    "UND_ERR_CONNECT_TIMEOUT": "Connection timed out",
    "UND_ERR_HEADERS_TIMEOUT": "Request timed out",
    "UND_ERR_SOCKET": "Network socket failed",
    "ERR_SOCKET_CONNECTION_TIMEOUT": "Connection timed out",
}


# ── 错误链分类（kilocode chain()/code()/disconnected()/message()移植）─────

def chain(err, seen=None):
    """展开异常链：__cause__/__context__ + ExceptionGroup子异常
    （kilocode chain()：cause + errors[]递归，防环）"""
    if err is None:
        return []
    if seen is None:
        seen = set()
    if id(err) in seen:
        return []
    seen.add(id(err))
    items = [err]
    items.extend(chain(getattr(err, "__cause__", None), seen))
    items.extend(chain(getattr(err, "__context__", None), seen))
    if isinstance(err, BaseExceptionGroup):
        for kid in err.exceptions:
            items.extend(chain(kid, seen))
    return items


def error_code(err) -> str | None:
    """从异常链提取错误码（kilocode code()：第一个code字段）。
    Python映射：显式code字符串属性 → OSError errno → socket.gaierror。"""
    import errno as _errno
    import socket

    for item in chain(err):
        code = getattr(item, "code", None)
        if isinstance(code, str):
            return code
    for item in chain(err):
        if isinstance(item, socket.gaierror):
            # EAI_AGAIN（临时DNS失败）/EAI_NONAME≈ENOTFOUND
            return "EAI_AGAIN" if item.errno == getattr(socket, "EAI_AGAIN", -3) else "ENOTFOUND"
        if isinstance(item, OSError) and item.errno is not None:
            name = _errno.errorcode.get(item.errno)
            if name:
                return name
    return None


def disconnected(err) -> bool:
    """网络级断连判定（kilocode disconnected()）：错误码∈codes集合 ||
    文案命中msgs匹配 || TimeoutError（kilocode对AbortSignal.timeout同判）。"""
    code = error_code(err)
    if code is not None and code in DISCONNECT_CODES:
        return True
    for item in chain(err):
        if isinstance(item, (TimeoutError, asyncio.TimeoutError)):
            return True
    for item in chain(err):
        msg = str(item).lower()
        if any(m in msg for m in DISCONNECT_MESSAGES):
            return True
    return False


def network_message(err) -> str:
    """友好断网文案（kilocode message()逐码对应），用于park记录/日志可见"""
    code = error_code(err)
    if code is not None and code in _CODE_MESSAGES:
        return _CODE_MESSAGES[code]
    for item in chain(err):
        msg = str(item).lower()
        if "unable to connect" in msg and "access the url" in msg:
            return str(item)
        if "failed to fetch" in msg or "fetch failed" in msg:
            return "Network request failed"
    return "Network connection failed"


# ── wait记录（kilocode Wait schema：id/message/restored/时间戳）──────────

@dataclass
class NetworkWait:
    id: str
    message: str
    state: str = BLOCKED
    created: float = field(default_factory=time.time)
    restored: float | None = None
    error: str | None = None


_waits: dict[str, NetworkWait] = {}
_stats = {
    "total_parks": 0,  # 进入blocked的次数
    "resumed": 0,      # 恢复续跑次数（retry）
    "aborted": 0,      # 放弃次数
}
_restore_callbacks: list = []


def list_waits() -> list[dict]:
    """全部wait记录（blocked/resumed/aborted）——观测面板/API消费"""
    return [
        {
            "id": w.id, "message": w.message, "state": w.state,
            "created": w.created, "restored": w.restored, "error": w.error,
        }
        for w in _waits.values()
    ]


def stats() -> dict:
    """状态汇总（/api/network/status消费——'它是不是在等网络'必须可见）"""
    return {
        **_stats,
        "pending": sum(1 for w in _waits.values() if w.state == BLOCKED),
        "probe_urls": list(PROBE_URLS),
        "poll_s": POLL_S,
        "max_wait_s": MAX_WAIT_S,
        "resume_grace_s": RESUME_GRACE_S,
    }


def register_restore_callback(cb) -> None:
    """注册网络恢复回调（kilocode reply()的MCP重连语义挂载点）"""
    if cb not in _restore_callbacks:
        _restore_callbacks.append(cb)


# ── 探测（kilocode check()/probe()：HEAD+5s超时+Promise.any竞速）─────────

async def _probe_one(url: str) -> bool:
    """单URL探测：HEAD、PROBE_TIMEOUT_S超时、status<500即在线（kilocode check()）。
    trust_env不关：与LLM传输同一条出网路径（代理环境探测结果才真实反映调用可达性）。"""
    try:
        async with _PROBE_CLIENT_CLS(timeout=PROBE_TIMEOUT_S) as client:
            resp = await client.head(url, follow_redirects=True)
            return resp.status_code < 500
    except Exception:
        return False


async def probe_online() -> bool:
    """3URL竞速（kilocode probe()=Promise.any）：任一在线即在线，全败才离线。
    竞速而非串行：断网时也要在PROBE_TIMEOUT_S内拿到结论，不拖慢park判定。"""
    tasks = [asyncio.ensure_future(_probe_one(u)) for u in PROBE_URLS]
    try:
        for fut in asyncio.as_completed(tasks):
            if await fut:
                return True
        return False
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


# ── 恢复回调（kilocode reply()的MCP重连，适配#4跨进程HTTP）───────────────

async def reconnect_failed_mcp_servers(base_url: str | None = None) -> dict:
    """网络恢复后重连failed的远程MCP Server（kilocode reply()语义：只重连
    status=="failed"者；本项目mcp-client的ServerStatus.ERROR为对应态）。
    跨进程HTTP：GET /api/mcp/status找error态 → POST /servers/{id}/connect。
    整体绝不抛出（重连失败只记日志，不反噬LLM续跑主流程）。"""
    base = (base_url or MCP_CLIENT_BASE).rstrip("/")
    result = {"checked": 0, "reconnected": 0, "errors": []}
    try:
        async with _PROBE_CLIENT_CLS(timeout=5) as client:
            resp = await client.get(f"{base}/api/mcp/status")
            servers = (resp.json() or {}).get("servers") or []
            result["checked"] = len(servers)
            for s in servers:
                if not isinstance(s, dict) or s.get("status") != "error":
                    continue  # kilocode：非failed不动（disconnected可能是人为断开）
                sid = s.get("id") or s.get("name")
                if not sid:
                    continue
                try:
                    r = await client.post(f"{base}/api/mcp/servers/{sid}/connect")
                    if r.status_code < 300:
                        result["reconnected"] += 1
                    else:
                        result["errors"].append({"server_id": sid, "error": f"HTTP {r.status_code}"})
                except Exception as e:
                    result["errors"].append({"server_id": sid, "error": str(e)})
    except Exception as e:
        # mcp-client服务本身不可达：记日志绝不抛出（kilocode log.error同语义）
        logger.warning(f"[网络] MCP重连跳过（mcp-client不可达）: {e}")
        result["errors"].append({"server_id": "*", "error": f"mcp-client不可达: {e}"})
    return result


async def _run_restore_callbacks() -> None:
    """恢复回调逐个执行（kilocode reply() fire-and-forget语义：单个失败不阻塞）"""
    for cb in list(_restore_callbacks):
        try:
            res = cb()
            if asyncio.iscoroutine(res):
                res = await res
            if res:
                logger.info(f"[网络] 恢复回调完成: {res}")
        except Exception as e:
            logger.warning(f"[网络] 恢复回调失败（续跑照常）: {e}")


# ── 离线park主入口（kilocode ask()/watch()/restore()/resume()合流）────────

async def maybe_park(exc, cancel_event: asyncio.Event | None = None) -> str:
    """传输错误离线判定+park等待恢复（kilocode SessionNetwork三态主入口）。

    返回三态决策（调用方据此决定下一步，原有重试/失败契约不变）：
    - NOT_OFFLINE：非网络级断连，或探测说在线（端点级故障≠断网）→ 走原有退避重试
    - RESUMED：离线park后网络恢复（含10s宽限）→ 应立即重试
    - ABORTED：park超时（MAX_WAIT_S）/取消 → 走原有失败契约（可见失败）

    服务端适配（docstring适配#3）：kilocode无界park+人工拒绝，本实现有界park，
    aborted后回落到llm_engine既有重试记账/可见失败路径。
    """
    if not disconnected(exc):
        return NOT_OFFLINE
    # 先探测再park（适配#2）：探测在线=端点级故障，不park
    if await probe_online():
        logger.info(f"[网络] 传输错误但探测在线（端点级故障，不park）: {network_message(exc)}")
        return NOT_OFFLINE

    wait = NetworkWait(id=f"nw-{uuid.uuid4().hex[:12]}", message=network_message(exc))
    _waits[wait.id] = wait
    _stats["total_parks"] += 1
    logger.warning(
        f"[网络] 离线park开始（{wait.message}），每{POLL_S:.0f}s竞速探测，"
        f"上限{MAX_WAIT_S:.0f}s（{wait.id}）"
    )

    # watch()语义：轮询竞速探测，恢复即restore；poll上限双保险（时间+次数，
    # 防asyncio.sleep被测试伪造后时间不走导致死循环）
    max_polls = max(1, int(MAX_WAIT_S / POLL_S) + 1)
    deadline = time.monotonic() + MAX_WAIT_S
    polls = 0
    while polls < max_polls and time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            wait.state = ABORTED
            wait.error = "cancelled"
            _stats["aborted"] += 1
            logger.warning(f"[网络] 离线park被取消（{wait.id}）")
            return ABORTED
        await _sleep(POLL_S)
        polls += 1
        if not await probe_online():
            continue
        # restore()：标记恢复+事件；resume()：10s宽限后自动续跑（人工拒绝窗口）
        wait.restored = time.time()
        logger.info(f"[网络] 网络恢复（{wait.id}），{RESUME_GRACE_S:.0f}s宽限后自动续跑")
        await _sleep(RESUME_GRACE_S)
        if cancel_event is not None and cancel_event.is_set():
            wait.state = ABORTED
            wait.error = "cancelled during grace"
            _stats["aborted"] += 1
            logger.warning(f"[网络] 宽限期内被取消（{wait.id}）")
            return ABORTED
        wait.state = RESUMED
        _stats["resumed"] += 1
        logger.info(f"[网络] 离线park恢复续跑（{wait.id}，轮询{polls}次）")
        await _run_restore_callbacks()
        return RESUMED

    wait.state = ABORTED
    wait.error = f"timeout after {MAX_WAIT_S:.0f}s"
    _stats["aborted"] += 1
    logger.warning(f"[网络] 离线park超时放弃（{wait.id}，{MAX_WAIT_S:.0f}s仍离线）")
    return ABORTED
