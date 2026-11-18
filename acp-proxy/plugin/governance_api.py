"""
MCP 治理观察面 HTTP API — 策略快照 + 调用审计台账（只读）

路由前缀: /governance
- GET /governance/policies   Catalog 风险分级 + Profile/Binding/Policy 快照
- GET /governance/audit      调用审计台账（F2：谁/节点/参数脱敏摘要/判定/结果/耗时）
配置面走数据文件（TOOL_GOVERNANCE_FILE），观察面只看不动（与 /api/pulse 同纪律）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from plugin.tool_governance import RISK_LEVELS, get_governance


def build_governance_router(governance=None) -> APIRouter:
    governance = governance or get_governance()
    router = APIRouter(prefix="/governance", tags=["tool-governance"])

    @router.get("/policies")
    async def policies() -> dict[str, Any]:
        snapshot = governance.snapshot()
        snapshot["risk_levels"] = list(RISK_LEVELS)
        snapshot["audit_file"] = str(governance.audit_file)
        return snapshot

    @router.get("/audit")
    async def audit(limit: int = Query(100, ge=1, le=500)) -> dict[str, Any]:
        events = governance.read_audit(limit=limit)
        return {"events": events, "count": len(events)}

    return router
