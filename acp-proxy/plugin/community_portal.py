# -*- coding: utf-8 -*-
"""社区门户接口（插件市场 step 3——留接口，不擅选外部服务）。

社区侧的评分、签名信任链、沙箱审计需要外部基础设施/托管选型（拍板项）。
本模块只定义**接口契约 + 未配置空实现**：任何托管选型后续以实现
CommunityPortal 协议接入，仓库（PluginRepo）已预留挂点（publish/install
可携带 audit/verify_signature 结果），不在本层拍板选型。

约定：verify_signature 返回 None = 未校验（不拦，只如实呈现）；
audit 返回 {"configured": False, "skipped": True} = 未配置审计（同样如实呈现）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# 社区门户能力清单（接口面，不绑定具体服务）
COMMUNITY_CAPABILITIES = ("rating", "reviews", "signatures", "sandbox-audit")


@runtime_checkable
class CommunityPortal(Protocol):
    """社区门户协议——评分/评价/签名信任链/沙箱审计。"""

    def status(self) -> dict[str, Any]:
        """门户配置现状：{configured, capabilities, note}"""
        ...

    def rate(self, plugin_id: str, version: str, rating: int,
             review: str = "") -> dict[str, Any]:
        """评分/评价上传（rating 1-5）。"""
        ...

    def reviews(self, plugin_id: str, version: str = "") -> dict[str, Any]:
        """评价列表。"""
        ...

    def verify_signature(self, zip_path: Path,
                         metadata: dict[str, Any]) -> bool | None:
        """签名校验：True=通过 / False=不通过 / None=未校验（不拦，如实呈现）。"""
        ...

    def audit(self, zip_path: Path) -> dict[str, Any]:
        """沙箱审计（发布/安装前）。"""
        ...


class NullCommunityPortal:
    """未配置外部服务的空实现——接口齐全、行为透明，绝不假装已接入。"""

    def status(self) -> dict[str, Any]:
        return {
            "configured": False,
            "capabilities": list(COMMUNITY_CAPABILITIES),
            "note": "社区门户未配置外部服务（拍板项）：评分/签名信任链/沙箱审计留接口，"
                    "实现 CommunityPortal 协议即可接入。",
        }

    def rate(self, plugin_id: str, version: str, rating: int,
             review: str = "") -> dict[str, Any]:
        return {"configured": False, "skipped": True,
                "detail": f"评分未送达（社区门户未配置）：{plugin_id}@{version}={rating}"}

    def reviews(self, plugin_id: str, version: str = "") -> dict[str, Any]:
        return {"configured": False, "skipped": True, "reviews": []}

    def verify_signature(self, zip_path: Path,
                         metadata: dict[str, Any]) -> bool | None:
        return None                       # 未校验（不拦，如实呈现）

    def audit(self, zip_path: Path) -> dict[str, Any]:
        return {"configured": False, "skipped": True}
