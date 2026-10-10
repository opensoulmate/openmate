"""SoulMate 技能保存器 — 把成功流程写成标准 SKILL.md（路线图 6.1）

产出与 .agents/skills 同构的标准格式：YAML frontmatter（name/description/category/triggers）
+ 正文固定分区（触发条件/步骤/坑/验证）。落盘到**可写技能库**（默认
~/.openmate/skill-library/<slug>/SKILL.md），由 SkillManager 的 saved: 层扫描进运行时
技能面（search_skills 命中即可被系统提示注入复用——保存即进入检索链路）。

与只读标准层的边界（单写入者纪律）：
- .agents/skills / shared-skills 标准层由外部管理（skill_guard 供应链/人工），本模块绝不写；
- 可写库只有 SkillSaver 写（原子替换），SkillManager 对 saved: 条目只读（update/delete 拒绝）——
  更新走 save_flow(overwrite=True) 单一入口，revision 递增可审计。

确定性原则：渲染/落盘/去重全确定性，不走 LLM（内容由调用方/LLM 组织，格式与持久化由本模块保证）。
"""

import logging
import os
import re
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from skill_manager import SKILL_LIBRARY_DIR  # 单一真源（skill_manager不依赖本模块，无环）

logger = logging.getLogger("acp-proxy.skill_saver")

_SLUG_SAFE_RE = re.compile(r"[^\w\u4e00-\u9fff-]+", re.UNICODE)
_SLUG_MAX = 64

# 正文固定分区（路线图 6.1：触发条件/步骤/坑）
SECTION_TRIGGERS = "## 触发条件"
SECTION_STEPS = "## 步骤"
SECTION_PITFALLS = "## 坑"
SECTION_VERIFY = "## 验证"


def _as_list(value) -> list[str]:
    """list[str] 或多行 str → 去空条目 list[str]（确定性，不做 bullet 剥离）"""
    if value is None:
        return []
    if isinstance(value, str):
        items = value.splitlines()
    else:
        # None 条目必须丢弃（str(None)='None' 会蒙混过非空校验）
        items = [str(v) for v in value if v is not None]
    return [s.strip() for s in items if s and s.strip()]


def _one_line(text: str) -> str:
    """frontmatter 是单行 k: v（skill_manager 宽容解析器只取首冒号后整行）：
    值内换行/连续空白压成单空格，防解析错位"""
    return re.sub(r"\s+", " ", (text or "")).strip()


def slugify(name: str) -> str:
    """技能名 → 目录 slug：保留 CJK/word/-，空白转-，空结果回退 name hash（确定性）"""
    base = _SLUG_SAFE_RE.sub("-", (name or "").strip())
    base = re.sub(r"-{2,}", "-", base).strip("-").lower()
    if not base:
        base = "skill-" + hashlib.sha256((name or "").encode("utf-8")).hexdigest()[:8]
    return base[:_SLUG_MAX]


