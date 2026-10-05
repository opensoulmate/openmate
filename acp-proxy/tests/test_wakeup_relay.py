# -*- coding: utf-8 -*-
"""WakeupRelay — 唤醒回合实时可见跨进程推送面（dev-reports 11:11遗留#3销账）。

缺口：唤醒回合在无客户端headless实例/对端proxy实例执行，_notify_client天然no-op，
消息落opensoul.db前端只能下次加载可见——会话正连着也"看不见它在干嘛"。
本面：落库行带turn_origin=wakeup（wakeup_executor窗口 + _save_message metadata
合并）→ WakeupRelay给本地正连WS会话补推为ACP session/update通知（角色保真：
user出身行=user_message、assistant出身行=agent_message，与前端loadHistory的
role映射一致，同一行live视图与历史视图角色不翻转）。

安全声明（测试即规格）：
1. 普通回合行绝不补推（子进程自己流式，推了=重复）——interleaving也不误推；
2. 既有metadata（记忆marker/cache_replay）合并保留，不可解析原文保全不丢；
3. WS发送失败停止relay并可见计数，不无限重试打爆死连接；
4. relay启动前的旧行不补推（前端历史加载覆盖）；
5. 无标记窗口的落库行为零变化（回归面）。
"""

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.soulmate_agent import SoulMateAgent
from agent.wakeup_executor import WAKEUP_TURN_ORIGIN, default_headless_fire
from agent.wakeup_relay import (
    WakeupRelay,
    fetch_rows,
    is_wakeup_meta,
    max_row_id,
    relay_stats,
    update_payload,
)

SID = "om-relay-test"


def run(coro):
    return asyncio.run(coro)


def _make_db(tag_meta=None):
    """临时agent_messages库（真实schema子集：含metadata列+_save_message联动的会话表）"""
    db_path = tempfile.mktemp(suffix=".db")
    db = sqlite3.connect(db_path)
    db.execute(
        "CREATE TABLE agent_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,"
        " timestamp REAL, attachments TEXT, parent_message_id INTEGER, metadata TEXT)"
    )
    db.execute(
        "CREATE TABLE agent_sessions (id TEXT PRIMARY KEY,"
        " message_count INTEGER DEFAULT 0, last_activity_at REAL)"
    )
    db.commit()
    db.close()
    return db_path


def _insert(db_path, role, content, metadata=None, session_id=SID):
    db = sqlite3.connect(db_path)
    cur = db.execute(
        "INSERT INTO agent_messages (session_id, role, content, metadata) VALUES (?, ?, ?, ?)",
        (session_id, role, content, metadata),
    )
    db.commit()
    db.close()
    rid = cur.lastrowid
    assert rid is not None
    return int(rid)


def _tag(origin=WAKEUP_TURN_ORIGIN, extra=None):
    meta = dict(extra or {})
    meta["turn_origin"] = origin
    return json.dumps(meta, ensure_ascii=False)


def _bare_agent(db_path):
    """裸SoulMateAgent（__new__绕过重型__init__，既有测试同款惯例）"""
    a = SoulMateAgent.__new__(SoulMateAgent)
    a._db_path = Path(db_path)
    a._client = None
    return a


# ════════════════════════════════════════════════════════════════
# ① 回合出身标记：_save_message metadata合并（识别面的前提）
# ════════════════════════════════════════════════════════════════


