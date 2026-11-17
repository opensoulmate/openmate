# -*- coding: utf-8 -*-
"""Sprint C 市场 step 3——本地远端仓库（HTTP 远端源拉取安装）+ 社区门户留接口。

最小可行（用户拍板）：远端协议=仓库自身目录布局（catalog.json + <id>/<version>.zip，
任意静态 HTTP 可托管，零专有服务）；社区门户（评分/签名信任链/沙箱审计）只留
CommunityPortal 接口 + 未配置空实现，选型不擅定。
"""
from __future__ import annotations

import json
import threading
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from plugin.community_portal import (
    COMMUNITY_CAPABILITIES,
    CommunityPortal,
    NullCommunityPortal,
)
from plugin.plugin_forge import ForgeError, PluginForge
from plugin.plugin_repo import PluginRepo

MANIFEST = """\
id: remote-demo
name: 远端演示
version: 1.0.0
description: 市场 step 3 验收插件
author: soulmate
entry: main.py
permissions: [tool.register, tool.call]
ui:
  pages:
    - id: home
      title: 工作台
      dsl: ui/home.json
tools:
  - name: ping
    description: ping一下
    handler: handle_ping
    approval: never
    input_schema: {type: object}
"""

ENTRY = """\
def handle_ping(args, ctx):
    return {"pong": True}
"""

UI = '{"version": 1, "page": {"title": "工作台"}, "panels": [{"type": "report", "id": "r", "title": "结果"}]}'


def run(coro):
    import asyncio
    return asyncio.run(coro)


def _pack(src_dir, zip_path):
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                zf.write(p, f"{src_dir.name}/{p.relative_to(src_dir)}")
    return zip_path


def _make_src(tmp_path, pid="remote-demo", version="1.0.0"):
    src = tmp_path / "src" / pid
    (src / "ui").mkdir(parents=True)
    (src / "plugin.yaml").write_text(
        MANIFEST.replace("id: remote-demo", f"id: {pid}")
                .replace("version: 1.0.0", f"version: {version}"),
        encoding="utf-8")
    (src / "main.py").write_text(ENTRY, encoding="utf-8")
    (src / "ui" / "home.json").write_text(UI, encoding="utf-8")
    return src


@pytest.fixture()
def remote_store(tmp_path):
    """远端仓库（发布一个版本，目录布局即远端协议）。"""
    remote = PluginRepo(tmp_path / "remote-store")
    z = _pack(_make_src(tmp_path), tmp_path / "remote-demo-1.0.0.zip")
    remote.publish(z, publisher="远端")
    return remote


def make_fetch(remote: PluginRepo):
    """注入式 fetch：从远端仓库目录直接出 catalog.json / zip（免网络）。"""

    def fetch(url, *, max_bytes=None, **kw):
        if url.endswith("/catalog.json"):
            return json.dumps(remote.catalog(), ensure_ascii=False).encode()
        for p in remote.store_dir.rglob("*.zip"):
            if url.endswith(f"/{p.parent.name}/{p.name}"):
                return p.read_bytes()
        raise RuntimeError(f"404: {url}")

    return fetch


@pytest.fixture()
def local(tmp_path):
    return PluginRepo(tmp_path / "local-store")


class TestCatalog:
    def test_publish_refreshes_catalog(self, local, tmp_path):
        z = _pack(_make_src(tmp_path), tmp_path / "p.zip")
        local.publish(z)
        cat = json.loads((local.store_dir / "catalog.json").read_text())
        assert cat["count"] == 1 and cat["entries"][0]["id"] == "remote-demo"
        # 远端协议文件静态可托管：catalog() 与磁盘一致
        assert local.catalog()["count"] == 1

    def test_unpublish_refreshes_catalog(self, local, tmp_path):
        z = _pack(_make_src(tmp_path), tmp_path / "p.zip")
        local.publish(z)
        local.unpublish("remote-demo", "1.0.0")
        assert json.loads((local.store_dir / "catalog.json").read_text())["count"] == 0


class TestRemotes:
    def test_add_list_remove(self, local):
        r = local.add_remote("team", "http://10.0.0.5:8080/repo/")
        assert r["url"] == "http://10.0.0.5:8080/repo".rstrip("/")  # 尾斜杠归一
        assert local.list_remotes()[0]["name"] == "team"
        with pytest.raises(ForgeError, match="已存在"):
            local.add_remote("team", "http://x/")
        local.remove_remote("team")
        assert local.list_remotes() == []
        with pytest.raises(ForgeError, match="不存在"):
            local.remove_remote("team")

    def test_url_and_name_validation(self, local):
        with pytest.raises(ForgeError, match="URL 非法"):
            local.add_remote("evil", "file:///etc/passwd")
        with pytest.raises(ForgeError, match="远端名非法"):
            local.add_remote("Bad Name", "http://x/")


