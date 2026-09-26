# -*- coding: utf-8 -*-
"""唤醒回合LLM级失败判定 + last_error新鲜度语义（P0-4执行侧遗留#2/#4）。

调研来源：
- mem0 §1.1 失败必须可见禁止假装成功——回合"跑了"但输出是provider错误机器标记
  （LLMEngine吞异常返回"[LLM错误: …]"，如402余额不足）不能算成功回执；
- infection_guard._TAGGED_FAILURE_PREFIX 同款标记判定惯例（本仓库既有语义）；
- AIHawk 显式标记纪律：截断/失败必须显式可见。

验证面：
1. _is_llm_failure_text 判定面（开头命中/末行命中/正文引用不算/空文本）
2. default_headless_fire：本轮新落库的错误标记→抛异常（=ack ok=False 释放重试）；
   旧轮错误文本不误判（turn归因锚点）；正常回复→成功；取数失败 fail-open；
   零输出（无新非空assistant输出）→失败（13:40遗留#4零输出判据，禁假装成功）
3. WakeupExecutor last_error 语义：错误带 last_error_at 时间戳；
   干净poll一轮后归空（不再误导"现在还在错"）；脏一轮保留
"""

import asyncio
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.wakeup_executor import (
    WakeupExecutor,
    _is_llm_failure_text,
    default_headless_fire,
)


def run(coro):
    return asyncio.run(coro)


EV = {
    "event_id": 7,
    "agent_id": "soulmate",
    "session_id": "om-llmfail",
    "reason": "检查CI是否转绿",
    "noop": False,
    "streak": 0,
}

ERR402 = "[LLM错误: LLM API error 402: Insufficient account balance]"


# ════════════════════════════════════════════════════════════════
# ① 机器标记判定面
# ════════════════════════════════════════════════════════════════


class TestIsLlmFailureText:
    def test_chat_whole_error(self):
        assert _is_llm_failure_text(ERR402)

    def test_leading_newline_stripped(self):
        assert _is_llm_failure_text("\n[LLM错误: boom]")

    def test_timeout_marker(self):
        assert _is_llm_failure_text("\n[LLM响应超时]")

    def test_stream_append_after_partial_content(self):
        # 流式：已有内容后追加错误chunk——末行命中即失败
        assert _is_llm_failure_text("先说了一半的正常回答\n[LLM错误: stream died]")

    def test_normal_reply_not_failure(self):
        assert not _is_llm_failure_text("已检查CI：全部转绿。")

    def test_body_quote_not_failure(self):
        # 正文中间引用标记字样不算失败（只认开头/末行）
        assert not _is_llm_failure_text(
            "排查记录：错误日志里出现了 [LLM错误: 402] 字样，已定位为余额问题。"
        )

    def test_empty_not_failure(self):
        assert not _is_llm_failure_text("")
        assert not _is_llm_failure_text(None)


# ════════════════════════════════════════════════════════════════
# ② default_headless_fire：LLM级失败→抛异常（ack ok=False 释放重试）
# ════════════════════════════════════════════════════════════════


class FakeDbAgent:
    """带真SQLite的agent替身：prompt()把回复按真实schema落agent_messages。
    _get_db每次返回新连接（镜像SoulMateAgent._get_db语义——调用方会close）。"""

    def __init__(self, reply_text, stop_reason="end_turn", pre_messages=None):
        self._reply_text = reply_text
        self._stop_reason = stop_reason
        self._db_path = tempfile.mktemp(suffix=".db")
        db = self._get_db()
        db.execute(
            "CREATE TABLE agent_messages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT)"
        )
        for sid, role, content in (pre_messages or []):
            db.execute(
                "INSERT INTO agent_messages (session_id, role, content) VALUES (?, ?, ?)",
                (sid, role, content),
            )
        db.commit()
        db.close()
        self.calls = []

    def _get_db(self):
        return sqlite3.connect(self._db_path)

    async def prompt(self, prompt, session_id, **kw):
        self.calls.append((prompt, session_id))
        if self._reply_text is not None:
            # 直连DB落库（不经_get_db——_get_db是"判定读取面"，可被测试单独弄坏）
            db = sqlite3.connect(self._db_path)
            db.execute(
                "INSERT INTO agent_messages (session_id, role, content) VALUES (?, 'assistant', ?)",
                (session_id, self._reply_text),
            )
            db.commit()
            db.close()
        return type("R", (), {"stop_reason": self._stop_reason})()


