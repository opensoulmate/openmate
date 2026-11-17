# -*- coding: utf-8 -*-
"""reflex.py — ⚡Reflex 反射层：事件驱动快速应激（事件总线 + 反射弧）。

原始设计四条（照此实现，禁走样）：
① 外部事件/信号/用户动作/MCP 调用/前端 Electron 上报事件的**即时捕获**；
② 轻量判断/路由/安全拦截/快速回调，**不进重型 LLM 主循环**（本模块零 LLM 调用）；
③ 高优先突发信号：窗口变化/快捷键/报错/工具返回/权限校验/异常熔断；
④ 反射弧语义：**来信号立刻反应**（capture → 轻量规则 → 回调/路由/拦截，同步短路径）。

分工铁律：Reflex 管前台应激（快、短路径）；Pulse（pulse.py）管后台自治（持续、
保活）。与既有件整合勿平行造轮子：
- 事件词汇复用 agent/event_bus.EventPriority；可挂既有 EventBus 观测面（旁路
  转发，never-raise）；
- 中断/插话语义复用 agent/steering.SteeringQueue.is_abort（不另立 abort 词汇）；
- 工具返回/权限拦截/报错事件由 soulmate_agent 工具循环钩子与 ws_acp 事件源喂入。

反射弧结构：ReflexEvent（捕获）→ ReflexRule 序列（轻量规则，按优先级）→
终态 ∈ {pass（无规则命中）, handled（快速回调已执行）, routed（路由到目标）,
blocked（安全拦截短路——首个拦截即终态）}。异常熔断：单规则回调连续失败达阈值
→ 熔断开路（冷却期跳过，half-open 试探单次），宿主 emit **永不抛异常**；突发
风暴护栏：同类事件滑窗超限显式丢弃计数（安全类事件豁免，绝不丢）。

独立可测（tests/test_reflex.py）；零 LLM 依赖（测试断言源码无 llm 导入）。
"""
from __future__ import annotations

import inspect
import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from agent.event_bus import EventPriority
from agent.steering import SteeringQueue

logger = logging.getLogger("acp-agent.reflex")

__all__ = [
    "EVENT_USER_ACTION", "EVENT_INTERRUPT", "EVENT_FRONTEND", "EVENT_WINDOW_CHANGE",
    "EVENT_HOTKEY", "EVENT_MCP_CALL", "EVENT_TOOL_RETURN", "EVENT_PERMISSION",
    "EVENT_SANDBOX_BLOCK", "EVENT_ERROR", "EVENT_CIRCUIT_OPEN", "EVENT_SIGNAL",
    "ACTION_CALLBACK", "ACTION_ROUTE", "ACTION_BLOCK",
    "VERDICT_PASS", "VERDICT_HANDLED", "VERDICT_ROUTED", "VERDICT_BLOCKED",
    "VERDICT_DROPPED",
    "ReflexEvent", "ReflexAction", "ReflexResponse", "ReflexRule", "ReflexHub",
    "get_reflex", "reset_reflex",
]

# ── 事件种类（③ 高优先突发信号全集）────────────────────────────────
EVENT_USER_ACTION = "user.action"            # 用户动作（prompt/答复）
EVENT_INTERRUPT = "user.interrupt"           # 快捷键式中断（cancel/abort）
EVENT_FRONTEND = "frontend.event"            # 前端 Electron 上报事件
EVENT_WINDOW_CHANGE = "frontend.window"      # 窗口变化
EVENT_HOTKEY = "frontend.hotkey"             # 快捷键
EVENT_MCP_CALL = "mcp.call"                  # MCP 调用
EVENT_TOOL_RETURN = "tool.return"            # 工具返回
EVENT_PERMISSION = "permission.check"        # 权限校验/拦截
EVENT_SANDBOX_BLOCK = "permission.sandbox"   # 沙箱拦截
EVENT_ERROR = "error"                        # 报错
EVENT_CIRCUIT_OPEN = "circuit.open"          # 异常熔断（自我观察信号）
EVENT_SIGNAL = "signal"                      # 通用外部信号

