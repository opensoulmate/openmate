"""WebSocket 群组讨论端点 — 群消息实时推送

功能:
1. JWT 认证（复用 ws_chat.py 的认证机制）
2. 群组房间管理 — 每个 group_id 一个房间，连接自动加入
3. 消息广播 — 同群组所有客户端实时收到消息
4. 消息持久化 — 存入 OpenSoul 的 discussion_messages 表
5. 支持多种消息类型: user_message, agent_message, system_message,
   discussion_round, task_update, typing

WebSocket 路由: /ws/group/{group_id}?token=xxx

客户端发送格式:
    {"type": "message"|"join"|"leave", "content": "...", "agent_id": "...", "intent": "..."}

服务端推送格式:
    {"type": "user_message"|"agent_message"|..., "data": {...}}
"""

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# 讨论编排器 — 自动化群组 Agent 讨论
from discussion_engine import (
    DiscussionAgent,
    DiscussionOrchestrator,
    DiscussionTask,
)

logger = logging.getLogger("acp-proxy.ws-group")
router = APIRouter()

# 讨论编排器实例 — 全局共享，管理所有群组的讨论任务
_discussion_orchestrator = DiscussionOrchestrator()

# ============================================================
# 数据库配置 — 群组数据在 OpenSoul 的 ai_groups.db
# ============================================================
_OPENSOUL_ROOT = os.environ.get("OPENSOUL_ROOT", "/home/climbing/opensoul")
_OPENSOUL_DB = f"{_OPENSOUL_ROOT}/data/ai_groups.db"


def _get_db():
    """获取 OpenSoul 群组数据库连接"""
    db = sqlite3.connect(_OPENSOUL_DB)
    db.row_factory = sqlite3.Row
    return db


