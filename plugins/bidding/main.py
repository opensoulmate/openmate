"""
招投标插件 — MCP 工具入口（plugin.yaml 清单式，照 demo-greeter 形状）

设计文档: docs/plugin-system-design.md §8（招投标插件规格）
四个核心工具（对话与 UI 共用同一 handler）：
  bid_parse   解析招标文件 → 结构化关键信息
  bid_generate 编排→逐节生成标书 Word→一致性审计（python-docx，断点续跑）
  bid_review  废标风险检查 🔴🟡🟢
  price_calc  报价策略（最低价法/平均价法/线性插值）
辅助工具（UI DSL 数据源）：bid_score_config / bid_generate_status / bid_knowledge_list

确定性引擎在 bid_engine.py（无 LLM 依赖，真实招标文件可离线跑通）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# 自包含加载同目录引擎（entry 由 scanner 以独立模块载入，不能用包内相对导入）
_DIR = Path(__file__).resolve().parent
if str(_DIR) not in sys.path:
    sys.path.insert(0, str(_DIR))

import bid_engine as E  # noqa: E402


# ── 参数小工具（表单字段为字符串，这里归一化为引擎所需类型）──────────────────


def _s(args: dict[str, Any], key: str, default: str = "") -> str:
    val = args.get(key)
    if val is None:
        return default
    return str(val).strip()


def _num(args: dict[str, Any], key: str) -> float | None:
    val = args.get(key)
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _opt_num(args: dict[str, Any], key: str) -> float | None:
    """可选数值：表单 number 组件把空值转成 0，这里把 0/空 一并视为未填。"""
    val = _num(args, key)
    if val is None or val == 0:
        return None
    return val


def _num_list(args: dict[str, Any], key: str) -> list[float]:
    raw = args.get(key)
    if raw is None:
        return []
    if isinstance(raw, list):
        out = []
        for x in raw:
            try:
                out.append(float(x))
            except (TypeError, ValueError):
                continue
        return out
    out = []
    for token in str(raw).replace("，", ",").replace("、", ",").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            out.append(float(token))
        except ValueError:
            continue
    return out


def _str_list(args: dict[str, Any], key: str) -> list[str]:
    raw = args.get(key)
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [t.strip() for t in str(raw).replace("，", ",").replace("、", ",").split(",") if t.strip()]


# ── 核心工具 handler ────────────────────────────────────────────────────────


def handle_bid_parse(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """解析招标文件 → 结构化关键信息（项目/评分办法/废标条款/资格/技术参数）。"""
    file_path = _s(args, "file_path")
    if not file_path:
        return {"error": "缺少招标文件路径 file_path"}
    try:
        result = E.parse_tender(file_path)
    except Exception as e:  # noqa: BLE001
        return {"error": f"解析失败: {e}", "file_path": file_path}
    pi = result.get("project_info", {})
    if _s(args, "save_to_kb") in ("true", "True", "1", "yes") or args.get("save_to_kb") is True:
        E.knowledge_index([file_path])
    ctx.logger.info("bid_parse: %s → 评分 %d 项，废标条款 %d 条",
                    pi.get("project_name"), len(result.get("scoring", [])), len(result.get("rejection_clauses", [])))
    # 展平关键字段便于 report kv 展示；结构化明细放同名列表
    return {
        "source_file": result["source_file"],
        "project_name": pi.get("project_name"),
        "project_code": pi.get("project_code"),
        "budget": pi.get("budget"),
        "budget_text": pi.get("budget_text"),
        "purchaser": pi.get("purchaser"),
        "agency": pi.get("agency"),
        "method": pi.get("method"),
        "location": pi.get("location"),
        "deadline": pi.get("deadline"),
        "bid_open_time": pi.get("bid_open_time"),
        "duration": pi.get("duration"),
        "quality": pi.get("quality"),
        "fund_source": pi.get("fund_source"),
        "bid_validity": pi.get("bid_validity"),
        "margin": pi.get("margin"),
        "evaluation_method": result.get("evaluation_method"),
        "scoring_count": len(result.get("scoring", [])),
        "rejection_count": len(result.get("rejection_clauses", [])),
        "qualification_count": len(result.get("qualification", [])),
        "params_count": len(result.get("params", [])),
        "scoring": result.get("scoring", []),
        "rejection_clauses": result.get("rejection_clauses", []),
        "qualification": result.get("qualification", []),
    }


def handle_bid_generate(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """编排→逐节生成标书 Word→一致性审计→断点续跑。"""
    file_path = _s(args, "file_path")
    if not file_path:
        return {"error": "缺少招标文件路径 file_path"}
    company = _s(args, "company") or "投标人"
    output_dir = _s(args, "output_dir") or None
    resume = args.get("resume")
    resume_bool = True if resume is None else bool(resume)
    try:
        result = E.generate_bid(file_path, company=company, output_dir=output_dir, resume=resume_bool)
    except Exception as e:  # noqa: BLE001
        return {"error": f"生成失败: {e}", "file_path": file_path}
    audit = result.get("consistency_audit", {})
    ctx.logger.info("bid_generate: %s → %s（%d 章节，一致性 %s）",
                    result.get("project"), result.get("output_docx"),
                    len(result.get("sections", [])), "通过" if audit.get("ok") else "有问题")
    return {
        "output_docx": result["output_docx"],
        "project": result.get("project"),
        "company": result.get("company"),
        "section_count": len(result.get("sections", [])),
        "word_total": result.get("word_total"),
        "resumed": result.get("resumed"),
        "checkpoint": result.get("checkpoint"),
        "audit_ok": audit.get("ok"),
        "audit_problems": audit.get("problems", []),
        "scoring_covered": audit.get("scoring_covered"),
        "scoring_total": audit.get("scoring_total"),
        "sections": result.get("sections", []),
    }


def handle_bid_review(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """废标风险检查 🔴🟡🟢（报价/资格材料/★响应/评审因素合规/废标条款）。"""
    file_path = _s(args, "file_path")
    if not file_path:
        return {"error": "缺少招标文件路径 file_path"}
    bid_price = _opt_num(args, "bid_price")
    materials = _str_list(args, "materials_provided")
    bid_doc_path = _s(args, "bid_doc_path") or None
    try:
        result = E.review_bid(file_path, bid_price=bid_price,
                              materials_provided=materials, bid_doc_path=bid_doc_path)
    except Exception as e:  # noqa: BLE001
        return {"error": f"审查失败: {e}", "file_path": file_path}
    summary = result.get("summary", {})
    ctx.logger.info("bid_review: %s → 🔴%d 🟡%d 🟢%d（%s）",
                    result.get("project"), summary.get("red", 0), summary.get("yellow", 0),
                    summary.get("green", 0), result.get("verdict"))
    return {
        "verdict": result.get("verdict"),
        "red": summary.get("red"),
        "yellow": summary.get("yellow"),
        "green": summary.get("green"),
        "total": summary.get("total"),
        "project": result.get("project"),
        "source_file": result.get("source_file"),
        "bid_doc": result.get("bid_doc"),
        "checks": result.get("checks", []),
    }


def handle_price_calc(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """报价策略与价格得分预估（最低价法/平均价法/线性插值）。"""
    bid_price = _num(args, "bid_price")
    if bid_price is None or bid_price <= 0:
        return {"error": "缺少有效投标报价 bid_price"}
    method = _s(args, "method") or "最低价法"
    price_weight = _num(args, "price_weight") or 30.0
    budget = _opt_num(args, "budget")
    cost = _opt_num(args, "cost")
    competitors = _num_list(args, "competitor_prices")
    try:
        result = E.price_calc(bid_price=bid_price, method=method, price_weight=price_weight,
                              budget=budget, competitor_prices=competitors, cost=cost)
    except Exception as e:  # noqa: BLE001
        return {"error": f"报价计算失败: {e}"}
    ctx.logger.info("price_calc: %s 报价 %.2f → 价格分 %.2f/%.0f",
                    method, bid_price, result.get("price_score", 0), price_weight)
    out = {
        "method": result.get("method"),
        "bid_price": result.get("bid_price"),
        "price_weight": result.get("price_weight"),
        "price_score": result.get("price_score"),
        "within_budget": result.get("within_budget"),
        "budget": result.get("budget"),
        "recommendation": result.get("recommendation"),
        "breakdown": result.get("breakdown", {}),
    }
    return out


# ── 辅助工具 handler（UI DSL 数据源，均无必填参数）──────────────────────────


def handle_bid_score_config(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """评分办法配置：写入/读取评分权重（评分办法配置页数据源）。"""
    cfg_path = E._default_data_dir() / "score_config.json"
    cfg: dict[str, Any] = {
        "method": "综合评估法",
        "price_method": "最低价法",
        "price_weight": 30.0,
        "tech_weight": 45.0,
        "business_weight": 20.0,
        "service_weight": 5.0,
    }
    if cfg_path.is_file():
        try:
            cfg.update(json.loads(cfg_path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    for key in ("method", "price_method"):
        val = _s(args, key)
        if val:
            cfg[key] = val
    for key in ("price_weight", "tech_weight", "business_weight", "service_weight"):
        val = _opt_num(args, key)
        if val is not None:
            cfg[key] = val
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(float(cfg[k]) for k in ("price_weight", "tech_weight", "business_weight", "service_weight"))
    items = [
        {"key": "method", "label": "评标方法", "value": cfg["method"]},
        {"key": "price_method", "label": "价格分公式", "value": cfg["price_method"]},
        {"key": "price_weight", "label": "价格分权重", "value": cfg["price_weight"]},
        {"key": "tech_weight", "label": "技术分权重", "value": cfg["tech_weight"]},
        {"key": "business_weight", "label": "商务分权重", "value": cfg["business_weight"]},
        {"key": "service_weight", "label": "服务分权重", "value": cfg["service_weight"]},
        {"key": "total", "label": "权重合计", "value": round(total, 2)},
    ]
    return {
        "method": cfg["method"],
        "price_method": cfg["price_method"],
        "price_weight": cfg["price_weight"],
        "tech_weight": cfg["tech_weight"],
        "business_weight": cfg["business_weight"],
        "service_weight": cfg["service_weight"],
        "total_weight": round(total, 2),
        "valid": abs(total - 100.0) < 0.01,
        "items": items,
    }


def handle_bid_generate_status(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """标书生成工作台：读取最近一次生成的章节树/断点状态（列表数据源）。"""
    key = _s(args, "project_key")
    cp_dir = E._default_data_dir() / "checkpoints"
    state_path: Path | None = None
    if key:
        state_path = cp_dir / f"{key}.json"
    elif cp_dir.is_dir():
        candidates = sorted(cp_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        state_path = candidates[0] if candidates else None
    items: list[dict[str, Any]] = []
    checkpoint = ""
    if state_path and state_path.is_file():
        checkpoint = str(state_path)
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            for sid, info in state.get("sections", {}).items():
                items.append({
                    "id": sid,
                    "status": info.get("status", ""),
                    "word_count": info.get("word_count", 0),
                    "title": sid,
                })
        except Exception:  # noqa: BLE001
            pass
    return {"items": items, "count": len(items), "checkpoint": checkpoint}


def handle_bid_knowledge_list(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """知识库管理：登记（入库存）/检索已解析招标文件（列表数据源）。"""
    files = _str_list(args, "files")
    result = E.knowledge_index(files if files else None)
    entries = result.get("entries", [])
    query = _s(args, "query").lower()
    if query:
        entries = [e for e in entries if query in json.dumps(e, ensure_ascii=False).lower()]
    limit = _num(args, "limit")
    if limit:
        entries = entries[: int(limit)]
    items = [
        {
            "project": e.get("project"),
            "code": e.get("code"),
            "budget": e.get("budget"),
            "scoring_count": e.get("scoring_count"),
            "rejection_count": e.get("rejection_count"),
            "file": e.get("file"),
        }
        for e in entries
    ]
    return {"items": items, "total": result.get("total"), "added": result.get("added")}
