"""讨论编排器 — 自动化群组 Agent 讨论流程

DiscussionOrchestrator 按 Advisor → Executor → Verifier 角色顺序执行讨论，
每轮每个 Agent 调用 LLM 生成回复，通过 broadcast_fn 广播给所有客户端，
同时持久化到 OpenSoul DB。

特性:
1. 按角色顺序执行: Advisor → Executor → Verifier
2. 默认 3 轮讨论，每轮每个 Agent 回复一次
3. 轮次之间发送 system_message 通知
4. 异步执行，不阻塞 WS 消息循环
5. 支持取消（cancel_discussion）
6. 错误处理: LLM失败跳过Agent、单轮超时60s、全局超时5分钟
"""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from uuid import uuid4

from agent.llm_engine import LLMEngine
from utils import network_state  # turn级park合并：讨论turn接入park预算

logger = logging.getLogger("acp-proxy.discussion-engine")

# ============================================================
# 常量 — 讨论流程配置
# ============================================================

# 讨论轮次数（默认3轮）
DEFAULT_ROUNDS = 3

# 单个 Agent 回复超时（秒）
AGENT_TIMEOUT = 60

# 全局讨论超时（秒）— 5分钟
GLOBAL_TIMEOUT = 300

# Agent 角色执行顺序: 顾问 → 执行者 → 审核者
ROLE_ORDER = ["advisor", "executor", "verifier"]

# 角色对应的 intent 标签
ROLE_INTENT_MAP = {
    "advisor": "suggest",       # 顾问: 建议/分析
    "executor": "claim",        # 执行者: 认领/执行
    "verifier": "review",       # 审核者: 审核/评分
}


# ============================================================
# Agent 系统提示词模板
# ============================================================

# 每个角色的系统提示词模板，{agent_name} 和 {goal} 会动态替换
SYSTEM_PROMPTS = {
    "advisor": (
        "你是{agent_name}，群组中的顾问。当前任务：{goal}。"
        "请分析任务并提出执行方案。"
        "要求：1）分解任务为可执行的子任务；2）指出潜在风险；3）给出优先级建议。"
        "请用中文回复，简洁明了。"
    ),
    "executor": (
        "你是{agent_name}，群组中的执行者。当前任务：{goal}。"
        "请认领子任务并说明执行计划。"
        "要求：1）选择你擅长的子任务；2）说明具体执行步骤；3）预估时间和资源。"
        "请用中文回复，简洁明了。"
    ),
    "verifier": (
        "你是{agent_name}，群组中的审核者。当前任务：{goal}。"
        "请说明你的审核标准。"
        "要求：1）列出关键验收标准；2）说明质量检查要点；3）提出改进建议。"
        "请用中文回复，简洁明了。"
    ),
}

# 讨论开始时的系统提示词（用于第一轮，注入之前轮次的上下文）
CONTEXT_PROMPT = (
    "这是第{round_num}轮讨论（共{total_rounds}轮）。"
    "以下是之前的讨论内容：\n{history}\n"
    "请基于以上讨论继续发表你的观点。"
)


# ============================================================
# 数据类 — Agent 和讨论任务
# ============================================================

@dataclass
class DiscussionAgent:
    """讨论参与者 — 从数据库加载的 Agent 信息"""
    agent_id: str           # Agent 唯一标识
    name: str               # Agent 显示名称
    role: str               # 角色: advisor / executor / verifier
    model: str = ""         # 使用的模型（空则用默认）
    temperature: float = 0.7  # 温度参数


@dataclass
class DiscussionTask:
    """讨论任务 — 包含任务目标和配置"""
    group_id: str           # 群组ID
    task_id: str            # 任务ID
    goal: str               # 任务目标描述
    constraints: list = field(default_factory=list)      # 约束条件
    completion_criteria: list = field(default_factory=list)  # 完成标准
    total_rounds: int = DEFAULT_ROUNDS  # 讨论轮次数


@dataclass
class DiscussionState:
    """讨论运行状态 — 用于跟踪和取消"""
    task_id: str
    group_id: str
    is_running: bool = False
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    current_round: int = 0
    start_time: float = 0.0


# ============================================================
# 讨论编排器 — 核心类
# ============================================================

