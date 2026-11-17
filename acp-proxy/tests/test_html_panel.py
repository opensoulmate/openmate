# -*- coding: utf-8 -*-
"""Sprint C HTML 面板层②——静态只读服务（step ①）+ postMessage RPC 宿主侧
（step ③）。

设计 docs/plugin-system-design.md §3：html/ 只读静态子树、
{id, method, params}→{id, result|error}、method 白名单=声明工具+ui.* 事件、
工具调用与 DSL 面同一审批闸。整合面：plugin/dsl_api.py（DSL 面同一路由工厂）。

通用插件面板能力（非投标专用）。
"""
import asyncio

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import plugin.dsl_api as dsl_api
from plugin.mcp_bridge import PluginToolRegistry
from plugin.scanner import bind_tools, scan_plugins

MANIFEST = """\
id: html-demo
name: HTML面板演示
version: 0.1.0
description: 层②验收样板
author: t
entry: main.py
permissions: [tool.register, tool.call]
ui:
  pages:
    - id: panel
      title: 面板
      html: html/panel.html
tools:
  - name: ping
    description: ping一下
    handler: handle_ping
    input_schema: {type: object, properties: {n: {type: integer}}}
  - name: danger
    description: 需人工确认的动作
    handler: handle_danger
    approval: always
    input_schema: {type: object}
"""

ENTRY = """\
def handle_ping(args, ctx):
    return {"pong": int(args.get("n") or 0) + 1}


def handle_danger(args, ctx):
    return {"done": True}
"""

PANEL = """\
<!doctype html><html><body><h1>面板</h1>
<button onclick="rpc('ping', {n: 1})">ping</button>
<script>
async function rpc(method, params) {
  const res = await fetch('html-rpc', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({id: 1, method, params})});
  return res.json();   // {id, result|error}
}
</script></body></html>
"""


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def plugin_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENMATE_PLUGIN_DATA_DIR", str(tmp_path / "plugin-data"))
    d = tmp_path / "plugins" / "html-demo"
    (d / "html").mkdir(parents=True)
    (d / "plugin.yaml").write_text(MANIFEST, encoding="utf-8")
    (d / "main.py").write_text(ENTRY, encoding="utf-8")
    (d / "html" / "panel.html").write_text(PANEL, encoding="utf-8")
    (d / "html" / "app.js").write_text("console.log('x')", encoding="utf-8")
    return d


@pytest.fixture()
def wired(plugin_dir):
    registry = PluginToolRegistry()
    scan = scan_plugins(plugin_dir.parent)
    descriptor = scan.get("html-demo")
    assert descriptor is not None, scan.errors
    bind_tools(descriptor, registry)
    return descriptor, registry


@pytest.fixture()
def client(wired):
    descriptor, registry = wired
    app = FastAPI()
    app.include_router(dsl_api.build_plugin_router(descriptor, registry))
    return TestClient(app), descriptor, registry


# ── ① html/ 静态只读服务 ─────────────────────────────────────────

class TestStaticServing:
    def test_html_served_with_content_type(self, client):
        c, _, _ = client
        r = c.get("/plugins/html-demo/html/panel.html")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "<h1>面板</h1>" in r.text
        assert r.headers["x-content-type-options"] == "nosniff"

    def test_js_content_type(self, client):
        c, _, _ = client
        r = c.get("/plugins/html-demo/html/app.js")
        assert r.status_code == 200
        assert "text/javascript" in r.headers["content-type"]

    def test_traversal_rejected(self, client):
        c, _, _ = client
        for evil in ("../plugin.yaml", "../../etc/passwd",
                     "..%2Fplugin.yaml", "sub/../../main.py"):
            r = c.get(f"/plugins/html-demo/html/{evil}")
            assert r.status_code in (403, 404), evil
            assert "id: html-demo" not in r.text               # 清单绝不泄出

    def test_missing_file_404(self, client):
        c, _, _ = client
        assert c.get("/plugins/html-demo/html/ghost.html").status_code == 404

    def test_no_html_dir_404(self, wired, tmp_path):
        descriptor, registry = wired
        (descriptor.dir_path / "html" / "panel.html").unlink()
        (descriptor.dir_path / "html" / "app.js").unlink()
        (descriptor.dir_path / "html").rmdir()
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            run(dsl_api._html_file(descriptor, "panel.html"))
        assert ei.value.status_code == 404


