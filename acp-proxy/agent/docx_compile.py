# -*- coding: utf-8 -*-
"""docx_compile.py — C4 OpenXML 确定性编译管线（python-docx 深排版）。

通用文档编译能力（公文/合同/方案/课件/标书——一切格式强约束交付）：
LLM 只出 JSON AST → 本模块确定性渲染 docx，排版不猜：

- **三线表**（顶线/栏目线/底线，无竖线）+ **跨页续表头**（w:tblHeader 重复
  表头行 + w:cantSplit 行不跨页拆）+ 表题（"表 X-X …"）；
- **页眉页脚分节**（节间 header/footer 独立断链，页脚 PAGE 页码域，横向节）；
- **修订留痕**（w:ins/w:del + 作者/时间；段内 segments 支持 普通/新增/删除 混排）：
  **来源血缘** = 修订块 source 字段 → 批注（可锚定时）+ <文件>.provenance.json
  血缘侧车（落盘可审计）；
- **拒绝全部修订自校验**（LingYan 口径）：reject-all 视图与原文逐字一致才许交付
  （revision_views / verify_reject_all）。

确定性纪律：同样 AST 恒出同样文档（时间戳/修订者显式传入）；参数非法显式
ValueError 报因。纯 python-docx + lxml，无网络无子进程。
独立可测（tests/test_docx_compile.py）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

__all__ = [
    "BLOCK_TYPES",
    "compile_docx",
    "revision_views",
    "verify_reject_all",
]

BLOCK_TYPES = ("heading", "paragraph", "bullet", "table", "pagebreak")
_SEGMENT_MODES = ("normal", "ins", "del")

_THREE_LINE = "three_line"
_GRID = "grid"

_AUTHOR_DEFAULT = "SoulMate"
_REVISION_DATE_DEFAULT = "2026-01-01T00:00:00Z"


# ── 入口 ─────────────────────────────────────────────────────────────

def compile_docx(spec: dict, out_path, author: str = _AUTHOR_DEFAULT) -> dict:
    """AST → docx 确定性编译。

    spec 形状：
    {"title"?: str, "font"?: {"east_asia": "宋体", "size_pt": 12},
     "sections": [{"header"?: {"text", "align"?}, "footer"?: {"text"?,
                   "page_number"?: true, "align"?}, "landscape"?: bool,
                   "content": [block...]}...]}
    或简式 {"content": [block...]}（单节无页眉页脚）。

    block：
    - {"type": "heading", "level": 1-4, "text": str}
    - {"type": "paragraph", "text": str | "segments": [{text, mode?:
        normal|ins|del, author?, date?, source?}], "align"?, "bold"?,
        "first_line_indent_chars"?}
    - {"type": "bullet", "text": str}
    - {"type": "table", "rows": [[...]], "style": three_line|grid,
        "caption"?, "header_repeat"?: true, "col_widths"?: [cm,...]}
    - {"type": "pagebreak"}

    返回 {"path", "blocks", "tables", "sections", "revisions", "provenance",
          "trace_comments", "reject_all_verified"}
    """
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError as e:
        raise RuntimeError(f"python-docx 不可用: {e}") from e
    out = Path(out_path)
    spec = _validate_spec(spec)

    doc = Document()
    font = spec.get("font") or {}
    font_size = float(font.get("size_pt", 12) or 12)
    _set_normal_font(doc, str(font.get("east_asia", "宋体") or "宋体"), font_size)

    ctx = {
        "doc": doc, "author": author, "font_size": font_size,
        "counts": {"blocks": 0, "tables": 0, "headings": 0, "paragraphs": 0,
                   "bullets": 0},
        "revisions": [],        # 来源血缘条目
        "rev_id": 0,
        "trace_comments": 0,
        "expected_original": [],   # reject-all 应得原文（渲染级跟踪，回读对账）
    }
    if spec.get("title"):
        title = str(spec["title"])
        p = doc.add_paragraph(title)
        try:
            p.style = "Title"
        except KeyError:
            pass
        ctx["expected_original"].append(title)
        ctx["counts"]["blocks"] += 1
    sections = spec.get("sections")
    if not sections:
        sections = [{"content": spec["content"]}]
    for si, sec in enumerate(sections):
        section = doc.sections[0] if si == 0 else doc.add_section()
        _configure_section(ctx, section, sec, si)
        for bi, block in enumerate(sec["content"]):
            _render_block(ctx, block, si, bi)

    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))

    provenance_path = None
    if ctx["revisions"]:
        provenance_path = Path(str(out) + ".provenance.json")
        provenance_path.write_text(json.dumps({
            "document": str(out),
            "author": author,
            "revision_count": len(ctx["revisions"]),
            "revisions": ctx["revisions"],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    verified = None
    if ctx["revisions"]:
        # 拒绝全部修订 ≡ 原文自校验（LingYan 口径）：w:ins/w:del 写出后回读
        # reject-all 视图，与渲染级跟踪的应得原文逐字对账（防 delText 写成
        # w:t、ins 泄漏进原文等 OpenXML 级手误）
        views = revision_views(str(out))
        expected = "\n".join(ctx["expected_original"])
        verified = _squash(views["original"]) == _squash(expected)

    return {
        "path": str(out),
        "blocks": ctx["counts"]["blocks"],
        "tables": ctx["counts"]["tables"],
        "sections": len(sections),
        "revisions": len(ctx["revisions"]),
        "provenance": str(provenance_path) if provenance_path else None,
        "trace_comments": ctx["trace_comments"],
        "reject_all_verified": verified,
    }


# ── 校验 ─────────────────────────────────────────────────────────────

def _validate_spec(spec) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("spec 必须是对象（AST）")
    if "sections" in spec:
        sections = spec["sections"]
        if not isinstance(sections, list) or not sections:
            raise ValueError("sections 需要非空列表")
    elif "content" in spec:
        sections = None
    else:
        raise ValueError("spec 需要 content（单节）或 sections（分节）")
    if sections is not None:
        for si, sec in enumerate(sections):
            if not isinstance(sec, dict):
                raise ValueError(f"sections[{si}] 必须是对象")
            _validate_content(sec.get("content"), f"sections[{si}].content")
    else:
        _validate_content(spec.get("content"), "content")
    return spec


def _validate_content(content, where: str) -> None:
    if not isinstance(content, list) or not content:
        raise ValueError(f"{where} 需要非空块列表")
    for i, block in enumerate(content):
        if not isinstance(block, dict):
            raise ValueError(f"{where}[{i}] 必须是对象 {{type, ...}}")
        btype = str(block.get("type", "") or "").strip().lower()
        if btype not in BLOCK_TYPES:
            raise ValueError(f"{where}[{i}] 未知块类型 {btype!r}，支持: {BLOCK_TYPES}")
        if btype == "table":
            rows = block.get("rows")
            if not isinstance(rows, list) or not rows:
                raise ValueError(f"{where}[{i}] table 需要非空 rows 二维列表")
            for ri, row in enumerate(rows):
                if not isinstance(row, (list, tuple)):
                    raise ValueError(f"{where}[{i}].rows[{ri}] 必须是列表")
        elif btype == "pagebreak":
            pass
        elif btype == "paragraph":
            segs = block.get("segments")
            if segs is not None:
                if not isinstance(segs, list) or not segs:
                    raise ValueError(f"{where}[{i}] segments 需要非空列表")
                for si, seg in enumerate(segs):
                    if not isinstance(seg, dict) or "text" not in seg:
                        raise ValueError(f"{where}[{i}].segments[{si}] 须含 text")
                    mode = str(seg.get("mode", "normal") or "normal")
                    if mode not in _SEGMENT_MODES:
                        raise ValueError(
                            f"{where}[{i}].segments[{si}] mode 非法: {mode!r}"
                            f"（支持 {_SEGMENT_MODES}）")
            elif not str(block.get("text", "") or "").strip():
                raise ValueError(f"{where}[{i}] paragraph 需要 text 或 segments")
        else:
            if not str(block.get("text", "") or "").strip():
                raise ValueError(f"{where}[{i}]（{btype}）text 为空")


# ── 样式/节 ──────────────────────────────────────────────────────────

def _set_normal_font(doc, east_asia: str, size_pt: float) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt
    style = doc.styles["Normal"]
    style.font.size = Pt(size_pt)
    style.font.name = east_asia
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        from docx.oxml import OxmlElement
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:eastAsia"), east_asia)


def _configure_section(ctx, section, sec: dict, si: int) -> None:
    from docx.enum.section import WD_ORIENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    if sec.get("landscape"):
        section.orientation = WD_ORIENT.LANDSCAPE
        w, h = section.page_width, section.page_height
        if w is not None and h is not None and w < h:
            section.page_width, section.page_height = h, w
    header = sec.get("header")
    if header:
        section.header.is_linked_to_previous = False
        hp = section.header.paragraphs[0]
        hp.text = str(header.get("text", "") or "")
        hp.alignment = _align(header.get("align"), WD_ALIGN_PARAGRAPH.CENTER)
    footer = sec.get("footer")
    if footer:
        section.footer.is_linked_to_previous = False
        fp = section.footer.paragraphs[0]
        fp.alignment = _align(footer.get("align"), WD_ALIGN_PARAGRAPH.CENTER)
        text = str(footer.get("text", "") or "")
        if text:
            fp.add_run(text)
        if footer.get("page_number"):
            _append_page_field(fp)
        elif not text:
            fp.add_run("")


def _align(value, default):
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    return {
        "left": WD_ALIGN_PARAGRAPH.LEFT,
        "center": WD_ALIGN_PARAGRAPH.CENTER,
        "right": WD_ALIGN_PARAGRAPH.RIGHT,
        "justify": WD_ALIGN_PARAGRAPH.JUSTIFY,
    }.get(str(value or "").strip().lower(), default)


def _append_page_field(paragraph) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = " PAGE "
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    run._r.append(begin)
    run._r.append(instr)
    run._r.append(end)


# ── 块渲染 ───────────────────────────────────────────────────────────

def _render_block(ctx, block: dict, si: int, bi: int) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    doc = ctx["doc"]
    btype = str(block.get("type", "") or "").strip().lower()
    ctx["counts"]["blocks"] += 1

    if btype == "pagebreak":
        doc.add_page_break()
        return
    if btype == "heading":
        level = min(max(int(block.get("level", 2) or 2), 1), 4)
        doc.add_heading(str(block["text"]), level=level)
        ctx["counts"]["headings"] += 1
        ctx["expected_original"].append(str(block["text"]))
        return
    if btype == "bullet":
        doc.add_paragraph(str(block["text"]), style="List Bullet")
        ctx["counts"]["bullets"] += 1
        ctx["expected_original"].append(str(block["text"]))
        return
    if btype == "table":
        _render_table(ctx, block, si, bi)
        return

    # paragraph（text 或 segments 混排修订）
    p = doc.add_paragraph()
    if block.get("align"):
        p.alignment = _align(block.get("align"), WD_ALIGN_PARAGRAPH.LEFT)
    indent_chars = float(block.get("first_line_indent_chars", 0) or 0)
    if indent_chars > 0:
        p.paragraph_format.first_line_indent = Pt(ctx["font_size"] * indent_chars)
    segs = block.get("segments")
    if segs is None:
        run = p.add_run(str(block["text"]))
        if block.get("bold"):
            run.bold = True
        ctx["expected_original"].append(str(block["text"]))
    else:
        _render_segments(ctx, p, segs, si, bi)
        ctx["expected_original"].append("".join(
            str(s.get("text", "") or "")
            for s in segs
            if str(s.get("mode", "normal") or "normal") != "ins"))
    ctx["counts"]["paragraphs"] += 1


def _render_segments(ctx, p, segs: list, si: int, bi: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = ctx["doc"]
    anchor_runs = []          # 直属 run（批注锚点候选）
    for s_i, seg in enumerate(segs):
        text = str(seg.get("text", "") or "")
        mode = str(seg.get("mode", "normal") or "normal")
        if not text:
            continue
        if mode == "normal":
            anchor_runs.append(p.add_run(text))
            continue
        ctx["rev_id"] += 1
        rev_id = ctx["rev_id"]
        author = str(seg.get("author", "") or ctx["author"])
        date = str(seg.get("date", "") or _REVISION_DATE_DEFAULT)
        if mode == "ins":
            run = p.add_run(text)
            p._p.remove(run._r)
            ins = OxmlElement("w:ins")
            ins.set(qn("w:id"), str(rev_id))
            ins.set(qn("w:author"), author)
            ins.set(qn("w:date"), date)
            ins.append(run._r)
            p._p.append(ins)
        else:  # del
            d = OxmlElement("w:del")
            d.set(qn("w:id"), str(rev_id))
            d.set(qn("w:author"), author)
            d.set(qn("w:date"), date)
            r = OxmlElement("w:r")
            t = OxmlElement("w:delText")
            t.set(qn("xml:space"), "preserve")
            t.text = text
            r.append(t)
            d.append(r)
            p._p.append(d)
        source = str(seg.get("source", "") or "")
        ctx["revisions"].append({
            "rev_id": rev_id,
            "location": f"sections[{si}].content[{bi}].segments[{s_i}]",
            "mode": mode,
            "author": author,
            "date": date,
            "source": source,
            "text": text,
        })
        # 来源血缘批注（有直属 run 可锚定时；否则血缘走侧车）
        if source and anchor_runs:
            try:
                doc.add_comment(
                    anchor_runs[-1],
                    text=f"修订溯源（{'新增' if mode == 'ins' else '删除'}）：{text}"
                         f"｜来源：{source}",
                    author=ctx["author"], initials="SM")
                ctx["trace_comments"] += 1
            except Exception:  # noqa: BLE001 — 批注失败不阻断编译，血缘侧车兜底
                pass


def _render_table(ctx, block: dict, si: int, bi: int) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm
    doc = ctx["doc"]
    rows = block["rows"]
    ncols = max(len(r) for r in rows)
    style = str(block.get("style", _THREE_LINE) or _THREE_LINE)
    caption = str(block.get("caption", "") or "")
    if caption:
        from docx.enum.text import WD_ALIGN_PARAGRAPH as _WDA
        cap = doc.add_paragraph(caption)
        try:
            cap.style = "Caption"
        except KeyError:
            pass
        cap.alignment = _WDA.CENTER
        ctx["expected_original"].append(caption)
    table = doc.add_table(rows=len(rows), cols=ncols)
    if style == _GRID:
        table.style = "Table Grid"
    for ri, row in enumerate(rows):
        for ci in range(ncols):
            cell = table.rows[ri].cells[ci]
            cell.text = str(row[ci]) if ci < len(row) else ""
            ctx["expected_original"].append(
                str(row[ci]) if ci < len(row) else "")
    header_repeat = bool(block.get("header_repeat", True))
    if style == _THREE_LINE:
        _apply_three_line(table)
    if header_repeat:
        _apply_header_repeat(table)
    widths = block.get("col_widths")
    if isinstance(widths, (list, tuple)):
        for ci, w in enumerate(widths):
            if ci < ncols:
                for cell in table.columns[ci].cells:
                    cell.width = Cm(float(w))
    ctx["counts"]["tables"] += 1


def _apply_three_line(table) -> None:
    """三线表：顶线/底线粗、栏目线（表头下）细，无竖线无横格线。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge, val, sz in (("top", "single", "12"), ("bottom", "single", "12"),
                          ("left", "none", None), ("right", "none", None),
                          ("insideH", "none", None), ("insideV", "none", None)):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), val)
        if sz:
            el.set(qn("w:sz"), sz)
        el.set(qn("w:color"), "000000")
        borders.append(el)
    tbl_pr.append(borders)
    # 栏目线：表头行单元格底线
    for cell in table.rows[0].cells:
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_borders = OxmlElement("w:tcBorders")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:color"), "000000")
        tc_borders.append(bottom)
        tc_pr.append(tc_borders)


