# -*- coding: utf-8 -*-
"""RelayTrajectory — 非soulmate引擎回合trajectory记账（04:20轮遗留#4销账）。

安全声明（测试即规格）：
1. 观察绝不阻塞/破坏透传——observe_*同步never-raise，畸形消息（非dict/坏JSON
   content/无id的tool_call_update）不炸不落脏账；
2. 记账顺序保真——start_turn→record_llm_call→end_turn按回合顺序落（单worker），
   血缘root_id经turn状态对象传递；
3. 失败必须可见（mem0 §1.1）——响应error/stopReason∈{error,refusal,cancelled}、
   新prompt挤掉旧回合、连接关闭未收口→status=error；工具failed/cancelled→
   tool_call span status=error；
4. 无session id（session/new响应未到）→零写入，绝不写无主span；
5. 双形状工具解析——平台形态（content.text=JSON字符串）与标准ACP形态
   （toolCallId/title/rawInput直接在update上）都认；
6. ws_acp接线钉——observer只对非soulmate路由启用（防与引擎内深记录双记），
   observe_client/observe_agent/close三个调用点在透传真实路径上；
7. recorder兼容钉——agent_id/source默认值下soulmate既有payload逐字不变。
"""

import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.relay_trajectory import RelayTrajectory
from agent.trajectory_recorder import TrajectoryRecorder, llm_outcome_status
import ws_acp


def run(coro):
    return asyncio.run(coro)


class FakeRecorder:
    """替身recorder：捕获调用序列，不碰网络。"""

    def __init__(self, root_id="root-1"):
        self.calls = []
        self.root_id = root_id

    async def start_turn(self, session_id, user_text="", agent_id="soulmate"):
        self.calls.append(("start_turn", session_id, user_text, agent_id))
        return self.root_id

    async def record_llm_call(self, session_id, parent_id, content="", duration_ms=0.0,
                              token_usage=0, status="ok", tools=None,
                              agent_id="soulmate", source=""):
        self.calls.append(("record_llm_call", session_id, parent_id, content, status,
                           list(tools or []), agent_id, source))
        return "llm-1"

    async def end_turn(self, session_id, root_id, status="ok", token_usage=0):
        self.calls.append(("end_turn", session_id, root_id, status))


def _rt(recorder=None, sid="om-relay-1", agent="hermes"):
    return RelayTrajectory(
        agent_id=agent,
        session_id_getter=lambda: sid,
        recorder=recorder or FakeRecorder(),
    )


async def _feed(rt, msgs):
    """按序喂（dir, msg）：'c'=客户端→agent，'a'=agent→客户端。"""
    for dir_, m in msgs:
        if dir_ == "c":
            rt.observe_client(m)
        else:
            rt.observe_agent(m)
    await rt.drain()


PROMPT = {
    "jsonrpc": "2.0", "id": 7, "method": "session/prompt",
    "params": {"sessionId": "om-relay-1", "messageId": "m1",
               "prompt": [{"type": "text", "text": "你好世界"}]},
}
CHUNK = lambda t: {
    "jsonrpc": "2.0", "method": "session/update",
    "params": {"sessionId": "om-relay-1",
               "update": {"sessionUpdate": "agent_message_chunk",
                          "content": {"type": "text", "text": t}}},
}
RESPONSE = lambda stop="end_turn": {
    "jsonrpc": "2.0", "id": 7, "result": {"stopReason": stop},
}


# ── 1. 基本回合：prompt→chunks→响应 → start/record/end序列 ─────

def test_basic_turn_recorded():
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_feed(rt, [
        ("c", PROMPT),
        ("a", CHUNK("你好")),
        ("a", CHUNK("，世界")),
        ("a", RESPONSE()),
    ]))
    kinds = [c[0] for c in rec.calls]
    assert kinds == ["start_turn", "record_llm_call", "end_turn"], kinds
    _, sid, text, agent = rec.calls[0]
    assert sid == "om-relay-1" and text == "你好世界" and agent == "hermes"
    _, sid2, parent, content, status, tools, agent2, source = rec.calls[1]
    assert parent == "root-1" and content == "你好，世界"
    assert status == "ok" and tools == [] and agent2 == "hermes" and source == "relay"
    assert rec.calls[2] == ("end_turn", "om-relay-1", "root-1", "ok")
    assert rt.turns_seen == 1


