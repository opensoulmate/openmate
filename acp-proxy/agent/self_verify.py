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
from agent.subagent_manager import SUBAGENT_PROMPT_MARKER

# 自检报告首行（frame_origin 采集侧帧分类的单一真源——改措辞须同步语义）
REPORT_HEADER = "🔍 需求自检（对照原始需求逐条核对）："

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

# 结构检出（_structure_tokens 用）：markdown 表格 / 列表 / JSON / 代码围栏
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:\-|]+\|\s*$", re.M)
_LIST_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s+\S", re.M)
_JSON_RE = re.compile(r"^[{[][\s\S]*[}\]]$")

# 条件子句（"如果X就Y / 如遇X则Y"类）：是否触发取决于运行时条件，确定性核对
# 无法观测条件是否成立——"未覆盖"≠"没做"，不能进补做清单（⚡8.5c-①实证：
# 补做条件子句只会诱发"补做结果/逐项说明"式逐条复盘元叙事，违反"简短回答"约束）
_CONDITIONAL_RE = re.compile(
    r"如果|假如|假使|要是|一旦|万一|如遇|如需|除非|的话|若(?!干)"
    r"|当[^，。；;]{0,12}时"
    r"|\b[Ii]f\b|\b[Uu]nless\b"
)

# 自否定元指令（"此行仅为…忽略其语义"类）：文本自己声明"不是需求"（⚡8.5c-①
# 周期#43 实证：防缓存标记行被拆成需求项且必然 unmet → 每轮必触发补做 →
# 回答尾部"固定带"补做元叙事）。只认显式自否定措辞，"忽略大小写"类真实需求不受影响。
_SELF_NEGATING_RE = re.compile(
    r"仅为防缓存|忽略其语义|忽略本行|忽略这[一行]|忽略该行|本行可忽略"
    r"|此行(仅[为是]|只是)[^，。；;]{0,8}(标记|占位)"
    r"|[Ii]gnore (this|the) (line|row)"
)
# 系统附件锚点注记（"[附件已保存到: …]"/"[文件/图片已保存到: …]"/"[语音附件已随消息直发：…]"）：
# 消息管道注入的元信息，不是用户需求——周期#102 部署后真实 WS audio e2e 实证：锚点行被
# 拆成需求项必然 unmet → 每条带附件消息必触发补做 + "覆盖 0/N" 噪音横幅（语音消息
# 实测三项全"未覆盖"其中两项是锚点）。纯语音消息剥掉锚点后无正文=闲聊不自验，天然干净。
_ANCHOR_NOTE_RE = re.compile(r"\[[^\[\]]*(?:已保存到|已随消息直发)[^\[\]]*\]")

# 子代理任务脚手架（2026-10-23 周期#666，REJ661 真实缺陷）：delegate worker 回合
# prompt 由 build_subagent_prompt 模板拼装（声明行「【SoulMate子代理任务】…」＋目标行
# 「子任务目标：…」＋背景行＋要求行）。模板行是派发机制不是用户需求——把脚手架行拆成
# 需求项必然 unmet（worker 纯产出回答天然不复述模板措辞）→ 假 unmet → 补做 → 目标词/
# 结论重复×2（"重复的话"家族残余面）。唯一需求 = 子任务目标本身（#664 修的是主 turn
# 词元/脚手架词面，本件是子代理轮残面）。
_SUBAGENT_GOAL_BLOCK_RE = re.compile(
    r"子任务目标：(.*?)(?=\n（所属父任务背景：|\n补充背景/约束：|\n要求：|$)",
    re.S,
)
# 纯字面量输出目标（"只回复一个词：X"类）：交付物 = X 本体，格式前缀是指令脚手架
# （与 _SELF_NEGATING_RE 同哲学：按其自身语义不是可验收内容）。
_PURE_OUTPUT_GOAL_RE = re.compile(r"^只[^：:]*[：:]\s*(.+)$")

# 感知类元指令（"复述你听到的内容"/"说说音频里讲了什么"类）：要求复述附件媒体里
# 感知到的内容，正确答案以媒体语义内容作答，与指令文本天然零词元重合——确定性
# 词元核对必然假阴性（周期#102 真实 WS audio e2e 实证残留：答对仍判未覆盖→补做
# 1轮+噪音横幅）。只认"纯感知复述"形态：带动作动词（写入/保存/生成…）的媒体任务
# 是真实可验需求，仍按常规核对。词元核对验不了"听到"，不冒进修覆盖率阈值。
_PERCEPTION_REF_RE = re.compile(
    r"你听(?:到|见|清)|听到(?:的|了|什么|啥)|听见|听清"
    r"|音频(?:里|中|内容|讲|说|录)|语音(?:里|中|内容|讲|说|录)"
    r"|录音(?:里|中|内容|讲|说)|声音(?:里|中|内容)|这段(?:音频|语音|录音|声音)"
    r"|\bwhat (?:did )?you hear\b"
    r"|\b(?:repeat|restate|parrot)\b.{0,24}\b(?:you )?(?:heard|hear|audio)\b"
)

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
            # 弱 bigram（含停用字）= 跨词边界噪声（"格对""蕉的""个维"）+ 指令脚手架
            # （"请用""不要"），对称过滤（需求侧/证据侧同口径）。不过滤会把纯产物型
            # 回答（表格/JSON，天然不复述请求措辞）的覆盖率压到假 unmet → 触发补做
            # 整题重答 → 追加拼接 → 同一答案出现两遍（2026-10-23 周期#664 实证）。
            if bg[0] not in _STOP_CHARS and bg[1] not in _STOP_CHARS:
                toks.add(bg)
    return toks


