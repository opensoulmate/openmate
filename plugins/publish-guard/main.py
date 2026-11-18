"""
publish-guard — 公众号发布保障插件（真实业务流，非样板）

装了就干活：plugin.yaml flows: 把 publish-guard 流注册进流程引擎，
cron 每晚 21:30 自动对账两本真账本（ai-learning / agent-research）vs
微信平台实际发布，检出漏发/封面未挂/待确认积压 → human 节点开人工工单
进 /tasks 收发台并催办（复用 flow_collab 工单账，不重造）。

工具 reconcile 与流程引擎 / UI / 对话共用同一 handler（MCP 工具注册桥）。
对账逻辑在 reconcile.py（纯 Python），平台只读接口在 wechat_platform.py。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"plugin_ext_publish_guard_{name}", _DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"publish-guard 模块无法加载: {name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_reconcile = _load("reconcile")


def handle_reconcile(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：账本 vs 实际发布对账（只读账本 + 只读平台，绝不改写）。"""
    window_days = int(args.get("window_days") or 3)
    check_platform = bool(args.get("check_platform", True))
    result = _reconcile.run_reconcile(
        window_days=window_days, check_platform=check_platform)
    ctx.logger.info(
        "publish-guard 对账完成: issues=%d platform=%s",
        result["issue_count"], result["platform"])
    return result
