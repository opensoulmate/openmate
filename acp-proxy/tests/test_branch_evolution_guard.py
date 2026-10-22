# -*- coding: utf-8 -*-
"""branch_evolution 防误伤（2026-10-23）：未提交工作=他人在途，拒绝切实验分支。

背景（2026-10-22 实证）：旧行为"先 stash 再切"（evo-pre-branch-*）把 Hermes 的
未提交修复无声扫进 stash——改完的文件瞬间"回到 HEAD"，只能靠 stash 找回。
docstring 契约本来就是"有未提交更改→None"；stash 是后来的错误放宽。
本测试锁死三件事：脏树拒绝切分支、在途改动原样在场、零 stash 清扫。
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from branch_evolution import BranchManager


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.txt").write_text("hello")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")
    return repo


def test_dirty_tree_refused_no_stash(tmp_path):
    repo = _init_repo(tmp_path)
    # 他人在途工作：改了不提交
    (repo / "a.txt").write_text("uncommitted work")
    bm = BranchManager(repo_root=repo, base_branch="main")
    exp = bm.create_experiment_branch("t")
    assert exp is None, "脏树必须拒绝切分支（docstring 契约）"
    assert (repo / "a.txt").read_text() == "uncommitted work", "在途改动必须原样在场"
    assert _git(repo, "stash", "list").stdout.strip() == "", "禁止 stash 清扫他人工作"
    assert _git(repo, "branch", "--list", "evo/exp-*").stdout.strip() == "", "分支也不得建"


def test_clean_tree_still_branches(tmp_path):
    repo = _init_repo(tmp_path)
    bm = BranchManager(repo_root=repo, base_branch="main")
    exp = bm.create_experiment_branch("t")
    assert exp is not None, "干净树照常切实验分支"
    assert exp.branch_name.startswith("evo/exp-")
