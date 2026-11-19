"""P0 技能命中记账（skill_feedback）测试。

覆盖：三态记账（有用/没用/unknown fail-closed）、情况语句沉淀（去重/截长/封顶）、
反馈增益（Laplace 平滑+界）、证据判定、锚词（CJK 滑窗+通用停用词过滤）、
持久化（原子写/损坏文件从零开始/新实例读同一文件）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skill_feedback import (  # noqa: E402
    OUTCOME_NOT_USEFUL,
    OUTCOME_UNKNOWN,
    OUTCOME_USEFUL,
    SkillFeedbackLedger,
    anchor_tokens,
    ngram_tokenize,
)


def make_ledger(tmp_path, name="ledger.json") -> SkillFeedbackLedger:
    return SkillFeedbackLedger(path=str(tmp_path / name))


class TestRecordHit:
    def test_trichotomy_counts(self, tmp_path):
        ledger = make_ledger(tmp_path)
        ledger.record_hit("s1", "帮我处理发票报销", outcome=OUTCOME_USEFUL)
        ledger.record_hit("s1", "帮我处理发票报销", outcome=OUTCOME_NOT_USEFUL)
        ledger.record_hit("s1", "帮我处理发票报销", outcome=OUTCOME_UNKNOWN)
        e = ledger.entry_for("s1")
        assert e["hits"] == 3
        assert e["useful"] == 1
        assert e["not_useful"] == 1
        assert e["unknown"] == 1
        assert e["last_outcome"] == "unknown"

    def test_invalid_outcome_fail_closed_to_unknown(self, tmp_path):
        ledger = make_ledger(tmp_path)
        ledger.record_hit("s1", "任务", outcome="完全瞎编的状态")
        e = ledger.entry_for("s1")
        assert e["unknown"] == 1
        assert e["useful"] == 0 and e["not_useful"] == 0

    def test_empty_skill_id_no_record(self, tmp_path):
        ledger = make_ledger(tmp_path)
        assert ledger.record_hit("", "任务") == {}
        assert ledger.snapshot()["skills_tracked"] == 0

    def test_situation_dedup_and_cap(self, tmp_path):
        ledger = make_ledger(tmp_path)
        ledger.record_hit("s1", "帮我处理发票报销", outcome=OUTCOME_USEFUL)
        ledger.record_hit("s1", "帮我处理发票报销", outcome=OUTCOME_USEFUL)  # 同句去重
        assert ledger.situations_for("s1") == ["帮我处理发票报销"]
        for i in range(30):
            ledger.record_hit("s1", f"第{i}类别的处理任务", outcome=OUTCOME_USEFUL)
        assert len(ledger.situations_for("s1")) == 20  # MAX_SITUATIONS_PER_SKILL

    def test_situation_strips_whitespace_and_truncates(self, tmp_path):
        ledger = make_ledger(tmp_path)
        ledger.record_hit("s1", "  a   b  " + "长" * 200)
        (s,) = ledger.situations_for("s1")
        assert s == "a b " + "长" * 76  # 压空白后 MAX_SITUATION_LEN=80 截长


class TestBoostAndEvidence:
    def test_no_entry_boost_zero(self, tmp_path):
        assert make_ledger(tmp_path).boost_for("ghost") == 0.0

    def test_useful_boosts_positive_not_useful_negative(self, tmp_path):
        ledger = make_ledger(tmp_path)
        for _ in range(5):
            ledger.record_hit("good", "任务A", outcome=OUTCOME_USEFUL)
        for _ in range(5):
            ledger.record_hit("bad", "任务B", outcome=OUTCOME_NOT_USEFUL)
        assert ledger.boost_for("good") > 0
        assert ledger.boost_for("bad") < 0

    def test_boost_bounded(self, tmp_path):
        ledger = make_ledger(tmp_path)
        for _ in range(50):
            ledger.record_hit("s", "任务", outcome=OUTCOME_USEFUL)
        b = ledger.boost_for("s")
        assert -0.25 <= b <= 0.35

    def test_unknown_outcome_dilutes_but_does_not_judge(self, tmp_path):
        ledger = make_ledger(tmp_path)
        ledger.record_hit("s", "任务", outcome=OUTCOME_USEFUL)
        ledger.record_hit("s", "任务", outcome=OUTCOME_UNKNOWN)
        # unknown 计 hit（摊薄分母）但不进成败判定
        assert ledger.entry_for("s")["hits"] == 2
        assert ledger.entry_for("s")["unknown"] == 1
        assert ledger.boost_for("s") > 0
        # 全 unknown = 无判定 = 零增益（不猜）
        ledger.record_hit("u", "任务", outcome=OUTCOME_UNKNOWN)
        assert ledger.boost_for("u") == 0.0

    def test_has_evidence(self, tmp_path):
        ledger = make_ledger(tmp_path)
        assert not ledger.has_evidence("s")
        ledger.record_hit("s", "帮我处理发票报销", outcome=OUTCOME_USEFUL)
        assert ledger.has_evidence("s")


class TestAnchors:
    def test_cjk_ngram_overlap_across_phrasings(self):
        # 换说法最大连续段不同，滑窗 bigram 才能交集
        a = ngram_tokenize("帮我把销售数据整理成表格")
        b = ngram_tokenize("请把销售数据做成表")
        assert set(a) & set(b)  # "销售"/"数据" 等

    def test_generic_tokens_not_anchors(self):
        tokens = anchor_tokens("帮我一下看看这个")
        assert "帮我" not in tokens
        assert "一下" not in tokens

    def test_anchor_overlap_shared_content_only(self, tmp_path):
        ledger = make_ledger(tmp_path)
        ledger.record_hit("s1", "请帮我解析这份PDF文档的内容", outcome=OUTCOME_USEFUL)
        shared = ledger.anchor_overlap("s1", "今天天气怎么样帮我查一下北京天气")
        assert shared == []  # 只有"帮我"级虚词重合=不命中
        shared2 = ledger.anchor_overlap("s1", "帮我解析PDF文档要点")
        assert any(t in ("pdf", "文档", "解析") for t in shared2)


class TestPersistence:
    def test_reload_from_disk(self, tmp_path):
        path = str(tmp_path / "ledger.json")
        SkillFeedbackLedger(path=path).record_hit("s1", "任务", outcome=OUTCOME_USEFUL)
        fresh = SkillFeedbackLedger(path=path)
        assert fresh.entry_for("s1")["hits"] == 1

    def test_corrupted_file_starts_fresh(self, tmp_path):
        path = tmp_path / "ledger.json"
        path.write_text("{broken json", encoding="utf-8")
        ledger = SkillFeedbackLedger(path=str(path))
        assert ledger.snapshot()["skills_tracked"] == 0
        ledger.record_hit("s1", "任务")
        assert ledger.entry_for("s1")["hits"] == 1

    def test_snapshot_totals(self, tmp_path):
        ledger = make_ledger(tmp_path)
        ledger.record_hit("s1", "任务", outcome=OUTCOME_USEFUL)
        ledger.record_hit("s2", "任务", outcome=OUTCOME_NOT_USEFUL)
        snap = ledger.snapshot()
        assert snap["skills_tracked"] == 2
        assert snap["hits_total"] == 2
        assert snap["useful_total"] == 1
        assert snap["not_useful_total"] == 1
