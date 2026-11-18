"""
协作工单控制面测试 — 工单语义（A1-A5）/ 组织角色路由（D1/D2）/ 预算闸（C1-C3）

对照 docs/reports/paperclip-control-plane-study.md 映射表逐项实证：
- A1 原子领取（条件转移抢锁，锁不住不发）+ 抢占（租约过期可夺）
- A2 (run_id, node_id, kind) 开态唯一（≙ 部分唯一索引 WHERE status IN open）
- A3 前任未清场拒领（predecessor_still_executing）+ 回流 CAS 归属校验
- A4 输入合并防重复（重复建单并入在途/重复派发不双发/交付幂等）
- A5 领取决策漏斗留痕（claimed/rejected 均进工单 history）
- C1 三档预算策略（company/agent/project × day/month/lifetime × warn%/hard_stop）
- C2 派发前拦截（blocked 事实）+ C3 审批放行 waiver（一次性豁免，记账照常）
- D1 org 表 + reports_to 递归汇报链 backup；D2 角色路由=数据（YAML 只写 role）
"""
from __future__ import annotations

import asyncio

import pytest

from plugin.flow_collab import (
    BudgetLedger,
    BudgetPolicy,
    CollabError,
    CollabRuntime,
    RoleRouter,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_QUEUED,
    STATUS_WORKING,
    WorkOrderStore,
)
from plugin.flow_def import BudgetSpec


@pytest.fixture()
def store(tmp_path) -> WorkOrderStore:
    return WorkOrderStore(tmp_path / "collab")


@pytest.fixture()
def no_cost_db(monkeypatch, tmp_path):
    """预算落账进临时库，绝不写真实 ~/.hermes 成本台账。"""
    monkeypatch.setenv("COLLAB_COST_DB", str(tmp_path / "costs.db"))


def _mk(store: WorkOrderStore, task_id: str, **kw):
    return store.create(task_id=task_id, kind=kw.pop("kind", "human"),
                        run_id=kw.pop("run_id", "run1"), node_id=kw.pop("node_id", "n1"),
                        assignee=kw.pop("assignee", "alice"), now=kw.pop("now", 1000.0),
                        **kw)[0]


# ── A1 原子领取 + 抢占锁 ──────────────────────────────────────────

def test_claim_is_atomic_conditional_transition(store):
    """A1：todo→working 条件转移；第二人抢活锁单被拒（predecessor_still_executing）"""
    task = _mk(store, "TO-1-aa")
    _, token = store.claim("TO-1-aa", claimed_by="bob", now=1000.0)
    task2 = store.load("TO-1-aa")
    assert task2.status == STATUS_WORKING and task2.claimed_by == "bob"
    assert token == task2.claim_token
    with pytest.raises(CollabError, match="predecessor_still_executing"):
        store.claim("TO-1-aa", claimed_by="carol", now=1001.0)


def test_claim_after_lease_expiry_steals(store):
    """抢占（VibeKanban 语义）：租约过期后可夺锁，前任令牌即刻作废"""
    _mk(store, "TO-2-bb")
    _, old_token = store.claim("TO-2-bb", claimed_by="bob", lease_seconds=60, now=1000.0)
    with pytest.raises(CollabError):
        store.claim("TO-2-bb", claimed_by="carol", now=1050.0)   # 租约未过期
    task, new_token = store.claim("TO-2-bb", claimed_by="carol", lease_seconds=60, now=1100.0)
    assert task.claimed_by == "carol" and new_token != old_token
    # 前任心跳/交付一律被拒（令牌已换）
    with pytest.raises(CollabError, match="claim_token 无效"):
        store.heartbeat("TO-2-bb", old_token, now=1101.0)
    with pytest.raises(CollabError, match="claim_token 无效"):
        store.deliver("TO-2-bb", "旧结果", claim_token=old_token, now=1101.0)


