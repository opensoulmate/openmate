"""browser_tools.py — browser 工具面（路线图 7.1：浏览器自动化 Playwright/CDP）。

BrowserTools = 有界子进程调用 + 确定性输出格式化。worker（agent/browser_worker.py）
在独立 interpreter 里跑 playwright（服务 venv 无 playwright，SOULMATE_BROWSER_PYTHON
默认 /usr/bin/python 实测带 playwright 1.60），JSONL 协议常驻浏览器会话。

诚实状态语义（对标 search_backend 四态纪律，禁静默）：
- 后端不可用（interpreter/worker 起不来）→ 显式"⚠️ 浏览器后端不可用"+原因+启动方法；
- 动作超时/进程崩溃 → 显式报因 + 会话重建提示（页面状态丢失如实告知）；
- 动作失败（选择器没找到等）→ 具体错误文本回注 LLM，不吞。
"""
import json
import logging
import os
import re
import select
import subprocess
import time
import urllib.request
from pathlib import Path

# flow_receipts 纯逻辑层（webview 失活判定同一真源）：sys.path 不可达时现场补路径
try:
    from flow_receipts import webview_lost as _webview_lost
except ImportError:  # pragma: no cover - agent 子进程独立 sys.path 形态
    import sys as _sys
    _ACP_ROOT = str(Path(__file__).resolve().parent.parent)
    if _ACP_ROOT not in _sys.path:
        _sys.path.insert(0, _ACP_ROOT)
    from flow_receipts import webview_lost as _webview_lost

logger = logging.getLogger("acp-agent.browser-tools")

DEFAULT_PYTHON = "/usr/bin/python"
DEFAULT_TIMEOUT_S = 30.0
WORKER_START_TIMEOUT_S = 15.0

SUPPORTED_ACTIONS = (
    "goto", "snapshot", "click", "type", "fill", "eval", "screenshot", "back", "close",
    "resume", "activity", "flows", "replay",
)

_START_HINT = (
    "启动方法：①确认 interpreter 带 playwright（SOULMATE_BROWSER_PYTHON 指向的 "
    "python 需 `import playwright` 成功，默认 /usr/bin/python）②确认 "
    "chromium 已安装（playwright install chromium）③或改用 CDP 附加既有浏览器"
    "（SOULMATE_BROWSER_CDP_URL=http://127.0.0.1:9222）。"
)


class WorkerUnavailable(RuntimeError):
    """worker 无法启动（interpreter 不存在 / playwright 缺失 / 崩溃）。"""


class WorkerTimeout(RuntimeError):
    """单次动作超时。"""


class WorkerCrash(RuntimeError):
    """worker 进程异常退出。"""


