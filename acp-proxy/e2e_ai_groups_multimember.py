# -*- coding: utf-8 -*-
"""9.4 AI 群组真实多 agent 成员端到端探针（可复跑）。

真实用户路径：POST /api/ai-groups 建组（3 个真实成员：hermes/soulmate/mimo）
→ POST /{gid}/tasks 下任务（自动拆 4 子任务+自动链式执行，**不手动 execute**）
→ 同时挂 ws://127.0.0.1:8092/ws/group/{gid} 群组 WS 采集实时广播帧
→ 轮询到父任务收口，落证：
  V1 自动链式执行（4 子任务全部终态，无人工触发）
  V2 真实多 agent 参与（agent_reply 行的 agent_id 去重数 ≥ 2，目标 3/3）
  V3 每步结果回群聊（agent_reply 行数 == 子任务数）
  V4 群组 WS 实时广播（agent_message/task 帧 ≥ 1）
  V5 父任务收口（scored/failed/skipped）
  V6 独立通道（result 行 metadata.source：soulmate 子任务 == "soulmate-agent"，
     其余成员为各自 agent_id，禁 soulmate 兜底 hermes CLI）

运行：~/opensoul/.venv/bin/python e2e_ai_groups_multimember.py
结果 JSON：/tmp/e2e_94_result.json；WS 帧：/tmp/e2e_94_ws_frames.jsonl
"""
import json
import sqlite3
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

OPSOUL = Path.home() / "opensoul"
sys.path.insert(0, str(OPSOUL))

API = "http://127.0.0.1:8090"
GROUP_WS = "ws://127.0.0.1:8092/ws/group"
AI_DB = OPSOUL / "data" / "ai_groups.db"
RESULT_PATH = Path("/tmp/e2e_94_result.json")
FRAMES_PATH = Path("/tmp/e2e_94_ws_frames.jsonl")

MEMBERS = [
    {"agent_id": "hermes", "name": "Hermes", "role": "advisor", "model": ""},
    {"agent_id": "soulmate", "name": "SoulMate", "role": "executor", "model": ""},
    {"agent_id": "mimo", "name": "MiMo", "role": "verifier", "model": ""},
]
POLL_TIMEOUT_S = 2400
TERMINAL = ("scored", "failed", "completed", "skipped")


def mint_token() -> str:
    from src.services.auth import create_access_token

    return create_access_token(uuid.uuid4())


def ws_listener(gid: str, token: str, frames: list, stop: threading.Event):
    """群组 WS 采集线程：收帧落盘，断连如实记，不抛。"""
    try:
        from websockets.sync.client import connect
    except Exception as e:  # pragma: no cover
        frames.append({"_error": f"websockets unavailable: {e}"})
        return
    try:
        with connect(f"{GROUP_WS}/{gid}?token={token}", open_timeout=15) as ws:
            frames.append({"_connected": True, "ts": time.time()})
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
                frames.append(frame)
                with FRAMES_PATH.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(frame, ensure_ascii=False) + "\n")
    except Exception as e:
        frames.append({"_error": f"{type(e).__name__}: {e}", "ts": time.time()})


def main() -> int:
    import httpx

    marker = f"94multi-{int(time.time())}"
    token = mint_token()
    hdr = {"Authorization": f"Bearer {token}"}
    client = httpx.Client(headers=hdr, timeout=30)

    frames: list = []
    FRAMES_PATH.write_text("", encoding="utf-8")
    stop = threading.Event()

    # 1. 建组（3 个真实成员，角色 advisor/executor/verifier 恰覆盖 4 子任务的角色分配）
    r = client.post(
        f"{API}/api/ai-groups",
        json={
            "name": f"9.4多agent真实e2e-{marker}",
            "description": "周期#53 真实多agent成员端到端验收",
            "agents": MEMBERS,
        },
    )
    r.raise_for_status()
    gid = r.json()["id"]
    print(f"[1] group created: {gid}")

    t = threading.Thread(target=ws_listener, args=(gid, token, frames, stop), daemon=True)
    t.start()
    time.sleep(1.5)

    # 2. 下任务（自动拆解+自动链式执行；goal 无关键词→默认 规划/执行/验证/审查 4 子任务）
    goal = f"口算17加25的结果并用一句话说明理由[防缓存标记 {marker}]"
    r = client.post(f"{API}/api/ai-groups/{gid}/tasks",
                    json={"goal": goal,
                          # 探针走真实写路径：声明 test 出身（产生侧标注，
                          # test_probe_provenance 要求；API 按 BaseModel 忽略扩展字段）
                          "_meta": {"source": "test"}})
    r.raise_for_status()
    task_id = r.json()["task_id"]
    print(f"[2] task submitted: {task_id} subtasks={r.json()['subtasks_count']}")

    # 3. 轮询到父任务收口（不手动 execute，验证自动链式执行）
    parent = None
    t0 = time.time()
    while time.time() - t0 < POLL_TIMEOUT_S:
        time.sleep(6)
        g = client.get(f"{API}/api/ai-groups/{gid}").json()
        tasks = g.get("tasks", [])
        parent = next((x for x in tasks if not x.get("parent_task_id")), None)
        subs = [x for x in tasks if x.get("parent_task_id")]
        live = [
            f"{x['id']}:{x.get('assigned_agent_id')}:{x['status']}"
            for x in subs
        ]
        print(f"[3] {int(time.time() - t0)}s parent={parent and parent['status']} {live}")
        if parent and parent.get("status") in TERMINAL:
            break
    stop.set()
    t.join(timeout=5)

    tasks = client.get(f"{API}/api/ai-groups/{gid}").json().get("tasks", [])
    parent = next((x for x in tasks if not x.get("parent_task_id")), None)
    subs = [x for x in tasks if x.get("parent_task_id")]

    # 4. 落证：discussion_messages（agent_reply + result）
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

    frame_types: dict = {}
    for f in frames:
        k = f.get("type") or f.get("_error") and "error" or f.get("_connected") and "connected" or "?"
        frame_types[k] = frame_types.get(k, 0) + 1
    agent_frames = [f for f in frames if f.get("type") in ("agent_message", "task_update", "task_progress")]

    verdicts = {
        "V1 自动链式执行(子任务全终态,无手动execute)": (
            len(subs) == 4 and all(x.get("status") in TERMINAL for x in subs)
        ),
        "V2 真实多agent参与(≥2个不同成员回群聊)": len(reply_agents) >= 2,
        "V3 每步结果回群聊(agent_reply行==子任务数)": len(replies) == len(subs) == 4,
        "V4 群组WS实时广播(≥1帧)": len(agent_frames) >= 1,
        "V5 父任务收口": bool(parent and parent.get("status") in TERMINAL),
        "V6 独立通道(source无冒名兜底)": (
            len(soulmate_results) >= 1
            and all(_src(x) == "soulmate-agent" for x in soulmate_results)
            and all(_src(x) == x["agent_id"] for x in other_results)
            and all(_src(x) != "soulmate-agent" for x in other_results)
        ),
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
        "result_rows": [
            {"agent": x["agent_id"], "source": _src(x), "len": len(x["content"] or "")}
            for x in results
        ],
        "ws_frame_types": frame_types,
        "ws_agent_frames": len(agent_frames),
        "verdicts": verdicts,
    }
    RESULT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n===== 9.4 多agent真实e2e 结果 =====")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = all(verdicts.values())
    print("\nVERDICT:", "PASS" if ok else "FAIL")
    for k, v in verdicts.items():
        print(f"  {'✅' if v else '❌'} {k}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