def test_heartbeat_renews_lease(store):
    _mk(store, "TO-3-cc")
    _, token = store.claim("TO-3-cc", claimed_by="bob", lease_seconds=60, now=1000.0)
    store.heartbeat("TO-3-cc", token, now=1050.0)
    task = store.load("TO-3-cc")
    assert task.lease_expires_at == pytest.approx(1110.0)
    with pytest.raises(CollabError, match="租约已过期"):
        store.heartbeat("TO-3-cc", token, now=2000.0)


# ── A2 开态唯一 + A4 合并防重 ────────────────────────────────────

def test_open_task_unique_per_run_node_kind(store):
    """A2：(run_id, node_id, kind) 开态唯一 ≙ 部分唯一索引；重复建单并入（A4）"""
    t1, created1 = store.create(task_id="TO-4-dd", kind="agent", run_id="r", node_id="n",
                                assignee="alice", now=1000.0)
    t2, created2 = store.create(task_id="TO-5-ee", kind="agent", run_id="r", node_id="n",
                                assignee="alice", now=1001.0)
    assert created1 and not created2
    assert t2.task_id == t1.task_id                       # 并入在途，不新开
    assert any(h["event"] == "create-coalesced" for h in t2.history)
    # 不同 kind 各自可开（预算闸 human 单与 agent 单并存）
    t3, created3 = store.create(task_id="TO-6-ff", kind="human", run_id="r", node_id="n",
                                assignee="bob", now=1002.0)
    assert created3 and t3.task_id != t1.task_id


def test_deliver_is_idempotent_receipt(store):
    """A4 投递回执 CAS：交付幂等，重交回同一结果绝不覆盖"""
    _mk(store, "TO-7-gg")
    store.deliver("TO-7-gg", {"v": 1}, delivered_by="alice")
    task, dup = store.deliver("TO-7-gg", {"v": 2}, delivered_by="mallory")
    assert dup and task.result == {"v": 1} and task.delivered_by == "alice"
    assert task.status == STATUS_DELIVERED


# ── A3 前任未清场拒领 + A5 决策留痕 ──────────────────────────────

def test_predecessor_not_cleared_rejects_new_claim(store):
    """A3：同 run+node+kind 前任持锁未清场 → 新单拒领（重派须等清场）"""
    _mk(store, "TO-8-hh", kind="agent")
    store.claim("TO-8-hh", claimed_by="soulmate:a", lease_seconds=600, now=1000.0)
    # 造一个"前任未清场"的重复单（绕过 create 查重直接落盘，模拟并发竞争漏网）
    from plugin.flow_collab import WorkOrder
    leak = WorkOrder(task_id="TO-9-ii", kind="agent", run_id="run1", node_id="n1",
                     assignee="alice", created_at=1000.0)
    store.save(leak)
    with pytest.raises(CollabError, match="predecessor_still_executing"):
        store.claim("TO-9-ii", claimed_by="soulmate:b", now=1001.0)
    # A5：拒领决策留痕（漏斗可查"为什么没派出去"）
    reloaded = store.load("TO-9-ii")
    assert any(h["event"] == "claim-decision" and "rejected" in h["detail"]
               for h in reloaded.history)
    # A5：领取成功同样留痕
    ok = store.load("TO-8-hh")
    assert any(h["event"] == "claim-decision" and "claimed" in h["detail"]
               for h in ok.history)


def test_closed_task_rejects_claim(store):
    _mk(store, "TO-10-jj")
    store.deliver("TO-10-jj", "done", delivered_by="alice")
    with pytest.raises(CollabError, match="closed"):
        store.claim("TO-10-jj", claimed_by="bob")


# ── D1/D2 组织角色路由 ──────────────────────────────────────────

