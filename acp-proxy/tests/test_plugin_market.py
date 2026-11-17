# -*- coding: utf-8 -*-
"""Sprint C 插件市场 step 1——打包/导入导出（zip + 清单校验 + 权限预览 +
复用 forge 确认链，zip-slip 必拒）。

整合面：plugin/plugin_forge.py 既有「草稿→dry-run→确认→安装」闭环扩展
export_zip / preview_zip / install_zip（暂存复用 .drafts，安装复用 install
确认链——铁律：生成≠安装逐字保留）。通用插件生态能力（非投标专用）。
"""
import stat
import zipfile

import pytest

import plugin.plugin_forge as _module
from plugin.plugin_forge import (
    ForgeError,
    PluginForge,
    _safe_extract_zip,
)


@pytest.fixture()
def forge(tmp_path):
    return PluginForge(tmp_path / "plugins")


def _make_plugin(forge: PluginForge, plugin_id: str = "market-demo") -> str:
    """forge 脚手架→草稿→确认安装（既有链），返回 plugin_id。"""
    forge.draft(spec={"id": plugin_id, "name": "市场演示",
                      "feature": "记录一条待办"})
    forge.install(plugin_id, confirmed=True)
    return plugin_id


def _make_zip(path, entries: dict[str, bytes], symlink: str | None = None):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo("m/link")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, symlink)


# ── ① 打包导出 ───────────────────────────────────────────────────

class TestExport:
    def test_export_roundtrip(self, forge, tmp_path):
        pid = _make_plugin(forge)
        out = forge.export_zip(pid, tmp_path / "out" / f"{pid}.zip")
        assert out["plugin_id"] == pid and out["count"] >= 2
        with zipfile.ZipFile(out["zip"]) as zf:
            names = zf.namelist()
        assert all(n.startswith(f"{pid}/") for n in names)   # 成员名 <id>/<rel>
        assert f"{pid}/plugin.yaml" in names
        assert not any("__pycache__" in n or "dry-run-report" in n for n in names)

    def test_export_missing_plugin(self, forge, tmp_path):
        with pytest.raises(ForgeError, match="插件不存在"):
            forge.export_zip("ghost", tmp_path / "x.zip")

    def test_export_invalid_manifest_rejected(self, forge, tmp_path):
        bad = forge.plugins_dir / "bad-plugin"
        bad.mkdir(parents=True)
        (bad / "plugin.yaml").write_text("id: bad-plugin\n", encoding="utf-8")
        with pytest.raises(ForgeError, match="清单非法"):
            forge.export_zip("bad-plugin", tmp_path / "x.zip")


# ── ② 导入预览（零落盘 + 权限预览）───────────────────────────────

class TestPreview:
    def test_preview_permission_whitepaper(self, forge, tmp_path):
        pid = _make_plugin(forge)
        z = tmp_path / f"{pid}.zip"
        forge.export_zip(pid, z)
        pv = forge.preview_zip(z)
        assert pv["ok"] is True
        assert pv["manifest"]["id"] == pid
        perm = pv["permission_preview"]
        assert "tool.call" in perm["permissions"]            # 权限白描
        assert "hooks" in perm                               # hooks 白描（占位可见）
        assert any(n.endswith("save_item") for n in
                   perm["tools_requiring_approval"])          # 需审批工具白描
        assert "plugin.yaml" in pv["files"]                   # 文件清单（插件根相对）

    def test_preview_zero_persist(self, forge, tmp_path):
        """预览不落任何文件（plugins/.drafts 与 plugins/<id> 都不出现）。"""
        pid = _make_plugin(forge)
        z = tmp_path / f"{pid}.zip"
        forge.export_zip(pid, z)
        f2 = PluginForge(tmp_path / "fresh")
        f2.preview_zip(z)
        assert not (tmp_path / "fresh").exists()              # 连目录都没建

    def test_preview_invalid_manifest_rejected(self, tmp_path):
        z = tmp_path / "bad.zip"
        _make_zip(z, {"bad-plugin/plugin.yaml": b"id: bad-plugin\n"})
        with pytest.raises(ForgeError, match="清单非法"):
            PluginForge(tmp_path / "p").preview_zip(z)


# ── ③ zip-slip 必拒（越界矩阵）──────────────────────────────────

