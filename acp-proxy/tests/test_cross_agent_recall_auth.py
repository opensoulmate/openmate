"""supplement3 #13 跨agent会话读取二次授权 单测 + 接线断言（写了≠接线了）

调研来源：kilocode-source-supplement3.md #13「跨workspace读取二次授权：recall read模式
目标session不在当前worktree家族→ctx.ask(permission:"recall")再读」。本系统会话家族=
agent归属（agent_sessions.agent_id）：soulmate家族外（hermes/codex/opencode/imported…）
的会话read必须人工二次授权；search跨家族开放（kilocode同款：发现工具+inert转义）。
"""

import asyncio
import inspect
import sqlite3
import time

from agent.session_recall import (
    FOREIGN_READ_DENIED,
    SessionRecallEngine,
    execute_tool,
    foreign_read_context,
)


def _make_db(tmp_path, with_agent_id: bool = True):
    db_path = str(tmp_path / "recall_auth_test.db")
    con = sqlite3.connect(db_path)
    owner_col = ", agent_id TEXT DEFAULT 'soulmate'" if with_agent_id else ""
    con.executescript(
        f"""
        CREATE TABLE agent_sessions (
            id TEXT PRIMARY KEY, title TEXT,
            created_at REAL, last_activity_at REAL,
            message_count INTEGER DEFAULT 0, metadata TEXT{owner_col}
        );
        CREATE TABLE agent_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT, role TEXT, content TEXT,
            timestamp REAL, attachments TEXT, parent_message_id INTEGER
        );
        """
    )
    con.commit()
    con.close()
    return db_path


def _add_session(db_path, sid, title="t", agent_id="soulmate"):
    con = sqlite3.connect(db_path)
    cols = "id, title, created_at, last_activity_at"
    vals = "?, ?, ?, ?"
    params = [sid, title, time.time(), time.time()]
    has_col = any(
        r[1] == "agent_id"
        for r in con.execute("PRAGMA table_info(agent_sessions)").fetchall()
    )
    if has_col:
        cols += ", agent_id"
        vals += ", ?"
        params.append(agent_id)
    con.execute(f"INSERT OR REPLACE INTO agent_sessions ({cols}) VALUES ({vals})", params)
    con.commit()
    con.close()


def _add_msg(db_path, sid, role, content):
    con = sqlite3.connect(db_path)
    con.execute(
        "INSERT INTO agent_messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (sid, role, content, time.time()),
    )
    con.commit()
    con.close()


SECRET = "TOP-SECRET-HERMES-TRANSCRIPT-CONTENT"


class TestOwnerAgent:
    def test_owner_of_rowed_session(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="hermes")
        assert SessionRecallEngine(db).owner_agent("s1") == "hermes"

    def test_owner_none_for_missing_row(self, tmp_path):
        db = _make_db(tmp_path)
        assert SessionRecallEngine(db).owner_agent("nope") is None

    def test_owner_empty_for_untrusted(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="")
        assert SessionRecallEngine(db).owner_agent("s1") == ""

    def test_owner_fail_closed_on_old_schema(self, tmp_path):
        db = _make_db(tmp_path, with_agent_id=False)
        _add_session(db, "s1")
        assert SessionRecallEngine(db).owner_agent("s1") == ""  # 缺列=归属不可信


class TestForeignReadContext:
    def test_foreign_read_needs_auth(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="hermes")
        needs, owner = foreign_read_context(
            SessionRecallEngine(db), {"mode": "read", "session_id": "s1"})
        assert needs is True
        assert owner == "hermes"

    def test_own_agent_read_free(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="soulmate")
        needs, owner = foreign_read_context(
            SessionRecallEngine(db), {"mode": "read", "session_id": "s1"})
        assert needs is False
        assert owner == "soulmate"

    def test_current_session_always_free(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "cur", agent_id="hermes")  # 即便归属异常，本会话=自家
        needs, _ = foreign_read_context(
            SessionRecallEngine(db), {"mode": "read", "session_id": "cur"},
            current_session_id="cur")
        assert needs is False

    def test_search_mode_never_asks(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="hermes")
        _add_msg(db, "s1", "user", "deploy widget")
        needs, _ = foreign_read_context(
            SessionRecallEngine(db), {"mode": "search", "query": "deploy"})
        assert needs is False

    def test_missing_session_id_never_asks(self, tmp_path):
        db = _make_db(tmp_path)
        assert foreign_read_context(SessionRecallEngine(db), {"mode": "read"}) == (False, "")

    def test_no_row_session_not_gated(self, tmp_path):
        # ws直建自家会话/不存在：无归属行→不拦（不存在由read()报标准错误）
        db = _make_db(tmp_path)
        assert foreign_read_context(
            SessionRecallEngine(db), {"mode": "read", "session_id": "ghost"}) == (False, "")

    def test_empty_owner_fail_closed(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="")
        needs, owner = foreign_read_context(
            SessionRecallEngine(db), {"mode": "read", "session_id": "s1"})
        assert needs is True
        assert owner == ""

    def test_custom_agent_family(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="hermes")
        needs, _ = foreign_read_context(
            SessionRecallEngine(db), {"mode": "read", "session_id": "s1"},
            current_agent_id="hermes")
        assert needs is False


