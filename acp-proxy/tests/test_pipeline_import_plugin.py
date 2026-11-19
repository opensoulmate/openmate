"""
pipeline-import 插件 — 导入加工（流水线=后端工序）流程引擎桥测试

用户架构纠正（2026-11-19）："那我觉得它应该是后端的功能流程啊，怎么会是前端的"
——OpenPipeline 流水线归位为 OpenSoul 后端工序，本插件把它注册为流程引擎
tool（flow 节点可调「导入加工」）。本测试离线证：
① 清单/工具绑定（pipeline-import__import 进 MCP 工具注册桥）
② 流程定义校验（flows/import-kb.yaml 过 parse_flow）
③ 引擎全链（FlowEngine run_now → tool 节点 → 桥 → handler；HTTP 面打桩，
   真后端链见 e2e_pipeline_import_flow.py 的 live 实证）
④ 入参契约（file_path/file_id 二选一、$run.inputs 缺省 None 不炸 schema）
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.flow_def import load_flow_file
from plugin.flow_engine import FlowEngine
from plugin.mcp_bridge import PluginToolRegistry
from plugin.scanner import bind_tools, scan_plugins

PLUGINS_DIR = Path(__file__).resolve().parent.parent.parent / "plugins"
PLUGIN_DIR = PLUGINS_DIR / "pipeline-import"


@pytest.fixture()
def registry() -> PluginToolRegistry:
    reg = PluginToolRegistry()
    scan = scan_plugins(PLUGINS_DIR)
    descriptor = scan.get("pipeline-import")
    assert descriptor is not None, f"pipeline-import 插件未扫到: {scan.errors}"
    bind_tools(descriptor, reg)
    return reg


class TestManifestAndBinding:
    def test_tool_registered_on_bridge(self, registry):
        assert registry.has_tool("pipeline-import__import")
        meta = registry.tool_meta("pipeline-import__import")
        assert meta["approval"] == "never"
        assert meta["input_schema"]["type"] == "object"

    def test_flow_def_parses(self):
        defn = load_flow_file(PLUGIN_DIR / "flows" / "import-kb.yaml")
        assert defn.id == "pipeline-import"
        node_types = {n.id: n.type for n in defn.nodes}
        assert node_types["import"] == "tool"
        import_node = next(n for n in defn.nodes if n.id == "import")
        assert import_node.tool == "pipeline-import__import"


class TestArgContract:
    def test_missing_inputs_guard(self, registry):
        """$run.inputs 缺省解析成 None：schema 不炸、handler 出可读错。"""
        out = asyncio.run(
            registry.call_tool_async(
                "pipeline-import__import",
                {"file_path": None, "file_id": None, "pipeline": None},
            )
        )
        assert "error" in out and "file_path" in out["error"]

    def test_bad_pipeline_rejected(self, registry):
        out = asyncio.run(
            registry.call_tool_async(
                "pipeline-import__import",
                {"file_path": "/tmp/whatever", "pipeline": "wrong-line"},
            )
        )
        assert "pipeline 取值非法" in out.get("error", "")


class TestEngineFullChain:
    def test_flow_run_calls_import_tool(self, tmp_path, registry, monkeypatch):
        """FlowEngine run_now → tool 节点调「导入加工」→ 桥 → handler（HTTP 桩）。"""
        # handler 由 scanner 按文件路径加载（模块名 plugin_ext_<id>，不进
        # sys.modules）——经函数 __globals__ 对 HTTP 面打桩（_upload_file 换桩）
        tool = registry._tools["pipeline-import__import"]
        monkeypatch.setitem(
            tool.handler.__globals__,
            "_upload_file",
            lambda file_path, fields: {
                "pipeline_id": "pipe_test_1",
                "status": "completed",
                "pipeline_type": "ocr",
                "file_id": "vf_1",
                "elapsed_ms": 12,
                "steps": [
                    {"step": "vein", "status": "ok"},
                    {"step": "sense-ocr", "status": "ok"},
                    {"step": "immune", "status": "ok"},
                    {"step": "knowledge", "status": "ok", "entry_id": "kb_1"},
                ],
            },
        )

        engine = FlowEngine(tmp_path / "runs")
        engine.set_tool_caller(
            lambda name, args, approved=False: registry.call_tool_async(
                name, args, approved=approved
            )
        )
        engine.register_flow_file(PLUGIN_DIR / "flows" / "import-kb.yaml", "pipeline-import")

        fake_file = tmp_path / "scan.png"
        fake_file.write_bytes(b"\x89PNG-fake")

        run = asyncio.run(
            engine.run_now(
                "pipeline-import",
                {"type": "manual", "source": "test"},
                inputs={"file_path": str(fake_file), "pipeline": "auto"},
            )
        )
        assert run.status in ("done", "ok", "completed"), run.status
        output = run.outputs["import"]
        assert output["pipeline_id"] == "pipe_test_1"
        assert output["knowledge_entry_id"] == "kb_1"
        assert output["ok"] is True
