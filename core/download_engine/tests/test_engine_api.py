"""Engine 聚合层 + REST API 集成测试（本地 HTTP 服务器 + httpx 客户端）。"""

import asyncio
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from download_engine.api import ApiServer
from download_engine.engine import Engine, EngineError
from download_engine.models import TaskStatus

BODY = bytes((i * 31 + 7) % 256 for i in range(300 * 1024))


class SimpleHandler(BaseHTTPRequestHandler):
    content = BODY

    def do_GET(self):  # noqa: N802
        if self.path.endswith("/slow"):
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.content)))
            self.end_headers()
            for i in range(0, len(self.content), 64 * 1024):
                self.wfile.write(self.content[i : i + 64 * 1024])
                time.sleep(0.03)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.content)))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Disposition", 'attachment; filename="pkg.bin"')
        self.end_headers()
        self.wfile.write(self.content)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture()
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), SimpleHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server.server_address[1]
    server.shutdown()


def _torrent_file(tmp_path):
    import hashlib

    from download_engine import torrent as tm

    content = b"q" * 2048
    pieces = [hashlib.sha1(content).digest()]
    raw = tm.build_torrent_bytes("q.bin", [("q.bin", 2048)], 2048, pieces)
    p = tmp_path / "q.torrent"
    p.write_bytes(raw)
    return p


# ── Engine 聚合层 ─────────────────────────────────────────────


def test_resolve_dir_precedence(tmp_path):
    e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "default"))
    assert e.resolve_dir(str(tmp_path / "explicit")) == str(tmp_path / "explicit")
    assert e.resolve_dir(None) == str(tmp_path / "default")
    e.config_set(dir=str(tmp_path / "configured"))
    assert e.resolve_dir(None) == str(tmp_path / "configured")
    assert e.resolve_dir(str(tmp_path / "explicit")) == str(tmp_path / "explicit")
    # 三者皆无 → 明确报错（引擎不硬编码 ~/Downloads）
    e2 = Engine(data_dir=str(tmp_path / "data2"))
    with pytest.raises(EngineError):
        e2.resolve_dir(None)