# ============================================================
# JWT 认证 — 与 ws_chat.py 共享同一套密钥
# ============================================================
_OPSOUL_ENV: dict[str, str] = {}
_env_path = f"{_OPENSOUL_ROOT}/.env"
try:
    with open(_env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                _OPSOUL_ENV[k.strip()] = v.strip()
except FileNotFoundError:
    logger.warning(f"OpenSoul .env 未找到: {_env_path}")

JWT_SECRET = _OPSOUL_ENV.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = _OPSOUL_ENV.get("JWT_ALGORITHM", "HS256")


def decode_token(token: str) -> UUID | None:
    """解码 JWT token，返回用户 UUID，失败返回 None"""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if user_id:
            try:
                return UUID(str(user_id))
            except ValueError:
                return UUID(int=int(user_id))
    except Exception as e:
        logger.warning(f"[AUTH] JWT 解码失败: {e}")
    return None


# ============================================================
# 群组房间管理 — 内存中的连接池
# ============================================================

# 结构: {group_id: {websocket: {"user_id": str, "joined_at": float}}}
_group_rooms: dict[str, dict[WebSocket, dict]] = {}


def _get_room_connections(group_id: str) -> dict[WebSocket, dict]:
    """获取群组房间的所有连接，不存在则创建空房间"""
    if group_id not in _group_rooms:
        _group_rooms[group_id] = {}
    return _group_rooms[group_id]


def _remove_from_room(group_id: str, ws: WebSocket):
    """从房间中移除连接，房间空则清理"""
    room = _group_rooms.get(group_id, {})
    room.pop(ws, None)
    if not room:
        _group_rooms.pop(group_id, None)
    logger.info(f"[房间] 群组 {group_id} 当前连接数: {len(room)}")


# ============================================================
# 消息持久化 — 写入 discussion_messages 表
# ============================================================

def _load_group_agents(group_id: str) -> list[DiscussionAgent]:
    """从数据库加载群组的所有 Agent 成员

    Args:
        group_id: 群组ID

    Returns:
        DiscussionAgent 列表
    """
    db = _get_db()
    try:
        cursor = db.execute(
            "SELECT agent_id, name, role, model, temperature "
            "FROM ai_group_agents WHERE group_id = ? AND status = 'online'",
            (group_id,),
        )
        rows = cursor.fetchall()
        return [
            DiscussionAgent(
                agent_id=row["agent_id"],
                name=row["name"],
                role=row["role"],
                model=row["model"] or "",
                temperature=row["temperature"] or 0.7,
            )
            for row in rows
        ]
    except Exception as e:
        logger.error(f"[数据库] 加载群组 {group_id} Agent 失败: {e}")
        return []
    finally:
        db.close()


def _create_discussion_task_db(
    group_id: str,
    goal: str,
    constraints: list,
    completion_criteria: list,
) -> str:
    """在数据库中创建讨论任务记录

    Args:
        group_id: 群组ID
        goal: 任务目标
        constraints: 约束条件
        completion_criteria: 完成标准

    Returns:
        任务ID
    """
    task_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db = _get_db()
    try:
        db.execute(
            """INSERT INTO ai_group_tasks
               (id, group_id, goal, constraints, completion_criteria, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'discussing', ?, ?)""",
            (
                task_id,
                group_id,
                goal,
                json.dumps(constraints, ensure_ascii=False),
                json.dumps(completion_criteria, ensure_ascii=False),
                now,
                now,
            ),
        )
        db.commit()
        logger.info(f"[数据库] 创建讨论任务: {task_id}")
        return task_id
    except Exception as e:
        logger.error(f"[数据库] 创建任务失败: {e}")
        return ""
    finally:
        db.close()


def _persist_message(
    group_id: str,
    agent_id: str,
    agent_name: str,
    content: str,
    intent: str = "comment",
    task_id: str = "",
    round_num: int = 1,
    metadata: dict | None = None,
):
    """将讨论消息持久化到 OpenSoul 的 discussion_messages 表"""
    db = _get_db()
    try:
        msg_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        db.execute(
            """INSERT INTO discussion_messages
               (id, group_id, task_id, agent_id, agent_name, intent, content, metadata, round_num, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                msg_id,
                group_id,
                task_id or f"ws_{group_id}",
                agent_id,
                agent_name,
                intent,
                content,
                json.dumps(metadata or {}, ensure_ascii=False),
                round_num,
                now,
            ),
        )
        db.commit()
        logger.info(f"[持久化] 消息已存入 discussion_messages: {msg_id}")
        return msg_id
    except Exception as e:
        logger.error(f"[持久化] 写入失败: {e}")
        return None
    finally:
        db.close()


# ============================================================
# 广播工具 — 向群组所有连接发送消息
# ============================================================

async def _broadcast(group_id: str, data: dict, exclude: WebSocket | None = None):
    """向群组房间所有客户端广播 JSON 消息，可排除指定连接"""
    room = _group_rooms.get(group_id, {})
    if not room:
        return

    # 构造发送任务列表，跳过排除的连接
    tasks = []
    for ws in room:
        if ws is exclude:
            continue
        tasks.append(_safe_send(ws, data))

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # 清理发送失败的连接
        failed_wss = []
        for ws, result in zip(
            [ws for ws in room if ws is not exclude], results
        ):
            if isinstance(result, Exception):
                failed_wss.append(ws)
        for ws in failed_wss:
            _remove_from_room(group_id, ws)


async def _safe_send(ws: WebSocket, data: dict) -> bool:
    """安全发送 JSON 到 WebSocket，失败返回 False"""
    try:
        await ws.send_json(data)
        return True
    except Exception:
        return False


# ============================================================
# 任务执行事件尾随广播 — task_progress 实时推送
# ============================================================

# 尾随后台任务全局引用（防 GC），事件游标记录已广播的最大事件 id
_task_tail_task: asyncio.Task | None = None
_task_event_cursor: int | None = None
_TASK_EVENT_POLL_INTERVAL = 1.0


def _init_task_event_cursor() -> int:
    """初始化事件游标为当前表最大 id，避免重放历史事件"""
    global _task_event_cursor
    try:
        db = _get_db()
        try:
            row = db.execute(
                "SELECT MAX(id) AS max_id FROM task_execution_events"
            ).fetchone()
            _task_event_cursor = int(row["max_id"] or 0)
        finally:
            db.close()
    except sqlite3.OperationalError:
        # 表可能尚未创建 — 从 0 开始
        _task_event_cursor = 0
    except Exception as e:
        logger.warning(f"[task_progress] 初始化事件游标失败: {e}")
        _task_event_cursor = 0
    return _task_event_cursor


async def _tail_task_events():
    """尾随 task_execution_events 表，把新事件实时广播到对应群组房间

    每 1.0 秒查询一次 id 大于游标的新事件，逐条以 type=task_progress
    广播到该事件所属群组的房间。仅在有房间连接时才查询。
    """
    global _task_event_cursor
    if _task_event_cursor is None:
        _init_task_event_cursor()
    logger.info(f"[task_progress] 事件尾随任务已启动，游标={_task_event_cursor}")
    while True:
        try:
            await asyncio.sleep(_TASK_EVENT_POLL_INTERVAL)
            # 无房间连接时跳过本轮查询，省资源
            if not _group_rooms:
                continue
            cursor = _task_event_cursor or 0
            db = _get_db()
            try:
                rows = db.execute(
                    "SELECT * FROM task_execution_events "
                    "WHERE id > ? ORDER BY id LIMIT 200",
                    (cursor,),
                ).fetchall()
            finally:
                db.close()
            for row in rows:
                _task_event_cursor = int(row["id"])
                await _broadcast(row["group_id"], {
                    "type": "task_progress",
                    "data": {
                        "event_id": row["id"],
                        "task_id": row["task_id"],
                        "agent_id": row["agent_id"],
                        "event_type": row["event_type"],
                        "content": row["content"],
                        "created_at": row["created_at"],
                    },
                })
        except sqlite3.OperationalError as e:
            # 表可能暂时不存在 — 静默跳过不崩
            if "no such table" not in str(e):
                logger.warning(f"[task_progress] 查询事件失败: {e}")
        except asyncio.CancelledError:
            logger.info("[task_progress] 事件尾随任务已取消")
            raise
        except Exception as e:
            logger.warning(f"[task_progress] 事件尾随异常: {e}")


def _ensure_task_tail_running():
    """惰性启动事件尾随任务（已在运行则跳过）"""
    global _task_tail_task
    if _task_tail_task is None or _task_tail_task.done():
        _task_tail_task = asyncio.create_task(_tail_task_events())
        logger.info("[task_progress] 已创建事件尾随后台任务")


# ============================================================
# 获取在线用户列表 — 用于 join/leave 通知
# ============================================================

def _get_online_users(group_id: str) -> list[dict]:
    """获取群组房间内所有在线用户信息"""
    room = _group_rooms.get(group_id, {})
    return [
        {"user_id": info["user_id"], "joined_at": info["joined_at"]}
        for info in room.values()
    ]


# ============================================================
# WebSocket 端点 — /ws/group/{group_id}
# ============================================================

@router.websocket("/ws/group/{group_id}")
async def group_websocket(websocket: WebSocket, group_id: str):
    """群组讨论 WebSocket 端点

    连接流程:
    1. 客户端通过 query param 传递 token
    2. 服务端验证 JWT，失败则关闭连接
    3. 验证通过后加入群组房间
    4. 广播 join 通知给房间内其他客户端
    5. 进入消息循环，接收并广播消息

    消息类型 (客户端发送):
    - message: 普通消息
    - join: 加入通知
    - leave: 离开通知

    消息类型 (服务端推送):
    - user_message: 用户发的消息
    - agent_message: Agent 发的消息
    - system_message: 系统消息
    - discussion_round: 讨论轮次变化
    - task_update: 任务状态更新
    - typing: 正在输入提示
    """
    # --- 第一步: 接受 WebSocket 连接 ---
    await websocket.accept()

    # --- 第二步: JWT 认证 ---
    token = websocket.query_params.get("token", "")
    if not token:
        await _safe_send(websocket, {"type": "error", "message": "缺少认证令牌"})
        await websocket.close(code=4001, reason="缺少认证令牌")
        return

    user_id = decode_token(token)
    if not user_id:
        await _safe_send(websocket, {"type": "error", "message": "认证令牌无效或已过期"})
        await websocket.close(code=4001, reason="认证失败")
        return

    user_id_str = str(user_id)

    # --- 第三步: 加入群组房间 ---
    room = _get_room_connections(group_id)
    room[websocket] = {"user_id": user_id_str, "joined_at": time.time()}

    logger.info(
        f"[连接] 用户 {user_id_str} 加入群组 {group_id}，"
        f"房间当前 {len(room)} 人"
    )

    # 惰性启动任务执行事件尾随广播（首次建房时启动）
    _ensure_task_tail_running()

    # 发送连接成功确认给当前客户端
    await _safe_send(websocket, {
        "type": "connected",
        "group_id": group_id,
        "user_id": user_id_str,
        "online_users": _get_online_users(group_id),
    })

    # 广播 join 通知给房间内其他客户端
    await _broadcast(group_id, {
        "type": "system_message",
        "data": {
            "message": f"用户 {user_id_str[:8]}... 加入了群组",
            "user_id": user_id_str,
            "event": "join",
            "online_users": _get_online_users(group_id),
            "timestamp": time.time(),
        },
    }, exclude=websocket)

    # --- 第四步: 消息循环 ---
    try:
        while True:
            # 接收客户端消息
            raw = await websocket.receive_text()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await _safe_send(websocket, {
                    "type": "error",
                    "message": "消息格式错误，需要 JSON",
                })
                continue

            msg_type = data.get("type", "message")
            content = data.get("content", "").strip()
            agent_id = data.get("agent_id", user_id_str)
            intent = data.get("intent", "comment")

            # --- 处理不同消息类型 ---

            if msg_type == "leave":
                # 客户端主动离开
                logger.info(f"[离开] 用户 {user_id_str} 主动离开群组 {group_id}")
                await _broadcast(group_id, {
                    "type": "system_message",
                    "data": {
                        "message": f"用户 {user_id_str[:8]}... 离开了群组",
                        "user_id": user_id_str,
                        "event": "leave",
                        "online_users": _get_online_users(group_id),
                        "timestamp": time.time(),
                    },
                })
                break

            elif msg_type == "join":
                # 重复 join（已在房间内），只确认
                await _safe_send(websocket, {
                    "type": "system_message",
                    "data": {
                        "message": "已在群组中",
                        "event": "already_joined",
                        "timestamp": time.time(),
                    },
                })

            elif msg_type == "typing":
                # 正在输入提示 — 广播给其他人，不持久化
                await _broadcast(group_id, {
                    "type": "typing",
                    "data": {
                        "user_id": user_id_str,
                        "agent_id": agent_id,
                        "timestamp": time.time(),
                    },
                }, exclude=websocket)

            elif msg_type == "start_discussion":
                # 启动讨论流程 — 创建任务，加载Agent，后台执行编排
                goal = data.get("goal", "").strip()
                if not goal:
                    await _safe_send(websocket, {
                        "type": "error",
                        "message": "start_discussion 缺少 goal 字段",
                    })
                    continue

                # 从数据库加载群组 Agent
                agents = _load_group_agents(group_id)
                if not agents:
                    await _safe_send(websocket, {
                        "type": "error",
                        "message": f"群组 {group_id} 没有在线 Agent，无法启动讨论",
                    })
                    continue

                # 在数据库中创建任务记录
                constraints = data.get("constraints", [])
                completion_criteria = data.get("completion_criteria", [])
                task_id = _create_discussion_task_db(
                    group_id=group_id,
                    goal=goal,
                    constraints=constraints,
                    completion_criteria=completion_criteria,
                )
                if not task_id:
                    await _safe_send(websocket, {
                        "type": "error",
                        "message": "创建讨论任务失败",
                    })
                    continue

                # 构造讨论任务
                task = DiscussionTask(
                    group_id=group_id,
                    task_id=task_id,
                    goal=goal,
                    constraints=constraints,
                    completion_criteria=completion_criteria,
                    total_rounds=data.get("total_rounds", 3),
                )

                # 广播任务创建通知
                await _broadcast(group_id, {
                    "type": "task_update",
                    "data": {
                        "task_id": task_id,
                        "group_id": group_id,
                        "goal": goal,
                        "status": "discussing",
                        "agents": [{"agent_id": a.agent_id, "name": a.name, "role": a.role} for a in agents],
                        "user_id": user_id_str,
                        "timestamp": time.time(),
                    },
                })

                # 后台异步执行讨论 — 不阻塞 WS 消息循环
                asyncio.create_task(
                    _discussion_orchestrator.run(
                        task=task,
                        agents=agents,
                        broadcast_fn=_broadcast,
                        persist_fn=_persist_message,
                    )
                )

                # 立即回复发送者确认
                await _safe_send(websocket, {
                    "type": "message_ack",
                    "data": {
                        "task_id": task_id,
                        "status": "discussion_started",
                        "message": f"讨论已启动，{len(agents)} 个 Agent 参与",
                        "timestamp": time.time(),
                    },
                })
                logger.info(
                    f"[讨论] 群组 {group_id} 讨论已启动: task_id={task_id}, "
                    f"goal={goal}, agents={len(agents)}"
                )

            elif msg_type == "cancel_discussion":
                # 取消讨论 — 支持取消指定任务或群组所有讨论
                target_task_id = data.get("task_id", "")
                if target_task_id:
                    # 取消指定任务
                    success = _discussion_orchestrator.cancel_discussion(target_task_id)
                    if success:
                        await _safe_send(websocket, {
                            "type": "message_ack",
                            "data": {
                                "task_id": target_task_id,
                                "status": "cancel_requested",
                                "message": "取消信号已发送",
                                "timestamp": time.time(),
                            },
                        })
                    else:
                        await _safe_send(websocket, {
                            "type": "error",
                            "message": f"未找到活跃的讨论任务: {target_task_id}",
                        })
                else:
                    # 取消群组所有讨论
                    count = _discussion_orchestrator.cancel_all_discussions(group_id)
                    await _safe_send(websocket, {
                        "type": "message_ack",
                        "data": {
                            "status": "cancel_requested",
                            "message": f"已发送取消信号，共 {count} 个讨论",
                            "timestamp": time.time(),
                        },
                    })

            elif msg_type == "message":
                # 普通消息 — 广播 + 持久化
                if not content:
                    await _safe_send(websocket, {
                        "type": "error",
                        "message": "消息内容不能为空",
                    })
                    continue

                # 判断是用户消息还是 Agent 消息
                is_agent = agent_id != user_id_str
                push_type = "agent_message" if is_agent else "user_message"

                # 持久化到 OpenSoul discussion_messages 表
                msg_id = _persist_message(
                    group_id=group_id,
                    agent_id=agent_id,
                    agent_name=data.get("agent_name", ""),
                    content=content,
                    intent=intent,
                    task_id=data.get("task_id", ""),
                    round_num=data.get("round_num", 1),
                    metadata=data.get("metadata"),
                )

                # 构造广播消息
                broadcast_data = {
                    "type": push_type,
                    "data": {
                        "id": msg_id,
                        "group_id": group_id,
                        "agent_id": agent_id,
                        "agent_name": data.get("agent_name", ""),
                        "content": content,
                        "intent": intent,
                        "task_id": data.get("task_id", ""),
                        "round_num": data.get("round_num", 1),
                        "user_id": user_id_str,
                        "timestamp": time.time(),
                    },
                }

                # 广播给所有客户端（包括发送者）
                await _broadcast(group_id, broadcast_data)

                # 回复发送者确认
                await _safe_send(websocket, {
                    "type": "message_ack",
                    "data": {
                        "id": msg_id,
                        "status": "sent",
                        "timestamp": time.time(),
                    },
                })

            elif msg_type in ("discussion_round", "task_update"):
                # 讨论轮次 / 任务状态变更 — 广播 + 持久化
                _persist_message(
                    group_id=group_id,
                    agent_id=agent_id,
                    agent_name=data.get("agent_name", "系统"),
                    content=content or f"{msg_type} 事件",
                    intent=msg_type,
                    task_id=data.get("task_id", ""),
                    round_num=data.get("round_num", 1),
                    metadata=data.get("metadata"),
                )

                await _broadcast(group_id, {
                    "type": msg_type,
                    "data": {
                        "group_id": group_id,
                        "agent_id": agent_id,
                        "content": content,
                        "task_id": data.get("task_id", ""),
                        "round_num": data.get("round_num", 1),
                        "status": data.get("status", ""),
                        "metadata": data.get("metadata", {}),
                        "user_id": user_id_str,
                        "timestamp": time.time(),
                    },
                })

            else:
                # 未知消息类型
                await _safe_send(websocket, {
                    "type": "error",
                    "message": f"未知消息类型: {msg_type}",
                })

    except WebSocketDisconnect:
        logger.info(f"[断开] 用户 {user_id_str} 从群组 {group_id} 断开连接")
    except Exception as e:
        logger.error(f"[错误] 群组 {group_id} WebSocket 异常: {e}", exc_info=True)
    finally:
        # --- 第五步: 清理 — 从房间移除并广播离开通知 ---
        _remove_from_room(group_id, websocket)
        await _broadcast(group_id, {
            "type": "system_message",
            "data": {
                "message": f"用户 {user_id_str[:8]}... 离开了群组",
                "user_id": user_id_str,
                "event": "leave",
                "online_users": _get_online_users(group_id),
                "timestamp": time.time(),
            },
        })
        logger.info(
            f"[清理] 用户 {user_id_str} 已从群组 {group_id} 移除，"
            f"房间剩余 {len(_group_rooms.get(group_id, {}))} 人"
        )
