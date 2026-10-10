"""8.4 调研兜底 tests：web_search 后端可用性探测 + 显式降级（禁静默归零）。

全程注入假 opener 零网络；接线护栏断言 soulmate_agent 两执行点共用单一真源。
"""
import json
import os
import re
import socket
import threading
from pathlib import Path

import pytest

from agent.search_backend import (
    STARTUP_HINT,
    BackendAttempt,
    SearchBackends,
    _curl_opener,
    parse_bing_html,
    run_search,
)

ACPPROXY_ROOT = Path(__file__).resolve().parent.parent
SOULMATE_SRC = ACPPROXY_ROOT / "agent" / "soulmate_agent.py"


# ---------- 假 opener ----------

class FakeOpener:
    """按 URL 子串路由的假 opener，记录每次请求 URL（编码断言用）。"""

    def __init__(self, routes):
        # routes: list[(url_substring, (ok, body, err))]
        self.routes = list(routes)
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        for sub, resp in self.routes:
            if sub in url:
                return resp
        return False, "", "no route"


def searx_body(results):
    return json.dumps({"results": results})


RES = [{"title": "T1", "url": "https://a.example/1", "content": "c1"},
       {"title": "T2", "url": "https://a.example/2", "content": "c2"}]

BING_HTML = (
    '<li class="b_algo"><h2><a href="https://b.example/x">Bing Title</a></h2>'
    "<p>Bing snippet text</p></li>"
    '<li class="b_algo"><h2><a href="https://b.example/y">Bing T2</a></h2>'
    "<p>second</p></li>"
)


# ---------- happy path：主后端命中 ----------

def test_searx_ok_formats_results_and_limit():
    op = FakeOpener([("localhost:8888", (True, searx_body(RES), ""))])
    out = SearchBackends(opener=op).search("hello", limit=1)
    assert out.status == "ok" and out.hit_backend == "searxng"
    assert "T1: https://a.example/1" in out.text
    assert "T2" not in out.text  # limit=1
    assert "降级" not in out.text  # 主后端命中不加降级噪音


def test_query_is_url_encoded():
    """根因回归：裸 query 拼 URL，空格/中文被截断→假零结果（静默归零根因之一）。"""
    op = FakeOpener([("localhost:8888", (True, searx_body(RES), ""))])
    SearchBackends(opener=op).search("open mate 测试", limit=5)
    assert op.calls, "必须发起请求"
    assert "open%20mate%20%E6%B5%8B%E8%AF%95" in op.calls[0]
    assert " " not in op.calls[0]


def test_limit_clamped():
    op = FakeOpener([("localhost:8888", (True, searx_body(RES), ""))])
    out = SearchBackends(opener=op).search("q", limit=0)
    assert out.status == "ok"  # 0 夹取到 1，不崩
    out2 = SearchBackends(opener=op).search("q", limit="bad")
    assert out2.status == "ok"  # 非法 limit 回默认，不崩


# ---------- 降级链 ----------

