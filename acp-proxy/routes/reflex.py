"""⚡Reflex 反射层观察面 REST API — 只读（统计/事件流/熔断状态）

路由前缀: /api/reflex
数据源: agent.reflex.get_reflex().stats()/recent() —— 进程级 ReflexHub
（/ws_acp 双向事件源桥 ws_acp._reflex_observe 同源喂入：in=用户动作/快捷键/
窗口变化/前端上报，out=工具返回/报错信号；捕获→轻量规则→回调/路由/拦截，
零 LLM、emit 永不抛异常）。

只读铁律：观察面只看不动——不发事件、不改规则、不重置计数。
"""

import logging

from fastapi import APIRouter, Query

from agent.reflex import get_reflex

logger = logging.getLogger("acp-proxy.reflex_api")

router = APIRouter(prefix="/api/reflex", tags=["reflex"])


@router.get("/stats")
async def reflex_stats() -> dict:
    """ReflexHub.stats() 全量：事件延迟（avg/max ms）、熔断状态（rules[].circuit
    + circuit_trips + error_storms）、按类目/终态计数、风暴护栏丢弃计数。"""
    return get_reflex().stats()


@router.get("/events")
async def reflex_events(limit: int = Query(50, ge=1, le=500)) -> dict:
    """事件流：ReflexHub.recent(limit)（列表尾部=最新）。

    每条含 kind/verdict/latency_ms/text/payload/actions（回调/路由/拦截产物）。
    """
    hub = get_reflex()
    events = hub.recent(limit)
    return {"events": events, "count": len(events),
            "total": hub.stats()["total"]}
