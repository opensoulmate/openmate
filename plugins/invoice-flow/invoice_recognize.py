"""
发票识别 — 二维码 + ocrmypdf/OCR 字段抽取（纯 Python + 系统工具）

识别链（整合 dhcc-reimbursement 既有资产，禁平行造轮子）：
① PDF → pdftoppm 渲染首页 → zbarimg 解析发票二维码（100% 准：
   01,32,,发票号码20位,价税合计,YYYYMMDD,,校验码4位 —— invoice-reimbursement skill 同款格式）
② 文本层：pdftotext（原生 PDF）→ 不足则 ocrmypdf --sidecar（scan/图片走 OCR）
③ 字段抽取：二维码定号码/金额/日期/校验码；文本补销售方/税号/商品名称
④ 分类：复用 ~/Documents/Hermes/dhcc-reimbursement/invoice_classifier.classify_invoice
   （差旅/支出 + 报销类别）；不可导入时走同规则最小兜底
缺字段记 warnings，留人工确认环节补全（vision/手补），不瞎编。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
PDF_EXTS = {".pdf"}
DHCC_DIR_ENV = "DHCC_REIMBURSE_DIR"
DEFAULT_DHCC_DIR = "~/Documents/Hermes/dhcc-reimbursement"

# 兜底分类关键词（与 dhcc invoice_classifier 同规则：先支出后差旅）
_TRAVEL_KEYWORDS = ("火车", "高铁", "动车", "机票", "航空", "飞机", "铁路")
_EXPENSE_CATEGORY_MAP = (
    (("出租", "滴滴", "市内车费", "公交", "网约车"), "【普】市内车费"),
    (("会议",), "【普】会议费"),
    (("评审",), "【普】项目评审费"),
    (("外包",), "【普】外包人员及外包顾问费"),
)


def dhcc_dir() -> Path:
    return Path(os.environ.get(DHCC_DIR_ENV) or DEFAULT_DHCC_DIR).expanduser()


# ---- 二维码（优先，100% 准） ----

def _render_first_page(path: Path, out_prefix: str) -> str | None:
    """PDF 首页渲染 PNG；图片直接返回原路径。"""
    if path.suffix.lower() in IMAGE_EXTS:
        return str(path)
    cmd = ["pdftoppm", "-png", "-r", "300", "-f", "1", "-l", "1",
           "-singlefile", str(path), out_prefix]
    result = subprocess.run(cmd, capture_output=True, text=True)
    png = f"{out_prefix}.png"
    return png if result.returncode == 0 and Path(png).is_file() else None


def decode_qr(image_path: str) -> str:
    """zbarimg 解码发票二维码（无第三方 Python 依赖；解不出返回空串）。"""
    try:
        result = subprocess.run(["zbarimg", "--quiet", "--raw", image_path],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if line.count(",") >= 5 and any(ch.isdigit() for ch in line):
            return line
    return ""


def parse_qr_payload(payload: str) -> dict[str, Any]:
    """01,32,,发票号码20位,价税合计,YYYYMMDD,,校验码4位"""
    parts = payload.split(",")
    if len(parts) < 6:
        return {}
    date_raw = parts[5].strip()
    date = ""
    if re.fullmatch(r"\d{8}", date_raw):
        date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
    return {
        "发票号码": parts[3].strip(),
        "价税合计": _money(parts[4]),
        "开票日期": date,
        "校验码": parts[7].strip() if len(parts) > 7 else "",
    }


def _money(text: str) -> float | None:
    try:
        return round(float(str(text).replace(",", "").strip()), 2)
    except (TypeError, ValueError):
        return None


# ---- 文本层（pdftotext → ocrmypdf sidecar → tesseract） ----

def extract_text(path: Path, image_path: str | None) -> tuple[str, str]:
    """返回 (文本, 来源 pdftotext|ocrmypdf|tesseract|none)。"""
    if path.suffix.lower() in PDF_EXTS:
        result = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                capture_output=True, text=True, timeout=60)
        text = (result.stdout or "").strip()
        if len(text) >= 100 and re.search(r"[\u4e00-\u9fff]{4,}", text):
            return text, "pdftotext"
    text = _ocrmypdf_text(path)
    if text:
        return text, "ocrmypdf"
    if image_path:
        try:
            result = subprocess.run(
                ["tesseract", image_path, "stdout", "-l", "chi_sim+eng"],
                capture_output=True, text=True, timeout=120)
            text = (result.stdout or "").strip()
            if text:
                return text, "tesseract"
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "", "none"


def _ocrmypdf_text(path: Path) -> str:
    """ocrmypdf --skip-text --sidecar：scan/图片出可检索文本（用户指定识别链）。"""
    with tempfile.TemporaryDirectory(prefix="invoice_ocr_") as tmp:
        out_pdf = Path(tmp) / "searchable.pdf"
        sidecar = Path(tmp) / "sidecar.txt"
        try:
            result = subprocess.run(
                ["ocrmypdf", "--skip-text", "--language", "chi_sim+eng",
                 "--sidecar", str(sidecar), str(path), str(out_pdf)],
                capture_output=True, text=True, timeout=240)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if sidecar.is_file():
            return sidecar.read_text(encoding="utf-8", errors="replace").strip()
        return ""


# ---- 字段抽取 ----

def _parties(text: str) -> tuple[str, str, str, str, list[str]]:
    """(购买方, 购买方税号, 销售方, 销售方税号, warnings)。

    票面布局为左购买方/右销售方，pdftotext 按行交错——名称/税号按出现顺序配对。
    「项目名称/商品名称」等表头不是往来方（负向后顾+停用词表排除）。
    """
    warnings: list[str] = []
    stoplist = {"规格型号", "项目名称", "商品名称", "购买方", "销售方", "开票人",
                "复核", "收款人", "备注", "合计", "单价", "数量", "单位", "金额",
                "税额", "税率", "名称"}
    names: list[str] = []
    for name in re.findall(
            r"(?<!项)(?<!商)(?<!购)(?<!销)名\s*称\s*[:：]?\s*([^\s:：]{4,40}?)(?=\s{2,}|\n|$)",
            text):
        name = name.strip()
        if name and name not in stoplist and name not in names:
            names.append(name)
    ids = re.findall(
        r"(?:统一社会信用代码|纳税人识别号|税\s*号)[^0-9A-Z]{0,12}([0-9A-Z]{15,20})", text)

    buyer = names[0] if names else ""
    seller = names[1] if len(names) > 1 else ""
    buyer_id = seller_id = ""
    if len(ids) == len(names) and len(ids) >= 2:
        buyer_id, seller_id = ids[0], ids[1]
    elif len(ids) >= 2:
        buyer_id, seller_id = ids[0], ids[-1]
    elif len(ids) == 1:
        seller_id = ids[0]
        warnings.append("税号仅识别到 1 个，按销售方税号记（请人工核对）")
    if not seller:
        warnings.append("销售方未识别")
    if buyer and "东华" not in buyer:
        warnings.append(f"购买方「{buyer}」非东华系（请核对是否为报销发票）")
    return buyer, buyer_id, seller, seller_id, warnings


def extract_fields(text: str) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    fields: dict[str, Any] = {}

    m = re.search(r"发票号码[^\d]{0,12}(\d{8,20})", text) or \
        re.search(r"票\s*号[^\d]{0,12}(\d{8,20})", text)
    if m:
        fields["发票号码"] = m.group(1)
    else:
        warnings.append("发票号码未识别（OCR 缺失）")

    m = re.search(r"开票日期[^\d]{0,8}(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if m:
        fields["开票日期"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    else:
        m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", text)
        if m:
            fields["开票日期"] = m.group(0)
        else:
            warnings.append("开票日期未识别")

    m = re.search(r"价税合计[^0-9]{0,24}[￥¥]?\s*([\d,]+\.\d{2})", text) or \
        re.search(r"[（(]小写[)）][^0-9]{0,12}[￥¥]?\s*([\d,]+\.\d{2})", text)
    if m:
        fields["价税合计"] = _money(m.group(1))
    else:
        warnings.append("价税合计未识别")

    m = re.search(r"(\*[^\*\n]{1,20}\*[^\s\*\n]{1,20})", text)
    if m:
        fields["商品名称"] = m.group(1)
    else:
        warnings.append("商品名称未识别（分类走默认）")

    buyer, buyer_id, seller, seller_id, party_warnings = _parties(text)
    warnings.extend(party_warnings)
    if buyer:
        fields["购买方"] = buyer
    if seller:
        fields["销售方"] = seller
    if buyer_id:
        fields["购买方税号"] = buyer_id
    if seller_id:
        fields["销售方税号"] = seller_id
    return fields, warnings


# ---- 分类（整合 dhcc invoice_classifier） ----

def _dhcc_classify(record: dict[str, Any]) -> dict[str, Any]:
    target = dhcc_dir() / "invoice_classifier.py"
    if not target.is_file():
        raise FileNotFoundError(str(target))
    inserted = str(dhcc_dir()) not in sys.path
    if inserted:
        sys.path.insert(0, str(dhcc_dir()))
    try:
        name = "dhcc_invoice_classifier"
        if name in sys.modules:
            mod = sys.modules[name]
        else:
            spec = importlib.util.spec_from_file_location(name, target)
            if spec is None or spec.loader is None:
                raise RuntimeError("invoice_classifier 加载失败")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        return mod.classify_invoice(record)
    finally:
        if inserted:
            try:
                sys.path.remove(str(dhcc_dir()))
            except ValueError:
                pass


def _fallback_classify(record: dict[str, Any]) -> dict[str, Any]:
    description = f"{record.get('商品名称', '')} {record.get('销售方', '')}"
    for keywords, category in _EXPENSE_CATEGORY_MAP:
        if any(k in description for k in keywords):
            record["分类"] = "expense"
            record["报销类别"] = category
            record["出差信息"] = None
            return record
    if any(k in description for k in _TRAVEL_KEYWORDS):
        record["分类"] = "travel"
        record["报销类别"] = None
    else:
        record["分类"] = "expense"
        record["报销类别"] = "【普】业务费"
    record.setdefault("出差信息", None)
    return record


def classify(record: dict[str, Any]) -> dict[str, Any]:
    try:
        result = _dhcc_classify(record)
        if result.get("分类"):
            return result
    except Exception:  # noqa: BLE001 — 分类器不可用走同规则兜底，不阻断识别
        pass
    return _fallback_classify(record)


# ---- 识别入口 ----

def recognize_file(path: Path) -> dict[str, Any]:
    """识别单张发票（PDF/图片）→ 结构化记录（缺字段进 warnings，不瞎编）。"""
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"发票文件不存在: {path}")
    if path.suffix.lower() not in (PDF_EXTS | IMAGE_EXTS):
        raise ValueError(f"不支持的发票文件类型: {path.suffix}")

    with tempfile.TemporaryDirectory(prefix="invoice_rec_") as tmp:
        image_path = _render_first_page(path, str(Path(tmp) / "page"))
        qr_fields: dict[str, Any] = {}
        method = []
        if image_path:
            payload = decode_qr(image_path)
            if payload:
                qr_fields = parse_qr_payload(payload)
                method.append("qr")
        text, text_source = extract_text(path, image_path)
        warnings: list[str] = []
        if text:
            method.append(text_source)
            ocr_fields, warnings = extract_fields(text)
        else:
            ocr_fields, warnings = {}, ["文本层为空（无法 OCR）"]

    record: dict[str, Any] = {
        "发票类型": "全电发票" if len(str(qr_fields.get("发票号码", ""))) == 20 else "",
        "发票号码": qr_fields.get("发票号码") or ocr_fields.get("发票号码", ""),
        "开票日期": qr_fields.get("开票日期") or ocr_fields.get("开票日期", ""),
        "价税合计": qr_fields.get("价税合计") if qr_fields.get("价税合计") is not None
                    else ocr_fields.get("价税合计"),
        "校验码": qr_fields.get("校验码", ""),
        "购买方": ocr_fields.get("购买方", ""),
        "购买方税号": ocr_fields.get("购买方税号", ""),
        "销售方": ocr_fields.get("销售方", ""),
        "销售方税号": ocr_fields.get("销售方税号", ""),
        "商品名称": ocr_fields.get("商品名称", ""),
        "原始文件": str(path),
        "识别方式": "+".join(method) if method else "none",
    }
    if not record["发票号码"]:
        warnings.append("发票号码缺失——记录保留待人工补全")
    if qr_fields and text_source == "none":
        warnings.append("仅二维码识别，销售方/商品名称待人工补全")
    record["warnings"] = warnings
    return classify(record)


def recognize_paths(file: str = "", dir: str = "") -> list[dict[str, Any]]:
    """识别入口：单文件或目录（目录只认 PDF/图片，按名排序）。"""
    targets: list[Path] = []
    if file:
        targets.append(Path(file))
    if dir:
        base = Path(dir)
        if not base.is_dir():
            raise NotADirectoryError(f"目录不存在: {base}")
        targets.extend(sorted(
            p for p in base.iterdir()
            if p.is_file() and p.suffix.lower() in (PDF_EXTS | IMAGE_EXTS)))
    if not targets:
        if dir and Path(dir).expanduser().is_dir():
            return []          # 目录里没有发票：count=0 走 decision else 分支
        raise ValueError("未指定发票文件：请传 file（单张）或 dir（目录）")
    return [recognize_file(p) for p in targets]


def to_json(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, ensure_ascii=False, default=str)
