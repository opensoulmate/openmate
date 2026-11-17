"""
插件锻造坊 — 自我开发闭环（SoulMate 自产插件）

设计文档: docs/plugin-system-design.md §7
用户需求 → SoulMate 生成插件草稿（plugin.yaml + main.py + ui/*.json [+ tests/]）
→ dry-run 校验（清单/DSL/流程/entry handler/自带测试）→ 用户确认安装（铁律：
生成≠安装，装载器不自动安装任何插件）→ 装载（菜单长出来+页面渲染+工具进对话）
→ 沉淀为常驻入口。

- 草稿落 plugins/.drafts/<id>/（点目录，scanner 跳过；确认前不进 /plugins）
- dry-run 不过不能安装（install 内部重跑 dry-run，不信任旧报告）
- 支持模板脚手架（demo 级：菜单+页面+工具）与 LLM 自写文件两种草稿来源
- 装载后热注册：工具进 MCP 桥、页面进 /plugins/{id}/ui/{page}、流程进流程引擎
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from plugin.yaml_manifest import ManifestError, load_yaml_manifest

logger = logging.getLogger("plugin.plugin_forge")

DRAFTS_DIRNAME = ".drafts"
ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
ICON_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")
MAX_DRY_RUN_TEST_SECONDS = 120   # dry-run 自带测试的等待上限（超时显式报因，不静默）


class ForgeError(ValueError):
    """锻造坊操作失败（草稿不存在/校验不过/未确认安装）"""


# ---- 脚手架模板（demo 级：菜单 + 页面 + 工具）----

def scaffold_plugin(spec: dict[str, Any]) -> dict[str, str]:
    """按功能描述生成插件三件套 + 自带测试：{相对路径: 文件内容}"""
    plugin_id = str(spec.get("id", "")).strip()
    if not ID_PATTERN.match(plugin_id):
        raise ForgeError(f"id 非法: {plugin_id!r}（^[a-z][a-z0-9-]{{1,63}}$）")
    name = str(spec.get("name") or plugin_id).strip()
    description = str(spec.get("description") or f"{name}（SoulMate 生成）").strip()
    feature = str(spec.get("feature") or name).strip()
    item_label = str(spec.get("item_label") or "记录").strip()
    icon = str(spec.get("icon") or "Puzzle").strip()
    if not ICON_PATTERN.match(icon):
        raise ForgeError(f"icon 非法: {icon!r}（PascalCase lucide 图标名）")

    tool_prefix = plugin_id.replace("-", "_")
    manifest = f"""id: {plugin_id}
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
  - data.write
ui:
  nav:
    label: {name}
    icon: {icon}
    position: end
  pages:
    - id: home
      title: {name}工作台
      dsl: ui/home.json
tools:
  - name: save_item
    description: 保存一条{item_label}
    handler: handle_save_item
    permissions: [tool.call, data.write]
    approval: never
    input_schema:
      type: object
      properties:
        content:
          type: string
          description: {item_label}内容
        tag:
          type: string
          description: 标签（可选）
      required: [content]
  - name: list_items
    description: 查询{item_label}列表
    handler: handle_list_items
    permissions: [tool.call, data.read]
    approval: never
    input_schema:
      type: object
      properties:
        limit:
          type: integer
          description: 返回条数上限
flows: []
"""
    main_py = f'''"""
{name} — SoulMate 生成的插件（功能：{feature}）

三件套之二：工具 handler（UI 面板与对话共用同一份 handler，经 MCP 工具注册桥）。
数据落 ctx.store_dir()（plugin-data/{plugin_id}/，跨进程持久）。
"""

from __future__ import annotations

import json
import time
from typing import Any

_STORE_NAME = "items.json"


def _store_path(ctx: Any):
    store_dir = ctx.store_dir()
    return store_dir / _STORE_NAME


def _load(ctx: Any) -> list[dict[str, Any]]:
    path = _store_path(ctx)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(ctx: Any, items: list[dict[str, Any]]) -> None:
    path = _store_path(ctx)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def handle_save_item(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：保存一条{item_label}"""
    content = str(args.get("content", "")).strip()
    if not content:
        return {{"error": "content 不能为空"}}
    entry = {{
        "id": f"item_{{int(time.time() * 1000)}}",
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "content": content,
        "tag": str(args.get("tag", "")),
    }}
    items = _load(ctx)
    items.append(entry)
    _save(ctx, items)
    ctx.logger.info("{plugin_id} 保存{item_label}: %s", content[:50])
    return entry


