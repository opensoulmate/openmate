# -*- coding: utf-8 -*-
"""kilocode supplement3 #19 MCP resource工具三件套 测试。

调研来源：kilocode-source-supplement3.md #19「MCP resource工具三件套（tools.ts）：
list/read resources + 10MB blob上限 + 附件MIME白名单（pdf/gif/jpeg/png/webp才可
作为附件注入）」。移植源：~/agent-research-src/kilocode/packages/opencode/src/
session/tools.ts（MCP_RESOURCE_TOOLS / MAX_MCP_RESOURCE_BLOB_BYTES /
SUPPORTED_MCP_RESOURCE_ATTACHMENT_MIMES / formatMcpResourceContent / base64Size /
formatBytes）。

覆盖：
1. base64_size / format_bytes：Math.ceil语义逐行对齐
2. 常量锁死：10MB上限 + 5个附件MIME白名单（防后人"简化"）
3. format_resource_content：text直进 / blob双闸（先MIME后大小，顺序锁死）/
   通过者attached标记 / 被拦者显式省略标记（AIHawk SHOWN/SENT铁律：禁静默丢弃）/
   无text无blob / 空contents / 单dict容错
4. save_attachment / build_read_text：附件落盘 + 显式saved/failed标注 + fail-safe
5. registry（mcp-client）：resources能力门 / 聚合+排序+server归属 / 单Server失败
   显式进errors不拖垮聚合 / read_resource拒绝无能力Server
6. 接线断言（防死接线）：soulmate工具面门+主循环分派+code_mode分派+_fetch刷新
   resource清单；mcp-client FastAPI app真的挂载了4个resource路由
"""
import asyncio
import base64
import importlib
import inspect
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
MCP_CLIENT_DIR = str(Path(__file__).resolve().parents[2] / "mcp-client")

from agent import mcp_resources
from agent.mcp_resources import (
    MAX_MCP_RESOURCE_BLOB_BYTES,
    MCP_RESOURCE_TOOL_NAMES,
    MCP_RESOURCE_TOOLS,
    SUPPORTED_MCP_RESOURCE_ATTACHMENT_MIMES,
    base64_size,
    build_read_text,
    format_bytes,
    format_resource_content,
    format_resource_entry,
    format_resource_template_entry,
    resource_tool_defs,
    save_attachment,
)

WHITELISTED_B64 = base64.b64encode(b"%PDF-1.4 fake pdf bytes").decode()
PLAIN_B64 = base64.b64encode(b"MZ\x90 executable-ish").decode()


class TestBase64AndBytes:
    def test_base64_size_no_padding(self):
        assert base64_size(base64.b64encode(b"abc").decode()) == 3

    def test_base64_size_padding(self):
        assert base64_size(base64.b64encode(b"a").decode()) == 1
        assert base64_size(base64.b64encode(b"ab").decode()) == 2

    def test_base64_size_strips_whitespace(self):
        raw = base64.b64encode(b"hello world").decode()
        assert base64_size(raw[:4] + "\n " + raw[4:]) == 11

    def test_base64_size_empty_and_garbage_floor_zero(self):
        assert base64_size("") == 0
        assert base64_size("A") >= 0  # 折算公式向下取整不为负

    def test_format_bytes_boundaries(self):
        assert format_bytes(0) == "0 B"
        assert format_bytes(512) == "512 B"
        assert format_bytes(1024) == "1 KB"
        assert format_bytes(1025) == "2 KB"  # Math.ceil语义
        assert format_bytes(1024 * 1024) == "1 MB"
        assert format_bytes(1024 * 1024 + 1) == "2 MB"

    def test_constants_locked(self):
        # kilocode session/tools.ts逐行常量（防"简化"破坏安全语义）
        assert MAX_MCP_RESOURCE_BLOB_BYTES == 10 * 1024 * 1024
        assert set(SUPPORTED_MCP_RESOURCE_ATTACHMENT_MIMES) == {
            "application/pdf", "image/gif", "image/jpeg", "image/png", "image/webp",
        }


