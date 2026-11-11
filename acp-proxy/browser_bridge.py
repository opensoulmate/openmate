"""browser_bridge — 内置浏览器自动化通道（SoulMate 的 browser 工具 ↔ OpenMate 应用）。

架构：应用（Electron <webview>）长连 /ws/browser 领任务 → 在内置浏览器里执行（真实键鼠+
页面本体 DOM，webview 桥四口 exec/input/nav/capture）→ 回传结果。agent 侧 BrowserTools
走 HTTP 任务队列（POST /api/browser/task + 长轮询 /api/browser/result/{id}），动作结果
字段与 worker 模式同构（browser_tools._format 无改动消费）。

接管语义（用户随时接管）：用户手动输入（真实鼠标/键盘）→ 执行器立刻拒动并暂停（⏸），
所有后续动作拒绝执行，直到 agent 调 browser(action=resume)（用户说"继续"）。
窗口折叠=webview 隐藏照跑，展开=用户实时观看（同一扇真浏览器窗）。
"""
import asyncio
import base64
import hashlib
import json
import re
import threading
import time
import uuid
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

# 执行凭证/分层路由/skill 沉淀纯逻辑层（同一真源，无 fastapi 依赖）
from flow_receipts import (  # noqa: F401  (re-export：agent 侧统一从 browser_bridge 取)
    decide_engine,
    execute_flow_steps,
    execution_report,
    get_receipts,
    headless_refusal_reason,
    infer_flow_context,
    mask_params,
    record_receipt,
    skill_md_for,
)

# 任务信箱：task_id -> rec（done=threading.Event，WS 线程与 HTTP 线程共享）
_TASKS: dict = {}
_LOCK = threading.Lock()
_APPS: dict = {}  # session_id -> {"ws": WebSocket, "loop": asyncio.AbstractEventLoop}
_SHOT_DIR = Path.home() / ".hermes" / "soulmate" / "browser_shots"
# webview 自救控制通道：request_id -> {"event": threading.Event, "result": dict|None}
_ENSURE_WAITERS: dict = {}

# 任务执行上限（应用端单动作）；长轮询上限必须大于它，让真实错误先回来如实报因
TASK_TIMEOUT_S = 45.0
# webview 自救等待上限（webview-ready 有界等待，禁无限等）
ENSURE_WEBVIEW_TIMEOUT_S = 10.0


def submit_task(action: str, params: dict, session_id: str = "", secret: bool = False,
                receipt_action: str = "", fail_note: str = "") -> dict:
    """agent 侧投递任务（HTTP 线程调用）。返回 task_id。

    secret=True 表示本步输入含密码/凭据——回执摘要脱敏为 ***（铁律：不落回执）。
    secret 只留服务端（不进 ws 派发消息）。
    receipt_action：回执上的动作标注（如 screenshot(重试1)——重试进凭证，账单不掩盖自救）。
    fail_note：失败时附在回执错误后的注记（如 （重开后仍失败）），成功不附。
    """
    tid = uuid.uuid4().hex[:12]
    rec = {
        "task_id": tid, "action": action, "params": params or {},
        "session_id": session_id, "created": time.time(), "secret": bool(secret),
        "done": threading.Event(), "ok": False, "result": {}, "error": "",
        "receipt_action": str(receipt_action or ""), "fail_note": str(fail_note or ""),
    }
    with _LOCK:
        _TASKS[tid] = rec
    return {"task_id": tid}


def _digest(result: dict) -> str:
    """结果摘要（回执用）：URL/标题/点击/输入目标等非敏感锚点，绝不带输入值。"""
    if not isinstance(result, dict):
        return str(result)[:120]
    bits = [str(result.get(k, "")) for k in ("url", "title", "clicked", "typed_into", "path")]
    s = " ".join(b for b in bits if b)
    return s[:160] or "(无结果摘要)"


