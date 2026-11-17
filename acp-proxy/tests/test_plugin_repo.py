# -*- coding: utf-8 -*-
"""Sprint C 市场 step 2——本地/内网插件仓库（发布上传/检索/版本/yank/装入）
+ 卸载生命周期（补 ROADMAP「Plugin 系统完善」的卸载缺口）。

整合面（勿平行造轮子）：zip 预检=preview_zip_file 单一来源；装入=install_zip
确认链（生成≠安装）；卸载=uninstall 确认门禁 + make_hot_unloader（工具/流程解绑）
+ dsl_api 路由组自惰（卸载即收走）。
"""
import zipfile

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.dsl_api import build_plugin_router
from plugin.flow_engine import get_engine
from plugin.forge_api import build_forge_router, make_hot_loader, make_hot_unloader
from plugin.mcp_bridge import PluginToolRegistry
from plugin.plugin_forge import ForgeError, PluginForge
from plugin.plugin_repo import PluginRepo
from plugin.repo_api import build_repo_router
from plugin.scanner import scan_plugins

MANIFEST = """\
id: repo-demo
name: 仓库演示
version: 1.0.0
description: 市场 step 2 验收插件
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
  - name: danger
    description: 需审批动作
    handler: handle_danger
    approval: always
    input_schema: {type: object}
"""

ENTRY = """\
def handle_ping(args, ctx):
    return {"pong": True}


def handle_danger(args, ctx):
    return {"done": True}
"""

UI = '{"version": 1, "page": {"title": "工作台"}, "panels": [{"type": "report", "id": "r", "title": "结果"}]}'


def run(coro):
    import asyncio
    return asyncio.run(coro)


@pytest.fixture()
def env(tmp_path):
    plugins_dir = tmp_path / "plugins"
    store_dir = tmp_path / "repo-store"
    src = tmp_path / "src" / "repo-demo"
    (src / "ui").mkdir(parents=True)
    (src / "plugin.yaml").write_text(MANIFEST, encoding="utf-8")
    (src / "main.py").write_text(ENTRY, encoding="utf-8")
    (src / "ui" / "home.json").write_text(UI, encoding="utf-8")
    plugins_dir.mkdir()
    forge = PluginForge(plugins_dir)
    repo = PluginRepo(store_dir)
    registry = PluginToolRegistry()
    engine = get_engine()
    return {"plugins_dir": plugins_dir, "store_dir": store_dir, "src": src,
            "forge": forge, "repo": repo, "registry": registry, "engine": engine}


def _pack(src_dir, zip_path):
    """打包为 zip（成员名 <id>/<rel>——合法单顶层结构）。"""
    with zipfile.ZipFile(zip_path, "w") as zf:
        for p in sorted(src_dir.rglob("*")):
            if p.is_file():
                zf.write(p, f"{src_dir.name}/{p.relative_to(src_dir)}")
    return zip_path


@pytest.fixture()
def demo_zip(env, tmp_path):
    return _pack(env["src"], tmp_path / "repo-demo-1.0.0.zip")


# ── 发布上传 / 检索 / 版本 ─────────────────────────────────────────

class TestPublishSearchVersions:
    def test_publish_stores_metadata_and_zip(self, env, demo_zip):
        pub = env["repo"].publish(demo_zip, publisher="soulmate", notes="首发")
        assert pub["published"] is True
        assert pub["id"] == "repo-demo" and pub["version"] == "1.0.0"
        assert pub["tools_requiring_approval"] == ["repo-demo__danger"]   # 权限白描
        assert len(pub["sha256"]) == 64 and pub["size"] > 0
        z = env["repo"].get_zip("repo-demo", "1.0.0")
        assert z.is_file()

    def test_search_by_keyword(self, env, demo_zip):
        env["repo"].publish(demo_zip)
        assert len(env["repo"].search("")) == 1
        assert len(env["repo"].search("仓库演示")) == 1
        assert len(env["repo"].search("REPO-DEMO")) == 1       # 大小写不敏感
        assert env["repo"].search("不存在的词") == []

    def test_versions_ordered(self, env, demo_zip, tmp_path):
        env["repo"].publish(demo_zip)
        (env["src"] / "plugin.yaml").write_text(
            MANIFEST.replace("version: 1.0.0", "version: 1.1.0"), encoding="utf-8")
        z2 = _pack(env["src"], tmp_path / "repo-demo-1.1.0.zip")
        env["repo"].publish(z2)
        vs = env["repo"].versions("repo-demo")
        assert [v["version"] for v in vs] == ["1.0.0", "1.1.0"]

    def test_version_immutable(self, env, demo_zip):
        env["repo"].publish(demo_zip)
        with pytest.raises(ForgeError, match="版本不可变"):
            env["repo"].publish(demo_zip)

    def test_publish_rejects_malicious_zip(self, env, tmp_path):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../../evil.py", "print('pwned')")
        with pytest.raises(ForgeError):
            env["repo"].publish(evil)
        assert not (env["store_dir"] / "repo-demo").exists()      # 拒收零入库

    def test_unpublish_yanks_version(self, env, demo_zip):
        env["repo"].publish(demo_zip)
        out = env["repo"].unpublish("repo-demo", "1.0.0")
        assert out["unpublished"] is True
        assert env["repo"].search("repo-demo") == []              # 检索即收走
        with pytest.raises(ForgeError):
            env["repo"].get_zip("repo-demo", "1.0.0")

    def test_unknown_plugin_and_version(self, env):
        with pytest.raises(ForgeError):
            env["repo"].versions("ghost")
        with pytest.raises(ForgeError):
            env["repo"].get_zip("repo-demo", "9.9.9")


