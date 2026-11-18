"""os.pwrite/os.pread 缺失（Windows）时 fallback 路径验证。

Windows Python 没有 os.pwrite/os.pread（PyInstaller 冻结的 download-engine.exe 同炸）。
本组测试用 monkeypatch 删掉 os.pwrite/os.pread 强制走 util 的 lseek+write/read fallback：

- 单元：偏移写不扰他字节、并发同 fd 不竞态（锁）、pread 偏移回读
- 集成：HTTP 分段下载 / FTP 下载真跑落盘（长度+SHA256 字节对账）、
  BT PieceWriter 跨文件边界写读对账
"""

import asyncio
import hashlib
import os
import threading

import pytest

from download_engine import util
from download_engine.bt_engine import PieceWriter
from download_engine.ftp_engine import FtpEngine
from download_engine.http_engine import HttpEngine
from download_engine.torrent import TorrentFile

# 复用同目录已有测试的服务端实现（本地 Range 服务器 / 内置 Fake FTP）
from test_ftp_engine import CONTENT as FTP_CONTENT
from test_ftp_engine import FakeFtpServer
from test_ftp_engine import _wait_done as _wait_ftp_done
from test_http_engine import CONTENT as HTTP_CONTENT
from test_http_engine import RangeHandler, _serve, _wait_status


def _force_fallback(monkeypatch):
    """删掉 os.pwrite/os.pread，模拟 Windows（无此属性）→ util 走锁+lseek+write/read。"""
    monkeypatch.delattr(os, "pwrite", raising=False)
    monkeypatch.delattr(os, "pread", raising=False)
    assert getattr(os, "pwrite", None) is None and getattr(os, "pread", None) is None


# ── 单元 ───────────────────────────────────────────────────────


def test_native_pwrite_pread_used_when_available(tmp_path):
    """Unix 上 os.pwrite/os.pread 存在 → util 直通原生（不走 fallback）。"""
    if not hasattr(os, "pwrite"):
        pytest.skip("本平台无 os.pwrite（Windows），原生路径不存在")
    path = tmp_path / "native.bin"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | util.O_BINARY, 0o644)
    try:
        assert util.pwrite(fd, b"hello", 0) == 5
        assert util.pread(fd, 5, 0) == b"hello"
    finally:
        os.close(fd)


def test_fallback_pwrite_writes_at_offset(tmp_path, monkeypatch):
    _force_fallback(monkeypatch)
    path = tmp_path / "f.bin"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | util.O_BINARY, 0o644)
    try:
        assert util.pwrite(fd, b"AAAA", 0) == 4
        assert util.pwrite(fd, b"BB", 2) == 2  # 覆盖中段 [2,4)
        assert util.pwrite(fd, b"CC", 6) == 2  # 稀疏跳写 [6,8)
    finally:
        os.close(fd)
    data = open(path, "rb").read()
    assert data == b"AABB\x00\x00CC", data
    assert len(data) == 8


def test_fallback_pwrite_concurrent_same_fd_no_race(tmp_path, monkeypatch):
    """并发分段共享同一 fd：无锁时 lseek+write 会互相顶掉偏移写串；按 fd 加锁后必须全对。"""
    _force_fallback(monkeypatch)
    n_threads, n_writes, chunk = 8, 200, 1024
    total = n_threads * n_writes * chunk
    path = tmp_path / "race.bin"
    fd = os.open(path, os.O_RDWR | os.O_CREAT | util.O_BINARY, 0o644)
    os.ftruncate(fd, total)

    def worker(t: int) -> None:
        tag = bytes([0x40 + t]) * chunk
        for i in range(n_writes):
            util.pwrite(fd, tag, (t * n_writes + i) * chunk)

    try:
        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join(60)
        assert all(not th.is_alive() for th in threads), "写线程卡死"
    finally:
        os.close(fd)

    data = open(path, "rb").read()
    assert len(data) == total
    for t in range(n_threads):
        want = bytes([0x40 + t]) * chunk
        for i in range(n_writes):
            off = (t * n_writes + i) * chunk
            assert data[off : off + chunk] == want, f"fd 竞态写串: thread={t} write={i}"


