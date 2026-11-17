#!/usr/bin/env python3
"""
下载页「本机」目标端到端验收（xvfb 模拟真实启动路径）：
  启动桌面壳 → /download → 新建下载 → 选「本机」→ HTTP 下载 → 文件落盘（sha256 对账）

模拟两条真实启动路径：
  --mode dev       桌面图标路径：desktop/launch.sh 同款（electron . 源码运行）
  --mode packaged  已安装版路径：electron-builder 产物（linux-unpacked/openmate 等），
                   验证 asar 内 preload 桥 + resources/download-engine 引擎解析

用法：
  python3 desktop/e2e-local-engine.py --mode dev
  python3 desktop/e2e-local-engine.py --mode packaged --binary desktop/dist/linux-unpacked/openmate

依赖：python3 websockets（pip install websockets）；xvfb-run；本机 :3000 OpenMate 前端可访问。
会把临时测试文件下载进系统下载目录，验收后自动清理；只杀自己拉起的进程树。
"""
import argparse
import asyncio
import hashlib
import http.server
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

SCRATCH = pathlib.Path(tempfile.mkdtemp(prefix="om-e2e-"))
FILE_NAME = f"e2e-local-{int(time.time())}.bin"
PAYLOAD = SCRATCH / FILE_NAME
PAYLOAD_SIZE = 4 * 1024 * 1024


# ── 最小 CDP 客户端 ─────────────────────────────────────────────
def list_targets(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10) as r:
        return json.load(r)


class CDP:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self._id = 0
        self.ws = None

    async def connect(self):
        import websockets
        self.ws = await websockets.connect(self.ws_url, max_size=256 * 1024 * 1024)

    async def send(self, method, **params):
        assert self.ws is not None, "connect() first"
        self._id += 1
        mid = self._id
        await self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def eval(self, expr, await_promise=True):
        res = await self.send("Runtime.evaluate", expression=expr, returnByValue=True,
                              awaitPromise=await_promise, userGesture=True)
        if res.get("exceptionDetails"):
            raise RuntimeError(str(res["exceptionDetails"])[:400])
        return res.get("result", {}).get("value")

    async def close(self):
        if self.ws:
            await self.ws.close()


