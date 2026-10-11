# -*- coding: utf-8 -*-
"""模型降级有序链 — CowAgent chat_fallback移植（2026-09-29 P0）

调研来源：SUMMARY.md §五 cortex缺口「模型降级有序链（fallback+限流立即切备胎）
| CowAgent chat fallback链 | P0」——38-CowAgent-source-supplement3.md #12
（grep确认OpenSoul/OpenMate"完全没有"）；源码逐行对照 zhayujie/CowAgent：
- bridge/agent_bridge.py：fallback_config（链规范化）/fallback_available（
  环绕预算）/use_fallback（推进+sticky）/reset_fallback（下个run回primary）
- agent/protocol/agent_stream.py `_call_llm_stream`：失败处理（_on_fallback/
  _exhausted 参数注释即规格）、429 switch_now、链耗尽列全部试过

CowAgent语义逐条对应（commit message即规格书）：
1. **有序链[{provider,model}]走完环绕第二遍**：_FALLBACK_MAX_PASSES=2——"瞬时限流
   已恢复不该废掉整个turn"；第三遍只会重探不会自愈的故障
   （agent_bridge.py fallback_available注释）
2. **每link只给一次尝试**（_on_fallback → link_retries=0）：防长链睡过web SSE
   idle超时——"链存在的意义是换provider，不是等这个provider"
   （agent_stream.py link_retries注释）
3. **链耗尽报错列出所有试过的模型**：只报最后一个="那个模型坏了"，实际每个都
   试过都失败（agent_stream.py "Every link is spent … Report the whole chain"）
4. **限流有备胎立即切换、没备胎干等**：429+有链=switch_now跳过等待（备胎是不同
   provider自带配额，切换能立即回答）；429+无链=保持原有退避等待
   （agent_stream.py switch_now注释）
5. **链规范化**：缺provider/model的半截link丢弃（"half a link could route the
   turn nowhere"）；与primary/先前link重复的丢弃（防把turn弹回刚证明已挂的
   模型）；chain是opt-in，空链=禁用切换、错误照旧
   （agent_bridge.py fallback_config + config.py chat_fallback注释）
6. **sticky**：一个run内切到哪个link就停在哪个link（持续故障不逐步重探）；
   下个run reset回primary重新走链（use_fallback/reset_fallback docstring）

配置：LLM_FALLBACK_CHAIN 环境变量（.env文件优先，utils.llm_config.resolve_env_value
单一真源）两种形态：
- 紧凑格式 "provider:model,provider:model"（与MODEL_ROUTER_*同词表）
- CowAgent原生JSON：[{"provider": "openai", "model": "gpt-4o-mini"}, …]
provider凭据/端点解析=model_router.resolve_provider_config 单一真源
（.env文件优先、key_source可观测、key不进日志）。provider在PROVIDER_ENV无登记
或无凭据（requires_auth而key为空）的link丢弃并告警——半截link等于把turn路由到
虚空。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("acp.model_fallback")

# 环境变量名（opt-in：不设置=空链=行为不变，CowAgent "an empty chain disables
# the switch and the error surfaces as before"）
ENV_FALLBACK_CHAIN = "LLM_FALLBACK_CHAIN"

# 一个turn最多环绕走链遍数（CowAgent _FALLBACK_MAX_PASSES=2）：
# 两遍而非一遍——走一遍链是真实耗时，走回link0时当初的限流窗口大概率已过；
# 第三遍基本只是重探不会自愈的故障
MAX_PASSES = 2


@dataclass(frozen=True)
class FallbackLink:
    """链上一个备胎（CowAgent {provider, model} + 解析出的凭据/端点）"""

    provider: str
    model: str
    base_url: str
    api_key: str = ""
    key_source: str = ""
    requires_auth: bool = True

    def label(self) -> str:
        """可观测标签：provider/model（报错/日志用，key绝不进标签）"""
        return f"{self.provider}/{self.model}"

    def headers(self) -> dict:
        """该link的请求头（独立凭据——备胎是不同provider，鉴权跟着link走）"""
        # Accept-Encoding: identity（14.5）——禁透明压缩（同 llm_engine）；
        # _apply_link 整体替换 client.headers，此处漏了即降级链 link 压缩复活
        h = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h


@dataclass
class FallbackChain:
    """有序降级链 + 环绕预算 + sticky推进状态

    语义（CowAgent逐条对应，见模块docstring）：
    - available()：本turn还有link尝试预算（depth < len(links)*MAX_PASSES）
    - advance()：推进到下一个link（depth % len 环绕第二遍），sticky
    - reset()：下个turn回primary重新走链（本模块不持primary状态，由调用方
      在turn开始时新建/复位——等价CowAgent reset_fallback的"回链头"半边）
    """

    links: list[FallbackLink] = field(default_factory=list)
    _depth: int = 0
    # sticky（CowAgent use_fallback："the run stays on whichever link answered"）：
    # 切换后本run停在应答的link上，不逐步重探持续故障；reset回链头
    _current: FallbackLink | None = None
    # 失败记账（CowAgent语义3）：每link失败一条"{label}: {summary}"，链耗尽报错
    # 列出**所有**试过的模型（fail()消费）——只报最后一个="那个模型坏了"误导
    spent: list[str] = field(default_factory=list)
    # primary可观测标签（load_fallback_chain按primary_model填；key绝不进标签）
    primary_label: str = ""

    @property
    def depth(self) -> int:
        """已消耗的link尝试数（含环绕——CowAgent "_fallback_depth counts
        attempts, not links"）"""
        return self._depth

    @property
    def current(self) -> FallbackLink | None:
        """当前sticky的link（None=还没切换过，起始仍在primary）"""
        return self._current

    def __bool__(self) -> bool:
        return bool(self.links)

    def available(self) -> bool:
        """本turn还能否推进到下一个link（环绕预算内）"""
        return bool(self.links) and self._depth < len(self.links) * MAX_PASSES

    def advance(self) -> FallbackLink:
        """推进到下一个link并返回（环绕：第二遍从link0重新走）。

        调用方须先确认available()；预算耗尽时抛RuntimeError（编程错误，
        不静默返回None——失败必须可见）。
        """
        if not self.available():
            raise RuntimeError("fallback chain exhausted (no attempts left this turn)")
        link = self.links[self._depth % len(self.links)]
        self._depth += 1
        self._current = link  # sticky：本run停在这个link上
        total = len(self.links) * MAX_PASSES
        logger.warning(
            "[model-fallback] 当前模型失败，切换到 %s (link %d/%d)"
            % (link.label(), self._depth, total)
        )
        return link

    def reset(self) -> None:
        """回到链头（下个turn从primary重新走链——CowAgent reset_fallback）"""
        self._depth = 0
        self._current = None

    def current_label(self) -> str:
        """当前link可观测标签（未切换过=primary_label）"""
        return self._current.label() if self._current else (self.primary_label or "primary")

    def note_failure(self, summary: str) -> None:
        """记账一次link失败（CowAgent语义3的spent，key绝不进标签）"""
        self.spent.append(f"{self.current_label()}: {summary}")

    def try_advance(
        self,
        summary: str,
        *,
        retry_planned: bool,
        status: int | None = None,
        has_output: bool = False,
    ) -> bool:
        """失败后是否立即切换到链上下一个link（llm_engine三路会话面的切换决策单点）。

        CowAgent语义逐条：
        - **429限流+链上有备胎=switch_now立即切换**（语义4"限流有备胎立即切换、
          没备胎干等"）——即使同link重试预算还在：备胎是不同provider自带配额，
          等Retry-After=浪费能立即回答的备胎
        - 其余失败：同link重试已规划→先等重试（瞬时故障重试有望自愈，与
          llm_transport单link重试环同语义）；重试已放弃→换link再试（含400/401/
          402等不可重试状态——CowAgent _on_fallback任何link失败都换，llm_transport
          "402不可重试也换link"同款）
        - **has_output=True永不切换**：已流出/已累积内容再重发=双份回复/tool_calls
          状态串attempt污染（utils.llm_retry同款守卫）
        - 链不可用（空链opt-in禁用/环绕预算耗尽）恒False=行为与无链逐字一致

        切换成功=True（spent记账+advance+sticky，调用方重发到chain.current）；
        返回False=不切换且**不记账**（调用方继续走原重试/失败契约，最终放弃时用
        fail()记账——一次失败只记一条）。
        """
        if not self.available() or has_output:
            return False
        switch_now = status is not None and int(status) == 429
        if not (switch_now or not retry_planned):
            return False
        self.note_failure(summary)
        self.advance()
        return True

    def fail(self, summary: str, default: str) -> str:
        """链上最终失败：记账并返回报错文案（CowAgent语义3）。

        单link失败=default（原错误契约逐字不变，无链行为零变化）；多个link试过=
        chain_failure_message列出**所有**试过的模型与各自错误。
        """
        self.note_failure(summary)
        return chain_failure_message(self.spent) if len(self.spent) > 1 else default


