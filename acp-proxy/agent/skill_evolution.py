"""路线图 6.3 技能进化 —— 用执行结果反哺修正技能内容（确定性，不走 LLM）。

反馈闭环：回合工具账本 + 自验结果 → 只对可写 saved 技能做"坑"沉淀
（失败签名去重后才写入），经 SkillSaver.save_flow(overwrite=True) 单一
写入源 revision 递增原子替换；每次进化落 skill_evolution.db 可审计。

失败判定复用 tool_recovery.classify_result 单一真源；账本拆分复用
report_evidence.parse_ledger（只认真实执行，被拦截不算失败证据）。

边界（诚实）：
- 只进化 saved:<slug> 可写库技能——标准层只读技能单写入者纪律，不动
- 内容变更仅"坑"追加（确定性成文）；步骤/触发词的语义改写需 LLM 或
  用户拍板，不做
- 每回合每技能最多 max_new_per_turn 条新坑、总坑数封顶 max_pitfalls，
  超限显式报因不硬塞；正文解析不出（非 SkillSaver 渲染格式）→ 不改写
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from agent.report_evidence import parse_ledger
from agent.tool_recovery import FailureKind, classify_result, _first_line

logger = logging.getLogger("acp-proxy.skill_evolution")

DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".hermes", "soulmate", "memory", "skill_evolution.db"
)

_SAVED_PREFIX = "saved:"
_PITFALL_MAX_CHARS = 200
_SIG_KEY_CHARS = 24  # 失败签名去重键长度（首行前缀，避开行尾易变细节）


@dataclass
class EvolutionResult:
    """一次进化尝试的结果（无论改没改写，都可观测）"""

    skill_id: str
    slug: str
    action: str  # evolved | no_change | skipped
    reason: str = ""
    added: list[str] = field(default_factory=list)
    revision_before: int = 0
    revision_after: int = 0


def _norm_line(text: str) -> str:
    """失败签名归一：小写+折叠空白，供去重比对（不做激进归一防误去重）"""
    return " ".join(str(text or "").lower().split())


def _pitfall_text(tool: str, kind: FailureKind, line: str, recovered: bool) -> str:
    """确定性坑条目成文（不走 LLM，措辞固定可去重）"""
    line_s = str(line or "").strip()[:_PITFALL_MAX_CHARS]
    if recovered:
        return f"{tool} 可能报错「{line_s}」（{kind.value}）—— 重试/换路径后可成功，遇到先重试或换路径"
    return f"{tool} 可能报错「{line_s}」（{kind.value}）—— 本次未恢复，执行前先核对参数/路径，失败即停不要硬跑"


def parse_sections(body: str) -> dict:
    """解析 SkillSaver.render_skill_md 渲染的正文分区（确定性）。

    返回 {triggers, steps, pitfalls, verify}；steps 解析为空视为
    非渲染格式（调用方跳过改写，禁猜禁编造）。
    """
    out = {"triggers": [], "steps": [], "pitfalls": [], "verify": ""}
    section = None
    verify_lines: list[str] = []
    for raw in str(body or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## 触发条件"):
            section = "triggers"
            continue
        if line.startswith("## 步骤"):
            section = "steps"
            continue
        if line.startswith("## 坑"):
            section = "pitfalls"
            continue
        if line.startswith("## 验证"):
            section = "verify"
            continue
        if section == "triggers":
            s = line.strip()
            if s.startswith("- "):
                out["triggers"].append(s[2:].strip())
        elif section == "steps":
            s = line.strip()
            if s and s[0].isdigit() and ". " in s[:4]:
                out["steps"].append(s.split(". ", 1)[1].strip())
        elif section == "pitfalls":
            s = line.strip()
            if s.startswith("- "):
                out["pitfalls"].append(s[2:].strip())
        elif section == "verify":
            verify_lines.append(line)
    out["verify"] = "\n".join(verify_lines).strip()
    return out


class SkillEvolver:
    """执行结果 → 技能内容的确定性反哺器"""

    def __init__(
        self,
        saver=None,
        skill_manager=None,
        db_path: Optional[str] = None,
        max_new_per_turn: int = 2,
        max_pitfalls: int = 12,
    ):
        self._saver = saver  # lazy：None 时用 get_skill_saver()
        self._skill_manager = skill_manager  # 可选：写回后强刷 saved 层缓存
        self._db_path = db_path or os.environ.get("SOULMATE_SKILL_EVOLUTION_DB") or DEFAULT_DB_PATH
        self.max_new_per_turn = int(max_new_per_turn)
        self.max_pitfalls = int(max_pitfalls)

    # ── 持久化 ─────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS evolutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                skill_id TEXT NOT NULL,
                slug TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                added TEXT NOT NULL DEFAULT '[]',
                revision_before INTEGER NOT NULL DEFAULT 0,
                revision_after INTEGER NOT NULL DEFAULT 0
            )"""
        )
        return conn

    def _record(
        self,
        session_id: str,
        result: EvolutionResult,
    ) -> None:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO evolutions (ts, session_id, skill_id, slug, action, reason,"
                    " added, revision_before, revision_after) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        session_id or "",
                        result.skill_id,
                        result.slug,
                        result.action,
                        result.reason,
                        json.dumps(result.added, ensure_ascii=False),
                        result.revision_before,
                        result.revision_after,
                    ),
                )
        except Exception as e:  # 旁路纪律：审计写失败不冒泡不阻塞主链路
            logger.debug(f"[skill-evolution] audit write failed: {e}")

    def recent(self, limit: int = 20) -> list[dict]:
        """最近进化记录（审计可见）"""
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT ts, session_id, skill_id, slug, action, reason, added,"
                    " revision_before, revision_after FROM evolutions"
                    " ORDER BY id DESC LIMIT ?",
                    (int(limit),),
                ).fetchall()
        except Exception as e:
            logger.debug(f"[skill-evolution] recent query failed: {e}")
            return []
        keys = [
            "ts", "session_id", "skill_id", "slug", "action", "reason",
            "added", "revision_before", "revision_after",
        ]
        out = []
        for row in rows:
            d = dict(zip(keys, row))
            try:
                d["added"] = json.loads(d.get("added") or "[]")
            except (TypeError, ValueError):
                d["added"] = []
            out.append(d)
        return out

    # ── 证据提取 ───────────────────────────────────────────

    def _failure_evidence(self, tool_calls_log) -> list[dict]:
        """从回合账本提取失败证据（只认真实执行，被拦截不算技能缺陷）。

        返回 [{tool, kind, line, recovered}]；recovered=同名工具后续
        出现 OK 结果（重试/换路径成功）。
        """
        executed, _blocked = parse_ledger(tool_calls_log)
        failures: list[dict] = []
        for idx, entry in enumerate(executed):
            kind = classify_result(entry.result_preview)
            if kind in (FailureKind.OK, FailureKind.BLOCKED):
                continue
            recovered = any(
                classify_result(later.result_preview) == FailureKind.OK
                for later in executed[idx + 1:]
                if later.name == entry.name
            )
            failures.append(
                {
                    "tool": entry.name,
                    "kind": kind,
                    "line": _first_line(entry.result_preview),
                    "recovered": recovered,
                }
            )
        return failures

    @staticmethod
    def _slug_of(skill: dict) -> str:
        sid = str((skill or {}).get("id") or "")
        return sid[len(_SAVED_PREFIX):] if sid.startswith(_SAVED_PREFIX) else ""

    # ── 主入口 ─────────────────────────────────────────────

    def evolve_from_turn(
        self,
        skills,
        tool_calls_log=None,
        verify_report=None,
        session_id: str = "",
    ) -> list[EvolutionResult]:
        """回合收尾反哺：对本回合命中过的 saved 技能沉淀新坑。

        - 无失败证据 → no_change（自验结果照样留痕，供审计）
        - 有失败但签名已在坑里 → no_change（去重，不重复写）
        - 正文解析不出/非 saved → skipped（显式报因）
        """
        results: list[EvolutionResult] = []
        skill_list = [s for s in (skills or []) if isinstance(s, dict)]
        failures = self._failure_evidence(tool_calls_log)
        verify_info = None
        if verify_report is not None:
            try:
                verify_info = {
                    "verify_ok": bool(getattr(verify_report, "ok", True)),
                    "items": len(getattr(verify_report, "items", []) or []),
                }
            except Exception:
                verify_info = None

        for skill in skill_list:
            slug = self._slug_of(skill)
            skill_id = str(skill.get("id") or "")
            if not slug:
                # 只读标准层技能：单写入者纪律，绝不改写
                results.append(
                    EvolutionResult(skill_id, "", "skipped", "非saved可写技能（单写入者纪律）")
                )
                continue
            result = self._evolve_one(slug, skill_id, failures)
            if verify_info and result.action == "no_change":
                result.reason = (result.reason + f" verify:{verify_info}").strip()
            self._record(session_id, result)
            results.append(result)
        if results:
            evolved = [r for r in results if r.action == "evolved"]
            logger.info(
                f"[skill-evolution] skills={len(results)} evolved={len(evolved)} "
                f"failures={len(failures)}"
            )
        return results

    def _evolve_one(self, slug: str, skill_id: str, failures: list[dict]) -> EvolutionResult:
        if not failures:
            return EvolutionResult(skill_id, slug, "no_change", "本回合无失败证据")
        saver = self._saver
        if saver is None:
            from agent.skill_saver import get_skill_saver

            saver = get_skill_saver()
            self._saver = saver
        existing = saver.load_skill(slug)
        if not existing:
            return EvolutionResult(skill_id, slug, "skipped", "技能文件不存在")
        sections = parse_sections(existing.get("body") or "")
        if not sections["steps"]:
            return EvolutionResult(
                skill_id, slug, "skipped", "正文非SkillSaver渲染格式（步骤解析为空），不改写"
            )
        rev_before = int(existing.get("revision", 1) or 1)
        cur_pitfalls: list[str] = list(sections["pitfalls"])
        cur_text = _norm_line(" ".join(cur_pitfalls))

        added: list[str] = []
        for f in failures:
            if len(added) >= self.max_new_per_turn:
                break
            if len(cur_pitfalls) + len(added) >= self.max_pitfalls:
                break
            sig_key = _norm_line(f["line"])[:_SIG_KEY_CHARS]
            if not sig_key:
                continue
            if sig_key in cur_text or sig_key in _norm_line(" ".join(added)):
                continue  # 去重：同签名坑已存在（含本回合已加的）
            added.append(_pitfall_text(f["tool"], f["kind"], f["line"], f["recovered"]))

        if not added:
            reason = "失败签名均已在坑中（去重）"
            if len(cur_pitfalls) >= self.max_pitfalls:
                reason = f"坑数已达上限{self.max_pitfalls}，未新增"
            return EvolutionResult(skill_id, slug, "no_change", reason, revision_before=rev_before)

        triggers = sections["triggers"] or [
            t.strip() for t in str(existing.get("triggers") or "").split(",") if t.strip()
        ]
        try:
            saved = saver.save_flow(
                name=existing.get("name") or slug,
                description=existing.get("description") or "",
                triggers=triggers,
                steps=sections["steps"],
                pitfalls=cur_pitfalls + added,
                verify=sections["verify"],
                category=existing.get("category") or "general",
                overwrite=True,
            )
        except Exception as e:
            logger.warning(f"[skill-evolution] save failed for {slug}: {e}")
            return EvolutionResult(
                skill_id, slug, "skipped", f"写回失败: {e}", added=added,
                revision_before=rev_before,
            )
        rev_after = int(saved.get("revision", rev_before + 1) or rev_before + 1)
        # 内容变了：强刷 skill_manager saved 层 60s TTL 缓存（保存即命中检索面，
        # 与 skill_save 工具同款 force_refresh 语义）
        try:
            mgr = self._skill_manager
            if mgr is not None and hasattr(mgr, "scan_saved_skills"):
                mgr.scan_saved_skills(force_refresh=True)
        except Exception as e:
            logger.debug(f"[skill-evolution] cache refresh skipped: {e}")
        return EvolutionResult(
            skill_id, slug, "evolved",
            f"新增{len(added)}条坑",
            added=added,
            revision_before=rev_before,
            revision_after=rev_after,
        )


_evolver_singleton: Optional[SkillEvolver] = None


def get_skill_evolver() -> SkillEvolver:
    """进程内单例（缺省库路径）"""
    global _evolver_singleton
    if _evolver_singleton is None:
        _evolver_singleton = SkillEvolver()
    return _evolver_singleton
