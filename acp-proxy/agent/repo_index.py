# -*- coding: utf-8 -*-
"""repo_index.py — 大仓库轻量静态索引（符号/文件地图，AST 语义级）。

来源：P1-代码工程能力「大仓库理解」——回答"X 在哪实现 / 谁调 Y"靠索引，
不把全仓塞进 LLM 上下文。对标 OMH 的 LSP 环路（omh-lsp 四工具：
lsp_definition / lsp_references / lsp_rename / lsp_diagnostics）——本模块用
纯 Python ast 自实现同等语义（无需外部 LSP server），语义规则对齐 lsp_nav.py
的护栏哲学：**按 AST 语义定位，不按字符串匹配**。

语义保证（区别于 grep/字符串匹配）：
- 别名不误伤：`from m import f as g` 后引用 `g` 归属 `m.f`；`import m as mm`
  后 `mm.f` 归属 `m.f`。
- 同名不误伤：函数局部变量/参数/类属性/局部 import 对模块级同名符号的遮蔽
  （shadowing）按作用域链解析——局部 `f` 的使用不算模块级 `f` 的引用。
- 调用者（谁调 Y）：引用点是 Call 节点的才算 caller（`y()` 算，`print(y)` 不算）。

索引落盘：acp-proxy/data/repo-index/<repo-slug>.db（SQLite，增量重建按
sha256 跳过未变文件）。查询全程走索引，零全仓文本进上下文。

已知边界（显式声明，不假装全能）：
- 动态派发（getattr/setattr、globals()['x']）解析不了——如实报"动态引用未知"；
- `from m import *` 的星号导入不解析（宁缺勿错）；
- 仅索引 Python（.py/.pyi）；其它语言只进文件地图（文件名/行数/语言）。
"""
from __future__ import annotations

import ast
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("acp-proxy.repo-index")

__all__ = [
    "RepoIndex",
    "SymbolHit",
    "RefHit",
    "DEFAULT_INDEX_ROOT",
]

# 索引落盘根（任务口径"轻量静态索引落 data/"=acp-proxy/data/）
DEFAULT_INDEX_ROOT = Path(__file__).resolve().parent.parent / "data" / "repo-index"

_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build", ".eggs",
    "site-packages", ".next", ".turbo",
})
_MAX_FILE_BYTES = 2_000_000  # 超大文件不解析 AST（只进文件地图）
_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# bindings.kind 取值
K_DEF = "def"                # def/async def（含方法）
K_CLASS = "class"
K_ASSIGN = "assign"          # 模块/函数级赋值名
K_PARAM = "param"
K_IMPORT_NAME = "import_name"    # import a.b as c → 绑定 c → target="a.b"
K_IMPORT_FROM = "import_from"    # from m import f as g → 绑定 g → target="m.f"
K_COMPREHENSION = "comprehension"
K_EXCEPT_AS = "except_as"
K_FOR = "for_target"
K_WITH = "with_as"
K_GLOBAL = "global"          # 声明：本 scope 内该名解析外抛
K_NONLOCAL = "nonlocal"
K_WALRUS = "walrus"
K_LAMBDA = "lambda"
K_SELF_ATTR = "self_attr"    # self.x = SomeClass(...) → self.x 的类型线索


@dataclass
class SymbolHit:
    name: str
    qualname: str
    kind: str          # class / function / async_function / method / assignment
    file: str          # 仓库相对路径
    line: int
    end_line: int
    parent: str = ""   # 父 qualname（类方法的类名）

    def brief(self) -> str:
        return f"{self.file}:{self.line} {self.qualname} [{self.kind}]"


@dataclass
class RefHit:
    name: str
    file: str
    line: int
    col: int
    kind: str          # name | attr | call | import_alias
    resolved: str      # 归属目标（"file.py:qualname" 或 "external:m.f"）
    is_call: bool = False
    context: str = ""  # 该行源码片段（供 LLM 定位，不塞全文件）

    def brief(self) -> str:
        call = "()" if self.is_call else ""
        return f"{self.file}:{self.line}:{self.col} {self.name}{call} → {self.resolved}"


@dataclass
class _Scope:
    """作用域节点：parent 为外层 scope id（-1=模块）。"""
    id: int
    parent: int
    kind: str          # module | function | class | lambda | comprehension
    name: str          # 完整显示名（module / func:foo / class:C / func:C.bar）


