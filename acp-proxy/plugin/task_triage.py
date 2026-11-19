"""
任务三级分流门（task triage gate）— 小/中/大判级 + 三轨分流 + 统一回看板 + 个人/企业双语义

用户定稿任务架构（2026-11-19）：
  小任务 → 普通 agent 独自处理（会话功能，SoulMate 单 agent）
  中等难度 → AI 群组让 AI 自己拆解处理（临时工作小组，docs/ai-groups-model.md）
  大型复杂 → paperclip 式流程化处理（flow_engine 工单/agent_node/human_node/decision/预算闸）
  流程化对应个人用户 = 一个人的公司；对应企业 = 企业的内部流程。

本模块职责（纯 Python 后端，与 flow_studio 同款红线：生成唯一入口 = LLMEngine）：
  - analyze_level：一句话 → AI 判级（small/medium/large + 理由）；LLM 失败回落
    规则判级（如实标注 level_source='heuristic'，禁兜底假造）
  - dispatch：按轨分流执行
      chat  → OpenSoul POST /api/sessions 开/续 SoulMate 会话（deep link 自动送达）
      group → OpenSoul POST /api/ai-groups 建组 + POST /{gid}/tasks 任务发布
              （服务端自动拆解 + 链式执行）
      flow  → flow_studio.generate_from_nl 生成流程草稿（预览）→ confirm_flow 装载跑
  - switch_track：一键换轨（会话↔群组↔流程），上下文带过去（旧记录留痕 switched_to）
  - 统一回看板：GET /triage/records，三轨产物（session/group/run）在同一个看板回看
  - 租户双语义：mode=personal（一人公司）| enterprise（企业内部流程），
    影响流程化视图文案与模板偏好（templates_for_mode）

存储：data/task_triage.db（SQLite，records + settings 两表）。
跨服务调用：OpenSoul :8090（会话/群组），Authorization 头透传（租户身份派生）。
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable

logger = logging.getLogger("plugin.task_triage")

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "task_triage.db"
OPSOUL_API = "http://127.0.0.1:8090"

# 三级 ↔ 三轨（用户定稿：小→会话、中→群组、大→流程化）
LEVEL_TRACK = {"small": "chat", "medium": "group", "large": "flow"}
TRACKS = ("chat", "group", "flow")
LEVELS = ("small", "medium", "large")

# 记录状态机：created → dispatched（chat/group）| draft_ready（flow）
#           → running → done/failed；换轨 = switched（留痕 switched_to）
STATUS_SWITCHED = "switched"

# 群组默认成员（三角色体系：顾问/执行者/验证者，docs/ai-groups-model.md 不变部分）
DEFAULT_GROUP_MEMBERS = [
    {"agent_id": "hermes", "name": "Hermes", "role": "advisor", "model": ""},
    {"agent_id": "soulmate", "name": "SoulMate", "role": "executor", "model": ""},
    {"agent_id": "mimo", "name": "MiMo", "role": "verifier", "model": ""},
]

# 模板偏好（个人/企业双语义）：preferred 顺序即推荐序
#   personal（一人公司）：自动化替身优先——文件监听/定时对账/多 agent 分身
#   enterprise（企业内部流程）：审批链/跨角色协作优先
TEMPLATE_MODES: dict[str, list[str]] = {
    "scheduled-reconcile": ["personal", "enterprise"],
    "file-watch-process": ["personal"],
    "approval-chain": ["enterprise"],
    "multi-agent-collab": ["personal", "enterprise"],
}
MODE_PREFERRED_ORDER = {
    "personal": ["file-watch-process", "scheduled-reconcile", "multi-agent-collab"],
    "enterprise": ["approval-chain", "multi-agent-collab", "scheduled-reconcile"],
}


# ─────────────────────────── 存储 ───────────────────────────

def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS triage_records (
                id TEXT PRIMARY KEY,
                prompt TEXT NOT NULL,
                level TEXT,
                confidence REAL,
                reasons TEXT,
                level_source TEXT,
                track TEXT,
                status TEXT,
                context TEXT,
                artifact TEXT,
                detail TEXT,
                switched_from TEXT,
                switched_to TEXT,
                tenant_mode TEXT,
                created_at REAL,
                updated_at REAL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS triage_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )"""
        )


