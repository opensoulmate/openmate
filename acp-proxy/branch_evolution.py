"""分支进化管理器 — git分支上试验，集成测试过才merge

借鉴：
- Agno: baseline diff（改前改后逐任务对比）
- SWE-agent: docker隔离环境（我们的隔离=gitcharacteristics branch）
- deepagents: 乐观锁+冲突重试

核心改变：evo不再直接改main分支。
流程：创建evo/exp-xxx分支→修改→集成测试→过=merge/不过=回滚+记录失败
"""
import asyncio
import logging
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass, field

logger = logging.getLogger("branch-evolution")


@dataclass
class BranchExperiment:
    """一次分支实验"""
    branch_name: str
    base_branch: str = "main"
    changed_files: list[str] = field(default_factory=list)
    created_at: float = 0.0
    merged: bool = False
    rolled_back: bool = False
    test_result: dict = field(default_factory=dict)


class BranchManager:
    """git分支进化管理器"""
    
    def __init__(self, repo_root: Path, base_branch: str = "main"):
        self.repo_root = repo_root
        self.base_branch = base_branch
        self._current_experiment: BranchExperiment | None = None
    
    def _git(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.repo_root),
            capture_output=True, text=True, timeout=30,
        )
    
    def create_experiment_branch(self, reason: str = "") -> BranchExperiment | None:
        """创建实验分支
        
        Returns: BranchExperiment 或 None（如果当前不在base分支或有未提交更改）
        """
        # 确保在base分支且干净
        status = self._git("status", "--porcelain")
        if status.stdout.strip():
            # 未提交工作=他人/手动在途工作，evo 无权清扫（2026-10-23 防误伤根修）。
            # 旧行为"先 stash 再切"（evo-pre-branch-*）把在途修复无声扫进 stash——
            # 2026-10-22 实证：Hermes 的未提交修复被扫走、改完的文件瞬间"回到 HEAD"。
            # docstring 契约本来就是拒绝；stash 是后来的错误放宽，现在回归契约：
            # 谁在途谁提交，evo 不代替别人决定。返回 None=本周期跳过，下周期再试。
            logger.warning(
                "[branch-evolution] 工作树有未提交更改（疑似他人在途工作），拒绝切实验分支。"
                "请先提交/收尾在途更改后重试。明细:\n" + status.stdout.strip()[:500]
            )
            return None
        
        current = self._git("branch", "--show-current")
        if current.stdout.strip() != self.base_branch:
            self._git("checkout", self.base_branch)
        
        # 创建实验分支
        ts = int(time.time())
        branch_name = f"evo/exp-{ts}"
        
        result = self._git("checkout", "-b", branch_name)
        if result.returncode != 0:
            logger.error(f"Failed to create branch {branch_name}: {result.stderr}")
            return None
        
        exp = BranchExperiment(
            branch_name=branch_name,
            base_branch=self.base_branch,
            created_at=time.time(),
        )
        self._current_experiment = exp
        
        logger.info(f"Created experiment branch: {branch_name} (reason: {reason})")
        return exp
    
    def commit_changes(self, message: str, files: list[str] | None = None) -> bool:
        """在实验分支上提交更改"""
        if files:
            for f in files:
                self._git("add", str(f))
        else:
            self._git("add", "-A")
        
        result = self._git("commit", "-m", message)
        if result.returncode != 0:
            logger.warning(f"Commit failed (maybe nothing to commit): {result.stderr[:200]}")
            return False
        
        # 记录变更文件
        if self._current_experiment:
            diff = self._git("diff", "--name-only", "HEAD~1", "HEAD")
            self._current_experiment.changed_files = [
                f.strip() for f in diff.stdout.strip().splitlines() if f.strip()
            ]
        
        return True
    
    def merge_experiment(self) -> bool:
        """集成测试通过后，merge实验分支到base分支"""
        if not self._current_experiment:
            return False
        
        exp = self._current_experiment
        
        # 切回base分支
        self._git("checkout", self.base_branch)
        
        # merge实验分支
        result = self._git("merge", "--no-ff", exp.branch_name, "-m",
                          f"evo: merge {exp.branch_name}")
        
        if result.returncode != 0:
            logger.error(f"Merge failed: {result.stderr[:200]}")
            # 尝试rebase后重试
            self._git("checkout", exp.branch_name)
            self._git("rebase", self.base_branch)
            self._git("checkout", self.base_branch)
            result = self._git("merge", "--no-ff", exp.branch_name)
            if result.returncode != 0:
                logger.error(f"Merge retry failed: {result.stderr[:200]}")
                self._git("merge", "--abort")
                return False
        
        exp.merged = True
        logger.info(f"Merged {exp.branch_name} into {self.base_branch}")
        
        # 清理实验分支
        self._git("branch", "-d", exp.branch_name)
        self._current_experiment = None
        return True
    
    def rollback_experiment(self) -> bool:
        """集成测试失败，回滚实验分支"""
        if not self._current_experiment:
            return False
        
        exp = self._current_experiment
        
        # 切回base分支
        self._git("checkout", self.base_branch)
        
        # 删除实验分支（不merge）
        self._git("branch", "-D", exp.branch_name)
        
        exp.rolled_back = True
        logger.info(f"Rolled back {exp.branch_name}")
        self._current_experiment = None
        return True
    
    def get_current_branch(self) -> str:
        result = self._git("branch", "--show-current")
        return result.stdout.strip()
    
    def get_experiment(self) -> BranchExperiment | None:
        return self._current_experiment


async def run_branch_evolution(
    repo_root: Path,
    apply_changes_fn,  # async callable(branch_manager) -> bool
    run_tests_fn,  # async callable(changed_files) -> IntegrationTestResult
    reason: str = "",
) -> dict:
    """完整的分支进化流程
    
    1. 创建实验分支
    2. 应用更改（由apply_changes_fn执行）
    3. 提交
    4. 运行集成测试
    5. 通过=merge / 失败=回滚+记录失败
    
    Returns: {"merged": bool, "branch": str, "test_result": dict, "changed_files": list}
    """
    bm = BranchManager(repo_root)
    
    # 1. 创建分支
    exp = bm.create_experiment_branch(reason)
    if not exp:
        return {"merged": False, "error": "无法创建实验分支"}
    
    try:
        # 2. 应用更改
        success = await apply_changes_fn(bm)
        if not success:
            bm.rollback_experiment()
            return {"merged": False, "branch": exp.branch_name, "error": "更改应用失败"}
        
        # 3. 提交
        bm.commit_changes(f"evo: {reason}" if reason else "evo: experiment")
        changed_files = exp.changed_files
        
        # 4. 集成测试
        test_result = await run_tests_fn(changed_files)
        exp.test_result = test_result.to_dict() if hasattr(test_result, 'to_dict') else test_result
        
        # 5. 判定
        passed = test_result.passed if hasattr(test_result, 'passed') else test_result.get("passed", False)
        
        if passed:
            merged = bm.merge_experiment()
            return {
                "merged": merged,
                "branch": exp.branch_name,
                "changed_files": changed_files,
                "test_result": exp.test_result,
            }
        else:
            bm.rollback_experiment()
            return {
                "merged": False,
                "branch": exp.branch_name,
                "changed_files": changed_files,
                "test_result": exp.test_result,
                "reason": "集成测试未通过",
            }
    
    except Exception as e:
        logger.error(f"Branch evolution error: {e}")
        bm.rollback_experiment()
        return {"merged": False, "branch": exp.branch_name, "error": str(e)}
