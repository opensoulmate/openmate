# -*- coding: utf-8 -*-
"""SoulMate 智能搜索编排（agent/smart_search.py）测试。

覆盖：意图理解+查询改写（多路查询/去重合并/坏 JSON 降级原词）、LLM 语义相关性
筛选与排序（丢科普页/不相干项、未判项如实丢弃、score 排序、kind 资源/网页）、
歧义追问「哪一部/哪个版本」、确定性免费地板（词面过滤/零命中放宽/无键不筛）、
入任务参数提取（magnet/网盘分享链+提取码/直链）、LLM 失败降级地板（失败可见
notes）、torrent_search 智能呈现（语义标注/丢弃清单/多路查询/源降级）与
_download_dispatch 完整链（搜索→精选→加任务）。LLM 全部走桩，零网络零 LLM 成本。
"""
import asyncio
import json

import pytest

from agent.download_tools import DownloadTools
from agent.smart_search import SmartSearchEngine
from tests.mock_download_backend import MockDownloadBackend


def _run(coro):
    return asyncio.run(coro)


# ── 桩：LLM（按提示词特征回放）+ fetch（记录调用）──────────────────


class StubLLM:
    def __init__(self, rewrite=None, semantic=None, fail=False, raw=None):
        self.rewrite = rewrite          # dict → JSON 回放；None → 回放非 JSON
        self.semantic = semantic
        self.fail = fail
        self.raw = raw                  # 显式原文覆盖（所有调用）
        self.calls = []

    async def __call__(self, messages):
        blob = "\n".join(str(m.get("content", "")) for m in messages)
        self.calls.append(blob)
        if self.fail:
            raise RuntimeError("llm boom")
        if self.raw is not None:
            return self.raw
        if "查询规划器" in blob:
            if self.rewrite is None:
                return "这不是 JSON"
            return json.dumps(self.rewrite, ensure_ascii=False)
        if self.semantic is None:
            return "这不是 JSON"
        return json.dumps(self.semantic, ensure_ascii=False)


class StubFetch:
    def __init__(self, rows_by_query=None, default_rows=(), errors=(), err=""):
        self.rows_by_query = rows_by_query or {}
        self.default_rows = list(default_rows)
        self.errors = list(errors)
        self.err = err
        self.calls = []

    async def __call__(self, query, category, limit):
        self.calls.append((query, category, limit))
        rows = self.rows_by_query.get(query, self.default_rows)
        return list(rows), list(self.errors), self.err


def _row(title, magnet=None, url=None, **kw):
    r = {"title": title, "size": 1, "seeders": 1, "source": "stub"}
    if magnet:
        r["magnet"] = magnet
    if url:
        r["url"] = url
    r.update(kw)
    return r


# ── ① 意图理解 + 查询改写（多路查询）────────────────────────────


def test_rewrite_multi_query_fetch_merge_dedupe():
    llm = StubLLM(rewrite={"intent": "找三体有声书", "category_hint": "audio",
                           "queries": ["三体 有声书", "三体 有声小说"]})
    fetch = StubFetch(rows_by_query={
        "三体 有声书": [_row("三体有声书全集", magnet="magnet:?xt=urn:btih:aaa")],
        "三体 有声小说": [
            _row("三体有声书全集", magnet="magnet:?xt=urn:btih:aaa"),  # 重复行
            _row("三体有声小说", url="https://x/2.mp3"),
        ],
    })
    res = _run(SmartSearchEngine(llm_fn=llm, fetch=fetch).run("三体的有声书"))
    assert res.queries == ["三体 有声书", "三体 有声小说"]
    assert res.intent == "找三体有声书"
    # 多路合并按 (title, 链接) 去重保序：3 行进 2 行
    titles = [r["title"] for r in res.items] + [d["title"] for d in res.dropped]
    assert titles.count("三体有声书全集") == 1
    assert [c[0] for c in fetch.calls] == ["三体 有声书", "三体 有声小说"]


def test_rewrite_bad_json_falls_back_single_query_with_note():
    llm = StubLLM(rewrite=None)  # 回放非 JSON
    fetch = StubFetch(default_rows=[_row("三体有声书", magnet="magnet:?xt=urn:btih:aaa")])
    res = _run(SmartSearchEngine(llm_fn=llm, fetch=fetch).run("三体的有声书"))
    assert res.queries == ["三体的有声书"]           # 原词单路
    assert any("非 JSON" in n and "免费地板" in n for n in res.notes)


