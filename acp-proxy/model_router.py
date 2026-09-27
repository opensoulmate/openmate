"""智能模型路由模块 — 根据任务复杂度和路由模式自动选择最优LLM

提供4种路由模式（cost/balance/intelligence/auto），支持多provider配置，
通过环境变量覆盖默认模型配置，实现智能的成本-质量平衡。

调用面语义（2026-09-27收口，00:10遗留#4第二批——先定语义再收口）：
1. 本模块=**多提供商路由面**（用户/业务流量）：每个provider有自己的凭据/端点
   （MIMO_API_KEY/OPENAI_API_KEY/CLAUDE_API_KEY/...），统一走
   utils.llm_config.resolve_env_value() 单一真源解析——**.env文件优先于进程环境**
   （opensoul/.env/acp-proxy/.env是运维配置真源）。旧实现import期直接
   os.environ.get：key只写在.env时解析成""→选中的模型不带鉴权→静默401
   （与DNA进化strand/llm_gateway同款根因，禁止重犯）。
2. MIMO_API_KEY 在本模块= **mimo提供商凭据**（与OPENAI_API_KEY同级的历史命名），
   不是 resolve_llm_config 的"进化覆写"语义——两语义并存：
   提供商凭据查 MIMO_API_KEY，极端兜底（全tier无可用模型）查系统默认LLM
   resolve_llm_config(include_mimo_overrides=False)（不跟随MIMO_*进化覆写）。
3. fail-closed-lite：requires_auth的provider若key解析为空→该模型**不参选**
   （显式warning给出期望配置名，401早知道），绝不静默发无鉴权请求；
   ollama等本地provider免key合法。无鉴权本地server的逃逸口=设任意非空
   MODEL_<MODEL名>_API_KEY 或 provider级API_KEY。
"""

import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import Optional

from utils.llm_config import resolve_env_value, resolve_llm_config

logger = logging.getLogger("acp-proxy.model_router")


# ============================================================
# 路由模式枚举
# ============================================================
class RouteMode(str, Enum):
    """4种路由模式"""
    COST = "cost"              # 成本优先 — 便宜快速
    BALANCE = "balance"        # 平衡模式 — 性价比
    INTELLIGENCE = "intelligence"  # 智能优先 — 最强模型
    AUTO = "auto"              # 自动模式 — 根据prompt复杂度动态选择


# ============================================================
# 模型配置数据类
# ============================================================
@dataclass
class ModelConfig:
    """单个模型的完整配置 — 包含API连接信息"""
    name: str           # 模型名称（如 mimo-v2.5-pro）
    provider: str       # 提供商标识（如 openai, deepseek, qwen, mimo, claude, ollama）
    base_url: str       # API基础URL
    api_key: str        # API密钥
    tier: str = "balance"  # 模型层级: cost / balance / intelligence
    max_tokens: int = 4096  # 最大输出token数
    temperature: float = 0.7  # 默认温度
    key_source: str = ""  # key来源（provider路径=file:NAME/env:NAME；兜底路径=resolve_llm_config词表file/env/mimo_env；key原文不进日志）
    requires_auth: bool = True  # False=本地provider（ollama）免key合法


@dataclass
class ModelSelection:
    """模型选择结果 — 返回给调用方的完整信息"""
    model: str          # 模型名称
    base_url: str       # API基础URL
    api_key: str        # API密钥
    provider: str       # 提供商
    mode_used: str      # 实际使用的路由模式
    complexity: float   # 估算的复杂度分数（0-1）
    reason: str         # 选择原因说明
    key_source: str = ""  # key来源（两真源各自词表如实透传，见ModelConfig.key_source注释）


