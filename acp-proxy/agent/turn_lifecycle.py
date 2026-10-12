# -*- coding: utf-8 -*-
"""kilocode supplement3 #7 Turn生命周期事件 + #10 记忆事件总线。

调研来源：kilocode-source-supplement3.md
- #7（turn.ts）："bus订阅TurnOpen/TurnClose事件驱动记忆采集；superseded→按interrupted
  处理（被排队消息顶掉的turn=被中断，不完整不digest）；订阅器失败永不破坏宿主会话流"
- #10（events.ts）："memory.status/updated/error三事件+best-effort sink（无实例上下文
  就丢弃不报错）"

此前状态：记忆采集（回合digest /ltm/add）内联在 _prompt_inner 后处理块里——
不管turn是正常完成、被插话中断还是被取消，只要走到底就digest；被中断turn的
不完整内容照样蒸馏进长期记忆（kilocode明令禁止）。turn边界没有任何生命周期事件，
前端/其他订阅者无从感知"现在在跑哪个turn、怎么结束的"。

本模块：
1. TurnLifecycleBus：turn.open / turn.close 生命周期事件总线——订阅器逐个隔离
   （单个订阅者抛异常只记日志，永不破坏宿主会话流）；close_reason 语义分级：
   completed / interrupted / superseded / cancelled / error。
2. memory_close_view()：**superseded→按interrupted处理**（kilocode原文语义）——
   被排队消息顶掉的turn与被中断的turn同等对待：不完整，不进记忆。
3. MemoryDigestCollector：TurnClose订阅者=事件驱动记忆采集。只有
   memory_close_view(close_reason)=="completed" 才digest；缓存命中回合无新信息
   不digest；digest复用 memory_echo.build_digest_payload（echo_guard显式）+
   is_echo_blocked（跳过必须可见，mem0禁止静默降级）。
4. emit_memory_event()：memory.status / memory.updated / memory.error 三事件，
   best-effort sink到全局eventbus（eventbus不可用/发布异常→丢弃不报错），
   OpenMate前端可订阅 memory/# 渲染记忆活动流。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger("acp.turn_lifecycle")

# ── close_reason 语义分级（kilocode turn.ts close_reason）──
CLOSE_COMPLETED = "completed"
CLOSE_INTERRUPTED = "interrupted"
CLOSE_SUPERSEDED = "superseded"
CLOSE_CANCELLED = "cancelled"
CLOSE_ERROR = "error"

EVENT_TURN_OPEN = "turn.open"
EVENT_TURN_CLOSE = "turn.close"


def memory_close_view(close_reason: str) -> str:
    """记忆采集视角的turn结束原因（kilocode：superseded→按interrupted处理）。

    被排队消息顶掉的turn（superseded）与被中断的turn（interrupted）同等对待：
    内容不完整，不digest。completed 之外的所有原因都视同 interrupted。
    """
    if close_reason == CLOSE_COMPLETED:
        return CLOSE_COMPLETED
    return CLOSE_INTERRUPTED


def should_digest(close_reason: str, meta: dict | None = None) -> bool:
    """TurnClose后是否做记忆采集（digest）。

    规则（kilocode turn.ts + 防回声精神）：
    - 只有完整turn（memory_close_view=="completed"）才digest
    - 缓存命中回合（meta["cached"]）无新信息不digest
    - 长度过滤沿用旧内联逻辑：user_text>50 或 full_response>100
    """
    meta = meta or {}
    if memory_close_view(close_reason) != CLOSE_COMPLETED:
        return False
    if meta.get("cached"):
        return False
    user_text = str(meta.get("user_text") or "")
    full_response = str(meta.get("full_response") or "")
    return len(user_text) > 50 or len(full_response) > 100


@dataclass
class TurnRecord:
    """一个turn的生命周期记录。"""

    session_id: str
    turn_id: str = field(default_factory=lambda: f"turn_{uuid.uuid4().hex[:12]}")
    opened_at: float = field(default_factory=time.time)
    closed_at: float | None = None
    close_reason: str | None = None
    meta: dict = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.closed_at is None

    def as_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "close_reason": self.close_reason,
            "memory_view": memory_close_view(self.close_reason or CLOSE_COMPLETED),
            "meta": {k: v for k, v in self.meta.items() if not k.startswith("_")},
        }


# 订阅器形态：同步或异步 callable(record) -> Any
TurnSubscriber = Callable[[TurnRecord], Any]


class TurnLifecycleBus:
    """TurnOpen/TurnClose 生命周期事件总线（kilocode turn.ts bus订阅形态）。

    - subscribe(event, cb)：event ∈ {turn.open, turn.close}，返回self可链式
    - open_turn / aclose_turn：触发订阅器，**逐个隔离**——单订阅器异常只记日志
      （kilocode："订阅器失败永不破坏宿主会话流"）
    - cancel_turn(session_id)：ACP cancel()语义——当前turn收尾时降级为cancelled
    - 重复close幂等：无活跃turn时close只记日志返回None（防错误路径二次关闭）
    - 生命周期事件best-effort转发到全局eventbus（agent/turn/#）供前端订阅
    """

    def __init__(self):
        self._subs: dict[str, list[TurnSubscriber]] = {
            EVENT_TURN_OPEN: [],
            EVENT_TURN_CLOSE: [],
        }
        self._active: dict[str, TurnRecord] = {}  # session_id -> 当前turn
        self._cancel_requested: set[str] = set()
        self._history: list[TurnRecord] = []  # 最近结束的turn（观测用，环形上限）
        self._history_limit = 50

    # ── 订阅 ──
    def subscribe(self, event: str, callback: TurnSubscriber) -> "TurnLifecycleBus":
        if event not in self._subs:
            raise ValueError(f"unknown turn event: {event}")
        self._subs[event].append(callback)
        return self

    # ── turn.open ──
    def open_turn(self, session_id: str, **meta) -> TurnRecord:
        record = TurnRecord(session_id=session_id, meta=dict(meta))
        self._active[session_id] = record
        self._cancel_requested.discard(session_id)
        self._fire(EVENT_TURN_OPEN, record)
        _publish_turn_event("agent/turn/opened", record)
        logger.info(
            f"[turn] open {record.turn_id} session={session_id} "
            f"meta_keys={sorted(meta.keys())}"
        )
        return record

    # ── turn.close（异步：close订阅器可做HTTP类副作用=事件驱动记忆采集）──
    async def aclose_turn(
        self,
        session_id: str,
        close_reason: str = CLOSE_COMPLETED,
        **meta,
    ) -> TurnRecord | None:
        record = self._active.pop(session_id, None)
        if record is None:
            # 幂等：二次close/未open的close不炸（错误路径与正常路径可能都收尾）
            logger.debug(f"[turn] close ignored (no active turn): session={session_id}")
            return None
        # kilocode cancel语义：请求过取消的turn即使跑完也按cancelled收尾
        if session_id in self._cancel_requested and close_reason == CLOSE_COMPLETED:
            close_reason = CLOSE_CANCELLED
        self._cancel_requested.discard(session_id)
        record.closed_at = time.time()
        record.close_reason = close_reason
        record.meta.update(meta)
        self._history.append(record)
        if len(self._history) > self._history_limit:
            self._history = self._history[-self._history_limit:]
        await self._afire(EVENT_TURN_CLOSE, record)
        _publish_turn_event("agent/turn/closed", record)
        logger.info(
            f"[turn] close {record.turn_id} session={session_id} "
            f"reason={close_reason} memory_view={memory_close_view(close_reason)} "
            f"duration={record.closed_at - record.opened_at:.2f}s"
        )
        return record

    def cancel_turn(self, session_id: str) -> bool:
        """ACP cancel()：标记当前turn为取消请求（收尾时close_reason=cancelled）。"""
        if session_id in self._active:
            self._cancel_requested.add(session_id)
            logger.info(f"[turn] cancel requested: session={session_id}")
            return True
        logger.debug(f"[turn] cancel ignored (no active turn): session={session_id}")
        return False

    def is_cancel_requested(self, session_id: str) -> bool:
        """P1-15：回合收口前查取消标记（external_cancel 终止条件落 trace 的裁决输入）。

        aclose_turn 收尾时会消费掉标记，end_turn（trajectory）先于 aclose_turn
        调用——用本方法在收口前读取，不消费。"""
        return session_id in self._cancel_requested

    # ── 观测 ──
    def active(self, session_id: str) -> TurnRecord | None:
        return self._active.get(session_id)

    def recent(self, limit: int = 10) -> list[dict]:
        return [r.as_dict() for r in self._history[-limit:]]

    # ── 订阅器触发（逐个隔离）──
    def _fire(self, event: str, record: TurnRecord) -> None:
        for cb in list(self._subs.get(event, [])):
            try:
                result = cb(record)
                if asyncio.iscoroutine(result):
                    # 同步入口触发的异步订阅器：fire-and-forget，失败只记日志
                    asyncio.ensure_future(_swallow_async(result, cb))
            except Exception as e:
                logger.warning(f"[turn] subscriber {getattr(cb, '__name__', cb)} "
                               f"failed on {event} (host flow unaffected): {e}")

    async def _afire(self, event: str, record: TurnRecord) -> None:
        for cb in list(self._subs.get(event, [])):
            try:
                result = cb(record)
                if asyncio.iscoroutine(result):
                    await _swallow_async(result, cb)
            except Exception as e:
                logger.warning(f"[turn] subscriber {getattr(cb, '__name__', cb)} "
                               f"failed on {event} (host flow unaffected): {e}")


async def _swallow_async(coro: Awaitable, cb: Any) -> None:
    try:
        await coro
    except Exception as e:
        logger.warning(f"[turn] async subscriber {getattr(cb, '__name__', cb)} "
                       f"failed (host flow unaffected): {e}")


# ════════════════════════════════════════════════════════════════
# 记忆事件总线（kilocode supplement3 #10 events.ts）
# ════════════════════════════════════════════════════════════════

MEMORY_EVENT_TOPICS = {
    "status": "memory/status",
    "updated": "memory/updated",
    "error": "memory/error",
}


def emit_memory_event(kind: str, **payload) -> bool:
    """memory.status / memory.updated / memory.error 三事件，best-effort sink。

    kilocode events.ts："best-effort sink（无实例上下文就丢弃不报错）"——
    eventbus不可用/发布异常一律静默丢弃（debug日志），绝不影响记忆采集主流程。
    返回是否成功发布（False=丢弃）。
    """
    topic = MEMORY_EVENT_TOPICS.get(kind)
    if not topic:
        logger.debug(f"[memory-events] unknown event kind, dropped: {kind}")
        return False
    try:
        from eventbus import Event, EventType, get_eventbus

        bus = get_eventbus()
        if bus is None:
            return False
        event = Event(
            event_topic=topic,
            event_type=EventType.NAMESPACE,
            source_service="acp-proxy",
            payload={"kind": kind, **payload},
        )
        bus.publish(event)
        return True
    except Exception as e:
        logger.debug(f"[memory-events] best-effort sink dropped {kind}: {e}")
        return False


def _publish_turn_event(topic: str, record: TurnRecord) -> None:
    """turn生命周期事件best-effort转发到全局eventbus（agent/turn/#）。"""
    try:
        from eventbus import Event, EventType, get_eventbus

        bus = get_eventbus()
        if bus is None:
            return
        bus.publish(Event(
            event_topic=topic,
            event_type=EventType.NAMESPACE,
            source_service="acp-proxy",
            trace_id=record.turn_id,
            payload=record.as_dict(),
        ))
    except Exception as e:
        logger.debug(f"[turn] eventbus publish dropped {topic}: {e}")


