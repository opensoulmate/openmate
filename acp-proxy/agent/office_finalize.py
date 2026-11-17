# -*- coding: utf-8 -*-
"""office_finalize.py — C7 LibreOffice headless 终稿管线（TOC 页码更新 + PDF 导出）。

通用文档终稿能力（方案/可研/课件/标书一切含目录交付，非投标专用）：

- **TOC 页码更新**：Word 目录域缓存页码在正文变动后即过期；本管线以
  LibreOffice headless Basic 宏遍历 DocumentIndexes.update() + TextFields
  .refresh() **确定性刷新目录页码**再导出（实测：目录页码 2/3/4 与正文实际
  起始页逐一相符）；
- **PDF 终稿导出**：writer_pdf_Export 落 PDF（docx 原件不动，Agent 产物保留
  可继续修订）；
- **有界子进程**：独立 `-env:UserInstallation` 专用配置（不污染用户 LibreOffice
  配置、不与既有 soffice 实例互扰），宏经环境变量传参（路径含空格/逗号零转义
  问题），结果标记文件判完成（宏写 OK/ERR），超时显式报因禁静默。

实现要点（实测定型）：全新 profile 首次启动会覆写 basic/Standard/Module1.xba，
故必须先 `--terminate_after_init` 引导配置、再落宏文件（marker 防重复引导）。
soffice 缺失/宏失败/超时 → RuntimeError 显式报因；check_toc 报告目录域存在
性（无目录域时明说"仅导出 PDF"，不假称更新了目录）。
独立可测（tests/test_office_final.py）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

__all__ = [
    "DEFAULT_SOFFICE",
    "check_toc",
    "update_toc_and_export",
]

DEFAULT_SOFFICE = os.environ.get("SOULMATE_SOFFICE", "soffice")
DEFAULT_PROFILE_ROOT = os.path.expanduser("~/.hermes/soulmate/lo-profile")
DEFAULT_TIMEOUT_S = 180.0
_MARKER = ".soulmate-macro-seeded"
_RESULT_STEM = "soulmate-lo-result"

_INSTALL_HINT = "安装方法：系统安装 LibreOffice（如 `pacman -S libreoffice-fresh`）"

_MODULE_XBA = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1" script:language="StarBasic">
Sub UpdateTocExportPdf
  Dim sDocx As String
  Dim sPdf As String
  Dim sResult As String
  Dim oDoc As Object
  Dim oIndexes As Object
  Dim i As Integer
  Dim oArgs(0) As New com.sun.star.beans.PropertyValue
  Dim oLoadArgs(0) As New com.sun.star.beans.PropertyValue
  Dim iFree As Integer
  sDocx = Environ("SOULMATE_LO_DOCX")
  sPdf = Environ("SOULMATE_LO_PDF")
  sResult = Environ("SOULMATE_LO_RESULT")
  On Error GoTo Handler
  oLoadArgs(0).Name = "Hidden"
  oLoadArgs(0).Value = True
  oDoc = StarDesktop.loadComponentFromURL(ConvertToURL(sDocx), "_blank", 0, oLoadArgs())
  oIndexes = oDoc.getDocumentIndexes()
  For i = 0 To oIndexes.getCount() - 1
    oIndexes.getByIndex(i).update()
  Next i
  oDoc.getTextFields().refresh()
  oArgs(0).Name = "FilterName"
  oArgs(0).Value = "writer_pdf_Export"
  oDoc.storeToURL(ConvertToURL(sPdf), oArgs())
  oDoc.close(False)
  iFree = FreeFile
  Open sResult For Output As #iFree
  Print #iFree, "OK"
  Close #iFree
  StarDesktop.terminate()
  Exit Sub
Handler:
  iFree = FreeFile
  Open sResult For Output As #iFree
  Print #iFree, "ERR " &amp; Err &amp; " " &amp; Error$
  Close #iFree
  StarDesktop.terminate()
End Sub
</script:module>
"""

_SCRIPT_XLB = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "library.dtd">
<library:library xmlns:library="http://openoffice.org/2000/library" library:name="Standard" library:readonly="false" library:passwordprotected="false">
 <library:element library:name="Module1"/>
</library:library>
"""

_DIALOG_XLB = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "library.dtd">
<library:library xmlns:library="http://openoffice.org/2000/library" library:name="Standard" library:readonly="false" library:passwordprotected="false">
</library:library>
"""


# ── 目录域检测 ──────────────────────────────────────────────────────

def check_toc(docx_path) -> dict:
    """docx 是否含 Word 目录域（w:instrText/fldSimple instr 含 'TOC '）。"""
    docx_p = Path(docx_path)
    if not docx_p.is_file():
        raise ValueError(f"docx 不存在: {docx_p}")
    try:
        with zipfile.ZipFile(docx_p) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except zipfile.BadZipFile as e:
        raise ValueError(f"非 docx 文件: {docx_p}") from e
    has = (' TOC ' in xml) or ('TOC \\o' in xml) or (' TOC\\o' in xml)
    return {"has_toc_field": has, "path": str(docx_p)}


# ── 终稿导出（TOC 更新 + PDF） ──────────────────────────────────────

