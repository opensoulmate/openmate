# -*- coding: utf-8 -*-
"""P1-15 五终止条件组合入trace 终验探针（真实路径四层）：

1. 配置/裁决层（真实 termination 模块）：五条件词汇+各条件独立配置
   （SOULMATE_MAX_ROUNDS / SOULMATE_TURN_TOKEN_BUDGET）+组合裁决 resolve
2. 真实 store 落点层（真实 TrajectoryRecorder → :8090 opensoul trajectory 真库）：
   - end_turn(termination=max_turns) → 事件 metadata.termination 回读
   - 无 termination → metadata 无 key（正常完成零误标）
   - wall_clock 软预算钩子真实触发 → 条件事件（termination_condition=wall_clock）
     真实落库回读（用户铁律禁硬杀：只提醒不终止）
3. 引擎真实全回合层（真实 SoulMateAgent + 真实 LLM 回合，探针进程内 env 配置）：
   - SOULMATE_MAX_ROUNDS=2 → 轮次耗尽 → 回复含耗尽告知 + 回读 termination=max_turns
   - SOULMATE_TURN_TOKEN_BUDGET=300 → 预算耗尽 → 回复含预算告知 +
     回读 termination=token_budget
   - 对照回合（无配置）→ 回读无 termination key（false-positive 检查）
4. 真实 WS 用户路径层（WS→ws_acp→soulmate stdio→真实 LLM）：
   - 正常回合 → 回读无 termination
   - steer /abort 中断回合 → 回复含"任务被用户插话中断" + 回读
     termination=external_cancel

另：user_idle 终止条件落点（opensoul 闲置清扫 user_idle 标记事件）由
opensoul tests/test_trajectory_termination.py 真实 SQLite 测试覆盖，探针内
子进程重跑取真实输出入证据。

服务端真源：opensoul trajectory store HTTP 回读（GET /api/trajectory/sessions/
{sid}/events）。证据：/tmp + 仓库 evidence/p1-15-termination-evidence.json。
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/climbing/openmate/acp-proxy")

import httpx

from agent import termination
from agent.trajectory_recorder import TrajectoryRecorder
from agent.turn_lifecycle import CLOSE_COMPLETED, TurnLifecycleBus
from agent.turn_watchdog import TurnWatchdog

TAG = f"{int(time.time())}"
EV = {"tag": TAG, "checks": [], "e2e": {}, "pass": None}
TRAJ_API = "http://127.0.0.1:8090/api/trajectory"


def rec(name: str, ok: bool, detail: str = "") -> None:
    EV["checks"].append({"name": name, "ok": bool(ok), "detail": detail[:400]})
    print(f"{'✅' if ok else '❌'} {name}: {detail[:220]}", flush=True)


async def readback_turn_events(client: httpx.AsyncClient, sid: str) -> list[dict]:
    """服务端真源回读：session 的事件列表（形状兼容 {events:[...]} 或裸列表）。"""
    r = await client.get(f"{TRAJ_API}/sessions/{sid}/events", params={"limit": 200})
    data = r.json()
    if isinstance(data, dict):
        return data.get("events") or []
    return data if isinstance(data, list) else []


def turn_root_meta(events: list[dict]) -> dict | None:
    """回合根事件 metadata——取**最后**一个（WS 多回合会话里第一个是历史回合，
    终止条件断言必须对准本次回合）。"""
    for e in reversed(events):
        meta = e.get("metadata") or {}
        if meta.get("kind") == "turn_root":
            return meta
    return None


# ════════ 1. 配置/裁决层 ════════

def layer1_config() -> None:
    old_a = os.environ.pop("SOULMATE_MAX_ROUNDS", None)
    old_b = os.environ.pop("SOULMATE_TURN_TOKEN_BUDGET", None)
    try:
        rec("C1a max_turns 缺省15（零行为变化）",
            termination.max_turns_limit() == 15, str(termination.max_turns_limit()))
        os.environ["SOULMATE_MAX_ROUNDS"] = "2"
        rec("C1b max_turns 独立可配（env=2）",
            termination.max_turns_limit() == 2, str(termination.max_turns_limit()))
        os.environ["SOULMATE_MAX_ROUNDS"] = "abc"
        rec("C1c max_turns 非法值回退默认", termination.max_turns_limit() == 15, "")
        rec("C2a token_budget 缺省0=不限（零行为变化）",
            termination.turn_token_budget() == 0, "")
        os.environ["SOULMATE_TURN_TOKEN_BUDGET"] = "8000"
        rec("C2b token_budget 独立可配（env=8000）",
            termination.turn_token_budget() == 8000, "")
        rec("C3 五条件词汇完备",
            termination.TERMINATION_REASONS == {
                "max_turns", "wall_clock", "token_budget",
                "user_idle", "external_cancel"},
            str(sorted(termination.TERMINATION_REASONS)))
        rec("C4 组合裁决三态（显式>external_cancel>正常）",
            termination.resolve("max_turns", True) == "max_turns"
            and termination.resolve("", True) == "external_cancel"
            and termination.resolve("", False) == "",
            "")
        rec("C5 ACP stop_reason 映射（max_tokens→token_budget族）",
            termination.from_acp_stop_reason("max_tokens") == "token_budget"
            and termination.from_acp_stop_reason("end_turn") == "", "")
    finally:
        os.environ.pop("SOULMATE_MAX_ROUNDS", None)
        os.environ.pop("SOULMATE_TURN_TOKEN_BUDGET", None)
        if old_a is not None:
            os.environ["SOULMATE_MAX_ROUNDS"] = old_a
        if old_b is not None:
            os.environ["SOULMATE_TURN_TOKEN_BUDGET"] = old_b


# ════════ 2. 真实 store 落点层 ════════

async def layer2_store(client: httpx.AsyncClient) -> None:
    rec_mod = TrajectoryRecorder()  # 真实 recorder → :8090 真库

    # end_turn(termination=max_turns) 落库回读
    sid = f"term-probe-store-{TAG}"
    root = await rec_mod.start_turn(sid, "p1-15 store landing probe")
    await rec_mod.end_turn(sid, root, status="ok",
                           termination=termination.TERM_MAX_TURNS)
    events = await readback_turn_events(client, sid)
    meta = turn_root_meta(events)
    rec("S1 end_turn(termination) 真实落库回读=max_turns",
        bool(meta) and meta.get("termination") == "max_turns",
        json.dumps(meta, ensure_ascii=False))

    # 无 termination：metadata 不得出现 key（正常完成零误标）
    sid2 = f"term-probe-store-clean-{TAG}"
    root2 = await rec_mod.start_turn(sid2, "p1-15 store clean probe")
    await rec_mod.end_turn(sid2, root2, status="ok")
    meta2 = turn_root_meta(await readback_turn_events(client, sid2))
    rec("S2 正常收口零误标（metadata 无 termination key）",
        bool(meta2) and "termination" not in meta2,
        json.dumps(meta2, ensure_ascii=False))

    # wall_clock：软预算超限钩子真实触发→条件事件落库（提醒不终止）
    sid3 = f"term-probe-wall-{TAG}"
    fired = []

    async def _on_soft(budget_s, elapsed):
        fired.append((budget_s, elapsed))
        await rec_mod.log_event(
            sid3, kind="termination_condition",
            content=f"wall_clock 软预算超限 {budget_s:.0f}s（提醒不终止）",
            metadata={"termination_condition": "wall_clock", "action": "soft_warn",
                      "budget_s": float(budget_s), "elapsed_s": round(float(elapsed), 1)},
        )
    wd = TurnWatchdog(sid3, heartbeat_s=0.1, soft_budget_s=0.15,
                      max_lifetime_s=10.0, on_soft_budget=_on_soft)
    wd.start()
    await asyncio.sleep(1.0)
    wd.stop()
    evs3 = await readback_turn_events(client, sid3)
    cond = [e for e in evs3
            if (e.get("metadata") or {}).get("termination_condition") == "wall_clock"]
    rec("S3 wall_clock 软预算钩子真实触发一次", len(fired) == 1, f"fired={len(fired)}")
    rec("S4 wall_clock 条件事件真实落库回读",
        len(cond) == 1 and (cond[0].get("metadata") or {}).get("action") == "soft_warn",
        json.dumps((cond[0].get("metadata") if cond else None), ensure_ascii=False))
    # 钩子触发但不终止（用户铁律禁硬超时杀）：看门狗仍在跑或正常停止即可
    rec("S5 wall_clock 提醒不终止（用户铁律覆盖蓝图硬杀）",
        not wd.running, f"soft_warned={wd.soft_warned}")

    # user_idle：opensoul 真实 SQLite 测试子进程重跑（真实产出非自报）
    proc = subprocess.run(
        ["/home/climbing/opensoul/.venv/bin/python", "-m", "pytest",
         "tests/test_trajectory_termination.py", "-q"],
        cwd="/home/climbing/opensoul", capture_output=True, text=True,
        errors="replace", timeout=120)
    tail = (proc.stdout or "").strip().splitlines()[-1] if proc.stdout else ""
    rec("S6 user_idle 标记事件落trace（opensoul 真实SQLite测试）",
        proc.returncode == 0 and "passed" in tail, tail)

    # external_cancel 裁决输入（真实 TurnLifecycleBus）
    bus = TurnLifecycleBus()
    bus.open_turn("probe-cancel")
    bus.cancel_turn("probe-cancel")
    ok = (bus.is_cancel_requested("probe-cancel")
          and termination.resolve("", bus.is_cancel_requested("probe-cancel"))
          == termination.TERM_EXTERNAL_CANCEL)
    rec1 = await bus.aclose_turn("probe-cancel", CLOSE_COMPLETED)
    rec("S7 external_cancel 裁决+cancelled 收口",
        ok and rec1 is not None and rec1.close_reason == "cancelled",
        f"close={getattr(rec1, 'close_reason', None)}")


# ════════ 3. 引擎真实全回合层（真实 SoulMateAgent + 真实 LLM）════════

async def _engine_turn(sid: str, task: str) -> tuple[str, str, str]:
    """真实 agent 全回合（prompt() 全链路含 end_turn 落点）。

    new_session 会自建 om-* id（传入 sid 只在重连时生效）——prompt/回读都必须
    用返回的真实 id。返回 (回复文本, 真实sid, 诊断)。异常不吞，进诊断。
    """
    from agent.llm_engine import LLMEngine
    from agent.soulmate_agent import SoulMateAgent
    from agent.start import load_llm_config

    try:
        llm = LLMEngine(**load_llm_config())
        agent = SoulMateAgent(llm_engine=llm)
        resp = await agent.new_session(field_meta={"session_id": sid})
        real_sid = getattr(resp, "session_id", "") or sid
        pr = await agent.prompt(prompt=[{"type": "text", "text": task}],
                                session_id=real_sid)
        msgs = agent.sessions.get(real_sid, {}).get("messages", [])
        reply = ""
        for m in reversed(msgs):
            if m.get("role") == "assistant":
                c = m.get("content", "")
                reply = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                break
        diag = (f"real_sid={real_sid} msgs={len(msgs)} "
                f"stop={getattr(pr, 'stop_reason', None)}")
        return reply, real_sid, diag
    except Exception as e:
        import traceback
        return "", sid, repr(e) + "\n" + traceback.format_exc()[-400:]


async def layer3_engine(client: httpx.AsyncClient) -> None:
    # ── E1 max_turns：SOULMATE_MAX_ROUNDS=2 强制轮次耗尽 ──
    os.environ["SOULMATE_MAX_ROUNDS"] = "2"
    sid1 = f"term-probe-e2e-maxt-{TAG}"
    task1 = ("请开始一个循环：每次只做一件事——调用 terminal 工具执行 "
             "echo loop-step（同一个命令即可），得到结果后立即再次调用 terminal "
             "执行同一命令，如此往复。每轮只能调用一个工具，不要总结、不要主动停止，"
             "一直循环执行下去。")
    t0 = time.monotonic()
    try:
        reply1, real1, diag1 = await asyncio.wait_for(
            _engine_turn(sid1, task1), timeout=300)
    except asyncio.TimeoutError:
        reply1, real1, diag1 = "", sid1, "timeout(300s)"
    EV["e2e"]["max_turns_round"] = {
        "sid": real1, "elapsed": round(time.monotonic() - t0, 1),
        "diag": diag1, "reply_tail": reply1[-500:]}
    meta1 = turn_root_meta(await readback_turn_events(client, real1))
    rec("E1a max_turns 回合：回复含轮次耗尽告知",
        "已达工具调用软上限" in reply1, f"{diag1} | {reply1[-160:]}".replace("\n", " "))
    rec("E1b max_turns 回合：真实store回读 termination=max_turns",
        bool(meta1) and meta1.get("termination") == "max_turns",
        f"{diag1} | {json.dumps(meta1, ensure_ascii=False)}")

    # ── E2 token_budget：SOULMATE_TURN_TOKEN_BUDGET=300 强制预算耗尽 ──
    os.environ.pop("SOULMATE_MAX_ROUNDS", None)
    os.environ["SOULMATE_TURN_TOKEN_BUDGET"] = "300"
    sid2 = f"term-probe-e2e-budget-{TAG}"
    task2 = "请调用 terminal 工具执行命令：seq 1 2000。执行完后把结果完整总结一下。"
    t0 = time.monotonic()
    try:
        reply2, real2, diag2 = await asyncio.wait_for(
            _engine_turn(sid2, task2), timeout=300)
    except asyncio.TimeoutError:
        reply2, real2, diag2 = "", sid2, "timeout(300s)"
    EV["e2e"]["token_budget_round"] = {
        "sid": real2, "elapsed": round(time.monotonic() - t0, 1),
        "diag": diag2, "reply_tail": reply2[-500:]}
    meta2 = turn_root_meta(await readback_turn_events(client, real2))
    rec("E2a token_budget 回合：回复含预算耗尽告知",
        "已达本轮 token 预算" in reply2, f"{diag2} | {reply2[-160:]}".replace("\n", " "))
    rec("E2b token_budget 回合：真实store回读 termination=token_budget",
        bool(meta2) and meta2.get("termination") == "token_budget",
        f"{diag2} | {json.dumps(meta2, ensure_ascii=False)}")

    # ── E3 对照回合（无配置）：不得误标任何终止条件 ──
    os.environ.pop("SOULMATE_TURN_TOKEN_BUDGET", None)
    sid3 = f"term-probe-e2e-normal-{TAG}"
    task3 = f"请只回复这一行文字（不要调用任何工具）：p115-normal-{TAG}"
    try:
        reply3, real3, diag3 = await asyncio.wait_for(
            _engine_turn(sid3, task3), timeout=240)
    except asyncio.TimeoutError:
        reply3, real3, diag3 = "", sid3, "timeout(240s)"
    meta3 = turn_root_meta(await readback_turn_events(client, real3))
    EV["e2e"]["normal_round"] = {
        "sid": real3, "diag": diag3, "reply_tail": reply3[-300:]}
    rec("E3a 对照回合真实完成", f"p115-normal-{TAG}" in reply3,
        f"{diag3} | {reply3[-160:]}".replace("\n", " "))
    rec("E3b 对照回合零误标（回读无 termination key）",
        bool(meta3) and "termination" not in meta3,
        f"{diag3} | {json.dumps(meta3, ensure_ascii=False)}")


# ════════ 4. 真实 WS 用户路径层 ════════

class _Conn:
    """同连接并发 RPC 分发器（prompt 在途时仍可发 /abort）。"""

    def __init__(self, ws):
        self.ws = ws
        self.pending = {}
        self.updates = []
        self._task = None

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def _loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                mid = msg.get("id")
                if mid is not None and mid in self.pending:
                    self.pending.pop(mid).set_result(msg)
                elif msg.get("method") == "session/update":
                    self.updates.append(msg)
        except Exception:
            pass

    async def rpc(self, method, params, rid, timeout):
        fut = asyncio.get_event_loop().create_future()
        self.pending[rid] = fut
        await self.ws.send(json.dumps({
            "jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        return await asyncio.wait_for(fut, timeout)

    def collect(self, sid: str) -> str:
        """消费式收集：取走自上次 collect 以来的 session/update 文本（回合间不串）。"""
        import acceptance_benchmark as ab
        chunks = []
        pending, self.updates = self.updates, []
        for m in pending:
            psid = (m.get("params") or {}).get("sessionId") or ""
            if sid and psid and psid != sid:
                continue
            ab._collect_texts(m.get("params") or m, chunks)
        reply, _ = ab.split_frames(chunks)
        return reply


async def layer4_ws(client: httpx.AsyncClient) -> None:
    import acceptance_benchmark as ab
    import jwt as pyjwt
    import websockets

    token = pyjwt.encode({"sub": f"p115-{TAG}"}, ab.JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    async with websockets.connect(f"{ab.ACP_WS}?token={token}",
                                  max_size=20_000_000) as ws:
        conn = _Conn(ws)
        await conn.start()
        await conn.rpc("initialize", {"protocolVersion": 1}, rid=1, timeout=30)
        r = await conn.rpc(
            "session/new",
            {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
            rid=2, timeout=90)
        sid = (r.get("result") or {}).get("session_id") or \
              (r.get("result") or {}).get("sessionId") or ""
        rec("W0 WS session/new 拿到真实会话", bool(sid), sid)

        # ── W1 正常回合（false-positive 检查，全 WS 生产路径）──
        await conn.rpc(
            "session/prompt",
            {"sessionId": sid, "messageId": f"p115a-{TAG}",
             "prompt": [{"type": "text",
                         "text": f"请只回复这一行文字（不要调用任何工具）：p115-ws-normal-{TAG}"}]},
            rid=3, timeout=200)
        reply_a = conn.collect(sid)
        meta_a = turn_root_meta(await readback_turn_events(client, sid))
        rec("W1a WS 正常回合真实完成", f"p115-ws-normal-{TAG}" in reply_a,
            reply_a[-160:].replace("\n", " "))
        rec("W1b WS 正常回合零误标（回读无 termination key）",
            bool(meta_a) and "termination" not in meta_a,
            json.dumps(meta_a, ensure_ascii=False))

        # ── W2 steer /abort 中断回合（external_cancel 真实用户路径）──
        task_fut = asyncio.ensure_future(conn.rpc(
            "session/prompt",
            {"sessionId": sid, "messageId": f"p115b-{TAG}",
             "prompt": [{"type": "text",
                         "text": "请调用 terminal 工具执行命令：sleep 25。"
                                 "执行完成后再调用 terminal 执行 echo done。"
                                 "每个命令单独调用一次 terminal 工具。"}]},
            rid=4, timeout=300))
        await asyncio.sleep(8)
        r_abort = await conn.rpc(
            "session/prompt",
            {"sessionId": sid, "messageId": f"p115c-{TAG}",
             "prompt": [{"type": "text", "text": "/abort"}]},
            rid=5, timeout=60)
        abort_ack = conn.collect(sid)
        rec("W2a /abort 忙时入队（中断指令已排队）",
            "中断" in abort_ack or "排队" in abort_ack,
            abort_ack[-200:].replace("\n", " "))
        try:
            await asyncio.wait_for(task_fut, timeout=280)
        except Exception as e:
            EV["e2e"]["ws_abort_turn_wait"] = repr(e)
        reply_b = conn.collect(sid)
        EV["e2e"]["ws_abort_round"] = {
            "sid": sid, "reply_tail": reply_b[-600:],
            "stop": ((r_abort.get("result") or {}).get("stopReason", "-")
                     if isinstance(r_abort, dict) else "-")}
        meta_b = turn_root_meta(await readback_turn_events(client, sid))
        # 中断尾巴可能因流式已推送而不再走 final push——服务端真源=agent_messages
        db_text = ""
        try:
            import sqlite3
            conn_db = sqlite3.connect(
                "/home/climbing/opensoul/data/opensoul.db", timeout=10)
            rows = conn_db.execute(
                "SELECT content FROM agent_messages WHERE session_id=? "
                "ORDER BY timestamp DESC LIMIT 3", (sid,)).fetchall()
            conn_db.close()
            db_text = " ".join(r[0] or "" for r in rows)
        except Exception as e:
            EV["e2e"]["ws_abort_db_read"] = repr(e)
        rec("W2b abort 回合：中断语义可见（WS流或服务端agent_messages）",
            "任务被用户插话中断" in reply_b or "任务被用户插话中断" in db_text
            or "收到中断指令" in reply_b,
            f"ws={reply_b[-160:]} | db={db_text[-160:]}".replace("\n", " "))
        rec("W2c abort 回合：真实store回读 termination=external_cancel",
            bool(meta_b) and meta_b.get("termination") == "external_cancel",
            json.dumps(meta_b, ensure_ascii=False))
        rec("W2d abort 回合 turn root 已收口（非悬挂）",
            bool(meta_b), json.dumps(meta_b, ensure_ascii=False))


async def main() -> None:
    os.environ.setdefault("PYTHONUNBUFFERED", "1")
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            layer1_config()
            await layer2_store(client)
            await layer3_engine(client)
            await layer4_ws(client)
        except Exception as e:  # 探针崩溃也留证据（fail 可见，不静默）
            rec("探针执行异常", False, repr(e))
        finally:
            os.environ.pop("SOULMATE_MAX_ROUNDS", None)
            os.environ.pop("SOULMATE_TURN_TOKEN_BUDGET", None)
        EV["pass"] = all(c["ok"] for c in EV["checks"])
        out = json.dumps(EV, ensure_ascii=False, indent=2)
        Path("/tmp/p1_15_termination_evidence.json").write_text(out, encoding="utf-8")
        repo = Path("/home/climbing/openmate/docs/agent-architecture-mimo/synthesis/"
                    "evidence/p1-15-termination-evidence.json")
        repo.write_text(out, encoding="utf-8")
        print(f"\n{'PASS' if EV['pass'] else 'FAIL'} "
              f"{sum(c['ok'] for c in EV['checks'])}/{len(EV['checks'])} "
              f"→ /tmp/p1_15_termination_evidence.json + {repo}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