async def wait_page(port, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            for t in list_targets(port):
                if t.get("type") == "page" and t.get("url", "").startswith("http"):
                    cdp = CDP(t["webSocketDebuggerUrl"])
                    await cdp.connect()
                    return cdp
        except Exception:
            pass
        await asyncio.sleep(1)
    raise RuntimeError("Electron 页面目标超时未出现")


# ── 本地 HTTP 测试源 ────────────────────────────────────────────
def start_http_server():
    handler_cls = http.server.SimpleHTTPRequestHandler

    class Handler(handler_cls):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(SCRATCH), **kw)

        def log_message(self, format, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# ── 引擎 REST 直查（脚本侧对账，不依赖页面轮询） ────────────────
def engine_api(base, token, path, method="GET", body=None):
    req = urllib.request.Request(
        base + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"X-Auth-Token": token, "Authorization": "Bearer " + token,
                 "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read() or b"null")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ── 主流程 ──────────────────────────────────────────────────────
def launch(mode, binary, port):
    env = {**os.environ, "ELECTRON_DISABLE_SANDBOX": "1"}
    if mode == "dev":
        desktop = pathlib.Path(__file__).resolve().parent
        cmd = ["xvfb-run", "-a", str(desktop / "node_modules/electron/dist/electron"),
               ".", "--disable-gpu", f"--remote-debugging-port={port}"]
        cwd = str(desktop)
    else:
        if not binary or not pathlib.Path(binary).exists():
            sys.exit(f"packaged 模式需要存在的 --binary（got: {binary}）")
        cmd = ["xvfb-run", "-a", str(pathlib.Path(binary).resolve()),
               "--disable-gpu", f"--remote-debugging-port={port}"]
        cwd = str(pathlib.Path(binary).resolve().parent)
    log = open(SCRATCH / "app.log", "w")
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                            start_new_session=True)
    return proc


async def run(args):
    # 1. 测试载荷 + 本地 HTTP 源
    PAYLOAD.write_bytes(os.urandom(PAYLOAD_SIZE))
    want_sha = sha256(PAYLOAD)
    srv, http_port = start_http_server()
    url = f"http://127.0.0.1:{http_port}/{PAYLOAD.name}"

    # 2. 启动（真实启动路径）
    proc = launch(args.mode, args.binary, args.debug_port)
    print(f"[e2e] app pid={proc.pid} mode={args.mode} payload sha256={want_sha[:16]}…")

    def cleanup():
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            pass
        srv.shutdown()

    try:
        cdp = await wait_page(args.debug_port)
        # 3. preload 握手：downloadEngine 桥在（= isLocalEngineAvailable()===true）
        bridge = await cdp.eval("""(() => {
          const b = window.openmateDesktop;
          return { has: !!(b && b.downloadEngine), keys: b ? Object.keys(b) : [] };
        })()""")
        print(f"[e2e] bridge: {bridge}")
        assert bridge["has"], f"preload 桥缺 downloadEngine：{bridge}"

        # 4. 下载页 → 新建下载弹层 → 目标含「本机」
        await cdp.eval("""(() => {
          const a = [...document.querySelectorAll('a')].find(x => (x.getAttribute('href')||'').includes('/download'));
          if (a) a.click(); else location.href = location.origin + '/download';
        })()""")
        for _ in range(60):
            await asyncio.sleep(1)
            if await cdp.eval("location.pathname.startsWith('/download')"):
                break
        clicked = await cdp.eval("""(async () => {
          for (let i = 0; i < 30; i++) {
            const b = [...document.querySelectorAll('button')]
              .find(x => /新建下载|New Download/i.test(x.textContent.trim()));
            if (b) { b.click(); await new Promise(r => setTimeout(r, 1000)); return true; }
            await new Promise(r => setTimeout(r, 500));
          }
          return false;
        })()""")
        assert clicked, "未找到「新建下载」按钮"
        targets = await cdp.eval(
            "[...document.querySelectorAll('button')].map(b=>b.textContent.trim()).filter(s=>s==='本机'||s==='后端')")
        print(f"[e2e] target selector: {targets}")
        assert "本机" in targets and "后端" in targets, f"目标选择器缺「本机」：{targets}"

        # 5. 选「本机」，填 URL（React 受控输入走原生 setter + input 事件），提交
        await cdp.eval("""(() => {
          const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === '本机');
          b.click();
          const el = document.querySelector('textarea');
          const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
          setter.call(el, %s);
          el.dispatchEvent(new Event('input', { bubbles: true }));
        })()""" % json.dumps(url))
        await asyncio.sleep(500 / 1000)
        started = await cdp.eval("""(async () => {
          const b = [...document.querySelectorAll('button')]
            .find(x => /开始下载|Start Download/i.test(x.textContent.trim()));
          if (!b) return 'no-button';
          b.click();
          return 'clicked';
        })()""")
        print(f"[e2e] submit: {started}")
        assert started == "clicked", "未找到「开始下载」按钮"

        # 6. 引擎任务 → 完成 → 落盘对账（脚本侧直查引擎 REST）
        info = None
        for _ in range(60):
            await asyncio.sleep(1)
            info = await cdp.eval("window.openmateDesktop.downloadEngine.getInfo()")
            if info and info.get("port"):
                break
        assert info and info.get("port"), f"引擎握手无端点：{info}"
        base, token = f"http://127.0.0.1:{info['port']}", info["token"]
        print(f"[e2e] engine endpoint: {base} (pid {info.get('pid')})")

        done = None
        for _ in range(120):
            await asyncio.sleep(1)
            data = engine_api(base, token, "/api/tasks")
            tasks = [t for t in data.get("tasks", [])
                     if FILE_NAME in str(t.get("url", "")) or FILE_NAME in str(t.get("filename", ""))]
            if tasks and all(str(t.get("status")) == "done" for t in tasks):
                done = tasks
                break
            if tasks and any(str(t.get("status")) == "error" for t in tasks):
                raise RuntimeError(f"任务出错：{tasks}")
        assert done, "下载任务未在 120s 内完成"
        print(f"[e2e] task done: {json.dumps(done, ensure_ascii=False)[:300]}")

        # 7. 文件落盘 + sha256 对账（下载目录取引擎配置）
        cfg = engine_api(base, token, "/api/config")
        dl_dir = pathlib.Path(cfg.get("dir") or (pathlib.Path.home() / "Downloads"))
        out = dl_dir / FILE_NAME
        assert out.exists(), f"文件未落盘：{out}"
        got_sha = sha256(out)
        assert got_sha == want_sha, f"sha256 不符：{got_sha} != {want_sha}"
        print(f"[e2e] PASS: {out} ({out.stat().st_size} bytes) sha256 对账通过")
        out.unlink()
    finally:
        cleanup()
        shutil.rmtree(SCRATCH, ignore_errors=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["dev", "packaged"], default="dev")
    p.add_argument("--binary", help="packaged 模式的可执行（如 dist/linux-unpacked/openmate）")
    p.add_argument("--debug-port", type=int, default=9444)
    asyncio.run(run(p.parse_args()))


if __name__ == "__main__":
    main()
