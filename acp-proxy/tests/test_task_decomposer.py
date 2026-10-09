"""路线图 2.3 复杂任务分解器测试——多文件改动自动拆步 + 逐步提交。

覆盖：目标文件抽取（去重/噪音过滤）/ 多文件改动判定 / 确定性拆步（一步一文件+
顺序依赖+整体验证步）/ commit_step 逐步提交（真实tmp git仓库：只提交本步文件、
其余保持未提交；无变更noop；路径越界拒绝）/ task_engine 接线（多文件目标
确定性拆步覆盖LLM"simple"误判）/ soulmate_agent 双成功路径接线断言。
"""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.task_decomposer import TaskDecomposer  # noqa: E402


# ── 目标文件抽取 ──────────────────────────────────────

def test_extract_target_files_dedupe_and_noise():
    dec = TaskDecomposer()
    goal = (
        "修改 src/a/alpha.py 和 src/b/beta.tsx，再更新 src/a/alpha.py 的逻辑，"
        "别碰 node_modules/foo/bar.js"
    )
    files = dec.extract_target_files(goal)
    assert files == ["src/a/alpha.py", "src/b/beta.tsx"]


def test_extract_target_files_absolute_and_docs():
    dec = TaskDecomposer()
    goal = "更新 /home/climbing/openmate/docs/specs/29-Time.md 和 README.md"
    files = dec.extract_target_files(goal)
    assert "/home/climbing/openmate/docs/specs/29-Time.md" in files
    assert "README.md" in files


# ── 多文件改动判定 ────────────────────────────────────

def test_is_multi_file_change_true():
    dec = TaskDecomposer()
    assert dec.is_multi_file_change("修复 src/x.py 和 src/y.py 的导入错误") is True


def test_is_multi_file_change_single_file_false():
    dec = TaskDecomposer()
    assert dec.is_multi_file_change("修改 src/x.py 的导入错误") is False


def test_is_multi_file_change_no_verb_false():
    dec = TaskDecomposer()
    # 提到多个文件但只是提问/阅读，不是改动任务
    assert dec.is_multi_file_change("帮我看看 src/x.py 和 src/y.py 里写了什么") is False


def test_is_multi_file_change_empty_false():
    assert TaskDecomposer().is_multi_file_change("") is False


# ── 确定性拆步 ────────────────────────────────────────

def test_decompose_one_step_per_file_plus_verify():
    dec = TaskDecomposer()
    goal = "重构 src/p.py、src/q.py、src/r.py，统一错误处理"
    steps = dec.decompose(goal, session_id="s1")
    # 3个文件步 + 1个整体验证步
    assert len(steps) == 4
    assert [s.target_file for s in steps[:3]] == ["src/p.py", "src/q.py", "src/r.py"]
    # 顺序依赖：step_2 依赖 step_1 ...
    assert steps[1].depends_on == ["step_1"]
    assert steps[2].depends_on == ["step_2"]
    # 整体验证步依赖全部文件步
    assert steps[3].depends_on == ["step_1", "step_2", "step_3"]
    assert steps[3].target_file == ""
    assert "验证" in steps[3].description


def test_step_commit_message_contains_target():
    dec = TaskDecomposer()
    msg = dec.step_commit_message("修改 src/z.py：xxx", 0)
    assert "step1" in msg and "src/z.py" in msg


# ── commit_step 真实 tmp git 仓库 ─────────────────────

def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
    ):
        subprocess.run(cmd, cwd=repo, check=True, capture_output=True)
    (repo / "a.py").write_text("a0\n")
    (repo / "b.py").write_text("b0\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_commit_step_only_commits_target_file(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "a.py").write_text("a1\n")
    (repo / "b.py").write_text("b1\n")
    dec = TaskDecomposer()
    res = dec.commit_step(str(repo), ["a.py"], "soulmate(step1): a.py")
    assert res["action"] == "committed", res
    # HEAD 只包含 a.py
    shown = subprocess.run(
        ["git", "show", "--name-only", "--format=%s", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "a.py" in shown and "b.py" not in shown
    # b.py 仍是未提交改动
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--", "b.py"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert dirty  # 仍有未提交改动
    # 第二步提交 b.py
    res2 = dec.commit_step(str(repo), ["b.py"], "soulmate(step2): b.py")
    assert res2["action"] == "committed", res2
    clean = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert clean == ""


def test_commit_step_noop_when_clean(tmp_path):
    repo = _init_repo(tmp_path)
    res = TaskDecomposer().commit_step(str(repo), ["a.py"], "msg")
    assert res["action"] == "noop"


def test_commit_step_rejects_path_escape(tmp_path):
    repo = _init_repo(tmp_path)
    res = TaskDecomposer().commit_step(str(repo), ["../../etc/passwd"], "msg")
    assert res["action"] == "error"
    assert "越界" in res["detail"]


def test_commit_step_untracked_new_file(tmp_path):
    repo = _init_repo(tmp_path)
    (repo / "new_mod.py").write_text("x = 1\n")
    res = TaskDecomposer().commit_step(str(repo), ["new_mod.py"], "soulmate(step1): new_mod.py")
    assert res["action"] == "committed", res


# ── task_engine 接线 ─────────────────────────────────

def test_task_engine_multi_file_overrides_llm_simple(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSOUL_DB", str(tmp_path / "t.db"))
    from agent.task_engine import TaskPlanner

    async def fake_llm(messages):
        # LLM 误判为简单任务（不拆步）——确定性拆解必须顶上
        return '{"needs_planning": false, "reason": "simple", "subtasks": []}'

    planner = TaskPlanner(llm_call_fn=fake_llm)
    goal = "修复 src/m1.py 和 src/m2.py 的类型错误"
    plan = asyncio.run(planner.plan(goal, session_id="s-test"))
    assert plan.status == "active"
    assert len(plan.subtasks) == 3  # 2文件步 + 1验证步
    assert "src/m1.py" in plan.subtasks[0].description
    assert "src/m2.py" in plan.subtasks[1].description


def test_task_engine_non_multi_file_keeps_llm_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENSOUL_DB", str(tmp_path / "t.db"))
    from agent.task_engine import TaskPlanner

    async def fake_llm(messages):
        return '{"needs_planning": false, "reason": "simple", "subtasks": []}'

    planner = TaskPlanner(llm_call_fn=fake_llm)
    plan = asyncio.run(planner.plan("你好，在吗", session_id="s-test2"))
    # 非多文件改动 → 走LLM简单任务路径
    assert plan.status == "completed"
    assert plan.subtasks == []


# ── soulmate_agent 接线断言 ──────────────────────────

def test_soulmate_agent_step_commit_wiring():
    src = Path(__file__).resolve().parent.parent / "agent" / "soulmate_agent.py"
    text = src.read_text(encoding="utf-8")
    # helper 存在
    assert "async def _commit_step_changes" in text
    # 两个成功路径（首试/重试）都调用
    assert text.count("await self._commit_step_changes(step, idx, session_id)") == 2