def test_role_routing_org_chart_least_loaded(tmp_path, monkeypatch):
    """D2：YAML 只写 role；org 表成员按最少在办确定性分发"""
    from rbac.org import OrgChart, OrgMember
    chart = OrgChart(tmp_path / "org.json")
    chart.upsert(OrgMember(user_id="alice", role="engineer", team="eng", reports_to="cto"))
    chart.upsert(OrgMember(user_id="bob", role="engineer", team="eng", reports_to="cto"))
    chart.upsert(OrgMember(user_id="cto", role="cto", team="eng"))
    store = WorkOrderStore(tmp_path / "collab")
    router = RoleRouter(store=store, org=chart,
                        rbac=_FakeRbac())  # org 表命中时不查 RBAC
    # bob 已有 2 张在办 → 派 alice（最少在办）
    for i in range(2):
        store.create(task_id=f"TO-b{i}-x", kind="human", run_id=f"r{i}", node_id="n",
                     assignee="bob", now=1000.0 + i)
    assignee, role = router.resolve(role="engineer")
    assert (assignee, role) == ("alice", "engineer")


def test_role_routing_backup_via_reporting_chain(tmp_path):
    """D1：角色成员全满载 → 沿 reports_to 递归汇报链找 backup（主管兜底）"""
    from rbac.org import OrgChart, OrgMember
    chart = OrgChart(tmp_path / "org.json")
    chart.upsert(OrgMember(user_id="alice", role="engineer", reports_to="lead"))
    chart.upsert(OrgMember(user_id="lead", role="lead", reports_to="cto"))
    chart.upsert(OrgMember(user_id="cto", role="cto"))
    assert chart.reporting_chain("alice") == ["lead", "cto"]   # 递归链
    store = WorkOrderStore(tmp_path / "collab")
    router = RoleRouter(store=store, org=chart, rbac=_FakeRbac())
    router.member_max_open = 1
    for i in range(1):  # alice 满载（1/1）
        store.create(task_id=f"TO-c{i}-x", kind="human", run_id=f"r{i}", node_id="n",
                     assignee="alice", now=1000.0 + i)
    assignee, _ = router.resolve(role="engineer")
    assert assignee == "lead"     # 汇报链第一站 backup


def test_role_routing_rbac_fallback(tmp_path):
    """D2 兜底：org 表无此角色 → RBAC 角色表查人"""
    from rbac.org import OrgChart
    from rbac.models import Role
    from rbac.store import RBACStore
    RBACStore.reset_instance()
    rbac = RBACStore()
    rbac.add_user("dave", "Dave")
    rbac.add_role(Role(id="manager", name="经理"))
    rbac.assign_role("dave", "manager")
    store = WorkOrderStore(tmp_path / "collab")
    router = RoleRouter(store=store, org=OrgChart(tmp_path / "org.json"), rbac=rbac)
    assert router.resolve(role="manager") == ("dave", "manager")
    with pytest.raises(CollabError, match="无可用成员"):
        router.resolve(role="ghost")


class _FakeRbac:
    def list_users(self):
        return []


# ── C1-C3 预算闸（三档 scope / 派发前拦截 / 放行续跑）─────────────

def test_budget_three_scope_policies_and_states(no_cost_db):
    """C1：company/agent/project 三档 × 窗口 × warn%/hard_stop，状态 ok/warning/hard_stop"""
    ledger = BudgetLedger(daily_budget_usd=100.0, daily_budget_tokens=10 ** 9)
    ledger.add_policy(BudgetPolicy("company", "*", "month",
                                   max_cost_usd=5.0, warn_percent=25))
    ledger.add_policy(BudgetPolicy("project", "flow-a", "lifetime",
                                   max_cost_usd=2.0, warn_percent=80))
    ledger.charge(assignee="alice", flow_id="flow-a", cost_usd=1.3, tokens=10)
    states = {p.id: p.state for p in ledger.policies()}
    assert states["company:*:month"] == "warning"        # 1.3/5=26% 过 25% warn 线
    assert states["project:flow-a:lifetime"] == "ok"     # 1.3/2=65% 未过 80%
    ledger.charge(assignee="alice", flow_id="flow-a", cost_usd=1.0)
    states = {p.id: p.state for p in ledger.policies()}
    assert states["project:flow-a:lifetime"] == "hard_stop"   # 2.3 ≥ 2.0 硬停
    assert states["company:*:month"] == "warning"             # 2.3/5=46% 仍预警未硬停
    # 隔离：bob 的账不受 alice 用量影响
    assert ledger.usage_for("agent", "bob", "day")["cost_usd"] == 0.0


