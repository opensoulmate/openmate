"""OpenMate 本机下载引擎（纯 Python，零 Rust/aria2 依赖）。

组件：
- http_engine：asyncio + httpx 的 HTTP(S) 分段下载（Range 多线段、断点续传、限速）
- bt 核心：bencode / .torrent 解析 / magnet 解析 / tracker announce（BEP3/15）
  / peer wire（BEP3）/ ut_metadata 元数据交换（BEP9/10）/ rarest-first+endgame 分片调度
  / SHA1 校验 / 做种
- api：127.0.0.1 + token 的 REST 层（供 Electron spawn 子进程 / 后端服务两种跑法）
- store：任务 JSON 持久化（重启恢复、断点续传）

入口：python -m download_engine  （见 cli.py / README.md）
"""

__version__ = "0.1.0"

from .models import DownloadTask, TaskStatus, TaskType  # noqa: F401
