"""P1 交互式提问卡（多问题/多选）——clarify 升级为 Warp AskUserQuestion 形态

调研来源：SUMMARY.md 前端差距表「交互式提问卡（多问题/多选）| acp-approval-modal单问题 |
部分有 | Warp AskUserQuestion/Dify表单 | P1」。单问题/单选的 clarify 菜单升级为
多问题×多选×可自定义的提问卡：一张卡一次作答，作答文本经既有 pending_choices
闸门回流（resolve_choice），LLM 拿到的工具结果=作答文本。

契约钉：
- 单问题旧形态 payload 逐字不变（回归钉）
- 多问题形态 payload={"type":"choice","text":title,"questions":[…]}
- 参数错误必须可见返回（不静默）
- 超时消息格式与旧契约一致（"用户未在60秒内选择，请继续"）
"""
import asyncio
import inspect
import json

import pytest

import agent.soulmate_agent as sma
from agent.soulmate_agent import SoulMateAgent
from tests.test_loop_guard_wiring import FakeGateAllow, _tc, _wire_agent
from tests.test_steering import make_agent, run


class FakeClient:
    """捕获 session_update 推送的提问卡 payload"""

    def __init__(self, fail: bool = False):
        self.updates = []
        self.fail = fail

    async def session_update(self, session_id=None, update=None, **kw):
        if self.fail:
            raise RuntimeError("ws broken")
        self.updates.append((session_id, update))


def _pushed_json(client: FakeClient) -> dict:
    """从捕获的session_update流里提取提问卡payload（跳过普通文本chunk）"""
    assert client.updates, "session_update 未被调用（提问卡没推给前端）"
    for _sid, update in reversed(client.updates):
        try:
            obj = json.loads(update.content.text)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("type") == "choice":
            return obj
    raise AssertionError("session_update 流中没有提问卡 payload（type=choice）")


def _agent(tmp_path):
    a = make_agent(tmp_path)
    a._client = FakeClient()
    a._permission_gate = FakeGateAllow()
    return a


MULTI_ARGS = {
    "question": "部署确认",
    "questions": [
        {"id": "env", "question": "部署环境",
         "options": [{"id": "prod", "label": "生产"}, {"id": "staging", "label": "预发", "description": "灰度"}],
         "multi": True, "allow_custom": False},
        {"question": "数据库",
         "options": [{"id": "pg", "label": "PostgreSQL"}, {"id": "my", "label": "MySQL"}],
         "multi": False, "allow_custom": True},
    ],
}


# ── ① payload 构建 ──────────────────────────────────────────────

