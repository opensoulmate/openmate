"""路线图 7.1 浏览器自动化（CDP）测试。

覆盖：act 校验面/确定性输出、extract/click/fill/eval/screenshot/tabs/close
动作语义（真实鼠标/输入事件序列）、_CdpSession 命令收发（通知跳过/超时/命令错）、
连接失败启动指引、PUT→GET /json/new 回退、soulmate_agent 接线护栏 +
__new__ 真实 dispatch 烟测，以及真实 headless Chromium CDP 端到端
（无 chromium 自动 skip）。
"""

from __future__ import annotations

import asyncio
import base64
import json
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest

from agent.browser_automation import (
    MAX_RESULT_CHARS,
    BrowserAutomation,
    BrowserAutomationError,
    TabInfo,
    _CdpSession,
    get_browser_automation,
)

# ── fake 传输层（零网络）───────────────────────────────────────


class FakeHttp:
    """http_json 接缝 fake：记录调用，按路径脚本化返回。"""

    def __init__(self, tabs=None, new_tab=None, close_status=200,
                 new_put_status=200, new_get_status=200):
        self.tabs = tabs if tabs is not None else [
            {"id": "T1", "title": "Page", "url": "about:blank",
             "webSocketDebuggerUrl": "ws://tab/T1"},
        ]
        self.new_tab = new_tab or {
            "id": "T2", "title": "", "url": "about:blank",
            "webSocketDebuggerUrl": "ws://tab/T2",
        }
        self.close_status = close_status
        self.new_put_status = new_put_status
        self.new_get_status = new_get_status
        self.calls = []

    async def __call__(self, method, url, timeout):
        self.calls.append((method, url))
        if url.endswith("/json/list"):
            return 200, json.dumps(self.tabs)
        if "/json/new" in url:
            if method == "PUT":
                if self.new_put_status >= 400:
                    return self.new_put_status, "method not allowed"
                return self.new_put_status, json.dumps(self.new_tab)
            return self.new_get_status, json.dumps(self.new_tab)
        if "/json/close/" in url:
            return self.close_status, "Target is closing"
        return 404, "not found"


class FakeSession:
    """CDP 会话 fake：记录命令，按 Runtime.evaluate 表达式脚本化返回。"""

    def __init__(self, on_eval=None, screenshot_b64="", fail_methods=None,
                 load_event=True, nav_result=None):
        self.on_eval = on_eval or (lambda expr: "ok")
        self.screenshot_b64 = screenshot_b64
        self.fail_methods = fail_methods or {}
        self.load_event = load_event
        self.nav_result = nav_result or {}
        self.sent = []
        self.closed = False

    async def call(self, method, params=None, timeout=15.0):
        self.sent.append((method, params or {}))
        if method in self.fail_methods:
            raise BrowserAutomationError(self.fail_methods[method])
        if method == "Page.navigate":
            return self.nav_result
        if method == "Runtime.evaluate":
            value = self.on_eval((params or {}).get("expression", ""))
            return {"result": {"value": value}}
        if method == "Page.captureScreenshot":
            return {"data": self.screenshot_b64}
        return {}

    async def wait_event(self, name, timeout=20.0):
        self.sent.append(("wait_event:" + name, {}))
        if not self.load_event:
            raise BrowserAutomationError(f"等待 CDP 事件超时（{timeout:.0f}s）: {name}")
        return {}

    async def close(self):
        self.closed = True


def make_browser(http=None, session=None, **kw):
    http = http or FakeHttp()
    session = session or FakeSession()
    b = BrowserAutomation(
        cdp_http_base="http://cdp.test:9222",
        http_json=http,
        connect_tab=lambda ws_url: _async_ret(session),
        **kw,
    )
    return b, http, session


async def _async_ret(v):
    return v


def run(coro):
    return asyncio.run(coro)


# ── act 校验面 ─────────────────────────────────────────────────


def test_unknown_action_rejected():
    b, _, _ = make_browser()
    out = run(b.act("bogus"))
    assert out.startswith("错误: 未知 action") and "open" in out


def test_open_requires_url():
    b, _, _ = make_browser()
    assert run(b.act("open")).startswith("错误: open 需要 url")


def test_click_requires_selector_or_xy():
    b, _, _ = make_browser()
    assert "click 需要 selector 或 x+y" in run(b.act("click"))


def test_fill_requires_selector_and_text():
    b, _, _ = make_browser()
    assert "fill 需要 selector" in run(b.act("fill", text="x"))
    assert "fill 需要 text" in run(b.act("fill", selector="#i"))


