"""路线图 7.1 浏览器自动化：browser 工具面（Playwright/CDP）。

覆盖：JSONL worker 协议真子进程往返 / 后端不可用·超时·崩溃三态显式报因 /
状态丢失如实告知 / 截断自报 / screenshot 自动落盘 / 未知 action 报清单 /
sandbox 网络类归类 / soulmate_agent 四点接线 + __new__ 真实 dispatch 烟测。
（真实 Playwright 页面操作实证走"真实机器烟测"，不在单测里付浏览器启动成本。）
"""

import sys
import textwrap
import time
from pathlib import Path

import pytest

from agent.browser_tools import BrowserTools, SubprocessWorker, SUPPORTED_ACTIONS
from agent.sandbox_policy import SandboxPolicy


# ── fake worker（真实子进程，真JSONL协议；不依赖 playwright）──────────

FAKE_WORKER = textwrap.dedent('''
    import json, os, sys, time
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        rid, action = req.get("id"), req.get("action", "")
        params = req.get("params") or {}
        # 测试触发器挂在合法 action 上（BrowserTools 先做 action 校验）：
        # eval js="__crash__" 崩溃 / js="__sleep__" 慢动作 / click #missing 失败
        if action == "eval" and params.get("js") == "__crash__":
            # 不回协议行直接崩——测 EOF→WorkerCrash 显式报因
            os._exit(9)
        if action == "eval" and params.get("js") == "__sleep__":
            time.sleep(float(params.get("seconds", 3)))
            resp = {"id": rid, "ok": True, "result": {"slept": True}}
        elif action == "goto":
            resp = {"id": rid, "ok": True,
                    "result": {"url": params.get("url", ""), "title": "T", "note": "(已启动本地chromium)"}}
        elif action == "snapshot":
            resp = {"id": rid, "ok": True, "result": {
                "url": "u", "title": "T", "text": "hello", "total_chars": 5,
                "truncated": False, "links": [{"text": "a", "href": "h"}],
                "links_total": 1, "note": ""}}
        elif action == "click" and params.get("selector") == "#missing":
            resp = {"id": rid, "ok": False, "error": "选择器没找到: #missing"}
        elif action == "screenshot":
            p = params.get("path", "")
            open(p, "wb").write(b"PNG")
            resp = {"id": rid, "ok": True,
                    "result": {"path": p, "size": 3, "url": "u", "title": "T", "note": ""}}
        else:
            resp = {"id": rid, "ok": True, "result": {"action": action, **params}}
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\\n")
        sys.stdout.flush()
''')

CRASH_ON_START_WORKER = textwrap.dedent('''
    import sys
    sys.stderr.write("ModuleNotFoundError: No module named 'playwright'\\n")
    sys.exit(1)
''')


@pytest.fixture
def fake_tools(tmp_path):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    tools = BrowserTools(python=sys.executable, worker_path=str(script),
                         timeout_s=5.0, shot_dir=str(tmp_path / "shots"))
    yield tools
    if tools._worker is not None:
        tools._worker.close()


# ── 协议层：真子进程 JSONL 往返 ─────────────────────────────────────


def test_subprocess_worker_roundtrip(tmp_path):
    script = tmp_path / "w.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    w = SubprocessWorker(sys.executable, str(script))
    try:
        resp = w.request({"action": "goto", "params": {"url": "http://x"}}, timeout=5)
        assert resp["ok"] is True and resp["result"]["url"] == "http://x"
        resp2 = w.request({"action": "snapshot", "params": {}}, timeout=5)
        assert resp2["id"] != resp["id"]  # id 递增，串扰可辨
        assert resp2["result"]["text"] == "hello"
    finally:
        w.close()


def test_subprocess_worker_crash_then_respawn(tmp_path):
    script = tmp_path / "w.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    tools = BrowserTools(python=sys.executable, worker_path=str(script),
                         timeout_s=5.0, shot_dir=str(tmp_path))
    msg = tools.run("eval", {"js": "__crash__"})
    assert "异常退出" in msg and "页面状态丢失" in msg
    # 下次调用重建会话，状态丢失如实告知一次
    msg2 = tools.run("goto", {"url": "http://x"})
    assert "已重建" in msg2 and "此前页面状态已丢失" in msg2
    assert "http://x" in msg2
    # 再下一次不再重复告警
    msg3 = tools.run("snapshot")
    assert "已重建" not in msg3
    tools._worker.close()


def test_timeout_explicit_and_state_lost(tmp_path):
    script = tmp_path / "w.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")
    tools = BrowserTools(python=sys.executable, worker_path=str(script),
                         timeout_s=0.5, shot_dir=str(tmp_path))
    msg = tools.run("eval", {"js": "__sleep__", "seconds": 3})
    assert "超时" in msg and "SOULMATE_BROWSER_TIMEOUT_S" in msg and "页面状态丢失" in msg
    tools._worker.close()


# ── 诚实状态语义 ──────────────────────────────────────────────────


def test_backend_unavailable_python_missing():
    tools = BrowserTools(python="/nonexistent/python", worker_path="/nonexistent/w.py",
                         timeout_s=2.0)
    msg = tools.run("goto", {"url": "http://x"})
    assert "浏览器后端不可用" in msg and "启动方法" in msg
    # 不可用不吞：不返回空成功
    assert "✅" not in msg


