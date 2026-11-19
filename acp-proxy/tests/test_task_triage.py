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


# ─────────────────── 对话内隐式分流（无第二入口，2026-11-19 归位）───────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "DB_PATH", tmp_path / "triage.db")
    core.init_db()


def _chat_json(payload: str):
    async def _chat(messages):
        return payload
    return _chat


def test_conversation_triage_small_stays_in_chat(db):
    out = asyncio.run(core.conversation_triage(
        "你好呀，帮我解释一下什么是向量数据库", session_id="s-1",
        chat=_chat_json(json.dumps({"level": "small", "confidence": 0.9,
                                    "reasons": ["一句话即办"]}))))
    assert out is None  # 小任务不惊动用户——SoulMate 在会话里直接干
    assert core.list_records() == []  # 也不产生分流记录


def test_conversation_triage_low_confidence_or_heuristic_no_escalate(db):
    # LLM 不可用 → 规则回落 → 不升级（不假造分流），SoulMate 自己接着办
    out1 = asyncio.run(core.conversation_triage(
        "调研对比两款工具并给出选型建议", chat=_chat_json("不是 JSON")))
    assert out1 is None
    # LLM 低置信 → 同样不升级
    out2 = asyncio.run(core.conversation_triage(
        "调研对比两款工具并给出选型建议",
        chat=_chat_json(json.dumps({"level": "medium", "confidence": 0.3,
                                    "reasons": ["有点像协作"]}))))
    assert out2 is None
    assert core.list_records() == []


def test_conversation_triage_medium_auto_group_and_notice(db, fake_http):
    out = asyncio.run(core.conversation_triage(
        "调研对比 Notion 和 Obsidian 的优劣并给出选型方案", session_id="s-42",
        chat=_chat_json(JUDGE_JSON)))
    assert out is not None and out["track"] == "group"
    # 透明告知：转到群组了 + 产物深链，用户不需要做选择
    assert "转到群组了" in out["notice"]
    assert "/ai-groups?group=g-test01" in out["notice"]
    rec = out["record"]
    assert rec["status"] == "dispatched"
    assert rec["artifact"]["group_id"] == "g-test01" and rec["artifact"]["task_id"] == "t-test01"
    assert rec["context"] == {"source": "conversation", "session_id": "s-42"}
    # 确实自动建组 + 发布任务（服务端拆解执行）
    assert any(u.endswith("/api/ai-groups") for _, u, _, _ in fake_http)
    assert any(u.endswith("/api/ai-groups/g-test01/tasks") for _, u, _, _ in fake_http)


def test_conversation_triage_large_auto_flow(db, monkeypatch):
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

    out = asyncio.run(core.conversation_triage(
        "搭建每周发布对账的审批流程并长期例行运行",
        chat=_chat_json(json.dumps({"level": "large", "confidence": 0.9,
                                    "reasons": ["多阶段流程化工作"]}))))
    assert out is not None and out["track"] == "flow"
    # 大任务自动起流程：草稿 → 装载跑（隐式形态无预览确认入口，流程自身人工闸是安全闸）
    rec = out["record"]
    assert rec["status"] == "running" and rec["artifact"]["run_id"] == "run-001"
    assert rec["artifact"]["flow_id"] == "tri-flow"
    assert "流程" in out["notice"] and "/will?flow=tri-flow" in out["notice"]
    assert "跑了" in out["notice"]  # 已起流程，不是"等确认"


def test_conversation_triage_flow_role_retry_then_run(db, monkeypatch):
    """生成物引用无人可派角色 → 带可执行性约束重生成一次 → 装载跑真起流程"""
    prompts: list[str] = []

    async def fake_generate(prompt):
        prompts.append(prompt)
        return {"ok": True, "definition": FLOW_DRAFT, "yaml": "",
                "flow_id": "tri-flow", "name": "测试流程", "attempts": 1}

    calls = {"save": 0}

    def fake_save(data, *, engine):
        calls["save"] += 1
        if calls["save"] == 1:
            raise RuntimeError("角色 '发布负责人' 无可用成员（org 表无在办容量成员/无 backup），无法派单")
        return {"flow_id": data["id"], "saved": True}

    class FakeRun:
        run_id = "run-002"

    class FakeEngine:
        async def run_now(self, flow_id, trigger, **kw):
            return FakeRun()

    monkeypatch.setattr("plugin.flow_studio.generate_from_nl", fake_generate)
    monkeypatch.setattr("plugin.flow_studio.save_user_flow", fake_save)
    monkeypatch.setattr("plugin.flow_engine.get_engine", lambda: FakeEngine())

    out = asyncio.run(core.conversation_triage(
        "搭建每周发布对账的审批流程并长期例行运行",
        chat=_chat_json(json.dumps({"level": "large", "confidence": 0.9,
                                    "reasons": ["多阶段流程化工作"]}))))
    assert out is not None
    assert out["record"]["status"] == "running" and out["record"]["artifact"]["run_id"] == "run-002"
    assert "跑了" in out["notice"]
    # 重试生成带了可执行性约束（human 节点派本人、agent 节点派 soulmate）
    assert len(prompts) == 2 and "可执行性约束" in prompts[1] and "用户" in prompts[1]


def test_conversation_triage_flow_confirm_failure_keeps_draft_honest(db, monkeypatch):
    async def fake_generate(prompt):
        return {"ok": True, "definition": FLOW_DRAFT, "yaml": "",
                "flow_id": "tri-flow", "name": "测试流程", "attempts": 1}

    def fake_save(data, *, engine):
        raise RuntimeError("磁盘写入失败")

    monkeypatch.setattr("plugin.flow_studio.generate_from_nl", fake_generate)
    monkeypatch.setattr("plugin.flow_studio.save_user_flow", fake_save)

    out = asyncio.run(core.conversation_triage(
        "搭建每周发布对账的审批流程并长期例行运行",
        chat=_chat_json(json.dumps({"level": "large", "confidence": 0.9,
                                    "reasons": ["多阶段流程化工作"]}))))
    # 装载失败：草稿保留 + 如实降级告知（不假装已跑）
    assert out is not None
    assert out["record"]["status"] == "draft_ready"
    assert "草稿" in out["notice"] and "确认" in out["notice"]
    assert "跑了" not in out["notice"]


def test_conversation_triage_dispatch_failure_falls_back_to_soulmate(db, monkeypatch):
    async def _boom(method, url, *, body=None, auth=None, attempts=3):
        raise RuntimeError("OpenSoul 不可达")
    monkeypatch.setattr(core, "_http", _boom)
    out = asyncio.run(core.conversation_triage(
        "调研对比 Notion 和 Obsidian 的优劣并给出选型方案",
        chat=_chat_json(JUDGE_JSON)))
    # 分流执行失败 → 返回 None，SoulMate 直接办（不打断对话、不假造升级）
    assert out is None
    recs = core.list_records()
    assert len(recs) == 1 and recs[0]["status"] == "failed"  # 失败留痕如实可查


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
