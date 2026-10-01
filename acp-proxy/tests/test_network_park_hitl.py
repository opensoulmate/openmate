# -*- coding: utf-8 -*-
"""HITL网络等待交互卡——人工拒绝 + 跨进程park状态可见（kilocode人工拒绝窗口语义）。

调研来源：kilocode session/network.ts（RESUME_MS宽限期=人工拒绝窗口，前端
"等待网络恢复…[拒绝]"卡片）；SUMMARY P0-9 HITL专项（dev-reports多轮挂账
"HITL网络等待交互卡"）；tool_output账本同款"子进程写/app进程读"跨进程模式。

覆盖：
1. 拒绝标志文件单元：显式id列表消费/部分消费/空ids不写/坏文件fail-safe
2. park人工拒绝：live拒绝（真实竞态）/watch期拒绝/宽限期拒绝>自动续跑/
   误伤防护（他id不拒）/只消费一次/turn预算同款spent（后续fail-fast）
3. per-pid状态快照：写快照/合并多进程/排除本进程/死pid清理/坏文件跳过/
   merge_park_states去重+计数器求和+pending重算
4. 端点接线（真实FastAPI TestClient）：/api/network/status跨进程合并可见、
   /api/network/reject 两种模式写标志
5. 接线钉：app.py端点+agent/start.py恢复回调（MCP重连补到park所在进程）+
   llm_engine三路消费不变+kilocode常量逐值不变
"""
import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))
# 与main.py同款：正式插件统一存放于<root>/plugins/（routes.gene_loop依赖）
sys.path.insert(0, str(HERE.parent))

import httpx
import pytest

from utils import network_state
from utils.network_state import (
    ABORTED,
    RESUMED,
)


def _src(rel):
    return (HERE / rel).read_text(encoding="utf-8")


# ── fixtures：状态清零 + tmp状态目录 + 快速park常量（真实毫秒级sleep让出循环）──


@pytest.fixture
def clean(monkeypatch, tmp_path):
    """状态清零 + 状态目录重定向tmp（防污染data/network_park真实目录）"""
    monkeypatch.setenv("NETWORK_PARK_STATE_DIR", str(tmp_path))
    network_state._waits.clear()
    for k in network_state._stats:
        network_state._stats[k] = 0
    saved = list(network_state._restore_callbacks)
    network_state._restore_callbacks.clear()
    yield tmp_path
    network_state._waits.clear()
    for k in network_state._stats:
        network_state._stats[k] = 0
    network_state._restore_callbacks[:] = saved


def _fast_parks(monkeypatch, hook=None):
    """park常量调小 + _sleep假实现（记录时长+真实毫秒sleep让出事件循环——
    live拒绝竞态测试需要协调协程有机会写标志文件；时长仍不拖慢测试）"""
    monkeypatch.setattr(network_state, "POLL_S", 0.01)
    monkeypatch.setattr(network_state, "RESUME_GRACE_S", 0.02)
    monkeypatch.setattr(network_state, "MAX_WAIT_S", 0.2)
    sleeps: list = []

    async def _fake_sleep(delay):
        sleeps.append(float(delay))
        if hook:
            hook(delay)
        await asyncio.sleep(float(delay))

    monkeypatch.setattr(network_state, "_sleep", _fake_sleep)
    return sleeps


def _stub_probe(monkeypatch, results):
    """probe_online顺序桩：按序返回，用完重复最后一个；记录调用次数"""
    seq = list(results)
    calls = {"n": 0}

    async def _fake_probe():
        idx = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return bool(seq[idx])

    monkeypatch.setattr(network_state, "probe_online", _fake_probe)
    return calls


def _disconnected_err():
    return httpx.ConnectError("All connection attempts failed")


# ════════════════════════════════════════════════════════════════
# 1. 拒绝标志文件（跨进程拒绝通道）单元
# ════════════════════════════════════════════════════════════════


