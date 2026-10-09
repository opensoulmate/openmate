# -*- coding: utf-8 -*-
"""路线图 1.1 MEMORY.md 型持久记忆契约测试。

覆盖：
1. write/read 往返（行号格式、分页提示）
2. write 拒绝空内容（防误清空）
3. append 带时间戳追加、保留既有内容、文件不存在时创建
4. load_prompt_section 注入内容（缺失/空→""，超长显式截断标注）
5. 接线护栏：soulmate_agent 真实路径含三工具 + 系统提示注入 + dispatch 分支
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.persistent_memory import PersistentMemory  # noqa: E402


def _mem(tmp_path):
    return PersistentMemory(path=str(tmp_path / "MEMORY.md"))


# ── 1. write/read 往返 ──────────────────────────────────────────

def test_write_then_read_roundtrip(tmp_path):
    m = _mem(tmp_path)
    ok, msg = m.write("# 记忆\n- 事实A\n- 事实B")
    assert ok, msg
    out = m.read()
    assert "1|# 记忆" in out
    assert "2|- 事实A" in out
    assert "3|- 事实B" in out


def test_read_missing_file_reports_path(tmp_path):
    m = _mem(tmp_path)
    out = m.read()
    assert "不存在" in out


def test_read_paging_hint(tmp_path):
    m = _mem(tmp_path)
    m.write("\n".join(f"line{i}" for i in range(1, 26)))
    out = m.read(offset=1, limit=10)
    assert "1|line1" in out
    assert "还有 15 行" in out  # 25行读10行，剩15行


# ── 2. write 防误清空 ───────────────────────────────────────────

def test_write_rejects_empty_content(tmp_path):
    m = _mem(tmp_path)
    m.write("原有内容")
    ok, msg = m.write("   ")
    assert not ok
    assert "不能为空" in msg
    assert "原有内容" in m.read()  # 原内容未被清掉


# ── 3. append ───────────────────────────────────────────────────

def test_append_creates_file_with_timestamp(tmp_path):
    m = _mem(tmp_path)
    ok, msg = m.append("用户偏好：回答简洁")
    assert ok, msg
    out = m.read()
    assert re.search(r"1\|- \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] 用户偏好：回答简洁", out)


def test_append_preserves_existing(tmp_path):
    m = _mem(tmp_path)
    m.write("# 既有记忆")
    m.append("第一条")
    m.append("第二条")
    out = m.read()
    assert "# 既有记忆" in out
    assert "第一条" in out
    assert "第二条" in out
    assert out.index("第一条") < out.index("第二条")  # 追加有序


def test_append_rejects_empty_entry(tmp_path):
    m = _mem(tmp_path)
    ok, msg = m.append("")
    assert not ok
    assert "不能为空" in msg


# ── 4. load_prompt_section 系统提示注入 ─────────────────────────

def test_load_prompt_section_missing_or_empty_is_blank(tmp_path):
    m = _mem(tmp_path)
    assert m.load_prompt_section() == ""
    m.path = str(tmp_path / "empty.md")
    Path(m.path).write_text("   \n")
    assert m.load_prompt_section() == ""


def test_load_prompt_section_returns_content(tmp_path):
    m = _mem(tmp_path)
    m.write("- [2026-10-10 00:00] 重要事实")
    assert "重要事实" in m.load_prompt_section()


def test_load_prompt_section_truncates_explicitly(tmp_path):
    m = _mem(tmp_path)
    m.write("x" * 500)
    sec = m.load_prompt_section(max_chars=100)
    assert "截断" in sec  # 显式标注，不静默
    assert "memory_read" in sec  # 提示读全量路径


# ── 5. 接线护栏（写了≠接线了）───────────────────────────────────

def _agent_src():
    return (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_tools_registered_in_builtin_tools():
    src = _agent_src()
    assert '"name": "memory_read"' in src
    assert '"name": "memory_write"' in src
    assert '"name": "memory_append"' in src
    assert "builtin_tools.extend(memory_tools)" in src  # 真的进了工具面


def test_wiring_system_prompt_injects_memory_md():
    src = _agent_src()
    assert "self._persistent_memory.load_prompt_section()" in src
    assert "持久记忆（MEMORY.md" in src
    assert 'name="MEMORY.md", source="persistent_memory"' in src  # token归因在场


def test_wiring_dispatch_branch_executes_memory_tools():
    src = _agent_src()
    assert 'elif func_name in ("memory_read", "memory_write", "memory_append"):' in src
    assert "self._persistent_memory.read(" in src
    assert "self._persistent_memory.write(" in src
    assert "self._persistent_memory.append(" in src


def test_wiring_prompt_tool_list_mentions_memory():
    src = _agent_src()
    assert "memory_read / memory_write / memory_append" in src


def test_atomic_write_used_not_bare_open():
    """项目铁律：文件写入走 utils.file_safety.atomic_write。"""
    mod_src = (Path(__file__).resolve().parents[1] / "agent" / "persistent_memory.py").read_text(encoding="utf-8")
    assert "from utils.file_safety import atomic_write" in mod_src
    assert "atomic_write(self.path" in mod_src
