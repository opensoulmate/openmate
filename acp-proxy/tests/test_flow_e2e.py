"""
流程引擎端到端真实验证 — 生产同款装配（scan_and_build + /flows* + /plugins* HTTP 面）

真实链路（任务验收项「一条示例流程全链跑通」）：
定时触发（cron 工作日 09:00 tick）→ 调工具（demo-greeter greet_log/greet，真实
handler 过 MCP 注册桥）→ 人工审批闸（实例挂起，HTTP 审批恢复）→ 出报告
（demo-greeter__build_report，工具级 approval=always 经流程审批 token 放行）
→ 收尾通知 → 可观测（flow-console 运行面板工具：实例列表/节点日志/重试/回放）。

外加自我开发闭环端到端：草稿→dry-run→未确认不装（铁律）→确认安装→菜单/页面/
工具即刻长出并可调。
"""
from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_PLUGIN_ID = "zz-test-notes"


@pytest.fixture()
def env(tmp_path, monkeypatch, plugins_dir):
    """生产同款装配：插件扫描装载 + 流程引擎 + HTTP 面（flow/forge）"""
    monkeypatch.setenv("FLOW_RUNS_DIR", str(tmp_path / "flow-runs"))
    monkeypatch.setenv("OPENMATE_PLUGIN_DATA_DIR", str(tmp_path / "plugin-data"))
    from plugin.dsl_api import scan_and_build
    from plugin.flow_engine import get_engine, reset_engine
    from plugin.flow_api import build_flow_router
    from plugin.forge_api import build_forge_router
    from plugin.mcp_bridge import PluginToolRegistry

    reset_engine()
    registry = PluginToolRegistry()
    engine = get_engine()

    async def _flow_tool_caller(name, args, approved=False):
        return await registry.call_tool_async(name, args, approved=approved)

    engine.set_tool_caller(_flow_tool_caller)

    app = FastAPI()
    routers, index = scan_and_build(plugins_dir, registry)
    for router in routers:
        app.include_router(router)
    index_plugins = list(index["plugins"])

    def on_installed(grown):
        index_plugins.append({
            "name": grown["frontend_manifest"].get("name", ""),
            "status": "enabled",
            "manifest": grown["frontend_manifest"],
        })

    app.include_router(build_flow_router(engine))
    app.include_router(build_forge_router(
        plugins_dir, registry=registry, engine=engine,
        include_router=app.include_router, on_installed=on_installed))

    @app.get("/plugins")
    async def list_plugins():
        # 与 app.py::list_plugins 同款载荷（yaml 清单式插件 + 热装载增量）
        return {"plugins": index_plugins, "count": len(index_plugins),
                "errors": index["errors"]}

    client = TestClient(app)
    yield {
        "client": client, "engine": engine, "registry": registry,
        "tmp_path": tmp_path, "index_plugins": index_plugins,
        "plugins_dir": plugins_dir,
    }
    # 清理：测试插件（如已安装）+ 草稿
    registry.unregister_plugin(TEST_PLUGIN_ID)
    for plugin_id in (TEST_PLUGIN_ID, "zz-bad-dsl"):
        for path in (plugins_dir / plugin_id, plugins_dir / ".drafts" / plugin_id):
            if path.exists():
                shutil.rmtree(path)
    reset_engine()


# ─────────────────────────── 示例流程全链 ───────────────────────────

