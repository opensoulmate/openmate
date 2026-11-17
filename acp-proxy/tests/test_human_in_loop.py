# -*- coding: utf-8 -*-
"""Pi 取长补短 #10——结构化人机对话工具（ask_user / report_failure）+ 中途插话合流。

治两个现行痛点：clarify 阻塞（无人值守空等）、Agent 私自放弃任务（终止需确认）。
整合面：steering.py（人机对话通道纯函数核心）+ clarify 提问卡回流通道复用
（pending_choices/resolve_choice 前端零改动）+ run_guard 防跑飞合流（等待用户
期暂停无进展计时，Pi 同款）。

通用人机协同能力（非投标专用）。
"""
import asyncio
import inspect
import json

import pytest

import agent.soulmate_agent as sma
from agent import steering
from agent.guard_tools import GuardTools
from agent.soulmate_agent import SoulMateAgent
from tests.test_steering import make_agent, run


class FakeClient:
    """捕获 session_update 推送的提问卡 payload"""

    def __init__(self):
        self.updates = []

    async def session_update(self, session_id=None, update=None, **kw):
        self.updates.append((session_id, update))


def _pushed_choice(client: FakeClient) -> dict:
    for _sid, update in reversed(client.updates):
        try:
            obj = json.loads(update.content.text)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("type") == "choice":
            return obj
    raise AssertionError("session_update 流中没有提问卡 payload（type=choice）")


OPTS = [{"id": "a", "label": "方案A"}, {"id": "b", "label": "方案B"}]


def _attended(tmp_path):
    a = make_agent(tmp_path)
    a._client = FakeClient()
    return a


# ── ① 纯参数面（agent/steering.py）────────────────────────────────

class TestAskUserPayload:
    def test_valid_appends_unique_custom(self):
        payload, err = steering.build_ask_user_payload("部署到哪个环境？", OPTS)
        assert err == ""
        assert payload["type"] == "choice"
        assert payload["text"] == "部署到哪个环境？"
        ids = [c["id"] for c in payload["choices"]]
        assert ids == ["a", "b", "custom"]          # 唯一 custom 项系统追加
        custom = payload["choices"][-1]
        assert custom["label"] == "自定义"

    def test_five_options_plus_custom_ok(self):
        opts = [{"id": f"o{i}", "label": f"选项{i}"} for i in range(5)]
        payload, err = steering.build_ask_user_payload("选一个", opts)
        assert err == "" and len(payload["choices"]) == 6

    def test_six_options_rejected(self):
        opts = [{"id": f"o{i}", "label": f"选项{i}"} for i in range(6)]
        payload, err = steering.build_ask_user_payload("选一个", opts)
        assert payload is None and "2-5" in err

    def test_one_option_rejected(self):
        payload, err = steering.build_ask_user_payload("选一个", OPTS[:1])
        assert payload is None and "2-5" in err

    def test_question_required(self):
        payload, err = steering.build_ask_user_payload("", OPTS)
        assert payload is None and "question 必填" in err

    def test_id_label_required(self):
        payload, err = steering.build_ask_user_payload("q", [{"id": "a"}, {"id": "b", "label": "B"}])
        assert payload is None and "id+label" in err

    def test_reserved_custom_id_rejected(self):
        opts = [{"id": "custom", "label": "自定义"}, {"id": "b", "label": "B"}]
        payload, err = steering.build_ask_user_payload("q", opts)
        assert payload is None and "保留字" in err

    def test_non_dict_option_rejected(self):
        payload, err = steering.build_ask_user_payload("q", ["a", {"id": "b", "label": "B"}])
        assert payload is None and "不是对象" in err


class TestFailureReport:
    def test_report_structure(self):
        txt = steering.build_failure_report(
            "依赖装不上", done="分析完日志", remaining="重装依赖", blocked_by="无网络")
        assert txt.startswith("【失败报告】")
        for seg in ("原因：依赖装不上", "已完成：分析完日志",
                    "未完成：重装依赖", "阻塞点：无网络"):
            assert seg in txt

    def test_report_reason_visible_even_if_empty(self):
        txt = steering.build_failure_report("")
        assert "原因：（未填写）" in txt

    def test_confirm_card_options(self):
        payload = steering.build_failure_confirm_payload("缺料", done="已出提纲")
        ids = [c["id"] for c in payload["choices"]]
        assert ids == [steering.FAILURE_ACCEPT_ID, steering.FAILURE_KEEP_TRYING_ID, "custom"]
        assert "缺料" in payload["text"] and "已出提纲" in payload["text"]