class DiscussionOrchestrator:
    """讨论编排器 — 自动化群组 Agent 讨论流程

    按 Advisor → Executor → Verifier 角色顺序执行讨论，
    每轮每个 Agent 调用 LLM 生成回复，通过 broadcast_fn 广播。
    """

    def __init__(self):
        """初始化编排器"""
        # 活跃讨论状态: {task_id: DiscussionState}
        self._active_discussions: dict[str, DiscussionState] = {}

    def get_active_discussion(self, task_id: str) -> DiscussionState | None:
        """获取活跃讨论状态"""
        return self._active_discussions.get(task_id)

    def cancel_discussion(self, task_id: str) -> bool:
        """取消讨论 — 设置取消信号

        Args:
            task_id: 要取消的任务ID

        Returns:
            是否成功设置取消信号
        """
        state = self._active_discussions.get(task_id)
        if state and state.is_running:
            state.cancel_event.set()
            logger.info(f"[取消] 讨论 {task_id} 已发送取消信号")
            return True
        return False

    def cancel_all_discussions(self, group_id: str) -> int:
        """取消指定群组的所有讨论

        Args:
            group_id: 群组ID

        Returns:
            取消的讨论数量
        """
        count = 0
        for tid, state in self._active_discussions.items():
            if state.group_id == group_id and state.is_running:
                state.cancel_event.set()
                count += 1
        if count:
            logger.info(f"[取消] 群组 {group_id} 的 {count} 个讨论已发送取消信号")
        return count

    async def run(
        self,
        task: DiscussionTask,
        agents: list[DiscussionAgent],
        broadcast_fn,
        persist_fn,
        db_query_fn=None,
    ):
        """主流程 — 执行完整讨论流程

        Args:
            task: 讨论任务
            agents: 参与讨论的 Agent 列表
            broadcast_fn: 广播回调 async def(group_id, data)
            persist_fn: 持久化回调 def(group_id, agent_id, agent_name, content, intent, task_id, round_num) -> msg_id
            db_query_fn: 数据库查询回调 def(sql, params) -> rows（可选，用于查询历史消息）
        """
        # 创建讨论状态
        state = DiscussionState(
            task_id=task.task_id,
            group_id=task.group_id,
            is_running=True,
            start_time=time.time(),
        )
        self._active_discussions[task.task_id] = state

        # ── turn级park合并（kilocode "park整个turn"）：一个讨论任务=一次ask→
        # 最终reply的完整turn。turn预算随contextvar传递到每条LLM调用
        # （run→_get_agent_response→wait_for子task→chat_stream→maybe_park）：
        # 断网时整个讨论最多park一次，不叠加N×MAX_WAIT_S干等。
        network_state.begin_turn(f"discussion:{task.task_id}")

        try:
            # 广播讨论开始通知
            await broadcast_fn(task.group_id, {
                "type": "system_message",
                "data": {
                    "message": f"📢 讨论开始：{task.goal}",
                    "task_id": task.task_id,
                    "event": "discussion_start",
                    "total_rounds": task.total_rounds,
                    "agents": [{"agent_id": a.agent_id, "name": a.name, "role": a.role} for a in agents],
                    "timestamp": time.time(),
                },
            })

            # 按角色顺序排列 Agent
            ordered_agents = self._order_agents_by_role(agents)

            # 讨论历史记录（用于上下文传递）
            discussion_history: list[dict] = []

            # 执行多轮讨论
            for round_num in range(1, task.total_rounds + 1):
                # 检查全局超时
                elapsed = time.time() - state.start_time
                if elapsed >= GLOBAL_TIMEOUT:
                    logger.warning(f"[超时] 讨论 {task.task_id} 全局超时 ({elapsed:.0f}s >= {GLOBAL_TIMEOUT}s)")
                    await broadcast_fn(task.group_id, {
                        "type": "system_message",
                        "data": {
                            "message": f"⏰ 讨论已达到最大时长 ({GLOBAL_TIMEOUT}秒)，自动结束",
                            "task_id": task.task_id,
                            "event": "discussion_timeout",
                            "timestamp": time.time(),
                        },
                    })
                    break

                # 检查取消信号
                if state.cancel_event.is_set():
                    logger.info(f"[取消] 讨论 {task.task_id} 在第 {round_num} 轮被取消")
                    await broadcast_fn(task.group_id, {
                        "type": "system_message",
                        "data": {
                            "message": "🚫 讨论已被用户取消",
                            "task_id": task.task_id,
                            "event": "discussion_cancelled",
                            "timestamp": time.time(),
                        },
                    })
                    break

                # 更新当前轮次
                state.current_round = round_num

                # 广播轮次开始通知
                await broadcast_fn(task.group_id, {
                    "type": "discussion_round",
                    "data": {
                        "message": f"🔄 第 {round_num}/{task.total_rounds} 轮讨论开始",
                        "task_id": task.task_id,
                        "round_num": round_num,
                        "status": "round_start",
                        "timestamp": time.time(),
                    },
                })

                # 每个 Agent 按顺序回复
                for agent in ordered_agents:
                    # 再次检查取消
                    if state.cancel_event.is_set():
                        break

                    # 调用 Agent LLM 生成回复
                    content = await self._get_agent_response(
                        agent=agent,
                        task=task,
                        round_num=round_num,
                        history=discussion_history,
                        cancel_event=state.cancel_event,
                    )

                    # LLM 调用失败（内容为空或错误标记）
                    if not content or content.startswith("[LLM错误"):
                        await broadcast_fn(task.group_id, {
                            "type": "system_message",
                            "data": {
                                "message": f"⚠️ {agent.name} 在第 {round_num} 轮未能生成回复，已跳过",
                                "task_id": task.task_id,
                                "agent_id": agent.agent_id,
                                "round_num": round_num,
                                "event": "agent_skip",
                                "timestamp": time.time(),
                            },
                        })
                        continue

                    # 生成消息ID
                    msg_id = str(uuid4())
                    intent = ROLE_INTENT_MAP.get(agent.role, "comment")

                    # 持久化消息到数据库
                    persisted_id = persist_fn(
                        group_id=task.group_id,
                        agent_id=agent.agent_id,
                        agent_name=agent.name,
                        content=content,
                        intent=intent,
                        task_id=task.task_id,
                        round_num=round_num,
                    )
                    # 使用持久化返回的ID（如果成功）
                    if persisted_id:
                        msg_id = persisted_id

                    # 广播 Agent 消息给所有客户端
                    await broadcast_fn(task.group_id, {
                        "type": "agent_message",
                        "data": {
                            "id": msg_id,
                            "group_id": task.group_id,
                            "agent_id": agent.agent_id,
                            "agent_name": agent.name,
                            "content": content,
                            "intent": intent,
                            "round_num": round_num,
                            "task_id": task.task_id,
                            "timestamp": time.time(),
                        },
                    })

                    # 记录到讨论历史
                    discussion_history.append({
                        "role": "assistant",
                        "name": agent.name,
                        "agent_id": agent.agent_id,
                        "content": content,
                        "round_num": round_num,
                    })

                    logger.info(
                        f"[讨论] {agent.name} ({agent.role}) 第{round_num}轮回复已广播，"
                        f"内容长度: {len(content)} 字符"
                    )

                # 广播轮次结束通知
                await broadcast_fn(task.group_id, {
                    "type": "discussion_round",
                    "data": {
                        "message": f"✅ 第 {round_num}/{task.total_rounds} 轮讨论结束",
                        "task_id": task.task_id,
                        "round_num": round_num,
                        "status": "round_end",
                        "timestamp": time.time(),
                    },
                })

            # 讨论结束 — 广播总结通知
            if not state.cancel_event.is_set():
                await broadcast_fn(task.group_id, {
                    "type": "system_message",
                    "data": {
                        "message": f"🏁 讨论结束：{task.goal}",
                        "task_id": task.task_id,
                        "event": "discussion_end",
                        "total_rounds": task.total_rounds,
                        "total_messages": len(discussion_history),
                        "elapsed_seconds": round(time.time() - state.start_time, 1),
                        "timestamp": time.time(),
                    },
                })

        except Exception as e:
            logger.error(f"[错误] 讨论 {task.task_id} 执行异常: {e}", exc_info=True)
            # 广播错误通知
            await broadcast_fn(task.group_id, {
                "type": "system_message",
                "data": {
                    "message": f"❌ 讨论执行出错: {str(e)}",
                    "task_id": task.task_id,
                    "event": "discussion_error",
                    "timestamp": time.time(),
                },
            })
        finally:
            # 清理状态（turn上下文必清：讨论task结束=turn结束，无上下文=旧行为）
            network_state.end_turn()
            state.is_running = False
            self._active_discussions.pop(task.task_id, None)
            logger.info(f"[完成] 讨论 {task.task_id} 已结束")

    def _order_agents_by_role(self, agents: list[DiscussionAgent]) -> list[DiscussionAgent]:
        """按角色顺序排列 Agent: Advisor → Executor → Verifier

        未匹配角色的 Agent 放在末尾。

        Args:
            agents: 原始 Agent 列表

        Returns:
            按角色排序后的 Agent 列表
        """
        # 按角色分组
        role_groups: dict[str, list[DiscussionAgent]] = {}
        for agent in agents:
            role = agent.role.lower()
            if role not in role_groups:
                role_groups[role] = []
            role_groups[role].append(agent)

        # 按 ROLE_ORDER 排序
        ordered = []
        for role in ROLE_ORDER:
            if role in role_groups:
                ordered.extend(role_groups[role])

        # 添加未在 ROLE_ORDER 中的角色
        for role, group in role_groups.items():
            if role not in ROLE_ORDER:
                ordered.extend(group)

        return ordered

    async def _get_agent_response(
        self,
        agent: DiscussionAgent,
        task: DiscussionTask,
        round_num: int,
        history: list[dict],
        cancel_event: asyncio.Event,
    ) -> str:
        """调用 Agent 的 LLM 生成回复

        Args:
            agent: Agent 信息
            task: 讨论任务
            round_num: 当前轮次
            history: 之前的讨论历史
            cancel_event: 取消信号

        Returns:
            Agent 的回复内容，失败返回空字符串或错误标记
        """
        try:
            # 构建系统提示词
            role = agent.role.lower()
            template = SYSTEM_PROMPTS.get(role, SYSTEM_PROMPTS["advisor"])
            system_prompt = template.format(
                agent_name=agent.name,
                goal=task.goal,
            )

            # 如果有约束条件，追加到系统提示词
            if task.constraints:
                constraints_text = "、".join(task.constraints)
                system_prompt += f"\n约束条件：{constraints_text}"

            # 如果有完成标准，追加到系统提示词
            if task.completion_criteria:
                criteria_text = "、".join(task.completion_criteria)
                system_prompt += f"\n完成标准：{criteria_text}"

            # 构建对话消息
            messages = []

            # 如果有历史记录，注入上下文
            if history:
                # 构建历史摘要
                history_parts = []
                for h in history:
                    history_parts.append(
                        f"[第{h['round_num']}轮] {h['name']}({h['agent_id']}): {h['content'][:200]}"
                    )
                history_text = "\n".join(history_parts)

                context = CONTEXT_PROMPT.format(
                    round_num=round_num,
                    total_rounds=task.total_rounds,
                    history=history_text,
                )
                messages.append({"role": "user", "content": context})

            # 添加当前轮次的触发消息
            messages.append({
                "role": "user",
                "content": f"请开始第 {round_num} 轮讨论，发表你的观点。",
            })

            # 创建 LLM 引擎（使用 Agent 指定的模型或默认模型）
            engine = LLMEngine(
                model=agent.model or "",
                system_prompt=system_prompt,
            )

            # 流式调用 LLM，带超时
            full_content = ""
            try:
                # 使用 asyncio.wait_for 实现超时
                async def _stream_llm():
                    """内部流式调用，收集完整回复"""
                    nonlocal full_content
                    async for chunk in engine.chat_stream(
                        messages=messages,
                        cancel_event=cancel_event,
                        system_prompt=system_prompt,
                    ):
                        full_content += chunk
                    return full_content

                result = await asyncio.wait_for(
                    _stream_llm(),
                    timeout=AGENT_TIMEOUT,
                )
                return result

            except asyncio.TimeoutError:
                logger.warning(f"[超时] Agent {agent.name} 第{round_num}轮回复超时 ({AGENT_TIMEOUT}s)")
                # 如果有部分内容，仍然返回
                if full_content:
                    return full_content + "\n[回复超时，部分内容]"
                return ""

        except Exception as e:
            logger.error(f"[错误] Agent {agent.name} LLM调用失败: {e}", exc_info=True)
            return f"[LLM错误: {e}]"
