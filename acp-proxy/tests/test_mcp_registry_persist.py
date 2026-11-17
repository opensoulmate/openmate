# -*- coding: utf-8 -*-
"""MCP Server注册持久化 + mcp-attachments retention 测试（双遗留销账）。

调研来源：
1. kilocode AGENTS.md「Storage: Filesystem-based JSON...Storage.write」本地JSON快照
   持久化模式（supplement3 #19轮遗留#5：mcp-client registry纯内存态，服务重启后
   已配置Server全部丢失需API重新注册）。
2. kilocode-source-supplement3.md #2「Truncate保留策略：7天retention+每小时
   cleanup扫mtime」扩展到MCP附件目录（#19轮遗留#3：mcp-attachments无retention）。

覆盖：
1. persistence：save/load全量快照roundtrip / 原子替换（无tmp残留）/ 0600权限
   （快照含env密钥）/ 坏JSON·非列表·缺失文件→[]绝不抛出 / registry_path env覆盖
2. registry持久化：add/remove/connect/disconnect写快照（connected标志）/
   默认不持久化（防单测污染真实快照）/ persist_info可观测
3. restore：重注册+connected/auto_connect重连（stub _connect_stdio）/
   重连失败→ERROR+errors记录不阻塞其余 / 坏条目跳过其余照常 / 整体绝不抛出
4. mcp-attachments retention：save_attachment触发mtime清扫（超龄删新留）/
   maybe_sweep节流（窗口内不重复扫）/ 清扫失败不反噬落盘
5. 接线断言（防死接线）：main.py lifespan真调registry.restore / /api/mcp/status
   真带persist快照 / save_attachment源码真调maybe_sweep
"""
import asyncio
import inspect
import json
import os
import stat
import sys
import time
import importlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
MCP_CLIENT_DIR = str(Path(__file__).resolve().parents[2] / "mcp-client")

from agent import mcp_resources, retention

# 顶层同名模块冲突面（acp-proxy 与 mcp-client 各有一份 routes/main）
_TOP_LEVEL_COLLIDE = ("routes", "main", "models", "registry", "persistence")


def _load_mcp_client_modules():
    """装载 mcp-client 侧模块（模块名遮蔽防护）。

    外部同名顶层模块（如 acp-proxy 的 routes——tests/test_reflex_pulse_routes
    顶层 import routes.pulse 会先行占位 sys.modules）会遮蔽 mcp-client 的
    `from routes import servers`。导入期临时让位、导完恢复原位并清掉 mcp-client
    侧缓存（模块对象由返回值持有）——两侧测试互不污染。
    """
    sys.path.insert(0, MCP_CLIENT_DIR)
    saved = {}
    for name in _TOP_LEVEL_COLLIDE:
        mod = sys.modules.get(name)
        if mod is not None and not str(
                getattr(mod, "__file__", "") or "").startswith(MCP_CLIENT_DIR):
            saved[name] = sys.modules.pop(name)
    try:
        persistence_mod = importlib.import_module("persistence")
        registry_mod = importlib.import_module("registry")
        models_mod = importlib.import_module("models")
        main_mod = importlib.import_module("main")
        return persistence_mod, registry_mod, models_mod, main_mod
    finally:
        try:
            sys.path.remove(MCP_CLIENT_DIR)
        except ValueError:
            pass
        for name in _TOP_LEVEL_COLLIDE:
            mod = sys.modules.get(name)
            if mod is not None and str(
                    getattr(mod, "__file__", "") or "").startswith(MCP_CLIENT_DIR):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


