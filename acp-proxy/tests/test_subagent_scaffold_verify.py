# -*- coding: utf-8 -*-
"""子代理任务脚手架不计入自验需求——2026-10-23 周期#665 真实缺陷回归。

实证病灶（REJ661 双 worker 轮）：delegate worker 回合 prompt 由 build_subagent_prompt
模板拼装（【SoulMate子代理任务】声明行 + 子任务目标行 + 要求行），extract_requirements
把模板脚手架行拆成需求项（4 条全是脚手架）→ worker 纯产出回答（"ALPHA_DONE"）天然
不复述模板措辞 → coverage 0.5 < 0.55 → 假 unmet → 补做 → 目标词/结论重复×2
（"重复的话"家族残余面，#664 已修主 turn 大 bigram/脚手架词面，本件是子代理轮残面）。

期望行为（语义修非守卫）：
1. worker 回合唯一需求 = 子任务目标本身，模板行（声明行/背景行/要求行）不是需求；
2. 纯字面量输出目标（"只回复一个词：X"）交付物=X 本体，格式前缀是指令脚手架；
3. 目标词在回复中 = met（ok，零补做，worker 输出一次成型）；
4. 普通用户消息的需求提取不受影响（回归）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.self_verify import SelfVerifier, extract_requirements  # noqa: E402
from agent.subagent_manager import Subtask, build_subagent_prompt  # noqa: E402


def _alpha_prompt() -> str:
    return build_subagent_prompt(Subtask(goal="只回复一个词：ALPHA_DONE"))


def test_subagent_scaffold_goal_only():
    """模板脚手架行不进 items，唯一需求=纯字面量目标本体。"""
    reqs = extract_requirements(_alpha_prompt())
    assert reqs == ["ALPHA_DONE"], reqs


def test_subagent_worker_pure_output_met_no_remediation():
    """worker 纯产出回答 = met、零补做清单（一次成型，禁目标词重复）。"""
    report = SelfVerifier().verify(_alpha_prompt(), "ALPHA_DONE", tool_calls_log=[])
    assert [i.status for i in report.items] == ["met"], report.items
    assert report.ok
    assert report.missing_requirements() == []


def test_subagent_rej661_real_scaffold_text():
    """REJ661 原文形态（四行脚手架逐字）回归钉死。"""
    text = (
        "【SoulMate子代理任务】｜系统派发的并行子任务，非用户消息]\n"
        "子任务目标：只回复一个词：ALPHA_DONE\n"
        "要求：只完成上面这一个子任务，不要做无关的事；"
        "完成后输出该子任务的结论/结果摘要（≤300字），不要寒暄、不要复述任务。"
    )
    reqs = extract_requirements(text)
    assert reqs == ["ALPHA_DONE"], reqs
    report = SelfVerifier().verify(text, "ALPHA_DONE", tool_calls_log=[])
    assert report.missing_requirements() == []


def test_subagent_task_goal_kept_whole():
    """非字面量目标：目标整体算一条需求，背景/要求行仍不进 items。"""
    prompt = build_subagent_prompt(
        Subtask(goal="总结Q3销售数据并给出结论", context="数据在 /tmp/q3.csv"),
        parent_goal="做一份年度经营分析",
    )
    reqs = extract_requirements(prompt)
    assert reqs == ["总结Q3销售数据并给出结论"], reqs


def test_subagent_no_goal_returns_empty():
    prompt = build_subagent_prompt(Subtask(goal="   "))
    assert extract_requirements(prompt) == []


def test_normal_user_prompt_regression():
    """普通用户消息需求提取回归：多需求枚举照常拆分。"""
    reqs = extract_requirements("帮我完成三件事：1. 修复登录bug 2. 生成月报 3. 更新文档")
    assert reqs == ["修复登录bug", "生成月报", "更新文档"], reqs
