# -*- coding: utf-8 -*-
"""能力层第二批 · 非投标演示场景验证（每能力至少一个真实场景，走 SoulMate 工具面）。

C5+C6+C7 全链 → 《智慧园区建设方案》：Mermaid 流程图入图（自动"图 X-X"题注）
+ 现场照片压图（≤300KB）+ TOC 页码更新 + PDF 终稿
P1 防跑飞三件套 → 长文档批量生成任务（看门狗/轮数上限/空转诊断逐一触发）
P2 task_complete 免总结 → 整批 terminate 省末尾总结请求（混批不提前收）
P3 三段式修复循环 → 课件成稿验收不过（工期矛盾）→ 修复 → stalled 带遗留接受

全部经 WritingTools/GuardTools 工具面调用（与 SoulMate function-calling 同一
入口），断言落在真实产物（docx XML / PDF / 图片字节）上。
"""
import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from agent import run_guard, summary_control
from agent.guard_tools import GuardTools
from agent.writing_tools import WritingTools

MMDC_OK = shutil.which("mmdc") is not None
SOFFICE_OK = shutil.which("soffice") is not None
PDFTOTEXT_OK = shutil.which("pdftotext") is not None


def _tools(tmp_path):
    return WritingTools(out_dir=str(tmp_path / "docs"), now=lambda: 1700000000.0)


FLOW = ("flowchart LR\n"
        "    A[需求调研] --> B[方案设计]\n"
        "    B --> C[设备安装]\n"
        "    C --> D[联调测试]\n"
        "    D --> E[验收交付]")


# ── C5+C6+C7 全链演示：方案文含 Mermaid 流程图 + 压图 + PDF 终稿 ────

@pytest.mark.skipif(not (MMDC_OK and SOFFICE_OK),
                    reason="需要 mmdc + LibreOffice")
def test_demo_c5_c6_c7_solution_doc_full_chain(tmp_path, tmp_path_factory):
    """非投标场景：《智慧园区建设方案》——Mermaid 实施流程图（自动图 1-1 题注）
    入 docx + 现场勘查照片压图（防上传超限）+ 目录页码更新 + PDF 终稿。"""
    from PIL import Image
    tools = _tools(tmp_path)

    # ① C5：Mermaid 流程图本地渲染（Agent 产源码，程序出图）
    out = tools.run("diagram_figure", {
        "action": "render", "code": FLOW, "filename": "实施流程图.png"})
    assert "✅ Mermaid 已渲染" in out and "MEDIA:" in out
    png = Path(out.split("MEDIA:")[-1].strip())
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    # ② C6：现场勘查照片（2400×1600 噪声大图）→ 压图 ≤300KB
    photo = tmp_path / "现场勘查照片.png"
    Image.frombytes("RGB", (2400, 1600),
                    random.Random(11).randbytes(2400 * 1600 * 3)).save(photo)
    out = tools.run("image_compress", {"action": "compress",
                                       "path": str(photo),
                                       "out_name": "现场勘查照片.jpg"})
    assert "✅ 压图完成" in out
    jpg = Path(out.split("MEDIA:")[-1].strip())
    assert jpg.stat().st_size <= 300 * 1024            # 单张 300KB 硬保证

    # ③ 方案文确定性编译：目录域 + Mermaid 图 + 压缩照片（自动图 X-X 题注）
    out = tools.run("docx_compile", {
        "action": "compile", "filename": "智慧园区建设方案",
        "spec": {
            "title": "智慧园区建设方案",
            "content": [
                {"type": "toc", "title": "目录"},
                {"type": "pagebreak"},
                {"type": "heading", "level": 1, "text": "第一章 项目概述"},
                {"type": "paragraph",
                 "text": "本方案面向智慧园区综合管理平台建设，覆盖需求调研、"
                         "方案设计、设备安装、联调测试与验收交付全过程。"},
                {"type": "figure", "mermaid": FLOW,
                 "caption_title": "实施流程图", "chapter": "1"},
                {"type": "heading", "level": 1, "text": "第二章 现场条件"},
                {"type": "figure", "image": str(jpg),
                 "caption_title": "现场勘查照片", "chapter": "2"},
                {"type": "paragraph", "text": "现场条件满足施工要求。"},
                {"type": "heading", "level": 1, "text": "第三章 实施计划"},
                {"type": "paragraph", "text": "按实施流程分五阶段推进。"},
            ]}})
    assert "✅ 文档已编译" in out and "MEDIA:" in out
    docx = Path(out.split("MEDIA:")[-1].strip())
    # 图 X-X 自动题注（图 1-1 / 图 2-1）+ InlineShape 嵌入
    from docx import Document
    back = Document(str(docx))
    assert len(back.inline_shapes) == 2
    texts = [p.text for p in back.paragraphs]
    assert "图 1-1 实施流程图" in texts and "图 2-1 现场勘查照片" in texts

    # ④ C7：TOC 页码确定性更新 + PDF 终稿（docx 原件不动）
    out = tools.run("office_finalize", {"action": "check_toc",
                                        "docx_path": str(docx)})
    assert "含 Word 目录域" in out
    out = tools.run("office_finalize", {"action": "export_pdf",
                                        "docx_path": str(docx),
                                        "out_name": "智慧园区建设方案-终稿"})
    assert "✅ PDF 终稿已导出" in out and "TOC 页码已确定性更新" in out
    pdf = Path(out.split("MEDIA:")[-1].strip())
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert docx.is_file()                               # 原件不动

    if PDFTOTEXT_OK:
        txt = subprocess.run(["pdftotext", str(pdf), "-"],
                             capture_output=True, text=True).stdout
        assert "目录占位" not in txt                     # 目录域已刷新
        for title in ("第一章 项目概述", "第二章 现场条件", "第三章 实施计划"):
            assert title in txt
        assert "图 1-1 实施流程图" in txt and "图 2-1 现场勘查照片" in txt


