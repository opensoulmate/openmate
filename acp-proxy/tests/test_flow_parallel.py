# -*- coding: utf-8 -*-
"""Pi #14 流程引擎 parallel 节点 + Pi #5 compress-handoff 动作——引擎级验证。

parallel 节点 = 子代理治理运行时基线：并发乘数×2 超出排队、统一轮数上限、
runChildTask 沿用（分支经 governor.run_child 同源执行）。
compress-handoff = 阶段切换节点声明压缩要求（自定义压缩指令经既有事件总线广播）。
"""
from __future__ import annotations

import asyncio

import pytest

import plugin.flow_engine as flow_engine_mod
from plugin.flow_def import FlowDefError, parse_flow
from plugin.flow_engine import FlowEngine


def run(coro):
    return asyncio.run(coro)


def _flow(nodes):
    return parse_flow({
        "id": "par-test",
        "name": "并行测试",
        "triggers": [{"type": "manual"}],
        "nodes": nodes,
    })


@pytest.fixture()
def engine(tmp_path):
    calls: list[str] = []

    async def caller(name, args, approved=False):
        calls.append(name)
        await asyncio.sleep(0.02)
        return {"ok": name}

    eng = FlowEngine(tmp_path / "runs", caller)
    eng.calls = calls
    return eng


class TestParallelNode:
    def test_branches_run_with_governance(self, engine):
        """全部分支完成；治理面观测可见（capacity=base×2、排队计数）。"""
        defn = _flow([{
            "id": "fan", "type": "parallel", "max_concurrency": 1,
            "branches": [
                {"id": "a", "tool": "p__ta", "args": {"i": 1}},
                {"id": "b", "tool": "p__tb", "args": {"i": 2}},
                {"id": "c", "tool": "p__tc", "args": {"i": 3}},
            ],
        }])
        engine.register_flow(defn, "p")
        r = run(engine.run_now("par-test", {"type": "manual"}))
        assert r.status == "completed", r.error
        out = r.outputs["fan"]
        assert sorted(out["branches"]) == ["a", "b", "c"]
        assert out["failed_branches"] == []
        assert out["governor"]["capacity"] == 2          # base 1 × 2
        assert out["governor"]["queued_total"] == 1      # 3 分支容量 2 → 1 真排队
        assert sorted(engine.calls) == ["p__ta", "p__tb", "p__tc"]
        # 分支合成节点各自留痕（可观测）
        assert "fan__a" in r.nodes and "fan__c" in r.nodes

    def test_branch_failure_isolated_on_error_skip(self, engine):
        """分支失败隔离 → 聚合后按 parallel 节点 on_error 定夺（skip 收尾）。"""
        async def flaky(name, args, approved=False):
            if name == "p__tb":
                raise RuntimeError("分支炸了")
            return {"ok": name}

        engine.set_tool_caller(flaky)
        defn = _flow([{
            "id": "fan", "type": "parallel", "on_error": "skip",
            "branches": [
                {"id": "a", "tool": "p__ta"},
                {"id": "b", "tool": "p__tb"},
            ],
        }])
        engine.register_flow(defn, "p")
        r = run(engine.run_now("par-test", {"type": "manual"}))
        assert r.status == "completed"                   # skip 不杀流程
        out = r.outputs["fan"]
        assert out.get("skipped") is True                # on_error=skip 形状
        assert "分支失败" in (r.nodes["fan"].error or "")

    def test_branch_failure_on_error_fail_aborts(self, engine):
        async def flaky(name, args, approved=False):
            if name == "p__tb":
                raise RuntimeError("分支炸了")
            return {"ok": name}

        engine.set_tool_caller(flaky)
        defn = _flow([{
            "id": "fan", "type": "parallel", "on_error": "fail",
            "branches": [
                {"id": "a", "tool": "p__ta"},
                {"id": "b", "tool": "p__tb"},
            ],
        }])
        engine.register_flow(defn, "p")
        r = run(engine.run_now("par-test", {"type": "manual"}))
        assert r.status == "failed"
        assert "分支失败" in (r.error or "")

    def test_turn_limit_enforced_across_branches(self, engine, monkeypatch):
        """②统一轮数上限：全分支共享（此处压到 2），超限分支显式报因不硬杀。"""
        monkeypatch.setattr(flow_engine_mod, "SUBAGENT_MAX_TURNS", 2)
        defn = _flow([{
            "id": "fan", "type": "parallel", "on_error": "skip",
            "branches": [
                {"id": "a", "tool": "p__ta"},
                {"id": "b", "tool": "p__tb"},
                {"id": "c", "tool": "p__tc"},
            ],
        }])
        engine.register_flow(defn, "p")
        r = run(engine.run_now("par-test", {"type": "manual"}))
        assert r.status == "completed"                   # 显式报因不硬杀
        assert r.outputs["fan"].get("skipped") is True   # 聚合后 on_error=skip
        # 撞上限的分支留痕：合成节点记录带 TurnLimitExceeded
        branch_records = [rec for nid, rec in r.nodes.items()
                          if nid.startswith("fan__")]
        governed = [rec for rec in branch_records
                    if "TurnLimitExceeded" in (rec.error or "")]
        assert governed, {rec.node_id: rec.error for rec in branch_records}
        # 治理统计经 parallel 日志可读
        log_text = " ".join(e["detail"] for e in r.log if e["node"] == "fan")
        assert "'max_turns': 2" in log_text or '"max_turns": 2' in log_text

    def test_parse_validation(self):
        with pytest.raises(FlowDefError, match="重复"):
            _flow([{"id": "fan", "type": "parallel", "branches": [
                {"id": "a", "tool": "p__ta"}, {"id": "a", "tool": "p__tb"}]}])
        with pytest.raises(FlowDefError, match="tool 非法"):
            _flow([{"id": "fan", "type": "parallel", "branches": [
                {"id": "a", "tool": "bad"}]}])
        with pytest.raises(FlowDefError, match="branches 应为非空"):
            _flow([{"id": "fan", "type": "parallel", "branches": []}])
        with pytest.raises(FlowDefError, match="max_concurrency"):
            _flow([{"id": "fan", "type": "parallel", "max_concurrency": -1,
                    "branches": [{"id": "a", "tool": "p__ta"}]}])