def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
    rec = dict(row)
    for key in ("reasons", "context", "artifact", "detail"):
        rec[key] = json.loads(rec.get(key) or ("[]" if key == "reasons" else "{}"))
    return rec


def create_record(**fields: Any) -> dict[str, Any]:
    now = time.time()
    rec = {
        "id": fields.get("id") or f"tri-{uuid.uuid4().hex[:8]}",
        "prompt": fields["prompt"],
        "level": fields.get("level") or "",
        "confidence": fields.get("confidence") or 0.0,
        "reasons": fields.get("reasons") or [],
        "level_source": fields.get("level_source") or "",
        "track": fields.get("track") or "",
        "status": fields.get("status") or "created",
        "context": fields.get("context") or {},
        "artifact": fields.get("artifact") or {},
        "detail": fields.get("detail") or {},
        "switched_from": fields.get("switched_from") or "",
        "switched_to": fields.get("switched_to") or "",
        "tenant_mode": fields.get("tenant_mode") or get_mode(),
        "created_at": now,
        "updated_at": now,
    }
    with _connect() as conn:
        conn.execute(
            "INSERT INTO triage_records (id, prompt, level, confidence, reasons, level_source,"
            " track, status, context, artifact, detail, switched_from, switched_to, tenant_mode,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rec["id"], rec["prompt"], rec["level"], rec["confidence"],
                json.dumps(rec["reasons"], ensure_ascii=False), rec["level_source"],
                rec["track"], rec["status"],
                json.dumps(rec["context"], ensure_ascii=False),
                json.dumps(rec["artifact"], ensure_ascii=False),
                json.dumps(rec["detail"], ensure_ascii=False),
                rec["switched_from"], rec["switched_to"], rec["tenant_mode"],
                rec["created_at"], rec["updated_at"],
            ),
        )
    return rec


def update_record(record_id: str, **fields: Any) -> dict[str, Any] | None:
    rec = get_record(record_id)
    if rec is None:
        return None
    rec.update(fields)
    rec["updated_at"] = time.time()
    with _connect() as conn:
        conn.execute(
            "UPDATE triage_records SET level=?, confidence=?, reasons=?, level_source=?,"
            " track=?, status=?, context=?, artifact=?, detail=?, switched_from=?,"
            " switched_to=?, tenant_mode=?, updated_at=? WHERE id=?",
            (
                rec["level"], rec["confidence"],
                json.dumps(rec["reasons"], ensure_ascii=False), rec["level_source"],
                rec["track"], rec["status"],
                json.dumps(rec["context"], ensure_ascii=False),
                json.dumps(rec["artifact"], ensure_ascii=False),
                json.dumps(rec["detail"], ensure_ascii=False),
                rec["switched_from"], rec["switched_to"], rec["tenant_mode"],
                rec["updated_at"], record_id,
            ),
        )
    return rec


