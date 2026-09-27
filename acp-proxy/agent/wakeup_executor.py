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
from collections import deque

import httpx

logger = logging.getLogger("acp-proxy.wakeup_executor")

# ── LLM级失败判定（P0-4执行侧遗留#2："LLM错误回合不能假装成功"）────────
# LLMEngine 吞异常/超时时把机器标记当文本返回/追加（llm_engine.py
# `return f"[LLM错误: {e}]"` / 流式 `yield f"\\n[LLM错误: {e}]"` /
# `"\\n[LLM响应超时]"`），与 infection_guard._TAGGED_FAILURE_PREFIX 同语义——
# 模型真实回复不会以此格式开头/收尾（正文引用不算，见 _is_llm_failure_text）。
# mem0 §1.1 失败必须可见禁止假装成功：唤醒回合输出若是机器错误标记（如
# provider 402 余额不足），必须按 fire 失败回执（释放重试/上限EXHAUSTED留痕），
# 不能算"回合已执行"——否则 LLM 故障期间唤醒循环被错误地标记为成功。
_LLM_FAILURE_MARKERS = ("[LLM错误", "[LLM响应超时")

# 唤醒回合出身标记（wakeup_relay实时推送面单一真源）：default_headless_fire在
# prompt()窗口内给落库行打turn_origin=WAKEUP_TURN_ORIGIN，relay据此把唤醒回合
# 行实时推给正连该会话的前端（跨proxy实例可见性，11:11遗留#3）。WAKEUP_PROMPT_MARKER
# 是唤醒prompt的固定开头（识别/展示面复用，不自创第二份字面量）。
WAKEUP_PROMPT_MARKER = "⏰ [ScheduleWakeup 到点唤醒"
WAKEUP_TURN_ORIGIN = "wakeup"


def _is_llm_failure_text(text: str | None) -> bool:
    """输出是否为LLM级失败机器标记（fail-closed只认肯定证据）。

    判定面：整段以标记开头（chat()非流式整段错误）或末行以标记开头
    （流式在已有内容后追加错误chunk）。正文中间引用该字样不判失败。"""
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith(_LLM_FAILURE_MARKERS):
        return True
    return t.splitlines()[-1].strip().startswith(_LLM_FAILURE_MARKERS)


def _max_assistant_id(agent, session_id: str) -> int | None:
    """本轮开始前会话最新assistant消息id（turn归因锚点——旧轮的错误文本
    不算本轮失败）。

    会话尚无assistant消息→返回0（id从1起，0=时间原点锚点，id>0即全部新消息）；
    取数失败→None（无法归因，判定侧fail-open）。此区分是零输出判定的前提：
    "没有新输出"与"取数失败"必须可判别。"""
    try:
        db = agent._get_db()
        try:
            row = db.execute(
                "SELECT MAX(id) FROM agent_messages WHERE session_id=? AND role='assistant'",
                (session_id,),
            ).fetchone()
        finally:
            db.close()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception as e:
        logger.warning("[wakeup] turn归因锚点取数失败（fail-open）: %s", e)
        return None


def _llm_failure_reply(agent, session_id: str, after_id: int | None) -> str | None:
    """检查本轮新落库的assistant输出是否为LLM级失败标记。

    只认肯定证据（命中标记才判失败），取数失败/无新消息 fail-open 不判失败——
    DB读故障不该把成功回合误判成失败引发重试风暴。"""
    try:
        db = agent._get_db()
        try:
            if after_id is None:
                rows = db.execute(
                    "SELECT content FROM agent_messages "
                    "WHERE session_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                    (session_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT content FROM agent_messages "
                    "WHERE session_id=? AND role='assistant' AND id>? ORDER BY id",
                    (session_id, int(after_id)),
                ).fetchall()
        finally:
            db.close()
        for r in rows:
            content = r[0] or ""
            if _is_llm_failure_text(content):
                return content.strip()[-300:]
        return None
    except Exception as e:
        logger.warning("[wakeup] LLM失败判定取数失败（fail-open不判失败）: %s", e)
        return None


def _has_turn_output(agent, session_id: str, after_id: int | None) -> bool | None:
    """本轮是否落库了非空assistant输出（turn归因锚点之后）。

    返回False=本轮零输出（回合无声消失：跑了但什么都没留下）——按失败回执
    释放重试，不假装成功（mem0 §1.1；13:40轮遗留#4"回合零输出判据"：此前
    fail-open按成功，缓存丢回复等无声回合被记成唤醒成功）。纯空白输出同判
    零输出（用户/前端看不见任何内容）。
    返回None=无法判定（锚点缺失/取数失败）→fail-open不判失败——DB读故障
    不得把成功回合误判成失败引发重试风暴。"""
    if after_id is None:
        return None
    try:
        db = agent._get_db()
        try:
            rows = db.execute(
                "SELECT content FROM agent_messages "
                "WHERE session_id=? AND role='assistant' AND id>?",
                (session_id, int(after_id)),
            ).fetchall()
        finally:
            db.close()
        return any((r[0] or "").strip() for r in rows)
    except Exception as e:
        logger.warning("[wakeup] 零输出判定取数失败（fail-open不判失败）: %s", e)
        return None