def wait_result(task_id: str, wait_s: float = 30.0) -> dict:
    """长轮询结果（HTTP 线程调用）。超时=如实报因，禁编造。

    超时/未连接也写执行回执（ok=False「未确认完成」）——派发过就有账单；
    迟到的真实结果由 _complete 按 task_id 覆盖改写（同一份实录）。
    """
    with _LOCK:
        rec = _TASKS.get(task_id)
    if rec is None:
        return {"ok": False, "error": f"未知任务 {task_id}"}
    if not rec["done"].wait(timeout=wait_s):
        if not app_connected(rec.get("session_id", "")):
            err = "OpenMate 应用未连接（内置浏览器通道无客户端）——确认桌面端在跑"
        else:
            err = f"应用执行超时（{wait_s:g}s）——页面卡死或动作过慢，未确认完成"
        record_receipt(task_id=task_id,
                       action=rec.get("receipt_action") or rec.get("action", ""),
                       params=rec.get("params") or {},
                       ok=False,
                       error=err + str(rec.get("fail_note", "") or ""),
                       duration=time.time() - rec.get("created", time.time()),
                       engine="app", secret=bool(rec.get("secret")))
        return {"ok": False, "error": err}
    if rec["ok"]:
        return {"ok": True, "result": rec["result"]}
    return {"ok": False, "error": rec["error"]}


def app_connected(session_id: str = "") -> bool:
    with _LOCK:
        if session_id and session_id in _APPS:
            return True
        return bool(_APPS)


def status() -> dict:
    with _LOCK:
        pending = [t["action"] for t in _TASKS.values() if not t["done"].is_set()]
        return {"apps": list(_APPS.keys()), "pending_tasks": pending,
                "tasks_total": len(_TASKS)}


# ── webview 自救控制通道（ensure-webview ⇄ webview-ready）────────────
# 浏览器 tab 被关/切走后 <webview> 卸载（webContents 销毁）→ 步骤报「webview 已关闭」。
# 自救：bridge 发 ensure-webview 控制消息 → 应用自动创建/切到浏览器标签（复用「打开浏览器」
# 同条代码路径）→ webview 就绪回 webview-ready。等待有界（默认 10s），禁无限等。

def _on_control(msg: dict) -> None:
    """应用侧控制回执（webview-ready 等）：唤醒对应 ensure 等待者（request_id 配对）。"""
    rid = str(msg.get("request_id", "") or "")
    with _LOCK:
        w = _ENSURE_WAITERS.get(rid)
        if w is None:
            return
        w["result"] = {"ok": bool(msg.get("ok", True)), "error": str(msg.get("error", "") or "")}
        w["event"].set()


def ensure_webview(session_id: str = "", timeout: float = ENSURE_WEBVIEW_TIMEOUT_S) -> dict:
    """webview 自救：向应用发 ensure-webview 控制消息，等 webview-ready（有界 timeout）。

    应用收到后自动创建/切到浏览器标签并注册 webview，就绪回 webview-ready；
    无应用连接/发送失败/webview-ready 超时 → 如实报因（不挂死调用方，禁止无限等）。
    """
    try:
        timeout = max(1.0, min(float(timeout or ENSURE_WEBVIEW_TIMEOUT_S), 30.0))
    except (TypeError, ValueError):
        timeout = ENSURE_WEBVIEW_TIMEOUT_S
    rid = uuid.uuid4().hex[:8]
    ev = threading.Event()
    with _LOCK:
        apps = dict(_APPS)
        _ENSURE_WAITERS[rid] = {"event": ev, "result": None}
    try:
        app = apps.get(session_id or "_default") or next(iter(apps.values()), None)
        if app is None:
            return {"ok": False,
                    "error": "OpenMate 应用未连接（内置浏览器通道无客户端）——确认桌面端在跑"}
        msg = json.dumps({"type": "control", "cmd": "ensure-webview", "request_id": rid},
                         ensure_ascii=False)
        try:
            fut = asyncio.run_coroutine_threadsafe(app["ws"].send_text(msg), app["loop"])
            fut.result(timeout=min(timeout, 5.0))
        except Exception as e:
            return {"ok": False, "error": f"ensure-webview 指令下发失败：{e}"}
        if not ev.wait(timeout):
            return {"ok": False,
                    "error": f"webview-ready 等待超时（{timeout:g}s 有界）——应用未确认 webview 就绪"}
        with _LOCK:
            res = (_ENSURE_WAITERS.get(rid) or {}).get("result") or {}
        if res.get("ok"):
            return {"ok": True, "error": ""}
        return {"ok": False, "error": str(res.get("error", "") or "应用报告 webview 未就绪")}
    finally:
        with _LOCK:
            _ENSURE_WAITERS.pop(rid, None)


