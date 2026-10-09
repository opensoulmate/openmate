"""
复杂任务分解器（路线图 2.3）— 多文件改动自动拆步骤 + 逐步提交

两部分：
1. MultiFileSplitter：确定性按文件拆步——目标点名 ≥2 个文件但 LLM 规划器判"简单"
   （0 个子任务）时，按文件生成子任务（一文件一步），走既有 plan 执行循环。
   LLM 已拆出子任务时不干预（尊重 LLM 规划）。
2. StepCommitter：逐步提交——每个步骤成功后，把该步骤工具调用触碰的文件
   以独立 commit 入库（只 add 明确触碰的文件，绝不 git add -A；只本地 commit 不 push）。
"""

import json
import logging
import re
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("acp-proxy.multi-file-plan")

# 文件扩展名白名单（版本号"1.2.3"、普通词不命中）
_FILE_EXT = (
    r"(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|json|md|markdown|yaml|yml|toml|cfg|ini|"
    r"css|scss|less|html|htm|sh|bash|sql|txt|log|conf|xml|csv|env|lock|service|"
    r"docx?|xlsx?|pptx?|pdf|png|jpe?g|gif|svg|webp)"
)
# 路径 token：含 / 的路径或裸文件名，均需以白名单扩展名结尾
_TOKEN_RE = re.compile(rf"[\w\-./\\~]+?\.{_FILE_EXT}\b", re.IGNORECASE)

# 变量占位/明显非文件的噪音（如 http://... 里的域名截取）
_NOISE_RE = re.compile(r"^(?:v|ver|version)?\d+(?:\.\d+)+$")


def extract_file_paths(text: str) -> list[str]:
    """从文本提取文件路径（保序去重）。版本号等噪音不收。"""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0).strip().lstrip("~").lstrip("/")
        tok = tok.removeprefix("./")
        if not tok or tok in seen:
            continue
        stem = tok.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        # 噪音过滤：版本号形态、stem 无字母（如 "1.2"）不收
        if _NOISE_RE.match(tok.rsplit("/", 1)[-1]) or not re.search(r"[A-Za-z\u4e00-\u9fff]", stem):
            continue
        seen.add(tok)
        out.append(tok)
    return out


class MultiFileSplitter:
    """多文件改动确定性拆步（LLM 规划器判"简单"时的兜底）"""

    MIN_FILES = 2

    def extract_files(self, goal: str) -> list[str]:
        return extract_file_paths(goal)

    def should_split(self, goal: str, plan) -> bool:
        """≥2 个被点名文件 且 现有计划没有子任务（LLM 判简单/规划失败）→ 拆步"""
        if plan is not None and getattr(plan, "subtasks", None):
            return False
        return len(self.extract_files(goal)) >= self.MIN_FILES

    def build_subtasks(self, goal: str):
        """一文件一步生成 SubTask 列表（走 agent.task_engine 的 SubTask 契约）"""
        from agent.task_engine import SubTask

        paths = self.extract_files(goal)
        steps = []
        for i, p in enumerate(paths, 1):
            steps.append(SubTask(
                id=f"file_{i}",
                description=f"修改 {p}：只完成总体目标中针对该文件的改动（不要动其他文件）",
                tool_hint="write_file/patch",
            ))
        return steps


