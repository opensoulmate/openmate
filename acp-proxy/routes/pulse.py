"""❤️Pulse 心跳层观察面 REST API — 只读（心跳流/自检结果/漂移/僵尸回收）

路由前缀: /api/pulse
数据源: agent.pulse.PulseLoop.status()/recent_beats() —— acp-proxy 进程级
心跳宿主（app lifespan 起后台心跳：DEFAULT_INTERVAL_S=30s 节拍对齐，漂移/
漏拍可观测；每拍六段逐段隔离，软预算禁硬杀）。

② 周期自检五类目由本宿主接既有件（pulse.py 设计：五类目钩子由宿主接线）：
- backend_service    : 平台服务端口探活（opensoul/acp-proxy×2/a2a/mcp/ui）
- connection_state   : ACP stdio 桥存活 + /ws_acp 观察窗（ws_acp._LIVE）
- task_queue         : 唤醒执行器队列（agent/wakeup_executor：due→领取→fire→ack）
- work_order         : 未完成工单（心跳宿主 LongTask 账 + 唤醒回合在途）
- memory_consistency : 记忆库可读一致（opensoul.db 记忆大脑 + gene_loop.db）

自检发现（finding）落日志并旁路进 ReflexHub（get_reflex().on_error）——
故障在 /api/reflex/events 事件流同样可见。僵尸回收只收本进程子进程尸体
（os.waitpid(WNOHANG)，绝不给活进程发信号）。

只读铁律：观察面只看不动——不代发心跳、不改检查、不动工单。
"""

import logging
import threading
import time

from fastapi import APIRouter, Query

from agent.pulse import (
    CATEGORY_BACKEND,
    CATEGORY_CONNECTION,
    CATEGORY_MEMORY,
    CATEGORY_QUEUE,
    CATEGORY_WORK_ORDER,
    PulseCheck,
    PulseLoop,
)

logger = logging.getLogger("acp-proxy.pulse_api")

router = APIRouter(prefix="/api/pulse", tags=["pulse"])


# ── 五类目自检（宿主接既有件，真实探测） ────────────────────────────


def _check_backend_services():
    """后端服务：平台关键端口 TCP 探活（critical：全挂=心跳也救不了）。"""
    import socket
    ports = {8090: "opensoul-brain", 8092: "acp-proxy-a", 8095: "acp-proxy-b",
             8093: "openmate-a2a", 8094: "mcp-client", 3000: "openmate-ui"}
    down = []
    for port, name in ports.items():
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        except OSError:
            down.append(f"{name}:{port}")
    return {"ok": not down,
            "detail": "全部在听" if not down else "未监听: " + ", ".join(down)}


def _check_connection_state():
    """连接状态：ACP stdio 桥（proxy.get_acp_process）+ /ws_acp 观察窗。"""
    try:
        from proxy import get_acp_process
        acp_running = bool(get_acp_process().is_running)
    except Exception as e:  # noqa: BLE001 — 探测异常=发现故障
        return {"ok": False, "detail": f"acp stdio 探测异常: {type(e).__name__}: {e}"}
    try:
        import ws_acp
        live = getattr(ws_acp, "_LIVE", {}) or {}
        alive = sum(1 for e in live.values()
                    if (e.get("proc") is None or e.get("proc").returncode is None))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"ws_acp 观察窗探测异常: {type(e).__name__}: {e}"}
    return {"ok": True,
            "detail": (f"acp_stdio={'running' if acp_running else 'idle'} "
                       f"ws_live={len(live)} detached_alive={alive}")}


def _check_task_queue():
    """任务队列：唤醒执行器（due→CAS领取→fire→ack）计数与新鲜错误。"""
    try:
        from agent.wakeup_executor import get_wakeup_executor
        snap = get_wakeup_executor().snapshot()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"wakeup executor 快照异常: {type(e).__name__}: {e}"}
    last_err_at = snap.get("last_error_at")
    fresh_err = bool(last_err_at) and (time.time() - float(last_err_at)) < 120
    detail = (f"running={snap.get('running')} polls={snap.get('polls')} "
              f"due_seen={snap.get('due_seen')} claimed={snap.get('claimed')} "
              f"fire_ok={snap.get('fire_ok')} fire_failed={snap.get('fire_failed')} "
              f"claim_errors={snap.get('claim_errors')} ack_errors={snap.get('ack_errors')}")
    if fresh_err:
        detail += f" | 新鲜错误: {snap.get('last_error', '')[:120]}"
    return {"ok": not fresh_err, "detail": detail}


def _check_work_orders():
    """未完成工单：心跳宿主 LongTask 账（overdue/needs_attention 算异常）
    + 唤醒回合在途（claimed 未回执）。"""
    try:
        st = get_pulse().status()
        tasks = st.get("tasks") or {}
        by_status = tasks.get("by_status") or {}
        bad = sorted(set(by_status) & {"overdue", "needs_attention"})
        items = [t for t in (tasks.get("items") or [])
                 if t.get("status") in ("overdue", "needs_attention")]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"工单账读取异常: {type(e).__name__}: {e}"}
    inflight = 0
    try:
        from agent.wakeup_executor import get_wakeup_executor
        snap = get_wakeup_executor().snapshot()
        inflight = max(0, int(snap.get("claimed") or 0)
                       - int(snap.get("fire_ok") or 0)
                       - int(snap.get("fire_failed") or 0))
    except Exception:  # noqa: BLE001 — 在途数拿不到不判失败
        pass
    detail = (f"工单 total={tasks.get('total', 0)} by_status={by_status or '{}'} "
              f"唤醒在途={inflight}")
    if items:
        detail += " | " + "; ".join(
            f"{t.get('name') or t.get('task_id')}:{t.get('status')}" for t in items[:5])
    return {"ok": not bad, "detail": detail}


