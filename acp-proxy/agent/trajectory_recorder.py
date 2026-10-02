"""
TrajectoryRecorder — 真实聊天回合 → opensoul trajectory 嵌套span的写入端
=====================================================================

背景（dev-reports 2026-09-17 15:37轮建的langfuse三层观测模型）：opensoul
trajectory_store 有 Trace→Observation→Score 全套API与前端展示面，但真实消息
路径（soulmate _prompt_inner）零生产写入方——trajectory_events生产表恒空，
"我都不知道他们在干嘛"的观察面无数据可看（写了≠接线了）。

本模块是接线端：把每个真实回合写成嵌套span树
    user_input(turn root) ── llm_call(段) ── llm_call(round N) ── tool_call × M
经既有HTTP面（:8090 /api/trajectory/*）落库，前端trajectory页与
GET /sessions/{id}/trace 嵌套树端点即有真实数据。

设计纪律（调研来源）：
- langfuse instrumentation语义（SUMMARY.md P0-4三层模型）：span在真实执行
  路径被记录，事后可回放；
- "记账失败永不阻塞spawn"（CowAgent subagent runner哲学，hippo轮同款）：
  所有方法吞异常返回空值，观察面故障绝不破坏会话流；
- agno §1.4错误风暴熔断：连续同错即停——连续N次失败进入冷却窗口，不给
  一个已死的opensoul每个事件都白付timeout（失败必须可见：状态翻转记warning）；
- mem0 §1.1失败必须可见：熔断进入/退出各记一条warning，不静默。
"""

from __future__ import annotations

import functools
import logging
import time

import httpx

logger = logging.getLogger("acp-proxy.trajectory-recorder")

TRAJECTORY_BASE_URL = "http://127.0.0.1:8090"

# llm_engine把LLM失败作为可见文本chunk回传而非抛异常（"[LLM错误: …]"/
# "[LLM响应超时]"，见llm_engine.py:474等）——结局判定必须认这些失败标记。
LLM_FAILURE_MARKERS = ("[LLM错误", "[LLM响应超时", "推理错误")


def llm_outcome_status(response_text: str) -> str:
    """LLM段/回合结局判定（失败必须可见，mem0 §1.1）。

    live E2E实测抓到的失真：402 insufficient_balance的失败回合，错误以
    "\\n[LLM错误: …]"文本形态成为回复内容，异常不逃逸——若span status无条件
    "ok"，观察面显示一个成功的llm段（duration/token照常），"跑了但错了"不可查。
    判定用前缀/行首标记，不用"错误"子串包含（正常回答里也会出现，不能误伤）。
    """
    text = response_text or ""
    t = text.lstrip()
    if t.startswith(LLM_FAILURE_MARKERS):
        return "error"
    if "\n[LLM错误" in text or "\n[LLM响应超时" in text:
        return "error"
    return "ok"


def _fail_safe(fallback):
    """纵深防御：公共面绝不向会话流漏异常（_post自身已never-raise，此层
    再兜一层——观察面的bug也不能炸掉用户回合，CowAgent记账哲学）。"""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                logger.debug(f"[trajectory] {fn.__name__} failed (non-fatal): {e}")
                return fallback

        return wrapper

    return deco


