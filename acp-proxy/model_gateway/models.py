"""ModelGateway 数据模型 — Provider注册、模型配置、调用计量

定义模型网关所需的核心数据结构：
- ModelProvider: LLM提供商枚举（OpenAI/Ollama/MiMo等）
- ModelConfig: 单个模型端点配置（base_url/api_key/priority等）
- TokenUsage: 单次调用的Token计量记录
- ModelStats: 模型维度的累计统计
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from .circuit_breaker import CircuitBreaker


class ModelProvider(str, Enum):
    """LLM提供商枚举

    覆盖主流兼容OpenAI接口的LLM服务。
    """
    OPENAI = "openai"
    OLLAMA = "ollama"
    MIMO = "mimo"               # 小米MiMo模型
    DEEPSEEK = "deepseek"
    QWEN = "qwen"               # 通义千问
    CLAUDE = "claude"           # Anthropic Claude（通过兼容层）
    CUSTOM = "custom"           # 自定义Provider


@dataclass
class ModelConfig:
    """单个模型端点配置

    每个ModelConfig代表一个具体的模型部署实例（一个base_url）。
    同一个model_name可以有多个实例用于负载均衡。

    Attributes:
        provider: 所属的LLM提供商
        model_name: 模型名称（如 gpt-4o、deepseek-r1、mimo-v2.5-pro）
        base_url: API基础地址（如 https://api.openai.com/v1）
        api_key: API密钥
        priority: 优先级，数字越小优先级越高，默认0
        max_tokens: 该模型支持的最大token数（上下文窗口大小）
        cost_per_1k_input: 每1000个输入token的费用（美元），用于计量
        cost_per_1k_output: 每1000个输出token的费用（美元），用于计量
        enabled: 是否启用该端点
        circuit_breaker: 该端点的熔断器实例
        _round_robin_index: 轮询计数器（内部使用）
    """
    provider: ModelProvider
    model_name: str
    base_url: str
    api_key: str = ""
    priority: int = 0
    max_tokens: int = 128000
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    enabled: bool = True
    circuit_breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    def endpoint_id(self) -> str:
        """返回端点唯一标识：provider@base_url"""
        return f"{self.provider.value}@{self.base_url}"


@dataclass
class TokenUsage:
    """单次LLM调用的Token计量记录

    Attributes:
        model_name: 调用的模型名
        provider: 提供商
        input_tokens: 输入token数
        output_tokens: 输出token数
        total_tokens: 总token数
        cost_usd: 本次调用费用（美元）
        latency_ms: 调用延迟（毫秒）
        timestamp: 调用完成时间戳
        success: 调用是否成功
        error_message: 失败时的错误信息
    """
    model_name: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    success: bool = True
    error_message: str = ""
    # ── Pi #6 增量（错误归一 + 排队计量）：结构化错误字段（决策只看码，
    # error_message 保留原文仅供人工排查）──
    error_code: int = 0          # error_codes ErrorCode（LLM供应商域），0=无
    error_kind: str = ""         # utils.llm_retry 九态分类码（记账口径）
    retryable: bool = False      # 可重试错误标记（结构化判据，非文案正则）
    queue_wait_ms: float = 0.0   # 请求排队等待耗时（并发节流可观测）


@dataclass
class ModelStats:
    """模型维度的累计统计

    按model_name聚合所有调用记录，用于监控和报表。

    Attributes:
        model_name: 模型名
        total_calls: 总调用次数
        successful_calls: 成功次数
        failed_calls: 失败次数
        total_input_tokens: 累计输入token
        total_output_tokens: 累计输出token
        total_cost_usd: 累计费用（美元）
        avg_latency_ms: 平均延迟（毫秒）
        last_call_time: 最后一次调用时间戳
    """
    model_name: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    avg_latency_ms: float = 0.0
    last_call_time: float = 0.0

    def record(self, usage: TokenUsage) -> None:
        """累计一条TokenUsage记录到统计中

        Args:
            usage: 单次调用计量记录
        """
        self.total_calls += 1
        if usage.success:
            self.successful_calls += 1
        else:
            self.failed_calls += 1

        self.total_input_tokens += usage.input_tokens
        self.total_output_tokens += usage.output_tokens
        self.total_cost_usd += usage.cost_usd
        self.last_call_time = usage.timestamp

        # 递推平均延迟
        if self.total_calls > 0:
            self.avg_latency_ms = (
                self.avg_latency_ms * (self.total_calls - 1) + usage.latency_ms
            ) / self.total_calls

    def to_dict(self) -> dict[str, Any]:
        """导出统计为字典"""
        return {
            "model_name": self.model_name,
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "success_rate": (
                round(self.successful_calls / self.total_calls * 100, 2)
                if self.total_calls > 0
                else 0.0
            ),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "last_call_time": self.last_call_time,
        }
