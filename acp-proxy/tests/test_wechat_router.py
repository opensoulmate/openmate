"""路线图 4.1c 会话级路由（agent/wechat_router.py）测试。

覆盖面：查询全量/过滤/反查 / 改绑（created与previous_session_id）/ 解绑幂等 /
校验拒绝（空值/空白字符）/ 跨实例持久（重启不丢映射）/
BridgeStore.list_mappings+unbind 持久层 / soulmate_agent 接线护栏 + 真实dispatch烟测。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.wechat_bridge import BridgeStore  # noqa: E402
from agent.wechat_router import WeChatRouter, get_wechat_router  # noqa: E402


@pytest.fixture()
def router(tmp_path):
    store = BridgeStore(db_path=str(tmp_path / "wechat.db"))
    return WeChatRouter(store=store)


# ── 查询 ─────────────────────────────────────────────────────

def test_list_empty(router):
    assert router.list_routes() == []


def test_bind_then_list(router):
    r = router.bind("peer-a", "sess-1", now_ms=1_700_000_000_000)
    assert r == {"peer": "peer-a", "session_id": "sess-1",
                 "previous_session_id": None, "created": True}
    routes = router.list_routes()
    assert len(routes) == 1
    assert routes[0]["peer"] == "peer-a" and routes[0]["session_id"] == "sess-1"
    assert routes[0]["last_seen_ms"] == 1_700_000_000_000


def test_list_sorted_by_last_seen_desc(router):
    router.bind("old", "s1", now_ms=1_000)
    router.bind("new", "s2", now_ms=9_000)
    routes = router.list_routes()
    assert [m["peer"] for m in routes] == ["new", "old"]


def test_list_filter_by_peer(router):
    router.bind("peer-a", "s1")
    router.bind("peer-b", "s2")
    routes = router.list_routes(peer="peer-a")
    assert len(routes) == 1 and routes[0]["peer"] == "peer-a"


def test_list_filter_by_session_reverse_lookup(router):
    """session_id 过滤=反查：同一会话可以绑多个微信peer。"""
    router.bind("peer-a", "s1")
    router.bind("peer-b", "s1")
    router.bind("peer-c", "s2")
    routes = router.list_routes(session_id="s1")
    assert sorted(m["peer"] for m in routes) == ["peer-a", "peer-b"]


def test_list_filter_no_match(router):
    router.bind("peer-a", "s1")
    assert router.list_routes(peer="nope") == []
    assert router.list_routes(session_id="nope") == []


# ── 改绑 ─────────────────────────────────────────────────────

def test_rebind_returns_previous(router):
    router.bind("peer-a", "s1")
    r = router.bind("peer-a", "s2")
    assert r["created"] is False and r["previous_session_id"] == "s1"
    assert r["session_id"] == "s2"
    routes = router.list_routes(peer="peer-a")
    assert len(routes) == 1 and routes[0]["session_id"] == "s2"


# ── 解绑 ─────────────────────────────────────────────────────

def test_unbind_removes_mapping(router):
    router.bind("peer-a", "s1")
    r = router.unbind("peer-a")
    assert r == {"peer": "peer-a", "session_id": "s1", "removed": True}
    assert router.list_routes() == []


def test_unbind_missing_is_idempotent(router):
    r = router.unbind("ghost")
    assert r["removed"] is False and r["session_id"] is None


# ── 校验 ─────────────────────────────────────────────────────

@pytest.mark.parametrize("peer,sid", [
    ("", "s1"), ("   ", "s1"), (None, "s1"),          # peer 空
    ("peer-a", ""), ("peer-a", "  "), ("peer-a", None),  # session 空
    ("pe er", "s1"), ("peer\na", "s1"), ("peer-a", "s 1"),  # 含空白
])
def test_bind_validation_rejects(router, peer, sid):
    with pytest.raises(ValueError):
        router.bind(peer, sid)
    assert router.list_routes() == []


def test_unbind_validation_rejects_empty(router):
    with pytest.raises(ValueError):
        router.unbind("")


# ── 持久层（BridgeStore 新增方法）+ 跨实例持久 ─────────────────

def test_store_list_mappings_and_unbind(tmp_path):
    db_path = str(tmp_path / "wechat.db")
    store = BridgeStore(db_path=db_path)
    store.remember_session("peer-a", "s1", 1_000)
    store.remember_session("peer-b", "s2", 2_000)
    ms = store.list_mappings()
    assert [m["peer"] for m in ms] == ["peer-b", "peer-a"]
    assert store.unbind("peer-a") == "s1"
    assert store.unbind("peer-a") is None
    assert [m["peer"] for m in store.list_mappings()] == ["peer-b"]


def test_mappings_survive_router_recreation(tmp_path):
    """映射持久化：新BridgeStore/Router实例（=进程重启）后仍可见、可解绑。"""
    db_path = str(tmp_path / "wechat.db")
    WeChatRouter(store=BridgeStore(db_path=db_path)).bind("peer-a", "s1")
    router2 = WeChatRouter(store=BridgeStore(db_path=db_path))
    routes = router2.list_routes()
    assert len(routes) == 1 and routes[0]["session_id"] == "s1"
    assert router2.unbind("peer-a")["removed"] is True
    # 第三个实例确认删除也持久
    assert WeChatRouter(store=BridgeStore(db_path=db_path)).list_routes() == []


def test_get_wechat_router_singleton(monkeypatch, tmp_path):
    import agent.wechat_router as wr
    monkeypatch.setattr(wr, "_router", None)
    monkeypatch.setenv("SOULMATE_WECHAT_DB_PATH", str(tmp_path / "w.db"))
    a = get_wechat_router()
    b = get_wechat_router()
    assert a is b


# ── soulmate_agent 接线护栏 + 真实dispatch烟测 ──────────────────

def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 三处dispatch点：主循环 + code-mode内层（tuple声明至少2处）
    assert src.count('func_name in ("wechat_route_list", "wechat_route_bind",'
                     ' "wechat_route_unbind")') >= 2
    # 工具定义
    assert '"name": "wechat_route_list"' in src
    assert '"name": "wechat_route_bind"' in src
    assert '"name": "wechat_route_unbind"' in src
    # 系统提示工具清单
    assert "wechat_route_list / wechat_route_bind / wechat_route_unbind" in src
    # 分发helper + lazy-init
    assert "def _wechat_route_dispatch" in src
    assert "_get_wechat_router" in src


def test_wechat_route_dispatch_smoke(monkeypatch, tmp_path):
    """真实dispatch烟测（__new__构造+lazy-init）：bind→list→rebind→unbind→异常路径。"""
    import agent.soulmate_agent as sa

    router = WeChatRouter(store=BridgeStore(db_path=str(tmp_path / "w.db")))
    monkeypatch.setattr(sa, "_get_wechat_router", lambda: router)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__（lazy-init路径）

    out = agent._wechat_route_dispatch(
        "wechat_route_bind", {"peer": "wxid_a", "session_id": "om-1"})
    assert "✅ 已绑定" in out and "wxid_a" in out and "om-1" in out

    out = agent._wechat_route_dispatch("wechat_route_list", {})
    assert "🔀 微信路由映射共1条" in out and "wxid_a" in out

    out = agent._wechat_route_dispatch(
        "wechat_route_bind", {"peer": "wxid_a", "session_id": "om-2"})
    assert "✅ 已改绑" in out and "om-1" in out and "om-2" in out

    out = agent._wechat_route_dispatch("wechat_route_list", {"session_id": "om-2"})
    assert "共1条" in out
    out = agent._wechat_route_dispatch("wechat_route_list", {"session_id": "om-x"})
    assert "没有匹配" in out

    out = agent._wechat_route_dispatch("wechat_route_unbind", {"peer": "wxid_a"})
    assert "✅ 已解绑" in out and "om-2" in out
    out = agent._wechat_route_dispatch("wechat_route_unbind", {"peer": "wxid_a"})
    assert "❌" in out and "没有已绑定" in out

    # 校验/缺参路径：错误文本而非异常
    out = agent._wechat_route_dispatch("wechat_route_bind", {"peer": "", "session_id": "s"})
    assert out.startswith("错误:")
    out = agent._wechat_route_dispatch("wechat_route_unbind", {})
    assert out.startswith("错误:")

    # lazy-init真的挂到了实例上（二次调用不重复建）
    assert getattr(agent, "_wechat_router", None) is router