def test_fallback_pread_reads_at_offset(tmp_path, monkeypatch):
    _force_fallback(monkeypatch)
    path = tmp_path / "r.bin"
    content = bytes(range(256)) * 8  # 2048 B
    with open(path, "wb") as f:
        f.write(content)
    fd = os.open(path, os.O_RDONLY | util.O_BINARY)
    try:
        assert util.pread(fd, 10, 0) == content[:10]
        assert util.pread(fd, 16, 1000) == content[1000:1016]
        assert util.pread(fd, 10, len(content) - 4) == content[-4:]  # 尾部截断
        assert util.pread(fd, 5, 0) == content[:5]  # 读不移动文件位置
    finally:
        os.close(fd)


# ── 集成：真跑下载，字节对账 ──────────────────────────────────


def test_http_segmented_download_without_pwrite(tmp_path, monkeypatch):
    """HTTP 多段并发下载真跑落盘（fallback 路径）：长度 + SHA256 字节对账。"""
    _force_fallback(monkeypatch)
    server, port = _serve(support_range=True, filename=None, delay=0.0, not_found=False)

    async def scenario():
        engine = HttpEngine(persist=lambda: None, concurrency=4)
        task = await engine.add(f"http://127.0.0.1:{port}/f.bin", str(tmp_path))
        done = await _wait_status(engine, task.id)
        assert done is not None and done.status.value == "done", getattr(done, "error", "超时")
        await engine.shutdown()
        return task

    try:
        task = asyncio.run(scenario())
    finally:
        server.shutdown()

    got = open(os.path.join(str(tmp_path), task.filename), "rb").read()
    assert len(got) == len(HTTP_CONTENT), "字节对账：长度不一致"
    assert hashlib.sha256(got).digest() == hashlib.sha256(HTTP_CONTENT).digest(), "字节对账：内容不一致"
    assert any(r[0] for r in RangeHandler.log), "应有 Range 请求（真分段）"


def test_ftp_download_without_pwrite(tmp_path, monkeypatch):
    """FTP 下载真跑落盘（fallback 路径）：字节对账。"""
    _force_fallback(monkeypatch)
    server = FakeFtpServer()
    try:
        async def scenario():
            engine = FtpEngine(persist=lambda: None, concurrency=2)
            task = await engine.add(f"ftp://127.0.0.1:{server.port}/f.bin", str(tmp_path))
            done = await _wait_ftp_done(engine, task.id)
            await engine.shutdown()
            return task, done

        task, done = asyncio.run(scenario())
        assert done.status.value == "done", done.error
        got = open(os.path.join(str(tmp_path), task.filename), "rb").read()
        assert got == FTP_CONTENT, "字节对账：FTP 落盘内容不一致"
    finally:
        server.close()


def test_bt_piecewriter_without_pwrite_pread(tmp_path, monkeypatch):
    """BT PieceWriter 跨文件边界写读（fallback 路径）：字节对账。"""
    _force_fallback(monkeypatch)
    files = [
        TorrentFile(path="dir_a/part1.bin", length=300, offset=0),
        TorrentFile(path="dir_b/part2.bin", length=500, offset=300),
    ]
    store = PieceWriter(str(tmp_path), files)
    store.ensure_files()
    data = os.urandom(800)
    # 一次写跨过文件边界（300 处切开）
    store.write_at(0, data)
    # 分片乱序写（模拟多 peer 并发落盘的分段）
    store.write_at(600, data[600:700])
    store.write_at(100, data[100:250])
    assert store.read_at(0, 800) == data, "字节对账：PieceWriter 写读不一致"
    assert (tmp_path / "dir_a" / "part1.bin").read_bytes() == data[:300]
    assert (tmp_path / "dir_b" / "part2.bin").read_bytes() == data[300:]