class TestZipSlip:
    @pytest.mark.parametrize("name", [
        "../escape.txt",           # 上跳
        "/abs.txt",                # 绝对路径
        "C:\\win.txt",             # 盘符 + 反斜杠
        "a\\b.txt",                # 反斜杠分隔
        "x/../../y.txt",           # 中段上跳
        "..",                      # 裸上跳段
    ])
    def test_evil_members_rejected(self, tmp_path, name):
        z = tmp_path / "evil.zip"
        _make_zip(z, {name: b"x"})
        with pytest.raises(ForgeError, match="zip-slip"):
            PluginForge(tmp_path / "p").preview_zip(z)
        assert not (tmp_path / "p").exists()                  # 零落盘残留

    def test_symlink_member_rejected(self, tmp_path):
        z = tmp_path / "evil-link.zip"
        _make_zip(z, {"m/plugin.yaml": b"id: m\n"}, symlink="/etc/passwd")
        with pytest.raises(ForgeError, match="符号链接"):
            PluginForge(tmp_path / "p").preview_zip(z)

    def test_member_count_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_module, "ZIP_MAX_MEMBERS", 2)
        z = tmp_path / "bomb.zip"
        _make_zip(z, {f"m/f{i}.txt": b"x" for i in range(5)})
        with pytest.raises(ForgeError, match="成员数"):
            PluginForge(tmp_path / "p").preview_zip(z)

    def test_size_caps(self, tmp_path, monkeypatch):
        monkeypatch.setattr(_module, "ZIP_MAX_FILE_BYTES", 4)
        z = tmp_path / "big.zip"
        _make_zip(z, {"m/f.txt": b"123456"})
        with pytest.raises(ForgeError, match="单文件上限"):
            PluginForge(tmp_path / "p").preview_zip(z)
        monkeypatch.setattr(_module, "ZIP_MAX_FILE_BYTES", 10 ** 9)
        monkeypatch.setattr(_module, "ZIP_MAX_TOTAL_BYTES", 5)
        z2 = tmp_path / "big2.zip"
        _make_zip(z2, {"m/a.txt": b"123", "m/b.txt": b"456"})
        with pytest.raises(ForgeError, match="总量上限"):
            PluginForge(tmp_path / "p").preview_zip(z2)

    def test_empty_and_flat_structure_rejected(self, tmp_path):
        z = tmp_path / "flat.zip"
        _make_zip(z, {"plugin.yaml": b"id: x\n"})             # 平铺（无顶层目录）
        with pytest.raises(ForgeError, match="顶层"):
            PluginForge(tmp_path / "p").preview_zip(z)
        z2 = tmp_path / "multi.zip"
        _make_zip(z2, {"a/x.txt": b"x", "b/y.txt": b"y"})     # 多顶层
        with pytest.raises(ForgeError, match="单一顶层目录"):
            PluginForge(tmp_path / "p").preview_zip(z2)

    def test_not_a_zip_rejected(self, tmp_path):
        z = tmp_path / "not.zip"
        z.write_bytes(b"plain text")
        with pytest.raises(ForgeError, match="无法读取"):
            PluginForge(tmp_path / "p").preview_zip(z)

    def test_safe_extract_lands_only_inside(self, tmp_path):
        z = tmp_path / "ok.zip"
        _make_zip(z, {"m/a.txt": b"x", "m/sub/b.txt": b"y"})
        dest = tmp_path / "dest"
        dest.mkdir()
        names = _safe_extract_zip(z, dest)
        assert sorted(names) == ["m/a.txt", "m/sub/b.txt"]
        assert (dest / "m" / "sub" / "b.txt").read_bytes() == b"y"


# ── ④ 导入安装（复用 forge 确认链——铁律：生成≠安装）─────────────

class TestInstallZip:
    def _zip(self, forge, tmp_path, pid="market-demo"):
        _make_plugin(forge, pid)
        z = tmp_path / f"{pid}.zip"
        forge.export_zip(pid, z)
        return z

    def test_unconfirmed_never_installs(self, forge, tmp_path):
        z = self._zip(forge, tmp_path)
        f2 = PluginForge(tmp_path / "fresh")
        r = f2.install_zip(z, confirmed=False)
        assert r["installed"] is False and r["needs_confirmation"] is True
        assert not (tmp_path / "fresh" / "market-demo").exists()   # 铁律
        assert (tmp_path / "fresh" / ".drafts" / "market-demo").is_dir()  # 暂存同草稿
        assert "铁律" in r["message"]
        assert r["permission_preview"]["permissions"]             # 确认信息含权限预览
        assert r["dry_run"]["ok"] is True                          # 且深度校验已跑

    def test_confirmed_installs_via_forge_chain(self, forge, tmp_path):
        z = self._zip(forge, tmp_path)
        f2 = PluginForge(tmp_path / "fresh")
        grown = {}
        r = f2.install_zip(z, confirmed=True, decided_by="测试",
                           hot_load=lambda pid: grown.update({"pid": pid}) or {"ok": 1})
        assert r["installed"] is True and r["decided_by"] == "测试"
        assert (tmp_path / "fresh" / "market-demo" / "plugin.yaml").is_file()
        assert not (tmp_path / "fresh" / ".drafts" / "market-demo").exists()
        assert grown == {"pid": "market-demo"}                     # 热装载被调

    def test_dry_run_fail_rejected_no_residue(self, forge, tmp_path):
        """坏插件（entry 缺 handler）：dry-run 不过→拒绝导入，零残留。"""
        z = tmp_path / "bad.zip"
        _make_zip(z, {
            "bad-plugin/plugin.yaml": (
                b"id: bad-plugin\nname: x\nversion: 0.1.0\ndescription: x\n"
                b"author: t\nentry: main.py\npermissions: [tool.call]\n"
                b"tools:\n  - name: t1\n    description: d\n    handler: missing\n"
                b"    input_schema: {type: object}\n").decode(),
            "bad-plugin/main.py": "def noop():\n    pass\n",
        })
        f2 = PluginForge(tmp_path / "fresh")
        with pytest.raises(ForgeError, match="dry-run 未通过"):
            f2.install_zip(z, confirmed=True)
        assert not (tmp_path / "fresh" / "bad-plugin").exists()
        assert not (tmp_path / "fresh" / ".drafts" / "bad-plugin").exists()

    def test_existing_plugin_rejected(self, forge, tmp_path):
        z = self._zip(forge, tmp_path)
        with pytest.raises(ForgeError, match="插件已存在"):
            forge.install_zip(z, confirmed=False)   # 同 forge 已装 market-demo

    def test_confirm_then_reconfirm_idempotent_guard(self, forge, tmp_path):
        """确认安装后再导入同 id → 明确拒绝（升级须显式卸载）。"""
        z = self._zip(forge, tmp_path)
        f2 = PluginForge(tmp_path / "fresh")
        f2.install_zip(z, confirmed=True)
        with pytest.raises(ForgeError, match="插件已存在"):
            f2.install_zip(z, confirmed=True)
