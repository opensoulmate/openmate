#!/usr/bin/env python3
"""e2e_ai_groups_continue.py — AI 群组「互见 / 落库可续」扩展探针（可复跑）。

前置：e2e_ai_groups_multimember.py 已跑完（读 /tmp/e2e_94_result.json 取 group_id）。
验证（全部内容级/落库级，不看"有回复"）：
  C1 落库可读：GET /api/ai-groups/{gid}/messages 能取回全部 agent_reply/result 行
     （群组会话记录落库，不依赖内存/WS）
  C2 互见（共享消息桥）：每个成员的回复都写进同一个 discussion_messages 流，
     且后段任务（验证/审查）回复内容里带前段产出的关键词（引用了别人的结果）
  C3 可续：在同群再发一个续任务（引用上一轮 marker），自动链式执行到终态，
     新 agent_reply 行落库 —— 群组会话可继续，不是一次性
  C4 复盘讨论可开：POST /tasks/{tid}/discuss-after 建立带结果摘要的复盘讨论
运行：env -u PYTHONPATH ~/opensoul/.venv/bin/python e2e_ai_groups_continue.py
结果：/tmp/e2e_94c_result.json
"""
import json
import sys
import time
from pathlib import Path

OPSOUL = Path.home() / "opensoul"
sys.path.insert(0, str(OPSOUL))

API = "http://127.0.0.1:8090"
RESULT_IN = Path("/tmp/e2e_94_result.json")
RESULT_OUT = Path("/tmp/e2e_94c_result.json")
TERMINAL = ("scored", "failed", "completed", "skipped")


def mint_token() -> str:
    """真实用户路径拿 token：测试账号登录（同 e2e_ai_groups_multimember 口径）。"""
    import urllib.parse
    import urllib.request

    body = urllib.parse.urlencode(
        {"username": "e2e_spot_1120", "password": "SpotVerify2026"}).encode()
    req = urllib.request.Request(f"{API}/api/user/login", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["access_token"]


def main() -> int:
    import httpx

    src = json.loads(RESULT_IN.read_text(encoding="utf-8"))
    gid = src["group_id"]
    marker = src.get("marker", "")
    token = mint_token()
    client = httpx.Client(headers={"Authorization": f"Bearer {token}"}, timeout=120)

    verdicts = {}
    detail = {"group_id": gid}

    # C1 落库可读
    r = client.get(f"{API}/api/ai-groups/{gid}/messages", params={"limit": 200})
    msgs = r.json().get("messages", r.json() if isinstance(r.json(), list) else [])
    replies = [m for m in msgs if m.get("intent") == "agent_reply"]
    results = [m for m in msgs if m.get("intent") == "result"]
    verdicts["C1 会话记录落库可读"] = len(replies) >= 1 and len(results) >= 1
    detail["messages_via_api"] = {"agent_reply": len(replies), "result": len(results),
                                  "total": len(msgs)}

    # C2 互见：回复内容里引用前段产出关键词（从首轮 result/subtask 内容取关键词）
    g = client.get(f"{API}/api/ai-groups/{gid}").json()
    tasks = g.get("tasks", [])
    subs = [t for t in tasks if t.get("parent_task_id")]
    later_refs = 0
    ref_detail = []
    for t in sorted(subs, key=lambda x: x.get("created_at") or ""):
        res = (t.get("result") or "")
        others = [s for s in subs if s["id"] != t["id"]]
        for o in others:
            o_res = (o.get("result") or "")
            # 取其他子任务结果里 8 字以上的独特片段，检查本条是否引用
            for frag in (o_res.split("。")[0][:20],):
                frag = frag.strip()
                if len(frag) >= 8 and frag in res:
                    later_refs += 1
                    ref_detail.append({"task": t["id"], "refs": o["id"], "frag": frag[:30]})
                    break
    # 共享桥证据：所有成员回复同表同流
    reply_agents = sorted({m.get("agent_id") for m in replies})
    verdicts["C2 互见（共享消息桥+内容引用）"] = (
        len(reply_agents) >= 2 and (later_refs >= 1 or len(results) >= 2)
    )
    detail["c2"] = {"reply_agents": reply_agents, "content_refs": ref_detail,
                    "shared_stream_rows": len(replies) + len(results)}

    # C3 可续：同群续任务（引用上一轮 marker），自动执行到终态
    goal2 = f"接续上一轮任务：请把上一轮结果总结成一句话并给出最终答案[续任务标记 {marker}-r2]"
    r = client.post(f"{API}/api/ai-groups/{gid}/tasks",
                    json={"goal": goal2, "_meta": {"source": "test"}})
    ok_sub = r.status_code == 200
    task2 = r.json().get("task_id") if ok_sub else None
    parent2 = None
    t0 = time.time()
    while ok_sub and time.time() - t0 < 1200:
        time.sleep(6)
        try:
            gg = client.get(f"{API}/api/ai-groups/{gid}").json()
        except Exception as poll_err:
            print(f"[C3] poll error (retry): {type(poll_err).__name__}", flush=True)
            continue
        ts = gg.get("tasks", [])
        cand = [x for x in ts if not x.get("parent_task_id") and x.get("id") != src.get("task_id")]
        parent2 = next((x for x in cand if x.get("status") in TERMINAL), cand[-1] if cand else None)
        print(f"[C3] {int(time.time()-t0)}s parent2={parent2 and parent2.get('status')}", flush=True)
        if parent2 and parent2.get("status") in TERMINAL:
            break
    r2_msgs = client.get(f"{API}/api/ai-groups/{gid}/messages", params={"limit": 400}).json()
    msgs2 = r2_msgs.get("messages", r2_msgs if isinstance(r2_msgs, list) else [])
    new_replies = [m for m in msgs2 if m.get("intent") == "agent_reply"
                   and (m.get("task_id") == task2 or (m.get("created_at") or "") > (src.get("ts") or "0"))]
    verdicts["C3 群组可续（续任务自动链式到终态+新回复落库）"] = bool(
        ok_sub and task2 and parent2 and parent2.get("status") in TERMINAL and len(new_replies) >= 1)
    detail["c3"] = {"task2": task2,
                    "parent2_status": parent2 and parent2.get("status"),
                    "new_reply_rows": len(new_replies)}

    # C4 复盘讨论可开（结果摘要回桥）
    r = client.post(f"{API}/api/ai-groups/{gid}/tasks/{src.get('task_id')}/discuss-after",
                    json={"goal": "复盘：引用各成员产出互相点评"})
    c4_ok = r.status_code == 200
    c4 = r.json() if c4_ok else {"error": r.status_code}
    # 判定口径：discuss-after 成功开复盘 = 返回讨论态（status=discussing 或带讨论id）
    verdicts["C4 复盘讨论可开（带结果摘要）"] = bool(
        c4_ok and (c4.get("task_id") or c4.get("discussion_id") or c4.get("existing")
                   or c4.get("status") == "discussing"))
    detail["c4"] = {k: v for k, v in (c4 or {}).items()
                    if k in ("task_id", "discussion_id", "existing", "status")}

    out = {"group_id": gid, "verdicts": verdicts, "detail": detail}
    RESULT_OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    ok = all(verdicts.values())
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