# ── ② LLM 语义相关性筛选与排序 ─────────────────────────────────


def test_semantic_drops_kep_pages_and_ranks():
    rows = [
        _row("三体是什么含义-知乎科普", url="https://x/kepu"),   # 地板词面分最低
        _row("三体有声书全集MP3", magnet="magnet:?xt=urn:btih:bbb"),
        _row("三体有声书-喜马拉雅", url="https://x/xmly"),
    ]
    llm = StubLLM(
        rewrite={"intent": "找三体有声书", "queries": ["三体 有声书"]},
        # 语义窗按地板分降序=[全集MP3, 喜马拉雅, 知乎科普]，i 为窗内编号
        semantic={"relevant": [
            {"i": 1, "score": 95, "kind": "resource", "reason": "正是三体有声书"},
            {"i": 2, "score": 70, "kind": "web", "reason": "三体有声书收听页"},
        ], "irrelevant": [
            {"i": 3, "reason": "科普页，非资源"},
        ], "clarify": ""})
    res = _run(SmartSearchEngine(llm_fn=llm, fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    assert res.llm_used is True
    assert [r["title"] for r in res.items] == ["三体有声书全集MP3", "三体有声书-喜马拉雅"]
    assert res.items[0]["_smart"]["score"] == 95
    assert res.items[1]["_smart"]["kind"] == "web"   # 相关网页单独标注
    assert res.dropped == [{"title": "三体是什么含义-知乎科普",
                            "reason": "科普页，非资源", "layer": "llm"}]


def test_semantic_unmentioned_candidates_dropped_honestly():
    rows = [_row("三体有声书", magnet="magnet:?xt=urn:btih:ccc"),
            _row("三体广播剧", url="https://x/1")]  # 过地板（含「三体」）但 LLM 未判
    llm = StubLLM(
        rewrite={"queries": ["三体 有声书"]},
        semantic={"relevant": [{"i": 1, "score": 90, "reason": "相关"}],
                  "irrelevant": [], "clarify": ""})
    res = _run(SmartSearchEngine(llm_fn=llm, fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    assert len(res.items) == 1
    # LLM 漏判（未提及）的候选如实丢弃并记因，不冒充语义判定
    assert res.dropped[0]["reason"] == "语义层未确认相关"


def test_semantic_clarify_on_ambiguity():
    rows = [_row("三体广播剧", magnet="magnet:?xt=urn:btih:ddd"),
            _row("三体有声书", magnet="magnet:?xt=urn:btih:eee")]
    llm = StubLLM(
        rewrite={"queries": ["三体"]},
        semantic={"relevant": [
            {"i": 1, "score": 80, "reason": "三体广播剧"},
            {"i": 2, "score": 85, "reason": "三体有声书"}],
            "irrelevant": [],
            "clarify": "你要的是小说有声书、广播剧还是影视原声？"})
    res = _run(SmartSearchEngine(llm_fn=llm, fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    assert "哪部/哪版" not in res.clarify  # 桩原文透传
    assert res.clarify == "你要的是小说有声书、广播剧还是影视原声？"


def test_semantic_scores_desc_order():
    rows = [_row(f"资源{i}", magnet=f"magnet:?xt=urn:btih:{i:040d}") for i in range(5)]
    llm = StubLLM(
        rewrite={"queries": ["资源"]},
        semantic={"relevant": [
            {"i": 3, "score": 99, "reason": "最相关"},
            {"i": 1, "score": 50, "reason": "一般"},
            {"i": 2, "score": 80, "reason": "较相关"}],
            "irrelevant": [{"i": 4, "reason": "不相干"}, {"i": 5, "reason": "不相干"}],
            "clarify": ""})
    res = _run(SmartSearchEngine(llm_fn=llm, fetch=StubFetch(default_rows=rows))
               .run("资源", limit=2))
    # score 降序；精选全量保留（展示上限由渲染层自报，不静默截断）
    assert [r["_smart"]["score"] for r in res.items] == [99, 80, 50]
    assert {d["title"] for d in res.dropped} == {"资源3", "资源4"}


# ── ③ 免费地板（确定性词面过滤，零 LLM 成本）─────────────────────


def test_floor_filters_unrelated_titles_without_llm():
    rows = [_row("三体有声书全集", magnet="magnet:?xt=urn:btih:aaa"),
            _row("Python 编程入门", url="https://x/py")]
    res = _run(SmartSearchEngine(llm_fn=None, fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    assert res.llm_used is False
    assert [r["title"] for r in res.items] == ["三体有声书全集"]
    assert res.dropped == [{"title": "Python 编程入门",
                            "reason": "词面全不相干（免费地板过滤）", "layer": "floor"}]
    # 地板路径也带可入任务参数（免费地板=完整降级路径，不是残废）
    assert res.items[0]["_add"] == {"url": "magnet:?xt=urn:btih:aaa"}


def test_floor_relaxes_when_zero_overlap():
    rows = [_row("完全无关的标题", url="https://x/1"),
            _row("另一个无关标题", url="https://x/2")]
    res = _run(SmartSearchEngine(llm_fn=None, fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    # 词面零命中：过滤器没长眼睛时不冒充有——放宽为原始候选并如实标注
    assert len(res.items) == 2
    assert any("词面零命中" in n and "未过滤" in n for n in res.notes)


def test_floor_no_keys_keeps_all():
    rows = [_row("随便什么", url="https://x/1")]
    res = _run(SmartSearchEngine(llm_fn=None, fetch=StubFetch(default_rows=rows))
               .run("？？？"))
    assert [r["title"] for r in res.items] == ["随便什么"]  # 无键可判不筛


def test_add_extraction_magnet_pan_http():
    rows = [_row("磁力资源", magnet="magnet:?xt=urn:btih:abc"),
            _row("网盘资源", share_url="https://pan.baidu.com/s/1X", extract_code="a1b2"),
            _row("直链资源", url="https://x/f.iso")]
    res = _run(SmartSearchEngine(llm_fn=None, fetch=StubFetch(default_rows=rows))
               .run("资源"))
    adds = {r["title"]: r["_add"] for r in res.items}
    assert adds["磁力资源"] == {"url": "magnet:?xt=urn:btih:abc"}
    assert adds["网盘资源"] == {"url": "https://pan.baidu.com/s/1X",
                                "extract_code": "a1b2"}
    assert adds["直链资源"] == {"url": "https://x/f.iso"}


# ── ④ LLM 失败降级（免费地板，失败可见）───────────────────────────


def test_llm_exception_falls_back_to_floor_with_visible_notes():
    rows = [_row("三体有声书全集", magnet="magnet:?xt=urn:btih:aaa"),
            _row("Python 编程入门", url="https://x/py")]
    res = _run(SmartSearchEngine(llm_fn=StubLLM(fail=True),
                                 fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    assert res.llm_used is False
    assert [r["title"] for r in res.items] == ["三体有声书全集"]  # 地板照常过滤
    blob = " ".join(res.notes)
    assert "查询改写 LLM 不可用" in blob and "LLM 语义筛选不可用" in blob
    assert "免费地板" in blob  # 失败可见+降级报因，不静默


def test_llm_bad_json_semantic_falls_back_to_floor():
    rows = [_row("三体有声书", magnet="magnet:?xt=urn:btih:aaa")]
    res = _run(SmartSearchEngine(llm_fn=StubLLM(raw="垃圾输出"),
                                 fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    assert res.llm_used is False
    assert any("非 JSON" in n for n in res.notes)


def test_llm_error_text_treated_as_failure():
    rows = [_row("三体有声书", magnet="magnet:?xt=urn:btih:aaa")]
    res = _run(SmartSearchEngine(llm_fn=StubLLM(raw="[LLM错误: timeout]"),
                                 fetch=StubFetch(default_rows=rows))
               .run("三体的有声书"))
    assert res.llm_used is False


# ── ⑤ 边界：空 query / 取数失败 ─────────────────────────────────


def test_empty_query_fatal():
    res = _run(SmartSearchEngine(fetch=StubFetch()).run("  "))
    assert res.fatal == "错误: query 参数不能为空"


def test_fetch_error_propagated_as_fatal():
    res = _run(SmartSearchEngine(fetch=StubFetch(err="错误: 下载后端不可达（x）"))
               .run("三体"))
    assert res.fatal.startswith("错误: 下载后端不可达")
    assert res.items == []


# ── ⑥ torrent_search 智能呈现（DownloadTools 渲染层）──────────────


@pytest.fixture()
def backend():
    b = MockDownloadBackend()
    b.start()
    yield b
    b.stop()


def test_torrent_search_smart_render(backend):
    llm = StubLLM(
        rewrite={"intent": "找演示资源", "queries": ["demo"]},
        semantic={"relevant": [{"i": 1, "score": 92, "kind": "resource",
                                "reason": "正是要的资源"}],
                  "irrelevant": [{"i": 2, "reason": "科普页，非资源"},
                                 {"i": 3, "reason": "不相干"},
                                 {"i": 4, "reason": "不相干"},
                                 {"i": 5, "reason": "不相干"}],
                  "clarify": ""})
    tools = DownloadTools(api_base=backend.base_url, timeout=5.0, llm_fn=llm)
    out = _run(tools.torrent_search({"query": "demo"}))
    assert "🔍 种子搜索 demo（SoulMate 智能搜索）：1 个结果" in out
    assert "🧠 意图: 找演示资源" in out
    assert "多路查询: demo" in out and "LLM 语义精选" in out
    assert "相关性 92（正是要的资源）" in out
    assert "🗑 已丢弃 4 条（科普页/不相干）" in out
    assert "科普页，非资源" in out            # 丢弃项如实列因
    assert "可入任务: magnet:?xt=urn:btih:" in out
    assert "有歧义" in out and "download_add" in out  # 对话流引导保留


def test_torrent_search_floor_mode_render(backend):
    tools = DownloadTools(api_base=backend.base_url, timeout=5.0)  # 无 LLM
    out = _run(tools.torrent_search({"query": "blade runner"}))
    assert "🔍 种子搜索 blade runner（SoulMate 智能搜索）：5 个结果" in out
    assert "免费地板（确定性词面过滤，LLM 未参与）" in out
    assert "词面命中" in out


def test_torrent_search_clarify_rendered(backend):
    llm = StubLLM(
        rewrite={"queries": ["demo"]},
        semantic={"relevant": [{"i": 1, "score": 90, "reason": "相关"},
                               {"i": 2, "score": 85, "reason": "相关"}],
                  "irrelevant": [], "clarify": "你指的是哪一部/哪个版本？"})
    tools = DownloadTools(api_base=backend.base_url, timeout=5.0, llm_fn=llm)
    out = _run(tools.torrent_search({"query": "demo"}))
    assert "❓ 先确认（哪一部/哪个版本）: 你指的是哪一部/哪个版本？" in out


def test_torrent_search_source_errors_surfaced(backend):
    tools = DownloadTools(api_base=backend.base_url, timeout=5.0)
    out = _run(tools.torrent_search({"query": "demo failsource"}))
    assert "⚠️ 源降级: Broken RSS: timeout (15s)" in out
    assert "5 个结果" in out  # 降级不拖垮其它源


def test_torrent_search_unreachable_returns_fatal_verbatim():
    tools = DownloadTools(api_base="http://127.0.0.1:1", timeout=1.0)
    out = _run(tools.torrent_search({"query": "x"}))
    assert out.startswith("错误: 下载后端不可达")
    assert "/search 与 /download" in out


# ── ⑦ 完整链（真实 dispatch：搜索→精选→加任务）───────────────────


def test_full_chain_via_dispatch_with_llm_stub(backend):
    from agent.soulmate_agent import SoulMateAgent
    llm = StubLLM(
        rewrite={"intent": "找演示资源", "queries": ["demo"]},
        semantic={"relevant": [{"i": 1, "score": 95, "kind": "resource",
                                "reason": "最合适的资源"}],
                  "irrelevant": [], "clarify": ""})
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent._download_tools_inst = DownloadTools(
        api_base=backend.base_url, timeout=5.0, llm_fn=llm)
    sid = "sess-smart"
    r1 = _run(agent._download_dispatch(
        "torrent_search", {"query": "最新有什么好的资源帮我找一下"}, sid))
    assert "LLM 语义精选" in r1 and "相关性 95" in r1
    # 一键加任务：精选第 1 条的磁力链直接 download_add
    import re
    m = re.search(r"可入任务: (magnet:\S+)", r1)
    assert m is not None
    r2 = _run(agent._download_dispatch(
        "download_add", {"url": m.group(1), "target": "remote"}, sid))
    assert "✅ 下载任务已添加" in r2