class TestFormatResourceContent:
    def test_text_item_passthrough(self):
        out = format_resource_content("srv", "u", {
            "contents": [{"uri": "res://doc", "mimeType": "text/markdown", "text": "# hi"}],
        })
        assert out["contents"] == 1
        assert out["text"] == "Resource: res://doc\nMIME: text/markdown\n# hi"
        assert out["attachments"] == []

    def test_blob_whitelisted_attaches(self):
        out = format_resource_content("srv", "res://a.pdf", {
            "contents": [{"uri": "res://a.pdf", "mimeType": "application/pdf", "blob": WHITELISTED_B64}],
        })
        assert "[Binary MCP resource attached: res://a.pdf (application/pdf)]" in out["text"]
        assert len(out["attachments"]) == 1
        att = out["attachments"][0]
        assert att["mime"] == "application/pdf"
        assert att["blob"] == WHITELISTED_B64
        assert att["size_bytes"] == len(b"%PDF-1.4 fake pdf bytes")

    def test_blob_non_whitelisted_explicitly_omitted(self):
        out = format_resource_content("srv", "res://x.exe", {
            "contents": [{"uri": "res://x.exe", "mimeType": "application/octet-stream", "blob": PLAIN_B64}],
        })
        size = len(b"MZ\x90 executable-ish")
        assert out["text"] == (
            f"[Binary MCP resource omitted: res://x.exe (application/octet-stream, "
            f"{format_bytes(size)}) is not a supported attachment type]"
        )
        assert out["attachments"] == []  # 禁静默丢弃=有显式标记；禁注入=无附件

    def test_blob_over_cap_explicitly_omitted(self, monkeypatch):
        monkeypatch.setattr(mcp_resources, "MAX_MCP_RESOURCE_BLOB_BYTES", 8)
        blob = base64.b64encode(b"x" * 64).decode()
        out = format_resource_content("srv", "res://big.pdf", {
            "contents": [{"uri": "res://big.pdf", "mimeType": "application/pdf", "blob": blob}],
        })
        assert "exceeds 8 B]" in out["text"]
        assert "omitted" in out["text"]
        assert out["attachments"] == []

    def test_dual_gate_order_whitelisted_but_oversize_says_exceeds(self, monkeypatch):
        # 双闸顺序锁死：先MIME后大小——白名单内超限的文案必须是exceeds而非not supported
        monkeypatch.setattr(mcp_resources, "MAX_MCP_RESOURCE_BLOB_BYTES", 4)
        blob = base64.b64encode(b"y" * 32).decode()
        out = format_resource_content("srv", "res://big.png", {
            "contents": [{"uri": "res://big.png", "mimeType": "image/png", "blob": blob}],
        })
        assert "exceeds" in out["text"]
        assert "not a supported attachment type" not in out["text"]

    def test_no_text_no_blob_marker(self):
        out = format_resource_content("srv", "res://empty", {"contents": [{"uri": "res://empty"}]})
        assert out["text"] == "[MCP resource content without text or blob: res://empty]"

    def test_empty_contents_fallback(self):
        out = format_resource_content("srv1", "res://u1", {"contents": []})
        assert out["text"] == "MCP resource res://u1 from srv1 returned no contents."
        out2 = format_resource_content("srv1", "res://u1", {})
        assert out2["text"] == "MCP resource res://u1 from srv1 returned no contents."

    def test_single_dict_contents_tolerated(self):
        out = format_resource_content("srv", "u", {"contents": {"text": "solo", "uri": "res://s"}})
        assert out["contents"] == 1
        assert "solo" in out["text"]

    def test_items_joined_blank_line(self):
        out = format_resource_content("srv", "u", {
            "contents": [{"text": "a", "uri": "r1"}, {"text": "b", "uri": "r2"}],
        })
        assert out["text"] == (
            "Resource: r1\nMIME: application/octet-stream\na\n\n"
            "Resource: r2\nMIME: application/octet-stream\nb"
        )

    def test_item_uri_fallback_to_request_uri(self):
        out = format_resource_content("srv", "res://req", {"contents": [{"text": "t"}]})
        assert "Resource: res://req" in out["text"]

    def test_mixed_text_blob_markers_order(self):
        out = format_resource_content("srv", "u", {
            "contents": [
                {"uri": "r1", "text": "hello"},
                {"uri": "r2", "mimeType": "image/png", "blob": WHITELISTED_B64},
            ],
        })
        assert out["text"].startswith("Resource: r1")
        assert "[Binary MCP resource attached: r2 (image/png)]" in out["text"]
        assert len(out["attachments"]) == 1


class TestEntryFormatting:
    def test_format_resource_entry_rewrites_client_key(self):
        out = format_resource_entry({
            "uri": "res://a", "name": "A", "client": "srv1",
            "server_id": "srv1", "server_name": "Srv One", "_sort": ("x",),
        })
        assert "client" not in out
        assert "_sort" not in out
        assert out["uri"] == "res://a"
        assert out["server_id"] == "srv1"
        assert out["server_name"] == "Srv One"

    def test_format_resource_template_entry(self):
        out = format_resource_template_entry({"uriTemplate": "res://{x}", "client": "c"})
        assert out == {"uriTemplate": "res://{x}"}


