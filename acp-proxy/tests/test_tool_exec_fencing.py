"""
路线图 5.2 工具执行层并发隔离测试。

覆盖：跨进程文件栅栏（真多进程lost-update复现/串行化/有界超时/进程死亡自动释放/
不在项目目录撒锁文件）+ PersistentMemory 并发追加不丢行 + 超时显式报冲突 +
soulmate_agent 接线护栏（code-mode patch 行为实测 + 主循环接线护栏 + _get_db 超时）。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.persistent_memory as pm
import agent.soulmate_agent as sa
from agent.persistent_memory import PersistentMemory
from agent.tool_exec_fencing import (
    DEFAULT_FENCE_TIMEOUT_S,
    FileFenceTimeout,
    _lock_file_for,
    file_fence,
)

ACPPROXY = Path(__file__).resolve().parent.parent


def _worker_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ACPPROXY) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _spawn(script: str, *args: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        env=_worker_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


# ── 跨进程文件栅栏：真多进程 lost-update 复现 ──────────────────────

_RMW_WORKER = """
    import sys, time
    from agent.tool_exec_fencing import file_fence
    from utils.file_safety import atomic_write

    path, tag, delay, start_at = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
    while time.time() < start_at:          # 对齐起跑，确保竞争窗口重叠
        time.sleep(0.001)
    with file_fence(path):
        cur = open(path, encoding="utf-8").read()
        time.sleep(delay)                  # 放大读-改-写窗口
        atomic_write(path, cur + tag + "\\n")
"""


def test_multiprocess_rmw_no_lost_update(tmp_path):
    """两个进程并发 read-modify-write 同一文件：两个改动都必须保留。

    这是本切片的核心回归：没有栅栏时后写者基于旧内容覆盖（lost update），
    文件里只剩一个 tag；有栅栏时读-改-写串行，两个 tag 都在。
    """
    target = tmp_path / "shared.txt"
    target.write_text("base\n", encoding="utf-8")
    start_at = time.time() + 0.5
    procs = [
        _spawn(_RMW_WORKER, str(target), "A", "0.2", str(start_at)),
        _spawn(_RMW_WORKER, str(target), "B", "0.2", str(start_at)),
    ]
    for p in procs:
        _, err = p.communicate(timeout=30)
        assert p.returncode == 0, err
    content = target.read_text(encoding="utf-8")
    assert "base" in content
    assert "A" in content, f"lost update: A 的改动被覆盖了: {content!r}"
    assert "B" in content, f"lost update: B 的改动被覆盖了: {content!r}"


# ── 栅栏语义：串行化 / 有界超时 / 释放 ─────────────────────────────


def test_fences_serialize_same_process(tmp_path):
    """同进程两个 fd 之间同样互斥（flock 按 open file description 计）。"""
    target = str(tmp_path / "f.txt")
    with file_fence(target):
        with pytest.raises(FileFenceTimeout):
            with file_fence(target, timeout=0.2):
                pass


def test_fences_bounded_wait_then_acquires(tmp_path):
    """持有者很快释放时，等待方在超时前拿到锁（有界等待≠直接拒绝）。"""
    target = str(tmp_path / "f.txt")

    def _hold():
        with file_fence(target):
            time.sleep(0.2)

    t = threading.Thread(target=_hold)
    t.start()
    time.sleep(0.05)  # 让持有者先拿到锁
    t0 = time.monotonic()
    with file_fence(target, timeout=3.0):
        waited = time.monotonic() - t0
    t.join()
    assert waited >= 0.05  # 确实等待了（不是偷跑）
    assert waited < 3.0


def test_fences_timeout_message_visible(tmp_path):
    target = str(tmp_path / "f.txt")
    with file_fence(target):
        with pytest.raises(FileFenceTimeout) as ei:
            with file_fence(target, timeout=0.15):
                pass
    msg = str(ei.value)
    assert "并发写入冲突" in msg
    assert "f.txt" in msg  # 失败必须可见：带上具体文件


def test_fences_auto_release_on_process_death(tmp_path):
    """进程死亡（os._exit 未释放锁）后锁自动可得——flock 无 stale lock 问题。"""
    target = str(tmp_path / "f.txt")
    (tmp_path / "f.txt").write_text("x", encoding="utf-8")
    p = _spawn(
        """
        import os, sys, time
        from agent.tool_exec_fencing import file_fence
        with file_fence(sys.argv[1]):
            os._exit(0)   # 故意不释放锁直接死
        """,
        str(target),
    )
    p.communicate(timeout=30)
    assert p.returncode == 0
    t0 = time.monotonic()
    with file_fence(target, timeout=2.0):  # 死进程的锁已随进程释放
        pass
    assert time.monotonic() - t0 < 2.0


def test_lock_files_not_in_project_dir(tmp_path):
    """锁文件走中央临时目录，不在项目目录撒 *.lock。"""
    target = tmp_path / "f.txt"
    target.write_text("x", encoding="utf-8")
    with file_fence(str(target)):
        pass
    assert list(tmp_path.glob("*.lock")) == []
    lock_path = _lock_file_for(str(target))
    assert Path(lock_path).parent.name == "soulmate_file_fences"
    # 同一文件（含相对/绝对路径）映射同一把锁
    assert _lock_file_for(os.path.join(str(tmp_path), ".", "f.txt")) == lock_path


# ── PersistentMemory：并发追加不丢行 + 超时显式报错 ─────────────────


_APPEND_WORKER = """
    import sys, time
    from agent.persistent_memory import PersistentMemory

    path, tag, count, start_at = sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4])
    mem = PersistentMemory(path)
    while time.time() < start_at:
        time.sleep(0.001)
    for i in range(count):
        ok, msg = mem.append(f"{tag}-{i}")
        assert ok, msg