def test_budget_hard_stop_blocks_only_that_scope(no_cost_db):
    """C2 派发前拦截 + 隔离：agent 档硬停只挡该 assignee，其他人照常"""
    ledger = BudgetLedger(daily_budget_usd=1.0, daily_budget_tokens=10 ** 9)
    ledger.charge(assignee="alice", flow_id="f", cost_usd=2.0)   # 超 alice 日预算
    reasons = ledger.blocked(assignee="alice", flow_id="f")
    assert reasons and "agent:alice:day" in reasons[0]
    assert ledger.blocked(assignee="bob", flow_id="f") == []     # 隔离：bob 不受影响
    assert ledger.exhausted("alice") and not ledger.exhausted("bob")


def test_budget_waiver_grants_one_shot_pass(no_cost_db):
    """C3：人工放行一次性豁免（消耗即失效），记账照常不重置策略"""
    ledger = BudgetLedger(daily_budget_usd=1.0, daily_budget_tokens=10 ** 9)
    ledger.charge(assignee="alice", cost_usd=2.0)
    gate = "run-x:node-y"
    assert ledger.blocked(assignee="alice") and not ledger.take_waiver(gate)
    ledger.grant_waiver(gate)
    assert ledger.take_waiver(gate) is True      # 放行消耗
    assert ledger.take_waiver(gate) is False     # 一次性，不循环放行
    assert ledger.blocked(assignee="alice")      # 策略照旧硬停（真闸）


def test_node_budget_over_is_soft(no_cost_db):
    """节点级预算：超出只标记 over_budget（软预算禁硬杀，C5）"""
    ledger = BudgetLedger()
    verdict = ledger.charge(assignee="alice", tokens=200, cost_usd=0.5,
                            budget=BudgetSpec(max_tokens=100, max_cost_usd=0.1))
    assert verdict["over_budget"] and len(verdict["over_reasons"]) == 2


# ── B1 唤醒三态（纯函数 policy，对位 wake-queue/domain/policy）────────

def test_wake_decision_three_states():
    """B1：proceed/coalesce/defer 三态 + closed/superseded 静默取消（纯 facts）"""
    from plugin.flow_collab import wake_decision
    assert wake_decision({})["action"] == "proceed"                     # 锁空闲
    assert wake_decision({"in_flight": True, "same_task": True})["action"] == "coalesce"
    assert wake_decision({"in_flight": True, "same_task": False})["action"] == "defer"
    assert wake_decision({"closed": True})["action"] == "cancel"       # 已关不行动
    assert wake_decision({"superseded": True})["action"] == "cancel"
    # 已关/被取代优先于在途（绝不并入已关任务）
    assert wake_decision({"closed": True, "in_flight": True,
                          "same_task": True})["action"] == "cancel"
    # 决策带 reason（可观测）
    assert wake_decision({"in_flight": True, "same_task": False})["reason"] == "lock-held-queued"


# ── 运行时派发：A1 领取后才出网 + C2 兜底 ────────────────────────

class _FakeChannel:
    def __init__(self):
        self.calls = []

    async def dispatch(self, **kw):
        self.calls.append(kw)
        return {"a2a_task_id": "a2a-1", "status": "pending"}

    async def fetch(self, a2a_task_id):
        return {"state": "working", "result": ""}


