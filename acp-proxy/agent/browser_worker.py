"""browser_worker.py — 浏览器自动化底层驱动（路线图 7.1：Playwright/CDP）。

独立子进程运行（interpreter 需带 playwright——acp-proxy 服务 venv 无 playwright，
故走子进程隔离；默认 SOULMATE_BROWSER_PYTHON=/usr/bin/python 实测带 playwright 1.60）。
JSONL 协议（stdin 收请求 / stdout 收响应）：
    请求  {"id": N, "action": "...", "params": {...}}
    响应  {"id": N, "ok": true,  "result": {...}}
          {"id": N, "ok": false, "error": "..."}
设计：
- 常驻浏览器会话：页面状态跨调用保留（goto→click→snapshot 连续操作）；
- 单动作失败 ok:false 不杀进程，下次调用继续用同一会话；
- 连接方式二选一：SOULMATE_BROWSER_CDP_URL 附加既有浏览器（CDP），
  否则本地 launch chromium（SOULMATE_BROWSER_HEADLESS=0 可显示界面）。
本文件只做确定性页面操作，不做 LLM；格式化输出归 agent/browser_tools.py。
"""
import json
import os
import sys

DEFAULT_TIMEOUT_MS = 30000
SNAPSHOT_MAX_CHARS = 4000
LINKS_MAX = 25
EVAL_MAX_CHARS = 4000

_STATE: dict = {"pw": None, "browser": None, "context": None, "page": None}


def _ensure_page():
    """懒启动浏览器会话（launch 或 CDP 附加），返回 (page, note)。"""
    if _STATE["page"] is not None and not _STATE["page"].is_closed():
        return _STATE["page"], ""
    from playwright.sync_api import sync_playwright  # 延迟到首次动作才付 import 成本

    if _STATE["pw"] is None:
        _STATE["pw"] = sync_playwright().start()
    pw = _STATE["pw"]
    cdp_url = str(os.environ.get("SOULMATE_BROWSER_CDP_URL", "") or "").strip()
    note = ""
    if cdp_url:
        # CDP 附加既有浏览器（用户已开的 Chromium），不另起实例
        _STATE["browser"] = pw.chromium.connect_over_cdp(cdp_url)
        contexts = _STATE["browser"].contexts
        _STATE["context"] = contexts[0] if contexts else _STATE["browser"].new_context()
        note = f"（已附加CDP浏览器 {cdp_url}）"
    else:
        headless = str(os.environ.get("SOULMATE_BROWSER_HEADLESS", "1")).strip() not in (
            "0", "false", "no", "off")
        _STATE["browser"] = pw.chromium.launch(headless=headless)
        _STATE["context"] = _STATE["browser"].new_context()
        note = "（已启动本地chromium）"
    _STATE["page"] = _STATE["context"].new_page()
    return _STATE["page"], note


def _timeout_ms(params: dict) -> int:
    try:
        v = int(params.get("timeout_ms") or 0)
        if v > 0:
            return v
    except (TypeError, ValueError):
        pass
    return DEFAULT_TIMEOUT_MS


def _act_goto(params: dict) -> dict:
    url = str(params.get("url", "") or "").strip()
    if not url:
        return {"error": "goto 需要 url 参数"}
    page, note = _ensure_page()
    wait_until = str(params.get("wait_until") or "domcontentloaded")
    page.goto(url, wait_until=wait_until, timeout=_timeout_ms(params))
    return {"url": page.url, "title": page.title(), "note": note}


def _act_snapshot(params: dict) -> dict:
    page, note = _ensure_page()
    try:
        max_chars = int(params.get("max_chars") or 0)
    except (TypeError, ValueError):
        max_chars = 0
    max_chars = max_chars if max_chars > 0 else SNAPSHOT_MAX_CHARS
    total_text = page.evaluate("() => (document.body ? document.body.innerText : '')") or ""
    truncated = len(total_text) > max_chars
    text = total_text[:max_chars]
    raw_links = page.evaluate(
        "() => Array.from(document.querySelectorAll('a[href]'))"
        ".map(a => ({text: (a.innerText || '').trim().slice(0, 80), href: a.href}))") or []
    links = raw_links[:LINKS_MAX]
    return {
        "url": page.url, "title": page.title(), "text": text,
        "total_chars": len(total_text), "truncated": truncated,
        "links": links, "links_total": len(raw_links), "note": note,
    }


