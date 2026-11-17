"""
插件目录扫描器 — plugin.yaml 清单式插件的装载入口

设计文档: docs/plugin-system-design.md §1
- 扫描 plugins/<id>/plugin.yaml → PluginDescriptor（解析失败记 errors 不中断）
- entry 模块按文件路径加载（不污染 sys.path）
- 导航注册表 + 工具注册（经 mcp_bridge）
"""

from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from plugin.mcp_bridge import PluginToolRegistry
from plugin.yaml_manifest import ManifestError, YamlPluginManifest, load_yaml_manifest

logger = logging.getLogger("plugin.scanner")

# 跳过的目录（数据目录/缓存/隐藏目录不是插件）
SKIP_DIRS = {"data", "__pycache__", "node_modules"}


@dataclass
class PluginDescriptor:
    """一个 plugin.yaml 插件的装载描述符"""

    manifest: YamlPluginManifest
    dir_path: Path
    entry_path: Path
    state: str = "enabled"
    module: Any = None
    errors: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return self.manifest.id

    def page_path(self, page_id: str) -> Path | None:
        for page in self.manifest.pages:
            if page.id == page_id:
                rel = page.dsl or page.html
                return self.dir_path / rel if rel else None
        return None

    def nav_item(self) -> dict[str, Any] | None:
        """导航项（前端 plugin-nav.ts 消费形状）"""
        nav = self.manifest.nav
        if nav is None or self.state != "enabled":
            return None
        return {
            "href": f"/plugins/{self.id}",
            "label": nav.label,
            "icon": nav.icon,
            "pluginId": self.id,
            "position": nav.position,
        }


@dataclass
class ScanResult:
    descriptors: list[PluginDescriptor] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)

    def get(self, plugin_id: str) -> PluginDescriptor | None:
        for descriptor in self.descriptors:
            if descriptor.id == plugin_id:
                return descriptor
        return None


def scan_plugins(plugins_dir: Path | str) -> ScanResult:
    """扫描插件目录，返回描述符与逐插件错误（单插件失败不影响其它插件）"""
    plugins_dir = Path(plugins_dir)
    result = ScanResult()
    if not plugins_dir.is_dir():
        return result
    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        if plugin_dir.name.startswith(".") or plugin_dir.name in SKIP_DIRS:
            continue
        if not (plugin_dir / "plugin.yaml").is_file():
            continue  # plugin.json 旧式插件由 app.py::load_plugins 原路径处理
        try:
            manifest = load_yaml_manifest(plugin_dir)
        except ManifestError as e:
            logger.warning("插件 %s 清单校验失败: %s", plugin_dir.name, e)
            result.errors.append({"plugin": plugin_dir.name, "error": str(e)})
            continue
        descriptor = PluginDescriptor(
            manifest=manifest,
            dir_path=plugin_dir,
            entry_path=plugin_dir / manifest.entry,
        )
        result.descriptors.append(descriptor)
        logger.info(
            "插件已扫描: %s v%s (pages=%d, tools=%d)",
            manifest.id, manifest.version, len(manifest.pages), len(manifest.tools),
        )
    return result


def load_entry_module(descriptor: PluginDescriptor) -> Any:
    """按文件路径加载插件 entry 模块（模块名 plugin_ext_<id>，可重复调用复用缓存）"""
    if descriptor.module is not None:
        return descriptor.module
    module_name = f"plugin_ext_{descriptor.id.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, descriptor.entry_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"插件 {descriptor.id} entry 无法加载: {descriptor.entry_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    descriptor.module = module
    return module


def bind_tools(descriptor: PluginDescriptor, registry: PluginToolRegistry) -> int:
    """把清单 tools[] 绑定到 entry 模块 handler 并注册进 MCP 工具注册桥

    返回注册的工具数量；要求插件声明 tool.register 权限（否则整体拒绝）。
    """
    if not descriptor.manifest.tools:
        return 0
    manifest_permissions = set(descriptor.manifest.permissions)
    module = load_entry_module(descriptor)
    count = 0
    for spec in descriptor.manifest.tools:
        handler = getattr(module, spec.handler, None)
        if handler is None:
            raise RuntimeError(f"插件 {descriptor.id} entry 缺少 handler: {spec.handler}")
        registry.register(descriptor.id, spec, handler, manifest_permissions,
                          data_dir=str(descriptor.dir_path))
        count += 1
    return count


def collect_nav(result: ScanResult) -> list[dict[str, Any]]:
    """收集全部启用插件的导航项（前端底部导航由此长出）"""
    nav_items = []
    for descriptor in result.descriptors:
        item = descriptor.nav_item()
        if item is not None:
            nav_items.append(item)
    return nav_items