class TestTurnOriginTagging:
    def test_window_rows_tagged(self):
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._turn_origin = {}
        agent.set_turn_origin(SID, WAKEUP_TURN_ORIGIN)
        agent._save_message(SID, "assistant", "唤醒回合回复")
        meta = json.loads(self._meta_of(db_path))
        assert meta["turn_origin"] == WAKEUP_TURN_ORIGIN

    def _meta_of(self, db_path):
        db = sqlite3.connect(db_path)
        try:
            return db.execute("SELECT metadata FROM agent_messages").fetchone()[0]
        finally:
            db.close()

    def test_merges_existing_metadata(self):
        # 记忆marker/cache_replay既有键必须全保留，turn_origin并入不覆盖
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._turn_origin = {}
        agent.set_turn_origin(SID, WAKEUP_TURN_ORIGIN)
        agent._save_message(
            SID, "assistant", "带marker的回复",
            metadata=json.dumps({"cache_replay": True, "bytes": 7}, ensure_ascii=False),
        )
        meta = json.loads(self._meta_of(db_path))
        assert meta["cache_replay"] is True
        assert meta["bytes"] == 7
        assert meta["turn_origin"] == WAKEUP_TURN_ORIGIN

    def test_no_window_metadata_unchanged(self):
        # 回归面：无标记窗口的普通回合行metadata零变化（None保持None）
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._turn_origin = {}
        agent._save_message(SID, "assistant", "普通回合回复")
        assert self._meta_of(db_path) is None

    def test_no_window_explicit_metadata_byte_identical(self):
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._turn_origin = {}
        raw = json.dumps({"kiloMemory": ["m1"]}, ensure_ascii=False)
        agent._save_message(SID, "assistant", "普通回合回复", metadata=raw)
        assert self._meta_of(db_path) == raw

    def test_unparsable_metadata_preserved(self):
        # 不可解析metadata：原文保全在raw_metadata下，绝不静默丢数据
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._turn_origin = {}
        agent.set_turn_origin(SID, WAKEUP_TURN_ORIGIN)
        agent._save_message(SID, "assistant", "回复", metadata="not-json{{")
        meta = json.loads(self._meta_of(db_path))
        assert meta["raw_metadata"] == "not-json{{"
        assert meta["turn_origin"] == WAKEUP_TURN_ORIGIN

    def test_clear_turn_origin_ends_window(self):
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._turn_origin = {}
        agent.set_turn_origin(SID, WAKEUP_TURN_ORIGIN)
        agent.clear_turn_origin(SID)
        agent._save_message(SID, "assistant", "窗口外回复")
        assert self._meta_of(db_path) is None

    def test_bare_agent_without_attr_no_crash(self):
        # getattr防御：无_turn_origin属性的实例（旧测试替身）落库不炸不标记
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._save_message(SID, "assistant", "无属性实例回复")
        assert self._meta_of(db_path) is None


# ════════════════════════════════════════════════════════════════
# ② 出身判定 + 拉取面（interleaving精确性）
# ════════════════════════════════════════════════════════════════


class TestRelayFilter:
    def test_is_wakeup_meta(self):
        assert is_wakeup_meta(_tag())
        assert is_wakeup_meta(_tag(extra={"cache_replay": True}))
        assert not is_wakeup_meta(None)
        assert not is_wakeup_meta("")
        assert not is_wakeup_meta("not-json")
        assert not is_wakeup_meta(json.dumps({"turn_origin": "normal"}))
        assert not is_wakeup_meta(json.dumps(["turn_origin"]))

    def test_interleaved_rows_only_wakeup_flagged(self):
        # 关键安全声明：唤醒回合行与普通回合行id交错时，只标wakeup行——
        # 普通行由子进程流式推送，relay误推=前端重复消息
        db_path = _make_db()
        _insert(db_path, "user", "⏰ [ScheduleWakeup 到点唤醒...]", _tag())
        _insert(db_path, "assistant", "唤醒回合回复A", _tag())
        _insert(db_path, "user", "用户普通提问", None)  # 普通回合（子进程流式）
        _insert(db_path, "assistant", "普通回合回复B", None)
        _insert(db_path, "assistant", "唤醒回合回复C", _tag())
        rows = fetch_rows(db_path, SID, 0)
        flagged = [r["content"] for r in rows if r["is_wakeup"]]
        assert flagged == ["⏰ [ScheduleWakeup 到点唤醒...]", "唤醒回合回复A", "唤醒回合回复C"]

    def test_after_id_boundary(self):
        db_path = _make_db()
        id1 = _insert(db_path, "assistant", "旧轮回复", _tag())
        _insert(db_path, "assistant", "新轮回复", _tag())
        rows = fetch_rows(db_path, SID, id1)
        assert [r["content"] for r in rows] == ["新轮回复"]

    def test_missing_table_fail_safe(self):
        # 空库/旧库无表：fail-safe空结果不炸轮询循环
        assert fetch_rows(tempfile.mktemp(suffix=".db"), SID, 0) == []
        assert max_row_id(tempfile.mktemp(suffix=".db"), SID) == 0

    def test_max_row_id(self):
        db_path = _make_db()
        assert max_row_id(db_path, SID) == 0
        idn = _insert(db_path, "assistant", "x", _tag())
        assert max_row_id(db_path, SID) == idn


