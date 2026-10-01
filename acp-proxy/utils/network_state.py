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
5. **turn级park合并**（2026-10-01轮，2026-09-29遗留"park粒度是调用不是turn"
   销账）：kilocode park的单位是整个turn（ask()一次挂起turn）；移植初版按单次
   LLM调用park，一个回合多条调用可叠加N×MAX_WAIT_S干等。现合并到turn粒度：
   同turn并发attach共享一个park会话、本回合park放弃后同turn后续调用fail-fast
   不再叠加等待（详见"turn级park合并"节）

接线点：agent/llm_engine.py三条传输路径的except httpx.TransportError分支
（chat()/chat_stream()/chat_stream_with_tools()）——传输错误先过maybe_park，
离线则park等恢复再走原有重试；app.py /api/network/status暴露park状态可观测。
"""
import asyncio
import contextvars
import glob
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

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
    turn_id: str | None = None  # turn级合并：所属逻辑回合（无turn上下文=None）
    merged_calls: int = 0  # 合并进本记录的并发调用数（attach共享会话）


_waits: dict[str, NetworkWait] = {}
_stats = {
    "total_parks": 0,  # 进入blocked的次数
    "resumed": 0,      # 恢复续跑次数（retry）
    "aborted": 0,      # 放弃次数
    "human_rejected": 0,  # HITL人工拒绝放弃次数（kilocode拒绝按钮语义）
    "turn_fail_fast": 0,  # turn级合并：本回合已park放弃，后续调用fail-fast不叠加等待
}
_restore_callbacks: list = []


def list_waits() -> list[dict]:
    """全部wait记录（blocked/resumed/aborted）——观测面板/API消费"""
    return [
        {
            "id": w.id, "message": w.message, "state": w.state,
            "created": w.created, "restored": w.restored, "error": w.error,
            "turn_id": w.turn_id, "merged_calls": w.merged_calls,
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


# ── turn级park合并（kilocode "park整个turn"语义；2026-09-29 09:30轮遗留#3销账）──
# 移植初版park粒度=单次LLM调用：一个逻辑回合的plan/judge/回复/摘要多条调用各自
# park，断网回合最坏叠加N×MAX_WAIT_S干等、产生N条wait记录。kilocode park的单位
# 是整个turn（ask()把整个turn挂起等reply）。本节把park等待合并到turn粒度：
# 1. 同turn并发调用attach进同一个park会话（turn整体挂起，一条wait记录，
#    NetworkWait.merged_calls记合并数）；
# 2. 本回合已有park**放弃**（aborted）后，同turn后续调用遇离线fail-fast返回
#    ABORTED——turn已等待过一次，再叠加等待只会把可见失败拖长N倍；
# 3. park恢复续跑（RESUMED）后再次遇新断网允许再park（新outage新park，
#    RESUMED不占turn预算）——与kilocode "reply后再次错误再ask()"同语义。
# 无turn上下文的调用（title生成/摘要压缩）保持旧行为=按调用park。
# 接线：soulmate TurnLifecycleBus turn.open→begin_turn / turn.close→end_turn；
#       discussion_engine讨论task=一个turn（run()入口begin_turn/finally end_turn）。

@dataclass
class TurnParkState:
    """一个逻辑回合的park预算（park的单位=kilocode的turn）。"""
    turn_id: str
    aborted: bool = False  # 本回合已有park放弃→后续调用fail-fast


_current_turn: contextvars.ContextVar["TurnParkState | None"] = (
    contextvars.ContextVar("network_turn_park_state", default=None)
)


def begin_turn(turn_id: str) -> None:
    """turn开始：建立本回合park预算（TurnLifecycleBus turn.open接线点）。"""
    _current_turn.set(TurnParkState(turn_id=str(turn_id or "")))


def end_turn() -> None:
    """turn结束：清除回合上下文（turn.close接线点；无上下文=按调用park旧行为）。"""
    _current_turn.set(None)


@dataclass
class _ParkSession:
    """一个进行中的park会话——同turn并发调用attach共享（turn整体挂起）。"""
    wait: NetworkWait
    outcome: str = ABORTED  # 会话结束后的最终三态（RESUMED/ABORTED），默认安全值
    done: asyncio.Event = field(default_factory=asyncio.Event)


_active_park: "_ParkSession | None" = None


# ── HITL人工拒绝 + 跨进程park状态可见（kilocode人工拒绝窗口语义补全）────────
# kilocode SessionNetwork带人工拒绝（前端"等待网络恢复…[拒绝]"卡片，RESUME_MS
# 宽限期=人工拒绝窗口）；移植初版（适配#3）只有有界park超时出口，人工拒绝面
# 缺失（dev-reports多轮挂账"HITL网络等待交互卡"）。本节补两件配套：
# 1. **拒绝标志文件**：park真实发生在agent子进程（ws_acp spawn的agent.start
#    --stdio），拒绝入口在app进程（POST /api/network/reject）——进程内存Event
#    跨不过进程边界，用标志文件（与tool_output账本同款跨进程模式）。park循环
#    每次轮询消费检查；宽限期（人工拒绝窗口本体）结束后、自动续跑前再查一次
#    ——窗口内拒绝 > 自动续跑。命中→ABORTED(error=human_rejected)，
#    llm_engine既有"[LLM错误: …]"可见失败契约不变。
# 2. **per-pid状态快照**：park状态此前只在park所在进程内存可见——app端
#    /api/network/status看不到子进程chat turn的park（观测面形同虚设）。
#    每次状态变化写 state.<pid>.json（原子写），app端合并读取+死pid清理。
PARK_STATE_DIR = os.environ.get("NETWORK_PARK_STATE_DIR") or str(
    Path(__file__).resolve().parent.parent / "data" / "network_park"
)
REJECT_FLAG_NAME = "reject.json"
# 跨进程合并时只对计数器求和；配置标量（poll_s/probe_urls等）取本地不求和
COUNTER_KEYS = (
    "total_parks", "resumed", "aborted", "human_rejected", "turn_fail_fast", "pending",
)


def _state_dir() -> str:
    """状态目录（每次调用解析env，测试可重定向tmp）；建目录失败返回''→写侧fail-safe"""
    try:
        d = os.environ.get("NETWORK_PARK_STATE_DIR") or PARK_STATE_DIR
        os.makedirs(d, exist_ok=True)
        return d
    except Exception:
        return ""


def _pid_alive(pid: int) -> bool:
    """pid存活判定（load_park_state清理死进程快照的seam，测试可替换）"""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True  # 无权限等不确定情况：宁可保留快照不误删


def _atomic_write_json(path: str, payload: dict) -> bool:
    """原子写（tmp+rename）；任何失败fail-safe返回False不抛出（观测/拒绝面不反噬主流程）"""
    try:
        tmp = f"{path}.tmp-{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.warning(f"[网络] 状态写入失败（fail-safe忽略）: {e}")
        return False


def request_reject(wait_ids: list) -> dict:
    """人工拒绝入口（app进程 /api/network/reject调用，跨进程标志文件）。

    拒绝只对用户看到的pending wait生效（显式id列表）——不设通配：通配标志
    会被拒绝时刻之后新起的park误伤，也会被先到的park单方面消费。"""
    ids = [str(w) for w in (wait_ids or []) if w]
    payload = {"wait_ids": ids, "ts": time.time()}
    if not ids:
        return payload  # 无可拒绝目标：不写标志文件（防空标志残留）
    d = _state_dir()
    if d:
        _atomic_write_json(os.path.join(d, REJECT_FLAG_NAME), payload)
    logger.warning(f"[网络] 收到人工拒绝请求: {ids}")
    return payload


def _consume_reject(wait_id: str) -> bool:
    """park循环消费拒绝标志：本wait在列表中→从列表移除（空则删文件）→True。
    文件缺失/损坏fail-safe返回False（绝不误伤正常park等待）。"""
    d = _state_dir()
    if not d:
        return False
    path = os.path.join(d, REJECT_FLAG_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        ids = [str(w) for w in (data or {}).get("wait_ids") or []]
    except FileNotFoundError:
        return False
    except Exception:
        return False
    if wait_id not in ids:
        return False
    ids.remove(wait_id)
    if ids:
        _atomic_write_json(path, {"wait_ids": ids, "ts": (data or {}).get("ts")})
    else:
        try:
            os.remove(path)
        except Exception:
            pass
    return True


def _persist_state() -> None:
    """per-pid快照（跨进程可见的数据源）：state.<pid>.json = {pid,updated,stats,waits}"""
    d = _state_dir()
    if not d:
        return
    _atomic_write_json(
        os.path.join(d, f"state.{os.getpid()}.json"),
        {"pid": os.getpid(), "updated": time.time(), "stats": stats(), "waits": list_waits()},
    )


def load_park_state(exclude_pid: int | None = None) -> dict:
    """合并读取全部存活pid快照（app端 /api/network/status消费）：
    - 每个进程一份 state.<pid>.json（无跨进程写竞争）；
    - 死pid文件删除（进程退出的遗留快照不污染观测面）；
    - 坏文件跳过（fail-safe：观测面不能因脏文件崩）；
    - waits按id去重（后读覆盖前读），计数器跨源求和。
    exclude_pid：排除本进程（调用方另有进程内存态，防重复计数）。"""
    d = _state_dir()
    merged_stats: dict = {}
    merged_waits: dict = {}
    sources: list = []
    if not d:
        return {"stats": merged_stats, "waits": [], "sources": sources}
    for path in sorted(glob.glob(os.path.join(d, "state.*.json"))):
        try:
            pid = int(Path(path).stem.split(".")[1])
        except Exception:
            continue
        if pid == exclude_pid:
            continue
        if not _pid_alive(pid):
            try:
                os.remove(path)
            except Exception:
                pass
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for k, v in (data.get("stats") or {}).items():
            if k in COUNTER_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
                merged_stats[k] = merged_stats.get(k, 0) + v
            else:
                merged_stats.setdefault(k, v)
        for w in data.get("waits") or []:
            if isinstance(w, dict) and w.get("id"):
                merged_waits[w["id"]] = w
        sources.append({"pid": pid, "updated": data.get("updated")})
    return {
        "stats": merged_stats,
        "waits": sorted(merged_waits.values(), key=lambda w: w.get("created") or 0),
        "sources": sources,
    }


def merge_park_states(local_stats: dict, local_waits: list, remote: dict) -> dict:
    """本进程内存态 × 跨进程快照 合并（/api/network/status响应的单一真源）：
    waits按id去重（本地优先）；计数器跨源求和；配置标量取本地；pending按合并后
    waits重算（挂账语义：'是不是在等网络'以wait记录为真源，不信任任何单侧计数）。"""
    merged_waits = {w["id"]: w for w in (remote.get("waits") or []) if w.get("id")}
    for w in local_waits:
        merged_waits[w["id"]] = w
    out: dict = dict(remote.get("stats") or {})
    for k, v in local_stats.items():
        if k in COUNTER_KEYS:
            out[k] = out.get(k, 0) + (v if isinstance(v, (int, float)) else 0)
        else:
            out[k] = v
    out["pending"] = sum(1 for w in merged_waits.values() if w.get("state") == BLOCKED)
    return {
        "stats": out,
        "waits": sorted(merged_waits.values(), key=lambda w: w.get("created") or 0),
        "sources": remote.get("sources") or [],
    }


# ── 离线park主入口（kilocode ask()/watch()/restore()/resume()合流）────────

async def maybe_park(exc, cancel_event: asyncio.Event | None = None) -> str:
    """传输错误离线判定+park等待恢复（kilocode SessionNetwork三态主入口）。

    返回三态决策（调用方据此决定下一步，原有重试/失败契约不变）：
    - NOT_OFFLINE：非网络级断连，或探测说在线（端点级故障≠断网）→ 走原有退避重试
    - RESUMED：离线park后网络恢复（含10s宽限）→ 应立即重试
    - ABORTED：park超时（MAX_WAIT_S）/取消 → 走原有失败契约（可见失败）

    服务端适配（docstring适配#3）：kilocode无界park+人工拒绝，本实现有界park，
    aborted后回落到llm_engine既有重试记账/可见失败路径。

    turn级合并（kilocode "park整个turn"）：同turn并发调用attach共享一个park
    会话（一条wait记录）；本回合已有park放弃后同turn后续调用fail-fast返回
    ABORTED，不再叠加等待（详见"turn级park合并"节）。
    """
    global _active_park
    if not disconnected(exc):
        return NOT_OFFLINE
    # 先探测再park（适配#2）：探测在线=端点级故障，不park
    if await probe_online():
        logger.info(f"[网络] 传输错误但探测在线（端点级故障，不park）: {network_message(exc)}")
        return NOT_OFFLINE

    turn = _current_turn.get()
    # turn级合并#1：本回合已有park放弃——fail-fast不叠加等待（turn预算用尽）
    if turn is not None and turn.aborted:
        _stats["turn_fail_fast"] += 1
        logger.warning(
            f"[网络] 本回合已等待过网络（{turn.turn_id}），"
            f"合并park不再重复等待: {network_message(exc)}"
        )
        return ABORTED

    # turn级合并#2：进行中的park会话——attach共享（turn整体挂起，一条wait记录）
    session = _active_park
    if session is not None and not session.done.is_set():
        session.wait.merged_calls += 1
        logger.info(
            f"[网络] 并发调用合并进进行中的park（{session.wait.id}，"
            f"merged={session.wait.merged_calls}）"
        )
        _persist_state()  # merged_calls变化同步快照
        await session.done.wait()
        return session.outcome

    wait = NetworkWait(
        id=f"nw-{uuid.uuid4().hex[:12]}",
        message=network_message(exc),
        turn_id=turn.turn_id if turn is not None else None,
    )
    _waits[wait.id] = wait
    _stats["total_parks"] += 1
    _persist_state()  # HITL：park开始即写快照，前端卡片/观测面立刻可见
    logger.warning(
        f"[网络] 离线park开始（{wait.message}），每{POLL_S:.0f}s竞速探测，"
        f"上限{MAX_WAIT_S:.0f}s（{wait.id}）"
    )

    session = _ParkSession(wait=wait)
    _active_park = session
    try:
        return await _run_park(session, cancel_event, turn)
    finally:
        session.done.set()
        if _active_park is session:
            _active_park = None


async def _run_park(session: _ParkSession, cancel_event, turn) -> str:
    """单个park会话的watch()/restore()/resume()主循环（kilocode语义逐字保留）。

    返回前必设session.outcome（attach并发调用据此共享同一三态）；ABORTED路径
    同时把turn预算标记spent（同turn后续调用fail-fast不再叠加等待）。
    """
    wait = session.wait
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
            session.outcome = ABORTED
            if turn is not None:
                turn.aborted = True
            logger.warning(f"[网络] 离线park被取消（{wait.id}）")
            _persist_state()
            return ABORTED
        # HITL人工拒绝（kilocode拒绝按钮语义）：每轮询消费一次跨进程拒绝标志，
        # 命中即ABORTED（error=human_rejected），不再等满MAX_WAIT_S
        if _consume_reject(wait.id):
            wait.state = ABORTED
            wait.error = "human_rejected"
            _stats["aborted"] += 1
            _stats["human_rejected"] += 1
            session.outcome = ABORTED
            if turn is not None:
                turn.aborted = True
            logger.warning(f"[网络] 离线park被人工拒绝（{wait.id}）")
            _persist_state()
            return ABORTED
        await _sleep(POLL_S)
        polls += 1
        if not await probe_online():
            continue
        # restore()：标记恢复+事件；resume()：10s宽限后自动续跑（人工拒绝窗口）
        wait.restored = time.time()
        _persist_state()  # restored时间戳同步快照（前端卡片进入"已恢复宽限期"观感）
        logger.info(f"[网络] 网络恢复（{wait.id}），{RESUME_GRACE_S:.0f}s宽限后自动续跑")
        await _sleep(RESUME_GRACE_S)
        if cancel_event is not None and cancel_event.is_set():
            wait.state = ABORTED
            wait.error = "cancelled during grace"
            _stats["aborted"] += 1
            session.outcome = ABORTED
            if turn is not None:
                turn.aborted = True
            logger.warning(f"[网络] 宽限期内被取消（{wait.id}）")
            _persist_state()
            return ABORTED
        # HITL：宽限期=kilocode人工拒绝窗口本体——窗口内人工拒绝 > 自动续跑
        if _consume_reject(wait.id):
            wait.state = ABORTED
            wait.error = "human_rejected"
            _stats["aborted"] += 1
            _stats["human_rejected"] += 1
            session.outcome = ABORTED
            if turn is not None:
                turn.aborted = True
            logger.warning(f"[网络] 宽限期内人工拒绝，不自动续跑（{wait.id}）")
            _persist_state()
            return ABORTED
        wait.state = RESUMED
        _stats["resumed"] += 1
        session.outcome = RESUMED
        logger.info(f"[网络] 离线park恢复续跑（{wait.id}，轮询{polls}次）")
        _persist_state()
        await _run_restore_callbacks()
        return RESUMED

    wait.state = ABORTED
    wait.error = f"timeout after {MAX_WAIT_S:.0f}s"
    _stats["aborted"] += 1
    session.outcome = ABORTED
    if turn is not None:
        turn.aborted = True
    logger.warning(f"[网络] 离线park超时放弃（{wait.id}，{MAX_WAIT_S:.0f}s仍离线）")
    _persist_state()
    return ABORTED
