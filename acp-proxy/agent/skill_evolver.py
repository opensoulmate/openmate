"""SoulMate 技能进化器 — 用执行结果反哺修正技能内容（路线图 6.3）

反馈闭环（确定性，不走 LLM）：
1. 回合执行中的工具失败（2.2 tool_recovery 路径）与自验未覆盖项（2.1
   self_verify）→ note_event 记入按会话缓冲（有界 deque）；
2. 回合收尾（"记录技能使用"处）drain 缓冲，按本回合命中过的 saved: 技能
   record_outcome 落反馈库（(slug, signature) 去重——重复失败只累计
   times_seen，不刷屏）；
3. skill_evolve(action=apply) 把待应用反馈确定性合并进 SKILL.md「坑」分区
   （条目去重 + 单次上限 + 24h 冷却防 revision 抖动），revision 递增原子
   替换——更新仍走 SkillSaver.save_flow(overwrite=True) 单一写入入口
   （单写入者纪律）。

边界：
- 只进化可写技能库（~/.openmate/skill-library）的 saved: 技能；
  只读标准层（.agents/skills / shared-skills）绝不写；
- 反馈只做加法（追加坑条目），不改写既有步骤/触发词/描述——修正内容是
  执行证据的确定性转写，不是 LLM 重写（禁静默改语义）；
- 采集/落库/合并全程观测旁路原则：单点异常只计日志，绝不阻断主执行链路
  （反哺失败不影响任务本身）。
"""

import logging
import os
import re
import sqlite3
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from skill_manager import SKILL_LIBRARY_DIR  # 单一真源（skill_manager不依赖本模块，无环）

logger = logging.getLogger("acp-proxy.skill_evolver")

_DEFAULT_DB = os.path.join(str(Path.home()), ".openmate", "skill-feedback.db")

MAX_EVENTS_PER_SESSION = 20   # 每会话缓冲上限（防长会话无界增长）
MAX_APPLIES_PER_DAY = 5       # 单技能 24h 内 apply 上限（防 revision 抖动）
MAX_ITEMS_PER_APPLY = 10      # apply 单次条数硬上限
DEFAULT_ITEMS_PER_APPLY = 3

_WS_RE = re.compile(r"\s+", re.UNICODE)
_NUM_PREFIX_RE = re.compile(r"^\d+\.\s*", re.UNICODE)


def _one_line(text) -> str:
    """压成单行（frontmatter/坑条目都是单行语义）"""
    return _WS_RE.sub(" ", str(text or "")).strip()


def _norm(text) -> str:
    """去重用归一化：单行 + 小写"""
    return _one_line(text).lower()


def _parse_sections(body: str) -> dict:
    """确定性解析 SKILL.md 正文固定分区（触发条件/步骤/坑/验证）。

    只认 SkillSaver.render_skill_md 的固定格式；解析不出的分区回空列表
    （apply 时校验 steps 非空，不会把技能写残）。
    """
    triggers: list[str] = []
    steps: list[str] = []
    pitfalls: list[str] = []
    verify_lines: list[str] = []
    section = None
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            head = line[3:].strip()
            if head.startswith("触发条件"):
                section = "triggers"
            elif head.startswith("步骤"):
                section = "steps"
            elif head.startswith("坑"):
                section = "pitfalls"
            elif head.startswith("验证"):
                section = "verify"
            else:
                section = None
            continue
        if not line:
            continue
        if line.startswith("# "):  # 标题行
            continue
        if section == "triggers" and line.startswith("- "):
            triggers.append(_one_line(line[2:]))
        elif section == "steps":
            steps.append(_one_line(_NUM_PREFIX_RE.sub("", line)))
        elif section == "pitfalls" and line.startswith("- "):
            pitfalls.append(_one_line(line[2:]))
        elif section == "verify":
            verify_lines.append(line)
    return {
        "triggers": triggers,
        "steps": steps,
        "pitfalls": pitfalls,
        "verify": "\n".join(verify_lines).strip(),
    }