class TestRejectFlag:
    def test_request_reject_writes_flag(self, clean):
        payload = network_state.request_reject(["nw-a", "nw-b"])
        assert payload["wait_ids"] == ["nw-a", "nw-b"]
        path = clean / network_state.REJECT_FLAG_NAME
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["wait_ids"] == ["nw-a", "nw-b"] and "ts" in data

    def test_request_reject_empty_ids_no_file(self, clean):
        payload = network_state.request_reject([])
        assert payload["wait_ids"] == []
        assert not (clean / network_state.REJECT_FLAG_NAME).exists()

    def test_consume_match_removes_and_persists_rest(self, clean):
        network_state.request_reject(["nw-a", "nw-b"])
        assert network_state._consume_reject("nw-a") is True
        data = json.loads(
            (clean / network_state.REJECT_FLAG_NAME).read_text(encoding="utf-8")
        )
        assert data["wait_ids"] == ["nw-b"]

    def test_consume_other_id_noop(self, clean):
        network_state.request_reject(["nw-other"])
        assert network_state._consume_reject("nw-mine") is False
        data = json.loads(
            (clean / network_state.REJECT_FLAG_NAME).read_text(encoding="utf-8")
        )
        assert data["wait_ids"] == ["nw-other"]  # 未命中不消费不误伤

    def test_consume_last_id_deletes_file(self, clean):
        network_state.request_reject(["nw-only"])
        assert network_state._consume_reject("nw-only") is True
        assert not (clean / network_state.REJECT_FLAG_NAME).exists()

    def test_consume_missing_or_corrupt_fail_safe(self, clean):
        assert network_state._consume_reject("nw-x") is False  # 无文件
        (clean / network_state.REJECT_FLAG_NAME).write_text("{corrupt", encoding="utf-8")
        assert network_state._consume_reject("nw-x") is False  # 坏文件不抛不误伤


# ════════════════════════════════════════════════════════════════
# 2. park人工拒绝（kilocode拒绝按钮语义）
# ════════════════════════════════════════════════════════════════


class TestParkHumanReject:
    def test_reject_live_park_aborts_with_human_rejected(self, monkeypatch, clean):
        """真实竞态流：park运行中（前端看到卡片）→request_reject→下一轮询消费即中止"""
        sleeps = _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])  # 永远离线（不拒绝就park到超时）

        async def _run():
            task = asyncio.ensure_future(network_state.maybe_park(_disconnected_err()))
            rejected = False
            for _ in range(400):
                ws = [w for w in network_state.list_waits() if w["state"] == "blocked"]
                if ws:
                    # 快照先证：park进行中跨进程可见（前端卡片数据源）
                    snap = json.loads(
                        (clean / f"state.{os.getpid()}.json").read_text(encoding="utf-8")
                    )
                    assert any(w["id"] == ws[0]["id"] for w in snap["waits"])
                    network_state.request_reject([ws[0]["id"]])
                    rejected = True
                    break
                await asyncio.sleep(0.002)
            assert rejected, "wait记录未出现（park没启动？）"
            return await task

        res = asyncio.run(_run())
        assert res == ABORTED
        w = network_state.list_waits()[0]
        assert w["error"] == "human_rejected"
        st = network_state.stats()
        assert st["human_rejected"] == 1 and st["aborted"] == 1
        # 未等满MAX_WAIT_S（21次poll上限；拒绝在前几轮生效）
        assert len(sleeps) < 21

    def test_reject_during_watch_loop(self, monkeypatch, clean):
        # hook在第一次sleep时写拒绝标志（模拟异步HTTP拒绝请求在park期间到达）
        seen = {}

        def _hook(delay):
            if not seen:
                ws = network_state.list_waits()
                if ws:
                    seen["id"] = ws[0]["id"]
                    network_state.request_reject([seen["id"]])

        _fast_parks(monkeypatch, hook=_hook)
        _stub_probe(monkeypatch, [False])
        res = asyncio.run(network_state.maybe_park(_disconnected_err()))
        assert res == ABORTED
        w = network_state.list_waits()[0]
        assert w["id"] == seen["id"] and w["error"] == "human_rejected"

    def test_reject_during_grace_beats_auto_resume(self, monkeypatch, clean):
        # kilocode宽限期=人工拒绝窗口本体：窗口内拒绝 > 自动续跑
        seen = {}

        def _hook(delay):
            if abs(delay - 0.02) < 1e-9 and not seen:  # RESUME_GRACE_S被调用=进入宽限
                ws = network_state.list_waits()
                if ws:
                    seen["id"] = ws[0]["id"]
                    network_state.request_reject([seen["id"]])

        _fast_parks(monkeypatch, hook=_hook)
        _stub_probe(monkeypatch, [False, True])  # 第二次探测=恢复
        res = asyncio.run(network_state.maybe_park(_disconnected_err()))
        assert res == ABORTED
        w = network_state.list_waits()[0]
        assert w["error"] == "human_rejected"
        st = network_state.stats()
        assert st["resumed"] == 0 and st["human_rejected"] == 1  # 拒绝不让位给自动续跑

    def test_reject_other_wait_does_not_abort(self, monkeypatch, clean):
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False, True])
        network_state.request_reject(["nw-not-this-one"])
        res = asyncio.run(network_state.maybe_park(_disconnected_err()))
        assert res == RESUMED  # 他id拒绝不误伤本park
        assert network_state.stats()["human_rejected"] == 0

    def test_reject_flag_consumed_once_only(self, monkeypatch, clean):
        seen = {}

        def _hook(delay):
            if not seen:
                ws = network_state.list_waits()
                if ws:
                    seen["id"] = ws[0]["id"]
                    network_state.request_reject([seen["id"]])

        _fast_parks(monkeypatch, hook=_hook)
        _stub_probe(monkeypatch, [False])
        assert asyncio.run(network_state.maybe_park(_disconnected_err())) == ABORTED
        # 标志消费即删：第二个park不再被同一标志误伤
        monkeypatch.setattr(network_state, "_sleep", _sleep_no_hook())
        _stub_probe(monkeypatch, [False, True])
        assert asyncio.run(network_state.maybe_park(_disconnected_err())) == RESUMED
        assert network_state.stats()["human_rejected"] == 1

    def test_human_reject_spends_turn_budget(self, monkeypatch, clean):
        # 人工拒绝=本回合park放弃：同turn后续调用fail-fast不叠加等待（turn预算语义不变）
        seen = {}

        def _hook(delay):
            if not seen:
                ws = network_state.list_waits()
                if ws:
                    seen["id"] = ws[0]["id"]
                    network_state.request_reject([seen["id"]])

        _fast_parks(monkeypatch, hook=_hook)
        _stub_probe(monkeypatch, [False])
        network_state.begin_turn("turn-hitl-1")
        assert asyncio.run(network_state.maybe_park(_disconnected_err())) == ABORTED
        counter = _make_counter()
        monkeypatch.setattr(network_state, "_sleep", counter["fn"])
        assert asyncio.run(network_state.maybe_park(_disconnected_err())) == ABORTED
        assert counter["n"] == 0  # fail-fast零睡眠零干等
        assert network_state.stats()["turn_fail_fast"] == 1
        network_state.end_turn()


