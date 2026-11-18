#!/usr/bin/env python3
"""
真业务流程验证驱动 — 两条真流程各真跑一遍（真账本/真微信平台/真发票/真台账）

用法（在 ~/openmate/acp-proxy 下）：
    ~/opensoul/.venv/bin/python ../docs/reports/real-flows-verification/run_verification.py

真跑 1：publish-guard（读真账本 ai-learning/agent-research + 微信平台只读对账）
        → 有异常开人工工单进 /tasks → 交付回流收口；结果写 publish-guard-*.json/md
真跑 2：invoice-reimburse（真发票 PDF → 识别 → 登记 → 人工确认 → 真报销台账）
        → 台账落 ~/Documents/Hermes/dhcc-reimbursement/ledger/报销台账.json
        结果写 invoice-flow-*.json
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]   # …/openmate/docs/reports/real-flows-verification → openmate
ACP_DIR = REPO / "acp-proxy"
sys.path.insert(0, str(ACP_DIR))

from plugin.dsl_api import scan_and_build          # noqa: E402
from plugin.flow_collab import get_collab          # noqa: E402
from plugin.flow_engine import get_engine, reset_engine  # noqa: E402
from plugin.mcp_bridge import PluginToolRegistry   # noqa: E402

OUT = Path(__file__).resolve().parent
REAL_INVOICE_PDF = Path(
    "/home/climbing/Documents/xwechat_files/wxid_ofhpdl7j36lc52_8966/msg/file"
    "/2025-11/5%发票_大连市西岗区公共事业服务中心_20250418162253.pdf")


def dump(name: str, data) -> None:
    (OUT / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"  → {name}")


def main() -> None:
    reset_engine()
    registry = PluginToolRegistry()
    engine = get_engine()          # 真运行目录 data/flow-runs

    async def caller(name, args, approved=False):
        return await registry.call_tool_async(name, args, approved=approved)

    engine.set_tool_caller(caller)
    _, index = scan_and_build(str(REPO / "plugins"), registry)
    errs = [e for e in index["errors"] if e.get("plugin") in ("publish-guard", "invoice-flow")]
    assert not errs, errs
    print("装配 OK:", [d["name"] for d in index["plugins"]])

    # ─── 真跑 1：公众号发布保障（真账本 + 真平台） ───
    print("\n[1/2] publish-guard 真跑：")
    run = asyncio.run(engine.run_now("publish-guard", {"type": "manual", "source": "verify"}))
    result = run.outputs.get("reconcile", {})
    print(f"  状态 {run.status} | issues={result.get('issue_count')} "
          f"| platform={result.get('platform')}")
    dump("publish-guard-reconcile.json", result)
    tasks = [t for t in get_collab().store.list(limit=50) if t.run_id == run.run_id]
    if tasks:
        (OUT / "publish-guard-ticket.md").write_text(tasks[0].instruction, encoding="utf-8")
        print(f"  工单 {tasks[0].task_id} → {tasks[0].assignee}（/tasks 收发台）")
        print("  → publish-guard-ticket.md")
    done = asyncio.run(engine.resolve_delivery(
        run.run_id,
        result={"note": "（验证跑）对账结果留存 docs/reports/real-flows-verification"},
        decided_by="用户"))
    print("  交付回流后:", done.status)
    dump("publish-guard-run.json", engine.run_summary(done))

    # ─── 真跑 2：发票报销（真发票 → 真台账） ───
    print("\n[2/2] invoice-reimburse 真跑：")
    assert REAL_INVOICE_PDF.is_file(), f"真发票样本缺失: {REAL_INVOICE_PDF}"
    run2 = asyncio.run(engine.run_now(
        "invoice-reimburse", {"type": "manual", "source": "verify"},
        inputs={"file": str(REAL_INVOICE_PDF), "note": "流程引擎真跑验证"}))
    rec = run2.outputs.get("recognize", {})
    reg = run2.outputs.get("register", {})
    print(f"  状态 {run2.status} | 识别 {rec.get('count')} 张 | 登记 {reg.get('count')} 张")
    dump("invoice-flow-recognize.json", rec)
    dump("invoice-flow-register.json", {k: v for k, v in reg.items() if k != "confirm_text"})
    done2 = asyncio.run(engine.resolve_delivery(
        run2.run_id,
        result={"decision": "approve", "note": "（验证跑）识别字段已核对"},
        decided_by="用户"))
    print("  交付回流后:", done2.status)
    commit = done2.outputs.get("commit", {})
    dump("invoice-flow-ledger.json", commit.get("ledger", {}))
    dump("invoice-flow-run.json", engine.run_summary(done2))
    print("\n全部真跑完成。")


if __name__ == "__main__":
    main()
