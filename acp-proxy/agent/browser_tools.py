"""browser_tools.py — browser 工具面（路线图 7.1：浏览器自动化 Playwright/CDP）。

BrowserTools = 有界子进程调用 + 确定性输出格式化。worker（agent/browser_worker.py）
在独立 interpreter 里跑 playwright（服务 venv 无 playwright，SOULMATE_BROWSER_PYTHON
默认 /usr/bin/python 实测带 playwright 1.60），JSONL 协议常驻浏览器会话。

诚实状态语义（对标 search_backend 四态纪律，禁静默）：
- 后端不可用（interpreter/worker 起不来）→ 显式"⚠️ 浏览器后端不可用"+原因+启动方法；
- 动作超时/进程崩溃 → 显式报因 + 会话重建提示（页面状态丢失如实告知）；
- 动作失败（选择器没找到等）→ 具体错误文本回注 LLM，不吞。
"""
import logging
import os
import select
import subprocess
import time
from pathlib import Path

logger = logging.getLogger("acp-agent.browser-tools")

DEFAULT_PYTHON = "/usr/bin/python"
DEFAULT_TIMEOUT_S = 30.0
WORKER_START_TIMEOUT_S = 15.0

SUPPORTED_ACTIONS = (
    "goto", "snapshot", "click", "type", "fill", "eval", "screenshot", "back", "close",
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

    def run(self, action: str, params: dict | None = None) -> str:
        action = str(action or "").strip().lower()
        params = dict(params or {})
        if action not in SUPPORTED_ACTIONS:
            return (f"❌ 未知 action: {action!r}。支持: {', '.join(SUPPORTED_ACTIONS)}")
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

    # ── 确定性输出格式化（人类可读，截断自报） ─────────────────────

    @staticmethod
    def _format(action: str, r: dict) -> str:
        head_note = f"{r.get('note', '')}\n" if r.get("note") else ""
        if action == "goto":
            return f"{head_note}✅ 已打开 {r.get('url', '')}\n标题: {r.get('title', '')}"
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