class TestAttachmentSave:
    def test_save_attachment_writes_decoded_bytes(self, tmp_path):
        path = save_attachment(
            {"blob": WHITELISTED_B64, "mime": "application/pdf", "uri": "res://docs/a.pdf", "size_bytes": 1},
            str(tmp_path),
        )
        assert path is not None
        assert Path(path).read_bytes() == b"%PDF-1.4 fake pdf bytes"
        assert Path(path).name.endswith("a.pdf")

    def test_save_attachment_sanitizes_path_traversal(self, tmp_path):
        path = save_attachment(
            {"blob": WHITELISTED_B64, "mime": "image/png", "uri": "../../etc/passwd?x=1"},
            str(tmp_path),
        )
        assert path is not None
        assert os.path.dirname(path) == str(tmp_path)  # 绝不写出tmp_path之外
        assert ".." not in os.path.basename(path)

    def test_save_attachment_failure_returns_none(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        assert save_attachment({"blob": WHITELISTED_B64, "uri": "u"}, str(blocker)) is None


class TestBuildReadText:
    def test_text_only_passthrough(self, tmp_path):
        text = build_read_text("srv", "u", {"contents": [{"uri": "u", "text": "plain"}]}, str(tmp_path))
        assert text == "Resource: u\nMIME: application/octet-stream\nplain"

    def test_attached_blob_saved_with_explicit_path(self, tmp_path):
        text = build_read_text("srv", "res://a.pdf", {
            "contents": [{"uri": "res://a.pdf", "mimeType": "application/pdf", "blob": WHITELISTED_B64}],
        }, str(tmp_path))
        assert "[Binary MCP resource attached: res://a.pdf (application/pdf)]" in text
        assert "[MCP resource attachment saved: " in text
        saved = [ln for ln in text.splitlines() if ln.startswith("[MCP resource attachment saved:")]
        assert len(saved) == 1
        saved_path = saved[0][len("[MCP resource attachment saved: "):].rstrip("]")
        assert os.path.isfile(saved_path)

    def test_save_failure_explicit_marker(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("not a dir")
        text = build_read_text("srv", "res://a.pdf", {
            "contents": [{"uri": "res://a.pdf", "mimeType": "application/pdf", "blob": WHITELISTED_B64}],
        }, str(blocker))
        size = format_bytes(len(b"%PDF-1.4 fake pdf bytes"))
        assert f"[MCP resource attachment save failed: res://a.pdf (application/pdf, {size})]" in text

    def test_format_failure_fail_safe(self, tmp_path):
        # 非dict载荷（类型失真）→显式失败文本，绝不抛出（采集/注入端fail-safe铁律）
        text = build_read_text("srv", "u", "not-a-dict", str(tmp_path))
        assert text.startswith("[MCP resource格式化失败] srv/u")


class TestToolDefs:
    def test_three_tools_with_locked_names(self):
        defs = resource_tool_defs()
        names = [d["function"]["name"] for d in defs]
        assert names == list(MCP_RESOURCE_TOOL_NAMES)
        assert names == [
            MCP_RESOURCE_TOOLS["list"],
            MCP_RESOURCE_TOOLS["listTemplates"],
            MCP_RESOURCE_TOOLS["read"],
        ]

    def test_read_requires_server_and_uri(self):
        read_def = resource_tool_defs()[2]["function"]
        assert read_def["parameters"]["required"] == ["server", "uri"]
        assert set(read_def["parameters"]["properties"]) == {"server", "uri"}

    def test_all_defs_openai_function_shape(self):
        for d in resource_tool_defs():
            assert d["type"] == "function"
            assert d["function"]["description"]
            assert d["function"]["parameters"]["type"] == "object"


# ── mcp-client registry / 路由接线 ────────────────────────────────


def _load_mcp_client_modules():
    """装载 mcp-client 侧模块（模块名遮蔽防护，同
    tests/test_mcp_registry_persist._load_mcp_client_modules——acp-proxy 的
    routes/main 同名占位会遮蔽 mcp-client 侧导入，导入期让位、导完恢复）。"""
    sys.path.insert(0, MCP_CLIENT_DIR)
    saved = {}
    for name in ("routes", "main", "models", "registry"):
        mod = sys.modules.get(name)
        if mod is not None and not str(
                getattr(mod, "__file__", "") or "").startswith(MCP_CLIENT_DIR):
            saved[name] = sys.modules.pop(name)
    try:
        registry_mod = importlib.import_module("registry")
        models_mod = importlib.import_module("models")
        main_mod = importlib.import_module("main")
        return registry_mod, models_mod, main_mod
    finally:
        try:
            sys.path.remove(MCP_CLIENT_DIR)
        except ValueError:
            pass
        for name in ("routes", "main", "models", "registry"):
            mod = sys.modules.get(name)
            if mod is not None and str(
                    getattr(mod, "__file__", "") or "").startswith(MCP_CLIENT_DIR):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


class FakeConn:
    """最小JSON-RPC假连接：按method返回canned结果或抛错（registry只依赖send_request/close）"""

    def __init__(self, responses=None, errors=None):
        self.responses = responses or {}
        self.errors = errors or {}
        self.calls = []
        self.closed = False

    async def send_request(self, method, params=None):
        self.calls.append((method, params))
        if method in self.errors:
            raise self.errors[method]
        return self.responses.get(method, {})

    async def close(self):
        self.closed = True


def _make_registry_with(models_mod, registry_mod, server_id="s1", capabilities=None, conn=None):
    reg = registry_mod.MCPRegistry()
    cfg = models_mod.ServerConfig(id=server_id, name="Srv One", transport=models_mod.TransportType.STDIO, command="x")
    state = models_mod.ServerState(
        config=cfg,
        status=models_mod.ServerStatus.CONNECTED,
        capabilities=capabilities if capabilities is not None else {"resources": {}},
    )
    reg._servers[server_id] = state
    if conn is not None:
        reg._connections[server_id] = conn
    return reg, state


class TestRegistryResources:
    def test_capability_gate(self):
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        conn = FakeConn()
        reg, _ = _make_registry_with(models_mod, registry_mod, capabilities={}, conn=conn)
        assert reg.resource_server_ids() == []  # 无resources能力→不进resource面
        assert reg._resource_connections() == {}
        # "resources": {}（空dict声明）也必须判定为有能力——JS ?.语义空对象truthy
        reg2, _ = _make_registry_with(models_mod, registry_mod, server_id="s2", capabilities={"resources": {}}, conn=conn)
        assert reg2.resource_server_ids() == ["s2"]
        reg3, _ = _make_registry_with(models_mod, registry_mod, server_id="s3", capabilities={"resources": {"listChanged": True}}, conn=conn)
        assert reg3.resource_server_ids() == ["s3"]

    def test_not_connected_excluded(self):
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        reg, state = _make_registry_with(models_mod, registry_mod, conn=FakeConn())
        state.status = models_mod.ServerStatus.DISCONNECTED
        assert reg.resource_server_ids() == []

    def test_list_resources_annotates_and_sorts(self):
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        conn = FakeConn(responses={"resources/list": {"resources": [
            {"uri": "res://b", "name": "B"},
            {"uri": "res://a", "name": "A"},
        ]}})
        reg, _ = _make_registry_with(models_mod, registry_mod, capabilities={"resources": {}}, conn=conn)
        result = asyncio.run(reg.list_resources())
        assert [r["name"] for r in result["resources"]] == ["A", "B"]  # (server_name,name,uri)排序
        assert all(r["server_id"] == "s1" and r["server_name"] == "Srv One" for r in result["resources"])
        assert result["errors"] == []
        assert ("resources/list", {}) in conn.calls

    def test_list_resources_per_server_error_visible(self):
        from connection import MCPError
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        bad = FakeConn(errors={"resources/list": MCPError("boom")})
        good = FakeConn(responses={"resources/list": {"resources": [{"uri": "res://ok", "name": "OK"}]}})
        reg, _ = _make_registry_with(models_mod, registry_mod, server_id="bad", conn=bad)
        reg2 = reg
        cfg = models_mod.ServerConfig(id="good", name="Good", transport=models_mod.TransportType.STDIO, command="x")
        reg2._servers["good"] = models_mod.ServerState(
            config=cfg, status=models_mod.ServerStatus.CONNECTED, capabilities={"resources": {}})
        reg2._connections["good"] = good
        result = asyncio.run(reg.list_resources())
        # 单Server失败不拖垮聚合：错误显式进errors（mem0 §1.1失败可见）
        assert [r["server_id"] for r in result["resources"]] == ["good"]
        assert result["errors"] == [{"server_id": "bad", "error": "boom"}]

    def test_list_resource_templates(self):
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        conn = FakeConn(responses={"resources/templates/list": {"resourceTemplates": [
            {"uriTemplate": "res://{x}", "name": "T"},
        ]}})
        reg, _ = _make_registry_with(models_mod, registry_mod, conn=conn)
        result = asyncio.run(reg.list_resource_templates())
        assert result["resourceTemplates"][0]["uriTemplate"] == "res://{x}"
        assert result["resourceTemplates"][0]["server_id"] == "s1"
        assert ("resources/templates/list", {}) in conn.calls

    def test_read_resource_passes_uri(self):
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        conn = FakeConn(responses={"resources/read": {"contents": [{"uri": "res://a", "text": "T"}]}})
        reg, _ = _make_registry_with(models_mod, registry_mod, conn=conn)
        out = asyncio.run(reg.read_resource("s1", "res://a"))
        assert out["contents"][0]["text"] == "T"
        assert ("resources/read", {"uri": "res://a"}) in conn.calls

    def test_read_resource_rejects_disconnected_and_no_capability(self):
        from connection import MCPError
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        reg, _ = _make_registry_with(models_mod, registry_mod, conn=FakeConn())
        try:
            asyncio.run(reg.read_resource("nope", "res://a"))
            raise AssertionError("应当拒绝未连接Server")
        except MCPError as e:
            assert "未连接" in str(e)
        reg2, state = _make_registry_with(models_mod, registry_mod, server_id="s2", capabilities={}, conn=FakeConn())
        try:
            asyncio.run(reg2.read_resource("s2", "res://a"))
            raise AssertionError("应当拒绝无resources能力Server")
        except MCPError as e:
            assert "does not support resources" in str(e)

    def test_disconnect_clears_capabilities(self):
        registry_mod, models_mod, _ = _load_mcp_client_modules()
        conn = FakeConn()
        reg, state = _make_registry_with(models_mod, registry_mod, conn=conn)

        async def _disc():
            await reg._disconnect_internal("s1")
        asyncio.run(_disc())
        assert state.capabilities == {}


class TestRouteAndAgentWiring:
    def test_mcp_client_app_mounts_resource_routes(self):
        _, _, main_mod = _load_mcp_client_modules()
        # 本环境FastAPI把子router作为嵌套对象挂载（app.routes不扁平化）——
        # 用OpenAPI schema断言真实可达路由（curl同一来源）
        paths = set(main_mod.app.openapi()["paths"].keys())
        for p in ("/api/mcp/resources/servers", "/api/mcp/resources/all",
                  "/api/mcp/resources/templates", "/api/mcp/resources/read"):
            assert p in paths, f"mcp-client FastAPI app未挂载{p}"

    def test_soulmate_dispatch_and_tool_face_wiring(self):
        import agent.soulmate_agent as sma
        run_src = inspect.getsource(sma.SoulMateAgent._run_llm_with_tools)
        # Pi #1 拆钩子面包裹层后执行体=_code_mode_tool_call_inner（接线不变）
        code_src = inspect.getsource(sma.SoulMateAgent._code_mode_tool_call_inner)
        call_src = inspect.getsource(sma.SoulMateAgent._call_mcp_resource_tool)
        fetch_src = inspect.getsource(sma.SoulMateAgent._fetch_mcp_tools)
        # 工具面门（hasMcpResourceServer）：无resource能力Server时不暴露
        assert "resource_tool_defs() if getattr(self, \"_mcp_resource_servers\", None) else []" in run_src
        # 主工具循环分派真实接线（非死代码）
        assert "elif func_name in MCP_RESOURCE_TOOL_NAMES:" in run_src
        assert "await self._call_mcp_resource_tool(func_name, func_args)" in run_src
        # code_mode批内分派
        assert "func_name in MCP_RESOURCE_TOOL_NAMES" in code_src
        # resource清单随fetch刷新（工具面门的数据源）
        assert "/api/mcp/resources/servers" in fetch_src
        # 执行体走注入安全层+附件落盘
        assert "build_read_text(server, uri, resp.json(), self._mcp_attachment_dir())" in call_src
        assert "format_resource_entry" in call_src and "format_resource_template_entry" in call_src

    def test_call_mcp_resource_tool_arg_validation_no_http(self):
        import agent.soulmate_agent as sma
        inst = sma.SoulMateAgent.__new__(sma.SoulMateAgent)
        inst._mcp_base_url = "http://127.0.0.1:1"  # 不该被触达
        out = asyncio.run(inst._call_mcp_resource_tool("read_mcp_resource", {"server": "s"}))
        assert out.startswith("错误: read_mcp_resource 需要 server 和 uri 参数")
