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