def get_record(record_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM triage_records WHERE id=?", (record_id,)).fetchone()
    return _row_to_record(row) if row else None


def list_records(limit: int = 50) -> list[dict[str, Any]]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM triage_records ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [_row_to_record(row) for row in rows]


def get_mode() -> str:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM triage_settings WHERE key='tenant_mode'").fetchone()
    mode = (row["value"] if row else "") or "personal"
    return mode if mode in ("personal", "enterprise") else "personal"


def set_mode(mode: str) -> str:
    if mode not in ("personal", "enterprise"):
        raise ValueError(f"mode 非法: {mode!r}（personal|enterprise）")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO triage_settings (key, value) VALUES ('tenant_mode', ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value", (mode,))
    return mode


# ─────────────────────────── 判级（AI + 规则回落）───────────────────────────

ChatFn = Callable[[list[dict]], Awaitable[str]]

TRIAGE_SYSTEM_PROMPT = (
    "你是任务分级器。把用户任务按难度分为三级之一：\n"
    "- small（小任务）：普通 agent 单独在一次会话里就能完成的事，如写一段文字、翻译、"
    "解释一个概念、改个格式、简单问答。\n"
    "- medium（中任务）：需要多个 AI 角色集思广益、拆解分工、讨论共识才能做好的事，"
    "如调研对比、出方案、设计评审、多角度分析、内容策划。\n"
    "- large（大任务）：复杂流程化工作——多阶段、带审批/工单/预算闸、需要例行化或长期运行，"
    "如定时对账、发布保障流程、报销审批流、跨部门制度性工作。\n"
    "只输出 JSON 本体：{\"level\":\"small|medium|large\",\"confidence\":0到1,"
    "\"reasons\":[\"判级理由1\",\"判级理由2\"]}（reasons 用用户语言，1-3条）。"
)


def _extract_json(text: str) -> dict[str, Any]:
    """容错抽取 JSON 对象本体（与 flow_studio.extract_json 同款策略）"""
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("输出中未找到 JSON 对象")
    return json.loads(m.group(0))


def heuristic_level(prompt: str) -> dict[str, Any]:
    """规则判级回落（LLM 不可用时；如实标注 level_source='heuristic'）"""
    text = (prompt or "").strip()
    large_kw = ["流程", "审批", "工单", "预算", "每周", "每月", "每天定时", "例行",
                "长期", "自动化流水线", "多部门", "合规", "pipeline", "发布保障", "对账"]
    medium_kw = ["对比", "调研", "分析", "方案", "策划", "评审", "设计", "规划",
                 "选型", "报告", "拆解", "多个", "优劣", "建议"]
    small_kw = ["翻译", "解释", "写一", "总结一下", "改一下", "润色", "起个", "问一下",
                "是什么", "怎么读", "算一下"]
    reasons: list[str] = []
    level = "medium"
    for kw in large_kw:
        if kw in text:
            level = "large"
            reasons.append(f"命中流程化特征「{kw}」")
            break
    if level != "large":
        hit_med = [kw for kw in medium_kw if kw in text]
        hit_small = [kw for kw in small_kw if kw in text]
        if hit_med and not hit_small:
            level = "medium"
            reasons.append(f"命中协作拆解特征「{hit_med[0]}」")
        elif hit_small and not hit_med:
            level = "small"
            reasons.append(f"命中单会话即办特征「{hit_small[0]}」")
        else:
            # 复合信号：按文本规模与并列子句数兜底（长而多并列 → 至少 medium）
            clauses = len(re.split(r"[；;。？?！!]", text))
            if len(text) > 60 or clauses > 3:
                level = "medium"
                reasons.append("任务描述较长/多子句，需拆解协作")
            else:
                level = "small"
                reasons.append("描述简短，单会话可独立完成")
    if not reasons:
        reasons.append("按关键词与规模特征判级")
    return {"level": level, "confidence": 0.5, "reasons": reasons, "source": "heuristic"}


async def analyze_level(prompt: str, *, chat: ChatFn | None = None,
                        system_prompt: str = TRIAGE_SYSTEM_PROMPT) -> dict[str, Any]:
    """一句话 → 判级。LLM 生成唯一入口（LLMEngine）；失败回落规则判级（如实标注）。"""
    prompt = (prompt or "").strip()
    if not prompt:
        return {"level": "small", "confidence": 0.0, "reasons": ["任务为空"],
                "source": "heuristic", "track": "chat"}
    if chat is None:
        chat = _default_chat
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"【任务】{prompt}"},
    ]
    try:
        raw = await chat(messages)
        data = _extract_json(raw or "")
        level = str(data.get("level", "")).strip().lower()
        if level not in LEVELS:
            raise ValueError(f"level 非法: {level!r}")
        confidence = float(data.get("confidence", 0.5))
        reasons = [str(r) for r in (data.get("reasons") or [])][:3]
        return {
            "level": level,
            "confidence": max(0.0, min(confidence, 1.0)),
            "reasons": reasons or ["AI 判级"],
            "source": "llm",
            "track": LEVEL_TRACK[level],
        }
    except Exception as e:  # LLM 不可用/输出不合规 → 规则回落（如实标注来源）
        logger.info("AI 判级失败，回落规则判级: %s", e)
        result = heuristic_level(prompt)
        result["track"] = LEVEL_TRACK[result["level"]]
        return result


async def _default_chat(messages: list[dict]) -> str:
    """默认判级生成方 = LLMEngine（SoulMate 同款推理层，配置单一真源）"""
    from agent.llm_engine import LLMEngine
    engine = LLMEngine()
    return await engine.chat(messages)


# ─────────────────────────── 跨服务 HTTP（可注入，测试可替换）───────────────────────────

