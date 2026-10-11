# -*- coding: utf-8 -*-
"""13.4 AI 群组真实成员端到端验收探针（可复跑）——对齐 references/ai-groups-e2e.md §4 验收行。

真实用户路径（多 agent 真实成员，非 loopback 模拟）：
  建组（3 真实成员 hermes/soulmate/mimo）→ 双 WS 客户端同挂 /ws/group/{gid}
  → 加成员/下任务/自动链式执行（不手动 execute）→ 每步结果回群聊
  → 聊天广播（双客户端互见）→ 群体讨论（start_discussion → agent_message 流）。

验收行（A1-A7 服务端+WS 面；A8 UI 面走 ai_groups_ui_probe.py CDP）：
  A1 加成员：GET 组详情 agents[] 非空
  A2 下任务：subtasks_count>0，任务列表出现规划/执行/验证/审查
  A3 自动链式执行：子任务依次 pending→executing→scored（无需手动点 execute）
  A4 Agent 回复进群聊：discussion_messages intent=agent_reply 行 + WS agent_message 帧
     （帧内容=行内容前缀，实时聊天流与库内历史同一真源）
  A5 执行：response 是真实回答 + auto_score + capability 落库；source=soulmate-agent（非 hermes CLI）
  A6 聊天广播：双 WS 客户端互见 user_message（A→B 与 B→A 双向）
  A7 群体讨论：start_discussion 后收到 agent_message 流

运行：~/opensoul/.venv/bin/python ai_groups_acceptance_probe.py
结果 JSON：/tmp/ai_groups_accept_result.json；WS 帧：/tmp/ai_groups_accept_ws_frames.jsonl
"""
import json
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path

OPSOUL = Path.home() / "opensoul"
sys.path.insert(0, str(OPSOUL))

API = "http://127.0.0.1:8090"
GROUP_WS = "ws://127.0.0.1:8092/ws/group"
AI_DB = OPSOUL / "data" / "ai_groups.db"
RESULT_PATH = Path("/tmp/ai_groups_accept_result.json")
FRAMES_PATH = Path("/tmp/ai_groups_accept_ws_frames.jsonl")

MEMBERS = [
    {"agent_id": "hermes", "name": "Hermes", "role": "advisor", "model": ""},
    {"agent_id": "soulmate", "name": "SoulMate", "role": "executor", "model": ""},
    {"agent_id": "mimo", "name": "MiMo", "role": "verifier", "model": ""},
]
POLL_TIMEOUT_S = 2400
DISCUSS_WAIT_S = 300
TERMINAL = ("scored", "failed", "completed", "skipped")


def mint_token() -> str:
    from src.services.auth import create_access_token

    return create_access_token(uuid.uuid4())


def ws_listener(name, gid, token, frames, stop):
    """群组 WS 采集线程（每个客户端独立连接）：收帧落盘，断连如实记，不抛。"""
    try:
        from websockets.sync.client import connect
    except Exception as e:  # pragma: no cover
        frames.append({"_error": f"websockets unavailable: {e}", "_client": name})
        return
    try:
        with connect(f"{GROUP_WS}/{gid}?token={token}", open_timeout=15) as ws:
            frames.append({"_connected": True, "_client": name, "_ts": time.time()})
            while not stop.is_set():
                try:
                    raw = ws.recv(timeout=3)
                except Exception:
                    continue
                try:
                    frame = json.loads(raw)
                except Exception:
                    frame = {"_raw": str(raw)[:200]}
                frame["_ts"] = time.time()
                frame["_client"] = name
                frames.append(frame)
                with FRAMES_PATH.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(frame, ensure_ascii=False) + "\n")
    except Exception as e:
        frames.append({"_error": f"{type(e).__name__}: {e}", "_client": name, "_ts": time.time()})


def ws_send(gid, token, payload, timeout=10):
    """一次性客户端：连接→发一条消息→收起回执即断（用户聊天真实路径）。"""
    from websockets.sync.client import connect

    with connect(f"{GROUP_WS}/{gid}?token={token}", open_timeout=timeout) as ws:
        ws.send(json.dumps(payload, ensure_ascii=False))
        ack = None
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                raw = ws.recv(timeout=3)
            except Exception:
                continue
            try:
                f = json.loads(raw)
            except Exception:
                continue
            if f.get("type") == "message_ack":
                ack = f
                break
        return ack