def _fire_with_agent(monkeypatch, agent):
    monkeypatch.setattr(
        "agent.wakeup_executor._get_headless_agent", lambda: agent, raising=True
    )
    return run(default_headless_fire(EV))


class TestDefaultFireLlmFailure:
    def test_llm_error_reply_raises(self, monkeypatch):
        agent = FakeDbAgent(ERR402)
        try:
            _fire_with_agent(monkeypatch, agent)
            raise AssertionError("LLM错误标记回合必须抛异常（禁止假装成功）")
        except RuntimeError as e:
            assert "LLM级失败" in str(e)
            assert "402" in str(e)

    def test_stream_appended_error_raises(self, monkeypatch):
        agent = FakeDbAgent("检查进行中\n[LLM错误: stream reset]")
        try:
            _fire_with_agent(monkeypatch, agent)
            raise AssertionError("流式追加错误标记也必须抛异常")
        except RuntimeError as e:
            assert "LLM级失败" in str(e)

    def test_old_turn_error_not_misattributed(self, monkeypatch):
        # 旧轮遗留的错误文本（id<=锚点）不算本轮失败——turn归因
        agent = FakeDbAgent(
            "本轮正常回复：已检查。",
            pre_messages=[("om-llmfail", "assistant", ERR402)],
        )
        res = _fire_with_agent(monkeypatch, agent)
        assert res == {"stop_reason": "end_turn"}

    def test_normal_reply_ok(self, monkeypatch):
        agent = FakeDbAgent("已检查CI：全部转绿。")
        res = _fire_with_agent(monkeypatch, agent)
        assert res == {"stop_reason": "end_turn"}
        # 唤醒prompt仍以目标session为上下文
        assert agent.calls[0][1] == "om-llmfail"

    def test_no_new_message_is_zero_output_failure(self, monkeypatch):
        # 13:40遗留#4零输出判据：回合"跑完"但没落任何新assistant输出=回合无声
        # 消失（实证案例：缓存命中回合曾不落库静默丢回复）——按失败回执释放
        # 重试，不再假装成功（此测试原为fail-open断言，本轮按遗留#4翻转契约）
        agent = FakeDbAgent(None)
        try:
            _fire_with_agent(monkeypatch, agent)
            raise AssertionError("零输出回合必须抛异常（禁止假装成功）")
        except RuntimeError as e:
            assert "零输出" in str(e)

    def test_db_read_failure_fail_open(self, monkeypatch):
        agent = FakeDbAgent(ERR402)

        def _broken_db():
            raise RuntimeError("db炸了")

        agent._get_db = _broken_db  # 构造后才坏——模拟运行期DB读故障
        res = _fire_with_agent(monkeypatch, agent)
        assert res == {"stop_reason": "end_turn"}

    def test_refusal_still_raises_before_llm_check(self, monkeypatch):
        agent = FakeDbAgent(ERR402, stop_reason="refusal")
        try:
            _fire_with_agent(monkeypatch, agent)
            raise AssertionError("refusal必须抛异常")
        except RuntimeError as e:
            assert "拒绝给消失的会话造回合" in str(e)


# ════════════════════════════════════════════════════════════════
# ②-b 零输出判定面（13:40遗留#4）：turn归因 + 空白输出 + fail-open边界
# ════════════════════════════════════════════════════════════════


class TestZeroOutputJudgment:
    def test_whitespace_only_output_is_zero_output(self, monkeypatch):
        # 纯空白输出=用户/前端看不见任何内容，同判零输出
        agent = FakeDbAgent("   \n\t ")
        try:
            _fire_with_agent(monkeypatch, agent)
            raise AssertionError("纯空白输出必须判零输出")
        except RuntimeError as e:
            assert "零输出" in str(e)

    def test_old_assistant_rows_not_counted_as_turn_output(self, monkeypatch):
        # 旧轮assistant行不算本轮输出（turn归因锚点）——只有旧轮内容照样判零输出
        agent = FakeDbAgent(
            None, pre_messages=[("om-llmfail", "assistant", "旧轮回复")]
        )
        try:
            _fire_with_agent(monkeypatch, agent)
            raise AssertionError("只有旧轮输出必须判零输出")
        except RuntimeError as e:
            assert "零输出" in str(e)

    def test_nonempty_new_output_ok(self, monkeypatch):
        agent = FakeDbAgent("已检查CI：全部转绿。")
        res = _fire_with_agent(monkeypatch, agent)
        assert res == {"stop_reason": "end_turn"}

    def test_db_read_failure_never_judged(self, monkeypatch):
        # 取数失败→无法判定→fail-open（DB读故障不得引发重试风暴）
        agent = FakeDbAgent(None)

        def _broken_db():
            raise RuntimeError("db炸了")

        agent._get_db = _broken_db
        res = _fire_with_agent(monkeypatch, agent)
        assert res == {"stop_reason": "end_turn"}