# ── 从仓库装入（生成≠安装确认链逐字）────────────────────────────

class TestInstallFromRepo:
    def test_unconfirmed_never_installs(self, env, demo_zip):
        """铁律：生成≠安装——未确认只回确认所需信息，plugins/<id> 绝不出现。"""
        env["repo"].publish(demo_zip)
        hot_load = make_hot_loader(env["plugins_dir"], env["registry"],
                                   env["engine"], None, None)
        ins = env["repo"].install("repo-demo", "1.0.0", forge=env["forge"],
                                  confirmed=False, hot_load=hot_load)
        assert ins["installed"] is False and ins["needs_confirmation"] is True
        assert not (env["plugins_dir"] / "repo-demo").exists()
        assert "用户确认" in ins["message"] or "确认" in ins["message"]

    def test_confirmed_installs_and_hot_loads(self, env, demo_zip):
        env["repo"].publish(demo_zip)
        hot_load = make_hot_loader(env["plugins_dir"], env["registry"],
                                   env["engine"], None, None)
        ins = env["repo"].install("repo-demo", "1.0.0", forge=env["forge"],
                                  confirmed=True, hot_load=hot_load)
        assert ins["installed"] is True
        assert (env["plugins_dir"] / "repo-demo" / "plugin.yaml").is_file()
        grew = ins.get("grown") or ins
        assert "repo-demo__ping" in grew["tool_names"]
        assert env["registry"].list_tools("repo-demo")                # 工具已绑

    def test_dry_run_failure_rejects_import(self, env, tmp_path):
        """dry-run 不过（entry handler 缺失）→ 拒绝导入，零安装残留。"""
        (env["src"] / "main.py").write_text("# 无 handler\n", encoding="utf-8")
        z = _pack(env["src"], tmp_path / "broken.zip")
        env["repo"].publish(z)      # 发布只做 zip 预检（清单/越界），深度校验在装入
        hot_load = make_hot_loader(env["plugins_dir"], env["registry"],
                                   env["engine"], None, None)
        with pytest.raises(ForgeError, match="dry-run"):
            env["repo"].install("repo-demo", "1.0.0", forge=env["forge"],
                                confirmed=True, hot_load=hot_load)
        assert not (env["plugins_dir"] / "repo-demo").exists()
        assert not (env["plugins_dir"] / ".drafts" / "repo-demo").exists()   # 暂存已清

    def test_yanked_version_not_installable(self, env, demo_zip):
        env["repo"].publish(demo_zip)
        env["repo"].unpublish("repo-demo", "1.0.0")
        with pytest.raises(ForgeError, match="无此版本"):
            env["repo"].install("repo-demo", "1.0.0", forge=env["forge"],
                                confirmed=True)


# ── 卸载生命周期（DELETE 门禁 + 卸载即收走）────────────────────────

