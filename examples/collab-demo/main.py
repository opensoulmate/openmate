"""
协作流程演示 — 示例插件工具面（确定性计算，供示例流程取数节点调用）。
"""
from __future__ import annotations

from typing import Any


def handle_collect_summary(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """汇总数字列表：条数 + 合计 + 均值（确定性，可断言）。"""
    numbers = args.get("numbers") or []
    numbers = [float(x) for x in numbers if isinstance(x, (int, float))]
    total = sum(numbers)
    return {
        "count": len(numbers),
        "total": total,
        "average": (total / len(numbers)) if numbers else 0.0,
    }
