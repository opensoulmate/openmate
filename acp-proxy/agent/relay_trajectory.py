"""
RelayTrajectory — 非soulmate引擎回合的trajectory记账端（ws_acp纯透传层观察者）
=============================================================================

背景（04:20轮遗留#4"hermes引擎回合不落trajectory"）：ws_acp.py把所有ACP流量
WebSocket↔stdio子进程双向透传——soulmate路由的回合由SoulMateAgent内部的
TrajectoryRecorder深记录（root→llm段→round→tool_call四级树），但hermes/
openclaw/opencode/mimo路由是外部进程，代理层只转发消息，一个span都不落——
/trajectory观察面（SUMMARY P0-4"我都不知道他们在干嘛"）对这些引擎的回合全盲。

本模块在透传层旁路观察JSON-RPC消息流（不改一个字节的转发内容），把每个
session/prompt回合记成扁平span树：
    user_input(turn root) ── llm_call(段, source=relay) ── tool_call × M
复用TrajectoryRecorder既有公共面（start_turn/record_llm_call/end_turn），
opensoul零改动，前端树/列表视图直接渲染。

设计纪律（沿用TrajectoryRecorder同款）：
- "记账失败绝不阻塞会话流"（CowAgent记账哲学）：observe_*同步入队即返回，
  真实HTTP写在后台worker里做——透传路径零额外延迟；任何异常吞掉只记日志；
- 记账顺序保真：单worker顺序消费队列，start_turn→record_llm_call→end_turn
  的血缘顺序不乱（root_id在turn状态对象上传递，闭包执行时读取）；
- 失败必须可见（mem0 §1.1）：响应带error/stopReason∈{error,refusal,cancelled}
  、回合被新prompt挤掉、连接关闭未收口→status=error；工具failed/cancelled→
  tool_call span status=error（_tool_status认permission位的fail/cancel）；
- 观察粒度声明（诚实边界）：代理层看不到引擎内部LLM轮次，所以是扁平
  llm_call段（metadata.source="relay"标记浅记录来源），不冒充round级深记录；
- 双形状工具解析：平台形态tool_call的content.text是JSON字符串
  {toolCallId,name,arguments}（chat-client.tsx同款契约），标准ACP形态字段
  直接在update上（toolCallId/title/rawInput）——两种都认。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from agent.frame_origin import KIND_ENGINE_CHROME, split_frames, strip_engine_chrome
from agent import termination
from agent.trajectory_recorder import TrajectoryRecorder, llm_outcome_status

logger = logging.getLogger("acp-proxy.relay-trajectory")

# ACP stopReason里代表"回合没正常完成"的取值（失败必须可见）
STOP_ERROR_REASONS = ("error", "refusal", "cancelled")


@dataclass
class _TurnState:
    """单个session/prompt回合的观察状态（observe_*同步写，worker闭包按引用读）。"""

    prompt_id: object
    session_id: str
    t0: float
    text_parts: list[str] = field(default_factory=list)
    full_text: str = ""
    thought_chars: int = 0
    tools: dict[str, dict] = field(default_factory=dict)
    tool_order: list[str] = field(default_factory=list)
    root_id: str = ""

    def tool_entries(self) -> list[dict]:
        """recorder账本形状条目（_tool_child_events消费的同一契约）。"""
        return [self.tools[k] for k in self.tool_order]

    def text(self) -> str:
        # 优先chunk流（增量真源）；无chunk时回退完整agent_message（某些引擎只发整条）
        return "".join(self.text_parts) or self.full_text

    def filtered_text(self) -> tuple[str, dict[str, int]]:
        """⚡周期#47 采集侧同源过滤：系统帧/引擎chrome不进trajectory content。

        帧级：split_frames 判别 heartbeat/self_check/audit_notice（soulmate 生产者
        帧走同一 agent_message_chunk 通道——8.5c-⑤ 同源粘连）；合并级：
        strip_engine_chrome 剥开头的引擎启动横幅块（真实库实证：hermes acp 横幅
        占满 content[:500] 遮蔽真实回答）。返回 (回答文本, 过滤计数)。
        """
        frames = self.text_parts or ([self.full_text] if self.full_text else [])
        text, counts = split_frames(frames)
        text, chrome_n = strip_engine_chrome(text)
        if chrome_n:
            counts[KIND_ENGINE_CHROME] = counts.get(KIND_ENGINE_CHROME, 0) + chrome_n
        return text, counts


class RelayTrajectory:
    """ws_acp透传层的trajectory观察者：同步观察+后台顺序落账。"""

    def __init__(
        self,
        agent_id: str = "hermes",
        session_id_getter=None,
        recorder=None,  # TrajectoryRecorder或测试替身（duck-typed，可注入）
    ):
        self.agent_id = agent_id
        self._session_id_getter = session_id_getter or (lambda: "")
        self.recorder = recorder or TrajectoryRecorder()
        self._queue: asyncio.Queue | None = None
        self._worker_task: asyncio.Task | None = None
        self._turn: _TurnState | None = None
        self.turns_seen = 0  # 可观测：观察到的prompt回合数（测试/日志用）

    # ── 队列worker（顺序保真） ─────────────────────────────────

    def _ensure_worker(self) -> asyncio.Queue | None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # 无事件循环（非async上下文）：丢弃观察，绝不为记账造循环
            logger.debug("[relay-traj] no running loop, observation dropped")
            return None
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = loop.create_task(self._worker())
        return self._queue

    def _enqueue(self, coro_factory) -> None:
        q = self._ensure_worker()
        if q is not None:
            q.put_nowait(coro_factory)

    async def _worker(self) -> None:
        assert self._queue is not None
        while True:
            factory = await self._queue.get()
            try:
                await factory()
            except Exception as e:  # 记账故障绝不外漏（CowAgent记账哲学）
                logger.debug(f"[relay-traj] record task failed (non-fatal): {e}")
            finally:
                self._queue.task_done()

    async def drain(self) -> None:
        """等队列清空（测试确定性收尾用）。"""
        if self._queue is not None:
            await self._queue.join()

    async def close(self) -> None:
        """连接收尾：未收口回合判error落账（失败必须可见），再停worker。"""
        self._finalize(status_override="error")
        await self.drain()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass
            self._worker_task = None

    # ── 观察入口（同步、never-raise、零延迟） ──────────────────

    def observe_client(self, msg) -> None:
        """客户端→子进程消息观察。只认session/prompt（回合起点）。"""
        try:
            if not isinstance(msg, dict) or msg.get("method") != "session/prompt":
                return
            params = msg.get("params") or {}
            prompt_id = msg.get("id")
            text = self._extract_prompt_text(params.get("prompt"))
            # 新prompt挤掉未收口旧回合：旧回合判error落账（不悬挂root）
            self._finalize(status_override="error")
            sid = str(self._session_id_getter() or "")
            turn = _TurnState(
                prompt_id=prompt_id,
                session_id=sid,
                t0=time.time(),
            )
            self._turn = turn
            self.turns_seen += 1

            async def _start(t: _TurnState = turn, s: str = sid, tx: str = text):
                if not s:
                    return
                t.root_id = await self.recorder.start_turn(
                    s, tx, agent_id=self.agent_id
                )

            self._enqueue(_start)
        except Exception as e:
            logger.debug(f"[relay-traj] observe_client failed (non-fatal): {e}")

    def observe_agent(self, msg) -> None:
        """子进程→客户端消息观察。prompt响应=回合收口；session/update=增量。"""
        try:
            if not isinstance(msg, dict):
                return
            turn = self._turn
            if turn is None:
                return
            # prompt响应（JSON-RPC response：id匹配+result/error）
            if (
                msg.get("id") == turn.prompt_id
                and turn.prompt_id is not None
                and ("result" in msg or "error" in msg)
            ):
                result = msg.get("result") or {}
                stop = str(
                    result.get("stopReason") or result.get("stop_reason") or ""
                ).lower()
                override = "error" if (
                    "error" in msg or stop in STOP_ERROR_REASONS
                ) else None
                self._finalize(status_override=override, stop_reason=stop)
                return
            if msg.get("method") != "session/update":
                return
            update = (msg.get("params") or {}).get("update") or {}
            if not isinstance(update, dict):
                return
            su = str(update.get("sessionUpdate") or update.get("session_update") or "")
            if su in ("agent_message_chunk",):
                t = self._content_text(update)
                if t:
                    turn.text_parts.append(t)
            elif su in ("agent_message",):
                t = self._content_text(update)
                if t:
                    turn.full_text = t  # 回退真源（仅当无chunk时使用）
            elif su in ("agent_thought_chunk", "agent_thought"):
                turn.thought_chars += len(self._content_text(update))
            elif su == "tool_call":
                self._add_tool(turn, update)
            elif su == "tool_call_update":
                self._update_tool(turn, update)
        except Exception as e:
            logger.debug(f"[relay-traj] observe_agent failed (non-fatal): {e}")

    def finalize_open(self, status: str = "error") -> None:
        """显式收口未完成回合（子进程退出/写管道失败/超时——回合确定已死时
        调用方主动收口error，不等下一个prompt"挤掉"，不悬挂root）。"""
        self._finalize(status_override=status)

    def observe_cli_turn(
        self,
        user_text: str,
        answer_text: str,
        stop_reason: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        """CLI兜底回合记账（proxy.py HTTP /acp/send的hermes -z降级路径）。

        ACP回合失败/空响应后CLI兜底给用户的回答同样是一整个用户可见回合——
        观察面必须"ACP失败回合+CLI兜底回合"两笔都看得到（禁止静默降级的
        观察面语义，mem0 §1.1），否则只见error回合，与用户实际拿到回答的
        体感对不上。回合在单个闭包里start→record→end一次收口（无悬挂root）。
        """
        try:
            # 与observe_client同款"挤掉"语义：前序未收口回合判error
            self._finalize(status_override="error")
            sid = str(self._session_id_getter() or "")
            self.turns_seen += 1

            async def _one(
                s: str = sid,
                tx: str = user_text,
                ax: str = answer_text,
                stop: str = stop_reason,
                dur: float = duration_ms,
            ):
                if not s:
                    return
                # ⚡周期#47：CLI兜底回答同样过采集侧过滤（hermes -z 输出可带启动横幅）
                ax, _filtered = strip_engine_chrome(ax)
                root_id = await self.recorder.start_turn(s, tx, agent_id=self.agent_id)
                status = (
                    "error" if stop in STOP_ERROR_REASONS else llm_outcome_status(ax)
                )
                await self.recorder.record_llm_call(
                    s, root_id, content=ax, duration_ms=dur,
                    status=status, tools=[], agent_id=self.agent_id, source="relay",
                )
                await self.recorder.end_turn(
                    s, root_id, status=status,
                    # P1-15：ACP stop_reason→终止条件词汇落 trace（max_tokens=
                    # token_budget 族，其余空=正常完成）
                    termination=termination.from_acp_stop_reason(stop),
                )
                logger.debug(
                    f"[relay-traj] cli turn closed: agent={self.agent_id} sid={s} "
                    f"status={status} stop={stop or '-'} chars={len(ax)}"
                )

            self._enqueue(_one)
        except Exception as e:
            logger.debug(f"[relay-traj] observe_cli_turn failed (non-fatal): {e}")

    # ── 内部：回合收口 ─────────────────────────────────────────

    def _finalize(self, status_override: str | None = None, stop_reason: str = "") -> None:
        turn = self._turn
        if turn is None:
            return
        self._turn = None

        async def _finish(
            t: _TurnState = turn,
            override: str | None = status_override,
            stop: str = stop_reason,
        ):
            if not t.session_id:
                return
            text, filtered = t.filtered_text()
            status = override or llm_outcome_status(text)
            meta_stop = stop  # 保留stop_reason供日志核对
            tools = t.tool_entries()
            await self.recorder.record_llm_call(
                t.session_id,
                t.root_id,
                content=text,
                duration_ms=(time.time() - t.t0) * 1000.0,
                status=status,
                tools=tools,
                agent_id=self.agent_id,
                source="relay",
            )
            await self.recorder.end_turn(
                t.session_id, t.root_id, status=status,
                # P1-15：ACP stop_reason→终止条件词汇落 trace
                termination=termination.from_acp_stop_reason(stop),
            )
            logger.debug(
                f"[relay-traj] turn closed: agent={self.agent_id} sid={t.session_id} "
                f"status={status} stop={meta_stop or '-'} chars={len(text)} "
                f"tools={len(tools)} thought_chars={t.thought_chars} "
                f"filtered={filtered or '-'}"
            )

        self._enqueue(_finish)

    # ── 内部：消息字段解析（双形状） ───────────────────────────

    @staticmethod
    def _content_text(update: dict) -> str:
        content = update.get("content")
        if isinstance(content, dict):
            return str(content.get("text") or "")
        if isinstance(content, str):
            return content
        return str(update.get("text") or "")

    @staticmethod
    def _extract_prompt_text(prompt) -> str:
        """session/prompt params.prompt → 纯文本（[{type:text,text}] / str / dict）。"""
        if isinstance(prompt, str):
            return prompt
        if isinstance(prompt, dict):
            return str(prompt.get("text") or "")
        if isinstance(prompt, list):
            parts = []
            for item in prompt:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "".join(parts)
        return ""

    @staticmethod
    def _tool_json(update: dict) -> dict:
        """平台形态：content.text是JSON字符串（chat-client.tsx handleToolCall契约）。"""
        content = update.get("content")
        text = content.get("text") if isinstance(content, dict) else (
            content if isinstance(content, str) else ""
        )
        if not text:
            return {}
        try:
            data = json.loads(text)
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _add_tool(self, turn: _TurnState, update: dict) -> None:
        data = self._tool_json(update)
        raw_input = update.get("rawInput", data.get("arguments"))
        tc_id = str(
            update.get("toolCallId") or data.get("toolCallId") or f"tc-{len(turn.tool_order)}"
        )
        name = str(
            data.get("name") or update.get("title") or update.get("name") or "tool"
        )
        arguments = raw_input
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments, ensure_ascii=False, default=str) if arguments is not None else ""
        entry = {
            "name": name,
            "arguments": arguments,
            "result_preview": "",
            "permission": str(update.get("state") or data.get("state") or ""),
            "cached": False,
        }
        if tc_id not in turn.tools:
            turn.tool_order.append(tc_id)
        turn.tools[tc_id] = entry

    def _update_tool(self, turn: _TurnState, update: dict) -> None:
        data = self._tool_json(update)
        tc_id = str(update.get("toolCallId") or data.get("toolCallId") or "")
        if not tc_id:
            return
        entry = turn.tools.get(tc_id)
        if entry is None:
            # 只见到update没见到tool_call（引擎跳步）：建空条目兜底，工具仍可见
            self._add_tool(turn, update)
            entry = turn.tools.get(tc_id)
            if entry is None:
                return
        raw_output = update.get("rawOutput", data.get("content", data.get("result")))
        if isinstance(raw_output, dict):
            raw_output = raw_output.get("text") or json.dumps(
                raw_output, ensure_ascii=False, default=str
            )
        if raw_output:
            entry["result_preview"] = str(raw_output)[:300]
        state = str(update.get("state") or data.get("state") or "")
        if state:
            # permission位承载ACP工具状态（completed/failed/cancelled），
            # _tool_status认fail/cancel关键词→span status=error（失败必须可见）
            entry["permission"] = state