def _sleep_no_hook():
    async def _fake_sleep(delay):
        await asyncio.sleep(float(delay))

    return _fake_sleep


def _make_counter():
    """睡眠计数桩：counter['n']记调用次数（fail-fast零睡眠断言用）"""
    counter = {"n": 0}

    async def _fake_sleep(delay):
        counter["n"] += 1
        await asyncio.sleep(float(delay))

    counter["fn"] = _fake_sleep
    return counter


# ════════════════════════════════════════════════════════════════
# 3. per-pid状态快照（跨进程可见数据源）
# ════════════════════════════════════════════════════════════════


class TestStateSnapshot:
    def test_persist_writes_pid_snapshot(self, clean, monkeypatch):
        _fast_parks(monkeypatch)
        _stub_probe(monkeypatch, [False])

        def _hook(delay):
            pass  # park进行中（每次轮询）留出快照检查点

        monkeypatch.setattr(
            network_state,
            "_sleep",
            _make_hooked_sleep(_hook),
        )
        asyncio.run(network_state.maybe_park(_disconnected_err()))
        path = clean / f"state.{os.getpid()}.json"
        assert path.exists()
        snap = json.loads(path.read_text(encoding="utf-8"))
        assert snap["pid"] == os.getpid()
        assert snap["stats"]["total_parks"] == 1
        assert snap["waits"] and snap["waits"][0]["state"] in ("blocked", "aborted")

    def test_load_merges_foreign_pids(self, clean, monkeypatch):
        monkeypatch.setattr(network_state, "_pid_alive", lambda pid: True)
        for pid, parks in ((111, 1), (222, 2)):
            (clean / f"state.{pid}.json").write_text(
                json.dumps({
                    "pid": pid, "updated": pid,
                    "stats": {"total_parks": parks, "aborted": 0, "poll_s": 3.0},
                    "waits": [{"id": f"nw-{pid}", "state": "blocked", "created": pid}],
                }),
                encoding="utf-8",
            )
        merged = network_state.load_park_state()
        assert merged["stats"]["total_parks"] == 3  # 计数器跨源求和
        assert merged["stats"]["poll_s"] == 3.0  # 配置标量不求和（setdefault）
        assert [w["id"] for w in merged["waits"]] == ["nw-111", "nw-222"]  # created升序
        assert {s["pid"] for s in merged["sources"]} == {111, 222}

    def test_load_excludes_own_pid(self, clean, monkeypatch):
        monkeypatch.setattr(network_state, "_pid_alive", lambda pid: True)
        (clean / f"state.{os.getpid()}.json").write_text(
            json.dumps({"pid": os.getpid(), "stats": {"total_parks": 5}, "waits": []}),
            encoding="utf-8",
        )
        merged = network_state.load_park_state(exclude_pid=os.getpid())
        assert merged["stats"] == {} and merged["sources"] == []

    def test_load_dead_pid_removed(self, clean, monkeypatch):
        monkeypatch.setattr(network_state, "_pid_alive", lambda pid: False)
        path = clean / "state.333.json"
        path.write_text(json.dumps({"pid": 333, "stats": {}, "waits": []}), encoding="utf-8")
        merged = network_state.load_park_state()
        assert merged["sources"] == []
        assert not path.exists()  # 死进程快照顺手清理

    def test_load_corrupt_file_skipped(self, clean, monkeypatch):
        monkeypatch.setattr(network_state, "_pid_alive", lambda pid: True)
        (clean / "state.444.json").write_text("{broken", encoding="utf-8")
        (clean / "state.555.json").write_text("[]", encoding="utf-8")  # 非dict
        merged = network_state.load_park_state()
        assert merged["waits"] == [] and merged["sources"] == []

    def test_merge_dedupe_counters_and_config(self, clean):
        remote = {
            "stats": {"total_parks": 2, "aborted": 1, "poll_s": 3.0},
            "waits": [
                {"id": "nw-1", "state": "blocked", "created": 1.0},
                {"id": "nw-2", "state": "resumed", "created": 2.0},
            ],
            "sources": [{"pid": 999}],
        }
        local_stats = {
            "total_parks": 1, "aborted": 0, "human_rejected": 0,
            "poll_s": 0.01, "turn_fail_fast": 0,
        }
        local_waits = [
            {"id": "nw-2", "state": "aborted", "created": 2.0},  # 本地优先覆盖
            {"id": "nw-3", "state": "blocked", "created": 3.0},
        ]
        merged = network_state.merge_park_states(local_stats, local_waits, remote)
        assert merged["stats"]["total_parks"] == 3  # 计数器求和
        assert merged["stats"]["poll_s"] == 0.01  # 配置标量取本地
        by_id = {w["id"]: w for w in merged["waits"]}
        assert by_id["nw-2"]["state"] == "aborted"  # 去重本地优先
        assert merged["stats"]["pending"] == 2  # pending按合并waits重算（nw-1+nw-3）
        assert merged["sources"] == [{"pid": 999}]

    def test_merge_pending_counts_blocked_only(self, clean):
        merged = network_state.merge_park_states(
            {"total_parks": 0},
            [{"id": "a", "state": "blocked", "created": 1}, {"id": "b", "state": "resumed", "created": 2}],
            {"stats": {}, "waits": [{"id": "c", "state": "aborted", "created": 3}], "sources": []},
        )
        assert merged["stats"]["pending"] == 1