def test_backend_unavailable_worker_dies_on_start(tmp_path):
    script = tmp_path / "dead.py"
    script.write_text(CRASH_ON_START_WORKER, encoding="utf-8")
    tools = BrowserTools(python=sys.executable, worker_path=str(script), timeout_s=2.0)
    msg = tools.run("goto", {"url": "http://x"})
    assert "浏览器后端不可用" in msg
    assert "playwright" in msg  # stderr 尾部如实带出原因


def test_unknown_action_lists_supported():
    tools = BrowserTools(python="/nonexistent/python", worker_path="x", timeout_s=1)
    msg = tools.run("frobnicate")
    assert "未知 action" in msg
    for a in SUPPORTED_ACTIONS:
        assert a in msg


# ── 输出格式化（确定性+截断自报）────────────────────────────────────


def test_format_snapshot_truncation_self_report():
    out = BrowserTools._format("snapshot", {
        "url": "u", "title": "T", "text": "abc",
        "total_chars": 99999, "truncated": True,
        "links": [{"text": "x", "href": "h"}], "links_total": 40, "note": ""})
    assert "已截断" in out and "99999" in out
    assert "1/40" in out  # 链接也自报截断
    assert "abc" in out


def test_format_eval_truncation_self_report():
    out = BrowserTools._format("eval", {"result": "x" * 10, "truncated": True,
                                        "total_chars": 5000, "note": ""})
    assert "已截断" in out and "5000" in out


def test_screenshot_auto_path_and_format(fake_tools):
    msg = fake_tools.run("screenshot", {})
    assert "截图已保存" in msg
    # 自动路径落在 shot_dir 下且文件真实存在
    line = [l for l in msg.split("\n") if "截图已保存" in l][0]
    path = line.split("截图已保存: ")[1].split("（")[0]
    assert Path(path).exists()
    assert str(Path(path).parent).endswith("shots")


def test_tool_error_surfaces_reason(fake_tools):
    msg = fake_tools.run("click", {"selector": "#missing"})
    assert "浏览器操作失败" in msg and "选择器没找到" in msg


# ── sandbox 归类（浏览器直接出网，受限会话同款收缩）──────────────────


def test_browser_is_network_tool():
    assert SandboxPolicy.is_network_tool("browser") is True
    assert SandboxPolicy.is_network_tool("read_file") is False


# ── soulmate_agent 接线护栏 + 真实 dispatch 烟测 ─────────────────────


def _src() -> str:
    return (Path(__file__).resolve().parent.parent
            / "agent" / "soulmate_agent.py").read_text(encoding="utf-8")


def test_wiring_source_guards():
    src = _src()
    # 工具定义进 LLM 工具面
    assert '"name": "browser"' in src
    assert "builtin_tools.extend(browser_tool)" in src
    # 主循环 dispatch + code-mode 批内 dispatch 两处调用 + 共享入口定义
    assert src.count("self._browser_dispatch(func_args, session_id)") == 2
    assert "def _browser_dispatch" in src
    # 系统提示工具清单
    assert "- browser: 浏览器自动化" in src
    # sandbox 网络类收口
    sandbox = (Path(__file__).resolve().parent.parent
               / "agent" / "sandbox_policy.py").read_text(encoding="utf-8")
    assert '"browser"' in sandbox


def test_real_dispatch_smoke(fake_tools):
    from agent.soulmate_agent import SoulMateAgent
    agent = SoulMateAgent.__new__(SoulMateAgent)
    agent._browser_tools_inst = fake_tools
    out = agent._browser_dispatch({"action": "goto", "url": "http://x", "timeout_ms": 100})
    assert "已打开 http://x" in out and "T" in out
    out2 = agent._browser_dispatch({"action": "snapshot"})
    assert "页面快照" in out2 and "hello" in out2


def test_action_timeout_shorter_than_request_timeout():
    """动作级 timeout_ms 必须短于请求级硬超时——等长时 watchdog 先开火，
    选择器没找到的真实错误会被误报成会话超时重建（真实 smoke 暴露的缺口）。"""
    captured = {}

    class CaptureWorker:
        def alive(self):
            return True

        def request(self, payload, timeout):
            captured["params"] = payload.get("params") or {}
            captured["timeout"] = timeout
            return {"id": 1, "ok": True, "result": {"closed": True}}

        def close(self):
            pass

    tools = BrowserTools(python="x", worker_path="y", timeout_s=30.0,
                         worker_factory=lambda *a, **k: CaptureWorker())
    tools.run("close")
    assert captured["timeout"] == 30.0
    assert captured["params"]["timeout_ms"] == 25000  # 留5s余量给真实错误回程
    # 显式给 timeout_ms 时不覆盖
    tools.run("close", {"timeout_ms": 1234})
    assert captured["params"]["timeout_ms"] == 1234
    # 短预算（≤8s）不再压缩，免得动作级超时被压到无意义
    tools.timeout_s = 4.0
    tools.run("close")
    assert "timeout_ms" not in captured["params"]


def test_dispatch_lazy_init_constructs_browsertools(monkeypatch):
    import agent.browser_tools as bt
    import agent.soulmate_agent as sa

    made = {}

    class FakeTools:
        def __init__(self, *a, **k):
            made["init"] = True

        def run(self, action, params):
            return f"FAKE:{action}:{params.get('url', '')}"

    monkeypatch.setattr(sa, "BrowserTools", FakeTools)
    agent = sa.SoulMateAgent.__new__(sa.SoulMateAgent)
    out = agent._browser_dispatch({"action": "goto", "url": "http://y"})
    assert made.get("init") is True          # lazy-init 真的构造了
    assert out == "FAKE:goto:http://y"       # 参数透传（action剥离）
