"""写作能力层 P0 · 非投标演示场景验证（每能力一个真实场景，走 SoulMate 工具面）。

C1 → 可研报告（多章节事实一致性：植入矛盾→校验器点名定位→修正→通过）
C2 → 设备维护服务合同（缺资料留白：登记待补→Word 批注+待补清单→占位自查）
C3 → 实验室设备采购报价单（Decimal 精算+大写回填报价单文档）
C4 → 安全生产培训课件（三线表/跨页续表头/分节页眉页脚/修订留痕+拒绝全部
     修订自校验）

全部经 WritingTools 工具面调用（与 SoulMate function-calling 同一入口），
断言落在真实产物（docx XML / 批注 / 血缘侧车 / 数字）上。
"""

import json
from pathlib import Path

from docx import Document

from agent.writing_tools import WritingTools


def _tools(tmp_path):
    return WritingTools(out_dir=str(tmp_path / "docs"), now=lambda: 1700000000.0)


# ── C1 演示：可研报告——全局事实锁 + 跨章节校验 ─────────────────────

def test_demo_c1_feasibility_report_fact_lock(tmp_path):
    """非投标场景：《智慧园区建设项目可行性研究报告》5 章，锁 4 个核心事实，
    第 3 章植入工期矛盾 → 校验器点名（章节+行号+原文）；修正后全绿。"""
    tools = _tools(tmp_path)
    facts = [
        {"key": "建设工期", "value": "18个月", "scope": "全文",
         "source": "立项批复 2026-11-01", "confirmed_by": "王主任",
         "aliases": ["工期", "施工周期"]},
        {"key": "总投资", "value": "5600万元", "scope": "全文",
         "source": "估算表 v3", "confirmed_by": "王主任"},
        {"key": "质保期", "value": "2年", "scope": "全文",
         "source": "用户确认", "confirmed_by": "王主任"},
        {"key": "系统全称", "value": "智慧园区综合管理平台", "scope": "全文",
         "source": "用户确认", "confirmed_by": "李经理", "aliases": ["系统名称"]},
    ]
    built = tools.run("fact_lock", {"action": "build", "facts": facts})
    assert "事实锁已构建" in built
    lock = {"locked_facts": facts}

    # 成稿章节（第 3 章故意写错工期 24 个月）
    chapters = [
        {"id": "ch1", "title": "总论",
         "text": "智慧园区综合管理平台建设工期为18个月，总投资5600万元。"},
        {"id": "ch2", "title": "建设方案",
         "text": "系统全称：智慧园区综合管理平台。\n施工周期18个月，分三期实施。"},
        {"id": "ch3", "title": "实施计划",
         "text": "本项目工期24个月。\n质保期为2年。"},
        {"id": "ch4", "title": "投资估算",
         "text": "项目总投资5600万元，其中硬件占六成。"},
        {"id": "ch5", "title": "效益分析",
         "text": "平台上线后质保期2年，总投资5600万元内控不突破。"},
    ]
    checked = tools.run("fact_lock", {"action": "check", "lock": lock,
                                      "chapters": chapters})
    assert "矛盾点名" in checked
    assert "ch3" in checked and "第1行" in checked        # 冲突定位到章节+行
    assert "24个月" in checked and "18个月" in checked
    issues = json.loads(checked.split("\n\n", 1)[1])
    conflict = [i for i in issues["issues"] if i["type"] == "conflict"][0]
    assert conflict["key"] == "建设工期" and conflict["chapter_id"] == "ch3"

    # 修正第 3 章后复检通过
    chapters[2]["text"] = "本项目工期18个月。\n质保期为2年。"
    passed = tools.run("fact_lock", {"action": "check", "lock": lock,
                                     "chapters": chapters})
    assert "✅ 事实锁一致性通过" in passed

    # 事实锁注入块（写作前贴 System Prompt 顶部）
    prompt = tools.run("fact_lock", {"action": "prompt", "lock": lock})
    assert "【全局事实锁·只读】" in prompt and "18个月" in prompt


# ── C2 演示：设备维护服务合同——留白协议 ────────────────────────────

