"""
invoice-flow — 发票报销插件（真实业务流，非样板）

装了就干活：plugin.yaml flows: 把 invoice-reimburse 流注册进流程引擎。
链路：发票进来（PDF/图片）→ 识别（二维码 + ocrmypdf/OCR，整合
dhcc-reimbursement 分类器）→ 登记（报销台账 status=待确认）→
human 节点人工确认（/tasks 收发台交付）→ commit 入台账（已确认/已驳回）。

工具与流程引擎 / UI / 对话共用同一 handler（MCP 工具注册桥）。
识别在 invoice_recognize.py，台账在 invoice_ledger.py（纯 Python）。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"plugin_ext_invoice_flow_{name}", _DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise RuntimeError(f"invoice-flow 模块无法加载: {name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_recognize = _load("invoice_recognize")
_ledger = _load("invoice_ledger")


def handle_recognize(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：识别发票（二维码 + ocrmypdf/OCR），返回结构化记录。"""
    records = _recognize.recognize_paths(
        file=str(args.get("file") or ""), dir=str(args.get("dir") or ""))
    ctx.logger.info("invoice-flow 识别 %d 张发票", len(records))
    return {"records": records, "count": len(records)}


def handle_register(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：识别结果登记入账（status=待确认，同号码去重）。"""
    records = args.get("records")
    if not isinstance(records, list):
        raise ValueError("records 应为识别记录列表（$.recognize.records）")
    result = _ledger.register(
        records, run_id=str(args.get("run_id") or ""), note=str(args.get("note") or ""))
    result["confirm_text"] = _ledger.render_confirm_text(result)
    ctx.logger.info("invoice-flow 登记 %d 张（重复 %d）",
                    result["count"], len(result["duplicates"]))
    return result


def handle_commit(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：人工确认回流入台账（approve=已确认 / reject=已驳回）。"""
    entry_ids = args.get("entry_ids")
    if not isinstance(entry_ids, list):
        raise ValueError("entry_ids 应为登记条目 id 列表（$.register.entry_ids）")
    decision, note = _parse_confirmation(args.get("confirmation"))
    result = _ledger.confirm(
        [str(i) for i in entry_ids], decision,
        note=note, confirmed_by=str(args.get("confirmed_by") or "用户"))
    summary = _ledger.summary()
    ctx.logger.info("invoice-flow 入账: decision=%s updated=%d",
                    decision, len(result["updated"]))
    return {"decision": decision, "updated": result["updated"],
            "missing": result["missing"], "ledger": summary}


def handle_ledger(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：报销台账查询（最近条目 + 汇总，只读）。"""
    return _ledger.query(limit=int(args.get("limit") or 20))


def _parse_confirmation(confirmation: Any) -> tuple[str, str]:
    """人工交付物 → (decision, note)：认 dict/字符串，驳回显式判词才驳回。"""
    note = ""
    if isinstance(confirmation, dict):
        decision = str(confirmation.get("decision") or "").lower()
        note = str(confirmation.get("note") or "")
        if decision in ("reject", "rejected", "驳回"):
            return "reject", note or str(confirmation)
        return "approve", note
    text = str(confirmation or "")
    if any(word in text for word in ("驳回", "reject")):
        return "reject", text
    return "approve", text