def test_dispatch_claims_before_sending(tmp_path, no_cost_db, monkeypatch):
    """A1：锁不住不发 SoulMate——派发即领取（claimed_by=soulmate），出网前已持锁"""
    monkeypatch.setenv("COLLAB_TASKS_DIR", str(tmp_path / "collab"))
    from plugin.flow_collab import reset_collab
    reset_collab()
    store = WorkOrderStore(tmp_path / "collab")
    channel = _FakeChannel()
    runtime = CollabRuntime(store=store, channel=channel,
                            ledger=BudgetLedger(daily_budget_usd=100.0))
    task = runtime.create(kind="agent", run_id="r", flow_id="f", node_id="n",
                          assignee="alice", target_agent="soulmate",
                          instruction="干活")
    out = asyncio.run(runtime.dispatch(task))
    assert len(channel.calls) == 1                       # 真出网一次
    assert out.claimed_by == "soulmate:soulmate"         # 已被 SoulMate 领取持锁
    assert out.handed_off_at > 0                         # C4 交接标记先落
    # A4：重复派发并入在途，不双发
    out2 = asyncio.run(runtime.dispatch(runtime.store.load(task.task_id)))
    assert len(channel.calls) == 1
    # B1 唤醒三态：重复派发 → coalesce 并入在途（纯函数决策留痕）
    wake_notes = [h for h in out2.history if h["event"] == "wake-decision"]
    assert wake_notes and "coalesce" in wake_notes[-1]["detail"]


def test_dispatch_budget_blocked_queues_without_sending(tmp_path, no_cost_db):
    """C2 runtime 兜底：预算硬停 → queued 不出网（引擎侧另有预算闸转人工）"""
    store = WorkOrderStore(tmp_path / "collab")
    channel = _FakeChannel()
    ledger = BudgetLedger(daily_budget_usd=1.0)
    ledger.charge(assignee="alice", cost_usd=5.0)
    runtime = CollabRuntime(store=store, channel=channel, ledger=ledger)
    task = runtime.create(kind="agent", run_id="r", flow_id="f", node_id="n",
                          assignee="alice", instruction="干活")
    out = asyncio.run(runtime.dispatch(task))
    assert out.status == STATUS_QUEUED and channel.calls == []
    assert any(h["event"] == "budget-blocked" for h in out.history)


def test_wake_decision_drives_dispatch_and_create(tmp_path, no_cost_db):
    """B1 集成：决策真走 create/dispatch 唤醒点（coalesce 并入 / cancel 幂等）"""
    store = WorkOrderStore(tmp_path / "collab")
    channel = _FakeChannel()
    runtime = CollabRuntime(store=store, channel=channel,
                            ledger=BudgetLedger(daily_budget_usd=100.0))
    task = runtime.create(kind="agent", run_id="r", flow_id="f", node_id="n",
                          assignee="alice", instruction="活")
    # create 唤醒点：重复建单 → coalesce（wake-decision 留痕并入在途）
    again = runtime.create(kind="agent", run_id="r", flow_id="f", node_id="n",
                           assignee="alice", instruction="活")
    assert again.task_id == task.task_id
    assert any(h["event"] == "create-coalesced" and "coalesce" in h["detail"]
               for h in again.history)
    # dispatch 唤醒点：已交付 → cancel 幂等（绝不重派）
    asyncio.run(runtime.dispatch(task))
    runtime.store.deliver(task.task_id, "done", delivered_by="soulmate")
    out = asyncio.run(runtime.dispatch(runtime.store.load(task.task_id)))
    assert any(h["event"] == "wake-decision" and "cancel" in h["detail"]
               for h in out.history)
    assert len(channel.calls) == 1


# ── B2 派发序（Person>Work>Recovery + 防饿死份额）──────────────────