class TestExecuteToolEnforcement:
    def test_foreign_read_denied_without_approval(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="hermes")
        _add_msg(db, "s1", "assistant", SECRET)
        out = execute_tool(
            SessionRecallEngine(db), {"mode": "read", "session_id": "s1"})
        assert "二次授权" in out
        assert SECRET not in out  # 决定性：家族外转录内容零泄漏
        assert "hermes" in out

    def test_foreign_read_allowed_with_approval(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="hermes")
        _add_msg(db, "s1", "assistant", SECRET)
        out = execute_tool(
            SessionRecallEngine(db), {"mode": "read", "session_id": "s1"},
            foreign_read_approved=True)
        assert SECRET in out

    def test_own_read_unaffected(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "s1", agent_id="soulmate")
        _add_msg(db, "s1", "assistant", SECRET)
        out = execute_tool(
            SessionRecallEngine(db), {"mode": "read", "session_id": "s1"})
        assert SECRET in out

    def test_denied_text_is_inert_escaped(self, tmp_path):
        db = _make_db(tmp_path)
        _add_session(db, "<script>", agent_id="hermes")
        _add_msg(db, "<script>", "assistant", SECRET)
        out = execute_tool(
            SessionRecallEngine(db), {"mode": "read", "session_id": "<script>"})
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_nonexistent_session_standard_error_preserved(self, tmp_path):
        db = _make_db(tmp_path)
        out = execute_tool(
            SessionRecallEngine(db), {"mode": "read", "session_id": "ghost"})
        assert "会话不存在" in out  # 无归属行→不拦，标准错误语义保持

    def test_search_still_cross_agent(self, tmp_path):
        # kilocode语义：search是发现工具，跨家族开放（片段inert转义）
        db = _make_db(tmp_path)
        _add_session(db, "s1", "工作", agent_id="hermes")
        _add_msg(db, "s1", "user", "deploy the widget manually")
        out = execute_tool(
            SessionRecallEngine(db), {"mode": "search", "query": "deploy widget"})
        assert "s1" in out
        assert "deploy" in out

    def test_denied_template_covers_fields(self):
        assert "{sid}" in FOREIGN_READ_DENIED
        assert "{owner}" in FOREIGN_READ_DENIED
        assert "{current}" in FOREIGN_READ_DENIED


class TestSoulmateWiring:
    def test_approval_helper_exists(self):
        from agent.soulmate_agent import SoulMateAgent

        assert inspect.iscoroutinefunction(SoulMateAgent._request_foreign_read_approval)

    def test_main_loop_calls_guarded_flow(self):
        from agent.soulmate_agent import SoulMateAgent

        src = inspect.getsource(SoulMateAgent._run_llm_with_tools)
        assert "session_recall.foreign_read_context(" in src
        assert "foreign_read_approved=" in src
        assert "_request_foreign_read_approval(" in src
        assert "session_recall.execute_tool(" in src  # 既有断言兼容

    def test_code_mode_calls_guarded_flow(self):
        from agent.soulmate_agent import SoulMateAgent

        # Pi #1 拆钩子面包裹层后执行体=_code_mode_tool_call_inner（接线不变）
        src = inspect.getsource(SoulMateAgent._code_mode_tool_call_inner)
        assert "session_recall.foreign_read_context(" in src
        assert "foreign_read_approved=" in src

    def test_approval_helper_records_provenance(self):
        from agent.soulmate_agent import SoulMateAgent

        src = inspect.getsource(SoulMateAgent._request_foreign_read_approval)
        assert "build_provenance(" in src
        assert "_perm_provenance" in src


class TestApprovalFlow:
    """轻量harness：不构造完整SoulMateAgent，直接驱动未绑定方法。"""

    class _Fake:
        def __init__(self, approve: bool):
            self._approve = approve
            self.requested = []
            self.recorded = []
            self._perm_provenance = self

        async def _request_tool_approval(self, session_id, tool_name, tool_args, decision):
            self.requested.append((session_id, tool_name, tool_args, decision))
            return self._approve

        def record(self, prov):
            self.recorded.append(prov)

    def test_approved_flow(self):
        from agent.soulmate_agent import SoulMateAgent

        fake = self._Fake(approve=True)
        ok = asyncio.run(
            SoulMateAgent._request_foreign_read_approval(fake, "sid1", "s-hermes", "hermes"))
        assert ok is True
        assert fake.requested[0][1] == "search_chat_history.read"
        assert fake.requested[0][2]["target_agent"] == "hermes"
        assert fake.recorded and fake.recorded[0]["rule_source"] == "cross-agent-recall"
        assert fake.recorded[0]["allowed"] is True

    def test_rejected_flow_fail_closed(self):
        from agent.soulmate_agent import SoulMateAgent

        fake = self._Fake(approve=False)
        ok = asyncio.run(
            SoulMateAgent._request_foreign_read_approval(fake, "sid1", "s-hermes", "hermes"))
        assert ok is False
        assert fake.recorded[0]["allowed"] is False
        assert fake.recorded[0]["denial_class"]  # 拒绝分层留痕

    def test_request_exception_fail_closed(self):
        from agent.soulmate_agent import SoulMateAgent

        class _Boom(self._Fake):
            async def _request_tool_approval(self, *a, **k):
                raise RuntimeError("client gone")

        fake = _Boom(approve=True)
        ok = asyncio.run(
            SoulMateAgent._request_foreign_read_approval(fake, "sid1", "s-hermes", "hermes"))
        assert ok is False  # 异常绝不放行