class RepoIndex:
    """仓库静态索引：build（增量）→ find_def / find_refs / find_callers / file_map。

    语义解析在 build 时完成并落 SQLite；查询纯读索引（含遮蔽/别名归属）。
    """

    def __init__(self, root: str | os.PathLike, db_path: str | os.PathLike | None = None):
        self.root = Path(root).resolve()
        if db_path is None:
            slug = re.sub(r"[^A-Za-z0-9_-]+", "_", self.root.name) or "repo"
            db_path = DEFAULT_INDEX_ROOT / f"{slug}.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── SQLite 基础 ────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(str(self.db_path), timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _init_db(self) -> None:
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    mtime REAL NOT NULL,
                    loc INTEGER NOT NULL,
                    language TEXT DEFAULT 'python',
                    tree_path TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS symbols (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file TEXT NOT NULL,
                    name TEXT NOT NULL,
                    qualname TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    parent TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
                CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
                CREATE TABLE IF NOT EXISTS scopes (
                    id INTEGER NOT NULL,
                    file TEXT NOT NULL,
                    parent INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    PRIMARY KEY (file, id)
                );
                CREATE INDEX IF NOT EXISTS idx_scopes_file ON scopes(file);
                CREATE TABLE IF NOT EXISTS bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file TEXT NOT NULL,
                    name TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    scope INTEGER NOT NULL,
                    kind TEXT NOT NULL,
                    target TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_bindings_name ON bindings(name);
                CREATE INDEX IF NOT EXISTS idx_bindings_file ON bindings(file);
                CREATE TABLE IF NOT EXISTS usages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file TEXT NOT NULL,
                    name TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    col INTEGER NOT NULL,
                    scope INTEGER NOT NULL,
                    ctx TEXT NOT NULL,
                    is_call INTEGER DEFAULT 0,
                    is_attr INTEGER DEFAULT 0,
                    base TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_usages_name ON usages(name);
                CREATE INDEX IF NOT EXISTS idx_usages_file ON usages(file);
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)

    def _set_meta(self, key: str, value) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_meta(self, key: str, default=None):
        with self._connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if not row:
            return default
        try:
            return json.loads(row[0])
        except (ValueError, TypeError):
            return default

    # ── 建索引 ─────────────────────────────────────────────────────

    def _iter_files(self):
        """遍历仓库文件：Python 走 AST 解析，其余语言只进文件地图。"""
        db_resolved = self.db_path.resolve()
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(
                d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")
            )
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                # 索引库自身（及其 journal/wal）不索引——否则每次 build 都"自变"
                if fn.endswith((".db-journal", ".db-wal", ".db-shm")):
                    continue
                try:
                    if p.resolve() == db_resolved:
                        continue
                except OSError:
                    continue
                yield p

    @staticmethod
    def _language(path: Path) -> str:
        ext = path.suffix.lower()
        return {
            ".py": "python", ".pyi": "python",
            ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx",
            ".rs": "rust", ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
            ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
            ".toml": "toml", ".sh": "bash",
        }.get(ext, "text")

    def build(self, force: bool = False) -> dict:
        """增量建索引：sha256 未变的文件跳过（force=True 强制全量重建）。

        返回 {"indexed": N, "skipped": N, "failed": [path...], "total_files": N}。
        """
        indexed, skipped, failed = 0, 0, []
        seen: set[str] = set()
        with self._connect() as db:
            old = {r["path"]: r["sha256"] for r in db.execute("SELECT path, sha256 FROM files")}
        for p in self._iter_files():
            rel = str(p.relative_to(self.root))
            try:
                raw = p.read_bytes()
            except OSError as e:
                failed.append(f"{rel}: {e}")
                continue
            if len(raw) > _MAX_FILE_BYTES:
                seen.add(rel)
                with self._connect() as db:
                    db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                               (rel, hashlib.sha256(raw).hexdigest(), p.stat().st_mtime,
                                raw.count(b"\n") + 1, self._language(p), self._tree_path(rel)))
                skipped += 1
                continue
            digest = hashlib.sha256(raw).hexdigest()
            if not force and old.get(rel) == digest:
                seen.add(rel)
                skipped += 1
                continue
            lang = self._language(p)
            try:
                if lang == "python":
                    self._index_python(db_rel=rel, raw=raw, digest=digest,
                                       mtime=p.stat().st_mtime)
                else:
                    with self._connect() as db:
                        self._clear_file(db, rel)
                        db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                                   (rel, digest, p.stat().st_mtime,
                                    raw.count(b"\n") + 1, lang, self._tree_path(rel)))
                indexed += 1
                seen.add(rel)
            except (SyntaxError, ValueError, UnicodeDecodeError) as e:
                # 语法坏文件：进文件地图但不解析符号（如实标注，不假造符号）
                failed.append(f"{rel}: {e.__class__.__name__}")
                with self._connect() as db:
                    db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                               (rel, digest, p.stat().st_mtime,
                                raw.count(b"\n") + 1, lang + "+syntax-error",
                                self._tree_path(rel)))
                seen.add(rel)
        # 消失的文件出索引
        with self._connect() as db:
            for rel in old:
                if rel not in seen:
                    self._clear_file(db, rel)
        self._rebuild_modmap()
        self._set_meta("root", str(self.root))
        self._set_meta("built_at", time.time())
        self._set_meta("file_count", len(seen))
        return {"indexed": indexed, "skipped": skipped, "failed": failed,
                "total_files": len(seen)}

    @staticmethod
    def _tree_path(rel: str) -> str:
        return str(Path(rel).parent).replace(os.sep, "/")

    @staticmethod
    def _clear_file(db: sqlite3.Connection, rel: str) -> None:
        db.execute("DELETE FROM files WHERE path=?", (rel,))
        for table in ("symbols", "scopes", "bindings", "usages"):
            db.execute(f"DELETE FROM {table} WHERE file=?", (rel,))

    # ── Python AST 索引（符号 + 作用域 + 绑定 + 使用点） ─────────────

    def _index_python(self, db_rel: str, raw: bytes, digest: str, mtime: float) -> None:
        text = raw.decode("utf-8", errors="replace")
        tree = ast.parse(text, filename=db_rel)
        w = _PyWalker(db_rel, text)
        w.prescan(tree)
        w.visit(tree)
        lines = text.splitlines()
        with self._connect() as db:
            self._clear_file(db, db_rel)
            db.execute("INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)",
                       (db_rel, digest, mtime, len(lines), "python", self._tree_path(db_rel)))
            for s in w.symbols:
                db.execute(
                    "INSERT INTO symbols (file, name, qualname, kind, line, end_line, parent)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (db_rel, s.name, s.qualname, s.kind, s.line, s.end_line, s.parent))
            for sc in w.scopes:
                db.execute("INSERT INTO scopes (id, file, parent, kind, name) VALUES (?,?,?,?,?)",
                           (sc.id, db_rel, sc.parent, sc.kind, sc.name))
            for b in w.bindings:
                db.execute(
                    "INSERT INTO bindings (file, name, line, scope, kind, target)"
                    " VALUES (?,?,?,?,?,?)",
                    (db_rel, b[0], b[1], b[2], b[3], b[4]))
            for u in w.usages:
                db.execute(
                    "INSERT INTO usages (file, name, line, col, scope, ctx, is_call, is_attr, base)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (db_rel, u[0], u[1], u[2], u[3], u[4], u[5], u[6], u[7]))

    # ── 查询：定义 ─────────────────────────────────────────────────

    def find_def(self, name: str, limit: int = 20) -> list[SymbolHit]:
        """符号定义查找（AST 语义）：`name` 可裸名（`save`）或限定名（`Cls.save`）。"""
        name = (name or "").strip()
        if not name:
            return []
        with self._connect() as db:
            if "." in name:
                rows = db.execute(
                    "SELECT * FROM symbols WHERE qualname=? OR qualname LIKE ? "
                    "ORDER BY file, line LIMIT ?",
                    (name, "%." + name, limit)).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM symbols WHERE name=? ORDER BY file, line LIMIT ?",
                    (name, limit)).fetchall()
        return [self._symbol_hit(r) for r in rows]

    @staticmethod
    def _symbol_hit(r) -> SymbolHit:
        return SymbolHit(name=r["name"], qualname=r["qualname"], kind=r["kind"],
                         file=r["file"], line=r["line"], end_line=r["end_line"],
                         parent=r["parent"])

    # ── 查询：引用 / 调用者 ────────────────────────────────────────

    def find_refs(self, name: str, target_file: str = "", limit: int = 50) -> list[RefHit]:
        """语义引用查找：只算**解析归属到目标符号**的使用点。

        同名遮蔽（局部变量/参数/类属性）与别名（as）按作用域链解析，
        不做字符串匹配。target_file 给定时只归属该文件定义的符号。
        """
        name = (name or "").strip()
        if not name:
            return []
        bare = name.split(".")[-1]
        with self._connect() as db:
            # 目标符号定义（归属锚点）
            if target_file:
                defs = db.execute(
                    "SELECT * FROM symbols WHERE name=? AND file=?", (bare, target_file)
                ).fetchall()
            else:
                defs = db.execute("SELECT * FROM symbols WHERE name=?", (bare,)).fetchall()
            if not defs:
                return []  # 无定义锚点 = 没有"语义归属"可言，宁缺勿错
            ext_targets = {f"{d['file']}:{d['qualname']}" for d in defs}

            # 别名扫描：`from m import f as g` → g 的使用归属 m.f
            alias_rows = db.execute(
                "SELECT file, name, target FROM bindings WHERE kind=? AND target LIKE ?",
                (K_IMPORT_FROM, "%." + bare)).fetchall()
            scan_names = {bare}
            for a in alias_rows:
                if a["target"].rsplit(".", 1)[-1] == bare:
                    scan_names.add(a["name"])

            placeholders = ",".join("?" * len(scan_names))
            usages = db.execute(
                f"SELECT * FROM usages WHERE name IN ({placeholders}) AND ctx='load' "
                f"ORDER BY file, line, col", tuple(scan_names)).fetchall()
            scope_cache: dict[str, dict] = {}
            hits: list[RefHit] = []
            for u in usages:
                file = u["file"]
                info = scope_cache.get(file)
                if info is None:
                    info = self._load_scope_info(db, file)
                    scope_cache[file] = info
                if u["is_attr"]:
                    base_name = (u["base"] or "").split(".")[0]
                    base_resolved = (
                        self._resolve_name(base_name, u["scope"], info) or ""
                        if base_name and base_name != "self" else ""
                    )
                    qual = self._resolve_attr_target(u, info, base_resolved)
                    if not qual or not self._match_target(qual, ext_targets):
                        continue
                    resolved_str = qual
                else:
                    resolved = self._resolve_name(u["name"], u["scope"], info)
                    if not resolved or not self._match_target(resolved, ext_targets):
                        continue
                    resolved_str = resolved
                ctx = self._line_text(db, file, u["line"])
                hits.append(RefHit(
                    name=u["name"], file=file, line=u["line"], col=u["col"],
                    kind="attr" if u["is_attr"] else "name",
                    resolved=resolved_str, is_call=bool(u["is_call"]),
                    context=ctx[:160]))
                if len(hits) >= limit:
                    break
            return hits

    def find_callers(self, name: str, target_file: str = "", limit: int = 50) -> list[RefHit]:
        """谁调 Y：引用点中是 Call 节点函数位的（`y()` 算，`print(y)` 不算）。"""
        return [r for r in self.find_refs(name, target_file=target_file, limit=limit * 2)
                if r.is_call][:limit]

    @staticmethod
    def _match_target(resolved: str, ext_targets: set) -> bool:
        return resolved in ext_targets

    def _load_scope_info(self, db: sqlite3.Connection, file: str) -> dict:
        scopes = {r["id"]: dict(r) for r in
                  db.execute("SELECT * FROM scopes WHERE file=?", (file,))}
        bindings = [dict(r) for r in
                    db.execute("SELECT * FROM bindings WHERE file=?", (file,))]
        by_name: dict[str, list[dict]] = {}
        for b in bindings:
            by_name.setdefault(b["name"], []).append(b)
        return {"scopes": scopes, "bindings": by_name, "file": file}

    def _resolve_name(self, name: str, scope_id: int, info: dict) -> str | None:
        """名字归属解析：沿作用域链找最近绑定 → 映射到符号归属。

        返回归属标识 "file.py:qualname" / "file.py:"（模块）/ "external:module.qual"；
        解析不出（星号导入/动态/链外同名）返回 None（宁缺勿错）。
        """
        if not name:
            return None
        scopes = info["scopes"]
        cand = info["bindings"].get(name, [])
        if not cand:
            return None
        # 作用域链（由内到外）；module scope id=0, parent=-1。
        # Python 语义：方法体内的裸名解析**跳过类作用域**（类属性只经属性访问可达）。
        chain: list[int] = []
        sid = scope_id
        first = True
        while sid != -1 and sid in scopes:
            sc = scopes[sid]
            if sc["kind"] == "class" and not first:
                sid = sc["parent"]
                continue
            chain.append(sid)
            first = False
            sid = sc["parent"]
        best = None
        best_depth = None
        for b in cand:
            bscope = b["scope"]
            if bscope not in chain:
                continue  # 别处作用域的同名绑定（别的函数的局部名）→ 与本使用点无关
            depth = chain.index(bscope)
            if best_depth is None or depth < best_depth:
                best, best_depth = b, depth
        if best is None:
            return None
        # global 声明：本 scope 不绑定，外抛到模块级定义
        if best["kind"] == K_GLOBAL:
            for b in cand:
                sc = scopes.get(b["scope"])
                if b["kind"] in (K_DEF, K_CLASS, K_ASSIGN) and (
                        sc is None or sc["kind"] == "module"):
                    best = b
                    break
        return self._binding_target(best, info)

    def _binding_target(self, b: dict, info: dict) -> str | None:
        scopes = info["scopes"]
        kind = b["kind"]
        file = b["file"]
        if kind == K_IMPORT_FROM:
            return self._import_target_key(b["target"])
        if kind == K_IMPORT_NAME:
            return self._import_target_key(b["target"])
        if kind == K_SELF_ATTR:
            # self.x 的类型线索：把类型名解析回符号键（import/类定义同款归属）
            return self._resolve_name(b["target"], b["scope"], info) or f"external:{b['target']}"
        if kind in (K_DEF, K_CLASS, K_ASSIGN, K_PARAM, K_COMPREHENSION,
                    K_EXCEPT_AS, K_FOR, K_WITH, K_WALRUS, K_LAMBDA):
            sc = scopes.get(b["scope"])
            qual = b["name"]
            if sc and sc["kind"] in ("function", "class") and sc["id"] != 0:
                prefix = sc["name"].split(":", 1)[-1]
                if prefix and prefix not in ("module", "comp", "lambda"):
                    qual = f"{prefix}.{b['name']}"
            # 局部名（参数/函数内赋值/推导式/lambda）→ local: 前缀键，
            # 永不与符号键 "file:qualname" 相等（同名不误伤的最后一道闸）
            if kind in (K_PARAM, K_COMPREHENSION, K_LAMBDA) or (
                    sc is not None and sc["kind"] in ("function", "comprehension", "lambda")
                    and kind != K_DEF and not (kind == K_CLASS and sc["kind"] == "function")
            ):
                if not (kind in (K_DEF, K_CLASS)):
                    return f"local:{file}:{sc['name'] if sc else ''}:{b['name']}"
            return f"{file}:{qual}"
        return None

    def _resolve_attr_target(self, u, info: dict, base_resolved: str) -> str | None:
        """属性使用 `base.attr` 的归属：base 归属 + attr 拼限定名。

        - base 归属某类/模块符号（"F:C" / "F:mod"）→ "F:C.attr"；
        - base 是 self 且使用点在类作用域内（self.m()）→ "F:C.attr"；
        - 其它（实例变量/动态）→ None（宁缺勿错）。
        """
        attr = u["name"]
        if base_resolved.endswith(":"):
            # 模块归属键（"file.py:" / "external:a.b:"）→ 符号键 "file.py:attr"
            return f"{base_resolved.rstrip(':')}:{attr}"
        # self.X.attr：实例属性类型线索（self.X = SomeClass(...)）→ SomeClass.attr
        base = u["base"] or ""
        if base.startswith("self.") and base.count(".") == 1:
            scopes = info["scopes"]
            sid = u["scope"]
            class_sid = None
            while sid != -1 and sid in scopes:
                sc = scopes[sid]
                if sc["kind"] == "class":
                    class_sid = sc["id"]
                    break
                sid = sc["parent"]
            for b in info["bindings"].get(base, []):
                if b["kind"] != K_SELF_ATTR:
                    continue
                if class_sid is not None and b["scope"] != class_sid:
                    continue  # 别的类的同名 self.X → 同名不误伤
                cls_key = self._binding_target(b, info) or f"external:{b['target']}"
                return f"{cls_key}.{attr}"
            return None
        # self 特判：归属到所在最近类
        if u["base"] == "self":
            scopes = info["scopes"]
            sid = u["scope"]
            while sid != -1 and sid in scopes:
                sc = scopes[sid]
                if sc["kind"] == "class":
                    cls = sc["name"].split(":", 1)[-1]
                    return f"{info['file']}:{cls}.{attr}"
                sid = sc["parent"]
            return None
        if u["base"] and "." in u["base"]:
            return None  # 多级动态 base（x.y.m）：解析不了，宁缺勿错
        if not base_resolved:
            return None  # base 解析不出（动态/星号导入）
        # base 解析到的符号是类/模块 → 拼 qualname
        return f"{base_resolved}.{attr}"

    def _import_target_key(self, target: str) -> str:
        """import 目标（"module.qual"）→ 仓库内符号键或 external: 前缀。"""
        if not target:
            return "external:unknown"
        modmap = self._modmap_cached()
        parts = target.split(".")
        for i in range(len(parts), 0, -1):
            mod = ".".join(parts[:i])
            rel = modmap.get(mod)
            if rel is not None:
                qual = ".".join(parts[i:]) or ""
                return f"{rel}:{qual}" if qual else f"{rel}:"
        return f"external:{target}"

    def _modmap_cached(self) -> dict:
        cache = getattr(self, "_modmap_cache", None)
        if cache is None or cache.get("_built_at") != self.get_meta("built_at"):
            cache = dict(self.get_meta("_modmap", {}) or {})
            cache["_built_at"] = self.get_meta("built_at")
            self._modmap_cache = cache
        return cache

    def _line_text(self, db: sqlite3.Connection, file: str, line: int) -> str:
        try:
            p = self.root / file
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for i, text in enumerate(f, 1):
                    if i == line:
                        return text.strip()
        except OSError:
            pass
        return ""

    # ── 查询：文件地图 / 统计 ──────────────────────────────────────

    def file_map(self, subtree: str = "", limit: int = 200) -> dict:
        """文件地图：目录树 + 每文件行数/语言/顶层符号名（轻量，不塞内容）。"""
        with self._connect() as db:
            rows = db.execute(
                "SELECT path, loc, language FROM files WHERE path LIKE ? ORDER BY path LIMIT ?",
                (f"{subtree}%" if subtree else "%", limit)).fetchall()
            files = []
            for r in rows:
                syms = db.execute(
                    "SELECT name, kind FROM symbols WHERE file=? AND parent='' LIMIT 20",
                    (r["path"],)).fetchall()
                files.append({
                    "path": r["path"], "loc": r["loc"], "language": r["language"],
                    "top_symbols": [f"{s['name']}[{s['kind'][:4]}]" for s in syms],
                })
        tree: dict = {}
        for f in files:
            node = tree
            parts = Path(f["path"]).parts
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = f
        return {"root": str(self.root), "files": files, "tree": tree}

    def stats(self) -> dict:
        with self._connect() as db:
            n_files = db.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            n_syms = db.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            n_usages = db.execute("SELECT COUNT(*) FROM usages").fetchone()[0]
        return {
            "root": str(self.root),
            "db_path": str(self.db_path),
            "files": n_files,
            "symbols": n_syms,
            "usages": n_usages,
            "built_at": self.get_meta("built_at"),
        }

    # ── 模块名映射（import 归属用，build 时重建） ───────────────────

    def _rebuild_modmap(self) -> None:
        modmap: dict[str, str] = {}
        with self._connect() as db:
            rows = db.execute("SELECT path FROM files WHERE language='python'").fetchall()
        for r in rows:
            rel = r["path"]
            if not rel.endswith(".py"):
                continue
            mod = rel[:-3].replace("/", ".")
            if mod.endswith(".__init__"):
                mod = mod[: -len(".__init__")]
            modmap[mod] = rel
        self._set_meta("_modmap", modmap)


class _PyWalker(ast.NodeVisitor):
    """单文件 AST 遍历：符号 + 作用域 + 绑定 + 使用点。"""

    def __init__(self, rel: str, text: str):
        self.rel = rel
        self.lines = text.splitlines()
        self.symbols: list[SymbolHit] = []
        self.scopes: list[_Scope] = []
        self.bindings: list[tuple] = []   # (name, line, scope_id, kind, target)
        self.usages: list[tuple] = []     # (name, line, col, scope_id, ctx, is_call, is_attr, base)
        self._scope_id = 0
        self._cur: _Scope = _Scope(id=0, parent=-1, kind="module", name="module")
        self.scopes.append(self._cur)
        self._qual_stack: list[str] = []
        self._call_nodes: set[int] = set()
        self._attr_bases: dict[int, str] = {}

    # -- 作用域管理 --

    def _push_scope(self, kind: str, name: str) -> _Scope:
        self._scope_id += 1
        parent_name = self._cur.name.split(":", 1)[-1] if self._cur.kind != "module" else ""
        full = f"{parent_name}.{name}" if parent_name and self._cur.kind in ("function", "class") else name
        sc = _Scope(id=self._scope_id, parent=self._cur.id, kind=kind,
                    name=f"{kind[0]}:{full}" if kind != "module" else "module")
        self.scopes.append(sc)
        self._cur = sc
        return sc

    def _pop_scope(self) -> None:
        for s in reversed(self.scopes):
            if s.id == self._cur.parent:
                self._cur = s
                return
        self._cur = self.scopes[0]

    def _bind(self, name: str, node, kind: str, target: str = "") -> None:
        self.bindings.append((name, getattr(node, "lineno", 0), self._cur.id, kind, target))

    # -- 预扫描：Call 节点与属性 base --

    def prescan(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    self._call_nodes.add(id(fn))
                elif isinstance(fn, ast.Attribute):
                    self._call_nodes.add(id(fn))
                    self._attr_bases[id(fn)] = self._base_name(fn.value)
            elif isinstance(node, ast.Attribute):
                self._attr_bases.setdefault(id(node), self._base_name(node.value))

    @staticmethod
    def _base_name(node) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            inner = _PyWalker._base_name(node.value)
            return f"{inner}.{node.attr}" if inner else node.attr
        return ""

    # -- 主遍历 --

    def _record_symbol(self, name: str, node, kind: str) -> None:
        parent = self._qual_stack[-1] if self._qual_stack else ""
        qual = f"{parent}.{name}" if parent else name
        self.symbols.append(SymbolHit(
            name=name, qualname=qual, kind=kind, file=self.rel,
            line=getattr(node, "lineno", 0),
            end_line=getattr(node, "end_lineno", None) or getattr(node, "lineno", 0),
            parent=parent))

    # def / class

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node, "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node, "async_function")

    def _visit_func(self, node, kind: str) -> None:
        self._record_symbol(node.name, node, kind)
        self._bind(node.name, node, K_DEF, target=f"{self.rel}:{node.name}")
        self._qual_stack.append(node.name)
        self._push_scope("function", node.name)
        for a in list(node.args.posonlyargs) + list(node.args.args) + \
                list(node.args.kwonlyargs):
            self._bind(a.arg, a, K_PARAM)
        if node.args.vararg:
            self._bind(node.args.vararg.arg, node.args.vararg, K_PARAM)
        if node.args.kwarg:
            self._bind(node.args.kwarg.arg, node.args.kwarg, K_PARAM)
        for deco in node.decorator_list:
            self.visit(deco)
        for stmt in node.body:
            self.visit(stmt)
        self._pop_scope()
        self._qual_stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record_symbol(node.name, node, "class")
        self._bind(node.name, node, K_CLASS, target=f"{self.rel}:{node.name}")
        self._qual_stack.append(node.name)
        self._push_scope("class", node.name)
        for stmt in node.body:
            self.visit(stmt)
        self._pop_scope()
        self._qual_stack.pop()

    # 赋值绑定

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if isinstance(t, ast.Attribute):
                self._bind_self_attr(t, node.value)
            else:
                self._bind_target(t, K_ASSIGN)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Attribute):
            self._bind_self_attr(node.target, node.value, annotation=node.annotation)
        else:
            self._bind_target(node.target, K_ASSIGN)
        if node.value:
            self.visit(node.value)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._bind_target(node.target, K_ASSIGN)
        self.visit(node.value)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        self._bind_target(node.target, K_WALRUS)
        self.visit(node.value)

    def _bind_target(self, target, kind: str) -> None:
        if isinstance(target, ast.Name):
            self._bind(target.id, target, kind, target=f"{self.rel}:{target.id}")
            if self._cur.kind == "module" or (
                    self._cur.kind == "class"):
                self._record_symbol(target.id, target,
                                    "assignment" if self._cur.kind == "module" else "class_attr")
        elif isinstance(target, ast.Attribute):
            self._bind_self_attr(target, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                self._bind_target(elt, kind)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, kind)
        else:
            for child in ast.iter_child_nodes(target):
                if isinstance(child, ast.expr):
                    self.visit(child)

    def _bind_self_attr(self, target: ast.Attribute, value_node, annotation=None) -> None:
        """self.x = SomeClass(...) / self.x: SomeClass → 记类型线索（归属最近类）。

        实例属性在 __init__ 赋值、在别的方法里 `self.x.method()` 使用——绑定归
        类作用域而非方法作用域，跨方法可查（同名不误伤靠类隔离）。
        """
        if not (isinstance(target.value, ast.Name) and target.value.id == "self"):
            return
        type_name = ""
        node = annotation if annotation is not None else value_node
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            type_name = node.func.id
        elif isinstance(node, ast.Name):
            type_name = node.id
        if not type_name:
            return
        # 归属最近类作用域（实例属性是类的，不是某方法的）
        sid = self._cur.id
        class_sid = None
        while sid != -1 and sid in {s.id for s in self.scopes}:
            sc = next(s for s in self.scopes if s.id == sid)
            if sc.kind == "class":
                class_sid = sc.id
                break
            sid = sc.parent
        if class_sid is None:
            return
        self.bindings.append(
            (f"self.{target.attr}", getattr(target, "lineno", 0), class_sid,
             K_SELF_ATTR, type_name))

    # 循环 / with / except 绑定

    def visit_For(self, node: ast.For) -> None:
        self._bind_target(node.target, K_FOR)
        self.visit(node.iter)
        for s in node.body + node.orelse:
            self.visit(s)

    visit_AsyncFor = visit_For

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self._bind_target(node.target, K_COMPREHENSION)
        self.visit(node.iter)
        for cond in node.ifs:
            self.visit(cond)

    def _visit_comp(self, node, elts: list) -> None:
        # 推导式目标有自己的作用域（Python 语义），不泄漏到外层
        self._push_scope("comprehension", "comp")
        for gen in node.generators:
            self.visit(gen)
        for elt in elts:
            self.visit(elt)
        self._pop_scope()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comp(node, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comp(node, [node.elt])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comp(node, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comp(node, [node.key, node.value])

    def visit_With(self, node: ast.With) -> None:
        self._visit_with(node)

    visit_AsyncWith = visit_With

    def _visit_with(self, node) -> None:
        for item in node.items:
            self.visit(item.context_expr)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars, K_WITH)
        for s in node.body:
            self.visit(s)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self._bind(node.name, node, K_EXCEPT_AS, target=f"{self.rel}:{node.name}")
        for s in node.body:
            self.visit(s)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._push_scope("lambda", "lambda")
        for a in list(node.args.posonlyargs) + list(node.args.args) + \
                list(node.args.kwonlyargs):
            self._bind(a.arg, a, K_PARAM)
        if node.args.vararg:
            self._bind(node.args.vararg.arg, node.args.vararg, K_PARAM)
        if node.args.kwarg:
            self._bind(node.args.kwarg.arg, node.args.kwarg, K_PARAM)
        self.visit(node.body)
        self._pop_scope()

    # global / nonlocal

    def visit_Global(self, node: ast.Global) -> None:
        for name in node.names:
            self._bind(name, node, K_GLOBAL)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        for name in node.names:
            self._bind(name, node, K_NONLOCAL)

    # import

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound = alias.asname or alias.name.split(".")[0]
            target = alias.name if alias.asname else alias.name.split(".")[0]
            self._bind(bound, node, K_IMPORT_NAME, target=target)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        mod = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue  # 星号导入不解析（宁缺勿错）
            bound = alias.asname or alias.name
            self._bind(bound, node, K_IMPORT_FROM, target=f"{mod}.{alias.name}")

    # 使用点：Name / Attribute

    def visit_Name(self, node: ast.Name) -> None:
        self.usages.append((
            node.id, node.lineno, node.col_offset, self._cur.id,
            "load" if isinstance(node.ctx, ast.Load) else "store",
            1 if id(node) in self._call_nodes else 0, 0, ""))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        base = self._attr_bases.get(id(node), "")
        self.usages.append((
            node.attr, node.lineno, node.col_offset, self._cur.id, "load",
            1 if id(node) in self._call_nodes else 0, 1, base))
        self.visit(node.value)