def test_duration_positive_and_no_token_claim():
    rec = FakeRecorder()
    rt = _rt(rec)

    async def go():
        rt.observe_client(PROMPT)
        rt.observe_agent(CHUNK("x"))
        rt.observe_agent(RESPONSE())
        await rt.drain()

    run(go())
    # record_llm_call调用里duration由t0推导（FakeRecorder未收duration，形状由
    # recorder层钉）；此处钉token_usage默认0不虚报
    assert rec.calls[1][0] == "record_llm_call"


# ── 2. 工具账本：双形状解析 + failed→error ──────────────────────

def test_tool_platform_json_shape_and_failed_state():
    rec = FakeRecorder()
    rt = _rt(rec)
    tool_call = {
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"update": {"sessionUpdate": "tool_call",
                              "content": {"type": "text", "text": json.dumps(
                                  {"toolCallId": "tc-1", "name": "terminal",
                                   "arguments": "ls -la"})}}},
    }
    tool_update = {
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"update": {"sessionUpdate": "tool_call_update",
                              "content": {"type": "text", "text": json.dumps(
                                  {"toolCallId": "tc-1", "state": "failed",
                                   "content": "boom"})}}},
    }
    run(_feed(rt, [("c", PROMPT), ("a", CHUNK("执行中")), ("a", tool_call),
                   ("a", tool_update), ("a", RESPONSE())]))
    _, _, _, _, _, tools, _, _ = rec.calls[1]
    assert len(tools) == 1
    t = tools[0]
    assert t["name"] == "terminal" and t["arguments"] == "ls -la"
    assert t["result_preview"] == "boom" and t["permission"] == "failed"
    # 失败必须可见：permission位的failed/cancelled → tool span status=error
    assert TrajectoryRecorder._tool_status(t) == "error"


def test_tool_standard_acp_shape():
    rec = FakeRecorder()
    rt = _rt(rec)
    tool_call = {
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"update": {"sessionUpdate": "tool_call", "toolCallId": "tc-9",
                              "title": "read_file", "rawInput": {"path": "a.txt"}}},
    }
    tool_update = {
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"update": {"sessionUpdate": "tool_call_update", "toolCallId": "tc-9",
                              "state": "completed", "rawOutput": "file-content"}},
    }
    run(_feed(rt, [("c", PROMPT), ("a", tool_call), ("a", tool_update),
                   ("a", RESPONSE())]))
    _, _, _, _, _, tools, _, _ = rec.calls[1]
    assert len(tools) == 1
    t = tools[0]
    assert t["name"] == "read_file"
    assert json.loads(t["arguments"]) == {"path": "a.txt"}
    assert t["result_preview"] == "file-content" and t["permission"] == "completed"
    assert TrajectoryRecorder._tool_status(t) == "ok"


def test_tool_update_without_start_is_backfilled():
    """引擎跳步：只见tool_call_update没见tool_call——工具仍可见（兜底建条目）。"""
    rec = FakeRecorder()
    rt = _rt(rec)
    tool_update = {
        "jsonrpc": "2.0", "method": "session/update",
        "params": {"update": {"sessionUpdate": "tool_call_update", "toolCallId": "tc-x",
                              "state": "completed", "rawOutput": "ok"}},
    }
    run(_feed(rt, [("c", PROMPT), ("a", tool_update), ("a", RESPONSE())]))
    _, _, _, _, _, tools, _, _ = rec.calls[1]
    assert len(tools) == 1 and tools[0]["result_preview"] == "ok"


# ── 3. 失败必须可见：error响应/stopReason/挤掉/连接收尾 ──────────

def test_error_response_marks_turn_error():
    rec = FakeRecorder()
    rt = _rt(rec)
    err_resp = {"jsonrpc": "2.0", "id": 7,
                "error": {"code": -32603, "message": "boom"}}
    run(_feed(rt, [("c", PROMPT), ("a", CHUNK("半截")), ("a", err_resp)]))
    assert rec.calls[1][4] == "error" and rec.calls[2][3] == "error"


def test_stop_reason_refusal_marks_error():
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_feed(rt, [("c", PROMPT), ("a", CHUNK("")), ("a", RESPONSE("refusal"))]))
    assert rec.calls[1][4] == "error"


def test_llm_failure_marker_text_marks_error():
    rec = FakeRecorder()
    rt = _rt(rec)
    run(_feed(rt, [("c", PROMPT), ("a", CHUNK("\n[LLM错误: 402]")),
                   ("a", RESPONSE())]))
    assert llm_outcome_status("\n[LLM错误: 402]") == "error"
    assert rec.calls[1][4] == "error"