def test_eval_requires_expression():
    b, _, _ = make_browser()
    assert "eval 需要 expression" in run(b.act("eval"))


def test_screenshot_rejects_non_png(tmp_path):
    b, _, _ = make_browser()
    out = run(b.act("screenshot", path=str(tmp_path / "shot.jpg")))
    assert "必须是 .png" in out


# ── open / tabs / close ────────────────────────────────────────


def test_open_navigates_waits_ready_and_reports_title():
    def on_eval(expr):
        if expr == "document.readyState":
            return "complete"
        if expr == "document.title":
            return "示例页"
        return None

    b, http, sess = make_browser(session=FakeSession(on_eval=on_eval))
    out = run(b.act("open", url="https://example.com"))
    assert "✅ 已打开 https://example.com" in out
    assert "标题: 示例页" in out
    assert "⚠️" not in out
    methods = [m for m, _ in sess.sent]
    assert "Page.navigate" in methods and "Page.enable" in methods
    # 导航命令确实带目标URL + 事件等待驱动加载完成
    nav = [p for m, p in sess.sent if m == "Page.navigate"][0]
    assert nav == {"url": "https://example.com"}
    assert ("wait_event:Page.loadEventFired", {}) in [
        (m, p) for m, p in sess.sent]


def test_open_incomplete_load_reports_note():
    b, _, _ = make_browser(session=FakeSession(load_event=False),
                           nav_timeout_s=0.5)
    out = run(b.act("open", url="https://slow.example"))
    assert "✅ 已打开" in out and "⚠️ 页面加载事件未在预算内完成" in out


def test_open_falls_back_to_readystate_poll_without_wait_event():
    """无 wait_event 的会话（轮询回退路径）：readyState=loading 超预算 → ⚠️ 显式说明。"""
    def on_eval(expr):
        return "loading" if expr == "document.readyState" else "标题X"

    sess = FakeSession(on_eval=on_eval)
    setattr(sess, "wait_event", None)  # 模拟无事件能力的会话（实例属性遮蔽）
    b, _, _ = make_browser(session=sess, nav_timeout_s=0.5)
    out = run(b.act("open", url="https://slow.example"))
    assert "✅ 已打开" in out and "标题: 标题X" in out
    assert "⚠️ 页面加载事件未在预算内完成" in out

    def on_eval2(expr):
        return "complete" if expr == "document.readyState" else "标题Y"

    sess2 = FakeSession(on_eval=on_eval2)
    setattr(sess2, "wait_event", None)
    b2, _, _ = make_browser(session=sess2)
    out = run(b2.act("open", url="https://ok.example"))
    assert "标题: 标题Y" in out and "⚠️" not in out


def test_new_tab_put_fallback_to_get():
    http = FakeHttp(tabs=[], new_put_status=405)
    b, _, _ = make_browser(http=http)
    out = run(b.act("open", url="https://example.com"))
    assert "✅ 已打开" in out
    # 列表为空 → 新建标签页（about:blank）→ PUT 405 回退 GET
    assert ("PUT", "http://cdp.test:9222/json/new?url=about:blank") in http.calls
    assert ("GET", "http://cdp.test:9222/json/new?url=about:blank") in http.calls


def test_background_page_tab_is_skipped():
    """实测坑：扩展 background_page 也带 ws_url，必须按 type=page 过滤。"""
    http = FakeHttp(tabs=[
        {"id": "BG", "title": "Google Hangouts", "type": "background_page",
         "url": "chrome-extension://x/background.html",
         "webSocketDebuggerUrl": "ws://tab/BG"},
        {"id": "PG", "title": "真页面", "type": "page", "url": "about:blank",
         "webSocketDebuggerUrl": "ws://tab/PG"},
    ])
    b, _, sess = make_browser(http=http)
    out = run(b.act("open", url="https://example.com"))
    assert "标签页: PG" in out, out
    out = run(b.act("tabs"))
    assert "background_page" not in out and "真页面" in out


def test_navigate_error_text_is_explicit_failure():
    """实测坑：net::ERR_ABORTED 导航失败必须显式报错，禁假'已打开'。"""
    b, _, _ = make_browser(session=FakeSession(
        nav_result={"errorText": "net::ERR_ABORTED", "isDownload": False}))
    out = run(b.act("open", url="https://x.test"))
    assert out.startswith("[错误] 页面导航失败") and "net::ERR_ABORTED" in out
    assert "✅" not in out


def test_tabs_marks_current_and_empty():
    b, http, _ = make_browser()
    run(b.act("open", url="https://example.com"))
    out = run(b.act("tabs"))
    assert "共 1 个标签页" in out and "◀当前" in out
    http.tabs = []
    out = run(b.act("tabs"))
    assert "没有打开的标签页" in out


