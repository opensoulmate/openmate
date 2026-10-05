"""会话级provenance产生侧标记测试（jobs.source同款"从产生侧标注"）。

背景（2026-10-05 17:30轮遗留#2销账轮）："请回复ok两个字即可"×3 以confidence=1.0
占据 opensoul learned_patterns.strategy——e2e_ws_calibration.py live探针会话的
用户消息污染生产experiences。根治=会话级provenance标记（类似jobs.source），
不是字符串猜测：测试客户端在 session/new 的 _meta.source 显式声明出身，
SoulMateAgent 绑定后会话内所有落库行metadata带session_source，ExperienceCollector
（opensoul src/learn/experience_collector.py）据此跳过测试会话。

本文件钉住产生侧契约：
1. new_session 绑定：_meta.source（field_meta形态）与展开kwargs形态都认；
2. _save_message 全行携带：session_source并入metadata，既有键全保留，
   turn_origin与session_source可共存；
3. 无标记会话零变化（普通用户聊天行为不变）；
4. 进程重启重连：_load_messages_from_db 从历史行恢复绑定（标记不丢）。
"""

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

from agent.soulmate_agent import SoulMateAgent

SID = "om-prov-test"


def _make_db():
    """临时opensoul.db子集（agent_messages含metadata + agent_sessions）"""
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


def _bare_agent(db_path):
    """裸SoulMateAgent（__new__绕过重型__init__，既有测试同款惯例）"""
    a = SoulMateAgent.__new__(SoulMateAgent)
    a._db_path = Path(db_path)
    a._client = None
    a.sessions = {}
    a._session_cwds = {}
    a._turn_origin = {}
    a._session_sources = {}
    a._project_root = "/tmp"
    return a


def _meta_of(db_path, n=0):
    db = sqlite3.connect(db_path)
    try:
        return db.execute(
            "SELECT metadata FROM agent_messages ORDER BY id LIMIT 1 OFFSET ?", (n,)
        ).fetchone()[0]
    finally:
        db.close()


class TestBindSessionSource:
    def test_meta_source_bound(self):
        """_meta.source（ACP标准扩展字段）绑定为会话出身。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        resp = asyncio.run(agent.new_session(field_meta={"source": "test"}))
        assert agent._session_sources[resp.session_id] == "test"

    def test_kwargs_source_bound(self):
        """acp router把_meta键展开为kwargs的派发形态同样认。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        resp = asyncio.run(agent.new_session(source="test"))
        assert agent._session_sources[resp.session_id] == "test"

    def test_no_source_zero_change(self):
        """未声明source：不绑定（普通用户会话行为零变化）。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        resp = asyncio.run(agent.new_session(field_meta={"session_id": None}))
        assert agent._session_sources == {}
        assert resp.session_id.startswith("om-")

    def test_reconnect_memory_rebinds(self):
        """内存重连路径也绑定（声明优先于历史）。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent.sessions[SID] = {"session_id": SID, "messages": []}
        asyncio.run(agent.new_session(session_id=SID, field_meta={"source": "test"}))
        assert agent._session_sources[SID] == "test"

    def test_reconnect_db_path_binds(self):
        """DB重连路径同样绑定。"""
        db_path = _make_db()
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO agent_sessions (id, message_count) VALUES (?, 0)", (SID,)
        )
        db.commit()
        db.close()
        agent = _bare_agent(db_path)
        asyncio.run(agent.new_session(session_id=SID, field_meta={"source": "test"}))
        assert agent._session_sources[SID] == "test"


class TestSaveMessageStamping:
    def test_session_source_stamped(self):
        """绑定后落库行metadata带session_source（采集侧provenance门的依据）。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._session_sources[SID] = "test"
        agent._save_message(SID, "user", "请回复OK两个字即可")
        meta = json.loads(_meta_of(db_path))
        assert meta["session_source"] == "test"

    def test_existing_metadata_keys_preserved(self):
        """既有metadata键（记忆marker/cache_replay等）全保留，provenance并入。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._session_sources[SID] = "test"
        agent._save_message(
            SID,
            "assistant",
            "OK",
            metadata=json.dumps({"cache_replay": True}, ensure_ascii=False),
        )
        meta = json.loads(_meta_of(db_path))
        assert meta["cache_replay"] is True
        assert meta["session_source"] == "test"

    def test_both_markers_coexist(self):
        """turn_origin（回合出身）与session_source（会话出身）共存不互斥。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._session_sources[SID] = "test"
        agent.set_turn_origin(SID, "wakeup")
        agent._save_message(SID, "assistant", "唤醒回合回复")
        meta = json.loads(_meta_of(db_path))
        assert meta["turn_origin"] == "wakeup"
        assert meta["session_source"] == "test"

    def test_no_markers_metadata_unchanged(self):
        """回归面：无标记会话落库行metadata零变化（None保持None）。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._save_message(SID, "assistant", "普通回复")
        assert _meta_of(db_path) is None

    def test_unparsable_metadata_preserved(self):
        """不可解析metadata：原文保全raw_metadata，provenance照并（不静默丢数据）。"""
        db_path = _make_db()
        agent = _bare_agent(db_path)
        agent._session_sources[SID] = "test"
        agent._save_message(SID, "assistant", "回复", metadata="not-json{{")
        meta = json.loads(_meta_of(db_path))
        assert meta["raw_metadata"] == "not-json{{"
        assert meta["session_source"] == "test"


class TestProvenanceRecovery:
    def test_load_recovers_binding_from_history(self):
        """进程重启后重连：历史行的session_source恢复绑定，后续行继续标记。"""
        db_path = _make_db()
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO agent_messages (session_id, role, content, metadata)"
            " VALUES (?, 'user', '请回复OK两个字即可', ?)",
            (SID, json.dumps({"session_source": "test"})),
        )
        db.commit()
        db.close()
        agent = _bare_agent(db_path)
        msgs = agent._load_messages_from_db(SID)
        assert len(msgs) == 1  # 历史消息照常加载
        assert agent._session_sources[SID] == "test"
        # 恢复后新落库行继续携带标记
        agent._save_message(SID, "assistant", "OK")
        assert json.loads(_meta_of(db_path, 1))["session_source"] == "test"

    def test_load_unmarked_session_no_binding(self):
        """无标记历史会话：不绑定（行为零变化）。"""
        db_path = _make_db()
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO agent_messages (session_id, role, content)"
            " VALUES (?, 'user', '帮我查看当前目录')",
            (SID,),
        )
        db.commit()
        db.close()
        agent = _bare_agent(db_path)
        agent._load_messages_from_db(SID)
        assert agent._session_sources == {}

    def test_load_old_db_without_metadata_ok(self):
        """老库无metadata列：消息照常加载，不绑定（fail-safe）。"""
        db_path = tempfile.mktemp(suffix=".db")
        db = sqlite3.connect(db_path)
        db.execute(
            "CREATE TABLE agent_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,"
            " content TEXT, timestamp REAL)"
        )
        db.execute(
            "INSERT INTO agent_messages (session_id, role, content)"
            " VALUES (?, 'user', 'hi')",
            (SID,),
        )
        db.commit()
        db.close()
        agent = _bare_agent(db_path)
        msgs = agent._load_messages_from_db(SID)
        assert len(msgs) == 1
        assert agent._session_sources == {}
