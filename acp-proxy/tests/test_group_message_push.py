# -*- coding: utf-8 -*-
"""ws_group /internal/group-message 内部推送端点（周期#81 · 13.4 验收行4根因修复）。

背景：任务链"每步结果回群聊"此前只落 discussion_messages + task_progress 时间线帧，
而聊天流前端只认 agent_message 帧（ai-groups-store.ts case 'agent_message'）→
群里实时看不到 Agent 回复（用户历史投诉"群里永远没有 Agent 回复"的残留形态）。
opensoul ai_groups.GROUP_MSG_PUSH 常量指向本端点但此前不存在（死契约）。

覆盖：
1. loopback 调用广播 agent_message 帧到房间内连接（帧形状对齐前端 ai-groups-store）
2. 非 loopback 拒绝（内部面不开注入口）+ 缺 group_id/content 拒绝
3. 默认只广播不落库（落库单一写入源=opensoul _notify_group_chat 直写）；
   persist=true 才写 discussion_messages
4. 房间无人在线 delivered=0 不算错（消息在库，历史加载可见）
"""
import asyncio
import sqlite3
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).parent.parent))

import ws_group

HERE = Path(__file__).parent.parent


class FakeWS:
    """最小 WebSocket 替身：_safe_send 只用 send_json。"""

    def __init__(self):
        self.frames = []

    async def send_json(self, data):
        self.frames.append(data)


def _make_app():
    app = FastAPI()
    app.include_router(ws_group.router)
    return app


def _call(payload, client_host="127.0.0.1", app=None):
    """经 ASGI 真实 HTTP 路径调用端点（可指定 client host 语义）。"""
    app = app or _make_app()
    transport = httpx.ASGITransport(app=app, client=(client_host, 50000))

    async def _run():
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await c.post("/internal/group-message", json=payload)

    return asyncio.run(_run())


@pytest.fixture()
def room(monkeypatch):
    """房间状态清零 + fixture 期注册的 fake 连接收尾清理。"""
    ws_group._group_rooms.clear()
    yield
    ws_group._group_rooms.clear()


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """discussion_messages 落库面隔离到 tmp（不摸真实 ai_groups.db）。"""
    db_path = tmp_path / "ai_groups.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE discussion_messages (
            id TEXT, group_id TEXT, task_id TEXT, agent_id TEXT, agent_name TEXT,
            intent TEXT, content TEXT, metadata TEXT, round_num INTEGER, created_at TEXT)"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(ws_group, "_OPENSOUL_DB", str(db_path))
    return db_path


def test_loopback_broadcasts_agent_message_frame(room):
    """验收行4契约：内部推送 → 房间内连接收到 agent_message 帧。"""
    fake = FakeWS()
    ws_group._group_rooms["g-push1"] = {fake: {"user_id": "u1", "joined_at": 0.0}}
    resp = _call(
        {
            "group_id": "g-push1",
            "agent_id": "soulmate",
            "agent_name": "SoulMate",
            "content": "步骤结果：已完成",
            "task_id": "t1",
        }
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["delivered"] == 1
    assert len(fake.frames) == 1
    frame = fake.frames[0]
    assert frame["type"] == "agent_message"
    d = frame["data"]
    # 帧形状对齐前端 ai-groups-store.ts case 'agent_message' 消费字段
    assert d["agent_id"] == "soulmate"
    assert d["agent_name"] == "SoulMate"
    assert d["content"] == "步骤结果：已完成"
    assert d["intent"] == "agent_reply"
    assert d["task_id"] == "t1"
    assert d["id"] and d["timestamp"] > 0


def test_non_loopback_rejected(room):
    """内部面：非 loopback 调用拒绝，房间零帧（不对外开注入口）。"""
    fake = FakeWS()
    ws_group._group_rooms["g-push2"] = {fake: {"user_id": "u1", "joined_at": 0.0}}
    resp = _call(
        {"group_id": "g-push2", "content": "x"}, client_host="203.0.113.9"
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is False
    assert "loopback" in resp.json()["error"]
    assert fake.frames == []


def test_missing_params_rejected(room):
    """缺 group_id / content 显式拒绝，不广播。"""
    fake = FakeWS()
    ws_group._group_rooms["g-push3"] = {fake: {"user_id": "u1", "joined_at": 0.0}}
    for payload in ({"content": "x"}, {"group_id": "g-push3"}, {"group_id": "  ", "content": "x"}):
        resp = _call(payload)
        assert resp.json()["ok"] is False, payload
    assert fake.frames == []


def test_default_broadcast_only_no_persist(room, tmp_db):
    """默认只广播不落库（落库单一写入源=opensoul 直写，禁双写）。"""
    fake = FakeWS()
    ws_group._group_rooms["g-push4"] = {fake: {"user_id": "u1", "joined_at": 0.0}}
    resp = _call({"group_id": "g-push4", "content": "结果A", "agent_id": "hermes"})
    assert resp.json()["ok"] is True
    assert resp.json()["msg_id"].startswith("push-")
    assert len(fake.frames) == 1
    conn = sqlite3.connect(str(tmp_db))
    n = conn.execute("SELECT COUNT(*) FROM discussion_messages").fetchone()[0]
    conn.close()
    assert n == 0


def test_persist_true_writes_row(room, tmp_db):
    """persist=true 供无落库能力的调用方：写 discussion_messages 且帧 id 与行 id 一致。"""
    fake = FakeWS()
    ws_group._group_rooms["g-push5"] = {fake: {"user_id": "u1", "joined_at": 0.0}}
    resp = _call(
        {
            "group_id": "g-push5",
            "content": "结果B",
            "agent_id": "mimo",
            "agent_name": "MiMo",
            "task_id": "t5",
            "persist": True,
        }
    )
    body = resp.json()
    assert body["ok"] is True
    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT id, group_id, agent_id, intent, content, metadata FROM discussion_messages"
    ).fetchall()
    conn.close()
    assert len(row) == 1
    rid, gid, aid, intent, content, metadata = row[0]
    assert (gid, aid, intent, content) == ("g-push5", "mimo", "agent_reply", "结果B")
    assert rid == body["msg_id"]
    assert fake.frames[0]["data"]["id"] == rid


def test_empty_room_delivered_zero_is_ok(room):
    """房间无人在线：delivered=0 不算错（消息在库，客户端连上走历史加载）。"""
    resp = _call({"group_id": "g-nobody", "content": "结果C"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["delivered"] == 0


def test_source_wiring_notify_calls_push_endpoint():
    """接线钉：opensoul _notify_group_chat 必须真的推 GROUP_MSG_PUSH（死常量复活）。"""
    src = (Path("/home/climbing/opensoul") / "src" / "api" / "ai_groups.py").read_text(
        encoding="utf-8"
    )
    assert "GROUP_MSG_PUSH" in src
    # 常量被真实消费（调用点），不是只有定义
    assert src.count("GROUP_MSG_PUSH") >= 2
    assert "push_cli.post" in src or "post(\n                    GROUP_MSG_PUSH" in src
    assert "await push_cli.post(" in src or "GROUP_MSG_PUSH," in src
