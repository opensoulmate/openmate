"""复杂任务分解器（2.3）— 多文件改动自动拆步 + 逐步提交。

确定性拆解（不走LLM）：从用户目标里抽取文件路径，按"一个文件一步"顺序拆解，
最后追加整体验证步；每步成功后由主循环调用 commit_step 把该步目标文件的改动
单独 git commit（只 add 指定文件，绝不 push / 绝不动其他文件）。

安全边界：
- 路径必须落在 repo_root 内（realpath 前缀校验），越界直接拒绝
- commit_step 只 `git add -- <指定文件>` + `git commit -m`，无变更时 noop
- 绝不执行 git push / reset / checkout / stash
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

# 文件路径 token：含 / 的相对/绝对路径，或 带已知扩展名的文件名
_EXT = r"(?:py|ts|tsx|js|jsx|json|md|txt|yaml|yml|toml|ini|cfg|sh|css|html|sql|go|rs|java|c|h|cpp|docx?|xlsx?|pptx?|pdf)"
_PATH_TOKEN = re.compile(
    rf"(?:[\w.\-~/]+/)*[\w.\-]+\.{_EXT}\b"
)

# 变更动词：命中才认为是"改动类"任务（纯阅读/提问不算）
_CHANGE_VERBS = (
    "修改", "改动", "更新", "修复", "重构", "新增", "删除", "批量", "改造", "调整", "优化",
    "改一下", "改下", "加上", "去掉", "迁移", "重写",
    "fix", "update", "refactor", "edit", "patch", "change", "modify", "rewrite",
)

# 噪音路径：不作为改动目标
_NOISE_PARTS = ("node_modules/", ".next/", "__pycache__/", ".git/")


@dataclass
class DecomposedStep:
    id: str
    description: str
    target_file: str = ""
    tool_hint: str = "patch/write_file"
    depends_on: list[str] = field(default_factory=list)


class TaskDecomposer:
    """多文件改动任务的确定性拆解器。"""

    # ── 目标解析 ──────────────────────────────────────

    def extract_target_files(self, goal: str) -> list[str]:
        """从目标文本抽取改动目标文件（保序去重，过滤噪音路径）。"""
        seen: set[str] = set()
        files: list[str] = []
        for m in _PATH_TOKEN.finditer(goal or ""):
            tok = m.group(0)
            if tok.startswith("./"):
                tok = tok[2:]
            if any(p in tok for p in _NOISE_PARTS):
                continue
            if tok in seen:
                continue
            seen.add(tok)
            files.append(tok)
        return files

    def is_multi_file_change(self, goal: str) -> bool:
        """≥2 个目标文件 且 含变更动词 → 多文件改动任务。"""
        if not goal:
            return False
        low = goal.lower()
        if not any(v in low for v in _CHANGE_VERBS):
            return False
        return len(self.extract_target_files(goal)) >= 2

    # ── 拆解 ──────────────────────────────────────────

    def decompose(self, goal: str, session_id: str = "") -> list[DecomposedStep]:
        """一个文件一步（顺序依赖）+ 末尾整体验证步。"""
        files = self.extract_target_files(goal)
        goal_brief = goal.strip().replace("\n", " ")[:60]
        steps: list[DecomposedStep] = []
        prev_id = ""
        for i, f in enumerate(files, 1):
            sid = f"step_{i}"
            steps.append(DecomposedStep(
                id=sid,
                description=f"修改 {f}：{goal_brief}",
                target_file=f,
                tool_hint="patch/write_file",
                depends_on=[prev_id] if prev_id else [],
            ))
            prev_id = sid
        # 整体验证步：依赖全部文件步
        steps.append(DecomposedStep(
            id=f"step_{len(files) + 1}",
            description=f"整体验证：确认全部 {len(files)} 个文件改动一致、可构建/可运行（build 或 pytest），汇总结果",
            target_file="",
            tool_hint="terminal",
            depends_on=[s.id for s in steps],
        ))
        return steps

    def step_commit_message(self, step_description: str, idx: int) -> str:
        files = self.extract_target_files(step_description)
        target = files[0] if files else "step"
        return f"soulmate(step{idx + 1}): {target}"

    # ── 逐步提交 ──────────────────────────────────────

    def commit_step(self, repo_root: str, files: list[str], message: str) -> dict:
        """把本步骤目标文件的改动单独 commit。

        返回 {action: "committed"|"noop"|"error", detail, sha?}
        只 add 指定文件；无变更 noop；路径越界/非 git 仓库 error。
        """
        safe_files: list[str] = []
        root_real = os.path.realpath(repo_root)
        for f in files:
            abs_f = f if os.path.isabs(f) else os.path.join(repo_root, f)
            real = os.path.realpath(abs_f)
            if not (real == root_real or real.startswith(root_real + os.sep)):
                return {"action": "error", "detail": f"路径越界拒绝: {f}"}
            # 用相对 repo 的路径给 git（保持可读）
            rel = os.path.relpath(real, root_real)
            safe_files.append(rel)
        if not safe_files:
            return {"action": "noop", "detail": "无目标文件"}

        def _git(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git", "-C", repo_root, *args],
                capture_output=True, text=True, errors="replace", timeout=30,
            )

        try:
            status = _git("status", "--porcelain", "--", *safe_files)
            if status.returncode != 0:
                return {"action": "error", "detail": f"git status 失败: {status.stderr.strip()[:200]}"}
            if not status.stdout.strip():
                return {"action": "noop", "detail": "无变更（本步文件未改动或已提交）"}

            add = _git("add", "--", *safe_files)
            if add.returncode != 0:
                return {"action": "error", "detail": f"git add 失败: {add.stderr.strip()[:200]}"}

            commit = _git("commit", "-m", message)
            if commit.returncode != 0:
                return {"action": "error", "detail": f"git commit 失败: {commit.stderr.strip()[:200]}"}

            sha = _git("rev-parse", "--short", "HEAD").stdout.strip()
            return {"action": "committed", "detail": f"已提交 {len(safe_files)} 个文件", "sha": sha}
        except Exception as e:  # 日志类操作绝不阻塞工具链路
            return {"action": "error", "detail": f"commit_step 异常: {e}"}
