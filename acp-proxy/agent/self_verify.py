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
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.report_evidence import parse_ledger

# 短于该长度且无枚举结构的消息视为闲聊，不做自验
_MIN_TASK_LEN = 20

# 需求项提取：枚举标记（1. / 1) / （1）/ 1、/ ① / - / *）后接内容；
# 标记前需是行首或分隔标点/空白（防"2026.10"类误切：标记后紧跟数字的不认）
_MARKER_RE = re.compile(
    r"(?:^|[\s：:，,。；;、（(])"
    r"(?:[（(]?\d{1,2}[）)、．.]|[①②③④⑤⑥⑦⑧⑨⑩]|[-*•])(?!\d)\s*"
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
        # ⚡8.2 报告/自检以工具执行证据为前提：账本拆"真实执行 vs 被拦截"
        executed_tools, blocked_tools = parse_ledger(tools)
        tool_bits = []
        for e in executed_tools:
            tool_bits.append(f"{e.name} {e.arguments}")
        for e in blocked_tools:
            tool_bits.append(f"{e.name} {e.arguments}")
        tool_text = " ".join(tool_bits)

        ev_tokens = _tokens(response or "") | _tokens(tool_text)
        has_executed = bool(executed_tools)

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
                # "说了≠做了"护栏（⚡8.2收紧）：动作类需求的 met 必须以工具执行
                # 证据为前提——回复自称"已完成"不是证据（原 has_marker 自证漏洞封死），
                # 工具被拦/未执行 → 最多证据不足，禁报"完成"
                if needs_action and not has_executed:
                    status = "uncertain"
            elif score >= self._UNCERTAIN_MIN:
                status = "uncertain"
            else:
                status = "unmet"

            evidence = (f"命中: " + "、".join(sorted(hit)[:6])) if hit else "无证据重合"
            if executed_tools:
                evidence += "；工具: " + ", ".join(e.name for e in executed_tools[:8])
            if blocked_tools:
                # 工具被拦时报"被拦截+原因"，不计执行证据
                evidence += "；⛔被拦截: " + "；".join(
                    f"{e.name}({(e.result_preview.strip() or e.permission)[:60]})"
                    for e in blocked_tools[:8]
                )
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
