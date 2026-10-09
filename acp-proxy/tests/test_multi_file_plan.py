# -*- coding: utf-8 -*-
"""路线图 2.3 复杂任务分解器——契约测试。

覆盖：
1. extract_file_paths：多路径提取/保序去重/版本号等噪音不收
2. MultiFileSplitter.should_split：≥2文件且LLM计划无子任务才拆；已有子任务/单文件不拆
3. build_subtasks：一文件一步，SubTask 契约字段齐全
4. StepCommitter.files_touched：写类工具 path 提取（dict/JSON串参数都认），只读工具不收
5. commit_step 真实 git 仓库：成功 commit+sha+消息、只 add 触碰文件、无关文件不入库
6. commit_step no_changes：内容与 HEAD 一致不造空 commit
7. commit_step 安全边界：越界文件（repo_root 外）不碰；非 git 目录降级 reason
8. JSONL 日志字段齐全
9. 接线护栏：soulmate_agent 真实路径含 MultiFileSplitter/StepCommitter 初始化+调用
"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.multi_file_plan import (  # noqa: E402
    MultiFileSplitter,
    StepCommitter,
    extract_file_paths,
)

AGENTS = Path(__file__).resolve().parents[1] / "agent"


def _git(args, cwd):
    return subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=30,
    )


def make_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(["init"], str(root))
    _git(["config", "user.email", "t@t"], str(root))
    _git(["config", "user.name", "t"], str(root))
    return root


# ── 1. extract_file_paths ─────────────────────────────
def test_extract_paths_ordered_dedup():
    text = "修改 src/a.py 和 src/b.tsx，再回头改 src/a.py，最后写 docs/说明.md"
    got = extract_file_paths(text)
    assert got == ["src/a.py", "src/b.tsx", "docs/说明.md"]


def test_extract_paths_noise_filtered():
    assert extract_file_paths("版本从 1.2.3 升到 1.3.0") == []
    assert extract_file_paths("") == []


# ── 2/3. splitter ─────────────────────────────────────
def test_should_split_only_when_multi_file_and_no_subtasks():
    sp = MultiFileSplitter()
    empty_plan = SimpleNamespace(subtasks=[])
    split_plan = SimpleNamespace(subtasks=[SimpleNamespace(id="s1")])
    assert sp.should_split("改 src/a.py 和 src/b.py", empty_plan) is True
    assert sp.should_split("只改 src/a.py", empty_plan) is False
    assert sp.should_split("改 src/a.py 和 src/b.py", split_plan) is False  # LLM已拆步不干预


def test_build_subtasks_one_file_per_step():
    sp = MultiFileSplitter()
    steps = sp.build_subtasks("改 src/a.py、src/b.py 和 src/c.py")
    assert len(steps) == 3
    assert [s.id for s in steps] == ["file_1", "file_2", "file_3"]
    for s, p in zip(steps, ["src/a.py", "src/b.py", "src/c.py"]):
        assert p in s.description
        assert s.tool_hint
        assert s.status.value == "pending"


# ── 4. files_touched ──────────────────────────────────
def test_files_touched_write_tools_only():
    cm = StepCommitter(log_dir=None)
    calls = [
        {"name": "write_file", "arguments": {"path": "/x/a.py", "content": "1"}},
        {"name": "patch", "arguments": json.dumps({"path": "/x/b.py", "old": "1"})},
        {"name": "read_file", "arguments": {"path": "/x/c.py"}},
        {"name": "write_file", "arguments": {"path": "/x/a.py"}},  # 去重
        {"name": "terminal", "arguments": {"command": "ls"}},
        {"name": "write_file", "arguments": "not-json"},
    ]
    assert cm.files_touched(calls) == ["/x/a.py", "/x/b.py"]
    assert cm.files_touched([]) == []
    assert cm.files_touched(None) == []


# ── 5/6/7/8. commit_step 真实仓库 ─────────────────────
def test_commit_step_real_repo(tmp_path):
    root = make_repo(tmp_path)
    (root / "a.py").write_text("v1")
    (root / "keep.py").write_text("untouched")
    _git(["add", "--", "keep.py"], str(root))
    _git(["commit", "-m", "init"], str(root))

    (root / "a.py").write_text("v2")
    cm = StepCommitter(log_dir=str(tmp_path / "logs"))
    res = cm.commit_step(str(root), 1, "修改 a.py\n多行描述", ["a.py"], session_id="s1")
    assert res["committed"] is True
    assert res["sha"]
    assert res["files"] == ["a.py"]

    log = _git(["log", "--oneline"], str(root)).stdout
    assert "[soulmate step 1]" in log
    # 只 add 触碰文件：keep.py 的既有 commit 内容不受影响、无多余 staged
    assert "init" in log
    body = _git(["show", "--stat", "--name-only", "HEAD"], str(root)).stdout
    assert "a.py" in body and "keep.py" not in body

    # JSONL 日志字段
    entries = [json.loads(l) for l in (tmp_path / "logs" / "step_commit_log.jsonl").read_text().splitlines()]
    assert entries[-1]["committed"] is True
    for k in ("ts", "session_id", "step_no", "files", "sha", "reason"):
        assert k in entries[-1]


def test_commit_step_no_changes_no_empty_commit(tmp_path):
    root = make_repo(tmp_path)
    (root / "a.py").write_text("v1")
    _git(["add", "--", "a.py"], str(root))
    _git(["commit", "-m", "init"], str(root))

    cm = StepCommitter(log_dir=str(tmp_path / "logs"))
    res = cm.commit_step(str(root), 2, "没改出东西", ["a.py"])
    assert res["committed"] is False
    assert res["reason"] == "no_changes"
    n = len(_git(["log", "--oneline"], str(root)).stdout.strip().splitlines())
    assert n == 1  # 不造空 commit


def test_commit_step_outside_root_and_missing_rejected(tmp_path):
    root = make_repo(tmp_path)
    (root / "a.py").write_text("v1")
    outside = tmp_path / "outside.py"
    outside.write_text("x")
    _git(["add", "--", "a.py"], str(root))
    _git(["commit", "-m", "init"], str(root))

    cm = StepCommitter(log_dir=str(tmp_path / "logs"))
    (root / "a.py").write_text("v2")
    res = cm.commit_step(str(root), 3, "越界", [str(outside), "/no/such/file.py"])
    assert res["committed"] is False
    assert res["reason"] == "no_valid_files"
    # 越界文件绝不入 git
    assert "outside.py" not in _git(["log", "--name-only"], str(root)).stdout


def test_commit_step_not_a_git_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x")
    cm = StepCommitter(log_dir=str(tmp_path / "logs"))
    res = cm.commit_step(str(plain), 1, "d", ["a.py"])
    assert res["committed"] is False
    assert res["reason"] == "not_a_git_repo"


# ── 9. 接线护栏 ───────────────────────────────────────
def test_wiring_in_soulmate_agent():
    src = (AGENTS / "soulmate_agent.py").read_text(encoding="utf-8")
    assert "from agent.multi_file_plan import MultiFileSplitter, StepCommitter" in src
    assert "self._multi_file_splitter = MultiFileSplitter()" in src
    assert "self._step_committer = StepCommitter()" in src
    assert "self._multi_file_splitter.should_split(user_text, plan)" in src
    assert "await self._commit_step_changes(" in src
    assert "self._step_committer.commit_step(" in src
    # 逐步提交只在步骤成功路径触发（首试+重试），失败路径不提交
    assert src.count("await self._commit_step_changes(") == 2