def test_demo_c2_service_contract_abstention(tmp_path):
    """非投标场景：《设备维护服务合同》缺乙方资质/联系人资料——登记留白不
    编造，缺口挂 Word 批注 + 输出待补清单 + 占位残留自查。"""
    tools = _tools(tmp_path)
    s1 = tools.run("doc_blank", {
        "action": "register", "description": "乙方营业执照统一社会信用代码",
        "chapter": "第2条 乙方资质", "reason": "用户未提供，禁编造"})
    s2 = tools.run("doc_blank", {
        "action": "register", "description": "7×24 值班电话号码",
        "chapter": "第6条 服务响应", "reason": "用户未提供"})
    assert "SLOT-001" in s1 and "SLOT-002" in s2

    # 合同正文（不写占位文字，缺口留承接句）
    contract = tmp_path / "设备维护服务合同.docx"
    doc = Document()
    doc.add_paragraph("设备维护服务合同")
    doc.add_paragraph("第2条 乙方资质：乙方资质信息见附件扫描件。")
    doc.add_paragraph("第6条 服务响应：乙方提供7×24小时值班服务。")
    doc.save(str(contract))

    out = tools.run("doc_blank", {
        "action": "attach_comments", "docx_path": str(contract),
        "comments": [
            {"anchor": "见附件扫描件",
             "text": "【SLOT-001】待补：乙方营业执照统一社会信用代码——用户未提供，禁编造"},
            {"anchor": "7×24小时值班服务",
             "text": "【SLOT-002】待补：7×24 值班电话号码——用户未提供"},
        ]})
    assert "已挂 Word 批注 2 条" in out
    back = Document(str(contract))
    comments = list(back.comments)
    assert len(comments) == 2
    assert "统一社会信用代码" in comments[0].text
    assert comments[0].author == "SoulMate留白"

    # 待补清单（文末落点/交人补充）
    lst = tools.run("doc_blank", {"action": "list"})
    assert "待人工补充清单" in lst and "SLOT-001" in lst and "SLOT-002" in lst

    # 交付前占位自查：干净正文通过；植入占位即点名
    body_text = "\n".join(p.text for p in back.paragraphs)
    assert "通过" in tools.run("doc_blank", {"action": "scan", "text": body_text})
    dirty = tools.run("doc_blank", {
        "action": "scan", "text": body_text + "\n第9条 其他：【待补：签约日期】"})
    assert "占位残留 1 处" in dirty

    # 写作纪律块（下轮生成注入）
    proto = tools.run("doc_blank", {"action": "protocol"})
    assert "不=编造" in proto and "Word 批注" in proto


# ── C3 演示：实验室设备采购报价单——Decimal 精算 + 大写 ─────────────

def test_demo_c3_lab_equipment_quote(tmp_path):
    """非投标场景：《实验室设备采购报价单》——LLM 只提参数，金额全部工具算，
    大写回填报价单 docx（报销/报价/预算全场景同源）。"""
    tools = _tools(tmp_path)
    out = tools.run("money_calc", {
        "action": "quote_total",
        "items": [
            {"name": "超净工作台", "qty": "2", "unit_price": "28500.50"},
            {"name": "恒温培养箱", "qty": "3", "unit_price": "19800"},
            {"name": "安装调试", "qty": "1", "unit_price": "12000.88"},
        ],
        "discount": "0.95", "tax_rate": "0.13"})
    # 行小计到分：57001.00 / 59400.00 / 12000.88；合计 128401.88
    assert "超净工作台: 2 × 28500.50 = 57001.00" in out
    assert "合计（未折）: 128401.88" in out
    assert "折扣（系数 0.95）: -6420.09" in out          # 128401.88×0.05→6420.09
    assert "税（0.13）: +15857.63" in out                # 121981.79×0.13→15857.63
    assert "总计: 137839.42" in out
    assert "大写: 人民币壹拾叁万柒仟捌佰叁拾玖元肆角贰分" in out

    # 大写回填报价单文档（金额链全 Decimal，无 LLM 算数）
    total = tools.run("money_calc", {"action": "to_upper", "amount": "137839.42"})
    quote_doc = tools.run("docx_compile", {
        "action": "compile", "filename": "实验室设备采购报价单",
        "spec": {"title": "实验室设备采购报价单", "content": [
            {"type": "table", "style": "three_line", "caption": "表 1 设备明细",
             "rows": [["名称", "数量", "单价(元)", "小计(元)"],
                      ["超净工作台", "2", "28,500.50", "57,001.00"],
                      ["恒温培养箱", "3", "19,800.00", "59,400.00"],
                      ["安装调试", "1", "12,000.88", "12,000.88"],
                      ["合计（九五折后含13%税）", "", "", "137,839.42"]]},
            {"type": "paragraph",
             "text": "报价总金额（大写）：人民币壹拾叁万柒仟捌佰叁拾玖元肆角贰分"},
        ]}})
    assert "MEDIA:" in quote_doc
    path = Path(quote_doc.split("MEDIA:")[-1].strip())
    back = Document(str(path))
    texts = [p.text for p in back.paragraphs]
    assert any("壹拾叁万柒仟捌佰叁拾玖元肆角贰分" in t for t in texts)
    assert back.tables[0].rows[4].cells[3].text == "137,839.42"

    # 税率拆分（财务核对）与折扣反算（审计口径）
    split = tools.run("money_calc", {
        "action": "tax", "mode": "from_inclusive",
        "total_incl": "137839.42", "tax_rate": "0.13"})
    assert "不含税 121981.79" in split and "税额 15857.63" in split
    rev = tools.run("money_calc", {
        "action": "discount", "list_price": "128401.88", "sale_price": "121981.79"})
    assert "折后价系数 0.9500" in rev and "优惠率 0.0500" in rev