class TestAutoAnswerBudget:
    def test_decrement_and_exhaust(self):
        b = steering.AutoAnswerBudget(max_auto=2)
        assert b.take() == {"allowed": True, "remaining": 1}
        assert b.take() == {"allowed": True, "remaining": 0}
        assert b.take() == {"allowed": False, "remaining": 0}
        assert b.take() == {"allowed": False, "remaining": 0}   # 耗尽后永不放行

    def test_zero_budget_never_allows(self):
        b = steering.AutoAnswerBudget(max_auto=0)
        assert b.take()["allowed"] is False

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            steering.AutoAnswerBudget(max_auto=-1)

    def test_snapshot(self):
        b = steering.AutoAnswerBudget(max_auto=3)
        b.take()
        assert b.snapshot() == {"max_auto": 3, "remaining": 2}


class TestToolDefs:
    def test_names_and_required(self):
        ask = steering.ask_user_tool_def()["function"]
        rep = steering.report_failure_tool_def()["function"]
        assert ask["name"] == steering.ASK_USER_TOOL
        assert rep["name"] == steering.REPORT_FAILURE_TOOL
        assert ask["parameters"]["required"] == ["question", "options"]
        assert rep["parameters"]["required"] == ["reason"]
        assert rep["parameters"]["properties"]["mode"]["enum"] == ["confirm", "direct"]


# ── ② 执行面（soulmate_agent handler，真实 pending_choices 回流）──

class TestAskUserHandler:
    def test_attended_card_and_answer(self, tmp_path):
        async def main():
            a = _attended(tmp_path)
            task = asyncio.ensure_future(a._handle_ask_user("s1", {
                "question": "部署到哪个环境？", "options": OPTS}))
            await asyncio.sleep(0.05)
            assert a._pending_choices.get("s1") is not None      # 等待中
            card = _pushed_choice(a._client)
            assert [c["id"] for c in card["choices"]] == ["a", "b", "custom"]
            a.resolve_choice("s1", "生产环境")
            assert await task == "用户回答：生产环境"
            assert "s1" not in a._pending_choices                 # 回流后清理
        run(main())

    def test_unattended_never_blocks(self, tmp_path):
        async def main():
            a = make_agent(tmp_path)                              # _client=None
            result = await a._handle_ask_user("s1", {"question": "q", "options": OPTS})
            assert "无人值守" in result and "假设" in result
            assert "s1" not in a._pending_choices                 # 根本没挂起
        run(main())

    def test_param_error_visible(self, tmp_path):
        async def main():
            a = _attended(tmp_path)
            result = await a._handle_ask_user("s1", {"question": "q", "options": []})
            assert result.startswith("ask_user参数错误")
        run(main())


class TestReportFailureHandler:
    def test_attended_requires_user_decision(self, tmp_path):
        async def main():
            a = _attended(tmp_path)
            task = asyncio.ensure_future(a._handle_report_failure("s1", {
                "reason": "供应商 API 额度耗尽", "done": "已完成方案框架",
                "remaining": "报价明细"}))
            await asyncio.sleep(0.05)
            assert a._pending_choices.get("s1") is not None      # 终止需确认：在等用户
            card = _pushed_choice(a._client)
            ids = [c["id"] for c in card["choices"]]
            assert steering.FAILURE_ACCEPT_ID in ids
            assert steering.FAILURE_KEEP_TRYING_ID in ids
            a.resolve_choice("s1", "接受失败终止")
            result = await task
            assert "【失败报告】" in result and "额度耗尽" in result
            assert "用户决定：接受失败终止" in result
        run(main())

    def test_direct_mode_no_confirmation(self, tmp_path):
        async def main():
            a = _attended(tmp_path)
            result = await a._handle_report_failure(
                "s1", {"reason": "缺关键资料", "mode": "direct"})
            assert "【失败报告】" in result and "direct 模式" in result
            assert not a._client.updates                        # 不推确认卡
            assert "s1" not in a._pending_choices
        run(main())

    def test_reason_required(self, tmp_path):
        async def main():
            a = _attended(tmp_path)
            result = await a._handle_report_failure("s1", {"reason": "  "})
            assert result.startswith("report_failure参数错误")
        run(main())

    def test_bad_mode_visible(self, tmp_path):
        async def main():
            a = _attended(tmp_path)
            result = await a._handle_report_failure("s1", {"reason": "x", "mode": "yolo"})
            assert "mode 仅支持" in result
        run(main())

    def test_unattended_auto_answer_decrements_then_refuses(self, tmp_path):
        """autoAnswer 有界递减：预算内自动确认（留痕），耗尽后禁代拍板（禁私弃）。"""
        async def main():
            a = make_agent(tmp_path)                              # 无人值守
            r1 = await a._handle_report_failure("s1", {"reason": "r1"})
            assert "auto-answer 自动确认" in r1 and "剩余自动确认预算 1" in r1
            r2 = await a._handle_report_failure("s1", {"reason": "r2"})
            assert "auto-answer 自动确认" in r2 and "剩余自动确认预算 0" in r2
            r3 = await a._handle_report_failure("s1", {"reason": "r3"})
            assert "预算已耗尽" in r3
            assert "请勿放弃任务" in r3 and "禁假称完成" in r3    # 不许私自放弃
            assert "【失败报告】" in r3                            # 失败仍必须可见
        run(main())


