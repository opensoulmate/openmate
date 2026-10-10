"""SoulMate 常驻托管守护进程（路线图3.1/3.3遗留销账）

背景：soulmate_scheduler（3.1 cron引擎）与 health_monitor（3.3 监控告警）的后台
循环此前宿主=存活的 agent 进程（start.py stdio 入口"有任务/目标才自举"）——
没有任何客户端会话时无宿主进程，到点任务/检查不执行（错过不补跑），即
"无进程时不执行"遗留。

本模块是独立常驻宿主（systemd --user soulmate-daemon.service）：
- 无条件托管两条循环（与 start.py 嵌入式"有任务才启动"不同——守护进程是
  常驻宿主，用户后续新建任务/目标无需重启即可被轮询自然拾取）
- 与 agent 进程内嵌循环多宿主共存安全：SQLite CAS 到点领取
  （UPDATE rowcount==1）保证同一任务/检查只被一个宿主执行，不双跑
- SIGTERM/SIGINT 优雅退出（systemctl --user stop/restart 干净收场）

用法：
    cd acp-proxy && python -m agent.soulmate_daemon
"""

import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logger = logging.getLogger("soulmate.daemon")


async def run_daemon(scheduler=None, monitor=None, stop_event: asyncio.Event | None = None) -> None:
    """托管调度+监控两条后台循环，直到收到停止信号。

    scheduler/monitor 可注入（测试用）；缺省取生产单例。
    stop_event 可注入（测试用）；缺省注册 SIGTERM/SIGINT 信号。
    """
    if scheduler is None or monitor is None:
        from agent.health_monitor import get_health_monitor
        from agent.soulmate_scheduler import get_scheduler

        scheduler = scheduler if scheduler is not None else get_scheduler()
        monitor = monitor if monitor is not None else get_health_monitor()

    # 无条件托管：不查 has_enabled_tasks/has_enabled_targets——常驻宿主的
    # 意义就是"后建的任务/目标也被拾取"，轮询空转是廉价 SQLite 查询。
    cron_task = asyncio.create_task(scheduler.run_forever())
    mon_task = asyncio.create_task(monitor.run_forever())
    logger.info(
        "[daemon] 常驻托管已启动：cron调度+健康监控（db=%s / %s）",
        getattr(scheduler, "_db_path", "?"), getattr(monitor, "_db_path", "?"),
    )

    stop = stop_event if stop_event is not None else asyncio.Event()
    if stop_event is None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):  # 非POSIX/测试环境
                pass

    try:
        await stop.wait()
    finally:
        # 优雅退出：先让循环自然收尾（_running=False），再取消兜底
        try:
            scheduler.stop()
        except Exception as e:
            logger.warning("[daemon] scheduler.stop 异常（继续退出）: %s", e)
        try:
            monitor.stop()
        except Exception as e:
            logger.warning("[daemon] monitor.stop 异常（继续退出）: %s", e)
        for t in (cron_task, mon_task):
            t.cancel()
        await asyncio.gather(cron_task, mon_task, return_exceptions=True)
        logger.info("[daemon] 已优雅退出")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )
    logger.info("[daemon] soulmate-daemon 启动中（常驻托管宿主）")
    asyncio.run(run_daemon())


if __name__ == "__main__":
    main()
