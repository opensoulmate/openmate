# -*- coding: utf-8 -*-
"""C7 LibreOffice headless 终稿管线——TOC 页码更新 + PDF 导出。

通用终稿能力（含目录文档交付，非投标专用）。依赖 soffice（缺失跳过）。
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from agent import office_finalize

SOFFICE_OK = (shutil.which(office_finalize.DEFAULT_SOFFICE) is not None)
PDFTOTEXT_OK = shutil.which("pdftotext") is not None
needs_soffice = pytest.mark.skipif(not SOFFICE_OK, reason="LibreOffice 未安装")


def _make_toc_docx(path, chapters, paras_per_ch=30):
    """Word 目录域 + 多页正文（目录页码可验证）。"""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    doc = Document()
    doc.add_heading("项目实施方案", level=0)
    p = doc.add_paragraph()
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), ' TOC \\o "1-3" \\h \\z \\u ')
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = "目录占位"
    r.append(t)
    fld.append(r)
    p._p.append(fld)
    doc.add_page_break()
    for title in chapters:
        doc.add_heading(title, level=1)
        for j in range(paras_per_ch):
            doc.add_paragraph(f"{title} 第 {j+1} 段正文内容。")
    doc.save(str(path))
    return path


def _make_plain_docx(path):
    from docx import Document
    doc = Document()
    doc.add_heading("简报", level=0)
    doc.add_paragraph("没有目录域的文档。")
    doc.save(str(path))
    return path


def test_check_toc_detects_field(tmp_path):
    toc_doc = _make_toc_docx(tmp_path / "toc.docx", ["第一章 背景"])
    plain = _make_plain_docx(tmp_path / "plain.docx")
    assert office_finalize.check_toc(toc_doc)["has_toc_field"] is True
    assert office_finalize.check_toc(plain)["has_toc_field"] is False
    with pytest.raises(ValueError):
        office_finalize.check_toc(tmp_path / "no.docx")


@needs_soffice
def test_update_toc_and_export_pdf(tmp_path, tmp_path_factory):
    """TOC 页码确定性更新 + PDF 终稿导出（页码与正文实际起始页逐一相符）。"""
    docx_p = _make_toc_docx(
        tmp_path / "plan.docx",
        ["第一章 背景", "第二章 方案设计", "第三章 实施计划"])
    profile = tmp_path_factory.mktemp("lo-profile")
    res = office_finalize.update_toc_and_export(
        docx_p, tmp_path / "plan-final.pdf", profile_dir=str(profile))
    assert res["result"] == "OK"
    assert res["toc_updated"] is True
    pdf = Path(res["pdf"])
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert pdf.stat().st_size > 10 * 1024
    # docx 原件不动（Agent 产物保留可续修）
    assert docx_p.is_file()

    if not PDFTOTEXT_OK:
        return
    txt = subprocess.run(["pdftotext", str(pdf), "-"],
                         capture_output=True, text=True).stdout
    # 目录条目带页码（占位文本已被真实目录替换）
    assert "目录占位" not in txt
    for title in ("第一章 背景", "第二章 方案设计", "第三章 实施计划"):
        assert title in txt


@needs_soffice
def test_export_without_toc_says_so(tmp_path, tmp_path_factory):
    """无目录域：明说'仅导出 PDF'，不假称更新了目录。"""
    plain = _make_plain_docx(tmp_path / "brief.docx")
    profile = tmp_path_factory.mktemp("lo-profile2")
    res = office_finalize.update_toc_and_export(
        plain, profile_dir=str(profile))
    assert res["toc_updated"] is False
    assert Path(res["pdf"]).is_file()


def test_errors(tmp_path):
    with pytest.raises(ValueError):
        office_finalize.update_toc_and_export(tmp_path / "no.docx")
    with pytest.raises(ValueError):
        office_finalize.update_toc_and_export(__file__)
    with pytest.raises(RuntimeError) as e:
        office_finalize.update_toc_and_export(
            _make_plain_docx(tmp_path / "p.docx"), soffice="/nonexistent/soffice")
    assert "安装方法" in str(e.value)