# ── P1 演示：防跑飞三件套——长文档批量生成任务 ──────────────────────

def test_demo_p1_run_guard_long_task_stall_trip():
    """非投标场景：批量生成 200 份设备档案的长任务——①轮数上限拦住烧轮次
    ②空转诊断点名'同一档案反复改' ③无进展看门狗在挂死时出 stop 结论。"""
    class Clock:
        t = 0.0

        def __call__(self):
            return self.t

    clock = Clock()
    g = run_guard.RunGuard(max_turns=10, no_progress_timeout=480,
                           stall_threshold=3, now=clock)

    # 前 3 轮正常产出（每轮不同档案 = 实质进展）
    for i in range(3):
        g.record_turn("docx_compile", {"filename": f"档案{i}.docx"})
        clock.t += 30
    assert g.check()["verdict"] == "ok"

    # ② 空转：同一份档案反复重生成 3 次（同签名）→ 空转诊断出结论
    for _ in range(3):
        g.record_turn("docx_compile", {"filename": "档案3.docx"})
        clock.t += 30
    d = g.diagnose()
    assert d["stalled"] is True
    assert d["recommendation"] == "wrap_up"             # 已有产物→建议收尾
    assert "档案3" not in d["reason"]
    rec = g.extract_session_record(limit=50)
    assert len(rec.splitlines()) == 6                   # 会话摘录喂诊断子任务

    # ① 轮数上限：继续烧轮次到 10 → stop（max_turns）
    for _ in range(4):
        g.record_turn("docx_compile", {"filename": "档案4.docx"}, progress=True)
        clock.t += 1
    chk = g.check()
    assert chk["verdict"] == "stop" and chk["reason"] == "max_turns"

    # ③ 无进展看门狗：重置后任务挂死（480s 无任何进展）→ stop（no_progress）
    g2 = run_guard.RunGuard(no_progress_timeout=480, now=clock)
    g2.record_turn("web_extract", {"url": "http://x"})
    clock.t += 481
    chk = g2.check()
    assert chk["verdict"] == "stop" and chk["reason"] == "no_progress"

    # 工具面（与 LLM function-calling 同一入口）可见处决结论
    t = GuardTools(now=clock)
    t.run("run_guard", {"action": "configure", "max_turns": 2})
    t.run("run_guard", {"action": "beat", "tool": "a", "args": {"x": 1}})
    t.run("run_guard", {"action": "beat", "tool": "a", "args": {"x": 1}})
    out = t.run("run_guard", {"action": "check"})
    assert "🛑" in out and "max_turns" in out


