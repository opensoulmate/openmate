"""util.py — 小工具。"""

from __future__ import annotations

import os
import threading
import time

# Windows 下 os.open 必须显式带 O_BINARY：默认文本模式会做 \\n↔\\r\\n 翻译，
# 分段落盘的字节会被破坏；Unix 无该常量，取 0 无副作用。
O_BINARY = getattr(os, "O_BINARY", 0)

# ── 跨平台 pwrite/pread 兼容层 ─────────────────────────────────
# os.pwrite/os.pread 是 Unix 专用（Windows Python 无此属性，PyInstaller 冻结的
# download-engine.exe 同炸：'module os has no attribute pwrite'）。
# 不可用时退化为 lseek+write/read——但共享同一 fd 的并发分段会竞态（任务 A 的 lseek
# 被任务 B 顶掉，数据落错偏移），因此 fallback 必须按 fd 加锁，把「定位+读/写」
# 合成原子操作；os.pwrite 可用时走原生（内核按偏移原子写，无需锁）。
# 每次调用现查 hasattr：测试可 monkeypatch 删 os.pwrite 强制走 fallback。

_fd_locks: dict[int, threading.Lock] = {}
_fd_locks_guard = threading.Lock()


def _fd_lock(fd: int) -> threading.Lock:
    """按 fd 编号取锁。

    fd 编号在 close 后会被复用，复用同一把锁最多多一次串行，无正确性问题；
    字典上界=最大 fd 数，不会随 open/close 无限增长。
    """
    with _fd_locks_guard:
        lock = _fd_locks.get(fd)
        if lock is None:
            lock = threading.Lock()
            _fd_locks[fd] = lock
        return lock


def pwrite(fd: int, data: bytes, offset: int) -> int:
    """在 offset 处写 data，不移动文件位置。返回写入字节数。

    跨平台：Unix 走 os.pwrite；Windows（无 os.pwrite）退化为按 fd 加锁的
    lseek+write，防止并发分段竞态覆盖。
    """
    fn = getattr(os, "pwrite", None)
    if fn is not None:
        return fn(fd, data, offset)
    with _fd_lock(fd):
        os.lseek(fd, offset, os.SEEK_SET)
        return os.write(fd, data)


def pread(fd: int, n: int, offset: int) -> bytes:
    """在 offset 处读至多 n 字节，不移动文件位置。返回实际读到的字节。

    跨平台：Unix 走 os.pread；Windows（无 os.pread）退化为按 fd 加锁的
    lseek+read（与 pwrite fallback 共用同一把锁，读写互不干扰定位）。
    """
    fn = getattr(os, "pread", None)
    if fn is not None:
        return fn(fd, n, offset)
    with _fd_lock(fd):
        os.lseek(fd, offset, os.SEEK_SET)
        return os.read(fd, n)


def generate_token() -> str:
    """本地 REST token（随机 32 hex）。"""
    return os.urandom(16).hex()


def now() -> float:
    return time.time()
