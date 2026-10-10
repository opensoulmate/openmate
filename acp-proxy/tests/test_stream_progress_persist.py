# -*- coding: utf-8 -*-
"""路线图0.1 简单对话流式路径增量落库——"生成中任意时刻切页/重载不丢已生成内容"。

背景（用户实测08:22切页丢前段流式内容的根因）：simple chat的full_response流式
积累只推客户端不落库，生成中途被杀→agent_messages里什么都没有。任务管线已有
per-step落库（_save_progress），此切片补齐simple路径三层：

1. 节流触发：流式积累处按节奏调用_save_progress（每积累≥800字或距上次≥2秒取先到）；
2. 接管：最终回复由_save_message_final原地接管同一行，不重复插入；
3. 无重复/不串行：新回合开始_reset_progress_row作废上回合遗留指针，旧行部分内容
   原样保留（恢复语义），新回合自建新行——绝不UPDATE旧行覆盖丢失。
"""

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import agent.soulmate_agent as sa
from agent.soulmate_agent import SoulMateAgent

SID = "om-stream"


def run(coro):
    return asyncio.run(coro)


def _bare_agent():
    """裸SoulMateAgent实例（__new__绕过重型__init__）：只带增量落库所需面。"""
    a = SoulMateAgent.__new__(SoulMateAgent)
    a._db_path = Path(tempfile.mktemp(suffix=".db"))
    db = sqlite3.connect(a._db_path)
    db.execute(
        "CREATE TABLE agent_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT,"
        " timestamp REAL, attachments TEXT, parent_message_id INTEGER, metadata TEXT)"
    )
    db.execute(
        "CREATE TABLE agent_sessions (id TEXT PRIMARY KEY,"
        " message_count INTEGER DEFAULT 0, last_activity_at REAL)"
    )
    db.execute("INSERT INTO agent_sessions (id) VALUES (?)", (SID,))
    db.commit()
    db.close()
    a._client = None
    a.sessions = {SID: {}}
    return a


def _rows(agent):
    db = sqlite3.connect(agent._db_path)
    try:
        return db.execute(
            "SELECT id, role, content, metadata FROM agent_messages"
            " WHERE session_id=? ORDER BY id", (SID,)
        ).fetchall()
    finally:
        db.close()


class TestProgressSaverThrottle:
    def test_no_save_under_thresholds(self, monkeypatch):
        # 双阈值以下不落库（避免每chunk一写）
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_CHARS", 800)
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_INTERVAL_S", 2.0)
        agent = _bare_agent()
        saver = agent._progress_saver(SID)
        saver("短文本")
        saver("短文本追加一点")
        assert _rows(agent) == []

    def test_chars_threshold_saves_and_updates_same_row(self, monkeypatch):
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_CHARS", 10)
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_INTERVAL_S", 2.0)
        agent = _bare_agent()
        saver = agent._progress_saver(SID)
        saver("x" * 10)          # 10字 → 触发首落库
        rows = _rows(agent)
        assert len(rows) == 1 and rows[0][2] == "x" * 10
        assert json.loads(rows[0][3]).get("progress") is True
        saver("x" * 15)          # 新增5字 < 10 → 不落
        assert _rows(agent)[0][2] == "x" * 10
        saver("x" * 25)          # 新增15字 ≥ 10 → 原地更新同一行
        rows = _rows(agent)
        assert len(rows) == 1 and rows[0][2] == "x" * 25

    def test_time_threshold_triggers(self, monkeypatch):
        # 时间阈值：积累不到字数阈值但距上次≥2秒也落库
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_CHARS", 1000)
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_INTERVAL_S", 2.0)

        class FakeClock:
            now = 100.0

            @classmethod
            def time(cls):
                return cls.now

        monkeypatch.setattr(sa, "time", FakeClock)
        agent = _bare_agent()
        saver = agent._progress_saver(SID)
        FakeClock.now = 100.5
        saver("短")               # 0.5秒 → 不落
        assert _rows(agent) == []
        FakeClock.now = 102.6
        saver("短+长尾")          # 距上次2.1秒 → 触发
        rows = _rows(agent)
        assert len(rows) == 1 and rows[0][2] == "短+长尾"

    def test_first_chunk_not_saved_immediately(self, monkeypatch):
        # 创建后立刻来首个chunk（<2秒且<字数阈值）不落库——初始时间戳=创建时刻
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_CHARS", 800)
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_INTERVAL_S", 2.0)
        agent = _bare_agent()
        saver = agent._progress_saver(SID)
        saver("第一段内容")
        assert _rows(agent) == []


