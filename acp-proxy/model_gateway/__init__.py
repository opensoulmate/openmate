"""ModelGateway v1.0 — 统一模型网关模块

多模型路由 + 负载均衡 + 熔断降级 + Token计量

快速开始：
    from model_gateway import ModelGateway, ModelProvider

    gateway = ModelGateway()
    gateway.register_model("openai", "gpt-4o", "https://api.openai.com/v1", "sk-xxx")
    gateway.register_model("ollama", "deepseek-r1", "http://localhost:11434/v1")
    gateway.register_model("mimo", "mimo-v2.5-pro", "https://api.mimo.ai/v1", "key")

    # 自动路由+负载均衡+熔断保护
    response = await gateway.chat_completion(
        messages=[{"role": "user", "content": "你好"}],
        model="gpt-4o",
    )

    # 流式输出
    async for chunk in gateway.chat_completion_stream(
        messages=[{"role": "user", "content": "你好"}],
        model="deepseek-r1",
    ):
        print(chunk, end="")

    # 查看统计
    print(gateway.get_stats())
"""

from .circuit_breaker import CircuitBreaker, CircuitState
from .gateway import (
    AllEndpointsCircuitBrokenError,
    CostTrackerAccountant,
    ModelGateway,
    ModelGatewayError,
    ModelNotFoundError,
)
from .models import ModelConfig, ModelProvider, ModelStats, TokenUsage
from .queue import RequestQueue, RequestQueueError

__all__ = [
    # 核心网关
    "ModelGateway",
    # 数据模型
    "ModelProvider",
    "ModelConfig",
    "TokenUsage",
    "ModelStats",
    # 熔断器
    "CircuitBreaker",
    "CircuitState",
    # 请求排队与并发节流（Pi #6）
    "RequestQueue",
    "RequestQueueError",
    # 用量入账（Pi #6）
    "CostTrackerAccountant",
    # 异常
    "ModelGatewayError",
    "ModelNotFoundError",
    "AllEndpointsCircuitBrokenError",
]

__version__ = "1.0.0"