def _complete(tid: str, ok: bool, result: dict | None = None, error: str = "") -> None:
    """ws 回传点：任务完成（result/cancel）→ 写执行回执（服务端派发实录，凭证数据源）。"""
    with _LOCK:
        rec = _TASKS.get(tid)
        if rec is None:
            return
        rec["ok"] = ok
        if ok:
            rec["result"] = result or {}
            if isinstance(rec["result"], dict) and rec["result"].get("data_url"):
                # 截图：数据 URL 落盘成文件（agent 用 MEDIA: 下发；路径在服务端）
                rec["result"] = dict(rec["result"], path=_save_shot(rec["result"].pop("data_url")))
        else:
            rec["error"] = error or "未知错误"
        rec["done"].set()
        action, params = rec.get("action", ""), rec.get("params") or {}
        secret, created = bool(rec.get("secret")), rec.get("created", time.time())
        res, err = rec.get("result") or {}, rec.get("error", "")
        rec_action = rec.get("receipt_action") or action
        fail_note = str(rec.get("fail_note", "") or "")
    record_receipt(task_id=tid, action=rec_action, params=params, ok=ok,
                   error=("" if ok else err + fail_note), summary=(_digest(res) if ok else ""),
                   duration=time.time() - created, engine="app", secret=secret)


def _save_shot(data_url: str) -> str:
    try:
        _SHOT_DIR.mkdir(parents=True, exist_ok=True)
        head, _, b64 = data_url.partition(",")
        p = _SHOT_DIR / f"shot-{int(time.time() * 1000)}.png"
        p.write_bytes(base64.b64decode(b64))
        return str(p)
    except Exception as e:
        return f"(截图落盘失败: {e})"


def _pop_pending_task(session_id: str):
    """取一个该会话的待发任务（同会话优先；空会话任务发给任一连接）。"""
    with _LOCK:
        for tid, rec in _TASKS.items():
            if rec["done"].is_set() or rec.get("_dispatched"):
                continue
            if rec.get("session_id") and session_id and rec["session_id"] != session_id:
                continue
            rec["_dispatched"] = True
            return {"type": "task", "task_id": tid, "action": rec["action"], "params": rec["params"]}
    return None


async def ws_browser_endpoint(websocket: WebSocket):
    """应用侧长连接：hello → 领任务 → 回 result。

    关键实现约束：Starlette/uvicorn 的 WebSocket receive 取消不安全（wait_for 取消会
    吞消息/卡死循环，实测实证）——收消息用常驻 recv_task 模式，永不取消 receive。
    """
    await websocket.accept()
    session_id = ""
    loop = asyncio.get_running_loop()  # 自救控制消息跨线程下发用（run_coroutine_threadsafe）
    recv_task = asyncio.create_task(websocket.receive_text())
    try:
        while True:
            # ① 投递队列里的下一个任务
            task = _pop_pending_task(session_id)
            if task:
                import sys as _sys
                print(f"[browser_bridge] 派发 {task['task_id']} {task['action']}", file=_sys.stderr, flush=True)
                await websocket.send_text(json.dumps(task, ensure_ascii=False))
            # ② 等收消息（不取消 receive：只等待 recv_task，到点继续投递节拍）
            done, _ = await asyncio.wait({recv_task}, timeout=0.3,
                                         return_when=asyncio.FIRST_COMPLETED)
            if recv_task not in done:
                continue
            raw = recv_task.result()  # 断线会在这里抛 WebSocketDisconnect
            recv_task = asyncio.create_task(websocket.receive_text())
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type", "")
            if mtype == "hello":
                session_id = str(msg.get("session_id", "") or "")
                with _LOCK:
                    _APPS[session_id or "_default"] = {"ws": websocket, "loop": loop}
            elif mtype == "control":
                _on_control(msg)  # webview-ready 等控制回执（自救等待者配对唤醒）
            elif mtype == "result":
                _complete(str(msg.get("task_id", "")), bool(msg.get("ok")),
                          result=msg.get("result") or {}, error=str(msg.get("error", "")))
            elif mtype == "cancel":
                _complete(str(msg.get("task_id", "")), False, error="应用侧取消")
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        recv_task.cancel()
        with _LOCK:
            for k in [k for k, v in _APPS.items() if v.get("ws") is websocket]:
                _APPS.pop(k, None)
        # 连接断了：还挂着的任务如实失败（禁静默悬挂）
        with _LOCK:
            stuck = [t for t in _TASKS.values()
                     if not t["done"].is_set() and t.get("_dispatched")]
        for rec in stuck:
            _complete(rec["task_id"], False, error="应用连接断开，动作未确认完成")