# 高优先突发（进反射弧按 CRITICAL/HIGH 处理）
BURST_KINDS = {
    EVENT_WINDOW_CHANGE, EVENT_HOTKEY, EVENT_ERROR,
    EVENT_TOOL_RETURN, EVENT_PERMISSION, EVENT_SANDBOX_BLOCK, EVENT_CIRCUIT_OPEN,
}
# 安全类事件永不丢弃（风暴护栏豁免：拦截路径必须完整留痕）
NEVER_DROP_KINDS = {
    EVENT_PERMISSION, EVENT_SANDBOX_BLOCK, EVENT_ERROR, EVENT_CIRCUIT_OPEN,
}

# ── 规则动作 / 响应终态 ────────────────────────────────────────────
ACTION_CALLBACK = "callback"   # 快速回调（立刻执行轻量副作用）
ACTION_ROUTE = "route"         # 路由（指出去向：steering/permission/...）
ACTION_BLOCK = "block"         # 安全拦截（短路，首个拦截即终态）

VERDICT_PASS = "pass"          # 无规则命中（仅捕获计数）
VERDICT_HANDLED = "handled"    # 快速回调已执行
VERDICT_ROUTED = "routed"      # 已路由
VERDICT_BLOCKED = "blocked"    # 已拦截
VERDICT_DROPPED = "dropped"    # 风暴护栏显式丢弃

DEFAULT_STORM_WINDOW_S = 10.0
DEFAULT_STORM_MAX = 200        # 每类每窗上限（突发信号护栏）
DEFAULT_CIRCUIT_FAILURES = 3   # 同规则连续失败 N 次开路
DEFAULT_CIRCUIT_COOLDOWN_S = 60.0
DEFAULT_ERROR_STORM = 5        # 同签名报错 N 次/窗 → 异常熔断记录
HISTORY_LIMIT = 500


@dataclass
class ReflexEvent:
    """一条被即时捕获的信号（反射弧输入）。"""
    kind: str
    source: str = ""                  # ws_acp / tool_loop / mcp / ...
    session_id: str = ""
    payload: dict = field(default_factory=dict)
    text: str = ""                    # 文本预览（is_abort 等轻量判断用）
    priority: int = EventPriority.NORMAL
    ts: float = field(default_factory=time.time)
    event_id: str = field(default_factory=lambda: f"rfx_{uuid.uuid4().hex[:12]}")

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id, "kind": self.kind, "source": self.source,
            "session_id": self.session_id, "payload": self.payload,
            "text": self.text[:120], "priority": int(self.priority),
            "ts": self.ts,
        }


@dataclass
class ReflexAction:
    """单条规则的快速产物（回调结果/路由去向/拦截原因）。"""
    rule: str
    action: str
    ok: bool = True
    result: Any = None
    error: str = ""
    circuit: str = ""     # "" / "open" / "half_open"

    def as_dict(self) -> dict:
        return {"rule": self.rule, "action": self.action, "ok": self.ok,
                "result": self.result, "error": self.error,
                **({"circuit": self.circuit} if self.circuit else {})}


@dataclass
class ReflexResponse:
    """反射弧响应：来信号立刻反应的产物（同步返回，零 LLM）。"""
    event: ReflexEvent
    verdict: str = VERDICT_PASS
    blocked: bool = False
    reason: str = ""
    actions: list = field(default_factory=list)
    dropped: bool = False
    latency_ms: float = 0.0

    def as_dict(self) -> dict:
        return {
            "event_id": self.event.event_id, "kind": self.event.kind,
            "verdict": self.verdict, "blocked": self.blocked,
            "reason": self.reason, "dropped": self.dropped,
            "latency_ms": round(self.latency_ms, 3),
            "text": self.event.text[:80],
            "payload": self.event.payload,
            "actions": [a.as_dict() for a in self.actions],
        }


