# -*- coding: utf-8 -*-
"""proxy.py HTTP /acp/send入口的relay轨迹接线（10:02轮遗留#3销账）。

背景：ws_acp（WS /ws/acp）的hermes回合已由RelayTrajectory记账，但HTTP
/acp/send（proxy.py硬编码hermes acp路由）不经ws_acp观察者——该入口的
hermes回合仍零记账，/trajectory观察面对HTTP入口全盲。

安全声明（测试即规格）：
1. per-session观察者隔离——跨会话并发各记各的（_session_locks已把同会话
   FIFO串行=单回合在途），A会话的chunk/response绝不落到B的root上；
2. 路由纪律——session/update按params.sessionId路由（缺失才广播）；
   prompt响应按msg_id→sid路由，未知id/迟到重复响应no-op不炸；
3. 回合确定已死（写管道失败/超时/子进程退出）→显式收口error，不悬挂root；
4. CLI兜底回合（hermes -z）同样记账——用户拿到了回答，观察面不能只有
   ACP失败回合（禁止静默降级的观察面语义）；
5. 记账失败绝不阻塞会话流——所有入口never-raise，畸形消息不炸不落脏账；
6. 接线钉（写了≠接线了）——observe_client/_traj_fail_turn在_prompt_parts
   真实路径、observe_agent在_read_loop真实路径、cli_turn在_send_message_inner
   CLI兜底处、finalize_all在read_loop退出收尾处；
7. 双记防护——proxy.py硬编码hermes引擎（非soulmate）恒记账是安全的，
   soulmate深记录只在ws_acp DEFAULT_ROUTE子进程内，两条路径不重叠。
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import proxy
from agent.relay_trajectory import RelayTrajectory


def run(coro):
    return asyncio.run(coro)


class FakeRecorder:
    """替身recorder：捕获调用序列，不碰网络。"""

    def __init__(self, root_id="root-1"):
        self.calls = []
        self.root_id = root_id
        self._n = 0

    async def start_turn(self, session_id, user_text="", agent_id="soulmate"):
        self._n += 1
        self.calls.append(("start_turn", session_id, user_text, agent_id))
        return f"{self.root_id}-{self._n}"

    async def record_llm_call(self, session_id, parent_id, content="", duration_ms=0.0,
                              token_usage=0, status="ok", tools=None,
                              agent_id="soulmate", source=""):
        self.calls.append(("record_llm_call", session_id, parent_id, content, status,
                           list(tools or []), agent_id, source, duration_ms))
        return "llm-1"

    async def end_turn(self, session_id, root_id, status="ok", token_usage=0):
        self.calls.append(("end_turn", session_id, root_id, status))


def _acp(rec=None):
    return proxy.ACPProcess(traj_recorder=rec or FakeRecorder())


def _prompt_req(msg_id, sid, text):
    """_prompt_parts构造的真实session/prompt请求形状（id=str）。"""
    return {
        "jsonrpc": "2.0", "id": str(msg_id), "method": "session/prompt",
        "params": {"prompt": [{"type": "text", "text": text}], "sessionId": sid},
    }


def _chunk(sid, t):
    return {
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"sessionId": sid,
                   "update": {"sessionUpdate": "agent_message_chunk",
                              "content": {"type": "text", "text": t}}},
    }


def _resp(msg_id, stop="end_turn"):
    return {"jsonrpc": "2.0", "id": str(msg_id), "result": {"stopReason": stop}}


async def _drain(acp):
    for relay in acp._traj_relays.values():
        await relay.drain()


# ── 1. 全链路由：prompt→chunk→response → start/record/end ────────

def test_http_turn_recorded_end_to_end():
    rec = FakeRecorder()
    acp = _acp(rec)
    sid = "om-http-1"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, sid, "你好HTTP"), sid)
        acp._traj_observe_agent(_chunk(sid, "你好"))
        acp._traj_observe_agent(_chunk(sid, "，HTTP"))
        acp._traj_observe_agent(_resp(1))
        await _drain(acp)
    run(flow())

    kinds = [c[0] for c in rec.calls]
    assert kinds == ["start_turn", "record_llm_call", "end_turn"], kinds
    _, rsid, text, agent = rec.calls[0]
    assert rsid == sid and text == "你好HTTP" and agent == "hermes"
    _, _, parent, content, status, tools, agent2, source, dur = rec.calls[1]
    assert parent.startswith("root-1") and content == "你好，HTTP"
    assert status == "ok" and tools == [] and agent2 == "hermes" and source == "relay"
    assert rec.calls[2][0] == "end_turn" and rec.calls[2][3] == "ok"


# ── 2. per-session隔离：跨会话并发各记各的 ───────────────────────

def test_concurrent_sessions_isolated():
    rec = FakeRecorder()
    acp = _acp(rec)
    a, b = "om-http-A", "om-http-B"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, a, "问题A"), a)
        acp._traj_observe_client(_prompt_req(2, b, "问题B"), b)
        acp._traj_observe_agent(_chunk(a, "答案A"))
        acp._traj_observe_agent(_chunk(b, "答案B"))
        acp._traj_observe_agent(_resp(1))
        acp._traj_observe_agent(_resp(2))
        await _drain(acp)
    run(flow())

    starts = [c for c in rec.calls if c[0] == "start_turn"]
    recs = [c for c in rec.calls if c[0] == "record_llm_call"]
    ends = [c for c in rec.calls if c[0] == "end_turn"]
    assert len(starts) == 2 and len(recs) == 2 and len(ends) == 2
    by_sid = {c[1]: c for c in recs}
    assert by_sid[a][3] == "答案A" and by_sid[b][3] == "答案B", by_sid
    # 血缘不串：record挂在各自start的root下
    assert by_sid[a][2] == "root-1-1" and by_sid[b][2] == "root-1-2"


# ── 3. 路由纪律 ──────────────────────────────────────────────────

def test_session_update_routes_by_session_id():
    rec = FakeRecorder()
    acp = _acp(rec)
    a, b = "om-http-A", "om-http-B"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, a, "问A"), a)
        acp._traj_observe_client(_prompt_req(2, b, "问B"), b)
        acp._traj_observe_agent(_chunk(b, "只给B"))  # A的回合不该收到
        acp._traj_observe_agent(_resp(1, stop="refusal"))  # A以refusal收口
        acp._traj_observe_agent(_resp(2))
        await _drain(acp)
    run(flow())

    recs = [c for c in rec.calls if c[0] == "record_llm_call"]
    by_sid = {c[1]: c for c in recs}
    assert by_sid[a][3] == "" and by_sid[a][4] == "error"  # A无chunk+refusal→error
    assert by_sid[b][3] == "只给B" and by_sid[b][4] == "ok"


def test_session_update_without_sid_broadcasts_only_to_open_turns():
    rec = FakeRecorder()
    acp = _acp(rec)
    a = "om-http-A"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, a, "问"), a)
        orphan = dict(_chunk("", "广播"))  # sessionId缺失=协议违规
        acp._traj_observe_agent(orphan)
        acp._traj_observe_agent(_resp(1))
        await _drain(acp)
    run(flow())

    recs = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert len(recs) == 1 and recs[0][3] == "广播"


def test_unknown_and_late_responses_noop():
    rec = FakeRecorder()
    acp = _acp(rec)
    sid = "om-http-1"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, sid, "问"), sid)
        acp._traj_observe_agent(_resp(99))       # 未知id：no-op不收口
        acp._traj_observe_agent(_chunk(sid, "答"))
        acp._traj_observe_agent(_resp(1))
        acp._traj_observe_agent(_resp(1))        # 迟到重复响应：no-op不双记
        acp._traj_observe_agent({"jsonrpc": "2.0", "id": "7"})  # 无result/error：no-op
        acp._traj_observe_agent("not-a-dict")    # 畸形：never-raise
        acp._traj_observe_agent({"method": "session/update", "params": {"update": "bad"}})
        await _drain(acp)
    run(flow())

    kinds = [c[0] for c in rec.calls]
    assert kinds == ["start_turn", "record_llm_call", "end_turn"], kinds
    assert rec.calls[1][3] == "答"


# ── 4. 回合确定已死→显式收口error（不悬挂root） ──────────────────

def test_fail_turn_closes_open_turn_error():
    rec = FakeRecorder()
    acp = _acp(rec)
    sid = "om-http-1"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, sid, "问"), sid)
        acp._traj_fail_turn("1", sid)  # 写管道失败/超时路径
        await _drain(acp)
    run(flow())

    kinds = [c[0] for c in rec.calls]
    assert kinds == ["start_turn", "record_llm_call", "end_turn"], kinds
    assert rec.calls[1][4] == "error" and rec.calls[2][3] == "error"
    assert "1" not in acp._traj_prompt_sid  # msg_id映射已清


def test_finalize_all_closes_all_open_turns():
    rec = FakeRecorder()
    acp = _acp(rec)
    a, b = "om-http-A", "om-http-B"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, a, "问A"), a)
        acp._traj_observe_client(_prompt_req(2, b, "问B"), b)
        acp._traj_finalize_all("error")  # read_loop退出收尾
        await _drain(acp)
    run(flow())

    ends = [c for c in rec.calls if c[0] == "end_turn"]
    assert len(ends) == 2 and all(c[3] == "error" for c in ends), ends
    assert acp._traj_prompt_sid == {}


# ── 5. CLI兜底回合记账（禁止静默降级的观察面语义） ───────────────

def test_cli_fallback_turn_recorded():
    rec = FakeRecorder()
    acp = _acp(rec)
    sid = "om-http-1"

    async def flow():
        acp._traj_cli_turn("问题", {"response_text": "CLI答案", "stopReason": "end_turn"},
                           sid, duration_ms=1234.0)
        await _drain(acp)
    run(flow())

    kinds = [c[0] for c in rec.calls]
    assert kinds == ["start_turn", "record_llm_call", "end_turn"], kinds
    assert rec.calls[0][2] == "问题"
    _, _, parent, content, status, tools, agent, source, dur = rec.calls[1]
    assert content == "CLI答案" and status == "ok" and source == "relay"
    assert agent == "hermes" and dur == 1234.0
    assert rec.calls[2][3] == "ok"


def test_cli_turn_refusal_marks_error_and_supersedes_open_turn():
    rec = FakeRecorder()
    acp = _acp(rec)
    sid = "om-http-1"

    async def flow():
        acp._traj_observe_client(_prompt_req(1, sid, "被挤掉的回合"), sid)
        acp._traj_cli_turn("问题", {"response_text": "兜底", "stopReason": "refusal"}, sid)
        await _drain(acp)
    run(flow())

    kinds = [c[0] for c in rec.calls]
    # 前序未收口回合先判error（挤掉语义），再CLI回合start/record/end
    assert kinds == ["start_turn", "record_llm_call", "end_turn",
                     "start_turn", "record_llm_call", "end_turn"], kinds
    assert rec.calls[1][4] == "error" and rec.calls[2][3] == "error"  # 被挤掉的
    assert rec.calls[4][4] == "error" and rec.calls[5][3] == "error"  # refusal→error


def test_cli_turn_empty_sid_writes_nothing():
    rec = FakeRecorder()
    acp = _acp(rec)

    async def flow():
        acp._traj_cli_turn("问", {"response_text": "答"}, "")
        await _drain(acp)
    run(flow())
    assert rec.calls == []


# ── 6. RelayTrajectory.observe_cli_turn 单元（新公共面） ──────────

def test_relay_observe_cli_turn_never_raise_and_unit_shape():
    rec = FakeRecorder()
    rt = RelayTrajectory(agent_id="hermes", session_id_getter=lambda: "om-1", recorder=rec)

    async def flow():
        rt.observe_cli_turn("问", "答", stop_reason="", duration_ms=50.0)
        rt.observe_cli_turn(None, None)  # 畸形入参never-raise  # pyright: ignore[reportArgumentType]
        await rt.drain()
    run(flow())

    kinds = [c[0] for c in rec.calls]
    assert kinds.count("start_turn") == 2  # 两回合都尝试（后者text None→coerce）
    assert rt.turns_seen == 2


def test_relay_finalize_open_noop_without_turn():
    rt = RelayTrajectory(agent_id="hermes", session_id_getter=lambda: "om-1",
                        recorder=FakeRecorder())
    rt.finalize_open("error")  # 无未收口回合：no-op不炸
    rt.finalize_open()  # 默认error


# ── 7. 接线钉（写了≠接线了） ────────────────────────────────────

def test_proxy_wiring_pins():
    src = (Path(__file__).parent.parent / "proxy.py").read_text(encoding="utf-8")
    # observe_client在_prompt_parts真实路径、写stdin之前（防响应先到竞态）
    pp_pos = src.index("async def _prompt_parts")
    next_def = src.index("async def _cli", pp_pos)  # _prompt_parts的收尾=下一个方法
    pp_body = src[pp_pos:next_def]
    assert re.search(r"self\._traj_observe_client\(request, session_id\)", pp_body)
    assert (pp_body.index("self._traj_observe_client")
            < pp_body.index("self._proc.stdin.write")), "observe_client必须在写stdin前"
    # 3个死亡收口点：stdin不可用/写失败/超时
    assert pp_body.count("self._traj_fail_turn(msg_id, session_id)") == 3, \
        "BrokenPipe/写失败/超时三处都必须收口"
    # observe_agent在_read_loop真实路径、id=0过滤之后（只记用户可见面）
    rl_pos = src.index("async def _read_loop")
    rl_end = src.index("# ── relay轨迹观察者", rl_pos)  # 本方法收尾=辅助方法块起点
    rl_body = src[rl_pos:rl_end]
    assert re.search(r"self\._traj_observe_agent\(msg\)", rl_body)
    assert (rl_body.index("Ignoring system message id=0")
            < rl_body.index("self._traj_observe_agent(msg)")), \
        "observe_agent必须在id=0过滤之后（与chunk收集同口径）"
    # read_loop退出收尾：所有未收口回合判error
    assert re.search(r"self\._traj_finalize_all\(\"error\"\)", rl_body)
    # CLI兜底记账在_send_message_inner的_cli()调用后
    sm_pos = src.index("async def _send_message_inner")
    sm_end = src.index("async def send_message_with_image")
    sm_body = src[sm_pos:sm_end]
    assert (sm_body.index("await self._cli(text)")
            < sm_body.index("self._traj_cli_turn(")), "CLI兜底记账必须在_cli()拿到结果后"
    # per-session观察者构造
    assert re.search(r"RelayTrajectory\(\s*\n\s*agent_id=\"hermes\"", src)
    # 双记防护：proxy硬编码hermes引擎（非soulmate深记录子进程），恒记账安全
    assert '"hermes", "acp", "--accept-hooks"' in src
    # observe入口全部never-raise（记账失败绝不阻塞会话流）
    for name in ("_traj_observe_client", "_traj_observe_agent",
                 "_traj_fail_turn", "_traj_finalize_all", "_traj_cli_turn",
                 "_traj_relay_for"):
        body = src[src.index(f"def {name}"):]
        body = body[:body.index("def ", 10)]
        assert "except Exception" in body, f"{name}必须never-raise"


def test_relay_cli_turn_is_public_api():
    """observe_cli_turn/finalize_open是RelayTrajectory公共面（proxy调用的真源）。"""
    src = (Path(__file__).parent.parent / "agent" / "relay_trajectory.py").read_text(encoding="utf-8")
    assert re.search(r"def observe_cli_turn\(", src)
    assert re.search(r"def finalize_open\(", src)
    assert 'source="relay"' in src
