# -*- coding: utf-8 -*-
"""P1 大仓库理解：repo_index AST 语义索引——定义/引用/调用方/文件地图 + 语义护栏。

覆盖（拔高口径：对标 OMH LSP 环路的语义定位，别名/同名不误伤）：
1. find_def：裸名/限定名定义查找，跨文件同名全列
2. find_refs 别名语义：`from m import f as g` 后 g 的使用归属 m.f
3. find_refs 同名不误伤：局部变量/lambda 绑定/参数/推导式变量不算引用；
   别的文件的同名函数不算引用；类体外裸名不查类作用域
4. find_refs 属性语义：`mm.f()`（模块属性）、`C.m()`（类属性）、`self.m()`
5. find_callers：只算 Call 函数位（`y()` 算，`print(y)` 不算）
6. 增量 build：未变文件跳过、消失文件出索引、force 全量
7. file_map/stats：文件地图 + 索引规模
8. 接线护栏：soulmate_agent 工具面/主 dispatch/code-mode dispatch/系统提示
"""
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.repo_index import RepoIndex  # noqa: E402
from agent.soulmate_agent import SoulMateAgent  # noqa: E402


def _make_repo(root: Path, build: bool = True) -> RepoIndex:
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "__init__.py").write_text("")
    (root / "pkg" / "m.py").write_text(textwrap.dedent('''\
        def save(data):
            return data

        class C:
            def run(self):
                self.save()
    '''))
    (root / "pkg" / "n.py").write_text(textwrap.dedent('''\
        from pkg.m import save as save_it
        import pkg.m as mm

        def caller1():
            save_it(1)          # 别名引用 → pkg/m.py:save
            mm.save(2)          # 模块属性引用 → pkg/m.py:save
            save = lambda x: x
            save(3)             # lambda 绑定遮蔽 → 不算

        def caller2(save):
            save(4)             # 参数遮蔽 → 不算

        def unrelated():
            v = [save for save in range(3)]  # 推导式变量 → 不算
    '''))
    (root / "pkg" / "o.py").write_text(textwrap.dedent('''\
        def save(x):
            return x

        def use():
            save(9)       # 归属 o.py:save，不算到 m.py:save 头上
    '''))
    (root / "pkg" / "p.py").write_text(textwrap.dedent('''\
        from pkg.m import C

        def drive():
            C().run()     # 类实例化+方法调用（动态，不解析）
            C.run         # 类属性引用 → pkg/m.py:C.run
    '''))
    idx = RepoIndex(root, db_path=root / "idx.db")
    if build:
        idx.build()
    return idx


def test_find_def_bare_and_qualified(tmp_path):
    idx = _make_repo(tmp_path)
    saves = idx.find_def("save")
    assert {s.file for s in saves} == {"pkg/m.py", "pkg/o.py"}
    runs = idx.find_def("run")
    assert [s.brief() for s in runs] == ["pkg/m.py:5 C.run [function]"]
    assert idx.find_def("C.run")[0].file == "pkg/m.py"


def test_find_refs_alias_and_attr(tmp_path):
    idx = _make_repo(tmp_path)
    refs = idx.find_refs("save", target_file="pkg/m.py")
    got = {(r.file, r.line) for r in refs}
    # 别名 save_it(1) 行5 + mm.save(2) 行6
    assert got == {("pkg/n.py", 5), ("pkg/n.py", 6)}, got
    # 局部 lambda save(3)/参数 save(4)/推导式 save 不在列（同名不误伤）
    assert all(r.line not in (8, 11, 14) for r in refs)


def test_find_refs_same_name_cross_file_not_confused(tmp_path):
    idx = _make_repo(tmp_path)
    # o.py 的 save(9) 只归属 o.py:save
    refs_o = idx.find_refs("save", target_file="pkg/o.py")
    assert [(r.file, r.line) for r in refs_o] == [("pkg/o.py", 5)]
    # m.py 的 save 引用里绝不含 o.py 行
    refs_m = idx.find_refs("save", target_file="pkg/m.py")
    assert all(r.file != "pkg/o.py" for r in refs_m)


def test_find_refs_class_attr(tmp_path):
    idx = _make_repo(tmp_path)
    refs = idx.find_refs("run", target_file="pkg/m.py")
    got = [(r.file, r.line, r.kind) for r in refs]
    # C.run 类属性引用（p.py 行5）
    assert ("pkg/p.py", 5, "attr") in got, got


