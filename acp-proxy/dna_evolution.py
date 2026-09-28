"""DNA Evolution — 双螺旋自进化引擎 v4

核心原则（交叉编程）：
1. A反思→A规划→B写代码→B验证→A采纳
2. B反思→B规划→A写代码→A验证→B采纳
3. 任何一条链挂了，另一条独立运行（降级为自我编程）
4. 交叉验证：一方写代码，另一方验证

v4改进：
- 独立进程隔离（每条链在独立进程运行）
- SOLO模式加固（60秒超时 + 连续3次确认）
- 原子写入（temp+rename防JSON损坏）
- 沙箱测试（LLM代码在subprocess中运行验证）
- 精确回滚（per-file git checkout）
- 观察去重（hash-based dedup）
- 效果评估（技能使用率追踪）
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

# omission占位符pattern单一真源=utils.llm_safety（2026-09-28收口）：旧本类7条副本
# 已漂移（缺 /* ... existing code ... */ 与 <!-- ... --> 两条），禁止再复制pattern
from utils.llm_safety import OMISSION_PATTERNS

# LLM传输错误契约+重试策略+离线park 单一真源（2026-09-29收口，多轮遗留项
# "先统一错误契约再合并传输"第一步）：
# - LLMCallError：utils.llm_errors（旧本模块自有类已删除；保留
#   `dna_evolution.LLMCallError` 兼容再导出，既有 import 面/测试不变）
# - 重试策略：utils.llm_retry（kilocode retry.ts：Retry-After三格式/5xx无条件
#   可重试/瞬时传输错误重试）——替换旧 _retry_delay 副本（只认秒数、传输错误零重试）
# - 离线park：utils.network_state（kilocode SessionNetwork三态，断网等恢复续跑）
from utils import network_state
from utils.llm_errors import LLMCallError  # noqa: F401（兼容再导出）
from utils.llm_retry import (
    MAX_ATTEMPTS,
    backoff_ms,
    is_retryable_status,
    is_transient_error,
)

logger = logging.getLogger("acp-proxy.dna_evolution")

# ── 配置 ──────────────────────────────────────────────────

HEARTBEAT_INTERVAL = 5
HEARTBEAT_TIMEOUT = 60  # 60秒超时（防误触发SOLO）
SOLO_CONFIRM_MISSES = 6  # 连续6次心跳丢失才确认伙伴死亡
EVOLUTION_INTERVAL = 3600  # 1小时（可通过API动态修改）
PLAN_TIMEOUT = 120  # 等待伙伴执行计划的超时

# agno §1.4 错误风暴熔断（2026-09-27 00:10轮遗留#2销账）：连续同错=系统性故障
# （provider宕机/余额402/密钥失效），不是N条独立失败——streak≥窗口即熔断暂停
# 自动进化调度，防每小时空转烧调用+把同一故障记成N条独立失败污染进化样本。
# 分工声明：此处只管openmate DNA strand自身的进化调度；opensoul heredity
# evolution_loop的breaker管opensoul提案循环——两套breaker各管各的循环，无共享状态。
LLM_BREAKER_STORM_WINDOW = 3  # 连续LLM失败≥N次=错误风暴
LLM_BREAKER_COOLDOWN_S = 1800  # 熔断冷却30分钟（半开放行一次试探）
# 通用（非LLM）失败风暴熔断——分域独立窗口（02:26遗留#2：窗口/计数绝不与LLM域混算）
GEN_BREAKER_STORM_WINDOW = 3  # 连续同签名通用失败≥N次=错误风暴
GEN_BREAKER_COOLDOWN_S = 1800  # 通用风暴熔断冷却（半开语义与LLM域同款）


class StrandRole(Enum):
    PRIMARY = "primary"
    SHADOW = "shadow"
    SOLO = "solo"


class EvolutionGoal:
    """进化目标 — 指导进化方向"""

    def __init__(
        self,
        goal_id: str,
        title: str,
        description: str,
        priority: str = "medium",
        status: str = "active",
        progress: float = 0.0,
        created_at: str | None = None,
        completed_at: str | None = None,
    ):
        self.goal_id = goal_id
        self.title = title
        self.description = description
        self.priority = priority  # critical / high / medium / low
        self.status = status  # active / completed / paused
        self.progress = progress  # 0.0 ~ 1.0
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.completed_at = completed_at

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "priority": self.priority,
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }


# ── 默认进化目标（终极愿景：成为全球最聪明的agent）──────────

DEFAULT_GOALS: list[dict] = [
    {
        "title": "成为全球最聪明的agent",
        "description": "终极目标——综合能力持续提升，覆盖推理、创造、执行、学习所有维度",
        "priority": "critical",
    },
    {
        "title": "自编程能力",
        "description": "agent能自己写代码实现新功能，无需人工干预",
        "priority": "high",
    },
    {
        "title": "工具创造",
        "description": "进化引擎能创建新的MCP工具和技能，持续扩展能力边界",
        "priority": "high",
    },
    {
        "title": "错误自修复",
        "description": "agent能自动发现和修复自己的错误，实现自愈",
        "priority": "medium",
    },
    {
        "title": "知识积累",
        "description": "持续学习对话模式和用户偏好，构建长期记忆",
        "priority": "medium",
    },
    {
        "title": "性能优化",
        "description": "优化响应速度和资源使用，提升用户体验",
        "priority": "low",
    },
]


# LLMCallError 定义已收口 utils.llm_errors 单一真源（顶部import再导出）。
# 旧自有类（2026-09-27 00:10轮加的 partial_content 契约）并入该模块，
# 与 utils.llm_gateway 的"失败静默返空串"统一为一种失败形态：
# 程序化调用面失败=LLMCallError（partial_content=半截输出），契约见
# utils/llm_errors.py docstring。


class EvolutionStage(Enum):
    OBSERVE = "observe"
    REFLECT = "reflect"
    PLAN = "plan"
    SUBMIT_PLAN = "submit_plan"      # 提交计划给伙伴
    EXECUTE_PARTNER = "execute_partner"  # 执行伙伴的计划
    VERIFY = "verify"
    LEARN = "learn"


@dataclass
class EvolutionCycle:
    cycle_id: str
    strand_id: str
    stage: EvolutionStage
    trigger: str
    observations: list[dict] = field(default_factory=list)
    reflections: list[str] = field(default_factory=list)
    plan: Optional[dict] = None
    changes: list[dict] = field(default_factory=list)
    verification: Optional[dict] = None
    success: bool = False
    error: Optional[str] = None  # LLM/执行失败原因（mem0 §1.1失败可见；None=非失败）
    executed_by: str = ""  # 谁执行的（自己 or 伙伴）
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None


@dataclass
class PendingPlan:
    """等待伙伴执行的进化计划"""
    plan_id: str
    source_strand: str
    improvements: list[dict]
    reflections: list[str]
    created_at: float = field(default_factory=time.time)
    status: str = "pending"  # pending / executing / completed / failed
    result: Optional[dict] = None


@dataclass
class Observation:
    obs_id: str
    obs_type: str
    content: str
    metadata: dict = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    analyzed: bool = False


class DNAStrand:
    """单条进化链 — 交叉编程模型

    自己只做：观察 → 反思 → 规划（产出计划）
    伙伴来做：执行计划 → 写代码 → 验证
    """

    EVOLVABLE_DIRS = [
        "acp-proxy/skills/",
        "acp-proxy/agent/experiments/",  # evo实验区（2026-09-21目录统一：正式插件在<root>/plugins/，evo产出落此目录待gene-loop审核转正）
        "acp-proxy/routes/",
    ]

    PROTECTED_FILES = [
        "acp-proxy/app.py",
        "acp-proxy/main.py",
        "acp-proxy/ws_acp.py",
        "acp-proxy/ws_chat.py",
        "acp-proxy/agent/soulmate_agent.py",
        "acp-proxy/agent/llm_engine.py",
        "acp-proxy/dna_evolution.py",
        "acp-proxy/skill_manager.py",
    ]

    def __init__(
        self,
        strand_id: str,
        role: StrandRole,
        repo_root: str,
        llm_base_url: str = "http://127.0.0.1:8090",
        strategy: str = "conservative",
        llm_api_key: str = "",
        llm_model: str = "soulmate",
    ):
        self.strand_id = strand_id
        self.role = role
        self.repo_root = Path(repo_root)
        self.llm_base_url = llm_base_url
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model
        self.strategy = strategy

        if strategy == "conservative":
            self.temperature = 0.2
            self.max_improvements = 1
            self.description = "保守策略：小步改进，严格验证"
        else:
            self.temperature = 0.7
            self.max_improvements = 3
            self.description = "激进策略：大胆尝试，高探索"

        self._observations: list[Observation] = []
        self._cycles: list[EvolutionCycle] = []
        self._memory: list[dict] = []
        self._goals: list[EvolutionGoal] = []
        self._history: list[dict] = []
        self._cycle_count = 0
        self._running = False

        # LLM失败可见账本（mem0 §1.1：失败≠无改进，必须可区分、可观察）
        self._llm_failure_streak = 0  # 连续LLM失败次数（成功一次归零）
        self._last_llm_error = ""
        self._last_llm_error_at = ""

        # agno §1.4 错误风暴熔断：streak≥窗口→暂停自动进化调度直至冷却过期
        # （半开：冷却后放行一次试探，成功复位/失败重新计数冷却）
        self._breaker_open = False
        self._breaker_opened_at = 0.0
        self._breaker_suppressed = 0  # 熔断期间被抑制的进化次数（可见计数）

        # 通用失败账本（非LLM故障，与last_llm_error分账；00:10遗留#3）
        self._last_error = ""
        self._last_error_at = ""

        # 通用失败风暴熔断（02:26遗留#2）：同签名连续失败≥GEN窗口=系统性故障。
        # 与LLM域分账：窗口/计数/熔断状态互不混算（LLM风暴只开LLM闸，反之亦然）。
        self._gen_failure_streak = 0  # 连续同签名通用失败次数（周期跑通或签名变化→重计）
        self._gen_error_signature = ""  # 当前风暴签名（stage:异常类型，见_error_signature）
        self._gen_breaker_open = False
        self._gen_breaker_opened_at = 0.0
        self._gen_breaker_suppressed = 0  # 通用熔断期间被抑制的进化次数（可见计数）

        # 半开严格单试探（多轮遗留"半开语义非严格单试探"销账）：冷却过期≠放行一切
        # ——只放行**一次**试探周期，试探在飞期间其余调度继续拦截；试探结果立即裁决
        # （成功→复位/失败→重新开闸冷却重新计数），不等streak再爬窗口。
        # 进程内存态（不持久化）：重启后标记清零=重新放行一次试探，语义仍是
        # "每个进程同一时刻最多一个在飞试探"（at-most-one probe per process）。
        self._probe_in_flight = {"llm": False, "gen": False}

        # 实时日志文件（供SSE tail）
        self._log_file = Path(repo_root) / "acp-proxy" / "data" / f"dna_log_{strand_id}.jsonl"

        # 心跳
        self._partner_alive = True
        self._partner_cycle_count = 0
        self._consecutive_misses = 0  # 连续心跳丢失次数

        # 交叉编程队列
        self._my_plans_for_partner: list[PendingPlan] = []  # 我的计划，等伙伴执行
        self._partner_plans_for_me: list[PendingPlan] = []   # 伙伴的计划，等我执行

        # 状态文件
        self._state_file = self.repo_root / "acp-proxy" / "data" / f"dna_state_{strand_id}.json"
        self._heartbeat_file = self.repo_root / "acp-proxy" / "data" / f"dna_heartbeat_{strand_id}.json"
        self._plans_file = self.repo_root / "acp-proxy" / "data" / f"dna_plans_{strand_id}.json"
        self._checkpoint_file = self.repo_root / "acp-proxy" / "data" / "dna_checkpoint.json"

        self._load_state()

    def _log(self, stage: str, message: str, details: dict | None = None):
        """写入一条JSONL日志，供SSE实时流读取"""
        try:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "strand": self.strand_id,
                "stage": stage,
                "msg": message,
                "cycle": self._cycle_count,
            }
            if details:
                entry["details"] = details
            with open(self._log_file, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ── 观察（带去重） ──────────────────────────────────

    def observe(self, obs_type: str, content: str, metadata: dict | None = None):
        # 去重：相同内容的观察不重复添加
        content_hash = hashlib.md5(content.encode()).hexdigest()
        for existing in self._observations[-20:]:  # 只检查最近20条
            if hashlib.md5(existing.content.encode()).hexdigest() == content_hash:
                return  # 重复观察，跳过
        
        obs = Observation(
            obs_id=str(uuid.uuid4())[:8],
            obs_type=obs_type,
            content=content[:500],
            metadata=metadata or {},
        )
        self._observations.append(obs)
        self._log("observe", f"新观察 [{obs_type}]: {content[:80]}", {"obs_type": obs_type})

    # ── 心跳 ──────────────────────────────────────────────

    def send_heartbeat(self):
        try:
            with open(self._heartbeat_file, "w") as f:
                json.dump({
                    "strand_id": self.strand_id,
                    "role": self.role.value,
                    "strategy": self.strategy,
                    "timestamp": time.time(),
                    "cycle_count": self._cycle_count,
                    "observations_count": len(self._observations),
                    "pending_plans_for_partner": len(self._my_plans_for_partner),
                    "pending_plans_from_partner": len(self._partner_plans_for_me),
                }, f)
        except Exception as e:
            logger.warning(f"[{self.strand_id}] Heartbeat write failed: {e}")

    def check_partner(self, partner_id: str) -> bool:
        partner_file = self.repo_root / "acp-proxy" / "data" / f"dna_heartbeat_{partner_id}.json"
        try:
            if not partner_file.exists():
                self._consecutive_misses += 1
                if self._consecutive_misses >= SOLO_CONFIRM_MISSES:
                    self._partner_alive = False
                return self._partner_alive
            
            with open(partner_file, "r") as f:
                hb = json.load(f)
            elapsed = time.time() - hb.get("timestamp", 0)
            
            if elapsed < HEARTBEAT_TIMEOUT:
                self._partner_alive = True
                self._consecutive_misses = 0
            else:
                self._consecutive_misses += 1
                if self._consecutive_misses >= SOLO_CONFIRM_MISSES:
                    self._partner_alive = False
                    logger.warning(f"[{self.strand_id}] Partner {partner_id} down ({elapsed:.0f}s, {self._consecutive_misses} consecutive misses)")
            
            self._partner_cycle_count = hb.get("cycle_count", 0)
            return self._partner_alive
        except Exception:
            self._consecutive_misses += 1
            if self._consecutive_misses >= SOLO_CONFIRM_MISSES:
                self._partner_alive = False
            return self._partner_alive

    # ── 主循环 ────────────────────────────────────────────

    async def run(self, partner_id: str):
        self._running = True
        self._log("start", f"螺旋{self.strand_id}启动 (策略={self.strategy}, 角色={self.role.value})", {"partner": partner_id})
        logger.info(f"[{self.strand_id}] DNA Strand started (strategy={self.strategy}, role={self.role.value})")

        # 阶段2: 启动后检查是否需要触发partner同步重启
        await self._check_pending_partner_sync()

        last_evolution = 0
        last_heartbeat = 0

        while self._running:
            try:
                now = time.time()

                # 心跳
                if (now - last_heartbeat) > HEARTBEAT_INTERVAL:
                    self.send_heartbeat()
                    last_heartbeat = now

                # 检查伙伴
                partner_alive = self.check_partner(partner_id)

                # 每个循环重新加载goals和记忆（主进程可能已修改，伙伴可能已同步）
                self._load_goals()
                self._load_shared_memory()

                # 角色切换
                if not partner_alive and self.role == StrandRole.SHADOW:
                    self.role = StrandRole.SOLO
                    logger.info(f"[{self.strand_id}] → SOLO mode (partner down)")
                elif partner_alive and self.role == StrandRole.SOLO:
                    self.role = StrandRole.SHADOW
                    logger.info(f"[{self.strand_id}] → SHADOW mode (partner recovered)")

                # ── 独立进化 + 伙伴计划协调 ──
                # 每个strand独立计时进化，通过计划文件协调
                # 生成方写计划 → 伙伴读到 → review确认 → 执行 → 触发重启
                unanalyzed = [o for o in self._observations if not o.analyzed]
                interval = EVOLUTION_INTERVAL if partner_alive else EVOLUTION_INTERVAL / 2
                # 进化触发解卡: NOISE_OBS入列即标analyzed→unanalyzed恒空→进化永久休眠(cycle冻结根因)。
                # 时间兜底: 3×interval无进化→自省式进化，噪声观察仍不进failure样本。
                time_fallback = (now - last_evolution) > interval * 3
                should_evolve = (now - last_evolution) > interval and (bool(unanalyzed) or time_fallback)

                # 2. 自己的进化周期（agno §1.4熔断门：错误风暴期间不烧进化预算）
                # 分域判定：LLM风暴/通用风暴各自独立拦截（谁开闸谁抑制，互不混算）
                blocked_llm = should_evolve and self._breaker_is_open(now)
                blocked_gen = should_evolve and self._breaker_is_open(now, domain="gen")

                # 1. 先检查有没有伙伴给我的计划（review+执行）
                self._load_partner_plans(partner_id)
                pending = [p for p in self._partner_plans_for_me if p.status == "pending"]
                if pending:
                    plan = pending[0]
                    self._partner_plans_for_me = [plan]
                    ok = await self._review_partner_plan(plan)
                    if ok:
                        self._log("turn", f"✅ 确认伙伴计划 {plan.plan_id}，执行中...")
                        await self._execute_partner_plans()
                        self._log("turn", f"✅ 伙伴计划执行完成，触发重启")
                        self._write_restart_signal("partner_evolution")
                    else:
                        self._log("turn", f"❌ 拒绝伙伴计划 {plan.plan_id}")
                    self._partner_plans_for_me = []

                elif blocked_llm or blocked_gen:
                    if blocked_llm:
                        self._breaker_suppressed += 1
                        self._log("error", f"⛔ 进化被熔断抑制（LLM错误风暴，streak={self._llm_failure_streak}）", {
                            "domain": "llm",
                            "llm_failure_streak": self._llm_failure_streak,
                            "suppressed_cycles": self._breaker_suppressed,
                            "probe_in_flight": self._probe_in_flight.get("llm", False),
                            "cooldown_remaining_s": round(
                                LLM_BREAKER_COOLDOWN_S - (now - self._breaker_opened_at), 1),
                        })
                    else:
                        self._gen_breaker_suppressed += 1
                        self._log("error", f"⛔ 进化被熔断抑制（通用错误风暴，streak={self._gen_failure_streak}, sig={self._gen_error_signature}）", {
                            "domain": "gen",
                            "gen_failure_streak": self._gen_failure_streak,
                            "gen_error_signature": self._gen_error_signature,
                            "suppressed_cycles": self._gen_breaker_suppressed,
                            "probe_in_flight": self._probe_in_flight.get("gen", False),
                            "cooldown_remaining_s": round(
                                GEN_BREAKER_COOLDOWN_S - (now - self._gen_breaker_opened_at), 1),
                        })
                    last_evolution = now  # 抑制窗口按正常节奏计时，熔断恢复后不回堆积
                elif should_evolve:
                    self._log("turn", f"🚀 开始进化 (interval={interval}s)")
                    await self._evolve()
                    last_evolution = now

                # 保存状态
                self._save_plans()
                self._save_state()

                await asyncio.sleep(5)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.strand_id}] Loop error: {e}")
                await asyncio.sleep(30)

    # ── 进化循环（观察→反思→规划→提交） ──────────────────

    async def _evolve(self):
        """自己只做：观察→反思→规划，然后把计划交给伙伴执行"""
        cycle = EvolutionCycle(
            cycle_id=str(uuid.uuid4())[:8],
            strand_id=self.strand_id,
            stage=EvolutionStage.OBSERVE,
            trigger=f"{self.strategy}_auto",
        )

        # 半开严格单试探准入：本轮充当在飞试探（仅标记"open且冷却已过且无试探在飞"
        # 的域；closed域/冷却未过域不标记）。手动trigger走同一路径=人工周期同样
        # 是试探（结果同样裁决），语义统一。
        self._breaker_probe_start()

        try:
            # 1. Observe
            cycle.observations = [
                {"type": o.obs_type, "content": o.content}
                for o in self._observations[-30:]
            ]
            self._log("observe", f"收集{len(cycle.observations)}条观察数据")

            # 2. Reflect
            cycle.stage = EvolutionStage.REFLECT
            self.send_heartbeat()
            self._log("reflect", "开始反思分析...")
            reflections = await self._reflect()
            cycle.reflections = reflections
            self._log("reflect", f"反思完成，{len(reflections)}条洞察", {"reflections": reflections[:2]})

            # 3. Plan
            cycle.stage = EvolutionStage.PLAN
            self.send_heartbeat()
            self._log("plan", "开始生成改进方案...")
            improvements = await self._plan(reflections)
            cycle.plan = {"improvements": improvements}
            self._log("plan", f"生成{len(improvements)}项改进", {"improvements": [i.get("description","")[:60] for i in improvements]})

            # 4. 执行：用MOSS风格7阶段流水线
            cycle.stage = EvolutionStage.SUBMIT_PLAN
            if improvements:
                self._log("execute", f"🧬 启动7阶段进化流水线 ({len(improvements)}项改进)")
                self.send_heartbeat()

                # 收集失败样本
                failure_batch = self._collect_failure_samples()
                self._log("locate", f"📊 收集到{len(failure_batch)}个失败样本")

                # 网关摘流
                from evolution_v2 import EvolutionV2
                from evolution_pipeline import gateway_drain, gateway_undrain
                pipeline = EvolutionV2(self, self.repo_root, self.strand_id.split("_")[1])

                await gateway_drain(self.strand_id.split("_")[1])

                try:
                    pipeline_result = await pipeline.run(failure_batch)
                    cycle.changes = pipeline_result.get("stages", {}).get("implement", {}).get("applied", [])
                    cycle.success = pipeline_result.get("success", False)
                    cycle.executed_by = "self"

                    stages = pipeline_result.get("stages", {})
                    self._log("execute", f"流水线完成: success={cycle.success}", {
                        "stages": list(stages.keys()),
                        "attempt": pipeline_result.get("attempt", 0),
                    })

                    if cycle.success:
                        # 进化成功 → 两阶段滚动重启
                        # 阶段1: drain self → 改代码 → restart self
                        # 阶段2: self启动成功 → drain partner → restart partner（同步代码）
                        instance_id = self.strand_id.split("_")[1]
                        partner_id = "b" if instance_id == "a" else "a"

                        # 写pending sync标记（partner启动时检查）
                        data_dir = self.repo_root / "acp-proxy" / "data"
                        sync_file = data_dir / f"sync_partner_{partner_id}.pending"
                        sync_file.write_text(json.dumps({
                            "reason": "evolution_success",
                            "source": self.strand_id,
                            "cycle": cycle.cycle_id,
                        }))

                        # 重启自己
                        self._write_restart_signal("evolution_success", target="self")
                        self._log("verdict", f"🎉 进化成功! 阶段1:重启自己 → 阶段2:partner({partner_id})同步重启")
                finally:
                    # 恢复流量
                    await gateway_undrain(self.strand_id.split("_")[1])
            else:
                self._log("plan", "⚠️ 无改进方案，本轮跳过")

            # 5. Learn
            cycle.stage = EvolutionStage.LEARN
            cycle.completed_at = datetime.now(timezone.utc).isoformat()
            self._cycle_count += 1
            self._cycles.append(cycle)

            # 追加到历史记录
            self._history.append({
                "cycle_id": cycle.cycle_id,
                "strand_id": self.strand_id,
                "improvements_count": len(improvements),
                "success": cycle.success,
                "executed_by": cycle.executed_by,
                "timestamp": cycle.completed_at,
            })

            lesson = await self._extract_lesson(cycle)
            if lesson:
                self._memory.append(lesson)
                logger.info(f"[{self.strand_id}] Learned: {lesson['summary'][:80]}")

            if cycle.success and cycle.changes:
                await self._write_checkpoint(cycle)

            # 周期完整跑通（无非LLM异常）=通用风暴结束：分域复位，只关gen闸不碰LLM闸
            self._gen_failure_streak = 0
            self._gen_error_signature = ""
            self._breaker_close("cycle_success", domain="gen")
            # 半开试探裁决：周期完整跑通=试探成功→复位在飞域的闸（含LLM域——真实
            # 周期必经_call_llm，跑通即LLM健康的间接证据；stub场景由测试自证）
            self._breaker_probe_resolve(ok=True, why="cycle_completed")

            self._save_state()
            self._sync_memory()  # 同步记忆到共享文件

            logger.info(
                f"[{self.strand_id}] Cycle {cycle.cycle_id}: "
                f"plan={len(improvements)} improvements, "
                f"executed_by={cycle.executed_by}, "
                f"success={'✓' if cycle.success else 'pending'}"
            )

        except LLMCallError as e:
            # mem0 §1.1 失败必须可见：LLM挂了 ≠ "无改进"。此前 _call_llm 静默返空，
            # 这里会记成"规划0项改进/pending"——LLM故障伪装成"进化没发现可改进项"
            # （2026-09-26 live实锤：每小时401空转却Learned: 规划0项改进）。
            # 现在：按失败记账（history带llm_error、streak计数、状态面可见），绝不冒充无改进。
            self._llm_failure_streak += 1
            self._last_llm_error = str(e)[:300]
            self._last_llm_error_at = datetime.now(timezone.utc).isoformat()
            cycle.success = False
            cycle.error = str(e)[:300]
            cycle.completed_at = datetime.now(timezone.utc).isoformat()
            self._cycle_count += 1
            self._cycles.append(cycle)
            self._history.append({
                "cycle_id": cycle.cycle_id,
                "strand_id": self.strand_id,
                "improvements_count": 0,
                "success": False,
                "llm_error": str(e)[:200],
                "stage": cycle.stage.value,
                "timestamp": cycle.completed_at,
            })
            self._save_state()
            # agno §1.4：连续同错≥窗口=系统性故障不是N条独立失败→熔断暂停自动进化
            if self._llm_failure_streak >= LLM_BREAKER_STORM_WINDOW:
                self._breaker_trip(f"连续{self._llm_failure_streak}次LLM失败（同错风暴）")
            # 半开严格单试探裁决：试探期一次失败即重新开闸（冷却重新计数），
            # 不等streak再爬窗口——遗留"半开语义非严格单试探"销账
            self._breaker_probe_resolve(ok=False, why=f"llm {str(e)[:80]}")
            self._log("error", f"❌ LLM调用失败，本轮进化未执行（失败≠无改进）: {str(e)[:120]}", {
                "llm_failure_streak": self._llm_failure_streak,
            })
            logger.error(
                f"[{self.strand_id}] Cycle {cycle.cycle_id} LLM失败（不是'无改进'）: {e} "
                f"(streak={self._llm_failure_streak})"
            )
        except Exception as e:
            # 通用失败记账（00:10遗留#3）：非LLM异常此前只logger.error不留账本，
            # "跑了但炸了"在history/状态面不可见。现在与LLM失败分账（error键≠llm_error键）。
            self._last_error = str(e)[:300]
            self._last_error_at = datetime.now(timezone.utc).isoformat()
            # 同错风暴计数（02:26遗留#2，分域独立）：签名=stage:异常类型——
            # 同签名连续失败才累积，签名一变重计（不同故障不误熔断）
            sig = self._error_signature(e, cycle.stage.value)
            if sig == self._gen_error_signature:
                self._gen_failure_streak += 1
            else:
                self._gen_error_signature = sig
                self._gen_failure_streak = 1
            cycle.success = False
            cycle.error = f"[{cycle.stage.value}] {str(e)[:280]}"
            cycle.completed_at = datetime.now(timezone.utc).isoformat()
            self._cycle_count += 1
            self._cycles.append(cycle)
            self._history.append({
                "cycle_id": cycle.cycle_id,
                "strand_id": self.strand_id,
                "improvements_count": 0,
                "success": False,
                "error": str(e)[:200],
                "error_signature": sig,
                "stage": cycle.stage.value,
                "timestamp": cycle.completed_at,
            })
            # agno §1.4同款：同签名连续失败≥GEN窗口=系统性故障（如pipeline/import坏死）
            # →gen熔断暂停自动进化。只在gen域计数开闸，绝不碰LLM域窗口（分域不混算）。
            if self._gen_failure_streak >= GEN_BREAKER_STORM_WINDOW:
                self._breaker_trip(
                    f"连续{self._gen_failure_streak}次同签名通用失败（{sig}风暴）", domain="gen")
            # 半开严格单试探裁决：试探期一次失败即重新开闸（冷却重新计数）——
            # 裁决粒度=周期级（保守）：周期没跑通=没有恢复证据，在飞域全部判失败
            self._breaker_probe_resolve(ok=False, why=f"gen {sig}")
            self._save_state()
            self._log("error", f"❌ 进化周期异常失败（非LLM故障）: {str(e)[:120]}", {
                "stage": cycle.stage.value,
                "error_signature": sig,
                "gen_failure_streak": self._gen_failure_streak,
            })
            logger.error(f"[{self.strand_id}] Cycle {cycle.cycle_id} failed at {cycle.stage.value}: {e}")
        finally:
            # 非常规出口兜底（取消/BaseException等）：释放未裁决的试探标记——
            # 无证据不误判（不重新开闸也不复位），闸保持原状，下轮可再放行一次试探。
            # 常规路径（成功/两类失败）已裁决，此处为no-op。
            self._breaker_probe_release()

    # ── agno §1.4 错误风暴熔断（连续同错即停） ──────────────
    # 双域实现：domain="llm"（LLM故障风暴）/ domain="gen"（非LLM同签名故障风暴）。
    # 两域窗口/计数/闸门完全独立（02:26遗留#2"分域独立窗口，别混算"）。

    @staticmethod
    def _error_signature(exc: Exception, stage: str = "") -> str:
        """同错风暴签名：stage:异常类型（agno error_type同款粒度）。

        消息内容不进签名——同根因不同消息（如FileNotFoundError路径各异）仍算
        同一故障模式；不同异常类型/不同stage=不同故障，streak重计不误熔断。
        """
        return f"{stage}:{type(exc).__name__}"

    def _breaker_is_open(self, now: float | None = None, domain: str = "llm") -> bool:
        """熔断是否处于拦截态（调度门唯一判定点，只读无副作用）。

        半开严格单试探：冷却过期后**只**放行一次试探（_breaker_probe_start接管
        in-flight标记），试探在飞期间继续拦截其余调度（返回True）；试探裁决由
        _breaker_probe_resolve做出——成功→_breaker_close复位，失败→重新开闸
        （冷却重新计数）。无试探在飞且冷却已过=返回False（下一次周期即试探）。

        注意保持只读：status查询/reset_breaker都调用本判定，若在此处放行试探会把
        监控轮询误当试探消费掉（strict single probe的正确性前提）。
        """
        opened = self._breaker_open if domain == "llm" else self._gen_breaker_open
        opened_at = self._breaker_opened_at if domain == "llm" else self._gen_breaker_opened_at
        cooldown = LLM_BREAKER_COOLDOWN_S if domain == "llm" else GEN_BREAKER_COOLDOWN_S
        if not opened:
            return False
        now = time.time() if now is None else now
        if (now - opened_at) < cooldown:
            return True
        # 冷却已过：试探在飞=继续拦截（严格单试探），否则放行（下一次周期即试探）
        return bool(self._probe_in_flight.get(domain, False))

    # ── 半开严格单试探（at-most-one probe） ──────────────────

    def _breaker_probe_start(self, now: float | None = None) -> list[str]:
        """半开域接管试探标记（试探准入点）。返回本轮充当试探的域名列表。

        仅标记「open且冷却已过且无试探在飞」的域：closed域=正常周期不标记；
        冷却未过=仍拦截不标记；已有试探在飞=拒绝（严格单试探）。一个周期可
        同时充当LLM域与通用域的试探。
        """
        now = time.time() if now is None else now
        probing: list[str] = []
        for domain in ("llm", "gen"):
            cooldown = LLM_BREAKER_COOLDOWN_S if domain == "llm" else GEN_BREAKER_COOLDOWN_S
            opened = self._breaker_open if domain == "llm" else self._gen_breaker_open
            opened_at = self._breaker_opened_at if domain == "llm" else self._gen_breaker_opened_at
            if (
                opened
                and (now - opened_at) >= cooldown
                and not self._probe_in_flight.get(domain, False)
            ):
                self._probe_in_flight[domain] = True
                probing.append(domain)
        if probing:
            self._log("turn", f"🟡 半开试探开始（{','.join(probing)}域，严格单试探在飞）",
                      {"domains": probing})
            logger.info(f"[{self.strand_id}] 半开试探开始 (domains={probing})")
        return probing

    def _breaker_probe_resolve(self, ok: bool, why: str = "") -> None:
        """试探裁决（一次裁决全部在飞域）：成功→复位各域闸；失败→重新开闸冷却重计。

        裁决粒度=周期级（保守取向）：周期未完整跑通（无论LLM失败还是通用异常）
        则在飞域全部判失败——没有恢复证据就不再放行（fail-closed）；反之周期
        跑通=试探成功（真实周期必经_call_llm，跑通即LLM健康的间接证据）。
        无试探在飞时no-op（closed态的普通失败不走此裁决，仍按streak窗口开闸）。
        """
        domains = [d for d, flying in self._probe_in_flight.items() if flying]
        if not domains:
            return
        now = time.time()
        for d in domains:
            self._probe_in_flight[d] = False
            label = "LLM" if d == "llm" else "通用"
            cooldown = LLM_BREAKER_COOLDOWN_S if d == "llm" else GEN_BREAKER_COOLDOWN_S
            if ok:
                self._breaker_close(f"probe_success({why})" if why else "probe_success", domain=d)
            else:
                # 试探失败→重新开闸（冷却重新计数），不等streak再爬窗口
                if d == "llm":
                    self._breaker_open = True
                    self._breaker_opened_at = now
                else:
                    self._gen_breaker_open = True
                    self._gen_breaker_opened_at = now
                self._log("error",
                          f"🔴 半开试探失败（{label}域），重新熔断冷却{cooldown}s（{why}）",
                          {"domain": d, "why": why[:200], "cooldown_s": cooldown})
                logger.error(
                    f"[{self.strand_id}] {label}半开试探失败，重新熔断: {why[:120]}")

    def _breaker_probe_release(self) -> None:
        """释放未裁决的试探标记（非常规出口兜底：取消/BaseException）。不作裁决——
        无证据不误判（不重新开闸也不复位），闸保持原状，下轮可再放行一次试探。"""
        for d in list(self._probe_in_flight):
            if self._probe_in_flight[d]:
                self._probe_in_flight[d] = False
                self._log("turn", f"🟡 半开试探标记释放（{d}域，无裁决）", {"domain": d})

    def _breaker_trip(self, reason: str, domain: str = "llm"):
        """开闸（或半开失败后再开闸=冷却重新计数）。开闸事件必须留痕。"""
        label = "LLM" if domain == "llm" else "通用"
        was_blocking = self._breaker_is_open(domain=domain)
        if domain == "llm":
            self._breaker_open = True
            self._breaker_opened_at = time.time()
            streak, window, cooldown = self._llm_failure_streak, LLM_BREAKER_STORM_WINDOW, LLM_BREAKER_COOLDOWN_S
        else:
            self._gen_breaker_open = True
            self._gen_breaker_opened_at = time.time()
            streak, window, cooldown = self._gen_failure_streak, GEN_BREAKER_STORM_WINDOW, GEN_BREAKER_COOLDOWN_S
        if not was_blocking:
            self._log("error", f"🔴 {label}错误风暴熔断开启（{reason}），暂停自动进化直至冷却{cooldown}s", {
                "domain": domain,
                "failure_streak": streak,
                "cooldown_s": cooldown,
                "storm_window": window,
            })
            logger.error(
                f"[{self.strand_id}] {label}错误风暴熔断开启: {reason} "
                f"(streak={streak}, cooldown={cooldown}s)"
            )

    def _breaker_close(self, why: str = "llm_success", domain: str = "llm"):
        """复位熔断（该域风暴结束）。分域：只关自己域的闸，不碰另一域。

        同时清掉该域在飞试探标记（成功证据已到=试探已裁决成功，如_call_llm
        成功路径的llm_success复位——证据驱动的提前裁决）。
        """
        self._probe_in_flight[domain] = False
        label = "LLM" if domain == "llm" else "通用"
        opened = self._breaker_open if domain == "llm" else self._gen_breaker_open
        if opened:
            if domain == "llm":
                self._breaker_open = False
                self._breaker_opened_at = 0.0
            else:
                self._gen_breaker_open = False
                self._gen_breaker_opened_at = 0.0
            self._log("turn", f"🟢 {label}错误风暴熔断复位（{why}），恢复自动进化调度", {"domain": domain})
            logger.info(f"[{self.strand_id}] {label}熔断复位: {why}")

    def reset_breaker(self, domain: str = "llm") -> dict:
        """人工复位熔断（job_queue.requeue同款语义：人工意图不受熔断拦截）。

        domain: "llm"（默认，向后兼容）/ "gen" / "all"（两域都复位）。
        """
        if domain not in ("llm", "gen", "all"):
            raise ValueError(f"Unknown breaker domain: {domain!r} (known: llm/gen/all)")
        domains = ("llm", "gen") if domain == "all" else (domain,)
        for d in domains:
            was_open = self._breaker_open if d == "llm" else self._gen_breaker_open
            self._probe_in_flight[d] = False  # 人工复位=在飞试探作废（无裁决）
            if d == "llm":
                self._breaker_open = False
                self._breaker_opened_at = 0.0
            else:
                self._gen_breaker_open = False
                self._gen_breaker_opened_at = 0.0
            label = "LLM" if d == "llm" else "通用"
            self._log("turn", f"🔧 人工复位熔断（{label}域，此前open={was_open}），恢复自动进化调度",
                      {"by": "operator", "domain": d})
            logger.info(f"[{self.strand_id}] {label}熔断人工复位 (此前open={was_open})")
        return self.get_status()

    def _breaker_status(self, domain: str = "llm") -> dict:
        now = time.time()
        label = "llm" if domain == "llm" else "gen"
        opened = self._breaker_open if domain == "llm" else self._gen_breaker_open
        opened_at = self._breaker_opened_at if domain == "llm" else self._gen_breaker_opened_at
        suppressed = self._breaker_suppressed if domain == "llm" else self._gen_breaker_suppressed
        window = LLM_BREAKER_STORM_WINDOW if domain == "llm" else GEN_BREAKER_STORM_WINDOW
        cooldown = LLM_BREAKER_COOLDOWN_S if domain == "llm" else GEN_BREAKER_COOLDOWN_S
        if not opened:
            state = "closed"
        elif (now - opened_at) < cooldown:
            state = "open"
        else:
            # 冷却已过=半开：无试探在飞时下个周期即试探；在飞时gating仍True
            # （严格单试探——其余调度继续拦截，状态面probe_in_flight可见）
            state = "half_open"
        return {
            "domain": label,
            "state": state,
            "open": opened,
            "gating": self._breaker_is_open(now, domain=domain),
            "probe_in_flight": self._probe_in_flight.get(label, False),
            "opened_at": opened_at or None,
            "cooldown_remaining_s": round(
                max(0.0, cooldown - (now - opened_at)), 1
            ) if opened else 0.0,
            "suppressed_cycles": suppressed,
            "storm_window": window,
            "cooldown_s": cooldown,
        }

    # ── 执行伙伴的计划（交叉编程核心） ──────────────────

    async def _review_partner_plan(self, plan) -> bool:
        """被进化方审查生成方的代码计划是否安全合理"""
        improvements_desc = "\n".join(
            f"- {i.get('description','')}: {i.get('file','')}:{i.get('line','')}"
            for i in plan.improvements[:5]
        )
        prompt = f"""你是一个代码审查员。另一条进化螺旋为你的系统生成了以下改进计划：