def _check_memory_consistency():
    """记忆一致性：记忆库 SQLite 可读且结构可查（opensoul.db + gene_loop.db）。"""
    import os
    import sqlite3
    stores = {
        "opensoul.db": os.path.join(os.path.expanduser("~"),
                                    "opensoul", "data", "opensoul.db"),
        "gene_loop.db": os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "data", "gene_loop.db"),
    }
    details = []
    ok = True
    for name, path in stores.items():
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
            try:
                n = conn.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type='table'"
                ).fetchone()[0]
            finally:
                conn.close()
            details.append(f"{name}:tables={n}")
        except Exception as e:  # noqa: BLE001 — 读不到/坏库=一致性故障
            ok = False
            details.append(f"{name}:FAIL({type(e).__name__}: {str(e)[:80]})")
    return {"ok": ok, "detail": " ".join(details)}


def _build_checks() -> list:
    """② 周期自检五类目清单（宿主按类目接既有件，不平行造轮子）。"""
    return [
        PulseCheck("backend_services", _check_backend_services,
                   category=CATEGORY_BACKEND, critical=True),
        PulseCheck("connection_state", _check_connection_state,
                   category=CATEGORY_CONNECTION),
        PulseCheck("task_queue", _check_task_queue,
                   category=CATEGORY_QUEUE),
        PulseCheck("unfinished_work_orders", _check_work_orders,
                   category=CATEGORY_WORK_ORDER),
        PulseCheck("memory_consistency", _check_memory_consistency,
                   category=CATEGORY_MEMORY),
    ]


def _on_finding(finding: dict) -> None:
    """自检发现落日志 + Reflex 旁路留痕（never-raise：故障在事件流同样可见）。"""
    logger.warning("[pulse] 自检发现: %s (%s): %s",
                   finding.get("check"), finding.get("category"),
                   finding.get("detail"))
    try:
        from agent.reflex import get_reflex
        get_reflex().on_error(error=str(finding.get("detail") or ""),
                              where=f"pulse.{finding.get('check')}")
    except Exception:  # noqa: BLE001
        pass


# ── 进程级心跳宿主（app lifespan 起停；观测面共享同一实例） ──────────

_pulse: PulseLoop | None = None
_pulse_lock = threading.Lock()


def get_pulse() -> PulseLoop:
    """进程级 PulseLoop 单例（lazy-init，与 get_reflex 同款惯例）。"""
    global _pulse
    with _pulse_lock:
        if _pulse is None:
            _pulse = PulseLoop(checks=_build_checks(), on_finding=_on_finding)
        return _pulse


async def start_pulse() -> None:
    """起后台心跳（幂等；已在跑则复用）。失败只记日志，不阻断宿主启动。"""
    try:
        get_pulse().start()
        logger.info("[pulse] 心跳宿主已启动（interval=%.0fs）",
                    get_pulse().interval_s)
    except Exception as e:  # noqa: BLE001
        logger.warning("[pulse] 心跳启动失败(非致命): %s", e)


async def stop_pulse() -> None:
    """软停心跳（等当前拍自然结束）。"""
    pl = _pulse
    if pl is not None:
        try:
            await pl.stop()
        except Exception as e:  # noqa: BLE001
            logger.warning("[pulse] 心跳停止失败(非致命): %s", e)


# ── 只读观察端点 ───────────────────────────────────────────────────


@router.get("/status")
async def pulse_status() -> dict:
    """PulseLoop.status() 全量：beats/漏拍/漂移（avg/max ms）/自检清单/
    工单账/僵尸回收计数（reaped_total）/唤醒计数。"""
    return get_pulse().status()


@router.get("/beats")
async def pulse_beats(limit: int = Query(20, ge=1, le=200)) -> dict:
    """心跳流：PulseLoop.recent_beats(limit)（列表尾部=最新一拍）。

    每拍含自检结果（checks）、故障发现（findings）、工单推进/顺延、僵尸回收、
    空闲唤醒、软预算（elapsed_ms/over_budget）。
    """
    pl = get_pulse()
    return {"beats": pl.recent_beats(limit), "beats_total": pl.beats}


@router.get("/checks")
async def pulse_checks() -> dict:
    """自检清单 + 最近一拍自检结果（五类目：名称/类目/ok/detail/连续失败数）。"""
    pl = get_pulse()
    st = pl.status()
    beats = pl.recent_beats(1)
    last = beats[-1] if beats else {}
    results = {c.get("check"): c for c in last.get("checks", [])}
    items = []
    for c in st.get("checks", []):
        r = results.get(c.get("name"), {})
        items.append({
            **c,
            "ok": r.get("ok"),
            "detail": r.get("detail", ""),
            "severity": r.get("severity", ""),
            "slow": r.get("slow", False),
            "ts": r.get("ts"),
        })
    return {"checks": items,
            "last_beat": last.get("beat"),
            "last_beat_ts": last.get("ts"),
            "findings": last.get("findings", [])}