# ============================================================
# 默认模型配置表 — 硬编码，可被环境变量覆盖
# ============================================================
# 每个tier定义一组有序的候选模型，第一个为首选
DEFAULT_MODEL_TABLE: dict[str, list[dict]] = {
    "cost": [
        # 成本优先: 便宜快速，适合简单任务
        {"name": "mimo-auto", "provider": "mimo"},
        {"name": "claude-haiku-4-20250514", "provider": "claude"},
        {"name": "qwen-turbo", "provider": "qwen"},
    ],
    "balance": [
        # 平衡模式: 性价比最优，适合中等复杂度
        {"name": "mimo-v2.5-pro", "provider": "mimo"},
        {"name": "claude-sonnet-4-20250514", "provider": "claude"},
        {"name": "deepseek-chat", "provider": "deepseek"},
    ],
    "intelligence": [
        # 智能优先: 最强模型，适合复杂推理/代码任务
        {"name": "deepseek-r1", "provider": "deepseek"},
        {"name": "gpt-4o", "provider": "openai"},
        {"name": "qwen-max", "provider": "qwen"},
    ],
}

# provider 凭据/端点解析表 — 只存"环境变量别名 + 公开默认端点"，值一律惰性解析。
# 2026-09-27收口（00:10遗留#4第二批）：旧 PROVIDER_DEFAULTS 在 import 期直接
# os.environ.get（key 只写在 .env 文件时解析成 "" → 选中模型不带鉴权 → 静默401），
# 现统一走 resolve_env_value 单一真源（文件优先 + key_source 可观测）。
# api_key_env/base_url_env：别名列表，先者优先（专用别名在前，通用兜底在后）。
# requires_auth=False 的本地 provider（ollama）免 key 合法。
PROVIDER_ENV: dict[str, dict] = {
    "mimo": {
        # MIMO_API_KEY 在此= mimo 提供商凭据（历史命名），非"进化覆写"语义（见模块docstring）
        "api_key_env": ["MIMO_API_KEY", "LLM_API_KEY"],
        "base_url_env": ["MIMO_BASE_URL"],
        "default_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "requires_auth": True,
    },
    "openai": {
        "api_key_env": ["OPENAI_API_KEY"],
        "base_url_env": ["OPENAI_BASE_URL"],
        "default_base_url": "https://api.openai.com/v1",
        "requires_auth": True,
    },
    "claude": {
        "api_key_env": ["CLAUDE_API_KEY", "ANTHROPIC_API_KEY"],
        "base_url_env": ["CLAUDE_BASE_URL"],
        "default_base_url": "https://api.anthropic.com/v1",
        "requires_auth": True,
    },
    "deepseek": {
        "api_key_env": ["DEEPSEEK_API_KEY"],
        "base_url_env": ["DEEPSEEK_BASE_URL"],
        "default_base_url": "https://api.deepseek.com/v1",
        "requires_auth": True,
    },
    "qwen": {
        "api_key_env": ["QWEN_API_KEY", "DASHSCOPE_API_KEY"],
        "base_url_env": ["QWEN_BASE_URL"],
        "default_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "requires_auth": True,
    },
    "ollama": {
        "api_key_env": ["OLLAMA_API_KEY"],
        "base_url_env": ["OLLAMA_BASE_URL"],
        "default_base_url": "http://localhost:11434/v1",
        "requires_auth": False,
    },
}


def resolve_provider_config(provider: str) -> dict:
    """惰性解析 provider 凭据/端点 — 单一真源（.env文件优先于进程环境）。

    返回 {base_url, api_key, key_source, base_url_source, requires_auth}；
    key_source/base_url_source ∈ {"file:NAME", "env:NAME", ""}（可观测，key不进日志）。
    未知 provider 返回空配置（requires_auth=True → 视为无鉴权不参选）。
    """
    spec = PROVIDER_ENV.get(provider, {})
    api_key, key_source = resolve_env_value(spec.get("api_key_env", []))
    base_url, base_url_source = resolve_env_value(
        spec.get("base_url_env", []), default=spec.get("default_base_url", "")
    )
    return {
        "base_url": base_url,
        "api_key": api_key,
        "key_source": key_source,
        "base_url_source": base_url_source,
        "requires_auth": spec.get("requires_auth", True),
    }

# 环境变量覆盖: MODEL_ROUTER_COST / MODEL_ROUTER_BALANCE / MODEL_ROUTER_INTELLIGENCE
# 格式: "provider:model_name" 如 "mimo:mimo-auto,claude:claude-haiku-4-20250514"