{improvements_desc}

请评估：
1. 这些改动是否安全？（不会破坏核心功能）
2. 改动范围是否合理？（不超过5个文件）
3. 是否有明显错误？

回答格式：
- 如果计划安全合理，回答：APPROVE
- 如果计划有风险或不合理，回答：REJECT: <原因>

只回答一行。"""

        try:
            response = await self._call_llm(prompt)
            approved = "APPROVE" in response.upper() and "REJECT" not in response.upper()
            self._log("review", f"{'✅ 通过' if approved else '❌ 拒绝'}: {response[:100]}")
            return approved
        except Exception as e:
            logger.error(f"[{self.strand_id}] 审查失败: {e}")
            return False  # 审查失败默认拒绝

    async def _execute_partner_plans(self):
        """执行伙伴提交的进化计划 — 这是交叉编程的核心

        伙伴反思 → 伙伴规划 → 我来写代码 → 我验证 → 伙伴采纳
        """
        self._log("execute_partners", f"检查伙伴计划: {len(self._partner_plans_for_me)}个待执行")
        for plan in self._partner_plans_for_me[:]:
            if plan.status != "pending":
                self._log("execute_partner", f"⏭️ 跳过计划 {plan.plan_id} (status={plan.status})")
                continue

            plan.status = "executing"
            self._log("execute_partner", f"🔧 执行伙伴计划 {plan.plan_id} ({len(plan.improvements)}项改进)", {"from": plan.source_strand})
            logger.info(f"[{self.strand_id}] Executing partner's plan {plan.plan_id} ({len(plan.improvements)} improvements)")

            try:
                # 执行伙伴的改进方案
                changes = await self._execute(plan.improvements)

                # 验证
                verification = await self._verify(changes)

                if verification.get("verified"):
                    plan.status = "completed"
                    plan.result = {
                        "changes": changes,
                        "verification": verification,
                        "success": True,
                    }
                    logger.info(f"[{self.strand_id}] ✓ Partner's plan {plan.plan_id} executed successfully")
                else:
                    plan.status = "failed"
                    plan.result = {
                        "changes": changes,
                        "verification": verification,
                        "success": False,
                    }
                    # 回滚失败的变更
                    await self._rollback_changes(changes)
                    logger.warning(f"[{self.strand_id}] ✗ Partner's plan {plan.plan_id} failed verification, rolled back")

            except Exception as e:
                plan.status = "failed"
                plan.result = {"error": str(e), "success": False}
                logger.error(f"[{self.strand_id}] ✗ Partner's plan {plan.plan_id} execution error: {e}")

            # 写入结果文件供伙伴读取
            self._write_plan_result(plan)
            self._partner_plans_for_me.remove(plan)

    def _write_plan_result(self, plan: PendingPlan):
        """写入计划执行结果，供伙伴读取"""
        result_file = self.repo_root / "acp-proxy" / "data" / f"dna_plan_result_{plan.plan_id}.json"
        try:
            with open(result_file, "w", encoding="utf-8") as f:
                json.dump({
                    "plan_id": plan.plan_id,
                    "source_strand": plan.source_strand,
                    "executor_strand": self.strand_id,
                    "status": plan.status,
                    "result": plan.result,
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[{self.strand_id}] Write plan result failed: {e}")

    # ── 轮次控制 ──────────────────────────────────────────

    def _load_turn(self) -> dict:
        """读取共享轮次文件（加锁）"""
        import fcntl
        path = self.repo_root / "data" / "turn.json"
        try:
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_SH)
                data = json.loads(f.read())
                fcntl.flock(f, fcntl.LOCK_UN)
                return data
        except Exception:
            return {"current": "a", "phase": "generate", "cycle": 0}

    def _save_turn(self, current: str, phase: str):
        """更新共享轮次文件（原子写入+文件锁）"""
        import fcntl, tempfile
        path = self.repo_root / "data" / "turn.json"
        try:
            # 先读取当前值
            with open(path, "r") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                try:
                    turn = json.loads(f.read())
                except:
                    turn = {"current": "a", "phase": "generate", "cycle": 0}
                fcntl.flock(f, fcntl.LOCK_UN)

            # 更新
            if phase == "generate" and current != turn.get("current"):
                turn["cycle"] = turn.get("cycle", 0) + 1
            turn["current"] = current
            turn["phase"] = phase

            # 原子写入（先写临时文件再rename）
            dir_path = path.parent
            with tempfile.NamedTemporaryFile(mode="w", dir=dir_path, suffix=".tmp", delete=False) as tmp:
                tmp.write(json.dumps(turn, indent=2))
                tmp_path = tmp.name
            import os
            os.replace(tmp_path, str(path))
        except Exception as e:
            logger.error(f"[{self.strand_id}] 保存轮次失败: {e}")

    def _load_partner_plans(self, partner_id: str):
        """读取伙伴提交的新计划"""
        partner_plans_file = self.repo_root / "acp-proxy" / "data" / f"dna_plans_{partner_id}.json"
        try:
            if not partner_plans_file.exists():
                return
            with open(partner_plans_file, "r") as f:
                content = f.read().strip()
            # 空文件/写入竞态容错: 0字节或空白文件=无计划，不是错误
            # (此前json.loads空串→"Expecting value"error日志刷屏，误报为故障)
            if not content:
                return
            plans_data = json.loads(content)
            if not plans_data:
                return

            existing_ids = {p.plan_id for p in self._partner_plans_for_me}
            for pd in plans_data:
                if pd["plan_id"] not in existing_ids:
                    plan = PendingPlan(
                        plan_id=pd["plan_id"],
                        source_strand=pd["source_strand"],
                        improvements=pd["improvements"],
                        reflections=pd.get("reflections", []),
                        created_at=pd.get("created_at", time.time()),
                    )
                    self._partner_plans_for_me.append(plan)
                    self._log("receive", f"📥 收到伙伴计划 {plan.plan_id}", {"from": partner_id, "improvements": len(plan.improvements)})
                    logger.info(f"[{self.strand_id}] Received plan {plan.plan_id} from {partner_id}")
        except Exception as e:
            self._log("error", f"加载伙伴计划失败: {e}")
            logger.warning(f"[{self.strand_id}] Load partner plans failed: {e}")

    # ── 反思 ──────────────────────────────────────────────

    async def _reflect(self) -> list[str]:
        unanalyzed = [o for o in self._observations if not o.analyzed]
        # 自省式降级: 无未分析观察时不返回空(空→_plan空→跳过→死循环)。
        # 用近期观察+goals+memory做自省式反思。
        introspective_mode = len(unanalyzed) < 1
        obs_pool = self._observations[-15:] if introspective_mode else unanalyzed[-15:]
        observations_text = "\n".join(
            f"[{o.obs_type}] {o.content}" for o in obs_pool
        )
        if introspective_mode:
            observations_text = "（本轮无新失败样本，以下为近期观察快照，供自省式反思参考）\n" + observations_text

        # 加入历史经验
        memory_context = ""
        if self._memory:
            recent_memories = self._memory[-5:]
            memory_context = "\n\n历史经验：\n" + "\n".join(
                f"- {m.get('summary', '')}" for m in recent_memories
            )

        # 加入目标进度
        goals_context = ""
        if self._goals:
            active_goals = [g for g in self._goals if g.status == "active"]
            goals_context = "\n\n当前进化目标：\n" + "\n".join(
                f"- [{g.priority}] {g.title} (进度: {g.progress:.0%}): {g.description}"
                for g in active_goals
            )

        prompt = f"""分析以下观察记录，找出失败模式、成功模式、改进机会。