# ── ③ postMessage RPC 宿主侧（白名单 + 同一审批闸）────────────────

class TestHtmlRpc:
    def test_method_whitelist_rejects_undeclared(self, wired):
        """白名单穿透测试（验收项）：未声明 method 必拒。"""
        descriptor, registry = wired
        out = run(dsl_api.html_panel_rpc(
            descriptor, registry,
            {"id": 7, "method": "delete_everything", "params": {}}))
        assert out["id"] == 7
        assert out["error"]["code"] == "method_not_allowed"
        # 面板调用未声明的其它工具名同样必拒
        out2 = run(dsl_api.html_panel_rpc(
            descriptor, registry, {"id": 8, "method": "unlisted"}))
        assert out2["error"]["code"] == "method_not_allowed"

    def test_declared_tool_dispatches(self, wired):
        descriptor, registry = wired
        out = run(dsl_api.html_panel_rpc(
            descriptor, registry, {"id": 1, "method": "ping", "params": {"n": 41}}))
        assert out == {"id": 1, "result": {"pong": 42}}

    def test_approval_gate_shared_with_dsl(self, wired):
        """同一审批闸：approval=always 工具未批必拒（approval_required），
        带 approved=true 重发才执行——与 DSL 面 tools 调用同构。"""
        descriptor, registry = wired
        out = run(dsl_api.html_panel_rpc(
            descriptor, registry, {"id": 2, "method": "danger", "params": {}}))
        assert out["error"]["code"] == "approval_required"
        out2 = run(dsl_api.html_panel_rpc(
            descriptor, registry, {"id": 3, "method": "danger", "params": {}},
            approved=True))
        assert out2["result"] == {"done": True}

    def test_tool_not_found_after_unbind(self, wired):
        descriptor, registry = wired
        registry.unregister_plugin("html-demo")
        out = run(dsl_api.html_panel_rpc(
            descriptor, registry, {"id": 4, "method": "ping", "params": {}}))
        assert out["error"]["code"] == "tool_not_found"

    def test_ui_store_roundtrip_and_cap(self, wired, monkeypatch):
        descriptor, registry = wired
        run(dsl_api.html_panel_rpc(
            descriptor, registry,
            {"id": 5, "method": "ui.store.set", "params": {"value": {"a": 1}}}))
        out = run(dsl_api.html_panel_rpc(
            descriptor, registry, {"id": 6, "method": "ui.store.get"}))
        assert out["result"]["value"] == {"a": 1}
        big = "x" * (dsl_api.HTML_RPC_MAX_STORE_BYTES + 1)
        out2 = run(dsl_api.html_panel_rpc(
            descriptor, registry,
            {"id": 7, "method": "ui.store.set", "params": {"value": big}}))
        assert out2["error"]["code"] == "store_value_too_large"

    def test_ui_events_ack(self, wired):
        descriptor, registry = wired
        for m in ("ui.toast", "ui.refresh"):
            out = run(dsl_api.html_panel_rpc(
                descriptor, registry, {"id": 1, "method": m}))
            assert out["result"] == {"ok": True, "method": m}

    def test_methods_endpoint_shape(self, client):
        c, _, _ = client
        r = c.get("/plugins/html-demo/html-methods")
        assert r.status_code == 200
        assert r.json() == {"tools": ["danger", "ping"],
                            "ui_events": list(dsl_api.UI_PANEL_EVENTS)}

    def test_rpc_route_end_to_end(self, client):
        c, _, _ = client
        r = c.post("/plugins/html-demo/html-rpc",
                   json={"id": 9, "method": "ping", "params": {"n": 1}})
        assert r.status_code == 200 and r.json() == {"id": 9, "result": {"pong": 2}}
        r2 = c.post("/plugins/html-demo/html-rpc",
                    json={"id": 10, "method": "evil"})
        assert r2.json()["error"]["code"] == "method_not_allowed"