"""


def test_persistent_memory_concurrent_appends_no_lost_lines(tmp_path):
    """两个进程并发 memory_append：所有条目都要在（read-modify-write 不丢行）。"""
    mem_path = tmp_path / "MEMORY.md"
    start_at = time.time() + 0.5
    procs = [
        _spawn(_APPEND_WORKER, str(mem_path), "alpha", "5", str(start_at)),
        _spawn(_APPEND_WORKER, str(mem_path), "beta", "5", str(start_at)),
    ]
    for p in procs:
        _, err = p.communicate(timeout=30)
        assert p.returncode == 0, err
    content = mem_path.read_text(encoding="utf-8")
    for tag in ("alpha", "beta"):
        for i in range(5):
            assert f"{tag}-{i}" in content, f"lost update: 缺 {tag}-{i}: {content!r}"


def test_persistent_memory_fence_timeout_reports_error(tmp_path, monkeypatch):
    """栅栏超时 → (False, 显式冲突消息)，文件不变，绝不无锁裸写。"""
    mem_path = tmp_path / "MEMORY.md"
    mem_path.write_text("old\n", encoding="utf-8")
    mem = PersistentMemory(str(mem_path))
    # 把持久记忆的栅栏超时压短（真实语义不变，只改等待上限）
    monkeypatch.setattr(
        pm, "file_fence", lambda p: file_fence(p, timeout=0.2),
    )
    with file_fence(str(mem_path)):
        ok, msg = mem.append("新条目")
        assert ok is False
        assert "并发写入冲突" in msg
        ok, msg = mem.write("整体改写")
        assert ok is False
        assert "并发写入冲突" in msg
    assert mem_path.read_text(encoding="utf-8") == "old\n"  # 未被改动


# ── soulmate_agent 接线：code-mode patch 行为实测 + 护栏 ────────────


def _stub_agent():
    """__new__ 构造（不过 __init__）+ 工具执行路径最小依赖。"""
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    agent._sandbox_policy = SimpleNamespace(deny_reason=lambda *a, **k: None)
    gate = SimpleNamespace(allowed=True, behavior="allow", blocked_reason="")
    agent._permission_gate = SimpleNamespace(
        check=lambda *a, **k: _awaitable(gate),
    )
    agent._turn_read_view = SimpleNamespace(
        record_file_change=lambda *a, **k: None,
    )
    return agent


async def _awaitable(value):
    return value


def test_codemode_patch_uses_fence_and_reports_conflict(tmp_path, monkeypatch):
    """行为实测：code-mode patch 的读-改-写走栅栏，冲突时显式报错且不改文件。"""
    target = tmp_path / "code.py"
    target.write_text("hello world\n", encoding="utf-8")
    monkeypatch.setattr(
        sa, "file_fence", lambda p: file_fence(p, timeout=0.3),
    )
    agent = _stub_agent()

    def _hold():
        with file_fence(str(target)):
            time.sleep(1.0)

    t = threading.Thread(target=_hold)
    t.start()
    time.sleep(0.05)

    out = asyncio.run(agent._code_mode_tool_call(
        "om-s1", "patch",
        {"path": str(target), "old_string": "world", "new_string": "fence"},
        cwd=str(tmp_path),
    ))
    t.join()
    assert "并发写入冲突" in out
    assert target.read_text(encoding="utf-8") == "hello world\n"  # 未被改动

    # 无竞争正路径照常工作
    out = asyncio.run(agent._code_mode_tool_call(
        "om-s1", "patch",
        {"path": str(target), "old_string": "world", "new_string": "fence"},
        cwd=str(tmp_path),
    ))
    assert out == f"已修改 {target}"
    assert target.read_text(encoding="utf-8") == "hello fence\n"


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 两个patch执行点（主循环 + code-mode内层）都包了栅栏
    assert src.count("with file_fence(path):") >= 2
    assert src.count("except FileFenceTimeout") >= 2
    assert "from agent.tool_exec_fencing import" in src
    # _get_db 并发写busy等待
    assert 'sqlite3.connect(str(self._db_path), timeout=30)' in src
    # persistent_memory 追加/覆盖写都持栅栏
    pm_src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/persistent_memory.py").read_text(encoding="utf-8")
    assert pm_src.count("with file_fence(self.path):") == 2


def test_get_db_uses_busy_timeout(monkeypatch):
    """_get_db 带 timeout=30：并发写忙等30s，不再5s就 'database is locked'。"""
    captured = {}

    def fake_connect(path, **kwargs):
        captured.update(kwargs)
        captured["path"] = path
        return SimpleNamespace(row_factory=None)

    monkeypatch.setattr(sa.sqlite3, "connect", fake_connect)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    agent._db_path = "/tmp/whatever.db"
    agent._get_db()
    assert captured.get("timeout") == 30
