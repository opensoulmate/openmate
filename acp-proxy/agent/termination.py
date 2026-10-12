# -*- coding: utf-8 -*-
"""P1-15 五终止条件组合——词汇与配置单一真源（blueprint P1.6 终止条件防失控）。

组合：max_turns / wall_clock / token_budget / user_idle / external_cancel，
每个终止条件可单独配置并落入 trace（轨迹回合根事件 metadata.termination，
服务端真源=opensoul trajectory store）。

配置面（各条件独立、缺省零行为变化）：
- max_turns       env SOULMATE_MAX_ROUNDS（默认15）——工具调用轮次硬上限
- wall_clock      env SOULMATE_TURN_SOFT_BUDGET_S（见 turn_watchdog）——回合软预算；
                  用户铁律"任务禁硬超时杀（超时零事件只提醒）"覆盖蓝图硬杀语义，
                  超限只提醒不终止，提醒事件落 trace（condition=wall_clock）
- token_budget    env SOULMATE_TURN_TOKEN_BUDGET（默认0=不限）——回合token预算
                  硬闸（Langroid max_cost/max_turns 同构），估算口径=每轮新增
                  （回复文本+工具结果）累计
- user_idle       env TRAJ_SESSION_IDLE_S（opensoul 侧会话闲置收口，sweep时
                  termination=user_idle 标记事件落 trace）
- external_cancel 事件驱动无需配置——ACP cancel() / steer /abort / 插话中断

落 trace 单一出口：TrajectoryRecorder.end_turn(termination=...)（回合根事件）；
条件内事件（wall_clock 软提醒）走 TrajectoryRecorder.log_event()。
"""
from __future__ import annotations

import os

# ── 五终止条件词汇（trace metadata.termination 取值域，单一真源）──
TERM_MAX_TURNS = "max_turns"
TERM_WALL_CLOCK = "wall_clock"
TERM_TOKEN_BUDGET = "token_budget"
TERM_USER_IDLE = "user_idle"
TERM_EXTERNAL_CANCEL = "external_cancel"

# 空字符串=正常完成（无终止条件触发），不属于"条件"取值域
TERM_NONE = ""

TERMINATION_REASONS = frozenset({
    TERM_MAX_TURNS,
    TERM_WALL_CLOCK,
    TERM_TOKEN_BUDGET,
    TERM_USER_IDLE,
    TERM_EXTERNAL_CANCEL,
})

DEFAULT_MAX_ROUNDS = 15


def max_turns_limit() -> int:
    """max_turns 条件配置（每回合调用时读取，测试/探针可经 env 单独配置）。"""
    raw = os.environ.get("SOULMATE_MAX_ROUNDS", "")
    try:
        value = int(raw) if raw.strip() else DEFAULT_MAX_ROUNDS
    except (TypeError, ValueError):
        return DEFAULT_MAX_ROUNDS
    return value if value >= 1 else DEFAULT_MAX_ROUNDS


def turn_token_budget() -> int:
    """token_budget 条件配置（0=不限=缺省零行为变化；负值/非法按0处理）。"""
    raw = os.environ.get("SOULMATE_TURN_TOKEN_BUDGET", "")
    try:
        value = int(raw) if raw.strip() else 0
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def is_valid(reason: str) -> bool:
    """reason 是否属于五条件取值域（空=正常完成，单独判）。"""
    return reason in TERMINATION_REASONS


def resolve(explicit: str = "", cancelled: bool = False) -> str:
    """组合裁决单一真源：显式条件（max_turns/token_budget/...）优先；
    无显式但回合被外部取消 → external_cancel；否则空=正常完成。"""
    if explicit:
        return explicit
    if cancelled:
        return TERM_EXTERNAL_CANCEL
    return TERM_NONE


def from_acp_stop_reason(stop_reason: str) -> str:
    """ACP stop_reason → 终止条件词汇（relay 轨迹轮消费）。

    max_tokens=模型输出预算截断（token_budget 语义族）；end_turn/refusal 是
    内容结局不是终止条件（status 轴已覆盖），返回空。
    """
    if (stop_reason or "").strip().lower() == "max_tokens":
        return TERM_TOKEN_BUDGET
    return TERM_NONE