def test_dispatch_order_tiers_and_starvation_shares():
    """B2：三级派发序 + 每第4名额 aging 防饿死 + recovery 保留名额"""
    from plugin.flow_collab import dispatch_order
    now = 1_000_000.0
    # 防饿死：4 个新人单 + 1 个 2h 前的 work 单——aging 名额让老单第 4 位先做
    persons = [{"task_id": f"p{i}", "origin": "person", "created_at": now - i}
               for i in (4, 3, 2, 1)]
    w_old = {"task_id": "w-old", "origin": "work", "created_at": now - 7200}
    order = dispatch_order(persons + [w_old], now)
    ids = [t["task_id"] for t in order]
    assert ids[3] == "w-old"                    # 第 4 名额给最久等待者（防饿死）
    assert ids[4] == "p1"                       # 最新人单让位（不再饿死旁人）
    assert ids[:3] == ["p4", "p3", "p2"]        # 层内先到先做
    # recovery 保留名额（第 2 位）：恢复不被新活挤死
    r_old = {"task_id": "r1", "origin": "recovery", "created_at": now - 7200}
    order2 = dispatch_order(
        [{"task_id": "p1", "origin": "person", "created_at": now - 1},
         {"task_id": "w1", "origin": "work", "created_at": now - 2},
         r_old], now)
    assert [t["task_id"] for t in order2] == ["p1", "r1", "w1"]
    # 无 recovery 时纯层级序：person > work
    order3 = dispatch_order(
        [{"task_id": "w", "origin": "work", "created_at": now - 8},
         {"task_id": "p", "origin": "person", "created_at": now - 7},
         {"task_id": "w2", "origin": "work", "created_at": now - 9}], now)
    assert [t["task_id"] for t in order3] == ["p", "w2", "w"]   # 层内先到先做


def test_claim_next_follows_dispatch_order_and_funnel(tmp_path, no_cost_db):
    """B2 集成：claim_next 按派发序领取；被占候选顺延（A5 决策漏斗）"""
    store = WorkOrderStore(tmp_path / "collab")
    now = 1_000_000.0
    for i in (3, 2, 1):
        store.create(task_id=f"TO-p{i}-x", kind="human", run_id=f"r{i}",
                     node_id="n", assignee="alice", now=now - i)
    # 最老 person 单先领
    got = store.claim_next("alice", claimed_by="alice", now=now)
    assert got is not None and got[0].task_id == "TO-p3-x"
    # 已被他人持锁的候选顺延到下一个（漏斗留痕）
    got2 = store.claim_next("alice", claimed_by="bob", now=now)
    assert got2 is not None and got2[0].task_id == "TO-p2-x"
    assert any(h["event"] == "claim-decision" and "skipped" in h["detail"]
               for h in store.load("TO-p3-x").history)
    store.claim_next("alice", claimed_by="carol", now=now)
    assert store.claim_next("alice", claimed_by="dave", now=now) is None  # 领完


# ── B3 三拍判死（Pulse 既有恢复账复用）────────────────────────────

def test_three_beat_death_requeues_then_fails_bounded(tmp_path, no_cost_db):
    """B3：三拍无心跳判死→runtime_offline 回队（origin=recovery）；有界 3 次→failed"""
    clock = {"now": 1_000_000.0}
    runtime = CollabRuntime(store=WorkOrderStore(tmp_path / "collab"),
                            channel=_FakeChannel(),
                            ledger=BudgetLedger(daily_budget_usd=100.0),
                            clock=lambda: clock["now"])
    task = runtime.create(kind="agent", run_id="r", flow_id="f", node_id="n",
                          assignee="alice", instruction="活")
    requeues_seen = []
    for round_ in range(4):
        runtime.store.claim(task.task_id, claimed_by="soulmate:a",
                            lease_seconds=9999, now=clock["now"])
        clock["now"] += 3 * runtime.beat_interval_s + 1    # 三拍无心跳
        out = runtime.liveness_sweep(clock["now"])
        reloaded = runtime.store.load(task.task_id)
        if round_ < 3:
            assert out["requeued"] == 1 and out["failed"] == 0
            assert reloaded.status == "todo" and reloaded.origin == "recovery"
            assert reloaded.requeues == round_ + 1 and reloaded.claimed_by == ""
            assert any(h["event"] == "runtime_offline" for h in reloaded.history)
            requeues_seen.append(reloaded.requeues)
        else:
            # 有界：3 次回队仍失联 → failed 显式收口（绝不静默）
            assert out["failed"] == 1
            assert reloaded.status == "failed"
    assert requeues_seen == [1, 2, 3]