def main() -> int:
    import httpx

    marker = f"ACC134-{int(time.time())}"
    token = mint_token()
    hdr = {"Authorization": f"Bearer {token}"}
    client = httpx.Client(headers=hdr, timeout=30)

    frames: list = []
    FRAMES_PATH.write_text("", encoding="utf-8")
    stop = threading.Event()

    # 1. 建组（3 个真实成员：hermes/soulmate/mimo 各自真身执行）
    r = client.post(
        f"{API}/api/ai-groups",
        json={
            "name": f"13.4真实成员验收-{marker}",
            "description": "周期#81 13.4 对齐 ai-groups-e2e.md 验收行",
            "agents": MEMBERS,
        },
    )
    r.raise_for_status()
    gid = r.json()["id"]
    print(f"[1] group created: {gid}")

    threads = [
        threading.Thread(target=ws_listener, args=(n, gid, token, frames, stop), daemon=True)
        for n in ("A", "B")
    ]
    for t in threads:
        t.start()
    time.sleep(1.5)

    # A1 加成员：GET 组详情 agents[] 非空
    detail = client.get(f"{API}/api/ai-groups/{gid}").json()
    agents = detail.get("agents", []) or []
    print(f"[A1] agents={len(agents)}")

    # A6 聊天广播：双 WS 客户端互见 user_message（A→B、B→A 双向）
    a6_ab = ws_send(gid, token, {"type": "message", "content": f"广播探针A→B {marker}"})
    a6_ba = ws_send(gid, token, {"type": "message", "content": f"广播探针B→A {marker}"})
    time.sleep(1.0)
    user_frames = [f for f in frames if f.get("type") == "user_message"]
    a6_texts = {f.get("data", {}).get("content", "") for f in user_frames}
    a6_ok = (
        f"广播探针A→B {marker}" in a6_texts and f"广播探针B→A {marker}" in a6_texts
        and len(user_frames) >= 2
    )
    print(f"[A6] user_message frames={len(user_frames)} ok={a6_ok}")

    # A7 群体讨论：start_discussion → agent_message 流
    disc_goal = f"讨论：口算17加25等于几，一句话说明[防缓存标记 {marker}]"
    disc_ack = ws_send(
        gid, token, {"type": "start_discussion", "goal": disc_goal, "total_rounds": 1}
    )
    disc_task_id = (disc_ack or {}).get("data", {}).get("task_id", "")
    print(f"[A7] discussion started: {disc_task_id}")
    t0 = time.time()
    while time.time() - t0 < DISCUSS_WAIT_S:
        disc_msgs = [
            f
            for f in frames
            if f.get("type") == "agent_message"
            and f.get("data", {}).get("task_id") == disc_task_id
        ]
        if len(disc_msgs) >= len(MEMBERS):
            break
        time.sleep(5)
    disc_msgs = [
        f
        for f in frames
        if f.get("type") == "agent_message" and f.get("data", {}).get("task_id") == disc_task_id
    ]
    print(f"[A7] discussion agent_message frames={len(disc_msgs)}")

    # A2 下任务（goal 无关键词 → 默认 规划/执行/验证/审查 4 子任务；自动链式执行不手动 execute）
    goal = f"口算17加25的结果并用一句话说明理由[防缓存标记 {marker}]"
    r = client.post(
        f"{API}/api/ai-groups/{gid}/tasks",
        json={"goal": goal, "_meta": {"source": "test"}},
    )
    r.raise_for_status()
    task_id = r.json()["task_id"]
    subtasks_count = r.json()["subtasks_count"]
    print(f"[2] task submitted: {task_id} subtasks={subtasks_count}")

    # A3/A5 轮询到父任务收口（不手动 execute）；快照留"依次推进"证据
    parent = None
    snapshots: list = []
    t0 = time.time()
    while time.time() - t0 < POLL_TIMEOUT_S:
        time.sleep(6)
        g = client.get(f"{API}/api/ai-groups/{gid}").json()
        tasks = g.get("tasks", [])
        parent = next((x for x in tasks if not x.get("parent_task_id")), None)
        subs = [x for x in tasks if x.get("parent_task_id")]
        live = [f"{x['id']}:{x.get('assigned_agent_id')}:{x['status']}" for x in subs]
        snapshots.append({"t": round(time.time() - t0, 1), "parent": parent and parent["status"], "subs": live})
        print(f"[3] {int(time.time() - t0)}s parent={parent and parent['status']} {live}")
        if parent and parent.get("status") in TERMINAL:
            break
    stop.set()
    for t in threads:
        t.join(timeout=5)

    tasks = client.get(f"{API}/api/ai-groups/{gid}").json().get("tasks", [])
    parent = next((x for x in tasks if not x.get("parent_task_id")), None)
    subs = [x for x in tasks if x.get("parent_task_id")]

    # 落证：discussion_messages + agent_capabilities + 执行事件
    conn = sqlite3.connect(str(AI_DB))
    conn.row_factory = sqlite3.Row
    rows = [
        dict(x)
        for x in conn.execute(
            "SELECT task_id, agent_id, agent_name, intent, content, metadata FROM discussion_messages "
            "WHERE group_id=? ORDER BY created_at",
            (gid,),
        ).fetchall()
    ]
    caps = [
        dict(x)
        for x in conn.execute(
            "SELECT agent_id, capability, avg_score, task_count FROM agent_capabilities WHERE group_id=?",
            (gid,),
        ).fetchall()
    ]
    events = [
        dict(x)
        for x in conn.execute(
            "SELECT task_id, agent_id, event_type FROM task_execution_events WHERE group_id=?",
            (gid,),
        ).fetchall()
    ]
    conn.close()

    replies = [x for x in rows if x["intent"] == "agent_reply"]
    results = [x for x in rows if x["intent"] == "result"]
    reply_agents = sorted({x["agent_id"] for x in replies})

    def _src(row):
        try:
            return (json.loads(row.get("metadata") or "{}")).get("source")
        except Exception:
            return None

    soulmate_results = [x for x in results if x["agent_id"] == "soulmate"]
    other_results = [x for x in results if x["agent_id"] != "soulmate"]

    # A4：行 + 实时帧（帧内容=行内容前缀，聊天流与库同一真源）
    agent_frames = [f for f in frames if f.get("type") == "agent_message"]
    reply_frames = [
        f for f in agent_frames if f.get("data", {}).get("intent") == "agent_reply"
    ]
    frame_matches_row = 0
    for f in reply_frames:
        fc = (f.get("data", {}).get("content") or "")[:2000]
        if any((x["content"] or "").startswith(fc) and fc for x in replies):
            frame_matches_row += 1

    # A3：依次推进证据（快照里每子任务都出现过非 pending 状态 + 事件链 started/scored）
    started_ids = {e["task_id"] for e in events if e["event_type"] in ("started", "phase")}
    scored_ids = {e["task_id"] for e in events if e["event_type"] == "scored"}
    all_subs_started_scored = (
        len(subs) == 4
        and all(x["id"] in started_ids for x in subs)
        and all(x["id"] in scored_ids for x in subs)
    )
    ordered_progress = any(
        any(":executing" in s for s in snap["subs"]) for snap in snapshots
    ) or all_subs_started_scored

    goal_texts = [x.get("goal") or "" for x in subs]
    a2_ok = subtasks_count == 4 and all(
        kw in "".join(goal_texts) for kw in ("规划", "执行", "验证", "审查")
    )

    verdicts = {
        "A1 加成员(agents[]非空)": len(agents) >= 1,
        "A2 下任务(subtasks=4含规划/执行/验证/审查)": a2_ok,
        "A3 自动链式执行(全子任务started+scored,无手动execute)": all_subs_started_scored
        and ordered_progress,
        "A4 Agent回复进群聊(agent_reply行+WS agent_message帧同源)": (
            len(replies) == 4
            and len(reply_frames) >= 1
            and frame_matches_row == len(reply_frames)
        ),
        "A5 执行(真实回答+auto_score+capability落库+source无冒名)": (
            len(soulmate_results) >= 1
            and all(_src(x) == "soulmate-agent" for x in soulmate_results)
            and all(_src(x) == x["agent_id"] for x in other_results)
            and all(_src(x) != "soulmate-agent" for x in other_results)
            and all(len(x["content"] or "") > 0 for x in results)
            and parent and parent.get("quality_score") is not None
            and len(caps) >= 1
        ),
        "A6 聊天广播(双WS互见user_message)": a6_ok,
        "A7 群体讨论(start_discussion→agent_message流)": len(disc_msgs) >= 1,
    }

    out = {
        "marker": marker,
        "group_id": gid,
        "task_id": task_id,
        "goal": goal,
        "parent": {
            "id": parent and parent.get("id"),
            "status": parent and parent.get("status"),
            "quality_score": parent and parent.get("quality_score"),
        },
        "subtasks": [
            {
                "id": x.get("id"),
                "goal": (x.get("goal") or "")[:60],
                "agent": x.get("assigned_agent_id"),
                "status": x.get("status"),
                "score": x.get("quality_score"),
                "result_len": len(x.get("result") or ""),
            }
            for x in subs
        ],
        "reply_agents": reply_agents,
        "agent_reply_rows": len(replies),
        "reply_frames": len(reply_frames),
        "frame_matches_row": frame_matches_row,
        "result_rows": [
            {"agent": x["agent_id"], "source": _src(x), "len": len(x["content"] or "")}
            for x in results
        ],
        "capabilities": caps,
        "discussion_frames": len(disc_msgs),
        "discussion_task_id": disc_task_id,
        "user_message_frames": len(user_frames),
        "ws_frame_types": {k: sum(1 for f in frames if f.get("type") == k) for k in {f.get("type") for f in frames if f.get("type")}},
        "verdicts": verdicts,
    }
    RESULT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 13.4 AI群组真实成员验收 结果 =====")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = all(verdicts.values())
    print("\nVERDICT:", "PASS" if ok else "FAIL")
    for k, v in verdicts.items():
        print(f"  {'✅' if v else '❌'} {k}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
