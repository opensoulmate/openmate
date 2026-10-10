# -*- coding: utf-8 -*-
"""工具执行层并发隔离（路线图 5.2）——跨进程文件读-改-写互斥。

背景（写锁已有，本模块把互斥扩到工具执行层）：
- 会话级 Writer Fence（writer_fence.py）只串行化同一 session 的回合；
- 但多会话并发是既定行为：acp-proxy a/b 双实例、每会话 stdio 子进程、
  5.1 子代理 worker（同进程多回合）都会用 patch / memory_append 等工具
  动**同一份共享文件**。patch 的"读→替换→写"和 PersistentMemory.append 的
  "读→追加→写"都是经典 read-modify-write：两个进程交叉执行时后写者会
  覆盖前写者的改动（lost update）——atomic_write 只保证单次写入完整，
  拯救不了读到旧内容后的覆盖写。

方案：以 realpath 为键的**跨进程文件栅栏**（flock 互斥）：
- 锁文件放中央临时目录（sha256(realpath) 命名），不在项目目录里撒 *.lock；
- flock 是建议性锁、进程死亡自动释放——无 stale lock 问题（对比 O_EXCL 锁文件）；
- 同进程不同 fd 之间同样互斥（flock 按 open file description 计），进程内、
  进程间一套语义统一；
- 有界等待（默认 10s），超时抛 FileFenceTimeout——调用方必须显式报冲突，
  绝不允许"拿不到锁就无锁裸写"（那等于没修）。

不在本模块范围（诚实边界）：
- terminal/execute_code 的任意副作用无法枚举上锁（交给任务分解/用户意图层）；
- git 操作的 index.lock 串行化（task_decomposer.commit_step）归后续切片。
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import logging
import os
import tempfile
import time

logger = logging.getLogger("acp-agent.tool-exec-fencing")

# 锁文件中央目录：不污染项目树；跨用户/跨进程共享同一目录即可互斥
_LOCK_DIR = os.path.join(tempfile.gettempdir(), "soulmate_file_fences")
# 有界等待默认值（秒）：读-改-写临界区是小文件同步 IO，正常争用窗口毫秒级
DEFAULT_FENCE_TIMEOUT_S = 10.0
# 轮询间隔（秒）：拿到锁的等待用短轮询（flock 无 async 原生等待）
_DEFAULT_POLL_S = 0.005


class FileFenceTimeout(TimeoutError):
    """文件栅栏有界等待超时（调用方必须显式报冲突，禁止无锁裸写）。"""

    def __init__(self, path: str, timeout: float):
        super().__init__(
            f"文件并发写入冲突: {path} 被其他会话/进程占用超过 {timeout:.0f}s"
        )
        self.path = path
        self.timeout = timeout


def _lock_file_for(path: str) -> str:
    """realpath → 中央锁文件路径（sha256 命名，长度安全、无特殊字符）。"""
    real = os.path.realpath(path)
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()
    return os.path.join(_LOCK_DIR, f"{digest}.lock")


@contextlib.contextmanager
def file_fence(path: str, timeout: float = DEFAULT_FENCE_TIMEOUT_S):
    """跨进程文件互斥栅栏：包住对同一文件的整个 read-modify-write 段。

    用法（patch 工具）：
        with file_fence(path):
            content = open(path).read()      # 读
            new = content.replace(a, b)      # 改
            atomic_write(path, new)          # 写

    超时抛 FileFenceTimeout（TimeoutError 子类）。flock 进程死亡自动释放。
    """
    lock_path = _lock_file_for(path)
    try:
        os.makedirs(_LOCK_DIR, exist_ok=True)
    except OSError as e:
        # 锁目录建不起来时不能静默裸写，也不能炸主链路——按冲突语义报错
        raise FileFenceTimeout(str(path), timeout) from e

    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
    acquired = False
    deadline = time.monotonic() + max(0.0, timeout)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    logger.warning(
                        "[fence] file_fence timeout: %s (waited %.1fs)",
                        path, timeout,
                    )
                    raise FileFenceTimeout(str(path), timeout)
                time.sleep(_DEFAULT_POLL_S)
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)