class TestPullSearch:
    def test_pull_and_search(self, local, remote_store):
        local.add_remote("team", "http://example.invalid/repo")
        out = local.pull_remote("team", fetch=make_fetch(remote_store))
        assert out["pulled"] is True and out["count"] == 1
        rs = local.search_remote("team", "远端")
        assert rs and rs[0]["id"] == "remote-demo" and rs[0]["sha256"]
        assert local.search_remote("team", "没有这词") == []
        # 同步状态留痕
        assert local.list_remotes()[0]["last_sync"] is not None
        assert local.list_remotes()[0]["last_error"] is None

    def test_pull_without_remote_and_before_pull(self, local, remote_store):
        with pytest.raises(ForgeError, match="远端不存在"):
            local.pull_remote("ghost", fetch=make_fetch(remote_store))
        local.add_remote("team", "http://example.invalid/repo")
        with pytest.raises(ForgeError, match="先 pull"):
            local.search_remote("team")

    def test_pull_failure_recorded(self, local):
        local.add_remote("team", "http://example.invalid/repo")

        def bad_fetch(url, *, max_bytes=None, **kw):
            raise RuntimeError("连接被拒")

        with pytest.raises(ForgeError, match="远端拉取失败"):
            local.pull_remote("team", fetch=bad_fetch)
        assert "连接被拒" in local.list_remotes()[0]["last_error"]


class TestInstallRemote:
    def test_unconfirmed_never_installs(self, local, remote_store, tmp_path):
        """铁律：生成≠安装——远端装入同样未确认只回确认信息。"""
        local.add_remote("team", "http://example.invalid/repo")
        local.pull_remote("team", fetch=make_fetch(remote_store))
        forge = PluginForge(tmp_path / "plugins")
        ins = local.install_remote("team", "remote-demo", "1.0.0", forge=forge,
                                   confirmed=False,
                                   fetch=make_fetch(remote_store))
        assert ins["installed"] is False and ins["needs_confirmation"] is True
        assert not (tmp_path / "plugins" / "remote-demo").exists()
        assert ins["remote"]["name"] == "team" and ins["remote"]["sha256"]

    def test_confirmed_installs(self, local, remote_store, tmp_path):
        local.add_remote("team", "http://example.invalid/repo")
        local.pull_remote("team", fetch=make_fetch(remote_store))
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        forge = PluginForge(plugins_dir)
        ins = local.install_remote("team", "remote-demo", "1.0.0", forge=forge,
                                   confirmed=True,
                                   fetch=make_fetch(remote_store))
        assert ins["installed"] is True
        assert (plugins_dir / "remote-demo" / "plugin.yaml").is_file()

    def test_sha256_mismatch_rejected(self, local, remote_store, tmp_path):
        """远端包对账不符→显式报因拒绝，零安装残留。"""
        local.add_remote("team", "http://example.invalid/repo")
        local.pull_remote("team", fetch=make_fetch(remote_store))
        forge = PluginForge(tmp_path / "plugins")

        def tampered(url, *, max_bytes=None, **kw):
            return b"PK\x03\x04 tampered"

        with pytest.raises(ForgeError, match="对账不符"):
            local.install_remote("team", "remote-demo", "1.0.0", forge=forge,
                                 confirmed=True, fetch=tampered)
        assert not (tmp_path / "plugins" / "remote-demo").exists()

    def test_unknown_version(self, local, remote_store, tmp_path):
        local.add_remote("team", "http://example.invalid/repo")
        local.pull_remote("team", fetch=make_fetch(remote_store))
        with pytest.raises(ForgeError, match="无此版本"):
            local.install_remote("team", "remote-demo", "9.9.9",
                                 forge=PluginForge(tmp_path / "plugins"),
                                 confirmed=True, fetch=make_fetch(remote_store))


class TestRealHttpRemote:
    def test_static_http_end_to_end(self, local, remote_store, tmp_path):
        """真实验证：任意静态 HTTP 托管仓库目录=远端源，拉取→检索→装入全链。"""
        handler = partial(SimpleHTTPRequestHandler,
                          directory=str(remote_store.store_dir))
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}"
            local.add_remote("local", base)
            out = local.pull_remote("local")          # 真 HTTP（默认 _http_get）
            assert out["pulled"] is True and out["count"] == 1
            assert local.search_remote("local", "remote-demo")
            plugins_dir = tmp_path / "plugins"
            plugins_dir.mkdir()
            ins = local.install_remote("local", "remote-demo", "1.0.0",
                                       forge=PluginForge(plugins_dir),
                                       confirmed=True)   # 真 HTTP 拉包
            assert ins["installed"] is True
            assert (plugins_dir / "remote-demo" / "plugin.yaml").is_file()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class TestCommunityPortal:
    def test_null_portal_status_and_interface(self):
        portal = NullCommunityPortal()
        assert isinstance(portal, CommunityPortal)      # 协议面齐全可替换
        st = portal.status()
        assert st["configured"] is False
        assert set(st["capabilities"]) == set(COMMUNITY_CAPABILITIES)
        assert portal.verify_signature(__import__("pathlib").Path("x.zip"), {}) is None
        assert portal.audit(__import__("pathlib").Path("x.zip"))["skipped"] is True

    def test_publish_records_portal_results(self, local, tmp_path):
        """留接口如实入账：未配置→audit=skipped、signature=None（不拦不装）。"""
        z = _pack(_make_src(tmp_path), tmp_path / "p.zip")
        pub = local.publish(z)
        assert pub["audit"]["skipped"] is True and pub["audit"]["configured"] is False
        assert pub["signature"] is None

    def test_blocked_audit_rejects_publish(self, local, tmp_path):
        class BlockingPortal(NullCommunityPortal):
            def audit(self, zip_path):
                return {"blocked": True, "reason": "沙箱审计未通过"}

        repo = PluginRepo(local.store_dir, portal=BlockingPortal())
        z = _pack(_make_src(tmp_path), tmp_path / "p.zip")
        with pytest.raises(ForgeError, match="审计拒绝发布"):
            repo.publish(z)
        assert not (repo.store_dir / "remote-demo").exists()   # 零入库残留