# ════════════════════════════════════════════════════════════════
# ③ 推送面：poll_once/run 行为 + 失败可见
# ════════════════════════════════════════════════════════════════


class _Collector:
    def __init__(self, fail_after=None):
        self.payloads = []
        self.fail_after = fail_after

    async def __call__(self, payload):
        if self.fail_after is not None and len(self.payloads) >= self.fail_after:
            raise ConnectionError("WS已断开")
        self.payloads.append(payload)


class TestRelayPush:
    def _relay(self, db_path, collector, **kw):
        return WakeupRelay(
            session_id_getter=lambda: SID,
            send=collector,
            db_path=db_path,
            interval_s=0.05,
            sid_wait_s=1.0,
            **kw,
        )

    def test_payload_shape_matches_frontend_contract(self):
        # assistant/缺role行→agent_message（chat-client既有处理面，形状逐字不变）
        for row in ({"content": "唤醒回合回复"}, {"content": "唤醒回合回复", "role": "assistant"}):
            p = update_payload(SID, row)
            assert p["method"] == "session/update"
            assert p["params"]["sessionId"] == SID
            upd = p["params"]["update"]
            assert upd["sessionUpdate"] == "agent_message"
            assert upd["content"] == {"type": "text", "text": "唤醒回合回复"}

    def test_payload_role_fidelity_user_row(self):
        # 角色保真（21:40轮遗留#4销账）：user出身行（唤醒触发行）→user_message，
        # 与前端loadHistory的role='user'→用户气泡映射一致（live/历史视图角色不翻转）
        p = update_payload(SID, {"role": "user", "content": "⏰ [ScheduleWakeup 到点唤醒]"})
        upd = p["params"]["update"]
        assert upd["sessionUpdate"] == "user_message"
        assert upd["content"] == {"type": "text", "text": "⏰ [ScheduleWakeup 到点唤醒]"}
        # 除sessionUpdate外形状与agent_message路径完全同构（前端两分支共用content契约）
        assert p["method"] == "session/update"
        assert p["params"]["sessionId"] == SID
        assert set(upd.keys()) == {"sessionUpdate", "content"}

    def test_payload_role_case_and_empty_fallback(self):
        # role大小写不敏感匹配，空/None/未知role→agent_message（不误伤非user行）
        assert update_payload(SID, {"role": "USER", "content": "x"})["params"]["update"]["sessionUpdate"] == "user_message"
        for row in ({"role": "", "content": "x"}, {"role": None, "content": "x"}, {"role": "tool", "content": "x"}, {}):
            assert update_payload(SID, row)["params"]["update"]["sessionUpdate"] == "agent_message"

    def test_poll_once_pushes_only_wakeup_rows_in_order(self):
        db_path = _make_db()
        c = _Collector()
        relay = self._relay(db_path, c)
        relay._last_id = 0
        _insert(db_path, "user", "⏰ [ScheduleWakeup 到点唤醒]", _tag())
        _insert(db_path, "assistant", "唤醒回复1", _tag())
        _insert(db_path, "user", "普通提问", None)
        _insert(db_path, "assistant", "普通回复2", None)
        pushed = run(relay.poll_once(SID))
        texts = [p["params"]["update"]["content"]["text"] for p in c.payloads]
        assert pushed == 2
        assert texts == ["⏰ [ScheduleWakeup 到点唤醒]", "唤醒回复1"]
        # 角色保真：触发行user_message在前、回合输出agent_message在后（顺序即回合时序）
        assert [p["params"]["update"]["sessionUpdate"] for p in c.payloads] == [
            "user_message",
            "agent_message",
        ]

    def test_poll_once_advances_cursor_over_untagged(self):
        # 未标记行推进游标：第二轮不重扫不重推（防重复推送+防O(n²)重扫）
        db_path = _make_db()
        c = _Collector()
        relay = self._relay(db_path, c)
        relay._last_id = 0
        _insert(db_path, "assistant", "普通回复", None)
        run(relay.poll_once(SID))
        assert c.payloads == []
        run(relay.poll_once(SID))
        assert c.payloads == []
        _insert(db_path, "assistant", "唤醒回复", _tag())
        run(relay.poll_once(SID))
        assert [p["params"]["update"]["content"]["text"] for p in c.payloads] == ["唤醒回复"]

    def test_run_baseline_skips_preexisting_rows(self):
        # relay启动前已落库的行不补推（前端历史加载覆盖）
        db_path = _make_db()
        _insert(db_path, "assistant", "启动前的唤醒回复", _tag())
        c = _Collector()
        relay = self._relay(db_path, c)

        async def scenario():
            task = asyncio.create_task(relay.run())
            await asyncio.sleep(0.2)
            _insert(db_path, "assistant", "启动后的唤醒回复", _tag())
            await asyncio.sleep(0.3)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        run(scenario())
        texts = [p["params"]["update"]["content"]["text"] for p in c.payloads]
        assert "启动前的唤醒回复" not in texts
        assert "启动后的唤醒回复" in texts

    def test_send_failure_stops_and_visible(self):
        # WS已死：停止relay（不无限重试打爆死连接）+ push_errors可见
        db_path = _make_db()
        _insert(db_path, "assistant", "唤醒回复1", _tag())
        _insert(db_path, "assistant", "唤醒回复2", _tag())
        before = relay_stats()["push_errors"]
        c = _Collector(fail_after=0)
        relay = self._relay(db_path, c)
        relay._last_id = 0
        relay._running = True
        run(relay.poll_once(SID))
        assert relay._running is False
        assert relay_stats()["push_errors"] == before + 1

    def test_run_cancel_cleans_active_counter(self):
        db_path = _make_db()
        relay = self._relay(db_path, _Collector())

        async def scenario():
            task = asyncio.create_task(relay.run())
            await asyncio.sleep(0.15)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        before = relay_stats()["relays_active"]
        run(scenario())
        assert relay_stats()["relays_active"] == before

    def test_sid_timeout_exits_visibly(self):
        before = relay_stats()["sid_timeout"]
        relay = WakeupRelay(
            session_id_getter=lambda: None,
            send=_Collector(),
            db_path=_make_db(),
            interval_s=0.05,
            sid_wait_s=0.3,
        )
        run(relay.run())
        assert relay_stats()["sid_timeout"] == before + 1