def test_close_tab_clears_current(tmp_path):
    b, _, _ = make_browser()
    run(b.act("open", url="https://example.com"))
    out = run(b.act("close"))
    assert "✅ 已关闭标签页" in out
    # 当前页清空后，下次 open 需重新走 ensure（新建或复用列表首项）
    out = run(b.act("open", url="https://example.com"))
    assert "✅ 已打开" in out


def test_close_failure_is_explicit():
    b, _, _ = make_browser(http=FakeHttp(close_status=500))
    out = run(b.act("close", tab_id="T9"))
    assert out.startswith("[错误] 关闭标签页失败")


# ── extract ────────────────────────────────────────────────────


def test_extract_body_text():
    def on_eval(expr):
        return json.dumps({"found": True, "text": "页面正文内容"})

    b, _, _ = make_browser(session=FakeSession(on_eval=on_eval))
    out = run(b.act("extract"))
    assert out == "页面正文内容"


def test_extract_selector_found_and_missing():
    seen = {}

    def on_eval(expr):
        seen["expr"] = expr
        if "nope" in expr:
            return json.dumps({"found": False})
        return json.dumps({"found": True, "text": "标题文本"})

    b, _, _ = make_browser(session=FakeSession(on_eval=on_eval))
    out = run(b.act("extract", selector="h1"))
    assert out == "标题文本" and "h1" in seen["expr"]
    out = run(b.act("extract", selector="nope"))
    assert out == "错误: 选择器无匹配: nope"


def test_extract_links_and_empty():
    def on_eval(expr):
        return json.dumps([
            {"text": "首页", "href": "https://example.com/"},
            {"text": "关于", "href": "https://example.com/about"},
        ])

    b, _, _ = make_browser(session=FakeSession(on_eval=on_eval))
    out = run(b.act("extract", links=True))
    assert "共 2 个链接" in out and "首页 | https://example.com/" in out

    b2, _, _ = make_browser(session=FakeSession(on_eval=lambda e: "[]"))
    out = run(b2.act("extract", links=True))
    assert "没有提取到任何链接" in out


def test_extract_truncation_self_reports():
    long_text = "长" * (MAX_RESULT_CHARS + 500)
    b, _, _ = make_browser(session=FakeSession(
        on_eval=lambda e: json.dumps({"found": True, "text": long_text})))
    out = run(b.act("extract"))
    assert "已截断" in out and f"共 {len(long_text)} 字符" in out
    assert len(out) < len(long_text)


# ── click / fill（真实鼠标/输入事件序列）────────────────────────


def test_click_selector_dispatches_real_mouse_events():
    def on_eval(expr):
        return json.dumps({"x": 105, "y": 205})

    b, _, sess = make_browser(session=FakeSession(on_eval=on_eval))
    out = run(b.act("click", selector="#btn"))
    assert "✅ 已点击 #btn" in out and "(105,205)" in out
    mouse = [(m, p) for m, p in sess.sent if m == "Input.dispatchMouseEvent"]
    assert [p["type"] for _, p in mouse] == [
        "mouseMoved", "mousePressed", "mouseReleased"]
    assert all(p["x"] == 105 and p["y"] == 205 for _, p in mouse)


def test_click_missing_selector_explicit_error():
    b, _, _ = make_browser(session=FakeSession(on_eval=lambda e: "null"))
    out = run(b.act("click", selector="#missing"))
    assert out.startswith("[错误] 浏览器操作失败") and "选择器无匹配" in out


def test_click_xy_coordinates():
    b, _, sess = make_browser()
    out = run(b.act("click", x=10, y=20))
    assert "✅ 已点击 坐标 (10,20)" in out
    mouse = [p for m, p in sess.sent if m == "Input.dispatchMouseEvent"]
    assert len(mouse) == 3 and mouse[0]["type"] == "mouseMoved"


def test_fill_focuses_clicks_and_inserts_text():
    b, _, sess = make_browser(session=FakeSession(
        on_eval=lambda e: json.dumps({"x": 5, "y": 6})))
    out = run(b.act("fill", selector="#name", text="张三"))
    assert "✅ 已填入 2 字符到 #name" in out
    methods = [m for m, _ in sess.sent]
    assert "Input.insertText" in methods
    insert_params = [p for m, p in sess.sent if m == "Input.insertText"][0]
    assert insert_params["text"] == "张三"
    # 点击聚焦 = mousePressed/mouseReleased 一对（无 mouseMoved 多余）
    mouse = [p["type"] for m, p in sess.sent if m == "Input.dispatchMouseEvent"]
    assert mouse == ["mousePressed", "mouseReleased"]
    # focus() 经 Runtime.evaluate
    assert any("focus()" in p.get("expression", "") for m, p in sess.sent
               if m == "Runtime.evaluate")