def _act_click(params: dict) -> dict:
    selector = str(params.get("selector", "") or "").strip()
    if not selector:
        return {"error": "click 需要 selector 参数（CSS选择器）"}
    page, note = _ensure_page()
    page.click(selector, timeout=_timeout_ms(params))
    return {"clicked": selector, "url": page.url, "title": page.title(), "note": note}


def _act_type(params: dict) -> dict:
    selector = str(params.get("selector", "") or "").strip()
    text = str(params.get("text", "") or "")
    if not selector:
        return {"error": "type 需要 selector 参数（CSS选择器）"}
    page, note = _ensure_page()
    page.fill(selector, text, timeout=_timeout_ms(params))
    if params.get("submit"):
        page.press(selector, "Enter")
    return {"typed_into": selector, "chars": len(text),
            "submitted": bool(params.get("submit")),
            "url": page.url, "title": page.title(), "note": note}


def _act_fill(params: dict) -> dict:
    # fill 与 type 的页面语义一致（清空后填入），独立动作名方便LLM直觉选择
    return _act_type(params)


def _act_eval(params: dict) -> dict:
    js = str(params.get("js", "") or "").strip()
    if not js:
        return {"error": "eval 需要 js 参数（JS表达式）"}
    page, note = _ensure_page()
    value = page.evaluate(js)
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = str(value)
    truncated = len(value) > EVAL_MAX_CHARS
    return {"result": value[:EVAL_MAX_CHARS], "truncated": truncated,
            "total_chars": len(value), "note": note}


def _act_screenshot(params: dict) -> dict:
    path = str(params.get("path", "") or "").strip()
    if not path:
        return {"error": "screenshot 需要 path 参数（PNG保存路径）"}
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    page, note = _ensure_page()
    page.screenshot(path=path)
    return {"path": path, "size": os.path.getsize(path),
            "url": page.url, "title": page.title(), "note": note}


def _act_back(params: dict) -> dict:
    page, note = _ensure_page()
    page.go_back(timeout=_timeout_ms(params))
    return {"url": page.url, "title": page.title(), "note": note}


def _act_close(params: dict) -> dict:
    browser = _STATE.get("browser")
    try:
        if browser is not None:
            browser.close()
    finally:
        _STATE["browser"] = None
        _STATE["context"] = None
        _STATE["page"] = None
    return {"closed": True}


ACTIONS = {
    "goto": _act_goto,
    "snapshot": _act_snapshot,
    "click": _act_click,
    "type": _act_type,
    "fill": _act_fill,
    "eval": _act_eval,
    "screenshot": _act_screenshot,
    "back": _act_back,
    "close": _act_close,
}


def _handle(req: dict) -> dict:
    rid = req.get("id")
    action = str(req.get("action", "") or "").strip().lower()
    params = req.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    handler = ACTIONS.get(action)
    if handler is None:
        return {"id": rid, "ok": False,
                "error": f"未知 action: {action!r}（支持: {', '.join(sorted(ACTIONS))}）"}
    try:
        result = handler(params)
    except Exception as e:  # 单动作失败只报因，不杀会话进程
        return {"id": rid, "ok": False, "error": f"{type(e).__name__}: {e}"[:500]}
    if isinstance(result, dict) and "error" in result and len(result) == 1:
        return {"id": rid, "ok": False, "error": result["error"]}
    return {"id": rid, "ok": True, "result": result}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as e:
            resp = {"id": -1, "ok": False, "error": f"请求JSON解析失败: {e}"}
        else:
            resp = _handle(req)
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
