"""A2A JSON-RPC 2.0 路由模块 — 对齐 A2A v1.0 规范。

在FastAPI app上挂载路由，提供：
- POST /a2a (旧路径兼容) + POST /rpc/a2a (规范路径)
- ws://8092/ws/a2a WebSocket长连接
- a2a/task/delegate, a2a/task/result, a2a/task/cancel, a2a/artifact/sync, a2a/agent/heartbeat
- a2a/event 统一事件广播
- AgentCard well-known 端点
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from models import (
    A2A_INVALID_TASK_STATE,
    A2A_TASK_NOT_CANCELABLE,
    A2A_TASK_NOT_FOUND,
    A2A_UNSUPPORTED_OPERATION,
    Artifact,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
    JSONRPCRequest,
    JSONRPCResponse,
    JSONRPCError,
    Message,
    TaskState,
    TextPart,
)
from sse_events import (
    A2ACompletedEvent,
    A2AErrorEvent,
    MessageAppendedEvent,
    NewArtifactEvent,
    TaskStatusChangedEvent,
)
from stream_manager import get_stream_manager
from bridge import get_bridge
from task_store import TaskStore
from agent_card import get_agent_card, list_agent_cards
from security import verify_auth
from push_notify import (
    get_push_config,
    push_task_event,
    register_push_config,
    remove_push_config,
)
from logger import log_error, log_request, log_response, log_task_event

logger = logging.getLogger("a2a.server")

# 全局TaskStore实例（惰性初始化）
_task_store: TaskStore | None = None


def get_task_store() -> TaskStore:
    """获取全局TaskStore单例。"""
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


# ---------------------------------------------------------------------------
# 路由器
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/a2a", tags=["A2A"])
# 规范路径 /rpc/a2a 的别名路由器
rpc_router = APIRouter(prefix="/rpc/a2a", tags=["A2A-RPC"])


def _error_response(request_id: Any, code: int, message: str,
                    data: Any = None) -> JSONResponse:
    """构造JSON-RPC错误响应。"""
    resp = JSONRPCResponse(
        id=request_id if request_id is not None else 0,
        error=JSONRPCError(code=code, message=message, data=data),
    )
    return JSONResponse(content=resp.model_dump(), status_code=200)


def _success_response(request_id: Any, result: Any) -> JSONResponse:
    """构造JSON-RPC成功响应。"""
    resp = JSONRPCResponse(id=request_id, result=result)
    return JSONResponse(content=resp.model_dump(exclude_none=True))


# ---------------------------------------------------------------------------
# JSON-RPC 方法处理器
# ---------------------------------------------------------------------------

async def _handle_task_delegate(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 a2a/task/delegate 方法：任务委派（A2A v1.0核心方法）。

    主Agent向子Agent下发可追溯子任务，支持父任务依赖、超时控制、上下文传递。
    向后兼容旧方法名 tasks/send。
    """
    message_data = params.get("message")
    if not message_data:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 message 参数")

    session_id = params.get("sessionId")
    metadata = params.get("metadata") or {}

    # 兼容字符串和对象格式
    if isinstance(message_data, str):
        msg = Message(role="user", parts=[TextPart(text=message_data)])
    else:
        msg = Message(**message_data)

    # 注入A2A v1.0规范字段到metadata
    metadata["task_id"] = params.get("task_id", metadata.get("task_id"))
    metadata["parent_task_id"] = params.get("parent_task_id")
    metadata["target_agent"] = params.get("target_agent")
    metadata["task_type"] = params.get("task_type", "execute")
    metadata["timeout"] = params.get("timeout", 30000)
    metadata["require_result"] = params.get("require_result", True)
    metadata["trace_id"] = params.get("trace_id")

    store = get_task_store()
    task = await store.create_task(
        session_id=session_id,
        initial_message=msg,
        metadata=metadata,
    )

    # 启动Agent任务（target_agent 分流，2026-10-23）：
    # evo=确定性处理端（evo_agent）内联执行并同步返回 result_data（require_result
    # 语义，即时动作无需轮询）；其余照旧走 bridge→Agent Engine（8787 ACP）。
    # Agent↔Agent 业务数据只走 A2A 链路（三层协议隔离）。
    if params.get("target_agent") == "evo":
        reply = await _run_evo_task(task.id, msg, params.get("task_type", "query_status"))
        return _success_response(request_id, {
            "task_id": task.id,
            "status": "completed",
            "result_data": {"reply": reply},
            "accepted_at": int(asyncio.get_event_loop().time()),
        })

    # 启动后台Agent任务
    asyncio.create_task(_agent_task_worker(task.id, msg))

    return _success_response(request_id, {
        "task_id": task.id,
        "status": "pending",
        "accepted_at": int(asyncio.get_event_loop().time()),
    })


