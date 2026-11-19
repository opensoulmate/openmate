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
class MenuEntrySpec:
    """二级菜单条目声明（docs/plugin-spec.md §菜单契约）

    - label/label_i18n：显示名（label_i18n={zh,en,ja} 覆盖 label，多语言强制约束）
    - href：应用内路由（本插件页 /plugins/<id>/<page> 或既有页面）
    - function：**工作流可调用单元**（必须指向本插件 tools[].name；安装后进流程工具面）
    - position：join 场景插入位（start | end，默认 end）
    """

    id: str
    label: str
    href: str
    label_i18n: dict[str, str] = field(default_factory=dict)
    function: str | None = None
    position: str = "end"


@dataclass(frozen=True)
class MenuSpec:
    """一级菜单贡献声明（插件贡献域）— 安装生成 / 卸载收走，禁手写死

    - join：可选——并入既有功能域（内置域 id 或插件 id）作二级条目；
      目标域缺席时回退独立一级位（同一功能只在一个层级出现一次）。
    - entries：二级条目清单，按声明顺序渲染。
    """

    label: str
    icon: str = "Puzzle"
    position: str = "end"
    join: str | None = None
    label_i18n: dict[str, str] = field(default_factory=dict)
    entries: tuple["MenuEntrySpec", ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    """MCP 工具声明 — handler 为 entry 模块内的函数名"""

    name: str
    description: str
    handler: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
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
    menu: MenuSpec | None = None
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
                    **({"output_schema": t.output_schema} if t.output_schema is not None else {}),
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
        if self.menu is not None:
            # 菜单贡献（docs/plugin-spec.md §菜单契约）：L1 + L2 清单，前端动态生成
            manifest["frontend"]["menu"] = {
                "label": self.menu.label,
                "icon": self.menu.icon,
                "position": self.menu.position,
                "join": self.menu.join,
                "label_i18n": dict(self.menu.label_i18n),
                "entries": [
                    {
                        "id": e.id,
                        "label": e.label,
                        "href": e.href,
                        "label_i18n": dict(e.label_i18n),
                        "function": e.function,
                        "position": e.position,
                    }
                    for e in self.menu.entries
                ],
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
        output_schema = item.get("output_schema")
        if output_schema is not None and not isinstance(output_schema, dict):
            raise ManifestError(f"{where}.output_schema 应为映射（JSON Schema）")
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
                output_schema=output_schema,
                permissions=tuple(permissions),
                approval=approval,
            )
        )
    return tuple(tools)


def _parse_label_i18n(data: Any, where: str) -> dict[str, str]:
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ManifestError(f"{where} 应为映射（zh/en/ja）")
    out: dict[str, str] = {}
    for lang, value in data.items():
        if lang not in ("zh", "en", "ja"):
            raise ManifestError(f"{where} 含未知语言键: {lang!r}（zh | en | ja）")
        if not isinstance(value, str):
            raise ManifestError(f"{where}.{lang} 应为字符串")
        out[lang] = value
    return out


def _parse_menu(data: Any, tools_by_name: dict[str, "ToolSpec"], plugin_id: str) -> MenuSpec | None:
    """ui.menu 解析与校验（docs/plugin-spec.md §菜单契约）

    - entries[].function 必须指向本插件 tools[].name（工作流可调用单元契约），
      且该工具必须声明 input_schema + output_schema（IO 契约双侧齐备）；
    - entries[].href 必须是应用内绝对路由且无越界；
    - join：内置域 id 或插件 id（目标缺席回退独立一级位）。
    """
    tool_names = set(tools_by_name)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ManifestError("ui.menu 应为映射")
    where = "ui.menu"
    label = _require(data, "label", str, where)
    label_i18n = _parse_label_i18n(data.get("label_i18n"), f"{where}.label_i18n")
    icon = data.get("icon", "Puzzle")
    if not isinstance(icon, str) or not ICON_PATTERN.match(icon):
        raise ManifestError(f"{where}.icon 非法: {icon!r}（应为 PascalCase lucide 图标名）")
    position = data.get("position", "end")
    if not isinstance(position, str):
        raise ManifestError(f"{where}.position 应为字符串")
    if position not in POSITION_VALUES and not position.startswith("after:"):
        raise ManifestError(f"{where}.position 非法: {position!r}（start | end | after:<href>）")
    join = data.get("join")
    if join is not None and (not isinstance(join, str) or not join):
        raise ManifestError(f"{where}.join 应为非空字符串（内置域 id 或插件 id）")
    raw_entries = data.get("entries", [])
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ManifestError(f"{where}.entries 应为非空列表（二级条目清单）")
    entries: list[MenuEntrySpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_entries):
        entry_where = f"{where}.entries[{index}]"
        if not isinstance(item, dict):
            raise ManifestError(f"{entry_where} 应为映射")
        entry_id = _require(item, "id", str, entry_where)
        if not PAGE_ID_PATTERN.match(entry_id):
            raise ManifestError(f"{entry_where}.id 非法: {entry_id!r}")
        if entry_id in seen:
            raise ManifestError(f"{entry_where}.id 重复: {entry_id!r}")
        seen.add(entry_id)
        entry_label = _require(item, "label", str, entry_where)
        entry_label_i18n = _parse_label_i18n(item.get("label_i18n"), f"{entry_where}.label_i18n")
        href = _require(item, "href", str, entry_where)
        if not href.startswith("/") or ".." in href:
            raise ManifestError(f"{entry_where}.href 应为应用内绝对路由: {href!r}")
        function = item.get("function")
        if function is not None:
            if not isinstance(function, str):
                raise ManifestError(f"{entry_where}.function 应为字符串（tools[].name）")
            if function not in tool_names:
                raise ManifestError(
                    f"{entry_where}.function 未声明为工具: {function!r}"
                    f"（二级功能=工作流可调用单元，须指向 tools[].name）"
                )
            tool = tools_by_name[function]
            if not tool.input_schema or tool.output_schema is None:
                raise ManifestError(
                    f"{entry_where}.function 的工具 {function!r} 缺 IO schema"
                    f"（input_schema + output_schema 双侧必填，工作流可调用单元契约）"
                )
        entry_position = item.get("position", "end")
        if entry_position not in POSITION_VALUES:
            raise ManifestError(f"{entry_where}.position 非法: {entry_position!r}（start | end）")
        entries.append(
            MenuEntrySpec(
                id=entry_id,
                label=entry_label,
                href=href,
                label_i18n=entry_label_i18n,
                function=function,
                position=entry_position,
            )
        )
    return MenuSpec(
        label=label,
        icon=icon,
        position=position,
        join=join,
        label_i18n=label_i18n,
        entries=tuple(entries),
    )


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
    menu = _parse_menu(ui.get("menu"), {t.name: t for t in tools}, plugin_id)

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
        menu=menu,
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
