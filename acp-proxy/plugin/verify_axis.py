"""
双轴完成状态 + settle 拒伪完成判定 — 纯 Python 确定性状态机（独立可测，无外部依赖）

痛点（用户原话）："建完自动化≠监督"；验收铁律："报成功没用，要用内容级探针、
子代理自报须抽查"。本模块是监督闭环的判档骨架（对位 OMH labels.py + settle 三条）：

- **双轴**：状态轴（Plan·not run / Code·reported done / Test·verified）+ 置信轴。
  执行器自报永远封顶 "reported done"（黄）；"verified"（绿）只由验收证据翻档。
  未知 fail-closed：没有证据就是没验证，宁黄不绿。
- **settle 拒伪完成三条**：自报失败→记 failed；缺交付物→拒 ok；
  有验收标准未满足→拒 ok。拒绝都带 reason_code 留痕。
- **防自审**：验收者=执行者（self_referential_evidence）→ 翻档被拒并留痕；
  证据为空（no_evidence）→ fail-closed 拒翻档。

铁律：本文件只做判定（纯函数），不碰状态机、不落盘、不发通知——
翻档与留痕的执行面在 flow_engine / flow_collab。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

# ── 双轴词表（OMH labels.py 语义：否定词放前，截断也保得住）──

AXIS_NOT_RUN = "not run"            # Plan·not run（灰）：没跑/没到
AXIS_REPORTED = "reported done"     # Code·reported done（黄）：执行器自报封顶
AXIS_VERIFIED = "verified"          # Test·verified（绿）：验收证据翻档

CLAIM_SELF = "self-report"          # 置信轴：执行者自报（永远只是 claim）
CLAIM_EVIDENCE = "evidence"         # 置信轴：验收证据（探针/非执行者验收）

# ── 拒绝理由码（settle / verify 翻档共用，留痕可读）──

REJECT_SELF_VERIFY = "self_referential_evidence"   # 验收者=执行者（自审）
REJECT_NO_EVIDENCE = "no_evidence"                 # 证据为空（fail-closed）
REJECT_MISSING_DELIVERABLE = "missing_deliverable" # 缺交付物
REJECT_ACCEPTANCE_MISSES = "acceptance_misses"     # 验收标准未满足
REJECT_NOT_DONE = "target_not_done"                # 被验收对象还没干完
REJECT_UNKNOWN_VERIFIER = "unknown_verifier"       # 验收者身份未知（fail-closed）

# 结构性拒绝：修复内容解决不了（身份/配置问题）——不进修复轮，直接定夺
_STRUCTURAL_REJECTS = frozenset({REJECT_SELF_VERIFY, REJECT_UNKNOWN_VERIFIER})

# 节点类型语义：产活节点（有产出，需验收）vs 验收节点（产出即验收证据）
PRODUCER_TYPES = frozenset({"tool", "agent", "human", "loop", "parallel"})
VERIFY_TYPE = "verify"

# settle 修复轮上限（超出→重规划；"失败先修复≤2 次再重规划"）
SETTLE_MAX_FIXES = 2

_INPUT_REQUIRED_RE = re.compile(r"\bINPUT_REQUIRED\b", re.IGNORECASE)


def is_input_required(state: str, result: Any = None) -> bool:
    """A2A input-required 判定：状态字面量或结果文本 INPUT_REQUIRED 标记。"""
    if str(state or "").lower().replace("_", "-") == "input-required":
        return True
    return bool(_INPUT_REQUIRED_RE.search(str(result or "")))


def node_axis(status: str, verified: bool = False) -> str:
    """状态轴渲染词（fail-closed：未知状态一律按未验证的自报/未跑处理）。"""
    if status == "ok":
        return AXIS_VERIFIED if verified else AXIS_REPORTED
    if status in ("pending", ""):
        return AXIS_NOT_RUN
    if status == "running":
        return "running"
    if status in ("waiting-approval", "waiting"):
        return "waiting"
    return status  # failed / skipped 原词（不是完成态，不进双轴）


def node_confidence(status: str, verified: bool = False) -> str:
    """置信轴（fail-closed）：verified 才是 verified；ok 只是 reported done。"""
    if verified and status == "ok":
        return AXIS_VERIFIED
    if status == "ok":
        return AXIS_REPORTED
    return AXIS_NOT_RUN


def has_deliverable(result: Any) -> bool:
    """交付物非空判定（None/空串/空容器=缺交付物）。"""
    if result is None:
        return False
    if isinstance(result, str):
        return bool(result.strip())
    if isinstance(result, (list, dict, tuple, set)):
        return bool(result)
    return True


def can_verify(*, verifier: str, executor: str, evidence: str) -> tuple[bool, str, str]:
    """翻 verified 判定：证据非空 + 验收者已知且非执行者。

    返回 (ok, reason_code, detail)；任一不满足即拒（fail-closed）。
    """
    if not str(verifier or "").strip():
        return False, REJECT_UNKNOWN_VERIFIER, "验收者身份未知，拒绝翻档（fail-closed）"
    if not str(evidence or "").strip():
        return False, REJECT_NO_EVIDENCE, "无验收证据，拒绝翻档（报成功没用，要内容级探针/验收证据）"
    if executor and verifier.strip() == executor.strip():
        return False, REJECT_SELF_VERIFY, \
            f"验收者=执行者（{verifier}），自审拒绝翻档（子代理自报须抽查）"
    return True, "", "验收证据成立"


def settle_decision(*, result: Any, acceptance_ok: bool | None = None,
                    claim_verified: bool = False) -> dict[str, Any]:
    """settle 拒伪完成判定（纯函数）。

    - 缺交付物 → 拒 ok（missing_deliverable）
    - 有验收标准且未满足 → 拒 ok（acceptance_misses）
    - 执行者自报 verified → 一律不采信（claim 只能到 reported done）；
      本判定不因 claim_verified 翻档，返回值里显式标注封顶。
    acceptance_ok=None 表示未声明验收标准。
    返回 {"accept": bool, "reason_code": str, "detail": str, "capped": bool}
    """
    if not has_deliverable(result):
        return {"accept": False, "reason_code": REJECT_MISSING_DELIVERABLE,
                "detail": "缺交付物，拒 ok（报成功没用）", "capped": bool(claim_verified)}
    if acceptance_ok is False:
        return {"accept": False, "reason_code": REJECT_ACCEPTANCE_MISSES,
                "detail": "验收标准未满足，拒 ok", "capped": bool(claim_verified)}
    return {"accept": True, "reason_code": "",
            "detail": "交付物成立（自报封顶 reported done）",
            "capped": bool(claim_verified)}


def reject_fixable(reason_code: str) -> bool:
    """拒绝是否可修复轮救回：结构性拒绝（自审/验收者未知）修复内容无用。"""
    return reason_code not in _STRUCTURAL_REJECTS


def verdict_reason_for(record: Any) -> str:
    """done 但未验证节点的归因码（goal_verdict 语义）。"""
    if getattr(record, "settle_rejects", 0) > 0:
        return "verification_misses"
    if getattr(record, "type", "") in ("agent", "human"):
        return "handoff_not_checked"
    return "no_verification_node"


def run_verdict(status: str, records: Iterable[Any]) -> dict[str, Any]:
    """实例级判档（fail-closed）：产活节点全部 verified → "verified"；
    否则 "done · unverified" + 理由码（no_verification_node / verification_misses /
    handoff_not_checked）。没有任何产活/存在未验证产活一律黄——宁黄不绿。

    records 为 NodeRun 形状对象（type/status/verified/settle_rejects/owned_by）。
    owned_by 非空（loop body / parallel 分支合成件）不独立判档，归其归属节点。
    非完成实例按状态轴原词（running / waiting / failed），不判 verified。
    """
    if status != "completed":
        return {"verdict": status, "reasons": [], "unverified": []}
    records = list(records)
    producers = [r for r in records
                 if getattr(r, "type", "") in PRODUCER_TYPES
                 and not getattr(r, "owned_by", "")]
    reasons = []
    for r in producers:
        if not getattr(r, "verified", False):
            reasons.append({"node": getattr(r, "node_id", "?"),
                            "reason": verdict_reason_for(r)})
    if not producers:
        reasons.append({"node": "-", "reason": "no_verification_node"})
    if reasons:
        return {"verdict": "done·unverified", "reasons": reasons,
                "unverified": [x["node"] for x in reasons]}
    return {"verdict": "verified", "reasons": [], "unverified": []}


def task_axis(status: str, verified: bool = False) -> str:
    """工单双轴渲染词（收发台/看板）：delivered 未验收=黄，verified=绿。"""
    if status == "delivered":
        return AXIS_VERIFIED if verified else AXIS_REPORTED
    if status in ("queued", "todo"):
        return AXIS_NOT_RUN
    return status
