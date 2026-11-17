"""写作能力层工具面（writing_tools.py）：四件套 MCP 工具 + soulmate 接线护栏。

覆盖：tool_defs 形状（4 个 function-calling 工具）/ 四工具 dispatch 真跑 /
未知工具与未知 action 显式拒绝 / doc_blank 会话隔离 / docx_compile 文件名
纪律与 MEDIA: 输出 / soulmate_agent 接线护栏（import/工具表/双 dispatch）/
__new__ 真实 dispatch 烟测 + lazy-init 构造。
"""

from pathlib import Path

import pytest

from agent.writing_tools import TOOL_NAMES, WritingTools, tool_defs


def _tools(tmp_path):
    return WritingTools(out_dir=str(tmp_path / "docs"),
                        now=lambda: 1700000000.0)


_FACTS = [{"key": "工期", "value": "90日历天", "scope": "全文",
           "source": "用户确认", "confirmed_by": "张工"}]


# ── 工具表形状 ──────────────────────────────────────────────────────

def test_tool_defs_shape():
    defs = tool_defs()
    names = [d["function"]["name"] for d in defs]
    assert names == list(TOOL_NAMES)
    for d in defs:
        fn = d["function"]
        assert d["type"] == "function"
        assert fn["description"] and fn["parameters"]["type"] == "object"
        assert "action" in fn["parameters"]["properties"]
        assert "action" in fn["parameters"]["required"]


# ── fact_lock ───────────────────────────────────────────────────────

def test_fact_lock_dispatch(tmp_path):
    tools = _tools(tmp_path)
    out = tools.run("fact_lock", {"action": "build", "facts": _FACTS})
    assert "✅ 事实锁已构建" in out and "90日历天" in out
    assert "【全局事实锁·只读】" in out
    check = tools.run("fact_lock", {
        "action": "check",
        "lock": {"locked_facts": _FACTS},
        "chapters": [{"id": "ch1", "title": "概述", "text": "工期为120日历天。"}],
    })
    assert "矛盾点名" in check and "120日历天" in check
    prompt = tools.run("fact_lock", {"action": "prompt",
                                     "lock": {"locked_facts": _FACTS}})
    assert "只读" in prompt


# ── doc_blank（含会话隔离）──────────────────────────────────────────

def test_doc_blank_dispatch_and_session_isolation(tmp_path):
    tools = _tools(tmp_path)
    out = tools.run("doc_blank", {"action": "register", "description": "证书编号"},
                    session_id="s1")
    assert "SLOT-001" in out
    tools.run("doc_blank", {"action": "register", "description": "财务结论"},
              session_id="s1")
    lst = tools.run("doc_blank", {"action": "list"}, session_id="s1")
    assert "合计 2 项待补" in lst
    other = tools.run("doc_blank", {"action": "list"}, session_id="s2")
    assert "为空" in other                            # 会话隔离
    scan = tools.run("doc_blank", {"action": "scan", "text": "正文【待补】x"})
    assert "占位残留 1 处" in scan
    proto = tools.run("doc_blank", {"action": "protocol"}, session_id="s1")
    assert "SLOT-001" in proto


def test_doc_blank_attach_comments_dispatch(tmp_path):
    tools = _tools(tmp_path)
    from docx import Document
    path = tmp_path / "c.docx"
    Document().save(str(path))                        # 空文档无段落 run
    doc = Document()
    doc.add_paragraph("见扫描件。")
    doc.save(str(path))
    out = tools.run("doc_blank", {
        "action": "attach_comments", "docx_path": str(path),
        "comments": [{"anchor": "见扫描件", "text": "待补：编号"}]})
    assert "已挂 Word 批注 1 条" in out
    assert "不存在" in tools.run("doc_blank", {
        "action": "attach_comments", "docx_path": "/nope.docx", "comments": [{}]})
    assert "comments" in tools.run("doc_blank", {
        "action": "attach_comments", "docx_path": str(path), "comments": []})


# ── money_calc ──────────────────────────────────────────────────────

def test_money_calc_dispatch(tmp_path):
    tools = _tools(tmp_path)
    out = tools.run("money_calc", {
        "action": "quote_total",
        "items": [{"name": "服务器", "qty": "2", "unit_price": "50000"},
                  {"name": "实施", "qty": "1", "unit_price": "30000"}],
        "discount": "0.9", "tax_rate": "0.13"})
    assert "合计（未折）: 130000.00" in out
    assert "总计: 132210.00" in out                   # 117000×1.13 = 117000+15210
    assert "大写: 人民币壹拾叁万贰仟贰佰壹拾元整" in out
    up = tools.run("money_calc", {"action": "to_upper", "amount": "13356305.2"})
    assert "壹仟叁佰叁拾伍万陆仟叁佰零伍元贰角" in up
    lo = tools.run("money_calc", {"action": "to_lower", "upper_text": "柒角整"})
    assert "0.7" in lo
    assert "浮点" in tools.run("money_calc", {"action": "to_upper", "amount": 0.1})
    assert "mode" in tools.run("money_calc", {"action": "tax"})