def test_add_list_events_flow(tmp_path, http_server):
    async def scenario():
        e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
        task = await e.add(f"http://127.0.0.1:{http_server}/f.bin")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            tasks = e.list()
            if tasks[0]["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        tasks = e.list()
        events = e.events(0)
        await e.shutdown()
        return task, tasks, events

    task, tasks, events = asyncio.run(scenario())
    assert task["url"].endswith("/f.bin")
    assert tasks[0]["filename"] == "pkg.bin"  # Content-Disposition（探测后更新）
    assert task["origin"] == "local"
    assert tasks[0]["status"] == "done"
    assert tasks[0]["totalBytes"] == len(BODY)
    kinds = [ev["kind"] for ev in events["events"]]
    assert "added" in kinds and "progress" in kinds
    assert events["cursor"] > 0


def test_thunder_route_ftp_accepted_video_reject(tmp_path, http_server):
    import base64

    async def scenario():
        e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
        real = f"http://127.0.0.1:{http_server}/f.bin"
        thunder = "thunder://" + base64.b64encode(b"AA" + real.encode() + b"ZZ").decode()
        task = await e.add(thunder)
        with pytest.raises(EngineError):
            await e.add("https://www.bilibili.com/video/BV1xx")
        # FTP 不再「暂缓」：接受并路由到 FTP 引擎（ftp- 前缀）
        ftp_task = await e.add("ftp://127.0.0.1:9/nope.bin")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if e.list()[0]["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.05)
        await e.shutdown()
        return task, ftp_task

    task, ftp_task = asyncio.run(scenario())
    assert task["type"] == "thunder"
    assert task["url"].startswith("thunder://")  # 契约：url 保留用户原始输入
    assert ftp_task["id"].startswith("ftp-")
    assert ftp_task["type"] == "http"


def test_config_get_set(tmp_path):
    e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
    c = e.config_get()
    assert c["concurrency"] == 5 and c["speedLimit"] == 0
    c = e.config_set(dir=str(tmp_path / "d2"), concurrency=999, speed_limit=2048)
    assert c["dir"] == str(tmp_path / "d2")
    assert c["concurrency"] == 64  # 钳制
    assert c["speedLimit"] == 2048
    # 配置持久化：新实例读回
    e2 = Engine(data_dir=str(tmp_path / "data"))
    assert e2.configured_dir == str(tmp_path / "d2")
    assert e2.speed_limit == 2048


def test_metadata_from_torrent_file(tmp_path):
    p = _torrent_file(tmp_path)

    async def scenario():
        e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
        files = await e.metadata(str(p))
        with pytest.raises(EngineError):
            await e.metadata("https://example.com/x")
        await e.shutdown()
        return files

    files = asyncio.run(scenario())
    assert files == [{"path": "q.bin", "length": 2048}]


def test_state_persists_and_restores(tmp_path, http_server):
    async def scenario():
        e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
        task = await e.add(f"http://127.0.0.1:{http_server}/slow")
        await asyncio.sleep(0.2)
        await e.pause(task["id"])
        paused = e.list()[0]
        await e.shutdown()
        return task, paused

    task, paused = asyncio.run(scenario())
    assert paused["status"] == "paused"
    # 重启：同一 data-dir 恢复任务
    e2 = Engine(data_dir=str(tmp_path / "data"))
    restored = e2.list()
    assert len(restored) == 1
    assert restored[0]["id"] == task["id"]
    assert restored[0]["status"] == "paused"

    async def cleanup():
        await e2.shutdown()

    asyncio.run(cleanup())


# ── REST API ─────────────────────────────────────────────────


def test_rest_api_end_to_end(tmp_path, http_server):
    async def scenario():
        e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
        server = ApiServer(e, token="sekrit", port=0)
        port = await server.start()
        base = f"http://127.0.0.1:{port}"
        async with httpx.AsyncClient(timeout=10) as c:
            # /health 免 token
            r = await c.get(f"{base}/health")
            assert r.status_code == 200 and r.json()["ok"]
            # 其余路由要求 token
            r = await c.get(f"{base}/api/tasks")
            assert r.status_code == 401
            r = await c.get(f"{base}/api/tasks", headers={"Authorization": "Bearer wrong"})
            assert r.status_code == 401
            auth = {"Authorization": "Bearer sekrit"}
            # 建任务
            r = await c.post(
                f"{base}/api/tasks",
                json={"url": f"http://127.0.0.1:{http_server}/f.bin"},
                headers=auth,
            )
            assert r.status_code == 201
            task = r.json()["task"]
            assert task["origin"] == "local"
            # 等完成
            for _ in range(300):
                r = await c.get(f"{base}/api/tasks", headers=auth)
                if r.json()["tasks"][0]["status"] in ("done", "error"):
                    break
                await asyncio.sleep(0.05)
            assert r.json()["tasks"][0]["status"] == "done"
            # 暂停/继续/移除
            tid = task["id"]
            r = await c.post(f"{base}/api/tasks/{tid}/pause", headers=auth)
            assert r.status_code == 200  # done 任务 pause 为 no-op
            r = await c.post(f"{base}/api/tasks/{tid}/resume", headers=auth)
            assert r.status_code in (200, 400)  # done → 400 报"无需继续"
            r = await c.post(f"{base}/api/tasks/{tid}/remove", json={"remove_files": True}, headers=auth)
            assert r.status_code == 200
            r = await c.get(f"{base}/api/tasks", headers=auth)
            assert r.json()["tasks"] == []
            # 事件
            r = await c.get(f"{base}/api/events?after=0", headers=auth)
            kinds = [ev["kind"] for ev in r.json()["events"]]
            assert "added" in kinds and "removed" in kinds
            # 配置
            r = await c.put(f"{base}/api/config", json={"concurrency": 3, "speedLimit": 0}, headers=auth)
            assert r.status_code == 200 and r.json()["concurrency"] == 3
            # 元数据（torrent 文件）
            p = _torrent_file(tmp_path)
            r = await c.post(f"{base}/api/metadata", json={"source": str(p)}, headers=auth)
            assert r.status_code == 200
            assert r.json()["files"] == [{"path": "q.bin", "length": 2048}]
            # 错误路由
            r = await c.post(f"{base}/api/nope", headers=auth)
            assert r.status_code == 404
        await server.stop()
        await e.shutdown()

    asyncio.run(scenario())


def test_rest_shutdown_endpoint(tmp_path):
    async def scenario():
        e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
        server = ApiServer(e, token="t", port=0)
        port = await server.start()
        serve = asyncio.get_running_loop().create_task(server.serve())
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"http://127.0.0.1:{port}/api/shutdown", headers={"X-Auth-Token": "t"})
            assert r.status_code == 200 and r.json()["ok"]
        await asyncio.wait_for(serve, 5)
        await e.shutdown()

    asyncio.run(scenario())


def test_rest_auth_via_x_auth_token(tmp_path):
    async def scenario():
        e = Engine(data_dir=str(tmp_path / "data"), default_dir=str(tmp_path / "dl"))
        server = ApiServer(e, token="tok2", port=0)
        port = await server.start()
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"http://127.0.0.1:{port}/api/config", headers={"X-Auth-Token": "tok2"})
            assert r.status_code == 200
        await server.stop()
        await e.shutdown()

    asyncio.run(scenario())