def handle_list_items(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    """MCP 工具：查询{item_label}列表（list 面板数据源）"""
    limit = int(args.get("limit", 20))
    items = _load(ctx)
    window = items[-limit:]
    return {{"items": window, "count": len(items)}}
'''
    home_json = {
        "version": 1,
        "page": {
            "title": f"{name}工作台",
            "description": f"{feature}。表单保存、列表查询、结果汇总——面板与对话共用同一工具。",
        },
        "panels": [
            {
                "type": "form",
                "id": "save-form",
                "title": f"保存{item_label}",
                "fields": [
                    {"name": "content", "label": f"{item_label}内容", "widget": "textarea",
                     "required": True, "placeholder": f"输入{item_label}内容"},
                    {"name": "tag", "label": "标签", "widget": "text", "placeholder": "可选"},
                ],
                "submit": {
                    "label": "保存",
                    "tool": f"{plugin_id}__save_item",
                    "target_panel": "save-result",
                },
            },
            {
                "type": "list",
                "id": "item-log",
                "title": f"{item_label}记录",
                "columns": [
                    {"key": "time", "label": "时间"},
                    {"key": "content", "label": "内容"},
                    {"key": "tag", "label": "标签"},
                ],
                "source": {"tool": f"{plugin_id}__list_items", "args": {"limit": 20}},
                "empty_text": f"暂无{item_label}",
            },
            {
                "type": "report",
                "id": "save-result",
                "title": "最新结果",
                "sections": [
                    {"kind": "text", "title": "说明",
                     "content": "保存成功后这里显示结果；也可以直接在对话里让 SoulMate 调用同一工具。"},
                ],
            },
        ],
    }
    tests_py = f'''"""{name} 插件自带测试（dry-run 会跑；不过不能装）"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load_entry():
    spec = importlib.util.spec_from_file_location("test_entry_{tool_prefix}", PLUGIN_DIR / "main.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Ctx:
    plugin_id = "{plugin_id}"
    logger = __import__("logging").getLogger("test")

    def store_dir(self):
        if not hasattr(self, "_store"):
            import tempfile
            from pathlib import Path
            self._store = Path(tempfile.mkdtemp(prefix="plugin-store-"))
        return self._store


def test_manifest_and_dsl():
    from plugin.ui_dsl import validate_dsl_page
    from plugin.yaml_manifest import load_yaml_manifest

    manifest = load_yaml_manifest(PLUGIN_DIR)
    assert manifest.id == "{plugin_id}"
    page = json.loads((PLUGIN_DIR / "ui" / "home.json").read_text(encoding="utf-8"))
    validate_dsl_page(page, source="home")


def test_handlers_roundtrip():
    module = _load_entry()
    ctx = _Ctx()
    saved = module.handle_save_item({{"content": "测试{item_label}", "tag": "t"}}, ctx)
    assert saved["content"] == "测试{item_label}"
    listed = module.handle_list_items({{"limit": 5}}, ctx)
    assert listed["count"] >= 1
'''
    return {
        "plugin.yaml": manifest,
        "main.py": main_py,
        "ui/home.json": json.dumps(home_json, ensure_ascii=False, indent=2),
        "tests/test_plugin.py": tests_py,
    }


# ---- 草稿管理 ----

class PluginForge:
    """草稿目录管理 + dry-run 校验 + 确认安装 + 热装载"""

    def __init__(self, plugins_dir: Path | str):
        self.plugins_dir = Path(plugins_dir)
        self.drafts_dir = self.plugins_dir / DRAFTS_DIRNAME

    # 草稿
    def draft(self, spec: dict[str, Any] | None = None,
              files: dict[str, str] | None = None) -> dict[str, Any]:
        """生成/覆盖草稿（模板脚手架或 LLM 自写文件），随即 dry-run"""
        if files:
            manifest_raw = files.get("plugin.yaml", "")
            plugin_id = str((spec or {}).get("id", "")).strip()
            if not plugin_id:
                for line in manifest_raw.splitlines():
                    if line.startswith("id:"):
                        plugin_id = line.split(":", 1)[1].strip()
                        break
            content = dict(files)
        else:
            if not spec:
                raise ForgeError("draft 需要 spec 或 files")
            plugin_id = str(spec.get("id", "")).strip()
            content = scaffold_plugin(spec)
        if not ID_PATTERN.match(plugin_id):
            raise ForgeError(f"id 非法: {plugin_id!r}")
        draft_dir = self.drafts_dir / plugin_id
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        for rel, text in content.items():
            rel_path = Path(rel)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                raise ForgeError(f"非法文件路径: {rel!r}")
            target = draft_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        report = self.dry_run(plugin_id)
        return {"draft_id": plugin_id, "dir": str(draft_dir),
                "files": sorted(content), "dry_run": report}

    def list_drafts(self) -> list[dict[str, Any]]:
        if not self.drafts_dir.is_dir():
            return []
        result = []
        for draft_dir in sorted(self.drafts_dir.iterdir()):
            if not draft_dir.is_dir():
                continue
            report_path = draft_dir / "dry-run-report.json"
            report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
            result.append({
                "draft_id": draft_dir.name,
                "files": sorted(str(p.relative_to(draft_dir)) for p in draft_dir.rglob("*") if p.is_file()),
                "dry_run_ok": bool(report and report.get("ok")),
            })
        return result

    def read_draft(self, plugin_id: str) -> dict[str, Any]:
        draft_dir = self._draft_dir(plugin_id)
        files = {
            str(p.relative_to(draft_dir)): p.read_text(encoding="utf-8")
            for p in sorted(draft_dir.rglob("*")) if p.is_file()
            and p.name != "dry-run-report.json"
        }
        report_path = draft_dir / "dry-run-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
        return {"draft_id": plugin_id, "files": files, "dry_run": report}

    def delete_draft(self, plugin_id: str) -> None:
        draft_dir = self._draft_dir(plugin_id)
        shutil.rmtree(draft_dir)

    def _draft_dir(self, plugin_id: str) -> Path:
        if not ID_PATTERN.match(plugin_id):
            raise ForgeError(f"id 非法: {plugin_id!r}")
        draft_dir = self.drafts_dir / plugin_id
        if not draft_dir.is_dir():
            raise ForgeError(f"草稿不存在: {plugin_id}")
        return draft_dir

    # dry-run 校验（装载器能力前置：清单/DSL/流程/entry/自带测试）
    def dry_run(self, plugin_id: str) -> dict[str, Any]:
        draft_dir = self._draft_dir(plugin_id)
        checks: list[dict[str, Any]] = []

        def record(name: str, ok: bool, detail: str) -> None:
            checks.append({"check": name, "ok": ok, "detail": detail[:1000]})

        # ① plugin.yaml 清单
        try:
            manifest = load_yaml_manifest(draft_dir)
            record("manifest", True,
                   f"id={manifest.id} v{manifest.version} pages={len(manifest.pages)} tools={len(manifest.tools)}")
        except (ManifestError, OSError) as e:
            record("manifest", False, str(e))
            report = {"ok": False, "checks": checks, "error": f"清单校验失败: {e}"}
            self._save_report(draft_dir, report)
            return report

        # ② UI DSL 页面
        for page in manifest.pages:
            if page.dsl is None:
                record(f"dsl:{page.id}", True, "HTML 面板（层②预留）")
                continue
            try:
                from plugin.ui_dsl import validate_dsl_page

                raw = json.loads((draft_dir / page.dsl).read_text(encoding="utf-8"))
                validate_dsl_page(raw, source=f"{plugin_id}/ui/{page.id}")
                record(f"dsl:{page.id}", True, f"{page.dsl} 校验通过")
            except Exception as e:  # noqa: BLE001 — dry-run 逐项记录
                record(f"dsl:{page.id}", False, f"{page.dsl}: {e}")

        # ③ 流程定义
        for rel in manifest.flows:
            try:
                from plugin.flow_def import load_flow_file

                defn = load_flow_file(draft_dir / rel)
                record(f"flow:{defn.id}", True, f"{rel} 校验通过（{len(defn.nodes)} 节点）")
            except Exception as e:  # noqa: BLE001
                record(f"flow:{rel}", False, f"{rel}: {e}")

        # ④ entry 模块 + handler 可调用
        try:
            module = self._load_entry(draft_dir, manifest.entry, plugin_id)
            missing = [t.name for t in manifest.tools if not callable(getattr(module, t.handler, None))]
            if missing:
                record("entry", False, f"entry 缺少 handler: {missing}")
            else:
                record("entry", True, f"handlers 全部可调用（{len(manifest.tools)} 个）")
        except Exception as e:  # noqa: BLE001
            record("entry", False, f"entry 加载失败: {e}")

        # ⑤ 插件自带测试（设计 §7：dry-run 不过不能提交确认）
        test_report = self._run_tests(draft_dir)
        record("tests", test_report["ok"], test_report["detail"])

        ok = all(item["ok"] for item in checks)
        report = {
            "ok": ok,
            "checks": checks,
            "test_output_tail": test_report.get("output_tail", ""),
            "error": "" if ok else "dry-run 未通过（见 checks）",
        }
        self._save_report(draft_dir, report)
        return report

    def _load_entry(self, draft_dir: Path, entry: str, plugin_id: str):
        module_name = f"plugin_draft_{plugin_id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, draft_dir / entry)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"entry 无法加载: {entry}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run_tests(self, draft_dir: Path) -> dict[str, Any]:
        tests_dir = draft_dir / "tests"
        if not tests_dir.is_dir():
            return {"ok": True, "detail": "无自带测试（建议带上）", "output_tail": ""}
        python = os.environ.get("PLUGIN_FORGE_PYTHON") or sys.executable
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)  # plugin 包可导入
        try:
            proc = subprocess.run(
                [python, "-m", "pytest", "tests", "-q", "--no-header", "-p", "no:cacheprovider"],
                cwd=str(draft_dir), env=env, capture_output=True, text=True,
                timeout=MAX_DRY_RUN_TEST_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "detail": f"自带测试超时（>{MAX_DRY_RUN_TEST_SECONDS}s），已终止测试子进程——请简化测试",
                "output_tail": "",
            }
        except OSError as e:
            return {"ok": False, "detail": f"测试子进程无法启动: {e}", "output_tail": ""}
        output = (proc.stdout or "") + (proc.stderr or "")
        tail = output[-2000:]
        return {
            "ok": proc.returncode == 0,
            "detail": f"pytest exit={proc.returncode}",
            "output_tail": tail,
        }

    @staticmethod
    def _save_report(draft_dir: Path, report: dict[str, Any]) -> None:
        (draft_dir / "dry-run-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    # 安装（必须用户确认——铁律：生成≠安装）
    def install(self, plugin_id: str, *, confirmed: bool,
                hot_load: Callable[[str], dict[str, Any]] | None = None) -> dict[str, Any]:
        """草稿 → plugins/<id>（确认后）。hot_load 负责热注册（工具/路由/流程）。

        未确认时只返回确认所需信息（清单 diff 摘要+权限列表），不落任何文件。
        """
        draft_dir = self._draft_dir(plugin_id)
        manifest_preview = self._preview_manifest(draft_dir)
        if not confirmed:
            return {
                "installed": False,
                "needs_confirmation": True,
                "draft_id": plugin_id,
                "manifest": manifest_preview,
                "message": "安装需要用户确认（铁律：生成≠安装）。确认后带 confirmed=true 再次调用。",
            }
        report = self.dry_run(plugin_id)   # 不信任旧报告：安装前重跑
        if not report["ok"]:
            raise ForgeError(f"dry-run 未通过，拒绝安装: {plugin_id}")
        target = self.plugins_dir / plugin_id
        if target.exists():
            raise ForgeError(f"插件已存在: {plugin_id}（升级请先显式卸载）")
        shutil.copytree(draft_dir, target)
        shutil.rmtree(draft_dir)
        grown = hot_load(plugin_id) if hot_load else {}
        logger.info("插件已安装: %s（菜单/页面/工具已长出）", plugin_id)
        return {
            "installed": True,
            "draft_id": plugin_id,
            "path": str(target),
            "manifest": manifest_preview,
            "grown": grown,
        }

    @staticmethod
    def _preview_manifest(draft_dir: Path) -> dict[str, Any]:
        try:
            manifest = load_yaml_manifest(draft_dir)
        except ManifestError as e:
            raise ForgeError(f"草稿清单非法: {e}") from e
        return {
            "id": manifest.id,
            "name": manifest.name,
            "version": manifest.version,
            "description": manifest.description,
            "permissions": list(manifest.permissions),
            "nav": {"label": manifest.nav.label, "icon": manifest.nav.icon} if manifest.nav else None,
            "pages": [{"id": p.id, "title": p.title} for p in manifest.pages],
            "tools": [
                {"name": f"{manifest.id}__{t.name}", "description": t.description,
                 "approval": t.approval, "permissions": list(t.permissions)}
                for t in manifest.tools
            ],
        }
