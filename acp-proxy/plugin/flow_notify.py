"""
流程结果触达 — 跑完/失败/待审批 → 微信通知（Hermes 微信 gateway 通知通道）+ Reflex 事件流

既有件整合（不重建）：
- 微信通道：`hermes send --to weixin`（Hermes 平台微信 gateway 统一出站，
  复用 gateway 平台凭据；acp-proxy 常驻 python 自带 hermes_cli，直调 -m 入口）。
- Reflex 事件流：flow.completed / flow.failed / flow.waiting-approval / flow.urge
  旁路进 get_reflex().emit（never-raise，/api/reflex/events 可见）。
- 催办（Pulse 语义）：待审批超 interval 未处理 → 微信催办「还在等你审批」。

纪律：
- 通知绝不阻断流程执行（投递在线程里，接口只登记 note 后返回）；
- 通知绝不抛异常（旁路 never-raise）；
- 每条通知落 run.notifications（channel/kind/status/message/message_id），UI 可见送达状态。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import Any, Callable

from plugin.flow_errors import normalize_error

logger = logging.getLogger("plugin.flow_notify")

# 注入点（测试可换桩）：fn(text, subject) -> {"ok": bool, "message_id": str, "error": str}
Sender = Callable[[str, str], dict[str, Any]]
_sender: Sender | None = None

KIND_COMPLETED = "completed"
KIND_FAILED = "failed"
KIND_WAITING = "waiting-approval"
KIND_URGE = "urge"


def set_sender(fn: Sender | None) -> None:
    """测试注入投递桩（None=恢复默认 `hermes send` 投递）"""
    global _sender
    _sender = fn


def _enabled() -> bool:
    if os.environ.get("FLOW_NOTIFY_ENABLED", "1") in ("0", "false", ""):
        return False
    if _sender is None and os.environ.get("PYTEST_CURRENT_TEST"):
        # 测试环境默认不注入投递桩=绝不真发微信（链路验证用 set_sender 桩）
        return False
    return True


def _target() -> str:
    return os.environ.get("FLOW_NOTIFY_TO") or "weixin"


def _urge_interval_s() -> float:
    try:
        return float(os.environ.get("FLOW_URGE_INTERVAL_HOURS") or 12) * 3600
    except ValueError:
        return 12 * 3600


def _default_sender(text: str, subject: str) -> dict[str, Any]:
    """Hermes 微信 gateway 投递：hermes send --to weixin（先 -m 直调，再退二进制）。

    注意：`hermes` 二进制可能跑在缺 aiohttp/cryptography 的解释器上（Weixin
    requirements not met），故优先用本进程解释器（acp-proxy 常驻 venv 自带依赖）。
    """
    target = _target()
    attempts = [
        [sys.executable, "-m", "hermes_cli.main", "send", "--to", target,
         "--json", "--subject", subject, text],
        ["hermes", "send", "--to", target, "--json", "--subject", subject, text],
    ]
    errors: list[str] = []
    for cmd in attempts:
        label = " ".join(cmd[:3])
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                cwd=os.path.expanduser("~"),
            )
        except (OSError, subprocess.SubprocessError) as e:
            errors.append(f"{label}: {type(e).__name__}: {e}")
            continue
        raw = (proc.stdout or "").strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if proc.returncode == 0 and data.get("success") and not data.get("error"):
            return {"ok": True,
                    "message_id": str(data.get("message_id") or ""),
                    "error": ""}
        errors.append(f"{label}: {str(data.get('error') or raw[-120:] or proc.stderr[-120:] or f'exit={proc.returncode}')}")
    return {"ok": False, "message_id": "", "error": " | ".join(errors)[:400]}


def build_message(kind: str, run: Any, *, node: str = "", detail: str = "") -> str:
    """结果触达文案（微信里一眼懂：✅/❌/⏳ + 流程名 + 人话原因 + 去哪处理）"""
    name = run.flow_name or run.flow_id
    rid = run.run_id
    if kind == KIND_COMPLETED:
        secs = ((run.finished_at or time.time()) - run.created_at)
        return (f"✅ 流程「{name}」已完成\n"
                f"耗时 {secs:.1f}s · 实例 {rid}\n"
                f"详情：OpenMate /will 运行实例")
    if kind == KIND_FAILED:
        norm = normalize_error(run.error or detail or "", where=node)
        where = f"（节点 {node}）" if node else ""
        return (f"❌ 流程「{name}」失败{where}\n"
                f"原因：{norm['human']}\n"
                f"建议：{norm['suggestion']}\n"
                f"实例 {rid} · /will 可一键重试/回放")
    if kind == KIND_WAITING:
        form = detail or node or "审批"
        return (f"⏳ 流程「{name}」等你审批\n"
                f"事项：{form}\n"
                f"到 OpenMate /will（或 /tasks 收发台）批准/驳回\n"
                f"实例 {rid}")
    if kind == KIND_URGE:
        return (f"⏰ 催办：流程「{name}」仍在等你审批\n"
                f"事项：{detail or node or '审批'}\n"
                f"已等待较久，尽快处理：OpenMate /will\n"
                f"实例 {rid}")
    return f"流程「{name}」{kind} · 实例 {rid}"


def _reflex_emit(kind: str, run: Any, node: str = "") -> None:
    """Reflex 事件流旁路（never-raise）：flow.* 事件进 /api/reflex/events"""
    try:
        from agent.reflex import get_reflex
        get_reflex().emit(
            f"flow.{kind}",
            source="flow_engine",
            session_id=run.run_id,
            flow_id=run.flow_id, flow_name=run.flow_name,
            status=run.status, node=node,
        )
    except Exception:  # noqa: BLE001 — 应激旁路绝不阻断
        pass


def notify_run_event(engine: Any, run: Any, kind: str, *,
                     node: str = "", detail: str = "",
                     dedupe_key: str = "") -> dict[str, Any] | None:
    """登记通知 note 并异步投递（微信 + Reflex）。返回 note（disabled/去重时 None）。

    - 绝不抛异常、绝不阻断流程（投递在后台线程）；
    - 去重按 (kind, dedupe_key)：同一状态迁移绝不重复触达；重试后再失败是新迁移
      （dedupe_key=finished_at/审批 created_at），催办（urge）不去重按间隔发。
    """
    try:
        if not _enabled():
            return None
        if kind != KIND_URGE:
            for note in run.notifications:
                if note.get("kind") == kind and note.get("dedupe_key") == dedupe_key:
                    return None   # 同一迁移已触达（成功/在途/失败都算），不重复
        message = build_message(kind, run, node=node, detail=detail)
        note = {
            "id": uuid.uuid4().hex[:10],
            "channel": f"wechat:{_target()}",
            "kind": kind,
            "message": message,
            "ts": time.time(),
            "status": "sending",
            "message_id": "",
            "run_id": run.run_id,
            "dedupe_key": dedupe_key,
            "attempts": 1,
        }
        run.notifications.append(note)
        try:
            engine.store.save(run)
        except Exception:  # noqa: BLE001
            pass
        _reflex_emit(kind, run, node=node)
        thread = threading.Thread(
            target=_deliver, args=(engine, run.run_id, note["id"], message, kind),
            daemon=True, name=f"flow-notify-{run.run_id}",
        )
        thread.start()
        return note
    except Exception as e:  # noqa: BLE001 — 触达旁路绝不阻断主流程
        logger.warning("流程通知登记失败: %s", e)
        return None


def _deliver(engine: Any, run_id: str, note_id: str,
             message: str, kind: str) -> None:
    """后台投递：微信通道发送 → 回写 note 送达状态（幂等 best-effort）。

    iLink 平台约束：对方发来消息前 bot 主动发会被拒（session not ready）——
    这类失败标 deferred 交 retry_deferred 定期补投，窗口一开即送达。
    """
    sender = _sender or _default_sender
    subject = {"completed": "[流程] 完成", "failed": "[流程] 失败",
               "waiting-approval": "[流程] 待审批", "urge": "[流程] 催办"}.get(
                   kind, "[流程]")
    try:
        result = sender(message, subject)
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "message_id": "", "error": f"{type(e).__name__}: {e}"}
    error_text = str(result.get("error") or "")
    if result.get("ok"):
        status = "delivered"
    elif "session not ready" in error_text or "prepare failed" in error_text:
        status = f"deferred: {error_text[:160]}"
    else:
        status = f"error: {error_text[:200]}"
    try:
        run = engine.store.find_run(run_id)
        if run is None:
            return
        for note in run.notifications:
            if note.get("id") == note_id:
                note["status"] = status
                note["message_id"] = str(result.get("message_id") or "")
                break
        engine.store.save(run)
    except Exception as e:  # noqa: BLE001
        logger.warning("通知送达状态回写失败 %s: %s", run_id, e)
    if result.get("ok"):
        logger.info("流程通知已投递微信: %s (%s)", run_id, kind)
    else:
        logger.warning("流程通知投递失败 %s (%s): %s", run_id, kind, status)


def retry_deferred(engine: Any, now: float | None = None,
                   interval_s: float = 600.0, max_attempts: int = 6) -> int:
    """补投 deferred 通知（iLink 发送窗口一开就送达）：间隔去重 + 尝试上限。"""
    if not _enabled():
        return 0
    now = now or time.time()
    sent = 0
    for run in engine.store.list_runs(limit=200):
        dirty = False
        for note in run.notifications:
            if not str(note.get("status") or "").startswith("deferred"):
                continue
            attempts = int(note.get("attempts") or 1)
            last = float(note.get("ts") or 0)
            if attempts >= max_attempts:
                if not str(note.get("status")).startswith("error"):
                    note["status"] = "error: 超出补投次数上限（等对方发来消息后可回放重触达）"
                    dirty = True
                continue
            if now - last < interval_s:
                continue
            note["ts"] = now
            note["attempts"] = attempts + 1
            note["status"] = "sending"
            dirty = True
            try:
                engine.store.save(run)   # 先落盘再投递：投递线程回写不丢 attempts
            except Exception:  # noqa: BLE001
                pass
            thread = threading.Thread(
                target=_deliver, args=(engine, run.run_id, note["id"],
                                       note.get("message") or "", note.get("kind") or ""),
                daemon=True, name=f"flow-notify-retry-{run.run_id}")
            thread.start()
            sent += 1
        if dirty:
            try:
                engine.store.save(run)
            except Exception:  # noqa: BLE001
                pass
    return sent


def urge_pending(engine: Any, now: float | None = None) -> int:
    """催办（Pulse 语义）：待审批超 interval 未处理 → 微信催办一次（间隔去重）。"""
    if not _enabled():
        return 0
    now = now or time.time()
    interval = _urge_interval_s()
    sent = 0
    for run in engine.store.list_runs(status="waiting-approval", limit=200):
        approval = run.approval
        if approval is None or approval.status != "pending":
            continue
        last = max(
            [float(n.get("ts") or 0) for n in run.notifications
             if n.get("kind") == KIND_URGE] or [approval.created_at],
        )
        if now - last < interval:
            continue
        node = run.nodes.get(approval.node_id)
        if notify_run_event(engine, run, KIND_URGE, node=approval.node_id,
                            detail=node.output_summary if node else ""):
            sent += 1
    return sent
