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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from plugin.plugin_forge import ForgeError, PluginForge, preview_zip_file

REPO_INDEX = "index.json"


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

    def __init__(self, store_dir: str | Path):
        self.store_dir = Path(store_dir)

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
        }
        entries.append(entry)
        self._save_index(plugin_id, entries)
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
        return {"unpublished": True, "id": plugin_id, "version": version}

    def install(self, plugin_id: str, version: str, *, forge: PluginForge,
                confirmed: bool,
                hot_load: Any = None,
                decided_by: str = "用户") -> dict[str, Any]:
        """从仓库装入：拉包 → install_zip 确认链（生成≠安装逐字：未确认只回确认信息）。"""
        z = self.get_zip(plugin_id, version)
        return forge.install_zip(z, confirmed=confirmed, hot_load=hot_load,
                                 decided_by=decided_by)
