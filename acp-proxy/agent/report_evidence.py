# -*- coding: utf-8 -*-
"""路线图 ⚡8.2 · 短路合成报告治理：报告/自检结论必须以工具执行证据为前提。

三个治理点（确定性，不走LLM）：
1. **执行证据判定**（parse_ledger）：从回合工具账本（all_tool_calls 形态）区分
   "真实执行"与"被拦截"——permission 语义单一真源（permission_gate.GateResult.behavior
   + infection/loop_guard/sandbox 留痕键）。被拦截的调用不是执行证据。
2. **报告治理**（assess/amend_response）：回复声称完成（"任务执行报告"/"已完成"…）
   但回合零成功工具执行 → 声明"未经执行验证，不构成完成证明"并降级报告标题；
   工具被拦 → 报"被拦截+原因"，禁报"完成"（标题降级+⛔拦截清单）。
3. **语义缓存准入**（is_cacheable_request）：执行类请求（创建/修改/删除…）不走
   语义缓存——缓存回放的"完成报告"对本回合请求不构成执行证据（run2 1.38s 秒合成
   "任务执行报告"短路路径的根因）。纯信息类问答仍可缓存。

设计约束（与 SelfVerifier/ExperienceSink 同风格）：
- 全确定性、可复现、测试稳定；启发式宁可多声明一次"未验证"，不谎报完成
- 本模块不 import self_verify（self_verify 反向依赖本模块的账本解析，避免循环）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent.subagent_manager import SUBAGENT_PROMPT_MARKER

# ── 账本 permission 语义（单一真源：permission_gate.GateResult.behavior 注释）──
# allow / deny / ask→approved / ask→denied / degraded-allow / cached-allow
EXECUTED_PERMISSIONS = {
    "allow",
    "ask-approved",
    "degraded-allow",
    "cached-allow",
    "infection:pass_with_trace",  # 8.1③ 无人值守放行+留痕：工具照常执行
}

BLOCKED_PERMISSIONS = {"deny", "ask-denied", "sandbox-restricted"}

# 回复里的完成声称标记（"说了≠做了"——声称本身不是证据）
COMPLETION_CLAIM_MARKERS = (
    "任务执行报告",
    "已完成", "完成", "已创建", "已生成", "已修复", "已发送", "已更新",
    "已实现", "已安装", "已部署", "已提交", "已写入", "已保存", "已配置",
    "通过", "成功", "✅",
)

# 执行类请求（有副作用的操作）——语义缓存不准入
_EXEC_REQ_RE = re.compile(
    r"(创建|新建|写一|写个|写入|帮我写|帮我改|帮我修|帮我建|帮我删|帮我装|"
    r"修复|修一|修改|改一|改下|改动|删除|删掉|生成|安装|运行|执行|部署|"
    r"提交|推送|下载|上传|发送|更新|配置|迁移|重构|清理|整理|实现|"
    r"替换|移动|重命名|建一|建个|搭一|补一|搜索|检索|抓取|爬取)"
)

# 显式点名工具（"用 web_search 搜索"、"调用 schedule_list"、"用 xxx 工具"）＝动作类。
# 周期#41实证漏网形态：动词白名单不含"搜索"且不认工具点名→调研回合被判非执行类→
# semantic-cache 88%相似命中回放旧"工具不可用"误报（复测标记只防EXACT不防SEMANTIC）。
# 工具点名是确定性高精度信号：请求显式要求调某个工具＝必须真实执行才算完成。
_TOOL_NAMED_RE = re.compile(
    r"(用|使用|调用|通过)\s*[`'\"]?[A-Za-z][A-Za-z0-9_\-]{1,}"
)


def _entry_name_args(tc) -> tuple[str, str]:
    """兼容两种账本形态：LLM 原始 {"function": {...}} 与轨迹账本扁平 dict。"""
    if isinstance(tc, dict):
        raw = tc.get("function")
        fn: dict = raw if isinstance(raw, dict) else tc
        return str(fn.get("name", "")), str(fn.get("arguments", ""))
    return "", ""


def _is_blocked(permission: str) -> bool:
    if permission in BLOCKED_PERMISSIONS:
        return True
    if permission.startswith("loop_guard:"):
        return True
    if permission.startswith("infection:") and permission != "infection:pass_with_trace":
        return True
    return False


@dataclass
class LedgerEntry:
    name: str
    arguments: str
    result_preview: str
    permission: str


def parse_ledger(tool_calls_log: list | None) -> tuple[list[LedgerEntry], list[LedgerEntry]]:
    """把回合工具账本拆成 (真实执行, 被拦截)。

    - 伪条目（"[infection-guard]" 类留痕行、无名条目）不计入任何一侧
    - permission 缺失（LLM 原始 tool_call 形态）视为已执行——门禁结果未知时
      不虚构拦截；拦截只在有明确拦截留痕时认定
    - cached-allow（工具结果缓存命中）计入执行证据：同名同参的确定性重放，
      结果对本次调用仍真实
    """
    executed: list[LedgerEntry] = []
    blocked: list[LedgerEntry] = []
    for tc in tool_calls_log or []:
        if not isinstance(tc, dict):
            continue
        name, args = _entry_name_args(tc)
        if not name or name.startswith("["):
            continue  # 伪条目/留痕行
        entry = LedgerEntry(
            name=name,
            arguments=args,
            result_preview=str(tc.get("result_preview", "")),
            permission=str(tc.get("permission", "")),
        )
        if _is_blocked(entry.permission):
            blocked.append(entry)
        elif entry.permission in EXECUTED_PERMISSIONS or entry.permission == "":
            executed.append(entry)
        # 其他未知 permission 值：不计执行证据（宁可证据不足）
    return executed, blocked


def claims_completion(response: str) -> bool:
    """回复是否声称完成/产出执行报告。"""
    text = response or ""
    return any(m in text for m in COMPLETION_CLAIM_MARKERS)


def is_action_request(user_text: str) -> bool:
    """用户请求是否为动作类（需要真实工具执行才算完成）。

    两种确定性形态：①动作动词（创建/修复/搜索…）②显式点名工具（"用 web_search"）。
    """
    text = user_text or ""
    return bool(_EXEC_REQ_RE.search(text) or _TOOL_NAMED_RE.search(text))


def is_cacheable_request(user_text: str) -> bool:
    """语义缓存准入判据：执行类请求不缓存不回放（防合成报告短路）。

    系统派发的子代理任务 prompt（SUBAGENT_PROMPT_MARKER）同样不准入——
    语义相似命中会把只差一个任务词的兄弟子任务答案互相回放（实证：ALPHA/BETA
    worker prompt 仅差一个词，Jaccard≈0.95 高于 0.85 阈值，BETA 条目被命中
    32 次、ALPHA worker 逐字回放 BETA 答案连错 11 轮）。子代理任务是执行类
    请求，每次必须真实执行，不准入缓存读写。
    """
    if SUBAGENT_PROMPT_MARKER in (user_text or ""):
        return False
    return not is_action_request(user_text)


@dataclass
class EvidenceAssessment:
    action_required: bool
    claims: bool
    executed: list[LedgerEntry] = field(default_factory=list)
    blocked: list[LedgerEntry] = field(default_factory=list)
    verdict: str = "no-claim"  # no-claim / grounded / blocked / unsupported

    @property
    def grounded(self) -> bool:
        return self.verdict == "grounded"


def assess(
    user_text: str,
    response: str,
    tool_calls_log: list | None = None,
) -> EvidenceAssessment:
    """对照工具执行证据评估回复的完成声称。"""
    executed, blocked = parse_ledger(tool_calls_log)
    action = is_action_request(user_text)
    claims = claims_completion(response)

    if not claims:
        verdict = "no-claim"
    elif executed:
        verdict = "grounded"
    elif blocked:
        verdict = "blocked"
    elif action:
        verdict = "unsupported"
    else:
        # 非动作请求的口头总结（如"分析成功"）没有可执行证据要求
        verdict = "no-claim"
    return EvidenceAssessment(
        action_required=action,
        claims=claims,
        executed=executed,
        blocked=blocked,
        verdict=verdict,
    )


_HEADING_RE = re.compile(r"^## 任务执行报告\s*$", re.MULTILINE)

# 横幅首行前缀（frame_origin 采集侧帧分类的单一真源——改措辞须同步语义）
GOV_BANNER_PREFIXES = (
    "⛔ **工具被拦截（未执行，不构成完成证明）**",
    "⚠️ **执行证据不足**",
)
AVAIL_BANNER_PREFIX = (
    "⚠️ **工具可用性更正**（对照工具清单/执行账本核验，上文相关说法不成立）："
)


def governance_banner(assessment: EvidenceAssessment) -> str:
    """治理声明（用户可见）：被拦截的工具报"被拦截+原因"；零执行报"不构成完成证明"。"""
    lines: list[str] = []
    if assessment.blocked:
        lines.append(f"{GOV_BANNER_PREFIXES[0]}：")
        for e in assessment.blocked[:8]:
            reason = e.result_preview.strip() or e.permission
            lines.append(f"- `{e.name}` —— 被拦截：{reason[:160]}")
    if assessment.verdict == "unsupported":
        lines.append(
            f"{GOV_BANNER_PREFIXES[1]}：本回合无成功工具执行记录，"
            "以下内容未经执行验证，不构成完成证明。"
        )
    return "\n".join(lines)


def amend_response(response: str, assessment: EvidenceAssessment) -> str:
    """无执行证据的完成声称 → 降级+声明（禁报"完成"）。

    - 报告标题降级：`## 任务执行报告` → `## 任务执行报告（⚠️ 未经执行验证）`
    - 前置治理声明：被拦截的工具报"被拦截+原因"；零执行报"不构成完成证明"
    - 有执行证据（grounded）或无声称（no-claim）时原文返回
    """
    if assessment.verdict not in ("blocked", "unsupported"):
        return response
    body = _HEADING_RE.sub("## 任务执行报告（⚠️ 未经执行验证）", response or "")
    return governance_banner(assessment) + "\n\n" + body


# ══════════════════════════════════════════════════════════════════
# ⚡8.5c② 可用性误报治理（8.2 "说了≠做了"的镜像："做了/有，说没有"）
# ══════════════════════════════════════════════════════════════════
# 实证形态（周期#41 两次）：模型谎称工具"不在工具列表/运行时未注入"并拒做——
#   run-A 称"没有 web_search"（同回合账本已成功调用 web_search）；
#   FINAL 轮称"message_send 运行时未注入"（builtin_tools 无条件装配+系统提示有清单）。
# 治理=可用性声称对照**工具面真源**（_active_tool_names，本轮真实注入的工具名集合）
# 与**执行账本**（parse_ledger 已执行侧）确定性核验，被证伪的声称降级修正（amend 同款）。
#
# 两级判据（宁可漏纠不误纠——误纠会侵蚀用户对更正的信任）：
# - registration（注册类）："X 未注入/不在工具列表/不存在/没有 X 工具…"——声称针对
#   **注册/注入事实**，工具名在工具面里即被证伪（无须执行过）
# - availability（可用性类）："X 不可用/无法使用 X 工具"——声称模糊（可能指后端故障
#   而非工具缺失），仅当 X 本回合**真实执行成功**才证伪（没试过就不武断更正）
# - generic（无名类）："运行时未注入工具/没有可用工具"——仅当本回合有成功工具执行才证伪

# 词尾边界内建进 token——防 _RESULTISH 排除词被回溯绕过（"terminal 输出"缩成"termina"）
_NAME_TOKEN = r"[A-Za-z][A-Za-z0-9_\-]{1,40}(?![A-Za-z0-9_\-])"

# "没有 X 的结果/输出…"不是可用性声称（是结果缺失）——排除词防误纠
_RESULTISH = (
    r"(?!\s*(?:的)?(?:结果|输出|返回|记录|数据|日志|缓存|命中|响应|内容|条目|行|"
    r"权限|密钥|报错|错误|超时|更新|变化|改动|新增|删除|效果|进展|回复|消息|文件|"
    r"截图|图片|附件|索引|配置|权限错误))"
)

# 注册类声称（名字在前 / 名字在后两形态）
_REG_NAME_FIRST = re.compile(
    rf"[`'\"]?(?P<name>{_NAME_TOKEN})[`'\"]?\s*"
    rf"(?:这个)?(?:工具|命令|功能)?\s*"
    rf"(?:运行时)?(?:未注入|未加载|未注册|没被注入|未被注入|没提供|未提供|"
    rf"不存在|(?:不|没)(?:有|在)(?:当前)?(?:会话的?)?(?:工具)?(?:列表|清单|之中|里面|里))"
)
_REG_NAME_LAST = re.compile(
    rf"(?:没有|缺少|缺|不存在|未提供|没有提供|没有注册|未注册)(?:名为|叫做|叫)?\s*"
    rf"[`'\"]?(?P<name>{_NAME_TOKEN})[`'\"]?\s*(?:这个)?(?:工具|命令|功能)"
)
# 容器在前形态（周期#66 实测事故句"当前会话的工具列表中没有 web_search"）——
# 工具列表/清单/可用工具做主语，名字在"没有/缺少"之后；_RESULTISH 排除
# "工具列表中没有 X 的结果"类结果缺失句防误纠
_REG_LIST_CONTAINER = re.compile(
    rf"(?:可用工具|工具列表|工具清单)(?:中|里|内|里面|都)?"
    rf"(?:没有|缺少|缺|不含|不包含|未包含|未提供)"
    rf"\s*[`'\"]?(?P<name>{_NAME_TOKEN})[`'\"]?{_RESULTISH}"
)
# 无"工具"后缀的裸形态（run-A "没有 web_search"）：要求主语语境+排除结果类宾语
_REG_BARE = re.compile(
    rf"(?:我|本agent|本Agent|系统|助手|环境|运行时|当前|这里)(?:里|中|内|面|都)?(?:没有|不具备)"
    rf"\s*[`'\"]?(?P<name>{_NAME_TOKEN})[`'\"]?{_RESULTISH}"
)

# 可用性类声称（模糊——仅执行账本可证伪）
_AVAIL_NAME_FIRST = re.compile(
    rf"[`'\"]?(?P<name>{_NAME_TOKEN})[`'\"]?\s*"
    rf"(?:这个)?(?:工具|命令|功能)\s*"
    rf"(?:现在)?(?:不|没)(?:可用|能用|可用了|能用了|有(?:响应|效果))|"
    rf"[`'\"]?(?P<name2>{_NAME_TOKEN})[`'\"]?\s*(?:无法使用|用不了|调不了)"
)
_AVAIL_NAME_LAST = re.compile(
    rf"(?:无法(?:使用|调用)|不能(?:使用|调用)|用不了)(?:该|这个)?(?:工具)?\s*"
    rf"[`'\"]?(?P<name>{_NAME_TOKEN})[`'\"]?\s*(?:这个)?(?:工具|命令|功能)?{_RESULTISH}"
)
# 裸可用性形态（"我无法使用 web_search"）——模糊声称，只按执行账本证伪
_AVAIL_BARE = re.compile(
    rf"(?:我|本agent|本Agent|系统|助手|环境|运行时|当前|这里)(?:里|中|内|面|都)?(?:无法(?:使用|调用)|用不了)"
    rf"\s*[`'\"]?(?P<name>{_NAME_TOKEN})[`'\"]?{_RESULTISH}"
)

# 无名类声称（必须与"工具"同现——防"参数没有注入到请求里"类误报）
_GENERIC_CLAIM = re.compile(
    r"(?:运行时)?(?:未|没有)(?:被)?注入(?:任何|可用的?)?工具|"
    r"工具(?:未|没有)(?:被)?注入|"
    r"没有(?:任何)?(?:可用的?)?工具(?:可用|可调用|可使用)?|"
    r"(?:工具|工具列表|工具清单)(?:为空|是空的|没有(?:任何|一个))|"
    r"未提供(?:任何)?(?:可用)?工具"
)


@dataclass
class AvailabilityClaim:
    kind: str          # "registration" / "availability" / "generic"
    tool_name: str     # generic 时为 ""
    snippet: str       # 声称原文片段（审计可见）
    proof: str = ""    # 证伪依据："registry"（工具面在册）/ "executed"（本回合真实执行成功）


def find_availability_claims(response: str) -> list[AvailabilityClaim]:
    """抽取回复中的工具可用性声称（确定性正则，不走LLM）。"""
    text = response or ""
    claims: list[AvailabilityClaim] = []
    seen: set[tuple[str, str]] = set()

    def _add(kind: str, name: str, span: tuple[int, int]) -> None:
        key = (kind, name)
        if key in seen:
            return
        seen.add(key)
        claims.append(AvailabilityClaim(
            kind=kind, tool_name=name,
            snippet=text[max(0, span[0] - 10):span[1] + 10].strip(),
        ))

    for pat, kind in ((_REG_NAME_FIRST, "registration"),
                      (_REG_NAME_LAST, "registration"),
                      (_REG_BARE, "registration"),
                      (_REG_LIST_CONTAINER, "registration")):
        for m in pat.finditer(text):
            _add(kind, m.group("name"), m.span())

    for pat in (_AVAIL_NAME_FIRST, _AVAIL_NAME_LAST, _AVAIL_BARE):
        for m in pat.finditer(text):
            name = m.groupdict().get("name") or m.groupdict().get("name2") or ""
            if name:
                _add("availability", name, m.span())

    # 无名类声称仅在没有任何具名声称时登记（具名更具体，同句双报属噪音）
    if not any(c.kind != "generic" for c in claims):
        for m in _GENERIC_CLAIM.finditer(text):
            _add("generic", "", m.span())
    return claims


@dataclass
class AvailabilityAssessment:
    claims: list[AvailabilityClaim] = field(default_factory=list)
    false_claims: list[AvailabilityClaim] = field(default_factory=list)

    @property
    def has_false_claim(self) -> bool:
        return bool(self.false_claims)


def assess_availability(
    response: str,
    available_tools: set | list | tuple | None,
    tool_calls_log: list | None = None,
) -> AvailabilityAssessment:
    """对照工具面真源+执行账本核验可用性声称，挑出被证伪的声称。

    - available_tools：本轮真实注入的工具名集合（soulmate_agent._active_tool_names）
      ——"运行时未注入"类声称的第一真源；None/空=无真源可对照，一律不更正（诚实边界）
    - tool_calls_log：回合工具账本；**已执行侧**（parse_ledger executed）才构成
      "工具实际可用"证据——被拦截的调用恰证明"不可用"，反向支持模型的说法
    """
    claims = find_availability_claims(response)
    names = {str(n) for n in (available_tools or []) if n}
    executed, _blocked = parse_ledger(tool_calls_log)
    executed_names = {e.name for e in executed}

    false_claims: list[AvailabilityClaim] = []
    for c in claims:
        if c.kind == "generic":
            if executed_names:
                c.proof = "executed"
                false_claims.append(c)
        elif c.kind == "registration":
            if c.tool_name and c.tool_name in executed_names:
                c.proof = "executed"
                false_claims.append(c)
            elif c.tool_name and c.tool_name in names:
                c.proof = "registry"
                false_claims.append(c)
        elif c.kind == "availability":
            # 模糊声称：只有"真实执行成功"能证伪（工具面在册≠后端可用）
            if c.tool_name and c.tool_name in executed_names:
                c.proof = "executed"
                false_claims.append(c)
    return AvailabilityAssessment(claims=claims, false_claims=false_claims)


def availability_banner(assessment: AvailabilityAssessment) -> str:
    """可用性更正声明（用户可见）：被证伪的声称逐条列出+真源说明。"""
    if not assessment.false_claims:
        return ""
    lines = [AVAIL_BANNER_PREFIX]
    for c in assessment.false_claims[:8]:
        if c.kind == "generic":
            lines.append(
                "- 上文称\"工具未注入/无可用工具\"不成立——本回合有真实工具执行记录。"
            )
        elif c.proof == "executed":
            lines.append(f"- `{c.tool_name}` —— {c.snippet[:80]}：该工具本回合已真实调用执行成功")
        else:
            lines.append(f"- `{c.tool_name}` —— {c.snippet[:80]}：该工具真实存在于本回合工具清单（已注入）")
    lines.append("以上工具可用性以工具清单/执行账本为准，请勿以\"工具不存在/未注入\"为由拒做。")
    return "\n".join(lines)


def amend_availability(response: str, assessment: AvailabilityAssessment) -> str:
    """被证伪的可用性声称 → 前置更正声明（原文保留，不删改模型原话——可审计）。"""
    if not assessment.false_claims:
        return response
    return availability_banner(assessment) + "\n\n" + (response or "")


# ── ⚡8.5c-②b 拒做补做（周期#42 诚实边界"拒做行为本身未强制重试"销账）────────
# 更正声明只治"说"不治"做"：模型谎称点名工具"没注入/不在清单"而拒做，实测间歇
# 复发（验收调研类 2026-10-12 1/2 复现）。此处定义**有界补做 1 轮**的目标判定
# （宁可不做不误做：proof=registry 即"工具在册但本回合零成功执行"——assess_availability
# 语义；用户明文点名才补，模型自选工具的取舍自由不越俎）与安全措辞（新文案不携带
# 任何可被 find_availability_claims 匹配的声称形态——防对更正文本二次误报/防判据
# 主答区被误判，措辞安全由测试钉死）。

def remediation_targets(
    assessment: AvailabilityAssessment,
    user_text: str = "",
) -> list[str]:
    """拒做补做目标：proof=registry 的具名被证伪声称 + 用户明文点名该工具。

    proof=registry 的语义（assess_availability 单一真源）= 工具在工具面真源在册、
    但本回合**没有成功执行**——恰是"说没有而没做"的拒做形态；proof=executed
    （做完了还说没有）不需要补做，走既有更正声明即可。返回按声称顺序去重。
    """
    out: list[str] = []
    for c in assessment.false_claims:
        if c.proof != "registry" or not c.tool_name:
            continue
        if c.tool_name not in (user_text or ""):
            continue
        if c.tool_name not in out:
            out.append(c.tool_name)
    return out


def build_execution_remediation_prompt(tool_name: str) -> str:
    """补做指令（注入为 user 消息）：点名工具真实在册，请直接调用完成任务。

    措辞安全：不含"X 工具不可用/没有 X 工具/未注入工具"等声称形态（find_availability_claims
    零命中，测试钉死）——防模型引用本指令文本触发二次误报更正。
    """
    return (
        f"系统更正指令：你上一条回复对 `{tool_name}` 的可用性判断有误。"
        f"`{tool_name}` 已在本回合工具清单中真实提供，请直接调用 `{tool_name}` "
        f"完成用户的请求并给出结果；不要以工具缺失为由拒绝执行，也不要用其他工具替代。"
    )


def remediation_succeeded(tool_name: str, tool_calls_log: list | None) -> bool:
    """补做是否真的执行了目标工具（执行账本 executed 侧单一真源）。"""
    executed, _blocked = parse_ledger(tool_calls_log)
    return any(e.name == tool_name for e in executed)


def build_remediation_response(original: str, tool_name: str, fix_resp: str) -> str:
    """补做成功的最终回复 = 补做答案 + 更正声明（首轮原文留档 journal/流式帧，
    不在回复里逐字复述误报——防可用性抽取对引用文本二次误报）。

    末行"可用性更正声明"同时是验收判据的主答区切割点（acceptance_benchmark
    verify_research 按该短语切主答区），补做答案在主答区首位。
    """
    return (
        (fix_resp or "").strip()
        + f"\n\n可用性更正声明（首轮）：首轮回复对 `{tool_name}` 的可用性判断有误，"
        + "该工具真实存在于本回合工具清单且已实际调用完成任务（首轮原文留档审计日志）。"
    )
