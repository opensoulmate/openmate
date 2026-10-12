"""路线图 20.1 会话文件索引 FTS 召回闭环——写入点真内容 + file_recall 消费端 + 接线护栏

覆盖：
1. SessionFileIndex 真实内容索引→search 命中内容（不是路径字符串）
2. _index_file_for_recall：真内容入索引 / 二进制跳过 / 超限跳过 / 不存在跳过
3. _file_recall_dispatch：命中格式 / 无匹配显式报因 / 空 query 报错 / limit 钳制
4. lazy-init（__new__ 构造实例未过 __init__ 自举不炸）
5. 接线护栏：工具面/主dispatch/code-mode dispatch/系统提示/写入点不再传参错位
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.file_index import SessionFileIndex  # noqa: E402
from agent.soulmate_agent import SoulMateAgent  # noqa: E402


def _index(tmp_path):
    return SessionFileIndex(db_path=str(tmp_path / "idx.db"))


def _agent(idx) -> SoulMateAgent:
    ag = SoulMateAgent.__new__(SoulMateAgent)
    ag._file_index = idx
    return ag


# ── 1. 索引真实内容→search 召回 ─────────────────────────────────

def test_index_real_content_then_search_hits(tmp_path):
    idx = _index(tmp_path)
    idx.index_file(file_path="/tmp/demo.py", content="def climb_rock():\n    return 'summit_token'\n")
    hits = idx.search("summit_token")
    assert hits and hits[0].file_path == "/tmp/demo.py"
    assert "summit_token" in hits[0].content


# ── 2. 写入点 _index_file_for_recall ────────────────────────────

def test_write_point_indexes_real_file_content(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("独特内容标记 ALPHA_BRAVO_CHARLIE 在此文件里\n", encoding="utf-8")
    idx = _index(tmp_path)
    ag = _agent(idx)
    ag._index_file_for_recall(str(f), "sess-1")
    hits = idx.search("ALPHA_BRAVO_CHARLIE")
    assert hits, "真实文件内容必须进索引"
    assert "ALPHA_BRAVO_CHARLIE" in hits[0].content
    # 回归护栏：旧 bug 把路径字符串当内容索引（file_path=session_id）
    assert not idx.search("sess-1") or all(
        h.file_path != "sess-1" for h in idx.search("sess-1"))
    summary = idx.get_file_summary(os.path.abspath(str(f)))
    assert summary and summary["metadata"].get("session_id") == "sess-1"


def test_write_point_skips_binary_and_oversize_and_missing(tmp_path):
    idx = _index(tmp_path)
    ag = _agent(idx)

    bin_f = tmp_path / "blob.bin"
    bin_f.write_bytes(b"\x00\x01\x02binary_payload")
    ag._index_file_for_recall(str(bin_f), "s")

    big_f = tmp_path / "big.txt"
    big_f.write_text("x" * (SoulMateAgent._FILE_INDEX_MAX_BYTES + 1), encoding="utf-8")
    ag._index_file_for_recall(str(big_f), "s")

    ag._index_file_for_recall(str(tmp_path / "nope.txt"), "s")

    assert idx.list_indexed_files() == [], "二进制/超限/缺失都必须显式跳过不索引"


# ── 3. file_recall 消费端 ───────────────────────────────────────

def test_recall_hit_formats_path_and_snippet(tmp_path):
    idx = _index(tmp_path)
    idx.index_file(file_path="/tmp/a.py", content="MAGIC_RECALL_PHRASE appears here\n" + "line\n" * 50)
    ag = _agent(idx)
    out = ag._file_recall_dispatch({"query": "MAGIC_RECALL_PHRASE"})
    assert "文件索引命中" in out and "/tmp/a.py" in out and "MAGIC_RECALL_PHRASE" in out


def test_recall_no_match_and_empty_query(tmp_path):
    ag = _agent(_index(tmp_path))
    assert "无匹配" in ag._file_recall_dispatch({"query": "绝不存在的词xyz"})
    assert "错误" in ag._file_recall_dispatch({"query": ""})


def test_recall_limit_clamped(tmp_path):
    idx = _index(tmp_path)
    for i in range(5):
        idx.index_file(file_path=f"/tmp/f{i}.py", content=f"common_token body {i}\n")
    ag = _agent(idx)
    out = ag._file_recall_dispatch({"query": "common_token", "limit": 999})
    assert out.count("- /tmp/") <= 20
    out1 = ag._file_recall_dispatch({"query": "common_token", "limit": 1})
    assert out1.count("- /tmp/") == 1


def test_recall_lazy_init_without_init(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.soulmate_agent.SessionFileIndex",
                        lambda *a, **k: _index(tmp_path))
    ag = SoulMateAgent.__new__(SoulMateAgent)  # 未过 __init__
    out = ag._file_recall_dispatch({"query": "anything"})
    assert "无匹配" in out


# ── 4. 接线护栏（真实源码路径）──────────────────────────────────

def _agent_src() -> str:
    return (Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_tool_registered_and_appended():
    src = _agent_src()
    assert '"name": "file_recall"' in src
    assert "builtin_tools.append(file_recall_tool)" in src


def test_wiring_main_dispatch_branch():
    src = _agent_src()
    main_start = src.index('elif func_name == "file_recall":')
    assert "self._file_recall_dispatch(func_args)" in src[main_start:main_start + 400]


def test_wiring_code_mode_dispatch():
    src = _agent_src()
    cm_start = src.index("async def _code_mode_tool_call")
    cm_end = src.index("async def _traj_record_round")
    cm_src = src[cm_start:cm_end]
    assert 'if func_name == "file_recall":' in cm_src
    assert "self._file_recall_dispatch(func_args)" in cm_src


def test_wiring_write_point_no_arg_mismatch_and_prompt_list():
    src = _agent_src()
    # 旧 bug 形态必须消失：self._file_index.index_file(session_id, fp) 传参错位
    assert "self._file_index.index_file(session_id, fp)" not in src
    assert "self._index_file_for_recall(fp, session_id)" in src
    assert "file_recall: 会话文件索引召回" in src  # 系统提示工具清单