class _Circuit:
    """单规则异常熔断（连续失败开路 → 冷却 → half-open 单次试探）。"""

    def __init__(self, max_failures: int, cooldown_s: float):
        self.max_failures = int(max_failures)
        self.cooldown_s = float(cooldown_s)
        self.failures = 0
        self.opened_at: float | None = None
        self.trips = 0

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        return "open"

    def allow(self, now: float) -> tuple[bool, str]:
        """(是否执行, circuit 标记)。"""
        if self.opened_at is None:
            return True, ""
        if now - self.opened_at >= self.cooldown_s:
            return True, "half_open"      # 试探单次
        return False, "open"

    def record_ok(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_fail(self, now: float) -> None:
        self.failures += 1
        if self.failures >= self.max_failures and self.opened_at is None:
            self.opened_at = now
            self.trips += 1


class ReflexRule:
    """轻量规则：match（谓词/种类）→ handler（同步快速回调，零 LLM）。"""

    def __init__(self, name: str, match: Callable | str | tuple | list,
                 handler: Callable[["ReflexEvent"], Any],
                 action: str = ACTION_CALLBACK, priority: int = 0,
                 max_failures: int = DEFAULT_CIRCUIT_FAILURES,
                 cooldown_s: float = DEFAULT_CIRCUIT_COOLDOWN_S):
        if action not in (ACTION_CALLBACK, ACTION_ROUTE, ACTION_BLOCK):
            raise ValueError(f"action 非法: {action!r}")
        self.name = str(name)
        self._match = match
        self.handler = handler
        self.action = action
        self.priority = int(priority)
        self.enabled = True
        self.circuit = _Circuit(max_failures, cooldown_s)
        self.calls = 0
        self.failures = 0

    def matches(self, event: ReflexEvent) -> bool:
        if not self.enabled:
            return False
        m = self._match
        if callable(m):
            try:
                return bool(m(event))
            except Exception as e:  # 谓词异常=不命中（轻量判断禁炸宿主）
                logger.warning(f"[reflex] rule {self.name} match error: {e}")
                return False
        if isinstance(m, str):
            return event.kind == m
        return event.kind in tuple(m)


class ReflexHub:
    """事件总线 + 反射弧：capture → 轻量规则 → 回调/路由/拦截（同步短路径）。

    纪律：
    - emit/capture **永不抛异常**（反射层故障绝不破坏宿主循环）；
    - 零 LLM 调用（本模块只做匹配/计数/回调/路由/拦截）；
    - 规则回调异常→异常熔断（连续失败开路冷却，half-open 试探单次）；
    - 突发风暴护栏：同类事件滑窗超限显式丢弃计数（NEVER_DROP_KINDS 豁免）；
    - 可挂既有 event_bus.EventBus 观测面（fire-and-forget，never-raise）。
    """

    def __init__(self, storm_window_s: float = DEFAULT_STORM_WINDOW_S,
                 storm_max: int = DEFAULT_STORM_MAX,
                 error_storm_threshold: int = DEFAULT_ERROR_STORM,
                 now=None, with_default_rules: bool = True):
        self._now = now or time.time
        self._lock = threading.RLock()   # 熔断记录可嵌套于风暴观察（可重入）
        self._rules: list[ReflexRule] = []
        self._history: deque[dict] = deque(maxlen=HISTORY_LIMIT)
        self._storm_window_s = float(storm_window_s)
        self._storm_max = int(storm_max)
        self._storm_seen: dict[str, deque] = {}
        self._dropped_by_kind: dict[str, int] = {}
        self._error_storm_threshold = int(error_storm_threshold)
        self._error_sigs: deque = deque(maxlen=200)
        self._error_storms = 0
        self._interceptions = 0
        self._event_bus = None
        self._stats = {
            "total": 0, "by_kind": {}, "by_verdict": {},
            "callbacks": 0, "routed": 0, "blocked": 0, "dropped": 0,
            "latency_ms_max": 0.0, "latency_ms_sum": 0.0,
        }
        if with_default_rules:
            self._install_default_rules()

    # ── 默认规则（零配置开箱：路由/拦截/中断语义复用既有件） ─────────

    def _install_default_rules(self) -> None:
        # ① 路由：中断/abort → steering 语义复用（SteeringQueue.is_abort 单一真源）
        self.add_rule(
            "steer_abort_route",
            lambda e: e.kind == EVENT_INTERRUPT
            or (e.kind == EVENT_USER_ACTION and SteeringQueue.is_abort(e.text)),
            lambda e: {"target": "steering.abort", "text": e.text[:80]},
            action=ACTION_ROUTE, priority=50,
        )
        # ② 安全拦截路径：权限校验/沙箱拦截事件即终态（首个拦截即短路）
        self.add_rule(
            "permission_intercept",
            (EVENT_PERMISSION, EVENT_SANDBOX_BLOCK),
            lambda e: {"target": "permission.interception",
                       "reason": e.payload.get("reason", "") or e.text
                       or "permission interception",
                       "behavior": e.payload.get("behavior", ""),
                       "tool": e.payload.get("tool", "")},
            action=ACTION_BLOCK, priority=100,   # 安全规则最先跑
        )

    # ── 规则管理 ────────────────────────────────────────────────────

    def add_rule(self, name: str, match, handler, action: str = ACTION_CALLBACK,
                 priority: int = 0, **kw) -> ReflexRule:
        rule = ReflexRule(name, match, handler, action=action,
                          priority=priority, **kw)
        with self._lock:
            self._rules = [r for r in self._rules if r.name != rule.name]
            self._rules.append(rule)
            self._rules.sort(key=lambda r: -r.priority)
        return rule

    def remove_rule(self, name: str) -> bool:
        with self._lock:
            before = len(self._rules)
            self._rules = [r for r in self._rules if r.name != name]
            return len(self._rules) != before

    def enable_rule(self, name: str, enabled: bool = True) -> bool:
        with self._lock:
            for r in self._rules:
                if r.name == name:
                    r.enabled = bool(enabled)
                    return True
        return False

    def rule_names(self) -> list[str]:
        with self._lock:
            return [r.name for r in self._rules]

    # ── 反射弧主入口（同步、立刻反应、零 LLM、永不抛异常） ───────────

    def capture(self, event: ReflexEvent) -> ReflexResponse:
        t0 = time.perf_counter()
        try:
            resp = self._capture_inner(event)
        except Exception as e:  # 防御底线：反射层自身 bug 也不许穿透宿主
            logger.error(f"[reflex] capture 内部异常(已吞): {type(e).__name__}: {e}")
            resp = ReflexResponse(event=event, verdict=VERDICT_PASS,
                                  reason=f"reflex-internal-error: {e}")
        resp.latency_ms = (time.perf_counter() - t0) * 1000.0
        with self._lock:
            self._stats["total"] += 1
            self._stats["by_kind"][event.kind] = \
                self._stats["by_kind"].get(event.kind, 0) + 1
            self._stats["by_verdict"][resp.verdict] = \
                self._stats["by_verdict"].get(resp.verdict, 0) + 1
            self._stats["latency_ms_sum"] += resp.latency_ms
            if resp.latency_ms > self._stats["latency_ms_max"]:
                self._stats["latency_ms_max"] = resp.latency_ms
            self._history.append(resp.as_dict())
        self._forward_event_bus(event, resp)
        return resp

    def emit(self, kind: str, *, source: str = "", session_id: str = "",
             payload: dict | None = None, text: str = "",
             priority: int | None = None, **payload_kw) -> ReflexResponse:
        """快捷捕获（来信号立刻反应）。多余关键字参数并入 payload。"""
        if priority is None:
            priority = (EventPriority.CRITICAL if kind == EVENT_INTERRUPT
                        else EventPriority.HIGH if kind in BURST_KINDS
                        else EventPriority.NORMAL)
        data = dict(payload or {})
        data.update(payload_kw)
        return self.capture(ReflexEvent(
            kind=kind, source=source, session_id=session_id,
            payload=data, text=str(text or ""),
            priority=int(priority)))

    def _capture_inner(self, event: ReflexEvent) -> ReflexResponse:
        # ③ 报错异常熔断观察：同签名报错风暴 → 记 circuit.open（自我观察，不递归）
        if event.kind == EVENT_ERROR:
            self._watch_error_storm(event)
        # 突发风暴护栏（安全类豁免，绝不丢）
        if self._storm_drop(event):
            with self._lock:
                self._stats["dropped"] += 1
                self._dropped_by_kind[event.kind] = \
                    self._dropped_by_kind.get(event.kind, 0) + 1
            return ReflexResponse(event=event, verdict=VERDICT_DROPPED,
                                  dropped=True,
                                  reason="storm-guard 显式丢弃（突发超限）")
        resp = ReflexResponse(event=event)
        with self._lock:
            rules = list(self._rules)
        now = self._now()
        for rule in rules:
            if not rule.matches(event):
                continue
            allow, circuit_flag = rule.circuit.allow(now)
            if not allow:
                resp.actions.append(ReflexAction(
                    rule=rule.name, action=rule.action, ok=False,
                    circuit="open", error="circuit-open: 冷却期跳过（异常熔断）"))
                continue
            rule.calls += 1
            try:
                result = rule.handler(event)
                if inspect.isawaitable(result):
                    # 反射弧=同步短路径：异步回调不在此等待，fire-and-forget 丢弃
                    logger.warning(f"[reflex] rule {rule.name} 返回协程——反射弧只收"
                                   "同步快速回调，协程被丢弃（勿把重活塞进反射层）")
                    result = None
                rule.circuit.record_ok()
                resp.actions.append(ReflexAction(
                    rule=rule.name, action=rule.action, ok=True,
                    result=result, circuit=circuit_flag))
            except Exception as e:
                rule.failures += 1
                rule.circuit.record_fail(now)
                tripped = rule.circuit.state == "open" and rule.circuit.trips > 0 \
                    and rule.circuit.failures >= rule.circuit.max_failures
                resp.actions.append(ReflexAction(
                    rule=rule.name, action=rule.action, ok=False,
                    error=f"{type(e).__name__}: {e}",
                    circuit=circuit_flag or ("open" if tripped else "")))
                logger.warning(f"[reflex] rule {rule.name} 失败"
                               f"（{rule.circuit.state}）: {type(e).__name__}: {e}")
                if tripped:
                    self._record_circuit_trip(f"rule:{rule.name}", str(e))
            if rule.action == ACTION_BLOCK:
                # ④ 安全拦截短路：首个拦截即终态
                resp.blocked = True
                _res = resp.actions[-1].result
                _reason = (_res.get("reason") if isinstance(_res, dict) else _res) \
                    or f"blocked by {rule.name}"
                resp.reason = str(_reason)
                break
        # 终态
        if resp.blocked:
            resp.verdict = VERDICT_BLOCKED
            with self._lock:
                self._stats["blocked"] += 1
        elif any(a.action == ACTION_ROUTE and a.ok for a in resp.actions):
            resp.verdict = VERDICT_ROUTED
            with self._lock:
                self._stats["routed"] += 1
        elif any(a.action == ACTION_CALLBACK and a.ok for a in resp.actions):
            resp.verdict = VERDICT_HANDLED
            with self._lock:
                self._stats["callbacks"] += 1
        return resp

    # ── 异常熔断 / 风暴护栏 ─────────────────────────────────────────

    def _record_circuit_trip(self, subject: str, detail: str) -> None:
        with self._lock:
            self._stats["by_kind"][EVENT_CIRCUIT_OPEN] = \
                self._stats["by_kind"].get(EVENT_CIRCUIT_OPEN, 0) + 1
            self._history.append({
                "event_id": f"rfx_{uuid.uuid4().hex[:12]}",
                "kind": EVENT_CIRCUIT_OPEN, "verdict": "circuit",
                "reason": f"异常熔断开路: {subject} — {detail}"[:200],
                "ts": self._now(),
            })

    def _watch_error_storm(self, event: ReflexEvent) -> None:
        sig = f"{event.kind}:{event.payload.get('error', event.text)[:80]}"
        now = self._now()
        with self._lock:
            self._error_sigs.append((now, sig))
            recent = [s for t, s in self._error_sigs
                      if now - t <= self._storm_window_s and s == sig]
            if len(recent) >= self._error_storm_threshold:
                self._error_storms += 1
                # 每窗只记一次熔断（首达阈值那一刻）
                if len(recent) == self._error_storm_threshold:
                    self._record_circuit_trip(f"error-storm:{sig}",
                                              f"{len(recent)} 次/{self._storm_window_s}s")

    def _storm_drop(self, event: ReflexEvent) -> bool:
        if event.kind in NEVER_DROP_KINDS:
            return False
        now = self._now()
        with self._lock:
            q = self._storm_seen.setdefault(event.kind, deque())
            q.append(now)
            while q and now - q[0] > self._storm_window_s:
                q.popleft()
            return len(q) > self._storm_max

    # ── 既有件整合：event_bus 观测面（旁路，never-raise） ───────────

    def attach_event_bus(self, bus) -> None:
        self._event_bus = bus

    def _forward_event_bus(self, event: ReflexEvent, resp: ReflexResponse) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return   # 无运行中事件循环：只留本层历史（不阻塞同步路径）
        try:
            loop.create_task(bus.emit(f"reflex.{event.kind}", resp.as_dict(),
                                      source="reflex"))
        except Exception as e:
            logger.debug(f"[reflex] event_bus 转发失败(非致命): {e}")

    # ── 事件源捕获助手（soulmate_agent 工具钩子 / ws_acp 桥 / MCP） ──

    def capture_ws_message(self, direction: str, raw,
                           source: str = "ws_acp") -> ReflexResponse | None:
        """/ws_acp 事件源：in=客户端→代理（用户动作/快捷键/窗口变化/Electron 上报），
        out=代理→客户端（工具返回/报错）。轻量分类→立刻反应，转发内容零改动。"""
        msg = raw
        if isinstance(raw, (str, bytes)):
            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                return None
        if not isinstance(msg, dict):
            return None
        method = str(msg.get("method") or "")
        _p = msg.get("params")
        params: dict = _p if isinstance(_p, dict) else {}
        if direction == "in":
            if method == "session/cancel":
                return self.emit(EVENT_INTERRUPT, source=source,
                                 session_id=str(params.get("sessionId") or ""),
                                 text=str(params.get("reason") or "cancel"))
            text = ""
            if isinstance(params.get("prompt"), str):
                text = params["prompt"]
            elif isinstance(params.get("prompt"), list):
                for _blk in params["prompt"]:   # ACP prompt parts: [{type:text,text}]
                    if isinstance(_blk, dict):
                        text += str(_blk.get("text") or "")
                    elif isinstance(_blk, str):
                        text += _blk
            elif isinstance(params.get("text"), str):
                text = params["text"]
            if method in ("session/prompt", "session/new"):
                if text and SteeringQueue.is_abort(text):
                    return self.emit(EVENT_INTERRUPT, source=source,
                                     session_id=str(params.get("sessionId") or ""),
                                     text=text)
                return self.emit(EVENT_USER_ACTION, source=source,
                                 session_id=str(params.get("sessionId") or ""),
                                 text=text, payload={"method": method})
            kind_param = str(params.get("kind") or params.get("type") or "").lower()
            if "window" in kind_param or "focus" in kind_param or "resize" in kind_param:
                return self.emit(EVENT_WINDOW_CHANGE, source=source,
                                 payload={"kind": kind_param, **_safe_payload(params)})
            if "hotkey" in kind_param or "shortcut" in kind_param or "key" in kind_param:
                return self.emit(EVENT_HOTKEY, source=source,
                                 payload={"kind": kind_param, **_safe_payload(params)})
            if method.startswith("ext/") or "notification" in method \
                    or "electron" in method.lower() or "telemetry" in method.lower():
                return self.emit(EVENT_FRONTEND, source=source, text=text,
                                 payload={"method": method, **_safe_payload(params)})
            return self.emit(EVENT_SIGNAL, source=source, text=text,
                             payload={"method": method, "direction": "in"})
        # direction == "out"（代理→客户端）
        if "error" in msg:
            err = msg.get("error") or {}
            return self.emit(EVENT_ERROR, source=source,
                             payload={"code": err.get("code"),
                                      "error": str(err.get("message") or "")[:200]})
        upd = params.get("sessionUpdate") or params.get("kind") or ""
        if "tool_call" in str(upd):
            return self.emit(EVENT_TOOL_RETURN, source=source,
                             session_id=str(params.get("sessionId") or ""),
                             payload={"update": str(upd),
                                      "tool": str((params.get("toolCall") or {})
                                                  .get("title") or "")
                                      if isinstance(params.get("toolCall"), dict) else ""})
        return self.emit(EVENT_SIGNAL, source=source,
                         payload={"direction": "out", "update": str(upd)[:60]})

    def on_tool_return(self, session_id: str = "", tool_name: str = "",
                       ok: bool = True, result_preview: str = "",
                       duration_ms: float = 0.0, error: str = "",
                       source: str = "tool_loop") -> ReflexResponse:
        """工具返回钩子（soulmate_agent 工具循环喂入，高优先突发）。"""
        return self.emit(EVENT_TOOL_RETURN, source=source, session_id=session_id,
                         text=tool_name,
                         payload={"tool": tool_name, "ok": bool(ok),
                                  "duration_ms": round(float(duration_ms), 2),
                                  "result_preview": str(result_preview)[:200],
                                  "error": str(error)[:200]})

    def on_permission_blocked(self, session_id: str = "", tool_name: str = "",
                              reason: str = "", behavior: str = "",
                              source: str = "permission_gate") -> ReflexResponse:
        """权限拦截路径（安全拦截即时捕获）。"""
        return self.emit(EVENT_PERMISSION, source=source, session_id=session_id,
                         text=reason,
                         payload={"tool": tool_name, "reason": reason[:200],
                                  "behavior": behavior, "blocked": True})

    def on_sandbox_blocked(self, session_id: str = "", tool_name: str = "",
                           reason: str = "",
                           source: str = "sandbox_policy") -> ReflexResponse:
        """沙箱拦截路径（网络受限等兜底拦截即时捕获）。"""
        return self.emit(EVENT_SANDBOX_BLOCK, source=source, session_id=session_id,
                         text=reason,
                         payload={"tool": tool_name, "reason": reason[:200],
                                  "blocked": True})

    def on_mcp_call(self, tool_name: str = "", session_id: str = "",
                    source: str = "mcp") -> ReflexResponse:
        """MCP 调用即时捕获（① 事件源之一）。"""
        return self.emit(EVENT_MCP_CALL, source=source, session_id=session_id,
                         text=tool_name, payload={"tool": tool_name})

    def on_user_action(self, session_id: str = "", text: str = "",
                       source: str = "agent") -> ReflexResponse:
        """用户动作即时捕获（abort 文本升级为中断信号，路由 steering 语义）。"""
        if text and SteeringQueue.is_abort(text):
            return self.emit(EVENT_INTERRUPT, source=source,
                             session_id=session_id, text=text)
        return self.emit(EVENT_USER_ACTION, source=source, session_id=session_id,
                         text=text)

    def on_error(self, error: str = "", where: str = "", session_id: str = "",
                 source: str = "agent") -> ReflexResponse:
        """报错信号即时捕获（同签名风暴→异常熔断记录）。"""
        return self.emit(EVENT_ERROR, source=source, session_id=session_id,
                         text=str(error)[:200],
                         payload={"error": str(error)[:200], "where": where})

    def on_frontend_event(self, kind: str = EVENT_FRONTEND, payload: dict | None = None,
                          session_id: str = "",
                          source: str = "electron") -> ReflexResponse:
        """前端 Electron 上报事件即时捕获（窗口变化/快捷键等子类传 kind）。"""
        return self.emit(kind, source=source, session_id=session_id,
                         payload=dict(payload or {}))

    # ── 观测 ────────────────────────────────────────────────────────

    def stats(self) -> dict:
        with self._lock:
            total = self._stats["total"]
            st = {
                "total": total,
                "by_kind": dict(self._stats["by_kind"]),
                "by_verdict": dict(self._stats["by_verdict"]),
                "callbacks": self._stats["callbacks"],
                "routed": self._stats["routed"],
                "blocked": self._stats["blocked"],
                "dropped": self._stats["dropped"],
                "dropped_by_kind": dict(self._dropped_by_kind),
                "error_storms": self._error_storms,
                "avg_latency_ms": round(
                    self._stats["latency_ms_sum"] / total, 3) if total else 0.0,
                "max_latency_ms": round(self._stats["latency_ms_max"], 3),
                "rules": [
                    {"name": r.name, "action": r.action, "enabled": r.enabled,
                     "priority": r.priority, "calls": r.calls,
                     "failures": r.failures, "circuit": r.circuit.state,
                     "circuit_trips": r.circuit.trips}
                    for r in self._rules
                ],
            }
        return st

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            return list(self._history)[-max(1, int(limit)):]


def _safe_payload(params: dict) -> dict:
    """前端上报 payload 只留浅层标量（轻量，防大对象进反射层）。"""
    out = {}
    for k, v in list(params.items())[:12]:
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[str(k)[:40]] = v if not isinstance(v, str) else v[:120]
    return out


# ── 进程级共享 hub（agent 子进程与 /ws_acp 事件源同进程内共享） ──────
_global_reflex: Optional[ReflexHub] = None
_global_lock = threading.Lock()


def get_reflex() -> ReflexHub:
    global _global_reflex
    with _global_lock:
        if _global_reflex is None:
            _global_reflex = ReflexHub()
        return _global_reflex


def reset_reflex() -> None:
    """测试隔离用：丢弃进程级 hub。"""
    global _global_reflex
    with _global_lock:
        _global_reflex = None
