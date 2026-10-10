"""OpenMate内置Agent启动入口

支持两种模式：
1. WebSocket模式：python -m agent.start（默认，端口8787）
2. Stdio模式：python -m agent.start --stdio（供ACP Proxy通过spawn_stdio_transport调用）

用法：
    cd acp-proxy && python -m agent.start
    cd acp-proxy && python -m agent.start --stdio
"""

import asyncio
import logging
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from agent.llm_engine import LLMEngine
from utils.llm_config import describe, resolve_agent_llm_config

logger = logging.getLogger("acp-agent")

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)


def load_llm_config() -> dict:
    """读取内置Agent LLM配置——收口 utils.llm_config.resolve_agent_llm_config 单一真源。

    2026-09-27销账遗留#2：本函数曾自扫 LLM_*elseOPENAI_* + 硬编码 qwen3:8b 兜底，
    与 llm_engine/wakeup_executor 三处双源漂移。现委托 agent 调用面契约
    （显式参数 > 宿主注入OPENAI_* > 静态单一真源 > 裸机本地Ollama兜底），
    key_source 可观测，key原文绝不进日志。返回形状保持 {api_key, base_url, model}
    不变（LLMEngine(**config) 零改动）。
    """
    cfg = resolve_agent_llm_config()
    logger.info(f"LLM config: {describe(cfg)}")
    return {"api_key": cfg["api_key"], "base_url": cfg["base_url"], "model": cfg["model"]}


async def main():
    llm_config = load_llm_config()
    llm_engine = LLMEngine(**llm_config)

    # 网络恢复回调（kilocode reply()的MCP重连语义）必须注册在park所在进程：
    # 真实chat turn的LLM调用/park发生在本agent进程（app.py的注册只覆盖app进程内
    # 的discussion park），此前子进程park恢复后MCP重连从未执行——接线补到真源。
    try:
        from utils import network_state
        network_state.register_restore_callback(
            network_state.reconnect_failed_mcp_servers
        )
        logger.info("network restore callback registered (MCP reconnect)")
    except Exception as e:
        logger.warning(f"network restore callback register failed (非致命): {e}")

    # 检查是否是stdio模式（供ACP Proxy子进程调用）
    if "--stdio" in sys.argv:
        await _run_stdio(llm_engine)
    else:
        await _run_websocket(llm_engine)


async def _run_stdio(llm_engine: LLMEngine):
    """Stdio模式 — 通过stdin/stdout运行官方ACP协议

    供ACP Proxy通过acp.spawn_stdio_transport()调用。
    这是官方ACP的标准传输方式。
    """
    import acp
    from agent.soulmate_agent import SoulMateAgent

    agent = SoulMateAgent(llm_engine=llm_engine)

    # 设置进化引擎（通过HTTP API，因为子进程无法访问主进程的Python对象）
    evo_url = os.environ.get("EVOLUTION_API_URL")
    if evo_url:
        agent._evolution_api_url = evo_url
        logger.info(f"[stdio] Evolution engine API: {evo_url}")
    else:
        logger.warning("[stdio] EVOLUTION_API_URL not set, evolution tools disabled")

    logger.info("Starting SoulMate Agent in stdio mode (ACP v1.0)")

    # 路线图3.1 cron引擎：本进程有存活的任务就拉起调度循环（幂等；多进程/
    # 双实例共存由SQLite CAS领取保证不双跑）。没有任何任务时不拉起零开销。
    try:
        from agent.soulmate_scheduler import get_scheduler as _get_cron_scheduler

        _cron = _get_cron_scheduler()
        if _cron.has_enabled_tasks():
            _cron.ensure_started()
            logger.info("[stdio] cron调度循环已启动（存在未完成定时任务）")
    except Exception as e:
        logger.warning("[stdio] cron调度循环启动失败（不阻塞agent）: %s", e)

    # 路线图3.3 监控告警：有启用的监控目标就拉起监控循环（幂等；无目标零开销）
    try:
        from agent.health_monitor import get_health_monitor as _get_health_monitor

        _monitor = _get_health_monitor()
        if _monitor.has_enabled_targets():
            _monitor.ensure_started()
            logger.info("[stdio] 监控循环已启动（存在启用的监控目标）")
    except Exception as e:
        logger.warning("[stdio] 监控循环启动失败（不阻塞agent）: %s", e)

    # acp.run_agent 默认使用 sys.stdin/sys.stdout
    await acp.run_agent(agent=agent)


async def _run_websocket(llm_engine: LLMEngine):
    """WebSocket模式 — 启动WebSocket服务器"""
    from agent.acp_server import ACPServer

    host = os.environ.get("ACP_AGENT_HOST", "0.0.0.0")
    port = int(os.environ.get("ACP_AGENT_PORT", "8787"))

    acp_server = ACPServer(host=host, port=port)
    acp_server.llm_engine = llm_engine

    logger.info(f"Starting on ws://{host}:{port} (ACP v1.0 official)")

    try:
        await acp_server.serve()
    except KeyboardInterrupt:
        logger.info("Shutting down...")


if __name__ == "__main__":
    asyncio.run(main())