def test_example_flow_cron_to_report_full_chain(env):
    """定时触发→调工具→人工审批→出报告（全链真实跑通）"""
    client = env["client"]
    engine = env["engine"]

    # ① 定时触发：cron「0 9 * * 1-5」在真实工作日 09:00 时刻命中（调度器 tick 同款）
    monday_9am = time.mktime((2026, 11, 16, 9, 0, 0, 0, 0, -1))   # 2026-11-16 周一

    async def tick_and_wait():
        started = engine.tick(now=monday_9am)
        assert len(started) == 1, f"cron 应触发 1 条实例，实际 {len(started)}"
        run_id = started[0].run_id
        for _ in range(200):
            await asyncio.sleep(0.01)
            run = engine.store.find_run(run_id)
            if run is not None and run.status != "running":
                break
        return run_id, run

    run_id, run = asyncio.run(tick_and_wait())
    assert run.trigger["type"] == "cron" and run.trigger["cron"] == "0 9 * * 1-5"

    # ② 调工具（真实 handler 过 MCP 注册桥）+ 停在人工审批闸
    assert run.status == "waiting-approval", (run.status, run.error)
    assert run.nodes["fetch-log"].status == "ok" and run.nodes["fetch-log"].attempts == 1
    assert run.nodes["greet-team"].status == "ok"
    assert run.nodes["confirm-report"].status == "waiting-approval"
    assert run.nodes["build-report"].status == "pending"   # 审批前不出报告
    assert run.outputs["greet-team"]["greeting"] == "团队，您好。"

    # ③ 可观测运行面板（flow-console 工具 = UI 面板数据源，同一后端）
    listed = client.post("/plugins/flow-console/tools/run_list",
                         json={"args": {"limit": 10}}).json()
    assert any(item["run_id"] == run_id and item["status"] == "waiting-approval"
               for item in listed["items"])
    detail = client.post("/plugins/flow-console/tools/run_detail",
                         json={"args": {"run_id": run_id}}).json()
    assert detail["状态"] == "waiting-approval"
    assert "节点 fetch-log" in detail and "事件日志" in detail

    # ④ 人工审批（面板按钮/对话同款 HTTP 面：approve 恢复执行）
    approved = client.post(f"/flows/runs/{run_id}/approval",
                           json={"decision": "approve", "decided_by": "用户",
                                 "note": "同意出报告"}).json()
    assert approved["status"] == "completed", approved.get("error")

    # ⑤ 出报告（approval=always 的生成文档工具，经流程审批 token 放行）
    outputs = client.get(f"/flows/runs/{run_id}").json()["outputs"]
    report = outputs["build-report"]
    assert report["title"] == "每日问候报告"
    assert report["greeting"] == "团队，您好。"
    assert "每日问候报告" in report["summary"] and "团队，您好。" in report["summary"]

    # ⑥ 运行日志（节点日志/尝试/审批/通知全留痕）
    log = client.get(f"/flows/runs/{run_id}/log").json()["log"]
    events = [e["event"] for e in log]
    for expected in ("run-start", "node-start", "attempt", "node-ok",
                     "approval-pending", "approval-approved", "notify", "run-ok"):
        assert expected in events, f"运行日志缺 {expected}: {events}"
    assert any(e["node"] == "build-report" and e["event"] == "attempt" for e in log)

    # ⑦ 回放（同触发/输入重跑，链回 replay_of；回放也要重新过审批闸）
    replay = client.post(f"/flows/runs/{run_id}/replay", json={}).json()
    assert replay["replay_of"] == run_id
    assert replay["status"] == "waiting-approval"
    rejected = client.post(f"/flows/runs/{replay['run_id']}/approval",
                           json={"decision": "reject", "note": "本次不发"}).json()
    assert rejected["status"] == "failed" and "驳回" in rejected["error"]


def test_manual_trigger_from_panel(env):
    """面板按钮/对话「跑一下」：manual 触发同一条流程"""
    client = env["client"]
    started = client.post("/flows/daily-greet-report/run", json={"inputs": {}}).json()
    assert started["status"] == "waiting-approval"
    assert started["trigger"] == "manual"
    # 幂等键：同键不重复触发
    again = client.post("/flows/daily-greet-report/run",
                        json={"inputs": {}, "idempotency_key": "panel-1"}).json()
    same = client.post("/flows/daily-greet-report/run",
                       json={"inputs": {}, "idempotency_key": "panel-1"}).json()
    assert again["run_id"] == same["run_id"]


def test_flow_console_dsl_page_loads(env):
    """可观测运行面板（UI DSL list/report 页面）真实装载 + 数据绑定走 MCP 工具"""
    client = env["client"]
    page = client.get("/plugins/flow-console/ui/home").json()
    assert page["version"] == 1
    types = [p["type"] for p in page["panels"]]
    assert types.count("list") == 2 and types.count("report") == 2
    # 数据绑定唯一来源是 MCP 工具（面板可做的事，对话也能做）
    tools = client.get("/plugins/flow-console/tools").json()["tools"]
    tool_names = {t["name"] for t in tools}
    for panel in page["panels"]:
        source = panel.get("source") or {}
        if source.get("tool"):
            assert source["tool"] in tool_names, f"面板数据源未注册: {source['tool']}"
        submit = panel.get("submit") or {}
        if submit.get("tool"):
            assert submit["tool"] in tool_names, f"表单提交工具未注册: {submit['tool']}"
    # 列表面板（实例）可出数（真实数据源）
    runs = client.post("/plugins/flow-console/tools/run_list",
                       json={"args": {"limit": 5}}).json()
    assert "items" in runs and "count" in runs


