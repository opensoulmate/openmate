"""C4 OpenXML 确定性编译（docx_compile.py）：三线表/续表头/分节页眉页脚/修订留痕。

覆盖：AST 全块型编译回读 / 三线表 tblBorders（顶底粗线+栏目线+无竖线）/
跨页续表头 w:tblHeader + w:cantSplit / 分节页眉页脚（断链+PAGE 域）/
修订留痕 w:ins/w:del（delText+作者时间）/ 来源血缘（provenance 侧车+批注）/
拒绝全部修订≡原文自校验 / verify_reject_all 对账 / 参数非法显式报错。
"""

import json
from pathlib import Path

import pytest

from docx import Document
from docx.oxml.ns import qn

from agent.docx_compile import compile_docx, revision_views, verify_reject_all


def _spec():
    return {
        "title": "培训讲义·安全生产",
        "sections": [
            {"header": {"text": "内部培训资料"},
             "footer": {"text": "第 ", "page_number": True},
             "content": [
                 {"type": "heading", "level": 1, "text": "一、课程说明"},
                 {"type": "paragraph", "segments": [
                     {"text": "课程时长为"},
                     {"text": "8课时", "mode": "ins", "author": "张老师",
                      "date": "2026-11-10T09:00:00Z",
                      "source": "教研会纪要 2026-11-10"},
                     {"text": "（原"},
                     {"text": "4课时", "mode": "del", "author": "张老师",
                      "date": "2026-11-10T09:00:00Z",
                      "source": "教研会纪要 2026-11-10"},
                     {"text": "）。"}]},
                 {"type": "table", "style": "three_line",
                  "caption": "表 1-1 学员成绩统计",
                  "rows": [["姓名", "理论", "实操"], ["甲", 88, 92]]},
                 {"type": "bullet", "text": "要点一"},
                 {"type": "pagebreak"},
             ]},
            {"header": {"text": "附录"}, "landscape": True,
             "footer": {"text": "附录 ", "page_number": True},
             "content": [
                 {"type": "paragraph", "text": "附录内容。"},
                 {"type": "table", "style": "three_line", "header_repeat": True,
                  "rows": [["项目", "数值"], ["指标A", 1]]},
             ]},
        ],
    }


# ── 编译回读 ────────────────────────────────────────────────────────

def test_compile_roundtrip_and_counts(tmp_path):
    out = tmp_path / "讲义.docx"
    res = compile_docx(_spec(), out)
    assert res["tables"] == 2 and res["sections"] == 2
    assert res["revisions"] == 2 and res["trace_comments"] == 2
    assert res["reject_all_verified"] is True
    assert Path(res["path"]).exists() and Path(res["provenance"]).exists()
    doc = Document(str(out))
    texts = [p.text for p in doc.paragraphs]
    assert "培训讲义·安全生产" in texts and "一、课程说明" in texts
    assert "表 1-1 学员成绩统计" in texts and "要点一" in texts
    assert doc.tables[0].rows[1].cells[0].text == "甲"


# ── 三线表 ──────────────────────────────────────────────────────────

def test_three_line_table_borders():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.docx"
        compile_docx({"content": [
            {"type": "table", "style": "three_line",
             "rows": [["A", "B"], ["1", "2"]]}]}, p)
        doc = Document(str(p))
        tbl_pr = doc.tables[0]._tbl.tblPr
        borders = tbl_pr.findall(qn("w:tblBorders"))
        assert len(borders) == 1
        b = borders[0]
        assert b.find(qn("w:top")).get(qn("w:val")) == "single"
        assert b.find(qn("w:bottom")).get(qn("w:val")) == "single"
        assert b.find(qn("w:insideV")).get(qn("w:val")) == "none"   # 无竖线
        assert b.find(qn("w:insideH")).get(qn("w:val")) == "none"
        # 栏目线：表头行单元格底线
        tc_borders = doc.tables[0].rows[0].cells[0]._tc.findall(
            ".//" + qn("w:tcBorders"))
        assert tc_borders and tc_borders[0].find(qn("w:bottom")).get(
            qn("w:val")) == "single"


# ── 跨页续表头 ──────────────────────────────────────────────────────

