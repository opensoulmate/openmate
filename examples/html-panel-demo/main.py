# -*- coding: utf-8 -*-
"""HTML面板样板 entry（层②验收样板，不进运行时）。

面板动作经宿主 postMessage RPC 面（POST /plugins/html-panel-demo/html-rpc）
调用本文件的 handler——与 DSL 面同一工具注册桥、同一审批闸。
"""


def handle_add(args, ctx):
    return {"sum": float(args.get("a") or 0) + float(args.get("b") or 0)}


def handle_reset(args, ctx):
    return {"reset": True}