# ════════════════════════════════════════════════════════════════
# 事件驱动记忆采集（kilocode turn.ts：bus订阅TurnClose驱动digest）
# ════════════════════════════════════════════════════════════════

LTM_ADD_URL = "http://127.0.0.1:8090/api/hippo/ltm/add"
HTTP_TIMEOUT = 2


class MemoryDigestCollector:
    """TurnClose订阅者：回合digest（事件驱动记忆采集，kilocode turn.ts形态）。

    - 只有完整turn才digest（memory_close_view==completed；superseded/interrupted/
      cancelled/error一律跳过——"被排队消息顶掉的turn=被中断，不完整不digest"）
    - digest载荷复用 memory_echo.build_digest_payload（echo_guard显式True，
      服务端回声二次闸）
    - is_echo_blocked 结果必须可见（mem0：跳过必须可见，不静默）
    - 全程发memory.status/updated/error事件（#10）；失败绝不破坏宿主会话流
    """

    def __init__(self, client_factory: Callable | None = None):
        # client_factory: () -> httpx.AsyncClient（测试注入FakeClient）
        self._client_factory = client_factory

    async def __call__(self, record: TurnRecord) -> dict | None:
        meta = record.meta or {}
        session_id = record.session_id
        reason = record.close_reason or CLOSE_COMPLETED

        if not should_digest(reason, meta):
            emit_memory_event(
                "status",
                session_id=session_id,
                turn_id=record.turn_id,
                state="skipped",
                close_reason=reason,
                memory_view=memory_close_view(reason),
            )
            logger.info(
                f"[turn-memory] digest skipped: turn={record.turn_id} "
                f"reason={reason} cached={bool(meta.get('cached'))}"
            )
            return None

        from agent import memory_echo  # 局部导入防环

        user_text = str(meta.get("user_text") or "")
        full_response = str(meta.get("full_response") or "")
        # kilocode #4 readTurn：记忆不只看"说了什么"，还要看"改了什么"——
        # 快照diff（本轮文件改动）+ 工具动作轮廓（toolSummary）随digest进记忆
        content_parts = [f"用户: {user_text[:200]}", f"助手: {full_response[:200]}"]
        # kilocode #4第4输入源：recent 8轮trace（ports.ts trace(messages, 8)）——
        # 记忆提取看得到近期对话上下文（readTurn输入序 user/assistant/recent）
        _rt = str(meta.get("recent_trace") or "").strip()
        if _rt:
            content_parts.append(f"近期对话（recent trace）:\n{_rt}")
        _fc = str(meta.get("file_changes") or "").strip()
        if _fc:
            content_parts.append(f"本轮文件改动（快照diff）:\n{_fc}")
        _ta = str(meta.get("tool_actions") or "").strip()
        if _ta:
            content_parts.append(f"工具动作（toolSummary）:\n{_ta}")
        payload = memory_echo.build_digest_payload(
            content="\n".join(content_parts),
            memory_type="episodic",
            importance=0.5,
            session_id=session_id,
        )
        emit_memory_event(
            "status",
            session_id=session_id,
            turn_id=record.turn_id,
            state="collecting",
            close_reason=reason,
        )
        try:
            if self._client_factory is not None:
                return await self._post_digest(payload, session_id, record)
            import httpx

            async with httpx.AsyncClient() as client:
                return await self._post_digest(payload, session_id, record, client=client)
        except Exception as e:
            # 失败可见但非致命（mem0：静默降级=失败记忆失真）
            logger.warning(f"[turn-memory] digest failed (non-fatal): {e}")
            emit_memory_event(
                "error",
                session_id=session_id,
                turn_id=record.turn_id,
                error=str(e)[:200],
            )
            return None

    async def _post_digest(
        self, payload: dict, session_id: str, record: TurnRecord, client=None
    ) -> dict | None:
        from agent import memory_echo

        if client is None:
            if self._client_factory is None:
                raise RuntimeError("no http client available for digest")
            client = self._client_factory()
        resp = await client.post(LTM_ADD_URL, json=payload, timeout=HTTP_TIMEOUT)
        try:
            resp_json = resp.json() if resp.status_code == 200 else {}
        except Exception:
            resp_json = {}
        if resp.status_code != 200:
            logger.warning(f"[turn-memory] /ltm/add HTTP {resp.status_code}")
            emit_memory_event(
                "error",
                session_id=session_id,
                turn_id=record.turn_id,
                error=f"ltm_add_http_{resp.status_code}",
            )
            return None
        if memory_echo.is_echo_blocked(resp_json):
            # mem0：跳过必须可见，不静默
            logger.info(
                "[turn-memory] 回声阻断：本轮召回过记忆，digest跳过"
                "（kilocode防自我污染）"
            )
            emit_memory_event(
                "status",
                session_id=session_id,
                turn_id=record.turn_id,
                state="echo_blocked",
            )
            return resp_json
        mem_id = ""
        if isinstance(resp_json, dict):
            mem_id = str(resp_json.get("memory_id") or resp_json.get("id") or "")
        logger.info(f"[turn-memory] digest collected: turn={record.turn_id} "
                    f"memory_id={mem_id or '(n/a)'}")
        emit_memory_event(
            "updated",
            session_id=session_id,
            turn_id=record.turn_id,
            memory_id=mem_id,
        )
        return resp_json