class SkillSaver:
    """成功流程 → 标准 SKILL.md 保存器（可写技能库单一写入源）"""

    def __init__(self, library_dir: str = ""):
        self.library_dir = library_dir or SKILL_LIBRARY_DIR
        os.makedirs(self.library_dir, exist_ok=True)

    # ── 渲染 ──────────────────────────────────────────────────

    @staticmethod
    def render_skill_md(
        name: str,
        description: str,
        triggers: list[str],
        steps: list[str],
        pitfalls: list[str] | None = None,
        verify: str = "",
        category: str = "general",
        revision: int = 1,
        updated_at: str = "",
    ) -> str:
        """确定性渲染标准 SKILL.md（frontmatter + 触发条件/步骤/坑/验证分区）"""
        name_l = _one_line(name)
        desc_l = _one_line(description)
        trig_l = [_one_line(t).lower() for t in _as_list(triggers) if _one_line(t)]
        cat_l = _one_line(category) or "general"
        lines = [
            "---",
            f"name: {name_l}",
            f"description: {desc_l}",
            f"category: {cat_l}",
            f"triggers: {', '.join(trig_l)}",
            f"revision: {revision}",
        ]
        if updated_at:
            lines.append(f"updated_at: {_one_line(updated_at)}")
        lines.append("---")
        lines.append("")
        lines.append(f"# {name_l}")
        lines.append("")
        lines.append(SECTION_TRIGGERS)
        for t in trig_l:
            lines.append(f"- {t}")
        lines.append("")
        lines.append(SECTION_STEPS)
        for i, step in enumerate(_as_list(steps), 1):
            lines.append(f"{i}. {step}")
        pit_list = _as_list(pitfalls)
        if pit_list:
            lines.append("")
            lines.append(SECTION_PITFALLS)
            for p in pit_list:
                lines.append(f"- {p}")
        verify_s = (verify or "").strip()
        if verify_s:
            lines.append("")
            lines.append(SECTION_VERIFY)
            lines.append(verify_s)
        return "\n".join(lines) + "\n"

    # ── 保存（upsert 单一入口） ───────────────────────────────

    def save_flow(
        self,
        name: str,
        description: str,
        triggers,
        steps,
        pitfalls=None,
        verify: str = "",
        category: str = "general",
        overwrite: bool = False,
        now: Optional[datetime] = None,
    ) -> dict:
        """把成功流程写成 SKILL.md。

        校验失败抛 ValueError（显式报因，不静默吞）；同名已存在且 overwrite=False
        时**不写盘**返回 exists 提示（禁静默覆盖既有技能）；overwrite=True 时
        revision 递增原子替换（tmp+os.replace，中途崩溃不留半截文件）。

        Returns: {created, updated, exists, slug, skill_md, revision, name}
        """
        name_s = (name or "").strip()
        desc_s = (description or "").strip()
        trig_list = [t for t in _as_list(triggers)]
        step_list = _as_list(steps)
        if not name_s:
            raise ValueError("name 不能为空")
        if not desc_s:
            raise ValueError("description 不能为空")
        if not trig_list:
            raise ValueError("triggers 至少 1 个触发条件")
        if not step_list:
            raise ValueError("steps 至少 1 个步骤")

        slug = slugify(name_s)
        skill_dir = os.path.join(self.library_dir, slug)
        md_path = os.path.join(skill_dir, "SKILL.md")
        exists = os.path.exists(md_path)

        if exists and not overwrite:
            existing = self.load_skill(slug) or {}
            return {
                "created": False,
                "updated": False,
                "exists": True,
                "slug": slug,
                "skill_md": md_path,
                "revision": int(existing.get("revision", 1) or 1),
                "name": name_s,
            }

        revision = 1
        if exists:
            existing = self.load_skill(slug) or {}
            try:
                revision = int(existing.get("revision", 1) or 1) + 1
            except (TypeError, ValueError):
                revision = 2

        ts = (now or datetime.now(timezone.utc)).isoformat()
        md_text = self.render_skill_md(
            name=name_s,
            description=desc_s,
            triggers=trig_list,
            steps=step_list,
            pitfalls=pitfalls,
            verify=verify,
            category=category,
            revision=revision,
            updated_at=ts,
        )

        os.makedirs(skill_dir, exist_ok=True)
        # 原子写：同目录 tmp + os.replace（读方要么见旧文件要么见新文件，不见半截）
        tmp_path = f"{md_path}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as fp:
            fp.write(md_text)
        os.replace(tmp_path, md_path)
        logger.info("Saved skill SKILL.md: %s (revision=%d)", md_path, revision)
        return {
            "created": not exists,
            "updated": exists,
            "exists": exists,
            "slug": slug,
            "skill_md": md_path,
            "revision": revision,
            "name": name_s,
        }

    # ── 读取 ──────────────────────────────────────────────────

    def load_skill(self, slug: str) -> Optional[dict]:
        """按 slug 读回已保存技能（frontmatter+正文原样，revision 整型化）"""
        md_path = os.path.join(self.library_dir, slug, "SKILL.md")
        if not os.path.exists(md_path):
            return None
        from skill_manager import SkillManager  # 延迟导入防环（skill_manager不依赖本模块）

        meta, body = SkillManager._parse_standard_skill_md(Path(md_path))
        try:
            meta["revision"] = int(meta.get("revision", 1) or 1)
        except (TypeError, ValueError):
            meta["revision"] = 1
        return {
            "slug": slug,
            "skill_md": md_path,
            "name": meta.get("name", slug),
            "description": meta.get("description", ""),
            "category": meta.get("category", ""),
            "triggers": meta.get("triggers", ""),
            "revision": meta["revision"],
            "body": body,
            "meta": meta,
        }

    def list_saved(self) -> list[dict]:
        """列出可写库全部技能（按 slug 排序，确定性）"""
        out: list[dict] = []
        try:
            entries = sorted(Path(self.library_dir).iterdir())
        except OSError:
            return out
        for d in entries:
            try:
                if d.is_dir() and (d / "SKILL.md").exists():
                    skill = self.load_skill(d.name)
                    if skill:
                        out.append(skill)
            except OSError:
                continue
        return out


_saver_singleton: Optional[SkillSaver] = None


def get_skill_saver() -> SkillSaver:
    """进程内单例（缺省可写技能库）"""
    global _saver_singleton
    if _saver_singleton is None:
        _saver_singleton = SkillSaver()
    return _saver_singleton
