# -*- coding: utf-8 -*-
"""doc_blank.py — C2 留白协议（Abstention），SoulMate 能力层纯函数库。

通用专业文档写作纪律（合同/审计/申报/可研/标书全场景——编数字=职业事故）：
- **缺资料不编造**：证据不足登记留白槽位（slot），禁行业惯例/模型记忆补全；
- **承诺类数字**（工期/报价/质保/人数）只出自《全局事实锁》或用户确认清单，
  拍板前不写正文；
- **正文禁【待补】占位文字**——正规落点是 Word 批注（锚定原文处）+ 文末
  待补清单（人工补充清单）；
- 交付前确定性自查：扫描占位残留（TBD/【待补】/TODO…）与无据格式。

工具化出口（SoulMate 可调，见 writing_tools.doc_blank）：
register / list / scan / attach_comments / protocol。

纯函数 + 受控 I/O（批注落盘）；独立可测（tests/test_doc_blank.py）。
依赖 python-docx ≥1.2（Document.add_comment 评论 API）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "BlankSlot",
    "BlankRegistry",
    "scan_placeholders",
    "attach_comments",
    "protocol_prompt",
]

# 占位残留扫描：只认【待补】式括注与拉丁占位符——裸词"待补"（"有待补充说明"
# 类正常行文）不误伤，正文占位必须带括注形态
_PLACEHOLDER_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    ("方括号占位", re.compile(r"【\s*(待补|待补充|待定|占位|待填|待完善|补充)[^】]*】")),
    ("圆括号占位", re.compile(r"[（(]\s*(待补|待补充|待定|占位|待填|\?\?+)\s*[)）]")),
    ("拉丁占位符", re.compile(r"\b(TBD|TBA|TODO|FIXME|XXX|PLACEHOLDER)\b")),
    ("问号占位", re.compile(r"[？?]{2,}")),
]

_COMMENT_AUTHOR_DEFAULT = "SoulMate留白"
_COMMENT_INITIALS_DEFAULT = "SL"


@dataclass
class BlankSlot:
    """留白槽位：缺什么、在哪、为什么缺、补了没。"""
    slot_id: str
    description: str                       # 缺什么（"ISO9001证书编号"）
    chapter: str = ""                      # 所在章节/位置描述
    reason: str = ""                       # 缺因（"用户未提供，禁编造"）
    status: str = "pending"                # pending | filled
    filled_value: str = ""

    def as_row(self) -> list[str]:
        return [self.slot_id, self.description, self.chapter, self.reason,
                "待补" if self.status == "pending" else f"已补：{self.filled_value}"]


class BlankRegistry:
    """留白槽位登记簿（会话级；工具面 per-session 持有）。"""

    def __init__(self):
        self._slots: dict[str, BlankSlot] = {}
        self._seq = 0

    def register(self, description: str, chapter: str = "", reason: str = "",
                 slot_id: str = "") -> BlankSlot:
        if not str(description or "").strip():
            raise ValueError("description 不能为空（缺什么要说清）")
        if slot_id:
            sid = str(slot_id).strip()
            if sid in self._slots:
                raise ValueError(f"slot_id 已存在: {sid}")
        else:
            self._seq += 1
            sid = f"SLOT-{self._seq:03d}"
        slot = BlankSlot(slot_id=sid, description=str(description).strip(),
                         chapter=str(chapter or ""), reason=str(reason or "") or "资料缺，禁编造")
        self._slots[sid] = slot
        return slot

    def resolve(self, slot_id: str, filled_value: str) -> BlankSlot:
        slot = self._slots.get(str(slot_id).strip())
        if slot is None:
            raise ValueError(f"未登记的 slot_id: {slot_id}")
        if not str(filled_value or "").strip():
            raise ValueError("filled_value 不能为空")
        slot.status = "filled"
        slot.filled_value = str(filled_value).strip()
        return slot

    def slots(self) -> list[BlankSlot]:
        return list(self._slots.values())

    def pending(self) -> list[BlankSlot]:
        return [s for s in self._slots.values() if s.status == "pending"]

    def pending_markdown(self) -> str:
        """待补清单（人工补充清单——文末落点/交付附页）。"""
        pend = self.pending()
        if not pend:
            return "✅ 待补清单为空（无留白槽位）"
        lines = ["## 待人工补充清单（正文未编造，缺口如下）",
                 "",
                 "| 槽位 | 缺什么 | 位置 | 缺因 | 状态 |",
                 "|---|---|---|---|---|"]
        for s in pend:
            lines.append("| " + " | ".join(s.as_row()) + " |")
        lines.append("")
        lines.append(f"合计 {len(pend)} 项待补；补齐后请逐项 resolve 并重新出稿。")
        return "\n".join(lines)


def scan_placeholders(text: str) -> list[dict]:
    """确定性占位残留扫描（交付前自查）。返回 [{kind, matched, line, col, excerpt}]。"""
    if not isinstance(text, str):
        raise ValueError("text 必须是字符串")
    out: list[dict] = []
    for kind, pat in _PLACEHOLDER_PATTERNS:
        for m in pat.finditer(text):
            line = text[:m.start()].count("\n") + 1
            col = m.start() - (text.rfind("\n", 0, m.start()) + 1) + 1
            lo = max(0, m.start() - 15)
            hi = min(len(text), m.end() + 15)
            out.append({
                "kind": kind,
                "matched": m.group(0),
                "line": line,
                "col": col,
                "excerpt": text[lo:hi].replace("\n", "⏎"),
            })
    out.sort(key=lambda v: (v["line"], v["col"]))
    return out


def attach_comments(docx_path: str, comments: list,
                    author: str = _COMMENT_AUTHOR_DEFAULT,
                    initials: str = _COMMENT_INITIALS_DEFAULT) -> dict:
    """把待补批注挂进 Word 文档（锚定原文处——正文禁占位文字的正规落点）。

    comments: [{"anchor": "锚定原文片段", "text": "批注内容"}]
              或 {"paragraph_index": N, "text": ...}（anchor 缺失时按段落序号）
    锚定失败不静默：逐条回报 attached/failed 与原因，文件只在有成功批注时保存。

    返回 {"attached": [...], "failed": [...], "saved": bool, "path": str}
    """
    try:
        from docx import Document
    except ImportError as e:
        raise RuntimeError(f"python-docx 不可用: {e}") from e
    if not isinstance(comments, list) or not comments:
        raise ValueError("comments 需要非空列表 [{anchor?, paragraph_index?, text}]")
    doc = Document(str(docx_path))
    paras = doc.paragraphs
    attached: list[dict] = []
    failed: list[dict] = []
    for i, c in enumerate(comments):
        if not isinstance(c, dict) or not str(c.get("text", "") or "").strip():
            failed.append({"index": i, "error": "缺 text"})
            continue
        target = None
        where = ""
        anchor = str(c.get("anchor", "") or "")
        if anchor:
            for p in paras:
                if anchor in p.text and p.runs:
                    target = p
                    where = f"锚点「{anchor[:20]}」"
                    break
            if target is None:
                failed.append({"index": i, "error": f"锚点未命中: {anchor[:30]!r}"})
                continue
        elif c.get("paragraph_index") is not None:
            try:
                idx = int(str(c.get("paragraph_index", "")))
                target = paras[idx]
            except (ValueError, IndexError):
                failed.append({"index": i, "error": f"paragraph_index 非法: {c.get('paragraph_index')!r}"})
                continue
            if not target.runs:
                failed.append({"index": i, "error": f"段落 {idx} 无文本 run，无法锚定"})
                continue
            where = f"段落 {idx}"
        else:
            failed.append({"index": i, "error": "缺 anchor / paragraph_index"})
            continue
        doc.add_comment(target.runs, text=str(c["text"]),
                        author=author, initials=initials)
        attached.append({"index": i, "where": where, "text": str(c["text"])[:50]})
    if attached:
        doc.save(str(docx_path))
    return {"attached": attached, "failed": failed,
            "saved": bool(attached), "path": str(docx_path)}


def protocol_prompt(registry: BlankRegistry | None = None) -> str:
    """留白协议写作纪律块（注入写作 System Prompt）。"""
    lines = [
        "【留白协议】专业文档写作纪律（缺资料=登记，不=编造）：",
        "1. 证据不足的槽位禁止用行业惯例/模型记忆/合理猜测补全——登记留白槽位"
        "（description+位置+缺因），正文用承接句自然过渡；",
        "2. 承诺类数字（工期/报价/质保/人数/响应时效/资质编号）只准出自《全局事实锁》"
        "或用户明确提供的清单，拍板前不写进正文；",
        "3. 正文禁止出现【待补】/TBD/？？ 等占位文字——缺口的正规落点是 Word 批注"
        "（锚定原文）+ 文末《待人工补充清单》；",
        "4. 交付前必须过占位残留扫描（scan_placeholders），残留即回改；",
        "5. 引用资料只写「未发现/已查范围」口径，禁扩写未查证结论。",
    ]
    if registry is not None and registry.pending():
        lines.append("")
        lines.append(f"当前待补槽位 {len(registry.pending())} 项（未补齐前相关正文保持开放表述）：")
        for s in registry.pending():
            lines.append(f"- {s.slot_id}｜{s.description}｜{s.chapter or '位置未定'}｜{s.reason}")
    return "\n".join(lines)