async def _http(method: str, url: str, *, body: dict[str, Any] | None = None,
                auth: str | None = None, attempts: int = 3) -> dict[str, Any]:
    """跨服务 HTTP（可注入，测试可替换）。瞬时故障（连接错/5xx，如 SQLite 锁）带退避重试。"""
    import asyncio
    import httpx
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = auth
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.request(method, url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json() if resp.content else {}
        except httpx.HTTPStatusError as e:
            last = e
            if e.response.status_code < 500 or attempt >= attempts:
                raise
        except httpx.HTTPError as e:
            last = e
            if attempt >= attempts:
                raise
        await asyncio.sleep(min(2.0, 0.5 * attempt))
    raise last if last else RuntimeError("HTTP 调用失败")


# ─────────────────────────── 三轨分流执行 ───────────────────────────

def _artifact_link(artifact: dict[str, Any]) -> str:
    """产物直达深链（统一回看板「打开」按钮用）"""
    kind = artifact.get("type")
    if kind == "chat":
        sid = artifact.get("session_id", "")
        fwd = artifact.get("prompt", "")
        return f"/chat?session={sid}&forward={quote(fwd)}&auto_send=1"
    if kind == "group":
        return f"/ai-groups?group={artifact.get('group_id', '')}"
    if kind == "flow":
        fid = artifact.get("flow_id", "")
        return f"/will?flow={fid}" if fid else "/will"
    return ""


def quote(text: str) -> str:
    from urllib.parse import quote as _q
    return _q(text or "", safe="")


async def dispatch_chat(rec: dict[str, Any], *, auth: str | None = None) -> dict[str, Any]:
    """小任务 → 开/续 SoulMate 会话直接办（deep link 落 /chat 自动送达）。
    会话行必须真建成（/chat 直载依赖它）——失败如实报错，禁假装成功。"""
    sid = f"om-triage-{rec['id']}"
    try:
        await _http(
            "POST", f"{OPSOUL_API}/api/sessions",
            body={"id": sid, "agent_id": "soulmate",
                  "name": rec["prompt"][:20] or "分流门任务"},
            auth=auth,
        )
    except Exception as e:
        raise RuntimeError(f"开 SoulMate 会话失败：{e}")
    artifact = {"type": "chat", "session_id": sid, "prompt": rec["prompt"],
                "agent_id": "soulmate"}
    artifact["link"] = _artifact_link(artifact)
    return update_record(rec["id"], track="chat", status="dispatched",
                         artifact=artifact) or rec


async def dispatch_group(rec: dict[str, Any], *, auth: str | None = None) -> dict[str, Any]:
    """中任务 → AI 群组发起：自动建组 + 任务发布（服务端自动拆解 + 链式执行）"""
    name = (rec["prompt"][:16] or "分流门任务").replace("\n", " ")
    group = await _http(
        "POST", f"{OPSOUL_API}/api/ai-groups",
        body={"name": f"任务组·{name}", "description": rec["prompt"][:200],
              "agents": DEFAULT_GROUP_MEMBERS},
        auth=auth,
    )
    gid = group["id"]
    constraints = [str(c) for c in (rec.get("context") or {}).get("constraints", [])]
    task = await _http(
        "POST", f"{OPSOUL_API}/api/ai-groups/{gid}/tasks",
        body={"goal": rec["prompt"], "constraints": constraints,
              "completion_criteria": ["成果发布到群并对照验收标准逐条满足"],
              "discuss_after": False},
        auth=auth,
    )
    artifact = {"type": "group", "group_id": gid, "task_id": task.get("task_id", ""),
                "subtasks_count": task.get("subtasks_count", 0), "name": f"任务组·{name}"}
    artifact["link"] = _artifact_link(artifact)
    return update_record(rec["id"], track="group", status="dispatched",
                         artifact=artifact) or rec


async def dispatch_flow(rec: dict[str, Any]) -> dict[str, Any]:
    """大任务 → 流程化：flow_studio 一句话建流产出草稿（预览，未装载）"""
    from plugin.flow_studio import generate_from_nl
    prompt = rec["prompt"]
    ctx = rec.get("context") or {}
    if ctx.get("carried"):
        prompt = f"{prompt}\n\n【换轨上下文】{ctx['carried']}"
    result = await generate_from_nl(prompt)
    if not result.get("ok"):
        update_record(rec["id"], track="flow", status="failed",
                      detail={"error": result.get("error", "生成失败")})
        raise RuntimeError(result.get("error", "一句话建流失败"))
    detail = {"definition": result["definition"], "yaml": result.get("yaml", ""),
              "name": result.get("name", ""), "attempts": result.get("attempts", 0)}
    artifact = {"type": "flow", "flow_id": "", "run_id": "",
                "draft_name": result.get("name", "")}
    artifact["link"] = _artifact_link(artifact)
    return update_record(rec["id"], track="flow", status="draft_ready",
                         artifact=artifact, detail=detail) or rec


async def confirm_flow(rec: dict[str, Any]) -> dict[str, Any]:
    """装载跑：草稿 → save_user_flow 落盘注册 → engine.run_now 真跑"""
    from plugin.flow_engine import get_engine
    from plugin.flow_studio import save_user_flow
    definition = (rec.get("detail") or {}).get("definition")
    if not definition:
        raise ValueError("该记录没有流程草稿（detail.definition 为空）")
    engine = get_engine()
    saved = save_user_flow(definition, engine=engine)
    flow_id = saved["flow_id"]
    run = await engine.run_now(flow_id, {"type": "manual", "source": "triage"})
    artifact = dict(rec.get("artifact") or {})
    artifact.update({"type": "flow", "flow_id": flow_id, "run_id": run.run_id})
    artifact["link"] = _artifact_link(artifact)
    return update_record(rec["id"], status="running", artifact=artifact) or rec


async def dispatch(rec: dict[str, Any], track: str, *, auth: str | None = None) -> dict[str, Any]:
    if track == "chat":
        return await dispatch_chat(rec, auth=auth)
    if track == "group":
        return await dispatch_group(rec, auth=auth)
    if track == "flow":
        return await dispatch_flow(rec)
    raise ValueError(f"track 非法: {track!r}（chat|group|flow）")


async def switch_track(rec: dict[str, Any], to_track: str, *,
                       auth: str | None = None) -> dict[str, Any]:
    """一键换轨：会话↔群组↔流程，上下文带过去；旧记录留痕 switched_to"""
    if to_track not in TRACKS:
        raise ValueError(f"track 非法: {to_track!r}（chat|group|flow）")
    if rec["track"] == to_track:
        raise ValueError("已在该轨道，无需换轨")
    prev_artifact = rec.get("artifact") or {}
    carried = (
        f"本任务原在「{rec['track']}」轨道办理后换轨而来；"
        f"原产物：{prev_artifact.get('link') or prev_artifact.get('type') or '无'}。"
        f"原任务：{rec['prompt']}"
    )
    if (rec.get("context") or {}).get("carried"):
        carried += f"\n更早换轨上下文：{rec['context']['carried']}"
    ctx = dict(rec.get("context") or {})
    ctx["carried"] = carried
    new_rec = create_record(
        prompt=rec["prompt"], level=rec["level"], confidence=rec["confidence"],
        reasons=rec["reasons"], level_source=rec["level_source"],
        track=to_track, status="created", context=ctx,
        switched_from=rec["id"], tenant_mode=rec.get("tenant_mode") or get_mode(),
    )
    new_rec = await dispatch(new_rec, to_track, auth=auth)
    update_record(rec["id"], status=STATUS_SWITCHED, switched_to=new_rec["id"])
    return new_rec


# ─────────────────────── 对话内隐式分流（2026-11-19 归位）───────────────────────────
# 用户裁定：不设第二入口——用户只在对话里跟 SoulMate 说事，SoulMate 沟通中自己判级：
# 小=直接干完；中=自动转 AI 群组拆解；大=自动转流程化。升级时向用户透明告知
# （"这事适合协作，我转到群组了"），而非让用户选。判级引擎能力保留，入口形态从
# 显式（FAB/下达面板）变隐式（所想即所见）。

CONVERSATION_TRIAGE_SYSTEM_PROMPT = (
    "你是对话中的任务分级器。用户在和 SoulMate 聊天，判断这条消息是不是「现在就要动手办的"
    "任务」以及难度级别：\n"
    "- small（小任务）：闲聊/追问/澄清/回答 SoulMate 的问题/一句话就能办完的小事——"
    "SoulMate 在会话里自己直接办。\n"
    "- medium（中任务）：需要多个 AI 角色集思广益、拆解分工、讨论共识才能做好的任务，"
    "如调研对比、出方案、设计评审、多角度分析、内容策划。\n"
    "- large（大任务）：复杂流程化工作——多阶段、带审批/工单/预算闸、需要例行化或长期运行，"
    "如定时对账、发布保障流程、报销审批流、跨部门制度性工作。\n"
    "注意：用户还在讨论需求、没让现在动手时判 small。只输出 JSON 本体："
    "{\"level\":\"small|medium|large\",\"confidence\":0到1,\"reasons\":[\"判级理由1\"]}"
    "（reasons 用用户语言，1-2条）。"
)

# 升级判据：LLM 判级且置信达线才自动转轨；LLM 不可用/低置信 → 不惊动用户、不假造
# 升级，SoulMate 自己接着办（不制造假分流）。
CONVERSATION_ESCALATE_MIN_CONFIDENCE = 0.6


def conversation_notice(rec: dict[str, Any]) -> str:
    """升级透明告知（SoulMate 口吻）：只讲转到哪了+怎么跟进，用户不需要做任何选择。"""
    art = rec.get("artifact") or {}
    link = art.get("link") or ""
    if rec["track"] == "group":
        name = art.get("name") or "任务组"
        return (
            "🤝 这事适合协作，我转到群组了。\n"
            f"已建「{name}」并发布任务：Hermes（顾问）/ 我（执行）/ MiMo（验证）"
            "会拆解分工推进。\n"
            f"查看讨论与进展：{link}\n"
            "有结果我会同步你；想让我自己直接办也可以直说。"
        )
    detail = rec.get("detail") or {}
    name = detail.get("name") or art.get("draft_name") or "该任务"
    if rec.get("status") == "running":
        return (
            "🧩 这事适合流程化，我转成流程在跑了。\n"
            f"「{name}」已按多阶段流程执行（含人工确认节点，需要你拍板时会来找你）。\n"
            f"查看流程：{link}"
        )
    err = detail.get("confirm_error")
    tail = f"（自动装载未成功：{err}，预览确认后即开跑）" if err else "（预览确认后即开跑）"
    return (
        "🧩 这事适合流程化，流程草稿已生成。\n"
        f"「{name}」：{link} {tail}"
    )


async def conversation_triage(user_text: str, *, session_id: str = "",
                              chat: ChatFn | None = None,
                              auth: str | None = None) -> dict[str, Any] | None:
    """对话内隐式分流（无第二入口）：判级为中/大且置信达线 → 自动转群组/流程化，
    返回 {"level","track","record","notice"}；小任务/低置信/分流执行失败 → 返回 None
    （SoulMate 在会话里直接办，不打断对话）。

    大任务自动起流程（dispatch_flow 草稿 → confirm_flow 装载跑）——隐式形态没有
    预览确认入口，流程引擎自身的人工节点/预算闸是安全闸；装载失败如实降级为
    "草稿已就绪"告知，不假装已跑。
    """
    text = (user_text or "").strip()
    if not text:
        return None
    judged = await analyze_level(text, chat=chat,
                                 system_prompt=CONVERSATION_TRIAGE_SYSTEM_PROMPT)
    if judged["level"] == "small":
        return None
    if (judged.get("source") != "llm"
            or float(judged.get("confidence") or 0) < CONVERSATION_ESCALATE_MIN_CONFIDENCE):
        logger.info("对话分流不升级（source=%s conf=%s）：SoulMate 直接办",
                    judged.get("source"), judged.get("confidence"))
        return None
    rec = create_record(
        prompt=text, level=judged["level"], confidence=judged["confidence"],
        reasons=judged["reasons"], level_source="llm", track=judged["track"],
        status="created",
        context={"source": "conversation", "session_id": session_id},
    )
    try:
        if rec["track"] == "group":
            rec = await dispatch_group(rec, auth=auth)
        else:
            rec = await dispatch_flow(rec)
            try:
                rec = await confirm_flow(rec)  # 隐式形态：大任务自动起流程
            except Exception as e:  # 草稿在，先补救，最终如实降级（不假装已跑）
                if "无可用成员" in str(e):
                    # 生成物引用了无人可派的组织角色（如"发布负责人"）→ 重申可执行性
                    # 约束重生成一次再装载：human 节点派给本人、agent 节点派 soulmate
                    logger.info("对话分流流程装载卡在派单解析，带约束重生成: %s", e)
                    retry = await dispatch_flow({
                        **rec,
                        "prompt": (
                            f"{text}\n\n【可执行性约束】human 节点的 user 一律填「用户」"
                            "（即本人拍板），不要用组织角色名；agent 节点 target_agent 用 soulmate。"
                        ),
                    })
                    try:
                        rec = await confirm_flow(retry)
                    except Exception as e2:
                        logger.warning("对话分流流程装载重试仍失败（保留草稿）: %s", e2)
                        rec = update_record(
                            rec["id"],
                            detail={**(rec.get("detail") or {}), "confirm_error": str(e2)},
                        ) or rec
                else:
                    logger.warning("对话分流流程装载失败（保留草稿）: %s", e)
                    rec = update_record(
                        rec["id"],
                        detail={**(rec.get("detail") or {}), "confirm_error": str(e)},
                    ) or rec
    except Exception as e:
        logger.warning("对话分流执行失败（回落 SoulMate 直接办）: %s", e)
        update_record(rec["id"], status="failed", detail={"error": str(e)})
        return None
    return {"level": judged["level"], "track": rec["track"], "record": rec,
            "notice": conversation_notice(rec)}


# ─────────────────────────── 回看板（三轨产物统一）───────────────────────────

async def live_status(rec: dict[str, Any], *, auth: str | None = None) -> dict[str, Any]:
    """按产物实时拉取对轨系统状态（失败如实标注，不假造）"""
    artifact = rec.get("artifact") or {}
    kind = artifact.get("type")
    try:
        if kind == "chat":
            info = await _http("GET", f"{OPSOUL_API}/api/sessions/{artifact['session_id']}",
                               auth=auth)
            count = int(info.get("message_count") or 0)
            return {"state": "running" if count else "waiting",
                    "label": f"会话已建 · {count} 条消息"}
        if kind == "group":
            info = await _http("GET", f"{OPSOUL_API}/api/ai-groups/{artifact['group_id']}",
                               auth=auth)
            tasks = info.get("tasks") or []
            task = next((t for t in tasks if t.get("id") == artifact.get("task_id")), None)
            status = (task or {}).get("status") or "unknown"
            return {"state": ("done" if status in ("scored", "completed")
                              else "failed" if status == "failed"
                              else "running"),
                    "label": f"群任务 {status}" + (f" · 子任务×{artifact.get('subtasks_count', 0)}"
                                                   if artifact.get("subtasks_count") else "")}
        if kind == "flow":
            run_id = artifact.get("run_id")
            if not run_id:
                return {"state": "draft", "label": "草稿待装载"}
            from plugin.flow_engine import get_engine
            run = get_engine().store.find_run(run_id)
            if run is None:
                return {"state": "unknown", "label": "实例不存在"}
            return {"state": ("done" if run.status == "completed"
                              else "failed" if run.status == "failed"
                              else "running"),
                    "label": f"流程实例 {run.status}"}
    except Exception as e:
        logger.info("live_status 拉取失败（%s）: %s", kind, e)
        return {"state": "unknown", "label": f"状态暂不可达（{kind}）"}
    return {"state": "unknown", "label": "未分流"}


async def board(limit: int = 50, *, auth: str | None = None) -> list[dict[str, Any]]:
    items = []
    for rec in list_records(limit):
        rec = dict(rec)
        rec["live"] = await live_status(rec, auth=auth)
        rec["link"] = (rec.get("artifact") or {}).get("link", "")
        items.append(rec)
    return items


# ─────────────────────────── 模板偏好（个人/企业双语义）───────────────────────────

def templates_for_mode(mode: str | None = None) -> dict[str, Any]:
    from plugin.flow_studio import list_templates
    mode = mode or get_mode()
    if mode not in ("personal", "enterprise"):
        mode = "personal"
    order = MODE_PREFERRED_ORDER[mode]
    items = []
    for tpl in list_templates():
        item = dict(tpl)
        item["modes"] = TEMPLATE_MODES.get(tpl["id"], ["personal", "enterprise"])
        item["preferred"] = mode in item["modes"]
        items.append(item)
    items.sort(key=lambda t: order.index(t["id"]) if t["id"] in order else len(order))
    return {"mode": mode, "templates": items}


init_db()