def build_wakeup_prompt(event: dict) -> str:
    """唤醒回合prompt——显式声明是自动回合（不是用户消息），并给出闭环指令。

    claude-code语义：runtime唤醒agent时告诉它"这是你之前要求的唤醒，检查reason"。
    闭环指令是执行侧闭环的一半：agent必须再调度或stop，否则循环自然终止。"""
    reason = (event or {}).get("reason", "")
    streak = (event or {}).get("streak", 0)
    noop = (event or {}).get("noop", False)
    return (
        f"{WAKEUP_PROMPT_MARKER}｜系统自动回合，非用户消息]\n"
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
        from utils.llm_config import describe, resolve_agent_llm_config

        # LLM配置收口单一真源（2026-09-27销账遗留#2）：旧实现此处自扫
        # LLM_*elseOPENAI_* + 硬编码 qwen3:8b 兜底，与 llm_engine/start 三处
        # 双源漂移。现委托 agent 调用面契约（显式参数 > 宿主注入OPENAI_* >
        # 静态单一真源 > 裸机本地Ollama兜底），返回形状保持不变（LLMEngine(**)零改动）。
        _cfg = resolve_agent_llm_config()
        logger.info("[wakeup] LLM config: %s", describe(_cfg))
        llm_config = {
            "api_key": _cfg["api_key"],
            "base_url": _cfg["base_url"],
            "model": _cfg["model"],
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
        sid = str(event.get("session_id") or "")
        # turn归因锚点：只把本轮新落库的assistant输出纳入LLM级失败判定
        pre_id = _max_assistant_id(agent, sid)
        # 回合出身标记（11:11遗留#3实时可见前提）：本回合落库行带
        # turn_origin=wakeup——wakeup_relay据此补推给正连该会话的前端（唤醒回合
        # 在headless实例/另一proxy实例执行，_notify_client天然no-op）。异常路径
        # finally也清除，不残留污染下一回合。
        agent.set_turn_origin(sid, WAKEUP_TURN_ORIGIN)
        try:
            resp = await agent.prompt(
                [{"type": "text", "text": build_wakeup_prompt(event)}],
                sid,
            )
        finally:
            agent.clear_turn_origin(sid)
        stop_reason = str(getattr(resp, "stop_reason", "") or "")
        if stop_reason == "refusal":
            raise RuntimeError(
                "唤醒回合被拒：会话在服务端不存在（memory+DB均未找到），拒绝给消失的会话造回合"
            )
        # LLM级失败判定（遗留#2）：回合"跑了"但输出是provider错误/超时的机器标记
        # （如402余额不足）→按失败回执释放重试，禁止假装成功（mem0 §1.1）
        bad = _llm_failure_reply(agent, sid, pre_id)
        if bad:
            raise RuntimeError(
                "唤醒回合LLM级失败（provider错误/超时机器标记，按失败重试不假装成功）: "
                + bad
            )
        # 零输出判定（13:40遗留#4）：回合无声消失（跑了但没落任何非空assistant
        # 输出）不能记成功——按失败回执释放重试，让"说了会醒却什么都没做"可见
        if _has_turn_output(agent, sid, pre_id) is False:
            raise RuntimeError(
                "唤醒回合零输出（本轮未落库任何非空assistant输出）——回合无声消失，按失败重试不假装成功"
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
        # 活动流环形缓冲（monitoring面板"tail日志流"数据面）：每个claim/fire/ack
        # 动作留一条可读记录，appendleft=最新在前，容量20防增长
        self._recent: deque = deque(maxlen=20)
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
            "last_error_at": None,
        }

    def _note_error(self, msg: str) -> None:
        """记录最近错误+发生时刻（遗留#4：last_error只增不清会误导"现在还在错"，
        last_error_at让监控面能判新鲜度；成功poll后整组归空见poll_once）。"""
        self._stats["last_error"] = msg[:300]
        self._stats["last_error_at"] = time.time()

    def _note_activity(self, kind: str, event_id=None, detail: str = "") -> None:
        """活动流留痕（monitoring面板tail数据面）：claim/fire/ack每个动作一条。

        kind取值：claimed / claim_lost / claim_error / fire_ok / fire_failed /
        ack_error / poll_error。与logger不同——logger进journal文件，这里进
        snapshot().recent 供面板实时渲染"它刚才干了什么"。"""
        self._recent.appendleft(
            {"ts": time.time(), "kind": kind, "event_id": event_id, "detail": detail[:200]}
        )

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
                self._note_error(f"{type(e).__name__}: {e}")
                self._note_activity("poll_error", None, f"{type(e).__name__}: {e}")
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
        err_before = (
            self._stats["claim_errors"]
            + self._stats["ack_errors"]
            + self._stats["fire_failed"]
        )
        resp = await self._http("get", "/api/will/wakeup/due")
        resp.raise_for_status()
        due = (resp.json() or {}).get("due") or []
        self._stats["due_seen"] += len(due)
        fired = 0
        for ev in due:
            if await self._fire_one(ev):
                fired += 1
        err_after = (
            self._stats["claim_errors"]
            + self._stats["ack_errors"]
            + self._stats["fire_failed"]
        )
        if err_after == err_before:
            # 成功poll后归空（遗留#4）：干净一轮清掉历史last_error，
            # 配合last_error_at判新鲜度，监控面不再被陈旧错误误导"现在还在错"
            self._stats["last_error"] = ""
            self._stats["last_error_at"] = None
        return {"due": len(due), "fired": fired}

    async def _fire_one(self, ev: dict) -> bool:
        eid = ev.get("event_id")
        try:
            cresp = await self._http("post", "/api/will/wakeup/claim", json={"event_id": eid})
        except Exception as e:
            self._stats["claim_errors"] += 1
            self._note_error(f"claim {eid}: {type(e).__name__}: {e}")
            self._note_activity("claim_error", eid, f"{type(e).__name__}: {e}")
            logger.warning("[wakeup] claim %s failed: %s", eid, e)
            return False
        if cresp.status_code == 409:
            # CAS输给对端实例=正常路径（at-most-once），不算错误
            self._stats["claim_lost"] += 1
            self._note_activity("claim_lost", eid, "CAS输给对端实例（at-most-once正常路径）")
            return False
        if cresp.status_code != 200:
            # 非409非200=opensoul侧异常，必须可见（与CAS败北区分开）
            self._stats["claim_errors"] += 1
            self._note_error(f"claim {eid}: HTTP {cresp.status_code}")
            self._note_activity("claim_error", eid, f"HTTP {cresp.status_code}")
            logger.warning("[wakeup] claim %s HTTP %s", eid, cresp.status_code)
            return False
        self._stats["claimed"] += 1
        self._note_activity(
            "claimed", eid, f"session={ev.get('session_id')} reason={(ev.get('reason') or '')[:60]}"
        )
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
                self._note_error(f"ack {eid}: HTTP {aresp.status_code}")
                self._note_activity("ack_error", eid, f"HTTP {aresp.status_code}")
        except Exception as e:
            self._stats["ack_errors"] += 1
            self._note_error(f"ack {eid}: {type(e).__name__}: {e}")
            self._note_activity("ack_error", eid, f"{type(e).__name__}: {e}")
            logger.warning("[wakeup] ack %s failed: %s", eid, e)
        self._stats["fire_ok" if ok else "fire_failed"] += 1
        self._stats["last_fire_at"] = time.time()
        if ok:
            self._note_activity("fire_ok", eid, f"session={ev.get('session_id')}")
            logger.info(
                "[wakeup] fired event %s (session=%s reason=%r)",
                eid,
                ev.get("session_id"),
                (ev.get("reason") or "")[:60],
            )
        else:
            # 失败必须可见（mem0 §1.1）：fire失败同样进last_error账本
            self._note_error(f"fire {eid}: {err}")
            self._note_activity("fire_failed", eid, err)
            logger.warning("[wakeup] fire %s failed: %s", eid, err)
        return ok

    # ── 可观测（monitoring/health直接消费） ─────────────────────

    def snapshot(self) -> dict:
        return {
            "running": self._running,
            "opensoul_url": self._opensoul_url,
            "poll_interval_s": self._poll_interval_s,
            "fire_timeout_s": self._fire_timeout_s,
            # recent：活动流环形缓冲（最新在前，容量20）——monitoring面板tail数据面
            "recent": list(self._recent),
            **self._stats,
        }


# ── 单例（get_wakeup_scheduler/get_job_queue 同款惯例） ──────────

_executor: WakeupExecutor | None = None


def get_wakeup_executor() -> WakeupExecutor:
    global _executor
    if _executor is None:
        _executor = WakeupExecutor()
    return _executor