class StepCommitter:
    """逐步提交：按步骤把触碰的文件独立 commit（本地 commit，不 push）"""

    WRITE_TOOLS = {"write_file", "patch", "edit_file", "create_file"}
    LOG_NAME = "step_commit_log.jsonl"

    def __init__(self, log_dir: str | None = None):
        self._log_dir = Path(log_dir) if log_dir else Path(__file__).resolve().parent.parent / "data"

    # ── 工具账本 → 文件清单 ─────────────────────────────
    def files_touched(self, tool_calls: list | None) -> list[str]:
        """从工具账本（[{name, arguments}]，arguments 可能是 dict 或 JSON 串）提取写类工具的 path"""
        out: list[str] = []
        for tc in tool_calls or []:
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or "").strip()
            if name not in self.WRITE_TOOLS:
                continue
            args = tc.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {}
            if not isinstance(args, dict):
                continue
            for key in ("path", "file_path", "filename"):
                v = args.get(key)
                if isinstance(v, str) and v.strip():
                    out.append(v.strip())
                    break
        seen: set[str] = set()
        res: list[str] = []
        for f in out:
            if f not in seen:
                seen.add(f)
                res.append(f)
        return res

    # ── 提交一步改动 ───────────────────────────────────
    @staticmethod
    def _run(args: list[str], cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, errors="replace", timeout=30,
        )

    def commit_step(self, repo_root: str, step_no: int, description: str,
                    files: list[str], session_id: str = "") -> dict:
        """
        把 files（相对/绝对路径，须在 repo_root 内）以独立 commit 入库。
        返回 {committed, sha, reason, files}。任何失败只降级不抛出。
        """
        result: dict = {"committed": False, "sha": "", "reason": "", "files": []}
        try:
            root = Path(repo_root).resolve()
            probe = self._run(["git", "rev-parse", "--is-inside-work-tree"], str(root))
            if probe.returncode != 0 or probe.stdout.strip() != "true":
                result["reason"] = "not_a_git_repo"
                self._log(session_id, step_no, files, result)
                return result

            # 只收 repo_root 内、真实存在的文件（越界/不存在的一律不碰）
            rel_files: list[str] = []
            for f in files:
                p = Path(f).expanduser()
                p = p if p.is_absolute() else (root / p)
                try:
                    rp = p.resolve()
                    rp.relative_to(root)
                except Exception:
                    continue
                if rp.is_file():
                    rel = str(rp.relative_to(root))
                    if rel not in rel_files:
                        rel_files.append(rel)
            if not rel_files:
                result["reason"] = "no_valid_files"
                self._log(session_id, step_no, files, result)
                return result
            result["files"] = rel_files

            add = self._run(["git", "add", "--"] + rel_files, str(root))
            if add.returncode != 0:
                result["reason"] = f"git_add_failed: {(add.stderr or '')[:200]}"
                self._log(session_id, step_no, rel_files, result)
                return result

            staged = self._run(["git", "diff", "--cached", "--quiet"], str(root))
            if staged.returncode == 0:
                # 没有实际变化（内容与 HEAD 一致）→ 不造空 commit
                result["reason"] = "no_changes"
                self._log(session_id, step_no, rel_files, result)
                return result

            msg = f"[soulmate step {step_no}] " + re.sub(r"\s+", " ", description).strip()[:80]
            commit = self._run(["git", "commit", "-m", msg], str(root))
            if commit.returncode != 0:
                # 无身份配置的仓库兜底（不覆盖已有 user 配置，仅失败重试）
                commit = self._run(
                    ["git", "-c", "user.name=SoulMate", "-c", "user.email=soulmate@local",
                     "commit", "-m", msg], str(root))
            if commit.returncode != 0:
                result["reason"] = f"commit_failed: {(commit.stderr or '')[:200]}"
                self._log(session_id, step_no, rel_files, result)
                return result

            sha = self._run(["git", "rev-parse", "HEAD"], str(root)).stdout.strip()
            result.update({"committed": True, "sha": sha, "reason": ""})
            self._log(session_id, step_no, rel_files, result)
            logger.info(f"[commit] step {step_no} committed {sha[:8]}: {rel_files}")
            return result
        except Exception as e:
            result["reason"] = f"commit_exception: {e}"
            try:
                self._log(session_id, step_no, files, result)
            except Exception:
                pass
            return result

    def _log(self, session_id: str, step_no: int, files: list, result: dict) -> None:
        try:
            self._log_dir.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts": time.time(),
                "session_id": session_id,
                "step_no": step_no,
                "files": list(files),
                "committed": result.get("committed", False),
                "sha": result.get("sha", ""),
                "reason": result.get("reason", ""),
            }
            with open(self._log_dir / self.LOG_NAME, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[commit] log write failed: {e}")