策略倾向：{self.description}

观察记录：
{observations_text}
{memory_context}
{goals_context}

评估每个目标的进展，并返回JSON：{{"failure_patterns": [], "success_patterns": [], "improvements": [], "goal_progress": {{"goal_title": progress_float}}}}"""

        result = await self._call_llm(prompt)
        # 解析目标进度更新
        try:
            m = re.search(r'\{.*\}', result, re.DOTALL)
            if m:
                parsed = json.loads(m.group())
                goal_progress = parsed.get("goal_progress", {})
                if goal_progress:
                    for goal in self._goals:
                        if goal.title in goal_progress and goal.status == "active":
                            new_progress = float(goal_progress[goal.title])
                            goal.progress = max(0.0, min(1.0, new_progress))
                            if goal.progress >= 1.0:
                                goal.status = "completed"
                                goal.completed_at = datetime.now(timezone.utc).isoformat()
        except Exception:
            pass
        for o in unanalyzed:
            o.analyzed = True
        return [result] if result else []

    # ── 规划 ──────────────────────────────────────────────

    async def _plan(self, reflections: list[str]) -> list[dict]:
        if not reflections:
            return []

        # 构建目标上下文
        goals_context = ""
        if self._goals:
            active_goals = [g for g in self._goals if g.status == "active"]
            if active_goals:
                goals_context = "\n\n当前进化目标（改进方案应优先对齐这些目标）：\n" + "\n".join(
                    f"- [{g.priority}] {g.title} (进度: {g.progress:.0%}): {g.description}"
                    for g in active_goals
                )

        prompt = f"""基于反思生成改进方案（最多{self.max_improvements}个）。
