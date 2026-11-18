"""
真业务流程验证 — publish-guard（公众号发布保障）+ invoice-flow（发票报销）

两条真实业务流全链测试（非样板）：
- publish-guard：真账本结构夹具对账（漏发/封面未挂/待确认积压）→ human 节点开
  人工工单进 /tasks 收发台 → 交付回流收口；干净账本走无异常分支不开单
- invoice-flow：真发票 PDF（二维码解析）→ 登记去重 → 人工确认 → 报销台账入账
  （approve/驳回两路），事件/空目录分支覆盖

测试隔离：账本/台账/平台全走临时目录与开关（PUBLISH_GUARD_LEDGERS /
PUBLISH_GUARD_NO_PLATFORM / INVOICE_LEDGER_FILE），绝不触真实账本与微信平台。
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import shutil
import time
from datetime import date, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
# 真实发票样本（存在才跑全链；来源：用户微信收文件，二维码可解）
REAL_INVOICE_PDF = Path(
    "/home/climbing/Documents/xwechat_files/wxid_ofhpdl7j36lc52_8966/msg/file"
    "/2025-11/5%发票_大连市西岗区公共事业服务中心_20250418162253.pdf")
REAL_INVOICE_NUMBER = "25112000000078684318"   # 二维码实证（20 位全电发票号）


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def reconcile_mod():
    return _load("t_reconcile", PLUGINS_DIR / "publish-guard" / "reconcile.py")


@pytest.fixture(scope="module")
def recognize_mod():
    return _load("t_recognize", PLUGINS_DIR / "invoice-flow" / "invoice_recognize.py")


@pytest.fixture(scope="module")
def ledger_mod():
    return _load("t_ledger", PLUGINS_DIR / "invoice-flow" / "invoice_ledger.py")


# ─────────────────── 账本夹具（真实账本结构缩样） ───────────────────

def _write_ledgers(tmp_path: Path, *, dirty: bool) -> str:
    """两本账本缩样：dirty=带漏发/封面未挂/待确认积压；clean=全部正常。"""
    today = date.today()
    cover = tmp_path / "cover.png"
    cover.write_bytes(b"\x89PNG\r\n\x1a\n")   # 封面资产占位（is_file 即可）

    if dirty:
        entry = {
            "title": "AI编程（测试）：样例文章",
            "date": (today - timedelta(days=1)).isoformat(),
            "wechat_draft_id": "draft_test_dirty_0001",
            "status": "wechat_draft_created_wordpress_published",
            "visuals": {"cover": str(cover), "publish": {}},   # 封面生成了没消费
        }
    else:
        entry = {
            "title": "AI编程（测试）：样例文章",
            "date": today.isoformat(),
            "wechat_draft_id": "draft_test_clean_0001",
            "status": "published",
            "published_at": f"{today.isoformat()}T12:00:00",
            "publish": {"thumb_media_id": "wx_thumb_ok", "cover_applied": True},
        }

    ai_path = tmp_path / "ai-learning.json"
    ai_path.write_text(json.dumps({
        "current_day": 3,
        "last_updated": today.isoformat(),
        "AI编程1": entry,
    }, ensure_ascii=False), encoding="utf-8")

    arch_path = tmp_path / "agent-research.json"
    arch_path.write_text(json.dumps({
        "series": "测试系列",
        "total": 3,
        "published": 1,
        "last_published": entry["date"],
        "publish_history": [{
            "index": 1, "title": entry["title"], "date": entry["date"],
            "wechat_draft_id": entry["wechat_draft_id"],
        }],
    }, ensure_ascii=False), encoding="utf-8")

    cfg = [
        {"name": "AI课/公众号日更", "path": str(ai_path),
         "slot_hour": 0.0, "cadence": "daily"},
        {"name": "AI Agent架构系列", "path": str(arch_path),
         "slot_hour": 0.0, "cadence": "daily"},
    ]
    return json.dumps(cfg, ensure_ascii=False)


# ─────────────────── 对账核心（真账本结构出真对账结果） ───────────────────

def test_reconcile_dirty_ledgers_finds_real_issues(reconcile_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISH_GUARD_NO_PLATFORM", "1")
    cfg = json.loads(_write_ledgers(tmp_path, dirty=True))
    result = reconcile_mod.run_reconcile(cfg, window_days=3, check_platform=False)
    kinds = {i["kind"] for i in result["issues"]}
    assert "漏发" in kinds, result["issues"]           # 昨天更完，今天时点已过→漏发
    assert "封面未挂" in kinds, result["issues"]      # 封面图在、publish 消费块空
    assert "待确认积压" in kinds, result["issues"]    # 昨天草稿仍待确认且无发布时刻
    assert result["issue_count"] == len(result["issues"]) >= 3
    assert "发布保障工单" in result["ticket_text"] and "封面未挂" in result["ticket_text"]
    assert result["platform"] == "skipped"


def test_reconcile_clean_ledgers_all_clear(reconcile_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISH_GUARD_NO_PLATFORM", "1")
    cfg = json.loads(_write_ledgers(tmp_path, dirty=False))
    result = reconcile_mod.run_reconcile(cfg, window_days=3, check_platform=False)
    assert result["issue_count"] == 0, result["issues"]
    assert "对账通过" in result["report"]
    assert "无需处置" in result["ticket_text"]


def test_reconcile_missing_ledger_explicit_reason(reconcile_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("PUBLISH_GUARD_NO_PLATFORM", "1")
    cfg = [{"name": "幽灵账本", "path": str(tmp_path / "nope.json"),
            "slot_hour": 0.0, "cadence": "daily"}]
    result = reconcile_mod.run_reconcile(cfg, check_platform=False)
    assert result["issue_count"] == 1
    assert result["issues"][0]["detail"].startswith("账本文件不存在")


# ─────────────────── 发票识别/台账（真实发票样本） ───────────────────

def test_qr_payload_parse(recognize_mod):
    fields = recognize_mod.parse_qr_payload(
        "01,32,,25112000000078684318,44263.50,20250418,,5EA0")
    assert fields["发票号码"] == "25112000000078684318"
    assert fields["价税合计"] == 44263.50
    assert fields["开票日期"] == "2025-04-18"
    assert fields["校验码"] == "5EA0"


def test_extract_fields_parties_pairing(recognize_mod):
    text = ("购 名称：大连市西岗区公共事业服务中心        销 名称：北京东华合创科技有限公司\n"
            "信 统一社会信用代码/纳税人识别号：12210203MB1637927F"
            "   信 统一社会信用代码/纳税人识别号：91110108795145245W\n"
            "     项目名称    规格型号        单 位\n"
            "价税合计（大写）  （小写）¥44263.50  开票日期：2025 年04 月18 日\n")
    fields, warnings = recognize_mod.extract_fields(text)
    assert fields["购买方"] == "大连市西岗区公共事业服务中心"
    assert fields["销售方"] == "北京东华合创科技有限公司"   # 表头「规格型号」不得冒充
    assert fields["销售方税号"] == "91110108795145245W"
    assert fields["价税合计"] == 44263.50
    assert fields["开票日期"] == "2025-04-18"


def test_ledger_register_dedupe_confirm(ledger_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("INVOICE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    record = {"发票号码": "25112000000078684318", "开票日期": "2025-04-18",
              "价税合计": 44263.5, "销售方": "北京东华合创科技有限公司",
              "商品名称": "*电子计算机*台式计算机", "分类": "expense",
              "报销类别": "【普】业务费", "识别方式": "qr"}
    first = ledger_mod.register([record], run_id="t1")
    assert first["count"] == 1 and not first["duplicates"]
    again = ledger_mod.register([record], run_id="t2")
    assert again["count"] == 0 and again["duplicates"][0]["发票号码"] == record["发票号码"]

    confirmed = ledger_mod.confirm(first["entry_ids"], "approve",
                                   note="核对无误", confirmed_by="用户")
    assert len(confirmed["updated"]) == 1
    summary = ledger_mod.summary()
    assert summary["已确认张数"] == 1 and summary["已确认金额"] == 44263.5


@pytest.mark.skipif(not REAL_INVOICE_PDF.is_file(),
                    reason="真实发票样本不在本机（跳过真样本全链）")
def test_recognize_real_invoice_pdf(recognize_mod, tmp_path):
    tmp_pdf = tmp_path / "invoice.pdf"
    shutil.copy(REAL_INVOICE_PDF, tmp_pdf)
    records = recognize_mod.recognize_paths(file=str(tmp_pdf))
    assert len(records) == 1
    record = records[0]
    assert record["发票号码"] == REAL_INVOICE_NUMBER     # 二维码 100% 准
    assert record["价税合计"] == 44263.5
    assert record["开票日期"] == "2025-04-18"
    assert "qr" in record["识别方式"]


# ─────────────────── 流程引擎全链（生产同款装配） ───────────────────

@pytest.fixture()
def env(tmp_path, monkeypatch, plugins_dir):
    """生产同款装配：插件扫描装载 + 流程引擎 + MCP 工具桥（真账本/台账走临时夹具）。"""
    monkeypatch.setenv("FLOW_RUNS_DIR", str(tmp_path / "flow-runs"))
    monkeypatch.setenv("OPENMATE_PLUGIN_DATA_DIR", str(tmp_path / "plugin-data"))
    monkeypatch.setenv("PUBLISH_GUARD_NO_PLATFORM", "1")
    monkeypatch.setenv("INVOICE_LEDGER_FILE", str(tmp_path / "ledger.json"))
    from plugin.flow_engine import get_engine, reset_engine
    from plugin.mcp_bridge import PluginToolRegistry

    reset_engine()
    registry = PluginToolRegistry()
    engine = get_engine()

    async def _flow_tool_caller(name, args, approved=False):
        return await registry.call_tool_async(name, args, approved=approved)

    engine.set_tool_caller(_flow_tool_caller)

    from plugin.dsl_api import scan_and_build
    _, index = scan_and_build(plugins_dir, registry)
    errors = [e for e in index["errors"]
              if e.get("plugin") in ("publish-guard", "invoice-flow")]
    assert not errors, errors

    yield {"engine": engine, "registry": registry, "tmp_path": tmp_path,
           "monkeypatch": monkeypatch}
    reset_engine()


def _wait_suspended(engine, run_id: str, rounds: int = 300):
    for _ in range(rounds):
        run = engine.store.find_run(run_id)
        if run is not None and run.status != "running":
            return run
        time.sleep(0.02)
    raise AssertionError(f"实例 {run_id} 迟迟不落状态")


def test_publish_guard_flow_opens_task_in_tasks_and_completes(env, tmp_path):
    """真账本结构出真对账结果 → 有异常开人工工单进 /tasks → 交付回流收口。"""
    engine = env["engine"]
    cfg = _write_ledgers(tmp_path, dirty=True)
    env["monkeypatch"].setenv("PUBLISH_GUARD_LEDGERS", cfg)

    run = asyncio.run(engine.run_now("publish-guard", {"type": "manual", "source": "test"}))
    run = _wait_suspended(engine, run.run_id)
    assert run.status == "waiting-approval", (run.status, run.error)
    assert run.nodes["reconcile"].status == "ok"
    assert run.nodes["open-ticket"].status == "waiting-approval"
    assert run.outputs["reconcile"]["issue_count"] >= 3

    # /tasks 收发台：工单开出、正文带问题清单（催办由协作层超期机制接管）
    from plugin.flow_collab import get_collab
    tasks = get_collab().store.list(limit=50)
    open_tasks = [t for t in tasks if t.run_id == run.run_id and t.is_open()]
    assert len(open_tasks) == 1
    task = open_tasks[0]
    assert "漏发" in task.instruction and "封面未挂" in task.instruction
    assert task.assignee == "用户"

    done = asyncio.run(engine.resolve_delivery(
        run.run_id, result={"decision": "approve", "note": "已补发并补挂封面"},
        decided_by="用户"))
    assert done.status == "completed", done.error
    assert done.nodes["open-ticket"].status == "ok"
    assert done.nodes["finish"].status == "ok"
    assert done.notifications


def test_publish_guard_flow_clean_no_ticket(env, tmp_path):
    engine = env["engine"]
    env["monkeypatch"].setenv("PUBLISH_GUARD_LEDGERS",
                              _write_ledgers(tmp_path, dirty=False))
    run = asyncio.run(engine.run_now("publish-guard", {"type": "manual", "source": "test"}))
    run = _wait_suspended(engine, run.run_id)
    assert run.status == "completed", (run.status, run.error)
    assert run.outputs["route"]["branch"] == "else"
    assert run.nodes["open-ticket"].status == "pending"       # 无异常不开单
    assert run.outputs["reconcile"]["issue_count"] == 0


def test_publish_guard_cron_trigger_registered(env):
    flows = {f["id"]: f for f in env["engine"].list_flows()}
    assert "publish-guard" in flows and "invoice-reimburse" in flows
    guard = flows["publish-guard"]
    assert any(t["type"] == "cron" and t["cron"] == "30 21 * * *"
               for t in guard["triggers"])


# ─────────────────── 发票报销流全链 ───────────────────

@pytest.mark.skipif(not REAL_INVOICE_PDF.is_file(),
                    reason="真实发票样本不在本机（跳过真样本全链）")
def test_invoice_flow_full_chain_real_invoice(env, tmp_path):
    """真实发票 PDF 全链：识别→登记→human 人工确认→报销台账入账。"""
    engine = env["engine"]
    tmp_pdf = tmp_path / "invoice.pdf"
    shutil.copy(REAL_INVOICE_PDF, tmp_pdf)

    run = asyncio.run(engine.run_now(
        "invoice-reimburse", {"type": "manual", "source": "test"},
        inputs={"file": str(tmp_pdf)}))
    run = _wait_suspended(engine, run.run_id)
    assert run.status == "waiting-approval", (run.status, run.error)
    assert run.nodes["recognize"].status == "ok"
    assert run.outputs["recognize"]["count"] == 1
    assert run.nodes["register"].status == "ok"
    assert run.outputs["register"]["entry_ids"]
    assert run.nodes["confirm"].status == "waiting-approval"

    done = asyncio.run(engine.resolve_delivery(
        run.run_id, result={"decision": "approve", "note": "核对无误"},
        decided_by="用户"))
    assert done.status == "completed", done.error
    assert done.nodes["commit"].status == "ok"
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    confirmed = [e for e in ledger["entries"] if e["status"] == "已确认"]
    assert len(confirmed) == 1
    assert confirmed[0]["发票号码"] == REAL_INVOICE_NUMBER
    assert confirmed[0]["价税合计"] == 44263.5


@pytest.mark.skipif(not REAL_INVOICE_PDF.is_file(),
                    reason="真实发票样本不在本机（跳过真样本全链）")
def test_invoice_flow_reject_path(env, tmp_path):
    engine = env["engine"]
    tmp_pdf = tmp_path / "invoice.pdf"
    shutil.copy(REAL_INVOICE_PDF, tmp_pdf)
    run = asyncio.run(engine.run_now(
        "invoice-reimburse", {"type": "manual", "source": "test"},
        inputs={"file": str(tmp_pdf)}))
    run = _wait_suspended(engine, run.run_id)

    # 人工交付的是「驳回」结论（交付物语义）：commit 按驳回留档，流程正常收口
    done = asyncio.run(engine.resolve_delivery(
        run.run_id,
        result={"decision": "reject", "note": "不是报销发票（业绩票）"},
        decided_by="用户"))
    assert done.status == "completed", done.error
    ledger = json.loads((tmp_path / "ledger.json").read_text(encoding="utf-8"))
    assert [e["status"] for e in ledger["entries"]] == ["已驳回"]


def test_invoice_flow_empty_dir_goes_finish(env, tmp_path):
    engine = env["engine"]
    empty = tmp_path / "empty"
    empty.mkdir()
    run = asyncio.run(engine.run_now(
        "invoice-reimburse", {"type": "manual", "source": "test"},
        inputs={"dir": str(empty)}))
    run = _wait_suspended(engine, run.run_id)
    assert run.status == "completed", (run.status, run.error)
    assert run.outputs["has-invoices"]["branch"] == "else"
    assert run.nodes["register"].status == "pending"


def test_invoice_flow_event_trigger(env, tmp_path):
    """invoice-dropped 事件触发（发票入目录场景）。"""
    engine = env["engine"]
    empty = tmp_path / "drop"
    empty.mkdir()
    started = engine.emit("invoice-dropped", {"dir": str(empty)})
    assert [r.flow_id for r in started] == ["invoice-reimburse"]
