"""pytest 全局隔离：失败落账/审计类副作用一律进临时库，禁写真实运行库。

根因（周期#83 例行探针实证）：make_agent/__new__ 构造的 SoulMateAgent 懒初始化
ToolRecoveryManager() 走默认真实库 ~/.hermes/soulmate/memory/tool_failures.db，
tests/test_turn_outcome.py 的 read_file 失败路径测试把 /tmp/pytest-of-climbing/...
的失败行落进了真实审计库（24 行污染，audit_trail_probe 例行抽查时暴露）。

TOOL_FAILURE_DB_PATH 是 tool_recovery.py 既有的官方覆盖机制（多个测试已单独
monkeypatch），这里统一由 autouse fixture 兜底——测试自己再 setenv 时以测试为准。
"""
import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
EXAMPLES_DIR = REPO_ROOT / "examples"


def make_plugins_dir(dst: Path) -> Path:
    """装配测试用插件扫描目录：plugins/ 真实插件 + examples/ 样板插件。

    运行时 plugins/ 不含样板（demo-greeter 已归位 examples/，防测试数据再生），
    但插件系统测试仍以样板为夹具——复制进临时目录扫描，不污染真实目录。
    """
    dst.mkdir(parents=True, exist_ok=True)
    candidates = list(PLUGINS_DIR.iterdir()) + [d for d in EXAMPLES_DIR.iterdir() if d.is_dir()]
    for src in candidates:
        if not src.is_dir() or src.name.startswith(".") or src.name == "__pycache__":
            continue
        shutil.copytree(src, dst / src.name, dirs_exist_ok=True)
    return dst


@pytest.fixture()
def plugins_dir(tmp_path) -> Path:
    return make_plugins_dir(tmp_path / "plugins")


@pytest.fixture(autouse=True)
def _isolate_tool_failure_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_FAILURE_DB_PATH", str(tmp_path / "tool_failures.db"))