def _make_hooked_sleep(hook):
    async def _fake_sleep(delay):
        hook(delay)
        await asyncio.sleep(0)

    return _fake_sleep


# ════════════════════════════════════════════════════════════════
# 4. 端点接线（真实FastAPI TestClient）
# ════════════════════════════════════════════════════════════════


@pytest.fixture
def client():
    """真实FastAPI TestClient（app全量路由）。

    跨测试模块遮蔽防御（全量跑实测根因）：test_mcp_*把<mcp-client>插入sys.path
    并经其main import了mcp-client自己的routes包——sys.modules['routes']被污染后
    import app的`from routes.skills import ...`解析到mcp-client/routes（无skills）
    即炸。导入期定点清除+重解析到acp-proxy/routes，用后完整恢复，不给其他测试留状态。
    """
    import importlib
    import sys

    saved_modules = {
        k: v for k, v in sys.modules.items() if k == "routes" or k.startswith("routes.")
    }
    for k in list(saved_modules):
        del sys.modules[k]
    sys.path.insert(0, str(HERE))
    importlib.invalidate_caches()
    try:
        app_module = importlib.import_module("app")
        from fastapi.testclient import TestClient

        yield TestClient(app_module.app)
    finally:
        for k in [k for k in list(sys.modules) if k == "routes" or k.startswith("routes.")]:
            del sys.modules[k]
        sys.modules.update(saved_modules)
        try:
            sys.path.remove(str(HERE))
        except ValueError:
            pass


