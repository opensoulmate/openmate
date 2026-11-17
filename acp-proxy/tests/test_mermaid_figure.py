# -*- coding: utf-8 -*-
"""C5 Mermaid 入图管线——题注编号/本地渲染/入 docx/编译 figure 块。

通用图表能力（方案/可研/课件含图文档，非投标专用）。
"""
import shutil
from pathlib import Path

import pytest

from agent import mermaid_figure
from agent.docx_compile import compile_docx

MMDC_OK = shutil.which(mermaid_figure.DEFAULT_MMDC) is not None
needs_mmdc = pytest.mark.skipif(not MMDC_OK, reason="mmdc（mermaid-cli）未安装")

FLOW = "flowchart TD\n    A[开始] --> B{判断}\n    B -->|是| C[执行]\n    B -->|否| D[结束]"


# ── 题注编号（图 X-X） ──────────────────────────────────────────────

def test_build_caption_and_counter():
    assert mermaid_figure.build_caption("系统架构图", "3", 1) == "图 3-1 系统架构图"
    assert mermaid_figure.build_caption("", "1", 2) == "图 1-2"

    c = mermaid_figure.FigureCounter()
    assert c.next_caption("总体架构") == "图 1-1 总体架构"
    assert c.next_caption("网络拓扑") == "图 1-2 网络拓扑"
    c.set_chapter("2")
    assert c.next_caption("实施流程") == "图 2-1 实施流程"
    assert c.next_caption("验收流程", "5") == "图 5-1 验收流程"


# ── 本地渲染（mmdc） ────────────────────────────────────────────────

@needs_mmdc
def test_render_png_and_svg(tmp_path):
    png = mermaid_figure.render_mermaid(FLOW, tmp_path / "c.png", scale=2)
    assert png["format"] == "png" and png["bytes"] > 0
    data = Path(png["path"]).read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"

    svg = mermaid_figure.render_mermaid(FLOW, tmp_path / "c.svg")
    assert svg["format"] == "svg"
    assert b"<svg" in Path(svg["path"]).read_bytes()


@needs_mmdc
def test_render_deterministic(tmp_path):
    """同源码同参数恒出同图（确定性纪律）。"""
    a = mermaid_figure.render_mermaid(FLOW, tmp_path / "a.png", scale=2)
    b = mermaid_figure.render_mermaid(FLOW, tmp_path / "b.png", scale=2)
    assert Path(a["path"]).read_bytes() == Path(b["path"]).read_bytes()


def test_render_errors(tmp_path):
    with pytest.raises(ValueError):
        mermaid_figure.render_mermaid("", tmp_path / "x.png")
    with pytest.raises(ValueError):
        mermaid_figure.render_mermaid(FLOW, tmp_path / "x.bmp")
    with pytest.raises(ValueError):
        mermaid_figure.render_mermaid(FLOW, tmp_path / "x.png", scale=0)
    with pytest.raises(RuntimeError) as e:   # 缺依赖显式报因禁静默
        mermaid_figure.render_mermaid(FLOW, tmp_path / "x.png",
                                      mmdc="/nonexistent/mmdc")
    assert "安装方法" in str(e.value)


# ── 入 docx（InlineShape + 题注） ───────────────────────────────────

@needs_mmdc
def test_insert_figure_into_docx(tmp_path):
    from docx import Document
    docx_p = tmp_path / "doc.docx"
    Document().save(str(docx_p))
    img = mermaid_figure.render_mermaid(FLOW, tmp_path / "fig.png")

    res = mermaid_figure.insert_figure(docx_p, img["path"], "图 1-1 测试流程图")
    assert res["figures"] == 1
    back = Document(str(docx_p))
    assert len(back.inline_shapes) == 1          # InlineShape 嵌入非外链
    texts = [p.text for p in back.paragraphs]
    assert "图 1-1 测试流程图" in texts           # 题注段
    assert back.paragraphs[-1].style.name == "Caption"

    # 二次插图计数递增
    res2 = mermaid_figure.insert_figure(docx_p, img["path"], "图 1-2 又一图")
    assert res2["figures"] == 2


def test_insert_figure_errors(tmp_path):
    with pytest.raises(ValueError):
        mermaid_figure.insert_figure(tmp_path / "no.docx", __file__)
    with pytest.raises(ValueError):
        mermaid_figure.insert_figure(__file__, tmp_path / "no.png")


# ── docx_compile figure 块（编译器复用本模块） ───────────────────────

@needs_mmdc
def test_compile_figure_block_mermaid_and_image(tmp_path):
    from docx import Document
    img_src = mermaid_figure.render_mermaid(FLOW, tmp_path / "photo.png")
    out = tmp_path / "report.docx"
    res = compile_docx({
        "title": "测试方案",
        "content": [
            {"type": "heading", "level": 1, "text": "第一章 概述"},
            {"type": "figure", "mermaid": FLOW,
             "caption_title": "系统流程图", "chapter": "1"},
            {"type": "figure", "image": img_src["path"],
             "caption_title": "现场照片", "chapter": "1"},
            {"type": "figure", "image": img_src["path"],
             "caption": "图 2-1 显式题注"},
        ],
    }, out)
    assert res["figures"] == 3
    assert Path(str(out) + ".fig1.png").is_file()   # mermaid 渲染件落盘可审计
    back = Document(str(out))
    assert len(back.inline_shapes) == 3
    texts = [p.text for p in back.paragraphs]
    assert "图 1-1 系统流程图" in texts              # 自动按章编号
    assert "图 1-2 现场照片" in texts
    assert "图 2-1 显式题注" in texts


def test_compile_figure_validation(tmp_path):
    with pytest.raises(ValueError):
        compile_docx({"content": [{"type": "figure"}]}, tmp_path / "a.docx")
    with pytest.raises(ValueError):
        compile_docx({"content": [{"type": "figure", "image": "x.png",
                                   "width_cm": 99}]}, tmp_path / "b.docx")
    with pytest.raises(ValueError):
        compile_docx({"content": [{"type": "figure",
                                   "image": str(tmp_path / "missing.png")}]},
                     tmp_path / "c.docx")