def _apply_header_repeat(table) -> None:
    """跨页续表头：表头行 w:tblHeader（Word 自动在续页重复）+ 行内不拆 w:cantSplit。"""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    for ri, row in enumerate(table.rows):
        tr_pr = row._tr.get_or_add_trPr()
        cant = OxmlElement("w:cantSplit")
        tr_pr.append(cant)
        if ri == 0:
            th = OxmlElement("w:tblHeader")
            th.set(qn("w:val"), "true")
            tr_pr.append(th)


# ── 修订视图 / 拒绝全部修订自校验 ────────────────────────────────────

def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _para_views(p_el) -> tuple[str, str]:
    """段落元素 → (reject-all 原文, accept-all 修订后)。"""
    from docx.oxml.ns import qn
    orig: list[str] = []
    rev: list[str] = []
    for child in p_el:
        tag = child.tag
        if tag == qn("w:r"):
            for t in child.findall(qn("w:t")):
                orig.append(t.text or "")
                rev.append(t.text or "")
        elif tag == qn("w:ins"):
            for t in child.iter(qn("w:t")):
                rev.append(t.text or "")
        elif tag == qn("w:del"):
            for t in child.iter(qn("w:delText")):
                orig.append(t.text or "")
    return "".join(orig), "".join(rev)


def revision_views(docx_path) -> dict:
    """文档修订双视图：{"original": 拒绝全部修订, "revised": 接受全部修订}。

    正文段落+表格内段落按文档顺序展开，逐段拼接（换行连接）。
    """
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(str(docx_path))
    body = doc.element.body
    orig_lines: list[str] = []
    rev_lines: list[str] = []
    for p_el in body.iter(qn("w:p")):
        o, r = _para_views(p_el)
        if o or r:
            orig_lines.append(o)
            rev_lines.append(r)
    return {"original": "\n".join(orig_lines), "revised": "\n".join(rev_lines)}


def verify_reject_all(original_text: str, docx_path) -> bool:
    """拒绝全部修订后与原文逐字一致自校验（忽略空白差异）。"""
    views = revision_views(docx_path)
    return _squash(views["original"]) == _squash(original_text)