# ── persistence 快照层 ───────────────────────────────────────────


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        records = [
            {"config": {"id": "a", "name": "A", "transport": "stdio", "command": "x"}, "connected": True},
            {"config": {"id": "b", "name": "B", "transport": "stdio", "command": "y"}, "connected": False},
        ]
        assert p.save_snapshot(records, snap) is True
        loaded = p.load_snapshot(snap)
        assert [r["config"]["id"] for r in loaded] == ["a", "b"]
        assert loaded[0]["connected"] is True and loaded[1]["connected"] is False

    def test_save_atomic_no_tmp_left(self, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([], snap)
        assert p.save_snapshot([{"config": {"id": "a"}, "connected": False}], snap) is True
        assert not Path(snap + ".tmp").exists()

    def test_snapshot_file_mode_0600(self, tmp_path):
        """快照含ServerConfig.env（可能有密钥）——必须0600（kilocode敏感配置纪律）"""
        p, _, _, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([{"config": {"id": "a", "env": {"API_KEY": "x"}}, "connected": False}], snap)
        mode = stat.S_IMODE(os.stat(snap).st_mode)
        assert mode == 0o600

    def test_load_missing_file_returns_empty(self, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        assert p.load_snapshot(str(tmp_path / "nope.json")) == []

    def test_load_bad_json_returns_empty_never_raises(self, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        snap = tmp_path / "bad.json"
        snap.write_text("{not json!!", encoding="utf-8")
        assert p.load_snapshot(str(snap)) == []

    def test_load_non_dict_root_returns_empty(self, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        snap = tmp_path / "arr.json"
        snap.write_text("[1,2,3]", encoding="utf-8")
        assert p.load_snapshot(str(snap)) == []

    def test_load_non_list_servers_returns_empty(self, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        snap = tmp_path / "obj.json"
        snap.write_text(json.dumps({"version": 1, "servers": {"x": 1}}), encoding="utf-8")
        assert p.load_snapshot(str(snap)) == []

    def test_registry_path_env_override(self, monkeypatch, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        monkeypatch.setenv("MCP_REGISTRY_PATH", str(tmp_path / "env.json"))
        assert p.registry_path() == str(tmp_path / "env.json")

    def test_save_snapshot_writes_version(self, tmp_path):
        p, _, _, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([], snap)
        data = json.loads(Path(snap).read_text(encoding="utf-8"))
        assert data["version"] == p.SNAPSHOT_VERSION and data["servers"] == []


# ── registry 持久化触发点 ────────────────────────────────────────


def _make_registry(registry_mod, models_mod, persist_path=None, sid="s1"):
    reg = registry_mod.MCPRegistry(persist_path=persist_path)
    cfg = models_mod.ServerConfig(id=sid, name="Srv", transport=models_mod.TransportType.STDIO, command="x")
    return reg, cfg


class TestRegistryPersist:
    def test_default_registry_not_persistent(self, tmp_path):
        """单测默认不持久化——防污染真实快照（构造契约锁死）"""
        _, registry_mod, models_mod, _ = _load_mcp_client_modules()
        reg, cfg = _make_registry(registry_mod, models_mod, persist_path=None)
        reg.add_server(cfg)
        assert reg.persist_info()["enabled"] is False
        assert not (tmp_path / "servers.json").exists()

    def test_add_server_writes_snapshot(self, tmp_path):
        _, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        reg, cfg = _make_registry(registry_mod, models_mod, persist_path=snap)
        reg.add_server(cfg)
        data = json.loads(Path(snap).read_text(encoding="utf-8"))
        assert data["servers"][0]["config"]["id"] == "s1"
        assert data["servers"][0]["connected"] is False

    def test_connected_flag_in_snapshot(self, tmp_path):
        _, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        reg, cfg = _make_registry(registry_mod, models_mod, persist_path=snap)
        reg.add_server(cfg)
        reg._connections["s1"] = object()  # 模拟已连接
        reg._persist()
        data = json.loads(Path(snap).read_text(encoding="utf-8"))
        assert data["servers"][0]["connected"] is True

    def test_remove_server_updates_snapshot(self, tmp_path):
        _, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        reg, cfg = _make_registry(registry_mod, models_mod, persist_path=snap)
        reg.add_server(cfg)
        reg.add_server(models_mod.ServerConfig(id="s2", name="S2", command="y"))
        reg.remove_server("s2")
        data = json.loads(Path(snap).read_text(encoding="utf-8"))
        assert [r["config"]["id"] for r in data["servers"]] == ["s1"]

    def test_persist_failure_never_raises(self, tmp_path):
        """持久化失败绝不反噬注册主流程（WARNING契约）"""
        _, registry_mod, models_mod, _ = _load_mcp_client_modules()
        bad_path = str(tmp_path)  # 目录当文件用→save必失败
        reg, cfg = _make_registry(registry_mod, models_mod, persist_path=bad_path)
        state = reg.add_server(cfg)  # 不抛出
        assert state.config.id == "s1"

    def test_persist_info_shape(self, tmp_path):
        _, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        reg, _ = _make_registry(registry_mod, models_mod, persist_path=snap)
        info = reg.persist_info()
        assert info["enabled"] is True and info["path"] == snap
        assert info["restored"] == 0 and info["reconnected"] == 0 and info["restore_errors"] == []


# ── restore 启动恢复 ─────────────────────────────────────────────


class TestRestore:
    def test_restore_registers_all(self, tmp_path):
        p, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([
            {"config": {"id": "a", "name": "A", "command": "x"}, "connected": False},
            {"config": {"id": "b", "name": "B", "command": "y"}, "connected": False},
        ], snap)
        reg = registry_mod.MCPRegistry(persist_path=snap)
        result = asyncio.run(reg.restore())
        assert result["restored"] == 2 and result["reconnected"] == 0
        assert sorted(s.config.id for s in reg.list_servers()) == ["a", "b"]

    def test_restore_reconnects_connected(self, tmp_path, monkeypatch):
        p, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([
            {"config": {"id": "a", "name": "A", "command": "x"}, "connected": True},
            {"config": {"id": "b", "name": "B", "command": "y"}, "connected": False},
        ], snap)

        async def fake_connect_stdio(self, server_id, state):
            self._connections[server_id] = object()
            state.status = models_mod.ServerStatus.CONNECTED
            state.tools = []
            state.error = None
            return state

        monkeypatch.setattr(registry_mod.MCPRegistry, "_connect_stdio", fake_connect_stdio)
        reg = registry_mod.MCPRegistry(persist_path=snap)
        result = asyncio.run(reg.restore())
        assert result["reconnected"] == 1 and result["errors"] == []
        assert reg.get_server("a").status == models_mod.ServerStatus.CONNECTED
        assert reg.get_server("b").status == models_mod.ServerStatus.DISCONNECTED

    def test_restore_reconnects_auto_connect(self, tmp_path, monkeypatch):
        p, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([
            {"config": {"id": "a", "name": "A", "command": "x", "auto_connect": True}, "connected": False},
        ], snap)

        async def fake_connect_stdio(self, server_id, state):
            self._connections[server_id] = object()
            state.status = models_mod.ServerStatus.CONNECTED
            return state

        monkeypatch.setattr(registry_mod.MCPRegistry, "_connect_stdio", fake_connect_stdio)
        reg = registry_mod.MCPRegistry(persist_path=snap)
        result = asyncio.run(reg.restore())
        assert result["reconnected"] == 1

    def test_restore_reconnect_failure_recorded_not_blocking(self, tmp_path):
        """重连失败→ERROR+errors记录，其余恢复照常，整体绝不抛出"""
        p, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([
            {"config": {"id": "dead", "name": "Dead", "command": "definitely-not-a-real-cmd-xyz"}, "connected": True},
            {"config": {"id": "ok", "name": "Ok", "command": "x"}, "connected": False},
        ], snap)
        reg = registry_mod.MCPRegistry(persist_path=snap)
        result = asyncio.run(reg.restore())
        assert result["restored"] == 2 and result["reconnected"] == 0
        assert len(result["errors"]) == 1 and result["errors"][0]["server_id"] == "dead"
        assert reg.get_server("dead").status == models_mod.ServerStatus.ERROR
        assert reg.get_server("ok") is not None  # 其余照常恢复

    def test_restore_skips_bad_records(self, tmp_path):
        p, registry_mod, models_mod, _ = _load_mcp_client_modules()
        snap = str(tmp_path / "servers.json")
        p.save_snapshot([
            "not-a-dict",
            {"config": {"id": ["bad"], "name": "Bad"}, "connected": False},  # id类型非法→跳过
            {"config": {"id": "good", "name": "Good", "transport": "not-a-transport"}, "connected": False},
            {"config": {"id": "fine", "name": "Fine", "command": "x"}, "connected": False},
        ], snap)
        reg = registry_mod.MCPRegistry(persist_path=snap)
        result = asyncio.run(reg.restore())
        # 坏条目跳过、其余照常（"fine"必恢复；部分失败可见进errors）
        assert reg.get_server("fine") is not None
        assert result["restored"] >= 1
        assert len(result["errors"]) >= 1

    def test_restore_empty_snapshot_zero(self, tmp_path):
        _, registry_mod, _, _ = _load_mcp_client_modules()
        reg = registry_mod.MCPRegistry(persist_path=str(tmp_path / "missing.json"))
        result = asyncio.run(reg.restore())
        assert result == {"restored": 0, "reconnected": 0, "errors": []}

    def test_restore_disabled_registry(self, tmp_path):
        _, registry_mod, _, _ = _load_mcp_client_modules()
        reg = registry_mod.MCPRegistry(persist_path=None)
        result = asyncio.run(reg.restore())
        assert result["restored"] == 0 and result["errors"] == []

    def test_restore_never_raises_on_garbage_snapshot(self, tmp_path):
        p, registry_mod, _, _ = _load_mcp_client_modules()
        snap = tmp_path / "g.json"
        snap.write_text("{{{garbage", encoding="utf-8")
        reg = registry_mod.MCPRegistry(persist_path=str(snap))
        result = asyncio.run(reg.restore())  # 绝不抛出
        assert result["restored"] == 0


# ── mcp-attachments retention ────────────────────────────────────


def _blob_att(mime="application/pdf"):
    import base64
    return {
        "uri": "e2e://doc.pdf",
        "mime": mime,
        "blob": base64.b64encode(b"%PDF-1.4 probe").decode(),
        "size_bytes": 14,
    }


class TestAttachmentsRetention:
    def test_save_attachment_sweeps_expired(self, tmp_path):
        """保存触发mtime清扫：超龄文件删、新文件留（kilocode #2：只看mtime）"""
        save_dir = tmp_path / "att"
        save_dir.mkdir()
        old = save_dir / "old_expired.pdf"
        old.write_bytes(b"old")
        old_t = time.time() - 8 * 86400  # 8天前>7天窗口
        os.utime(old, (old_t, old_t))
        fresh = save_dir / "fresh.pdf"
        fresh.write_bytes(b"fresh")
        with retention._sweep_lock:
            retention._last_sweep.pop("mcp-attachments", None)
        path = mcp_resources.save_attachment(_blob_att(), str(save_dir))
        assert path and Path(path).exists()
        assert not old.exists(), "超龄附件必须被清扫"
        assert fresh.exists(), "窗口内附件必须保留"
        assert Path(path).exists(), "本次新附件必须保留"

    def test_sweep_throttled_hourly(self, tmp_path):
        """maybe_sweep每小时最多一次：节流窗口内不重复扫（kilocode\"每小时cleanup\"）"""
        save_dir = tmp_path / "att2"
        save_dir.mkdir()
        with retention._sweep_lock:
            retention._last_sweep.pop("mcp-attachments", None)
        mcp_resources.save_attachment(_blob_att(), str(save_dir))
        # 第一次保存已触发清扫；再造一个超龄文件后立即再保存→节流不扫
        old2 = save_dir / "old2.pdf"
        old2.write_bytes(b"old2")
        old2_t = time.time() - 8 * 86400
        os.utime(old2, (old2_t, old2_t))
        mcp_resources.save_attachment(_blob_att(), str(save_dir))
        assert old2.exists(), "节流窗口内不得重复清扫"

    def test_sweep_failure_never_breaks_save(self, monkeypatch, tmp_path):
        """清扫失败绝不反噬附件落盘（观测/清理层不阻塞执行层）"""
        save_dir = tmp_path / "att3"

        def boom(*a, **k):
            raise RuntimeError("sweep down")

        monkeypatch.setattr(retention, "sweep_mtime", boom)
        with retention._sweep_lock:
            retention._last_sweep.pop("mcp-attachments", None)
        path = mcp_resources.save_attachment(_blob_att(), str(save_dir))
        assert path and Path(path).exists()

    def test_sweep_pattern_covers_all_files(self, tmp_path):
        """附件名带任意扩展名（_safe_basename）——patterns必须是*而非*.txt"""
        save_dir = tmp_path / "att4"
        save_dir.mkdir()
        weird = save_dir / "old_res"
        weird.write_bytes(b"x")  # 无扩展名
        weird_t = time.time() - 8 * 86400
        os.utime(weird, (weird_t, weird_t))
        r = retention.sweep_mtime(str(save_dir), patterns=("*",))
        assert r["removed_files"] == 1 and not weird.exists()


# ── 接线断言（防死接线）─────────────────────────────────────────


class TestWiring:
    def test_main_lifespan_calls_restore(self):
        _, _, _, main_mod = _load_mcp_client_modules()
        src = inspect.getsource(main_mod)
        assert "registry.restore()" in src, "main启动必须调registry.restore"
        assert "persist_info()" in src, "/api/mcp/status必须带persist快照"

    def test_main_registry_persistence_enabled(self):
        _, _, _, main_mod = _load_mcp_client_modules()
        assert main_mod.registry.persist_info()["enabled"] is True

    def test_main_status_endpoint_includes_persist(self):
        _, _, _, main_mod = _load_mcp_client_modules()
        resp = asyncio.run(main_mod.status())
        assert "persist" in resp and "servers" in resp

    def test_save_attachment_calls_maybe_sweep(self):
        src = inspect.getsource(mcp_resources.save_attachment)
        assert "maybe_sweep" in src and "sweep_mtime" in src

    def test_retention_docstring_covers_attachments(self):
        assert "mcp-attachments" in (retention.__doc__ or "")

    def test_registry_persist_hooks_in_mutating_paths(self):
        _, registry_mod, _, _ = _load_mcp_client_modules()
        for method in ("add_server", "remove_server", "connect", "_disconnect_internal"):
            body = inspect.getsource(getattr(registry_mod.MCPRegistry, method))
            assert "_persist()" in body, f"{method}必须触发持久化"