def test_find_refs_self_attr_type_inference(tmp_path):
    """self.X.method() 实例属性类型线索：X = SomeClass(...) 归属 SomeClass.method；
    别的类同名 self.X 指向别的类型 → 不误伤。"""
    (tmp_path / "lib.py").write_text(textwrap.dedent('''\
        class Engine:
            def start(self):
                return 1

        class Fake:
            def start(self):
                return 2
    '''))
    (tmp_path / "app.py").write_text(textwrap.dedent('''\
        from lib import Engine, Fake

        class Driver:
            def __init__(self):
                self.impl = Engine()
            def go(self):
                self.impl.start()   # → lib.py:Engine.start

        class Other:
            def __init__(self):
                self.impl = Fake()
            def go(self):
                self.impl.start()   # → lib.py:Fake.start（不误伤）
    '''))
    idx = RepoIndex(tmp_path, db_path=tmp_path / "idx.db")
    idx.build()
    refs = idx.find_refs("start", target_file="lib.py")
    by_target = {}
    for r in refs:
        by_target.setdefault(r.resolved, set()).add((r.file, r.line))
    # self.impl 归属按类隔离：Driver→Engine.start，Other→Fake.start（同名不误伤）
    assert by_target.get("lib.py:Engine.start") == {("app.py", 7)}, by_target
    assert by_target.get("lib.py:Fake.start") == {("app.py", 13)}, by_target


def test_find_callers_only_call_positions(tmp_path):
    idx = _make_repo(tmp_path)
    callers = idx.find_callers("save", target_file="pkg/m.py")
    assert {c.line for c in callers} == {5, 6}
    # 所有 caller 都是调用位
    assert all(c.is_call for c in callers)


def test_incremental_build_skips_and_removes(tmp_path):
    idx = _make_repo(tmp_path, build=False)
    res1 = idx.build()
    assert res1["indexed"] >= 4
    res2 = idx.build()
    assert res2["indexed"] == 0 and res2["skipped"] >= 4  # 未变全跳过
    (tmp_path / "pkg" / "o.py").unlink()
    res3 = idx.build()
    assert res3["indexed"] == 0
    assert idx.find_def("save")[0].file == "pkg/m.py"  # o.py 的定义已出索引
    assert len(idx.find_def("save")) == 1


def test_file_map_and_stats(tmp_path):
    idx = _make_repo(tmp_path)
    fm = idx.file_map(subtree="pkg")
    paths = {f["path"] for f in fm["files"]}
    assert "pkg/m.py" in paths
    m = next(f for f in fm["files"] if f["path"] == "pkg/m.py")
    assert any("save" in s for s in m["top_symbols"])
    st = idx.stats()
    assert st["files"] >= 4 and st["symbols"] > 0


def test_syntax_error_file_goes_to_filemap_not_symbols(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n")
    (tmp_path / "ok.py").write_text("def fine():\n    return 1\n")
    idx = RepoIndex(tmp_path, db_path=tmp_path / "idx.db")
    res = idx.build()
    assert any("bad.py" in f for f in res["failed"])
    assert idx.find_def("fine")  # 好文件照常
    assert not idx.find_def("broken")


# ── 接线护栏（写了≠接线了）────────────────────────────────────────

def test_wiring_tool_face():
    import inspect
    src = inspect.getsource(SoulMateAgent)
    assert '"name": "repo_index"' in src, "工具面缺 repo_index"
    assert '"name": "fix_loop"' in src, "工具面缺 fix_loop"
    assert "builtin_tools.append(repo_index_tool)" in src
    assert "builtin_tools.append(fix_loop_tool)" in src


def test_wiring_dispatch_routes():
    import inspect
    src = inspect.getsource(SoulMateAgent)
    # 主 dispatch + code-mode 内层 dispatch 都要有路由
    assert src.count('func_name == "repo_index"') >= 2, "主/code-mode dispatch 至少各一处"
    assert src.count('func_name == "fix_loop"') >= 2
    assert "self._repo_index_dispatch(func_args)" in src
    assert "self._fix_loop_dispatch(func_args, session_id)" in src


def test_wiring_system_prompt():
    import inspect
    src = inspect.getsource(SoulMateAgent)
    assert "- repo_index:" in src, "系统提示缺 repo_index 用法"
    assert "- fix_loop:" in src, "系统提示缺 fix_loop 用法"


def test_dispatch_end_to_end(tmp_path, monkeypatch):
    """工具分发端到端：build→def→refs→callers→file_map→stats 全走通。"""
    import agent.repo_index as ri_mod
    monkeypatch.setattr(ri_mod, "DEFAULT_INDEX_ROOT", tmp_path / "idxroot")
    (tmp_path / "lib.py").write_text("def helper():\n    return 1\n")
    (tmp_path / "main.py").write_text("from lib import helper as h\nh()\nh()\n")
    ag = SoulMateAgent.__new__(SoulMateAgent)
    ag._project_root = str(tmp_path)
    out = ag._repo_index_dispatch({"action": "build", "repo_path": str(tmp_path)})
    assert "仓库索引已建" in out
    out = ag._repo_index_dispatch({"action": "def", "name": "helper", "repo_path": str(tmp_path)})
    assert "lib.py" in out
    out = ag._repo_index_dispatch({"action": "callers", "name": "helper",
                                   "target_file": "lib.py", "repo_path": str(tmp_path)})
    assert "main.py" in out
    out = ag._repo_index_dispatch({"action": "file_map", "repo_path": str(tmp_path)})
    assert "lib.py" in out
    out = ag._repo_index_dispatch({"action": "stats", "repo_path": str(tmp_path)})
    assert "symbols" in out
    out = ag._repo_index_dispatch({"action": "nope", "repo_path": str(tmp_path)})
    assert "未知 action" in out
