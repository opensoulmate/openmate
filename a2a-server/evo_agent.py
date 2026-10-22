# -*- coding: utf-8 -*-
"""evo — 自主进化协调者的 A2A 处理端（Agent↔Agent 网格成员）。

通信链路（三层协议隔离原则）：
    用户 ↔ SoulMate（ACP 人机通道）
        → a2a/task/delegate(target_agent="evo")（A2A Agent 间通道）
        → 本模块（确定性处理：目标收件箱 + 状态应答）
SoulMate 只转达用户意图、回传 evo 的应答，不冒名替 evo 说话（每 agent 独立铁律）。
不经 ACP 人机通道传递 Agent 任务数据。

task_type 三能力（均确定性，不耗 LLM）：
- set_goal(text)     进化目标写入 docs/evo-goals.md（三车道每周期必读，最高优先级）
- query_status()     三仓真实动态（git log/路线图尾/未完成目标清单）
- explain(topic)     按主题检索 commits/路线图，证据化回答

设计取舍（2026-10-23）：evo 的"对话能力"=对自身真实状态的检索+转录，
不需要另开 LLM 大脑——进化协调者的价值是目标管理和状态透明，不是聊天。
"""
from __future__ import annotations

import datetime
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("a2a-evo")

REPO_ROOT = Path("/home/climbing/openmate")
GOALS_FILE = REPO_ROOT / "docs" / "evo-goals.md"
REPOS = [
    ("openmate", REPO_ROOT),
    ("opensoul", Path("/home/climbing/opensoul")),
    ("opensoma", Path("/home/climbing/opensoma")),
]
ROADMAP = REPO_ROOT / "docs" / "soulmate-roadmap.md"

_GOALS_HEADER = (
    "# evo 进化目标（用户经 SoulMate 对话写入，三车道每周期必读）\n\n"
    "> 来源唯一：用户对 SoulMate 说「告诉evo：…」→ A2A set_goal → 此处。\n"
    "> 车道消费规则：每周期开工先消费 `- [ ]` 未完成目标（最高优先级），完成打勾并注明 commit。\n\n"
)


def _git(repo: Path, *args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=15,
        )
        return (r.stdout or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"(git 失败: {e})"


def _ensure_goals_file() -> None:
    if not GOALS_FILE.exists():
        GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        GOALS_FILE.write_text(_GOALS_HEADER, encoding="utf-8")


def _set_goal(text: str) -> str:
    _ensure_goals_file()
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with GOALS_FILE.open("a", encoding="utf-8") as f:
        f.write(f"- [ ] **[{ts}]** {text}\n")
    logger.info("[evo] 新进化目标: %s", text[:80])
    return (
        f"✅ 已记入进化目标（docs/evo-goals.md），三条开发车道下一周期必读并优先消费。\n"
        f"目标：{text}"
    )


def _query_status() -> str:
    lines = ["📊 evo 当前动态（真实产出）："]
    for name, repo in REPOS:
        log = _git(repo, "log", "--oneline", "-5",
                   "--format=%h %ad %s", "--date=format:%m-%d %H:%M")
        lines.append(f"\n【{name} 最近提交】\n{log or '（无）'}")
    if ROADMAP.exists():
        tail = ROADMAP.read_text(encoding="utf-8")[-800:]
        lines.append(f"\n【soulmate 路线图尾】\n{tail}")
    if GOALS_FILE.exists():
        pending = [
            l for l in GOALS_FILE.read_text(encoding="utf-8").splitlines()
            if l.startswith("- [ ]")
        ]
        lines.append(f"\n【未完成进化目标 ×{len(pending)}】")
        lines.extend(pending[-10:] or ["（无）"])
    else:
        lines.append("\n【未完成进化目标】（无目标文件）")
    return "\n".join(lines)


def _explain(topic: str) -> str:
    lines = [f"🔍 主题「{topic}」的证据检索："]
    hit = False
    for name, repo in REPOS:
        log = _git(repo, "log", "--oneline", "-10",
                   f"--grep={topic}", "--regexp-ignore-case")
        if log:
            hit = True
            lines.append(f"\n【{name} commits】\n{log}")
    if ROADMAP.exists():
        hits = [l for l in ROADMAP.read_text(encoding="utf-8").splitlines() if topic in l]
        if hits:
            hit = True
            lines.append(f"\n【路线图命中 ×{len(hits)}】")
            lines.extend(hits[-8:])
    if not hit:
        lines.append("（无命中——换关键词再试，或用 query_status 看全貌）")
    return "\n".join(lines)


async def handle(task_type: str, text: str) -> str:
    """A2A 任务入口：返回 evo 的应答文本（确定性同步处理）。"""
    tt = (task_type or "").strip().lower()
    if tt in ("set_goal", "set-goal", "goal"):
        return _set_goal(text or "(空目标)")
    if tt in ("explain", "why"):
        return _explain(text or "")
    return _query_status()
