"""
上下文预算管理器 — 借鉴Claude Code的context window管理策略
核心思想：智能分配token预算，优先保留系统提示+工具定义+最近消息
支持：动态裁剪、重要性加权、自动压缩触发
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from enum import IntEnum

logger = logging.getLogger("acp-proxy.context-budget")

# ── kilocode overflow.ts移植：reserved buffer + 输入限额优先（supplement3 #17）──
# COMPACTION_BUFFER：kilocode overflow.ts "const COMPACTION_BUFFER = 20_000"——压缩缓冲
# 的默认预留（为压缩摘要/后续输出留出的输入侧余量）。
COMPACTION_BUFFER = 20_000
# 配置失真（限额缺失/自相矛盾）时的fail-safe兜底=既有硬编码8000行为（见model_history_target）。
DEFAULT_HISTORY_TARGET_TOKENS = 8_000


def max_output_tokens(model_output_limit: int, output_cap: int) -> int:
    """kilocode ProviderTransform.maxOutputTokens：min(model.limit.output, outputTokenMax) || outputTokenMax。

    模型未声明输出上限（0）时回退调用方cap——JS `||` 对0回退的语义逐行对齐（min结果为0即回退）。
    """
    return min(int(model_output_limit or 0), int(output_cap or 0)) or int(output_cap or 0)


def reserved_tokens(
    model_output_limit: int, output_cap: int, reserved: Optional[int] = None
) -> int:
    """kilocode overflow.ts reserved：cfg.compaction.reserved ?? min(COMPACTION_BUFFER, maxOutputTokens)。

    显式预留（cfg.compaction.reserved语义，含0）始终优先；缺省=min(20k, 模型最大输出)。
    """
    if reserved is not None:
        return max(0, int(reserved))
    return max(0, min(COMPACTION_BUFFER, max_output_tokens(model_output_limit, output_cap)))


def usable_input(
    context_limit: int,
    model_output_limit: int = 0,
    output_cap: int = 0,
    input_limit: int = 0,
    reserved: Optional[int] = None,
) -> int:
    """kilocode overflow.ts usable()——输入侧可用预算，**输入限额优先**（supplement3 #17）。

    双限额模型（model.limit.input存在，如"1M输入/32k输出"分离限额）：输入预算=input_limit
    只减压缩预留——输出不占输入窗口；单一context窗口模型：输入预算=context−最大输出——
    输出从同一窗口出。两个分支的减法对象不同正是"双限额模型比单一context窗口精确"的核心，
    勿"统一"简化。context_limit==0（模型未声明窗口）→0，与kilocode同。
    """
    context_limit = int(context_limit or 0)
    if context_limit == 0:
        return 0
    if input_limit:
        return max(
            0, int(input_limit) - reserved_tokens(model_output_limit, output_cap, reserved)
        )
    return max(0, context_limit - max_output_tokens(model_output_limit, output_cap))


class MessageImportance(IntEnum):
    """消息重要性等级 — 决定裁剪优先级"""
    CRITICAL = 100    # 系统提示、工具定义
    HIGH = 80         # 用户最新消息、AI最新回复
    MEDIUM = 60       # 工具调用结果
    NORMAL = 40       # 普通对话
    LOW = 20          # 旧的工具输出
    DISCARDABLE = 10  # 可丢弃的调试信息


@dataclass
class TokenBudget:
    """Token预算分配"""
    total_window: int = 128000
    system_reserve: int = 8000      # 系统提示+工具定义
    response_reserve: int = 8000    # 预留给响应的token
    min_recent_messages: int = 6    # 至少保留的最近消息数
    compression_threshold: float = 0.75  # 触发压缩的阈值
    # ── 模型双限额（kilocode model.limit，supplement3 #17）──
    input_limit: int = 0            # 模型独立输入限额（0=未声明→走context−输出分支）
    model_output_limit: int = 0     # 模型输出上限（0=未声明）
    output_cap: int = 0             # 调用方输出cap（ProviderTransform.maxOutputTokens第二参）
    compaction_reserved: Optional[int] = None  # 显式压缩预留（cfg.compaction.reserved语义，None=默认min(20k,输出)）

    @property
    def available_for_history(self) -> int:
        return self.total_window - self.system_reserve - self.response_reserve

    @property
    def usable_input_tokens(self) -> int:
        """整个输入侧可用预算（kilocode overflow.ts usable()，输入限额优先）——
        历史+system+工具定义共享此窗口。"""
        return usable_input(
            self.total_window,
            self.model_output_limit,
            self.output_cap,
            self.input_limit,
            self.compaction_reserved,
        )

    @property
    def compression_trigger_at(self) -> int:
        return int(self.available_for_history * self.compression_threshold)


@dataclass
class ManagedMessage:
    """带元数据的管理消息"""
    role: str
    content: str
    importance: MessageImportance = MessageImportance.NORMAL
    timestamp: float = field(default_factory=time.time)
    token_estimate: int = 0
    tool_call_id: str = ""
    compressed: bool = False

    def estimate_tokens(self) -> int:
        """粗略估算token数（中文1字≈1token，英文1词≈1.3token）"""
        if self.token_estimate:
            return self.token_estimate
        # 简单估算：字符数 / 2（中英混合）
        self.token_estimate = max(1, len(self.content) // 2)
        return self.token_estimate


class ContextBudgetManager:
    """上下文预算管理器"""

    def __init__(self, budget: Optional[TokenBudget] = None):
        self.budget = budget or TokenBudget()
        self._messages: list[ManagedMessage] = []
        self._total_tokens: int = 0
        self._compression_count: int = 0
        # 估算校准因子（d439f163遗留#3）：由token_attribution账本provider回填推出
        # （Σactual/Σestimated），manage()的裁剪估算=canonical estimate_tokens×此因子。
        # 默认1.0；调用方（soulmate_agent）在真实消息路径按TTL缓存刷新。
        self.calibration_factor: float = 1.0

    def manage(self, messages: list, max_tokens: Optional[int] = None) -> list:
        """按预算裁剪会话历史，返回供LLM请求使用的子集（原顺序保留）。

        背景（P0静默死路径修复）：soulmate_agent._prompt_inner此前调用本方法，但
        ContextBudgetManager上并不存在manage——AttributeError被except:pass静默吞掉，
        上下文预算裁剪从未生效（"写了≠接线了"）。本方法为该调用点的真实实现。

        估算公式（估算校准闭环）：token数用agent.token_attribution.estimate_tokens
        （canonical CJK感知公式，与归因/回填/校准同一公式——ManagedMessage的len//2
        旧估算仅用于add_message路径），再乘calibration_factor：provider视角系统性
        低估时提前触发裁剪，压缩决策与权威计数对齐。

        裁剪策略：从最旧的非保留消息开始丢弃；始终保留system消息与最后一条消息
        （即使其本身超预算——最新用户输入不可丢）；预算内不裁剪（原样返回副本）。
        空输入返回[]。fail-safe：方法内部不抛异常语义由调用方try/except兜底。
        """
        if not messages:
            return []
        from agent.token_attribution import estimate_content_tokens as _canonical_estimate

        target = max_tokens or self.budget.available_for_history
        factor = (
            self.calibration_factor
            if self.calibration_factor and self.calibration_factor > 0
            else 1.0
        )

        def _calibrated(msg) -> int:
            # 19.1：content 可能是多模态 block 列表（input_audio 等），
            # 用块语义估算——旧 str() 形态把 base64 载荷当文本计（虚高致裁剪提前）。
            return int(_canonical_estimate((msg or {}).get("content", "")) * factor)

        last_idx = len(messages) - 1
        must_keep = {
            i
            for i, m in enumerate(messages)
            if str((m or {}).get("role", "") or "") == "system"
        } | {last_idx}

        total = sum(_calibrated(m) for m in messages)
        if total <= target:
            return list(messages)

        selected = set(must_keep)
        used = sum(_calibrated(messages[i]) for i in selected)
        for i in range(last_idx - 1, -1, -1):  # 次新→旧依次保留，预算尽即止（最旧先丢）
            if i in selected:
                continue
            tok = _calibrated(messages[i])
            if used + tok > target:
                break
            selected.add(i)
            used += tok
        result = [m for i, m in enumerate(messages) if i in selected]
        self._compression_count += 1
        logger.info(
            f"[context-budget] manage #{self._compression_count}: "
            f"{len(messages)}→{len(result)}条 "
            f"(~{total}→~{used} tokens, factor={factor:.4f}, target={target})"
        )
        return result

    def model_history_target(
        self,
        context_limit: int,
        output_limit: int,
        input_limit: int = 0,
        reserved: Optional[int] = None,
        output_cap: Optional[int] = None,
        fallback: int = DEFAULT_HISTORY_TARGET_TOKENS,
    ) -> int:
        """kilocode overflow.ts usable()推导的会话历史token预算（supplement3 #17）。

        目标 = usable_input(整输入预算) − system_reserve（system提示+工具定义占用的
        输入侧预留，TokenBudget.system_reserve语义）。输入限额优先：双限额模型只减
        reserved=min(20k,最大输出)；单窗口模型减全量最大输出。

        fail-safe护栏（**有意偏离kilocode**，此处注明）：kilocode的model限额来自
        models.dev权威目录；本侧限额是env声明值可能失真（如context<输出上限的自相矛盾
        配置、0窗口）。推导结果<=0时**不把历史裁到只剩最后一条**，WARNING可见并回退
        fallback（=既有硬编码8000行为，行为不回退）。
        """
        cap = output_limit if output_cap is None else output_cap
        usable = usable_input(context_limit, output_limit, cap, input_limit, reserved)
        if usable <= 0:
            logger.warning(
                "[context-budget] 模型限额配置失真（context=%s output=%s input_limit=%s）"
                "→ usable=%s，回退 fallback=%s",
                context_limit, output_limit, input_limit, usable, fallback,
            )
            return fallback
        target = usable - self.budget.system_reserve
        if target <= 0:
            logger.warning(
                "[context-budget] usable=%s 不足以覆盖 system_reserve=%s，回退 fallback=%s",
                usable, self.budget.system_reserve, fallback,
            )
            return fallback
        return target

    def add_message(
        self,
        role: str,
        content: str,
        importance: MessageImportance = MessageImportance.NORMAL,
        tool_call_id: str = ""
    ) -> ManagedMessage:
        """添加消息到管理器"""
        msg = ManagedMessage(
            role=role,
            content=content,
            importance=importance,
            timestamp=time.time(),
            tool_call_id=tool_call_id,
        )
        msg.estimate_tokens()
        self._messages.append(msg)
        self._total_tokens += msg.token_estimate

        # 检查是否需要压缩
        if self._total_tokens > self.budget.compression_trigger_at:
            self._auto_compress()

        return msg

    def get_messages_for_llm(self, max_tokens: Optional[int] = None) -> list[dict]:
        """获取适合发送给LLM的消息列表（按预算裁剪）"""
        target = max_tokens or self.budget.available_for_history

        # 按重要性和时间排序：重要+新的优先保留
        scored = []
        for i, msg in enumerate(self._messages):
            # 新近度加权：越新越高
            recency_bonus = i * 0.5
            score = msg.importance + recency_bonus
            scored.append((score, i, msg))

        # 从高到低选择，直到预算用完
        scored.sort(key=lambda x: (-x[0], x[1]))
        selected_indices = set()
        used_tokens = 0

        for score, idx, msg in scored:
            if used_tokens + msg.token_estimate > target:
                continue
            selected_indices.add(idx)
            used_tokens += msg.token_estimate

        # 按原始顺序返回
        result = []
        for i, msg in enumerate(self._messages):
            if i in selected_indices:
                result.append({"role": msg.role, "content": msg.content})

        return result

    def _auto_compress(self):
        """自动压缩 — 合并旧的工具输出、截断长消息"""
        self._compression_count += 1
        original_tokens = self._total_tokens

        # 1. 压缩旧的工具输出（保留最近3个）
        tool_msgs = [
            (i, m) for i, m in enumerate(self._messages)
            if m.role == "tool" and not m.compressed
        ]
        if len(tool_msgs) > 3:
            for idx, msg in tool_msgs[:-3]:
                if len(msg.content) > 500:
                    self._messages[idx].content = msg.content[:200] + f"\n...[已压缩，原长{len(msg.content)}字]"
                    self._messages[idx].compressed = True
                    saved = msg.token_estimate - 100
                    self._total_tokens -= max(0, saved)

        # 2. 合并连续的assistant消息
        i = 0
        while i < len(self._messages) - 1:
            if (self._messages[i].role == "assistant" and
                self._messages[i + 1].role == "assistant" and
                not self._messages[i].compressed):
                merged = self._messages[i].content + "\n" + self._messages[i + 1].content
                self._messages[i].content = merged
                self._messages[i].token_estimate = 0
                self._messages[i].estimate_tokens()
                self._messages[i].compressed = True
                self._messages.pop(i + 1)
                self._recalculate_tokens()
            else:
                i += 1

        saved = original_tokens - self._total_tokens
        logger.info(
            f"Context auto-compressed #{self._compression_count}: "
            f"saved ~{saved} tokens ({original_tokens} -> {self._total_tokens})"
        )

    def _recalculate_tokens(self):
        self._total_tokens = sum(m.estimate_tokens() for m in self._messages)

    def get_stats(self) -> dict:
        return {
            "total_messages": len(self._messages),
            "total_tokens": self._total_tokens,
            "budget": {
                "total_window": self.budget.total_window,
                "available_for_history": self.budget.available_for_history,
                "compression_trigger_at": self.budget.compression_trigger_at,
            },
            "usage_percent": round(
                self._total_tokens / max(self.budget.available_for_history, 1) * 100, 1
            ),
            "compression_count": self._compression_count,
            "messages_by_role": {
                role: sum(1 for m in self._messages if m.role == role)
                for role in set(m.role for m in self._messages)
            },
        }

    def clear(self):
        self._messages.clear()
        self._total_tokens = 0


def budget_snapshot() -> dict:
    """双限额预算推导链快照（可观测性："预算怎么算出来的"一条响应看全）。

    读utils.token_manager的模型限额缓存（与soulmate真实消息路径**同一真源**），
    输出 limits→reserved→usable→history_target 完整推导链。fail-safe：任何异常
    只返回{"error": ...}，绝不反噬health存活判定。
    """
    try:
        from utils.token_manager import (
            get_context_window,
            get_input_limit,
            get_max_output_tokens,
        )
        ctx = get_context_window()
        out = get_max_output_tokens()
        inl = get_input_limit()
    except Exception as e:  # pragma: no cover - import/环境异常路径
        return {"error": str(e)}
    mgr = ContextBudgetManager()
    return {
        "context_window": ctx,
        "max_output_tokens": out,
        "input_limit": inl,
        "input_limit_first": bool(inl),
        "reserved": reserved_tokens(out, out),
        "usable_input_tokens": usable_input(ctx, out, out, inl),
        "history_target": mgr.model_history_target(ctx, out, input_limit=inl),
        "fallback_target": DEFAULT_HISTORY_TARGET_TOKENS,
        "system_reserve": mgr.budget.system_reserve,
    }
