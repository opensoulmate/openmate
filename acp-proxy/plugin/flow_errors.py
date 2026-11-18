"""
流程错误归一 — 失败可懂可修（/will 失败面「人话原因 + 建议 + 可否重试」）

对位 Sprint B 错误归一（error_codes.vendor_compat 是模型网关供应商错误边界）：
本模块收口**流程执行面**的错误形态（工具缺失/网络/超时/治理拦截/审批语义/表达式…），
产出结构化 {code, human, detail, suggestion, retryable}，决策面只看 code。

铁律：不吞原始错误——detail 保留原文（截断），human 说人话，suggestion 给下一步。
"""

from __future__ import annotations

import re
from typing import Any

# 归一码（决策面只看码）
CODE_NEEDS_APPROVAL = "needs-approval"
CODE_GOVERNANCE_BLOCKED = "governance-blocked"
CODE_TOOL_MISSING = "tool-missing"
CODE_NETWORK = "network"
CODE_TIMEOUT = "timeout"
CODE_SERVER = "server-error"
CODE_AUTH = "auth"
CODE_BAD_INPUT = "bad-input"
CODE_EXPR = "expr-error"
CODE_APPROVAL_REJECTED = "approval-rejected"
CODE_APPROVAL_TIMEOUT = "approval-timeout"
CODE_REVIEW_REJECTED = "review-rejected"
CODE_BUDGET_GATE = "budget-gate"
CODE_LOOP_ITEMS = "loop-items"
CODE_UNKNOWN = "unknown"

# (正则, code, 人话, 建议, 可重试) — 顺序即优先级，命中即止
_RULES: list[tuple[re.Pattern[str], str, str, str, bool]] = [
    (re.compile(r"ApprovalRequired|工具需用户审批|需要用户审批", re.I),
     CODE_NEEDS_APPROVAL, "工具被安全策略要求人工审批，尚未放行",
     "在流程里先加 approval 审批节点，或在治理面板放行该工具后重试", False),
    (re.compile(r"ToolGovernanceNeedsApproval|策略要求人工放行|tool-gate", re.I),
     CODE_NEEDS_APPROVAL, "工具治理策略要求人工放行",
     "在「审批闸」里批准放行后流程会原地续跑", False),
    (re.compile(r"ToolGovernanceBlocked|治理策略拦截|治理拦截", re.I),
     CODE_GOVERNANCE_BLOCKED, "工具被治理策略拦截（禁止调用）",
     "检查工具治理 Policy/Profile 是否允许该工具，或换用允许的工具", False),
    (re.compile(r"BudgetGate|预算闸|预算被拦|budget-gate", re.I),
     CODE_BUDGET_GATE, "预算闸拦截：预算超线，需人工放行",
     "审批放行（拨款）后流程原地续跑，或调整该节点预算", False),
    (re.compile(r"工具未注册|unknown tool|tool.*not (found|registered)|未找到工具|no such tool", re.I),
     CODE_TOOL_MISSING, "工具不存在或对应插件未装载",
     "检查工具名是否 <插件id>__<工具名>，并确认插件已装载（/plugins）", False),
    (re.compile(r"ConnectionRefused|ConnectError|ECONNREFUSED|Name or service not known"
                r"|nodename nor servname|getaddrinfo|Connection reset|RemoteDisconnected"
                r"|连接.*(失败|拒绝)|网络不通", re.I),
     CODE_NETWORK, "依赖的服务连不上（网络/服务未启动）",
     "确认目标服务已启动、地址端口可达后一键重试", True),
    (re.compile(r"timed? ?out|TimeoutError|超时", re.I),
     CODE_TIMEOUT, "执行超时（依赖服务响应太慢）",
     "稍后一键重试；频繁超时则检查依赖服务负载或调大节点 timeout_seconds", True),
    (re.compile(r"\b5\d\d\b|Bad Gateway|Service Unavailable|Internal Server Error|服务器.*(错误|故障)", re.I),
     CODE_SERVER, "依赖服务临时故障（5xx）",
     "通常是一过性故障，一键重试即可", True),
    (re.compile(r"\b401\b|\b403\b|Unauthorized|Forbidden|token|JWT|鉴权|权限不足|登录", re.I),
     CODE_AUTH, "鉴权/权限被拒绝",
     "检查凭据配置（token/密钥）与权限后重试", False),
    (re.compile(r"SchemaValidationError|类型应为|JSONDecodeError|KeyError|TypeError|ValueError|参数.*(非法|缺失|错误|类型)|invalid", re.I),
     CODE_BAD_INPUT, "节点参数有问题（缺字段/类型不对/值非法）",
     "在「编辑参数」里检查该节点的 args/表达式引用后重试", False),
    (re.compile(r"ExprError|表达式", re.I),
     CODE_EXPR, "表达式求值失败（引用的上游输出不存在或表达式非法）",
     "检查 decision/args 里的 $. 引用是否指向已执行的上游节点", False),
    (re.compile(r"人工审批驳回|审批驳回|rejected", re.I),
     CODE_APPROVAL_REJECTED, "审批被驳回，流程按驳回终止",
     "如需继续：一键重试会重新打开该审批，或回放重跑", True),
    (re.compile(r"验收驳回|review-rejected", re.I),
     CODE_REVIEW_REJECTED, "交付物验收被驳回，不算完成",
     "返工交付物后一键重试（从验收节点续跑）", True),
    (re.compile(r"审批超时|approval-timeout|timed-out", re.I),
     CODE_APPROVAL_TIMEOUT, "审批超时未处理，按策略终止",
     "一键重试重新打开审批；或调整该节点 timeout_hours", True),
    (re.compile(r"loop\.over 未解析为列表", re.I),
     CODE_LOOP_ITEMS, "循环数据源不是列表（loop.over 解析失败）",
     "检查 loop.over 的 $. 引用指向的输出是否为数组", False),
    (re.compile(r"节点不存在", re.I),
     CODE_BAD_INPUT, "流程定义里找不到要执行的节点",
     "检查判断分支/跳转指向的节点 id 是否存在（编辑参数）", False),
]


def normalize_error(raw: str | None, *, where: str = "") -> dict[str, Any]:
    """任意流程错误原文 → 结构化归一结果（决策面只看 code）。

    - human：说人话（失败卡片首行）
    - detail：原文留痕（截断 400 字，排查用）
    - suggestion：下一步怎么办（一键重试/改参数/去审批）
    - retryable：一键重试是否可能救回来
    """
    text = str(raw or "").strip()
    for pattern, code, human, suggestion, retryable in _RULES:
        if pattern.search(text):
            return {
                "code": code,
                "human": human,
                "detail": text[:400],
                "suggestion": suggestion,
                "retryable": retryable,
                "where": where,
            }
    return {
        "code": CODE_UNKNOWN,
        "human": "执行失败（未归类错误）",
        "detail": text[:400],
        "suggestion": "看事件日志定位失败节点；一键重试或回放重跑",
        "retryable": True,
        "where": where,
    }
