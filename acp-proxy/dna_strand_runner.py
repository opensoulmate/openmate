#!/usr/bin/env python3
"""DNA Strand Runner - 独立进程运行单条进化链"""
import asyncio
import signal
import sys
import os
import json
import glob
import subprocess
import logging
import argparse
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("strand-runner")

shutdown_event = asyncio.Event()


def handle_signal(sig, frame):
    logger.info(f"Received signal {sig}, shutting down...")
    shutdown_event.set()

# ── 可配置周期参数 ──────────────────────────────────
OBSERVE_INTERVAL = 120  # 自省/观察频率（秒），可通过API动态修改


# ── 外部学习配置 ──────────────────────────────────
GITHUB_LEARN_INTERVAL = 3600  # 每小时学习一次GitHub
_last_github_learn = 0

async def _learn_github_trending(strand, repo_root: Path):
    """从GitHub Top AI Agent项目学习架构和最佳实践"""
    import time
    global _last_github_learn
    now = time.time()
    if now - _last_github_learn < GITHUB_LEARN_INTERVAL:
        return
    _last_github_learn = now

    try:
        # 搜索GitHub上最热门的AI Agent项目
        result = subprocess.run(
            ["curl", "-s", "-H", "Accept: application/vnd.github.v3+json",
             "https://api.github.com/search/repositories?q=ai+agent+framework&sort=stars&order=desc&per_page=10"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            repos = data.get("items", [])[:10]
            summaries = []
            for r in repos:
                summaries.append(f"- {r['full_name']} ({r['stargazers_count']}★): {r.get('description', 'N/A')[:80]}")
            if summaries:
                strand.observe("github_learn", f"Top AI Agent repos today:\n" + "\n".join(summaries))
                logger.info(f"[GitHub] Learned {len(summaries)} trending repos")

                # 对top3深入学习：读README摘要
                for r in repos[:3]:
                    try:
                        readme = subprocess.run(
                            ["curl", "-s", f"https://raw.githubusercontent.com/{r['full_name']}/main/README.md"],
                            capture_output=True, text=True, timeout=15
                        )
                        if readme.returncode == 0 and len(readme.stdout) > 100:
                            # 提取前500字符作为摘要
                            summary = readme.stdout[:500].replace("\n", " ").strip()
                            strand.observe("github_learn", f"[{r['full_name']}] README摘要: {summary}...")
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"[GitHub learn error] {e}")


def _learn_user_feedback(strand, repo_root: Path):
    """从最近的用户对话中提取反馈"""
    try:
        # 读取最近的hermes session记录
        sessions_dir = Path.home() / ".hermes" / "sessions"
        if not sessions_dir.exists():
            return

        # 找最近的session文件
        session_files = sorted(sessions_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:3]
        feedbacks = []
        for sf in session_files:
            try:
                with open(sf) as f:
                    lines = f.readlines()[-20:]  # 最后20条
                for line in lines:
                    try:
                        msg = json.loads(line)
                        if msg.get("role") == "user":
                            text = str(msg.get("content", ""))[:200]
                            # 过滤出有价值的反馈
                            keywords = ["不好", "不对", "垃圾", "很好", "不错", "改", "加", "删", "优化", "bug", "错误", "漂亮", "喜欢", "讨厌", "烦"]
                            if any(kw in text for kw in keywords):
                                feedbacks.append(text)
                    except Exception:
                        pass
            except Exception:
                pass

        if feedbacks:
            # 去重，最多5条
            unique = list(dict.fromkeys(feedbacks))[:5]
            strand.observe("user_feedback", f"Recent user feedback ({len(unique)} items):\n" + "\n".join(f"- {f}" for f in unique))
    except Exception as e:
        logger.warning(f"[User feedback error] {e}")


def _learn_build_results(strand, repo_root: Path):
    """从最近的构建结果中学习"""
    try:
        # 检查next.js构建日志
        build_log = repo_root / ".next" / "trace"
        if build_log.exists():
            mtime = build_log.stat().st_mtime
            import time
            age_hours = (time.time() - mtime) / 3600
            if age_hours < 24:
                strand.observe("build_result", f"Next.js build: last build {age_hours:.1f}h ago, trace exists")

        # 检查是否有构建错误日志
        error_log = repo_root / ".next" / "server" / "app-build-manifest.json"
        if error_log.exists():
            strand.observe("build_result", f"Build manifest present, size={error_log.stat().st_size}")

        # 检查git status是否有未提交的改动
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=str(repo_root))
        if result.returncode == 0 and result.stdout.strip():
            changed = result.stdout.strip().split("\n")
            strand.observe("build_result", f"Uncommitted changes: {len(changed)} files modified")
    except Exception as e:
        logger.warning(f"[Build result error] {e}")


