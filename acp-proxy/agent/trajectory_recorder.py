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

import asyncio
import functools
import logging
import os
import re
import time

import httpx

from agent.read_turn import is_heading_prefix, warn_line_is_failure

logger = logging.getLogger("acp-proxy.trajectory-recorder")

TRAJECTORY_BASE_URL = "http://127.0.0.1:8090"


def _env_float(name: str, default: float) -> float:
    """env 浮点配置（坏值回落默认，观察面配置不炸主链）。"""
    try:
        return float(os.getenv(name, "") or default)
    except (TypeError, ValueError):
        return default


def _payload_summary(payload: dict) -> str:
    """丢弃告警里的payload摘要（对账用：哪个session哪个事件丢了）。"""
    try:
        return (
            f"event={payload.get('event_type', '?')} "
            f"session={payload.get('session_id', '-')}"
        )
    except Exception:  # noqa: BLE001 — 摘要本身绝不添乱
        return "event=?"

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


# 工具/执行侧失败的结构化文本标记（llm_outcome_status同哲学：认显式结构化
# 标记，不用"错误/error"子串包含——正常回答讨论error/报错/失败案例不能误判
# 成失败）。形态来源（全为本仓库机器生成的报错前缀/固定格式）：
#   「错误: …」/「错误：…」               session_recall.py:577、tool_output_handler.py:365
#   「写入失败: …」「执行失败: …」等X失败:  soulmate_agent.py各工具分支兜底
#   「{emoji} 工具 `X` 调用失败 (第N次尝试)」 tool_errors.to_model_message
#   「❌ 文件 `path` …失败: …」            edit_safety.py:68
#   「[MCP 工具调用失败]…」「[CODE_MODE] 批量执行失败:…」「[被拦截」  各分支
_TURN_FAILURE_MARKERS = (
    "[MCP 工具调用失败]",
    "[CODE_MODE] 批量执行失败",
    "[被拦截",
)
# 带前缀捕获的行首前缀族（捕获组1=前缀，供小节标题误伤面过滤——「常见错误：
# …」类小节标题≠失败汇报，见read_turn.is_heading_prefix；机器报错前缀是动作
# 动词含标题集外字符，照旧判失败）
_TURN_FAILURE_PREFIX_RES = (
    # 行首结构化前缀：「错误: …」/「错误：…」/「写入失败: …」/「执行遇到错误：…」
    re.compile(r"^[ \t]*(?:❌[ \t]*)?(\S{0,12})(?:错误|失败)[:：]", re.M),
    # 行首英文同款：「Error: …」「Exception: …」「Failed: …」
    re.compile(r"^[ \t]*(?:error|exception|failed|failure)[:：]", re.M | re.I),
)
_TURN_FAILURE_RES = (
    # tool_errors.to_model_message 固定格式：「工具 `X` 调用失败 (第N次尝试)」
    re.compile(r"工具 `[^`\n]+` 调用失败"),
    # edit_safety：「❌ 文件 `path` …失败: …」/「❌ 改进失败: …」（长路径不限60字符）
    re.compile(r"^❌[^\n]+失败[:：]", re.M),
)


def turn_outcome_success(response_text: str) -> bool:
    """回合真实结局判定（技能usage回执/能力评估/技能提取/语义缓存准入共用单一真源）。

    修复"指标说谎"：旧启发式 `"错误" not in text and "error" not in text.lower()`
    把正常回答里讨论error/报错/失败案例的回合误判成失败——技能success_rate被
    无辜稀释、能力评估按0.3记分、成功回合被挡在技能提取门外（"不进化"的度量
    假象之一）。现与llm_outcome_status同哲学：LLM基础设施失败仍判error；
    否则只认上列结构化失败标记（行首前缀/固定格式）。
    小节标题误伤面已收敛：行首「常见错误：…」「典型失败：…」类标题经
    read_turn.is_heading_prefix过滤，不再判失败（此前正是残余误伤面）。
    ⚠️告警细分（00:12轮遗留#3回合侧同源）：「⚠️ …不可用: …」类机器失败
    告警行=失败必须可见（此前漏判假成功）；「⚠️ 3个文件被跳过」类降级告警
    ≠失败——判法走read_turn.warn_line_is_failure单一真源。
    反向残余：模型用自然语言改述失败（如"我没有权限访问"）——文本启发式
    的固有极限。
    """
    text = response_text or ""
    if llm_outcome_status(text) == "error":
        return False
    if any(m in text for m in _TURN_FAILURE_MARKERS):
        return False
    for pat in _TURN_FAILURE_PREFIX_RES:
        for m in pat.finditer(text):
            if m.groups() and is_heading_prefix(m.group(1)):
                continue  # 「常见错误：」类小节标题≠失败汇报
            return False
    if any(warn_line_is_failure(line) for line in text.splitlines()):
        return False  # 「⚠️ …不可用: …」类机器失败告警行=失败
    return not any(p.search(text) for p in _TURN_FAILURE_RES)


