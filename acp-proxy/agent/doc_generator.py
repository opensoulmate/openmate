"""路线图 7.2 文档生成（docx/pdf/xlsx）。

DocGenerator：确定性文档生成面，不走 LLM——
- docx：python-docx（标题 + 段落 + 表格，Table Grid 边框）
- xlsx：openpyxl（多 sheet 行数据，数字/文本保型写入）
- pdf：docx 中间产物 + LibreOffice headless 转换（真实文本 PDF，中文可选中；
  soffice 独立 UserInstallation 防与用户桌面 LibreOffice 抢锁）

设计纪律（对齐 5.x/6.x/7.1 各模块）：
- 全确定性，不走 LLM；转换器可注入（soffice_runner 接缝），测试零外部依赖。
- 一切失败显式报因：格式/路径/内容校验拒绝执行并说明原因；soffice 缺失或
  转换失败带 stderr 摘要，绝不静默返回"成功"、绝不产出半截文件（tmp+os.replace）。
- 输出路径越界拒绝（默认仅允许 $HOME 与 /tmp 下，SOULMATE_DOC_ROOTS 可覆盖）。
- 成功结果带 MEDIA:{path} 标签（前端渲染下载卡片，与截图/文件发送同款链路）。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable

# 支持的格式（docx 为 pdf 的中间产物，也独立可输出）
FORMATS = ("docx", "pdf", "xlsx")
_EXT_OF = {"docx": ".docx", "pdf": ".pdf", "xlsx": ".xlsx"}

# 内容规模上限（超限显式拒绝，不静默截断）
MAX_PARAGRAPHS = 2000
MAX_TABLES = 50
MAX_TABLE_ROWS = 2000
MAX_SHEETS = 50
MAX_SHEET_ROWS = 5000

# soffice 转换超时（秒），env 可覆盖
SOFFICE_TIMEOUT_S = float(os.environ.get("SOULMATE_DOC_SOFFICE_TIMEOUT_S", "90"))

# Excel sheet 名非法字符（openpyxl 拒绝 []:*?/\\）与长度上限
_SHEET_BAD_CHARS = re.compile(r"[\[\]:*?/\\]")


class DocGeneratorError(RuntimeError):
    """文档生成失败（校验/依赖/转换），消息即给用户/模型的报因文本。"""


def _allowed_roots() -> list[str]:
    """输出路径允许的根目录（默认 $HOME 与 /tmp；SOULMATE_DOC_ROOTS 覆盖）。"""
    raw = os.environ.get("SOULMATE_DOC_ROOTS", "").strip()
    roots = [r for r in raw.split(os.pathsep) if r.strip()] if raw \
        else [str(Path.home()), "/tmp"]
    return [os.path.realpath(os.path.expanduser(r)) for r in roots]


def _resolve_target(path: str, fmt: str) -> Path:
    """校验并解析输出路径：空值拒绝、扩展名错配拒绝、越界拒绝。"""
    p = str(path or "").strip()
    if not p:
        raise DocGeneratorError("path 参数不能为空")
    if fmt not in FORMATS:
        raise DocGeneratorError(f"不支持的格式: {fmt!r}（可选: {', '.join(FORMATS)}）")
    p = os.path.expanduser(p)
    suffix = Path(p).suffix.lower()
    want = _EXT_OF[fmt]
    if not suffix:
        p = p + want  # 无扩展名自动补
    elif suffix != want:
        raise DocGeneratorError(
            f"路径扩展名 {suffix} 与格式 {fmt} 不匹配（应为 {want}）")
    parent = os.path.dirname(os.path.abspath(p)) or "."
    os.makedirs(parent, exist_ok=True)
    real = os.path.realpath(p)
    roots = _allowed_roots()
    if not any(real == r or real.startswith(r + os.sep) for r in roots):
        raise DocGeneratorError(
            f"输出路径越界拒绝: {real}（允许根目录: {os.pathsep.join(roots)}，"
            f"可用 SOULMATE_DOC_ROOTS 配置）")
    return Path(real)


def _normalize_paragraphs(content: Any) -> list[str]:
    """段落内容归一：str 按行拆 / list[str] 保序；空内容显式拒绝。"""
    if isinstance(content, str):
        paras = [ln.strip() for ln in content.splitlines() if ln.strip()]
    elif isinstance(content, (list, tuple)):
        paras = []
        for item in content:
            s = str(item or "").strip()
            if s:
                paras.append(s)
    else:
        raise DocGeneratorError(f"content 形状不对（应为字符串或字符串列表）: {type(content).__name__}")
    if not paras:
        raise DocGeneratorError("content 不能为空（至少一个段落）")
    if len(paras) > MAX_PARAGRAPHS:
        raise DocGeneratorError(f"段落数超限: {len(paras)} > {MAX_PARAGRAPHS}")
    return paras


def _normalize_tables(tables: Any) -> list[dict]:
    """表格归一：[{headers: [...], rows: [[...]]}]；形状错显式拒绝。"""
    if tables is None:
        return []
    if not isinstance(tables, (list, tuple)):
        raise DocGeneratorError(f"tables 形状不对（应为列表）: {type(tables).__name__}")
    if len(tables) > MAX_TABLES:
        raise DocGeneratorError(f"表格数超限: {len(tables)} > {MAX_TABLES}")
    out: list[dict] = []
    for i, tb in enumerate(tables):
        if not isinstance(tb, dict):
            raise DocGeneratorError(f"tables[{i}] 应为对象 {{headers, rows}}")
        headers = tb.get("headers") or []
        rows = tb.get("rows") or []
        if not isinstance(headers, (list, tuple)) or not headers:
            raise DocGeneratorError(f"tables[{i}].headers 不能为空列表")
        if not isinstance(rows, (list, tuple)):
            raise DocGeneratorError(f"tables[{i}].rows 应为列表")
        if len(rows) > MAX_TABLE_ROWS:
            raise DocGeneratorError(
                f"tables[{i}] 行数超限: {len(rows)} > {MAX_TABLE_ROWS}")
        norm_rows = []
        for r in rows:
            if not isinstance(r, (list, tuple)):
                raise DocGeneratorError(f"tables[{i}] 每行应为列表，收到 {type(r).__name__}")
            norm_rows.append(list(r))
        out.append({"headers": [str(h or "") for h in headers], "rows": norm_rows})
    return out


def _normalize_sheets(sheets: Any) -> list[dict]:
    """xlsx sheets 归一：[{name, rows: [[...]]}]；名字清洗为 Excel 合法名。"""
    if not isinstance(sheets, (list, tuple)) or not sheets:
        raise DocGeneratorError("sheets 不能为空（xlsx 需要 [{name, rows}]）")
    if len(sheets) > MAX_SHEETS:
        raise DocGeneratorError(f"sheet 数超限: {len(sheets)} > {MAX_SHEETS}")
    out: list[dict] = []
    used_names: set[str] = set()
    for i, sh in enumerate(sheets):
        if not isinstance(sh, dict):
            raise DocGeneratorError(f"sheets[{i}] 应为对象 {{name, rows}}")
        rows = sh.get("rows") or []
        if not isinstance(rows, (list, tuple)) or not rows:
            raise DocGeneratorError(f"sheets[{i}].rows 不能为空")
        if len(rows) > MAX_SHEET_ROWS:
            raise DocGeneratorError(
                f"sheets[{i}] 行数超限: {len(rows)} > {MAX_SHEET_ROWS}")
        for r in rows:
            if not isinstance(r, (list, tuple)):
                raise DocGeneratorError(
                    f"sheets[{i}] 每行应为列表，收到 {type(r).__name__}")
        name = _SHEET_BAD_CHARS.sub("_", str(sh.get("name", "") or "").strip())[:31]
        if not name:
            name = f"Sheet{i + 1}"
        base, n = name, 2
        while name in used_names:  # openpyxl 拒绝重名 sheet
            name = f"{base[:28]}_{n}"
            n += 1
        used_names.add(name)
        out.append({"name": name, "rows": [list(r) for r in rows]})
    return out


def _atomic_save(save_fn: Callable[[Path], None], target: Path) -> None:
    """tmp + os.replace 原子落盘，不留半截文件。"""
    tmp = target.parent / f".{target.name}.{os.getpid()}.tmp{target.suffix}"
    try:
        save_fn(tmp)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _default_soffice_runner(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """缺省 soffice 执行体：独立 UserInstallation 防与桌面 LibreOffice 抢锁。"""
    return subprocess.run(
        args, capture_output=True, text=True, errors="replace", timeout=timeout)


class DocGenerator:
    """确定性文档生成器。生成方法返回用户可读结果文本（成功带 MEDIA 标签）。"""

    def __init__(self, soffice_runner: Callable[..., Any] | None = None):
        self._soffice_runner = soffice_runner or _default_soffice_runner

    # ------------------------------------------------------------------
    # 生成面
    # ------------------------------------------------------------------

    def generate(self, fmt: str, path: str, **kwargs: Any) -> str:
        """按格式生成文档，返回确定性文本结果（失败返回 "[错误] …" 文本）。"""
        fmt = str(fmt or "").strip().lower()
        try:
            if fmt == "docx":
                target = _resolve_target(path, "docx")
                paras = _normalize_paragraphs(kwargs.get("content"))
                tables = _normalize_tables(kwargs.get("tables"))
                size = self._write_docx(target, str(kwargs.get("title", "") or ""),
                                        paras, tables)
            elif fmt == "pdf":
                target = _resolve_target(path, "pdf")
                paras = _normalize_paragraphs(kwargs.get("content"))
                tables = _normalize_tables(kwargs.get("tables"))
                size = self._write_pdf(target, str(kwargs.get("title", "") or ""),
                                       paras, tables)
            elif fmt == "xlsx":
                target = _resolve_target(path, "xlsx")
                sheets = _normalize_sheets(kwargs.get("sheets"))
                size = self._write_xlsx(target, sheets)
            else:
                return (
                    f"[错误] 不支持的格式: {fmt or '(空)'}（可选: {', '.join(FORMATS)}）")
        except DocGeneratorError as e:
            return f"[错误] 文档生成失败: {e}"
        except Exception as e:  # 依赖缺失/IO 异常同样显式报因
            return f"[错误] 文档生成异常: {e}"
        kind = {"docx": "Word 文档", "pdf": "PDF 文档", "xlsx": "Excel 表格"}[fmt]
        return (
            f"✅ {kind}已生成: {target}（{size} 字节）\n"
            f"MEDIA:{target}")

    def _write_docx(self, target: Path, title: str, paras: list[str],
                    tables: list[dict]) -> int:
        try:
            from docx import Document
            from docx.shared import Pt
        except ImportError as e:
            raise DocGeneratorError(f"python-docx 不可用: {e}")

        def save(tmp: Path) -> None:
            doc = Document()
            if title.strip():
                doc.add_heading(title.strip(), level=0)
            for p in paras:
                doc.add_paragraph(p)
            for tb in tables:
                table = doc.add_table(rows=1 + len(tb["rows"]),
                                      cols=len(tb["headers"]))
                table.style = "Table Grid"
                for c, h in enumerate(tb["headers"]):
                    cell = table.rows[0].cells[c]
                    cell.text = h
                    for run in cell.paragraphs[0].runs:
                        run.bold = True
                for r, row in enumerate(tb["rows"]):
                    for c in range(len(tb["headers"])):
                        val = row[c] if c < len(row) else ""
                        table.rows[r + 1].cells[c].text = "" if val is None else str(val)
            doc.save(str(tmp))

        _atomic_save(save, target)
        return target.stat().st_size

    def _write_xlsx(self, target: Path, sheets: list[dict]) -> int:
        try:
            from openpyxl import Workbook
        except ImportError as e:
            raise DocGeneratorError(f"openpyxl 不可用: {e}")

        def save(tmp: Path) -> None:
            wb = Workbook()
            _default = wb.active
            if _default is not None:
                wb.remove(_default)  # 空默认表去掉，sheet 全由数据决定
            for sh in sheets:
                ws = wb.create_sheet(title=sh["name"])
                for row in sh["rows"]:
                    # 数字/文本保型：int/float 原样写数值，其余 str 化
                    ws.append([v if isinstance(v, (int, float)) and not isinstance(v, bool)
                               else ("" if v is None else str(v))
                               for v in row])
            wb.save(str(tmp))

        _atomic_save(save, target)
        return target.stat().st_size

    def _write_pdf(self, target: Path, title: str, paras: list[str],
                   tables: list[dict]) -> int:
        """docx 中间产物 + soffice headless 转 PDF（真实文本，中文可选中）。"""
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise DocGeneratorError(
                "LibreOffice(soffice) 不可用，无法生成 PDF（docx/xlsx 不受影响）")
        with tempfile.TemporaryDirectory(prefix="soulmate_pdf_") as td:
            tdir = Path(td)
            mid = tdir / "middle.docx"
            self._write_docx(mid, title, paras, tables)
            profile = tdir / "lo_profile"
            args = [
                soffice,
                f"-env:UserInstallation=file://{profile}",
                "--headless", "--norestore",
                "--convert-to", "pdf", "--outdir", str(tdir), str(mid),
            ]
            try:
                proc = self._soffice_runner(args, SOFFICE_TIMEOUT_S)
            except subprocess.TimeoutExpired:
                raise DocGeneratorError(
                    f"LibreOffice 转换超时（>{SOFFICE_TIMEOUT_S:.0f}s）")
            rc = getattr(proc, "returncode", 1)
            if rc != 0:
                err = str(getattr(proc, "stderr", "") or "").strip()
                raise DocGeneratorError(
                    f"LibreOffice 转换失败 rc={rc}: {err[-400:] or '(无stderr)'}")
            produced = tdir / "middle.pdf"
            if not produced.is_file() or produced.stat().st_size == 0:
                raise DocGeneratorError("LibreOffice 未产出 PDF 文件")
            tmp = target.parent / f".{target.name}.{os.getpid()}.tmp.pdf"
            try:
                shutil.copyfile(produced, tmp)
                os.replace(tmp, target)
            finally:
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
        return target.stat().st_size


# 模块级共享单例（与 get_browser_automation 同款模式）
_generator: DocGenerator | None = None


def get_doc_generator() -> DocGenerator:
    global _generator
    if _generator is None:
        _generator = DocGenerator()
    return _generator