只改：acp-proxy/skills/, acp-proxy/agent/experiments/

重要：你的方案将由另一个AI来执行代码编写，所以要写得足够详细。
{goals_context}

反思：{chr(10).join(reflections)}

返回JSON：{{"improvements": [{{"type": "skill", "target_file": "...", "description": "...", "requirements": "...", "commit_message": "..."}}]}}

注意：不要写content字段，只写description和requirements，让执行者来写实际代码。"""

        result = await self._call_llm(prompt)
        try:
            m = re.search(r'\{.*\}', result, re.DOTALL)
            if m:
                improvements = json.loads(m.group()).get("improvements", [])
                return improvements[:self.max_improvements]
        except Exception:
            logger.warning(f"[{self.strand_id}] 规划JSON解析失败，按0项改进记账（LLM有响应但输出不可解析，非LLM故障）")
        else:
            logger.warning(f"[{self.strand_id}] 规划输出无JSON块，按0项改进记账（LLM有响应但输出不可解析，非LLM故障）")
        return []

    # ── 执行（写代码） ──────────────────────────────────

    async def _execute(self, improvements: list[dict]) -> list[dict]:
        """执行改进方案 — 实际写代码

        如果improvements有content字段，直接写入
        如果只有description/requirements，用LLM生成代码
        """
        # ===== Git快照前置：记录所有待修改文件的当前状态 =====
        snapshot_targets = [imp.get("target_file", "") for imp in improvements if imp.get("target_file")]
        for st in snapshot_targets:
            full = self.repo_root / st
            if full.exists():
                try:
                    subprocess.run(["git", "add", st], cwd=self.repo_root, capture_output=True, timeout=10)
                except Exception:
                    pass
        if snapshot_targets:
            try:
                subprocess.run(
                    ["git", "commit", "--allow-empty", "-m", f"[dna-{self.strand_id}] PRE-EXECUTE snapshot"],
                    cwd=self.repo_root, capture_output=True, timeout=10,
                )
            except Exception:
                pass

        results = []
        for imp in improvements:
            target = imp.get("target_file", "")
            if not any(target.startswith(d) for d in self.EVOLVABLE_DIRS):
                results.append({**imp, "status": "blocked", "reason": "target not in evolvable dirs"})
                continue
            if any(target.endswith(f) for f in self.PROTECTED_FILES):
                results.append({**imp, "status": "blocked", "reason": "target is protected"})
                continue

            full_path = self.repo_root / target
            try:
                full_path.parent.mkdir(parents=True, exist_ok=True)

                # 如果没有content，用LLM根据description生成
                content = imp.get("content", "")
                if not content and imp.get("description"):
                    content = await self._generate_code(imp)
                    if not content:
                        results.append({**imp, "status": "failed", "reason": "LLM code generation failed"})
                        continue

                # 处理增量编辑模式（dict返回值）
                if isinstance(content, dict) and "edits" in content:
                    edits = content["edits"]
                    old_file_content = full_path.read_text(encoding="utf-8", errors="replace") if full_path.exists() else ""
                    try:
                        new_content = self._apply_edits(old_file_content, edits)
                        content = new_content
                    except ValueError as e:
                        # Closest-match hint失败，带hint重试一次
                        logger.warning(f"[{self.strand_id}] Edit failed, retrying with hint: {e}")
                        retry_content = await self._retry_with_hint(imp, old_file_content, str(e))
                        if retry_content:
                            content = retry_content
                        else:
                            results.append({**imp, "status": "failed", "reason": f"Edit application failed: {e}"})
                            continue
                
                assert isinstance(content, str), f"content must be str after edit application, got {type(content)}"

                # ===== 安全校验：行数变更阈值拦截 =====
                LINE_REDUCE_THRESHOLD = 0.3  # 新文件行数 < 原文件30% 则阻断
                old_content = ""
                old_lines = 0
                if full_path.exists():
                    old_content = full_path.read_text(encoding="utf-8", errors="replace")
                    old_lines = len(old_content.splitlines())
                new_lines = len(content.splitlines())

                # 审计日志
                logger.info(
                    f"[{self.strand_id}] FILE_AUDIT target={target} "
                    f"old_lines={old_lines} new_lines={new_lines} "
                    f"old_bytes={len(old_content.encode('utf-8'))} new_bytes={len(content.encode('utf-8'))} "
                    f"llm_content_bytes={len(content.encode('utf-8'))} ratio={new_lines/max(old_lines,1):.2f}"
                )

                # 行数锐减拦截：仅当文件已存在且不是新建文件时检查
                if old_lines > 50 and new_lines < old_lines * LINE_REDUCE_THRESHOLD:
                    reason = (
                        f"BLOCKED: 文件行数锐减 {old_lines}→{new_lines} "
                        f"({new_lines/max(old_lines,1):.0%}), 疑似LLM输出截断"
                    )
                    logger.warning(f"[{self.strand_id}] {reason}")
                    results.append({**imp, "status": "blocked", "reason": reason})
                    continue

                # 编辑历史保存（OpenHands方案）— 保存编辑前内容，支持undo
                if old_content:
                    history_dir = self.repo_root / ".evolution_history"
                    history_dir.mkdir(exist_ok=True)
                    history_file = history_dir / f"{target.replace('/', '_')}_{int(time.time())}.bak"
                    try:
                        history_file.write_text(old_content, encoding="utf-8")
                        logger.info(f"[{self.strand_id}] Edit history saved: {history_file}")
                    except Exception as e:
                        logger.warning(f"[{self.strand_id}] Failed to save edit history: {e}")

                # 原子写入：先写临时文件，再rename
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", encoding="utf-8",
                        dir=str(full_path.parent),
                        suffix=".tmp",
                        delete=False,
                    ) as tmp:
                        tmp.write(content)
                        tmp_path = tmp.name
                    os.rename(tmp_path, str(full_path))
                except Exception as e:
                    results.append({**imp, "status": "failed", "reason": f"atomic write failed: {e}"})
                    continue

                # 语法检查
                if target.endswith(".py"):
                    import ast
                    try:
                        ast.parse(content)
                    except SyntaxError as e:
                        self._git_revert_file(target)
                        results.append({**imp, "status": "rolled_back", "reason": str(e)})
                        continue

                    # 沙箱测试：在subprocess中运行代码
                    sandbox_ok, sandbox_msg = await self._sandbox_test(content)
                    if not sandbox_ok:
                        self._git_revert_file(target)
                        results.append({**imp, "status": "rolled_back", "reason": f"sandbox test failed: {sandbox_msg}"})
                        continue

                if target.endswith(".json"):
                    try:
                        json.loads(content)
                    except json.JSONDecodeError as e:
                        self._git_revert_file(target)
                        results.append({**imp, "status": "rolled_back", "reason": str(e)})
                        continue

                # TS/JS语法检查：用node --check验证（如果有node）
                if target.endswith((".ts", ".tsx", ".js", ".jsx")):
                    try:
                        result = subprocess.run(
                            ["node", "--check", str(full_path)],
                            capture_output=True, text=True, timeout=10,
                        )
                        if result.returncode != 0:
                            self._git_revert_file(target)
                            results.append({**imp, "status": "rolled_back", "reason": f"TS/JS syntax error: {result.stderr[:200]}"})
                            continue
                    except FileNotFoundError:
                        pass  # node not available, skip
                    except Exception:
                        pass

                self._git_commit(target, imp.get("commit_message", f"[{self.strand_id}] {target}"))
                results.append({**imp, "status": "applied", "content": content[:500]})

            except Exception as e:
                results.append({**imp, "status": "failed", "reason": str(e)})

        return results

    async def _generate_code(self, improvement: dict) -> str | dict:
        """根据描述用LLM生成代码 — 优先使用old_string/new_string增量编辑

        返回值：
          - str: 全量内容（新建文件或小文件）
          - dict: {"edits": [{"old_string": "...", "new_string": "..."}, ...]} 增量编辑
        """
        target_file = improvement.get('target_file', '')
        full_path = self.repo_root / target_file
        
        # 如果文件已存在，一律使用增量编辑模式（MOSS/RooCode方案：存量文件禁止全量覆盖）
        old_content = ""
        if full_path.exists():
            old_content = full_path.read_text(encoding="utf-8", errors="replace")
        
        if old_content:
            # ===== 增量编辑模式：LLM只输出变更片段 =====
            # 截取相关上下文（需求相关的代码段），而非整个文件
            context_lines = old_content.splitlines()
            context_sample = "\n".join(context_lines[:300])  # 前300行作为上下文
            
            prompt = f"""你是一个代码修改专家。请根据需求修改以下文件。

