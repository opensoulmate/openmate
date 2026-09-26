# -*- coding: utf-8 -*-
"""语义缓存命中回复落库契约——"缓存命中不再静默丢回复"（P1 bug修复）。

背景：_prompt_inner语义缓存命中分支此前只把缓存文本推给客户端就return——
agent_messages不落行、session["messages"]不追加。后果：历史留下"有问无答"的
悬空回合（前端历史、后续LLM上下文、无客户端唤醒回合全部看不见这条回复），
唤醒回合还会因此被判零输出。修复后缓存重放与正常回合同一持久化契约：
脱敏展示 + session历史追加 + agent_messages落行（metadata带cache_replay标记）。

调研来源：mem0 §1.1（输出/数据可见性不得静默丢失）；P0输出侧护栏（Warp
secret_redaction——缓存重放也是模型输出展示边界，落库侧同走脱敏）；
kilocode #9 消息级metadata留痕（合成part可审计）。
"""

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.output_guard import redact_secrets
from agent.soulmate_agent import SoulMateAgent

CACHED = "这是语义缓存里的历史回复文本，超过二十个字符以命中缓存分支。"
SECRET_CACHED = "旧缓存条目：OpenAI key sk-" + "a1b2c3d4" * 6 + " 曾明文入库。"


def run(coro):
    return asyncio.run(coro)


def _bare_agent():
    """裸SoulMateAgent实例（__new__绕过重型__init__）：只带_cache_replay所需面。"""
    a = SoulMateAgent.__new__(SoulMateAgent)
    a._db_path = Path(tempfile.mktemp(suffix=".db"))
    db = sqlite3.connect(a._db_path)
    db.execute(
        "CREATE TABLE agent_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,"
        " timestamp REAL, attachments TEXT, parent_message_id INTEGER, metadata TEXT)"
    )
    db.execute(
        "CREATE TABLE agent_sessions (id TEXT PRIMARY KEY,"
        " message_count INTEGER DEFAULT 0, last_activity_at REAL)"
    )
    db.execute("INSERT INTO agent_sessions (id) VALUES ('om-cache')")
    db.commit()
    db.close()
    a._client = None
    return a


def _rows(agent):
    db = sqlite3.connect(agent._db_path)
    try:
        return db.execute(
            "SELECT role, content, metadata FROM agent_messages"
            " WHERE session_id='om-cache' ORDER BY id"
        ).fetchall()
    finally:
        db.close()


class TestCacheReplayPersist:
    def test_persists_assistant_row(self):
        agent = _bare_agent()
        run(agent._cache_replay("om-cache", {"messages": []}, CACHED))
        rows = _rows(agent)
        assert len(rows) == 1
        assert rows[0][0] == "assistant"
        assert rows[0][1] == CACHED

    def test_appends_session_messages_history(self):
        # 历史一致性：内存上下文（后续LLM回合的messages）必须与落库一致
        agent = _bare_agent()
        session = {"messages": [{"role": "user", "content": "问题"}]}
        run(agent._cache_replay("om-cache", session, CACHED))
        assert session["messages"][-1] == {"role": "assistant", "content": CACHED}

    def test_redacts_before_persist(self):
        # 未脱敏原文绝不落库（P0输出侧护栏：落库侧同走脱敏）
        assert redact_secrets(SECRET_CACHED)[1], "测试密钥必须命中脱敏规则（前提成立）"
        agent = _bare_agent()
        session = {"messages": []}
        run(agent._cache_replay("om-cache", session, SECRET_CACHED))
        display = redact_secrets(SECRET_CACHED)[0]
        rows = _rows(agent)
        assert rows[0][1] == display
        assert rows[0][1] != SECRET_CACHED
        assert "sk-a1b2c3d4" not in rows[0][1]
        assert session["messages"][-1]["content"] == display

    def test_metadata_marker(self):
        agent = _bare_agent()
        run(agent._cache_replay("om-cache", {"messages": []}, CACHED))
        meta = json.loads(_rows(agent)[0][2])
        assert meta["cache_replay"] is True
        assert meta["bytes"] == len(CACHED.encode("utf-8"))

    def test_client_receives_redacted_display(self):
        agent = _bare_agent()

        class FakeClient:
            def __init__(self):
                self.updates = []

            async def session_update(self, session_id=None, update=None):
                self.updates.append((session_id, update))

        agent._client = FakeClient()
        run(agent._cache_replay("om-cache", {"messages": []}, SECRET_CACHED))
        assert len(agent._client.updates) == 1
        sid, update = agent._client.updates[0]
        assert sid == "om-cache"
        assert redact_secrets(SECRET_CACHED)[0] in str(update)

    def test_session_without_messages_key_no_crash(self):
        agent = _bare_agent()
        run(agent._cache_replay("om-cache", {}, CACHED))
        assert len(_rows(agent)) == 1

    def test_multiple_replays_each_persist(self):
        # 每次命中都是一个真实回合输出——逐次落行，不覆盖不合并
        agent = _bare_agent()
        run(agent._cache_replay("om-cache", {"messages": []}, CACHED))
        run(agent._cache_replay("om-cache", {"messages": []}, CACHED))
        assert len(_rows(agent)) == 2


class TestCacheReplayWiring:
    def test_prompt_inner_calls_cache_replay(self):
        # 接线回归护栏：命中分支必须走_cache_replay（落库契约），
        # 不得退回"只推客户端不落库"的静默丢回复
        src = (
            Path(__file__).parent.parent / "agent" / "soulmate_agent.py"
        ).read_text(encoding="utf-8")
        assert "await self._cache_replay(session_id, session, cached)" in src