def test_superseded_turn_closed_as_error():
    """新prompt挤掉未收口旧回合：旧回合落账不悬挂root，判error。"""
    rec = FakeRecorder(root_id="root-A")
    rt = _rt(rec)
    p2 = dict(PROMPT, id=8, params={"prompt": [{"type": "text", "text": "第二问"}]})

    async def go():
        rt.observe_client(PROMPT)
        rt.observe_agent(CHUNK("还没说完"))
        rt.observe_client(p2)  # 旧回合被挤掉
        rt.observe_agent(CHUNK("答二"))
        rt.observe_agent({"jsonrpc": "2.0", "id": 8, "result": {"stopReason": "end_turn"}})
        await rt.drain()

    run(go())
    recs = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert len(recs) == 2
    assert recs[0][4] == "error" and recs[0][3] == "还没说完"
    assert recs[1][4] == "ok" and recs[1][3] == "答二"
    ends = [c for c in rec.calls if c[0] == "end_turn"]
    assert [e[3] for e in ends] == ["error", "ok"]


def test_close_finalizes_open_turn_as_error():
    rec = FakeRecorder()
    rt = _rt(rec)

    async def go():
        rt.observe_client(PROMPT)
        rt.observe_agent(CHUNK("断线半截"))
        await rt.close()

    run(go())
    recs = [c for c in rec.calls if c[0] == "record_llm_call"]
    assert len(recs) == 1 and recs[0][4] == "error"


# ── 4. 无主span防护 + never-raise ─────────────────────────────

def test_no_session_id_writes_nothing():
    rec = FakeRecorder()
    rt = RelayTrajectory(agent_id="hermes", session_id_getter=lambda: None,
                         recorder=rec)
    run(_feed(rt, [("c", PROMPT), ("a", CHUNK("x")), ("a", RESPONSE())]))
    assert rec.calls == [], "session/new响应未到绝不写无主span"


def test_malformed_messages_never_raise():
    rec = FakeRecorder()
    rt = _rt(rec)

    async def go():
        for m in [None, "str", [1, 2], {"method": "other"},
                  {"method": "session/update", "params": {"update": "not-dict"}},
                  {"method": "session/update", "params": {"update": {
                      "sessionUpdate": "tool_call", "content": {"text": "{bad json"}}}},
                  {"method": "session/update", "params": {"update": {
                      "sessionUpdate": "tool_call_update", "content": {"text": "{}"}}}}]:
            rt.observe_client(m)
            rt.observe_agent(m)
        rt.observe_client(PROMPT)
        rt.observe_agent(12345)
        rt.observe_agent(RESPONSE())
        await rt.drain()

    run(go())
    kinds = [c[0] for c in rec.calls]
    assert kinds == ["start_turn", "record_llm_call", "end_turn"], kinds


def test_full_agent_message_fallback_without_chunks():
    """引擎只发整条agent_message（无chunk）：content取整条文本。"""
    rec = FakeRecorder()
    rt = _rt(rec)
    full = {"jsonrpc": "2.0", "method": "session/update",
            "params": {"update": {"sessionUpdate": "agent_message",
                                  "content": {"type": "text", "text": "整条回答"}}}}
    run(_feed(rt, [("c", PROMPT), ("a", full), ("a", RESPONSE())]))
    assert rec.calls[1][3] == "整条回答"


def test_thought_chunks_not_in_content():
    rec = FakeRecorder()
    rt = _rt(rec)
    thought = {"jsonrpc": "2.0", "method": "session/update",
               "params": {"update": {"sessionUpdate": "agent_thought_chunk",
                                     "content": {"type": "text", "text": "内心戏"}}}}
    run(_feed(rt, [("c", PROMPT), ("a", thought), ("a", CHUNK("正文")),
                   ("a", RESPONSE())]))
    assert rec.calls[1][3] == "正文"
    assert rt.turns_seen == 1


# ── 5. ws_acp接线钉（写了≠接线了） ─────────────────────────────

