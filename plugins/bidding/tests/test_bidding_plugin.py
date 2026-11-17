"""
招投标插件本体单测 — plugin.yaml 清单 / UI DSL / MCP 工具桥 / 四核心工具端到端

覆盖（docs/plugin-system-design.md §8 招投标插件规格）：
① plugin.yaml 清单：id/6 页面/7 工具（含 4 核心）/权限
② 6 个 UI DSL 页面校验 + 工具绑定名一致性
③ 四核心工具（bid_parse/bid_generate/bid_review/price_calc）确定性跑通
④ 真实招标文件集成（存在则断言真实抽取值）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

BID_DIR = Path(__file__).resolve().parents[1]          # plugins/bidding
OPENMATE = BID_DIR.parents[1]                          # 仓库根
ACP_PROXY = OPENMATE / "acp-proxy"

for _p in (str(ACP_PROXY), str(BID_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from plugin.mcp_bridge import PluginToolRegistry, ToolContext  # noqa: E402
from plugin.scanner import PluginDescriptor, bind_tools  # noqa: E402
from plugin.ui_dsl import validate_dsl_page  # noqa: E402
from plugin.yaml_manifest import load_yaml_manifest  # noqa: E402

import bid_engine as E  # noqa: E402

REAL_TENDER = Path("/home/climbing/Documents/文件及清单/招标文件-海城市地热水资源特许经营项目（水厂自动化工程）.docx")

CORE_TOOLS = {"bid_parse", "bid_generate", "bid_review", "price_calc"}


@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("BIDDING_DATA_DIR", str(tmp_path / "bdata"))


@pytest.fixture(scope="module")
def manifest():
    return load_yaml_manifest(BID_DIR)


@pytest.fixture()
def registry(manifest):
    desc = PluginDescriptor(manifest=manifest, dir_path=BID_DIR, entry_path=BID_DIR / "main.py")
    reg = PluginToolRegistry()
    bind_tools(desc, reg)
    return reg


def _make_tender(path: Path) -> Path:
    """构造最小但结构齐全的招标文件（含前附表/评分表/★参数/废标条款）。"""
    from docx import Document

    doc = Document()
    doc.add_paragraph("测试项目招标文件")
    doc.add_paragraph("项目名称：测试信息化建设项目")
    doc.add_paragraph("项目编号：TEST2024001")
    doc.add_paragraph("本项目最高限价：1000000 元，招标方式为公开招标。")
    doc.add_paragraph("招标人：测试招标人有限公司")
    doc.add_paragraph("招标代理机构：测试代理机构有限公司")
    doc.add_paragraph("递交截止时间：2025年6月3日09时30分")
    doc.add_paragraph("开标时间：2025年6月3日09时30分")
    doc.add_paragraph("三、 投标人资格要求：具有有效的安全生产许可证。具备独立法人资格。")
    doc.add_paragraph("投标人不按要求提交投标保证金的，其投标文件作废标处理。")
    doc.add_paragraph("投标报价明显低于其他投标报价的，认定低于成本报价竞标，其投标作废标处理。")

    # 投标人须知前附表
    t1 = doc.add_table(rows=1, cols=3)
    t1.style = "Table Grid"
    hdr = t1.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "条款号", "条款名称", "编列内容"
    for no, k, v in [("1.1.4", "项目名称", "测试信息化建设项目"), ("1.3.3", "质量要求", "合格"),
                     ("1.4.1", "工期", "120日历天"), ("3.4.1", "投标保证金", "50000 元")]:
        row = t1.add_row().cells
        row[0].text, row[1].text, row[2].text = no, k, v

    # 评分标准表
    t2 = doc.add_table(rows=1, cols=3)
    t2.style = "Table Grid"
    h2 = t2.rows[0].cells
    h2[0].text, h2[1].text, h2[2].text = "条款号", "评分因素", "评分标准"
    for no, f, s in [("2.1", "技术方案（40分）", "优得40分"), ("2.2", "价格（30分）", "最低价法得30分"),
                     ("2.3", "服务（30分）", "优得30分")]:
        row = t2.add_row().cells
        row[0].text, row[1].text, row[2].text = no, f, s

    # ★/▲ 技术参数表
    t3 = doc.add_table(rows=1, cols=3)
    t3.style = "Table Grid"
    h3 = t3.rows[0].cells
    h3[0].text, h3[1].text, h3[2].text = "参数性质", "技术参数", "招标文件要求"
    for lvl, p, r in [("★", "服务器CPU", "≥16核"), ("▲", "内存", "≥64GB"), ("一般", "硬盘", "≥1TB")]:
        row = t3.add_row().cells
        row[0].text, row[1].text, row[2].text = lvl, p, r

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


# ─────────────────────────── ① 清单 ───────────────────────────


def test_manifest_core_and_pages(manifest):
    assert manifest.id == "bidding"
    page_ids = [p.id for p in manifest.pages]
    assert page_ids == ["parse", "scoring", "generate", "review", "price", "knowledge"]
    tool_names = {t.name for t in manifest.tools}
    assert CORE_TOOLS <= tool_names
    assert "tool.register" in manifest.permissions


def test_manifest_handlers_exist(manifest):
    import importlib.util

    spec = importlib.util.spec_from_file_location("bidding_main_check", BID_DIR / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for t in manifest.tools:
        assert hasattr(mod, t.handler), f"缺少 handler: {t.handler}"


# ─────────────────────────── ② UI DSL ───────────────────────────


def test_all_dsl_pages_valid(manifest):
    import json

    for page in manifest.pages:
        raw = json.loads((BID_DIR / page.dsl).read_text(encoding="utf-8"))
        dsl = validate_dsl_page(raw, source=page.id)
        assert dsl.panels, page.id


def test_dsl_tools_are_registered(manifest, registry):
    import json

    registered = {t["name"] for t in registry.list_tools("bidding")}
    referenced = set()
    for page in manifest.pages:
        raw = json.loads((BID_DIR / page.dsl).read_text(encoding="utf-8"))
        for panel in raw["panels"]:
            if panel.get("type") == "form" and panel.get("submit"):
                referenced.add(panel["submit"]["tool"])
            if panel.get("type") == "list" and panel.get("source"):
                referenced.add(panel["source"]["tool"])
    assert referenced, "DSL 未引用任何工具"
    assert referenced <= registered, f"DSL 引用了未注册工具: {referenced - registered}"


# ─────────────────────────── ③ 工具绑定/调用（MCP 桥）───────────────────────────


def test_bridge_registers_all_tools(registry):
    full = {t["name"] for t in registry.list_tools("bidding")}
    for short in list(CORE_TOOLS) + ["bid_score_config", "bid_generate_status", "bid_knowledge_list"]:
        assert f"bidding__{short}" in full


def test_bid_parse_via_bridge(registry, tmp_path):
    tender = _make_tender(tmp_path / "tender.docx")
    ctx = ToolContext(plugin_id="bidding", data_dir=str(BID_DIR))
    out = registry.call_tool("bidding__bid_parse", {"file_path": str(tender)}, ctx=ctx)
    assert out["project_name"] == "测试信息化建设项目"
    assert out["project_code"] == "TEST2024001"
    assert out["budget"] == 1000000
    assert out["evaluation_method"] in ("综合评估法", "最低评标价法", None)
    assert out["scoring_count"] == 3
    assert out["params_count"] == 3


def test_price_calc_via_bridge(registry):
    ctx = ToolContext(plugin_id="bidding", data_dir=str(BID_DIR))
    out = registry.call_tool("bidding__price_calc",
                             {"bid_price": 950000, "method": "最低价法", "price_weight": 30,
                              "budget": 1000000, "cost": 800000}, ctx=ctx)
    assert 0 < out["price_score"] <= 30
    assert out["within_budget"] is True


def test_review_via_bridge(registry, tmp_path):
    tender = _make_tender(tmp_path / "tender.docx")
    ctx = ToolContext(plugin_id="bidding", data_dir=str(BID_DIR))
    out = registry.call_tool("bidding__bid_review",
                             {"file_path": str(tender), "bid_price": 950000,
                              "materials_provided": "营业执照, 投标保证金"}, ctx=ctx)
    assert set(out) >= {"verdict", "red", "yellow", "green", "checks"}
    assert out["red"] + out["yellow"] + out["green"] == out["total"] == len(out["checks"])
    assert all(c["level"] in ("🔴", "🟡", "🟢") for c in out["checks"])


def test_generate_via_bridge(registry, tmp_path):
    tender = _make_tender(tmp_path / "tender.docx")
    outdir = tmp_path / "out"
    ctx = ToolContext(plugin_id="bidding", data_dir=str(BID_DIR))
    out = registry.call_tool("bidding__bid_generate",
                             {"file_path": str(tender), "company": "测试投标人",
                              "output_dir": str(outdir), "resume": False}, ctx=ctx)
    assert Path(out["output_docx"]).is_file()
    assert out["section_count"] == 9
    assert out["audit_ok"] is True
    assert out["scoring_covered"] == out["scoring_total"] == 3


# ─────────────────────────── ④ 引擎单测 ───────────────────────────


def test_generate_resume_checkpoint(tmp_path):
    tender = _make_tender(tmp_path / "tender.docx")
    outdir = tmp_path / "out"
    r1 = E.generate_bid(tender, company="ACME", output_dir=outdir, resume=False)
    assert r1["resumed"] is False
    r2 = E.generate_bid(tender, company="ACME", output_dir=outdir, resume=True)
    assert r2["resumed"] is True
    assert all(s["status"].startswith("done") for s in r2["sections"])


def test_price_methods_bounds(tmp_path):
    for method in ("最低价法", "平均价法", "线性插值"):
        r = E.price_calc(bid_price=950000, method=method, price_weight=30,
                         budget=1000000, competitor_prices=[980000, 960000])
        assert 0 <= r["price_score"] <= 30, method


def test_review_detects_forbidden_factor(tmp_path):
    from docx import Document

    p = tmp_path / "forbidden.docx"
    doc = Document()
    doc.add_paragraph("项目名称：带违规评审因素项目")
    doc.add_paragraph("项目编号：FORBID001")
    # 评分表里出现 29 禁评审因素（注册资本 / 本地业绩）
    t = doc.add_table(rows=1, cols=3)
    t.style = "Table Grid"
    h = t.rows[0].cells
    h[0].text, h[1].text, h[2].text = "条款号", "评分因素", "评分标准"
    for no, f, s in [("2.1", "注册资本得分", "注册资本 5000 万元以上得 5 分"),
                     ("2.2", "本地业绩加分", "本地业绩每项加 2 分")]:
        row = t.add_row().cells
        row[0].text, row[1].text, row[2].text = no, f, s
    doc.save(str(p))
    r = E.review_bid(p, materials_provided=["营业执照"])
    assert r["summary"]["red"] >= 1
    assert any("29 禁" in c["detail"] for c in r["checks"])


# ─────────────────────────── ⑤ 真实招标文件集成 ───────────────────────────


@pytest.mark.skipif(not REAL_TENDER.is_file(), reason="真实招标文件不存在")
def test_real_tender_end_to_end(tmp_path):
    parse = E.parse_tender(REAL_TENDER)
    pi = parse["project_info"]
    assert pi["project_code"] == "LNFH2024036"
    assert pi["budget"] == pytest.approx(13356305.2)
    assert parse["evaluation_method"] == "综合评估法"
    assert len(parse["scoring"]) >= 5
    assert len(parse["rejection_clauses"]) >= 3

    rv = E.review_bid(REAL_TENDER, bid_price=13000000,
                      materials_provided=["营业执照", "资质证书", "安全生产许可证", "投标保证金"])
    assert rv["summary"]["total"] == len(rv["checks"])

    gen = E.generate_bid(REAL_TENDER, company="东华软件股份公司",
                         output_dir=tmp_path / "real_out", resume=False)
    assert Path(gen["output_docx"]).is_file()
    assert gen["consistency_audit"]["ok"] is True
