# -*- coding: utf-8 -*-
"""
插件安装状态 — "默认未安装，谁使用谁安装"（用户 2026-11-19 架构原则）

- 状态文件：plugins/install-state.json（随仓库提交 = 默认安装态的声明）
  形状：{"<plugin-id>": true|false}   true=已安装（默认），false=可用未安装
- 缺省语义：状态文件里没有的插件 = **已安装**（存量插件零迁移）。
- 安装/卸载 = 热装载/热卸载 + 状态翻转；插件包目录不动（能力零删除，归位不砍杀）。
- 创建/安装双侧校验（docs/plugin-spec.md）：状态翻转只由 market/forge API 触发。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

STATE_FILENAME = "install-state.json"


def state_path(plugins_dir: Path | str) -> Path:
    return Path(plugins_dir) / STATE_FILENAME


def load_state(plugins_dir: Path | str) -> dict[str, bool]:
    path = state_path(plugins_dir)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): bool(v) for k, v in raw.items()}


def is_installed(plugins_dir: Path | str, plugin_id: str) -> bool:
    """缺省 = 已安装（只有显式 false 才是可用未安装）"""
    return load_state(plugins_dir).get(plugin_id, True)


def set_installed(plugins_dir: Path | str, plugin_id: str, installed: bool) -> dict[str, bool]:
    """原子写状态文件（临时文件 + replace），返回新状态"""
    state = load_state(plugins_dir)
    state[plugin_id] = bool(installed)
    path = state_path(plugins_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".install-state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return state


def installed_ids(plugins_dir: Path | str) -> set[str]:
    """显式标记为未安装的 id 集合（反向查询用）"""
    return {k for k, v in load_state(plugins_dir).items() if not v}


def uninstalled_ids(plugins_dir: Path | str) -> set[str]:
    return {k for k, v in load_state(plugins_dir).items() if not v}
