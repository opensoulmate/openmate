# -*- coding: utf-8 -*-
"""8.3 长回合治理 — 回合级进度/超时可见（禁静默）测试。

覆盖：
1. 心跳按节奏推送进度（已用时/轮次/工具数/最近工具），长回合中途可见
2. 软预算超限提醒恰好一次（⚠️ + 不中断 = 禁硬杀语义），心跳继续
3. 旁路纪律：notify 抛错只计数不冒泡，心跳继续
4. stop() 幂等 + 停后不再推送
5. max_lifetime 自过期（孤儿心跳不残留变噪音）
6. beat() 未知字段响亮报错（不静默丢）+ 快照反映到消息
7. 接线护栏：soulmate_agent 真实消息路径 start/beat/turn.close 收口
8. SoulMateAgent._watchdog_for/_stop_turn_watchdog 生命周期（同会话单实例）
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.turn_watchdog import TurnWatchdog
from agent.soulmate_agent import SoulMateAgent


def run(coro):
    return asyncio.run(coro)


def test_heartbeat_pushes_progress_with_snapshot():
    async def main():
        msgs = []

        async def notify(t):
            msgs.append(t)

        wd = TurnWatchdog("s1", notify=notify, heartbeat_s=0.05,
                          soft_budget_s=100, max_lifetime_s=100)
        wd.beat(round_index=2, max_rounds=15, tools_executed=3,
                last_tool="schedule_task")
        wd.start()
        await asyncio.sleep(0.18)
        wd.stop()
        return msgs

    msgs = run(main())
    assert len(msgs) >= 2, f"心跳未按节奏推送: {msgs}"
    assert any(
        "回合进行中" in m and "已用" in m and "第 2/15 轮" in m
        and "已执行工具 3 个" in m and "schedule_task" in m
        for m in msgs
    ), f"快照未进心跳消息: {msgs}"
    assert all("⚠️" not in m for m in msgs), f"未超预算不该警告: {msgs}"


def test_soft_budget_warns_once_and_keeps_beating():
    async def main():
        msgs = []

        async def notify(t):
            msgs.append(t)

        wd = TurnWatchdog("s2", notify=notify, heartbeat_s=0.05,
                          soft_budget_s=0.06, max_lifetime_s=100)
        wd.start()
        await asyncio.sleep(0.35)
        wd.stop()
        return msgs, wd

    msgs, wd = run(main())
    warns = [m for m in msgs if "⚠️" in m]
    assert len(warns) == 1, f"软预算提醒应恰好一次: {msgs}"
    assert "不中断" in warns[0] and "软预算" in warns[0], warns[0]
    assert wd.soft_warned is True
    # 提醒后心跳继续（禁硬杀：不终止任务）
    assert len(msgs) >= 3, f"提醒后应继续心跳: {msgs}"


def test_notify_failure_is_bypass_never_blocks():
    async def main():
        calls = []

        async def notify(t):
            calls.append(t)
            raise RuntimeError("notify boom")

        wd = TurnWatchdog("s3", notify=notify, heartbeat_s=0.05,
                          soft_budget_s=100, max_lifetime_s=100)
        wd.start()
        await asyncio.sleep(0.18)
        wd.stop()
        return wd

    wd = run(main())
    assert wd.notify_failures >= 2, f"notify失败应计数: {wd.notify_failures}"
    assert wd.beats_sent >= 2, f"notify失败心跳应继续: {wd.beats_sent}"


def test_stop_idempotent_and_no_beats_after_stop():
    async def main():
        count = {"n": 0}

        async def notify(t):
            count["n"] += 1

        wd = TurnWatchdog("s4", notify=notify, heartbeat_s=0.05,
                          soft_budget_s=100, max_lifetime_s=100)
        wd.start()
        await asyncio.sleep(0.13)
        wd.stop()
        wd.stop()  # 幂等
        after = count["n"]
        await asyncio.sleep(0.15)
        return wd, after, count["n"]

    wd, after, final = run(main())
    assert final == after, f"stop后仍有心跳: {after} -> {final}"
    assert wd.running is False


def test_max_lifetime_self_expiry_no_orphan():
    async def main():
        msgs = []

        async def notify(t):
            msgs.append(t)

        wd = TurnWatchdog("s5", notify=notify, heartbeat_s=0.03,
                          soft_budget_s=100, max_lifetime_s=0.1)
        wd.start()
        await asyncio.sleep(0.4)
        return wd, msgs

    wd, msgs = run(main())
    assert wd.running is False, "自过期后任务应结束"
    assert 1 <= wd.beats_sent <= 5, f"自过期前心跳次数异常: {wd.beats_sent}"


def test_beat_unknown_field_raises_and_snapshot_flows():
    wd = TurnWatchdog("s6")
    with pytest.raises(ValueError):
        wd.beat(rounds=3)  # 拼错字段必须响亮报错，不静默丢
    wd.beat(round_index=1, max_rounds=15, tools_executed=0)
    assert "第 1/15 轮" in wd.status_text()
    wd.beat(tools_executed=2, last_tool="write_file")
    assert "已执行工具 2 个" in wd.status_text()
    assert "write_file" in wd.status_text()
    # 无 max_rounds 时不显示分母
    wd2 = TurnWatchdog("s7")
    wd2.beat(round_index=4)
    assert "第 4 轮" in wd2.status_text() and "第 4/" not in wd2.status_text()


def test_agent_watchdog_lifecycle_single_per_session():
    async def main():
        agent = SoulMateAgent.__new__(SoulMateAgent)
        wd1 = agent._watchdog_for("sess-a")
        wd2 = agent._watchdog_for("sess-a")
        assert wd1 is wd2, "同会话必须单实例（任务管线多步共享回合时钟）"
        wd_other = agent._watchdog_for("sess-b")
        assert wd_other is not wd1
        assert wd1.running and wd_other.running
        agent._stop_turn_watchdog("sess-a")
        assert not wd1.running and "sess-a" not in agent._turn_watchdogs
        agent._stop_turn_watchdog("sess-a")  # 幂等
        agent._stop_turn_watchdog("sess-b")
        return True

    assert run(main())


def test_wiring_in_soulmate_agent_source():
    src = (Path(__file__).parent.parent / "agent" / "soulmate_agent.py").read_text(
        encoding="utf-8")
    assert "from agent.turn_watchdog import TurnWatchdog" in src
    assert "watchdog = self._watchdog_for(session_id)" in src
    assert "watchdog.beat(round_index=_round + 1" in src
    assert "tools_executed=len(all_tool_calls), last_tool=func_name" in src
    # 收口挂 turn.close 订阅（所有 close 路径必发事件，孤儿心跳不残留）
    assert "self._stop_turn_watchdog(record.session_id)" in src
