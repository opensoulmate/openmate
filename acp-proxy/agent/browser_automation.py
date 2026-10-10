"""路线图 7.1 浏览器自动化（CDP 后端）。

BrowserAutomation：确定性浏览器操作面，走 Chromium 远程调试协议（CDP）：
- HTTP 面（/json/list、/json/new、/json/close）管理标签页
- WebSocket 面对目标标签页发 CDP 命令（Page.navigate / Runtime.evaluate /
  Input.dispatchMouseEvent / Input.insertText / Page.captureScreenshot）

设计纪律（对齐 5.x/6.x 各模块）：
- 全确定性，不走 LLM；传输层可注入（http_json / connect_tab 两个接缝），
  测试零网络；缺省实现用 stdlib urllib + websockets（acp-proxy 运行时已有）。
- 一切失败显式报因：CDP 不可达给启动指引，选择器无匹配/参数缺失都拒绝执行
  并说明，绝不静默返回"成功"。
- 点击走真实鼠标事件（Input.dispatchMouseEvent），填字走真实输入事件
  （Input.insertText）——复现用户真实操作路径，不用 JS click 冒充。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

# 命令级默认超时（秒），env 可覆盖
DEFAULT_CMD_TIMEOUT_S = float(os.environ.get("SOULMATE_BROWSER_TIMEOUT_S", "15"))
DEFAULT_NAV_TIMEOUT_S = float(os.environ.get("SOULMATE_BROWSER_NAV_TIMEOUT_S", "20"))
# extract/eval 结果回显上限（超长显式截断，禁静默）
MAX_RESULT_CHARS = 8000

_ALLOWED_ACTIONS = (
    "open", "tabs", "screenshot", "extract", "click", "fill", "eval", "close",
)


class BrowserAutomationError(RuntimeError):
    """浏览器自动化失败（连接/命令/校验），消息即给用户/模型的报因文本。"""


@dataclass
class TabInfo:
    """CDP /json/list 一条标签页记录的最小形状。"""

    tab_id: str
    title: str = ""
    url: str = ""
    ws_url: str = ""
    type: str = "page"

    @classmethod
    def from_cdp(cls, item: dict) -> "TabInfo":
        return cls(
            tab_id=str(item.get("id", "") or ""),
            title=str(item.get("title", "") or ""),
            url=str(item.get("url", "") or ""),
            ws_url=str(item.get("webSocketDebuggerUrl", "") or ""),
            type=str(item.get("type", "") or "page"),
        )


class _CdpSession:
    """一条到标签页的 WS 会话：递增 id 发命令，通知帧缓存，按 id 收响应。"""

    def __init__(self, ws: Any):
        self._ws = ws
        self._next_id = 0
        self._events: list[dict] = []  # 通知帧缓存（wait_event 先查这里，防乱序丢事件）

    async def call(
        self, method: str, params: dict | None = None,
        timeout: float = DEFAULT_CMD_TIMEOUT_S,
    ) -> dict:
        self._next_id += 1
        cmd_id = self._next_id
        await self._ws.send(json.dumps(
            {"id": cmd_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrowserAutomationError(
                    f"CDP 命令超时（{timeout:.0f}s）: {method}")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise BrowserAutomationError(
                    f"CDP 命令超时（{timeout:.0f}s）: {method}") from None
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue  # 非 JSON 帧忽略
            if msg.get("id") != cmd_id:
                # 事件通知（loadEventFired 等）：缓存不丢，供 wait_event 取
                self._events.append(msg)
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise BrowserAutomationError(
                    f"CDP 命令失败 {method}: {err.get('message', err)}")
            return msg.get("result") or {}

    async def wait_event(
        self, name: str, timeout: float = DEFAULT_NAV_TIMEOUT_S,
    ) -> dict:
        """等待指定 CDP 事件（先查缓存——命令响应前到达的事件不丢）。"""
        for i, ev in enumerate(self._events):
            if ev.get("method") == name:
                return self._events.pop(i).get("params") or {}
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrowserAutomationError(
                    f"等待 CDP 事件超时（{timeout:.0f}s）: {name}")
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                raise BrowserAutomationError(
                    f"等待 CDP 事件超时（{timeout:.0f}s）: {name}") from None
            try:
                msg = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if msg.get("method") == name:
                return msg.get("params") or {}
            self._events.append(msg)

    async def close(self) -> None:
        try:
            await self._ws.close()
        except Exception:  # noqa: BLE001 — 关闭失败不影响已完成的动作
            pass


# ---------------------------------------------------------------------------
# 缺省传输实现（stdlib urllib + websockets），测试经接缝注入 fake 零网络
# ---------------------------------------------------------------------------

async def _default_http_json(method: str, url: str, timeout: float = 5.0) -> tuple[int, str]:
    def _do() -> tuple[int, str]:
        req = urllib.request.Request(url, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")

    return await asyncio.to_thread(_do)


async def _default_connect_tab(ws_url: str) -> _CdpSession:
    import websockets  # 延迟导入：测试注入 fake 时不需要真连

    ws = await websockets.connect(ws_url, max_size=32 * 1024 * 1024)
    return _CdpSession(ws)


@dataclass
class BrowserAutomation:
    """浏览器自动化门面（CDP 后端）。

    cdp_http_base: Chromium 调试 HTTP 面（默认 env SOULMATE_CDP_URL 或
    http://127.0.0.1:9222）；http_json / connect_tab 可注入（测试零网络）。
    """

    cdp_http_base: str = ""
    http_json: Callable[[str, str, float], Awaitable[tuple[int, str]]] = _default_http_json
    connect_tab: Callable[[str], Awaitable[_CdpSession]] = _default_connect_tab
    cmd_timeout_s: float = DEFAULT_CMD_TIMEOUT_S
    nav_timeout_s: float = DEFAULT_NAV_TIMEOUT_S
    _current_tab_id: str = field(default="", repr=False)
    _current_ws_url: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.cdp_http_base:
            self.cdp_http_base = os.environ.get(
                "SOULMATE_CDP_URL", "http://127.0.0.1:9222")
        self.cdp_http_base = self.cdp_http_base.rstrip("/")

    # ------------------------------------------------------------------
    # 标签页管理（HTTP 面）
    # ------------------------------------------------------------------

    async def _http(self, method: str, path: str) -> tuple[int, str]:
        try:
            return await self.http_json(
                method, f"{self.cdp_http_base}{path}", self.cmd_timeout_s)
        except (OSError, ConnectionError) as e:
            raise BrowserAutomationError(
                f"浏览器未连接（CDP {self.cdp_http_base} 不可达: {e}）。"
                f"请先以远程调试模式启动 Chromium："
                f"chromium --remote-debugging-port=9222 --user-data-dir=/tmp/cdp-profile，"
                f"或设置 SOULMATE_CDP_URL 指向调试地址") from e

    async def _list_tabs(self) -> list[TabInfo]:
        status, body = await self._http("GET", "/json/list")
        if status != 200:
            raise BrowserAutomationError(f"获取标签页列表失败（HTTP {status}）")
        try:
            items = json.loads(body)
        except ValueError:
            raise BrowserAutomationError("标签页列表响应不是合法 JSON") from None
        if not isinstance(items, list):
            raise BrowserAutomationError("标签页列表响应形状异常（非数组）")
        return [TabInfo.from_cdp(it) for it in items if isinstance(it, dict)]

    async def _ensure_tab(self) -> TabInfo:
        """返回当前标签页；无则新建 about:blank。显式失败不静默。"""
        if self._current_tab_id and self._current_ws_url:
            return TabInfo(self._current_tab_id, "", "", self._current_ws_url)
        tabs = await self._list_tabs()
        # 只认真实页面标签（实测 background_page 扩展页也带 ws_url，
        # 导航它会 net::ERR_ABORTED —— 必须按 type 过滤）
        usable = [
            t for t in tabs
            if t.ws_url and t.type == "page" and not t.url.startswith("devtools://")
        ]
        if usable:
            t = usable[0]
        else:
            t = await self._new_tab("about:blank")
        self._current_tab_id = t.tab_id
        self._current_ws_url = t.ws_url
        return t

    async def _new_tab(self, url: str) -> TabInfo:
        # 新版 Chromium /json/new 只接受 PUT，旧版只接受 GET —— 两态都试
        quoted = urllib.parse.quote(url, safe=":/?&=#%+,@;~")
        status, body = await self._http("PUT", f"/json/new?url={quoted}")
        if status not in (200, 201):
            status, body = await self._http("GET", f"/json/new?url={quoted}")
        if status not in (200, 201):
            raise BrowserAutomationError(
                f"新建标签页失败（HTTP {status}）: {body[:200]}")
        try:
            item = json.loads(body)
        except ValueError:
            raise BrowserAutomationError("新建标签页响应不是合法 JSON") from None
        tab = TabInfo.from_cdp(item if isinstance(item, dict) else {})
        if not tab.ws_url:
            raise BrowserAutomationError("新建标签页缺少 webSocketDebuggerUrl")
        return tab

    async def _session(self) -> _CdpSession:
        tab = await self._ensure_tab()
        if not tab.ws_url:
            raise BrowserAutomationError("当前标签页缺少 webSocketDebuggerUrl")
        try:
            return await self.connect_tab(tab.ws_url)
        except (OSError, ConnectionError) as e:
            raise BrowserAutomationError(
                f"连接标签页 WebSocket 失败: {e}") from e

    # ------------------------------------------------------------------
    # 动作面
    # ------------------------------------------------------------------

    async def act(self, action: str, **kwargs: Any) -> str:
        """执行一个浏览器动作，返回确定性文本结果（异常抛 BrowserAutomationError）。"""
        action = str(action or "").strip().lower()
        if action not in _ALLOWED_ACTIONS:
            return (
                f"错误: 未知 action '{action}'，可用: {', '.join(_ALLOWED_ACTIONS)}")
        try:
            if action == "open":
                return await self.open(str(kwargs.get("url", "") or ""))
            if action == "tabs":
                return await self.list_tabs_text()
            if action == "screenshot":
                return await self.screenshot(str(kwargs.get("path", "") or ""))
            if action == "extract":
                return await self.extract(
                    str(kwargs.get("selector", "") or ""),
                    links=bool(kwargs.get("links", False)))
            if action == "click":
                return await self.click(
                    str(kwargs.get("selector", "") or ""),
                    kwargs.get("x"), kwargs.get("y"))
            if action == "fill":
                return await self.fill(
                    str(kwargs.get("selector", "") or ""),
                    kwargs.get("text"))
            if action == "eval":
                return await self.evaluate(
                    str(kwargs.get("expression", "") or ""))
            return await self.close_tab(str(kwargs.get("tab_id", "") or ""))
        except BrowserAutomationError as e:
            return f"[错误] 浏览器操作失败: {e}"

    async def open(self, url: str) -> str:
        url = (url or "").strip()
        if not url:
            return "错误: open 需要 url 参数"
        tab = await self._ensure_tab()
        session = await self._session()
        try:
            await session.call("Page.enable")
            nav = await session.call("Page.navigate", {"url": url})
            # 导航错误显式失败（实测 background_page 上导航返回
            # net::ERR_ABORTED 也照报"已打开"= 假成功，必须查 errorText）
            error_text = str(nav.get("errorText", "") or "")
            if error_text and not nav.get("isDownload"):
                return f"[错误] 页面导航失败: {error_text}（URL: {url}）"
            # 等新文档 load 完成——事件驱动优先（readyState 轮询会误认旧文档
            # 已完成，实测真实 Chromium 竞态）；无 wait_event 的会话退回轮询
            wait_event = getattr(session, "wait_event", None)
            if callable(wait_event):
                try:
                    await wait_event("Page.loadEventFired", self.nav_timeout_s)
                    ready = True
                except BrowserAutomationError:
                    ready = False
            else:
                ready = await self._wait_ready(session)
            title = await self._eval_scalar(session, "document.title")
        finally:
            await session.close()
        self._current_tab_id = tab.tab_id
        self._current_ws_url = tab.ws_url
        note = "" if ready else "\n⚠️ 页面加载事件未在预算内完成（可能仍在加载）"
        return f"✅ 已打开 {url}\n标签页: {tab.tab_id}\n标题: {title}{note}"

    async def _wait_ready(self, session: _CdpSession) -> bool:
        """轮询 document.readyState（确定性，不依赖事件时序），预算内完成返回 True。"""
        deadline = time.monotonic() + self.nav_timeout_s
        while time.monotonic() < deadline:
            state = await self._eval_scalar(session, "document.readyState")
            if state in ("complete", "interactive"):
                return state == "complete"
            await asyncio.sleep(0.2)
        return False

    async def _eval_scalar(self, session: _CdpSession, expression: str) -> Any:
        result = await session.call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True,
             "awaitPromise": True},
            timeout=self.cmd_timeout_s)
        return (result.get("result") or {}).get("value")

    async def list_tabs_text(self) -> str:
        tabs = await self._list_tabs()
        visible = [
            t for t in tabs
            if t.type == "page" and not t.url.startswith("devtools://")
        ]
        if not visible:
            return "当前没有打开的标签页"
        lines = [f"共 {len(visible)} 个标签页："]
        for t in visible:
            marker = " ◀当前" if t.tab_id == self._current_tab_id else ""
            lines.append(f"- [{t.tab_id}] {t.title} | {t.url}{marker}")
        return "\n".join(lines)

    async def screenshot(self, path: str = "") -> str:
        if not path:
            ts = int(time.time() * 1000)
            path = str(
                Path.home() / ".openmate" / "browser-screenshots" / f"shot-{ts}.png")
        out = Path(path)
        if out.suffix.lower() != ".png":
            return "错误: 截图路径必须是 .png 文件"
        out.parent.mkdir(parents=True, exist_ok=True)
        session = await self._session()
        try:
            result = await session.call(
                "Page.captureScreenshot", {"format": "png"})
        finally:
            await session.close()
        import base64
        data_b64 = result.get("data") or ""
        if not data_b64:
            return "[错误] 截图返回空数据"
        out.write_bytes(base64.b64decode(data_b64))
        size = out.stat().st_size
        return (
            f"✅ 截图已保存: {out}（{size} 字节）\n"
            f"（在回复中附 MEDIA:{out} 可把截图发给用户）")

    async def extract(self, selector: str = "", links: bool = False) -> str:
        if links:
            expr = (
                "JSON.stringify(Array.from(document.querySelectorAll('a[href]'))"
                ".slice(0, 200).map(a => ({text: (a.innerText || '').trim(), "
                "href: a.href})))")
        elif selector:
            expr = (
                f"JSON.stringify((() => {{ const el = document.querySelector("
                f"{json.dumps(selector)}); return el ? {{found: true, text: "
                f"(el.innerText || el.textContent || '').trim()}} : {{found: "
                f"false}}; }})())")
        else:
            expr = (
                "JSON.stringify({found: true, text: "
                "(document.body ? (document.body.innerText || '') : '')})")
        session = await self._session()
        try:
            raw = await self._eval_scalar(session, expr)
        finally:
            await session.close()
        try:
            data = json.loads(raw or "null")
        except (ValueError, TypeError):
            return "[错误] 提取结果解析失败"
        if links:
            items = data if isinstance(data, list) else []
            if not items:
                return "页面没有提取到任何链接"
            lines = [f"共 {len(items)} 个链接："]
            for it in items:
                lines.append(f"- {it.get('text', '')} | {it.get('href', '')}")
            text = "\n".join(lines)
        else:
            if not isinstance(data, dict) or not data.get("found"):
                return f"错误: 选择器无匹配: {selector}"
            text = str(data.get("text", ""))
            if not text:
                return f"（选择器 {selector} 匹配到元素但无文本内容）"
        return _truncate(text)

    async def _resolve_point(
        self, session: _CdpSession, selector: str,
    ) -> tuple[float, float]:
        expr = (
            f"JSON.stringify((() => {{ const el = document.querySelector("
            f"{json.dumps(selector)}); if (!el) return null; const r = "
            f"el.getBoundingClientRect(); return {{x: r.left + r.width / 2, "
            f"y: r.top + r.height / 2}}; }})())")
        raw = await self._eval_scalar(session, expr)
        try:
            pos = json.loads(raw or "null")
        except (ValueError, TypeError):
            pos = None
        if not isinstance(pos, dict):
            raise BrowserAutomationError(f"选择器无匹配: {selector}")
        return float(pos.get("x", 0)), float(pos.get("y", 0))

    async def click(
        self, selector: str = "", x: Any = None, y: Any = None,
    ) -> str:
        if not selector and (x is None or y is None):
            return "错误: click 需要 selector 或 x+y 坐标"
        session = await self._session()
        try:
            if selector:
                cx, cy = await self._resolve_point(session, selector)
                target = f"{selector} ({cx:.0f},{cy:.0f})"
            else:
                cx, cy = float(x), float(y)
                target = f"坐标 ({cx:.0f},{cy:.0f})"
            # 真实鼠标事件路径（复现用户点击，不用 JS click 冒充）
            for typ in ("mouseMoved", "mousePressed", "mouseReleased"):
                params: dict = {"type": typ, "x": cx, "y": cy,
                                "button": "left", "clickCount": 1}
                if typ == "mouseMoved":
                    params.pop("button")
                    params.pop("clickCount")
                await session.call("Input.dispatchMouseEvent", params)
        finally:
            await session.close()
        return f"✅ 已点击 {target}"

    async def fill(self, selector: str, text: Any) -> str:
        if not selector:
            return "错误: fill 需要 selector 参数"
        if text is None:
            return "错误: fill 需要 text 参数"
        text = str(text)
        session = await self._session()
        try:
            cx, cy = await self._resolve_point(session, selector)
            # 点击聚焦（真实鼠标事件）+ 真实输入事件插入文本
            for typ in ("mousePressed", "mouseReleased"):
                await session.call("Input.dispatchMouseEvent",
                                   {"type": typ, "x": cx, "y": cy,
                                    "button": "left", "clickCount": 1})
            await session.call("Runtime.evaluate", {
                "expression": (
                    f"document.querySelector({json.dumps(selector)}) && "
                    f"document.querySelector({json.dumps(selector)}).focus()")})
            await session.call("Input.insertText", {"text": text})
        finally:
            await session.close()
        return f"✅ 已填入 {len(text)} 字符到 {selector}"

    async def evaluate(self, expression: str) -> str:
        expression = (expression or "").strip()
        if not expression:
            return "错误: eval 需要 expression 参数"
        session = await self._session()
        try:
            result = await session.call(
                "Runtime.evaluate",
                {"expression": expression, "returnByValue": True,
                 "awaitPromise": True, "timeout": int(self.cmd_timeout_s * 1000)},
                timeout=self.cmd_timeout_s)
        finally:
            await session.close()
        res = result.get("result") or {}
        if res.get("subtype") == "error":
            return f"[错误] 表达式执行失败: {res.get('description', '')[:500]}"
        value = res.get("value")
        if value is None and res.get("type") != "undefined":
            # 对象/不可序列化 → 回退 description（截断自报）
            desc = str(res.get("description", ""))[:MAX_RESULT_CHARS]
            return f"结果（不可序列化，已显示描述）: {desc}"
        return _truncate(json.dumps(value, ensure_ascii=False)
                         if not isinstance(value, str) else value)

    async def close_tab(self, tab_id: str = "") -> str:
        target = tab_id or self._current_tab_id
        if not target:
            tabs = await self._list_tabs()
            visible = [
                t for t in tabs
                if t.type == "page" and not t.url.startswith("devtools://")
            ]
            if not visible:
                return "当前没有可关闭的标签页"
            target = visible[0].tab_id
        status, body = await self._http("GET", f"/json/close/{target}")
        if status != 200:
            return f"[错误] 关闭标签页失败（HTTP {status}）: {body[:200]}"
        if target == self._current_tab_id:
            self._current_tab_id = ""
            self._current_ws_url = ""
        return f"✅ 已关闭标签页 {target}"


def _truncate(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return (text[:MAX_RESULT_CHARS]
            + f"\n…（结果超长已截断，共 {len(text)} 字符）")


# ---------------------------------------------------------------------------
# 单例（与 get_group_orchestrator 等同款）
# ---------------------------------------------------------------------------

_browser_singleton: BrowserAutomation | None = None


def get_browser_automation() -> BrowserAutomation:
    global _browser_singleton
    if _browser_singleton is None:
        _browser_singleton = BrowserAutomation()
    return _browser_singleton
