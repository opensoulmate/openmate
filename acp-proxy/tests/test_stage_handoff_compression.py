# -*- coding: utf-8 -*-
"""Pi #5 阶段交接压缩——业务摘要提示词模板 + compaction_pending 断点补偿。

①能力：ContextCompressor 支持自定义压缩指令（默认模板=业务摘要提示词模板，
"正文/台账以工作区文件为准，不在摘要中摘录"）；压缩失败置 compaction_pending，
下一轮断点补偿重压（force）。
③流程：插件阶段切换节点声明压缩要求（flow compress-handoff → 事件广播 →
SoulMateAgent.set_compression_instructions 注入）。
"""
import pytest

from agent.context_compression import (
    STAGE_HANDOFF_COMPRESSION_PROMPT,
    ContextCompressor,
    WorkState,
)


def _messages(n=30):
    return [{"role": "user" if i % 2 else "assistant",
             "content": f"第{i}条消息：做了一些工作，内容较长" * 5}
            for i in range(n)]


class TestBusinessSummaryTemplate:
    def test_template_wording(self):
        """业务摘要提示词模板固定语义（Pi #5 原文要求）。"""
        assert "工作区文件为准" in STAGE_HANDOFF_COMPRESSION_PROMPT
        assert "不在摘要中摘录" in STAGE_HANDOFF_COMPRESSION_PROMPT

    def test_to_prompt_injects_instructions(self):
        ws = WorkState(current_goal="写标书", current_step="第2章")
        p = ws.to_prompt(instructions=STAGE_HANDOFF_COMPRESSION_PROMPT)
        assert "## 压缩指令" in p
        assert "不在摘要中摘录" in p
        assert "## 当前目标" in p
        # 不传指令 → 无指令段（向后兼容）
        assert "## 压缩指令" not in ws.to_prompt()


class TestCustomInstructions:
    def test_compress_uses_default_template(self):
        c = ContextCompressor(max_tokens=100, keep_recent=2)
        out = c.compress(_messages(), session_id="s1")
        assert out is not None
        summary_msgs = [m for m in out if m.get("role") == "system"]
        assert any("不在摘要中摘录" in m["content"] for m in summary_msgs)

    def test_compress_custom_instructions(self):
        c = ContextCompressor(max_tokens=100, keep_recent=2)
        out = c.compress(_messages(), session_id="s1",
                         instructions="阶段二：只保留商务决策，技术细节看代码库")
        assert out is not None
        assert any("阶段二：只保留商务决策" in m["content"]
                   for m in out if m.get("role") == "system")


class TestCompactionPending:
    def test_failure_sets_pending_and_returns_none(self):
        """压缩失败→compaction_pending 置位 + 返回 None（不打断主流程）。"""
        c = ContextCompressor(max_tokens=100, keep_recent=2)

        def boom(*a, **k):
            raise RuntimeError("压缩器炸了")

        c._compress_impl = boom
        out = c.compress(_messages())
        assert out is None
        assert c.compaction_pending is True

    def test_pending_compensated_on_next_force(self):
        """断点补偿：下一轮 force=True 重压（不等阈值），成功即清标志。"""
        c = ContextCompressor(max_tokens=100, keep_recent=2)
        calls = {"n": 0}
        original = ContextCompressor._compress_impl

        def flaky(messages, session_id=None, instructions=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("首次失败")
            return original(c, messages, session_id=session_id,
                            instructions=instructions)

        c._compress_impl = flaky
        msgs = _messages()
        assert c.compress(msgs) is None and c.compaction_pending is True
        # 断点补偿：消息未过阈值也强制补压（force=True 语义由调用方传入）
        small = _messages(2)
        out = c.compress(small, force=True)
        assert out is not None and c.compaction_pending is False
        assert calls["n"] == 2

    def test_force_bypasses_threshold(self):
        c = ContextCompressor(max_tokens=100000, keep_recent=1)
        small = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}]
        # 未过阈值：不压
        assert c.compress(small) is small
        # force：照样压缩（补偿语义）
        out = c.compress(small, force=True)
        assert out is not None and len(out) <= 2


class TestSoulmateWiring:
    """soulmate_agent 压缩点接线（确定性源码检视，不猜不冒充——同 bootstrap_kpi 纪律）。"""

    def test_compress_point_wired(self):
        import inspect
        import agent.soulmate_agent as m
        src = inspect.getsource(m)
        assert "self._compaction_pending or self._ctx_compressor.should_compress" in src
        assert "force=_was_pending" in src                    # 断点补偿
        assert "instructions=self._compression_instructions" in src   # 自定义指令
        assert 'kind="compact"' in src                        # KPI 落账保留

    def test_setter_present(self):
        import agent.soulmate_agent as m
        assert hasattr(m.SoulMateAgent, "set_compression_instructions")