# ── C4 演示：安全生产培训课件——OpenXML 确定性编译 ──────────────────

def test_demo_c4_training_courseware_compile(tmp_path):
    """非投标场景：《安全生产培训课件》——三线表+跨页续表头+分节页眉页脚+
    修订留痕（课时调整 4→8）+来源血缘+拒绝全部修订自校验。"""
    tools = _tools(tmp_path)
    spec = {
        "title": "安全生产培训课件（2026 版）",
        "sections": [
            {"header": {"text": "安全生产培训 · 内部资料"},
             "footer": {"text": "第 ", "page_number": True},
             "content": [
                 {"type": "heading", "level": 1, "text": "第一章 培训安排"},
                 {"type": "paragraph", "segments": [
                     {"text": "本次培训总课时"},
                     {"text": "8课时", "mode": "ins", "author": "安环部",
                      "date": "2026-11-12T10:00:00Z",
                      "source": "安环部调课通知 2026-11-12"},
                     {"text": "（原"},
                     {"text": "4课时", "mode": "del", "author": "安环部",
                      "date": "2026-11-12T10:00:00Z",
                      "source": "安环部调课通知 2026-11-12"},
                     {"text": "），含实操考核。"}]},
                 {"type": "table", "style": "three_line",
                  "caption": "表 1-1 各车间参训人数统计",
                  "header_repeat": True,
                  "rows": [["车间", "计划人数", "实到人数", "到课率"],
                           ["一车间", 45, 45, "100%"],
                           ["二车间", 38, 36, "94.7%"],
                           ["三车间", 52, 52, "100%"]]},
                 {"type": "bullet", "text": "考核不合格者须补训"},
                 {"type": "pagebreak"},
             ]},
            {"header": {"text": "附录 · 考核记录"},
             "footer": {"text": "附录 ", "page_number": True},
             "landscape": True,
             "content": [
                 {"type": "heading", "level": 2, "text": "附录A 考核记录表"},
                 {"type": "table", "style": "three_line",
                  "header_repeat": True,
                  "rows": [["序号", "姓名", "理论", "实操", "结论"],
                           ["1", "张工", 92, 88, "合格"],
                           ["2", "李工", 76, 80, "合格"]]},
             ]},
        ],
    }
    out = tools.run("docx_compile", {"action": "compile", "filename": "安全生产培训课件",
                                     "spec": spec})
    assert "✅ 文档已编译" in out and "2 表，2 节，2 条修订" in out
    assert "拒绝全部修订≡原文自校验: ✅ 通过" in out
    assert "来源血缘侧车" in out and "MEDIA:" in out

    path = Path(out.split("MEDIA:")[-1].strip())
    back = Document(str(path))
    # 三线表 + 续表头（XML 实证）
    from docx.oxml.ns import qn
    tbl = back.tables[0]
    borders = tbl._tbl.tblPr.findall(qn("w:tblBorders"))
    assert borders and borders[0].find(qn("w:insideV")).get(qn("w:val")) == "none"
    assert tbl.rows[0]._tr.findall(".//" + qn("w:tblHeader"))
    # 分节页眉页脚
    assert len(back.sections) == 2
    assert back.sections[0].header.paragraphs[0].text == "安全生产培训 · 内部资料"
    assert back.sections[1].header.paragraphs[0].text == "附录 · 考核记录"
    assert "PAGE" in back.sections[0].footer.paragraphs[0]._p.xml
    # 修订留痕 + 血缘
    body_xml = back.element.body.xml
    assert body_xml.count("<w:ins ") == 1 and body_xml.count("<w:del ") == 1
    prov = json.loads(Path(str(path) + ".provenance.json").read_text(encoding="utf-8"))
    assert all(r["source"] == "安环部调课通知 2026-11-12" for r in prov["revisions"])
    comments = [c.text for c in back.comments]
    assert any("修订溯源" in t and "调课通知" in t for t in comments)

    # 工具面 verify_revisions 双视图
    ver = tools.run("docx_compile", {"action": "verify_revisions",
                                     "docx_path": str(path)})
    assert "本次培训总课时（原4课时），含实操考核。" in ver   # 拒绝全部=原文
    assert "本次培训总课时8课时（原），含实操考核。" in ver     # 接受全部=终稿
