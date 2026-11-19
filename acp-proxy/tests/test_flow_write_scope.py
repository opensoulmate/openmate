"""
并行写域隔离（shared_scope_pairs 移植）— 纯函数语义 + 流程装载拒载实测

铁律句（OMH SKILL.md:130）："Two nodes that can run at the same time never share
a write scope; a shared file needs an edge or one owner."
我们的 parallel 分支同时跑（彼此无边）——写域重叠即拒绝装载（FlowDefError 拒载），
"加顺序依赖或拆开 scope"。
"""
from __future__ import annotations

import pytest

from plugin.flow_def import (
    FlowDefError,
    dump_flow,
    parse_flow,
    shared_scope_pairs,
)


# ─────────────────── 纯函数：路径语义（原样移植 dag_core 口径）───────────────────

def test_scope_pairs_as_written_vs_as_paths():
    # 原样比较：字符串相同才算共享
    assert shared_scope_pairs(
        [("a", ["x.py"]), ("b", ["x.py"])], {}, as_paths=False)
    assert not shared_scope_pairs(
        [("a", ["src"]), ("b", ["src/x.py"])], {}, as_paths=False)
    # 路径语义：src 含 src/x.py；src/a 不含 src/ab；*.md 命中 docs/a.md 不命中 src/x.py
    pairs = shared_scope_pairs(
        [("a", ["src"]), ("b", ["src/x.py"])], {}, as_paths=True)
    assert pairs and pairs[0][3] is False          # 无边=冲突
    assert not shared_scope_pairs(
        [("a", ["src/a"]), ("b", ["src/ab"])], {}, as_paths=True)
    pairs = shared_scope_pairs(
        [("a", ["*.md"]), ("b", ["docs/a.md"])], {}, as_paths=True)
    assert pairs
    assert not shared_scope_pairs(
        [("a", ["*.md"]), ("b", ["src/x.py"])], {}, as_paths=True)
    # 有直接边（顺序执行不并发）→ 可共享，joined=True
    pairs = shared_scope_pairs(
        [("a", ["x.py"]), ("b", ["x.py"])], {"b": ["a"]}, as_paths=True)
    assert pairs[0][3] is True


# ─────────────────── 拒载实测：parallel 分支写域重叠 ───────────────────

def _flow(branches):
    return {
        "id": "scope-flow", "name": "写域流", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "fan", "type": "parallel", "branches": branches},
            {"id": "end", "type": "action",
             "actions": [{"type": "notify", "message": "收尾"}]},
        ],
    }


def test_parallel_write_scope_overlap_rejected_at_load():
    """并行分支写域重叠 → 拒载（FlowDefError，点名冲突对）"""
    with pytest.raises(FlowDefError, match="写域重叠") as exc:
        parse_flow(_flow([
            {"id": "b1", "tool": "p__t1", "write_scope": ["src/app.py"]},
            {"id": "b2", "tool": "p__t2", "write_scope": ["src/app.py"]},
        ]))
    assert "b1" in str(exc.value) and "b2" in str(exc.value)
    # 目录 vs 具体文件（路径语义）也重叠 → 拒载
    with pytest.raises(FlowDefError, match="写域重叠"):
        parse_flow(_flow([
            {"id": "b1", "tool": "p__t1", "write_scope": ["src"]},
            {"id": "b2", "tool": "p__t2", "write_scope": ["src/x.py"]},
        ]))
    # glob vs 具体文件（fnmatch）→ 拒载
    with pytest.raises(FlowDefError, match="写域重叠"):
        parse_flow(_flow([
            {"id": "b1", "tool": "p__t1", "write_scope": ["docs/*.md"]},
            {"id": "b2", "tool": "p__t2", "write_scope": ["docs/a.md"]},
        ]))


def test_parallel_write_scope_disjoint_loads_ok():
    """写域互不相交 → 正常装载（含 glob 不命中路径的边界）"""
    defn = parse_flow(_flow([
        {"id": "b1", "tool": "p__t1", "write_scope": ["src/a.py"]},
        {"id": "b2", "tool": "p__t2", "write_scope": ["src/ab.py"]},
        {"id": "b3", "tool": "p__t3", "write_scope": ["*.md"]},
    ]))
    fan = defn.node_map["fan"]
    assert fan.branches[0].write_scope == ("src/a.py",)
    # 未声明写域的分支不参与检查（OMH 口径：只查声明件）
    parse_flow(_flow([
        {"id": "b1", "tool": "p__t1", "write_scope": ["src"]},
        {"id": "b2", "tool": "p__t2"},
    ]))


def test_write_scope_roundtrip_and_edit_reject(tmp_path):
    """dump_flow 往返保住 write_scope；编辑塞进重叠写域 → 同样拒载"""
    defn = parse_flow(_flow([
        {"id": "b1", "tool": "p__t1", "write_scope": ["src/a.py"]},
        {"id": "b2", "tool": "p__t2", "write_scope": ["src/b.py"]},
    ]))
    data = dump_flow(defn)
    branches = data["nodes"][0]["branches"]
    assert branches[0]["write_scope"] == ["src/a.py"]
    assert branches[1]["write_scope"] == ["src/b.py"]
    again = parse_flow(data)
    assert again.node_map["fan"].branches[0].write_scope == ("src/a.py",)

    # 编辑面（update_flow 同一 parse_flow 校验）：塞重叠写域 → FlowRunError 拒载
    from plugin.flow_engine import FlowEngine, FlowRunError
    engine = FlowEngine(tmp_path / "runs")
    engine.register_flow(defn, plugin_id="t")
    patch = {"nodes": [
        {"id": "fan", "type": "parallel", "branches": [
            {"id": "b1", "tool": "p__t1", "write_scope": ["shared.txt"]},
            {"id": "b2", "tool": "p__t2", "write_scope": ["shared.txt"]},
        ]},
        {"id": "end", "type": "action",
         "actions": [{"type": "notify", "message": "收尾"}]},
    ]}
    with pytest.raises(FlowRunError, match="写域重叠"):
        engine.update_flow("scope-flow", patch)


def test_node_level_write_scope_declared():
    """节点级 write_scope 也解析保留（delegate lane 模板用）"""
    defn = parse_flow({
        "id": "w", "name": "w", "triggers": [{"type": "manual"}],
        "nodes": [
            {"id": "a", "type": "tool", "tool": "p__t",
             "write_scope": ["src/**", "docs/"]},
        ],
    })
    assert defn.node_map["a"].write_scope == ("src/**", "docs/")