def update_toc_and_export(docx_path, out_pdf=None, *, soffice: str = "",
                          profile_dir: str = "",
                          timeout: float = DEFAULT_TIMEOUT_S) -> dict:
    """更新目录页码并导出 PDF 终稿（docx 原件不动）。

    返回 {"pdf", "bytes", "elapsed_s", "toc_updated", "has_toc_field",
    "docx", "result"}。soffice 缺失/宏失败/超时 → RuntimeError 显式报因。
    """
    docx_p = Path(docx_path).resolve()
    if not docx_p.is_file():
        raise ValueError(f"docx 不存在: {docx_p}")
    if docx_p.suffix.lower() != ".docx":
        raise ValueError(f"仅支持 .docx，收到 {docx_p.suffix!r}")
    pdf_p = Path(out_pdf).resolve() if out_pdf else docx_p.with_suffix(".pdf")
    if pdf_p.suffix.lower() != ".pdf":
        raise ValueError(f"输出仅支持 .pdf，收到 {pdf_p.suffix!r}")

    exe = str(soffice or DEFAULT_SOFFICE).strip() or DEFAULT_SOFFICE
    if not shutil.which(exe) and not Path(exe).is_file():
        raise RuntimeError(f"LibreOffice 不可用: {exe!r} 未找到。{_INSTALL_HINT}")

    toc = check_toc(docx_p)
    profile = Path(profile_dir or DEFAULT_PROFILE_ROOT).resolve()
    _ensure_profile(profile, exe, timeout)

    pdf_p.parent.mkdir(parents=True, exist_ok=True)
    result_file = pdf_p.parent / f"{_RESULT_STEM}-{os.getpid()}-{int(time.time()*1000)}.txt"
    env = dict(os.environ)
    env["SOULMATE_LO_DOCX"] = str(docx_p)
    env["SOULMATE_LO_PDF"] = str(pdf_p)
    env["SOULMATE_LO_RESULT"] = str(result_file)
    macro_url = ("vnd.sun.star.script:Standard.Module1.UpdateTocExportPdf"
                 "?language=Basic&location=application")
    cmd = [exe, "--headless", "--norestore",
           f"-env:UserInstallation={_file_url(profile)}", macro_url]

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=env)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"LibreOffice 终稿导出超时（>{int(timeout)}s）：{docx_p.name}——"
            "子进程有界超时已放弃（任务禁无限等）") from e
    except OSError as e:
        raise RuntimeError(f"LibreOffice 启动失败: {exe!r}: {e}。{_INSTALL_HINT}") from e

    # 结果标记文件 = 宏侧完成信号（soffice 退出码不总是可靠，标记为准）
    deadline = t0 + timeout
    while not result_file.is_file() and time.time() < deadline:
        time.sleep(0.2)
    verdict = result_file.read_text(encoding="utf-8").strip() if result_file.is_file() else ""
    try:
        result_file.unlink()
    except OSError:
        pass
    elapsed = round(time.time() - t0, 2)

    if verdict.startswith("ERR"):
        raise RuntimeError(
            f"LibreOffice 宏执行失败（{docx_p.name}）：{verdict}"
            f"｜stdout 尾部: {(proc.stdout or '').strip()[-200:]}")
    if not verdict:
        raise RuntimeError(
            f"LibreOffice 终稿导出无结果标记（{docx_p.name}）：soffice 退出码 "
            f"{proc.returncode}，stderr 尾部: {(proc.stderr or '').strip()[-200:]}")
    if not pdf_p.is_file() or pdf_p.stat().st_size < 1024:
        raise RuntimeError(f"PDF 产物缺失或过小: {pdf_p}（{pdf_p.stat().st_size if pdf_p.is_file() else '不存在'}）")

    return {
        "pdf": str(pdf_p),
        "bytes": pdf_p.stat().st_size,
        "elapsed_s": elapsed,
        "toc_updated": toc["has_toc_field"],
        "has_toc_field": toc["has_toc_field"],
        "docx": str(docx_p),
        "result": verdict,
    }


def _file_url(p: Path) -> str:
    return "file://" + str(p)


def _ensure_profile(profile: Path, exe: str, timeout: float) -> None:
    """专用 LibreOffice 配置：先引导（首启会覆写 Module1.xba），再落宏。"""
    marker = profile / _MARKER
    basic = profile / "user" / "basic" / "Standard"
    if marker.is_file() and (basic / "Module1.xba").is_file():
        return
    basic.mkdir(parents=True, exist_ok=True)
    if not marker.is_file():
        # 引导 profile（--terminate_after_init：初始化后即退，不开文档）
        try:
            subprocess.run(
                [exe, "--headless", "--norestore",
                 f"-env:UserInstallation={_file_url(profile)}",
                 "--terminate_after_init"],
                capture_output=True, text=True, timeout=min(timeout, 120.0))
        except (subprocess.TimeoutExpired, OSError) as e:
            raise RuntimeError(
                f"LibreOffice 配置引导失败（{profile}）: {e}。{_INSTALL_HINT}") from e
    # 引导之后落宏（首启会覆写默认 Module1.xba，顺序不可反）
    (basic / "Module1.xba").write_text(_MODULE_XBA, encoding="utf-8")
    (basic / "script.xlb").write_text(_SCRIPT_XLB, encoding="utf-8")
    (basic / "dialog.xlb").write_text(_DIALOG_XLB, encoding="utf-8")
    marker.write_text("seeded", encoding="utf-8")