def chain_failure_message(spent: list[str]) -> str:
    """链耗尽报错文案——列出**所有**试过的模型与各自错误（CowAgent语义3）。

    只报最后一个="那个模型坏了"，实际是每个都试过都失败——"every backup is
    down"而非甩锅恰好最后失败的link。
    """
    detail = " | ".join(spent)
    return (
        f"主模型与全部兜底模型均调用失败（{len(spent)}个模型全部试过）: {detail}"
    )


def parse_chain_spec(raw: str) -> list[dict]:
    """解析链配置——紧凑格式 "provider:model,…" 或 CowAgent原生JSON列表。

    无法解析的输入返回[]（配置错误=空链=行为不变，不炸启动；解析告警可见）。
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    # JSON形态：[{"provider":…,"model":…}, …]（CowAgent config原生）
    if raw.startswith("["):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as e:
            logger.warning("[model-fallback] LLM_FALLBACK_CHAIN JSON解析失败，按空链处理: %s", e)
            return []
        return [item for item in data if isinstance(item, dict)]
    # 紧凑形态：与 model_router._parse_env_model_list 同词表
    out = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" not in item:
            logger.warning("[model-fallback] 链条目缺':'分隔（应为provider:model），丢弃: %r", item)
            continue
        provider, model = item.split(":", 1)
        out.append({"provider": provider.strip(), "model": model.strip()})
    return out


def normalize_chain(
    raw_items: list[dict],
    primary_model: str = "",
    primary_provider: str = "",
    resolver=None,
) -> list[FallbackLink]:
    """链规范化（CowAgent fallback_config逐条语义）→ 可用FallbackLink列表。

    - 缺provider或model的半截link丢弃（"half a link could route the turn nowhere"）
    - 与primary或先前link重复的丢弃（防把turn弹回刚证明已挂的模型）；
      primary无provider钉死时按模型名单独比对（CowAgent primary_model_only）
    - provider无登记/无凭据的link丢弃并告警（解析不出端点=路由到虚空）
    """
    from model_router import resolve_provider_config  # 延迟import防环

    resolver = resolver or resolve_provider_config
    chain: list[FallbackLink] = []
    primary_model = (primary_model or "").strip()
    primary_provider = (primary_provider or "").strip()
    seen = {(primary_provider, primary_model)} if primary_model else set()
    primary_model_only = primary_model if (primary_model and not primary_provider) else None

    for item in raw_items:
        if not isinstance(item, dict):
            continue
        provider = (item.get("provider") or "").strip()
        model = (item.get("model") or "").strip()
        if not provider or not model:
            logger.warning("[model-fallback] 半截link（缺provider/model）丢弃: %r", item)
            continue
        key = (provider, model)
        if key in seen or (primary_model_only and model == primary_model_only):
            logger.warning("[model-fallback] 重复link丢弃（%s/%s）: 与primary或先前link相同", provider, model)
            continue
        try:
            cfg = resolver(provider)
        except Exception as e:
            logger.warning("[model-fallback] provider %r 解析失败，丢弃link: %s", provider, e)
            continue
        base_url = (cfg.get("base_url") or "").strip()
        api_key = (cfg.get("api_key") or "").strip()
        requires_auth = bool(cfg.get("requires_auth", True))
        if not base_url or (requires_auth and not api_key):
            logger.warning(
                "[model-fallback] link %s/%s 无可用端点/凭据（base_url=%s key=%s），丢弃",
                provider, model, "有" if base_url else "无", "有" if api_key else "无",
            )
            continue
        seen.add(key)
        chain.append(
            FallbackLink(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                key_source=cfg.get("key_source", ""),
                requires_auth=requires_auth,
            )
        )
    return chain


def load_fallback_chain(
    primary_model: str = "",
    primary_provider: str = "",
    env_value: str | None = None,
    resolver=None,
) -> FallbackChain:
    """从环境构建降级链（opt-in：LLM_FALLBACK_CHAIN未设/空=空链=行为不变）。

    env_value显式传入优先（测试/未来配置面）；否则走 utils.llm_config.
    resolve_env_value（.env文件优先于进程环境——运维配置真源，与全系统一致）。
    """
    if env_value is None:
        from utils.llm_config import resolve_env_value  # 延迟import防环

        env_value, _src = resolve_env_value([ENV_FALLBACK_CHAIN], default="")
    raw_items = parse_chain_spec(env_value)
    links = normalize_chain(
        raw_items,
        primary_model=primary_model,
        primary_provider=primary_provider,
        resolver=resolver,
    )
    if raw_items and not links:
        logger.warning("[model-fallback] LLM_FALLBACK_CHAIN 配置了%d条但规范化后为空（全部被丢弃）", len(raw_items))
    return FallbackChain(
        links=links, primary_label=(primary_model or "").strip() or "primary"
    )