class TestUninstallLifecycle:
    def _installed(self, env, demo_zip):
        env["repo"].publish(demo_zip)
        hot_load = make_hot_loader(env["plugins_dir"], env["registry"],
                                   env["engine"], None, None)
        return env["repo"].install("repo-demo", "1.0.0", forge=env["forge"],
                                   confirmed=True, hot_load=hot_load)

    def test_unconfirmed_never_deletes(self, env, demo_zip):
        """DELETE 门禁（与 install 的生成≠安装逐字对称）：未确认一字不删。"""
        self._installed(env, demo_zip)
        before = sorted(p.name for p in (env["plugins_dir"] / "repo-demo").rglob("*"))
        hot_unload = make_hot_unloader(env["registry"], env["engine"])
        out = env["forge"].uninstall("repo-demo", confirmed=False,
                                     hot_unload=hot_unload)
        assert out["confirmed"] is False and out["will_remove"]
        assert "repo-demo__ping" in out["will_unbind"]["tools"]
        assert "DELETE 门禁" in out["message"]
        after = sorted(p.name for p in (env["plugins_dir"] / "repo-demo").rglob("*"))
        assert before == after                                       # 一字不删
        assert env["registry"].list_tools("repo-demo")               # 未解绑

    def test_confirmed_uninstalls_and_unbinds(self, env, demo_zip):
        self._installed(env, demo_zip)
        app = FastAPI()
        desc = scan_plugins(env["plugins_dir"]).get("repo-demo")
        assert desc is not None
        app.include_router(build_plugin_router(desc, env["registry"]))
        client = TestClient(app)
        assert client.get("/plugins/repo-demo/manifest").status_code == 200
        assert env["registry"].list_tools("repo-demo")

        hot_unload = make_hot_unloader(env["registry"], env["engine"])
        out = env["forge"].uninstall("repo-demo", confirmed=True,
                                     hot_unload=hot_unload, decided_by="验收")
        assert out["confirmed"] is True and out["removed_files"] > 0
        assert out["unbound"]["tools_unbound"] == 2                   # ping+danger
        assert out["decided_by"] == "验收"
        assert not (env["plugins_dir"] / "repo-demo").exists()
        assert env["registry"].list_tools("repo-demo") == []          # 工具解绑
        assert scan_plugins(env["plugins_dir"]).get("repo-demo") is None   # 菜单收走（backend 面）

        # 卸载即收走：已注册路由组自惰，整体 404
        r = client.get("/plugins/repo-demo/manifest")
        assert r.status_code == 404 and "已卸载" in r.json()["detail"]

    def test_uninstall_unknown_plugin(self, env):
        with pytest.raises(ForgeError, match="未安装"):
            env["forge"].uninstall("ghost", confirmed=True)


# ── 仓库 HTTP 面（真实验证：publish→检索→下载→装入→卸载→yank 全链）──

class TestRepoRoutes:
    def test_full_chain_over_http(self, env, demo_zip, tmp_path):
        import base64

        app = FastAPI()
        app.include_router(build_repo_router(
            env["store_dir"], env["plugins_dir"], env["registry"], env["engine"]))
        app.include_router(build_forge_router(
            env["plugins_dir"], env["registry"], env["engine"]))
        client = TestClient(app)

        # 发布上传
        b64 = base64.b64encode(demo_zip.read_bytes()).decode()
        r = client.post("/plugins/repo/publish",
                        json={"zip_b64": b64, "publisher": "验收", "notes": "HTTP 全链"})
        assert r.status_code == 200 and r.json()["version"] == "1.0.0"
        # 版本不可变 → 409
        r2 = client.post("/plugins/repo/publish", json={"zip_b64": b64})
        assert r2.status_code == 409
        # 检索 / 版本 / 下载
        assert client.get("/plugins/repo?keyword=repo").json()["count"] == 1
        vs = client.get("/plugins/repo/repo-demo/versions").json()
        assert vs["count"] == 1
        dl = client.get("/plugins/repo/repo-demo/versions/1.0.0/download")
        assert dl.status_code == 200 and dl.content[:2] == b"PK"
        # 装入（未确认→确认信息；确认→安装）
        ins = client.post("/plugins/repo/repo-demo/versions/1.0.0/install",
                          json={"confirmed": False}).json()
        assert ins["installed"] is False
        ins2 = client.post("/plugins/repo/repo-demo/versions/1.0.0/install",
                           json={"confirmed": True}).json()
        assert ins2["installed"] is True
        # 卸载（DELETE 门禁→确认→删）+ yank
        un = client.post("/plugins/forge/repo-demo/uninstall",
                         json={"confirmed": False}).json()
        assert un["confirmed"] is False and (env["plugins_dir"] / "repo-demo").is_dir()
        un2 = client.post("/plugins/forge/repo-demo/uninstall",
                          json={"confirmed": True, "decided_by": "验收"}).json()
        assert un2["confirmed"] is True and not (env["plugins_dir"] / "repo-demo").exists()
        assert client.delete("/plugins/repo/repo-demo/versions/1.0.0").json()["unpublished"] is True
        assert client.get("/plugins/repo").json()["count"] == 0

    def test_publish_rejects_bad_b64(self, env):
        app = FastAPI()
        app.include_router(build_repo_router(
            env["store_dir"], env["plugins_dir"], env["registry"], env["engine"]))
        client = TestClient(app)
        r = client.post("/plugins/repo/publish", json={"zip_b64": "!!!"})
        assert r.status_code == 400
