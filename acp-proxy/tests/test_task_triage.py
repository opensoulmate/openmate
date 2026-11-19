"""
任务三级分流门真实验证 — 判级 / 三轨分流 / 一键换轨 / 统一回看板 / 双语义模式

链路（与生产同款装配：task_triage_api + task_triage 同 app）：
  判级（假 chat 注入，确定性）→ 分流（OpenSoul HTTP 以假 transport 注入）→
  三轨产物落记录 → 换轨带上下文（旧记录留痕）→ 看板实时状态 → 模式切换改模板偏好。
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin import task_triage as core
from plugin.task_triage_api import build_triage_router


# ─────────────────────────── 夹具 ───────────────────────────

JUDGE_JSON = json.dumps({
    "level": "medium", "confidence": 0.8,
    "reasons": ["需要多角色拆解", "需讨论共识"], "track": "group",
}, ensure_ascii=False)

FLOW_DRAFT = {
    "id": "tri-flow", "name": "测试流程", "version": 1,
    "triggers": [{"type": "manual"}],
    "nodes": [
        {"id": "n1", "type": "human", "user": "用户", "task": "检查结果",
         "form": "结论", "timeout_hours": 1, "on_timeout": "skip"},
        {"id": "n2", "type": "action", "actions": [{"type": "notify", "channel": "wechat",
                                                    "message": "流程完成"}],
         "when": "n1.result.done == True", "then": "", "else": ""},
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "triage.db")
    core.init_db()
    app = FastAPI()
    app.include_router(build_triage_router())
    return TestClient(app)


@pytest.fixture()
def fake_chat():
    async def _chat(messages):
        return JUDGE_JSON
    return _chat


@pytest.fixture()
def fake_http(monkeypatch):
    calls: list[tuple] = []

    async def _http(method, url, *, body=None, auth=None):
        calls.append((method, url, body, auth))
        if url.endswith("/api/sessions"):
            return {"ok": True, "session_id": body["id"]}
        if url.endswith("/api/ai-groups"):
            return {"id": "g-test01", "name": body["name"], "status": "active"}
        if url.endswith("/tasks"):
            return {"task_id": "t-test01", "subtasks_count": 4, "status": "planning"}
        if "/api/ai-groups/" in url:
            return {"id": "g-test01", "tasks": [
                {"id": "t-test01", "status": "executing"}]}
        if "/api/sessions/" in url:
            return {"message_count": 3, "last_activity_at": 0}
        return {}

    monkeypatch.setattr(core, "_http", _http)
    return calls


# ─────────────────────────── 判级 ───────────────────────────

def test_analyze_llm_judges_and_falls_back(fake_chat):
    r1 = asyncio.run(core.analyze_level("对比 Notion 和 Obsidian 的优劣", chat=fake_chat))
    assert r1["level"] == "medium" and r1["source"] == "llm"
    assert r1["track"] == "group" and r1["reasons"]

    # LLM 输出不合规 → 规则回落，如实标注来源
    async def bad_chat(messages):
        return "抱歉我不能输出 JSON"

    r2 = asyncio.run(core.analyze_level("每天晚上对账并走审批流程", chat=bad_chat))
    assert r2["source"] == "heuristic" and r2["level"] == "large"
    assert r2["track"] == "flow"


def test_heuristic_levels():
    assert core.heuristic_level("翻译这段话")["level"] == "small"
    assert core.heuristic_level("调研对比两款工具并给出选型建议")["level"] == "medium"
    assert core.heuristic_level("搭建每周对账的审批流程")["level"] == "large"


# ─────────────────────────── 三轨分流 ───────────────────────────

def test_dispatch_chat_creates_session_deep_link(client, fake_http):
    r = client.post("/triage/dispatch", json={"prompt": "写一首关于秋天的诗", "track": "chat"})
    assert r.status_code == 200, r.text
    rec = r.json()["record"]
    assert rec["track"] == "chat" and rec["status"] == "dispatched"
    assert rec["artifact"]["session_id"] == f"om-triage-{rec['id']}"
    assert rec["artifact"]["link"].startswith("/chat?session=")
    assert "auto_send=1" in rec["artifact"]["link"]
    # 建会话确实打到了 OpenSoul
    assert any(u.endswith("/api/sessions") for _, u, _, _ in fake_http)


def test_dispatch_group_creates_group_and_task(client, fake_http):
    r = client.post("/triage/dispatch", json={"prompt": "调研对比并出选型方案", "track": "group"})
    assert r.status_code == 200, r.text
    rec = r.json()["record"]
    assert rec["artifact"]["group_id"] == "g-test01"
    assert rec["artifact"]["task_id"] == "t-test01"
    assert rec["artifact"]["subtasks_count"] == 4
    assert rec["artifact"]["link"] == "/ai-groups?group=g-test01"
    assert any(u.endswith("/api/ai-groups/g-test01/tasks") for _, u, _, _ in fake_http)


def test_dispatch_flow_draft_then_confirm(client, monkeypatch):
    async def fake_generate(prompt):
        return {"ok": True, "definition": FLOW_DRAFT, "yaml": "id: tri-flow",
                "flow_id": FLOW_DRAFT["id"], "name": FLOW_DRAFT["name"], "attempts": 1}

    def fake_save(data, *, engine):
        return {"flow_id": data["id"], "saved": True}

    class FakeRun:
        run_id = "run-001"

    class FakeEngine:
        async def run_now(self, flow_id, trigger, **kw):
            return FakeRun()

    monkeypatch.setattr("plugin.flow_studio.generate_from_nl", fake_generate)
    monkeypatch.setattr("plugin.flow_studio.save_user_flow", fake_save)
    monkeypatch.setattr("plugin.flow_engine.get_engine", lambda: FakeEngine())

    r = client.post("/triage/dispatch", json={"prompt": "搭建发布保障流程", "track": "flow"})
    assert r.status_code == 200, r.text
    rec = r.json()["record"]
    assert rec["status"] == "draft_ready"
    assert rec["detail"]["definition"]["id"] == "tri-flow"

    r2 = client.post(f"/triage/records/{rec['id']}/confirm")
    assert r2.status_code == 200, r2.text
    rec2 = r2.json()["record"]
    assert rec2["status"] == "running"
    assert rec2["artifact"]["run_id"] == "run-001"
    assert rec2["artifact"]["flow_id"] == "tri-flow"

    # 非 draft_ready 不可重复装载跑
    assert client.post(f"/triage/records/{rec['id']}/confirm").status_code == 409


# ─────────────────────────── 一键换轨（上下文带过去）───────────────────────────

def test_switch_track_carries_context(client, fake_http, monkeypatch):
    async def fake_generate(prompt):
        return {"ok": True, "definition": FLOW_DRAFT, "yaml": "id: tri-flow",
                "flow_id": "tri-flow", "name": "测试流程", "attempts": 1}

    monkeypatch.setattr("plugin.flow_studio.generate_from_nl", fake_generate)
    r = client.post("/triage/dispatch", json={"prompt": "调研对比并出选型方案", "track": "group"})
    old = r.json()["record"]
    r2 = client.post(f"/triage/records/{old['id']}/switch", json={"to_track": "flow"})
    assert r2.status_code == 200, r2.text
    new = r2.json()["record"]
    assert new["track"] == "flow" and new["switched_from"] == old["id"]
    assert old["prompt"] in new["context"]["carried"]
    assert "g-test01" in new["context"]["carried"] or "group" in new["context"]["carried"]
    # 旧记录留痕
    old_after = client.get(f"/triage/records/{old['id']}").json()
    assert old_after["status"] == "switched" and old_after["switched_to"] == new["id"]
    # 同轨换轨被拒
    assert client.post(f"/triage/records/{new['id']}/switch",
                       json={"to_track": "flow"}).status_code == 400


# ─────────────────────────── 统一回看板 ───────────────────────────

def test_board_unifies_three_tracks_with_live_status(client, fake_http, monkeypatch):
    async def fake_generate(prompt):
        return {"ok": True, "definition": FLOW_DRAFT, "yaml": "", "flow_id": "tri-flow",
                "name": "测试流程", "attempts": 1}

    monkeypatch.setattr("plugin.flow_studio.generate_from_nl", fake_generate)
    for prompt, track in [("写诗", "chat"), ("调研对比", "group"), ("发布保障流程", "flow")]:
        assert client.post("/triage/dispatch",
                           json={"prompt": prompt, "track": track}).status_code == 200
    board = client.get("/triage/records").json()
    assert board["count"] == 3
    tracks = {item["track"] for item in board["records"]}
    assert tracks == {"chat", "group", "flow"}
    for item in board["records"]:
        assert item["link"], f"记录缺产物深链: {item['id']}"
        assert item["live"]["state"] in ("waiting", "running", "draft", "done",
                                         "failed", "unknown")


# ─────────────────────────── 个人/企业双语义 ───────────────────────────

def test_mode_switch_changes_labels_and_template_preference(client):
    m = client.get("/triage/mode").json()
    assert m["mode"] == "personal" and m["labels"]["personal"]["title"] == "一人公司"
    assert client.put("/triage/mode", json={"mode": "bad"}).status_code == 400

    personal = client.get("/triage/templates").json()
    assert personal["mode"] == "personal"
    assert personal["templates"][0]["id"] == "file-watch-process"
    assert personal["templates"][0]["preferred"] is True

    assert client.put("/triage/mode", json={"mode": "enterprise"}).status_code == 200
    m2 = client.get("/triage/mode").json()
    assert m2["mode"] == "enterprise"
    assert m2["labels"]["enterprise"]["flow_hint"] == "流程化 = 企业的内部流程"
    ent = client.get("/triage/templates").json()
    assert ent["templates"][0]["id"] == "approval-chain"
    ids = [t["id"] for t in ent["templates"] if t["preferred"]]
    assert "approval-chain" in ids and "file-watch-process" not in ids