# ── docx_compile ────────────────────────────────────────────────────

def test_docx_compile_dispatch_and_media(tmp_path):
    tools = _tools(tmp_path)
    out = tools.run("docx_compile", {
        "action": "compile", "filename": "课件",
        "spec": {"title": "课件", "content": [
            {"type": "table", "style": "three_line",
             "rows": [["项目", "值"], ["A", 1]]},
            {"type": "paragraph", "segments": [
                {"text": "时长"},
                {"text": "8课时", "mode": "ins", "source": "教研会"}]},
        ]}})
    assert "✅ 文档已编译" in out and "MEDIA:" in out
    assert "拒绝全部修订≡原文自校验: ✅ 通过" in out
    assert "来源血缘侧车" in out
    path = Path(out.split("MEDIA:")[-1].strip())
    assert path.exists() and path.parent == tools.out_dir.resolve()
    # 文件名纪律
    assert "越界" in tools.run("docx_compile", {
        "action": "compile", "filename": "../evil",
        "spec": {"content": [{"type": "paragraph", "text": "x"}]}})
    assert "spec" in tools.run("docx_compile", {"action": "compile"})


# ── 分发纪律 ────────────────────────────────────────────────────────

def test_unknown_tool_and_action_explicit(tmp_path):
    tools = _tools(tmp_path)
    assert "未知写作工具" in tools.run("money_tool", {})
    assert "未知 action" in tools.run("money_calc", {"action": "divination"})
    assert "未知 action" in tools.run("fact_lock", {"action": "x"})
    assert "未知 action" in tools.run("doc_blank", {"action": "x"})
    assert "未知 action" in tools.run("docx_compile", {"action": "x"})


# ── soulmate_agent 接线护栏 + dispatch 烟测 ─────────────────────────

def _src() -> str:
    return (Path(__file__).resolve().parent.parent
            / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_source_guards():
    src = _src()
    # 工具定义进 LLM 工具面（tool_defs 由 writing_tools 提供）
    assert "builtin_tools.extend(writing_tool_defs())" in src
    assert "from agent.writing_tools import WritingTools" in src
    assert "WRITING_TOOL_NAMES" in src                 # dispatch 工具名清单（常量单一真源）
    # 主循环 dispatch + code-mode 批内 dispatch 两处 + 共享入口定义
    assert src.count("self._writing_dispatch(func_name, func_args, session_id)") == 2
    assert "def _writing_dispatch" in src
    # C5-C7 新工具进工具面（tool_defs 实际 API 面，不靠源码字符串）
    from agent.writing_tools import tool_defs as _wtool_defs
    names = {d["function"]["name"] for d in _wtool_defs()}
    assert {"fact_lock", "doc_blank", "money_calc", "docx_compile",
            "diagram_figure", "image_compress", "office_finalize"} <= names
    # 治理工具组（Pi 取长补短：run_guard/repair_loop）+ task_complete 免总结接线
    assert "builtin_tools.extend(guard_tool_defs())" in src
    assert src.count("self._guard_dispatch(func_name, func_args, session_id)") == 2
    assert "def _guard_dispatch" in src
    assert "summary_ctl.batch_terminate()" in src      # 整批 terminate 收尾
    # 系统提示工具清单
    assert "- fact_lock / doc_blank / money_calc / docx_compile" in src


def test_real_dispatch_smoke(tmp_path):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent._writing_tools_inst = _tools(tmp_path)
    out = agent._writing_dispatch("money_calc", {"action": "to_upper",
                                                 "amount": "0.7"})
    assert "柒角整" in out
    out2 = agent._writing_dispatch("fact_lock", {"action": "build",
                                                 "facts": _FACTS})
    assert "事实锁已构建" in out2


def test_dispatch_lazy_init_constructs_writing_tools(monkeypatch):
    import agent.soulmate_agent as sa

    made = {}

    class FakeTools:
        def __init__(self, *a, **k):
            made["init"] = True

        def run(self, func_name, func_args, session_id=""):
            return f"FAKE:{func_name}:{func_args.get('action', '')}"

    monkeypatch.setattr(sa, "WritingTools", FakeTools)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    out = agent._writing_dispatch("money_calc", {"action": "to_upper"})
    assert made.get("init") is True                   # lazy-init 真的构造了
    assert out == "FAKE:money_calc:to_upper"
