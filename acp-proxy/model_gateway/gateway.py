"""ModelGateway 单例 — 统一模型路由、负载均衡、熔断降级、Token计量

核心功能：
- register_model(): 注册模型端点（provider/model_name/base_url/api_key/priority）
- chat_completion(): 统一调用入口，根据model名自动路由到对应provider
- list_models(): 列出所有已注册模型
- get_stats(): 获取调用统计（Token计量、费用、延迟）

路由逻辑：
1. 按model_name精确匹配已注册的ModelConfig列表
2. 过滤enabled=True且熔断器未open的端点
3. 按priority分组，选最高优先级（数值最小）组
4. 同优先级内轮询（round-robin）负载均衡
5. 所有端点都被熔断时抛出ModelGatewayError

内部使用OpenAI client library调用，统一封装为chat/completions接口。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Any, AsyncGenerator, Optional

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion

from error_codes import vendor_compat
from .circuit_breaker import CircuitBreaker
from .models import ModelConfig, ModelProvider, ModelStats, TokenUsage
from .queue import RequestQueue, RequestQueueError

logger = logging.getLogger("model_gateway.gateway")


class ModelGatewayError(Exception):
    """模型网关异常基类

    Pi #6 错误归一（抄 piRetryErrorNormalizer 功能不抄机制）：携带结构化归一
    产物 normalized（error_codes LLM供应商域码 + retryable 标记），上层重试/降级
    决策**只看码**（retryable 属性），error 文案仅供人工排查。
    """
    normalized: Any = None   # error_codes.vendor_compat.NormalizedError | None
    retryable: bool = False  # 可重试错误标记（结构化判据，非文案正则）


class ModelNotFoundError(ModelGatewayError):
    """请求的模型未注册"""
    pass


class AllEndpointsCircuitBrokenError(ModelGatewayError):
    """该模型所有端点均被熔断"""
    pass


class CostTrackerAccountant:
    """TokenUsage → agent.cost_tracker.CostTracker 持久台账入账适配（Pi #6）。

    网关按端点单价精算 cost_usd → custom_cost 直接入账（台账 MODEL_PRICING
    不覆盖私有端点定价，网关侧精算优先）。request_type="gateway" 与会话路径
    （request_type="chat"）区分口径。
    """

    def __init__(self, tracker, session_id: str = "gateway"):
        self._tracker = tracker
        self._session_id = session_id

    def record_usage(self, usage: TokenUsage) -> None:
        self._tracker.record_usage(
            model=usage.model_name,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            session_id=self._session_id,
            request_type="gateway",
            custom_cost=usage.cost_usd,
        )


class ModelGateway:
    """模型网关单例 — 管理所有LLM模型端点的注册、路由和调用

    使用方法：
        gateway = ModelGateway()
        gateway.register_model("openai", "gpt-4o", "https://api.openai.com/v1", "sk-xxx")
        response = await gateway.chat_completion(
            messages=[{"role": "user", "content": "你好"}],
            model="gpt-4o",
        )

    Attributes:
        _models: 按model_name分组的端点配置列表
        _stats: 按model_name聚合的调用统计
        _usage_log: 最近N条调用计量记录（环形缓冲）
        _round_robin_counters: 按model_name维护的轮询计数器
    """

    def __init__(self, usage_log_size: int = 1000, usage_accountant: Any = "auto",
                 max_concurrent: int = 8, max_queue_depth: int = 32,
                 queue_timeout: float = 30.0) -> None:
        """初始化模型网关

        Args:
            usage_log_size: 调用日志环形缓冲区大小，默认保留最近1000条
            usage_accountant: 用量入账台账（Pi #6）——"auto"=懒创建
                agent.cost_tracker.CostTracker 持久台账（成本可观测的地基）；
                None=只留环形缓冲（零入账）；也可传 .record_usage(usage) 对象/
                callable(usage)（测试注入替身）。
            max_concurrent: 请求并发上限（Pi #6 排队与并发节流，默认 8）
            max_queue_depth: 排队深度上限（超出拒绝，禁无限堆积）
            queue_timeout: 排队等待上限秒（超出显式报因）
        """
        # model_name → [ModelConfig, ...] 按注册顺序存储
        self._models: dict[str, list[ModelConfig]] = defaultdict(list)
        # model_name → ModelStats
        self._stats: dict[str, ModelStats] = {}
        # 调用计量环形缓冲
        self._usage_log: list[TokenUsage] = []
        self._usage_log_size = usage_log_size
        # 轮询计数器：model_name → 当前索引
        self._round_robin_counters: dict[str, int] = defaultdict(int)
        # Pi #6：用量入账台账 + 请求排队/并发节流
        self._usage_accountant = usage_accountant
        self._queue = RequestQueue(max_concurrent=max_concurrent,
                                   max_depth=max_queue_depth,
                                   queue_timeout=queue_timeout)

        logger.info("ModelGateway 初始化完成")

    # ── 模型注册 ──────────────────────────────────────────────────

    def register_model(
        self,
        provider: str | ModelProvider,
        model_name: str,
        base_url: str,
        api_key: str = "",
        priority: int = 0,
        max_tokens: int = 128000,
        cost_per_1k_input: float = 0.0,
        cost_per_1k_output: float = 0.0,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
    ) -> ModelConfig:
        """注册一个模型端点

        同一个model_name可以注册多个端点（不同provider或不同base_url），
        网关会自动进行负载均衡和故障转移。

        Args:
            provider: LLM提供商名称或ModelProvider枚举
            model_name: 模型名称（如 gpt-4o、deepseek-r1）
            base_url: API基础地址
            api_key: API密钥（可选，Ollama等本地服务不需要）
            priority: 优先级，数字越小越优先，默认0
            max_tokens: 模型上下文窗口大小
            cost_per_1k_input: 每1k输入token费用（美元）
            cost_per_1k_output: 每1k输出token费用（美元）
            failure_threshold: 熔断器连续失败阈值，默认5
            recovery_timeout: 熔断器恢复超时（秒），默认60

        Returns:
            注册成功的ModelConfig实例
        """
        # 解析provider枚举
        if isinstance(provider, str):
            try:
                provider_enum = ModelProvider(provider.lower())
            except ValueError:
                provider_enum = ModelProvider.CUSTOM
        else:
            provider_enum = provider

        # 如果api_key为空，尝试从环境变量读取
        if not api_key:
            env_keys = {
                ModelProvider.OPENAI: "OPENAI_API_KEY",
                ModelProvider.DEEPSEEK: "DEEPSEEK_API_KEY",
                ModelProvider.MIMO: "MIMO_API_KEY",
                ModelProvider.QWEN: "QWEN_API_KEY",
                ModelProvider.CLAUDE: "ANTHROPIC_API_KEY",
            }
            env_key = env_keys.get(provider_enum, "")
            if env_key:
                api_key = os.environ.get(env_key, "")

        # 创建熔断器
        cb = CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
        )

        # 构建配置
        config = ModelConfig(
            provider=provider_enum,
            model_name=model_name,
            base_url=base_url.rstrip("/"),
            api_key=api_key,
            priority=priority,
            max_tokens=max_tokens,
            cost_per_1k_input=cost_per_1k_input,
            cost_per_1k_output=cost_per_1k_output,
            circuit_breaker=cb,
        )

        self._models[model_name].append(config)

        # 初始化统计（如果尚未初始化）
        if model_name not in self._stats:
            self._stats[model_name] = ModelStats(model_name=model_name)

        logger.info(
            f"注册模型端点: {provider_enum.value}/{model_name} → {base_url} "
            f"(priority={priority})"
        )
        return config

    # ── 端点选择（路由+负载均衡） ──────────────────────────────────

    def _select_endpoint(self, model_name: str) -> ModelConfig:
        """选择最佳可用端点

        路由策略：
        1. 精确匹配model_name
        2. 过滤enabled=True且熔断器可用的端点
        3. 按priority分组，选最高优先级组
        4. 同优先级内round-robin轮询

        Args:
            model_name: 请求的模型名称

        Returns:
            选中的ModelConfig

        Raises:
            ModelNotFoundError: 模型未注册
            AllEndpointsCircuitBrokenError: 所有端点均被熔断
        """
        configs = self._models.get(model_name)
        if not configs:
            raise ModelNotFoundError(
                f"模型 '{model_name}' 未注册。"
                f"已注册模型: {list(self._models.keys())}"
            )

        # 过滤可用端点（enabled + 熔断器可用）
        available = [
            c for c in configs
            if c.enabled and c.circuit_breaker.is_available()
        ]

        if not available:
            # 检查是否全部被熔断还是全部被禁用
            any_enabled = any(c.enabled for c in configs)
            if not any_enabled:
                raise ModelGatewayError(f"模型 '{model_name}' 所有端点均被禁用")
            raise AllEndpointsCircuitBrokenError(
                f"模型 '{model_name}' 所有端点均被熔断，请稍后重试"
            )

        # 按priority分组，选最高优先级（数值最小）
        min_priority = min(c.priority for c in available)
        best_group = [c for c in available if c.priority == min_priority]

        # 同优先级内round-robin
        idx = self._round_robin_counters[model_name] % len(best_group)
        self._round_robin_counters[model_name] = idx + 1

        selected = best_group[idx]
        logger.debug(
            f"路由选择: {model_name} → {selected.endpoint_id()} "
            f"(priority={selected.priority}, index={idx}/{len(best_group)})"
        )
        return selected

    # ── 统一调用入口 ──────────────────────────────────────────────

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        stream: bool = False,
        **kwargs: Any,
    ) -> ChatCompletion | AsyncGenerator:
        """统一Chat Completion调用入口

        根据model名自动路由到对应provider的端点，内置熔断保护和Token计量。

        Args:
            messages: 对话消息列表，格式 [{"role": "user", "content": "..."}]
            model: 模型名称（如 gpt-4o、deepseek-r1）
            stream: 是否流式返回，默认False
            **kwargs: 其他OpenAI兼容参数（temperature/max_tokens/tools等）

        Returns:
            非流式返回ChatCompletion对象
            流式返回AsyncGenerator

        Raises:
            ModelNotFoundError: 模型未注册
            AllEndpointsCircuitBrokenError: 所有端点均被熔断
            ModelGatewayError: 调用失败
        """
        # 选择端点
        config = self._select_endpoint(model)

        # 构建OpenAI client
        client = AsyncOpenAI(
            api_key=config.api_key or "not-needed",
            base_url=config.base_url,
        )

        # 调用参数
        call_kwargs: dict[str, Any] = {
            "model": config.model_name,
            "messages": messages,
            "stream": stream,
        }
        call_kwargs.update(kwargs)

        # 计时开始
        start_time = time.time()

        # Pi #6 请求排队与并发节流：高峰排队等空位（slot 内含排队计量；
        # 排队饱和/超时=RequestQueueError 直接上抛，不进调用失败计量）
        async with self._queue.slot(model) as slot_info:
            queue_wait_ms = slot_info["queue_wait_ms"]
            try:
                logger.info(
                    f"调用LLM: {config.provider.value}/{config.model_name} "
                    f"via {config.base_url} (stream={stream})"
                )

                response = await client.chat.completions.create(**call_kwargs)

                # 流式模式：返回generator，计量在消费完成后由调用方处理
                if stream:
                    config.circuit_breaker.record_success()
                    return response

                # 非流式：记录计量
                latency_ms = (time.time() - start_time) * 1000
                usage = self._build_usage(config, response, latency_ms,
                                          success=True,
                                          queue_wait_ms=queue_wait_ms)
                self._record_usage(usage)
                config.circuit_breaker.record_success()

                return response

            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                # Pi #6 错误归一：供应商错误边界归一一次 → 结构化码+可重试标记
                norm = self._normalize_error(e, config)
                logger.error(
                    f"LLM调用失败: {config.provider.value}/{config.model_name} "
                    f"error={type(e).__name__}: {e} "
                    f"(code={norm.code} kind={norm.kind} retryable={norm.retryable})"
                )

                # 记录失败计量（含归一错误字段）
                usage = TokenUsage(
                    model_name=config.model_name,
                    provider=config.provider.value,
                    latency_ms=latency_ms,
                    success=False,
                    error_message=str(e),
                    error_code=norm.code,
                    error_kind=norm.kind,
                    retryable=norm.retryable,
                    queue_wait_ms=queue_wait_ms,
                )
                self._record_usage(usage)
                config.circuit_breaker.record_failure()

                err = ModelGatewayError(
                    f"模型 '{model}' 调用失败({config.endpoint_id()}): {e}"
                )
                err.normalized = norm
                err.retryable = norm.retryable
                raise err from e

    # ── 流式调用辅助 ──────────────────────────────────────────────

    async def chat_completion_stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """流式Chat Completion — 逐token返回content字符串

        封装了stream=True的chat_completion，自动收集完整响应用于计量。

        Args:
            messages: 对话消息列表
            model: 模型名称
            **kwargs: 其他OpenAI兼容参数

        Yields:
            逐chunk的content字符串片段
        """
        config = self._select_endpoint(model)
        client = AsyncOpenAI(
            api_key=config.api_key or "not-needed",
            base_url=config.base_url,
        )

        call_kwargs: dict[str, Any] = {
            "model": config.model_name,
            "messages": messages,
            "stream": True,
        }
        call_kwargs.update(kwargs)

        start_time = time.time()
        collected_content = ""
        input_tokens = 0
        output_tokens = 0

        # Pi #6 请求排队与并发节流：流式期间全程持位（消费完才释放）
        async with self._queue.slot(model) as slot_info:
            queue_wait_ms = slot_info["queue_wait_ms"]
            try:
                stream = await client.chat.completions.create(**call_kwargs)
                async for chunk in stream:  # type: ignore
                    if chunk.choices and chunk.choices[0].delta.content:
                        content = chunk.choices[0].delta.content
                        collected_content += content
                        yield content

                    # 流式usage（部分provider支持）
                    if hasattr(chunk, "usage") and chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens or input_tokens
                        output_tokens = chunk.usage.completion_tokens or output_tokens

                # 流结束，记录计量
                latency_ms = (time.time() - start_time) * 1000
                total_tokens = input_tokens + output_tokens
                cost = self._calculate_cost(config, input_tokens, output_tokens)

                usage = TokenUsage(
                    model_name=config.model_name,
                    provider=config.provider.value,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    cost_usd=cost,
                    latency_ms=latency_ms,
                    success=True,
                    queue_wait_ms=queue_wait_ms,
                )
                self._record_usage(usage)
                config.circuit_breaker.record_success()

            except Exception as e:
                latency_ms = (time.time() - start_time) * 1000
                # Pi #6 错误归一：结构化码+可重试标记（决策只看码）
                norm = self._normalize_error(e, config)
                usage = TokenUsage(
                    model_name=config.model_name,
                    provider=config.provider.value,
                    latency_ms=latency_ms,
                    success=False,
                    error_message=str(e),
                    error_code=norm.code,
                    error_kind=norm.kind,
                    retryable=norm.retryable,
                    queue_wait_ms=queue_wait_ms,
                )
                self._record_usage(usage)
                config.circuit_breaker.record_failure()

                err = ModelGatewayError(
                    f"模型 '{model}' 流式调用失败: {e}"
                )
                err.normalized = norm
                err.retryable = norm.retryable
                raise err from e

    # ── 查询接口 ──────────────────────────────────────────────────

    def list_models(self) -> list[dict[str, Any]]:
        """列出所有已注册模型及其端点信息

        Returns:
            模型信息列表，每个元素包含model_name、endpoints数量、provider列表等
        """
        result = []
        for model_name, configs in self._models.items():
            endpoints = []
            for c in configs:
                endpoints.append({
                    "provider": c.provider.value,
                    "base_url": c.base_url,
                    "priority": c.priority,
                    "enabled": c.enabled,
                    "circuit_breaker": c.circuit_breaker.to_dict(),
                })
            result.append({
                "model_name": model_name,
                "endpoint_count": len(configs),
                "providers": list(set(c.provider.value for c in configs)),
                "endpoints": endpoints,
            })
        return result

    def get_stats(self, model_name: Optional[str] = None) -> dict[str, Any]:
        """获取调用统计

        Args:
            model_name: 指定模型名则返回该模型统计，否则返回全局汇总

        Returns:
            统计数据字典
        """
        if model_name:
            stats = self._stats.get(model_name)
            if not stats:
                return {"error": f"模型 '{model_name}' 无统计记录"}
            return stats.to_dict()

        # 全局汇总
        total_calls = sum(s.total_calls for s in self._stats.values())
        total_cost = sum(s.total_cost_usd for s in self._stats.values())
        total_input = sum(s.total_input_tokens for s in self._stats.values())
        total_output = sum(s.total_output_tokens for s in self._stats.values())

        return {
            "total_calls": total_calls,
            "total_cost_usd": round(total_cost, 6),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": total_input + total_output,
            "registered_models": list(self._models.keys()),
            "model_count": len(self._models),
            "per_model": {
                name: stats.to_dict() for name, stats in self._stats.items()
            },
        }

    def get_recent_usage(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近N条调用记录

        Args:
            limit: 返回条数，默认20

        Returns:
            最近的调用计量记录列表
        """
        recent = self._usage_log[-limit:]
        return [
            {
                "model_name": u.model_name,
                "provider": u.provider,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "total_tokens": u.total_tokens,
                "cost_usd": round(u.cost_usd, 6),
                "latency_ms": round(u.latency_ms, 2),
                "success": u.success,
                "error_message": u.error_message,
                "error_code": u.error_code,
                "error_kind": u.error_kind,
                "retryable": u.retryable,
                "queue_wait_ms": round(u.queue_wait_ms, 3),
                "timestamp": u.timestamp,
            }
            for u in reversed(recent)
        ]

    # ── 内部辅助 ──────────────────────────────────────────────────

    def _build_usage(
        self,
        config: ModelConfig,
        response: ChatCompletion,
        latency_ms: float,
        success: bool,
        queue_wait_ms: float = 0.0,
    ) -> TokenUsage:
        """从ChatCompletion响应构建TokenUsage记录

        Args:
            config: 模型配置
            response: OpenAI ChatCompletion响应
            latency_ms: 调用延迟（毫秒）
            success: 调用是否成功
            queue_wait_ms: 排队等待耗时（Pi #6 计量）

        Returns:
            TokenUsage计量记录
        """
        input_tokens = 0
        output_tokens = 0
        total_tokens = 0

        if response.usage:
            input_tokens = response.usage.prompt_tokens or 0
            output_tokens = response.usage.completion_tokens or 0
            total_tokens = response.usage.total_tokens or 0

        cost = self._calculate_cost(config, input_tokens, output_tokens)

        return TokenUsage(
            model_name=config.model_name,
            provider=config.provider.value,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            success=success,
            queue_wait_ms=queue_wait_ms,
        )

    @staticmethod
    def _normalize_error(e: Exception, config: ModelConfig):
        """Pi #6 错误归一：供应商错误边界归一一次（error_codes.vendor_compat）。

        OpenAI SDK 异常带 status_code/body → 归一到结构化码+retryable 标记
        （可重试错误标记的 SoulMate 版：结构化判据，不抄 piRetryErrorNormalizer
        的文案正则改写机制）。归一自身失败只降级为 UNKNOWN（禁破坏错误路径）。
        """
        try:
            return vendor_compat.normalize(
                provider=config.provider.value,
                status=getattr(e, "status_code", None),
                payload=getattr(e, "body", None) or getattr(e, "message", None),
                exc=e,
            )
        except Exception as ne:  # pragma: no cover — 归一兜底
            logger.warning(f"错误归一失败（降级 UNKNOWN）: {ne}")
            return vendor_compat.normalize(
                provider=config.provider.value, exc=e)

    def _calculate_cost(
        self,
        config: ModelConfig,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """计算调用费用

        Args:
            config: 模型配置（含费用单价）
            input_tokens: 输入token数
            output_tokens: 输出token数

        Returns:
            费用（美元）
        """
        cost = (
            input_tokens / 1000 * config.cost_per_1k_input
            + output_tokens / 1000 * config.cost_per_1k_output
        )
        return round(cost, 8)

    def _record_usage(self, usage: TokenUsage) -> None:
        """记录调用计量到统计和日志，并**入账**持久台账（Pi #6 用量统计入账）。

        Args:
            usage: 单次调用计量记录
        """
        # 累计到模型统计
        stats = self._stats.get(usage.model_name)
        if stats:
            stats.record(usage)

        # 追加到环形缓冲
        self._usage_log.append(usage)
        if len(self._usage_log) > self._usage_log_size:
            self._usage_log = self._usage_log[-self._usage_log_size:]

        # Pi #6 用量入账：网关计量 → 持久成本台账（成本可观测的地基）。
        # best-effort（台账故障绝不破坏调用流）但失败必须可见（warning）。
        self._account_usage(usage)

        logger.info(
            f"计量记录: {usage.model_name} "
            f"tokens={usage.input_tokens}+{usage.output_tokens}={usage.total_tokens} "
            f"cost=${usage.cost_usd:.6f} latency={usage.latency_ms:.0f}ms "
            f"success={usage.success}"
        )

    def _account_usage(self, usage: TokenUsage) -> None:
        """入账到台账（"auto"=懒创建 CostTracker；None=不入账）。"""
        accountant = self._usage_accountant
        if accountant is None:
            return
        if accountant == "auto":
            try:
                from agent.cost_tracker import CostTracker
                accountant = CostTrackerAccountant(CostTracker())
            except Exception as e:
                logger.warning(f"用量台账不可用（降级只留环形缓冲）: {e}")
                self._usage_accountant = None
                return
            self._usage_accountant = accountant
        try:
            if callable(accountant):
                accountant(usage)
            else:
                accountant.record_usage(usage)
        except Exception as e:
            logger.warning(f"用量入账失败（调用流不受影响，台账故障可见）: {e}")