def test_ws_acp_wiring_pins():
    src = (Path(__file__).parent.parent / "ws_acp.py").read_text(encoding="utf-8")
    # observer构造 + 非soulmate路由门闸（防与引擎内深记录双记）
    assert re.search(r"traj_relay\s*=\s*RelayTrajectory\(", src)
    assert 'route["cmd"] != DEFAULT_ROUTE["cmd"]' in src
    # 三个调用点在透传真实路径上（调用语法而非注释提及）
    assert re.search(r"traj_relay\.observe_client\(json\.loads\(raw\)\)", src)
    assert re.search(r"traj_relay\.observe_agent\(parsed\)", src)
    assert re.search(r"await traj_relay\.close\(\)", src)
    # 调用点位置纪律：observe_client在ws_to_stdin（客户端→子进程）内、
    # observe_agent在_process_line（子进程→客户端）内
    ws_pos = src.index("async def ws_to_stdin")
    pl_pos = src.index("async def _process_line")
    assert src.index("traj_relay.observe_client") > ws_pos
    assert src.index("traj_relay.observe_agent") > pl_pos
    # 双记防护钉：soulmate路由cmd与DEFAULT_ROUTE相同（引擎内深记录负责），
    # hermes路由不同（relay浅记录负责）
    assert ws_acp.AGENT_ROUTES["soulmate"]["cmd"] == ws_acp.DEFAULT_ROUTE["cmd"]
    assert ws_acp.AGENT_ROUTES["hermes"]["cmd"] != ws_acp.DEFAULT_ROUTE["cmd"]


# ── 5b. P0回归钉：os局部import遮蔽hermes pty路由（2026-10-02 live E2E抓到） ──

def test_ws_acp_no_function_local_os_import():
    """函数内"import os"会把os变局部变量→pty分支(os.close/os.write)UnboundLocalError
    →hermes路由每次启动子进程必炸（live E2E实测"Agent hermes unavailable"）。AST钉：
    ws_acp_endpoint函数体内不得出现任何os绑定（import/赋值/参数）。"""
    import ast

    src = (Path(__file__).parent.parent / "ws_acp.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "ws_acp_endpoint"
    )
    offenders = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" or (alias.asname or "") == "os":
                    offenders.append(f"import os @line {node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == "os":
                    offenders.append(f"from ... import os @line {node.lineno}")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id == "os":
            offenders.append(f"assign os @line {node.lineno}")
    assert offenders == [], f"ws_acp_endpoint内出现os局部绑定（hermes pty路由必炸）: {offenders}"
    # 模块顶部import os仍在（os.environ/os.close的全局真源）
    assert re.search(r"^import os$", src, re.M), "模块顶部import os缺失"


def test_ws_acp_startup_failure_cleans_up_subprocess():
    """启动失败路径必须terminate已spawn的子进程（os遮蔽案例里create_*成功后
    os.close才炸——不清理=孤儿hermes进程占pty泄漏）。"""
    src = (Path(__file__).parent.parent / "ws_acp.py").read_text(encoding="utf-8")
    fail_pos = src.index('Failed to start {agent_id}')
    cleanup_pos = src.index("proc.terminate()", fail_pos)
    assert 0 < cleanup_pos - fail_pos < 400, "Failed to start分支400字符内应有proc.terminate()清理"
    assert re.search(r"proc is not None and proc\.returncode is None", src)


# ── 6. recorder兼容钉：默认值下soulmate既有payload逐字不变 ──────

def test_recorder_agent_id_source_passthrough():
    class FakePoster:
        def __init__(self):
            self.calls = []

        async def __call__(self, path, payload):
            self.calls.append((path, payload))
            return {"id": "evt-1"} if path.endswith("/events") else {"ok": 1}

    fake = FakePoster()
    rec = TrajectoryRecorder()
    rec._post = fake

    async def go():
        await rec.record_llm_call(
            "s1", "root-1", content="c", status="ok",
            tools=[{"name": "t", "permission": "failed", "result_preview": "x"}],
            agent_id="hermes", source="relay",
        )
        await rec.record_llm_call("s1", "root-2", content="c")  # 默认值

    run(go())
    relay_body = fake.calls[0][1]
    assert relay_body["agent_id"] == "hermes"
    assert relay_body["metadata"]["source"] == "relay"
    batch_body = fake.calls[1][1]
    assert batch_body["events"][0]["agent_id"] == "hermes"
    # 默认值=既有soulmate契约零变化
    default_body = fake.calls[2][1]
    assert default_body["agent_id"] == "soulmate"
    assert "source" not in default_body["metadata"]


def test_tool_status_keywords_extended_for_relay():
    st = TrajectoryRecorder._tool_status
    assert st({"permission": "failed", "result_preview": "x"}) == "error"
    assert st({"permission": "cancelled", "result_preview": "x"}) == "error"
    # soulmate既有取值零误伤
    assert st({"permission": "allow", "result_preview": "ok"}) == "ok"
    assert st({"permission": "infection:terminate", "result_preview": "x"}) == "error"
    assert st({"permission": "loop_guard:high", "result_preview": "ok"}) == "ok"
    assert st({"permission": "sandbox-restricted", "result_preview": "ok"}) == "ok"