def _fail_safe(fallback):
    """纵深防御：公共面绝不向会话流漏异常（_post自身已never-raise，此层
    再兜一层——观察面的bug也不能炸掉用户回合，CowAgent记账哲学）。"""

    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except Exception as e:
                # 异常类名必带：str(e) 可能为空（ReadTimeout() 空消息实证 #678）
                logger.debug(
                    f"[trajectory] {fn.__name__} failed (non-fatal): "
                    f"{type(e).__name__}: {e}"
                )
                return fallback

        return wrapper

    return deco


class TrajectoryRecorder:
    """Fire-safe trajectory span writer for the real chat turn path."""

    def __init__(
        self,
        base_url: str = TRAJECTORY_BASE_URL,
        timeout: float | None = None,
        max_consecutive_failures: int = 3,
        cooldown_s: float = 60.0,
    ):
        # 读预算与 8090 响应延迟分布匹配（#678 真因链：1.5s 读预算 vs 全器官
        # stats 扫描风暴约 4s 拥塞 → ReadTimeout 丢事件批次 → tool_call 行缺失
        # → verify 判据 miss）。默认 8.0s 覆盖观察到的拥塞段，env 可调。
        if timeout is None:
            timeout = _env_float("SOULMATE_TRAJ_TIMEOUT_S", 8.0)
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        self.max_consecutive_failures = max_consecutive_failures
        self.cooldown_s = cooldown_s
        self._ensured_sessions: set[str] = set()
        self._consecutive_failures = 0
        self._paused_until = 0.0
        # 对账计数（期望④：发出行数 vs 落库行数可查，禁静默丢弃）
        self._stats = {"sent": 0, "ok": 0, "failed": 0, "retried": 0, "dropped": 0}

    def stats(self) -> dict:
        """发出/落库/丢弃对账计数（只读拷贝）。"""
        return dict(self._stats)

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

        重试语义（#678 ReadTimeout 丢批次真因链后续）：
        - 请求未到达服务端的错误（Connect*/Write*/Pool*）→ 立即重试一次，
          确定没写入、零重复行风险；
        - 响应读超时/读断（ReadTimeout/ReadError）→ **不重试**（请求可能已
          被服务端写入，盲目补发=重复行），靠读预算上调覆盖拥塞段；
        - 任何最终失败 = 可见丢弃：warning 带异常类名+payload 摘要+对账计数，
          禁静默（mem0 §1.1 失败必须可见）。
        """
        if self.is_paused:
            return None
        url = f"{self.base_url}{path}"
        self._stats["sent"] += 1
        attempts_allowed = 2  # 首发 + connect 类安全重试 1 次
        last_err: Exception | None = None
        for attempt in range(1, attempts_allowed + 1):
            if attempt > 1:
                self._stats["retried"] += 1
                await asyncio.sleep(0.2)
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(
                        self.timeout, connect=min(2.0, self.timeout)
                    )
                ) as client:
                    resp = await client.post(url, json=payload)
                if resp.status_code == 404 and allow_404:
                    return None
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:120]}")
                data = resp.json()
                self._stats["ok"] += 1
                if self._consecutive_failures:
                    logger.warning(
                        f"[trajectory] recovered after {self._consecutive_failures} failures "
                        f"(last ok: {path})"
                    )
                    self._consecutive_failures = 0
                return data if isinstance(data, dict) else None
            except Exception as e:
                last_err = e
                # 请求未到达类才重试；ReadTimeout/ReadError 可能已写入禁补发
                if attempt < attempts_allowed and isinstance(
                    e,
                    (
                        httpx.ConnectError,
                        httpx.ConnectTimeout,
                        httpx.WriteError,
                        httpx.WriteTimeout,
                        httpx.PoolTimeout,
                    ),
                ):
                    continue
                break
        # 最终失败：可见丢弃（异常类名必带——str(e) 可能为空，#678 实证）
        self._consecutive_failures += 1
        self._stats["failed"] += 1
        self._stats["dropped"] += 1
        err = f"{type(last_err).__name__}: {last_err}"
        summary = _payload_summary(payload)
        if self._consecutive_failures >= self.max_consecutive_failures:
            self._paused_until = time.time() + self.cooldown_s
            logger.warning(
                f"[trajectory] {self._consecutive_failures} consecutive failures, "
                f"pausing writes for {self.cooldown_s:.0f}s: {err} "
                f"(dropped: {path} {summary}; stats={self._stats})"
            )
            self._consecutive_failures = 0
        else:
            logger.warning(
                f"[trajectory] post {path} dropped (non-fatal): {err} "
                f"({summary}; stats={self._stats})"
            )
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
    async def log_event(
        self, session_id: str, kind: str = "note", content: str = "",
        metadata: dict | None = None, parent_id: str = "",
    ) -> str:
        """自由事件落 trace（P1-15 条件类事件用：wall_clock 软预算提醒等）。

        kind 进 metadata.kind；fire-safe（失败返回''不冒泡）。事件即活动——
        会触发 store 的"completed 自愈翻回 running"，只对 running 会话发。"""
        if not session_id:
            return ""
        if not await self.ensure_session(session_id):
            return ""
        meta = dict(metadata or {})
        meta.setdefault("kind", str(kind)[:40])
        data = await self._post(
            f"/api/trajectory/sessions/{session_id}/events",
            {
                "event_type": "custom",
                "agent_id": "soulmate",
                "content": (content or "")[:500],
                "metadata": meta,
                "parent_event_id": (parent_id or None),
                "status": "ok",
            },
        )
        return (data or {}).get("id", "")

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
        （截断防爆：观察面只需要轮廓），失败/拦截status=error（失败必须可见）；
        duration_ms（15.2 工具执行时长）透传落事件列（_tool_child_events 单一真源）。

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

    @_fail_safe("")
    async def record_aux_llm_call(
        self,
        session_id: str,
        label: str,
        duration_ms: float = 0.0,
        status: str = "ok",
        parent_id: str = "",
        token_usage: int = 0,
        agent_id: str = "soulmate",
    ) -> str:
        """辅助LLM往返落账（14.4 落账补洞）——planner任务拆解/任务裁决/注入审计
        判词/标题生成/技能语义改写等主聊天循环之外的LLM往返。

        此前这些往返零落账，turn_timing_report 四段归因的"⑤未归属空洞"只能靠
        journal 事后解释（14.2 实证首段 6-32s 空洞=未落账 LLM 往返）。本方法按
        report 既有口径落同一张表（llm_call 无 end_at=轮内往返，
        start=created_at-duration_ms），自动计入时间覆盖——"空洞≈0"由此成为
        归因仪表完备的验收行。content 统一"[aux] {label}"前缀（report 据此与主
        轮区分）；parent 未知（fire-and-forget 标题任务先于回合根建 span）时挂
        根层 parent_event_id=None，不伪造父子。never-raise（记账失败永不阻塞）。
        """
        if not session_id:
            return ""
        await self.ensure_session(session_id, agent_id=agent_id)
        data = await self._post(
            f"/api/trajectory/sessions/{session_id}/events",
            {
                "event_type": "llm_call",
                "agent_id": agent_id,
                "content": f"[aux] {str(label)[:80]}",
                "metadata": {"kind": "aux_llm", "label": str(label)[:80]},
                "parent_event_id": parent_id or None,
                "token_usage": int(token_usage or 0),
                "duration_ms": float(duration_ms or 0.0),
                "status": status,
            },
        )
        return (data or {}).get("id", "")

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
                    # 15.2 工具执行时长透传（账本条目 duration_ms → tool_call 事件列，
                    # turn_timing_report ③工具执行段独立列的单一真源；旧条目缺省 0 如实）
                    "duration_ms": float(t.get("duration_ms", 0.0) or 0.0),
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
        self, session_id: str, span_id: str, status: str, token_usage: int,
        termination: str = "",
    ) -> None:
        """span收口单一真源（end_turn回合根与end_llm_span llm段共用，禁止双实现
        漂移）：end_at盖章，store单一时钟推导duration，token按delta回填session累计。
        termination（P1-15 五终止条件词汇，见 agent/termination.py）落事件
        metadata——回合根以外的span传默认空串（正常完成语义）不变。"""
        payload: dict = {"status": status, "token_usage": int(token_usage or 0)}
        if termination:
            payload["termination"] = str(termination)[:40]
        await self._post(
            f"/api/trajectory/events/{span_id}/end",
            payload,
        )

    @_fail_safe(None)
    async def end_turn(
        self, session_id: str, root_id: str, status: str = "ok", token_usage: int = 0,
        termination: str = "",
    ) -> None:
        """回合根span收口（end_at盖章，store侧单一时钟推导duration）。

        termination：本回合被哪个终止条件收口（max_turns/token_budget/
        external_cancel等，空=正常完成）——P1-15"终止条件组合入trace"落点。"""
        if not session_id or not root_id:
            return
        await self._end_span(session_id, root_id, status, token_usage, termination)

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
