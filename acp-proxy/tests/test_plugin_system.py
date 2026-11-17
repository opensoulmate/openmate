"""
插件系统 PoC 单测 — plugin.yaml 装载器 / UI DSL 校验 / MCP 工具注册桥 / demo 插件端到端

覆盖（docs/plugin-system-design.md 第一路验收项）：
① plugin.yaml 解析+目录扫描+路由/导航注册
② UI DSL 最小版（form/list/report）校验
③ MCP 工具注册桥（注册/列取/调用/审批闸/卸载）
④ demo 插件跑通：菜单长出来（/plugins 带 nav）+ 页面渲染（/ui/home 三面板）
   + 一个工具可调（POST /plugins/demo-greeter/tools/greet）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.mcp_bridge import (
    ApprovalRequired,
    PluginToolRegistry,
    SchemaValidationError,
    ToolContext,
    ToolNotFound,
    tool_registry,
)
from plugin.scanner import bind_tools, collect_nav, load_entry_module, scan_plugins
from plugin.ui_dsl import DslError, FormPanel, validate_dsl_page
from plugin.yaml_manifest import ManifestError, ToolSpec, load_yaml_manifest, parse_yaml_manifest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
DEMO_DIR = PLUGINS_DIR / "demo-greeter"


# ─────────────────────────── ① plugin.yaml 清单解析 ───────────────────────────


def test_load_demo_manifest():
    manifest = load_yaml_manifest(DEMO_DIR)
    assert manifest.id == "demo-greeter"
    assert manifest.version == "0.1.0"
    assert manifest.nav is not None and manifest.nav.label == "问候演示"
    assert [p.id for p in manifest.pages] == ["home"]
    assert [t.name for t in manifest.tools] == ["greet", "greet_log", "build_report"]
    assert manifest.permissions == ("ui.serve", "tool.register", "tool.call", "data.read", "data.write")


def test_manifest_to_frontend_shape():
    """前端 plugin-nav.ts 消费形状：manifest.id + frontend.nav（href/icon/position）"""
    manifest = load_yaml_manifest(DEMO_DIR)
    data = manifest.to_frontend_manifest()
    assert data["id"] == "demo-greeter"
    nav = data["frontend"]["nav"]
    assert nav["href"] == "/plugins/demo-greeter"
    assert nav["icon"] == "Sparkles"
    assert nav["position"] == "end"


def _minimal_manifest(**overrides):
    base = {
        "id": "t-plt",
        "name": "测试",
        "version": "1.0.0",
        "entry": "main.py",
        "permissions": [],
    }
    base.update(overrides)
    return base


def test_manifest_rejects_bad_id():
    with pytest.raises(ManifestError, match="id 非法"):
        parse_yaml_manifest(_minimal_manifest(id="Bad_ID"), dir_name="Bad_ID", base_dir=DEMO_DIR)


def test_manifest_rejects_dir_mismatch():
    with pytest.raises(ManifestError, match="与目录名"):
        parse_yaml_manifest(_minimal_manifest(), dir_name="other-dir", base_dir=DEMO_DIR)


def test_manifest_rejects_unknown_permission():
    with pytest.raises(ManifestError, match="未知权限"):
        parse_yaml_manifest(
            _minimal_manifest(permissions=["root.everything"]), dir_name="t-plt", base_dir=DEMO_DIR
        )


def test_manifest_rejects_bad_nav_position():
    with pytest.raises(ManifestError, match="position"):
        parse_yaml_manifest(
            _minimal_manifest(ui={"nav": {"label": "X", "icon": "Star", "position": "middle"}}),
            dir_name="t-plt",
            base_dir=DEMO_DIR,
        )


def test_manifest_rejects_missing_entry(tmp_path):
    with pytest.raises(ManifestError, match="entry 文件不存在"):
        parse_yaml_manifest(_minimal_manifest(), dir_name="t-plt", base_dir=tmp_path)


def test_manifest_page_needs_exactly_one_of_dsl_html():
    with pytest.raises(ManifestError, match="dsl 或 html"):
        parse_yaml_manifest(
            _minimal_manifest(ui={"pages": [{"id": "p", "title": "P"}]}),
            dir_name="t-plt",
            base_dir=DEMO_DIR,
        )


def test_manifest_tool_schema():
    with pytest.raises(ManifestError, match="approval 非法"):
        parse_yaml_manifest(
            _minimal_manifest(
                tools=[{"name": "t", "description": "d", "handler": "h", "approval": "sometimes"}]
            ),
            dir_name="t-plt",
            base_dir=DEMO_DIR,
        )


# ─────────────────────────── ① 目录扫描 + 导航注册 ───────────────────────────


def test_scan_plugins_dir():
    result = scan_plugins(PLUGINS_DIR)
    demo = result.get("demo-greeter")
    assert demo is not None
    assert demo.state == "enabled"
    assert demo.page_path("home") == DEMO_DIR / "ui" / "home.json"
    # data/ __pycache__ 等非插件目录被跳过
    assert all(d.id != "data" for d in result.descriptors)


def test_scan_collects_nav():
    result = scan_plugins(PLUGINS_DIR)
    nav = collect_nav(result)
    demo_nav = [item for item in nav if item["pluginId"] == "demo-greeter"]
    assert demo_nav and demo_nav[0]["href"] == "/plugins/demo-greeter"
    assert demo_nav[0]["label"] == "问候演示"


def test_scan_bad_manifest_recorded_not_fatal(tmp_path):
    (tmp_path / "bad-one").mkdir()
    (tmp_path / "bad-one" / "plugin.yaml").write_text("id: wrong-dir\n", encoding="utf-8")
    (tmp_path / "good-one").mkdir()
    (tmp_path / "good-one" / "plugin.yaml").write_text(
        yaml.safe_dump(
            {"id": "good-one", "name": "好", "version": "0.0.1", "entry": "main.py"},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (tmp_path / "good-one" / "main.py").write_text("# stub\n", encoding="utf-8")
    result = scan_plugins(tmp_path)
    assert [d.id for d in result.descriptors] == ["good-one"]
    assert result.errors and result.errors[0]["plugin"] == "bad-one"


def test_load_entry_module():
    result = scan_plugins(PLUGINS_DIR)
    demo = result.get("demo-greeter")
    assert demo is not None
    module = load_entry_module(demo)
    assert callable(module.handle_greet)


# ─────────────────────────── ② UI DSL 校验（form/list/report）───────────────────────────


def test_validate_demo_dsl():
    raw = json.loads((DEMO_DIR / "ui" / "home.json").read_text(encoding="utf-8"))
    page = validate_dsl_page(raw)
    kinds = [panel.kind for panel in page.panels]
    assert kinds == ["form", "report", "list"]
    form = page.panels[0]
    assert isinstance(form, FormPanel)
    assert form.submit_tool == "demo-greeter__greet"
    assert form.target_panel == "greet-result"
    assert [f.name for f in form.fields] == ["name", "style"]


def _dsl(**panel_overrides):
    panel = {"type": "report", "id": "p1", "title": "T", "sections": [{"kind": "text", "content": "x"}]}
    panel.update(panel_overrides)
    return {"version": 1, "page": {"title": "P"}, "panels": [panel]}


def test_dsl_rejects_unknown_panel_type():
    with pytest.raises(DslError, match="type 非法"):
        validate_dsl_page(_dsl(type="carousel"))


def test_dsl_rejects_bad_widget():
    with pytest.raises(DslError, match="widget 非法"):
        validate_dsl_page(
            _dsl(type="form", fields=[{"name": "a", "label": "A", "widget": "slider"}], submit={"tool": "x__y"})
        )


def test_dsl_rejects_bad_tool_name():
    with pytest.raises(DslError, match="工具名非法"):
        validate_dsl_page(_dsl(type="form", fields=[], submit={"tool": "greet"}))


def test_dsl_rejects_unknown_target_panel():
    with pytest.raises(DslError, match="target_panel 未声明"):
        validate_dsl_page(_dsl(type="form", fields=[], submit={"tool": "x__y", "target_panel": "ghost"}))


def test_dsl_list_requires_columns():
    with pytest.raises(DslError, match="columns"):
        validate_dsl_page(_dsl(type="list", columns=[]))


def test_dsl_bars_items_need_numeric_value():
    with pytest.raises(DslError, match="数值"):
        validate_dsl_page(
            _dsl(type="report", sections=[{"kind": "bars", "items": [{"label": "a", "value": "很多"}]}])
        )


# ─────────────────────────── ③ MCP 工具注册桥 ───────────────────────────


def _bind_demo(registry: PluginToolRegistry) -> None:
    result = scan_plugins(PLUGINS_DIR)
    demo = result.get("demo-greeter")
    assert demo is not None
    bind_tools(demo, registry)


def test_bind_and_list_tools_mcp_shape():
    registry = PluginToolRegistry()
    _bind_demo(registry)
    tools = registry.list_tools()
    names = [t["name"] for t in tools]
    assert names == ["demo-greeter__build_report", "demo-greeter__greet", "demo-greeter__greet_log"]
    greet = next(t for t in tools if t["name"] == "demo-greeter__greet")
    assert greet["inputSchema"]["required"] == ["name"]
    assert set(greet.keys()) == {"name", "description", "inputSchema"}


def test_call_greet_tool():
    registry = PluginToolRegistry()
    _bind_demo(registry)
    ctx = ToolContext(plugin_id="demo-greeter")
    result = registry.call_tool("demo-greeter__greet", {"name": "小明", "style": "casual"}, ctx=ctx)
    assert result["greeting"] == "嗨，小明！今天也要加油鸭～"
    log = registry.call_tool("demo-greeter__greet_log", {"limit": 5}, ctx=ctx)
    assert log["count"] >= 1


def test_call_tool_schema_validation():
    registry = PluginToolRegistry()
    _bind_demo(registry)
    with pytest.raises(SchemaValidationError, match="缺少必填参数"):
        registry.call_tool("demo-greeter__greet", {})
    with pytest.raises(SchemaValidationError, match="取值应为"):
        registry.call_tool("demo-greeter__greet", {"name": "x", "style": "angry"})
    with pytest.raises(SchemaValidationError, match="类型应为"):
        registry.call_tool("demo-greeter__greet_log", {"limit": "很多"})


def test_call_unknown_tool():
    registry = PluginToolRegistry()
    with pytest.raises(ToolNotFound):
        registry.call_tool("demo-greeter__nope", {})


def test_approval_gate_always():
    registry = PluginToolRegistry()
    spec = ToolSpec(name="danger", description="危险", handler="h", input_schema={}, approval="always")
    registry.register("p-1", spec, lambda args, ctx: {"ok": True}, {"tool.register"})
    with pytest.raises(ApprovalRequired):
        registry.call_tool("p-1__danger", {})
    assert registry.call_tool("p-1__danger", {}, approved=True) == {"ok": True}


def test_approval_gate_on_first_use():
    registry = PluginToolRegistry()
    spec = ToolSpec(name="confirm", description="首用确认", handler="h", input_schema={}, approval="on-first-use")
    registry.register("p-1", spec, lambda args, ctx: {"ok": True}, {"tool.register"})
    with pytest.raises(ApprovalRequired):
        registry.call_tool("p-1__confirm", {})
    # 用户批准一次后放行，且不再要求
    assert registry.call_tool("p-1__confirm", {}, approved=True) == {"ok": True}
    assert registry.call_tool("p-1__confirm", {}) == {"ok": True}


def test_register_requires_permission():
    registry = PluginToolRegistry()
    spec = ToolSpec(name="t", description="d", handler="h", input_schema={})
    with pytest.raises(PermissionError, match="tool.register"):
        registry.register("p-1", spec, lambda args, ctx: {}, set())


def test_unregister_plugin_removes_tools():
    registry = PluginToolRegistry()
    _bind_demo(registry)
    assert registry.unregister_plugin("demo-greeter") == 3
    assert registry.list_tools() == []


def test_call_tool_mcp_error_shape():
    registry = PluginToolRegistry()
    outcome = registry.call_tool_mcp("demo-greeter__greet", {})
    assert outcome["isError"] is True
    assert "ToolNotFound" in outcome["content"][0]["text"]


# ─────────────────────────── ④ demo 插件端到端（FastAPI）───────────────────────────


@pytest.fixture()
def client():
    from plugin.dsl_api import scan_and_build

    registry = PluginToolRegistry()
    routers, index = scan_and_build(PLUGINS_DIR, registry)
    app = FastAPI()
    for router in routers:
        app.include_router(router)

    @app.get("/plugins")
    async def list_plugins():
        return index

    return TestClient(app)


def test_e2e_menu_grows(client):
    """菜单长出来：/plugins 返回 demo 插件且带 nav 声明"""
    data = client.get("/plugins").json()
    names = [p["manifest"]["id"] for p in data["plugins"]]
    assert "demo-greeter" in names
    demo = next(p for p in data["plugins"] if p["manifest"]["id"] == "demo-greeter")
    assert demo["status"] == "loaded" or demo["status"] == "enabled"
    assert demo["manifest"]["frontend"]["nav"]["label"] == "问候演示"


def test_e2e_page_renders(client):
    """页面渲染：/ui/home 返回经校验的三面板 DSL（前端宿主组件据此渲染）"""
    data = client.get("/plugins/demo-greeter/ui/home").json()
    kinds = [p["type"] for p in data["panels"]]
    assert kinds == ["form", "report", "list"]
    assert data["panels"][0]["submit"]["tool"] == "demo-greeter__greet"


def test_e2e_tool_callable(client):
    """一个工具可调：POST /tools/greet"""
    resp = client.post(
        "/plugins/demo-greeter/tools/greet",
        json={"args": {"name": "验收", "style": "formal"}},
    )
    assert resp.status_code == 200
    assert resp.json()["greeting"] == "验收，您好。"


def test_e2e_tool_errors(client):
    resp = client.post("/plugins/demo-greeter/tools/greet", json={"args": {"style": "formal"}})
    assert resp.status_code == 400
    resp = client.post("/plugins/demo-greeter/tools/ghost", json={"args": {}})
    assert resp.status_code == 404


def test_e2e_manifest_endpoint(client):
    data = client.get("/plugins/demo-greeter/manifest").json()
    assert data["id"] == "demo-greeter"
    assert [t["name"] for t in data["tools"]] == ["greet", "greet_log", "build_report"]