class TestCompressHandoffAction:
    def test_compress_handoff_declares_instructions(self, engine):
        """阶段切换节点声明压缩要求：自定义压缩指令经既有事件总线广播。"""
        seen: list[tuple[str, dict]] = []
        original_emit = engine.emit

        def spy(event, payload=None):
            seen.append((event, payload or {}))
            return original_emit(event, payload)

        engine.emit = spy
        defn = _flow([{
            "id": "handoff", "type": "action",
            "actions": [{"type": "compress-handoff",
                         "message": "正文/台账以工作区文件为准，不在摘要中摘录"}],
        }])
        engine.register_flow(defn, "p")
        r = run(engine.run_now("par-test", {"type": "manual"}))
        assert r.status == "completed"
        hits = [p for e, p in seen if e == "compress-handoff"]
        assert hits and hits[0]["instructions"] == \
            "正文/台账以工作区文件为准，不在摘要中摘录"
        assert hits[0]["flow_id"] == "par-test"
        # 节点输出同样带回指令（驱动层双通道可取）
        acts = r.outputs["handoff"]["actions"]
        assert acts[0]["compress_handoff"]["instructions"].startswith("正文/台账")

    def test_compress_handoff_parse(self):
        from plugin.flow_def import parse_flow as pf
        with pytest.raises(FlowDefError, match="type 非法"):
            pf({"id": "x", "name": "x", "triggers": [{"type": "manual"}],
                "nodes": [{"id": "n", "type": "action",
                           "actions": [{"type": "bogus"}]}]})
