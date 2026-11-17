"""
UI DSL 校验 — 声明式 UI DSL（层①）的宿主侧 schema

设计文档: docs/plugin-system-design.md §2
最小集三种面板：form（表单）/ list（列表）/ report（报告）。
数据绑定唯一来源是 MCP 工具（source.tool / submit.tool）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

TOOL_NAME_FULL_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}__[a-z][a-z0-9_-]{0,63}$")
PANEL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
WIDGETS = {"text", "textarea", "number", "select", "checkbox", "date"}
SECTION_KINDS = {"kv", "text", "bars"}


class DslError(ValueError):
    """UI DSL 校验失败（消息带字段路径）"""


@dataclass(frozen=True)
class FormField:
    name: str
    label: str
    widget: str = "text"
    required: bool = False
    placeholder: str = ""
    default: Any = None
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class FormPanel:
    id: str
    title: str
    fields: tuple[FormField, ...]
    submit_label: str = "提交"
    submit_tool: str = ""
    target_panel: str = ""
    kind: str = "form"


@dataclass(frozen=True)
class ListPanel:
    id: str
    title: str
    columns: tuple[tuple[str, str], ...]  # (key, label)
    source_tool: str = ""
    source_args: dict[str, Any] = field(default_factory=dict)
    empty_text: str = "暂无数据"
    kind: str = "list"


@dataclass(frozen=True)
class ReportSection:
    kind: str  # kv | text | bars
    title: str = ""
    content: str = ""
    items: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class ReportPanel:
    id: str
    title: str
    sections: tuple[ReportSection, ...]
    kind: str = "report"


Panel = FormPanel | ListPanel | ReportPanel


@dataclass(frozen=True)
class DslPage:
    version: int
    title: str
    description: str
    panels: tuple[Panel, ...]


def _check_tool_name(value: str, where: str) -> str:
    if not isinstance(value, str) or not TOOL_NAME_FULL_PATTERN.match(value):
        raise DslError(f"{where} 工具名非法: {value!r}（应为 <plugin-id>__<tool-name>）")
    return value


def _check_panel_id(value: str, where: str) -> str:
    if not isinstance(value, str) or not PANEL_ID_PATTERN.match(value):
        raise DslError(f"{where} 面板 id 非法: {value!r}")
    return value


def _parse_form(panel_id: str, title: str, data: dict, where: str) -> FormPanel:
    raw_fields = data.get("fields", [])
    if not isinstance(raw_fields, list):
        raise DslError(f"{where}.fields 应为列表")
    fields: list[FormField] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_fields):
        fw = f"{where}.fields[{i}]"
        if not isinstance(item, dict):
            raise DslError(f"{fw} 应为映射")
        name = item.get("name")
        if not isinstance(name, str) or not name or name in seen:
            raise DslError(f"{fw}.name 非法或重复: {name!r}")
        seen.add(name)
        label = item.get("label", name)
        if not isinstance(label, str):
            raise DslError(f"{fw}.label 应为字符串")
        widget = item.get("widget", "text")
        if widget not in WIDGETS:
            raise DslError(f"{fw}.widget 非法: {widget!r}（{sorted(WIDGETS)}）")
        options: list[tuple[str, str]] = []
        if widget == "select":
            raw_options = item.get("options", [])
            if not isinstance(raw_options, list) or not raw_options:
                raise DslError(f"{fw} select 字段需非空 options 列表")
            for j, opt in enumerate(raw_options):
                if not isinstance(opt, dict) or not isinstance(opt.get("value"), str) or not isinstance(opt.get("label"), str):
                    raise DslError(f"{fw}.options[{j}] 需为 {{value, label}} 映射")
                options.append((opt["value"], opt["label"]))
        fields.append(
            FormField(
                name=name,
                label=label,
                widget=widget,
                required=bool(item.get("required", False)),
                placeholder=str(item.get("placeholder", "")),
                default=item.get("default"),
                options=tuple(options),
            )
        )
    submit = data.get("submit", {})
    if not isinstance(submit, dict):
        raise DslError(f"{where}.submit 应为映射")
    submit_label = submit.get("label", "提交")
    if not isinstance(submit_label, str):
        raise DslError(f"{where}.submit.label 应为字符串")
    submit_tool = _check_tool_name(submit.get("tool", ""), f"{where}.submit")
    target_panel = submit.get("target_panel", "")
    if target_panel:
        _check_panel_id(target_panel, f"{where}.submit.target_panel")
    return FormPanel(
        id=panel_id,
        title=title,
        fields=tuple(fields),
        submit_label=submit_label,
        submit_tool=submit_tool,
        target_panel=str(target_panel),
    )


def _parse_list(panel_id: str, title: str, data: dict, where: str) -> ListPanel:
    raw_columns = data.get("columns", [])
    if not isinstance(raw_columns, list) or not raw_columns:
        raise DslError(f"{where}.columns 应为非空列表")
    columns: list[tuple[str, str]] = []
    for i, col in enumerate(raw_columns):
        cw = f"{where}.columns[{i}]"
        if not isinstance(col, dict) or not isinstance(col.get("key"), str) or not isinstance(col.get("label"), str):
            raise DslError(f"{cw} 需为 {{key, label}} 映射")
        columns.append((col["key"], col["label"]))
    source = data.get("source", {})
    if not isinstance(source, dict):
        raise DslError(f"{where}.source 应为映射")
    source_tool = source.get("tool", "")
    if source_tool:
        _check_tool_name(source_tool, f"{where}.source")
    source_args = source.get("args", {})
    if not isinstance(source_args, dict):
        raise DslError(f"{where}.source.args 应为映射")
    empty_text = data.get("empty_text", "暂无数据")
    if not isinstance(empty_text, str):
        raise DslError(f"{where}.empty_text 应为字符串")
    return ListPanel(
        id=panel_id,
        title=title,
        columns=tuple(columns),
        source_tool=str(source_tool),
        source_args=source_args,
        empty_text=empty_text,
    )


def _parse_report(panel_id: str, title: str, data: dict, where: str) -> ReportPanel:
    raw_sections = data.get("sections", [])
    if not isinstance(raw_sections, list):
        raise DslError(f"{where}.sections 应为列表")
    sections: list[ReportSection] = []
    for i, sec in enumerate(raw_sections):
        sw = f"{where}.sections[{i}]"
        if not isinstance(sec, dict):
            raise DslError(f"{sw} 应为映射")
        kind = sec.get("kind")
        if kind not in SECTION_KINDS:
            raise DslError(f"{sw}.kind 非法: {kind!r}（{sorted(SECTION_KINDS)}）")
        sec_title = sec.get("title", "")
        if not isinstance(sec_title, str):
            raise DslError(f"{sw}.title 应为字符串")
        content = ""
        items: list[tuple[str, Any]] = []
        if kind == "text":
            content = sec.get("content", "")
            if not isinstance(content, str):
                raise DslError(f"{sw}.content 应为字符串")
        else:
            raw_items = sec.get("items", [])
            if not isinstance(raw_items, list):
                raise DslError(f"{sw}.items 应为列表")
            for j, item in enumerate(raw_items):
                iw = f"{sw}.items[{j}]"
                if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                    raise DslError(f"{iw} 需为含 label 的映射")
                if kind == "bars" and not isinstance(item.get("value"), (int, float)):
                    raise DslError(f"{iw}.value 应为数值（bars）")
                items.append((item["label"], item.get("value")))
        sections.append(ReportSection(kind=kind, title=sec_title, content=content, items=tuple(items)))
    return ReportPanel(id=panel_id, title=title, sections=tuple(sections))


def validate_dsl_page(data: Any, *, source: str = "<dsl>") -> DslPage:
    """校验 UI DSL 页面定义，返回强类型 DslPage；失败抛 DslError"""
    where = source
    if not isinstance(data, dict):
        raise DslError(f"{where} 顶层应为映射")
    version = data.get("version")
    if version != 1:
        raise DslError(f"{where}.version 应为 1，实际 {version!r}")
    page = data.get("page", {})
    if not isinstance(page, dict):
        raise DslError(f"{where}.page 应为映射")
    title = page.get("title", "")
    if not isinstance(title, str):
        raise DslError(f"{where}.page.title 应为字符串")
    description = page.get("description", "")
    if not isinstance(description, str):
        raise DslError(f"{where}.page.description 应为字符串")
    raw_panels = data.get("panels", [])
    if not isinstance(raw_panels, list) or not raw_panels:
        raise DslError(f"{where}.panels 应为非空列表")
    panels: list[Panel] = []
    seen: set[str] = set()
    for i, item in enumerate(raw_panels):
        pw = f"{where}.panels[{i}]"
        if not isinstance(item, dict):
            raise DslError(f"{pw} 应为映射")
        panel_id = _check_panel_id(item.get("id", ""), pw)
        if panel_id in seen:
            raise DslError(f"{pw}.id 重复: {panel_id!r}")
        seen.add(panel_id)
        title_p = item.get("title", panel_id)
        if not isinstance(title_p, str):
            raise DslError(f"{pw}.title 应为字符串")
        kind = item.get("type")
        if kind == "form":
            panels.append(_parse_form(panel_id, title_p, item, pw))
        elif kind == "list":
            panels.append(_parse_list(panel_id, title_p, item, pw))
        elif kind == "report":
            panels.append(_parse_report(panel_id, title_p, item, pw))
        else:
            raise DslError(f"{pw}.type 非法: {kind!r}（form | list | report）")
    # form 的 target_panel 必须指向已声明面板
    declared = seen
    for panel in panels:
        if isinstance(panel, FormPanel) and panel.target_panel and panel.target_panel not in declared:
            raise DslError(f"面板 {panel.id} 的 submit.target_panel 未声明: {panel.target_panel!r}")
    return DslPage(version=1, title=title, description=description, panels=tuple(panels))
