"""
plugin.yaml 清单解析与校验 — 新规范插件的唯一清单入口

设计文档: docs/plugin-system-design.md §1
- 清单声明：id/版本/权限/导航/页面/工具/流程
- 校验失败抛 ManifestError（带字段路径），由 scanner 记录不中断其它插件
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from plugin.models import KNOWN_PERMISSIONS

# ---- 校验规则 ----

ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")
TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
HANDLER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PAGE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
ICON_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
POSITION_VALUES = {"start", "end"}
APPROVAL_VALUES = {"never", "on-first-use", "always"}
WIDGETS = {"text", "textarea", "number", "select", "checkbox", "date"}


class ManifestError(ValueError):
    """plugin.yaml 校验失败（消息带字段路径）"""


def _require(data: dict, key: str, typ: type, where: str) -> Any:
    if key not in data:
        raise ManifestError(f"{where}.{key} 缺失")
    value = data[key]
    if not isinstance(value, typ):
        raise ManifestError(f"{where}.{key} 类型应为 {typ.__name__}，实际 {type(value).__name__}")
    return value


@dataclass(frozen=True)
class NavSpec:
    """导航声明 — 前端底部导航由此长出（position 语义与 src/lib/plugin-nav.ts 一致）"""

    label: str
    icon: str = "Puzzle"
    position: str = "end"


@dataclass(frozen=True)
class PageSpec:
    """页面声明 — dsl（UI DSL JSON）或 html（自定义面板，二选一）"""

    id: str
    title: str
    dsl: str | None = None
    html: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """MCP 工具声明 — handler 为 entry 模块内的函数名"""

    name: str
    description: str
    handler: str
    input_schema: dict[str, Any]
    permissions: tuple[str, ...] = ("tool.call",)
    approval: str = "never"

    @property
    def mcp_name(self) -> str:
        """MCP 工具全名：<plugin-id>__<tool-name>（由 bridge 组装）"""
        return self.name


@dataclass(frozen=True)
class YamlPluginManifest:
    """plugin.yaml 全量清单"""

    id: str
    name: str
    version: str
    description: str
    author: str
    entry: str
    permissions: tuple[str, ...]
    nav: NavSpec | None
    pages: tuple[PageSpec, ...]
    tools: tuple[ToolSpec, ...]
    flows: tuple[str, ...] = field(default_factory=tuple)

    def to_frontend_manifest(self) -> dict[str, Any]:
        """转换为前端 plugin-nav.ts / plugin-manager.ts 消费的 manifest 形状"""
        manifest: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "permissions": list(self.permissions),
            "hooks": [],
            "routes": [f"/plugins/{self.id}"],
            "frontend": {
                "pages": [
                    {"id": p.id, "title": p.title, "dsl": p.dsl, "html": p.html}
                    for p in self.pages
                ],
            },
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "approval": t.approval,
                    "input_schema": t.input_schema,
                }
                for t in self.tools
            ],
        }
        if self.nav is not None:
            manifest["frontend"]["nav"] = {
                "label": self.nav.label,
                "icon": self.nav.icon,
                "href": f"/plugins/{self.id}",
                "position": self.nav.position,
            }
        return manifest


def _parse_nav(data: Any) -> NavSpec | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ManifestError("ui.nav 应为映射")
    label = _require(data, "label", str, "ui.nav")
    icon = data.get("icon", "Puzzle")
    if not isinstance(icon, str) or not ICON_PATTERN.match(icon):
        raise ManifestError(f"ui.nav.icon 非法: {icon!r}（应为 PascalCase lucide 图标名）")
    position = data.get("position", "end")
    if not isinstance(position, str):
        raise ManifestError("ui.nav.position 应为字符串")
    if position not in POSITION_VALUES and not position.startswith("after:"):
        raise ManifestError(f"ui.nav.position 非法: {position!r}（start | end | after:<href>）")
    return NavSpec(label=label, icon=icon, position=position)


def _parse_pages(data: Any) -> tuple[PageSpec, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ManifestError("ui.pages 应为列表")
    pages: list[PageSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        where = f"ui.pages[{index}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where} 应为映射")
        page_id = _require(item, "id", str, where)
        if not PAGE_ID_PATTERN.match(page_id):
            raise ManifestError(f"{where}.id 非法: {page_id!r}")
        if page_id in seen:
            raise ManifestError(f"{where}.id 重复: {page_id!r}")
        seen.add(page_id)
        title = _require(item, "title", str, where)
        dsl = item.get("dsl")
        html = item.get("html")
        if (dsl is None) == (html is None):
            raise ManifestError(f"{where} 需且仅需声明 dsl 或 html 之一")
        for key, value in (("dsl", dsl), ("html", html)):
            if value is not None and (not isinstance(value, str) or value.startswith("/") or ".." in value):
                raise ManifestError(f"{where}.{key} 应为插件目录内相对路径: {value!r}")
        pages.append(PageSpec(id=page_id, title=title, dsl=dsl, html=html))
    return tuple(pages)


def _parse_tools(data: Any) -> tuple[ToolSpec, ...]:
    if data is None:
        return ()
    if not isinstance(data, list):
        raise ManifestError("tools 应为列表")
    tools: list[ToolSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(data):
        where = f"tools[{index}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{where} 应为映射")
        name = _require(item, "name", str, where)
        if not TOOL_NAME_PATTERN.match(name) or "__" in name:
            raise ManifestError(f"{where}.name 非法: {name!r}（小写连字符，不含双下划线）")
        if name in seen:
            raise ManifestError(f"{where}.name 重复: {name!r}")
        seen.add(name)
        description = _require(item, "description", str, where)
        handler = _require(item, "handler", str, where)
        if not HANDLER_PATTERN.match(handler):
            raise ManifestError(f"{where}.handler 非法: {handler!r}")
        input_schema = item.get("input_schema", {"type": "object", "properties": {}})
        if not isinstance(input_schema, dict):
            raise ManifestError(f"{where}.input_schema 应为映射")
        permissions = item.get("permissions", ["tool.call"])
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise ManifestError(f"{where}.permissions 应为字符串列表")
        unknown = set(permissions) - KNOWN_PERMISSIONS
        if unknown:
            raise ManifestError(f"{where}.permissions 含未知权限: {sorted(unknown)}")
        approval = item.get("approval", "never")
        if approval not in APPROVAL_VALUES:
            raise ManifestError(f"{where}.approval 非法: {approval!r}（never | on-first-use | always）")
        tools.append(
            ToolSpec(
                name=name,
                description=description,
                handler=handler,
                input_schema=input_schema,
                permissions=tuple(permissions),
                approval=approval,
            )
        )
    return tuple(tools)


def parse_yaml_manifest(data: dict[str, Any], *, dir_name: str, base_dir: Path) -> YamlPluginManifest:
    """校验并构造 YamlPluginManifest。

    参数:
        data: plugin.yaml 原始映射
        dir_name: 插件目录名（必须与 id 一致）
        base_dir: 插件目录绝对路径（校验 entry 存在性）
    """
    if not isinstance(data, dict):
        raise ManifestError("plugin.yaml 顶层应为映射")

    plugin_id = _require(data, "id", str, "manifest")
    if not ID_PATTERN.match(plugin_id):
        raise ManifestError(f"id 非法: {plugin_id!r}（^[a-z][a-z0-9-]{{1,63}}$）")
    if plugin_id != dir_name:
        raise ManifestError(f"id {plugin_id!r} 与目录名 {dir_name!r} 不一致")

    name = _require(data, "name", str, "manifest")
    version = _require(data, "version", str, "manifest")
    if not VERSION_PATTERN.match(version):
        raise ManifestError(f"version 非法: {version!r}（应为 x.y.z）")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ManifestError("description 应为字符串")
    author = data.get("author", "")
    if not isinstance(author, str):
        raise ManifestError("author 应为字符串")

    entry = _require(data, "entry", str, "manifest")
    entry_path = base_dir / entry
    if not entry_path.is_file():
        raise ManifestError(f"entry 文件不存在: {entry}")

    permissions = data.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
        raise ManifestError("permissions 应为字符串列表")
    unknown = set(permissions) - KNOWN_PERMISSIONS
    if unknown:
        raise ManifestError(f"permissions 含未知权限: {sorted(unknown)}")

    ui = data.get("ui", {})
    if ui is None:
        ui = {}
    if not isinstance(ui, dict):
        raise ManifestError("ui 应为映射")
    nav = _parse_nav(ui.get("nav"))
    pages = _parse_pages(ui.get("pages"))

    # 页面文件存在性（dsl 必须是真实 JSON 文件；html 为层②预留，同样要求存在）
    for page in pages:
        rel = page.dsl or page.html
        assert rel is not None
        if not (base_dir / rel).is_file():
            raise ManifestError(f"页面文件不存在: {rel}（page {page.id}）")

    tools = _parse_tools(data.get("tools"))

    flows_raw = data.get("flows", [])
    if not isinstance(flows_raw, list) or not all(isinstance(f, str) for f in flows_raw):
        raise ManifestError("flows 应为相对路径字符串列表")
    for rel in flows_raw:
        if rel.startswith("/") or ".." in rel:
            raise ManifestError(f"flows 路径应为插件目录内相对路径: {rel!r}")
        if not (base_dir / rel).is_file():
            raise ManifestError(f"流程定义文件不存在: {rel}")

    return YamlPluginManifest(
        id=plugin_id,
        name=name,
        version=version,
        description=description,
        author=author,
        entry=entry,
        permissions=tuple(permissions),
        nav=nav,
        pages=pages,
        tools=tools,
        flows=tuple(flows_raw),
    )


def load_yaml_manifest(plugin_dir: Path) -> YamlPluginManifest:
    """从插件目录读取并校验 plugin.yaml"""
    manifest_path = plugin_dir / "plugin.yaml"
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ManifestError(f"plugin.yaml YAML 解析失败: {e}") from e
    return parse_yaml_manifest(raw, dir_name=plugin_dir.name, base_dir=plugin_dir)
