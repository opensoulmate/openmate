"""
SoulMate 触发面单测 — agent/plugin_tools.py：工具表/分发/审批闸/plugin_dev 闭环

覆盖（docs/plugin-system-design.md §4/§7 + 用户铁律）：
① 插件 MCP 工具（<plugin-id>__<tool>）进 function-calling 工具表（与 UI 共用）
② 分发：进程内注册桥直调 / HTTP 面兜底；未知工具返回 None 交回 MCP 路由
③ 审批闸：approval=always/on-first-use 先经真人确认（生成文档类须确认），拒绝不执行
④ plugin_dev 自我开发闭环：draft+dry-run → install 必须用户确认（生成≠安装）
⑤ SoulMateAgent._plugin_tools_dispatch 接线钉（下载四件套同款分发入口）
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_PLUGIN_ID = "zz-agent-notes"


class HttpShim:
    """httpx.get/post 垫片：出站 HTTP 转给同进程 TestClient（测 HTTP 兜底通道）"""

    def __init__(self, client: TestClient, base: str = "http://testserver"):
        self.client = client
        self.base = base

    def get(self, url: str, timeout=None):
        return self.client.get(url[len(self.base):] or "/")

    def post(self, url: str, json=None, timeout=None):
        return self.client.post(url[len(self.base):], json=json)


@pytest.fixture()
def rig(tmp_path, monkeypatch, plugins_dir):
    """插件注册桥 + forge/插件 HTTP 面 + PluginTools（注入 registry/http）"""
    monkeypatch.setenv("FLOW_RUNS_DIR", str(tmp_path / "flow-runs"))
    monkeypatch.setenv("OPENMATE_PLUGIN_DATA_DIR", str(tmp_path / "plugin-data"))
    from agent.plugin_tools import PluginTools
    from plugin.flow_engine import get_engine, reset_engine
    from plugin.forge_api import build_forge_router
    from plugin.mcp_bridge import PluginToolRegistry
    from plugin.scanner import bind_tools, scan_plugins

    reset_engine()
    registry = PluginToolRegistry()
    scan = scan_plugins(plugins_dir)
    demo = scan.get("demo-greeter")
    assert demo is not None
    bind_tools(demo, registry)

    app = FastAPI()
    app.include_router(build_forge_router(
        plugins_dir, registry=registry, engine=get_engine(),
        include_router=app.include_router))

    @app.post("/plugins/{plugin_id}/tools/{tool_name}")
    async def call_tool(plugin_id: str, tool_name: str, req: dict):
        from plugin.mcp_bridge import ApprovalRequired, SchemaValidationError, ToolNotFound

        ctx_cls = __import__("plugin.mcp_bridge", fromlist=["ToolContext"]).ToolContext
        try:
            outcome = await registry.call_tool_async(
                f"{plugin_id}__{tool_name}", req.get("args") or {},
                approved=bool(req.get("approved")),
                ctx=ctx_cls(plugin_id=plugin_id))
            return outcome
        except ApprovalRequired:
            return {"error": "approval_required"}
        except (ToolNotFound, SchemaValidationError) as e:
            return {"error": str(e)}

    client = TestClient(app)
    shim = HttpShim(client)
    tools = PluginTools(http_base=shim.base, registry=registry, http=shim)
    yield {"client": client, "registry": registry, "tools": tools, "shim": shim,
           "plugins_dir": plugins_dir}
    registry.unregister_plugin(TEST_PLUGIN_ID)
    for path in (plugins_dir / TEST_PLUGIN_ID, plugins_dir / ".drafts" / TEST_PLUGIN_ID):
        if path.exists():
            shutil.rmtree(path)
    reset_engine()


# ─────────────────────────── ① 工具表 ───────────────────────────

def test_tool_defs_function_calling_shape(rig):
    tools = rig["tools"]

    async def scenario():
        defs = await tools.tool_defs()
        by_name = {d["function"]["name"]: d["function"] for d in defs}
        assert "plugin_dev" in by_name                       # 自我开发闭环工具
        assert "demo-greeter__greet" in by_name              # 插件 MCP 工具（命名桥契约）
        assert "demo-greeter__build_report" in by_name
        greet = by_name["demo-greeter__greet"]
        assert greet["parameters"]["required"] == ["name"]   # input_schema → parameters
        assert set(by_name["plugin_dev"]["parameters"]["properties"]) >= {
            "action", "id", "feature"}
        assert all(d["type"] == "function" for d in defs)
        return by_name

    asyncio.run(scenario())


# ─────────────────────────── ② 分发 ───────────────────────────

def test_dispatch_registry_path_and_unknown_passthrough(rig):
    tools = rig["tools"]

    async def scenario():
        # 进程内注册桥直调（UI 与对话共用同一 handler）
        result = await tools.run("demo-greeter__greet", {"name": "小明", "style": "casual"})
        assert result is not None and "嗨，小明" in result
        # 未知工具返回 None（交回 MCP 路由），不吞调用
        assert await tools.run("some_mcp_tool", {}) is None
        assert tools.handles("demo-greeter__greet") is True
        assert tools.handles("some_mcp_tool") is False

    asyncio.run(scenario())


def test_dispatch_http_fallback(rig):
    """进程内注册桥无此工具时走 HTTP 面（POST /plugins/{id}/tools/{tool}）"""
    from agent.plugin_tools import PluginTools

    empty_registry = rig["registry"].__class__()
    tools = PluginTools(http_base=rig["shim"].base, registry=empty_registry,
                        http=rig["shim"])

    async def scenario():
        # 注册桥为空 → HTTP 兜底（同进程 TestClient 应答）
        tools._defs_cache = (0.0, [])   # 绕过 handles 缓存判别：靠 HTTP 面出结果
        result = await tools._call_tool("demo-greeter__greet", {"name": "网路"})
        assert "网路" in result

    asyncio.run(scenario())


def test_soulmate_agent_dispatch_wiring(rig):
    """接线钉：SoulMateAgent._plugin_tools_dispatch（下载四件套同款分发入口）"""
    from agent.plugin_tools import PluginTools
    from agent.soulmate_agent import SoulMateAgent

    agent = SoulMateAgent.__new__(SoulMateAgent)   # 裸实例：lazy-init 自举
    agent._plugin_tools_inst = PluginTools(
        http_base=rig["shim"].base, registry=rig["registry"], http=rig["shim"])

    async def scenario():
        result = await agent._plugin_tools_dispatch(
            "demo-greeter__greet", {"name": "接线"})
        assert result is not None and "接线" in result
        assert await agent._plugin_tools_dispatch("not_a_plugin_tool", {}) is None

    asyncio.run(scenario())


# ─────────────────────────── ③ 审批闸 ───────────────────────────

def test_approval_gate_blocks_then_allows(rig):
    """生成文档类（approval=always）：拒绝=不执行；批准=放行（铁律：须确认）"""
    tools = rig["tools"]

    async def scenario():
        decisions = []

        async def deny(tool_name, tool_args, decision):
            decisions.append((tool_name, decision))
            return False

        result = await tools.run("demo-greeter__build_report",
                                 {"title": "偷跑报告"}, request_approval=deny)
        assert result.startswith("[已拒绝]")                 # 未批准不执行
        assert decisions and decisions[0][0] == "demo-greeter__build_report"

        async def allow(tool_name, tool_args, decision):
            return True

        ok = await tools.run("demo-greeter__build_report",
                             {"title": "确认过的报告"}, request_approval=allow)
        assert "确认过的报告" in ok
        # approval=never 的工具不弹确认
        calls = []

        async def record(tool_name, tool_args, decision):
            calls.append(tool_name)
            return True

        await tools.run("demo-greeter__greet", {"name": "x"}, request_approval=record)
        assert calls == []

    asyncio.run(scenario())


# ─────────────────────────── ④ plugin_dev 闭环 ───────────────────────────

SPEC = {
    "id": TEST_PLUGIN_ID,
    "name": "代理笔记",
    "description": "agent 测试",
    "feature": "随手记",
    "item_label": "笔记",
    "icon": "StickyNote",
}


def test_plugin_dev_draft_and_install_needs_confirmation(rig):
    """SoulMate 生成草稿 → dry-run → install 必须用户确认（生成≠安装）"""
    tools = rig["tools"]

    async def scenario():
        import json

        draft = json.loads(await tools.plugin_dev(
            {"action": "draft", **SPEC}))
        assert draft["draft_id"] == TEST_PLUGIN_ID
        assert draft["dry_run"]["ok"] is True, draft["dry_run"]

        # 未确认（无审批回调）→ 不装
        blocked = await tools.plugin_dev(
            {"action": "install", "id": TEST_PLUGIN_ID})
        assert blocked.startswith("[已拒绝]")
        drafts = json.loads(await tools.plugin_dev({"action": "list"}))
        assert any(d["draft_id"] == TEST_PLUGIN_ID for d in drafts["drafts"])

        # 用户确认（审批回调放行）→ 安装载入
        async def allow(tool_name, tool_args, decision):
            assert tool_name == "plugin_dev"
            return True

        installed = json.loads(await tools.plugin_dev(
            {"action": "install", "id": TEST_PLUGIN_ID}, request_approval=allow))
        assert installed["installed"] is True
        assert installed["grown"]["tools_registered"] == 2
        assert installed["grown"]["nav"]["label"] == "代理笔记"

    asyncio.run(scenario())


def test_plugin_dev_install_denied_keeps_draft(rig):
    """用户拒绝安装：草稿保留、不落 plugins/（可后续再确认）"""
    tools = rig["tools"]

    async def scenario():
        import json

        await tools.plugin_dev({"action": "draft", **SPEC})

        async def deny(tool_name, tool_args, decision):
            return False

        blocked = await tools.plugin_dev(
            {"action": "install", "id": TEST_PLUGIN_ID}, request_approval=deny)
        assert blocked.startswith("[已拒绝]") and "草稿保留未安装" in blocked
        assert not (rig["plugins_dir"] / TEST_PLUGIN_ID).exists()
        drafts = json.loads(await tools.plugin_dev({"action": "list"}))
        assert any(d["draft_id"] == TEST_PLUGIN_ID for d in drafts["drafts"])

    asyncio.run(scenario())


def test_plugin_dev_bad_action_reports_error(rig):
    tools = rig["tools"]

    async def scenario():
        result = await tools.plugin_dev({"action": "explode", "id": "x"})
        assert result.startswith("错误:")

    asyncio.run(scenario())
