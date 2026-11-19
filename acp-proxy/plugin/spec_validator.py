# -*- coding: utf-8 -*-
"""
插件规范校验器 — 创建/安装双侧校验闸门（docs/plugin-spec.md）

- 机器可读规范：plugin/plugin_spec.schema.json（声明式：名称/图标/一级二级菜单/路由/能力/权限/版本）
- 语义校验（字段路径报错，SoulMate 自创建可自动执行）：
  * 结构/类型/命名模式（id/version/icon/page id/tool name/handler）
  * 菜单契约：ui.menu 一级声明 + 二级条目；entries[].function 必须指向 tools[].name；
    function 工具必须 input_schema + output_schema 双侧齐备（工作流可调用单元契约）
  * 权限白名单、dsl/html 二选一、href 越界
- 双侧：
  * 创建侧 validate_manifest_dict(data) — 只查声明（文件尚不存在时可用）
  * 安装侧 validate_plugin_dir(path) — 加查 entry/页面/流程文件真实存在（装载即拒收）

CLI:
  python -m plugin.spec_validator plugins/bidding          # 校验插件目录（安装侧）
  python -m plugin.spec_validator --dict manifest.json     # 校验 manifest 字典（创建侧）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from plugin.yaml_manifest import (
    KNOWN_PERMISSIONS,
    ManifestError,
    _parse_menu,
    _parse_nav,
    _parse_pages,
    _parse_tools,
    load_yaml_manifest,
    parse_yaml_manifest,
)

SCHEMA_PATH = Path(__file__).parent / "plugin_spec.schema.json"


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_manifest_dict(data: dict[str, Any], *, dir_name: str | None = None) -> dict[str, Any]:
    """创建侧校验：声明合规即过（不查文件存在性）。

    通过返回 {"ok": True, "menu": ..., "tools": ...}；不合规抛 ManifestError（消息带字段路径）。
    """
    if not isinstance(data, dict):
        raise ManifestError("manifest 顶层应为映射")
    plugin_id = data.get("id")
    if not isinstance(plugin_id, str) or not plugin_id:
        raise ManifestError("id 缺失")
    if dir_name is not None and plugin_id != dir_name:
        raise ManifestError(f"id {plugin_id!r} 与目录名 {dir_name!r} 不一致")
    for key, typ in (("name", str), ("version", str), ("entry", str)):
        value = data.get(key)
        if not isinstance(value, typ) or not value:
            raise ManifestError(f"{key} 缺失或类型错误")
    permissions = data.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        raise ManifestError("permissions 应为字符串列表")
    unknown = set(permissions) - KNOWN_PERMISSIONS
    if unknown:
        raise ManifestError(f"permissions 含未知权限: {sorted(unknown)}")

    ui = data.get("ui", {}) or {}
    if not isinstance(ui, dict):
        raise ManifestError("ui 应为映射")
    _parse_nav(ui.get("nav"))
    _parse_pages(ui.get("pages"))
    tools = _parse_tools(data.get("tools"))
    menu = _parse_menu(ui.get("menu"), {t.name: t for t in tools}, str(plugin_id))
    flows_raw = data.get("flows", [])
    if not isinstance(flows_raw, list) or not all(isinstance(f, str) for f in flows_raw):
        raise ManifestError("flows 应为相对路径字符串列表")
    return {
        "ok": True,
        "id": plugin_id,
        "menu": menu is not None,
        "menu_entries": len(menu.entries) if menu else 0,
        "tools": [t.name for t in tools],
    }


def validate_plugin_dir(plugin_dir: Path | str) -> dict[str, Any]:
    """安装侧校验：声明 + 文件存在性（scanner 装载同口径）"""
    plugin_dir = Path(plugin_dir)
    if not (plugin_dir / "plugin.yaml").is_file():
        raise ManifestError(f"plugin.yaml 不存在: {plugin_dir}")
    manifest = load_yaml_manifest(plugin_dir)   # 全量校验（含 entry/页面/流程文件存在性）
    return {
        "ok": True,
        "id": manifest.id,
        "menu": manifest.menu is not None,
        "menu_entries": len(manifest.menu.entries) if manifest.menu else 0,
        "tools": [t.name for t in manifest.tools],
    }


def main(argv: list[str]) -> int:
    try:
        if argv and argv[0] == "--dict":
            data = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
            result = validate_manifest_dict(data)
        else:
            target = Path(argv[0]) if argv else Path(".")
            result = validate_plugin_dir(target)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except ManifestError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