class TestEndpoints:
    def test_status_merges_remote_snapshot(self, clean, monkeypatch, client):
        monkeypatch.setattr(network_state, "_pid_alive", lambda pid: True)
        (clean / "state.7777.json").write_text(
            json.dumps({
                "pid": 7777, "updated": 1.0,
                "stats": {"total_parks": 4, "human_rejected": 1, "poll_s": 3.0},
                "waits": [{"id": "nw-remote-1", "state": "blocked", "created": 1.0,
                           "message": "Connection refused", "turn_id": "t-9"}],
            }),
            encoding="utf-8",
        )
        r = client.get("/api/network/status")
        assert r.status_code == 200
        body = r.json()
        ids = [w["id"] for w in body["waits"]]
        assert "nw-remote-1" in ids  # 子进程park跨进程可见（本轮核心）
        assert body["stats"]["total_parks"] >= 4
        assert body["stats"]["human_rejected"] >= 1
        assert body["stats"]["poll_s"] == pytest.approx(network_state.POLL_S)  # 配置取本地
        assert body["stats"]["pending"] >= 1
        assert any(s["pid"] == 7777 for s in body["sources"])

    def test_reject_endpoint_writes_flag_for_wait_id(self, clean, client):
        r = client.post("/api/network/reject", json={"wait_id": "nw-live-9"})
        assert r.status_code == 200
        assert r.json()["rejected_ids"] == ["nw-live-9"]
        data = json.loads((clean / network_state.REJECT_FLAG_NAME).read_text(encoding="utf-8"))
        assert data["wait_ids"] == ["nw-live-9"]

    def test_reject_endpoint_no_body_rejects_pending(self, clean, monkeypatch, client):
        monkeypatch.setattr(network_state, "_pid_alive", lambda pid: True)
        (clean / "state.8888.json").write_text(
            json.dumps({
                "pid": 8888, "updated": 1.0, "stats": {},
                "waits": [{"id": "nw-pending", "state": "blocked", "created": 1.0}],
            }),
            encoding="utf-8",
        )
        r = client.post("/api/network/reject")
        assert r.status_code == 200
        assert r.json()["rejected_ids"] == ["nw-pending"]  # 缺省=拒绝当前全部pending

    def test_reject_endpoint_empty_ok(self, clean, client):
        r = client.post("/api/network/reject", json={})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["rejected_ids"] == []
        assert not (clean / network_state.REJECT_FLAG_NAME).exists()


# ════════════════════════════════════════════════════════════════
# 5. 接线钉（防死代码/防回归）
# ════════════════════════════════════════════════════════════════


class TestWiringPins:
    def test_app_endpoint_wiring(self):
        src = _src("app.py")
        assert '@app.post("/api/network/reject")' in src
        assert "load_park_state(exclude_pid=os.getpid())" in src
        assert "merge_park_states(" in src
        assert "request_reject(" in src

    def test_agent_start_restore_callback_wiring(self):
        # MCP重连恢复回调必须注册在park所在进程（agent子进程=真实chat turn）
        src = _src("agent/start.py")
        assert "register_restore_callback(" in src
        assert "reconnect_failed_mcp_servers" in src
        main_body = src.split("async def main()")[1]
        assert "register_restore_callback" in main_body

    def test_maybe_park_consumers_unchanged(self):
        # 三路LLM传输消费点数量不变（语义不变只加拒绝/快照）
        src = _src("agent/llm_engine.py")
        assert src.count("network_state.maybe_park") == 3
        transport = _src("utils/llm_transport.py")
        assert transport.count("network_state.maybe_park") >= 1

    def test_reject_check_both_park_phases(self):
        src = _src("utils/network_state.py")
        # watch循环 + 宽限期（人工拒绝窗口）两个消费点
        assert src.count("_consume_reject(wait.id)") == 2
        assert src.count("_persist_state()") >= 7  # 各状态变化点全持久化

    def test_kilocode_constants_unchanged(self):
        assert network_state.RESUME_GRACE_S == 10.0
        assert network_state.POLL_S == 3.0

    def test_stats_shape_has_human_rejected(self, clean):
        st = network_state.stats()
        assert "human_rejected" in st
        assert set(network_state.COUNTER_KEYS) >= {
            "total_parks", "resumed", "aborted", "human_rejected", "turn_fail_fast",
        }
