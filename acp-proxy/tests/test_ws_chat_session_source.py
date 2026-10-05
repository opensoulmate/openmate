# -*- coding: utf-8 -*-
"""会话级provenance · /ws/chat legacy桥写入路径 — acp-proxy侧

背景（dev-reports 20:35轮遗留#2实锤）：ws_chat._store_agent_message 直插
agent_messages 无metadata列，/ws/chat agent_proxy路径的落库行逃逸
ExperienceCollector的测试会话provenance门——52个evo-test-*探针会话63行
未标记，其中11条assistant行已派生假experiences污染生产经验链。

覆盖：
1. metadata列probe+ALTER幂等迁移（parent_message_id既有模式同款）
2. 产生侧标注：负载`_meta: {"source": "test"}`绑定会话出身→落库行metadata
   带session_source（顶层source兼容=kwargs派发形态）
3. 会话级语义：任一消息声明→会话内后续所有行携带（与ExperienceCollector
   "任一行标记即整会话生效"对称）
4. 跨进程重启恢复：内存绑定丢失后从历史行metadata沿用（soulmate
   _load_messages_from_db同款）
5. 零声明零变化：未声明会话metadata=NULL（真实用户对话行为不变）
6. merge语义：已有键全保留、坏JSON原文保全raw_metadata（soulmate
   _merge_session_meta同款）
7. 接线钉：chat_websocket真实路径调用_bind_session_source；integration_test
   探针声明test出身

无网络/无live server依赖：隔离SQLite + monkeypatch模块级DB路径。
"""
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ws_chat  # noqa: E402

# v1精确老schema（无attachments/parent_message_id/metadata列）
OLD_DDL = [
    """CREATE TABLE IF NOT EXISTS agent_sessions (
        id TEXT PRIMARY KEY,
        title TEXT,
        created_at REAL,
        last_activity_at REAL,
        message_count INTEGER DEFAULT 0,
        metadata TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS agent_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        role TEXT,
        content TEXT,
        timestamp REAL,
        FOREIGN KEY (session_id) REFERENCES agent_sessions(id)
    )""",
]


@pytest.fixture(autouse=True)
def _clean_binding_registry():
    """模块级绑定表清零——测试间不泄漏会话出身。"""
    ws_chat._session_sources.clear()
    yield
    ws_chat._session_sources.clear()


def _make_old_db(tmp_path, sessions=("sess_ws_a",)) -> str:
    db_path = str(tmp_path / "ws_source.db")
    conn = sqlite3.connect(db_path)
    for ddl in OLD_DDL:
        conn.execute(ddl)
    for sid in sessions:
        conn.execute(
            "INSERT INTO agent_sessions (id, title, created_at, last_activity_at, message_count)"
            " VALUES (?, 'New Chat', 1000.0, 1000.0, 0)",
            (sid,),
        )
    conn.commit()
    conn.close()
    return db_path


def _metas(db_path, sid) -> list:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT metadata FROM agent_messages WHERE session_id = ? ORDER BY id", (sid,)
    ).fetchall()
    conn.close()
    return [r["metadata"] for r in rows]


def _cols(db_path) -> list:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    names = [r["name"] for r in conn.execute("PRAGMA table_info(agent_messages)")]
    conn.close()
    return names


class TestMetadataColumnMigration:
    def test_probe_adds_metadata_column_to_old_schema(self, tmp_path, monkeypatch):
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._store_agent_message("sess_ws_a", "user", "x")
        assert "metadata" in _cols(db)

    def test_parent_column_migration_not_regressed(self, tmp_path, monkeypatch):
        """parent_message_id既有probe行为回归（新probe并存不破坏旧probe）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._store_agent_message("sess_ws_a", "user", "x")
        cols = _cols(db)
        assert "parent_message_id" in cols and "metadata" in cols


class TestSessionSourceStamping:
    def test_no_source_declared_metadata_null(self, tmp_path, monkeypatch):
        """零声明零变化：真实用户对话metadata=NULL（旧行为逐字节保持）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._store_agent_message("sess_ws_a", "user", "你好")
        ws_chat._store_agent_message("sess_ws_a", "assistant", "回复")
        assert _metas(db, "sess_ws_a") == [None, None]

    def test_bind_meta_source_stamps_all_rows(self, tmp_path, monkeypatch):
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "test"}})
        ws_chat._store_agent_message("sess_ws_a", "user", "ping")
        ws_chat._store_agent_message("sess_ws_a", "assistant", "pong")
        metas = [json.loads(m) for m in _metas(db, "sess_ws_a")]
        assert [m["session_source"] for m in metas] == ["test", "test"]

    def test_top_level_source_fallback(self, tmp_path, monkeypatch):
        """顶层source兼容（kwargs派发形态——soulmate _bind_session_source同款双形态）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"source": "test"})
        ws_chat._store_agent_message("sess_ws_a", "user", "ping")
        assert json.loads(_metas(db, "sess_ws_a")[0])["session_source"] == "test"

    def test_session_level_binding_persists_across_messages(self, tmp_path, monkeypatch):
        """会话级语义：首条声明→后续未声明消息同样携带（与collector对称）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "test"}})
        ws_chat._store_agent_message("sess_ws_a", "user", "m1")
        ws_chat._store_agent_message("sess_ws_a", "user", "m2")  # 无声明
        metas = [json.loads(m) for m in _metas(db, "sess_ws_a")]
        assert [m["session_source"] for m in metas] == ["test", "test"]

    def test_db_fallback_recovers_binding_after_restart(self, tmp_path, monkeypatch):
        """跨进程重启：内存绑定丢失→历史行metadata沿用（_load_messages_from_db同款）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "test"}})
        ws_chat._store_agent_message("sess_ws_a", "user", "m1")
        ws_chat._session_sources.clear()  # 模拟进程重启
        ws_chat._store_agent_message("sess_ws_a", "assistant", "m2")
        metas = [json.loads(m) for m in _metas(db, "sess_ws_a")]
        assert [m["session_source"] for m in metas] == ["test", "test"]
        # 恢复的绑定已缓存进内存表
        assert ws_chat._session_sources.get("sess_ws_a") == "test"

    def test_binding_isolated_per_session(self, tmp_path, monkeypatch):
        db = _make_old_db(tmp_path, sessions=("sess_ws_a", "sess_ws_b"))
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "test"}})
        ws_chat._store_agent_message("sess_ws_a", "user", "a1")
        ws_chat._store_agent_message("sess_ws_b", "user", "b1")
        assert json.loads(_metas(db, "sess_ws_a")[0])["session_source"] == "test"
        assert _metas(db, "sess_ws_b") == [None]

    def test_non_test_source_still_stamped(self, tmp_path, monkeypatch):
        """非test出身（real等）同样落标记——采集侧只跳过精确'test'（jobs.source同款）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "real"}})
        ws_chat._store_agent_message("sess_ws_a", "user", "m")
        assert json.loads(_metas(db, "sess_ws_a")[0])["session_source"] == "real"


