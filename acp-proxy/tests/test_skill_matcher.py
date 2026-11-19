"""P0 任务→技能混合匹配（skill_matcher）测试。

覆盖：混合打分（触发/BM25/语义/反馈四分量可观测）、放行三路（trigger /
semantic / anchor+sim 佐证）、"Ranking is not admission"（纯描述匹配排前
也不放行——pitfall 30 语义逐条保留）、负对照防线（无证据技能/通用词/普通
问题零误放行）、CJK 滑窗分词、供应商降级留痕（No silent downgrade）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skill_feedback import SkillFeedbackLedger  # noqa: E402
from agent.skill_matcher import SkillMatcher  # noqa: E402
from agent.skill_semantic import EmbedResult, cosine  # noqa: E402


class StubEmbedder:
    """可控向量嵌入器：text→vec 映射，未命中给零向量（cos=0）。"""

    def __init__(self, vecs: dict, provider: str = "opensoul", degraded: str | None = None):
        self.vecs = vecs
        self.provider = provider
        self.degraded = degraded

    def embed(self, texts):
        vectors = [self.vecs.get(t, [0.0, 0.0, 0.0]) for t in texts]
        return EmbedResult(vectors, self.provider, self.degraded)


SKILL_PDF = {
    "id": "s-pdf", "name": "pdf-skill", "content": "用read_file解析PDF",
    "triggers": ["pdf", "PDF解析"], "code_template": "read_file(path)",
}
SKILL_INVOICE = {
    "id": "s-inv", "name": "invoice-skill", "content": "发票OCR→填单→提交报销",
    "triggers": ["发票报销", "ocr发票"], "description": "发票OCR识别并填报销单",
}
TASK1 = "帮我把这个季度的发票报销单整理一下提交财务"
TASK2 = "请将本季度发票整理成报销单并交给财务部门"  # 同类任务换说法


def make_matcher(tmp_path, skills, ledger=None, embedder=None, top_k=3):
    import types

    mgr = types.SimpleNamespace(search_skills=lambda q, limit=50: list(skills)[:limit])
    return SkillMatcher(
        skill_manager=mgr,
        ledger=ledger or SkillFeedbackLedger(path=str(tmp_path / "ledger.json")),
        embedder=embedder,
        top_k=top_k,
    )


class TestTriggerPath:
    def test_trigger_admit_reason(self, tmp_path):
        m = make_matcher(tmp_path, [SKILL_PDF])
        hits = m.admit("请帮我解析这份PDF文档的内容并总结要点")
        assert [h["name"] for h in hits] == ["pdf-skill"]
        assert hits[0]["match_reason"].startswith("trigger:")
        assert "score_breakdown" in hits[0]

    def test_generic_trigger_not_admitted(self, tmp_path):
        skill = {"id": "s1", "name": "n1", "content": "c", "triggers": ["使用"]}
        m = make_matcher(tmp_path, [skill])
        assert m.admit("请使用这个功能帮我处理一下这个问题") == []


class TestRankingNotAdmission:
    def test_description_only_ranked_but_not_admitted(self, tmp_path):
        """pitfall 30：描述匹配可以排最前，但绝不放行（Ranking is not admission）。"""
        skill = {"id": "s1", "name": "n1", "content": "c",
                 "triggers": ["门禁系统运维"], "description": "文档总结要点"}
        m = make_matcher(tmp_path, [skill])
        ranked = m.rank("请帮我总结这份文档的要点内容")
        assert ranked and ranked[0]["score_breakdown"]["bm25"] > 0  # 排序层认了
        assert m.admit("请帮我总结这份文档的要点内容", ranked) == []  # 放行层拒了

    def test_no_evidence_semantic_never_admits(self, tmp_path):
        """有情况语句但 hits=0（手改账本）也不许语义放行——证据前置。"""
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        ledger._load()["skills"]["s-inv"] = {
            "name": "invoice-skill", "hits": 0, "useful": 0, "not_useful": 0,
            "unknown": 0, "situations": [TASK1], "last_outcome": None, "updated_at": None,
        }
        embedder = StubEmbedder({TASK2: [1, 0, 0], TASK1: [1, 0, 0]})
        m = make_matcher(tmp_path, [SKILL_INVOICE], ledger=ledger, embedder=embedder)
        assert m.admit(TASK2) == []
        assert m.stats["rejections_no_evidence"] >= 1


class TestSemanticAndAnchorPaths:
    def test_semantic_admit_high_sim(self, tmp_path):
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        ledger.record_hit("s-inv", TASK1, outcome="useful", skill_name="invoice-skill")
        embedder = StubEmbedder({TASK2: [1, 0, 0], TASK1: [0.95, 0.05, 0]})
        m = make_matcher(tmp_path, [SKILL_INVOICE], ledger=ledger, embedder=embedder)
        hits = m.admit(TASK2)
        assert [h["name"] for h in hits] == ["invoice-skill"]
        assert hits[0]["match_reason"].startswith("semantic:sim=")

    def test_anchor_admit_with_semantic_corroboration(self, tmp_path):
        """中相似（[T_low, T_sem)）+ ≥2 锚词共享（发票/报销/财务）= anchor 路放行。"""
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        ledger.record_hit("s-inv", TASK1, outcome="useful", skill_name="invoice-skill")
        embedder = StubEmbedder({TASK2: [1, 0, 0], TASK1: [0.70, 0.71, 0]})  # cos≈0.70
        m = make_matcher(tmp_path, [SKILL_INVOICE], ledger=ledger, embedder=embedder)
        hits = m.admit(TASK2)
        assert hits and hits[0]["match_reason"].startswith("anchor:")
        assert "发票" in hits[0]["match_reason"] or "报销" in hits[0]["match_reason"]

    def test_low_sim_no_anchor_not_admitted(self, tmp_path):
        """负对照：普通问题对有证据技能也不放行。"""
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        ledger.record_hit("s-inv", TASK1, outcome="useful", skill_name="invoice-skill")
        embedder = StubEmbedder({TASK2: [1, 0, 0], TASK1: [0, 1, 0]})  # cos=0
        m = make_matcher(tmp_path, [SKILL_INVOICE], ledger=ledger, embedder=embedder)
        assert m.admit(TASK2) == []

    def test_anchor_alone_without_sim_threshold_rejected(self, tmp_path):
        """锚词共享但语义低于 T_low：不放行（混合佐证缺一不可）。"""
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        ledger.record_hit("s-inv", TASK1, outcome="useful", skill_name="invoice-skill")
        # cos=0.30 < T_LOW(0.55)：锚词再多也要语义佐证
        embedder = StubEmbedder({TASK2: [1, 0, 0], TASK1: [0.3, 0.95, 0]})
        m = make_matcher(tmp_path, [SKILL_INVOICE], ledger=ledger, embedder=embedder)
        assert m.admit(TASK2) == []


class TestFeedbackBoost:
    def test_useful_skill_ranks_above_not_useful(self, tmp_path):
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        for i in range(3):
            ledger.record_hit("s-a", TASK1, outcome="useful")
            ledger.record_hit("s-b", TASK1, outcome="not_useful")
        skill_a = dict(SKILL_INVOICE, id="s-a", name="a")
        skill_b = dict(SKILL_INVOICE, id="s-b", name="b")
        # 两技能都有情况语句→语义层会被触达，注入 StubEmbedder 防单测触网
        m = make_matcher(
            tmp_path, [skill_b, skill_a], ledger=ledger,
            embedder=StubEmbedder({TASK1: [1, 0, 0]}),
        )
        ranked = m.rank(TASK1)
        assert ranked[0]["name"] == "a"
        assert ranked[0]["score_breakdown"]["feedback_boost"] > 0
        assert ranked[1]["score_breakdown"]["feedback_boost"] < 0


class TestDegradationTrace:
    def test_degrade_recorded_in_stats(self, tmp_path):
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        ledger.record_hit("s-inv", TASK1, outcome="useful")
        embedder = StubEmbedder(
            {TASK2: [1, 0, 0], TASK1: [1, 0, 0]}, provider="hash",
            degraded="opensoul_embed_unreachable",
        )
        m = make_matcher(tmp_path, [SKILL_INVOICE], ledger=ledger, embedder=embedder)
        m.rank(TASK2)
        assert m.stats["semantic_degrades"] >= 1  # No silent downgrade

    def test_empty_vectors_fail_closed(self, tmp_path):
        ledger = SkillFeedbackLedger(path=str(tmp_path / "ledger.json"))
        ledger.record_hit("s-inv", TASK1, outcome="useful")

        class EmptyEmbedder:
            def embed(self, texts):
                return EmbedResult([], "none", "all_providers_failed")

        m = make_matcher(
            tmp_path, [SKILL_INVOICE], ledger=ledger, embedder=EmptyEmbedder())
        assert m.admit(TASK2) == []


class TestHelpers:
    def test_cosine_basic(self):
        assert abs(cosine([1, 0], [1, 0]) - 1.0) < 1e-9
        assert cosine([1, 0], [0, 1]) == 0.0
        assert cosine([], [1]) == 0.0

    def test_tokenize_cjk_sliding(self, tmp_path):
        from agent.skill_matcher import tokenize

        toks = tokenize("发票报销Excel")
        assert "发票" in toks and "报销" in toks and "excel" in toks