def install(app) -> None:
    """挂进 acp-proxy 的 FastAPI app（app.py 调一次）。"""

    @app.websocket("/ws/browser")
    async def _ws_browser(websocket: WebSocket):
        await ws_browser_endpoint(websocket)

    @app.post("/api/browser/task")
    async def _task(req: dict):
        action = str(req.get("action", "") or "").strip().lower()
        params = req.get("params") or {}
        session_id = str(req.get("session_id", "") or "")
        secret = bool(req.get("secret"))
        return submit_task(action, params, session_id, secret=secret,
                           receipt_action=str(req.get("receipt_action", "") or ""),
                           fail_note=str(req.get("fail_note", "") or ""))

    @app.post("/api/browser/ensure-webview")
    async def _ensure_webview(req: dict):
        # webview 自救控制口：向应用发 ensure-webview，等 webview-ready（有界 timeout，禁无限等）
        return await asyncio.to_thread(
            ensure_webview, str(req.get("session_id", "") or ""),
            float(req.get("timeout", ENSURE_WEBVIEW_TIMEOUT_S) or ENSURE_WEBVIEW_TIMEOUT_S))

    @app.get("/api/browser/receipts")
    async def _receipts(since: float = 0.0):
        # 执行回执（服务端派发实录）：since=<ts> 取该时刻后的回执——凭证的唯一数据源
        return {"receipts": get_receipts(since)}

    @app.post("/api/browser/receipt")
    async def _receipt_post(body: dict):
        # 跨进程补录（无头执行步骤回执）：与派发回执同一滚动表，供同一套执行凭证
        return {"ok": True, "receipt": record_receipt(
            task_id=str(body.get("task_id", "")), action=str(body.get("action", "")),
            params=body.get("params") or {}, ok=bool(body.get("ok")),
            error=str(body.get("error", "")), summary=str(body.get("summary", "")),
            duration=float(body.get("duration", 0) or 0),
            engine=str(body.get("engine", "") or "headless"), secret=bool(body.get("secret")),
            step_index=int(body.get("step_index", 0) or 0), flow=str(body.get("flow", "")))}

    @app.get("/api/browser/result/{task_id}")
    async def _result(task_id: str, wait_s: float = 30.0):
        # 长轮询必须让出事件循环（同步 Event.wait 会冻结整个 loop=WS 派发瘫痪，实测实证）
        return await asyncio.to_thread(wait_result, task_id, min(wait_s, TASK_TIMEOUT_S + 5))

    @app.get("/api/browser/status")
    async def _status():
        return status()

    # ── 流程配方（录制→回放，OpenLimb RPA 的录制层沉淀）────────
    # 录制的业务输入值只存本地 JSON 配方（data/browser_flows/），不进日常行为日志、不上云。
    # 密码类字段在录制点即参数化为 {{password}}，永不落盘。

    @app.post("/api/browser/flows")
    async def _flow_save(body: dict):
        return save_flow(body)

    @app.get("/api/browser/flows")
    async def _flow_list():
        return {"flows": list_flows()}

    @app.get("/api/browser/flows/{flow_id}")
    async def _flow_get(flow_id: str):
        return load_flow(flow_id)

    @app.delete("/api/browser/flows/{flow_id}")
    async def _flow_delete(flow_id: str):
        return delete_flow(flow_id)


# ── 流程配方存取（本地 JSON 文件库）──────────────────────────

_FLOWS_DIR = Path(__file__).resolve().parent / "data" / "browser_flows"


