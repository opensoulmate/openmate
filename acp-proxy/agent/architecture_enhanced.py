"""架构增强模块 — 将所有P0组件统一集成

这是openmate-rewrite-blueprint的落地实现，整合了98个Agent项目调研的核心结论：

P0组件清单：
1. Writer Claim Fencing (writer_fence.py) — 防并发写坏会话
2. Lane-aware Command Queue (lane_queue.py) — 消息排队+优先级
3. 分层超时体系 (layered_timeouts.py) — runtime/model/provider/wait
4. 工具失败三级 (tool_errors.py) — ignore/warn/raise + doom loop
5. 编辑安全系统 (edit_safety.py) — SEARCH/REPLACE + 原子写入 + 截断检测

Usage:
    from agent.architecture_enhanced import EnhancedArchitecture
    
    # 在SoulMateAgent.__init__中
    self._arch = EnhancedArchitecture()
    
    # 在prompt方法中
    async with self._arch.session_guard(session_id) as guard:
        ...
    
    # 工具调用时
    result = await self._arch.call_tool(session_id, tool_name, fn, **args)
    
    # 文件编辑时
    edit_result = self._arch.edit_file(path, old_string, new_string)
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Callable, Coroutine

from agent.writer_fence import SessionWriterFence, WriteAction
from agent.lane_queue import LaneQueue, Lane
from agent.layered_timeouts import LayeredTimeoutManager, TimeoutConfig, TimeoutLayer
from agent.tool_errors import ToolErrorHandler, FailureAction, ErrorCategory
from agent.edit_safety import EditSafetyGuard, EditConfig, EditResult
from agent.context_compression import ContextCompressor
from agent.observability import ObservabilityManager, SpanType, SpanStatus
from agent.permissions import PermissionManager, PermissionLevel
from agent.eval_pipeline import EvalPipeline, DEFAULT_EVAL_CASES
from agent.context_budget import ContextBudgetManager, TokenBudget
from agent.tool_cache import ToolResultCache
from agent.agent_checkpoint import AgentCheckpointManager
from agent.agent_tracer import AgentTracer
from agent.local_model import LocalModelRouter, LocalModelConfig
from agent.file_index import SessionFileIndex
from agent.cost_tracker import CostTracker, BudgetConfig
from agent.smart_retry import SmartRetryManager, RetryConfig
from agent.multi_agent import MultiAgentCoordinator
from agent.config_manager import HotConfigManager
from agent.event_bus import EventBus, get_event_bus
from agent.session_fsm import SessionStateMachine
from agent.output_validator import OutputValidator
from agent.memory_retrieval import MemoryRetrievalEngine
from agent.stream_manager import StreamingResponseManager
from agent.parallel_executor import ParallelToolExecutor

logger = logging.getLogger("acp-agent.architecture")


class EnhancedArchitecture:
    """统一的架构增强管理器
    
    整合所有P0组件，提供统一的接口给SoulMateAgent使用。
    """
    
    def __init__(
        self,
        timeout_config: TimeoutConfig | None = None,
        edit_config: EditConfig | None = None,
        max_workers: int = 4,
    ):
        # 1. Writer Fencing
        # P1插话修复：QUEUE→REJECT。
        # QUEUE模式的等待锁与claim未绑定（claim释放不signal lock，竞争者acquire到空闲锁
        # 立即返回→覆盖活跃claim）= 并发prompt都能"获取"栅栏，串行化完全失效；
        # 且插话语义（Khoj interrupt_queue/goose Steer）要求运行中消息立即拒绝→进插话队列
        # 在turn间隙注入，而非等待整个任务结束。session_guard唯一消费方=soulmate prompt()。
        # stale_after=1200s 对齐 turn_watchdog 的回合上限 960s（2026-10-23）：心跳已按轮
        # 接线（soulmate 主循环 watchdog.beat 同点位），过期抢夺只兜真死 writer——此前
        # 180s < 长回合时长且心跳零接线，claim 被当死 writer 抢走 → 并发回合双份生成。
        self.writer_fence = SessionWriterFence(
            action=WriteAction.REJECT,
            timeout=60.0,
            stale_after=1200.0,
        )
        
        # 2. Lane Queue
        self.lane_queue = LaneQueue(
            max_workers=max_workers,
            max_per_session_per_lane=2,
            task_timeout=300.0,
        )
        
        # 3. Layered Timeouts
        self.timeouts = LayeredTimeoutManager(
            config=timeout_config or TimeoutConfig()
        )
        
        # 4. Tool Error Handler
        self.tool_errors = ToolErrorHandler()
        # 注册默认策略
        self.tool_errors.register_tool("read_file", FailureAction.WARN)
        self.tool_errors.register_tool("write_file", FailureAction.RAISE)
        self.tool_errors.register_tool("edit_file", FailureAction.RAISE)
        self.tool_errors.register_tool("delete_file", FailureAction.RAISE)
        self.tool_errors.register_tool("web_search", FailureAction.WARN)
        self.tool_errors.register_tool("web_extract", FailureAction.WARN)
        self.tool_errors.register_tool("execute_code", FailureAction.WARN)
        self.tool_errors.register_tool("terminal", FailureAction.WARN)
        
        # 5. Edit Safety Guard
        self.edit_guard = EditSafetyGuard(
            config=edit_config or EditConfig()
        )
        
        # 6. Context Compressor
        self.context_compressor = ContextCompressor(max_tokens=8000)
        
        # 7. Observability
        self.observability = ObservabilityManager()
        
        # 8. Permission Manager
        self.permissions = PermissionManager()
        
        # 9. Eval Pipeline
        self.eval_pipeline = EvalPipeline()
        for case in DEFAULT_EVAL_CASES:
            self.eval_pipeline.add_case(case)
        
        # 11. Context Budget Manager
        self.context_budget = ContextBudgetManager()
        
        # 12. Tool Result Cache
        self.tool_cache = ToolResultCache()
        
        # 13. Agent Checkpoint Manager
        self.checkpoint_mgr = AgentCheckpointManager()
        
        # 14. Agent Tracer
        self.tracer = AgentTracer()
        
        # 15. Local Model Router
        self.local_models = LocalModelRouter()
        
        # 16. Session File Index
        self.file_index = SessionFileIndex()
        
        # 17. Cost Tracker
        self.cost_tracker = CostTracker()
        
        # 18. Smart Retry Manager
        self.retry_manager = SmartRetryManager()
        
        # 19. Multi-Agent Coordinator
        self.multi_agent = MultiAgentCoordinator()
        
        # 20. Hot Config Manager
        self.config_manager = HotConfigManager()
        
        # 21. Event Bus
        self.event_bus = get_event_bus()
        
        # 22. Session State Machine
        self.session_fsm = SessionStateMachine()
        
        # 23. Output Validator
        self.output_validator = OutputValidator()
        
        # 24. Memory Retrieval Engine
        self.memory_engine = MemoryRetrievalEngine()
        
        # 25. Streaming Response Manager
        self.stream_manager = StreamingResponseManager()
        
        # 26. Parallel Tool Executor
        self.parallel_executor = ParallelToolExecutor()
        
        # 统计
        self._start_time = time.time()
        self._operation_count = 0
    
    async def start(self):
        """启动异步组件"""
        await self.lane_queue.start()
        logger.info("[architecture] Enhanced architecture started")
    
    async def stop(self):
        """优雅关闭"""
        await self.lane_queue.stop()
        logger.info("[architecture] Enhanced architecture stopped")
    
    @asynccontextmanager
    async def session_guard(self, session_id: str, writer_id: str = "default"):
        """会话级写入保护
        
        Usage:
            async with self.arch.session_guard(session_id, "prompt") as acquired:
                if not acquired:
                    return error
                ... safe to write ...
        """
        async with self.writer_fence.write(session_id, writer_id=writer_id) as acquired:
            yield acquired
    
    async def call_tool(
        self,
        session_id: str,
        tool_name: str,
        fn: Callable[..., Coroutine],
        *,
        lane: Lane = Lane.NORMAL,
        timeout_layer: TimeoutLayer = TimeoutLayer.PROVIDER,
        **kwargs,
    ) -> Any:
        """统一的工具调用接口
        
        整合了：
        - Lane Queue（优先级排队）
        - Layered Timeout（分层超时+重试）
        - Tool Error Handler（三级错误处理+doom loop检测）
        """
        self._operation_count += 1
        
        try:
            # 通过lane queue调度，带超时
            result = await self.timeouts.call(
                layer=timeout_layer,
                operation=tool_name,
                fn=self.lane_queue.submit,
                session_id=session_id,
                fn_inner=fn,
                lane=lane,
                session_id_inner=session_id,
                **kwargs,
            )
            
            # 记录成功
            self.tool_errors.record_success(session_id, tool_name)
            return result
            
        except Exception as e:
            # 处理错误
            error = self.tool_errors.handle_error(
                session_id=session_id,
                tool_name=tool_name,
                error=e,
                args=kwargs,
            )
            
            if error.action == FailureAction.RAISE:
                raise
            
            # 返回结构化的错误消息（给模型看）
            return error.to_model_message()
    
    def edit_file(
        self,
        path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
    ) -> EditResult:
        """安全的文件编辑"""
        return self.edit_guard.edit_file(
            path=path,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
    
    def write_file(
        self,
        path: str,
        content: str,
        is_new_file: bool = True,
    ) -> EditResult:
        """安全的文件写入"""
        return self.edit_guard.write_file(
            path=path,
            content=content,
            is_new_file=is_new_file,
        )
    
    def read_file(self, path: str) -> str | None:
        """读取文件（标记为已读）"""
        return self.edit_guard.read_file(path)
    
    def get_full_stats(self) -> dict:
        """获取所有组件的完整统计"""
        return {
            "uptime": f"{time.time() - self._start_time:.0f}s",
            "total_operations": self._operation_count,
            "writer_fence": self.writer_fence.get_stats(),
            "lane_queue": self.lane_queue.get_stats(),
            "timeouts": self.timeouts.get_stats(),
            "tool_errors": self.tool_errors.get_stats(),
            "edit_guard": self.edit_guard.get_stats(),
            "context_compressor": self.context_compressor.get_stats(),
            "observability": self.observability.get_stats(),
            "permissions": self.permissions.get_stats(),
            "eval_pipeline": self.eval_pipeline.get_stats(),
            "context_budget": self.context_budget.get_stats(),
            "tool_cache": self.tool_cache.get_stats(),
            "checkpoints": self.checkpoint_mgr.get_stats(),
            "tracer": self.tracer.get_stats(),
            "local_models": self.local_models.get_stats(),
            "file_index": self.file_index.get_stats(),
            "cost_tracker": self.cost_tracker.get_stats(),
            "retry_manager": self.retry_manager.get_stats(),
            "multi_agent": self.multi_agent.get_stats(),
            "config_manager": self.config_manager.get_stats(),
            "event_bus": self.event_bus.get_stats(),
            "session_fsm": self.session_fsm.get_stats(),
            "output_validator": self.output_validator.get_stats(),
            "memory_engine": self.memory_engine.get_stats(),
            "stream_manager": self.stream_manager.get_stats(),
            "parallel_executor": self.parallel_executor.get_stats(),
        }
    
    def get_session_stats(self, session_id: str) -> dict:
        """获取特定会话的统计"""
        return {
            "writer_fence": {
                sid: info
                for sid, info in self.writer_fence.get_stats()["active_claims"].items()
                if sid == session_id
            },
            "tool_errors": self.tool_errors.get_stats(session_id),
        }
