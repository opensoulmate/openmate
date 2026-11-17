# -*- coding: utf-8 -*-
"""mermaid_figure.py — C5 Mermaid 流程图/架构图自动入图管线。

通用图表入图能力（方案/可研/课件/标书一切含图文档，非投标专用）：
**Agent 产 Mermaid 源码，程序确定性出图**（渲染不猜）：

- **本地渲染**（mermaid-cli `mmdc` 子进程，有界超时）：Mermaid 源码 → 高清
  PNG/SVG（默认 scale=2 两倍分辨率，印刷清晰）；mmdc 缺失显式报因禁静默
  （含安装指引），不降级假渲染；
- **自动题注**（FigureCounter）："图 X-X 标题" 按章-序号确定性编号（同章内
  递增，跨章重置），题注不猜；
- **入 docx**（InlineShape）：图片以嵌入形状落进 docx（非外链），居中 +
  Caption 样式题注段，宽度可控（默认 14cm 版心宽）。

确定性纪律：同源码+同参数恒出同图；参数非法/依赖缺失显式 ValueError/
RuntimeError 报因。执行体独立可测（tests/test_mermaid_figure.py）；docx_compile
的 figure 块型复用本模块渲染+插图（编译器只排版，出图归本模块）。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = [
    "DEFAULT_MMDC",
    "FigureCounter",
    "append_figure",
    "build_caption",
    "insert_figure",
    "render_mermaid",
]

# mmdc 可覆盖（环境注入/测试 fake 路径），缺省 PATH 查找
DEFAULT_MMDC = os.environ.get("SOULMATE_MMDC", "mmdc")
DEFAULT_RENDER_TIMEOUT_S = 120.0
DEFAULT_SCALE = 2.0
DEFAULT_WIDTH_CM = 14.0

_CAPTION_RE = re.compile(r"^\s*图\s*(\d+)-(\d+)\s*")
_INSTALL_HINT = (
    "安装方法：`npm i -g @mermaid-js/mermaid-cli`（需 Node.js），"
    "或设 SOULMATE_MMDC 指向 mmdc 可执行文件")


# ── 题注编号（图 X-X） ──────────────────────────────────────────────

def build_caption(title: str, chapter, no: int) -> str:
    """题注拼装："图 {chapter}-{no} {title}"（title 空则只有编号）。"""
    title = str(title or "").strip()
    head = f"图 {chapter}-{int(no)}"
    return f"{head} {title}" if title else head


class FigureCounter:
    """按章编号的图序号器（确定性：同序调用恒同号）。

    用法：counter = FigureCounter()；counter.set_chapter("3")（或传入文档章节
    序号）；counter.next_caption("系统架构图") → "图 3-1 系统架构图"。
    未设章节时默认章节 "1"；同一 chapter 内序号递增，换章重置。
    """

    def __init__(self, chapter="1"):
        self._chapter = str(chapter or "1")
        self._counts: dict[str, int] = {}

    def set_chapter(self, chapter) -> "FigureCounter":
        self._chapter = str(chapter or "1")
        return self

    def next_no(self, chapter=None) -> int:
        ch = str(chapter or self._chapter)
        n = self._counts.get(ch, 0) + 1
        self._counts[ch] = n
        return n

    def next_caption(self, title: str = "", chapter=None) -> str:
        ch = str(chapter or self._chapter)
        return build_caption(title, ch, self.next_no(ch))

    @property
    def chapter(self) -> str:
        return self._chapter


# ── 本地渲染（mmdc 子进程） ─────────────────────────────────────────

def render_mermaid(code: str, out_path, *, scale: float = DEFAULT_SCALE,
                   background: str = "white", theme: str = "default",
                   mmdc: str = "", timeout: float = DEFAULT_RENDER_TIMEOUT_S,
                   ) -> dict:
    """Mermaid 源码 → 高清图（.png/.svg 按输出扩展名）。

    返回 {"path", "format", "bytes", "scale", "background", "theme"}。
    code 空/输出扩展名非法 → ValueError；mmdc 缺失/渲染失败/超时 →
    RuntimeError（含 stderr 尾部与安装指引，显式报因禁静默）。
    """
    code = str(code or "").strip()
    if not code:
        raise ValueError("mermaid 源码为空")
    out = Path(out_path)
    fmt = out.suffix.lower().lstrip(".")
    if fmt not in ("png", "svg"):
        raise ValueError(f"输出格式仅支持 .png/.svg，收到 {out.suffix!r}")
    exe = str(mmdc or DEFAULT_MMDC).strip() or DEFAULT_MMDC
    if not shutil.which(exe) and not Path(exe).is_file():
        raise RuntimeError(f"mermaid 渲染依赖不可用: {exe!r} 未找到。{_INSTALL_HINT}")

    scale = float(scale)
    if scale <= 0 or scale > 8:
        raise ValueError(f"scale 需在 (0, 8]，收到 {scale}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="soulmate-mmd-") as td:
        src = Path(td) / "chart.mmd"
        src.write_text(code, encoding="utf-8")
        cmd = [exe, "-i", str(src), "-o", str(out),
               "-s", str(scale), "-b", str(background or "white")]
        if theme and theme != "default":
            cmd += ["-t", str(theme)]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"mermaid 渲染超时（>{int(timeout)}s）：{exe} 子进程有界超时已放弃"
                f"（源码 {len(code)} 字符）") from e
        except OSError as e:
            raise RuntimeError(f"mermaid 渲染启动失败: {exe!r}: {e}。{_INSTALL_HINT}") from e
    if proc.returncode != 0 or not out.is_file():
        tail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise RuntimeError(
            f"mermaid 渲染失败（{exe} 退出码 {proc.returncode}）：{tail or '无输出，产物未生成'}")
    return {
        "path": str(out),
        "format": fmt,
        "bytes": out.stat().st_size,
        "scale": scale,
        "background": str(background or "white"),
        "theme": str(theme or "default"),
    }


# ── 入 docx（InlineShape + 题注） ───────────────────────────────────

def insert_figure(docx_path, image_path, caption: str = "", *,
                  width_cm: float = DEFAULT_WIDTH_CM) -> dict:
    """图片以 InlineShape 嵌入 docx（追加到文档末尾）+ 居中题注段。

    返回 {"path", "caption", "width_cm", "image_bytes", "figures"}。
    docx/图片不存在 → ValueError；python-docx 缺失 → RuntimeError。
    """
    try:
        from docx import Document
        from docx.shared import Cm
    except ImportError as e:
        raise RuntimeError(f"python-docx 不可用: {e}") from e
    docx_p = Path(docx_path)
    img_p = Path(image_path)
    if not docx_p.is_file():
        raise ValueError(f"docx 不存在: {docx_p}")
    if not img_p.is_file():
        raise ValueError(f"图片不存在: {img_p}")
    width_cm = float(width_cm)
    if not (0.5 <= width_cm <= 30.0):
        raise ValueError(f"width_cm 需在 [0.5, 30]，收到 {width_cm}")

    doc = Document(str(docx_p))
    n_before = len(doc.inline_shapes)
    append_figure(doc, img_p, caption, width_cm)
    doc.save(str(docx_p))
    return {
        "path": str(docx_p),
        "caption": str(caption or ""),
        "width_cm": width_cm,
        "image_bytes": img_p.stat().st_size,
        "figures": len(doc.inline_shapes),  # 全文嵌入图计数（含本次）
    }


def append_figure(doc, img_path: Path, caption: str, width_cm: float) -> None:
    """doc 对象级插图（docx_compile figure 块复用）：图居中 + 题注段。"""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    run.add_picture(str(img_path), width=Cm(width_cm))
    if caption:
        cap = doc.add_paragraph(str(caption))
        try:
            cap.style = "Caption"
        except KeyError:
            pass
        cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
