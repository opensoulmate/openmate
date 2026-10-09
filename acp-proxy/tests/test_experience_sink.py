# -*- coding: utf-8 -*-
"""路线图 1.3 可复用经验沉淀（带去重审计）契约测试。

覆盖：
1. created：成功回合+工具调用 → 经验入库（content/指纹/tags/审计字段）
2. duplicate（精确）：同一经验再记录 → times_seen+1，不新增行
3. duplicate（近似）：措辞微调（ratio>=0.85）→ 判重累计，不新增行
4. distinct：不同经验 → 独立入库
5. skipped：失败回合 / 纯闲聊短回复 → 不沉淀
6. persistent_memory 同步：仅 created 时追加一行，duplicate 不重复追加
7. 审计：audit() 统计 total/seen/repeated；list_experiences 按 times_seen 排序
8. 接线护栏：soulmate_agent 真实路径含 ExperienceSink 初始化 + 任务完成钩子
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.experience_sink import ExperienceSink, _fingerprint, _normalize  # noqa: E402
from agent.persistent_memory import PersistentMemory  # noqa: E402

SUCCESS_RESP = (
    "问题已解决。经验：SQLite嵌套连接会死锁，外层事务未提交时禁开内层连接写库。"
    "修复方法是合并到单连接执行。"
)
TOOLS = [{"function": {"name": "terminal"}}, {"function": {"name": "patch"}}]


def _sink(tmp_path):
    return ExperienceSink(
        db_path=str(tmp_path / "exp.db"),
        persistent_memory=PersistentMemory(path=str(tmp_path / "MEMORY.md")),
    )


# ── 1. created ──────────────────────────────────────────────────

def test_record_creates_experience(tmp_path):
    s = _sink(tmp_path)
    r = s.record_experience(
        "数据库死锁怎么修", SUCCESS_RESP, tool_calls=TOOLS, session_id="sess-1"
    )
    assert r["action"] == "created", r
    exps = s.list_experiences()
    assert len(exps) == 1
    e = exps[0]
    assert "SQLite嵌套连接会死锁" in e["content"]
    assert e["times_seen"] == 1
    assert e["source_session"] == "sess-1"
    assert json.loads(e["tags"]) == ["terminal", "patch"]
    assert e["fingerprint"] == _fingerprint(e["content"])


# ── 2. duplicate（精确）─────────────────────────────────────────

def test_exact_duplicate_bumps_times_seen(tmp_path):
    s = _sink(tmp_path)
    r1 = s.record_experience("数据库死锁怎么修", SUCCESS_RESP, tool_calls=TOOLS)
    r2 = s.record_experience("又是这个死锁问题", SUCCESS_RESP, tool_calls=TOOLS)
    assert r1["action"] == "created"
    assert r2["action"] == "duplicate"
    assert r2["id"] == r1["id"]
    exps = s.list_experiences()
    assert len(exps) == 1
    assert exps[0]["times_seen"] == 2


# ── 3. duplicate（近似）─────────────────────────────────────────

def test_near_duplicate_detected(tmp_path):
    s = _sink(tmp_path)
    r1 = s.record_experience(
        "数据库死锁", SUCCESS_RESP, tool_calls=TOOLS
    )
    tweaked = SUCCESS_RESP.replace("外层事务未提交时禁开内层连接写库", "外层事务没提交时禁止开内层连接写库")
    r2 = s.record_experience("数据库死锁又来了", tweaked, tool_calls=TOOLS)
    assert r1["action"] == "created"
    assert r2["action"] == "duplicate", r2
    assert len(s.list_experiences()) == 1


# ── 4. distinct ─────────────────────────────────────────────────

def test_distinct_experiences_both_created(tmp_path):
    s = _sink(tmp_path)
    r1 = s.record_experience("死锁问题", SUCCESS_RESP, tool_calls=TOOLS)
    other = "经验：前端build后必须kill旧server再start，否则stale build混用chunk导致白屏。"
    r2 = s.record_experience("前端白屏", other, tool_calls=TOOLS)
    assert r1["action"] == "created"
    assert r2["action"] == "created"
    assert len(s.list_experiences()) == 2


# ── 5. skipped ──────────────────────────────────────────────────

def test_failed_turn_skipped(tmp_path):
    s = _sink(tmp_path)
    r = s.record_experience(
        "任务", "错误: 工具 `terminal` 调用失败 (第1次尝试)", tool_calls=TOOLS
    )
    assert r["action"] == "skipped"
    assert r["reason"] == "turn_failed"
    assert s.list_experiences() == []


def test_smalltalk_skipped(tmp_path):
    s = _sink(tmp_path)
    r = s.record_experience("你好", "你好！有什么可以帮你的吗？", tool_calls=[])
    assert r["action"] == "skipped"
    assert s.list_experiences() == []


# ── 6. persistent_memory 同步 ───────────────────────────────────

def test_persistent_memory_sync_only_on_create(tmp_path):
    s = _sink(tmp_path)
    s.record_experience("死锁", SUCCESS_RESP, tool_calls=TOOLS)
    s.record_experience("死锁again", SUCCESS_RESP, tool_calls=TOOLS)
    mem = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert mem.count("经验:") == 1  # duplicate 不重复追加
    assert "SQLite嵌套连接会死锁" in mem


# ── 7. 审计 ─────────────────────────────────────────────────────

def test_audit_and_list_ordering(tmp_path):
    s = _sink(tmp_path)
    s.record_experience("死锁", SUCCESS_RESP, tool_calls=TOOLS)
    s.record_experience("死锁", SUCCESS_RESP, tool_calls=TOOLS)
    s.record_experience("死锁", SUCCESS_RESP, tool_calls=TOOLS)
    other = "经验：Next.js的allowedDevOrigins缺IP会导致LAN访问白屏，要在配置里补。"
    s.record_experience("LAN白屏", other, tool_calls=TOOLS)
    a = s.audit()
    assert a["total_unique"] == 2
    assert a["total_seen"] == 4
    assert a["repeated_experiences"] == 1
    exps = s.list_experiences()
    assert exps[0]["times_seen"] == 3  # 按 times_seen 降序


# ── 8. 接线护栏 ─────────────────────────────────────────────────

def test_soulmate_agent_wiring():
    src = Path(__file__).resolve().parents[1] / "agent" / "soulmate_agent.py"
    text = src.read_text(encoding="utf-8")
    assert "from agent.experience_sink import ExperienceSink" in text
    assert "self._experience_sink = ExperienceSink(" in text
    assert "self._experience_sink.record_experience(" in text


def test_normalize_and_fingerprint_stable():
    a = _fingerprint("经验：SQLite 死锁，禁嵌套连接！")
    b = _fingerprint("经验: sqlite死锁 禁嵌套连接")
    assert a == b
    assert _normalize("  A ，B。 ") == "ab"