def test_heartbeat_prevents_death_and_pulse_requeue_verdict(tmp_path, no_cost_db):
    """B3：心跳即回 online；Pulse 超时恢复钩子判死→requeue（复用既有恢复账）"""
    clock = {"now": 1_000_000.0}
    runtime = CollabRuntime(store=WorkOrderStore(tmp_path / "collab"),
                            channel=_FakeChannel(),
                            ledger=BudgetLedger(daily_budget_usd=100.0),
                            clock=lambda: clock["now"])
    task = runtime.create(kind="human", run_id="r", flow_id="f", node_id="n",
                          assignee="alice", instruction="活")
    _, token = runtime.store.claim(task.task_id, claimed_by="alice",
                                   now=clock["now"])
    clock["now"] += 2 * runtime.beat_interval_s
    runtime.store.heartbeat(task.task_id, token, now=clock["now"])   # 心跳续租
    assert runtime.liveness_sweep(clock["now"]) == {"requeued": 0, "failed": 0}
    # 心跳断了：Pulse 超时恢复钩子判死 → requeue 判决（Pulse 账记恢复）
    clock["now"] += 3 * runtime.beat_interval_s + 1
    verdict = runtime._on_pulse_overdue({"task_id": f"collab:{task.task_id}",
                                         "recoveries": 0})
    assert verdict["action"] == "requeue"
    assert runtime.store.load(task.task_id).origin == "recovery"
    # 活着的领取：催办 defer（不误判死）
    task2 = runtime.create(kind="human", run_id="r2", flow_id="f", node_id="n2",
                           assignee="bob", instruction="活")
    runtime.store.claim(task2.task_id, claimed_by="bob", now=clock["now"])
    verdict2 = runtime._on_pulse_overdue({"task_id": f"collab:{task2.task_id}",
                                          "recoveries": 0})
    assert verdict2["action"] == "defer"


# ── E5 上下文卫生（history 有界 + 交付归档 + 接 Pi#5 压缩）──────────

def test_history_bounded_and_context_hygiene_on_deliver(tmp_path, no_cost_db):
    """E5：history 有界防累积；交付即归档任务包上下文并经 compress-handoff 接 Pi#5"""
    store = WorkOrderStore(tmp_path / "collab")
    task = store.create(task_id="TO-hy-x", kind="agent", run_id="r",
                        node_id="n", assignee="alice",
                        instruction="长任务包" * 200,
                        payload={"upstream_snapshot": {"a": 1}}, now=1000.0)[0]
    # history 有界（HISTORY_MAX），溢出计数留痕
    for i in range(60):
        task.note("tick", str(i))
    assert len(task.history) == 50 and task.history_trimmed == 10
    store.save(task)

    class FakeEngine:
        def __init__(self):
            self.events = []

        def emit(self, event, payload=None):
            self.events.append((event, payload))
            return []

        async def resolve_delivery(self, *a, **k):
            return None

    runtime = CollabRuntime(store=store, channel=_FakeChannel(),
                            ledger=BudgetLedger(), clock=lambda: 1000.0)
    fake = FakeEngine()
    runtime._engine = fake
    asyncio.run(runtime.deliver_and_resume("TO-hy-x", "交付", delivered_by="soulmate"))
    done = store.load("TO-hy-x")
    assert done.archived is True
    assert len(done.instruction) <= 310 and "已归档" in done.instruction
    assert "upstream_snapshot" not in done.payload            # 上下文释放
    assert any(h["event"] == "context-archived" for h in done.history)
    # Pi#5 阶段压缩接线：compress-handoff 事件带压缩指令（驱动层 ContextCompressor）
    assert fake.events and fake.events[0][0] == "compress-handoff"
    assert "归档" in fake.events[0][1]["instructions"]