class TestBuildPayload:
    def test_legacy_shape_byte_identical(self, tmp_path):
        """单问题旧形态 payload 逐字不变（回归钉）"""
        a = _agent(tmp_path)
        payload, err = a._build_clarify_payload({
            "question": "选方向",
            "options": [{"id": "a", "label": "A", "description": "da"},
                        {"id": "b", "label": "B"}],
        })
        assert err == ""
        assert payload == {
            "type": "choice", "text": "选方向",
            "choices": [{"id": "a", "label": "A", "description": "da"},
                        {"id": "b", "label": "B", "description": ""}],
        }

    def test_multi_shape(self, tmp_path):
        a = _agent(tmp_path)
        payload, err = a._build_clarify_payload(MULTI_ARGS)
        assert err == ""
        assert payload["type"] == "choice"
        assert payload["text"] == "部署确认"
        qs = payload["questions"]
        assert len(qs) == 2
        assert qs[0]["id"] == "env" and qs[0]["multi"] is True and qs[0]["allow_custom"] is False
        assert qs[0]["options"] == [{"id": "prod", "label": "生产", "description": ""},
                                    {"id": "staging", "label": "预发", "description": "灰度"}]
        # id 缺省 → qN
        assert qs[1]["id"] == "q2" and qs[1]["multi"] is False and qs[1]["allow_custom"] is True

    def test_multi_title_fallback(self, tmp_path):
        a = _agent(tmp_path)
        payload, err = a._build_clarify_payload({"questions": MULTI_ARGS["questions"]})
        assert err == ""
        assert payload["text"] == "请回答以下问题："

    def test_multi_too_many_questions(self, tmp_path):
        a = _agent(tmp_path)
        qs = [{"question": f"Q{i}", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}
              for i in range(6)]
        payload, err = a._build_clarify_payload({"questions": qs})
        assert payload is None and "1-5" in err

    def test_multi_question_not_dict(self, tmp_path):
        a = _agent(tmp_path)
        payload, err = a._build_clarify_payload({"questions": ["bad"]})
        assert payload is None and "不是对象" in err

    def test_multi_missing_question_text(self, tmp_path):
        a = _agent(tmp_path)
        payload, err = a._build_clarify_payload({
            "questions": [{"question": "  ", "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}]}]})
        assert payload is None and "question 必填" in err

    def test_multi_too_few_options(self, tmp_path):
        a = _agent(tmp_path)
        payload, err = a._build_clarify_payload({
            "questions": [{"question": "Q", "options": [{"id": "a", "label": "A"}]}]})
        assert payload is None and "2-8" in err

    def test_multi_invalid_options_filtered(self, tmp_path):
        """id/label 缺失的选项不算有效选项，不足2个→显式错误"""
        a = _agent(tmp_path)
        payload, err = a._build_clarify_payload({
            "questions": [{"question": "Q", "options": [{"id": "a", "label": "A"}, {"label": ""}, "junk"]}]})
        assert payload is None and "2-8" in err


# ── ② handler 执行面 ────────────────────────────────────────────

class TestHandleClarify:
    def test_multi_push_and_answer_flow(self, tmp_path):
        """多问题卡推出+用户作答经resolve_choice回流=工具结果"""
        a = _agent(tmp_path)

        async def scenario():
            async def _answer():
                while "s1" not in a._pending_choices:
                    await asyncio.sleep(0.01)
                assert a.resolve_choice("s1", "部署环境：生产、预发\n数据库：PostgreSQL")
            asyncio.ensure_future(_answer())
            return await a._handle_clarify("s1", MULTI_ARGS)

        result = run(scenario())
        pushed = _pushed_json(a._client)
        assert pushed["questions"][0]["multi"] is True
        assert result == "部署环境：生产、预发\n数据库：PostgreSQL"
        assert "s1" not in a._pending_choices

    def test_legacy_push_and_answer_flow(self, tmp_path):
        """单问题旧形态：payload逐字不变+等待回流"""
        a = _agent(tmp_path)

        async def scenario():
            async def _answer():
                while "s1" not in a._pending_choices:
                    await asyncio.sleep(0.01)
                a.resolve_choice("s1", "A")
            asyncio.ensure_future(_answer())
            return await a._handle_clarify("s1", {
                "question": "选哪个", "options": [{"id": "a", "label": "A"}]})

        result = run(scenario())
        assert _pushed_json(a._client) == {
            "type": "choice", "text": "选哪个",
            "choices": [{"id": "a", "label": "A", "description": ""}]}
        assert result == "A"

    def test_wait_message_contract(self, tmp_path):
        """超时消息格式=旧契约（默认60s→'用户未在60秒内选择，请继续'）"""
        assert SoulMateAgent.CLARIFY_WAIT_TIMEOUT_S == 60.0
        assert f"用户未在{int(SoulMateAgent.CLARIFY_WAIT_TIMEOUT_S)}秒内选择，请继续" == \
            "用户未在60秒内选择，请继续"

    def test_timeout_visible_and_clean(self, tmp_path):
        a = _agent(tmp_path)
        a.CLARIFY_WAIT_TIMEOUT_S = 0.05

        async def scenario():
            return await a._handle_clarify("s1", MULTI_ARGS)

        result = run(scenario())
        assert "秒内选择，请继续" in result
        assert "s1" not in a._pending_choices

    def test_validation_error_visible_no_wait(self, tmp_path):
        """参数错误必须可见返回，且不注册等待（不挂起回合）"""
        a = _agent(tmp_path)

        async def scenario():
            return await a._handle_clarify("s1", {"questions": [{"question": "Q", "options": []}]})

        result = run(scenario())
        assert result.startswith("clarify参数错误")
        assert "s1" not in a._pending_choices
        assert a._client.updates == []  # 参数错误不推半截卡

    def test_client_failure_visible(self, tmp_path):
        """前端通道故障→'clarify失败: …'可见，不假装已展示"""
        a = _agent(tmp_path)
        a._client = FakeClient(fail=True)

        async def scenario():
            return await a._handle_clarify("s1", MULTI_ARGS)

        result = run(scenario())
        assert result.startswith("clarify失败:")
        assert "s1" not in a._pending_choices

    def test_no_client_still_waits(self, tmp_path):
        """无前端连接（headless）：不推卡但等待语义保留，作答可回流"""
        a = _agent(tmp_path)
        a._client = None
        a.CLARIFY_WAIT_TIMEOUT_S = 0.5

        async def scenario():
            async def _answer():
                while "s1" not in a._pending_choices:
                    await asyncio.sleep(0.01)
                a.resolve_choice("s1", "环境：生产")
            asyncio.ensure_future(_answer())
            return await a._handle_clarify("s1", MULTI_ARGS)

        assert run(scenario()) == "环境：生产"


# ── ③ prompt() 选择闸门（作答回流入口）────────────────────────

class TestPromptChoiceGate:
    def test_pending_choice_resolves_and_ends_turn(self, tmp_path):
        """prompt()到达时有等待中的提问卡→作答文本唤醒future+end_turn（不跑新回合）"""
        a = make_agent(tmp_path)

        async def scenario():
            future = asyncio.get_event_loop().create_future()
            a._pending_choices["s1"] = future
            resp = await a.prompt(
                [type("B", (), {"text": "部署环境：生产"})()], "s1")
            return resp, future

        resp, future = run(scenario())
        assert resp.stop_reason == "end_turn"
        assert future.done() and future.result() == "部署环境：生产"
        assert a.llm_engine.seen_messages == []  # 未触发新LLM回合

    def test_no_pending_choice_falls_through(self, tmp_path):
        """无等待中的提问卡→不吞消息（不走choice捷径，resolve_choice不被调用）"""
        a = make_agent(tmp_path)
        calls = []
        a.resolve_choice = lambda sid, res: calls.append((sid, res)) or True

        async def scenario():
            return await a.prompt(
                [type("B", (), {"text": "hello"})()], "unknown-session")

        run(scenario())
        assert calls == []  # 消息没有被choice闸门吞掉
        assert a._pending_choices == {}


# ── ④ 真实 _run_llm_with_tools 循环 E2E ─────────────────────────

class TestRealLoopE2E:
    def test_multi_clarify_round_trip(self, tmp_path):
        """真实工具循环：clarify(questions)工具调用→提问卡推送→作答回流为工具结果进第2轮上下文"""
        call = _tc("clarify", args=MULTI_ARGS, tc_id="q1")
        a = _wire_agent(tmp_path, [[call], ["已收到，开始部署"]])
        a._client = FakeClient()

        async def scenario():
            async def _answer():
                while "s1" not in a._pending_choices:
                    await asyncio.sleep(0.01)
                a.resolve_choice("s1", "部署环境：生产\n数据库：PostgreSQL")
            asyncio.ensure_future(_answer())
            return await a._run_llm_with_tools(
                [{"role": "user", "content": "帮我部署"}], "s1")

        resp, tools = run(scenario())
        assert resp == "已收到，开始部署"
        pushed = _pushed_json(a._client)
        assert len(pushed["questions"]) == 2
        # 作答文本作为clarify工具结果进第2轮LLM上下文
        round2 = a.llm_engine.seen_messages[1]
        tool_msgs = [m for m in round2 if m.get("role") == "tool"]
        assert any("部署环境：生产" in str(m.get("content", "")) for m in tool_msgs)
        # 工具账本留痕
        assert any(t.get("tool") == "clarify" or t.get("name") == "clarify"
                   or "clarify" in str(t) for t in tools)

    def test_legacy_clarify_round_trip(self, tmp_path):
        """旧单问题形态在真实循环里行为不变"""
        call = _tc("clarify", args={"question": "方向？", "options": [{"id": "a", "label": "A"}]},
                   tc_id="q1")
        a = _wire_agent(tmp_path, [[call], ["好的"]])
        a._client = FakeClient()

        async def scenario():
            async def _answer():
                while "s1" not in a._pending_choices:
                    await asyncio.sleep(0.01)
                a.resolve_choice("s1", "A")
            asyncio.ensure_future(_answer())
            return await a._run_llm_with_tools(
                [{"role": "user", "content": "go"}], "s1")

        resp, _tools = run(scenario())
        assert resp == "好的"
        assert _pushed_json(a._client)["choices"] == [{"id": "a", "label": "A", "description": ""}]


# ── ⑤ 接线钉（防脱敏/防漂移）────────────────────────────────────

class TestWiringPins:
    def test_loop_dispatch_calls_method(self):
        """工具循环的clarify分支必须调用_handle_clarify（不是死代码）"""
        src = inspect.getsource(sma)
        assert 'elif func_name == "clarify":' in src
        assert "result = await self._handle_clarify(session_id, func_args)" in src

    def test_schema_has_questions(self):
        src = inspect.getsource(sma)
        assert '"questions": {' in src
        assert '"multi": {"type": "boolean"' in src
        assert '"allow_custom": {"type": "boolean"' in src

    def test_timeout_constant_single_source(self):
        src = inspect.getsource(SoulMateAgent._handle_clarify)
        assert "self.CLARIFY_WAIT_TIMEOUT_S" in src

    def test_payload_shape_constant_type_choice(self):
        """两形态同走 type:"choice" 通道（前端既有解析面）"""
        src = inspect.getsource(SoulMateAgent._build_clarify_payload)
        assert src.count('"type": "choice"') == 2