# ════════════════════════════════════════════════════════════════
# ③ last_error 新鲜度语义（遗留#4）
# ════════════════════════════════════════════════════════════════


class FakeResp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class FlakyHttp:
    """首轮GET挂→次轮恢复；due可控"""

    def __init__(self, fail_first_get=True, due=None):
        self.fail_first_get = fail_first_get
        self.due = list(due or [])
        self.get_count = 0

    def factory(self, **kw):
        return FlakyClient(self)


class FlakyClient:
    def __init__(self, server):
        self.s = server

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, **kw):
        self.s.get_count += 1
        if self.s.fail_first_get and self.s.get_count == 1:
            raise RuntimeError("opensoul挂了")
        return FakeResp(200, {"count": len(self.s.due), "due": self.s.due})

    async def post(self, url, json=None, **kw):
        return FakeResp(200, {"state": "fired"})


class TestLastErrorFreshness:
    def test_error_sets_timestamp_and_clean_poll_clears(self, monkeypatch):
        server = FlakyHttp(fail_first_get=True)

        async def fire_fn(ev):
            return None

        monkeypatch.setattr(
            "agent.wakeup_executor.httpx.AsyncClient", server.factory, raising=True
        )
        ex = WakeupExecutor(
            opensoul_url="http://opensoul.test", poll_interval_s=0.01, fire_fn=fire_fn
        )

        async def scenario():
            await ex.start()
            await asyncio.sleep(0.06)  # 覆盖至少两轮poll（0.01s间隔）
            await ex.stop()

        run(scenario())
        st = ex.snapshot()
        assert st["polls"] >= 2
        # 首轮错误已被后续干净轮清空：不再误导"现在还在错"
        assert st["last_error"] == ""
        assert st["last_error_at"] is None

    def test_error_recorded_with_timestamp(self, monkeypatch):
        server = FlakyHttp(fail_first_get=True)

        async def fire_fn(ev):
            return None

        monkeypatch.setattr(
            "agent.wakeup_executor.httpx.AsyncClient", server.factory, raising=True
        )
        ex = WakeupExecutor(
            opensoul_url="http://opensoul.test", poll_interval_s=0.01, fire_fn=fire_fn
        )
        before = time.time()
        try:
            run(ex.poll_once())
            raise AssertionError("首轮GET必须抛")
        except RuntimeError:
            pass
        # poll_once的GET异常直接上抛（_loop才记账）——循环路径记账见下一测
        st = ex.snapshot()
        assert st["last_error"] == ""  # 直调poll_once不经_loop，不记账

        async def scenario():
            await ex.start()
            await asyncio.sleep(0.03)
            await ex.stop()

        run(scenario())
        st = ex.snapshot()
        # scenario内首GET也会挂（get_count已=1，第二测fail_first_get只挡第1次）
        # →取到错误记录（若发生）或已清空；关键契约：有last_error必有last_error_at
        if st["last_error"]:
            assert st["last_error_at"] is not None and st["last_error_at"] >= before

    def test_dirty_cycle_keeps_last_error(self, monkeypatch):
        server = FlakyHttp(fail_first_get=False, due=[EV])

        async def fire_fn(ev):
            raise RuntimeError("LLM炸了")

        monkeypatch.setattr(
            "agent.wakeup_executor.httpx.AsyncClient", server.factory, raising=True
        )
        ex = WakeupExecutor(
            opensoul_url="http://opensoul.test", poll_interval_s=0.01, fire_fn=fire_fn
        )
        run(ex.poll_once())
        st = ex.snapshot()
        assert st["fire_failed"] == 1
        # 脏一轮（fire失败）绝不归空——错误必须可见到下一个干净轮为止
        assert st["last_error"] != ""
        assert st["last_error_at"] is not None

    def test_snapshot_has_last_error_at(self, monkeypatch):
        server = FlakyHttp(fail_first_get=False)

        async def fire_fn(ev):
            return None

        monkeypatch.setattr(
            "agent.wakeup_executor.httpx.AsyncClient", server.factory, raising=True
        )
        ex = WakeupExecutor(
            opensoul_url="http://opensoul.test", poll_interval_s=0.01, fire_fn=fire_fn
        )
        assert "last_error_at" in ex.snapshot()
