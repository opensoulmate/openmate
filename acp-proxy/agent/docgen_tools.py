"""docgen_tools.py — 文档生成工具面（路线图 7.2：docx/pdf/xlsx）。

DocGenTools = 确定性文档生成（结构化 spec → 真实文件），不走 LLM 排版猜测。
- docx：python-docx（服务 venv 实测可用）——heading/paragraph/bullet/table；
- xlsx：openpyxl（服务 venv 实测可用）——多 sheet 行数据，首行可作加粗表头；
- pdf：先生成同款 docx 再 LibreOffice headless 转换（系统实测 /usr/bin/libreoffice
  可用；服务 venv 无 reportlab/fpdf，中文 PDF 走 LibreOffice 字体最稳）。

诚实状态语义（对标 browser_tools 三态纪律，禁静默）：
- 依赖缺失（python-docx/openpyxl 缺失）→ 显式"⚠️ 文档后端不可用"+原因+安装提示；
- PDF 转换后端缺失/失败 → 显式报因（stderr 尾部），不假报成功；
- 参数非法（未知 format/空 content/越界文件名）→ 显式拒绝+正确用法。
"""
import logging
import os
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("acp-agent.docgen-tools")

SUPPORTED_FORMATS = ("docx", "xlsx", "pdf")
BLOCK_TYPES = ("heading", "paragraph", "bullet", "table", "pagebreak")

DEFAULT_OUT_DIR = os.path.expanduser("~/.hermes/soulmate/documents")
SOFFICE_TIMEOUT_S = 90.0

_DOCX_HINT = "安装方法：服务 venv 内 `pip install python-docx`（acp-proxy 服务所用 interpreter）。"
_XLSX_HINT = "安装方法：服务 venv 内 `pip install openpyxl`（acp-proxy 服务所用 interpreter）。"
_PDF_HINT = (
    "安装方法：系统安装 libreoffice（如 `pacman -S libreoffice-fresh`）或改用 "
    "format=docx 生成 Word 文档。"
)

_SAFE_NAME_RE = re.compile(r"^[\w\u4e00-\u9fff][\w\u4e00-\u9fff.\-]*$")