# ════════════════════════════════════════════════════════════════
# ④ 出身标记窗口：default_headless_fire包裹prompt（含异常路径）
# ════════════════════════════════════════════════════════════════


class _OriginAgent:
    def __init__(self, fail=False):
        self.origin_log = []
        self.fail = fail

    def set_turn_origin(self, session_id, origin):
        self.origin_log.append(("set", session_id, origin))

    def clear_turn_origin(self, session_id):
        self.origin_log.append(("clear", session_id))

    def _get_db(self):
        raise RuntimeError("判定取数fail-open路径（本测试不触发）")

    async def prompt(self, prompt, session_id, **kw):
        self.origin_log.append(("prompt", session_id))
        if self.fail:
            raise RuntimeError("回合内部炸了")
        return type("R", (), {"stop_reason": "end_turn"})()


class TestOriginWindow:
    def test_set_before_prompt_clear_after(self, monkeypatch):
        agent = _OriginAgent()
        monkeypatch.setattr(
            "agent.wakeup_executor._get_headless_agent", lambda: agent, raising=True
        )
        ev = {"session_id": "om-win", "reason": "检查窗口", "noop": False, "streak": 0}
        run(default_headless_fire(ev))
        assert agent.origin_log == [
            ("set", "om-win", WAKEUP_TURN_ORIGIN),
            ("prompt", "om-win"),
            ("clear", "om-win"),
        ]

    def test_clear_on_prompt_exception(self, monkeypatch):
        # finally语义：回合内部异常也必须清标记（不残留污染下一回合）
        agent = _OriginAgent(fail=True)
        monkeypatch.setattr(
            "agent.wakeup_executor._get_headless_agent", lambda: agent, raising=True
        )
        ev = {"session_id": "om-win", "reason": "检查窗口", "noop": False, "streak": 0}
        try:
            run(default_headless_fire(ev))
        except RuntimeError:
            pass
        assert agent.origin_log[-1] == ("clear", "om-win")


