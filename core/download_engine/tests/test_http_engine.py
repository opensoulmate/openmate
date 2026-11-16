"""HTTP 分段下载引擎集成测试（本地 Range 服务器）+ 分段规划单测。

注意：engine worker 跑在创建它的事件循环上，所以每个测试的
「创建→下载→轮询→断言→shutdown」必须在同一个 asyncio.run 里完成。
"""

import asyncio
import hashlib
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from download_engine.http_engine import (
    SEGMENT_THRESHOLD,
    HttpEngine,
    SpeedLimiter,
    plan_segments,
)

CONTENT = os.urandom(SEGMENT_THRESHOLD + 3 * 1024 * 1024)  # 7 MiB，强制多段
CHUNK = 64 * 1024


class RangeHandler(BaseHTTPRequestHandler):
    """支持 Range / If-Range 的静态内存服务器（含请求日志）。"""

    content = CONTENT
    filename = None        # Content-Disposition 文件名（None = 不给）
    support_range = True
    delay = 0.0            # 每 chunk 的延迟（模拟慢速，测暂停/续传）
    not_found = False      # 模拟 404
    log = []

    def do_GET(self):  # noqa: N802
        cls = type(self)
        if cls.not_found:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        cls.log.append((self.headers.get("Range"), self.headers.get("If-Range")))
        rng = self.headers.get("Range")
        start, end = 0, len(cls.content) - 1
        status = 200
        if cls.support_range and rng:
            m = re.match(r"bytes=(\d+)-(\d*)", rng)
            if m:
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), len(cls.content) - 1)
                status = 206
        body = cls.content[start : end + 1]
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if cls.support_range:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(cls.content)}")
        if cls.filename:
            self.send_header("Content-Disposition", f'attachment; filename="{cls.filename}"')
        self.end_headers()
        if cls.delay:
            for i in range(0, len(body), CHUNK):
                self.wfile.write(body[i : i + CHUNK])
                time.sleep(cls.delay)
        else:
            self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        pass


def _serve(**kwargs):
    for k, v in kwargs.items():
        setattr(RangeHandler, k, v)
    RangeHandler.log = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, server.server_address[1]


