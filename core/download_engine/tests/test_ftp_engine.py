"""FTP 引擎集成测试：内置最小 FTP 服务端（stdlib socketserver，支持 REST/SIZE/MDTM）。"""

import asyncio
import os
import socket
import threading
import time

import pytest

from download_engine.ftp_engine import FtpEngine, FtpEngineError, map_ftp_error, parse_ftp_url

CONTENT = bytes((i * 17 + 5) % 256 for i in range(512 * 1024))


class FakeFtpServer:
    """最小 FTP 服务端：USER/PASS/TYPE/PASV/SIZE/MDTM/REST/RETR/QUIT。"""

    def __init__(self, content: bytes = CONTENT, bad_auth: bool = False,
                 missing: bool = False, no_rest: bool = False, slow: bool = False):
        self.content = content
        self.bad_auth = bad_auth
        self.missing = missing
        self.no_rest = no_rest
        self.slow = slow
        self.rest_calls = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self._stop = False
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()

    def _accept_loop(self):
        while not self._stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                break
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _send(self, conn, s):
        conn.sendall((s + "\r\n").encode())

    def _handle(self, conn):
        data_listener = None
        try:
            self._send(conn, "220 FakeFTPD ready")
            f = conn.makefile("rb")
            while True:
                line = f.readline()
                if not line:
                    break
                cmd = line.decode("latin-1").strip()
                if cmd.upper().startswith("USER"):
                    self._send(conn, "331 password required")
                elif cmd.upper().startswith("PASS"):
                    if self.bad_auth:
                        self._send(conn, "530 login incorrect")
                        break
                    self._send(conn, "230 logged in")
                elif cmd.upper().startswith("TYPE"):
                    self._send(conn, "200 type set")
                elif cmd.upper().startswith("PASV"):
                    data_listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    data_listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    data_listener.bind(("127.0.0.1", 0))
                    data_listener.listen(1)
                    p = data_listener.getsockname()[1]
                    self._send(conn, f"227 Entering Passive Mode (127,0,0,1,{p // 256},{p % 256})")
                elif cmd.upper().startswith("SIZE"):
                    if self.missing:
                        self._send(conn, "550 file not found")
                    else:
                        self._send(conn, f"213 {len(self.content)}")
                elif cmd.upper().startswith("MDTM"):
                    self._send(conn, "213 20260101000000")
                elif cmd.upper().startswith("REST"):
                    if self.no_rest:
                        self._send(conn, "501 REST not supported")
                    else:
                        n = int(cmd.split()[1])
                        self.rest_calls.append(n)
                        self._send(conn, "350 restart ok")
                elif cmd.upper().startswith("RETR"):
                    if self.missing:
                        self._send(conn, "550 file not found")
                        continue
                    start = self.rest_calls[-1] if self.rest_calls else 0
                    self._send(conn, "150 opening data")
                    dconn, _ = data_listener.accept()
                    data = self.content[start:]
                    step = 64 * 1024
                    for i in range(0, len(data), step):
                        dconn.sendall(data[i : i + step])
                        if self.slow:
                            time.sleep(0.03)
                    dconn.close()
                    data_listener.close()
                    data_listener = None
                    self._send(conn, "226 transfer complete")
                elif cmd.upper().startswith("QUIT"):
                    self._send(conn, "221 bye")
                    break
                elif cmd.upper().startswith("ABOR"):
                    self._send(conn, "226 aborted")
                else:
                    self._send(conn, "502 command not implemented")
        except (OSError, ValueError, IndexError):
            pass
        finally:
            if data_listener:
                data_listener.close()
            conn.close()

    def close(self):
        self._stop = True
        self.sock.close()


@pytest.fixture()
def ftp_server():
    s = FakeFtpServer()
    yield s
    s.close()


async def _wait_done(engine, task_id, timeout=30):
    deadline = time.monotonic() + timeout
    t = None
    while time.monotonic() < deadline:
        for x in engine.list():
            if x.id == task_id and x.status.value in ("done", "error"):
                return x
            t = x
        await asyncio.sleep(0.05)
    return t


def test_parse_ftp_url():
    info = parse_ftp_url("ftp://user:pw@example.com:2121/pub/a%20b.bin")
    assert info == {"tls": False, "host": "example.com", "port": 2121,
                    "path": "/pub/a b.bin", "username": "user", "password": "pw"}
    info = parse_ftp_url("ftps://example.com/x")
    assert info["tls"] and info["port"] == 21 and info["username"] == "anonymous"
    with pytest.raises(FtpEngineError):
        parse_ftp_url("http://x/y")
    with pytest.raises(FtpEngineError):
        parse_ftp_url("ftp://")


def test_ftp_download_full(tmp_path, ftp_server):
    async def scenario():
        engine = FtpEngine(persist=lambda: None, concurrency=2)
        task = await engine.add(f"ftp://127.0.0.1:{ftp_server.port}/f.bin", str(tmp_path))
        done = await _wait_done(engine, task.id)
        await engine.shutdown()
        return task, done

    task, done = asyncio.run(scenario())
    assert done.status.value == "done", done.error
    assert done.total_bytes == len(CONTENT)
    assert open(os.path.join(str(tmp_path), task.filename), "rb").read() == CONTENT
    assert ftp_server.rest_calls == [0] or ftp_server.rest_calls == []