# ── P2 演示：task_complete 免总结——整批 terminate 省末尾请求 ────────

def test_demo_p2_task_complete_skips_summary():
    """非投标场景：报表生成流水线收尾——最后一个工具批全部声明任务完成，
    整批 terminate 直接收尾（省一次总结性 LLM 请求）；混批则不提前收。"""
    tools = _tools(Path("/tmp"))

    # 批内 3 个调用全部 task_complete=true（输出即最终交付）
    sc = summary_control.SummaryControl()
    sc.start_batch()
    r1 = tools.run("money_calc", {"action": "to_upper", "amount": "12500",
                                  "task_complete": True})
    assert "壹万贰仟伍佰元整" in r1                      # 元参数不影响业务
    sc.record({"action": "to_upper", "task_complete": True}, r1)
    r2 = tools.run("money_calc",
                   {"action": "to_lower",
                    "upper_text": "人民币壹万贰仟伍佰元整",
                    "task_complete": True})
    sc.record({"action": "to_lower", "task_complete": True}, r2)
    assert sc.batch_terminate() is True                 # 整批全 true 才收
    fin = sc.consume()
    assert fin["terminate"] is True
    assert "12500" in fin["final_text"]                 # 工具输出=最终交付文本

    # 混批（一个没带 task_complete）→ 不整批 → 不免总结（防半批胡写交差）
    sc.start_batch()
    sc.record({"task_complete": True}, "产物A")
    sc.record({"action": "check"}, "校验B")
    assert sc.batch_terminate() is False


# ── P3 演示：三段式修复循环——课件成稿验收修复 ───────────────────────

def test_demo_p3_repair_loop_course_doc_fix():
    """非投标场景：《设备维护培训课件》程序验收发现工期矛盾（阻塞）+2 处
    格式建议 → 修复提示词退回同会话 → 阻塞修复后建议项停滞两轮 →
    带遗留接受（stalled≥2 无阻塞，不死磕烧 token）。"""
    tools = GuardTools()

    # 程序验收（fact_lock 一致性校验的输出即验收输入）
    tools.run("repair_loop", {"action": "start",
                              "goal": "《设备维护培训课件》成稿验收"})
    v1 = {"ok": False,
          "blocking": ["issue-1"],
          "issues": [
              {"id": "issue-1", "severity": "blocking",
               "detail": "第2章写'培训周期3周'，事实锁为 2 周（章节+行号见校验报告）"},
              {"id": "issue-2", "severity": "minor", "detail": "表 2-1 未用三线表"},
              {"id": "issue-3", "severity": "minor", "detail": "附录编号跳号"}]}
    out = tools.run("repair_loop", {"action": "step", "validation": v1})
    assert "repair" in out and "修复指令·normal" in out    # ① 逐条修
    assert "🔴阻塞" in out and "培训周期3周" in out

    # 阻塞修复后：2 处建议项连续两轮无进展 → 带遗留接受
    v_minor = {"ok": False, "issues": [
        {"id": "issue-2", "detail": "表 2-1 未用三线表"},
        {"id": "issue-3", "detail": "附录编号跳号"}]}
    out = tools.run("repair_loop", {"action": "step", "validation": v_minor})
    assert "repair" in out                                # 进展后仍修
    out = tools.run("repair_loop", {"action": "step", "validation": v_minor})
    out2 = tools.run("repair_loop", {"action": "step", "validation": v_minor})
    assert "accept" in out2 and "遗留" in out2            # ③ stalled≥2 接受

    # 对照：阻塞项持续不修 → 换方法（change-strategy）模板出现
    tools.run("repair_loop", {"action": "start", "goal": "对照场景"})
    v_block = {"ok": False, "blocking": ["issue-1"], "issues": [
        {"id": "issue-1", "severity": "blocking", "detail": "金额与事实锁冲突"}]}
    tools.run("repair_loop", {"action": "step", "validation": v_block})
    tools.run("repair_loop", {"action": "step", "validation": v_block})
    out = tools.run("repair_loop", {"action": "step", "validation": v_block})
    assert "change-strategy" in out and "换一种方法" in out  # ② 换方法重写
