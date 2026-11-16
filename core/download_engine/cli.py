"""cli.py — 入口：python -m download_engine（Electron spawn / 后端服务两种跑法共用）。

启动后向 stdout 打一行 JSON（Electron 捕获）：
  {"event":"listening","port":N,"token":"...","pid":N,"dataDir":"...","origin":"local"}
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys

from . import __version__
from .api import ApiServer
from .engine import Engine
from .util import generate_token


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="download_engine",
        description="OpenMate 本机下载引擎（HTTP 分段 + 自研 BT 内核）",
    )
    p.add_argument("--data-dir", default="./openmate-engine-data", help="状态/会话持久化目录")
    p.add_argument("--dir", default=None, help="默认下载目录（调用方传入；引擎不硬编码 ~/Downloads）")
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅本机）")
    p.add_argument("--port", type=int, default=0, help="监听端口，0=随机")
    p.add_argument("--token", default=None, help="REST token（默认读环境变量 OPENMATE_ENGINE_TOKEN，再缺省随机生成）")
    p.add_argument("--concurrency", type=int, default=5, help="HTTP 分段并发数")
    p.add_argument("--speed-limit", type=int, default=0, help="全局限速 bytes/s，0=不限")
    p.add_argument("--origin", default="local", choices=["local", "remote"], help="任务 origin 值（后端部署可传 remote）")
    p.add_argument("--version", action="version", version=f"openmate-engine {__version__}")
    return p


async def _amain(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("OPENMATE_ENGINE_TOKEN") or generate_token()
    engine = Engine(
        data_dir=args.data_dir,
        default_dir=args.dir,
        concurrency=args.concurrency,
        speed_limit=args.speed_limit,
        origin=args.origin,
    )
    server = ApiServer(engine, token, host=args.host, port=args.port)
    port = await server.start()
    await engine.start()
    print(
        json.dumps(
            {
                "event": "listening",
                "port": port,
                "token": token,
                "pid": os.getpid(),
                "dataDir": engine.data_dir,
                "origin": engine.origin,
                "version": __version__,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    serve_task = asyncio.get_running_loop().create_task(server.serve())
    stop_task = asyncio.get_running_loop().create_task(stop.wait())
    await asyncio.wait({serve_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    await server.stop()
    await engine.shutdown()
    return 0


def main(argv=None) -> int:  # noqa: ANN001
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
