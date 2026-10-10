"""SoulMate 技能管理器 — 存储、CRUD、搜索、自动学习

技能以 JSON 文件形式存储在 skills/ 目录下。
支持按 triggers 和 description 模糊搜索，自动记录使用次数。
"""

import json
import os
import re
import time
import uuid
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("acp-proxy.skill_manager")

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

# saved: 可写技能库目录（路线图6.1 技能保存）——agent/skill_saver.py 唯一写入源，
# 本模块只读扫描（update/delete 拒绝 saved: 条目，单写入者纪律）
SKILL_LIBRARY_DIR = os.path.join(str(Path.home()), ".openmate", "skill-library")

# .agents/skills 跨agent技能目录标准（五方定案：goose/ChatDev2.0/FastGPT/OpenHands/Warp，
# SUMMARY.md行业信号#3"无可争议事实标准"，路线图第三阶段第10项）。
# 运行时消费层：opensoul API列表层此前已对齐，但真实聊天路径
# （soulmate_agent._skill_manager.search_skills→system prompt技能注入）只读本地JSON技能
# 目录，标准技能"API列表可见、运行时不可见"——本轮把标准层接进SkillManager扫描/搜索链路，
# 真实消息路径的技能匹配自动覆盖标准技能（消费方零改动，形状兼容）。
STANDARD_SKILL_DIRS: list[tuple[str, str]] = [
    ("agents-global", os.path.join(str(Path.home()), ".agents", "skills")),
    ("agents-openmate", os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", ".agents", "skills"))),
    ("agents-opensoul", os.path.join(str(Path.home()), "opensoul", ".agents", "skills")),
    ("shared", os.path.join(str(Path.home()), ".openmate", "shared-skills")),
]
# 注入preview上限（AIHawk SHOWN/SENT显式标记原则：截断必须可见）：
# SKILL.md正文截断到该长度，完整内容agent经read_file读skill_md路径（hermes索引+按需读全文同款）
STANDARD_SKILL_CONTENT_LIMIT = 4000
# 标准层扫描缓存TTL（聊天路径每条消息都search_skills，避免反复读盘）
_STDLAYER_CACHE_TTL = 60.0
_STD_TRIGGER_MAX = 40

# ── CJK-aware触发词/检索策略 ─────────────────────────────────
# 既有len>2(len>4)门槛按英文词标定，系统性排除中文2字词（邮件/发票/表格…）——
# 中文2字即完整词。通用动词/虚词进触发词会造成注入误报，做最小停用表。
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CJK_GENERIC_TRIGGERS = frozenset({
    "使用", "操作", "进行", "可以", "需要", "相关", "通过", "支持", "提供",
    "实现", "功能", "方式", "帮助", "自动", "处理", "分析", "生成", "创建",
    "查询", "管理", "系统", "服务", "平台", "工具", "数据", "信息", "内容",
})


def is_injectable_trigger(trigger: str) -> bool:
    """触发词是否可用于技能注入判定（soulmate_agent技能gate共用策略）

    英文：len>2（原行为不变）；中文：len==2的词是完整词，通用词除外。
    """
    t = (trigger or "").lower()
    if len(t) > 2:
        return True
    return len(t) == 2 and bool(_CJK_RE.search(t)) and t not in _CJK_GENERIC_TRIGGERS


def _cjk_aware_len(token: str, en_min: int) -> bool:
    """检索打分的长度门槛：英文按en_min，中文2字即有效"""
    t = token.lower()
    return len(t) > en_min or (len(t) >= 2 and bool(_CJK_RE.search(t)))


class SkillManager:
    """文件系统级技能管理器"""

    def __init__(
        self,
        skills_dir: str = SKILLS_DIR,
        standard_dirs: list[tuple[str, str]] | None = None,
        saved_dir: str | None = None,
    ):
        self.skills_dir = skills_dir
        os.makedirs(self.skills_dir, exist_ok=True)
        # .agents/skills标准层目录（测试可注入隔离目录，None=生产目录）
        self.standard_dirs = STANDARD_SKILL_DIRS if standard_dirs is None else standard_dirs
        # saved: 可写技能库（agent/skill_saver.py 唯一写入源，本类只读扫描）——
        # 路线图6.1 技能保存：成功流程写成SKILL.md后经此层进运行时技能面（search命中）
        self.saved_dir = saved_dir if saved_dir is not None else SKILL_LIBRARY_DIR
        self._std_cache: list[dict] | None = None
        self._std_cache_ts: float = 0.0
        self._saved_cache: list[dict] | None = None
        self._saved_cache_ts: float = 0.0

    # ── .agents/skills 标准层（只读） ──────────────────────────

    @staticmethod
    def _parse_standard_skill_md(md_path: Path) -> tuple[dict, str]:
        """解析SKILL.md YAML frontmatter + 正文（对齐opensoul seed_standard_skills同款宽容解析）"""
        text = md_path.read_text(encoding="utf-8", errors="replace")
        meta: dict = {}
        body = text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                body = parts[2]
                lines = parts[1].splitlines()
                i = 0
                while i < len(lines):
                    line = lines[i]
                    if ":" in line and not line.startswith((" ", "\t")):
                        k, _, v = line.partition(":")
                        v = v.strip()
                        if v in ("|", ">"):  # 多行YAML block scalar
                            block = []
                            i += 1
                            while i < len(lines) and lines[i].startswith((" ", "\t")):
                                block.append(lines[i].strip())
                                i += 1
                            meta[k.strip()] = " ".join(block)
                            continue
                        meta[k.strip()] = v.strip("\"'")
                    i += 1
        return meta, body.strip()

    @staticmethod
    def _extract_standard_triggers(name: str, description: str, category: str) -> list[str]:
        """从name/description/category派生搜索触发词

        hermes/goose式SKILL.md通常无triggers字段，描述派生是标准层触发词来源。
        与既有注入gate兼容：soulmate_agent:2061只注入triggers中len>2且出现在user_text里的
        条目，故中文触发词生成2/3字滑窗（2字词参与search_skills打分，3字词可通过注入gate）、
        英文取>=3字符词。
        """
        triggers: list[str] = []

        def _add(t: str):
            t = t.strip().lower()
            if t and t not in triggers:
                triggers.append(t)

        for part in re.split(r"[-_/.\s]+", name):
            if len(part) >= 2:
                _add(part)
        _add(name)
        if category and len(category) >= 2:
            _add(category)
        src = description or ""
        for en in re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,}", src):
            _add(en)
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", src):
            if len(run) <= 4:
                _add(run)
            for n in (2, 3):
                for i in range(0, len(run) - n + 1):
                    _add(run[i:i + n])
            if len(triggers) >= _STD_TRIGGER_MAX:
                break
        return triggers[:_STD_TRIGGER_MAX]

    def _parse_standard_skill(self, label: str, skill_dir: Path) -> dict | None:
        """单个标准技能目录→运行时技能dict；校验不过返回None（fail-closed，失败可见于日志）"""
        md = skill_dir / "SKILL.md"
        try:
            meta, body = self._parse_standard_skill_md(md)
        except Exception as e:
            logger.warning("标准技能SKILL.md解析失败 %s: %s", md, e)
            return None
        name = meta.get("name") or skill_dir.name
        description = meta.get("description", "")
        if not name or not description:
            # agno typed-error语义：缺必填字段（name/description对齐OpenHands规范）不进运行时技能面
            logger.warning("标准技能校验失败（缺name/description）: %s", md)
            return None
        category = meta.get("category", "")
        fm_triggers = meta.get("triggers", "")
        if isinstance(fm_triggers, str):
            fm_list = [t.strip().lower() for t in fm_triggers.strip("[]").split(",") if t.strip()]
        else:
            fm_list = [str(t).strip().lower() for t in fm_triggers]
        derived = self._extract_standard_triggers(name, description, category)
        seen: set = set()
        triggers: list[str] = []
        for t in derived + fm_list:
            if t and t not in seen:
                seen.add(t)
                triggers.append(t)
        header = f"[标准技能·{label}] SKILL.md路径: {md}\n\n"
        if len(body) > STANDARD_SKILL_CONTENT_LIMIT:
            content = (
                header
                + body[:STANDARD_SKILL_CONTENT_LIMIT]
                + f"\n\n[TRUNCATED: SKILL.md正文{len(body)}字符，preview截断至"
                + f"{STANDARD_SKILL_CONTENT_LIMIT}字符，完整内容用read_file读取上述路径]"
            )
        else:
            content = header + body
        try:
            mtime = datetime.fromtimestamp(md.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            mtime = None
        return {
            "id": f"std:{label}:{skill_dir.name}",
            "name": name,
            "description": description[:200],
            "triggers": triggers,
            "content": content,
            "code_template": "",
            "tags": ["standard", label] + ([category] if category else []),
            "source": label,
            "standard": "agents",
            "path": str(skill_dir),
            "skill_md": str(md),
            "created_at": mtime,
            "last_used": None,
            "use_count": 0,
            "read_only": True,
        }

    def scan_standard_skills(self, force_refresh: bool = False) -> list[dict]:
        """扫描.agents/skills标准层+shared-skills供给链层（只读）

        扫描规则：顶层目录含SKILL.md=技能；否则按category容器下探一层
        （shared-skills/hermes同构：category/skill/SKILL.md）。
        .staging-*/.backup-*供应链临时目录跳过（skill_guard同款）。
        带60s TTL缓存：聊天路径每条消息都search_skills，避免反复读盘。
        """
        now = time.monotonic()
        if (
            not force_refresh
            and self._std_cache is not None
            and (now - self._std_cache_ts) < _STDLAYER_CACHE_TTL
        ):
            return [dict(s) for s in self._std_cache]
        skills: list[dict] = []
        for label, base in self.standard_dirs:
            base_path = Path(base)
            try:
                # pathlib exists()对无权限路径会raise PermissionError（测试实证），fail-safe跳过
                if not base_path.exists():
                    continue
                entries = sorted(base_path.iterdir())
            except OSError as e:
                logger.warning("标准技能目录不可读 %s: %s", base, e)
                continue
            candidates: list[Path] = []
            for d in entries:
                try:
                    if not d.is_dir() or d.name.startswith("."):
                        continue
                    if (d / "SKILL.md").exists():
                        candidates.append(d)
                    else:
                        for child in sorted(d.iterdir()):
                            if (
                                child.is_dir()
                                and not child.name.startswith(".")
                                and (child / "SKILL.md").exists()
                            ):
                                candidates.append(child)
                except OSError:
                    continue
            for skill_dir in candidates:
                skill = self._parse_standard_skill(label, skill_dir)
                if skill:
                    skills.append(skill)
        self._std_cache = [dict(s) for s in skills]
        self._std_cache_ts = now
        return skills

    @staticmethod
    def _is_std_id(skill_id: str) -> bool:
        return skill_id.startswith("std:")

    # ── saved: 可写技能库（只读扫描；唯一写入源=agent/skill_saver.py） ──

    @staticmethod
    def _is_saved_id(skill_id: str) -> bool:
        return skill_id.startswith("saved:")

    def scan_saved_skills(self, force_refresh: bool = False) -> list[dict]:
        """扫描 saved: 可写技能库（skill_saver 保存的 SKILL.md）

        复用标准层解析（frontmatter+触发词派生+正文截断），但语义不同：
        read_only=False（可经 skill_save 覆盖更新）、id=saved:<slug>、revision 透传。
        同款 60s TTL 缓存（聊天路径每条消息都 search_skills）。
        """
        now = time.monotonic()
        if (
            not force_refresh
            and self._saved_cache is not None
            and (now - self._saved_cache_ts) < _STDLAYER_CACHE_TTL
        ):
            return [dict(s) for s in self._saved_cache]
        skills: list[dict] = []
        base_path = Path(self.saved_dir)
        try:
            if base_path.exists():
                for d in sorted(base_path.iterdir()):
                    try:
                        if not d.is_dir() or d.name.startswith(".") or not (d / "SKILL.md").exists():
                            continue
                    except OSError:
                        continue
                    skill = self._parse_standard_skill("saved", d)
                    if not skill:
                        continue
                    skill["id"] = f"saved:{d.name}"
                    skill["read_only"] = False
                    skill["tags"] = ["saved"] + [
                        t for t in skill.get("tags", []) if t not in ("standard", "saved")
                    ]
                    skill["content"] = skill["content"].replace(
                        "[标准技能·saved]", "[已保存技能]", 1)
                    meta, _body = self._parse_standard_skill_md(d / "SKILL.md")
                    try:
                        skill["revision"] = int(meta.get("revision", 1) or 1)
                    except (TypeError, ValueError):
                        skill["revision"] = 1
                    skills.append(skill)
        except OSError as e:
            logger.warning("saved技能库不可读 %s: %s", self.saved_dir, e)
        self._saved_cache = [dict(s) for s in skills]
        self._saved_cache_ts = now
        return skills

    # ── CRUD ──────────────────────────────────────────────────

    def list_skills(self) -> list[dict]:
        """列出所有技能：.agents/skills标准层+shared层（只读）+本地JSON技能，按 use_count 降序

        标准层与本地JSON层、以及标准层跨label（agents-global vs shared同名）全部去重：
        先出现者胜出（STANDARD_SKILL_DIRS顺序：agents-global优先于shared）。
        """
        skills: list[dict] = []
        seen: set = set()
        for s in self.scan_standard_skills():
            if s["name"] in seen:
                continue
            seen.add(s["name"])
            skills.append(s)
        # saved: 可写库（skill_saver 保存的SKILL.md）进运行时技能面——保存即被检索命中
        for s in self.scan_saved_skills():
            if s["name"] in seen:
                continue
            seen.add(s["name"])
            skills.append(s)
        for f in os.listdir(self.skills_dir):
            if f.endswith(".json"):
                try:
                    with open(os.path.join(self.skills_dir, f), encoding="utf-8") as fp:
                        s = json.load(fp)
                except Exception as e:
                    logger.warning("Failed to load skill %s: %s", f, e)
                    continue
                if s.get("name") in seen:
                    continue
                seen.add(s.get("name"))
                skills.append(s)
        skills.sort(key=lambda s: s.get("use_count", 0), reverse=True)
        return skills

    def create_skill(
        self,
        name: str,
        description: str,
        triggers: list[str],
        content: str,
        code_template: str = "",
        tags: list[str] | None = None,
    ) -> dict:
        """创建新技能并持久化到 JSON 文件"""
        now = datetime.now(timezone.utc).isoformat()
        skill = {
            "id": str(uuid.uuid4()),
            "name": name,
            "description": description,
            "triggers": triggers,
            "content": content,
            "code_template": code_template,
            "tags": tags or [],
            "created_at": now,
            "last_used": None,
            "use_count": 0,
        }
        self._save(skill)
        logger.info("Created skill: %s (%s)", name, skill["id"])
        return skill

    def get_skill(self, skill_id: str) -> Optional[dict]:
        """按 ID 获取技能（std: 前缀=只读标准层条目；saved: 前缀=可写库条目）"""
        if self._is_std_id(skill_id):
            for s in self.scan_standard_skills():
                if s["id"] == skill_id:
                    return dict(s)
            return None
        if self._is_saved_id(skill_id):
            for s in self.scan_saved_skills():
                if s["id"] == skill_id:
                    return dict(s)
            return None
        filepath = os.path.join(self.skills_dir, f"{skill_id}.json")
        if os.path.exists(filepath):
            with open(filepath, encoding="utf-8") as fp:
                return json.load(fp)
        return None

    def update_skill(self, skill_id: str, **kwargs) -> Optional[dict]:
        """更新技能字段（传入的 kwargs 会覆盖原值）"""
        if self._is_std_id(skill_id):
            # Letta READ_ONLY保护区 + deepagents"调用时拒绝"：.agents/skills由外部管理
            # （skill_guard供应链管线/人工），agent CRUD路径绝不写标准目录
            logger.warning("拒绝更新只读标准技能: %s", skill_id)
            return None
        if self._is_saved_id(skill_id):
            # 单写入者纪律：saved库唯一写入源=skill_saver.save_flow(overwrite=True)
            logger.warning("saved技能请经 skill_save 覆盖保存（skill_saver单一写入源）: %s", skill_id)
            return None
        skill = self.get_skill(skill_id)
        if not skill:
            return None
        # 只允许更新已知字段
        allowed = {"name", "description", "triggers", "content", "code_template", "tags", "last_used", "use_count"}
        for k, v in kwargs.items():
            if k in allowed:
                skill[k] = v
        self._save(skill)
        return skill

    def delete_skill(self, skill_id: str) -> bool:
        """删除技能（std: 标准层条目拒绝删除——目录由外部管理）"""
        if self._is_std_id(skill_id):
            logger.warning("拒绝删除只读标准技能: %s", skill_id)
            return False
        if self._is_saved_id(skill_id):
            # 单写入者纪律：saved库不走本类删除（skill_saver单一写入源，防误删既有技能）
            logger.warning("saved技能拒绝经SkillManager删除: %s", skill_id)
            return False
        filepath = os.path.join(self.skills_dir, f"{skill_id}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
            logger.info("Deleted skill: %s", skill_id)
            return True
        return False

    # ── 搜索 ─────────────────────────────────────────────────

    def search_skills(self, query: str, limit: int = 5) -> list[dict]:
        """搜索技能 — 按 triggers 和 description 匹配，返回最相关的技能

        匹配策略：
        1. 精确 trigger 匹配（query 包含 trigger 或 trigger 包含 query）
        2. description 关键词匹配
        3. tags 匹配
        """
        if not query.strip():
            return []

        query_lower = query.lower()
        query_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", query_lower))
        scored: list[tuple[float, dict]] = []

        for skill in self.list_skills():
            score = 0.0

            # trigger 匹配（权重最高）——CJK-aware：中文2字词是完整词，
            # 与注入gate共用is_injectable_trigger策略（通用词不参与子串/词token匹配）
            for trigger in skill.get("triggers", []):
                t_lower = trigger.lower()
                if t_lower == query_lower:
                    score += 15.0
                elif is_injectable_trigger(t_lower) and _cjk_aware_len(t_lower, 4) and (
                    t_lower in query_lower or query_lower in t_lower
                ):
                    score += 10.0
                elif is_injectable_trigger(t_lower) and _cjk_aware_len(t_lower, 4) and t_lower in query_tokens:
                    score += 5.0

            # tags 匹配
            for tag in skill.get("tags", []):
                if tag.lower() in query_tokens:
                    score += 3.0

            # description 匹配（CJK-aware：中文2字query词参与，英文仍要求len>2）
            desc_lower = skill.get("description", "").lower()
            for token in query_tokens:
                if _cjk_aware_len(token, 2) and token in desc_lower:
                    score += 1.0

            if score > 0:
                scored.append((score, skill))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [s for _, s in scored[:limit]]

    # ── 使用记录 ─────────────────────────────────────────────

    def record_usage(self, skill_id: str):
        """记录技能使用 — 更新 last_used 和 use_count

        std: 标准层条目为只读（.agents/skills目录不落agent侧状态文件，
        使用统计留在本地JSON层；soulmate_agent:2596对每次注入技能都会调用此处）。
        """
        if self._is_std_id(skill_id):
            logger.debug("标准技能使用不记账（只读层）: %s", skill_id)
            return
        if self._is_saved_id(skill_id):
            # saved库条目使用统计归本地JSON层无意义——与std同款不记账（不落库不炸）
            logger.debug("saved技能使用不记账: %s", skill_id)
            return
        skill = self.get_skill(skill_id)
        if not skill:
            return
        skill["last_used"] = datetime.now(timezone.utc).isoformat()
        skill["use_count"] = skill.get("use_count", 0) + 1
        self._save(skill)

    # ── 自动学习 ─────────────────────────────────────────────

    async def try_learn_skill(
        self,
        user_text: str,
        assistant_response: str,
        tool_calls: list[dict] | None = None,
    ) -> Optional[dict]:
        """尝试从交互中学习新技能

        学习条件：
        1. 使用了 2 个以上工具调用
        2. 不是已有技能的重复
        3. 任务模式可复用

        Args:
            user_text: 用户输入
            assistant_response: 助手完整回复
            tool_calls: 工具调用记录

        Returns:
            新创建的技能或 None
        """
        tool_calls = tool_calls or []

        # 条件：至少 2 个工具调用才算复杂任务
        if len(tool_calls) < 2:
            return None

        # 提取工具名列表
        tool_names = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                func = tc.get("function", {})
                tool_names.append(func.get("name", ""))

        # 检查是否已有类似技能（避免重复学习）
        existing = self.search_skills(user_text, limit=3)
        for skill in existing:
            if skill.get("use_count", 0) >= 3:
                # 已有高使用率的匹配技能，不重复创建
                return None

        # 从交互中提取模式 — 技能名必须基于实际使用的工具，不能用用户消息
        if not tool_names:
            return None
        # 技能名 = 工具链组合，如 "search_files→read_file"
        skill_name = "→".join(tool_names[:4])
        triggers = self._extract_triggers(user_text)
        content = self._extract_workflow(assistant_response, tool_names)
        code_template = self._extract_code(assistant_response)

        if not skill_name or len(triggers) < 1:
            return None

        # 创建新技能
        new_skill = self.create_skill(
            name=skill_name,
            description=f"从交互中自动学习: {user_text[:80]}",
            triggers=triggers,
            content=content,
            code_template=code_template,
            tags=["auto-learned"],
        )
        logger.info("Auto-learned skill: %s from interaction", skill_name)
        return new_skill

    # ── 学习辅助方法 ─────────────────────────────────────────

    @staticmethod
    def _extract_skill_name(text: str) -> str:
        """从用户输入中提取简洁的技能名称"""
        # 取前 30 个字符作为名称
        clean = text.strip().replace("\n", " ")
        if len(clean) > 30:
            clean = clean[:30].rsplit(" ", 1)[0] + "…"
        return clean

    @staticmethod
    def _extract_triggers(text: str) -> list[str]:
        """从用户输入中提取触发关键词"""
        # 中文分词（简单版）：提取连续中文片段和英文单词
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}", text)
        # 去重、取前 5 个
        seen = set()
        triggers = []
        for t in tokens:
            t_lower = t.lower()
            if t_lower not in seen:
                seen.add(t_lower)
                triggers.append(t)
            if len(triggers) >= 5:
                break
        return triggers

    @staticmethod
    def _extract_workflow(response: str, tool_names: list[str]) -> str:
        """从助手回复中提取工作流步骤"""
        lines = []
        if tool_names:
            lines.append("## 使用的工具")
            for tn in tool_names:
                lines.append(f"- {tn}")
            lines.append("")

        # 提取回复中的编号步骤
        steps = re.findall(r"(?:^|\n)\s*(?:\d+[.、)]\s*)(.+?)(?=\n\d+[.、)]|\n\n|\Z)", response, re.DOTALL)
        if steps:
            lines.append("## 步骤")
            for i, step in enumerate(steps[:8], 1):
                lines.append(f"{i}. {step.strip()[:100]}")

        return "\n".join(lines) if lines else f"## 工作流\n参考工具: {', '.join(tool_names)}"

    @staticmethod
    def _extract_code(response: str) -> str:
        """从助手回复中提取代码块"""
        code_blocks = re.findall(r"```(?:\w+)?\n(.*?)```", response, re.DOTALL)
        if code_blocks:
            # 取最长的代码块
            return max(code_blocks, key=len).strip()[:2000]
        return ""

    # ── 内部方法 ─────────────────────────────────────────────

    def _save(self, skill: dict):
        """保存技能到 JSON 文件"""
        filepath = os.path.join(self.skills_dir, f"{skill['id']}.json")
        with open(filepath, "w", encoding="utf-8") as fp:
            json.dump(skill, fp, ensure_ascii=False, indent=2)
