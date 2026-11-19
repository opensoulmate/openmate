"""agent/skill_load_verify.py — 技能装载验证（P0 验收钩子，"写了≠接线了"）

对标 OMH skill_load_protocol.py（卷一§4.2 第三路）："以为装载了其实没有"是
技能路由的最后一种失败。OMH 那套 nonce/digest 是跨宿主偏执（不抄，卷二§3），
我们验证对象是自家注入面——技能体是否真的进了 system prompt：

LOAD_STATES（OMH :16-17 同名四态，语义收敛到注入面）：
- all_loaded：技能体逐字节在注入段中（含预算内小技能的原文）；
- partially_loaded：截断自报（_TRUNCATED_MARK）或体只进了开头；
- none_loaded：注入段里根本没有该技能（接线断了/预算挤掉）；
- not_applicable：无注入段（本回合无技能，空跑）。

每回合写收据 data/skill_load_receipts.jsonl（jsonl 追加，原子 append）：
ts / session_id / 逐技能 state / 段 token 数 / 降级留痕。验收脚本读收据断言
"第二次任务技能确实注入了"——不是"匹配返回了"而是"装进上下文了"。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger("agent.skill_load_verify")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
RECEIPT_PATH = os.environ.get(
    "SOULMATE_SKILL_LOAD_RECEIPTS",
    os.path.join(DATA_DIR, "skill_load_receipts.jsonl"),
)

STATE_ALL_LOADED = "all_loaded"
STATE_PARTIALLY_LOADED = "partially_loaded"
STATE_NONE_LOADED = "none_loaded"
STATE_NOT_APPLICABLE = "not_applicable"

_TRUNCATED_MARK = "…(技能内容过长，已截断)"  # 与 skill_loader._TRUNCATED_MARK 同字面


def verify_loaded(
    expected_skills: list[dict], section: str, bodies: list[tuple[str, str]] | None = None
) -> list[dict]:
    """逐技能装载验证（纯函数）。expected_skills=预期注入清单，section=注入段全文。

    bodies（render_local_section 返回的逐技能体）在场时按体验证（更准）；
    不在场时按 "### {name}" 头 + 内容前缀验证。不猜、不编造——查不到就是
    none_loaded。
    """
    if not expected_skills:
        return []
    section = section or ""
    body_by_name = {name: body for name, body in (bodies or [])}
    out: list[dict] = []
    for skill in expected_skills:
        name = str(skill.get("name", "") or "")
        if not section.strip():
            state = STATE_NONE_LOADED
        elif name in body_by_name:
            body = body_by_name[name]
            content = str(skill.get("content", "") or "")
            if _TRUNCATED_MARK in body:
                state = STATE_PARTIALLY_LOADED
            elif not content or content in body or content in section:
                state = STATE_ALL_LOADED
            elif body.strip() and body.strip() in section:
                state = STATE_PARTIALLY_LOADED
            else:
                state = STATE_NONE_LOADED
        else:
            marker = f"### {name}"
            content = str(skill.get("content", "") or "")
            if marker in section and (not content or content in section):
                state = STATE_ALL_LOADED
            elif marker in section:
                state = STATE_PARTIALLY_LOADED
            else:
                state = STATE_NONE_LOADED
        out.append({
            "skill_id": skill.get("id", ""),
            "name": name,
            "state": state,
            "match_reason": skill.get("match_reason", ""),
        })
    return out


def overall_state(states: list[dict]) -> str:
    """整回合装载态：worst-of（none > partial > all）；空清单=not_applicable。"""
    if not states:
        return STATE_NOT_APPLICABLE
    values = {s["state"] for s in states}
    if STATE_NONE_LOADED in values:
        return STATE_NONE_LOADED
    if STATE_PARTIALLY_LOADED in values:
        return STATE_PARTIALLY_LOADED
    return STATE_ALL_LOADED


def write_receipt(
    session_id: str,
    states: list[dict],
    section_tokens: int = 0,
    extra: dict | None = None,
    path: str | None = None,
) -> dict:
    """追加装载收据（jsonl 单行）。写失败只记日志不抛（旁路纪律）。"""
    receipt = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id or "",
        "overall": overall_state(states),
        "skills": states,
        "section_tokens": int(section_tokens),
    }
    if extra:
        receipt.update(extra)
    target = path or os.environ.get("SOULMATE_SKILL_LOAD_RECEIPTS") or RECEIPT_PATH
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        with open(target, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.debug("skill load receipt write failed: %s", e)
    return receipt


def read_receipts(limit: int = 50, path: str | None = None) -> list[dict]:
    """读最近收据（验收脚本断言用）。文件不存在=空列表。"""
    target = path or os.environ.get("SOULMATE_SKILL_LOAD_RECEIPTS") or RECEIPT_PATH
    try:
        with open(target, encoding="utf-8") as fp:
            lines = fp.readlines()
    except FileNotFoundError:
        return []
    out: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
