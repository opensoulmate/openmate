"""
建流零门槛真实验证 — 一句话建流 / 模板库 / 常驻保存 / 可视化编辑回写 / YAML 导出

链路（与生产同款装配：flow_studio_api + flow_api 同 app、注册序同 app.py）：
自然语言 → LLM 生成（假 chat 注入，确定性）→ flow_def 校验 → 预览规范字典 →
存为常驻流程（覆盖件落盘）→ 重启装载 → 手动真跑全链（人工交付→判断分支→提醒）→
YAML 导出可回灌。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plugin.flow_def import FlowDefError, parse_flow
from plugin.flow_engine import FlowEngine
from plugin import flow_studio


# ─────────────────────────── 夹具 ───────────────────────────

# 真实诉求样例（用户原话）："每天晚上检查公众号发布情况，漏发就提醒我"
NL_FLOW = {
    "id": "mp-publish-check",
    "name": "公众号发布检查提醒",
    "version": 1,
    "triggers": [
        {"type": "cron", "cron": "0 21 * * *"},
        {"type": "manual"},
    ],
    "nodes": [
        {"id": "check-publish", "type": "human", "user": "用户",
         "task": "检查公众号今天的发布情况，交付 result.missed（true=漏发/false=已发）",
         "form": "检查结果（result.missed）", "timeout_hours": 12,
         "on_timeout": "skip"},
        {"id": "check", "type": "decision",
         "when": "$.check-publish.result.missed == true",
         "then": "remind", "else": "finish"},
        {"id": "remind", "type": "action",
         "actions": [{"type": "notify", "channel": "in-app",
                      "message": "公众号今天漏发了，赶紧补发"}]},
        {"id": "finish", "type": "action",
         "actions": [{"type": "notify", "channel": "in-app",
                      "message": "公众号发布检查完成"}]},
    ],
    "observability": {"retain_runs": 30},
}


@pytest.fixture()
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_TASKS_DIR", str(tmp_path / "collab-tasks"))
    from plugin.flow_collab import reset_collab
    reset_collab()
    return FlowEngine(tmp_path / "flow-runs")


@pytest.fixture()
def env(engine):
    app = FastAPI()
    # 生产同款注册序：flow_studio 在前（/flows/templates 不被 /flows/{flow_id} 吞）
    from plugin.flow_studio_api import build_flow_studio_router
    from plugin.flow_api import build_flow_router
    from plugin.collab_api import build_collab_router
    app.include_router(build_flow_studio_router(engine))
    app.include_router(build_flow_router(engine))
    app.include_router(build_collab_router())
    return {"client": TestClient(app), "engine": engine}


def _fake_chat(payloads: list[str]):
    """确定性假 LLM：按序吐出预设输出（可为异常），并记录收到的消息"""
    calls: list[list[dict]] = []

    async def chat(messages: list[dict]) -> str:
        calls.append(messages)
        item = payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return chat, calls


# ─────────────────────────── ① 模板库 ───────────────────────────

def test_templates_all_instantiate_and_validate():
    templates = flow_studio.list_templates()
    scenes = {t["id"] for t in templates}
    assert {"scheduled-reconcile", "file-watch-process",
            "approval-chain", "multi-agent-collab"} <= scenes
    for item in templates:
        result = flow_studio.instantiate_template(item["id"], {})
        defn = parse_flow(result["definition"])
        assert defn.nodes
        # 三语元数据齐全（i18n 三语铁律）
        for field in ("name", "description", "scene"):
            assert set(item[field]) == {"zh", "en", "ja"}
        for param in item["params"]:
            assert set(param["label"]) == {"zh", "en", "ja"}


def test_template_params_substitute_and_revalidate():
    result = flow_studio.instantiate_template("approval-chain", {
        "flow_id": "my-approval", "flow_name": "合同审批",
        "approver1": "张三", "approver2": "李四", "timeout_hours": "24",
    })
    data = result["definition"]
    assert data["id"] == "my-approval"
    assert data["name"] == "合同审批"
    users = {n["id"]: n.get("user") for n in data["nodes"] if n["type"] == "human"}
    assert users == {"approve-l1": "张三", "approve-l2": "李四"}
    assert all(n.get("timeout_hours") == 24.0 for n in data["nodes"]
               if n["type"] == "human")
    parse_flow(data)  # 参数替换后仍过校验


def test_template_unknown_id_rejected():
    with pytest.raises(FlowDefError):
        flow_studio.instantiate_template("no-such-template", {})


# ─────────────────────────── ② 一句话建流（LLM 生成 + 校验回喂）───────────────────────────

def test_nl_generate_first_shot_ok():
    chat, calls = _fake_chat([json.dumps(NL_FLOW, ensure_ascii=False)])
    result = asyncio.run(flow_studio.generate_from_nl(
        "每天晚上检查公众号发布情况，漏发就提醒我", chat=chat, tools=[]))
    assert result["ok"] and result["attempts"] == 1
    assert result["flow_id"] == "mp-publish-check"
    # schema 指南 + 需求都进了 prompt，工具清单给了生成方
    assert "流程定义生成器" in calls[0][0]["content"]
    assert "每天晚上检查公众号发布情况" in calls[0][1]["content"]
    parse_flow(result["definition"])


def test_nl_generate_repairs_on_validation_error():
    """校验失败带报因回喂，第二发修正后通过（不静默假造）"""
    broken = dict(NL_FLOW)
    broken = json.loads(json.dumps(NL_FLOW))
    broken["nodes"][1]["else"] = "no-such-node"   # 引用不存在节点 → 校验失败
    chat, calls = _fake_chat([
        "```json\n" + json.dumps(broken, ensure_ascii=False) + "\n```",
        json.dumps(NL_FLOW, ensure_ascii=False),
    ])
    result = asyncio.run(flow_studio.generate_from_nl("漏发提醒", chat=chat, tools=[]))
    assert result["ok"] and result["attempts"] == 2
    # 回喂消息里带真实报因
    feedback = calls[1][-1]["content"]
    assert "校验失败" in feedback and "no-such-node" in feedback


def test_nl_generate_all_attempts_fail_reports_error():
    chat, _ = _fake_chat(["不是 JSON", "仍然不是 JSON", "第三次也不行"])
    result = asyncio.run(flow_studio.generate_from_nl(
        "随便什么", chat=chat, tools=[], max_attempts=3))
    assert not result["ok"] and result["attempts"] == 3
    assert "JSON" in result["error"]


def test_extract_json_tolerates_fences_and_noise():
    assert flow_studio.extract_json('说明\n```json\n{"a": 1}\n```\n完毕') == {"a": 1}
    assert flow_studio.extract_json('杂文 {"id": "x", "n": 2} 尾巴') == {"id": "x", "n": 2}
    with pytest.raises(FlowDefError):
        flow_studio.extract_json("没有大括号")


# ─────────────────────────── ③ 常驻保存 / 编辑回写 / YAML 导出 ───────────────────────────

def test_save_user_flow_persists_and_reloads(tmp_path, engine):
    saved = flow_studio.save_user_flow(NL_FLOW, engine=engine)
    assert saved["flow_id"] == "mp-publish-check"
    overlay = Path(saved["path"])
    assert overlay.is_file() and overlay.parent.name == "flow-defs"
    assert engine.get_flow("mp-publish-check").name == "公众号发布检查提醒"
    # 重启同款：新引擎实例从覆盖件仓装载（常驻，插件重载不冲掉）
    reborn = FlowEngine(tmp_path / "flow-runs")
    assert reborn.get_flow("mp-publish-check").name == "公众号发布检查提醒"


def test_visual_edit_roundtrip_save(tmp_path, engine):
    """可视化编辑链：导出规范字典 → 前端改参数（触发器/节点/审批人）→ save 回写"""
    flow_studio.save_user_flow(NL_FLOW, engine=engine)
    data = flow_studio.defn_to_data(engine.get_flow("mp-publish-check"))
    assert data["nodes"][1]["else"] == "finish"      # 外键名 else（非 else_）
    # 模拟编辑器改参：换 cron、改提醒文案、换收件人
    data["triggers"][0]["cron"] = "30 22 * * *"
    data["nodes"][0]["user"] = "张三"
    data["nodes"][2]["actions"][0]["message"] = "改过的提醒文案"
    saved = flow_studio.save_user_flow(data, engine=engine)
    assert saved["triggers"] == ["cron", "manual"]
    updated = engine.get_flow("mp-publish-check")
    assert updated.triggers[0].cron == "30 22 * * *"
    assert updated.node_map["check-publish"].user == "张三"
    assert updated.node_map["remind"].actions[0].message == "改过的提醒文案"
    # 落盘 YAML 与内存一致（重启即编辑结果）
    reloaded = yaml.safe_load(Path(saved["path"]).read_text(encoding="utf-8"))
    assert reloaded["triggers"][0]["cron"] == "30 22 * * *"


def test_save_rejects_invalid_definition(engine):
    bad = json.loads(json.dumps(NL_FLOW))
    bad["nodes"][1]["when"] = "$$ 乱码表达式"
    with pytest.raises(FlowDefError):
        flow_studio.save_user_flow(bad, engine=engine)
    assert engine.store.runs_dir.exists()  # 失败不留半成品


def test_yaml_export_reimportable(tmp_path, engine):
    flow_studio.save_user_flow(NL_FLOW, engine=engine)
    data = flow_studio.defn_to_data(engine.get_flow("mp-publish-check"))
    text = flow_studio.data_to_yaml(data)
    reparsed = parse_flow(yaml.safe_load(text))
    assert reparsed.id == "mp-publish-check"
    assert reparsed.node_map["check"].else_ == "finish"


# ─────────────────────────── ④ HTTP 面 + 真跑全链 ───────────────────────────

def test_api_route_order_templates_not_swallowed(env):
    """/flows/templates 必须命中模板库（不被 flow_api /{flow_id} 吞成 flow id）"""
    res = env["client"].get("/flows/templates")
    assert res.status_code == 200
    assert res.json()["count"] >= 4


def test_api_instantiate_preview_save_export_chain(env):
    client, engine = env["client"], env["engine"]
    inst = client.post("/flows/templates/instantiate", json={
        "template_id": "scheduled-reconcile",
        "params": {"flow_id": "night-recon", "flow_name": "夜间对账"}})
    assert inst.status_code == 200
    definition = inst.json()["definition"]
    assert definition["id"] == "night-recon"

    # 预览校验：合法 → 规范化；非法 → 400 带字段报因
    ok = client.post("/flows/preview", json={"definition": definition})
    assert ok.status_code == 200 and ok.json()["ok"]
    bad = json.loads(json.dumps(definition))
    bad["nodes"] = []
    err = client.post("/flows/preview", json={"definition": bad})
    assert err.status_code == 400 and "nodes" in err.json()["detail"]

    # 存为常驻流程 → 清单可见（工作台常驻入口）
    saved = client.post("/flows/save", json={"definition": definition})
    assert saved.status_code == 200
    flows = client.get("/flows").json()["flows"]
    assert any(f["id"] == "night-recon" for f in flows)

    # YAML 导出可回灌
    exported = client.get("/flows/night-recon/yaml").json()
    parse_flow(yaml.safe_load(exported["yaml"]))

    # 404 语义
    assert client.get("/flows/no-such/yaml").status_code == 404


def test_api_draft_ok_and_unprocessable(env, monkeypatch):
    client = env["client"]

    async def good_chat(messages):
        return json.dumps(NL_FLOW, ensure_ascii=False)

    monkeypatch.setattr(flow_studio, "_default_chat", good_chat)
    res = client.post("/flows/draft", json={
        "prompt": "每天晚上检查公众号发布情况，漏发就提醒我"})
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] and body["flow_id"] == "mp-publish-check"

    async def bad_chat(messages):
        return "完全不是定义"

    monkeypatch.setattr(flow_studio, "_default_chat", bad_chat)
    res = client.post("/flows/draft", json={"prompt": "随便"})
    assert res.status_code == 422 and res.json()["detail"]


def test_nl_to_run_full_chain(env, monkeypatch):
    """一句真实自然语言 → 生成 → 保存 → 真跑全链（交付→判断分支→提醒触达）"""
    client, engine = env["client"], env["engine"]

    async def good_chat(messages):
        return json.dumps(NL_FLOW, ensure_ascii=False)

    monkeypatch.setattr(flow_studio, "_default_chat", good_chat)
    draft = client.post("/flows/draft", json={
        "prompt": "每天晚上检查公众号发布情况，漏发就提醒我"}).json()
    client.post("/flows/save", json={"definition": draft["definition"]})

    run = client.post("/flows/mp-publish-check/run", json={}).json()
    assert run["status"] == "waiting-approval"     # 检查任务入收发台
    tasks = client.get("/collab/tasks").json()
    task = next(t for t in tasks["tasks"]
                if t["flow_id"] == "mp-publish-check")
    assert "公众号" in task["instruction"]

    # 交付检查结果（漏发=true）→ 判断分支走 remind → 收尾 finish
    delivered = client.post(f"/collab/tasks/{task['task_id']}/deliver", json={
        "result": {"missed": True}, "delivered_by": "用户"})
    assert delivered.status_code == 200
    detail = client.get(f"/flows/runs/{run['run_id']}").json()
    assert detail["status"] == "completed"
    statuses = {n["node"]: n["status"] for n in detail["nodes"]}
    assert statuses["remind"] == "ok" and statuses["finish"] == "ok"
    messages = [n["message"] for n in detail["notifications"]]
    assert any("漏发" in m for m in messages)
    decision = [e for e in detail["log"] if e["event"] == "decision"]
    assert decision and "then" in decision[0]["detail"]