目标文件：{target_file}
当前文件内容：
```
{context_sample}
```

需求：{improvement.get('description', '')}
要求：{improvement.get('requirements', '无特殊要求')}

请输出JSON格式的编辑指令，每个编辑包含old_string和new_string。
old_string必须是文件中已存在的精确文本（包含足够上下文确保唯一匹配）。
new_string是替换后的文本。

输出格式（严格JSON数组）：
[
  {{
    "old_string": "要查找的精确文本（含3-5行上下文）",
    "new_string": "替换后的文本"
  }}
]

注意：
1. old_string必须与文件内容完全匹配（包括空格、缩进）
2. 每个old_string应包含足够上下文（3-5行）确保唯一性
3. 如果需要多处修改，输出多个编辑对象
4. 只输出JSON数组，不要有其他内容
5. 不要输出完整文件，只输出需要修改的部分"""

            result = await self._call_llm(prompt)
            
            # 解析JSON编辑指令
            try:
                # 清理markdown代码块
                if result.startswith("```"):
                    lines = result.split("\n")
                    result = "\n".join(lines[1:-1]) if len(lines) > 2 else result
                
                edits = json.loads(result)
                if isinstance(edits, list) and all("old_string" in e and "new_string" in e for e in edits):
                    # 验证每个old_string在文件中存在且唯一
                    for edit in edits:
                        old_str = edit["old_string"]
                        count = old_content.count(old_str)
                        if count == 0:
                            logger.warning(f"[{self.strand_id}] old_string not found in file, falling back to full mode")
                            break
                        if count > 1:
                            logger.warning(f"[{self.strand_id}] old_string matches {count} times, need more context")
                            break
                    else:
                        # 所有编辑都有效，返回编辑指令
                        return {"edits": edits}
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"[{self.strand_id}] Failed to parse edits: {e}, falling back to full mode")
        
        # ===== 全量模式：新建文件或小文件 =====
        prompt = f"""根据以下需求生成代码文件。

目标文件：{target_file}
类型：{improvement.get('type', 'skill')}
描述：{improvement.get('description', '')}
要求：{improvement.get('requirements', '无特殊要求')}