def test_gated_tool_blocked_without_flow_approval(env):
    """工具级审批闸独立生效：不经流程审批 token，approval=always 工具拒调"""
    client = env["client"]
    outcome = client.post("/plugins/demo-greeter/tools/build_report",
                          json={"args": {"title": "偷偷出报告"}})
    assert outcome.status_code == 409
    assert outcome.json()["detail"]["error"] == "approval_required"
    # 用户在 UI 弹窗确认（approved=true）后放行——与对话侧同一闸
    ok = client.post("/plugins/demo-greeter/tools/build_report",
                     json={"args": {"title": "确认过的报告"}, "approved": True}).json()
    assert ok["title"] == "确认过的报告"


# ─────────────────────────── 自我开发闭环 ───────────────────────────

SPEC = {
    "id": TEST_PLUGIN_ID,
    "name": "测试笔记",
    "description": "e2e 自我开发闭环验证",
    "feature": "随手记笔记",
    "item_label": "笔记",
    "icon": "StickyNote",
}


def test_self_dev_loop_draft_dry_run_confirm_install(env):
    """说「我需要一个XX功能」→ 草稿 → dry-run → 用户确认 → 装载（菜单/页面/工具长出）"""
    client = env["client"]

    # ① 生成草稿（三件套+自带测试）+ 自动 dry-run
    draft = client.post("/plugins/forge/drafts", json={"spec": SPEC}).json()
    assert draft["draft_id"] == TEST_PLUGIN_ID
    assert set(draft["files"]) == {"plugin.yaml", "main.py", "ui/home.json",
                                  "tests/test_plugin.py"}
    report = draft["dry_run"]
    checks = {c["check"]: c["ok"] for c in report["checks"]}
    assert checks.get("manifest") and checks.get("entry") and checks.get("tests")
    assert checks.get("dsl:home")
    assert report["ok"] is True, report

    # ② 未确认 → 不装（铁律：生成≠安装），但确认所需信息齐全（权限/页面/工具）
    pending = client.post(f"/plugins/forge/drafts/{TEST_PLUGIN_ID}/install",
                          json={"confirmed": False}).json()
    assert pending["installed"] is False and pending["needs_confirmation"] is True
    manifest_preview = pending["manifest"]
    assert manifest_preview["permissions"] == ["ui.serve", "tool.register", "tool.call",
                                               "data.read", "data.write"]
    assert [t["name"] for t in manifest_preview["tools"]] == [
        f"{TEST_PLUGIN_ID}__save_item", f"{TEST_PLUGIN_ID}__list_items"]
    # 装载器未确认不落盘：/plugins 里没有它
    listed = client.get("/plugins").json()["plugins"]
    assert all((p.get("manifest") or {}).get("id") != TEST_PLUGIN_ID for p in listed)

    # ③ 用户确认 → 安装装载（菜单长出来+页面渲染+工具进对话工具表）
    installed = client.post(f"/plugins/forge/drafts/{TEST_PLUGIN_ID}/install",
                            json={"confirmed": True, "decided_by": "用户"}).json()
    assert installed["installed"] is True
    grown = installed["grown"]
    assert grown["tools_registered"] == 2
    assert grown["nav"]["label"] == "测试笔记" and grown["nav"]["icon"] == "StickyNote"
    assert grown["pages"] == ["home"]

    # 菜单：/plugins 带 nav（前端 plugin-nav.ts merge 后长出入口）
    listed = client.get("/plugins").json()["plugins"]
    mine = next(p for p in listed if (p.get("manifest") or {}).get("id") == TEST_PLUGIN_ID)
    assert mine["manifest"]["frontend"]["nav"]["href"] == f"/plugins/{TEST_PLUGIN_ID}"

    # 页面：UI DSL 渲染（form+list+report）
    page = client.get(f"/plugins/{TEST_PLUGIN_ID}/ui/home").json()
    assert page["version"] == 1 and len(page["panels"]) == 3
    assert page["panels"][0]["submit"]["tool"] == f"{TEST_PLUGIN_ID}__save_item"

    # 工具：面板与对话共用（function-calling 工具表同名同 schema）
    tools = client.get(f"/plugins/{TEST_PLUGIN_ID}/tools").json()["tools"]
    assert {t["name"] for t in tools} == {
        f"{TEST_PLUGIN_ID}__save_item", f"{TEST_PLUGIN_ID}__list_items"}
    saved = client.post(f"/plugins/{TEST_PLUGIN_ID}/tools/save_item",
                        json={"args": {"content": "第一篇笔记", "tag": "e2e"}}).json()
    assert saved["content"] == "第一篇笔记"
    rows = client.post(f"/plugins/{TEST_PLUGIN_ID}/tools/list_items",
                       json={"args": {"limit": 5}}).json()
    assert rows["count"] >= 1 and rows["items"][-1]["content"] == "第一篇笔记"

    # ④ 沉淀：重复安装被拒（草稿已消耗进 plugins/），不再保留草稿
    repeat = client.post(f"/plugins/forge/drafts/{TEST_PLUGIN_ID}/install",
                         json={"confirmed": True})
    assert repeat.status_code == 409
    drafts = client.get("/plugins/forge/drafts").json()["drafts"]
    assert all(d["draft_id"] != TEST_PLUGIN_ID for d in drafts)