# ── ③ 防跑飞合流：等待用户期暂停无进展看门狗（Pi #7×#10）──────────

class TestGuardPauseIntegration:
    def _guard(self, tmp_path):
        a = _attended(tmp_path)
        gt = GuardTools()
        a._guard_tools_inst = gt
        gt.run("run_guard", {"action": "configure"}, session_id="s1")
        return a, gt

    def test_ask_user_wait_pauses_watchdog(self, tmp_path):
        async def main():
            a, gt = self._guard(tmp_path)
            task = asyncio.ensure_future(a._handle_ask_user(
                "s1", {"question": "q", "options": OPTS}))
            await asyncio.sleep(0.05)
            assert gt._states["s1"]["guard"].snapshot()["paused"] is True
            a.resolve_choice("s1", "A")
            await task
            assert gt._states["s1"]["guard"].snapshot()["paused"] is False
        run(main())

    def test_clarify_wait_also_pauses(self, tmp_path):
        """既有 clarify 等待同样吃暂停语义（合流：同一等待同一纪律）。"""
        async def main():
            a, gt = self._guard(tmp_path)
            task = asyncio.ensure_future(a._handle_clarify(
                "s1", {"question": "选方向", "options": OPTS}))
            await asyncio.sleep(0.05)
            assert gt._states["s1"]["guard"].snapshot()["paused"] is True
            a.resolve_choice("s1", "A")
            await task
            assert gt._states["s1"]["guard"].snapshot()["paused"] is False
        run(main())

    def test_no_guard_state_noop(self, tmp_path):
        """守卫未启用不凭空建状态（pause_guard 只作用于既有守卫）。"""
        async def main():
            a = _attended(tmp_path)
            gt = GuardTools()
            a._guard_tools_inst = gt
            assert gt.pause_guard("s1") is False and "s1" not in gt._states
        run(main())

    def test_steering_interject_not_blocked_during_wait(self, tmp_path):
        """中途插话：等待 ask_user 作答期间用户插话照常入队（不被等待阻塞）。"""
        async def main():
            a, gt = self._guard(tmp_path)
            task = asyncio.ensure_future(a._handle_ask_user(
                "s1", {"question": "q", "options": OPTS}))
            await asyncio.sleep(0.05)
            msg = a._steering.enqueue("s1", "顺便先看下日志")
            assert msg is not None and a._steering.pending("s1") == 1
            a.resolve_choice("s1", "A")
            await task
            assert [m.text for m in a._steering.drain("s1")] == ["顺便先看下日志"]
        run(main())


# ── ④ 接线钉（工具表/分发面）──────────────────────────────────────

class TestWiringPins:
    def test_tools_registered_in_all_tools(self):
        src = inspect.getsource(sma)
        assert "human_tools = [ask_user_tool_def(), report_failure_tool_def()]" in src
        assert "+ [clarify_tool] + human_tools" in src

    def test_dispatch_wiring(self):
        src = inspect.getsource(sma)
        assert 'elif func_name == "ask_user":' in src
        assert "result = await self._handle_ask_user(session_id, func_args)" in src
        assert 'elif func_name == "report_failure":' in src
        assert "result = await self._handle_report_failure(session_id, func_args)" in src

    def test_await_choice_single_source(self):
        """三个工具共用一次等待（同一超时/同一 run_guard 暂停语义）。"""
        src = inspect.getsource(SoulMateAgent)
        assert src.count("self._await_choice(session_id)") == 3
