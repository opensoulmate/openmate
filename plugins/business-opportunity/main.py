"""
business-opportunity — 商机管理插件

菜单+页面+工具三件套：新增/列表/编辑/删除/统计。数据落
plugin-data/business-opportunity/opportunities.json（跨进程持久）。
UI 面板与 SoulMate 对话共用同一 handler。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

STAGES = ["初步接触", "需求确认", "方案报价", "谈判", "赢单", "输单"]


def _store_path(ctx: Any) -> Path:
    return Path(ctx.store_dir()) / "opportunities.json"


def _load(ctx: Any) -> list[dict[str, Any]]:
    path = _store_path(ctx)
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        ctx.logger.warning("商机数据读取失败: %s", e)
        return []


def _save(ctx: Any, items: list[dict[str, Any]]) -> None:
    path = _store_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def handle_add(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：新增商机"""
    stage = str(args.get("stage", "初步接触"))
    if stage not in STAGES:
        return {"error": f"非法销售阶段 {stage!r}，可选：{STAGES}"}
    try:
        amount = float(args["amount"])
    except (KeyError, TypeError, ValueError):
        return {"error": "预计金额必须是数字"}
    items = _load(ctx)
    entry = {
        "id": (max((i["id"] for i in items), default=0) + 1),
        "customer": str(args["customer"]).strip(),
        "title": str(args["title"]).strip(),
        "amount": amount,
        "stage": stage,
        "expected_date": str(args.get("expected_date") or ""),
        "owner": str(args.get("owner") or ""),
        "note": str(args.get("note") or ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not entry["customer"] or not entry["title"]:
        return {"error": "客户名称与商机名称不能为空"}
    items.append(entry)
    _save(ctx, items)
    ctx.logger.info("新增商机 #%s: %s", entry["id"], entry["title"])
    return entry


def handle_list(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：商机列表（可按阶段/负责人筛选）"""
    items = _load(ctx)
    stage = str(args.get("stage") or "")
    owner = str(args.get("owner") or "")
    if stage:
        items = [i for i in items if i["stage"] == stage]
    if owner:
        items = [i for i in items if i["owner"] == owner]
    limit = int(args.get("limit", 100))
    items = items[-limit:]
    return {"items": items, "count": len(items)}


def handle_update(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：编辑商机（按 id 更新可选字段）"""
    try:
        opp_id = int(args["id"])
    except (KeyError, TypeError, ValueError):
        return {"error": "id 必须是整数"}
    items = _load(ctx)
    target = next((i for i in items if i["id"] == opp_id), None)
    if target is None:
        return {"error": f"商机 #{opp_id} 不存在"}
    if "stage" in args and args["stage"] is not None:
        if str(args["stage"]) not in STAGES:
            return {"error": f"非法销售阶段 {args['stage']!r}，可选：{STAGES}"}
        target["stage"] = str(args["stage"])
    if "amount" in args and args["amount"] is not None:
        try:
            target["amount"] = float(args["amount"])
        except (TypeError, ValueError):
            return {"error": "预计金额必须是数字"}
    for key in ("expected_date", "owner", "note"):
        if key in args and args[key] is not None:
            target[key] = str(args[key])
    target["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _save(ctx, items)
    ctx.logger.info("更新商机 #%s", opp_id)
    return target


def handle_delete(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：删除商机（按 id）"""
    try:
        opp_id = int(args["id"])
    except (KeyError, TypeError, ValueError):
        return {"error": "id 必须是整数"}
    items = _load(ctx)
    kept = [i for i in items if i["id"] != opp_id]
    if len(kept) == len(items):
        return {"error": f"商机 #{opp_id} 不存在"}
    _save(ctx, kept)
    ctx.logger.info("删除商机 #%s", opp_id)
    return {"deleted": opp_id, "count": len(kept)}


def handle_stats(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：商机统计（各阶段数量与金额汇总）"""
    items = _load(ctx)
    by_stage = {s: {"count": 0, "amount": 0.0} for s in STAGES}
    for i in items:
        bucket = by_stage.setdefault(i["stage"], {"count": 0, "amount": 0.0})
        bucket["count"] += 1
        bucket["amount"] += float(i.get("amount") or 0)
    return {
        "total_count": len(items),
        "total_amount": sum(float(i.get("amount") or 0) for i in items),
        "by_stage": by_stage,
    }