# ── eval / screenshot ──────────────────────────────────────────


def test_eval_value_and_error_subtype():
    b, _, _ = make_browser(session=FakeSession(on_eval=lambda e: 42))
    out = run(b.act("eval", expression="1+1"))
    assert out == "42"

    sess = FakeSession()
    async def fail_call(method, params=None, timeout=15.0):
        sess.sent.append((method, params or {}))
        if method == "Runtime.evaluate":
            return {"result": {"subtype": "error",
                               "description": "ReferenceError: x is not defined"}}
        return {}
    sess.call = fail_call
    b2, _, _ = make_browser(session=sess)
    out = run(b2.act("eval", expression="x"))
    assert out.startswith("[错误] 表达式执行失败") and "ReferenceError" in out


def test_screenshot_saves_png_and_media_tag(tmp_path):
    png_bytes = b"\x89PNG\r\n\x1a\nFAKE"
    b64 = base64.b64encode(png_bytes).decode()
    out_path = tmp_path / "page.png"
    b, _, _ = make_browser(session=FakeSession(screenshot_b64=b64))
    out = run(b.act("screenshot", path=str(out_path)))
    assert "✅ 截图已保存" in out and f"MEDIA:{out_path}" in out
    assert out_path.read_bytes() == png_bytes


def test_screenshot_empty_data_explicit_error(tmp_path):
    b, _, _ = make_browser(session=FakeSession(screenshot_b64=""))
    out = run(b.act("screenshot", path=str(tmp_path / "p.png")))
    assert out.startswith("[错误]") and "空数据" in out


# ── _CdpSession 协议语义 ───────────────────────────────────────


class FakeWs:
    def __init__(self, frames):
        self._frames = list(frames)
        self.sent = []
        self.closed = False

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        if not self._frames:
            await asyncio.sleep(3600)  # 无帧=永远等（配合 wait_for 超时语义）
        return self._frames.pop(0)

    async def close(self):
        self.closed = True


def test_cdp_session_matches_id_and_skips_notifications():
    ws = FakeWs([
        json.dumps({"method": "Page.loadEventFired", "params": {}}),
        json.dumps({"id": 1, "result": {"ok": 1}}),
        json.dumps({"id": 2, "result": {"ok": 2}}),
    ])
    sess = _CdpSession(ws)
    r1 = run(sess.call("Page.enable"))
    r2 = run(sess.call("Page.navigate", {"url": "x"}))
    assert r1 == {"ok": 1} and r2 == {"ok": 2}
    assert ws.sent[0]["method"] == "Page.enable"
    assert ws.sent[1]["params"] == {"url": "x"}


def test_cdp_session_wait_event_uses_buffered_notification():
    """事件先于命令响应到达 → call() 缓存不丢 → wait_event 从缓存命中。"""
    ws = FakeWs([
        json.dumps({"method": "Page.loadEventFired", "params": {"frameId": "f1"}}),
        json.dumps({"id": 1, "result": {"ok": 1}}),
    ])
    sess = _CdpSession(ws)
    run(sess.call("Page.navigate", {"url": "x"}))
    params = run(sess.wait_event("Page.loadEventFired", timeout=1))
    assert params == {"frameId": "f1"}
    # 已消费，再次等待会超时
    with pytest.raises(BrowserAutomationError, match="超时"):
        run(sess.wait_event("Page.loadEventFired", timeout=0.3))


def test_cdp_session_command_error_and_timeout():
    ws = FakeWs([json.dumps({"id": 1, "error": {"message": "No such target"}})])
    sess = _CdpSession(ws)
    with pytest.raises(BrowserAutomationError, match="No such target"):
        run(sess.call("Page.enable"))

    ws2 = FakeWs([])  # 永不回帧 → 超时
    sess2 = _CdpSession(ws2)
    with pytest.raises(BrowserAutomationError, match="超时"):
        run(sess2.call("Page.enable", timeout=0.3))


# ── 连接失败启动指引 ────────────────────────────────────────────


def test_connection_refused_gives_startup_guidance():
    async def refuse(method, url, timeout):
        raise ConnectionRefusedError("refused")

    b = BrowserAutomation(cdp_http_base="http://cdp.test:9222",
                          http_json=refuse,
                          connect_tab=lambda u: _async_ret(FakeSession()))
    out = run(b.act("tabs"))
    assert "浏览器未连接" in out and "SOULMATE_CDP_URL" in out \
        and "remote-debugging-port" in out


