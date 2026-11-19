"""记忆写入决策留痕（MEM-Semantic 加码①·OMH memory_admission 可抄件）。

OMH 深潜卷一 §5.1：「准入三岔……非自动批准必写 review_reason」「指令形摘要拒收
（记忆不能变成注入指令）」「类凭据元数据拒收」「重复候选盖 duplicate_of 章」。
本模块=SoulMate 本地记忆引擎的准入判定纯函数层：

- auto_approved   保留：描述性记忆内容过全部规则（reason 必写，写明过了什么）
- rejected        拒绝：空内容 / 类凭据（记忆库不是密钥库）——不入库（reason 必写）
- deferred_review 延后：指令形内容（「忽略之前…」类）——入库但不进召回，
                  待 review_memory(verdict) 复核放行（review_reason 必写）

三岔动词全带书面理由（OMH「处置动词全带 reason」逐字纪律）；判定纯函数=
可测、无 IO、确定性。
"""

import re
from dataclasses import dataclass

DECISION_APPROVED = "auto_approved"
DECISION_REJECTED = "rejected"
DECISION_DEFERRED = "deferred_review"

# 类凭据形态（只报类型不抄原文进 reason——防二次泄露，memory_redact 同纪律）
_CREDENTIAL_PATTERNS = [
    (re.compile(r"(?i)\b(password|passwd|pwd)\s*[:=]"), "password_assignment"),
    (re.compile(r"(?i)\b(api[_-]?key|secret|access[_-]?token)\s*[:=]\s*\S"), "api_credential"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private_key_block"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-\.]{20,}"), "bearer_token"),
]

# 指令形摘要（窄规则集：祈使/指令前缀；宁漏勿错杀普通事实描述）
_INSTRUCTION_PATTERNS = [
    re.compile(r"^\s*(指令|规则|system|assistant|important)\s*[:：]", re.IGNORECASE),
    re.compile(r"(忽略|无视|忘掉)(之前|以上|前面)(的|所有)"),
    re.compile(r"^\s*(ignore|forget|disregard)\s+(all\s+|previous\s+|prior\s+)", re.IGNORECASE),
    re.compile(r"^\s*你(必须|应该|得)(遵|执|照)"),
]


@dataclass
class AdmissionDecision:
    """准入三岔判定产出（decision + 书面理由，审计落账的单一来源）。"""

    decision: str  # auto_approved / rejected / deferred_review
    rule: str  # 命中的规则名；auto_approved 为 "ok"
    reason: str  # 人类可读书面理由（OMH：非自动批准必写 review_reason）
    duplicate_of: str = ""  # 近重复既有记忆 id（OMH duplicate_of 章）；无=""

    @property
    def admitted(self) -> bool:
        return self.decision == DECISION_APPROVED

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "rule": self.rule,
            "reason": self.reason,
            "duplicate_of": self.duplicate_of,
        }


def evaluate_admission(content: str, duplicate_of: str = "") -> AdmissionDecision:
    """准入判定（纯函数）：空内容/类凭据→拒收；指令形→延后；其余→保留。

    duplicate_of 由调用方查重后传入（本函数不查库）：重复候选即使保留也在
    reason 盖 duplicate_of 章（OMH :462 抄件）。
    """
    text = (content or "").strip()
    if len(text) < 2:
        return AdmissionDecision(
            DECISION_REJECTED, "empty_content", "content shorter than 2 chars: nothing to remember"
        )
    for pat, rule in _CREDENTIAL_PATTERNS:
        if pat.search(text):
            return AdmissionDecision(
                DECISION_REJECTED,
                rule,
                f"credential-like content rejected ({rule}): memory store is not a secret store",
            )
    for pat in _INSTRUCTION_PATTERNS:
        if pat.search(text):
            return AdmissionDecision(
                DECISION_DEFERRED,
                "instruction_shaped",
                "instruction-shaped candidate deferred for review: a memory must not become "
                "an injected instruction (review_memory verdict=approve|reject)",
            )
    reason = "descriptive memory content passed admission rules"
    if duplicate_of:
        reason += f" (duplicate_of:{duplicate_of})"
    return AdmissionDecision(DECISION_APPROVED, "ok", reason, duplicate_of=duplicate_of)
