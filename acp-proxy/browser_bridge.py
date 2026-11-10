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

# 任务信箱：task_id -> rec（done=threading.Event，WS 线程与 HTTP 线程共享）
_TASKS: dict = {}
_LOCK = threading.Lock()
_APPS: dict = {}  # session_id -> {"ws": WebSocket, "loop": asyncio.AbstractEventLoop}
_SHOT_DIR = Path.home() / ".hermes" / "soulmate" / "browser_shots"

# 任务执行上限（应用端单动作）；长轮询上限必须大于它，让真实错误先回来如实报因
TASK_TIMEOUT_S = 45.0


def submit_task(action: str, params: dict, session_id: str = "") -> dict:
    """agent 侧投递任务（HTTP 线程调用）。返回 task_id。"""
    tid = uuid.uuid4().hex[:12]
    rec = {
        "task_id": tid, "action": action, "params": params or {},
        "session_id": session_id, "created": time.time(),
        "done": threading.Event(), "ok": False, "result": {}, "error": "",
    }
    with _LOCK:
        _TASKS[tid] = rec
    return {"task_id": tid}


def wait_result(task_id: str, wait_s: float = 30.0) -> dict:
    """长轮询结果（HTTP 线程调用）。超时=如实报因，禁编造。"""
    with _LOCK:
        rec = _TASKS.get(task_id)
    if rec is None:
        return {"ok": False, "error": f"未知任务 {task_id}"}
    if not rec["done"].wait(timeout=wait_s):
        if not app_connected(rec.get("session_id", "")):
            return {"ok": False,
                    "error": "OpenMate 应用未连接（内置浏览器通道无客户端）——确认桌面端在跑"}
        return {"ok": False,
                "error": f"应用执行超时（{wait_s:g}s）——页面卡死或动作过慢，未确认完成"}
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


def _complete(tid: str, ok: bool, result: dict | None = None, error: str = "") -> None:
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
                    _APPS[session_id or "_default"] = {"ws": websocket}
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
        return submit_task(action, params, session_id)

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
    """保存录制流程。steps=[{action, selector, text, value, url, secret}]，notes=注意事项。"""
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
        "created_at": time.time(),
    }
    _FLOWS_DIR.mkdir(parents=True, exist_ok=True)
    _flow_path(fid).write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"ok": True, "id": fid, "steps": len(steps), "params": params}


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
    return rec


def delete_flow(flow_id: str) -> dict:
    try:
        p = _flow_path(flow_id)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    if p.is_file():
        p.unlink()
        return {"ok": True}
    return {"ok": False, "error": f"流程不存在：{flow_id}"}
