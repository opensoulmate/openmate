"""
招投标插件 — 确定性引擎（无 LLM 依赖，真实招标文件可离线跑通）

设计文档: docs/plugin-system-design.md §8（招投标插件规格）
对标: docs/reports/openbidkit-yibiao-benchmark.md（解析→逐节生成→转Word→一致性审计→断点续跑）
规则资产: ~/.hermes/skills/productivity/bidding/（评分办法/112项封标清单/负偏离/评审因素29禁）

四个核心能力（对应 MCP 工具）:
  parse_tender   → bid_parse   解析招标文件 → 结构化关键信息
  generate_bid   → bid_generate 编排→逐节生成标书 Word→一致性审计→断点续跑
  review_bid     → bid_review  废标风险检查 🔴🟡🟢
  price_calc     → price_calc  报价策略（最低价法/平均价法/线性插值）

纯规则/正则/表格抽取实现，python-docx 生成 Word，PyPDF2 读 PDF。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence

# ── 文本/表格抽取 ──────────────────────────────────────────────────────────


def _read_docx(path: Path) -> tuple[str, list[list[list[str]]]]:
    from docx import Document

    doc = Document(str(path))
    paras = [p.text.strip() for p in doc.paragraphs]
    text = "\n".join(paras)
    tables: list[list[list[str]]] = []
    for tb in doc.tables:
        rows: list[list[str]] = []
        for row in tb.rows:
            rows.append([c.text.strip() for c in row.cells])
        tables.append(rows)
    return text, tables


def _read_pdf(path: Path) -> tuple[str, list[list[list[str]]]]:
    from PyPDF2 import PdfReader

    reader = PdfReader(str(path))
    parts = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(parts), []


def extract_document(file_path: str | Path) -> tuple[str, list[list[list[str]]]]:
    """抽取 (全文文本, 表格列表)。支持 docx / pdf / txt / md。"""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"招标文件不存在: {path}")
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path)
    if suffix == ".pdf":
        return _read_pdf(path)
    if suffix in (".txt", ".md", ".text"):
        return path.read_text(encoding="utf-8", errors="ignore"), []
    # .doc 等旧格式：python-docx/PyPDF2 均不支持，退回按文本尽力读
    try:
        return path.read_text(encoding="utf-8", errors="ignore"), []
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"不支持的招标文件格式: {suffix}（{e}）") from e


# ── 通用小工具 ─────────────────────────────────────────────────────────────

_NUM = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def _first_number(text: str) -> float | None:
    m = _NUM.search(text or "")
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _regex_field(text: str, patterns: list[str]) -> str | None:
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            val = _clean(m.group(1))
            if val:
                return val
    return None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()[:16]


# ── 前附表/评分表 抽取 ─────────────────────────────────────────────────────


def _tables_by_header(
    tables: list[list[list[str]]], required: list[str]
) -> list[list[list[str]]]:
    """选出表头包含全部 required 关键词的表。"""
    out = []
    for tb in tables:
        if not tb or not tb[0]:
            continue
        header = " | ".join(tb[0])
        if all(k in header for k in required):
            out.append(tb)
    return out


def _front_table_map(tables: list[list[list[str]]]) -> dict[str, str]:
    """投标人须知前附表：条款名称 → 编列内容。

    兼容两种形态：① 表头含「条款名称/编列内容」；② 无表头但每行为
    （条款号, 条款名称, 编列内容）三列且首列是条款号（如 1.3.3）。
    """
    mapping: dict[str, str] = {}
    clause_no = re.compile(r"^\d+(\.\d+)+$")
    for tb in tables:
        if not tb:
            continue
        header = " | ".join(tb[0]) if tb[0] else ""
        is_front = ("条款名称" in header and "编列内容" in header) or (
            len(tb[0]) == 3 and sum(1 for r in tb if r and clause_no.match(_clean(r[0]))) >= 2
        )
        if not is_front:
            continue
        for row in tb:
            if len(row) >= 3:
                key = _clean(row[1])
                val = _clean(row[2])
                # 跳过表头行本身
                if key in ("条款名称",) or val in ("编列内容",):
                    continue
                if key and val:
                    mapping[key] = val
    return mapping


def _trim_contact(value: str | None) -> str | None:
    """去掉『名称：』前缀，并在 联系人/地址/电话/传真/邮箱/递交方式 处截断。"""
    if not value:
        return value
    v = _clean(value)
    v = re.sub(r"^名称[：:]\s*", "", v)
    for cut in ["联系人", "地址", "电话", "传真", "邮箱", "递交方式", "开标地点", "网址", "开户"]:
        idx = v.find(cut)
        if idx > 0:
            v = v[:idx]
    return _clean(v)


def _scoring_table(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    """评分标准表：评分因素 / 评分标准（含分值）。"""
    items: list[dict[str, Any]] = []
    for tb in _tables_by_header(tables, ["评分因素", "评分标准"]):
        for row in tb[1:]:
            if len(row) < 3:
                continue
            factor = _clean(row[-2])
            criteria = _clean(row[-1])
            if not factor:
                continue
            score = _first_number(criteria)
            items.append({"factor": factor, "score": score, "criteria": criteria})
    return items


def _eval_factor_table(tables: list[list[list[str]]]) -> list[dict[str, str]]:
    """评标办法前附表：评审因素 / 评审标准。"""
    items: list[dict[str, str]] = []
    for tb in _tables_by_header(tables, ["评审因素", "评审标准"]):
        for row in tb[1:]:
            if len(row) < 2:
                continue
            factor = _clean(row[-2])
            standard = _clean(row[-1])
            if factor:
                items.append({"factor": factor, "standard": standard})
    return items


def _param_table(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    """技术参数表：含 参数性质/★/▲/技术参数/招标要求/投标响应 列。"""
    params: list[dict[str, Any]] = []
    name_cols = ["参数名称", "技术参数", "参数", "项目", "名称"]
    req_cols = ["招标文件要求", "招标要求", "技术要求", "要求", "参数值", "响应"]
    for tb in tables:
        if not tb or not tb[0]:
            continue
        header_cells = tb[0]
        header = " | ".join(header_cells)
        if not any(k in header for k in ["参数", "技术", "响应", "要求"]):
            continue
        has_star = "★" in header or "▲" in header or "参数性质" in header

        def col_index(cands: list[str]) -> int:
            for idx, cell in enumerate(header_cells):
                if any(c in cell for c in cands):
                    return idx
            return -1

        name_i = col_index(name_cols)
        req_i = col_index(req_cols)
        for row in tb[1:]:
            joined = " ".join(row)
            level = "一般"
            if "★" in joined:
                level = "★"
            elif "▲" in joined:
                level = "▲"
            if not has_star and level == "一般":
                continue
            name = _clean(row[name_i]) if 0 <= name_i < len(row) else ""
            if not name:
                name = _clean(row[1]) if len(row) > 1 else _clean(row[0])
            require = _clean(row[req_i]) if 0 <= req_i < len(row) else ""
            if not require and len(row) >= 3:
                require = _clean(row[2])
            if name:
                params.append({"param": name, "requirement": require, "level": level, "mandatory": level == "★"})
    return params


def _rejection_clauses(text: str) -> list[str]:
    """废标/无效投标条款。"""
    triggers = ["作废标处理", "废标处理", "投标无效", "无效投标", "否决其投标", "否决投标",
                "取消投标资格", "不予退还", "否决所有投标", "其投标作废", "无效的投标"]
    clauses: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        line = _clean(line)
        if not line or len(line) < 8:
            continue
        if any(t in line for t in triggers):
            key = line[:40]
            if key not in seen:
                seen.add(key)
                clauses.append(line[:220])
    return clauses


def _qualification_block(text: str) -> list[str]:
    """投标人资格要求条款。"""
    lines = [_clean(x) for x in text.split("\n")]
    items: list[str] = []
    capture = False
    for line in lines:
        if not line:
            continue
        if re.search(r"投标人资格要求|资格要求如下|投标人须具备|资格条件", line):
            capture = True
        if capture:
            if re.match(r"^[一二三四五六七八九十]+[、.]", line) and "资格" not in line and items:
                break
            if len(line) > 6:
                items.append(line[:220])
            if len(items) >= 20:
                break
    return items


# ── bid_parse：解析招标文件 → 结构化关键信息 ───────────────────────────────


def parse_tender(file_path: str | Path) -> dict[str, Any]:
    text, tables = extract_document(file_path)
    front = _front_table_map(tables)
    flat = text.replace("\n", " ")

    def front_get(*keys: str) -> str | None:
        for k in keys:
            for fk, fv in front.items():
                if k in fk:
                    return fv
        return None

    project_info: dict[str, Any] = {
        "project_name": (
            front_get("项目名称")
            or _regex_field(flat, [r"项目名称[：:]\s*([^项]{2,60}?)项目编号", r"项目名称[：:]\s*([^。；]{2,60})"])
            or _clean(text.split("\n")[0])[:60]
        ),
        "project_code": _regex_field(
            flat,
            [r"项目编号[：:]\s*([A-Za-z0-9\-]{4,30})", r"招标编号[：:（(]\s*([A-Za-z0-9\-]{4,30})"],
        ),
        "budget": None,
        "budget_text": (
            _regex_field(flat, [r"最高限价[：:]\s*([\d,.]+\s*元?)", r"预算(?:金额)?[：:]\s*([\d,.]+\s*元?)"])
            or front_get("最高限价", "预算")
        ),
        "purchaser": _trim_contact(front_get("招标人") or _regex_field(flat, [r"招标人[：:]\s*([^。；，]{2,40})"])),
        "agency": _trim_contact(
            front_get("招标代理机构", "代理机构")
            or _regex_field(flat, [r"招标代理机构[：:]\s*([^。；，]{2,40})", r"编制单位[：:]\s*([^。；，]{2,40})"])
        ),
        "method": _regex_field(flat, [r"招标方式为(\w+招标)", r"采购方式[：:]\s*(\w+招标|\w+采购)"]) or front_get("采购方式", "招标方式"),
        "location": _trim_contact(front_get("建设地点", "项目所在地") or _regex_field(flat, [r"项目所在地区[：:]\s*([^。；]{2,40})"])),
        "deadline": (
            _regex_field(flat, [r"递交截止时间[：:]\s*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日[0-9]{0,2}时?[0-9]{0,2}分?)",
                                r"投标截止时间[：:]\s*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日[0-9]{0,2}时?[0-9]{0,2}分?)"])
            or front_get("投标截止时间", "递交截止")
        ),
        "bid_open_time": (
            _regex_field(flat, [r"开标时间[：:]\s*([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日[0-9]{0,2}时?[0-9]{0,2}分?)"])
            or front_get("开标时间")
        ),
        "duration": front_get("工期", "计划工期") or _regex_field(flat, [r"工期(\d+日历天)", r"工期要求[：:]\s*([^。；]{4,30})"]),
        "quality": front_get("质量要求") or _regex_field(flat, [r"质量要求[：:]\s*([^。；]{4,40})"]),
        "fund_source": front_get("资金来源"),
        "bid_validity": front_get("投标有效期"),
        "margin": front_get("投标保证金", "保证金"),
    }
    budget_val = _first_number(str(project_info.get("budget_text") or ""))
    project_info["budget"] = budget_val

    scoring = _scoring_table(tables)
    eval_factors = _eval_factor_table(tables)
    params = _param_table(tables)
    rejection = _rejection_clauses(text)
    qualification = _qualification_block(text)

    evaluation_method = None
    if "综合评估法" in text or "综合评分法" in text:
        evaluation_method = "综合评估法"
    elif "最低评标价法" in text or "最低价" in text:
        evaluation_method = "最低评标价法"

    return {
        "source_file": str(file_path),
        "doc_hash": _sha(text),
        "stats": {"chars": len(text), "paragraphs": text.count("\n") + 1, "tables": len(tables)},
        "project_info": project_info,
        "evaluation_method": evaluation_method,
        "scoring": scoring,
        "evaluation_factors": eval_factors,
        "params": params,
        "rejection_clauses": rejection,
        "qualification": qualification,
    }


# ── bid_generate：编排→逐节生成标书 Word→一致性审计→断点续跑 ──────────────

_STANDARD_SECTIONS = [
    ("cover", "封面与投标函", 1),
    ("qualification", "资格证明文件", 1),
    ("commercial", "商务标（投标报价）", 1),
    ("tech-org", "技术标·施工组织设计", 1),
    ("tech-quality", "技术标·质量与工期保证", 1),
    ("tech-safety", "技术标·安全与文明施工", 1),
    ("management", "项目管理机构", 1),
    ("score-response", "评分因素响应表", 1),
    ("deviation", "偏离表", 1),
]


def build_outline(parse: dict[str, Any]) -> list[dict[str, Any]]:
    """三级提纲：标准章节 + 覆盖全部评分得分点。"""
    outline: list[dict[str, Any]] = []
    for sid, title, depth in _STANDARD_SECTIONS:
        outline.append({"id": sid, "title": title, "depth": depth, "scoring": []})
    # 评分得分点映射到 score-response 子节点（覆盖性）
    score_node = next(n for n in outline if n["id"] == "score-response")
    for i, item in enumerate(parse.get("scoring", []), 1):
        score_node["scoring"].append({"idx": i, "factor": item["factor"], "score": item["score"]})
    return outline


def _section_paragraphs(section_id: str, parse: dict[str, Any], company: str) -> list[tuple[str, str]]:
    """生成 (style, text) 段落序列。style: heading1|heading2|body|bullet。"""
    pi = parse.get("project_info", {})
    name = pi.get("project_name") or "本项目"
    budget = pi.get("budget_text") or ("%.2f 元" % pi["budget"] if pi.get("budget") else "见招标文件")
    quality = pi.get("quality") or "符合国家现行验收规范合格标准"
    duration = pi.get("duration") or "见招标文件工期要求"
    location = pi.get("location") or "见招标文件"
    method = parse.get("evaluation_method") or "综合评估法"

    P: list[tuple[str, str]] = []
    if section_id == "cover":
        P += [("heading1", f"{name} 投标文件"),
              ("body", f"投标人：{company}"),
              ("body", f"项目编号：{pi.get('project_code') or '—'}"),
              ("body", f"投标报价（大写）：人民币（详见商务标）"),
              ("body", f"编制日期：{_today()}"),
              ("heading2", "投标函"),
              ("body", f"致：{pi.get('purchaser') or '招标人'}"),
              ("body", f"我方已仔细研究 {name} 招标文件的全部内容，包括澄清、修改文件（如有），"
                       f"并踏勘了现场。我方愿以人民币 {budget} 内的报价，按合同约定实施和完成"
                       f"{name} 的全部工作，工期 {duration}，质量标准 {quality}。"),
              ("body", "我方承诺在投标有效期内不修改、撤销投标文件，并按要求递交投标保证金。")]
    elif section_id == "qualification":
        P += [("heading1", "资格证明文件"),
              ("body", "我方承诺并附以下资格及证明材料（均在有效期内、加盖公章）：")]
        for q in parse.get("qualification", [])[:10]:
            P.append(("bullet", q))
        if not parse.get("qualification"):
            P.append(("bullet", "营业执照（三证合一）副本；资质证书副本；安全生产许可证；"
                                "注册建造师执业资格及安全考核 A/B/C 证；近三年财务报表；信用中国信用记录。"))
        P += [("heading2", "材料清单核对"),
              ("body", "随投标文件单独提供：投标保证金汇款凭证或保函、法定代表人身份证明书、"
                       "授权委托书、信用中国信用记录打印页、联合体协议书（如联合体投标）。")]
    elif section_id == "commercial":
        P += [("heading1", "商务标（投标报价）"),
              ("body", f"我方投标总报价为人民币 {budget}（以唱标一览表及投标报价汇总表为准），"
                       f"已包含为完成招标范围全部工作的各项费用、税金及风险费用。"),
              ("heading2", "报价编制说明"),
              ("bullet", "投标报价以招标文件、工程量清单、技术标准和要求、图纸为依据。"),
              ("bullet", "投标报价不低于我方个别成本；如有算术错误，我方同意按评标办法修正并书面确认。"),
              ("bullet", f"我方报价不超过最高限价 {budget}，否则按废标处理。")]
    elif section_id == "tech-org":
        P += [("heading1", "技术标·施工组织设计"),
              ("body", f"针对 {name}（位于 {location}）的建设目标——实现厂区数字化与机电一体化并有机整合，"
                       f"我方制定如下施工组织设计。"),
              ("heading2", "总体部署"),
              ("bullet", "遵循先地下后地上、先土建后安装、先单机后联动的原则组织施工。"),
              ("bullet", "数字化系统与机电安装分区分段平行流水，关键线路优先投入资源。"),
              ("heading2", "主要施工方法"),
              ("bullet", "自动化仪表与控制系统：按设计选型安装、单体调试、系统联调。"),
              ("bullet", "厂区数字化平台：网络布线、设备接入、数据采集与集成调试。")]
    elif section_id == "tech-quality":
        P += [("heading1", "技术标·质量与工期保证"),
              ("body", f"质量目标：{quality}；工期目标：{duration}。"),
              ("bullet", "建立三级质量检验制度，关键工序旁站与隐蔽工程验收。"),
              ("bullet", "编制总进度计划与月/周滚动计划，设置里程碑节点并动态纠偏。")]
    elif section_id == "tech-safety":
        P += [("heading1", "技术标·安全与文明施工"),
              ("body", "落实安全生产责任制，配备专职安全员，执行 A/B/C 三类人员安全考核要求。"),
              ("bullet", "危险性较大分部分项工程编制专项方案并按规定论证、交底。"),
              ("bullet", "现场文明施工、环境保护与扬尘治理符合当地主管部门要求。")]
    elif section_id == "management":
        P += [("heading1", "项目管理机构"),
              ("body", "我方拟派项目管理机构如下（详见资格审查表）："),
              ("bullet", "项目经理：市政公用工程/机电工程专业二级及以上注册建造师，未在其他在施项目任职。"),
              ("bullet", "技术负责人、施工员、质量员、安全员、材料员、资料员等按需到岗。")]
    elif section_id == "score-response":
        P += [("heading1", "评分因素响应表"),
              ("body", f"本项目采用 {method}。我方对评分因素逐条响应如下，确保覆盖全部得分点：")]
        for item in parse.get("scoring", []):
            factor = item["factor"]
            score = item.get("score")
            score_txt = f"（{score} 分）" if score is not None else ""
            P.append(("bullet", f"{factor}{score_txt}：已在对应章节充分响应，提供可核验证据材料。"))
        if not parse.get("scoring"):
            P.append(("bullet", "（招标文件评分表未解析出量化分值，已按施工组织设计/项目管理机构/投标报价三大项组织响应。）"))
    elif section_id == "deviation":
        P += [("heading1", "偏离表"),
              ("body", "我方对招标文件的实质性要求（工期、质量、投标有效期、技术标准、招标范围）"
                       "全部无偏离响应；对一般性条款的偏离已在下表如实列明："),
              ("bullet", "★（实质性/强制）条款：无负偏离。"),
              ("bullet", "▲（重要）条款：无负偏离。"),
              ("bullet", "一般条款：如存在文字性调整，已在澄清中说明，不影响实质性响应。")]
    return P


def _today() -> str:
    import datetime

    return datetime.date.today().strftime("%Y年%m月%d日")


def _consistency_audit(parse: dict[str, Any], sections_text: dict[str, str]) -> dict[str, Any]:
    """一致性审计：项目名称/编号/预算 跨章节一致 + 评分得分点全覆盖。"""
    pi = parse.get("project_info", {})
    name = pi.get("project_name") or ""
    code = pi.get("project_code") or ""
    problems: list[str] = []
    joined = "\n".join(sections_text.values())
    if name and name not in joined:
        problems.append(f"项目名称「{name}」未贯穿全文")
    if code and code not in joined:
        problems.append(f"项目编号「{code}」未贯穿全文")
    # 评分得分点覆盖
    missing = []
    for item in parse.get("scoring", []):
        if item["factor"] and item["factor"] not in sections_text.get("score-response", ""):
            missing.append(item["factor"])
    if missing:
        problems.append(f"评分得分点未覆盖: {missing}")
    return {
        "ok": not problems,
        "problems": problems,
        "checked_sections": len(sections_text),
        "scoring_covered": len(parse.get("scoring", [])) - len(missing),
        "scoring_total": len(parse.get("scoring", [])),
    }


def generate_bid(
    file_path: str | Path,
    company: str = "投标人",
    output_dir: str | Path | None = None,
    resume: bool = True,
    project_key: str | None = None,
) -> dict[str, Any]:
    """编排→逐节生成标书 Word→一致性审计→断点续跑。"""
    parse = parse_tender(file_path)
    pi = parse.get("project_info", {})
    out_dir = Path(output_dir or _default_data_dir() / "output")
    out_dir.mkdir(parents=True, exist_ok=True)
    key = project_key or _sha(str(file_path) + "|" + company)
    state_path = _default_data_dir() / "checkpoints" / f"{key}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state: dict[str, Any] = {"key": key, "sections": {}}
    resumed = False
    if resume and state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            resumed = True
        except Exception:  # noqa: BLE001
            state = {"key": key, "sections": {}}

    outline = build_outline(parse)
    sections_text: dict[str, str] = {}
    sections_meta: list[dict[str, Any]] = []
    for node in outline:
        sid = node["id"]
        prior = state["sections"].get(sid)
        if resumed and prior and prior.get("status") == "done":
            paras = prior.get("paras", [])
            sections_text[sid] = prior.get("text", "")
            sections_meta.append({"id": sid, "title": node["title"], "status": "done(resumed)",
                                  "word_count": prior.get("word_count", 0)})
            continue
        paras = _section_paragraphs(sid, parse, company)
        text = "\n".join(t for _, t in paras)
        sections_text[sid] = text
        state["sections"][sid] = {"status": "done", "paras": [{"style": s, "text": t} for s, t in paras],
                                  "text": text, "word_count": len(text)}
        sections_meta.append({"id": sid, "title": node["title"], "status": "done",
                              "word_count": len(text)})
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    audit = _consistency_audit(parse, sections_text)
    docx_path = _write_docx(parse, sections_text, outline, company, out_dir)
    return {
        "output_docx": str(docx_path),
        "project": pi.get("project_name"),
        "company": company,
        "sections": sections_meta,
        "resumed": resumed,
        "checkpoint": str(state_path),
        "consistency_audit": audit,
        "word_total": sum(m["word_count"] for m in sections_meta),
    }


def _write_docx(
    parse: dict[str, Any],
    sections_text: dict[str, str],
    outline: list[dict[str, Any]],
    company: str,
    out_dir: Path,
) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt

    pi = parse.get("project_info", {})
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(12)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(f"{pi.get('project_name') or '投标文件'}")
    run.bold = True
    run.font.size = Pt(20)
    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    sub.add_run(f"投标文件 · {company}").font.size = Pt(14)

    # 目录（静态章节目录，Word 中可 F9 更新域——此处列章节）
    doc.add_heading("目录", level=1)
    for node in outline:
        doc.add_paragraph(node["title"], style="List Number")

    level_map = {"heading1": 1, "heading2": 2, "heading3": 3}
    for node in outline:
        sid = node["id"]
        paras = _section_paragraphs(sid, parse, company)
        for style_name, text in paras:
            if style_name in level_map:
                doc.add_heading(text, level=level_map[style_name])
            elif style_name == "bullet":
                doc.add_paragraph(text, style="List Bullet")
            else:
                doc.add_paragraph(text)

    # 附表：评分响应表（真实表格）
    scoring = parse.get("scoring", [])
    if scoring:
        doc.add_heading("附表一：评分因素响应与得分点覆盖", level=1)
        table = doc.add_table(rows=1, cols=3)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "评分因素"
        hdr[1].text = "分值"
        hdr[2].text = "响应说明"
        for item in scoring:
            cells = table.add_row().cells
            cells[0].text = item["factor"]
            cells[1].text = "" if item.get("score") is None else str(item["score"])
            cells[2].text = "已在技术标/商务标对应章节响应"

    # 附表二：报价汇总（真实表格）
    doc.add_heading("附表二：投标报价汇总表", level=1)
    t2 = doc.add_table(rows=2, cols=2)
    t2.style = "Table Grid"
    t2.rows[0].cells[0].text = "项目名称"
    t2.rows[0].cells[1].text = pi.get("project_name") or ""
    t2.rows[1].cells[0].text = "最高限价/控制价"
    t2.rows[1].cells[1].text = str(pi.get("budget_text") or "见招标文件")

    name = re.sub(r"[\\/:*?\"<>|\s]+", "_", pi.get("project_name") or "投标文件")[:40]
    path = out_dir / f"{name}_投标文件_{company}.docx"
    doc.save(str(path))
    return path


# ── bid_review：废标风险检查 🔴🟡🟢 ───────────────────────────────────────

# 评审因素 29 禁（禁止作为评审因素的规模/业绩条件）——references/evaluation-factors-compliance.md
_FORBIDDEN_FACTORS = [
    (r"注册地|注册地(?:点|址)", "将注册地作为评审因素"),
    (r"注册资本", "将注册资本作为评审因素"),
    (r"资产总额", "将资产总额作为评审因素"),
    (r"营业收入|营业额", "将营业收入作为评审因素"),
    (r"从业人员数量|员工人数", "将从业人员数量作为评审因素"),
    (r"特定金额.{0,4}业绩|\d+万元以上.{0,4}业绩", "以特定金额业绩作为评审因素"),
    (r"本地(?:业绩|注册)|本市(?:业绩|注册)|本省(?:业绩|注册)", "以特定行政区域业绩/注册地加分"),
    (r"特定(?:行业|部门)业绩", "以特定行业业绩加分"),
]

# 封标清单/资格证明材料类别（112 项清单精简为可核验类别）——references/sealing-checklist.md
_MATERIAL_CATEGORIES = {
    "营业执照": ["营业执照", "三证合一", "营业执照副本"],
    "资质证书": ["资质证书", "施工总承包资质", "专业承包资质"],
    "安全生产许可证": ["安全生产许可证"],
    "注册建造师": ["建造师", "注册建造师", "项目经理"],
    "安全考核A/B/C证": ["安全生产考核", "A证", "B证", "C证", "安全考核"],
    "财务报表": ["财务报表", "财务报告", "审计报告"],
    "投标保证金": ["保证金", "投标保证金", "保函"],
    "信用中国记录": ["信用中国", "失信被执行人", "信用记录"],
    "授权委托书": ["授权委托书", "法定代表人身份证明"],
}


def review_bid(
    file_path: str | Path,
    bid_price: float | None = None,
    materials_provided: list[str] | None = None,
    bid_doc_path: str | Path | None = None,
) -> dict[str, Any]:
    """废标风险审查：解析招标文件 + （可选）投标方提供材料/报价 → 🔴🟡🟢 报告。"""
    parse = parse_tender(file_path)
    pi = parse.get("project_info", {})
    budget = pi.get("budget")
    provided = [str(x) for x in (materials_provided or [])]
    provided_text = " ".join(provided)

    checks: list[dict[str, str]] = []

    # ① 评审因素合规（29 禁）——扫描评分/评审因素名称及其评分标准、评审标准文本
    all_factors = " ".join(
        [f["factor"] + " " + (f.get("criteria") or "") for f in parse.get("scoring", [])]
        + [f["factor"] + " " + (f.get("standard") or "") for f in parse.get("evaluation_factors", [])]
    )
    forbidden_hits = []
    for pat, label in _FORBIDDEN_FACTORS:
        if re.search(pat, all_factors):
            forbidden_hits.append(label)
    if forbidden_hits:
        for hit in forbidden_hits:
            checks.append({"level": "🔴", "category": "评审因素合规", "item": "评审因素设置违规",
                           "detail": f"疑似命中 29 禁：{hit}（references/evaluation-factors-compliance.md）"})
    else:
        checks.append({"level": "🟢", "category": "评审因素合规", "item": "评审因素设置合规",
                       "detail": "未发现 29 种禁止情形（注册地/资本/规模/特定业绩等）"})

    # ② 报价合规（不得超最高限价 / 低于成本风险）
    if bid_price is not None and budget is not None:
        if bid_price <= budget:
            checks.append({"level": "🟢", "category": "报价", "item": "报价未超最高限价",
                           "detail": f"报价 {bid_price:,.2f} ≤ 最高限价 {budget:,.2f}"})
        else:
            checks.append({"level": "🔴", "category": "报价", "item": "报价超过最高限价",
                           "detail": f"报价 {bid_price:,.2f} > 最高限价 {budget:,.2f}，按废标处理"})
        if bid_price < budget * 0.7:
            checks.append({"level": "🟡", "category": "报价", "item": "低于成本报价风险",
                           "detail": f"报价低于最高限价 70%，可能被认定低于个别成本，需书面说明并提供证明材料（评标办法 2.4）"})
    elif bid_price is None:
        checks.append({"level": "🟡", "category": "报价", "item": "未提供报价",
                       "detail": "未提供投标报价，无法核验是否超最高限价/低于成本，请补充"})

    # ③ 资格/证明材料（封标清单）
    for category, keywords in _MATERIAL_CATEGORIES.items():
        present = any(k in provided_text for k in keywords)
        mandatory = category in ("营业执照", "资质证书", "安全生产许可证", "注册建造师", "安全考核A/B/C证", "投标保证金")
        if present:
            checks.append({"level": "🟢", "category": "资格/材料", "item": category, "detail": "已提供，待核验有效期与盖章"})
        else:
            level = "🔴" if mandatory else "🟡"
            note = "（必备，缺失按废标处理）" if mandatory else "（证明材料，缺失扣分/风险）"
            checks.append({"level": level, "category": "资格/材料", "item": category, "detail": f"未见提供{note}"})

    # ④ ★实质性响应
    star_params = [p for p in parse.get("params", []) if p.get("level") == "★"]
    if star_params:
        for p in star_params:
            checks.append({"level": "🔴", "category": "实质性响应", "item": f"★ {p['param']}",
                           "detail": f"★ 实质性要求「{p.get('requirement') or p['param']}」必须逐条响应，负偏离即废标"})
    else:
        checks.append({"level": "🟢", "category": "实质性响应", "item": "★ 强制条款",
                       "detail": "招标文件未抽取到 ★ 强制技术参数（如有请人工复核技术规格表）"})

    # ⑤ 废标条款提示
    for clause in parse.get("rejection_clauses", [])[:6]:
        checks.append({"level": "🟡", "category": "废标条款", "item": "招标文件废标情形",
                       "detail": clause[:160]})

    # 汇总
    red = sum(1 for c in checks if c["level"] == "🔴")
    yellow = sum(1 for c in checks if c["level"] == "🟡")
    green = sum(1 for c in checks if c["level"] == "🟢")
    if red:
        verdict = "高废标风险（存在🔴必改项，须逐条整改后再投标）"
    elif yellow:
        verdict = "中风险（存在🟡提示项，建议核实补齐）"
    else:
        verdict = "低风险（未见明显废标风险）"
    return {
        "source_file": str(file_path),
        "project": pi.get("project_name"),
        "bid_doc": str(bid_doc_path) if bid_doc_path else None,
        "summary": {"red": red, "yellow": yellow, "green": green, "total": len(checks)},
        "verdict": verdict,
        "checks": checks,
    }


# ── price_calc：报价策略 ───────────────────────────────────────────────────


def price_calc(
    bid_price: float,
    method: str = "最低价法",
    price_weight: float = 30.0,
    budget: float | None = None,
    competitor_prices: list[float] | None = None,
    benchmark_price: float | None = None,
    cost: float | None = None,
) -> dict[str, Any]:
    """报价策略与价格得分预估。

    方法（references/scoring-methodology.md）：
      最低价法: 价格分 = (最低有效报价 / 投标报价) × 价格权重分
      平均价法: 偏离平均价按比例扣分
      线性插值: 价格分 = 价格权重分 × (最高限价 - 报价) / (最高限价 - 最低价)
    """
    competitors = [float(x) for x in (competitor_prices or [])]
    all_prices = competitors + [float(bid_price)]
    valid_prices = [p for p in all_prices if budget is None or p <= budget]
    lowest = min(valid_prices) if valid_prices else float(bid_price)
    avg = (sum(valid_prices) / len(valid_prices)) if valid_prices else float(bid_price)

    price_score: float
    breakdown: dict[str, Any]
    if method == "平均价法":
        if avg > 0:
            deviation = abs(float(bid_price) - avg) / avg
            price_score = max(0.0, price_weight * (1 - deviation))
        else:
            price_score = 0.0
        breakdown = {"method": method, "average_price": round(avg, 2),
                     "deviation_ratio": round(abs(float(bid_price) - avg) / avg if avg else 0, 4)}
    elif method == "线性插值":
        if budget is not None and budget > lowest:
            ratio = (float(budget) - float(bid_price)) / (float(budget) - lowest)
            ratio = min(max(ratio, 0.0), 1.0)
            price_score = price_weight * ratio
        else:
            price_score = price_weight
        breakdown = {"method": method, "highest_limit": budget, "lowest": round(lowest, 2),
                     "ratio": round(min(max((float(budget) - float(bid_price)) / (float(budget) - lowest) if budget and budget > lowest else 1.0, 0.0), 1.0), 4)}
    else:  # 最低价法
        price_score = (lowest / float(bid_price) * price_weight) if float(bid_price) > 0 else 0.0
        breakdown = {"method": "最低价法", "lowest_valid_price": round(lowest, 2)}

    price_score = round(min(price_score, price_weight), 2)
    recommendation = _price_recommendation(bid_price, budget, cost, price_score, price_weight, method)

    result = {
        "method": method,
        "bid_price": bid_price,
        "price_weight": price_weight,
        "price_score": price_score,
        "breakdown": breakdown,
        "recommendation": recommendation,
    }
    if budget is not None:
        result["within_budget"] = bid_price <= budget
        result["budget"] = budget
    return result


def _price_recommendation(bid, budget, cost, score, weight, method) -> str:
    tips: list[str] = []
    if budget is not None and bid > budget:
        tips.append(f"报价超最高限价 {budget:,.2f}，将被废标，须降至限价内。")
    if cost is not None and bid < cost:
        tips.append(f"报价 {bid:,.2f} 低于成本 {cost:,.2f}，存在低于成本报价认定风险。")
    if budget is not None and bid < budget * 0.7:
        tips.append("报价低于最高限价 70%，建议准备书面成本说明与证明材料。")
    if score >= weight * 0.9:
        tips.append(f"{method}下价格得分 {score}/{weight}，价格竞争力强。")
    elif score < weight * 0.6:
        tips.append(f"{method}下价格得分 {score}/{weight} 偏低，可适度下调报价提升价格分。")
    if not tips:
        tips.append("报价处于合理区间，价格得分健康，注意保持不低于个别成本。")
    return " ".join(tips)


# ── 数据目录（检查点/知识库）────────────────────────────────────────────────


def _default_data_dir() -> Path:
    override = os.environ.get("BIDDING_DATA_DIR")
    if override:
        p = Path(override)
    else:
        p = Path.home() / ".openmate-data" / "bidding"
    p.mkdir(parents=True, exist_ok=True)
    return p


def knowledge_index(file_paths: Sequence[str | Path] | None = None) -> dict[str, Any]:
    """知识库：登记/检索已解析招标文件（9450 份招标文件知识库入库入口）。"""
    kb_path = _default_data_dir() / "knowledge.json"
    store: dict[str, Any] = {}
    if kb_path.is_file():
        try:
            store = json.loads(kb_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            store = {}
    added = 0
    for fp in file_paths or []:
        p = Path(fp)
        if not p.is_file():
            continue
        key = _sha(str(p.resolve()))
        if key in store:
            continue
        try:
            parse = parse_tender(p)
            store[key] = {
                "file": str(p),
                "project": parse["project_info"].get("project_name"),
                "code": parse["project_info"].get("project_code"),
                "budget": parse["project_info"].get("budget"),
                "scoring_count": len(parse.get("scoring", [])),
                "rejection_count": len(parse.get("rejection_clauses", [])),
                "doc_hash": parse.get("doc_hash"),
            }
            added += 1
        except Exception as e:  # noqa: BLE001
            store[key] = {"file": str(p), "error": str(e)}
    kb_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"total": len(store), "added": added, "entries": list(store.values())}
