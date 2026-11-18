"""
MCP 工具治理网关 — Profile / Binding / Policy 三件套（Paperclip MCP-ACCESS-GOVERNANCE 对位）

F1 策略层（flow_engine tool 节点/MCP 调用前过闸，deny 优先）：
- Catalog：工具风险分级（read | write | destructive），数据文件驱动（未分级按
  write 中危对待）
- Profile：allow/deny 白名单包（模式匹配：精确或前缀 `xxx__*`）
- Binding：Profile 绑 scope（company | flow:<id> | role:<id> | node:<id>）
- Policy：调用时动作 allow | block | require_approval | rate_limit
  判定序（deny 优先）：profile deny → policy block → require_approval（未批）
  → rate_limit 超限 → allow

F2 风险分级+审计：每次调用落 CallEvent（谁/哪个流程节点/参数摘要（密码脱敏）/
判定/结果/耗时）进滚动审计台账（模型无法伪造，排查有据）。

require_approval 转人工（真闸，F1 怎么抄）：未批 → ToolGovernanceNeedsApproval
→ 流程侧转 human 审批放行（tool-gate 挂起原地续跑）→ grant_waiver 一次性豁免。
rate_limit：窗口计数超限 → block（节点 on_error 语义处置），不杀在跑执行。

未配置零行为变化：无策略/无绑定时一律 allow（整合优先，不预设拦截）。
数据面：TOOL_GOVERNANCE_FILE（JSON：catalog/profiles/bindings/policies）+
TOOL_RISK_CATALOG（JSON：tool→risk）；审计台账 TOOL_CALL_AUDIT_FILE（缺省
data/tool-call-audit.json，滚动 500 条）。
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("plugin.tool_governance")

RISK_LEVELS = ("read", "write", "destructive")
DEFAULT_RISK = "write"          # 未分级按中危对待（谨慎原则）
ACTIONS = ("allow", "block", "require_approval", "rate_limit")
AUDIT_MAX = 500
SCOPE_KINDS = ("company", "flow", "role", "node")


class ToolGovernanceBlocked(RuntimeError):
    """策略拦截（block/profile-deny/rate_limit）：重试无意义，按节点 on_error 处置"""


class ToolGovernanceNeedsApproval(RuntimeError):
    """策略要求人工放行（require_approval）：流程侧转 human 审批闸"""


@dataclass
class GovernanceProfile:
    """工具白名单包（allow/deny 模式；deny 优先）。"""
    name: str
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()


@dataclass
class ToolPolicy:
    """调用时策略（Policy）：action × tool 模式 × 风险级 × scope。"""
    policy_id: str
    action: str                       # allow | block | require_approval | rate_limit
    tool: str = "*"                   # 精确名或前缀 `xxx__*`
    risk: str = ""                    # 只作用于该风险级（空=所有）
    scope: str = "company"            # company | flow:<id> | role:<id> | node:<id>
    limit_per_hour: int = 0           # rate_limit 用（0=不限）

    def matches(self, tool: str, risk: str, flow_id: str, role: str,
                node_id: str) -> bool:
        if not _tool_match(self.tool, tool):
            return False
        if self.risk and self.risk != risk:
            return False
        return _scope_match(self.scope, flow_id, role, node_id)


@dataclass
class Decision:
    action: str
    reason: str
    policy_id: str = ""
    risk: str = DEFAULT_RISK

    @property
    def allowed(self) -> bool:
        return self.action == "allow"


def _tool_match(pattern: str, tool: str) -> bool:
    """工具模式匹配：`*` 全部 / `prefix*` 前缀 / 精确名。"""
    if pattern in ("*", ""):
        return True
    if pattern.endswith("*"):
        return tool.startswith(pattern[:-1])
    return tool == pattern


def _scope_match(scope: str, flow_id: str, role: str, node_id: str) -> bool:
    if scope in ("company", "*", ""):
        return True
    kind, _, key = scope.partition(":")
    if kind == "flow":
        return bool(flow_id) and key == flow_id
    if kind == "role":
        return bool(role) and key == role
    if kind == "node":
        return bool(node_id) and key == node_id
    return False


class ToolGovernance:
    """治理网关：catalog + profiles/bindings + policies + rate 计数 + 审计台账。"""

    def __init__(self, audit_file: Path | str | None = None, clock=time.time):
        self._clock = clock
        self._lock = threading.Lock()
        self._catalog: dict[str, str] = {}
        self._profiles: dict[str, GovernanceProfile] = {}
        self._bindings: dict[str, list[str]] = {}   # scope → [profile name]
        self._policies: list[ToolPolicy] = []
        self._rate: dict[tuple[str, int], int] = {}  # (tool, 窗口起点) → 计数
        self._waivers: set[str] = set()
        env_audit = os.environ.get("TOOL_CALL_AUDIT_FILE")
        self.audit_file = Path(audit_file or env_audit
                               or Path(__file__).resolve().parent.parent
                               / "data" / "tool-call-audit.json")

    # -- 配置面 --

    def load_file(self, path: Path | str) -> dict[str, int]:
        """装载治理配置 JSON（catalog/profiles/bindings/policies），增量叠加。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        counts = {"catalog": 0, "profiles": 0, "bindings": 0, "policies": 0}
        for tool, risk in (data.get("catalog") or {}).items():
            self.set_risk(tool, str(risk))
            counts["catalog"] += 1
        for item in data.get("profiles") or []:
            self.add_profile(GovernanceProfile(
                name=str(item.get("name") or ""),
                allow=tuple(item.get("allow") or ()),
                deny=tuple(item.get("deny") or ())))
            counts["profiles"] += 1
        for item in data.get("bindings") or []:
            self.bind(str(item.get("scope") or "company"),
                      str(item.get("profile") or ""))
            counts["bindings"] += 1
        for item in data.get("policies") or []:
            self.add_policy(ToolPolicy(
                policy_id=str(item.get("id") or item.get("policy_id") or ""),
                action=str(item.get("action") or "allow"),
                tool=str(item.get("tool") or "*"),
                risk=str(item.get("risk") or ""),
                scope=str(item.get("scope") or "company"),
                limit_per_hour=int(item.get("limit_per_hour") or 0)))
            counts["policies"] += 1
        return counts

    def set_risk(self, tool: str, risk: str) -> None:
        if risk not in RISK_LEVELS:
            raise ValueError(f"风险级非法: {risk!r}（{sorted(RISK_LEVELS)}）")
        self._catalog[tool] = risk

    def risk_of(self, tool: str) -> str:
        if tool in self._catalog:
            return self._catalog[tool]
        for pattern, risk in self._catalog.items():
            if _tool_match(pattern, tool):
                return risk
        return DEFAULT_RISK

    def add_profile(self, profile: GovernanceProfile) -> GovernanceProfile:
        self._profiles[profile.name] = profile
        return profile

    def bind(self, scope: str, profile_name: str) -> None:
        if profile_name not in self._profiles:
            raise ValueError(f"绑定的 Profile 不存在: {profile_name!r}")
        self._bindings.setdefault(scope, [])
        if profile_name not in self._bindings[scope]:
            self._bindings[scope].append(profile_name)

    def add_policy(self, policy: ToolPolicy) -> ToolPolicy:
        if policy.action not in ACTIONS:
            raise ValueError(f"Policy.action 非法: {policy.action!r}（{sorted(ACTIONS)}）")
        self._policies.append(policy)
        return policy

    def snapshot(self) -> dict[str, Any]:
        return {
            "catalog": dict(self._catalog),
            "profiles": [asdict(p) for p in self._profiles.values()],
            "bindings": dict(self._bindings),
            "policies": [asdict(p) for p in self._policies],
        }

    # -- 放行一次性豁免（require_approval 转人工后）--

    def grant_waiver(self, gate_key: str) -> None:
        self._waivers.add(gate_key)

    def take_waiver(self, gate_key: str) -> bool:
        if gate_key in self._waivers:
            self._waivers.discard(gate_key)
            return True
        return False

    # -- 判定（deny 优先）--

    def evaluate(self, tool: str, *, flow_id: str = "", role: str = "",
                 node_id: str = "", approved: bool = False,
                 now: float | None = None) -> Decision:
        now = self._clock() if now is None else now
        risk = self.risk_of(tool)
        # ① profile deny（绑定 scope 的白名单包，deny 优先）
        for scope in _scopes(flow_id, role, node_id):
            for name in self._bindings.get(scope, []):
                profile = self._profiles.get(name)
                if profile is None:
                    continue
                for pattern in profile.deny:
                    if _tool_match(pattern, tool):
                        return Decision("block", f"profile-deny:{profile.name}:{pattern}",
                                        profile.name, risk)
        # ② policy block
        for policy in self._policies:
            if policy.action == "block" and policy.matches(tool, risk, flow_id, role, node_id):
                return Decision("block", f"policy-block:{policy.policy_id}",
                                policy.policy_id, risk)
        # ③ require_approval（未批 → 转人工；approved token / waiver 放行）
        for policy in self._policies:
            if policy.action == "require_approval" and not approved \
                    and policy.matches(tool, risk, flow_id, role, node_id):
                return Decision("require_approval",
                                f"policy-require_approval:{policy.policy_id}（需人工放行）",
                                policy.policy_id, risk)
        # ④ rate_limit（窗口计数超限 → block）
        for policy in self._policies:
            if policy.action == "rate_limit" and policy.limit_per_hour > 0 \
                    and policy.matches(tool, risk, flow_id, role, node_id):
                window = int(now // 3600)
                key = (f"{policy.policy_id}:{tool}", window)
                with self._lock:
                    used = self._rate.get(key, 0)
                    if used >= policy.limit_per_hour:
                        return Decision("block",
                                        f"rate_limit:{policy.policy_id} "
                                        f"({used}/{policy.limit_per_hour} 每小时)",
                                        policy.policy_id, risk)
                    self._rate[key] = used + 1
        return Decision("allow", "pass", "", risk)

    # -- F2 调用审计（滚动台账，模型无法伪造）--

    def record_call(self, *, tool: str, decision: Decision, ok: bool,
                    error: str = "", duration_ms: float = 0.0,
                    args: dict[str, Any] | None = None,
                    ctx: dict[str, Any] | None = None,
                    now: float | None = None) -> dict[str, Any]:
        try:
            from flow_receipts import mask_params
            safe_args = mask_params(dict(args or {}))
        except Exception:  # noqa: BLE001 — 脱敏失败按全量截断兜底
            safe_args = {k: str(v)[:40] for k, v in dict(args or {}).items()
                         if isinstance(k, str)}
        event = {
            "ts": self._clock() if now is None else now,
            "tool": tool,
            "risk": decision.risk,
            "action": decision.action,
            "reason": decision.reason,
            "policy_id": decision.policy_id,
            "flow_id": (ctx or {}).get("flow_id", ""),
            "node_id": (ctx or {}).get("node_id", ""),
            "run_id": (ctx or {}).get("run_id", ""),
            "args": safe_args,
            "ok": ok,
            "error": str(error)[:300],
            "duration_ms": round(duration_ms, 1),
        }
        self._append_audit(event)
        return event

    def _append_audit(self, event: dict[str, Any]) -> None:
        try:
            with self._lock:
                events = self._read_audit()
                events.append(event)
                events = events[-AUDIT_MAX:]
                self.audit_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.audit_file.with_suffix(".tmp")
                tmp.write_text(json.dumps(events, ensure_ascii=False, indent=1),
                               encoding="utf-8")
                os.replace(tmp, self.audit_file)
        except Exception as e:  # noqa: BLE001 — 审计落盘失败不阻断调用
            logger.warning("工具调用审计落盘失败: %s", e)

    def read_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._read_audit()[-max(1, limit):]

    def _read_audit(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.audit_file.read_text(encoding="utf-8"))
            return [e for e in data if isinstance(e, dict)][-AUDIT_MAX:]
        except Exception:
            return []


def _scopes(flow_id: str, role: str, node_id: str) -> list[str]:
    scopes = ["company"]
    if flow_id:
        scopes.append(f"flow:{flow_id}")
    if role:
        scopes.append(f"role:{role}")
    if node_id:
        scopes.append(f"node:{node_id}")
    return scopes


# ---- 进程级单例 ----

_governance: ToolGovernance | None = None
_gov_lock = threading.Lock()


def get_governance() -> ToolGovernance:
    global _governance
    with _gov_lock:
        if _governance is None:
            _governance = ToolGovernance()
            config = os.environ.get("TOOL_GOVERNANCE_FILE")
            if config and Path(config).is_file():
                try:
                    counts = _governance.load_file(config)
                    logger.info("工具治理配置已装载: %s (%s)", config, counts)
                except Exception as e:  # noqa: BLE001 — 坏配置显式报因不静默
                    logger.warning("工具治理配置装载失败(按无策略放行): %s", e)
        return _governance


def reset_governance() -> None:
    """测试用：重置单例（环境变量改后生效）。"""
    global _governance
    with _gov_lock:
        _governance = None