def _flow_path(flow_id: str) -> Path:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", str(flow_id or ""))[:64]
    if not safe:
        raise ValueError("flow id 为空")
    return _FLOWS_DIR / f"{safe}.json"


def save_flow(body: dict) -> dict:
    """保存录制流程。steps=[{action, selector, text, value, url, secret}]，notes=注意事项。

    落盘同时自动沉淀两样：①context（needs_login/intranet/preferred 自动推断，
    分层路由的依据）②<data/browser_flows/{flow_id}.skill.md>（录制完成自动 skill 化）。
    """
    name = str(body.get("name", "") or "").strip()
    if not name:
        return {"ok": False, "error": "流程名必填"}
    steps = [s for s in (body.get("steps") or []) if isinstance(s, dict) and s.get("action")]
    if not steps:
        return {"ok": False, "error": "步骤为空"}
    # 注意事项（可选）：执行约束/坑提示，随配方落盘，SoulMate 按名调出时当硬约束带回
    notes = str(body.get("notes", "") or "").strip()
    fid_raw = re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:48].strip("_")
    # 中文名洗出来是空/下划线 → 名称哈希做 ID（同名=同 ID=覆盖保存，语义正确）
    fid = fid_raw if (fid_raw and fid_raw.isascii()) else "flow-" + hashlib.md5(name.encode("utf-8")).hexdigest()[:8]
    params = sorted({
        p for s in steps
        for field in ("value", "url")
        for p in re.findall(r"\{\{(\w+)\}\}", str(s.get(field, "") or ""))
    })
    rec = {
        "id": fid,
        "name": name,
        "steps": steps,
        "params": params,
        "notes": notes,
        "context": infer_flow_context(steps),
        "created_at": time.time(),
    }
    _FLOWS_DIR.mkdir(parents=True, exist_ok=True)
    _flow_path(fid).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    # 录制完成自动 skill 化：同名 skill.md 随配方落盘（密码/凭据不落 skill）
    skill_path = _FLOWS_DIR / f"{fid}.skill.md"
    skill_path.write_text(skill_md_for(rec), encoding="utf-8")
    return {"ok": True, "id": fid, "steps": len(steps), "params": params,
            "context": rec["context"], "skill_file": str(skill_path)}


def list_flows() -> list[dict]:
    out: list[dict] = []
    if not _FLOWS_DIR.is_dir():
        return out
    for p in sorted(_FLOWS_DIR.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "id": rec.get("id", p.stem),
                "name": rec.get("name", p.stem),
                "steps": len(rec.get("steps") or []),
                "params": rec.get("params") or [],
                "notes": rec.get("notes", ""),
                "context": rec.get("context") or infer_flow_context(rec.get("steps") or []),
                "skill": (_FLOWS_DIR / f"{p.stem}.skill.md").is_file(),
                "created_at": rec.get("created_at", 0),
            })
        except Exception:
            continue
    return out


def load_flow(flow_id: str) -> dict:
    try:
        p = _flow_path(flow_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if not p.is_file():
        return {"ok": False, "error": f"流程不存在：{flow_id}"}
    rec = json.loads(p.read_text(encoding="utf-8"))
    # 旧配方无 notes 字段时透传空串（前端/SoulMate 消费面拿到的字段形状统一）
    rec.setdefault("notes", "")
    # 旧配方无 context → 现场推断（分层路由对旧流程同样生效）
    rec.setdefault("context", infer_flow_context(rec.get("steps") or []))
    # skill.md 全文随读出（执行计划注入「步骤意图+注意事项」的智能执行指导）
    try:
        rec["skill_md"] = (_FLOWS_DIR / f"{p.stem}.skill.md").read_text(encoding="utf-8")
    except Exception:
        rec["skill_md"] = ""
    return rec


def delete_flow(flow_id: str) -> dict:
    try:
        p = _flow_path(flow_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if p.is_file():
        p.unlink()
        skill_p = _FLOWS_DIR / f"{p.stem}.skill.md"
        if skill_p.is_file():
            skill_p.unlink()  # 配方删除 → 沉淀的 skill 同步清（不留幽灵技能）
        return {"ok": True}
    return {"ok": False, "error": f"流程不存在：{flow_id}"}