def test_ftp_resume_with_rest(tmp_path, ftp_server):
    """断点续传：已下一半 + 持久化 offset → REST 续传后半段。"""
    half = len(CONTENT) // 2
    with open(tmp_path / "f.bin", "wb") as f:
        f.write(CONTENT[:half])
    state = {
        "id": "ftp-777",
        "url": f"ftp://127.0.0.1:{ftp_server.port}/f.bin",
        "filename": "f.bin",
        "dir": str(tmp_path),
        "total_bytes": len(CONTENT),
        "downloaded_bytes": half,
        "offset": half,
        "status": "paused",
        "type": "http",
    }

    async def scenario():
        engine = FtpEngine(persist=lambda: None)
        engine.restore([state])
        await engine.resume("ftp-777")
        done = await _wait_done(engine, "ftp-777")
        await engine.shutdown()
        return done

    done = asyncio.run(scenario())
    assert done.status.value == "done", done.error
    assert open(tmp_path / "f.bin", "rb").read() == CONTENT
    assert half in ftp_server.rest_calls, f"REST 应从断点继续: {ftp_server.rest_calls}"


def test_ftp_no_rest_falls_back_to_restart(tmp_path):
    s = FakeFtpServer(no_rest=True)
    try:

        async def scenario():
            engine = FtpEngine(persist=lambda: None)
            task = await engine.add(f"ftp://127.0.0.1:{s.port}/f.bin", str(tmp_path))
            done = await _wait_done(engine, task.id)
            await engine.shutdown()
            return task, done

        task, done = asyncio.run(scenario())
        assert done.status.value == "done", done.error
        assert open(os.path.join(str(tmp_path), task.filename), "rb").read() == CONTENT
    finally:
        s.close()


def test_ftp_pause_resume(tmp_path):
    s = FakeFtpServer(slow=True)
    try:

        async def scenario():
            engine = FtpEngine(persist=lambda: None)
            task = await engine.add(f"ftp://127.0.0.1:{s.port}/f.bin", str(tmp_path))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                cur = engine.list()[0]
                if cur.downloaded_bytes > 100 * 1024 or cur.status.value in ("done", "error"):
                    break
                await asyncio.sleep(0.02)
            await engine.pause(task.id)
            paused = engine.list()[0]
            assert paused.status.value == "paused"
            assert 0 < paused.downloaded_bytes < len(CONTENT)
            await engine.resume(task.id)
            done = await _wait_done(engine, task.id, timeout=60)
            await engine.shutdown()
            return done

        done = asyncio.run(scenario())
        assert done.status.value == "done", done.error
        assert open(tmp_path / "f.bin", "rb").read() == CONTENT
    finally:
        s.close()


def test_ftp_error_mapping(tmp_path):
    # 认证失败
    s = FakeFtpServer(bad_auth=True)
    try:

        async def scenario():
            engine = FtpEngine(persist=lambda: None)
            task = await engine.add(f"ftp://127.0.0.1:{s.port}/f.bin", str(tmp_path))
            done = await _wait_done(engine, task.id, timeout=15)
            await engine.shutdown()
            return done

        done = asyncio.run(scenario())
        assert done.status.value == "error"
        assert "认证失败" in done.error
    finally:
        s.close()
    # 文件不存在
    s = FakeFtpServer(missing=True)
    try:

        async def scenario():
            engine = FtpEngine(persist=lambda: None)
            task = await engine.add(f"ftp://127.0.0.1:{s.port}/nope.bin", str(tmp_path))
            done = await _wait_done(engine, task.id, timeout=15)
            await engine.shutdown()
            return done

        done = asyncio.run(scenario())
        assert done.status.value == "error"
        assert "不存在" in done.error or "无权限" in done.error
    finally:
        s.close()
    # 连接被拒
    async def refused():
        engine = FtpEngine(persist=lambda: None)
        task = await engine.add("ftp://127.0.0.1:9/x.bin", str(tmp_path))
        done = await _wait_done(engine, task.id, timeout=15)
        await engine.shutdown()
        return done

    done = asyncio.run(refused())
    assert done.status.value == "error"
    assert "连接被拒" in done.error or "超时" in done.error or "网络错误" in done.error


def test_map_ftp_error_categories():
    import ftplib

    assert "认证失败" in map_ftp_error(ftplib.error_perm("530 no"))
    assert "不存在" in map_ftp_error(ftplib.error_perm("550 no"))
    assert "永久错误" in map_ftp_error(ftplib.error_perm("500 x"))
    assert "临时错误" in map_ftp_error(ftplib.error_temp("421 x"))
    assert "DNS" in map_ftp_error(socket.gaierror("nope"))
    assert "TLS" in map_ftp_error(__import__("ssl").SSLError("x"))
