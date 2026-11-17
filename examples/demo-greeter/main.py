"""
demo-greeter — 插件机制 PoC 演示插件

跑通三件事：菜单长出来（plugin.yaml ui.nav）+ 页面渲染（ui/home.json UI DSL）
+ 一个工具可调（handle_greet，经 MCP 工具注册桥，UI 与对话共用）。
"""

from __future__ import annotations

import time
from typing import Any

# 内存问候记录（进程内演示用；真实插件应落 plugin-data/<id>/）
_GREET_LOG: list[dict[str, Any]] = []


def handle_greet(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：生成问候语并记入日志"""
    name = str(args["name"]).strip()
    style = str(args.get("style", "formal"))
    if style == "casual":
        greeting = f"嗨，{name}！今天也要加油鸭～"
    else:
        greeting = f"{name}，您好。"
    entry = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "style": style,
        "greeting": greeting,
    }
    _GREET_LOG.append(entry)
    ctx.logger.info("demo-greeter 生成问候: %s", greeting)
    return entry


def handle_greet_log(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：查询问候记录（list 面板数据源）"""
    limit = int(args.get("limit", 20))
    items = _GREET_LOG[-limit:]
    return {"items": items, "count": len(items)}


def handle_build_report(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：生成问候报告（生成文档类动作，approval=always 须人工确认）。

    流程引擎经「人工审批闸」放行后调用（approved token）；对话/UI 直接调用
    走工具级审批闸。报告落 ctx.store_dir()/reports.json（跨进程持久）。
    """
    import json

    title = str(args.get("title") or "问候报告").strip()
    greeting = str(args.get("greeting") or "").strip()
    fetched = int(args.get("fetched", 0) or 0)
    report = {
        "title": title,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "greeting": greeting,
        "records_seen": fetched,
        "total_logged": len(_GREET_LOG),
        "summary": f"{title}：{greeting or '（无问候）'}，参考记录 {fetched} 条。",
    }
    try:
        store_dir = ctx.store_dir()
        path = store_dir / "reports.json"
        reports = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        reports.append(report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
        report["stored_at"] = str(path)
    except Exception as e:  # noqa: BLE001 — 落盘失败显式报因，报告本体仍返回
        report["store_error"] = str(e)
    ctx.logger.info("demo-greeter 生成报告: %s", title)
    return report
