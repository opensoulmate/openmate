"""pytest 全局隔离：失败落账/审计类副作用一律进临时库，禁写真实运行库。

根因（周期#83 例行探针实证）：make_agent/__new__ 构造的 SoulMateAgent 懒初始化
ToolRecoveryManager() 走默认真实库 ~/.hermes/soulmate/memory/tool_failures.db，
tests/test_turn_outcome.py 的 read_file 失败路径测试把 /tmp/pytest-of-climbing/...
的失败行落进了真实审计库（24 行污染，audit_trail_probe 例行抽查时暴露）。

TOOL_FAILURE_DB_PATH 是 tool_recovery.py 既有的官方覆盖机制（多个测试已单独
monkeypatch），这里统一由 autouse fixture 兜底——测试自己再 setenv 时以测试为准。
"""
import pytest


@pytest.fixture(autouse=True)
def _isolate_tool_failure_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TOOL_FAILURE_DB_PATH", str(tmp_path / "tool_failures.db"))
