"""SoulMate 验收总标准对标实测 — 10 类任务（开发/调试/运维/调研/发布/办公/定时/多渠道/记忆/协作）

真实链路（v2 修正）：WS /ws/acp?token=JWT → ws_acp AGENT_ROUTES["soulmate"]
→ spawn `python -m agent.start --stdio` → SoulMateAgent 真实 LLM 回合 + 真实工具执行。
⚠️ v1 走 HTTP /acp/send 是错误路径——proxy.py 硬编码 hermes acp 路由（L97/L141），
测到的是 Hermes 不是 SoulMate（10回合全打偏，见周期#30日志）。

验证纪律（P0"写了≠接线了"）：
- 服务端副作用优先（文件内容/SQLite 行），回复文本只作辅证；
- 回复文本无法独立验证的类别显式标注 verify_mode=reply_assisted（诚实边界）；
- 路线图12.2：运维/调研判据=工具账本真源（复用 trajectory_events 既有落账，
  零新增落库负担）——运维=体检结果落账、调研=检索调用账本+来源行；账本判据
  不可用时诚实降级回复辅证（verify_mode 不假称 server_side）；
- 每类任务独立 session（同一 stdio 子进程，上下文互不污染）；
- 测试产物显式清理（MEMORY.md 标记行、accept_probe 定时任务行、/tmp 工作目录）。

结果写 /tmp/soulmate_accept_results.json，stdout 打印摘要表。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sqlite3
import time
import zipfile

import jwt as pyjwt
import websockets

from agent.frame_origin import split_frames

ACP_WS = "ws://127.0.0.1:8092/ws/acp"
JWT_SECRET = "change-me-in-production"
WORKDIR = "/tmp/soulmate_accept"
RESULTS_PATH = "/tmp/soulmate_accept_results.json"
MEM_PATH = os.path.expanduser("~/.hermes/soulmate/memory/MEMORY.md")
CRON_DB = os.path.expanduser("~/.hermes/soulmate/memory/cron_tasks.db")
SUB_DB = os.path.expanduser("~/.hermes/soulmate/memory/subagent_runs.db")
# 路线图12.1：多渠道出站独立可查痕迹（agent/outbound_audit.py 单一写入源）
OUTBOUND_DB = (os.environ.get("SOULMATE_OUTBOUND_AUDIT_DB")
               or os.path.expanduser("~/.hermes/soulmate/memory/outbound_audit.db"))
MULTICHANNEL_MARKER = "验收多渠道探针"
# 周期#54：上游降级期实证回合 >300s 仍会服务端跑完（定时类 WS 300s 超时但
# accept_probe 行后到落库），默认 300s 保留，降级期可 ACCEPT_PROMPT_TIMEOUT_S 拉长。
PROMPT_TIMEOUT_S = int(os.environ.get("ACCEPT_PROMPT_TIMEOUT_S", "300"))
# 10.4（周期#58）：WS 超时≠失败——服务端回合照跑（观察窗纪律），产出后到落库。
# 超时后在预算内对服务端真源二次回读（落库回复+服务端副作用验证器），预算耗尽
# 才如实记失败；期间持续排水 WS 帧（防读缓冲顶满反压 agent stdout 把回合拖死）。
LATE_READBACK_BUDGET_S = float(os.environ.get("ACCEPT_LATE_READBACK_S", "300"))
LATE_READBACK_POLL_S = float(os.environ.get("ACCEPT_LATE_READBACK_POLL_S", "5"))
AGENT_MESSAGES_DB = os.path.expanduser("~/opensoul/data/opensoul.db")

MEM_MARKER = "ACCEPT_MEM_MARK_9"

# ── 8.5 验收复测支持（周期#40）────────────────────────────────────
# ACCEPT_CATEGORIES="定时,多渠道" 只跑指定类别（"每完成一个⚡项即重跑对应类别"）；
# 每次运行注入唯一 RUN_TAG 防缓存标记——semantic-cache EXACT 命中会回放旧响应
# （周期#31 实证假跑 0.98s 级），唯一标记让缓存永不命中。
RETEST_CATEGORIES = {c.strip() for c in os.environ.get("ACCEPT_CATEGORIES", "").split(",") if c.strip()}
RUN_TAG = os.environ.get("ACCEPT_RUN_TAG") or f"RETEST_{int(time.time())}"


def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _query(db_path: str, sql: str, args: tuple = ()) -> list[tuple]:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            return conn.execute(sql, args).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []


# ── 路线图12.2：工具账本真源回读（运维/调研 verify 升级 server_side）────────
# 真源=opensoul.db trajectory_events（trajectory_recorder 在真实回合写入的
# tool_call 轨迹账本）——复用既有落账，零新增落库负担（roadmap 12.2 约束）。
# verify() 单参签名不变，会话上下文经 _VERIFY_CTX 注入（session/new 后设置，
# 会话是新建的→账本行只可能来自本 run，天然免疫陈旧行假阳性）。
TOOL_LEDGER_DB = AGENT_MESSAGES_DB
_VERIFY_CTX: dict = {"session_id": "", "mode_note": ""}
# 轨迹落账是 fire-safe 异步（回合收口后可能尚在途）：缺行时有界轮询防竞态假阴性。
_LEDGER_WAIT_S = float(os.environ.get("ACCEPT_LEDGER_WAIT_S", "4"))
_LEDGER_POLL_S = 1.0


def _tool_ledger(tool_name: str | tuple[str, ...]) -> tuple[list, str]:
    """会话级工具账本回读。返回 (executed_hits, state)。

    tool_name 可为单名或多名字面量（12.6-②：dev 运行判据要认 terminal 与
    execute_code 两条真实执行路径，一次回读不双倍轮询）；名字匹配容忍
    "terminal: <cmd>" 冒号前缀形态（CLI 轨迹行实证形态）。

    - executed_hits：该工具的真实执行条目（report_evidence.parse_ledger 单一
      真源判执行/拦截 + 行 status=ok），条目含 result_preview（体检结果落账/
      检索来源行就在里面）。
    - state：ok=有执行行；missing=账本有行但无该工具执行（工具真没调/被拦）；
      unavailable=该会话账本整体无任何行或无会话上下文（记账链路未写入→
      判据诚实降级回复辅证，不假失败也绝不假称 server_side）。
    """
    from agent.report_evidence import parse_ledger

    names = (tool_name,) if isinstance(tool_name, str) else tuple(tool_name)

    sid = str(_VERIFY_CTX.get("session_id") or "")
    poll = max(0.0, _LEDGER_WAIT_S) if sid else 0.0  # 无会话上下文=离线调用，不空等
    deadline = time.monotonic() + poll
    while True:
        entries: list[dict] = []
        any_rows = 0
        if sid:
            any_rows = (_query(TOOL_LEDGER_DB,
                               "SELECT COUNT(*) FROM trajectory_events WHERE session_id = ?",
                               (sid,)) or [(0,)])[0][0]
            for content, meta_json, status in _query(
                TOOL_LEDGER_DB,
                "SELECT content, metadata_json, status FROM trajectory_events "
                "WHERE session_id = ? AND event_type = 'tool_call' ORDER BY id",
                (sid,)):
                if (status or "") != "ok":
                    continue  # 失败/拦截行不计执行证据
                try:
                    meta = json.loads(meta_json or "{}")
                except ValueError:
                    meta = {}
                entries.append({
                    "name": str(meta.get("name") or content or ""),
                    "arguments": str(meta.get("arguments", "")),
                    "result_preview": str(meta.get("result_preview", "")),
                    "permission": str(meta.get("permission", "")),
                })
        executed, _blocked = parse_ledger(entries)

        def _match(n: str) -> bool:
            n = (n or "").strip()
            return n in names or n.split(":", 1)[0].strip() in names

        hits = [e for e in executed if _match(e.name)]
        if hits or time.monotonic() >= deadline:
            if hits:
                return hits, "ok"
            return [], ("unavailable" if (not sid or any_rows == 0) else "missing")
        time.sleep(_LEDGER_POLL_S)


# ── 统一快照读取器（周期#59）────────────────────────────────────────
def _looks_timeout(text: str) -> bool:
    """超时特征判定（超时码/超时措辞），供快照状态标定。"""
    t = text or ""
    return bool(re.search(r"(?i)(\btimeout\b|超时|timed?\s*out|WS_TIMEOUT|超时码)", t))


def _read_result_snapshot(request_id: str, db_path: str = "") -> dict:
    """单一快照读取器：一次一致性读，多处比对。

    消除超时回读的竞态窗口——二次回读禁止使用不同读取路径/不同缓存，
    统一从 agent_messages 真源做一次快照读取；返回值携带 request_id/状态码/
    时间戳供日志与判定使用，不引入任何全局可变状态。

    状态码语义：ok=有正常产出；timeout=快照内含超时态（超时≠成功）；
    empty=尚无落库产出。
    """
    snap_ts = time.time()
    rows = _query(db_path or AGENT_MESSAGES_DB,
                  "SELECT content, role FROM agent_messages "
                  "WHERE session_id = ? ORDER BY id ASC",
                  (request_id,))
    contents = [r[0] or "" for r in rows if (r[1] or "assistant") == "assistant"]
    if not contents:
        status = "empty"
    elif any(_looks_timeout(c) for c in contents):
        status = "timeout"
    else:
        status = "ok"
    return {
        "request_id": request_id,
        "status": status,
        "timestamp": snap_ts,
        "reply": "\n\n".join(contents),  # 行间双换行（_read_back_reply 既有契约）
        "rows": rows,
    }


# ── 10.4 WS 超时的服务端后到产出二次回读（周期#58）──────────────────


def _read_back_reply(session_id: str, db_path: str = "") -> str:
    """服务端真源回复回读：统一走同一快照读取器，一次读取多次比对。

    WS 超时时回答帧已丢失，但服务端回合照跑并落库（观察窗纪律：页面/连接只是
    观察窗）——agent_messages 里该 session 的 assistant 行就是服务端产出真源。
    ⚠️ 超时≠成功：快照状态为 timeout 时必须判 FAIL/INCOMPLETE（返回空回复），
    禁止被二次回读的瞬时成功态翻案；快照本身只读一次，消除竞态窗口。
    """
    snap = _read_result_snapshot(session_id, db_path=db_path)  # 唯一读取路径：一次快照，多处比对
    if snap["status"] == "timeout":
        # 超时≠成功断言：快照态为 timeout，判 FAIL/INCOMPLETE，不翻案。
        return ""
    return snap["reply"] or ""


async def _drain_ws(ws, collect_sid: str, out: list, timeout: float) -> int:
    """排水 WS 帧：收走后到帧防读缓冲顶满反压 agent stdout（回合被拖死的隐患），
    顺带收集该会话文本作辅证。连接断开等异常只停排水不冒泡（DB 真源回读继续）。"""
    got = 0
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        except (TimeoutError, asyncio.TimeoutError):
            break
        except Exception:  # noqa: BLE001 — 连接已断/已关闭，排水终止即可
            break
        got += 1
        try:
            msg = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if msg.get("method") == "session/update":
            psid = (msg.get("params") or {}).get("sessionId") or ""
            if collect_sid and psid and psid != collect_sid:
                continue  # 其他会话的在途帧，不混进本任务后到产出
            _collect_texts(msg.get("params") or msg, out)
    return got


async def late_readback(verify, get_reply, ws=None, collect_sid: str = "",
                        budget_s: float | None = None,
                        poll_s: float | None = None) -> tuple[bool, str, float, int]:
    """WS 超时后的二次回读：预算内轮询服务端真源直到验证通过或预算耗尽。

    验证对象=落库回复（get_reply，服务端真源）+ 排水收到的后到帧文本；
    verify 复用各类别验证器（服务端副作用优先纪律不变）。返回
    (ok, detail, waited_s, attempts)——预算耗尽如实 False，禁把超时当成功。
    """
    budget_s = LATE_READBACK_BUDGET_S if budget_s is None else budget_s
    poll_s = LATE_READBACK_POLL_S if poll_s is None else poll_s
    t0 = time.monotonic()
    attempts = 0
    frame_chunks: list = []
    while True:
        attempts += 1
        db_reply = get_reply() or ""
        reply = "\n\n".join(x for x in (db_reply, "\n".join(frame_chunks)) if x)
        waited = round(time.monotonic() - t0, 2)
        try:
            ok, detail = verify(reply)
        except Exception as e:  # noqa: BLE001 — 验证器异常按未过计，不炸回读环
            ok, detail = False, f"验证器异常: {type(e).__name__}: {e}"
        if ok:
            return True, f"{detail}｜后到回读{waited:.1f}s/{attempts}次", waited, attempts
        if waited >= budget_s:
            return (False,
                    f"WS超时且后到回读预算耗尽({budget_s:.0f}s/{attempts}次仍无服务端产出): {detail}",
                    waited, attempts)
        window = min(poll_s, max(0.0, budget_s - (time.monotonic() - t0)))
        t_win = time.monotonic()
        if ws is not None:
            await _drain_ws(ws, collect_sid, frame_chunks, timeout=window)
        leftover = window - (time.monotonic() - t_win)
        if leftover > 0:
            await asyncio.sleep(leftover)


# ── 各类别独立验证（服务端副作用优先）────────────────────────────


# 12.6-②（周期#75）："用 terminal 运行它"判据——运行证据必须来自执行类工具账本行
# （terminal/execute_code），write_file 的内容行不算运行。真实库实证形态：
# name=terminal、args={'command': 'python3 /tmp/soulmate_accept/dev_hello.py'}、
# result_preview='SOULMATE_DEV_OK'（程序输出）。
_DEV_RUN_TOOLS = ("terminal", "execute_code")
_DEV_RUN_OUTPUT = "SOULMATE_DEV_OK"
# 源码回显不算运行（cat/echo dev_hello.py 的输出含 print(，真实运行输出只有标记行）
_DEV_SRC_ECHO_RE = re.compile(r"print\s*\(")


def verify_dev(reply: str) -> tuple[bool, str]:
    """路线图12.6-②：开发类判据=文件 marker + "用 terminal 运行它" 运行账本双判据。

    弱项根因（S1 实证）：只查文件 marker，terminal 步骤被审计误杀（未执行）仍判
    pass = 假过。升级：运行证据=terminal/execute_code 执行行（parse_ledger 单一
    真源）args 引用 dev_hello 且 result_preview 含程序输出、且非源码回显；
    回复辅证=报告输出。账本不可用诚实降级文件+回复（mode_note=ledger_unavailable，
    不假称 server_side）。
    """
    path = os.path.join(WORKDIR, "dev_hello.py")
    exists = os.path.exists(path)
    content = _read(path)
    file_ok = exists and _DEV_RUN_OUTPUT in content
    file_part = (f"dev_hello.py {'存在' if exists else '不存在'}，"
                 f"marker={'命中' if _DEV_RUN_OUTPUT in content else '未命中'}")
    reply_ok = _DEV_RUN_OUTPUT in (reply or "")
    reply_part = f"回复报告输出={'是' if reply_ok else '否'}"

    _VERIFY_CTX["mode_note"] = ""
    hits, state = _tool_ledger(_DEV_RUN_TOOLS)
    # 引用判定覆盖 name（"terminal: <cmd>" 冒号前缀形态的命令活在 name 里，
    # CLI 轨迹行实证）+ arguments + result_preview
    ran = [h for h in hits
           if "dev_hello" in f"{h.name} {h.arguments} {h.result_preview}"]
    run_ok = any(
        _DEV_RUN_OUTPUT in (h.result_preview or "")
        and not _DEV_SRC_ECHO_RE.search(h.result_preview or "")
        for h in ran
    )
    run_part = (f"运行账本={'命中' if run_ok else '未命中'}"
                f"（terminal/execute_code 引用 dev_hello 的执行行={len(ran)}，"
                f"其中含程序输出且非源码回显={'是' if run_ok else '否'}）")
    if state == "unavailable":
        _VERIFY_CTX["mode_note"] = "ledger_unavailable"
        return file_ok and reply_ok, (
            f"⚠️轨迹账本无本会话行（记账链路未写入），判据降级文件marker+回复辅证；"
            f"{file_part}；{reply_part}")
    return file_ok and run_ok and reply_ok, (
        f"{file_part}；{run_part}（真源=trajectory_events）；{reply_part}")


def verify_debug(reply: str) -> tuple[bool, str]:
    content = _read(os.path.join(WORKDIR, "buggy.py"))
    fixed = "return a + b" in content
    tagged = "FIXED" in content
    return fixed and tagged, f"buggy.py 修复={'是' if fixed else '否'}(return a + b)、FIXED标记={'有' if tagged else '无'}"


def verify_ops(reply: str) -> tuple[bool, str]:
    """路线图12.2：服务端判据=体检结果落账（工具账本真源）。

    运维类确定性判据：system_checkup 账本行 result_preview 内含 8090 端口与
    磁盘结论（体检结果落账/审计行）；回复文本只作辅证。账本判据不可用时诚实
    降级回复辅证（mode_note=ledger_unavailable，verify_mode 不假称 server_side）。
    """
    _VERIFY_CTX["mode_note"] = ""
    hits, state = _tool_ledger("system_checkup")
    ledger_ok = any(
        "8090" in h.result_preview
        and ("%" in h.result_preview or "磁盘" in h.result_preview
             or "disk" in h.result_preview.lower())
        for h in hits
    )
    reply_ok = "8090" in reply and ("%" in reply or "磁盘" in reply or "disk" in reply.lower())
    reply_part = (f"回复含8090={'是' if '8090' in reply else '否'}、"
                  f"磁盘信息={'是' if ('%' in reply or '磁盘' in reply) else '否'}")
    if state == "unavailable":
        _VERIFY_CTX["mode_note"] = "ledger_unavailable"
        return reply_ok, f"⚠️轨迹账本无本会话行（记账链路未写入），判据降级回复辅证；{reply_part}"
    return ledger_ok and reply_ok, (
        f"体检结果落账={'命中' if ledger_ok else '未命中'}"
        f"（账本 system_checkup 执行行={len(hits)}，真源=trajectory_events）；{reply_part}")


# 自报搜索不可用=真实声称，判据=主语邻接（确定性规则，周期#41两轮实证收敛）：
# "web_search 工具不可用"/"搜索工具不可用"（主体与"不可用"间≤6字且不在引号内）才算；
# 引用/抽象提及（'因此"不可用时…'、'不存在"不可用"情况'、'无需声明不可用'）不计——
# 模型会引用提示语里的"不可用"字样，裸子串判据会假阴性杀掉正确回答。
_UNAVAIL_CLAIM_RE = re.compile(
    r"(web_search|web_extract|搜索工具|搜索后端|搜索功能|搜索|工具)"
    r"[^\n。；！？\"'“”‘’]{0,6}不可用"
)


def verify_research(reply: str) -> tuple[bool, str]:
    text = reply or ""
    # ①剥离回合心跳噪声（turn_watchdog快照会被harness并进回复采集；
    #   心跳可能与回答粘连无换行——按心跳结构精确剥，禁吞答案里的括号）
    text = re.sub(
        r"⏳ 回合进行中：已用 [^｜\n]*｜[^｜\n]*｜已执行工具 \d+ 个（最近：[^）\n]*）?",
        "", text,
    )
    # ②自报不可用只在主答区判（首个段落边界前≤200字）——补做/逐项说明、需求自检
    #   报告、可用性更正声明里的引用提示语/需求原文复述/可用性复盘不计（周期#41
    #   E/G/H三轮实证+周期#43 STYLE85C_1 实证：需求自检报告逐条复述"如果搜索工具
    #   不可用…"类需求原文，harness 并帧采集后混入主答区 → 正确回答被假阳性判负）
    lead = re.split(
        r"\n\s*\n|逐项说明|补做结果|🔍\s*需求自检|可用性更正声明", text, maxsplit=1
    )[0][:200]
    claim = False
    for m in _UNAVAIL_CLAIM_RE.finditer(lead):
        if lead[:m.start()].count('"') % 2 == 1:
            continue  # 引号内的引用不计
        claim = True
        break
    # ③必须给出被搜事实（问题=Python 3.13 发布时间，答案含 3.13 + 2024 年份）
    has_fact = "3.13" in text and "2024" in text
    reply_ok = has_fact and len(text) > 30 and not claim
    reply_part = (f"回复含'3.13'={'是' if '3.13' in text else '否'}、"
                  f"含'2024'={'是' if '2024' in text else '否'}、"
                  f"主答区自报搜索不可用={'是' if claim else '否'}、"
                  f"长度{len(text)}字")
    # ④路线图12.2：服务端判据=检索调用账本+来源行（工具账本真源）——web_search
    #   执行行 result_preview 内含真实来源 URL（检索结果来源行），零新增落库负担。
    _VERIFY_CTX["mode_note"] = ""
    hits, state = _tool_ledger("web_search")
    ledger_ok = any(re.search(r"https?://", h.result_preview or "") for h in hits)
    if state == "unavailable":
        _VERIFY_CTX["mode_note"] = "ledger_unavailable"
        return (reply_ok,
                f"⚠️轨迹账本无本会话行（记账链路未写入），判据降级回复辅证；{reply_part}")
    return reply_ok and ledger_ok, (
        f"检索调用账本+来源行={'命中' if ledger_ok else '未命中'}"
        f"（web_search 执行行={len(hits)}，真源=trajectory_events）；{reply_part}")


def verify_publish(reply: str) -> tuple[bool, str]:
    # 只认真实投递行（session_id='accept-pub-target' 的落库行=服务端副作用），
    # 不认 user 提示行；cleanup() 预清探针行保证行数只来自本 run（复测铁律②补全：
    # 周期#41实证旧 cleanup 漏清 agent_messages 探针行→陈旧行假阳性放过了真实失败的run）
    rows = _query(
        os.path.expanduser("~/opensoul/data/opensoul.db"),
        "SELECT content FROM agent_messages WHERE session_id = 'accept-pub-target' "
        "AND content LIKE ? ORDER BY rowid DESC LIMIT 5",
        ("%SOULMATE_PUBLISH_PROBE%",),
    )
    return bool(rows), f"opensoul.db 投递行（accept-pub-target）数={len(rows)}"


def verify_office(reply: str) -> tuple[bool, str]:
    path = os.path.join(WORKDIR, "report.xlsx")
    exists = os.path.exists(path)
    valid = exists and zipfile.is_zipfile(path)
    size = os.path.getsize(path) if exists else 0
    return valid and size > 400, f"report.xlsx {'存在' if exists else '不存在'}、有效xlsx={'是' if valid else '否'}、{size}字节"


def verify_cron(reply: str) -> tuple[bool, str]:
    rows = _query(CRON_DB, "SELECT * FROM tasks WHERE name = ?", ("accept_probe",))
    return bool(rows), f"cron_tasks.db tasks 表 accept_probe 行数={len(rows)}"


def verify_multichannel(reply: str) -> tuple[bool, str]:
    """路线图12.1：服务端出站痕迹回读（不再依赖回复辅证）。

    判据=出站审计行（agent/outbound_audit.py）存在 channel=wechat、
    target=accept_peer、status=sent 且文本含探针文案的痕迹行。
    """
    rows = _query(
        OUTBOUND_DB,
        "SELECT ts_ms, kind, status, text_preview FROM outbound_log "
        "WHERE channel = 'wechat' AND target = ? AND status = 'sent' "
        "ORDER BY ts_ms DESC, id DESC LIMIT 20",
        ("accept_peer",))
    hit = [r for r in rows if MULTICHANNEL_MARKER in (r[3] or "")]
    return bool(hit), (
        f"出站痕迹行（wechat→accept_peer 含探针文案）={len(hit)}"
        f"（审计库总相关行={len(rows)}）")


def verify_memory(reply: str) -> tuple[bool, str]:
    content = _read(MEM_PATH)
    return MEM_MARKER in content, f"MEMORY.md 含 {MEM_MARKER}={'是' if MEM_MARKER in content else '否'}"


def verify_collab(reply: str) -> tuple[bool, str]:
    rows = _query(SUB_DB, "SELECT status FROM subagent_runs ORDER BY id DESC LIMIT 8")
    both = "ALPHA_DONE" in reply and "BETA_DONE" in reply
    db_ok = len(rows) >= 2
    return both and db_ok, f"回复含双worker产出={'是' if both else '否'}、subagent_runs近8行={len(rows)}"


# 12.2：运维/调研判据升级服务端（工具账本真源）→ 全部 10 类 server_side
SERVER_SIDE = {"开发", "调试", "运维", "调研", "发布", "办公", "定时", "记忆", "协作", "多渠道"}

TASKS = [
    {
        "category": "开发",
        "prompt": (
            "用 write_file 创建文件 /tmp/soulmate_accept/dev_hello.py，内容为一行："
            "print('SOULMATE_DEV_OK')。然后用 terminal 运行它，告诉我输出。"
        ),
        "verify": verify_dev,
    },
    {
        "category": "调试",
        "prompt": (
            "打开 /tmp/soulmate_accept/buggy.py，里面有一个 bug：add(2,3) 应该输出 5 但结果不对。"
            "请修复这个 bug（把注释里的 BUG 字样改成 FIXED），不要做其他改动。"
        ),
        "verify": verify_debug,
    },
    {
        "category": "运维",
        "prompt": (
            "用 system_checkup 工具做一次系统体检，告诉我磁盘使用百分比和 8090 端口是否在监听。"
        ),
        "verify": verify_ops,
    },
    {
        "category": "调研",
        "prompt": (
            "用 web_search 搜索 Python 3.13 的正式发布时间，简短回答（一句话）。"
            "如果搜索工具不可用，明确说明不可用。"
        ),
        "verify": verify_research,
    },
    {
        "category": "发布",
        "prompt": (
            "用 message_send 工具向 openmate 通道发送一条消息：channel='openmate'，"
            "target='accept-pub-target'，text='SOULMATE_PUBLISH_PROBE 验收发布探针'。"
            "发送后告诉我结果。"
        ),
        "verify": verify_publish,
    },
    {
        "category": "办公",
        "prompt": (
            "用 doc_generate 工具生成 Excel 文件 /tmp/soulmate_accept/report.xlsx，"
            "内容为二维表格：表头['名称','数量']，数据行[['苹果',3],['香蕉',5]]。完成后告诉我。"
        ),
        "verify": verify_office,
    },
    {
        "category": "定时",
        "prompt": (
            "依次执行：①用 schedule_task 创建定时任务 name='accept_probe'，"
            "schedule='interval:1h'，payload='验收探针任务'；②用 schedule_list 确认它在列表里；"
            "③用 schedule_cancel 停用它（不要 delete）。把任务 id 告诉我。"
        ),
        "verify": verify_cron,
    },
    {
        "category": "多渠道",
        "prompt": (
            "依次执行：①用 wechat_route_list 查看微信会话路由映射；"
            "②用 message_send 向微信通道发一条测试消息：channel='wechat'，"
            "target='accept_peer'，text='验收多渠道探针'。把两步结果都告诉我。"
        ),
        "verify": verify_multichannel,
    },
    {
        "category": "记忆",
        "prompt": (
            "依次执行：①用 memory_append 追加一行到持久记忆：MEMORY: ACCEPT_MEM_MARK_9 验收探针记录；"
            "②用 memory_read 读出记忆末尾并展示。"
        ),
        "verify": verify_memory,
    },
    {
        "category": "协作",
        "prompt": (
            "用 delegate_tasks 工具并行派发两个子任务（max_concurrency=2）："
            "任务1 goal='只回复一个词：ALPHA_DONE'；任务2 goal='只回复一个词：BETA_DONE'。"
            "完成后把两个子任务的结果都告诉我。"
        ),
        "verify": verify_collab,
    },
]


def setup() -> None:
    cleanup()  # 复测铁律：验证前先清陈旧产物（上次被打断轮的 accept_probe 行/记忆标记会假阳性）
    shutil.rmtree(WORKDIR, ignore_errors=True)
    os.makedirs(WORKDIR, exist_ok=True)
    with open(os.path.join(WORKDIR, "buggy.py"), "w", encoding="utf-8") as f:
        f.write(
            "def add(a, b):\n"
            "    return a - b  # BUG: should be addition\n"
            "\n"
            "print(add(2, 3))\n"
        )


def cleanup() -> None:
    if os.path.exists(MEM_PATH):
        lines = _read(MEM_PATH).splitlines(keepends=True)
        kept = [ln for ln in lines if MEM_MARKER not in ln]
        if len(kept) != len(lines):
            with open(MEM_PATH, "w", encoding="utf-8") as f:
                f.writelines(kept)
    try:
        conn = sqlite3.connect(CRON_DB, timeout=10)
        conn.execute("DELETE FROM tasks WHERE name = ?", ("accept_probe",))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    # 复测铁律②补全（周期#41）：发布类探针行同样要预清——旧 cleanup 只清 MEMORY
    # 标记和 cron 行，agent_messages 探针行跨 run 累积→陈旧行假阳性（实证放过了
    # 一个真实未发送的 run）
    try:
        conn = sqlite3.connect(AGENT_MESSAGES_DB, timeout=10)
        conn.execute(
            "DELETE FROM agent_messages WHERE content LIKE ?",
            ("%SOULMATE_PUBLISH_PROBE%",))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    # 路线图12.1：多渠道出站痕迹探针行同样预清（陈旧行假阳性防线）
    try:
        conn = sqlite3.connect(OUTBOUND_DB, timeout=10)
        conn.execute(
            "DELETE FROM outbound_log WHERE target = ? OR text_preview LIKE ?",
            ("accept_peer", f"%{MULTICHANNEL_MARKER}%"))
        conn.commit()
        conn.close()
    except sqlite3.Error:
        pass
    # 路线图12.5（周期#71）：run 末显式清理工作目录，兑现 docstring"测试产物
    # 显式清理"承诺（周期#64 实证：此前只有开跑前 rmtree，run 后 /tmp 残留
    # 产物）。setup() 开头也走本函数——那里 rmtree 无害（随后立即重建）。
    shutil.rmtree(WORKDIR, ignore_errors=True)


# ── WS 驱动（soulmate 真实用户路径）──────────────────────────────


def _collect_texts(obj, out: list) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("text", "content") and isinstance(v, str) and v.strip():
                out.append(v)
            else:
                _collect_texts(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_texts(v, out)


async def _rpc(ws, method: str, params: dict, rid: int, timeout: float,
               collect_sid: str = "") -> tuple[dict, str, dict]:
    """发请求等同 id 响应；期间收集 session/update 文本块为回复文本。

    collect_sid 非空时只收集该 session 的帧——同一 WS 连接上前一回合的
    未完帧会继续到达，不按 sessionId 过滤会串进下一任务的回复（周期#30
    实测踩坑：任务7超时回合的帧污染任务8回复采集）。

    ⚡8.5c-⑤：合并文本再按帧来源过滤（split_frames）——心跳快照/需求自检推送/
    治理横幅是系统帧，混入回复会污染判据（周期#43 STYLE85C_1 实证），过滤计数
    随返回值可观测。返回 (响应, 回答文本, 系统帧计数)。
    """
    await ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
    chunks: list = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, timeout - (time.time() - t0)))
        msg = json.loads(raw)
        if msg.get("id") == rid:
            _collect_texts(msg, chunks)
            reply, sys_counts = split_frames(chunks)
            return msg, reply, sys_counts
        if msg.get("method") == "session/update":
            psid = (msg.get("params") or {}).get("sessionId") or ""
            if collect_sid and psid and psid != collect_sid:
                continue  # 其他会话的在途帧，不串进本任务回复
            _collect_texts(msg.get("params") or msg, chunks)
    raise TimeoutError(f"{method} 无响应({timeout}s)")


async def main() -> None:
    setup()
    tasks = [t for t in TASKS if not RETEST_CATEGORIES or t["category"] in RETEST_CATEGORIES]
    if RETEST_CATEGORIES:
        missing = RETEST_CATEGORIES - {t["category"] for t in tasks}
        if missing:
            print(f"⚠️ 未知类别被忽略: {sorted(missing)}", flush=True)
    results = []
    token = pyjwt.encode({"sub": f"accept-{int(time.time())}"}, JWT_SECRET, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode()
    async with websockets.connect(f"{ACP_WS}?token={token}", max_size=20_000_000) as ws:
        r, _, _ = await _rpc(ws, "initialize", {"protocolVersion": 1}, rid=1, timeout=30)
        if not (r.get("result") or {}).get("protocolVersion"):
            print("initialize 握手失败:", r, flush=True)
        for i, task in enumerate(tasks, 1):
            print(f"[{i}/{len(tasks)}] {task['category']} (tag={RUN_TAG}) ...", flush=True)
            t0 = time.monotonic()
            entry = {"category": task["category"], "prompt": task["prompt"][:120], "run_tag": RUN_TAG}
            prompt_text = f"{task['prompt']}\n（复测标记 {RUN_TAG}，此行仅为防缓存标记，忽略其语义）"
            sid = ""
            prompt_timed_out = False
            try:
                r, _, _ = await _rpc(
                    ws, "session/new",
                    {"cwd": "/home/climbing", "mcpServers": [], "agent_id": "soulmate"},
                    rid=100 + i * 2, timeout=90,
                )
                res = r.get("result") or {}
                sid = res.get("session_id") or res.get("sessionId") or ""
                if not sid:
                    raise RuntimeError(f"session/new 无session_id: {r}")
                # 12.2：工具账本判据的会话上下文（新建会话→账本行只可能来自本 run）
                _VERIFY_CTX["session_id"] = sid
                try:
                    r, reply, sys_frames = await _rpc(
                        ws, "session/prompt",
                        {"sessionId": sid, "messageId": f"accept-{i}",
                         "prompt": [{"type": "text", "text": prompt_text}]},
                        rid=101 + i * 2, timeout=PROMPT_TIMEOUT_S, collect_sid=sid,
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    prompt_timed_out = True
                    raise
                stop = (r.get("result") or {}).get("stopReason", "-")
                entry["session_id"] = sid
                entry["stop_reason"] = stop
                entry["http_ok"] = "result" in r
                entry["system_frames"] = sys_frames
            except Exception as e:  # noqa: BLE001
                if prompt_timed_out and sid:
                    # 10.4（周期#58）：WS 超时≠失败——服务端回合照跑（观察窗纪律），
                    # 服务端产出会后到落库。二次回读服务端真源，与服务端真相同口径。
                    entry["session_id"] = sid
                    entry["ws_timeout"] = True
                    entry["latency_s"] = round(time.monotonic() - t0, 2)
                    print(f"    -> ⏱ WS {PROMPT_TIMEOUT_S}s 超时（服务端回合照跑），"
                          f"后到产出二次回读（预算{LATE_READBACK_BUDGET_S:.0f}s）...", flush=True)
                    ok, detail, waited, attempts = await late_readback(
                        task["verify"], lambda: _read_back_reply(sid),
                        ws=ws, collect_sid=sid)
                    mode = "server_side" if task["category"] in SERVER_SIDE else "reply_assisted"
                    if _VERIFY_CTX.get("mode_note"):
                        mode = "reply_assisted"  # 12.2：账本判据降级时诚实标注
                    entry.update(
                        ok=bool(ok),
                        verify_mode=f"{mode}_late" if ok else "ws_timeout_unrecovered",
                        detail=detail,
                        reply_excerpt=re.sub(r"\s+", " ", _read_back_reply(sid))[:200],
                        late_readback_wait_s=waited,
                        late_readback_attempts=attempts,
                        latency_s=round(time.monotonic() - t0, 2),
                    )
                else:
                    entry.update(ok=False, latency_s=round(time.monotonic() - t0, 2),
                                 verify_mode="error", detail=f"WS失败: {type(e).__name__}: {e}",
                                 reply_excerpt="")
                results.append(entry)
                print(f"    -> {'✅' if entry['ok'] else '❌'} {entry['detail']} ({entry['latency_s']}s)",
                      flush=True)
                continue
            latency = round(time.monotonic() - t0, 2)
            entry["reply_excerpt"] = re.sub(r"\s+", " ", reply)[:200]
            entry["latency_s"] = latency
            try:
                ok, detail = task["verify"](reply)
            except Exception as e:  # noqa: BLE001
                ok, detail = False, f"验证器异常: {type(e).__name__}: {e}"
            entry["ok"] = bool(ok) and entry.get("http_ok", False)
            entry["verify_mode"] = "server_side" if task["category"] in SERVER_SIDE else "reply_assisted"
            if _VERIFY_CTX.get("mode_note"):
                entry["verify_mode"] = "reply_assisted"  # 12.2：账本判据降级时诚实标注
            entry["detail"] = detail
            results.append(entry)
            print(f"    -> {'✅' if entry['ok'] else '❌'} {detail} "
                  f"({latency}s, stop={entry.get('stop_reason')}) reply={entry['reply_excerpt'][:80]!r}",
                  flush=True)
    cleanup()
    sys_total: dict[str, int] = {}
    for r in results:
        for kind, n in (r.get("system_frames") or {}).items():
            sys_total[kind] = sys_total.get(kind, 0) + n
    summary = {
        "run_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "run_tag": RUN_TAG,
        "categories": sorted(RETEST_CATEGORIES) if RETEST_CATEGORIES else "ALL",
        "passed": sum(1 for r in results if r["ok"]),
        "total": len(results),
        "system_frames_total": sys_total,
        "late_readback": {
            "ws_timeouts": sum(1 for r in results if r.get("ws_timeout")),
            "recovered": sum(1 for r in results
                             if r.get("ws_timeout") and r.get("verify_mode", "").endswith("_late")),
        },
        "results": results,
    }
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("=" * 60)
    print(f"验收结果: {summary['passed']}/{summary['total']} 通过")
    if summary["late_readback"]["ws_timeouts"]:
        lr = summary["late_readback"]
        print(f"WS超时后到回读（10.4）: 超时{lr['ws_timeouts']}个，二次回读恢复{lr['recovered']}个")
    if sys_total:
        print(f"采集侧系统帧过滤（8.5c-⑤）: {sys_total}")
    for r in results:
        print(f"  {'✅' if r['ok'] else '❌'} {r['category']:<4} {r['latency_s']:>7}s  {r['detail']}")
    print(f"完整结果: {RESULTS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