def test_get_browser_automation_singleton():
    assert get_browser_automation() is get_browser_automation()


# ── soulmate_agent 接线护栏 + 真实 dispatch 烟测 ────────────────


def test_soulmate_agent_wiring_guards():
    src = Path(__file__).resolve().parent.parent.joinpath(
        "agent/soulmate_agent.py").read_text(encoding="utf-8")
    # 工具定义
    assert '"name": "browser_act"' in src
    # 两处dispatch点：主循环 + code-mode内层
    assert src.count('func_name == "browser_act"') >= 2
    # 分发helper + lazy-init + 顶部import
    assert "def _browser_dispatch" in src
    assert "_get_browser_automation" in src
    assert "from agent.browser_automation import" in src
    # 系统提示工具清单
    assert "browser_act: 浏览器自动化" in src


def test_browser_dispatch_smoke(monkeypatch):
    """__new__构造+lazy-init真实dispatch烟测：open/extract/错误三态。"""
    import agent.soulmate_agent as sa

    def on_eval(expr):
        if expr == "document.readyState":
            return "complete"
        if expr == "document.title":
            return "烟测页"
        return json.dumps({"found": True, "text": "烟测内容"})

    b, _, _ = make_browser(session=FakeSession(on_eval=on_eval))
    monkeypatch.setattr(sa, "_get_browser_automation", lambda: b)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)  # 不过__init__

    out = run(agent._browser_dispatch({"action": "open", "url": "https://x.test"}))
    assert "✅ 已打开 https://x.test" in out and "标题: 烟测页" in out
    out = run(agent._browser_dispatch({"action": "extract"}))
    assert out == "烟测内容"
    out = run(agent._browser_dispatch({"action": "bogus"}))
    assert out.startswith("错误: 未知 action")
    # lazy-init 已缓存实例
    assert getattr(agent, "_browser_automation", None) is b


# ── 真实 headless Chromium CDP 端到端（无 chromium 自动 skip）────


CHROMIUM = pytest.mark.skipif(
    __import__("shutil").which("chromium") is None, reason="无 chromium 二进制")


@CHROMIUM
def test_real_chromium_cdp_end_to_end(tmp_path):
    """真实端到端：headless Chromium + CDP 完成 打开→提取→点击→填字→截图→eval→关页。"""
    import shutil

    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>CDP烟测</title></head><body>"
        "<h1 id='head'>端到端标题</h1>"
        "<input id='name' />"
        "<button id='btn' onclick=\"document.getElementById('head')"
        ".textContent='已点击'\">点我</button>"
        "<a href='https://example.com/'>示例链接</a>"
        "</body></html>", encoding="utf-8")

    # 空闲端口
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    profile = tmp_path / "cdp-profile"
    chromium_bin = str(shutil.which("chromium") or "chromium")
    proc = subprocess.Popen(
        [chromium_bin,
         "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}", "--no-first-run",
         "--no-default-browser-check", "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        # 等 CDP 就绪（≤15s）
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(base + "/json/list", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.3)
        else:
            pytest.skip("chromium CDP 未在预算内就绪")

        b = BrowserAutomation(cdp_http_base=base, nav_timeout_s=15)
        out = run(b.act("open", url=page.as_uri()))
        assert "✅ 已打开" in out and "标题: CDP烟测" in out, out

        out = run(b.act("extract", selector="#head"))
        assert out == "端到端标题", out

        out = run(b.act("click", selector="#btn"))
        assert "✅ 已点击" in out, out
        out = run(b.act("extract", selector="#head"))
        assert out == "已点击", out  # 真实鼠标事件确实点到了按钮

        out = run(b.act("fill", selector="#name", text="测试输入"))
        assert "已填入" in out, out
        out = run(b.act("eval",
                        expression="document.getElementById('name').value"))
        assert out == "测试输入", out  # 真实输入事件确实进了输入框

        out = run(b.act("extract", links=True))
        assert "示例链接" in out and "https://example.com/" in out, out

        shot = tmp_path / "real.png"
        out = run(b.act("screenshot", path=str(shot)))
        assert "✅ 截图已保存" in out and shot.exists() and shot.stat().st_size > 0, out
        assert shot.read_bytes()[:4] == b"\x89PNG"

        out = run(b.act("tabs"))
        assert "标签页" in out, out
        out = run(b.act("close"))
        assert "✅ 已关闭" in out, out
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