async def _wait_status(engine, task_id, want=("done", "error"), timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for t in engine.list():
            if t.id == task_id and t.status.value in want:
                return t
        await asyncio.sleep(0.05)
    return None


# ── 分段规划（纯函数） ────────────────────────────────────────


def test_plan_segments_splitting():
    segs = plan_segments(10 * 1024 * 1024, 4, True)
    assert len(segs) == 4
    assert segs[0].start == 0
    assert segs[-1].end == 10 * 1024 * 1024
    assert all(s.end > s.start for s in segs)
    for a, b in zip(segs, segs[1:]):
        assert a.end == b.start


def test_plan_segments_single_when_no_ranges_or_small():
    assert len(plan_segments(10 * 1024 * 1024, 4, False)) == 1
    assert len(plan_segments(1024, 4, True)) == 1
    assert len(plan_segments(0, 4, True)) == 1  # 未知长度 → 单段到 EOF


# ── 集成（本地服务器，同一事件循环） ───────────────────────────


def test_multithreaded_download_with_ranges(tmp_path):
    server, port = _serve(support_range=True, filename=None, delay=0.0, not_found=False)

    async def scenario():
        engine = HttpEngine(persist=lambda: None, concurrency=4)
        task = await engine.add(f"http://127.0.0.1:{port}/f.bin", str(tmp_path))
        done = await _wait_status(engine, task.id)
        assert done is not None and done.status.value == "done", getattr(done, "error", "超时")
        await engine.shutdown()
        return engine, task, done

    try:
        _, task, done = asyncio.run(scenario())
        assert done.total_bytes == len(CONTENT)
        path = os.path.join(str(tmp_path), task.filename)
        assert hashlib.sha256(open(path, "rb").read()).digest() == hashlib.sha256(CONTENT).digest()
        assert any(r[0] for r in RangeHandler.log), "应有 Range 请求（分段）"
    finally:
        server.shutdown()


def test_no_range_server_falls_back_to_single_stream(tmp_path):
    server, port = _serve(support_range=False, filename=None, delay=0.0, not_found=False)

    async def scenario():
        engine = HttpEngine(persist=lambda: None, concurrency=4)
        task = await engine.add(f"http://127.0.0.1:{port}/f.bin", str(tmp_path))
        done = await _wait_status(engine, task.id)
        assert done is not None and done.status.value == "done"
        await engine.shutdown()
        return task, done

    try:
        task, _ = asyncio.run(scenario())
        path = os.path.join(str(tmp_path), task.filename)
        assert open(path, "rb").read() == CONTENT
    finally:
        server.shutdown()


def test_content_disposition_filename(tmp_path):
    server, port = _serve(support_range=True, filename="artifact-v1.zip", delay=0.0, not_found=False)

    async def scenario():
        engine = HttpEngine(persist=lambda: None, concurrency=2)
        task = await engine.add(f"http://127.0.0.1:{port}/x", str(tmp_path))
        done = await _wait_status(engine, task.id)
        assert done is not None
        await engine.shutdown()
        return done

    try:
        done = asyncio.run(scenario())
        assert done.filename == "artifact-v1.zip"
        assert os.path.exists(os.path.join(str(tmp_path), "artifact-v1.zip"))
    finally:
        server.shutdown()


def test_pause_resume_breakpoint_continuation(tmp_path):
    server, port = _serve(support_range=True, filename=None, delay=0.02, not_found=False)

    async def scenario():
        engine = HttpEngine(persist=lambda: None, concurrency=2)
        task = await engine.add(f"http://127.0.0.1:{port}/f.bin", str(tmp_path))
        while True:
            cur = engine.list()[0]
            if cur.downloaded_bytes > 200 * 1024:
                break
            await asyncio.sleep(0.02)
        await engine.pause(task.id)
        paused = engine.list()[0]
        assert paused.status.value == "paused"
        assert 0 < paused.downloaded_bytes < len(CONTENT), "暂停时应有部分进度"
        partial = paused.downloaded_bytes
        RangeHandler.log.clear()
        await engine.resume(task.id)
        done = await _wait_status(engine, task.id, timeout=60)
        assert done is not None and done.status.value == "done"
        await engine.shutdown()
        return task, partial

    try:
        task, partial = asyncio.run(scenario())
        path = os.path.join(str(tmp_path), task.filename)
        assert open(path, "rb").read() == CONTENT, "断点续传后文件必须完整一致"
        resumed_starts = []
        for rng, _ in RangeHandler.log:
            if rng:
                resumed_starts.append(int(rng.split("=")[1].split("-")[0]))
        assert any(s > 0 for s in resumed_starts), f"续传未从断点继续: {resumed_starts[:5]}"
        assert partial > 0
    finally:
        server.shutdown()


def test_error_status_on_404(tmp_path):
    server, port = _serve(support_range=True, filename=None, delay=0.0, not_found=True)

    async def scenario():
        engine = HttpEngine(persist=lambda: None, concurrency=2)
        task = await engine.add(f"http://127.0.0.1:{port}/missing.bin", str(tmp_path))
        done = await _wait_status(engine, task.id)
        assert done is not None
        await engine.shutdown()
        return done

    try:
        done = asyncio.run(scenario())
        assert done.status.value == "error" and done.error
    finally:
        server.shutdown()


def test_speed_limiter_throttles():
    async def scenario():
        lim = SpeedLimiter(100_000)  # 100 KB/s（桶容量 100KB，突发后节流）
        t0 = time.monotonic()
        for _ in range(8):
            await lim.acquire(25_000)  # 共 200 KB：100KB 突发 + 100KB 节流 ≈ 1s
        return time.monotonic() - t0

    dt = asyncio.run(scenario())
    assert dt >= 0.5, f"限速未生效: {dt}s"
    assert dt < 10, f"限速过慢: {dt}s"


def test_speed_limiter_chunk_larger_than_limit_no_deadlock():
    async def scenario():
        lim = SpeedLimiter(10_000)  # 10 KB/s
        return await asyncio.wait_for(lim.acquire(50_000), timeout=10)  # 单块大于限速

    asyncio.run(scenario())  # 能返回即不卡死


def test_restore_resumes_from_persisted_segments(tmp_path):
    server, port = _serve(support_range=True, filename=None, delay=0.0, not_found=False)
    segs = [
        {"start": 0, "end": 3 * 1024 * 1024, "done": 3 * 1024 * 1024},
        {"start": 3 * 1024 * 1024, "end": 5 * 1024 * 1024, "done": 2 * 1024 * 1024},
        {"start": 5 * 1024 * 1024, "end": len(CONTENT), "done": 0},
    ]
    state = {
        "id": "http-777",
        "url": f"http://127.0.0.1:{port}/f.bin",
        "filename": "f.bin",
        "dir": str(tmp_path),
        "total_bytes": len(CONTENT),
        "downloaded_bytes": 5 * 1024 * 1024,
        "status": "paused",
        "type": "http",
        "segs": segs,
        "accept_ranges": True,
        "etag": None,
    }

    async def scenario():
        engine = HttpEngine(persist=lambda: None, concurrency=3)
        engine.restore([state])
        await engine.resume("http-777")
        done = await _wait_status(engine, "http-777", timeout=30)
        assert done is not None and done.status.value == "done"
        await engine.shutdown()
        return done

    try:
        # 真实断点场景：已下载的 5 MiB 本来就在磁盘上
        with open(os.path.join(str(tmp_path), "f.bin"), "wb") as f:
            f.write(CONTENT[: 5 * 1024 * 1024])
        asyncio.run(scenario())
        path = os.path.join(str(tmp_path), "f.bin")
        assert open(path, "rb").read() == CONTENT
        starts = [int(r[0].split("=")[1].split("-")[0]) for r in RangeHandler.log if r[0]]
        # 前两段已完，不重下：请求起点都在 5 MiB 及以后
        assert all(s >= 5 * 1024 * 1024 for s in starts), f"有分段重复下载: {starts}"
    finally:
        server.shutdown()