def _parse_env_model_list(env_value: str) -> list[dict]:
    """解析环境变量中的模型列表 — 格式: 'provider:model,provider:model'"""
    if not env_value:
        return []
    models = []
    for item in env_value.split(","):
        item = item.strip()
        if ":" in item:
            provider, name = item.split(":", 1)
            models.append({"name": name.strip(), "provider": provider.strip()})
    return models


# Auto模式的关键词列表 — 触发intelligence模式
INTELLIGENCE_KEYWORDS = [
    # 编程相关
    "代码", "编程", "开发", "debug", "调试", "重构", "架构", "设计模式",
    "code", "coding", "programming", "debug", "refactor", "architecture",
    # 分析相关
    "分析", "推理", "逻辑", "算法", "优化", "复杂度",
    "analyze", "reasoning", "logic", "algorithm", "optimize", "complexity",
    # 翻译相关
    "翻译", "translate", "本地化", "i18n",
    # 数学/科学
    "数学", "证明", "公式", "math", "proof", "formula",
    # 写作/创作
    "论文", "方案", "报告", "策划",
]

# 复杂度估算的权重因子
COMPLEXITY_LENGTH_WEIGHT = 0.3      # prompt长度权重
COMPLEXITY_KEYWORD_WEIGHT = 0.4     # 关键词命中权重
COMPLEXITY_STRUCTURE_WEIGHT = 0.3   # 结构复杂度权重（代码块、列表等）