# ════════════════════════════════════════════════════════════════
# ⑤ 接线回归护栏（写了≠接线了）
# ════════════════════════════════════════════════════════════════


class TestWiring:
    def _src(self, name):
        return (Path(__file__).parent.parent / name).read_text(encoding="utf-8")

    def test_ws_acp_starts_relay_for_soulmate(self):
        src = self._src("ws_acp.py")
        assert "WakeupRelay(" in src
        assert 'if agent_id == "soulmate"' in src
        assert "relay_task.cancel()" in src

    def test_fire_marks_turn_origin_window(self):
        src = self._src("agent/wakeup_executor.py")
        assert "agent.set_turn_origin(sid, WAKEUP_TURN_ORIGIN)" in src
        assert "agent.clear_turn_origin(sid)" in src

    def test_health_exposes_relay_stats(self):
        src = self._src("ws_chat.py")
        assert 'payload["wakeup_relay"] = relay_stats()' in src

    def test_save_message_merges_origin(self):
        # 合并入口改名_merge_session_meta（turn_origin+session_source两标记同列并入），
        # 接线钉随迁：_save_message仍必须走合并入口，且两个标记键都在合并逻辑里
        src = self._src("agent/soulmate_agent.py")
        assert "metadata = self._merge_session_meta(session_id, metadata)" in src
        assert 'meta["turn_origin"] = origin' in src
        assert 'meta["session_source"] = source' in src


# ════════════════════════════════════════════════════════════════
# ⑥ 前端角色保真接线钉（user_message处理面：写了≠接线了）
# ════════════════════════════════════════════════════════════════


class TestFrontendRoleFidelityWiring:
    """chat-client user_message分支 + buildUserMessage单一真源 + loadHistory角色映射一致性"""

    def _chat_src(self):
        return (
            Path(__file__).parent.parent.parent
            / "src" / "app" / "(app)" / "chat" / "chat-client.tsx"
        ).read_text(encoding="utf-8")

    def _helper_src(self):
        return (
            Path(__file__).parent.parent.parent
            / "src" / "app" / "(app)" / "chat" / "user-message.ts"
        ).read_text(encoding="utf-8")

    def test_chat_client_has_user_message_branch(self):
        src = self._chat_src()
        assert "sessionUpdate === 'user_message'" in src
        assert "import { buildUserMessage } from './user-message'" in src
        assert "buildUserMessage(text)" in src

    def test_user_message_branch_keeps_loading_state(self):
        # 触发行先到、回合输出后到：user_message分支不得setLoading(false)结束loading
        src = self._chat_src()
        start = src.index("sessionUpdate === 'user_message'")
        end = src.index("else if", start)
        branch = src[start:end]
        assert "setLoading" not in branch
        assert "buildUserMessage(text)" in branch

    def test_helper_single_source_user_role(self):
        src = self._helper_src()
        assert "export function buildUserMessage" in src
        assert "role: 'user'" in src
        assert "parts: [{ type: 'text', text }]" in src

    def test_load_history_role_mapping_consistent(self):
        # 一致性主张的根据：历史加载role='user'→用户气泡（live推送必须同角色）
        src = self._chat_src()
        assert "role: isAgent ? 'agent' : 'user'" in src
