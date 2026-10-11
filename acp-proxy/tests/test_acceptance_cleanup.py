# -*- coding: utf-8 -*-
"""12.5 harness 收尾清理（周期#71）——run 末显式清理工作目录。

实证缺陷（周期#64）：cleanup() 只清 MEMORY 标记/cron 行/探针行，**不删 /tmp
工作目录**（仅开跑前 rmtree），docstring"测试产物显式清理"承诺未兑现，run 后
/tmp/soulmate_accept 残留产物（本轮残留 3 件）。

修复契约：
  1. cleanup() 末尾 rmtree(WORKDIR)——run 末产物显式清理；
  2. setup() 仍重建工作目录（cleanup 先行的预清路径不受影响，幂等）；
  3. cleanup() 的 agent_messages 探针行清理走 AGENT_MESSAGES_DB 模块常量
     （单一真源，测试可隔离，不摸真实库）；
  4. 接线护栏：main() 真实路径调用 cleanup()。
"""
import inspect
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import acceptance_benchmark  # noqa: E402


def _isolate(monkeypatch, tmp_path):
    """把 cleanup/setup 触碰的全部路径重定向到 tmp（测试不摸真实库/真实记忆）。"""
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    monkeypatch.setattr(acceptance_benchmark, "WORKDIR", str(work))
    monkeypatch.setattr(acceptance_benchmark, "MEM_PATH", str(tmp_path / "MEMORY.md"))
    monkeypatch.setattr(acceptance_benchmark, "CRON_DB", str(tmp_path / "cron.db"))
    monkeypatch.setattr(acceptance_benchmark, "AGENT_MESSAGES_DB", str(tmp_path / "am.db"))
    monkeypatch.setattr(acceptance_benchmark, "OUTBOUND_DB", str(tmp_path / "out.db"))
    return work


def test_cleanup_removes_workdir(monkeypatch, tmp_path):
    """核心契约：cleanup() 后工作目录不残留（周期#64 残留 3 件形态）。"""
    work = _isolate(monkeypatch, tmp_path)
    (work / "dev_hello.py").write_text("print(1)", encoding="utf-8")
    (work / "report.xlsx").write_bytes(b"PK")
    assert work.exists()
    acceptance_benchmark.cleanup()
    assert not work.exists(), "cleanup() 必须显式删除工作目录"


def test_cleanup_tolerates_missing_workdir(monkeypatch, tmp_path):
    """工作目录不存在时 cleanup 不炸（幂等，setup 预清路径先于重建）。"""
    work = _isolate(monkeypatch, tmp_path)
    work.rmdir()
    acceptance_benchmark.cleanup()  # 不抛即过
    assert not work.exists()


def test_setup_recreates_workdir_with_buggy(monkeypatch, tmp_path):
    """setup() 在 cleanup 预清后重建工作目录 + buggy.py（调试类判据依赖）。"""
    _isolate(monkeypatch, tmp_path)
    acceptance_benchmark.setup()
    work = Path(acceptance_benchmark.WORKDIR)
    buggy = work / "buggy.py"
    assert work.is_dir()
    assert buggy.is_file()
    assert "BUG" in buggy.read_text(encoding="utf-8")


def test_cleanup_uses_module_db_constants(monkeypatch, tmp_path):
    """探针行清理走模块常量（单一真源可隔离）——不摸真实 ~/opensoul 库。"""
    work = _isolate(monkeypatch, tmp_path)
    # 造一个假 agent_messages 库，验证清理真的打到 AGENT_MESSAGES_DB 指向的库
    db = tmp_path / "am.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE agent_messages (content TEXT)")
    conn.execute("INSERT INTO agent_messages VALUES ('SOULMATE_PUBLISH_PROBE x')")
    conn.execute("INSERT INTO agent_messages VALUES ('keep me')")
    conn.commit()
    conn.close()
    acceptance_benchmark.cleanup()
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT content FROM agent_messages").fetchall()
    conn.close()
    assert rows == [("keep me",)], "探针行被清、普通行保留"
    assert not work.exists()


def test_main_calls_cleanup_wiring():
    """接线护栏：main() 真实调用 cleanup()（写了≠接线了）。"""
    src = inspect.getsource(acceptance_benchmark.main)
    assert "cleanup()" in src, "main() 必须调用 cleanup()（run 末清理）"
