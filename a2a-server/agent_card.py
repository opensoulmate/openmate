"""A2A AgentCard 动态发现模块。

自动扫描系统中所有可用的Agent CLI，生成AgentCard。
不硬编码——和ACP层的动态路由保持一致。
"""
from __future__ import annotations

import shutil
import logging
from typing import Optional

from models import AgentCapabilities, AgentCard, AgentSkill

logger = logging.getLogger("a2a.agent_card")

# ---------------------------------------------------------------------------
# 已知Agent的技能描述（只描述能力，不控制路由）
# 路由由 shutil.which() 动态发现
# ---------------------------------------------------------------------------

AGENT_SKILL_PRESETS: dict[str, dict] = {
    "soulmate": {
        "name": "SoulMate",
        "description": "通用对话与任务规划Agent，擅长理解用户意图、分解复杂任务并委派给合适的子Agent",
        "skills": [
            {"id": "chat", "name": "通用对话", "description": "与用户进行自然语言交互，理解需求并提供回应", "tags": ["conversation", "nlp"]},
            {"id": "planning", "name": "任务规划", "description": "将复杂目标拆解为可执行的步骤序列", "tags": ["planning", "decomposition"]},
            {"id": "task-delegation", "name": "任务委派", "description": "根据任务特征将子任务分配给合适的Agent", "tags": ["delegation", "orchestration"]},
        ],
    },
    "hermes": {
        "name": "Hermes",
        "description": "任务拆解与编排Agent，专注于将大任务分解为子任务并协调多个Agent协作完成",
        "skills": [
            {"id": "task-breakdown", "name": "任务拆解", "description": "将复杂任务递归分解为原子操作", "tags": ["breakdown", "planning"]},
            {"id": "sub-agent-delegation", "name": "子Agent委派", "description": "将子任务分配给专业Agent并跟踪执行进度", "tags": ["delegation", "monitoring"]},
            {"id": "orchestration", "name": "编排协调", "description": "管理多Agent间的依赖关系和执行顺序", "tags": ["orchestration", "workflow"]},
        ],
    },
    "opencode": {
        "name": "OpenCode",
        "description": "代码工程Agent，专注于代码重构、patch生成和LSP查询等软件工程任务",
        "skills": [
            {"id": "code-refactor", "name": "代码重构", "description": "对现有代码进行结构优化，提升可读性和可维护性", "tags": ["refactor", "code-quality"]},
            {"id": "patch-generation", "name": "Patch生成", "description": "根据需求描述生成可直接应用的代码补丁", "tags": ["patch", "diff"]},
        ],
    },
    "mimo": {
        "name": "MiMo",
        "description": "小米MiMo代码Agent，专注于代码生成和工程任务",
        "skills": [
            {"id": "code-generation", "name": "代码生成", "description": "根据需求快速生成高质量代码", "tags": ["codegen", "implementation"]},
            {"id": "bug-fix", "name": "Bug修复", "description": "分析错误信息并生成修复代码", "tags": ["debug", "fix"]},
        ],
    },
    "openclaw": {
        "name": "OpenClaw",
        "description": "知识检索与问答Agent，擅长从知识库中检索信息并生成回答",
        "skills": [
            {"id": "knowledge-qa", "name": "知识问答", "description": "从知识库中检索相关信息并回答问题", "tags": ["qa", "retrieval"]},
            {"id": "document-search", "name": "文档搜索", "description": "在文档集合中搜索匹配内容", "tags": ["search", "document"]},
        ],
    },
    "evo": {
        "name": "Evo",
        "description": "自主进化协调者：管理开发车道的进化目标与状态应答（用户经 SoulMate 对话下达目标）",
        "skills": [
            {"id": "set-goal", "name": "进化目标", "description": "将用户下达的进化目标写入目标收件箱，三车道每周期必读", "tags": ["goal", "evolution"]},
            {"id": "query-status", "name": "动态查询", "description": "返回三仓真实动态：最近提交/路线图尾/未完成目标", "tags": ["status", "observability"]},
            {"id": "explain", "name": "决策解释", "description": "按主题检索 commits 与路线图，给出证据化解释", "tags": ["explain", "evidence"]},
        ],
    },
}

# ---------------------------------------------------------------------------
# 动态发现
# ---------------------------------------------------------------------------

def _discover_agents() -> list[str]:
    """扫描系统中所有可用的Agent。

    soulmate 是内置主Agent，始终可用。
    其他Agent通过 shutil.which() 动态发现（和ACP层保持一致）。
    """
    found = ["soulmate", "evo"]  # 内置Agent始终可用（evo=进化协调者，A2A处理端）

    # 检查已知Agent是否在PATH中
    for name in AGENT_SKILL_PRESETS:
        if name == "soulmate":
            continue
        if shutil.which(name):
            found.append(name)

    logger.info(f"Discovered agents: {found}")
    return found


def _build_skill(skill_id: str, name: str, description: str, tags: list[str]) -> AgentSkill:
    """构建AgentSkill实例。"""
    return AgentSkill(id=skill_id, name=name, description=description, tags=tags)


def _build_card(agent_id: str) -> AgentCard:
    """为指定Agent生成AgentCard。

    已知Agent使用预设描述，未知Agent使用默认描述。
    """
    preset = AGENT_SKILL_PRESETS.get(agent_id, {})
    name = preset.get("name", agent_id.title())
    description = preset.get("description", f"{name} Agent")
    skills_data = preset.get("skills", [])

    skills = [_build_skill(s["id"], s["name"], s["description"], s["tags"]) for s in skills_data]

    # 如果没有预设技能，添加一个通用技能
    if not skills:
        skills = [_build_skill("general", "通用能力", f"{name} 的通用任务处理能力", ["general"])]

    return AgentCard(
        name=name,
        description=description,
        version="1.0.0",
        url=f"/a2a/{agent_id}",
        capabilities=AgentCapabilities(
            streaming=True,
            pushNotifications=False,
            stateTransitionHistory=True,
        ),
        skills=skills,
        defaultInputModes=["text/plain", "application/json"],
        defaultOutputModes=["text/plain", "application/json"],
    )


# ---------------------------------------------------------------------------
# 缓存（启动时生成，运行期间不变）
# ---------------------------------------------------------------------------

_CARD_CACHE: dict[str, AgentCard] = {}


def _ensure_cache() -> None:
    """确保缓存已填充。"""
    if not _CARD_CACHE:
        for agent_id in _discover_agents():
            _CARD_CACHE[agent_id] = _build_card(agent_id)


def get_agent_card(name: str) -> Optional[AgentCard]:
    """根据Agent名称获取AgentCard。"""
    _ensure_cache()
    return _CARD_CACHE.get(name.lower())


def list_agent_cards() -> list[AgentCard]:
    """列出所有已注册的AgentCard。"""
    _ensure_cache()
    return list(_CARD_CACHE.values())


def refresh_cache() -> None:
    """强制刷新缓存（用于热更新）。"""
    _CARD_CACHE.clear()
    _ensure_cache()
    logger.info(f"Agent card cache refreshed: {list(_CARD_CACHE.keys())}")