def _learn_code_diffs(strand, repo_root: Path):
    """从最近的代码改动中学习"""
    try:
        # 最近3个commit的diff
        result = subprocess.run(
            ["git", "log", "--oneline", "-3", "--stat"],
            capture_output=True, text=True, cwd=str(repo_root)
        )
        if result.returncode == 0 and result.stdout.strip():
            strand.observe("code_diff", f"Recent commits:\n{result.stdout.strip()}")

        # 最近commit的详细diff（只看关键文件）
        result2 = subprocess.run(
            ["git", "diff", "HEAD~1..HEAD", "--stat"],
            capture_output=True, text=True, cwd=str(repo_root)
        )
        if result2.returncode == 0 and result2.stdout.strip():
            strand.observe("code_diff", f"Latest commit diff summary:\n{result2.stdout.strip()[:500]}")
    except Exception as e:
        logger.warning(f"[Code diff error] {e}")


async def self_observe(strand, loop_interval):
    """自观察+外部学习：每loop_interval秒扫描系统状态和外部知识"""
    import dna_strand_runner as _self
    while True:
        await asyncio.sleep(_self.OBSERVE_INTERVAL)
        try:
            repo_root = Path(__file__).parent.parent.parent
            acp_dir = repo_root / "acp-proxy"

            # ── 内部状态（精简）──
            status = strand.get_status()
            strand.observe("self_introspect", f"Status: cycle={status['cycle_count']}, mem={status['memories']}, unanalyzed={status['observations_unanalyzed']}")

            # ── 外部学习 ──
            # 1. GitHub Top项目学习（每小时）
            await _learn_github_trending(strand, repo_root)

            # 2. 用户反馈学习
            _learn_user_feedback(strand, repo_root)

            # 3. 构建结果学习
            _learn_build_results(strand, repo_root)

            # 4. 代码diff学习
            _learn_code_diffs(strand, repo_root)

        except Exception as e:
            logger.warning(f"[{strand.strand_id}] Self-observe error: {e}")


async def run_strand(strand_id: str, strategy: str):
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    logger.info(f"Starting {strand_id} strand ({strategy})")

    # PID文件
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(exist_ok=True)
    pid_file = data_dir / f"dna_{strand_id}.pid"
    pid_file.write_text(str(os.getpid()))

    # 创建strand
    from dna_evolution import DNAStrand, StrandRole
    from utils.llm_config import describe, resolve_llm_config
    llm_cfg = resolve_llm_config()
    if not llm_cfg["api_key"]:
        logger.warning("进化strand无API key（MIMO_API_KEY/LLM_API_KEY均未配置），LLM调用将按'LLM失败'留痕")
    logger.info(f"进化LLM配置: {describe(llm_cfg)}")
    strand = DNAStrand(
        strand_id=strand_id,
        role=StrandRole.PRIMARY if strand_id == "strand_a" else StrandRole.SHADOW,
        repo_root=str(Path(__file__).parent.parent),
        llm_base_url=llm_cfg["base_url"],
        strategy=strategy,
        llm_api_key=llm_cfg["api_key"],
        llm_model=llm_cfg["model"],
    )

    partner_id = "strand_b" if strand_id == "strand_a" else "strand_a"

    try:
        # 启动进化循环
        evo_task = asyncio.create_task(strand.run(partner_id))

        # 启动自观察循环（使用OBSERVE_INTERVAL，可通过API动态修改）
        observe_task = asyncio.create_task(self_observe(strand, OBSERVE_INTERVAL))

        # 等待关闭信号
        await shutdown_event.wait()

        strand.stop()
        evo_task.cancel()
        observe_task.cancel()
        for t in [evo_task, observe_task]:
            try:
                await t
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"[{strand_id}] Fatal error: {e}", exc_info=True)
    finally:
        if pid_file.exists():
            pid_file.unlink()
        logger.info(f"[{strand_id}] Strand stopped")


def main():
    parser = argparse.ArgumentParser(description="DNA Strand Runner")
    parser.add_argument("--strand", required=True, choices=["strand_a", "strand_b"])
    parser.add_argument("--strategy", required=True, choices=["conservative", "aggressive"])
    args = parser.parse_args()

    asyncio.run(run_strand(args.strand, args.strategy))


if __name__ == "__main__":
    main()
