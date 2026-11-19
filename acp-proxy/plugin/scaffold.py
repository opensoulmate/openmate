# -*- coding: utf-8 -*-
"""
插件脚手架 — 标准骨架一键起插件（docs/plugin-spec.md §脚手架）

防手写出错：生成即合规（产出后立刻过 spec_validator 校验闸门）；
SoulMate 自创建插件接口：本模块 = 纯函数 generate_skeleton()，后续创建对话流直接调用。

CLI:
  python -m plugin.scaffold demo-greeter --name "示例插件" --label "示例域"
  python -m plugin.scaffold my-flow --name "流程插件" --join automation --entries home:流程面板
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from plugin.spec_validator import validate_plugin_dir

ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

MANIFEST_TMPL = """\
id: {id}
name: {name}
version: 0.1.0
description: {description}
author: soulmate
entry: main.py
permissions:
  - ui.serve
  - tool.register
  - tool.call
  - data.read
ui:
  menu:
    label: {label}
    label_i18n:
      zh: {label}
      en: {label_en}
      ja: {label_ja}
    icon: Puzzle
    position: end
{join_line}    entries:
{entries_yaml}
  pages:
{pages_yaml}
tools:
{tools_yaml}
flows: []
"""

ENTRY_TMPL = """\
      - id: {id}
        label: {label}
        label_i18n: {{zh: {label}, en: {label}, ja: {label}}}
        href: /plugins/{plugin_id}/{id}
        function: {id}_run
"""

TOOL_TMPL = """\
  - name: {id}_run
    description: {label}（工作流可调用单元：function id + IO schema 契约）
    handler: handle_{id}_run
    permissions: [tool.call]
    approval: never
    input_schema:
      type: object
      properties:
        query:
          type: string
          description: 输入参数
    output_schema:
      type: object
      properties:
        ok:
          type: boolean
          description: 是否成功
        data:
          type: object
          description: 结果数据
"""

MAIN_TMPL = '''\
"""{name} — 插件入口（MCP 工具 handler；对话与 UI 共用同一 handler）"""

from __future__ import annotations

from typing import Any


{handlers}
'''

HANDLER_TMPL = '''\
def handle_{id}_run(args: dict[str, Any]) -> dict[str, Any]:
    """{label} 工具 handler（示例实现——替换为真实业务）"""
    return {{"ok": True, "data": {{"echo": args.get("query", "")}}}}
'''

PAGE_TMPL = (
    '{\n'
    '  "version": 1,\n'
    '  "page": {\n'
    '    "title": "@@LABEL@@",\n'
    '    "description": "@@LABEL@@"\n'
    '  },\n'
    '  "panels": [\n'
    '    {\n'
    '      "type": "form",\n'
    '      "id": "@@ID@@-form",\n'
    '      "title": "@@LABEL@@",\n'
    '      "fields": [\n'
    '        {"name": "query", "label": "输入", "widget": "text"}\n'
    '      ],\n'
    '      "submit": {\n'
    '        "label": "执行",\n'
    '        "tool": "@@PLUGIN@@__@@ID@@_run",\n'
    '        "args": {"query": "$query"}\n'
    '      }\n'
    '    }\n'
    '  ]\n'
    '}\n'
)


def generate_skeleton(
    plugin_id: str,
    *,
    name: str,
    label: str,
    description: str = "",
    join: str | None = None,
    entries: list[tuple[str, str]] | None = None,
    plugins_dir: Path | str = "plugins",
) -> dict:
    """生成标准骨架并过校验闸门（生成≠安装：只产文件，装入走市场/forge 确认链）"""
    if not ID_PATTERN.match(plugin_id):
        raise ValueError(f"插件 id 非法: {plugin_id!r}（^[a-z][a-z0-9-]{{1,63}}$）")
    entries = entries or [(plugin_id.replace("-", "_"), label)]
    plugins_dir = Path(plugins_dir)
    target = plugins_dir / plugin_id
    if target.exists():
        raise FileExistsError(f"插件目录已存在: {target}")

    entries_yaml = "".join(
        ENTRY_TMPL.format(id=eid, label=elabel, plugin_id=plugin_id)
        for eid, elabel in entries
    )
    pages_yaml = "".join(
        f"    - id: {eid}\n      title: {elabel}\n      dsl: ui/{eid}.json\n"
        for eid, elabel in entries
    )
    tools_yaml = "\n".join(
        TOOL_TMPL.format(id=eid, label=elabel).rstrip("\n")
        for eid, elabel in entries
    )
    manifest = MANIFEST_TMPL.format(
        id=plugin_id,
        name=name,
        description=description or f"{name}（{label}）",
        label=label,
        label_en=label,
        label_ja=label,
        join_line=f"    join: {join}\n" if join else "",
        entries_yaml=entries_yaml.rstrip("\n"),
        pages_yaml=pages_yaml.rstrip("\n"),
        tools_yaml=tools_yaml,
    )
    handlers = "\n\n".join(
        HANDLER_TMPL.format(id=eid, label=elabel) for eid, elabel in entries
    )
    files = {
        "plugin.yaml": manifest,
        "main.py": MAIN_TMPL.format(name=name, handlers=handlers),
        "README.md": f"# {name}\n\n{description or label}\n\n"
                     f"规范：docs/plugin-spec.md · 校验：`python -m plugin.spec_validator {target}`\n",
    }
    for eid, elabel in entries:
        files[f"ui/{eid}.json"] = (
            PAGE_TMPL.replace("@@LABEL@@", elabel).replace("@@ID@@", eid)
            .replace("@@PLUGIN@@", plugin_id)
        )

    target.mkdir(parents=True)
    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    summary = validate_plugin_dir(target)   # 生成即过闸门（不过=删目录报错）
    return {"dir": str(target), "files": sorted(files), "validated": summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="插件标准骨架生成器（docs/plugin-spec.md）")
    parser.add_argument("id", help="插件 id（小写连字符，=目录名）")
    parser.add_argument("--name", required=True, help="显示名")
    parser.add_argument("--label", required=True, help="一级菜单名（功能域名）")
    parser.add_argument("--description", default="", help="一句话描述")
    parser.add_argument("--join", default=None, help="并入既有功能域（内置域 id 或插件 id）")
    parser.add_argument("--entries", nargs="*", default=None,
                        help="二级条目 id:标签（缺省一条 <id>_entry:<label>）")
    parser.add_argument("--dir", default="plugins", help="插件根目录（默认 plugins/）")
    args = parser.parse_args(argv)

    entries = [tuple(e.split(":", 1)) for e in args.entries] if args.entries else None
    try:
        result = generate_skeleton(
            args.id,
            name=args.name,
            label=args.label,
            description=args.description,
            join=args.join,
            entries=entries,  # type: ignore[arg-type]
            plugins_dir=args.dir,
        )
    except (ValueError, FileExistsError, Exception) as e:  # noqa: BLE001 — 校验错误也在此回显
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