def test_header_repeat_and_cant_split():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "t.docx"
        compile_docx({"content": [
            {"type": "table", "style": "three_line", "header_repeat": True,
             "rows": [["表头"], ["行1"], ["行2"]]}]}, p)
        doc = Document(str(p))
        tbl = doc.tables[0]
        assert tbl.rows[0]._tr.findall(".//" + qn("w:tblHeader"))    # 续表头
        for row in tbl.rows:
            assert row._tr.findall(".//" + qn("w:cantSplit"))        # 行不拆


# ── 分节页眉页脚 ────────────────────────────────────────────────────

def test_sections_headers_footers_page_field(tmp_path):
    out = tmp_path / "s.docx"
    compile_docx(_spec(), out)
    doc = Document(str(out))
    assert len(doc.sections) == 2
    assert doc.sections[0].header.paragraphs[0].text == "内部培训资料"
    assert doc.sections[1].header.paragraphs[0].text == "附录"
    # 断链：两节页眉各自独立
    assert doc.sections[1].header.is_linked_to_previous is False
    # 页脚 PAGE 域
    ftr_xml = doc.sections[0].footer.paragraphs[0]._p.xml
    assert "PAGE" in ftr_xml
    assert "第 " in doc.sections[0].footer.paragraphs[0].text


# ── 修订留痕 + 来源血缘 ─────────────────────────────────────────────

def test_revision_marks_and_provenance(tmp_path):
    out = tmp_path / "r.docx"
    res = compile_docx(_spec(), out)
    doc = Document(str(out))
    body_xml = doc.element.body.xml
    assert body_xml.count("<w:ins ") == 1
    assert body_xml.count("<w:del ") == 1
    assert "<w:delText" in body_xml
    assert 'w:author="张老师"' in body_xml
    assert "2026-11-10T09:00:00Z" in body_xml
    prov = json.loads(Path(res["provenance"]).read_text(encoding="utf-8"))
    assert prov["revision_count"] == 2
    modes = {r["mode"] for r in prov["revisions"]}
    assert modes == {"ins", "del"}
    assert all(r["source"] == "教研会纪要 2026-11-10" for r in prov["revisions"])
    # 来源血缘批注（锚定普通 run）
    texts = [c.text for c in doc.comments]
    assert any("修订溯源" in t and "教研会纪要" in t for t in texts)


def test_revision_views_and_reject_all(tmp_path):
    out = tmp_path / "v.docx"
    compile_docx(_spec(), out)
    views = revision_views(str(out))
    assert "课程时长为（原4课时）。" in views["original"]    # 拒绝全部=原文
    assert "课程时长为8课时（原）。" in views["revised"]      # 接受全部=终稿
    assert verify_reject_all(views["original"], out) is True
    assert verify_reject_all("完全不同的原文", out) is False


# ── 参数纪律 ────────────────────────────────────────────────────────

def test_compile_validation_errors(tmp_path):
    with pytest.raises(ValueError, match="spec"):
        compile_docx(None, tmp_path / "x.docx")
    with pytest.raises(ValueError, match="content"):
        compile_docx({}, tmp_path / "x.docx")
    with pytest.raises(ValueError, match="未知块类型"):
        compile_docx({"content": [{"type": "video", "text": "x"}]},
                     tmp_path / "x.docx")
    with pytest.raises(ValueError, match="rows"):
        compile_docx({"content": [{"type": "table", "rows": []}]},
                     tmp_path / "x.docx")
    with pytest.raises(ValueError, match="mode 非法"):
        compile_docx({"content": [{"type": "paragraph", "segments": [
            {"text": "x", "mode": "merge"}]}]}, tmp_path / "x.docx")
    with pytest.raises(ValueError, match="text 或 segments"):
        compile_docx({"content": [{"type": "paragraph", "text": ""}]},
                     tmp_path / "x.docx")
    with pytest.raises(ValueError, match="text 为空"):
        compile_docx({"content": [{"type": "bullet", "text": "  "}]},
                     tmp_path / "x.docx")


def test_no_revision_no_sidecar(tmp_path):
    out = tmp_path / "plain.docx"
    res = compile_docx({"content": [{"type": "paragraph", "text": "普通文档"}]}, out)
    assert res["revisions"] == 0 and res["provenance"] is None
    assert res["reject_all_verified"] is None