def _structure_tokens(response: str) -> set[str]:
    """回复的结构化产出 → 格式线索词证据（语义桥：请求措辞 ↔ 回复结构）。

    请求点名"表格/列表/JSON/代码"时，回复以对应结构交付 = 该格式词有证据。
    纯产物型回答天然不复述请求措辞（表里没有"表格"两个字），纯词元核对必然
    假阴性——2026-10-23 周期#664 实证：纯表格回答 6/27=0.22 误判 unmet →
    补做整题重答被追加 → 同一张表在回复里出现两遍。确定性检出，不走 LLM。
    """
    t = response or ""
    toks: set[str] = set()
    low = t.lower()
    has_table = bool(_TABLE_SEP_RE.search(t)) or "<table" in low
    has_list = bool(_LIST_RE.search(t))
    has_json = bool(_JSON_RE.search(t.strip()))
    has_fence = "```" in t
    if has_table:
        toks.update({"表格", "markdown"})
    if has_list:
        toks.update({"列表", "清单"})
    if has_json:
        toks.add("json")
    if has_fence:
        toks.update({"代码", "markdown"})
    return toks


def _clean_item(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t\n，,。；;、：:")


def is_conditional(text: str) -> bool:
    """条件子句判定（⚡8.5c-①）：'如果X就Y'类是否触发取决于运行时条件。

    确定性核对无法观测条件是否成立，条件项的"未覆盖"不是可执行缺口——
    进补做清单只会诱发"补做结果/逐项说明"式逐条复盘元叙事。
    """
    return bool(_CONDITIONAL_RE.search(text or ""))


def is_self_negating(text: str) -> bool:
    """自否定元指令判定（⚡8.5c-①）：'此行仅为…忽略其语义'类自证不是需求。

    此类文本按其自身语义不得作为可验收需求项——否则必然 unmet、必然触发补做。
    """
    return bool(_SELF_NEGATING_RE.search(text or ""))

def is_perception_meta(text: str) -> bool:
    """感知类元指令判定（18.3）：'复述你听到的内容'类，确定性词元天然验不了。

    正确答案以附件媒体的语义内容作答，与指令文本零词元重合是常态——"未覆盖"
    不是可执行缺口。带动作动词（写入/保存/生成…）的媒体任务是真实可验需求，
    不在此列（照常核对，工具产出可验）。
    """
    t = text or ""
    return bool(_PERCEPTION_REF_RE.search(t)) and not bool(_ACTION_RE.search(t))


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


def _extract_subagent_requirements(text: str) -> list[str]:
    """子代理 worker 回合：唯一需求 = 子任务目标，模板脚手架行不是需求。

    声明行/背景行/要求行按其自身语义是派发机制（"系统派发的并行子任务，非用户
    消息"自己就说了不是用户消息），不得作为可验收需求项——否则纯产出型 worker
    回答必然假 unmet → 补做 → 目标词重复。纯字面量输出目标（"只回复一个词：X"）
    再收窄到交付物本体 X：格式前缀是指令脚手架，交付物就是 X。
    """
    m = _SUBAGENT_GOAL_BLOCK_RE.search(text or "")
    if not m:
        return []
    goal = _clean_item(m.group(1))
    if not goal:
        return []
    pure = _PURE_OUTPUT_GOAL_RE.match(goal)
    if pure:
        goal = _clean_item(pure.group(1))
    return [goal] if goal else []


def extract_requirements(text: str) -> list[str]:
    """从用户原始请求提取需求清单。空列表 = 闲聊/不可拆，不进入自验。

    提取顺序：①数字/圆圈序号列表 ②顺序连接词拆分 ③分号分句（≥2句才拆）
    ④兜底：长任务整体作为单条需求。
    """
    text = (text or "").strip()
    # 剥掉系统附件锚点注记（"[附件已保存到: …]"类）——管道元信息不是用户需求
    text = _ANCHOR_NOTE_RE.sub("", text).strip()
    if not text:
        return []
    # 子代理 worker 回合：脚手架行不进需求项（唯一需求=子任务目标，见上）
    if SUBAGENT_PROMPT_MARKER in text:
        return _extract_subagent_requirements(text)

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
        # 去重保序 + 剔除自否定元指令（"此行仅为…忽略其语义"类自证不是需求）
        seen: set[str] = set()
        out: list[str] = []
        for it in items:
            if it in seen or is_self_negating(it):
                continue
            seen.add(it)
            out.append(it)
        return out

    # 兜底：够长的单件事整体算一条需求；短消息=闲聊不自验；自否定元指令不算需求
    if len(text) >= _MIN_TASK_LEN and not is_self_negating(text):
        return [text]
    return []


@dataclass
class VerifyItem:
    requirement: str
    status: str  # met / uncertain / unmet
    score: float  # 证据覆盖率 0~1
    evidence: str = ""
    conditional: bool = False  # 条件子句（"如果X就Y"）——不进补做清单
    perception: bool = False  # 感知类元指令（"复述你听到的内容"类）——不进补做/不计覆盖


@dataclass
class VerifyReport:
    items: list[VerifyItem] = field(default_factory=list)
    coverage: float = 1.0

    @property
    def ok(self) -> bool:
        # 感知类元指令不参与完成判定（18.3）：确定性词元验不了"听到"，其未覆盖
        # 不是可执行缺口——单独一条感知元指令 = ok，不触发自检横幅
        return all(i.status == "met" for i in self.items if not i.perception)

    @property
    def checked(self) -> int:
        return len(self.items)

    def missing_requirements(self) -> list[str]:
        """未覆盖项（unmet）——补做清单。uncertain 只提醒不补做，防误触发。

        条件子句（"如果X就Y"）在 verify 阶段已封顶 uncertain（⚡8.5c-①），
        不会进补做清单诱发"补做结果/逐项说明"式复盘。
        """
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

        ev_tokens = _tokens(response or "") | _tokens(tool_text) | _structure_tokens(response or "")
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

            # ⚡8.5c-① 条件子句封顶 uncertain：条件是否触发不可观测，
            # "未覆盖"不构成可执行缺口，只提醒不补做（防"补做结果/逐项说明"复盘）
            conditional = is_conditional(req)
            if conditional and status == "unmet":
                status = "uncertain"

            # 18.3 感知类元指令封顶 uncertain：正确答案以媒体语义内容作答，词元
            # 核对必然假阴性——"未覆盖"不进补做清单（答对仍补做1轮的残留根因）
            perception = is_perception_meta(req)
            if perception and status == "unmet":
                status = "uncertain"

            evidence = (f"命中: " + "、".join(sorted(hit)[:6])) if hit else "无证据重合"
            if conditional:
                evidence += "；条件项(不进补做)"
            if perception:
                evidence += "；感知类元指令(词元无法核验，不进补做/不计覆盖)"
            if executed_tools:
                evidence += "；工具: " + ", ".join(e.name for e in executed_tools[:8])
            if blocked_tools:
                # 工具被拦时报"被拦截+原因"，不计执行证据
                evidence += "；⛔被拦截: " + "；".join(
                    f"{e.name}({(e.result_preview.strip() or e.permission)[:60]})"
                    for e in blocked_tools[:8]
                )
            items.append(VerifyItem(req, status, round(score, 2), evidence, conditional, perception))

        # 覆盖率分母不含感知类元指令（18.3 覆盖率假阴性根因：分母掺入词元天然
        # 验不了的项 → 覆盖率假低 → "覆盖 0/N" 噪音横幅）
        verifiable = [i for i in items if not i.perception]
        met = sum(1 for i in verifiable if i.status == "met")
        return VerifyReport(
            items=items,
            coverage=round(met / len(verifiable), 2) if verifiable else 1.0,
        )

    def format_report(self, report: VerifyReport) -> str:
        """渲染自检结论（用户可见，可观测）。"""
        if not report.items:
            return ""
        icons = {"met": "✅", "uncertain": "⚠️", "unmet": "❌"}
        labels = {"met": "", "uncertain": "（证据不足）", "unmet": "（未覆盖）"}
        lines = [REPORT_HEADER]
        for i, item in enumerate(report.items, 1):
            label = labels.get(item.status, "")
            if item.perception and item.status != "met":
                label = "（感知类元指令，无法核验）"
            elif item.conditional and item.status != "met":
                label = "（条件项，仅提醒）"
            lines.append(f"{icons.get(item.status, '❓')} {i}. {item.requirement}{label}")
        verifiable = [x for x in report.items if not x.perception]
        met = sum(1 for x in verifiable if x.status == "met")
        line = f"— 覆盖 {met}/{len(verifiable)}"
        n_perc = len(report.items) - len(verifiable)
        if n_perc:
            line += f"（另 {n_perc} 项感知类元指令不计覆盖）"
        lines.append(line)
        return "\n".join(lines)

    def build_remediation_prompt(self, missing: list[str]) -> str:
        """补做提示词：只补未覆盖项，不重复已完成部分（上限1轮由调用方保证）。

        ⚡8.5c-①：产物只要补充内容本身，禁"补做结果/逐项说明"式复盘元叙事
        （旧措辞"完成后逐项简要说明结果"正是回答尾部元叙事的根因）。
        """
        lines = [
            "以下需求项在刚才的任务中未完成，请补做（只做这些，不要重复已完成部分）："
        ]
        for i, req in enumerate(missing, 1):
            lines.append(f"{i}. {req}")
        lines.append(
            "只输出需要补充的内容本身：不要写\"补做结果\"\"逐项说明\"之类的复盘标题，"
            "不要逐条复述需求条件，不要重复已完成部分。"
        )
        return "\n".join(lines)