def test_searx_down_bing_fallback_explicit_degradation():
    op = FakeOpener([
        ("localhost:8888", (False, "", "Connection refused")),
        ("bing.com", (True, BING_HTML, "")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "ok" and out.hit_backend == "bing"
    assert "已降级使用 bing 兜底" in out.text  # 显式标注降级
    assert "Bing Title: https://b.example/x" in out.text
    assert "Bing snippet text" in out.text


def test_searx_empty_falls_through_to_bing():
    """单后端 0 结果不直接定'空'——继续兜底，禁单后端假零。"""
    op = FakeOpener([
        ("localhost:8888", (True, searx_body([]), "")),
        ("bing.com", (True, BING_HTML, "")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "ok" and out.hit_backend == "bing"


def test_bing_disabled_honored():
    op = FakeOpener([("localhost:8888", (False, "", "refused"))])
    out = SearchBackends(opener=op, bing_enabled=False).search("query")
    assert out.status == "unavailable"
    assert any("已禁用" in a.detail for a in out.attempts)
    assert all("bing.com" not in u for u in op.calls)  # 禁用就不发请求


# ---------- 三态语义（禁静默归零核心）----------

def test_all_down_reports_unavailable_not_empty():
    op = FakeOpener([
        ("localhost:8888", (False, "", "Connection refused")),
        ("bing.com", (False, "", "timeout")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "unavailable"
    assert "搜索后端不可用" in out.text
    assert "无搜索结果" not in out.text.splitlines()[0]  # 绝不伪装成"无结果"
    assert "Connection refused" in out.text and "timeout" in out.text  # 逐后端报因


def test_unavailable_includes_startup_docs():
    """8.4 要求"启动文档化"：不可用时报因+启动命令，不止报错。"""
    op = FakeOpener([("x", (False, "", "refused"))])
    out = SearchBackends(opener=op, bing_enabled=False).search("query")
    assert STARTUP_HINT in out.text
    assert "start.sh" in out.text and "searx.webapp" in out.text


def test_reachable_zero_results_says_non_fault():
    """可达全空≠故障：显式声明'非故障'，与 unavailable 严格区分。"""
    op = FakeOpener([
        ("localhost:8888", (True, searx_body([]), "")),
        ("bing.com", (True, "<html>no blocks</html>", "")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "empty"
    assert "非故障" in out.text
    assert "搜索后端不可用" not in out.text


def test_searx_non_json_is_explicit_error_not_zero():
    """HTTP 403 HTML（formats 未含 json）≠ 0 结果：显式报响应异常。"""
    op = FakeOpener([
        ("localhost:8888", (True, "<html>403 Forbidden</html>", "")),
        ("bing.com", (False, "", "refused")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "unavailable"
    assert "非JSON" in out.text
    assert out.attempts[0].ok is False  # 响应异常记为失败触达


def test_searx_missing_results_key_is_explicit_error():
    op = FakeOpener([
        ("localhost:8888", (True, json.dumps({"foo": 1}), "")),
        ("bing.com", (False, "", "refused")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "unavailable"
    assert "缺 results 字段" in out.text


def test_empty_body_is_explicit_error():
    op = FakeOpener([
        ("localhost:8888", (True, "   ", "")),
        ("bing.com", (False, "", "refused")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert "空响应" in out.text


def test_empty_query_explicit_invalid():
    op = FakeOpener([])
    out = SearchBackends(opener=op).search("   ")
    assert out.status == "invalid_query"
    assert "query 为空" in out.text
    assert op.calls == []  # 空查询不发网络请求


def test_bing_captcha_not_silently_zero():
    """bing 0 块=反爬拦截与真无结果不可区分——inconclusive 禁断言'无资料'（真实机器实证）。"""
    op = FakeOpener([
        ("localhost:8888", (False, "", "refused")),
        ("bing.com", (True, "<html>please verify</html>", "")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "inconclusive"
    assert "反爬拦截" in out.text
    assert "不足以断言" in out.text  # 显式声明证据不足，禁静默归零
    assert "非故障" not in out.text  # 不可信空绝不冒充可信空


def test_bing_zero_blocks_after_searx_trusted_empty_still_empty():
    """searx 结构化 0 结果=可信空证据成立，bing 0 块不推翻'非故障'判定。"""
    op = FakeOpener([
        ("localhost:8888", (True, searx_body([]), "")),
        ("bing.com", (True, "<html>no blocks</html>", "")),
    ])
    out = SearchBackends(opener=op).search("query")
    assert out.status == "empty"
    assert "非故障" in out.text


# ---------- probe 探测面 ----------

def test_probe_reports_each_backend():
    op = FakeOpener([
        ("localhost:8888", (False, "", "refused")),
        ("bing.com", (True, "<html>x</html>", "")),
    ])
    attempts = SearchBackends(opener=op).probe()
    assert [a.backend for a in attempts] == ["searxng", "bing"]
    assert attempts[0].ok is False and "refused" in attempts[0].detail
    assert attempts[1].ok is True


def test_probe_no_results_parsing():
    """probe 只探连通性：bing 可达即 ok，不因 0 块判失败。"""
    op = FakeOpener([
        ("localhost:8888", (True, searx_body([]), "")),
        ("bing.com", (True, "<html>no blocks</html>", "")),
    ])
    attempts = SearchBackends(opener=op).probe()
    assert all(a.ok for a in attempts)


# ---------- bing 解析器（技能 parse_bing.py 同款规则）----------

def test_parse_bing_html_blocks():
    results = parse_bing_html(BING_HTML, 5)
    assert len(results) == 2
    assert results[0] == {"title": "Bing Title", "url": "https://b.example/x",
                          "content": "Bing snippet text"}


def test_parse_bing_html_strips_tags_in_title():
    results = parse_bing_html(
        '<li class="b_algo"><a href="https://b.example/z"><b>Bold</b> Tail</a>'
        "<p>s</p></li>", 5)
    assert results[0]["title"] == "Bold Tail"


# ---------- run_search 入口 ----------

def test_run_search_never_raises():
    class Boom:
        def __call__(self, url, timeout):
            raise RuntimeError("boom")
    text = run_search("q", backends=SearchBackends(opener=Boom(), bing_enabled=False))
    assert text.startswith("搜索失败")  # 连合成都炸也要显式成文


def test_run_search_delegates_to_backends():
    op = FakeOpener([("localhost:8888", (True, searx_body(RES), ""))])
    text = run_search("q", backends=SearchBackends(opener=op))
    assert "T1: https://a.example/1" in text


# ---------- 接线护栏（写了≠接线了）----------

def test_soulmate_agent_uses_single_source():
    src = SOULMATE_SRC.read_text(encoding="utf-8")
    assert "from agent import search_backend" in src
    # 两执行点（主循环 + code-mode 批内）都走 run_search
    assert src.count("search_backend.run_search(") == 2
    # 内联 curl-SearXNG 逻辑必须收口干净（禁双真源）
    assert "localhost:8888" not in src
    assert 'f"搜索不可用: {proc.stderr' not in src


def test_no_other_inline_searxng_call_sites():
    """全仓不再有绕过 search_backend 的 searxng 内联调用（测试/本模块除外）。"""
    offenders = []
    for p in ACPPROXY_ROOT.rglob("*.py"):
        if p.name in ("search_backend.py", "test_search_backend.py"):
            continue
        if "localhost:8888/search" in p.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(p))
    assert offenders == []


def test_attempt_dataclass_shape():
    a = BackendAttempt("searxng", True, "可达")
    assert a.backend == "searxng" and a.ok and a.detail == "可达"


# ---------- 周期#41：超时措辞 + 超时预算 ----------

def test_curl_exit28_wording_distinguishes_timeout_from_not_started():
    """真哑socket（accept后不响应）→ curl exit 28 → 措辞必须是"响应超时"且禁断言未启动。"""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _accept_and_hold():
        conn, _ = srv.accept()
        threading.Event().wait(30)  # 持有连接不回数据，逼curl超时
        conn.close()

    t = threading.Thread(target=_accept_and_hold, daemon=True)
    t.start()
    try:
        ok, body, err = _curl_opener(f"http://127.0.0.1:{port}/search?q=x", 1)
    finally:
        srv.close()
    assert not ok and body == ""
    assert "响应超时" in err and "勿断言未启动" in err
    assert "curl exit 28" not in err  # 旧误导措辞清零


def test_timeout_default_30_and_env_override(monkeypatch):
    fake = FakeOpener([])
    monkeypatch.delenv("SOULMATE_SEARCH_TIMEOUT_S", raising=False)
    assert SearchBackends(opener=fake).timeout == 30.0
    monkeypatch.setenv("SOULMATE_SEARCH_TIMEOUT_S", "45")
    assert SearchBackends(opener=fake).timeout == 45.0
    monkeypatch.setenv("SOULMATE_SEARCH_TIMEOUT_S", "not-a-number")
    assert SearchBackends(opener=fake).timeout == 30.0  # 坏值回退默认
    assert SearchBackends(opener=fake, timeout=7).timeout == 7.0  # 显式参数优先