class SubprocessWorker:
    """JSONL over stdio 的 worker 子进程封装（懒启动，崩溃可重建）。"""

    def __init__(self, python: str, worker_path: str):
        self.python = python
        self.worker_path = worker_path
        self.proc: subprocess.Popen | None = None
        self._next_id = 0

    def start(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        if not Path(self.python).exists() and not _which(self.python):
            raise WorkerUnavailable(f"interpreter 不存在: {self.python}")
        if not Path(self.worker_path).exists():
            raise WorkerUnavailable(f"worker 脚本不存在: {self.worker_path}")
        try:
            self.proc = subprocess.Popen(
                [self.python, "-u", self.worker_path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
        except OSError as e:
            raise WorkerUnavailable(f"worker 启动失败: {e}") from e
        # 启动探活：import 失败（playwright缺失）会让 worker 立刻退出——
        # 短暂等待后检查进程存活，死了就把 stderr 尾部作为"不可用"原因如实报出
        time.sleep(0.3)
        if self.proc.poll() is not None:
            tail = _stderr_tail(self.proc)
            self.proc = None
            raise WorkerUnavailable(f"worker 启动即退出（多半是 playwright 未安装）: {tail}")

    def request(self, payload: dict, timeout: float) -> dict:
        self.start()
        proc = self.proc
        assert proc is not None and proc.stdin and proc.stdout
        self._next_id += 1
        rid = self._next_id
        import json
        req = {"id": rid, "action": payload.get("action", ""),
               "params": payload.get("params") or {}}
        try:
            proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            proc.stdin.flush()
        except BrokenPipeError as e:
            self._mark_dead()
            raise WorkerCrash(f"worker 管道断裂（进程已退出 code={proc.poll()}）") from e
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                self.close()
                raise WorkerTimeout(f"{timeout:g}s")
            ready, _, _ = select.select([proc.stdout], [], [], min(remain, 1.0))
            if not ready:
                if proc.poll() is not None:
                    tail = _stderr_tail(proc)
                    self._mark_dead()
                    raise WorkerCrash(
                        f"worker 进程退出 code={proc.poll()}（stderr尾部: {tail}）")
                continue
            line = proc.stdout.readline()
            if not line:
                code = proc.poll()
                tail = _stderr_tail(proc)
                self._mark_dead()
                raise WorkerCrash(f"worker 输出EOF退出 code={code}（stderr尾部: {tail}）")
            line = line.strip()
            if not line:
                continue
            import json
            try:
                resp = json.loads(line)
            except Exception:
                continue  # 非协议输出（调试打印）不采信，继续等协议行
            if resp.get("id") == rid:
                return resp

    def _mark_dead(self) -> None:
        self.proc = None

    def close(self) -> None:
        proc, self.proc = self.proc, None
        if proc is None:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def _which(cmd: str) -> str | None:
    import shutil
    return shutil.which(cmd)


def _stderr_tail(proc: subprocess.Popen) -> str:
    try:
        if proc.stderr is None:
            return "(无stderr)"
        data = proc.stderr.read() or ""
        return (data.strip() or "(无stderr输出)")[-300:]
    except Exception:
        return "(stderr读取失败)"


class BrowserTools:
    """browser 工具面：动作校验 + 有界调用 + 诚实状态 + 确定性格式化。

    worker_factory 可注入（测试用 fake worker）；缺省 SubprocessWorker。
    """

    def __init__(self, python: str = "", worker_path: str = "",
                 timeout_s: float = 0.0, shot_dir: str = "",
                 worker_factory=None):
        self.python = python or os.environ.get("SOULMATE_BROWSER_PYTHON", "") or DEFAULT_PYTHON
        self.worker_path = worker_path or str(
            Path(__file__).resolve().parent / "browser_worker.py")
        env_timeout = os.environ.get("SOULMATE_BROWSER_TIMEOUT_S", "")
        try:
            self.timeout_s = float(timeout_s or env_timeout or DEFAULT_TIMEOUT_S)
        except ValueError:
            self.timeout_s = DEFAULT_TIMEOUT_S
        self.shot_dir = shot_dir or os.environ.get("SOULMATE_BROWSER_SHOT_DIR", "") or str(
            Path.home() / ".hermes" / "soulmate" / "browser_shots")
        self._worker_factory = worker_factory
        self._worker = None
        self._lost_state = False  # 上次崩溃/超时后页面状态丢失（下次调用如实告知）

    # ── worker 生命周期 ──────────────────────────────────────────

    def _get_worker(self):
        if self._worker is not None and getattr(self._worker, "alive", lambda: True)():
            return self._worker
        if self._worker_factory is not None:
            self._worker = self._worker_factory(self.python, self.worker_path, self.timeout_s)
        else:
            self._worker = SubprocessWorker(self.python, self.worker_path)
        return self._worker

    # ── 工具入口 ────────────────────────────────────────────────

    def run(self, action: str, params: dict | None = None,
            mode: str = "", secret: bool = False) -> str:
        """执行浏览器动作。mode 覆盖 SOULMATE_BROWSER_MODE（'app'=应用通道有头，
        'worker'=Playwright 独立会话）；secret=True 时本步输入含凭据（回执脱敏 ***）。"""
        action = str(action or "").strip().lower()
        params = dict(params or {})
        if action not in SUPPORTED_ACTIONS:
            return (f"❌ 未知 action: {action!r}。支持: {', '.join(SUPPORTED_ACTIONS)}")
        # ── activity=读用户全端操作流（OpenSense 行为感知）：纯服务端拉取，
        #    不走执行通道（懂用户在做什么、「这个地方」指的哪）。
        if action == "activity":
            return self._run_activity(params)
        # ── flows/replay=录制流程配方（录制→回放）：清单/查看/按流程重复执行。
        if action == "flows":
            return self._run_flows(params)
        if action == "replay":
            return self._run_replay(params)
        # ── 默认模式=应用通道：任务发给 OpenMate 内置浏览器执行（真浏览器同一扇窗，
        #    用户随时接管）。SOULMATE_BROWSER_MODE=worker 走独立 playwright 子进程；
        #    mode 入参可显式覆盖（流程分层路由：headed=app / headless=worker）。
        mode = str(mode or os.environ.get("SOULMATE_BROWSER_MODE", "") or "app").strip().lower()
        if mode == "app":
            return self._run_app_mode(action, params, secret=secret)
        # screenshot 未给路径 → 自动落 shot_dir（返回路径供 MEDIA: 下发）
        if action == "screenshot" and not str(params.get("path", "") or "").strip():
            os.makedirs(self.shot_dir, exist_ok=True)
            params["path"] = str(Path(self.shot_dir) / f"shot-{int(time.time() * 1000)}.png")
        timeout = self.timeout_s
        # 动作级超时必须短于请求级硬超时：等长时 watchdog 先开火，选择器没找到
        # 之类的真实错误会被误报成"会话超时重建"（实测 smoke 暴露）。留 5s 余量让
        # worker 的真实错误（TimeoutError: selector 等）先回来如实报因。
        if "timeout_ms" not in params and timeout > 8:
            params["timeout_ms"] = int((timeout - 5) * 1000)
        try:
            resp = self._get_worker().request({"action": action, "params": params}, timeout)
        except WorkerUnavailable as e:
            return f"⚠️ 浏览器后端不可用：{e}\n{_START_HINT}"
        except WorkerTimeout as e:
            self._lost_state = True
            return (f"⚠️ 浏览器动作超时（{e}，上限 {timeout:g}s 可用 "
                    f"SOULMATE_BROWSER_TIMEOUT_S 调整）：{action} 未确认完成。"
                    f"worker 已重启，页面状态丢失，需要重新 goto。")
        except WorkerCrash as e:
            self._lost_state = True
            return (f"⚠️ 浏览器进程异常退出：{e}。"
                    f"下次调用会重建会话，页面状态丢失，需要重新 goto。")
        note = ""
        if self._lost_state:
            self._lost_state = False
            note = "（⚠️ 浏览器已重建，此前页面状态已丢失）\n"
        if not resp.get("ok"):
            return f"{note}❌ 浏览器操作失败（{action}）：{resp.get('error', '未知错误')}"
        return note + self._format(action, resp.get("result") or {})

    # ── 应用通道模式（默认）：任务经 acp-proxy browser_bridge 发给 OpenMate 内置浏览器 ──

    @staticmethod
    def _bridge_base() -> str:
        return str(os.environ.get("SOULMATE_BROWSER_HTTP", "") or "http://127.0.0.1:8092").rstrip("/")

    def _run_flows(self, params: dict) -> str:
        """流程配方清单/查看（录制→回放的沉淀库）。flow=名称/ID 时输出完整步骤。"""
        flow = str(params.get("flow", "") or "").strip()
        try:
            if flow:
                url = f"{self._bridge_base()}/api/browser/flows/{flow}"
            else:
                url = f"{self._bridge_base()}/api/browser/flows"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            return f"❌ 读取流程库失败：{e}"
        if flow:
            if not data.get("ok", True):
                return f"❌ {data.get('error', '流程不存在')}"
            steps = data.get("steps") or []
            lines = [f"【流程】{data.get('name', flow)}（{len(steps)}步，参数：{', '.join(data.get('params') or []) or '无'}）"]
            for i, s in enumerate(steps, 1):
                desc = s.get("selector") or s.get("text") or s.get("url") or ""
                val = s.get("value", "")
                shown = "***" if s.get("secret") else (val if len(str(val)) <= 40 else str(val)[:40] + "…")
                lines.append(f"{i}. [{s.get('action')}] {desc}" + (f" = {shown}" if val else ""))
            return "\n".join(lines)
        flows = data.get("flows") or []
        if not flows:
            return "（流程库为空——让用户在内置浏览器点「⏺ 录制」录一段业务操作即可沉淀）"
        lines = ["【已保存流程】（用 replay(flow=名称) 重复执行）"]
        for f in flows:
            lines.append(f"- {f.get('name')}（{f.get('steps')}步，参数：{', '.join(f.get('params') or []) or '无'}）")
        return "\n".join(lines)

    def _run_replay(self, params: dict) -> str:
        """按录制流程重复执行（智能回放：逐步走应用通道，⏸接管即停，缺参数现场问）。

        flow=流程名/ID；data={参数:值} 填 {{param}} 占位；dry_run=true 只预览不执行。
        """
        flow = str(params.get("flow", "") or "").strip()
        if not flow:
            return "❌ 缺 flow（流程名/ID）。先调 flows 看有哪些流程。"
        data = dict(params.get("data") or {})
        try:
            with urllib.request.urlopen(f"{self._bridge_base()}/api/browser/flows/{flow}", timeout=5) as r:
                rec = json.loads(r.read().decode())
        except Exception as e:
            return f"❌ 读取流程失败：{e}"
        if not rec.get("ok", True):
            return f"❌ {rec.get('error', '流程不存在')}"
        steps = rec.get("steps") or []
        missing: set[str] = set()

        def subst(text: str) -> str:
            def _rep(m):
                key = m.group(1)
                if key in data:
                    return str(data[key])
                missing.add(key)
                return m.group(0)
            return re.sub(r"\{\{(\w+)\}\}", _rep, str(text or ""))

        # 预检：参数占位齐全才开跑（缺参现场问，别跑到一半卡住）
        for s in steps:
            for field in ("value", "url"):
                if s.get(field):
                    subst(s[field])
        if missing:
            return (f"❌ 流程「{rec.get('name')}」需要参数：{', '.join(sorted(missing))}"
                    f"（敏感字段不入流程，回放时提供 data={{...}} 填入后重跑）")
        if params.get("dry_run"):
            return self._run_flows({"flow": flow}) + "\n（dry_run 预览，未执行）"

        results = []
        for i, s in enumerate(steps, 1):
            action = str(s.get("action", ""))
            p: dict = {}
            if action == "goto":
                p["url"] = subst(s.get("url", ""))
            elif action in ("click",):
                p = {"selector": s.get("selector", ""), "text": s.get("text", "")}
            elif action in ("fill", "type"):
                p = {"selector": s.get("selector", ""), "text": subst(s.get("value", "")),
                     "submit": bool(s.get("submit"))}
            else:
                results.append(f"{i}. [{action}] ⏭ 跳过（不支持的录制步骤）")
                continue
            out = self._run_app_mode(action, p)
            results.append(f"{i}. [{action}] {out.strip()[:160]}")
            if "⏸" in out:
                results.append("—— 回放已中止（用户接管），用户说\"继续\"后可重新 replay")
                break
        return f"【回放】{rec.get('name')}（{len(steps)}步）\n" + "\n".join(results)

    def _run_activity(self, params: dict) -> str:
        """读用户全端操作流（OpenSense 行为感知 · Sense 第五通道）。

        data=recent 操作流明细（默认）/ context 摘要块；minutes 时间窗（默认 10）。
        用户说「这个地方/这个按钮」时：最近一条 hover 事件=鼠标指着的元素。
        """
        kind = str(params.get("data", "recent") or "recent").strip().lower()
        base = str(os.environ.get("SOULMATE_SENSE_URL", "http://127.0.0.1:8090") or "").rstrip("/")
        try:
            if kind == "context":
                minutes = float(params.get("minutes", 10) or 10)
                url = f"{base}/api/sense/behavior/context?minutes={minutes:g}"
            else:
                limit = int(params.get("limit", 40) or 40)
                since = float(params.get("since", 0) or 0)
                url = f"{base}/api/sense/behavior/recent?limit={limit}&since={since:g}"
            with urllib.request.urlopen(url, timeout=5) as r:
                data = json.loads(r.read().decode())
        except Exception as e:
            return f"❌ 读取用户操作流失败：{e}"
        if kind == "context":
            ctx = str(data.get("context", "") or "")
            return ctx or "（近期无用户操作记录）"
        events = data.get("events") or []
        if not events:
            return "（近期无用户操作记录）"
        lines = []
        for e in events:
            t = time.strftime("%H:%M", time.localtime(float(e.get("ts", 0) or 0)))
            src = "🌐" if e.get("source") == "browser" else "🖥"
            lines.append(
                f"{t} {src}[{e.get('type', '')}] {e.get('target', '')} {e.get('detail', '')}".rstrip()
            )
        return "【用户近期操作流】（hover=鼠标指着的元素，「这个地方」即指它）\n" + "\n".join(lines)

    def _run_app_mode(self, action: str, params: dict, secret: bool = False) -> str:
        """HTTP 投递任务 + 长轮询结果。结果与 worker 模式同构 → _format 无改动消费。

        webview 自救（单步有界重试，断点续跑）：结果含「webview 已关闭/未就绪」→
        ensure-webview（bridge 控制通道，有界 10s）→ 重试该步一次；成功继续（回执标注
        「(重试1)」），仍败如实报「（重开后仍失败）」。禁止无限重试（已自救标注的错误
        不再触发——execute_flow_steps 层同款判据，两层防线叠加仍为有界）。
        """
        out = self._app_dispatch(action, params, secret=secret)
        if not (str(out).lstrip().startswith(("❌", "⚠️")) and _webview_lost(out)):
            return out
        # ── 自救：ensure-webview（有界）→ 重试1次，重试进回执（账单不掩盖自救过程）──
        ens_ok, ens_note = self._ensure_webview()
        out2 = self._app_dispatch(action, params, secret=secret,
                                  receipt_action=f"{action}(重试1)",
                                  fail_note="（重开后仍失败）")
        if not str(out2).lstrip().startswith(("❌", "⚠️")):
            return f"（webview 自救：{'成功' if ens_ok else '未确认'}：{ens_note}→已重试1次）\n" + out2
        return out2 if "重开后仍失败" in str(out2) else f"{out2}（重开后仍失败）"

    def _ensure_webview(self, timeout_s: float = 10.0) -> tuple[bool, str]:
        """ensure-webview 自救口（bridge 控制通道）：应用自动重开浏览器 tab 并回 webview-ready。

        等待有界（timeout_s，缺省 10s）：超时/不可达如实报因，绝不挂死执行链。
        """
        base = str(os.environ.get("SOULMATE_BROWSER_HTTP", "") or "http://127.0.0.1:8092").rstrip("/")
        try:
            req = urllib.request.Request(
                f"{base}/api/browser/ensure-webview",
                data=json.dumps({"timeout": float(timeout_s),
                                 "session_id": str(os.environ.get("SOULMATE_SESSION_ID", "") or "")},
                                ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=float(timeout_s) + 5) as resp:
                out = json.loads(resp.read().decode())
        except Exception as e:
            return False, f"ensure-webview 通道不可达：{e}"
        if out.get("ok"):
            return True, "webview 已自动重开就绪"
        return False, f"webview 重开未就绪：{str(out.get('error', '未知错误'))}"

    def _app_dispatch(self, action: str, params: dict, secret: bool = False,
                      receipt_action: str = "", fail_note: str = "") -> str:
        """HTTP 投递任务 + 长轮询结果（单次派发，不做重试——重试在 _run_app_mode 层）。
        secret=True 透传给 bridge（执行回执按 *** 脱敏，密码/凭据不落回执）。
        receipt_action/fail_note 透传：重试步回执标注「(重试1)」/失败附「（重开后仍失败）」。"""
        import json
        import urllib.request
        base = str(os.environ.get("SOULMATE_BROWSER_HTTP", "") or "http://127.0.0.1:8092").rstrip("/")
        note = ""
        if self._lost_state:
            self._lost_state = False
            note = "（⚠️ 此前有动作未确认完成，页面状态可能已变化）\n"
        session_id = str(os.environ.get("SOULMATE_SESSION_ID", "") or "")
        try:
            req = urllib.request.Request(
                f"{base}/api/browser/task",
                data=json.dumps({"action": action, "params": params,
                                 "session_id": session_id, "secret": bool(secret),
                                 "receipt_action": str(receipt_action or ""),
                                 "fail_note": str(fail_note or "")},
                                ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                tid = json.loads(resp.read().decode()).get("task_id", "")
        except Exception as e:
            return f"⚠️ 浏览器通道不可达（{base}）：{e}\n确认 acp-proxy 在跑。"
        wait_s = min(float(self.timeout_s or 60), 40.0)
        try:
            with urllib.request.urlopen(
                    f"{base}/api/browser/result/{tid}?wait_s={wait_s:g}",
                    timeout=wait_s + 10) as resp:
                out = json.loads(resp.read().decode())
        except Exception as e:
            self._lost_state = True
            return f"⚠️ 浏览器结果轮询失败：{e}（动作未确认完成）"
        if not out.get("ok"):
            err = str(out.get("error", "未知错误"))
            if "超时" in err or "断开" in err:
                self._lost_state = True
            return f"{note}❌ 浏览器操作失败（{action}）：{err}"
        return note + self._format(action, out.get("result") or {})

    # ── 确定性输出格式化（人类可读，截断自报） ─────────────────────

    @staticmethod
    def _format(action: str, r: dict) -> str:
        head_note = f"{r.get('note', '')}\n" if r.get("note") else ""
        if action == "goto":
            return f"{head_note}✅ 已打开 {r.get('url', '')}\n标题: {r.get('title', '')}"
        if action == "resume":
            return f"{head_note}▶️ 已恢复自动操作（用户接管状态清除）"
        if action == "snapshot":
            lines = [
                f"📄 页面快照",
                f"标题: {r.get('title', '')}",
                f"URL: {r.get('url', '')}",
                f"正文（{r.get('total_chars', 0)} 字符"
                + ("，已截断" if r.get("truncated") else "") + "）:",
                str(r.get("text", "")),
            ]
            links = r.get("links") or []
            links_total = r.get("links_total", len(links))
            if links:
                lines.append(f"链接（{len(links)}/{links_total}）:")
                for lk in links:
                    lines.append(f"- [{lk.get('text', '')[:60]}]({lk.get('href', '')})")
            return head_note + "\n".join(lines)
        if action in ("click",):
            return (f"{head_note}✅ 已点击 {r.get('clicked', '')}\n"
                    f"当前页面: {r.get('title', '')} ({r.get('url', '')})")
        if action in ("type", "fill"):
            return (f"{head_note}✅ 已输入 {r.get('chars', 0)} 字符到 {r.get('typed_into', '')}"
                    + ("（已回车提交）" if r.get("submitted") else "")
                    + f"\n当前页面: {r.get('title', '')} ({r.get('url', '')})")
        if action == "eval":
            body = str(r.get("result", ""))
            suffix = ("\n（结果已截断，共 "
                      f"{r.get('total_chars', 0)} 字符）" if r.get("truncated") else "")
            return f"{head_note}JS 执行结果:\n{body}{suffix}"
        if action == "screenshot":
            return (f"{head_note}📸 截图已保存: {r.get('path', '')}"
                    f"（{r.get('size', 0)} 字节）\n页面: {r.get('title', '')} ({r.get('url', '')})")
        if action == "back":
            return f"{head_note}✅ 已后退\n当前页面: {r.get('title', '')} ({r.get('url', '')})"
        if action == "close":
            return "✅ 浏览器会话已关闭"
        return f"{head_note}{r}"
