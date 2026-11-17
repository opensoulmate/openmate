"""C2 留白协议（doc_blank.py）：缺料登记/待补清单/占位扫描/Word 批注出口。

覆盖：槽位登记与补齐 / 待补清单 markdown / 占位残留扫描（【待补】、TBD、
问号占位命中；"待补充说明"式正常行文不误伤）/ Word 批注锚定与回读 /
锚点未命中显式报因禁静默 / 纯段落序号锚定 / 写作纪律 prompt。
"""

from docx import Document

import pytest

from agent.doc_blank import (
    BlankRegistry, attach_comments, protocol_prompt, scan_placeholders,
)


# ── 槽位登记簿 ──────────────────────────────────────────────────────

def test_registry_register_resolve_pending():
    reg = BlankRegistry()
    s1 = reg.register("ISO9001证书编号", chapter="第2章 资质",
                      reason="用户未提供扫描件")
    s2 = reg.register("近三年财务审计结论", chapter="第5章 财务")
    assert s1.slot_id == "SLOT-001" and s2.slot_id == "SLOT-002"
    assert len(reg.pending()) == 2
    reg.resolve("SLOT-001", "0012345")
    assert [s.slot_id for s in reg.pending()] == ["SLOT-002"]
    assert s1.status == "filled" and s1.filled_value == "0012345"
    with pytest.raises(ValueError):
        reg.register("", chapter="x")                 # 缺什么必须说清
    with pytest.raises(ValueError):
        reg.resolve("SLOT-999", "v")                  # 未登记 ID
    with pytest.raises(ValueError):
        reg.register("x", slot_id="SLOT-001")         # 重复 ID


def test_pending_markdown_lists_open_items():
    reg = BlankRegistry()
    reg.register("证书编号", chapter="第2章")
    md = reg.pending_markdown()
    assert "待人工补充清单" in md and "SLOT-001" in md and "证书编号" in md
    assert "待补" in md and "合计 1 项待补" in md
    reg.resolve("SLOT-001", "ABC")
    assert "为空" in reg.pending_markdown()


# ── 占位残留扫描 ────────────────────────────────────────────────────

def test_scan_hits_placeholders_with_location():
    text = "第一段正文。\n第二段【待补：证书编号】保留缺口。TBD 项待定？？"
    viol = scan_placeholders(text)
    kinds = {v["kind"] for v in viol}
    assert "方括号占位" in kinds and "拉丁占位符" in kinds and "问号占位" in kinds
    lines = {v["line"] for v in viol}
    assert 2 in lines                                 # 定位到行
    assert any(v["col"] > 1 for v in viol)


def test_scan_no_false_positive_on_normal_prose():
    assert scan_placeholders("待补充说明事项按合同第5条执行。保修期见附件。") == []
    assert scan_placeholders("这不是TBD。".replace("TBD", "待机时间")) == []


def test_scan_type_guard():
    from typing import Any
    bad: Any = None
    with pytest.raises(ValueError):
        scan_placeholders(bad)


# ── Word 批注出口（真实 docx 回读）──────────────────────────────────

def test_attach_comments_roundtrip(tmp_path):
    path = tmp_path / "合同审查.docx"
    doc = Document()
    doc.add_paragraph("乙方资质：见扫描件附件。")
    doc.add_paragraph("联系人：张三。")
    doc.save(str(path))
    res = attach_comments(str(path), [
        {"anchor": "见扫描件", "text": "【SLOT-001】待补：ISO9001证书编号"},
        {"paragraph_index": 1, "text": "确认联系人电话"},
    ])
    assert res["saved"] is True and len(res["attached"]) == 2
    back = Document(str(path))
    comments = list(back.comments)
    texts = [c.text for c in comments]
    assert "ISO9001证书编号" in texts[0] and "确认联系人电话" in texts[1]
    assert comments[0].author == "SoulMate留白"


def test_attach_comments_anchor_miss_is_explicit(tmp_path):
    path = tmp_path / "报告.docx"
    doc = Document()
    doc.add_paragraph("正文。")
    doc.save(str(path))
    res = attach_comments(str(path), [
        {"anchor": "不存在的锚点", "text": "x"},
    ])
    assert res["attached"] == [] and res["saved"] is False
    assert "锚点未命中" in res["failed"][0]["error"]
    # 文件未改动（无批注不保存）
    assert len(Document(str(path)).comments) == 0


def test_attach_comments_validation(tmp_path):
    path = tmp_path / "a.docx"
    Document().save(str(path))
    with pytest.raises(ValueError):
        attach_comments(str(path), [])
    res = attach_comments(str(path), [{"text": "缺锚点"}])
    assert res["failed"] and "缺 anchor" in res["failed"][0]["error"]


# ── 写作纪律 prompt ─────────────────────────────────────────────────

def test_protocol_prompt_contains_discipline():
    reg = BlankRegistry()
    reg.register("审计报告结论", chapter="第5章")
    p = protocol_prompt(reg)
    assert "禁" in p and "不=编造" in p
    assert "Word 批注" in p and "待人工补充清单" in p
    assert "SLOT-001" in p                            # 带出当前待补项
    assert "承诺类数字" in p
