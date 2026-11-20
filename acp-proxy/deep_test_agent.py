#!/usr/bin/env python3
"""deep_test_agent.py — 单 agent 逐项功能深测探针（内容级证据，禁假成功）。

测试铁律落地：
- 内容级探针（42 / 暗号 / nonce token / 磁盘文件），不以"有回复"当通过
- 每 agent 独立会话，并发 canary 互查禁冒名兜底
- 杀后端再发消息看降级表现（优雅报错或自愈续答=过；挂死/假成功=败）
- 每测完一项立即写盘 JSON（中断也不丢已得结果）

用法:
    python3 deep_test_agent.py <agent_id> [--tests basic,multiturn,tool,file,skill,interrupt,concurrency,degrade]
输出:
    ~/openmate/docs/reports/evidence/deep-test/deep-<agent>-<ts>.json
退出码: 0=全过(允许 n/a)  1=存在 fail/partial
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import signal
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import websockets

HOME = Path.home()
SOUL = "http://127.0.0.1:8090"
ACP_WS = "ws://127.0.0.1:8092/ws/acp"
PROXY_PID_CANDIDATES = []  # 运行时探测 ws_acp 主进程
EVID_DIR = HOME / "openmate" / "docs" / "reports" / "evidence" / "deep-test"
SCRATCH = HOME / ".hermes" / "cache" / "scratch" / "deep-test"
LOGIN_USER = os.environ.get("DEEP_USER", "e2e_spot_1120")
LOGIN_PASS = os.environ.get("DEEP_PASS", "SpotVerify2026")

TURN_TIMEOUT = float(os.environ.get("DEEP_TURN_TIMEOUT", "300"))
SLOW_TURN_TIMEOUT = float(os.environ.get("DEEP_SLOW_TURN_TIMEOUT", "420"))

# 各 agent 的子进程 cmdline 特征（用于 degrade 杀后端）
PROC_MARKER = {
    "soulmate": "agent.start",
    "hermes": "hermes acp",
    "mimo": "mimo acp",
    "opencode": "opencode acp",
    "ollama": "agent.ollama_acp",
    "deepseek": "agent.ollama_acp",
    "aider": "agent.cli_acp aider",
    "qwen": "agent.cli_acp qwen",
    "interpreter": "agent.cli_acp interpreter",
    "copilot": "agent.cli_acp copilot",
    "codex": "codex-acp",
    "pi": "pi-acp",
    "claude-code": "claude-agent-acp",
    "openclaw": "openclaw acp",
}

# 技能测试模式（其余 agent 无技能系统 → n/a）
SKILL_MODE = {
    "hermes": "hermes-skillview",
    "claude-code": "nonce-dir",
    "openclaw": "nonce-dir",
    "soulmate": "soulmate-trigger",
    "opencode": "nonce-dir",
    "pi": "nonce-dir",
}
SKILL_NONCE_DIR = {
    "claude-code": HOME / ".claude" / "skills",
    "openclaw": HOME / ".openclaw" / "skills",
    "opencode": HOME / ".config" / "opencode" / "skills",
    "pi": HOME / ".pi" / "agent" / "skills",
}

results: list[dict] = []
OUT_PATH: Path | None = None


def flush() -> None:
    if OUT_PATH:
        OUT_PATH.write_text(json.dumps({
            "tests": results, "finished": False,
        }, ensure_ascii=False, indent=2), encoding="utf-8")


def record(name: str, status: str, detail: str, evidence: dict | None = None) -> None:
    results.append({"name": name, "status": status, "detail": detail,
                    "evidence": evidence or {}})
    print(f"[{status.upper():7s}] {name}: {detail}", flush=True)
    flush()


def login() -> str:
    last = None
    for attempt in range(5):
        try:
            body = urllib.parse.urlencode({"username": LOGIN_USER, "password": LOGIN_PASS}).encode()
            req = urllib.request.Request(f"{SOUL}/api/user/login", data=body, method="POST")
            with urllib.request.urlopen(req, timeout=90) as r:
                return json.loads(r.read())["access_token"]
        except Exception as e:  # noqa: BLE001 — 高负载下 :8090 会超时/断连，重试
            last = e
            print(f"[login] attempt {attempt+1} failed: {type(e).__name__}; retrying", flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"login failed after retries: {last!r}")


def find_proxy_pid() -> int | None:
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            cmd = open(p, "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "main.py" in cmd and "--port 8092" in cmd:
            return int(p.split("/")[2])
    return None


def descendants(root: int) -> set[int]:
    """root 的全部后代 pid（含直接子进程）。"""
    ppid_map: dict[int, int] = {}
    for p in glob.glob("/proc/[0-9]*/stat"):
        try:
            parts = open(p).read().rsplit(")", 1)[-1].split()
            pid, ppid = int(p.split("/")[2]), int(parts[1])
            ppid_map[pid] = ppid
        except (OSError, ValueError, IndexError):
            continue
    out: set[int] = set()
    changed = True
    frontier = {root}
    while changed:
        changed = False
        for pid, ppid in ppid_map.items():
            if ppid in frontier and pid not in out and pid != root:
                out.add(pid)
                changed = True
        frontier = out
    return out


def cmdline_of(pid: int) -> str:
    try:
        return open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        return ""


def find_agent_procs(agent: str, proxy_pid: int | None, extra_filter: set[int] | None = None) -> list[int]:
    marker = PROC_MARKER.get(agent, agent)
    if proxy_pid:
        cands = descendants(proxy_pid)
    else:
        cands = {int(p.split("/")[2]) for p in glob.glob("/proc/[0-9]*")}
    out = [pid for pid in cands if marker in cmdline_of(pid)]
    if extra_filter:
        out = [pid for pid in out if pid in extra_filter]
    return sorted(out)


class Session:
    """一条 WS /ws/acp 会话（initialize + session/new + session/prompt 透传）。"""

    def __init__(self, token: str, agent: str, tag: str = "main"):
        self.token = token
        self.agent = agent
        self.tag = tag
        self.ws = None
        self.sid = ""
        self.rid = 100
        self.updates: list[dict] = []
        self.all_msgs: list[dict] = []

    async def open(self) -> str:
        uri = f"{ACP_WS}?token={self.token}"
        self.ws = await websockets.connect(uri, max_size=20_000_000,
                                           ping_interval=20, ping_timeout=60,
                                           open_timeout=30)
        assert self.ws is not None
        await self.rpc_raw({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {"protocolVersion": 1}}, timeout=60)
        self.rid = 100
        r = await self.rpc_raw({"jsonrpc": "2.0", "id": 2, "method": "session/new",
                                "params": {"cwd": str(HOME), "mcpServers": [],
                                           "agent_id": self.agent}}, timeout=120)
        res = (r.get("result") or {})
        self.sid = res.get("sessionId") or res.get("session_id") or ""
        self.rid = 10
        return self.sid

    async def rpc_raw(self, msg: dict, timeout: float) -> dict:
        """发请求并等到同 id 应答（期间的 session/update 记入 self.updates）。"""
        await self.ws.send(json.dumps(msg, ensure_ascii=False))
        t0 = time.time()
        while time.time() - t0 < timeout:
            raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self.all_msgs.append(m)
            if m.get("method") == "session/update":
                self.updates.append(m)
            if m.get("id") == msg.get("id"):
                return m
        raise TimeoutError(f"rpc {msg.get('method')} 无应答 ({timeout}s)")

    async def prompt(self, text: str, timeout: float = TURN_TIMEOUT,
                     cancel_after_first_chunk: float | None = None) -> dict:
        """发一轮 prompt，返回 {reply, chunks, first_token_s, stop, err, canceled}。"""
        self.rid += 1
        rid = self.rid
        msg = {"jsonrpc": "2.0", "id": rid, "method": "session/prompt",
               "params": {"sessionId": self.sid, "messageId": f"deep-{rid}",
                          "prompt": [{"type": "text", "text": text}]}}
        before = len(self.updates)
        t_send = time.time()
        await self.ws.send(json.dumps(msg, ensure_ascii=False))
        chunks: list[str] = []
        first_token_s = None
        cancel_sent_at = None
        canceled = None
        final = None
        seen_updates: list[str] = []
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            except asyncio.TimeoutError as e:
                raise TimeoutError(
                    f"recv静默超时({timeout}s); 已收updates={seen_updates[-8:]}") from e
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                continue
            self.all_msgs.append(m)
            if m.get("method") == "session/update":
                self.updates.append(m)
                u = m.get("params", {}).get("update", {})
                seen_updates.append(str(u.get("sessionUpdate") or u.get("reason") or "?"))
                content = u.get("content") or {}
                if u.get("sessionUpdate") == "agent_message_chunk" and content.get("type") == "text":
                    if first_token_s is None:
                        first_token_s = time.time() - t_send
                    chunks.append(content.get("text") or "")
                if (cancel_after_first_chunk is not None and cancel_sent_at is None
                        and first_token_s is not None):
                    # 首 token 后发中断
                    cancel_sent_at = time.time()
                    self.rid += 1
                    cancel_id = self.rid
                    cmsg = {"jsonrpc": "2.0", "id": cancel_id, "method": "session/cancel",
                            "params": {"sessionId": self.sid}}
                    try:
                        await self.ws.send(json.dumps(cmsg))
                    except Exception:
                        pass
                    canceled = {"sent_after_s": time.time() - t_send, "resp_id": cancel_id}
                continue
            if m.get("method") == "session/request_permission" and m.get("id") is not None:
                # 复刻 UI 审批按钮的"批准"应答（chat-client P0-3 口径：allow 选项 → selected）
                pparams = m.get("params") or {}
                allow = next((o for o in (pparams.get("options") or [])
                              if str(o.get("kind", "")).startswith("allow")), None)
                allow_id = (allow or {}).get("optionId") or "allow_once"
                try:
                    await self.ws.send(json.dumps({
                        "jsonrpc": "2.0", "id": m["id"],
                        "result": {"outcome": {"outcome": "selected", "optionId": allow_id}}}))
                except Exception:
                    pass
                seen_updates.append(
                    f"auto-approved:{str((pparams.get('toolCall') or {}).get('title', ''))[:30]}")
                continue
            if m.get("method") and m.get("method") != "session/update":
                # 服务端主动请求（如权限审批）也留证
                seen_updates.append(str(m.get("method")))
            if m.get("id") == rid:
                final = m
                break
            if canceled and m.get("id") == canceled.get("resp_id"):
                canceled["resp"] = {k: v for k, v in m.items() if k in ("result", "error")}
        # 收尾：cancel 应答/慢首token可能晚到，短暂 drain
        try:
            while True:
                raw = await asyncio.wait_for(self.ws.recv(), timeout=5.0)
                m = json.loads(raw)
                self.all_msgs.append(m)
                if m.get("method") == "session/update":
                    self.updates.append(m)
                    u = m.get("params", {}).get("update", {})
                    c = u.get("content") or {}
                    if u.get("sessionUpdate") == "agent_message_chunk" and c.get("type") == "text":
                        chunks.append(c.get("text") or "")
                elif m.get("id") == rid:
                    final = m
        except (asyncio.TimeoutError, websockets.exceptions.ConnectionClosed):
            pass
        new_updates = self.updates[before:]
        tool_events = [u.get("params", {}).get("update", {}).get("sessionUpdate")
                       for u in new_updates
                       if u.get("params", {}).get("update", {}).get("sessionUpdate")
                       not in ("agent_message_chunk", None)]
        reply = "".join(chunks)
        err = None
        stop = None
        if final is not None:
            if "error" in final:
                err = final["error"]
            else:
                stop = (final.get("result") or {}).get("stopReason")
        return {"reply": reply, "chunks": len(chunks), "first_token_s": first_token_s,
                "stop": stop, "err": err, "tool_events": sorted(set(tool_events)),
                "canceled": canceled, "final": final,
                "seen_updates": seen_updates[-10:]}

    async def close(self) -> None:
        try:
            await self.ws.close()
        except Exception:
            pass


# ─────────────────────────────── 测试项 ───────────────────────────────

async def test_basic(sess: Session) -> None:
    r = await sess.prompt("请只回答数字：26+16等于多少？")
    ok = "42" in (r["reply"] or "") and not r["err"]
    record("basic 基础对话", "pass" if ok else "fail",
           f"reply含42={('42' in (r['reply'] or ''))}, chunks={r['chunks']}, "
           f"first_token={r['first_token_s']}, stop={r['stop']}, err={r['err']}",
           {"reply_head": (r["reply"] or "")[:120]})
    # 流式：用长回复探针区分真流式 vs 单块返回（短回复 1 chunk 不能定罪）
    rs = await sess.prompt("请从1数到12，用、分隔所有数字，只输出数字和分隔符，不要其他任何字")
    streaming = rs["chunks"] >= 3
    record("stream 流式回复",
           "pass" if streaming else ("partial" if rs["chunks"] == 2 else "fail"),
           f"长回复轮 agent_message_chunk 数={rs['chunks']}, 首token延迟={rs['first_token_s']}",
           {"chunks": rs["chunks"], "reply_head": (rs["reply"] or "")[:80]})


async def test_multiturn(sess: Session) -> None:
    r1 = await sess.prompt("请记住这个暗号：榛子77。只回复两个字：已记")
    r2 = await sess.prompt("刚才的暗号是什么？只回复暗号本身，不要其他字")
    ok = "榛子77" in (r2["reply"] or "")
    record("multiturn 多轮上下文", "pass" if ok else "fail",
           f"第2轮回复含暗号'榛子77'={ok}；第1轮={ (r1['reply'] or '')[:40]!r}，"
           f"第2轮={ (r2['reply'] or '')[:80]!r}")


async def test_tool_file(sess: Session, agent: str) -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    target = SCRATCH / f"{agent}-tool.txt"
    alt = HOME / "acp-cli-workspace" / f"{agent}-tool.txt"
    for p in (target, alt):
        if p.exists():
            p.unlink()
    r = await sess.prompt(
        f"请创建一个文件，绝对路径：{target}，文件内容只写一行：TOOL-OK-{agent}。"
        f"必须用工具实际写文件，做完后只回复两个字：完成")
    found = []
    for p in (target, alt):
        if p.exists():
            found.append((str(p), p.read_text(errors="replace")[:80]))
    content_ok = any(f"TOOL-OK-{agent}" in c for _, c in found)
    tool_ev = r["tool_events"]
    if content_ok:
        status = "pass"
    elif tool_ev:
        status = "partial"
    else:
        status = "fail"
    record("tool+file 工具调用/文件操作", status,
           f"磁盘文件落盘={bool(found)} 内容匹配={content_ok} 工具事件={tool_ev} err={r['err']}",
           {"files": found, "reply_head": (r["reply"] or "")[:100]})


async def test_file_read(sess: Session, agent: str) -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    nonce = f"READ-{agent}-{int(time.time())}"
    f = SCRATCH / f"{agent}-read.txt"
    f.write_text(nonce + "\n", encoding="utf-8")
    r = await sess.prompt(f"请读取文件 {f} 并原样输出其全部内容（一字不改）")
    ok = nonce in (r["reply"] or "")
    record("file-read 文件读取", "pass" if ok else "fail",
           f"回复含文件内 nonce={ok} err={r['err']}",
           {"nonce": nonce, "reply_head": (r["reply"] or "")[:120]})


def _write_nonce_skill(agent: str, nonce: str) -> Path:
    base = SKILL_NONCE_DIR[agent]
    d = base / "deep-test-probe"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: deep-test-probe\ndescription: Use when asked to run the deep test probe. "
        "Replies with the probe token.\n---\n\n# Deep Test Probe\n\n"
        f"When this skill is invoked, reply with exactly this token and nothing else: {nonce}\n",
        encoding="utf-8")
    return d


async def test_skill(sess: Session, agent: str) -> None:
    mode = SKILL_MODE.get(agent)
    if not mode:
        record("skill 技能加载", "n/a", "该 agent 无技能系统（CLI 一次性模式/纯模型路由）")
        return
    if mode == "hermes-skillview":
        cands = list((HOME / ".hermes" / "skills").glob("**/hermes-agent/SKILL.md"))
        sk = cands[0] if cands else HOME / ".hermes" / "skills" / "hermes-agent" / "SKILL.md"
        text = sk.read_text(encoding="utf-8") if sk.exists() else ""
        needle = ""
        for line in text.splitlines():
            if line.startswith("description:"):
                needle = line.split("description:", 1)[1].strip().strip('"').strip()[:24]
                break
        if not needle:
            record("skill 技能加载", "partial", f"技能文件缺失或无 description: {sk}")
            return
        r = await sess.prompt(
            "请调用 skill_view 工具加载名为 hermes-agent 的技能，然后原样输出其 "
            "frontmatter 中 description 字段冒号后的完整原文（不许改动任何字符）")
        ok = needle in (r["reply"] or "")
        record("skill 技能加载", "pass" if ok else "fail",
               f"回复含 description 原文片段 {needle!r}={ok} err={r['err']}",
               {"reply_head": (r["reply"] or "")[:150]})
    elif mode == "nonce-dir":
        nonce = f"DEEPPROBE-{agent}-{int(time.time())}"
        _write_nonce_skill(agent, nonce)
        r = await sess.prompt(
            "请使用 deep-test-probe 这个技能（skill）来完成当前任务，按该技能的指示回复。")
        ok = nonce in (r["reply"] or "")
        record("skill 技能加载", "pass" if ok else "fail",
               f"回复含技能内 nonce={ok} err={r['err']}",
               {"nonce": nonce, "reply_head": (r["reply"] or "")[:150]})
    elif mode == "soulmate-trigger":
        receipt = HOME / "openmate" / "acp-proxy" / "data" / "skill_load_receipts.jsonl"
        n_before = sum(1 for _ in open(receipt, encoding="utf-8")) if receipt.exists() else 0
        t0 = time.time()
        r = await sess.prompt("帮我列出所有项目：编号、名称、所属部门、状态、预算、已花费、开始日期")
        hits = []
        if receipt.exists():
            for line in open(receipt, encoding="utf-8"):
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("ts", "") >= time.strftime("%Y-%m-%dT%H:%M", time.gmtime(t0)):
                    for s in row.get("skills", []):
                        if s.get("state") != "none_loaded":
                            hits.append(s.get("name"))
        n_after = sum(1 for _ in open(receipt, encoding="utf-8")) if receipt.exists() else 0
        ok = bool(hits)
        record("skill 技能加载", "pass" if ok else ("partial" if n_after > n_before else "fail"),
               f"技能收据新增={n_after - n_before} 行，命中技能={hits} err={r['err']}",
               {"receipt_delta": n_after - n_before, "hits": hits,
                "reply_head": (r["reply"] or "")[:120]})


async def test_interrupt(sess: Session, agent: str) -> None:
    r = await sess.prompt("请写一篇200字的散文《春天》，要写满200字",
                          timeout=SLOW_TURN_TIMEOUT, cancel_after_first_chunk=1.0)
    cancel_info = r["canceled"]
    stop_norm = (r["stop"] or "").lower().replace("_", "")
    stopped_early = (stop_norm != "" and stop_norm != "endturn") or len(r["reply"] or "") < 120
    r2 = await sess.prompt("只回复四个字：恢复可用", timeout=SLOW_TURN_TIMEOUT)
    recover_ok = "恢复" in (r2["reply"] or "") or "可用" in (r2["reply"] or "")
    if r["canceled"] and stopped_early and recover_ok:
        status = "pass"
    elif recover_ok:
        status = "partial"
    else:
        status = "fail"
    record("interrupt 中断恢复", status,
           f"cancel已发={bool(cancel_info)} 提前停止={stopped_early} "
           f"(stop={r['stop']}, 回复字数={len(r['reply'] or '')}) 恢复轮={ (r2['reply'] or '')[:30]!r}",
           {"cancel": cancel_info})


async def test_concurrency(token: str, agent: str) -> None:
    a, b = Session(token, agent, "conc-a"), Session(token, agent, "conc-b")
    canary_a, canary_b = f"APPLEC-{agent}", f"BANANAC-{agent}"
    try:
        await a.open()
        await b.open()
        ra, rb = await asyncio.gather(
            a.prompt(f"请只回复这六个字：{canary_a}"),
            b.prompt(f"请只回复这六个字：{canary_b}"),
        )
        ta, tb = ra["reply"] or "", rb["reply"] or ""
        ok = (canary_a in ta and canary_b not in ta and
              canary_b in tb and canary_a not in tb)
        record("concurrency 并发会话不串答", "pass" if ok else "fail",
               f"A回复含A-canary={canary_a in ta} 含B-canary={canary_b in ta} | "
               f"B回复含B-canary={canary_b in tb} 含A-canary={canary_a in tb}",
               {"reply_a": ta[:100], "reply_b": tb[:100]})
    except Exception as e:
        record("concurrency 并发会话不串答", "fail", f"异常: {e!r}")
    finally:
        await a.close()
        await b.close()


async def test_degrade(sess: Session, agent: str, proxy_pid: int | None,
                       session_pids: set[int]) -> None:
    alive = set(find_agent_procs(agent, proxy_pid))
    pids = sorted(alive & session_pids)
    if not pids:
        record("degrade 错误降级", "partial",
               "找不到本会话 spawn 的 agent 子进程（不杀共享/他人进程），跳过杀后端"
               f"（session_pids={sorted(session_pids)}, alive={sorted(alive)}）")
        return
    killed = []
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except OSError:
            pass
    t_kill = time.time()
    try:
        r = await sess.prompt(f"请只回复这四个字：降级续答", timeout=240)
    except websockets.exceptions.ConnectionClosed as e:
        record("degrade 错误降级", "partial",
               f"杀掉子进程={killed} → WS被关闭({e.code}:{str(e.reason)[:40]})，"
               f"连接级降级（非挂死非假成功，但客户端须重连）")
        return
    except Exception as e:
        record("degrade 错误降级", "fail", f"杀掉子进程={killed} → {type(e).__name__}: {e}")
        return
    respawn = find_agent_procs(agent, proxy_pid)
    respawn = [p for p in respawn if p not in killed]
    replied = bool((r["reply"] or "").strip()) and not r["err"]
    graceful_err = bool(r["err"])
    if replied or graceful_err:
        status = "pass"
    else:
        status = "fail"
    record("degrade 错误降级", status,
           f"杀掉子进程={killed} → {'自愈续答' if replied else ('优雅报错:' + str(r['err'])[:80] if graceful_err else '无响应/挂死')}，"
           f"respawn新进程={respawn}，耗时={time.time()-t_kill:.1f}s",
           {"killed": killed, "respawn": respawn, "reply_head": (r["reply"] or "")[:80]})


# ─────────────────────────────── 主流程 ───────────────────────────────

ALL_TESTS = ["basic", "multiturn", "tool", "file", "skill", "interrupt", "concurrency", "degrade"]


async def run(agent: str, only: set[str]) -> int:
    global OUT_PATH
    EVID_DIR.mkdir(parents=True, exist_ok=True)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    OUT_PATH = EVID_DIR / f"deep-{agent}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    proxy_pid = find_proxy_pid()
    before_open = set(find_agent_procs(agent, proxy_pid))

    token = login()
    sess = Session(token, agent)
    try:
        sid = await sess.open()
        # 本会话 spawn 的子进程（session/new 窗口内新增）；degrade 只杀这些
        session_pids = set(find_agent_procs(agent, proxy_pid)) - before_open
        if not sid:
            record("session/new", "fail", "未取得 sessionId（agent 起不来）")
            return 1
        record("session/new", "pass", f"sessionId={sid}", {"route": agent})

        async def guarded(label: str, coro):
            try:
                await coro
            except (websockets.exceptions.ConnectionClosed, TimeoutError, asyncio.TimeoutError) as e:
                record(label, "fail", f"{type(e).__name__}: {str(e)[:120]}（超时/断连）")
            except Exception as e:  # noqa: BLE001 — 单测失败不拖垮其余测试
                record(label, "fail", f"{type(e).__name__}: {str(e)[:120]}")

        if "basic" in only:
            await guarded("basic 基础对话", test_basic(sess))
        if "multiturn" in only:
            await guarded("multiturn 多轮上下文", test_multiturn(sess))
        if "tool" in only:
            await guarded("tool+file 工具调用/文件操作", test_tool_file(sess, agent))
        if "file" in only:
            await guarded("file-read 文件读取", test_file_read(sess, agent))
        if "skill" in only:
            await guarded("skill 技能加载", test_skill(sess, agent))
        if "interrupt" in only:
            await guarded("interrupt 中断恢复", test_interrupt(sess, agent))
        if "concurrency" in only:
            await guarded("concurrency 并发会话不串答", test_concurrency(token, agent))
        if "degrade" in only:
            await guarded("degrade 错误降级", test_degrade(sess, agent, proxy_pid, session_pids))
    except Exception as e:
        record("harness 异常", "fail", f"{type(e).__name__}: {e}")
    finally:
        await sess.close()

    bad = [t for t in results if t["status"] in ("fail", "partial")]
    if OUT_PATH:
        OUT_PATH.write_text(json.dumps({
            "agent": agent, "tests": results,
            "finished": True, "bad": len(bad),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n== {agent}: {len(results)-len(bad)}/{len(results)} 过，"
              f"证据 {OUT_PATH} ==")
    return 1 if bad else 0


def main() -> int:
    agent = sys.argv[1] if len(sys.argv) > 1 else ""
    if agent not in PROC_MARKER:
        print(f"用法: python3 deep_test_agent.py <agent_id>，可选值: {list(PROC_MARKER)}")
        return 2
    only = set(ALL_TESTS)
    if len(sys.argv) > 3 and sys.argv[2] == "--tests":
        only = set(x.strip() for x in sys.argv[3].split(",") if x.strip())
    return asyncio.run(run(agent, only))


if __name__ == "__main__":
    sys.exit(main())