def test_self_dev_loop_dry_run_blocks_install(env):
    """dry-run 不过不能装：坏草稿被拒且报因明确"""
    client = env["client"]
    bad_files = {
        "plugin.yaml": (
            "id: zz-bad-dsl\nname: 坏DSL\nversion: 0.1.0\nentry: main.py\n"
            "permissions: [ui.serve, tool.register, tool.call]\n"
            "ui:\n  pages:\n    - id: home\n      title: H\n      dsl: ui/home.json\n"
        ),
        "main.py": "def handle_x(args, ctx):\n    return {}\n",
        "ui/home.json": '{"version": 1, "page": {"title": "H"}, "panels": []}',
    }
    draft = client.post("/plugins/forge/drafts",
                        json={"files": bad_files, "spec": {"id": "zz-bad-dsl"}}).json()
    assert draft["dry_run"]["ok"] is False
    assert any(c["check"] == "dsl:home" and not c["ok"]
               for c in draft["dry_run"]["checks"])
    outcome = client.post("/plugins/forge/drafts/zz-bad-dsl/install",
                          json={"confirmed": True})
    assert outcome.status_code == 409
    assert "dry-run 未通过" in outcome.json()["detail"]
    assert not (env["plugins_dir"] / "zz-bad-dsl").exists()


# ─────────────────────────── 流程清理面 ───────────────────────────

def test_delete_flow_purges_definition_and_runs(env):
    """DELETE /flows/{id}：定义+实例+幂等键全清（测试流彻底清理，面板零残留）"""
    client = env["client"]
    started = client.post("/flows/daily-greet-report/run",
                          json={"inputs": {}, "idempotency_key": "del-1"}).json()
    run_id = started["run_id"]
    assert started["status"] == "waiting-approval"

    deleted = client.delete("/flows/daily-greet-report").json()
    assert deleted == {"flow_id": "daily-greet-report",
                       "removed_definition": True, "purged_runs": 1}
    # 定义清零 + 实例清零（flow-console 面板数据源为空，无残留）
    assert all(f["id"] != "daily-greet-report"
               for f in client.get("/flows").json()["flows"])
    assert client.get("/flows/runs").json()["count"] == 0
    assert client.get(f"/flows/runs/{run_id}").status_code == 404
    # 幂等键随实例清掉：重新定义后同键可再次触发（防再生测试夹具独立）
    again = client.post("/flows/daily-greet-report/run", json={"inputs": {}})
    assert again.status_code == 404          # 定义已删，无法触发
    # 幂等清理：连删两次，第二次无残留可报 404
    assert client.delete("/flows/daily-greet-report").status_code == 404
