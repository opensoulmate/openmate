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
    """语义缓存准入判据：执行类请求不缓存不回放（防合成报告短路）。"""
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


def governance_banner(assessment: EvidenceAssessment) -> str:
    """治理声明（用户可见）：被拦截的工具报"被拦截+原因"；零执行报"不构成完成证明"。"""
    lines: list[str] = []
    if assessment.blocked:
        lines.append("⛔ **工具被拦截（未执行，不构成完成证明）**：")
        for e in assessment.blocked[:8]:
            reason = e.result_preview.strip() or e.permission
            lines.append(f"- `{e.name}` —— 被拦截：{reason[:160]}")
    if assessment.verdict == "unsupported":
        lines.append(
            "⚠️ **执行证据不足**：本回合无成功工具执行记录，"
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