class DocGenTools:
    """确定性文档生成（docx/pdf/xlsx），输出落盘 ~/.hermes/soulmate/documents。"""

    def __init__(self, out_dir: str = "", soffice: str = "",
                 converter=None, now=None):
        self.out_dir = Path(out_dir or DEFAULT_OUT_DIR)
        self.soffice = soffice or os.environ.get("SOULMATE_SOFFICE", "libreoffice")
        # converter 可注入（测试 fake）；缺省用真实 LibreOffice headless
        self._converter = converter or self._soffice_convert
        self._now = now or time.time

    # ── 入口 ─────────────────────────────────────────────────────────

    def run(self, fmt: str, params: dict) -> str:
        fmt = str(fmt or "").strip().lower()
        params = dict(params or {})
        if fmt not in SUPPORTED_FORMATS:
            return (f"❌ 未知 format: {fmt!r}。支持: {', '.join(SUPPORTED_FORMATS)}")
        try:
            out_path = self._resolve_out_path(fmt, params)
        except ValueError as e:
            return f"❌ 参数错误: {e}"
        try:
            if fmt == "docx":
                summary = self._build_docx(out_path, params)
            elif fmt == "xlsx":
                summary = self._build_xlsx(out_path, params)
            else:  # pdf
                return self._build_pdf(out_path, params)
        except ValueError as e:
            return f"❌ 参数错误: {e}"
        except Exception as e:
            logger.exception("docgen failed: fmt=%s", fmt)
            return f"❌ 文档生成失败（{fmt}）: {type(e).__name__}: {e}"
        return self._success(fmt, out_path, summary)

    # ── docx ─────────────────────────────────────────────────────────

    def _build_docx(self, out_path: Path, params: dict) -> dict:
        try:
            from docx import Document
            from docx.shared import Pt
        except ImportError as e:
            raise RuntimeError(f"python-docx 不可用: {e}") from e
        blocks = params.get("content") or []
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("docx 需要非空 content 列表（heading/paragraph/bullet/table 块）")
        doc = Document()
        title = str(params.get("title", "") or "").strip()
        if title:
            doc.add_heading(title, level=0)
        counts = {"heading": 0, "paragraph": 0, "bullet": 0, "table": 0}
        for i, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise ValueError(f"content[{i}] 必须是对象 {{type, text/rows}}")
            btype = str(block.get("type", "") or "").strip().lower()
            if btype not in BLOCK_TYPES:
                raise ValueError(
                    f"content[{i}] 未知块类型 {btype!r}，支持: {', '.join(BLOCK_TYPES)}")
            if btype == "pagebreak":
                doc.add_page_break()
                continue
            if btype == "table":
                rows = block.get("rows") or []
                if not isinstance(rows, list) or not rows:
                    raise ValueError(f"content[{i}] table 需要非空 rows 二维列表")
                table = doc.add_table(rows=len(rows), cols=max(len(r) for r in rows))
                table.style = "Table Grid"
                for ri, row in enumerate(rows):
                    for ci, cell in enumerate(row):
                        table.rows[ri].cells[ci].text = str(cell if cell is not None else "")
                counts["table"] += 1
                continue
            text = str(block.get("text", "") or "")
            if not text.strip():
                raise ValueError(f"content[{i}]（{btype}）text 为空")
            if btype == "heading":
                level = int(block.get("level", 2) or 2)
                level = min(max(level, 1), 4)
                doc.add_heading(text, level=level)
                counts["heading"] += 1
            elif btype == "bullet":
                doc.add_paragraph(text, style="List Bullet")
                counts["bullet"] += 1
            else:
                p = doc.add_paragraph(text)
                for run in p.runs:
                    run.font.size = Pt(11)
                counts["paragraph"] += 1
        doc.save(str(out_path))
        return counts

    # ── xlsx ─────────────────────────────────────────────────────────

    def _build_xlsx(self, out_path: Path, params: dict) -> dict:
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font
        except ImportError as e:
            raise RuntimeError(f"openpyxl 不可用: {e}") from e
        sheets = params.get("sheets")
        if not isinstance(sheets, list) or not sheets:
            rows = params.get("rows")
            if isinstance(rows, list) and rows:
                sheets = [{"name": str(params.get("title", "") or "Sheet1"), "rows": rows}]
            else:
                raise ValueError("xlsx 需要 sheets 列表（[{name, rows}]）或 rows 二维列表")
        wb = Workbook()
        default_sheet = wb.active
        if default_sheet is not None:
            wb.remove(default_sheet)
        total_rows = 0
        for si, sheet in enumerate(sheets):
            if not isinstance(sheet, dict):
                raise ValueError(f"sheets[{si}] 必须是对象 {{name, rows}}")
            rows = sheet.get("rows") or []
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"sheets[{si}] 需要非空 rows 二维列表")
            ws = wb.create_sheet(title=str(sheet.get("name", "") or f"Sheet{si + 1}")[:31])
            header_bold = bool(sheet.get("header_bold", True))
            for ri, row in enumerate(rows):
                if not isinstance(row, (list, tuple)):
                    raise ValueError(f"sheets[{si}].rows[{ri}] 必须是列表")
                for ci, cell in enumerate(row):
                    c = ws.cell(row=ri + 1, column=ci + 1,
                                value=cell if cell is not None else "")
                    if header_bold and ri == 0:
                        c.font = Font(bold=True)
                total_rows += 1
            # 列宽按内容估算（确定性，不猜）
            for ci in range(ws.max_column):
                width = max(
                    (len(str(ws.cell(row=r, column=ci + 1).value or ""))
                     for r in range(1, ws.max_row + 1)),
                    default=8,
                )
                ws.column_dimensions[ws.cell(row=1, column=ci + 1).column_letter].width = (
                    min(max(width + 2, 8), 60))
        wb.save(str(out_path))
        return {"sheets": len(sheets), "rows": total_rows}

    # ── pdf（docx → LibreOffice headless 转换）────────────────────────

    def _build_pdf(self, out_path: Path, params: dict) -> str:
        # 先生成同内容 docx（复用 docx 管线），再转换
        docx_path = out_path.with_suffix(".docx")
        counts = self._build_docx(docx_path, params)
        try:
            conv = self._converter(str(docx_path), str(out_path.parent), self.soffice)
        except RuntimeError as e:
            return (f"⚠️ PDF 转换后端不可用：{e}\n{_PDF_HINT}\n"
                    f"（同内容 docx 已生成: {docx_path}，可先交付 Word 版）")
        except subprocess.TimeoutExpired:
            return (f"⚠️ PDF 转换超时（上限 {SOFFICE_TIMEOUT_S:g}s）：未确认完成。"
                    f"同内容 docx 已生成: {docx_path}")
        ok, detail = conv
        if not ok or not out_path.exists():
            return (f"❌ PDF 转换失败：{detail}\n"
                    f"同内容 docx 已生成: {docx_path}（可先交付 Word 版）")
        return self._success("pdf", out_path, counts,
                             extra=f"同内容 docx: {docx_path}")

    def _soffice_convert(self, docx_path: str, out_dir: str, soffice: str):
        """真实 LibreOffice headless 转换。返回 (ok, detail)。"""
        exe = soffice if Path(soffice).exists() else _which(soffice)
        if not exe:
            raise RuntimeError(f"libreoffice 不存在: {soffice}")
        # 独立 UserInstallation 避免与用户正在跑的 LibreOffice 抢 profile 锁
        profile = f"file:///tmp/soulmate-lo-{int(self._now() * 1000)}"
        cmd = [exe, "--headless", f"-env:UserInstallation={profile}",
               "--convert-to", "pdf", "--outdir", out_dir, docx_path]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", timeout=SOFFICE_TIMEOUT_S)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            return False, f"libreoffice 退出码 {proc.returncode}: {tail}"
        return True, (proc.stdout or "").strip()

    # ── 路径/输出 ────────────────────────────────────────────────────

    def _resolve_out_path(self, fmt: str, params: dict) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        name = str(params.get("filename", "") or "").strip()
        if not name:
            name = f"doc-{int(self._now() * 1000)}.{fmt}"
        if not name.lower().endswith(f".{fmt}"):
            name = f"{name}.{fmt}"
        if "/" in name or "\\" in name or not _SAFE_NAME_RE.match(name):
            raise ValueError(
                f"filename 非法: {name!r}（只允许文件名本身，不含路径分隔符）")
        out_path = (self.out_dir / name).resolve()
        if out_path.parent != self.out_dir.resolve():
            raise ValueError("输出路径越界（必须落在文档目录内）")
        return out_path

    @staticmethod
    def _success(fmt: str, out_path: Path, summary, extra: str = "") -> str:
        size = out_path.stat().st_size if out_path.exists() else 0
        if isinstance(summary, dict):
            detail = "，".join(f"{k} {v}" for k, v in summary.items())
        else:
            detail = str(summary)
        lines = [
            f"✅ 文档已生成（{fmt}）: {out_path}（{size} 字节）",
            f"内容统计: {detail}",
        ]
        if extra:
            lines.append(extra)
        lines.append(f"MEDIA:{out_path}")
        return "\n".join(lines)


def _which(cmd: str):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        cand = Path(d) / cmd
        if cand.exists():
            return str(cand)
    return None