class TrajectoryRecorder:
    """Fire-safe trajectory span writer for the real chat turn path."""

    def __init__(
        self,
        base_url: str = TRAJECTORY_BASE_URL,
        timeout: float = 1.5,
        max_consecutive_failures: int = 3,
        cooldown_s: float = 60.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_consecutive_failures = max_consecutive_failures
        self.cooldown_s = cooldown_s
        self._ensured_sessions: set[str] = set()
        self._consecutive_failures = 0
        self._paused_until = 0.0

    # ── 传输 + 熔断 ─────────────────────────────────────────────

    @property
    def is_paused(self) -> bool:
        return time.time() < self._paused_until

    async def _post(self, path: str, payload: dict, allow_404: bool = False) -> dict | None:
        """POST到opensoul trajectory面。成功返回json dict，任何失败返回None。

        连续 max_consecutive_failures 次失败 → 冷却 cooldown_s（错误风暴熔断：
        opensoul已死时不给每个事件白付timeout）。熔断进出各记一条warning。
        allow_404=True：目标不存在（如session收口时session还没建）=正常no-op
        返回None，不计熔断失败——end_session的404是常态不是故障，不能让它把
        合法写入打进冷却窗。
        """
        if self.is_paused:
            return None
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code == 404 and allow_404:
                return None
            if resp.status_code >= 400:
                raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
            data = resp.json()
            if self._consecutive_failures:
                logger.warning(
                    f"[trajectory] recovered after {self._consecutive_failures} failures "
                    f"(last ok: {path})"
                )
                self._consecutive_failures = 0
            return data if isinstance(data, dict) else None
        except Exception as e:
            self._consecutive_failures += 1
            if self._consecutive_failures >= self.max_consecutive_failures:
                self._paused_until = time.time() + self.cooldown_s
                logger.warning(
                    f"[trajectory] {self._consecutive_failures} consecutive failures, "
                    f"pausing writes for {self.cooldown_s:.0f}s: {e}"
                )
                self._consecutive_failures = 0
            else:
                logger.debug(f"[trajectory] post {path} failed (non-fatal): {e}")
            return None

    # ── 公共面（全部never-raise）────────────────────────────────

    @_fail_safe(False)
    async def ensure_session(
        self, session_id: str, agent_id: str = "soulmate", task_description: str = ""
    ) -> bool:
        """幂等确保trajectory session存在（id=chat session id，单一真源）。"""
        if not session_id or session_id in self._ensured_sessions:
            return bool(session_id)
        data = await self._post(
            "/api/trajectory/sessions",
            {
                "session_id": session_id,
                "agent_id": agent_id,
                "task_description": task_description[:200],
            },
        )
        if data and data.get("id") == session_id:
            self._ensured_sessions.add(session_id)
            return True
        return False

    @_fail_safe(False)
    async def end_session(self, session_id: str, status: str = "completed") -> bool:
        """收口trajectory session（04:20轮遗留#2销账：session.status不再恒running）。

        ws_acp连接teardown调用（实时收口）；opensoul侧闲置清扫兜底+add_event
        自愈翻回running（长生命周期chat session跨连接继续不卡死）。顺序纪律：
        必须在回合事件全部落库（relay.close()已drain / 子进程死透）之后调用。
        session不存在=正常no-op（404不计熔断）。never-raise。
        """
        if not session_id:
            return False
        data = await self._post(
            f"/api/trajectory/sessions/{session_id}/end?status={status}",
            {},
            allow_404=True,
        )
        return bool(data and data.get("ended"))

    @_fail_safe("")
    async def start_turn(
        self, session_id: str, user_text: str = "", agent_id: str = "soulmate"
    ) -> str:
        """回合根span（event_type=user_input，metadata.kind=turn_root）。失败返回''。"""
        if not session_id:
            return ""
        if not await self.ensure_session(
            session_id, agent_id=agent_id, task_description=user_text
        ):
            return ""
        data = await self._post(
            f"/api/trajectory/sessions/{session_id}/events",
            {
                "event_type": "user_input",
                "agent_id": agent_id,
                "content": (user_text or "")[:500],
                "metadata": {"kind": "turn_root", "chars": len(user_text or "")},
                "parent_event_id": None,
                "status": "ok",
            },
        )
        return (data or {}).get("id", "")

    @_fail_safe("")
    async def record_llm_call(
        self,
        session_id: str,
        parent_id: str,
        content: str = "",
        duration_ms: float = 0.0,
        token_usage: int = 0,
        status: str = "ok",
        tools: list[dict] | None = None,
        agent_id: str = "soulmate",
        source: str = "",
    ) -> str:
        """LLM段span（child of turn root）+ 其工具调用span（child of llm span）。

        tools=all_tool_calls条目（soulmate _run_llm_with_tools轨迹账本形状），
        每条一个tool_call事件：name/arguments/result_preview/permission进metadata
        （截断防爆：观察面只需要轮廓），失败/拦截status=error（失败必须可见）。

        agent_id/source（relay轨迹轮新增，默认值=既有soulmate路径零变化）：非
        soulmate引擎（hermes/openclaw/opencode/mimo）的回合由ws_acp纯透传层的
        RelayTrajectory按消息流记账（agent_id=路由引擎名，source="relay"），观察面
        可区分"引擎内深记录"与"代理层浅记录"两种来源。
        """
        if not session_id or not parent_id:
            return ""
        tools = tools or []
        metadata: dict = {"tool_count": len(tools), "rounds_hint": content[:120]}
        if source:
            metadata["source"] = str(source)[:40]
        data = await self._post(
            f"/api/trajectory/sessions/{session_id}/events",
            {
                "event_type": "llm_call",
                "agent_id": agent_id,
                "content": content[:500],
                "metadata": metadata,
                "parent_event_id": parent_id,
                "token_usage": int(token_usage or 0),
                "duration_ms": float(duration_ms or 0.0),
                "status": status,
            },
        )
        span_id = (data or {}).get("id", "")
        if not span_id or not tools:
            return span_id
        await self._post(
            f"/api/trajectory/sessions/{session_id}/events/batch",
            {"events": self._tool_child_events(tools, span_id, agent_id=agent_id)},
        )
        return span_id

    # ── round级span（P1 span粒度round级嵌套：root→llm段→round→tool_call四级）──

    def _tool_child_events(self, tools: list[dict], parent_span_id: str, agent_id: str = "soulmate") -> list[dict]:
        """tool_call子span事件列表——record_llm_call平面批与record_round的round级
        批共用同一实现（禁止双实现漂移）。单批50条封顶，防病态回合刷爆观察面。
        agent_id透传（relay轨迹轮新增，默认"soulmate"=既有调用方零变化）。"""
        events = []
        for t in tools[:50]:  # 单批50工具调用封顶
            events.append(
                {
                    "event_type": "tool_call",
                    "agent_id": agent_id,
                    "content": str(t.get("name", ""))[:120],
                    "metadata": {
                        "name": str(t.get("name", ""))[:120],
                        "arguments": str(t.get("arguments", ""))[:400],
                        "result_preview": str(t.get("result_preview", ""))[:300],
                        "permission": str(t.get("permission", ""))[:60],
                        "cached": bool(t.get("cached", False)),
                    },
                    "parent_event_id": parent_span_id,
                    "status": self._tool_status(t),
                }
            )
        return events

    @_fail_safe("")
    async def start_llm_span(
        self, session_id: str, parent_id: str, content: str = ""
    ) -> str:
        """llm段span占位（langfuse Observation start→end语义）。

        round级嵌套要求段span先于round子span存在：先建段拿到id，_run_llm_with_tools
        每轮把round子span挂进来（record_round），最后end_llm_span盖总账。"""
        if not session_id or not parent_id:
            return ""
        data = await self._post(
            f"/api/trajectory/sessions/{session_id}/events",
            {
                "event_type": "llm_call",
                "agent_id": "soulmate",
                "content": (content or "")[:500],
                "metadata": {"kind": "llm_segment"},
                "parent_event_id": parent_id,
                "status": "ok",
            },
        )
        return (data or {}).get("id", "")

    @_fail_safe("")
    async def record_round(
        self,
        session_id: str,
        segment_span_id: str,
        round_index: int = 0,
        content: str = "",
        duration_ms: float = 0.0,
        tools: list[dict] | None = None,
    ) -> str:
        """round级span（child of llm段span）+ 其tool_call子span（child of round）。

        树形：user_input → llm_call(段) → llm_call(round N) → tool_call × M——
        多轮工具循环里"哪次LLM决策带出哪些工具"按round分组可见（此前平面tool批
        把轮次边界压平）。空round（无文本无工具，如steer在LLM前中止）不落噪声span；
        文本带LLM失败标记、或全部工具被拒/拦截→round判error（失败必须可见）。"""
        if not session_id or not segment_span_id:
            return ""
        tools = tools or []
        if not tools and not (content or "").strip():
            return ""
        round_status = llm_outcome_status(content or "")
        if tools and round_status == "ok" and all(
            self._tool_status(t) == "error" for t in tools
        ):
            round_status = "error"
        data = await self._post(
            f"/api/trajectory/sessions/{session_id}/events",
            {
                "event_type": "llm_call",
                "agent_id": "soulmate",
                "content": (content or "")[:500],
                "metadata": {
                    "kind": "round",
                    "round": int(round_index),
                    "tool_count": len(tools),
                },
                "parent_event_id": segment_span_id,
                # 段end时按delta回填session累计，round不重复计token
                "token_usage": 0,
                "duration_ms": float(duration_ms or 0.0),
                "status": round_status,
            },
        )
        span_id = (data or {}).get("id", "")
        if not span_id or not tools:
            return span_id
        await self._post(
            f"/api/trajectory/sessions/{session_id}/events/batch",
            {"events": self._tool_child_events(tools, span_id)},
        )
        return span_id

    async def _end_span(
        self, session_id: str, span_id: str, status: str, token_usage: int
    ) -> None:
        """span收口单一真源（end_turn回合根与end_llm_span llm段共用，禁止双实现
        漂移）：end_at盖章，store单一时钟推导duration，token按delta回填session累计。"""
        await self._post(
            f"/api/trajectory/events/{span_id}/end",
            {"status": status, "token_usage": int(token_usage or 0)},
        )

    @_fail_safe(None)
    async def end_turn(
        self, session_id: str, root_id: str, status: str = "ok", token_usage: int = 0
    ) -> None:
        """回合根span收口（end_at盖章，store侧单一时钟推导duration）。"""
        if not session_id or not root_id:
            return
        await self._end_span(session_id, root_id, status, token_usage)

    @_fail_safe(None)
    async def end_llm_span(
        self, session_id: str, span_id: str, status: str = "ok", token_usage: int = 0
    ) -> None:
        """llm段span收口（与start_llm_span配对：round子span挂齐后盖总账）。

        duration由store单一时钟created_at→end_at推导（与回合根同一时钟）；
        token_usage由store按delta回填session累计（段建时token=0，不会双记）。"""
        if not session_id or not span_id:
            return
        await self._end_span(session_id, span_id, status, token_usage)

    # ── 工具结局判定（失败必须可见）─────────────────────────────

    @staticmethod
    def _tool_status(entry: dict) -> str:
        perm = str(entry.get("permission", "")).lower()
        preview = str(entry.get("result_preview", ""))[:80]
        # perm关键词含fail/cancel：relay轨迹轮把ACP工具状态（completed/failed/
        # cancelled）放在permission位，失败必须可见（mem0 §1.1）；soulmate既有
        # permission取值（allow/deny/loop_guard:*/infection:*/sandbox-restricted）
        # 均不含这两个词，零误伤。
        if any(k in perm for k in ("deny", "block", "terminate", "reject", "fail", "cancel")):
            return "error"
        if preview.startswith(("blocked", "failed", "错误", "失败", "[BLOCKED]")):
            return "error"
        return "ok"