class TestProgressRowLifecycle:
    def test_midstream_interrupt_leaves_partial_row(self, monkeypatch):
        # 模拟生成中途被杀：只走节流落库，不走最终保存
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_CHARS", 5)
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_INTERVAL_S", 2.0)
        agent = _bare_agent()
        saver = agent._progress_saver(SID)
        saver("已生成的第一段内容")
        saver("已生成的第一段内容，还有第二段")
        rows = _rows(agent)
        assert len(rows) == 1
        assert rows[0][1] == "assistant"
        assert "第二段" in rows[0][2]
        assert json.loads(rows[0][3]).get("progress") is True

    def test_final_takeover_same_row_no_duplicate(self, monkeypatch):
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_CHARS", 5)
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_INTERVAL_S", 2.0)
        agent = _bare_agent()
        saver = agent._progress_saver(SID)
        saver("部分内容先行落库")   # 8字 ≥ 5 → 触发
        pid = agent.sessions[SID]["_progress_id"]
        final = "部分内容先行落库，以及最终完整回复全文。"
        agent._save_message_final(SID, final)
        rows = _rows(agent)
        assert len(rows) == 1, "最终回复必须接管同一行，不得重复插入"
        assert rows[0][0] == pid
        assert rows[0][2] == final
        assert "_progress_id" not in agent.sessions[SID]

    def test_stale_progress_row_not_hijacked_by_next_turn(self, monkeypatch):
        # 上回合中途被杀残留_progress_id → 新回合必须自建新行，旧行内容不被覆盖
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_CHARS", 5)
        monkeypatch.setattr(sa, "_PROGRESS_SAVE_INTERVAL_S", 2.0)
        agent = _bare_agent()
        saver1 = agent._progress_saver(SID)
        saver1("上回合已生成的部分")
        old_pid = agent.sessions[SID]["_progress_id"]
        old_content = _rows(agent)[0][2]
        # ── 新回合开始（_prompt_inner接线点等价调用）──
        agent._reset_progress_row(SID)
        assert "_progress_id" not in agent.sessions[SID]
        saver2 = agent._progress_saver(SID)
        saver2("本回合新内容")
        new_pid = agent.sessions[SID]["_progress_id"]
        assert new_pid != old_pid, "新回合必须自建新行，不得复用上回合残留指针"
        agent._save_message_final(SID, "本回合新内容收尾")
        rows = _rows(agent)
        assert len(rows) == 2, "旧行保留+新行接管，共两行"
        by_id = {r[0]: r[2] for r in rows}
        assert by_id[old_pid] == old_content, "旧行部分内容必须原样保留（恢复语义）"
        assert by_id[new_pid] == "本回合新内容收尾"

    def test_reset_without_session_no_crash(self):
        agent = _bare_agent()
        agent._reset_progress_row("no-such-session")  # 不炸即可

    def test_final_without_progress_row_inserts_new(self):
        # 无增量行时最终保存照常新插（向后兼容）
        agent = _bare_agent()
        agent._save_message_final(SID, "普通回复")
        rows = _rows(agent)
        assert len(rows) == 1 and rows[0][2] == "普通回复"


class TestWiring:
    def _src(self):
        return (Path(__file__).parent.parent / "agent" / "soulmate_agent.py").read_text(
            encoding="utf-8"
        )

    def test_wiring_stream_progress_only_on_simple_path(self):
        src = self._src()
        # simple任务调用点唯一开启；任务管线（step_messages/fix_messages）保持per-step落库
        assert src.count("stream_progress=True") == 1
        assert "user_text=user_text, traj_seg_id=_traj_seg,\n                    stream_progress=True" in src

    def test_wiring_saver_called_at_accumulation_and_tail(self):
        src = self._src()
        assert src.count("_stream_save(full_response)") == 2  # 流式积累 + round尾flush
        assert "_stream_save = self._progress_saver(session_id) if stream_progress else None" in src

    def test_wiring_prompt_inner_resets_progress_row(self):
        src = self._src()
        assert src.count("self._reset_progress_row(session_id)") == 1
        # 必须在_prompt_inner内（回合真正开始处），不在prompt()的choice/排队早退路径
        inner_at = src.index("async def _prompt_inner")
        reset_at = src.index("self._reset_progress_row(session_id)")
        assert inner_at < reset_at

    def test_wiring_task_pipeline_per_step_save_kept(self):
        src = self._src()
        assert "self._save_progress(session_id, self._plan_visible_text(plan))" in src