class ModelRouter:
    """智能模型路由器 — 根据任务复杂度和路由模式选择最优模型

    典型用法:
        router = ModelRouter()
        selection = router.select_model("帮我写一个Python排序算法")
        print(selection.model)       # → deepseek-r1
        print(selection.mode_used)   # → intelligence
    """

    def __init__(self, default_mode: str = "auto"):
        """初始化路由器

        Args:
            default_mode: 默认路由模式，可选 auto/cost/balance/intelligence
        """
        self._default_mode = default_mode
        self._lock = Lock()

        # 路由统计
        self._stats = {
            "total_requests": 0,
            "by_mode": {"cost": 0, "balance": 0, "intelligence": 0, "auto": 0},
            "by_model": {},  # model_name → count
            "avg_complexity": 0.0,
            "last_selection": None,  # 最近一次选择的时间戳
        }

        # 构建模型配置表 — 优先读取环境变量覆盖
        self._model_table: dict[str, list[ModelConfig]] = {}
        self._build_model_table()

        logger.info(
            "ModelRouter 初始化完成: mode=%s, 可用模型=%s",
            default_mode,
            {tier: [m.name for m in models] for tier, models in self._model_table.items()},
        )

    def _build_model_table(self):
        """构建模型配置表 — tier覆盖/每模型覆盖/provider凭据全部走单一真源惰性解析"""
        for tier in ["cost", "balance", "intelligence"]:
            # 检查环境变量覆盖（MODEL_ROUTER_<TIER>，文件优先于进程环境）
            env_key = f"MODEL_ROUTER_{tier.upper()}"
            env_models = _parse_env_model_list(resolve_env_value([env_key])[0])

            if env_models:
                # 使用环境变量配置
                raw_models = env_models
                logger.info("Tier %s 使用环境变量覆盖: %s", tier, [m["name"] for m in raw_models])
            else:
                # 使用硬编码默认值
                raw_models = DEFAULT_MODEL_TABLE[tier]

            # 构建 ModelConfig 列表
            configs = []
            for raw in raw_models:
                provider = raw["provider"]
                provider_cfg = resolve_provider_config(provider)

                # 允许每个模型通过 MODEL_<MODEL名>_* 单独覆盖（优先级最高，文件优先）
                model_env_prefix = raw["name"].replace("-", "_").replace(".", "_").upper()
                base_url, _ = resolve_env_value(
                    [f"MODEL_{model_env_prefix}_BASE_URL"],
                    default=provider_cfg["base_url"],
                )
                api_key, key_source = resolve_env_value(
                    [f"MODEL_{model_env_prefix}_API_KEY"],
                    default=provider_cfg["api_key"],
                )
                if key_source == "" and provider_cfg["key_source"]:
                    key_source = provider_cfg["key_source"]

                configs.append(ModelConfig(
                    name=raw["name"],
                    provider=provider,
                    base_url=base_url,
                    api_key=api_key,
                    tier=tier,
                    key_source=key_source,
                    requires_auth=provider_cfg["requires_auth"],
                ))

            self._model_table[tier] = configs

    def estimate_complexity(self, prompt: str) -> float:
        """估算任务复杂度 — 返回0-1之间的浮点数

        综合考虑三个维度:
        1. prompt长度 — 长文本通常更复杂
        2. 关键词命中 — 包含技术关键词说明任务更复杂
        3. 结构复杂度 — 代码块、列表、多轮指令等

        Args:
            prompt: 用户输入的prompt文本

        Returns:
            0.0（最简单）到 1.0（最复杂）
        """
        if not prompt:
            return 0.0

        # 维度1: 长度复杂度 — sigmoid曲线映射
        # 100字以下≈0.1, 500字≈0.5, 2000字≈0.9
        length = len(prompt)
        length_score = min(1.0, length / 2000.0)
        # 对短文本做衰减
        if length < 100:
            length_score = length / 1000.0  # 0-0.1

        # 维度2: 关键词命中 — 检查是否包含技术关键词
        prompt_lower = prompt.lower()
        keyword_hits = sum(1 for kw in INTELLIGENCE_KEYWORDS if kw in prompt_lower)
        # 命中越多越复杂，但有上限
        keyword_score = min(1.0, keyword_hits / 3.0)

        # 维度3: 结构复杂度 — 检测代码块、列表、多段落等
        structure_score = 0.0
        # 代码块
        if "```" in prompt:
            structure_score += 0.3
        # 列表或编号
        if re.search(r"^[\s]*[-*\d]+[.)]\s", prompt, re.MULTILINE):
            structure_score += 0.2
        # 多段落（超过3段）
        paragraphs = [p for p in prompt.split("\n\n") if p.strip()]
        if len(paragraphs) > 3:
            structure_score += 0.2
        # 包含URL或路径
        if re.search(r"https?://|/[\w/]+\.\w+", prompt):
            structure_score += 0.1
        # 包含多语言混合
        has_chinese = bool(re.search(r"[\u4e00-\u9fff]", prompt))
        has_english = bool(re.search(r"[a-zA-Z]{3,}", prompt))
        if has_chinese and has_english:
            structure_score += 0.1
        structure_score = min(1.0, structure_score)

        # 加权综合
        complexity = (
            COMPLEXITY_LENGTH_WEIGHT * length_score
            + COMPLEXITY_KEYWORD_WEIGHT * keyword_score
            + COMPLEXITY_STRUCTURE_WEIGHT * structure_score
        )

        return round(min(1.0, max(0.0, complexity)), 3)

    def _resolve_mode_for_auto(self, prompt: str) -> tuple[str, str]:
        """Auto模式: 根据prompt特征决定实际使用哪个模式

        规则:
        1. prompt < 100字 → cost模式（简单任务省钱）
        2. 包含intelligence关键词 → intelligence模式（复杂任务用强模型）
        3. 其他 → balance模式（中等任务平衡选择）

        Returns:
            (resolved_mode, reason)
        """
        length = len(prompt)

        # 规则1: 短prompt走cost
        if length < 100:
            return RouteMode.COST.value, f"prompt简短({length}字<100)，使用成本优先模式"

        # 规则2: 包含技术关键词走intelligence
        prompt_lower = prompt.lower()
        matched_keywords = [kw for kw in INTELLIGENCE_KEYWORDS if kw in prompt_lower]
        if matched_keywords:
            return RouteMode.INTELLIGENCE.value, (
                f"检测到技术关键词[{','.join(matched_keywords[:3])}]，使用智能优先模式"
            )

        # 规则3: 其他走balance
        return RouteMode.BALANCE.value, "中等复杂度任务，使用平衡模式"

    def _select_model_from_tier(self, tier: str, agent_id: str = "") -> Optional[ModelConfig]:
        """从指定tier的候选列表中选择一个可用模型

        策略: 按顺序选择第一个"可用"的模型（首选优先）。
        可用 = 有api_key，或provider免鉴权（requires_auth=False，如ollama）。
        requires_auth的模型缺key → 不参选（绝不静默发无鉴权请求），显式告警
        给出期望配置名（401早知道）；全tier都缺key时返回None，由select_model
        统一走系统默认LLM兜底（单一真源，缺失=显式报错）。
        如果agent有专属模型配置（AGENT_<ID>_MODEL=provider:model），优先使用。

        Args:
            tier: 模型层级（cost/balance/intelligence）
            agent_id: agent标识符，可用于agent级模型覆盖

        Returns:
            ModelConfig 或 None（如果该tier没有可用模型）
        """
        candidates = self._model_table.get(tier, [])
        if not candidates:
            logger.warning("Tier %s 没有候选模型", tier)
            return None

        # 检查agent专属配置（文件优先于进程环境）
        if agent_id:
            agent_model_env, _ = resolve_env_value([f"AGENT_{agent_id.upper()}_MODEL"])
            if agent_model_env:
                # 格式: "provider:model_name"
                if ":" in agent_model_env:
                    provider, name = agent_model_env.split(":", 1)
                    for cfg in candidates:
                        if cfg.name == name and cfg.provider == provider:
                            logger.info("Agent %s 使用专属模型: %s", agent_id, name)
                            return cfg

        # 默认: 选第一个可用模型（有api_key，或本地provider免key）
        skipped: list[str] = []
        for cfg in candidates:
            if not cfg.requires_auth or cfg.api_key:
                return cfg
            expected = PROVIDER_ENV.get(cfg.provider, {}).get("api_key_env", [])
            skipped.append(f"{cfg.name}(期望 {expected} 或 MODEL_{cfg.name.replace('-', '_').replace('.', '_').upper()}_API_KEY)")

        # 全部缺key：不参选（fail-closed-lite），告警给出期望配置名后交给上层兜底
        logger.warning(
            "Tier %s 所有模型都缺少api_key，不参选: %s",
            tier, "; ".join(skipped),
        )
        return None

    def select_model(
        self,
        prompt: str,
        mode: str = "auto",
        agent_id: str = "",
    ) -> ModelSelection:
        """选择最优模型 — 核心路由方法

        Args:
            prompt: 用户输入的prompt文本
            mode: 路由模式 — auto/cost/balance/intelligence
            agent_id: agent标识符，支持agent级模型覆盖

        Returns:
            ModelSelection 包含选择结果和元信息

        Raises:
            RuntimeError: 所有tier无可用模型且系统默认LLM配置不完整（fail-closed，
                绝不返回无鉴权/瞎猜的配置——调用方可自行降级）
        """
        with self._lock:
            self._stats["total_requests"] += 1

        # 参数校验
        if mode not in [m.value for m in RouteMode]:
            logger.warning("未知路由模式 '%s'，回退到 auto", mode)
            mode = RouteMode.AUTO.value

        # 估算复杂度
        complexity = self.estimate_complexity(prompt)

        # Auto模式: 根据复杂度动态决定实际模式
        actual_mode = mode
        reason = ""
        if mode == RouteMode.AUTO.value:
            actual_mode, reason = self._resolve_mode_for_auto(prompt)
        else:
            reason = f"手动指定 {mode} 模式"

        # 从对应tier选择模型
        model_cfg = self._select_model_from_tier(actual_mode, agent_id)
        if not model_cfg:
            # 回退到balance（最安全的默认选择）
            logger.warning("Tier %s 无可用模型，回退到 balance", actual_mode)
            model_cfg = self._select_model_from_tier("balance", agent_id)
            actual_mode = "balance"
            reason += " (回退到balance)"

        if not model_cfg:
            # 极端情况: 所有tier都无可用模型 → 系统默认LLM单一真源兜底。
            # include_mimo_overrides=False：用户/业务流量不跟随MIMO_*进化覆写；
            # 禁止硬编码模型名/URL/自扫env（llm_config调用方纪律）——配置不完整=显式报错。
            cfg = resolve_llm_config(include_mimo_overrides=False)
            missing = [k for k in ("api_key", "base_url", "model") if not cfg.get(k)]
            if missing:
                logger.error(
                    "所有tier都没有可用模型，且系统默认LLM配置缺失 %s（key_source=%s）——拒绝猜测默认值",
                    missing, cfg.get("key_source", ""),
                )
                raise RuntimeError(f"model_router: 无可用模型且系统默认LLM配置不完整，缺失={missing}")
            logger.warning(
                "所有tier都没有可用模型，回退系统默认LLM（key_source=%s, key_len=%d）",
                cfg["key_source"], len(cfg["api_key"]),
            )
            model_cfg = ModelConfig(
                name=cfg["model"],
                provider="default",
                base_url=cfg["base_url"],
                api_key=cfg["api_key"],
                tier="default",
                key_source=cfg["key_source"],
            )

        # 更新统计
        with self._lock:
            self._stats["by_mode"][actual_mode] = self._stats["by_mode"].get(actual_mode, 0) + 1
            self._stats["by_model"][model_cfg.name] = self._stats["by_model"].get(model_cfg.name, 0) + 1
            # 滑动平均复杂度
            n = self._stats["total_requests"]
            old_avg = self._stats["avg_complexity"]
            self._stats["avg_complexity"] = round(old_avg + (complexity - old_avg) / n, 4)
            self._stats["last_selection"] = time.time()

        selection = ModelSelection(
            model=model_cfg.name,
            base_url=model_cfg.base_url,
            api_key=model_cfg.api_key,
            provider=model_cfg.provider,
            mode_used=actual_mode,
            complexity=complexity,
            reason=reason,
            key_source=model_cfg.key_source,
        )

        logger.info(
            "模型选择: %s (provider=%s, mode=%s, complexity=%.3f, reason=%s)",
            selection.model, selection.provider, selection.mode_used,
            selection.complexity, selection.reason,
        )

        return selection

    def get_config(self) -> dict:
        """获取当前路由配置 — 供API端点使用"""
        config = {}
        for tier, models in self._model_table.items():
            config[tier] = [
                {
                    "name": m.name,
                    "provider": m.provider,
                    "base_url": m.base_url[:30] + "..." if len(m.base_url) > 30 else m.base_url,
                    "has_api_key": bool(m.api_key),
                    "key_source": m.key_source,
                }
                for m in models
            ]
        return {
            "default_mode": self._default_mode,
            "model_table": config,
            "available_modes": [m.value for m in RouteMode],
        }

    def get_status(self) -> dict:
        """获取路由状态和统计信息 — 供API端点使用"""
        with self._lock:
            return {
                "default_mode": self._default_mode,
                "total_requests": self._stats["total_requests"],
                "by_mode": dict(self._stats["by_mode"]),
                "by_model": dict(self._stats["by_model"]),
                "avg_complexity": self._stats["avg_complexity"],
                "last_selection": self._stats["last_selection"],
            }

    def set_mode(self, mode: str) -> bool:
        """修改默认路由模式 — 供API端点使用

        Args:
            mode: 新的默认模式

        Returns:
            是否设置成功
        """
        if mode not in [m.value for m in RouteMode]:
            return False
        old_mode = self._default_mode
        self._default_mode = mode
        logger.info("默认路由模式已修改: %s → %s", old_mode, mode)
        return True


# ============================================================
# 全局单例 — 供app.py和llm_engine.py共享
# ============================================================
_global_router: Optional[ModelRouter] = None


def get_model_router(default_mode: str = "auto") -> ModelRouter:
    """获取全局ModelRouter单例 — 延迟初始化"""
    global _global_router
    if _global_router is None:
        _global_router = ModelRouter(default_mode=default_mode)
    return _global_router


def reset_model_router():
    """重置全局单例 — 仅用于测试"""
    global _global_router
    _global_router = None