async def _handle_task_result(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 a2a/task/result 方法：子任务结果回传（A2A v1.0）。

    子Agent执行完成后回传结果、产物、日志、异常信息。
    向后兼容旧方法名 tasks/get（查询模式）。
    """
    # 如果有result_data，是结果回传模式
    if "result_data" in params or "status" in params:
        task_id = params.get("task_id")
        if not task_id:
            return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 task_id 参数")

        store = get_task_store()
        task = await store.get_task(task_id)
        if not task:
            return _error_response(request_id, A2A_TASK_NOT_FOUND, f"Task不存在: {task_id}")

        # 更新任务状态和结果
        status_str = params.get("status", "success")
        result_data = params.get("result_data", {})
        artifact_list = params.get("artifact_list", [])
        error_msg = params.get("error_msg", "")
        cost_time = params.get("cost_time", 0)

        new_state = TaskState.COMPLETED if status_str == "success" else TaskState.FAILED
        task = await store.update_task_status(task_id, new_state)

        return _success_response(request_id, {
            "task_id": task_id,
            "status": status_str,
            "result_data": result_data,
            "artifact_list": artifact_list,
            "cost_time": cost_time,
        })

    # 查询模式（兼容 tasks/get）
    task_id = params.get("task_id") or params.get("taskId")
    if not task_id:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 task_id 参数")

    store = get_task_store()
    task = await store.get_task(task_id)
    if not task:
        return _error_response(request_id, A2A_TASK_NOT_FOUND, f"Task不存在: {task_id}")

    history_length = params.get("historyLength")
    if history_length is not None and isinstance(history_length, int) and history_length >= 0:
        task.history = task.history[-history_length:]

    return _success_response(request_id, task.model_dump(exclude_none=True))


async def _handle_task_cancel(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 a2a/task/cancel 方法：任务取消（A2A v1.0）。

    向后兼容旧方法名 tasks/cancel。
    """
    task_id = params.get("task_id") or params.get("taskId")
    if not task_id:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 task_id 参数")

    store = get_task_store()
    task = await store.get_task(task_id)
    if not task:
        return _error_response(request_id, A2A_TASK_NOT_FOUND, f"Task不存在: {task_id}")

    if task.status.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELED, TaskState.REJECTED):
        return _error_response(request_id, A2A_TASK_NOT_CANCELABLE,
                               f"Task处于终态 {task.status.state.value}，无法取消")

    try:
        task = await store.update_task_status(task_id, TaskState.CANCELED)
    except ValueError as e:
        return _error_response(request_id, A2A_INVALID_TASK_STATE, str(e))

    return _success_response(request_id, {"task_id": task_id, "status": "canceled"})