class TestMetadataMerge:
    def test_merge_preserves_existing_metadata_keys(self, tmp_path, monkeypatch):
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "test"}})
        ws_chat._store_agent_message(
            "sess_ws_a", "user", "m", metadata='{"marker": "kiloMemory"}'
        )
        meta = json.loads(_metas(db, "sess_ws_a")[0])
        assert meta["marker"] == "kiloMemory"  # 既有键全保留
        assert meta["session_source"] == "test"

    def test_unparseable_metadata_raw_preserved(self, tmp_path, monkeypatch):
        """坏JSON原文保全raw_metadata（soulmate _merge_session_meta同款，绝不静默丢）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "test"}})
        ws_chat._store_agent_message("sess_ws_a", "user", "m", metadata="not-json{{")
        meta = json.loads(_metas(db, "sess_ws_a")[0])
        assert meta["raw_metadata"] == "not-json{{"
        assert meta["session_source"] == "test"

    def test_no_binding_metadata_passthrough(self, tmp_path, monkeypatch):
        """未绑定会话：调用方metadata原样落库（不并入任何provenance键）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._store_agent_message("sess_ws_a", "user", "m", metadata='{"k": 1}')
        assert json.loads(_metas(db, "sess_ws_a")[0]) == {"k": 1}


class TestBindContract:
    def test_empty_payload_no_binding(self):
        ws_chat._bind_session_source("sess_x", None)
        ws_chat._bind_session_source("sess_x", {})
        ws_chat._bind_session_source("sess_x", {"_meta": {}})
        assert ws_chat._session_sources == {}

    def test_meta_source_precedence_over_top_level(self):
        ws_chat._bind_session_source(
            "sess_x", {"_meta": {"source": "test"}, "source": "real"}
        )
        assert ws_chat._session_sources["sess_x"] == "test"

    def test_collector_contract_key_name(self, tmp_path, monkeypatch):
        """跨仓契约钉：落库键必须是session_source（opensoul
        experience_collector._row_session_source 读取的唯一键名）"""
        db = _make_old_db(tmp_path)
        monkeypatch.setattr(ws_chat, "_OPENSOUL_DB", db)
        ws_chat._bind_session_source("sess_ws_a", {"_meta": {"source": "test"}})
        ws_chat._store_agent_message("sess_ws_a", "user", "m")
        meta = json.loads(_metas(db, "sess_ws_a")[0])
        assert set(meta.keys()) == {"session_source"}


class TestWiring:
    def test_chat_websocket_calls_bind_on_real_path(self):
        """接线钉：chat_websocket真实消息路径调用_bind_session_source（防脱线死代码）"""
        src = inspect.getsource(ws_chat.chat_websocket)
        assert "_bind_session_source(agent_session_id, data)" in src

    def test_store_message_resolves_and_stamps(self):
        """接线钉：_store_agent_message体内走_resolve_session_source并写metadata列"""
        src = inspect.getsource(ws_chat._store_agent_message)
        assert "_resolve_session_source(db, session_id)" in src
        assert "meta[\"session_source\"] = source" in src

    def test_integration_probe_declares_test_origin(self):
        """接线钉：integration_test探针消息声明_meta.source='test'（产生侧标注）"""
        import integration_test

        src = inspect.getsource(integration_test._test_ws_send_receive)
        assert '"_meta": {"source": "test"}' in src