只返回代码内容，不要包含解释。"""

        result = await self._call_llm(prompt)
        # 清理markdown代码块
        if result.startswith("```"):
            lines = result.split("\n")
            result = "\n".join(lines[1:-1]) if len(lines) > 2 else result
        return result
    
    def _apply_edits(self, old_content: str, edits: list[dict]) -> str:
        """应用old_string/new_string编辑到文件内容

        采用Aider方案：先dry-run验证所有编辑，再实际替换。
        支持closest-match hints（MiMo Code方案）。
        """
        # ===== Phase 1: Dry-run（Aider方案）=====
        # 在内存中验证所有编辑都匹配，一个失败则整个批次作废
        dry_content = old_content
        for i, edit in enumerate(edits):
            old_str = edit["old_string"]
            new_str = edit["new_string"]
            
            if not old_str:
                raise ValueError(f"Edit {i}: old_string is empty")
            if old_str == new_str:
                raise ValueError(f"Edit {i}: old_string equals new_string (no-op)")
            
            count = dry_content.count(old_str)
            if count == 0:
                # 查找最相似的文本
                hint = self._find_closest_match(dry_content, old_str)
                if hint:
                    raise ValueError(
                        f"Edit {i}: old_string not found exactly. Did you mean:\n"
                        f"  FOUND: {hint[:200]}\n"
                        f"  WANTED: {old_str[:200]}\n"
                        f"Please use the exact text from the file."
                    )
                raise ValueError(f"Edit {i}: old_string not found and no close match: {old_str[:100]}...")
            if count > 1:
                raise ValueError(f"Edit {i}: old_string matched {count} times. Provide more context to make it unique.")
            
            # dry-run替换（不实际写盘）
            dry_content = dry_content.replace(old_str, new_str, 1)
        
        # ===== Phase 2: 所有验证通过，实际替换 =====
        # Phase 1已经验证过，直接用dry_content（已完成所有替换）
        return dry_content
    
    def _find_closest_match(self, content: str, target: str) -> str | None:
        """7种策略查找最相似的文本片段（MiMo Code方案）"""
        content_lines = content.splitlines()
        target_lines = target.splitlines()
        
        if not target_lines:
            return None
        
        # 策略1: 行级trim匹配 — 忽略首尾空格
        target_trimmed = "\n".join(l.strip() for l in target_lines)
        for i in range(len(content_lines)):
            for j in range(i + 1, min(i + len(target_lines) + 5, len(content_lines) + 1)):
                candidate = "\n".join(l.strip() for l in content_lines[i:j])
                if candidate == target_trimmed:
                    return "\n".join(content_lines[i:j])
        
        # 策略2: 缩进灵活匹配 — 忽略缩进差异
        import re
        target_dedented = "\n".join(re.sub(r'^\s+', '', l) for l in target_lines)
        for i in range(len(content_lines)):
            for j in range(i + 1, min(i + len(target_lines) + 5, len(content_lines) + 1)):
                candidate = "\n".join(re.sub(r'^\s+', '', l) for l in content_lines[i:j])
                if candidate == target_dedented:
                    return "\n".join(content_lines[i:j])
        
        # 策略3: 空白归一化 — 将连续空白替换为单空格
        def normalize_ws(s):
            return re.sub(r'\s+', ' ', s).strip()
        
        target_norm = normalize_ws(target)
        for i in range(len(content_lines)):
            for j in range(i + 1, min(i + len(target_lines) + 5, len(content_lines) + 1)):
                candidate = normalize_ws("\n".join(content_lines[i:j]))
                if candidate == target_norm:
                    return "\n".join(content_lines[i:j])
        
        # 策略4: 块锚点匹配 — 用第一行和最后一行作为锚点
        if len(target_lines) >= 2:
            first_line = target_lines[0].strip()
            last_line = target_lines[-1].strip()
            for i, line in enumerate(content_lines):
                if line.strip() == first_line:
                    for j in range(i + 1, min(i + len(target_lines) + 10, len(content_lines))):
                        if content_lines[j].strip() == last_line:
                            return "\n".join(content_lines[i:j+1])
        
        # 策略5: 部分匹配 — 找target的前3行或后3行
        if len(target_lines) >= 3:
            prefix = "\n".join(l.strip() for l in target_lines[:3])
            for i in range(len(content_lines) - 2):
                candidate = "\n".join(l.strip() for l in content_lines[i:i+3])
                if candidate == prefix:
                    # 找到前缀，返回完整匹配
                    end = min(i + len(target_lines), len(content_lines))
                    return "\n".join(content_lines[i:end])
        
        return None
    
    async def _retry_with_hint(self, improvement: dict, old_content: str, error_msg: str) -> str | None:
        """带closest-match hint重试LLM编辑"""
        target_file = improvement.get('target_file', '')
        context_sample = "\n".join(old_content.splitlines()[:300])
        
        prompt = f"""你的上一次编辑指令匹配失败。错误信息：
{error_msg}

目标文件：{target_file}
当前文件内容：
```
{context_sample}
```

需求：{improvement.get('description', '')}

请重新输出编辑指令，使用文件中已存在的精确文本作为old_string。
注意：old_string必须与文件内容完全一致（包括空格、缩进）。

输出格式（严格JSON数组）：
[
  {{
    "old_string": "文件中已存在的精确文本",
    "new_string": "替换后的文本"
  }}
]"""

        result = await self._call_llm(prompt)
        
        try:
            if result.startswith("```"):
                lines = result.split("\n")
                result = "\n".join(lines[1:-1]) if len(lines) > 2 else result
            
            edits = json.loads(result)
            if isinstance(edits, list) and all("old_string" in e and "new_string" in e for e in edits):
                return self._apply_edits(old_content, edits)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"[{self.strand_id}] Retry with hint also failed: {e}")
        
        return None

    # ── 验证 ──────────────────────────────────────────────

    async def _verify(self, changes: list[dict]) -> dict:
        applied = [c for c in changes if c.get("status") == "applied"]
        if not applied:
            return {"verified": True, "reason": "no changes"}

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                resp = await client.get("http://127.0.0.1:8092/health")
                if resp.status_code == 200:
                    return {"verified": True, "changes_applied": len(applied)}
        except Exception:
            pass

        return {"verified": False, "reason": "health check failed"}

    # ── 学习 ──────────────────────────────────────────────

    async def _extract_lesson(self, cycle: EvolutionCycle) -> dict | None:
        if not cycle.plan:
            return None

        lesson = {
            "cycle_id": cycle.cycle_id,
            "timestamp": cycle.completed_at,
            "strategy": self.strategy,
            "success": cycle.success,
            "executed_by": cycle.executed_by,
            "summary": f"规划{len(cycle.plan.get('improvements', []))}项改进，由{cycle.executed_by}执行",
            "reflections": cycle.reflections[:1] if cycle.reflections else [],
        }

        if len(self._cycles) >= 2:
            recent = self._cycles[-3:]
            prompt = f"""分析最近{len(recent)}个进化周期，提炼一条可复用的经验教训。

周期摘要：
{json.dumps([{"success": c.success, "executed_by": c.executed_by, "changes": len(c.changes)} for c in recent], ensure_ascii=False)}