def _is_dup(candidate: str, existing: list[str]) -> bool:
    """条目去重：归一化全等，或互为子集（短句被长句涵盖也算重复）"""
    c = _norm(candidate)
    if not c:
        return True
    for e in existing:
        en = _norm(e)
        if not en:
            continue
        if c == en or c in en or en in c:
            return True
    return False


class SkillEvolver:
    """执行结果 → 技能内容 反馈闭环（可写技能库单一进化源）"""

    def __init__(self, db_path: str = "", library_dir: str = ""):
        self.db_path = db_path or os.environ.get("SOULMATE_SKILL_FEEDBACK_DB") or _DEFAULT_DB
        self.library_dir = library_dir or SKILL_LIBRARY_DIR
        self._lock = threading.Lock()
        self._events: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=MAX_EVENTS_PER_SESSION)
        )
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()
        # 延迟导入（skill_saver 依赖 skill_manager，顶层导入无环但保持轻量）
        from agent.skill_saver import SkillSaver
        self._saver = SkillSaver(library_dir=self.library_dir)

    # ── SQLite ────────────────────────────────────────────────

    def _init_db(self):
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS skill_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    slug TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    success INTEGER NOT NULL DEFAULT 1,
                    tool TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    times_seen INTEGER NOT NULL DEFAULT 1,
                    applied INTEGER NOT NULL DEFAULT 0,
                    applied_ts INTEGER
                )
                """
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_skill_feedback "
                "ON skill_feedback(slug, signature)"
            )

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    # ── 采集（回合内事件缓冲） ────────────────────────────────

    def note_event(self, session_id: str, tool: str, kind: str, detail: str):
        """记一条执行事件到会话缓冲（有界 deque；观测旁路，异常只计日志）"""
        try:
            detail_l = _one_line(detail)
            tool_l = _one_line(tool)
            kind_l = _one_line(kind)
            if not detail_l or not tool_l:
                return
            with self._lock:
                self._events[session_id or "_"].append(
                    {"tool": tool_l, "kind": kind_l, "detail": detail_l}
                )
        except Exception as e:  # 采集绝不阻断执行
            logger.debug(f"[skill-evolve] note_event 失败(非致命): {e}")

    def drain_events(self, session_id: str) -> list[dict]:
        """取走并清空该会话的事件缓冲（回合收尾调用；无事件返回 []）"""
        with self._lock:
            dq = self._events.pop(session_id or "_", None)
        return list(dq) if dq else []

    # ── 反馈落库 ──────────────────────────────────────────────

    @staticmethod
    def render_amendment(tool: str, kind: str, detail: str) -> str:
        """执行事件 → 坑条目（确定性模板，两态：自验遗漏 / 工具失败）"""
        d = _one_line(detail)
        if kind == "unmet":
            return f"需求「{d}」曾被遗漏——步骤需显式覆盖该要求"
        return f"{_one_line(tool)} 曾失败（{_one_line(kind)}）：{d}"

    def record_outcome(
        self,
        slug: str,
        success: bool,
        events: list[dict],
        task_text: str = "",
    ) -> dict:
        """把一次 saved 技能参与的回合结果落反馈库。

        (slug, signature) 唯一去重：同签名重复失败只累计 times_seen 刷 last_ts，
        不重复排队修正条目。空事件不落库（成功无事不污染反馈面）。
        """
        slug_l = _one_line(slug)
        if not slug_l:
            raise ValueError("slug 不能为空")
        rows = []
        for ev in events or []:
            tool = _one_line((ev or {}).get("tool", ""))
            kind = _one_line((ev or {}).get("kind", ""))
            detail = _one_line((ev or {}).get("detail", ""))
            if not tool or not detail:
                continue
            signature = f"{tool}|{kind}|{_norm(detail)}"
            rows.append((tool, kind, detail, signature))
        if not rows:
            return {"recorded": 0, "deduped": 0, "slug": slug_l}
        now = int(time.time())
        recorded = 0
        deduped = 0
        with self._conn() as conn:
            for tool, kind, detail, signature in rows:
                # 先查后写（确定性计数；ON CONFLICT 的 rowcount/lastrowid
                # 在 update 分支同样非零，无法区分 insert/update）
                existing = conn.execute(
                    "SELECT id FROM skill_feedback WHERE slug = ? AND signature = ?",
                    (slug_l, signature),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE skill_feedback SET times_seen = times_seen + 1, "
                        "ts = ?, success = ? WHERE id = ?",
                        (now, 1 if success else 0, existing["id"]),
                    )
                    deduped += 1
                else:
                    conn.execute(
                        "INSERT INTO skill_feedback "
                        "(slug, ts, success, tool, kind, detail, signature) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (slug_l, now, 1 if success else 0, tool, kind, detail, signature),
                    )
                    recorded += 1
            conn.commit()
        return {"recorded": recorded, "deduped": deduped, "slug": slug_l}

    # ── 查询 ──────────────────────────────────────────────────

    def pending_rows(self, slug: str) -> list[dict]:
        """待应用反馈行（applied=0，times_seen 降序+id 升序稳定）"""
        slug_l = _one_line(slug)
        if not slug_l:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, tool, kind, detail, times_seen FROM skill_feedback "
                "WHERE slug = ? AND applied = 0 "
                "ORDER BY times_seen DESC, id ASC",
                (slug_l,),
            ).fetchall()
        return [dict(r) for r in rows]

    def pending_amendments(self, slug: str) -> list[str]:
        """待应用修正条目文本（render_amendment 单一真源）"""
        return [
            self.render_amendment(r["tool"], r["kind"], r["detail"])
            for r in self.pending_rows(slug)
        ]

    def stats(self, slug: str = "") -> dict:
        """反馈统计（可观测：每技能反馈数/已应用/待应用/累计命中）"""
        where = "WHERE slug = ?" if _one_line(slug) else ""
        args = (_one_line(slug),) if _one_line(slug) else ()
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT slug, COUNT(*) AS feedback, "
                f"SUM(applied) AS applied, SUM(times_seen) AS times_seen "
                f"FROM skill_feedback {where} GROUP BY slug ORDER BY slug",
                args,
            ).fetchall()
        out = {}
        for r in rows:
            out[r["slug"]] = {
                "feedback": int(r["feedback"] or 0),
                "applied": int(r["applied"] or 0),
                "pending": int(r["feedback"] or 0) - int(r["applied"] or 0),
                "times_seen": int(r["times_seen"] or 0),
            }
        return out

    def resolve_slug(self, slug_or_name: str) -> str:
        """slug 或技能名 → 库内 slug（slugify 兜底；都找不到返回原值由调用方报错）"""
        raw = _one_line(slug_or_name)
        if not raw:
            return ""
        if self._saver.load_skill(raw):
            return raw
        from agent.skill_saver import slugify
        cand = slugify(raw)
        if self._saver.load_skill(cand):
            return cand
        return raw

    # ── 应用（合并进 SKILL.md 坑分区） ────────────────────────

    def apply(self, slug: str, max_items: int = DEFAULT_ITEMS_PER_APPLY) -> dict:
        """把待应用反馈合并进技能「坑」分区（save_flow(overwrite=True) 单一写入口）。

        守卫链（全部显式报因，不静默）：
        - 技能不存在 → ValueError（只进化可写库 saved: 技能）；
        - 无待应用反馈 → {updated: False, reason: "no_pending"}；
        - 24h 内已 apply ≥ MAX_APPLIES_PER_DAY → {updated: False, reason: "cooldown"}
          （防反馈风暴把 revision 抖上去）；
        - 单次条数夹取 1..MAX_ITEMS_PER_APPLY；与既有坑条目互为子集即去重；
        - 解析不出 steps（文件非本模块格式）→ ValueError，禁把技能写残。
        """
        slug_l = _one_line(slug)
        skill = self._saver.load_skill(slug_l) if slug_l else None
        if not skill:
            raise ValueError(f"技能不存在（只进化可写技能库的 saved 技能）: {slug!r}")
        # save_flow 按 name 派生 slug 写盘——name→slug 必须与现有 slug 一致，
        # 否则会把技能写到另一个目录（留孤儿旧文件）
        from agent.skill_saver import slugify as _slugify
        if _slugify(skill.get("name", "")) != slug_l:
            raise ValueError(
                f"技能 name 派生 slug 与目录不一致，拒绝写错目录: "
                f"name={skill.get('name')!r} → {_slugify(skill.get('name', ''))!r} != {slug_l!r}"
            )
        rows = self.pending_rows(slug_l)
        if not rows:
            return {"updated": False, "reason": "no_pending", "slug": slug_l}

        # 24h 冷却：防反馈风暴 revision 抖动
        cutoff = int(time.time()) - 86400
        with self._conn() as conn:
            applied_recent = conn.execute(
                "SELECT COUNT(*) AS n FROM skill_feedback "
                "WHERE slug = ? AND applied = 1 AND applied_ts >= ?",
                (slug_l, cutoff),
            ).fetchone()["n"]
        if int(applied_recent or 0) >= MAX_APPLIES_PER_DAY:
            return {
                "updated": False, "reason": "cooldown", "slug": slug_l,
                "applied_24h": int(applied_recent or 0),
                "limit_24h": MAX_APPLIES_PER_DAY,
            }

        try:
            cap = int(max_items)
        except (TypeError, ValueError):
            cap = DEFAULT_ITEMS_PER_APPLY
        cap = max(1, min(cap, MAX_ITEMS_PER_APPLY))

        sections = _parse_sections(skill.get("body", ""))
        if not sections["steps"] or not sections["triggers"]:
            raise ValueError(f"技能正文解析不出触发条件/步骤分区，拒绝写残: {slug_l}")
        existing = list(sections["pitfalls"])
        added: list[str] = []
        add_ids: list[int] = []
        skipped: list[str] = []
        for row in rows:
            if len(added) >= cap:
                break
            line = self.render_amendment(row["tool"], row["kind"], row["detail"])
            if _is_dup(line, existing + added):
                skipped.append(line)
                add_ids.append(int(row["id"]))  # 重复也算已消化，不再排队
                continue
            added.append(line)
            add_ids.append(int(row["id"]))
        if not added and not skipped:
            return {"updated": False, "reason": "no_pending", "slug": slug_l}
        if not added:
            # 全部与既有条目重复：标记消化但不改文件（禁无意义 revision 递增）
            self._mark_applied(add_ids)
            return {
                "updated": False, "reason": "all_duplicated", "slug": slug_l,
                "skipped": skipped,
            }

        result = self._saver.save_flow(
            name=skill.get("name", slug_l),
            description=skill.get("description", ""),
            triggers=sections["triggers"],
            steps=sections["steps"],
            pitfalls=existing + added,
            verify=sections["verify"],
            category=skill.get("category", "") or "general",
            overwrite=True,
        )
        self._mark_applied(add_ids)
        logger.info(
            "[skill-evolve] %s revision=%s +%d条坑", slug_l, result.get("revision"), len(added)
        )
        return {
            "updated": True, "slug": slug_l,
            "revision": int(result.get("revision", 1) or 1),
            "added": added, "skipped": skipped,
            "skill_md": result.get("skill_md", ""),
        }

    def _mark_applied(self, ids: list[int]):
        if not ids:
            return
        now = int(time.time())
        with self._conn() as conn:
            conn.execute(
                f"UPDATE skill_feedback SET applied = 1, applied_ts = ? "
                f"WHERE id IN ({','.join('?' * len(ids))})",
                [now, *ids],
            )
            conn.commit()


_evolver_singleton: Optional[SkillEvolver] = None


def get_skill_evolver() -> SkillEvolver:
    """进程内单例（缺省反馈库 + 可写技能库）"""
    global _evolver_singleton
    if _evolver_singleton is None:
        _evolver_singleton = SkillEvolver()
    return _evolver_singleton
