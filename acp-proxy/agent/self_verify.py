# -*- coding: utf-8 -*-
"""路线图 2.1 · 计划-执行-验证循环：任务完成后对照原始需求逐条自验。

定位：计划(plan)→执行(_run_llm_with_tools)→**验证(本模块)** 闭环的验证环节。
任务收尾时用确定性规则（不走LLM）把原始需求拆成清单，逐条对照真实产出
（回复文本 + 工具调用账本）核对覆盖情况，产出三态结论：

- ``met``       有证据表明该项已完成
- ``uncertain`` 有提及但证据不足（"说了≠做了"——只提不做最多拿证据不足）
- ``unmet``     未覆盖（进入补做清单）

设计约束（与 ExperienceSink/UserPreferenceLearner 同风格）：
- 提取/核对全确定性，不调用LLM——省token、可复现、测试稳定
- 启发式三态诚实呈现，不谎报"全部完成"；证据不足宁可标 uncertain
- 会话收口只在"多需求项 或 有未覆盖项"时推送自检结论，闲聊零打扰

验证明细（v2）：verify 结束后逐项输出 item_text / evidence_snippets /
verdict / reason，汇总含 met/uncertain/unmet/blocking 计数，
blocking>0 时输出 ❌阻塞项 / ⚠️警告项 / ✅通过项 三块明细。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# 短于该长度且无枚举结构的消息视为闲聊，不做自验
_MIN_TASK_LEN = 20

# 需求项提取：枚举标记（1. / 1) / （1）/ 1、/ ① / - / * / 第一· / 一、）后接内容；
# 标记前需是行首或分隔标点/空白（防"2026.10"类误切：标记后紧跟数字的不认）
_MARKER_RE = re.compile(
    r"(?:^|[\s：:，,。；;、（(])"
    r"(?:[（(]?\d{1,2}[）)、．.]|[①②③④⑤⑥⑦⑧⑨⑩]|[-*•])"
    r"(?:[（(]?\d{1,2}[）)、．.]|[①②③④⑤⑥⑦⑧⑨⑩]|[-*•])"
)

# 顺序连接词：首先/其次/然后/最后/另外/还要… 作为拆分点（整词切分，保留全部片段）
_SEQ_SPLIT_RE = re.compile(r"首先|其次|然后|接着|最后|另外|此外|还要|并且|同时")

# 动作类需求（需要工具/实际操作才算完成，空口答应不算）
_ACTION_RE = re.compile(
    r"(做|改|写|建|创建|修复|删除|发送|生成|安装|运行|测试|检查|查|找|实现|"
    r"加|更新|配置|部署|迁移|重构|优化|提交|推送|下载|上传|整理|清理|分析)"
)

# 回复里的完成证据标记
_DONE_MARKERS = (
    "已完成", "完成", "已创建", "已生成", "已修复", "已发送", "已更新",
    "已实现", "已安装", "已部署", "已提交", "通过", "成功", "✅",
)

# 分句兜底：长任务无枚举时按分号/句末拆
_CLAUSE_SPLIT_RE = re.compile(r"[；;。\n]+")

# 中文停用字（不进匹配词元）
_STOP_CHARS = set(
    "的了请帮我你我要把和与等再就都也很吗呢吧啊呀一下进行做搞对给到在是"
    "为及或跟让被从将该这那个些什么怎么然后还有并且如果可以需要"
)

# 证据片段最大长度
_EVIDENCE_SNIPPET_MAX = 200


# ---------------------------------------------------------------------------
# v2 验证明细数据结构
# ---------------------------------------------------------------------------


@dataclass
class RequirementDetail:
    """单条需求的验证明细（供日志/调试输出）。"""

    item_text: str
    evidence_snippets: list[str] = field(default_factory=list)
    verdict: str = "unmet"          # met / uncertain / unmet
    reason: str = ""
    token_overlap: float = 0.0      # token 重叠率（0~1）
    tool_evidence: bool = False     # 是否有工具调用证据

    def clipped_snippets(self) -> list[str]:
        return [
            s[:_EVIDENCE_SNIPPET_MAX] if len(s) > _EVIDENCE_SNIPPET_MAX else s
            for s in self.evidence_snippets
        ]


@dataclass
class VerifyReport:
    """汇总验证报告。"""

    details: list[RequirementDetail] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.details)

    @property
    def met_count(self) -> int:
        return sum(1 for d in self.details if d.verdict == "met")

    @property
    def uncertain_count(self) -> int:
        return sum(1 for d in self.details if d.verdict == "uncertain")

    @property
    def unmet_count(self) -> int:
        return sum(1 for d in self.details if d.verdict == "unmet")

    @property
    def blocking_count(self) -> int:
        """blocking = unmet 数量（未覆盖项是阻塞项）。"""
        return self.unmet_count


def _clip_evidence(text: str) -> str:
    """截取证据片段至 200 字符。"""
    return text[:_EVIDENCE_SNIPPET_MAX] if len(text) > _EVIDENCE_SNIPPET_MAX else text


def _extract_evidence_snippets(
    item_text: str, *corpora: str, max_snippets: int = 5
) -> list[str]:
    """从回复/工具调用文本中抽取与需求项相关的原文片段（确定性，不含LLM）。"""
    item_toks = _tokens(item_text)
    if not item_toks:
        return []
    snippets: list[str] = []
    seen: set[str] = set()
    for corpus in corpora:
        if not corpus:
            continue
        # 按句/分段搜索相关片段
        for seg in re.split(r"[；;。\n]+", corpus):
            seg = seg.strip()
            if len(seg) < 4:
                continue
            seg_toks = _tokens(seg)
            overlap = item_toks & seg_toks
            if not overlap:
                continue
            snippet = _clip_evidence(seg)
            if snippet not in seen:
                seen.add(snippet)
                snippets.append(snippet)
                if len(snippets) >= max_snippets:
                    return snippets
    return snippets


def _format_detail(detail: RequirementDetail) -> str:
    """格式化单条验证明细为多行文本。"""
    lines = [
        f"  item_text : {detail.item_text}",
        f"  verdict   : {detail.verdict}",
        f"  reason    : {detail.reason}",
        f"  overlap   : {detail.token_overlap:.2f}  tool_evidence={detail.tool_evidence}",
    ]
    if detail.evidence_snippets:
        lines.append("  evidence  :")
        for i, sn in enumerate(detail.clipped_snippets(), 1):
            lines.append(f"    [{i}] {sn}")
    else:
        lines.append("  evidence  : (无)")
    return "\n".join(lines)


def log_verify_report(report: VerifyReport) -> None:
    """输出完整验证明细日志（逐项 + 汇总 + 分类明细块）。"""
    if report.total == 0:
        logger.info("[self-verify] 无可验证需求项，跳过自验")
        return

    # 逐项明细
    for idx, d in enumerate(report.details, 1):
        logger.info(
            "[self-verify] #%d/%d\n%s", idx, report.total, _format_detail(d)
        )

    # 汇总
    summary = (
        f"[self-verify] 汇总: 总需求数={report.total}  "
        f"met={report.met_count}  uncertain={report.uncertain_count}  "
        f"unmet={report.unmet_count}  blocking={report.blocking_count}"
    )
    logger.info(summary)

    # 分类明细块
    if report.blocking_count > 0:
        _log_block("❌ 阻塞项", [d for d in report.details if d.verdict == "unmet"])
    if report.uncertain_count > 0:
        _log_block("⚠️ 警告项", [d for d in report.details if d.verdict == "uncertain"])
    if report.met_count > 0:
        _log_block("✅ 通过项", [d for d in report.details if d.verdict == "met"])


def _log_block(title: str, details: list[RequirementDetail]) -> None:
    """输出分类明细块（阻塞/警告/通过）。"""
    logger.info("[self-verify] %s (%d 项)", title, len(details))
    for idx, d in enumerate(details, 1):
        logger.info(
            "[self-verify]   %s #%d  %s | reason: %s",
            title, idx, d.item_text[:80], d.reason,
        )


def _tokens(text: str) -> set[str]:
    """CJK 取 bigram + 拉丁词（≥3字符），过滤停用字——与经验沉淀同风格的确定性词元。"""
    text = (text or "").lower()
    toks: set[str] = set(re.findall(r"[a-z][a-z0-9_\-\.]{2,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        if len(run) == 1:
            if run not in _STOP_CHARS:
                toks.add(run)
        for i in range(len(run) - 1):
            bg = run[i : i + 2]
            if not (bg[0] in _STOP_CHARS and bg[1] in _STOP_CHARS):
                toks.add(bg)
    return toks


def _clean_item(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t\n，,。；;、：:")


def _split_by_markers(text: str, marker_re: re.Pattern) -> list[str]:
    """按枚举/连接词标记切分：取每段标记之后到下一标记之前的内容。"""
    matches = list(marker_re.finditer(text))
    items: list[str] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        seg = _clean_item(text[start:end])
        if len(seg) >= 2:
            items.append(seg)
    return items


def extract_requirements(text: str) -> list[str]:
    """从用户原始请求提取需求清单。空列表 = 闲聊/不可拆，不进入自验。

    提取顺序：①数字/圆圈序号列表 ②顺序连接词拆分 ③分号分句（≥2句才拆）
    ④兜底：长任务整体作为单条需求。
    """
    text = (text or "").strip()
    if not text:
        return []

    items = _split_by_markers(text, _MARKER_RE)
    if not items:
        # 顺序连接词整词切分，保留全部片段（首段也是需求，不能丢）
        parts = [_clean_item(p) for p in _SEQ_SPLIT_RE.split(text)]
        parts = [p for p in parts if len(p) >= 4]
        if len(parts) >= 2:
            items = parts
    if not items:
        clauses = [_clean_item(c) for c in _CLAUSE_SPLIT_RE.split(text)]
        clauses = [c for c in clauses if len(c) >= 4]
        if len(clauses) >= 2:
            items = clauses
    if items:
        # 去重保序
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            if it not in seen:
                seen.add(it)
                out.append(it)
        return out

    # 兜底：够长的单件事整体算一条需求；短消息=闲聊不自验
    if len(text) >= _MIN_TASK_LEN:
        return [text]
    return []


@dataclass
class VerifyItem:
    requirement: str
    status: str  # met / uncertain / unmet
    score: float  # 证据覆盖率 0~1
    evidence: str = ""


@dataclass
class VerifyReport:
    items: list[VerifyItem] = field(default_factory=list)
    coverage: float = 1.0

    @property
    def ok(self) -> bool:
        return all(i.status == "met" for i in self.items)

    @property
    def checked(self) -> int:
        return len(self.items)

    def missing_requirements(self) -> list[str]:
        """未覆盖项（unmet）——补做清单。uncertain 只提醒不补做，防误触发。"""
        return [i.requirement for i in self.items if i.status == "unmet"]

    def uncertain_requirements(self) -> list[str]:
        return [i.requirement for i in self.items if i.status == "uncertain"]


class SelfVerifier:
    """对照原始需求逐条自验（确定性启发式，三态，不谎报）。"""

    _MET_MIN = 0.55
    _UNCERTAIN_MIN = 0.25

    def verify(
        self,
        user_text: str,
        response: str,
        tool_calls_log: list | None = None,
    ) -> VerifyReport:
        reqs = extract_requirements(user_text)
        if not reqs:
            return VerifyReport(items=[], coverage=1.0)

        tools = tool_calls_log or []
        tool_bits = []
        for tc in tools:
            if isinstance(tc, dict):
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                name = str(fn.get("name", ""))
                args = str(fn.get("arguments", ""))
                tool_bits.append(f"{name} {args}")
        tool_text = " ".join(tool_bits)

        ev_tokens = _tokens(response or "") | _tokens(tool_text)
        turn_has_tools = bool(tools)
        has_marker = any(m in (response or "") for m in _DONE_MARKERS)

        items: list[VerifyItem] = []
        for req in reqs:
            rt = _tokens(req)
            if not rt:
                items.append(VerifyItem(req, "met", 1.0, "无实质词，跳过"))
                continue
            hit = rt & ev_tokens
            score = len(hit) / len(rt)
            needs_action = bool(_ACTION_RE.search(req))

            if score >= self._MET_MIN:
                status = "met"
                # "说了≠做了"护栏：动作类需求无工具执行也无完成证据 → 最多证据不足
                if needs_action and not turn_has_tools and not has_marker:
                    status = "uncertain"
            elif score >= self._UNCERTAIN_MIN:
                status = "uncertain"
            else:
                status = "unmet"

            evidence = ("命中: " + "、".join(sorted(hit)[:6])) if hit else "无证据重合"
            if turn_has_tools:
                names = []
                for t in tools:
                    if isinstance(t, dict):
                        raw_fn = t.get("function")
                        fn = raw_fn if isinstance(raw_fn, dict) else t
                        names.append(str(fn.get("name", "")))
                evidence += f"；工具: {', '.join(n for n in names if n)[:80]}"
            items.append(VerifyItem(req, status, round(score, 2), evidence))

        met = sum(1 for i in items if i.status == "met")
        return VerifyReport(items=items, coverage=round(met / len(items), 2))

    def format_report(self, report: VerifyReport) -> str:
        """渲染自检结论（用户可见，可观测）。"""
        if not report.items:
            return ""
        icons = {"met": "✅", "uncertain": "⚠️", "unmet": "❌"}
        labels = {"met": "", "uncertain": "（证据不足）", "unmet": "（未覆盖）"}
        lines = ["🔍 需求自检（对照原始需求逐条核对）："]
        for i, item in enumerate(report.items, 1):
            lines.append(
                f"{icons.get(item.status, '❓')} {i}. {item.requirement}{labels.get(item.status, '')}"
            )
        met = sum(1 for x in report.items if x.status == "met")
        lines.append(f"— 覆盖 {met}/{len(report.items)}")
        return "\n".join(lines)

    def build_remediation_prompt(self, missing: list[str]) -> str:
        """补做提示词：只补未覆盖项，不重复已完成部分（上限1轮由调用方保证）。"""
        lines = [
            "以下需求项在刚才的任务中未完成，请补做（只做这些，不要重复已完成部分）："
        ]
        for i, req in enumerate(missing, 1):
            lines.append(f"{i}. {req}")
        lines.append("完成后逐项简要说明结果。")
        return "\n".join(lines)
