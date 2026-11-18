"""
报销台账 — 发票登记/人工确认/台账落账（纯 Python，原子写）

生命周期：register 登记（status=待确认）→ human 节点人工确认 →
commit 确认入账（status=已确认）/ 驳回（status=已驳回）。

台账落盘：$INVOICE_LEDGER_FILE（测试隔离）→ 缺省
~/Documents/Hermes/dhcc-reimbursement/ledger/报销台账.json
（整合 dhcc-reimbursement 既有报销工作区，账本与既有报销单一处归口）。
去重：同发票号码不重复登记（东华平台 'repeat' 同语义）。
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

STATUS_PENDING = "待确认"
STATUS_CONFIRMED = "已确认"
STATUS_REJECTED = "已驳回"

DEFAULT_LEDGER = "~/Documents/Hermes/dhcc-reimbursement/ledger/报销台账.json"


def ledger_path() -> Path:
    return Path(os.environ.get("INVOICE_LEDGER_FILE") or DEFAULT_LEDGER).expanduser()


def _load() -> dict[str, Any]:
    path = ledger_path()
    if not path.is_file():
        return {"entries": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"entries": []}
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return {"entries": []}
    return data


def _save(data: dict[str, Any]) -> None:
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str),
                   encoding="utf-8")
    tmp.replace(path)


def register(records: list[dict[str, Any]], *, run_id: str = "",
             note: str = "") -> dict[str, Any]:
    """登记入账（status=待确认）：同发票号码去重（重复=幂等跳过，不重登）。"""
    data = _load()
    existing = {str(e.get("发票号码", "")) for e in data["entries"] if e.get("发票号码")}
    registered: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    now = time.time()
    for record in records or []:
        number = str(record.get("发票号码") or "").strip()
        entry = {
            "id": f"inv_{uuid.uuid4().hex[:10]}",
            "发票号码": number,
            "开票日期": record.get("开票日期", ""),
            "价税合计": record.get("价税合计"),
            "销售方": record.get("销售方", ""),
            "商品名称": record.get("商品名称", ""),
            "分类": record.get("分类", ""),
            "报销类别": record.get("报销类别"),
            "校验码": record.get("校验码", ""),
            "识别方式": record.get("识别方式", ""),
            "原始文件": record.get("原始文件", ""),
            "warnings": list(record.get("warnings") or []),
            "status": STATUS_PENDING,
            "registered_at": now,
            "registered_by": run_id,
            "note": note,
        }
        if number and number in existing:
            duplicates.append({"发票号码": number, "原因": "已登记（号码重复）"})
            continue
        data["entries"].append(entry)
        if number:
            existing.add(number)
        registered.append(entry)
    _save(data)
    return {
        "registered": registered,
        "duplicates": duplicates,
        "entry_ids": [e["id"] for e in registered],
        "count": len(registered),
    }


def confirm(entry_ids: list[str], decision: str = "approve", *, note: str = "",
            confirmed_by: str = "") -> dict[str, Any]:
    """人工确认回流：approve → 已确认入台账；reject → 已驳回留档。"""
    data = _load()
    updated: list[dict[str, Any]] = []
    missing: list[str] = []
    status = STATUS_CONFIRMED if decision == "approve" else STATUS_REJECTED
    now = time.time()
    wanted = {str(i) for i in (entry_ids or [])}
    for entry in data["entries"]:
        if entry.get("id") not in wanted:
            continue
        entry["status"] = status
        entry["confirmed_at"] = now
        entry["confirmed_by"] = confirmed_by
        if note:
            entry["confirm_note"] = note
        updated.append(entry)
    found = {e["id"] for e in updated}
    missing = [i for i in wanted if i not in found]
    _save(data)
    return {"updated": updated, "missing": missing, "decision": decision}


def summary() -> dict[str, Any]:
    """报销台账汇总：张数/金额/分类（只统计已确认=正式台账口径）。"""
    data = _load()
    entries = data["entries"]
    confirmed = [e for e in entries if e.get("status") == STATUS_CONFIRMED]
    pending = [e for e in entries if e.get("status") == STATUS_PENDING]
    rejected = [e for e in entries if e.get("status") == STATUS_REJECTED]

    def _sum(items: list[dict[str, Any]]) -> float:
        return round(sum(float(e.get("价税合计") or 0) for e in items), 2)

    by_category: dict[str, dict[str, Any]] = {}
    for entry in confirmed:
        key = str(entry.get("分类") or "未分类")
        bucket = by_category.setdefault(key, {"张数": 0, "金额": 0.0})
        bucket["张数"] += 1
        bucket["金额"] = round(bucket["金额"] + float(entry.get("价税合计") or 0), 2)
    return {
        "已确认张数": len(confirmed),
        "已确认金额": _sum(confirmed),
        "待确认张数": len(pending),
        "待确认金额": _sum(pending),
        "已驳回张数": len(rejected),
        "按分类": by_category,
        "台账文件": str(ledger_path()),
    }


def query(limit: int = 20) -> dict[str, Any]:
    data = _load()
    entries = sorted(data["entries"], key=lambda e: e.get("registered_at") or 0,
                     reverse=True)
    return {"entries": entries[:max(1, int(limit))], "summary": summary()}


def render_confirm_text(result: dict[str, Any]) -> str:
    """登记结果 → 人工确认工单正文（逐张列关键字段，缺字段点名待补）。"""
    lines = [f"发票登记确认：新登记 {result['count']} 张"
             + (f"，重复跳过 {len(result['duplicates'])} 张" if result["duplicates"] else ""),
             ""]
    for n, entry in enumerate(result["registered"], 1):
        amount = entry.get("价税合计")
        lines.append(
            f"{n}. {entry.get('发票号码') or '（号码缺失）'} "
            f"{entry.get('开票日期') or '?'} ¥{amount if amount is not None else '?'} "
            f"{entry.get('销售方') or '?'} / {entry.get('商品名称') or '?'} "
            f"→ {entry.get('分类') or '?'}({entry.get('报销类别') or '差旅单'}) "
            f"[{entry.get('识别方式', '')}]")
        for warning in (entry.get("warnings") or []):
            lines.append(f"   ⚠ {warning}（请补全/核对后交付）")
    for dup in result["duplicates"]:
        lines.append(f"- 跳过 {dup['发票号码']}：{dup['原因']}")
    lines += ["", "交付确认：逐张核对发票号码/金额/日期/分类。"
              "通过=入报销台账（可附 {" + '"decision": "approve"' + "}）；"
              "驳回请给 {" + '"decision": "reject", "note": "原因"' + "} 或直接写明驳回原因。"]
    return "\n".join(lines)