async def _handle_artifact_sync(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 a2a/artifact/sync 方法：跨Agent工件同步（A2A v1.0）。

    文档、代码、报表、结构化产物跨Agent共享。
    """
    artifact_id = params.get("artifact_id")
    if not artifact_id:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 artifact_id 参数")

    artifact_type = params.get("artifact_type", "document")
    content = params.get("content", "")
    meta = params.get("meta", {})
    target_agents = params.get("target_agent_list", [])

    # 广播工件更新事件
    stream = get_stream_manager()
    event_data = {
        "artifact_id": artifact_id,
        "artifact_type": artifact_type,
        "meta": meta,
        "target_agents": target_agents,
    }
    await stream.broadcast_event("artifact.update", event_data)

    return _success_response(request_id, {
        "artifact_id": artifact_id,
        "synced": True,
        "targets": target_agents,
    })


async def _handle_agent_heartbeat(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 a2a/agent/heartbeat 方法：节点心跳保活（A2A v1.0）。

    集群在线感知、负载探测、异常节点剔除。
    支持自注册：新Agent发心跳自动注册AgentCard。
    """
    agent_id = params.get("agent_id")
    if not agent_id:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 agent_id 参数")

    status = params.get("status", "online")
    load = params.get("load", 0.0)
    support_methods = params.get("support_methods", [])
    agent_name = params.get("name", agent_id)
    agent_description = params.get("description", f"{agent_name} Agent")
    agent_skills = params.get("skills", [])

    # 自注册：如果AgentCard不存在，自动创建
    from agent_card import get_agent_card, _CARD_CACHE, _build_card, _build_skill
    if not get_agent_card(agent_id):
        logger.info(f"Auto-registering new agent: {agent_id}")
        skills = []
        for s in agent_skills:
            if isinstance(s, dict):
                skills.append(_build_skill(
                    s.get("id", "general"),
                    s.get("name", "通用能力"),
                    s.get("description", ""),
                    s.get("tags", []),
                ))
        if not skills:
            skills = [_build_skill("general", "通用能力", f"{agent_name} 的通用任务处理能力", ["general"])]

        from models import AgentCard, AgentCapabilities
        _CARD_CACHE[agent_id] = AgentCard(
            name=agent_name,
            description=agent_description,
            version=params.get("version", "1.0.0"),
            url=f"/a2a/{agent_id}",
            capabilities=AgentCapabilities(
                streaming=True,
                pushNotifications=False,
                stateTransitionHistory=True,
            ),
            skills=skills,
            defaultInputModes=["text/plain", "application/json"],
            defaultOutputModes=["text/plain", "application/json"],
        )

    # 广播agent状态事件
    stream = get_stream_manager()
    await stream.broadcast_event("agent.status", {
        "agent_id": agent_id,
        "status": status,
        "load": load,
        "support_methods": support_methods,
    })

    return _success_response(request_id, {
        "agent_id": agent_id,
        "status": status,
        "acknowledged": True,
    })


async def _run_evo_task(task_id: str, message: Message | None, task_type: str) -> str:
    """evo 任务：确定性处理（目标收件箱/状态应答），不经过 Agent Engine。

    状态机照走（working→completed/failed），应答落 task 历史（全链路溯源），
    并同步返回应答文本。evo 的处理是即时确定性动作，调用方无需轮询。
    """
    from models import TaskState

    from evo_agent import handle as evo_handle

    store = get_task_store()
    try:
        await store.update_task_status(task_id, TaskState.WORKING)
        text = ""
        if message and message.parts:
            text = "".join(getattr(p, "text", "") or "" for p in message.parts)
        reply = await evo_handle(task_type, text)
        reply_msg = Message(role="agent", parts=[TextPart(text=reply)])
        await store.add_message(task_id, reply_msg)
        await store.update_task_status(task_id, TaskState.COMPLETED, status_message=reply_msg)
        return reply
    except Exception as e:  # noqa: BLE001
        logger.error(f"[evo] task {task_id} 处理失败: {e}")
        fail_msg = Message(role="agent", parts=[TextPart(text=f"evo 处理失败: {e}")])
        try:
            await store.update_task_status(task_id, TaskState.FAILED, status_message=fail_msg)
        except Exception:  # noqa: BLE001
            pass
        return f"evo 处理失败: {e}"


async def _agent_task_worker(task_id: str, message: Message | None) -> None:
    """后台Agent任务执行worker — 通过bridge调用Agent Engine。

    连接Agent Engine(端口8787)的ACP WebSocket，将A2A任务转发给内置Agent，
    流式接收结果并通过stream_manager广播A2A SSE事件。
    """
    store = get_task_store()
    stream = get_stream_manager()
    bridge = get_bridge()

    await bridge.run_task(task_id, message, store, stream)


async def _handle_tasks_send_subscribe(params: dict[str, Any], request_id: Any) -> EventSourceResponse:
    """处理 tasks/sendSubscribe 方法：SSE流式订阅。

    创建Task后启动后台Agent任务，返回SSE事件流。
    客户端通过SSE接收状态变更、消息追加、Artifact产出等事件。
    """
    task_id = params.get("taskId")
    message_data = params.get("message")
    session_id = params.get("sessionId")
    metadata = params.get("metadata")

    store = get_task_store()
    stream = get_stream_manager()

    # 创建或获取Task
    if task_id:
        task = await store.get_task(task_id)
        if not task:
            # Task不存在，创建新的
            initial_msg = Message(**message_data) if message_data else None
            task = await store.create_task(
                session_id=session_id,
                initial_message=initial_msg,
                metadata=metadata,
            )
            task_id = task.id
        else:
            # Task存在，追加消息
            if message_data:
                msg = Message(**message_data)
                await store.add_message(task_id, msg)
    else:
        # 创建新Task
        initial_msg = Message(**message_data) if message_data else None
        task = await store.create_task(
            session_id=session_id,
            initial_message=initial_msg,
            metadata=metadata,
        )
        task_id = task.id

    # 订阅SSE事件流
    queue = stream.subscribe(task_id)

    # 启动后台Agent任务
    msg = Message(**message_data) if message_data else None
    asyncio.create_task(_agent_task_worker(task_id, msg))

    async def event_generator() -> AsyncGenerator:
        """SSE事件生成器。"""
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield {
                    "event": event.type,
                    "data": json.dumps(event.model_dump(), ensure_ascii=False),
                }
        finally:
            stream.unsubscribe(task_id, queue)

    return EventSourceResponse(event_generator())


async def _handle_tasks_transition(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 tasks/transition 方法：转换Task状态。

    params:
        taskId: str
        state: str - 目标状态
        message: Message (可选) - 状态变更附加消息
    """
    task_id = params.get("taskId")
    state_str = params.get("state")

    if not task_id or not state_str:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 taskId 或 state 参数")

    try:
        target_state = TaskState(state_str)
    except ValueError:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, f"无效的状态值: {state_str}")

    message_data = params.get("message")
    status_message = Message(**message_data) if message_data else None

    store = get_task_store()
    try:
        task = await store.update_task_status(task_id, target_state, status_message)
    except ValueError as e:
        return _error_response(request_id, A2A_INVALID_TASK_STATE, str(e))

    return _success_response(request_id, task.model_dump(exclude_none=True))


async def _handle_push_notification_set(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 tasks/pushNotification/set 方法：注册推送通知配置。

    params:
        taskId: str - Task ID
        url: str - 回调URL
        token: str (可选) - 回调鉴权Token
    """
    task_id = params.get("taskId")
    url = params.get("url")

    if not task_id:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 taskId 参数")
    if not url:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 url 参数")

    store = get_task_store()
    task = await store.get_task(task_id)
    if not task:
        return _error_response(request_id, A2A_TASK_NOT_FOUND, f"Task不存在: {task_id}")

    config: dict[str, Any] = {"url": url}
    if params.get("token"):
        config["token"] = params["token"]
    register_push_config(task_id, config)

    return _success_response(request_id, {"taskId": task_id, "url": url, "registered": True})


async def _handle_push_notification_get(params: dict[str, Any], request_id: Any) -> JSONResponse:
    """处理 tasks/pushNotification/get 方法：查询推送通知配置。

    params:
        taskId: str - Task ID
    """
    task_id = params.get("taskId")
    if not task_id:
        return _error_response(request_id, JSONRPC_INVALID_PARAMS, "缺少 taskId 参数")

    config = get_push_config(task_id)
    if not config:
        return _success_response(request_id, {"taskId": task_id, "registered": False})

    # 返回时隐藏token
    return _success_response(request_id, {
        "taskId": task_id,
        "url": config.get("url"),
        "registered": True,
    })


# ---------------------------------------------------------------------------
# 方法分发表
# ---------------------------------------------------------------------------

_METHOD_HANDLERS: dict[str, Any] = {
    # A2A v1.0 规范方法名
    "a2a/task/delegate": _handle_task_delegate,
    "a2a/task/result": _handle_task_result,
    "a2a/task/cancel": _handle_task_cancel,
    "a2a/artifact/sync": _handle_artifact_sync,
    "a2a/agent/heartbeat": _handle_agent_heartbeat,
    # 向后兼容旧方法名
    "tasks/send": _handle_task_delegate,
    "tasks/get": _handle_task_result,
    "tasks/cancel": _handle_task_cancel,
    "tasks/transition": _handle_tasks_transition,
    "tasks/sendSubscribe": _handle_tasks_send_subscribe,
    "tasks/pushNotification/set": _handle_push_notification_set,
    "tasks/pushNotification/get": _handle_push_notification_get,
}


# ---------------------------------------------------------------------------
# JSON-RPC 统一入口
# ---------------------------------------------------------------------------

@router.post("")
async def jsonrpc_endpoint(request: Request):
    """A2A JSON-RPC 2.0 统一入口。

    接收JSON-RPC请求，分发到对应的处理器。
    所有A2A方法通过 method 字段区分。
    tasks/sendSubscribe 返回 EventSourceResponse（SSE流），其余返回 JSONResponse。
    """
    # 鉴权（默认跳过，A2A_SKIP_AUTH=true）
    auth_err = await verify_auth(request)
    if auth_err:
        return auth_err

    try:
        body = await request.json()
    except Exception:
        return _error_response(None, JSONRPC_PARSE_ERROR, "JSON解析失败")

    # 支持批量请求
    if isinstance(body, list):
        # 批量JSON-RPC（暂不实现，返回错误）
        return _error_response(None, JSONRPC_INVALID_REQUEST, "暂不支持批量JSON-RPC请求")

    # 解析单个请求
    try:
        rpc_req = JSONRPCRequest(**body)
    except Exception as e:
        return _error_response(body.get("id"), JSONRPC_INVALID_REQUEST, f"无效的请求格式: {e}")

    method = rpc_req.method
    params = rpc_req.params or {}

    handler = _METHOD_HANDLERS.get(method)
    if not handler:
        return _error_response(
            rpc_req.id, JSONRPC_METHOD_NOT_FOUND,
            f"未知方法: {method}。支持的方法: {list(_METHOD_HANDLERS.keys())}",
        )

    # 全链路日志：请求入口 + 计时
    client_ip = request.client.host if request.client else ""
    timer = log_request(method, rpc_req.id, client_ip)

    try:
        result = await handler(params, rpc_req.id)
        log_response(method, rpc_req.id, timer, status="ok")
        return result
    except Exception as e:
        log_error(f"A2A方法执行异常: {method}", e)
        log_response(method, rpc_req.id, timer, status="error", error_code=-32603)
        return _error_response(rpc_req.id, -32603, f"内部错误: {e}")


# ---------------------------------------------------------------------------
# AgentCard 端点
# ---------------------------------------------------------------------------

@router.get("/agents")
async def list_agents() -> JSONResponse:
    """列出所有已注册的AgentCard。"""
    cards = list_agent_cards()
    return JSONResponse(content=[c.model_dump() for c in cards])


@router.get("/agents/{name}")
async def get_agent(name: str) -> JSONResponse:
    """根据名称获取单个AgentCard。"""
    card = get_agent_card(name)
    if not card:
        return JSONResponse(
            content={"error": f"Agent不存在: {name}"},
            status_code=404,
        )
    return JSONResponse(content=card.model_dump())


# ---------------------------------------------------------------------------
# /rpc/a2a 规范路径端点（复用同一个处理器）
# ---------------------------------------------------------------------------

@rpc_router.post("")
async def rpc_jsonrpc_endpoint(request: Request):
    """A2A JSON-RPC 2.0 规范路径入口 (/rpc/a2a)。

    与 /a2a 共用同一套方法处理器，只是路由前缀不同。
    """
    return await jsonrpc_endpoint(request)
# ---------------------------------------------------------------------------

well_known_router = APIRouter(tags=["A2A-WellKnown"])


@well_known_router.get("/.well-known/agent.json")
async def well_known_agent_card(agent: str | None = None) -> JSONResponse:
    """标准 well-known AgentCard 端点。

    不带参数：返回所有已注册AgentCard列表。
    ?agent=xxx：返回指定Agent的AgentCard。
    """
    if agent:
        card = get_agent_card(agent)
        if not card:
            return JSONResponse(content={"error": f"Agent '{agent}' not found"}, status_code=404)
        return JSONResponse(content=card.model_dump())
    # 返回所有
    cards = list_agent_cards()
    return JSONResponse(content=[c.model_dump() for c in cards])