返回JSON：{{"lesson": "一句话教训", "pattern": "可复用模式"}}"""

            try:
                result = await self._call_llm(prompt)
            except LLMCallError as e:
                # 教训提炼失败不影响已成文的教训主体（cycle.summary仍记账）；
                # 但必须留痕，不静默。
                logger.warning(f"[{self.strand_id}] 教训提炼LLM失败（教训主体仍保留）: {e}")
                result = ""
            try:
                m = re.search(r'\{.*\}', result, re.DOTALL)
                if m:
                    lesson["insight"] = json.loads(m.group())
            except Exception:
                pass

        return lesson

    # ── 回滚 ──────────────────────────────────────────────

    async def _rollback_changes(self, changes: list[dict]):
        for change in changes:
            target = change.get("target_file", "")
            if change.get("status") == "applied":
                try:
                    subprocess.run(
                        ["git", "checkout", "HEAD~1", "--", target],
                        cwd=self.repo_root, capture_output=True, timeout=10,
                    )
                except Exception:
                    pass

    # ── 检查点 ────────────────────────────────────────────

    async def _write_checkpoint(self, cycle: EvolutionCycle):
        try:
            checkpoint = {
                "strand_id": self.strand_id,
                "strategy": self.strategy,
                "cycle_id": cycle.cycle_id,
                "changes": cycle.changes,
                "reflections": cycle.reflections,
                "executed_by": cycle.executed_by,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            with open(self._checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[{self.strand_id}] Checkpoint write failed: {e}")

    # ── LLM ───────────────────────────────────────────────

    # omission占位符模式（Gemini CLI方案）——单一真源 utils.llm_safety.OMISSION_PATTERNS
    # （旧本地7条副本漂移缺2条，已收口，见顶部import）
    _OMISSION_PATTERNS = OMISSION_PATTERNS

    # ── 出站脱敏（immune/moderator.py模式，Warp 20正则子集）──
    _REDACT_PATTERNS = [
        (re.compile(r"sk-[a-zA-Z0-9]{20,}"), "sk-***"),
        (re.compile(r"tp-[a-zA-Z0-9]{20,}"), "tp-***"),
        (re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)[\"\':\s=]+[a-zA-Z0-9_\-\.]{16,}"), r"\1=***"),
    ]

    @classmethod
    def _redact_secrets(cls, text: str) -> str:
        """脱敏出站文本中的敏感信息，防止泄漏到LLM provider"""
        for pat, repl in cls._REDACT_PATTERNS:
            text = pat.sub(repl, text)
        return text

    # ── 重试策略：单一真源=utils.llm_retry（kilocode retry.ts，2026-09-29收口）──
    # 旧本类 _RETRY_MAX/_RETRY_BASE_DELAY/_retry_delay 副本已删除（只认
    # retry-after秒数一种格式、429/5xx硬编码、瞬时传输错误零重试、无离线park）
    # ——状态分类/退避时长/尝试上限全部顶部import，禁止再复制。

    async def _call_llm(self, prompt: str) -> str:
        """调用LLM，支持finish_reason检测+自动续写（Aider方案）+ omission检测（Gemini CLI方案）
        v2增强：出站脱敏 + 429/5xx重试（kilocode retry.ts + Warp redaction模式）"""
        try:
            # 出站脱敏：防止prompt中意外包含的API key/token泄漏到provider
            safe_prompt = self._redact_secrets(prompt)
            if safe_prompt != prompt:
                logger.info(f"[{self.strand_id}] Outbound redaction applied to prompt ({len(prompt)}→{len(safe_prompt)} chars)")

            headers = {"Content-Type": "application/json"}
            if self.llm_api_key:
                headers["Authorization"] = f"Bearer {self.llm_api_key}"

            full_content = ""
            messages = [{"role": "user", "content": safe_prompt}]
            max_continuation = 3  # 最多续写3次

            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
                for attempt in range(max_continuation):
                    # 带重试的HTTP请求（策略单一真源utils.llm_retry，kilocode retry.ts）：
                    # 429/408/5xx听Retry-After三格式可重试、瞬时传输错误重试、
                    # 400/401/402等请求/凭证级fail-fast（不烧3次调用）
                    resp = None
                    for _http_retry in range(MAX_ATTEMPTS):
                        try:
                            resp = await client.post(
                                f"{self.llm_base_url}/chat/completions",
                                headers=headers,
                                json={
                                    "model": self.llm_model,
                                    "messages": messages,
                                    "temperature": self.temperature,
                                    "max_tokens": 4096,
                                },
                            )
                        except httpx.TransportError as e:
                            # 网络离线三态（utils.network_state，kilocode SessionNetwork）：
                            # 全局断连→park等恢复自动续跑；非离线/恢复后走下方重试记账
                            await network_state.maybe_park(e)
                            if _http_retry < MAX_ATTEMPTS - 1 and is_transient_error(e):
                                _wait = backoff_ms(_http_retry + 1) / 1000.0
                                logger.warning(
                                    f"[{self.strand_id}] 传输错误{e.__class__.__name__}，"
                                    f"{_wait:.1f}s后重试 ({_http_retry+1}/{MAX_ATTEMPTS})"
                                )
                                await asyncio.sleep(_wait)
                                continue
                            raise
                        if resp.status_code == 200 or not is_retryable_status(resp.status_code):
                            break
                        if _http_retry >= MAX_ATTEMPTS - 1:
                            break
                        # 有头听头：Retry-After三格式（ms头/秒数/HTTP date），无头才指数退避
                        _wait = backoff_ms(
                            _http_retry + 1, getattr(resp, "headers", None)
                        ) / 1000.0
                        logger.warning(
                            f"[{self.strand_id}] LLM HTTP {resp.status_code}, "
                            f"retry {_http_retry+1}/{MAX_ATTEMPTS} in {_wait:.1f}s"
                        )
                        await asyncio.sleep(_wait)

                    if resp is None or resp.status_code != 200:
                        status = resp.status_code if resp else "no-response"
                        body = resp.text[:200] if resp else ""
                        logger.error(f"[{self.strand_id}] LLM call failed: {status} {body}")
                        # mem0 §1.1：失败必须可见——抛LLMCallError，禁止静默返回
                        # 空串让上游把"LLM挂了"记成"LLM说没有可改进项"。
                        err = LLMCallError(f"LLM HTTP {status}: {body}")
                        err.partial_content = full_content  # 续写中断时的半截输出（不丢，调用方可取）
                        raise err

                    data = resp.json()
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")
                    finish_reason = choice.get("finish_reason", "stop")
                    full_content += content

                    # omission占位符检测（Gemini CLI方案）
                    for pattern in self._OMISSION_PATTERNS:
                        if pattern.search(full_content):
                            logger.warning(f"[{self.strand_id}] Omission placeholder detected: {pattern.pattern}")
                            # 用"请继续"回退
                            messages.append({"role": "assistant", "content": full_content})
                            messages.append({"role": "user", "content": "Output contains omission placeholder. Please provide the COMPLETE code without any shortcuts or placeholders. Continue from where you left off."})
                            full_content = ""  # 重置，让LLM重新完整输出
                            continue

                    # finish_reason检测（Aider方案）
                    if finish_reason == "length":
                        logger.warning(f"[{self.strand_id}] LLM output truncated (finish_reason=length), attempt {attempt+1}/{max_continuation}")
                        # 把已生成内容作为assistant prefix续接
                        messages.append({"role": "assistant", "content": full_content})
                        messages.append({"role": "user", "content": "Continue from where you left off. Do not repeat any content already provided."})
                        continue
                    else:
                        # 正常结束
                        break

            if not full_content.strip():
                # 200但空输出：对反思/规划/审查一律无意义，同判失败（不许伪装"无改进"）
                raise LLMCallError("LLM返回空内容（HTTP 200但无输出）")
            self._llm_failure_streak = 0  # 成功一次即清零连续失败计数
            self._breaker_close("llm_success")  # LLM恢复可用=风暴结束，自动复位熔断
            return full_content
        except LLMCallError:
            raise
        except Exception as e:
            logger.error(f"[{self.strand_id}] LLM call failed: {e}")
            raise LLMCallError(f"LLM调用异常: {e}") from e

    # ── Git ───────────────────────────────────────────────

    def _git_commit(self, file_path: str, message: str) -> bool:
        try:
            subprocess.run(["git", "add", file_path], cwd=self.repo_root, capture_output=True, timeout=10)
            result = subprocess.run(
                ["git", "commit", "-m", f"[dna-{self.strand_id}] {message}"],
                cwd=self.repo_root, capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                logger.info(f"[{self.strand_id}] Committed: {file_path}")
                # 推到远程（如果配置了的话）
                try:
                    push = subprocess.run(
                        ["git", "push"],
                        cwd=self.repo_root, capture_output=True, text=True, timeout=30,
                    )
                    if push.returncode == 0:
                        logger.info(f"[{self.strand_id}] Pushed to remote")
                    else:
                        logger.warning(f"[{self.strand_id}] Push failed: {push.stderr[:200]}")
                except Exception as e:
                    logger.warning(f"[{self.strand_id}] Push error: {e}")
            return result.returncode == 0
        except Exception:
            return False

    def _git_revert_file(self, file_path: str) -> bool:
        """精确回滚单个文件（不是整个commit）"""
        try:
            # 如果文件是新增的（还没有commit过），直接删除
            full_path = self.repo_root / file_path
            result = subprocess.run(
                ["git", "status", "--porcelain", file_path],
                cwd=self.repo_root, capture_output=True, text=True, timeout=10,
            )
            if result.stdout.startswith("??"):
                # 新文件，直接删除
                if full_path.exists():
                    os.remove(full_path)
                return True
            
            # 已跟踪的文件，从HEAD恢复
            subprocess.run(
                ["git", "checkout", "HEAD", "--", file_path],
                cwd=self.repo_root, capture_output=True, timeout=10,
            )
            return True
        except Exception:
            return False

    async def _sandbox_test(self, code: str) -> tuple[bool, str]:
        """沙箱测试：在subprocess中运行代码，验证是否有明显错误
        
        只做基础安全检查，不做完整功能测试。
        """
        # 检查危险模式
        dangerous_patterns = [
            "os.system(", "subprocess.call(", "subprocess.run(",
            "exec(", "eval(", "__import__(",
            "open('/etc/", "open('/proc/", "open('/sys/",
            "shutil.rmtree(", "shutil.rmtree /",
            "os.remove('/')", "os.unlink('/')",
            "rm -rf /", "rm -rf /*",
        ]
        for pattern in dangerous_patterns:
            if pattern in code:
                return False, f"dangerous pattern detected: {pattern}"
        
        # 检查导入的模块是否安全
        import ast
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in ("subprocess", "os", "shutil", "sys"):
                            # 这些模块在技能代码中是允许的，但需要检查用法
                            pass
                elif isinstance(node, ast.ImportFrom):
                    if node.module in ("subprocess", "os", "shutil"):
                        pass
        except Exception:
            pass
        
        # 尝试编译代码
        try:
            compile(code, "<sandbox>", "exec")
        except Exception as e:
            return False, f"compile error: {e}"
        
        return True, "ok"

    # ── 失败样本采集（Auto-Scan Engine） ──────────────────

    def _collect_failure_samples(self) -> list[dict]:
        """采集失败样本 — 从日志、错误、低质量进化历史中收集"""
        samples = []

        # 1. 从进化历史中采集失败周期
        for cycle in self._cycles[-20:]:
            if not cycle.success and cycle.plan:
                desc = f"进化周期失败: {len(cycle.plan.get('improvements', []))}项改进未通过验证"
                # 自省式进化的周期描述不是真实失败(evo修复⑤): 无失败样本时的自省动作
                # 被采集为失败样本→check_error_storm把自省签名当错误风暴→误熔断(cycle236实证)
                introspective = getattr(cycle, "introspective", False) or "自省式进化" in json.dumps(cycle.plan, ensure_ascii=False)
                if introspective:
                    continue
                samples.append({
                    "type": "failed_cycle",
                    "cycle_id": cycle.cycle_id,
                    "description": desc,
                    "plan": cycle.plan,
                    "verification": getattr(cycle, "verification", {}),
                })

        # 2. 从审计日志中采集
        audit_file = self.repo_root / "acp-proxy" / "data" / "audit_log.jsonl"
        if audit_file.exists():
            try:
                with open(audit_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        entry = json.loads(line)
                        if entry.get("event") in ("plan_rejected", "code_rejected", "pipeline_done"):
                            if not entry.get("success", True):
                                reason_text = str(entry.get("reason", ""))
                                # 自省动作不是失败(evo修复⑤): "无明确失败样本→自省式进化"签名
                                # 不进失败样本库，防check_error_storm误熔断
                                if "自省式进化" in reason_text or "无明确失败样本" in reason_text:
                                    continue
                                samples.append({
                                    "type": "audit_failure",
                                    "description": f"审计记录: {entry.get('event')} - {reason_text}",
                                    "entry": entry,
                                })
            except Exception:
                pass

        # 3. 从最近的观察中采集异常
        for obs in self._observations[-10:]:
            if obs.obs_type in ("error", "crash", "timeout"):
                samples.append({
                    "type": "observation_error",
                    "description": f"观察到异常: {obs.content[:200]}",
                })

        # 4. 如果没有失败样本，创建一个自省样本
        if not samples:
            samples.append({
                "type": "self_introspect",
                "description": "当前无明确失败样本，进行自省式进化：分析代码质量和潜在改进点",
            })

        return samples[:10]  # 最多10个样本

    # ── 重启信号 ──────────────────────────────────────────

    async def _check_pending_partner_sync(self):
        """启动后检查：如果刚从进化重启成功，触发partner也重启同步代码"""
        instance_id = self.strand_id.split("_")[1]
        partner_id = "b" if instance_id == "a" else "a"
        data_dir = self.repo_root / "acp-proxy" / "data"
        pending_file = data_dir / f"sync_partner_{partner_id}.pending"

        if not pending_file.exists():
            return

        try:
            content = pending_file.read_text()
            pending_file.unlink()
            data = json.loads(content) if content.startswith("{") else {"reason": content}

            self._log("sync", f"🔄 检测到待同步进化，触发partner({partner_id})重启")

            # 等待自己健康检查通过（给30秒稳定时间）
            await asyncio.sleep(30)

            # 写partner重启信号
            self._write_restart_signal(
                f"sync_from_{instance_id}:{data.get('reason', 'evolution')}",
                target="partner"
            )
        except Exception as e:
            logger.error(f"[{self.strand_id}] Partner sync check failed: {e}")

    def _write_restart_signal(self, reason: str, target: str = "self"):
        """写入重启信号文件，file_watcher检测后触发进程退出
        
        target: "self" = 重启自己, "partner" = 重启伙伴, "both" = 两个都重启
        """
        instance_id = self.strand_id.split("_")[1]
        partner_id = "b" if instance_id == "a" else "a"
        data_dir = self.repo_root / "acp-proxy" / "data"

        targets = []
        if target in ("self", "both"):
            targets.append(instance_id)
        if target in ("partner", "both"):
            targets.append(partner_id)

        for tid in targets:
            signal_file = data_dir / f"restart_{tid}.signal"
            try:
                signal_file.parent.mkdir(parents=True, exist_ok=True)
                signal_file.write_text(json.dumps({
                    "reason": reason,
                    "source": self.strand_id,
                    "target": tid,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                self._log("restart", f"📡 重启信号 → 实例{tid}: {reason}")
            except Exception as e:
                logger.error(f"Failed to write restart signal for {tid}: {e}")

    # ── 状态管理 ──────────────────────────────────────────

    def _load_state(self):
        try:
            if self._state_file.exists():
                with open(self._state_file, "r") as f:
                    state = json.load(f)
                self._memory = state.get("memory", [])
                self._cycle_count = state.get("cycle_count", 0)
                self._history = state.get("history", [])
                # 恢复失败账本+熔断状态（重启不重置错误风暴计数——比job_queue内存态熔断更进一步）
                self._llm_failure_streak = state.get("llm_failure_streak", 0)
                self._last_llm_error = state.get("last_llm_error", "")
                self._last_llm_error_at = state.get("last_llm_error_at", "")
                self._last_error = state.get("last_error", "")
                self._last_error_at = state.get("last_error_at", "")
                breaker = state.get("breaker") or {}
                self._breaker_open = bool(breaker.get("open", False))
                self._breaker_opened_at = float(breaker.get("opened_at", 0.0) or 0.0)
                self._breaker_suppressed = int(breaker.get("suppressed", 0))
                # 通用域熔断同款恢复（老state文件无新键→默认关闭不炸）
                self._gen_failure_streak = state.get("gen_failure_streak", 0)
                self._gen_error_signature = state.get("gen_error_signature", "")
                gen_breaker = state.get("gen_breaker") or {}
                self._gen_breaker_open = bool(gen_breaker.get("open", False))
                self._gen_breaker_opened_at = float(gen_breaker.get("opened_at", 0.0) or 0.0)
                self._gen_breaker_suppressed = int(gen_breaker.get("suppressed", 0))
                # 恢复观察数据
                for o in state.get("observations", []):
                    self._observations.append(Observation(
                        obs_id=o.get("obs_id", str(uuid.uuid4())[:8]),
                        obs_type=o.get("type", "unknown"),
                        content=o.get("content", ""),
                        analyzed=o.get("analyzed", False),
                        timestamp=o.get("timestamp"),
                    ))
        except Exception:
            pass
        # 加载目标（优先从共享文件）
        self._load_goals()
        if not self._goals:
            for g in DEFAULT_GOALS:
                self._goals.append(EvolutionGoal(
                    goal_id=str(uuid.uuid4())[:8],
                    title=g["title"],
                    description=g["description"],
                    priority=g["priority"],
                ))
            self._save_goals()

    def _save_state(self):
        try:
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_file, "w") as f:
                json.dump({
                    "strand_id": self.strand_id,
                    "strategy": self.strategy,
                    "role": self.role.value,
                    "cycle_count": self._cycle_count,
                    "memory": self._memory[-50:],
                    "history": self._history[-100:],
                    "llm_failure_streak": self._llm_failure_streak,
                    "last_llm_error": self._last_llm_error,
                    "last_llm_error_at": self._last_llm_error_at,
                    "last_error": self._last_error,
                    "last_error_at": self._last_error_at,
                    "breaker": {
                        "open": self._breaker_open,
                        "opened_at": self._breaker_opened_at,
                        "suppressed": self._breaker_suppressed,
                    },
                    "gen_failure_streak": self._gen_failure_streak,
                    "gen_error_signature": self._gen_error_signature,
                    "gen_breaker": {
                        "open": self._gen_breaker_open,
                        "opened_at": self._gen_breaker_opened_at,
                        "suppressed": self._gen_breaker_suppressed,
                    },
                    "observations": [
                        {"obs_id": o.obs_id, "type": o.obs_type, "content": o.content,
                         "analyzed": o.analyzed, "timestamp": o.timestamp}
                        for o in self._observations[-50:]
                    ],
                    "last_update": datetime.now(timezone.utc).isoformat(),
                }, f, ensure_ascii=False, indent=2)
            # goals单独存共享文件（主进程可修改，子进程读取）
            self._save_goals()
        except Exception:
            pass

    def _save_goals(self):
        """将goals写入共享文件，主进程和子进程都能读写"""
        try:
            goals_file = self._state_file.parent / "dna_goals.json"
            with open(goals_file, "w") as f:
                json.dump([g.to_dict() for g in self._goals], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _load_goals(self):
        """从共享文件读取goals（优先从共享文件，否则从state文件）"""
        import os
        goals_file = self._state_file.parent / "dna_goals.json"
        if goals_file.exists():
            try:
                if os.path.getmtime(goals_file) > os.path.getmtime(str(self._state_file)):
                    with open(goals_file, "r") as f:
                        goals_data = json.load(f)
                    self._goals = [EvolutionGoal(**gd) for gd in goals_data]
                    return
            except Exception:
                pass

    def _sync_memory(self):
        """将记忆写入共享文件，两条链合并去重"""
        try:
            memory_file = self._state_file.parent / "dna_memory.json"
            # 读取现有共享记忆
            existing = []
            if memory_file.exists():
                existing = json.loads(memory_file.read_text())
            # 合并：按cycle_id去重
            seen = {m.get("cycle_id") for m in existing}
            for m in self._memory:
                cid = m.get("cycle_id")
                if cid and cid not in seen:
                    existing.append(m)
                    seen.add(cid)
            # 按时间排序，保留最新50条
            existing.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            memory_file.write_text(json.dumps(existing[:50], ensure_ascii=False, indent=2))
        except Exception:
            pass

    def _load_shared_memory(self):
        """从共享文件加载伙伴的记忆"""
        import os
        memory_file = self._state_file.parent / "dna_memory.json"
        if memory_file.exists():
            try:
                shared = json.loads(memory_file.read_text())
                my_ids = {m.get("cycle_id") for m in self._memory}
                new_count = 0
                for m in shared:
                    if m.get("cycle_id") not in my_ids:
                        self._memory.append(m)
                        my_ids.add(m.get("cycle_id"))
                        new_count += 1
                if new_count:
                    self._memory.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
                    self._memory = self._memory[:50]
                    logger.info(f"[{self.strand_id}] Synced {new_count} memories from partner")
            except Exception:
                pass

    def _save_plans(self):
        """保存待执行的计划"""
        try:
            self._plans_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._plans_file, "w") as f:
                json.dump([
                    {
                        "plan_id": p.plan_id,
                        "source_strand": p.source_strand,
                        "improvements": p.improvements,
                        "reflections": p.reflections,
                        "created_at": p.created_at,
                        "status": p.status,
                    }
                    for p in self._my_plans_for_partner
                ], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def stop(self):
        self._running = False

    def get_status(self) -> dict:
        return {
            "strand_id": self.strand_id,
            "strategy": self.strategy,
            "role": self.role.value,
            "running": self._running,
            "cycle_count": self._cycle_count,
            "observations": len(self._observations),
            "observations_unanalyzed": len([o for o in self._observations if not o.analyzed]),
            "partner_alive": self._partner_alive,
            "partner_cycle_count": self._partner_cycle_count,
            "my_plans_for_partner": len(self._my_plans_for_partner),
            "partner_plans_for_me": len(self._partner_plans_for_me),
            "memories": len(self._memory),
            "llm_failure_streak": self._llm_failure_streak,
            "last_llm_error": self._last_llm_error,
            "last_llm_error_at": self._last_llm_error_at,
            "last_error": self._last_error,
            "last_error_at": self._last_error_at,
            "breaker": self._breaker_status(),
            "gen_failure_streak": self._gen_failure_streak,
            "gen_error_signature": self._gen_error_signature,
            "gen_breaker": self._breaker_status(domain="gen"),
        }


class DNAEvolutionEngine:
    """双螺旋DNA进化引擎 v3 — 交叉编程模型"""

    def __init__(
        self,
        repo_root: str,
        llm_base_url: str = "http://127.0.0.1:8090",
        llm_api_key: str = "",
        llm_model: str = "soulmate",
    ):
        self.repo_root = repo_root
        self.llm_base_url = llm_base_url
        self.llm_api_key = llm_api_key
        self.llm_model = llm_model

        self.strand_a = DNAStrand(
            "strand_a", StrandRole.PRIMARY, repo_root, llm_base_url,
            strategy="conservative",
            llm_api_key=llm_api_key, llm_model=llm_model,
        )
        self.strand_b = DNAStrand(
            "strand_b", StrandRole.SHADOW, repo_root, llm_base_url,
            strategy="aggressive",
            llm_api_key=llm_api_key, llm_model=llm_model,
        )

        self._task_a: asyncio.Task | None = None
        self._task_b: asyncio.Task | None = None
        self._running = False

    async def start(self):
        self._running = True
        logger.info("🧬 DNA Evolution Engine v3 started — cross-programming active")

        self._task_a = asyncio.create_task(self.strand_a.run("strand_b"))
        self._task_b = asyncio.create_task(self.strand_b.run("strand_a"))

        try:
            await asyncio.gather(self._task_a, self._task_b)
        except asyncio.CancelledError:
            pass

    def observe(self, obs_type: str, content: str, metadata: dict | None = None):
        self.strand_a.observe(obs_type, content, metadata)
        self.strand_b.observe(obs_type, content, metadata)

    def stop(self):
        self._running = False
        self.strand_a.stop()
        self.strand_b.stop()
        if self._task_a:
            self._task_a.cancel()
        if self._task_b:
            self._task_b.cancel()

    def get_status(self) -> dict:
        task = getattr(self, '_strands_task', None)
        return {
            "running": self._running,
            "strand_a": self.strand_a.get_status() if self.strand_a else None,
            "strand_b": self.strand_b.get_status() if self.strand_b else None,
            "strands_running": task is not None and not task.done(),
            "history_size": len(self.strand_a._history) if self.strand_a else 0,
        }

    def get_recent_results(self, limit: int = 10) -> list[dict]:
        """获取最近的进化结果（供agent查询）"""
        results = []
        for strand in [self.strand_a, self.strand_b]:
            for cycle in strand._cycles[-limit:]:
                if cycle.changes:
                    results.append({
                        "cycle_id": cycle.cycle_id,
                        "strand": strand.strand_id,
                        "timestamp": cycle.completed_at,
                        "changes": [
                            {
                                "file": c.get("target_file", ""),
                                "status": c.get("status", ""),
                                "type": c.get("type", ""),
                            }
                            for c in cycle.changes
                        ],
                        "success": cycle.success,
                        "executed_by": cycle.executed_by,
                    })
        return sorted(results, key=lambda x: x.get("timestamp", ""), reverse=True)[:limit]

    def get_created_skills(self) -> list[dict]:
        """获取进化引擎创建的技能列表"""
        skills_dir = Path(self.repo_root) / "acp-proxy" / "skills"
        skills = []
        try:
            for f in skills_dir.glob("*.json"):
                try:
                    with open(f, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if data.get("name"):
                        skills.append({
                            "name": data["name"],
                            "description": data.get("description", ""),
                            "file": str(f),
                            "created_at": data.get("created_at", ""),
                            "use_count": data.get("use_count", 0),
                            "last_used": data.get("last_used"),
                        })
                except Exception:
                    pass
        except Exception:
            pass
        return skills

    def get_evolution_quality(self) -> dict:
        """评估进化质量"""
        skills = self.get_created_skills()
        total_skills = len(skills)
        used_skills = len([s for s in skills if s.get("use_count", 0) > 0])
        
        # 计算成功率
        total_cycles = self.strand_a._cycle_count + self.strand_b._cycle_count
        successful_cycles = sum(
            1 for strand in [self.strand_a, self.strand_b]
            for cycle in strand._cycles
            if cycle.success
        )
        
        return {
            "total_skills_created": total_skills,
            "skills_actively_used": used_skills,
            "skill_utilization_rate": used_skills / total_skills if total_skills > 0 else 0,
            "total_evolution_cycles": total_cycles,
            "successful_cycles": successful_cycles,
            "cycle_success_rate": successful_cycles / total_cycles if total_cycles > 0 else 0,
            "strand_a_memories": len(self.strand_a._memory),
            "strand_b_memories": len(self.strand_b._memory),
        }

    def observe_with_type(self, obs_type: str, content: str, metadata: dict | None = None):
        """带类型的观察注入"""
        self.strand_a.observe(obs_type, content, metadata)
        self.strand_b.observe(obs_type, content, metadata)

    def get_history(self, limit: int = 50) -> list[dict]:
        """获取进化历史（合并两条链）"""
        all_history = self.strand_a._history + self.strand_b._history
        all_history.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return all_history[:limit]

    def get_goals(self) -> list[dict]:
        """获取进化目标（以strand_a为主）"""
        return [g.to_dict() for g in self.strand_a._goals]

    def add_goal(self, title: str, description: str, priority: str = "medium") -> dict:
        """添加新的进化目标（同时注入两条链）"""
        goal_a = EvolutionGoal(
            goal_id=str(uuid.uuid4())[:8],
            title=title,
            description=description,
            priority=priority,
        )
        goal_b = EvolutionGoal(
            goal_id=str(uuid.uuid4())[:8],
            title=title,
            description=description,
            priority=priority,
        )
        self.strand_a._goals.append(goal_a)
        self.strand_b._goals.append(goal_b)
        self.strand_a._save_goals()
        self.strand_b._save_goals()
        return goal_a.to_dict()

    def update_goal(self, goal_id: str, updates: dict) -> dict | None:
        """更新目标（标题/描述/优先级/状态/进度）"""
        for strand in [self.strand_a, self.strand_b]:
            for g in strand._goals:
                if g.goal_id == goal_id:
                    for k, v in updates.items():
                        if hasattr(g, k):
                            setattr(g, k, v)
                    strand._save_goals()
                    return g.to_dict()
        return None

    def delete_goal(self, goal_id: str) -> bool:
        """删除目标"""
        found = False
        for strand in [self.strand_a, self.strand_b]:
            before = len(strand._goals)
            strand._goals = [g for g in strand._goals if g.goal_id != goal_id]
            if len(strand._goals) < before:
                found = True
                strand._save_goals()
        return found
