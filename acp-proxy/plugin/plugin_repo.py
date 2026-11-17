# -*- coding: utf-8 -*-
"""本地/内网插件仓库（market step 2）：发布上传、检索、版本、拉取、yank、装入。

整合优先（不平行造轮子）：
- zip 预检复用 plugin_forge.preview_zip_file（清单校验/zip-slip/权限白描单一来源）；
- 装入复用 PluginForge.install_zip 确认链（**生成≠安装**铁律逐字：未确认只回确认信息）；
- 卸载复用 PluginForge.uninstall + make_hot_unloader（删除=破坏性动作，同门禁）。

仓库职责只在**分发与版本存储**：`<store>/<id>/<version>.zip` + `index.json`。
版本不可变：同 id+version 禁止覆盖（改版本号重发；撤下走 unpublish/yank）。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugin.plugin_forge import (
    ZIP_MAX_TOTAL_BYTES,
    ForgeError,
    PluginForge,
    preview_zip_file,
)

REPO_INDEX = "index.json"

# 远端拉取面限额（纯标准库 fetch；包上限同解包总量）
REMOTE_CATALOG_MAX_BYTES = 5 * 1024 * 1024      # 目录上限
REMOTE_ZIP_MAX_BYTES = ZIP_MAX_TOTAL_BYTES      # 包上限


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PluginRepo:
    """本地/内网插件仓库：发布上传、检索、版本、拉取、yank、装入。"""

    def __init__(self, store_dir: str | Path, *, portal: Any = None):
        self.store_dir = Path(store_dir)
        # 社区门户留接口（step 3 拍板项不擅选外部服务）：默认未配置空实现
        from plugin.community_portal import NullCommunityPortal
        self.portal = portal or NullCommunityPortal()

    def _plugin_store(self, plugin_id: str) -> Path:
        return self.store_dir / plugin_id

    def _index_path(self, plugin_id: str) -> Path:
        return self._plugin_store(plugin_id) / REPO_INDEX

    def _load_index(self, plugin_id: str) -> list[dict[str, Any]]:
        p = self._index_path(plugin_id)
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ForgeError(f"仓库索引损坏: {p}: {e}") from e
        return data if isinstance(data, list) else []

    def _save_index(self, plugin_id: str, entries: list[dict[str, Any]]) -> None:
        d = self._plugin_store(plugin_id)
        d.mkdir(parents=True, exist_ok=True)
        self._index_path(plugin_id).write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── 发布上传 ──────────────────────────────────────────────

    def publish(self, zip_path: str | Path, *, publisher: str = "",
                notes: str = "") -> dict[str, Any]:
        """发布上传：zip 预检（复用 step 1 单一来源，zip-slip/清单校验必拒）→
        版本不可变检查 → 入库 `<id>/<version>.zip` + 索引登记（含 sha256/权限白描）。"""
        preview = preview_zip_file(zip_path)      # 非法包在门口必拒
        meta = preview["manifest"]
        plugin_id, version = meta["id"], meta["version"]
        entries = self._load_index(plugin_id)
        if any(e.get("version") == version for e in entries):
            raise ForgeError(
                f"版本不可变：{plugin_id}@{version} 已发布（同版本禁止覆盖，改版本号重发；撤下走 unpublish）")
        store = self._plugin_store(plugin_id)
        store.mkdir(parents=True, exist_ok=True)
        dest = store / f"{version}.zip"
        shutil.copy2(zip_path, dest)
        # 社区门户留接口：沙箱审计/签名校验结果如实入账（未配置=None/skipped，不拦）
        audit = self.portal.audit(dest)
        if isinstance(audit, dict) and audit.get("blocked"):
            dest.unlink(missing_ok=True)
            if not self._index_path(plugin_id).is_file():
                shutil.rmtree(store, ignore_errors=True)   # 零入库残留
            raise ForgeError(f"社区沙箱审计拒绝发布: {audit.get('reason', audit)}")
        signature = self.portal.verify_signature(dest, {"id": plugin_id, "version": version})
        entry: dict[str, Any] = {
            "id": plugin_id,
            "version": version,
            "name": meta["name"],
            "description": meta["description"],
            "permissions": meta["permissions"],
            "hooks": meta["hooks"],
            "tools_requiring_approval": preview["permission_preview"]["tools_requiring_approval"],
            "pages": meta["pages"],
            "files": preview["files"],
            "sha256": _sha256(dest),
            "size": dest.stat().st_size,
            "published_at": _now_iso(),
            "publisher": publisher,
            "notes": notes,
            "audit": audit,
            "signature": signature,
        }
        entries.append(entry)
        self._save_index(plugin_id, entries)
        self._refresh_catalog()
        return {"published": True, **entry}

    # ── 检索 / 版本 ───────────────────────────────────────────

    def search(self, keyword: str = "", limit: int = 50) -> list[dict[str, Any]]:
        """检索：id/name/description 子串（大小写不敏感）；每插件回最新版本一条。"""
        kw = (keyword or "").strip().lower()
        out: list[dict[str, Any]] = []
        if not self.store_dir.is_dir():
            return out
        for idx_path in sorted(self.store_dir.glob(f"*/{REPO_INDEX}")):
            entries = self._load_index(idx_path.parent.name)
            if not entries:
                continue
            latest = sorted(entries, key=lambda e: str(e.get("published_at", "")))[-1]
            hay = f"{latest['id']} {latest['name']} {latest['description']}".lower()
            if kw and kw not in hay:
                continue
            out.append(latest)
            if len(out) >= limit:
                break
        return out

    def versions(self, plugin_id: str) -> list[dict[str, Any]]:
        """某插件全部版本（按发布时间升序）。"""
        if not self._plugin_store(plugin_id).is_dir():
            raise ForgeError(f"仓库无此插件：{plugin_id}")
        return sorted(self._load_index(plugin_id),
                      key=lambda e: str(e.get("published_at", "")))

    def get_zip(self, plugin_id: str, version: str) -> Path:
        z = self._plugin_store(plugin_id) / f"{version}.zip"
        if not z.is_file():
            raise ForgeError(f"仓库无此版本：{plugin_id}@{version}")
        return z

    # ── yank / 装入 ───────────────────────────────────────────

    def unpublish(self, plugin_id: str, version: str) -> dict[str, Any]:
        """yank：撤下版本（索引 + 包删除）。版本不可变语义＝重发新版本而非覆盖。"""
        entries = self._load_index(plugin_id)
        keep = [e for e in entries if e.get("version") != version]
        if len(keep) == len(entries):
            raise ForgeError(f"仓库无此版本：{plugin_id}@{version}")
        self.get_zip(plugin_id, version).unlink(missing_ok=True)
        if keep:
            self._save_index(plugin_id, keep)
        else:
            shutil.rmtree(self._plugin_store(plugin_id), ignore_errors=True)
        self._refresh_catalog()
        return {"unpublished": True, "id": plugin_id, "version": version}

    def install(self, plugin_id: str, version: str, *, forge: PluginForge,
                confirmed: bool,
                hot_load: Any = None,
                decided_by: str = "用户") -> dict[str, Any]:
        """从仓库装入：拉包 → install_zip 确认链（生成≠安装逐字：未确认只回确认信息）。"""
        z = self.get_zip(plugin_id, version)
        return forge.install_zip(z, confirmed=confirmed, hot_load=hot_load,
                                 decided_by=decided_by)

    # ── step 3：本地远端仓库（HTTP 远端源拉取安装）────────────────
    #
    # 远端协议=仓库自身目录布局（静态 HTTP 即可托管，零专有服务）：
    #   GET {base}/catalog.json          目录（全部版本条目，含 sha256）
    #   GET {base}/<id>/<version>.zip    包
    # 同目录的 PluginRepo 挂 catalog.json 路由即成远端源（repo→repo 拉取）。

    def catalog(self) -> dict[str, Any]:
        """远端协议目录：全部版本条目（静态文件 catalog.json 的内存形状）。"""
        entries: list[dict[str, Any]] = []
        if self.store_dir.is_dir():
            for idx_path in sorted(self.store_dir.glob(f"*/{REPO_INDEX}")):
                entries.extend(self._load_index(idx_path.parent.name))
        return {"updated_at": _now_iso(), "count": len(entries), "entries": entries}

    def _refresh_catalog(self) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        (self.store_dir / "catalog.json").write_text(
            json.dumps(self.catalog(), ensure_ascii=False, indent=2),
            encoding="utf-8")

    # ── 远端源管理 ──────────────────────────────────────────────

    def _remotes_path(self) -> Path:
        return self.store_dir / ".remotes.json"

    def list_remotes(self) -> list[dict[str, Any]]:
        p = self._remotes_path()
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return data if isinstance(data, list) else []

    def _save_remotes(self, remotes: list[dict[str, Any]]) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self._remotes_path().write_text(
            json.dumps(remotes, ensure_ascii=False, indent=2), encoding="utf-8")

    def add_remote(self, name: str, url: str) -> dict[str, Any]:
        """登记 HTTP 远端源（http/https；不擅选托管服务）。"""
        import re
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name or ""):
            raise ForgeError(f"远端名非法: {name!r}（小写字母/数字/连字符）")
        url = (url or "").strip().rstrip("/")
        if not re.fullmatch(r"https?://[^\s]+", url):
            raise ForgeError(f"远端 URL 非法: {url!r}（仅 http/https）")
        remotes = self.list_remotes()
        if any(r["name"] == name for r in remotes):
            raise ForgeError(f"远端已存在: {name}（先 remove 再登记）")
        entry = {"name": name, "url": url, "enabled": True,
                 "added_at": _now_iso(), "last_sync": None, "last_error": None}
        remotes.append(entry)
        self._save_remotes(remotes)
        return dict(entry)

    def remove_remote(self, name: str) -> dict[str, Any]:
        remotes = self.list_remotes()
        keep = [r for r in remotes if r["name"] != name]
        if len(keep) == len(remotes):
            raise ForgeError(f"远端不存在: {name}")
        self._save_remotes(keep)
        shutil.rmtree(self.store_dir / ".remotes" / name, ignore_errors=True)
        return {"removed": True, "name": name}

    def _remote(self, name: str) -> dict[str, Any]:
        for r in self.list_remotes():
            if r["name"] == name:
                return r
        raise ForgeError(f"远端不存在: {name}")

    # ── 拉取 / 远端检索 ─────────────────────────────────────────

    def pull_remote(self, name: str, *, fetch: Any = None,
                    max_bytes: int = REMOTE_CATALOG_MAX_BYTES) -> dict[str, Any]:
        """拉取远端目录（catalog.json）到本地缓存（离线可检索/装入对照）。"""
        remote = self._remote(name)
        fetch = fetch or _http_get
        try:
            raw = fetch(f"{remote['url']}/catalog.json", max_bytes=max_bytes)
            catalog = json.loads(raw)
            entries = catalog.get("entries")
            if not isinstance(entries, list):
                raise ForgeError("catalog.json 形状非法（缺 entries 列表）")
        except ForgeError:
            raise
        except Exception as e:  # noqa: BLE001 — 拉取失败显式留痕
            remotes = self.list_remotes()
            for r in remotes:
                if r["name"] == name:
                    r["last_error"] = f"{type(e).__name__}: {e}"
            self._save_remotes(remotes)
            raise ForgeError(f"远端拉取失败: {name}（{remote['url']}）: {e}") from e
        cache_dir = self.store_dir / ".remotes"
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / f"{name}.json").write_text(
            json.dumps({"pulled_at": _now_iso(), "base_url": remote["url"],
                        "entries": entries}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        remotes = self.list_remotes()
        for r in remotes:
            if r["name"] == name:
                r["last_sync"] = _now_iso()
                r["last_error"] = None
        self._save_remotes(remotes)
        return {"pulled": True, "name": name, "count": len(entries),
                "base_url": remote["url"]}

    def _remote_cache(self, name: str) -> list[dict[str, Any]]:
        self._remote(name)          # 不存在即报
        p = self.store_dir / ".remotes" / f"{name}.json"
        if not p.is_file():
            raise ForgeError(f"远端未拉取: {name}（先 pull）")
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise ForgeError(f"远端缓存损坏: {name}: {e}") from e
        entries = data.get("entries")
        return entries if isinstance(entries, list) else []

    def search_remote(self, name: str, keyword: str = "",
                      limit: int = 50) -> list[dict[str, Any]]:
        """远端检索（本地缓存目录；每插件最新版本一条）。"""
        kw = (keyword or "").strip().lower()
        latest: dict[str, dict[str, Any]] = {}
        for e in self._remote_cache(name):
            pid = str(e.get("id", ""))
            if not pid:
                continue
            cur = latest.get(pid)
            if cur is None or str(e.get("published_at", "")) > str(cur.get("published_at", "")):
                latest[pid] = e
        out = []
        for e in latest.values():
            hay = f"{e.get('id', '')} {e.get('name', '')} {e.get('description', '')}".lower()
            if kw and kw not in hay:
                continue
            out.append(e)
        return out[:limit]

    # ── 从远端装入（确认链复用：生成≠安装逐字）──────────────────

    def install_remote(self, name: str, plugin_id: str, version: str, *,
                       forge: PluginForge, confirmed: bool,
                       hot_load: Any = None, decided_by: str = "用户",
                       fetch: Any = None) -> dict[str, Any]:
        """拉包 → sha256 对账 → install_zip 确认链（未确认只回确认信息）。
        包校验失败/账目不符→显式报因拒绝（零安装残留）。"""
        entries = [e for e in self._remote_cache(name)
                   if e.get("id") == plugin_id and e.get("version") == version]
        if not entries:
            raise ForgeError(f"远端无此版本: {plugin_id}@{version}（@{name}）")
        entry = entries[0]
        base = self._remote(name)["url"]
        fetch = fetch or _http_get
        raw = fetch(f"{base}/{plugin_id}/{version}.zip",
                    max_bytes=REMOTE_ZIP_MAX_BYTES)
        digest = hashlib.sha256(raw).hexdigest()
        expected = str(entry.get("sha256", ""))
        if expected and digest != expected:
            raise ForgeError(
                f"远端包对账不符（sha256）: {plugin_id}@{version} "
                f"期望 {expected[:12]}… 实得 {digest[:12]}…——拒绝安装")
        with tempfile.TemporaryDirectory(prefix="remote-install-") as td:
            z = Path(td) / f"{plugin_id}-{version}.zip"
            z.write_bytes(raw)
            result = forge.install_zip(z, confirmed=confirmed, hot_load=hot_load,
                                       decided_by=decided_by)
        result["remote"] = {"name": name, "base_url": base,
                            "sha256": digest, "entry": entry}
        return result


# ---- 远端 HTTP 拉取面（纯标准库；fetch 可注入替换）----


def _http_get(url: str, *, max_bytes: int = REMOTE_CATALOG_MAX_BYTES,
              timeout: float = 15.0) -> bytes:
    """HTTP(S) GET（标准库，有界读取+超时）——本地/内网远端源的最小拉取面。"""
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "openmate-repo/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 仅 http(s) 登记面
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ForgeError(f"远端响应超限（>{max_bytes} 字节）: {url}")
    return data
