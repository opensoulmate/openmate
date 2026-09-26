"""WakeupExecutor — ScheduleWakeup 执行侧闭环（claude-code runtime"到点重新唤醒agent"）。

调研来源：claude-code SDK ScheduleWakeup（feature-matrix/10-claude-code-source.md #4）：
agent 调度"多久后再醒"后，**runtime 在 delay 到点时重新唤醒 agent 跑一轮**——
上一轮（P0-4）只落了"登记意图+noop自报+streak折叠"，执行侧缺口见 dev-reports
P0-4 遗留#1："due_wakeups()已提供到点判定，但尚无executor到点自动触发下一轮"。
本模块即该 executor（claude-code runtime 的执行语义），闭环链路：

  agent工具 schedule_wakeup → opensoul /api/will/wakeup/schedule（状态单一真源）
  → 本executor轮询 GET /api/will/wakeup/due → POST /wakeup/claim CAS领取
  → 无客户端模式跑一个soulmate唤醒回合（agent.prompt）→ POST /wakeup/fire-result 回执

设计纪律：
1. at-most-once：claim 是 SQLite CAS（fired_at IS NULL 且 attempts<MAX 的 UPDATE
   rowcount==1 即唯一赢家）——acp-proxy a/b 双实例都跑executor也不会双跑双烧；
2. 失败必须可见（mem0 §1.1）：fire异常→ack ok=False 释放回待执行=自动重试，
   达 MAX_FIRE_ATTEMPTS 上限进 EXHAUSTED 终态（opensoul侧留痕），绝不无限重试；
3. 可观测（"我都不知道他们在干嘛"）：snapshot() 计数器经
   GET /api/agent/wakeup-executor + /health(wakeup_executor键) 实时可见；
4. 唤醒回合是**自动回合非用户消息**：prompt文本显式声明，并要求agent二选一闭环
   （再调schedule_wakeup继续循环 / stop=true结束），noop如实自报。

进程归属：executor跑在 acp-proxy app 进程（常驻，a/b双实例）。唤醒回合以
无客户端SoulMateAgent执行（client=None→_notify_client天然no-op，消息落
opensoul.db agent_messages，前端下次加载可见）；会话在内存+DB均不存在时
prompt返回refusal→按失败回执（不给消失的会话造僵尸回合）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import httpx

logger = logging.getLogger("acp-proxy.wakeup_executor")


def build_wakeup_prompt(event: dict) -> str:
    """唤醒回合prompt——显式声明是自动回合（不是用户消息），并给出闭环指令。

    claude-code语义：runtime唤醒agent时告诉它"这是你之前要求的唤醒，检查reason"。
    闭环指令是执行侧闭环的一半：agent必须再调度或stop，否则循环自然终止。"""
    reason = (event or {}).get("reason", "")
    streak = (event or {}).get("streak", 0)
    noop = (event or {}).get("noop", False)
    return (
        "⏰ [ScheduleWakeup 到点唤醒｜系统自动回合，非用户消息]\n"
        f"你之前自报的检查计划：{reason}\n"
        f"（上轮自报 noop={noop}，连续空转 streak={streak}）\n"
        "请执行该检查事项。完成后必须二选一，否则唤醒循环自然终止：\n"
        "1) 再次调用 schedule_wakeup 安排下一次唤醒（继续循环，reason说明这次检查了什么，"
        "noop如实自报——空转也要承认）；\n"
        "2) 调用 schedule_wakeup(stop=true) 结束唤醒循环。\n"
        "如无用户新输入，不要执行与检查计划无关的大动作。"
    )


# ── 默认执行体：无客户端SoulMateAgent跑一个唤醒回合 ──────────────

_headless_agent = None


def _get_headless_agent():
    """懒构建无客户端SoulMateAgent（进程内单例；构建失败必须抛出，禁止静默降级）。

    arch_monitor礼貌恢复：SoulMateAgent.__init__会把全局监控面注册成自己的
    EnhancedArchitecture——恢复app.py lifespan注册的那一个，唤醒回合用agent自身
    _arch，不抢全局监控面。"""
    global _headless_agent
    if _headless_agent is None:
        from dotenv import load_dotenv

        load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
        from agent import arch_monitor
        from agent.llm_engine import LLMEngine
        from agent.soulmate_agent import SoulMateAgent

        llm_config = {
            "api_key": os.environ.get("LLM_API_KEY", os.environ.get("OPENAI_API_KEY", "")),
            "base_url": os.environ.get(
                "LLM_BASE_URL", os.environ.get("OPENAI_BASE_URL", "http://localhost:11434/v1")
            ),
            "model": os.environ.get("LLM_MODEL", os.environ.get("OPENAI_MODEL", "qwen3:8b")),
        }
        engine = LLMEngine(**llm_config)
        prev_arch = arch_monitor.get_architecture()
        try:
            agent = SoulMateAgent(llm_engine=engine)
        finally:
            if prev_arch is not None:
                arch_monitor.set_architecture(prev_arch)
        _headless_agent = agent
        logger.info("[wakeup] headless SoulMateAgent constructed (model=%s)", llm_config["model"])
    return _headless_agent


def default_headless_fire(event: dict):
    """默认fire_fn：在本进程以无客户端模式跑一个soulmate唤醒回合。

    session在内存+DB均不存在→prompt返回refusal→抛异常（失败必须可见，
    由ack_fire释放重试/EXHAUSTED，不给消失的会话造僵尸回合）。"""

    async def _run():
        agent = _get_headless_agent()
        resp = await agent.prompt(
            [{"type": "text", "text": build_wakeup_prompt(event)}],
            str(event.get("session_id") or ""),
        )
        stop_reason = str(getattr(resp, "stop_reason", "") or "")
        if stop_reason == "refusal":
            raise RuntimeError(
                "唤醒回合被拒：会话在服务端不存在（memory+DB均未找到），拒绝给消失的会话造回合"
            )
        return {"stop_reason": stop_reason}

    return _run()


class WakeupExecutor:
    """到点唤醒执行器：轮询due → CAS领取 → 执行唤醒回合 → 结果回执。"""

    def __init__(
        self,
        opensoul_url: str = "",
        poll_interval_s: float = 0.0,
        fire_fn=None,
        request_timeout_s: float = 10.0,
        fire_timeout_s: float = 600.0,
    ):
        self._opensoul_url = (
            opensoul_url
            or os.environ.get("WAKEUP_OPENSOUL_URL", "http://127.0.0.1:8090")
        ).rstrip("/")
        self._poll_interval_s = float(
            poll_interval_s
            or float(os.environ.get("WAKEUP_POLL_INTERVAL_S", "15"))
        )
        self._request_timeout_s = request_timeout_s
        self._fire_timeout_s = float(
            fire_timeout_s or float(os.environ.get("WAKEUP_FIRE_TIMEOUT_S", "600"))
        )
        self._fire_fn = fire_fn or default_headless_fire
        self._task: asyncio.Task | None = None
        self._running = False
        self._stats: dict = {
            "polls": 0,
            "due_seen": 0,
            "claimed": 0,
            "claim_lost": 0,
            "claim_errors": 0,
            "fire_ok": 0,
            "fire_failed": 0,
            "ack_errors": 0,
            "last_poll_at": None,
            "last_fire_at": None,
            "last_error": "",
        }

    # ── 生命周期 ────────────────────────────────────────────────

    async def start(self):
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(
            "[wakeup] executor started: poll=%.1fs opensoul=%s",
            self._poll_interval_s,
            self._opensoul_url,
        )

    async def stop(self):
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[wakeup] executor stopped")

    async def _loop(self):
        while self._running:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["last_error"] = f"{type(e).__name__}: {e}"[:300]
                logger.warning("[wakeup] poll cycle error: %s", e)
            await asyncio.sleep(self._poll_interval_s)

    # ── HTTP（httpx.AsyncClient每次调用新建，测试整体替身注入） ──

    async def _http(self, method: str, path: str, **kw) -> httpx.Response:
        async with httpx.AsyncClient(timeout=self._request_timeout_s) as client:
            return await getattr(client, method)(self._opensoul_url + path, **kw)

    # ── 单轮：due → claim → fire → ack ─────────────────────────

    async def poll_once(self) -> dict:
        self._stats["polls"] += 1
        self._stats["last_poll_at"] = time.time()
        resp = await self._http("get", "/api/will/wakeup/due")
        resp.raise_for_status()
        due = (resp.json() or {}).get("due") or []
        self._stats["due_seen"] += len(due)
        fired = 0
        for ev in due:
            if await self._fire_one(ev):
                fired += 1
        return {"due": len(due), "fired": fired}

    async def _fire_one(self, ev: dict) -> bool:
        eid = ev.get("event_id")
        try:
            cresp = await self._http("post", "/api/will/wakeup/claim", json={"event_id": eid})
        except Exception as e:
            self._stats["claim_errors"] += 1
            self._stats["last_error"] = f"claim {eid}: {type(e).__name__}: {e}"[:300]
            logger.warning("[wakeup] claim %s failed: %s", eid, e)
            return False
        if cresp.status_code == 409:
            # CAS输给对端实例=正常路径（at-most-once），不算错误
            self._stats["claim_lost"] += 1
            return False
        if cresp.status_code != 200:
            # 非409非200=opensoul侧异常，必须可见（与CAS败北区分开）
            self._stats["claim_errors"] += 1
            self._stats["last_error"] = f"claim {eid}: HTTP {cresp.status_code}"[:300]
            logger.warning("[wakeup] claim %s HTTP %s", eid, cresp.status_code)
            return False
        self._stats["claimed"] += 1
        ok, err = True, ""
        try:
            await asyncio.wait_for(self._fire_fn(ev), timeout=self._fire_timeout_s)
        except TimeoutError:
            ok, err = False, f"fire timeout after {self._fire_timeout_s}s"
        except Exception as e:
            ok, err = False, f"{type(e).__name__}: {e}"[:300]
        # 回执失败也要可见，但不影响本轮主路径（opensoul侧下次due仍可见/EXHAUSTED兜底）
        try:
            aresp = await self._http(
                "post",
                "/api/will/wakeup/fire-result",
                json={"event_id": eid, "ok": ok, "error": err},
            )
            if aresp.status_code != 200:
                self._stats["ack_errors"] += 1
                self._stats["last_error"] = f"ack {eid}: HTTP {aresp.status_code}"[:300]
        except Exception as e:
            self._stats["ack_errors"] += 1
            self._stats["last_error"] = f"ack {eid}: {type(e).__name__}: {e}"[:300]
            logger.warning("[wakeup] ack %s failed: %s", eid, e)
        self._stats["fire_ok" if ok else "fire_failed"] += 1
        self._stats["last_fire_at"] = time.time()
        if ok:
            logger.info(
                "[wakeup] fired event %s (session=%s reason=%r)",
                eid,
                ev.get("session_id"),
                (ev.get("reason") or "")[:60],
            )
        else:
            logger.warning("[wakeup] fire %s failed: %s", eid, err)
        return ok

    # ── 可观测（monitoring/health直接消费） ─────────────────────

    def snapshot(self) -> dict:
        return {
            "running": self._running,
            "opensoul_url": self._opensoul_url,
            "poll_interval_s": self._poll_interval_s,
            "fire_timeout_s": self._fire_timeout_s,
            **self._stats,
        }


# ── 单例（get_wakeup_scheduler/get_job_queue 同款惯例） ──────────

_executor: WakeupExecutor | None = None


def get_wakeup_executor() -> WakeupExecutor:
    global _executor
    if _executor is None:
        _executor = WakeupExecutor()
    return _executor
