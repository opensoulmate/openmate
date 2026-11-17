# -*- coding: utf-8 -*-
"""guard_tools.py — SoulMate 防跑飞治理工具组（Pi 取长补短 #7/#3/#12 触发面）。

工具形状 = MCP 工具契约（function-calling），对齐 writing_tools 分发先例：
`tool_defs()` 进 LLM 工具表，`run(func_name, args, session_id)` 主循环与
code-mode 批内共用。两件套（**通用任务治理能力**，长文档/长流水线/子代理
全场景复用，非投标专用）：

- run_guard    防跑飞三件套：configure（建/重置：轮数上限默认 100、无进展
               看门狗默认 480s、空转阈值默认 3）/ beat 记一轮 / progress /
               pause|resume（ask-user 等待期暂停计时）/ check 处决结论
               （ok|warn|stop + 原因）/ status / extract 会话摘录（JSONL
               执行记录，诊断子任务输入）/ diagnose 空转诊断
               （continue|wrap_up|stop 结论）
- repair_loop  三段式提交修复：start（立验收目标）/ step 吃验收结果出决策
               （accept|repair|stop + 结构化修复提示词）/ prompt 取模板 /
               status / reset——normal→change-strategy→minimum，stalled≥2
               无阻塞带遗留接受，minimum 只修阻塞，仍不行才停（失败可见）

task_complete 免总结：本组与写作组工具统一支持 task_complete 参数
（summary_control 参数面，整批 terminate 语义省末尾总结请求）。

纪律：参数非法/依赖缺失显式报因禁静默；状态按 session 持有（有界不膨胀）；
执行体 agent/run_guard.py / repair_loop.py（纯函数核心独立可测），本模块只做
参数面+结果呈现（分层铁律）。verdict=stop 只出结论不硬杀（用户铁律：任务禁
硬超时杀——处决由编排层显式决定）。
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from agent import repair_loop, run_guard, summary_control

logger = logging.getLogger("agent.guard_tools")

GUARD_ACTIONS = ("configure", "beat", "progress", "pause", "resume",
                 "check", "status", "extract", "diagnose")
REPAIR_ACTIONS = ("start", "step", "prompt", "status", "reset")

TOOL_NAMES = ("run_guard", "repair_loop")


def tool_defs() -> list[dict]:
    """治理工具组 function-calling 定义（含 task_complete 参数面）。"""
    tc = summary_control.param_schema()
    return [
        {
            "type": "function",
            "function": {
                "name": "run_guard",
                "description": (
                    "防跑飞三件套（长任务/长流水线/子代理治理）：无进展看门狗"
                    "（默认 480s 无实质进展判 stop）+ 轮数上限（默认 100）+ "
                    "空转诊断（同工具同参数重复即空转，给 continue/wrap_up/"
                    "stop 结论）。action=configure 建/重置（可调 max_turns/"
                    "no_progress_timeout/stall_threshold）/ beat 记一轮"
                    "（tool+args，progress=true 算实质进展）/ progress 手动标进展"
                    " / pause|resume（等用户/等外部时暂停无进展计时）/ check 取"
                    "处决结论（ok|warn|stop+原因）/ status 状态快照 / extract "
                    "会话摘录（JSONL 执行记录，供诊断子任务）/ diagnose 空转诊断"
                    "。⚠️ stop 只是结论不硬杀；查到 stop 应收尾交付已有产物并"
                    "如实报告，不要继续空转烧轮次。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(GUARD_ACTIONS)},
                        "max_turns": {"type": "integer",
                                      "description": "configure：轮数上限（默认 100）"},
                        "no_progress_timeout": {"type": "number",
                                                "description": "configure：无进展秒数（默认 480）"},
                        "stall_threshold": {"type": "integer",
                                            "description": "configure：同签名重复判空转阈值（默认 3）"},
                        "tool": {"type": "string", "description": "beat：工具名/步骤名"},
                        "args": {"type": "object", "description": "beat：调用参数（签名判重）"},
                        "progress": {"type": "boolean",
                                     "description": "beat：本轮是否算实质进展（默认 true）"},
                        "limit": {"type": "integer", "description": "extract：摘录条数（默认 20）"},
                        **tc,
                    },
                    "required": ["action"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "repair_loop",
                "description": (
                    "三段式提交修复循环（产物验收不过→结构化修复→再验收）："
                    "程序验收（如 fact_lock check / doc_blank scan 的输出）喂 "
                    "step，出 accept（通过）/ repair（附修复提示词退回同会话）/"
                    "stop（最低目标仍失败，如实报告禁假称完成）。修复策略 "
                    "normal（逐条修）→ change-strategy（连续两轮无进展换方法"
                    "重写）→ minimum（只修阻塞项，非阻塞遗留带标注接受）；"
                    "stalled≥2 且无阻塞项直接带遗留接受，不死磕烧 token。"
                    "action=start 立目标 / step 吃验收结果（validation 对象）"
                    " / prompt 取某模式修复模板 / status / reset。"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": list(REPAIR_ACTIONS)},
                        "goal": {"type": "string", "description": "start：验收目标描述"},
                        "validation": {
                            "type": "object",
                            "description": (
                                "step：{ok: bool, blocking?: [问题id...], "
                                "issues?: [{id, severity: blocking|minor, "
                                "detail}...] | [字符串问题...]}"),
                        },
                        "mode": {"type": "string", "enum": list(repair_loop.MODES),
                                 "description": "prompt：修复模式（默认当前模式）"},
                        "issues": {"type": "array", "items": {"type": "object"},
                                   "description": "prompt：问题清单"},
                        **tc,
                    },
                    "required": ["action"],
                },
            },
        },
    ]


class GuardTools:
    """治理工具组（run_guard / repair_loop）。状态按 session 持有（有界）。"""

    def __init__(self, now=None):
        self._now = now or time.time
        self._states: dict[str, dict[str, Any]] = {}

    # ── 分发 ─────────────────────────────────────────────────────────

    def run(self, func_name: str, func_args: dict, session_id: str = "") -> str:
        func_name = str(func_name or "").strip()
        args = summary_control.strip_meta(func_args)   # task_complete 元参数剔除
        try:
            if func_name == "run_guard":
                return self._run_guard(args, session_id)
            if func_name == "repair_loop":
                return self._repair_loop(args, session_id)
            return (f"❌ 未知治理工具: {func_name!r}。支持: {', '.join(TOOL_NAMES)}")
        except ValueError as e:
            return f"❌ 参数错误: {e}"
        except RuntimeError as e:
            return f"❌ 依赖不可用: {e}"
        except Exception as e:  # noqa: BLE001 — 显式报因禁静默
            logger.exception("guard tool failed: %s", func_name)
            return f"❌ 执行失败（{func_name}）: {type(e).__name__}: {e}"

    def _state(self, session_id: str) -> dict:
        sid = str(session_id or "default")
        st = self._states.get(sid)
        if st is None:
            st = {"guard": run_guard.RunGuard(now=self._now),
                  "repair": repair_loop.RepairLoop()}
            self._states[sid] = st
            if len(self._states) > 50:   # 有界：会话状态不无限膨胀
                self._states.pop(next(iter(self._states)))
        return st

    # ── run_guard ────────────────────────────────────────────────────

    def _run_guard(self, args: dict, session_id: str) -> str:
        action = str(args.get("action", "") or "").strip().lower()
        st = self._state(session_id)
        if action == "configure":
            g = run_guard.RunGuard(
                max_turns=int(args.get("max_turns") or run_guard.DEFAULT_MAX_TURNS),
                no_progress_timeout=float(
                    args.get("no_progress_timeout") or run_guard.DEFAULT_NO_PROGRESS_S),
                stall_threshold=int(
                    args.get("stall_threshold") or run_guard.DEFAULT_STALL_THRESHOLD),
                now=self._now)
            st["guard"] = g
            return (f"✅ 防跑飞守卫已配置：轮数上限 {g.max_turns}，无进展看门狗 "
                    f"{g.no_progress_timeout:.0f}s，空转阈值 {g.stall_threshold} 次"
                    f"（verdict=stop 只出结论不硬杀）")
        g = st["guard"]
        if action == "beat":
            chk = g.record_turn(
                str(args.get("tool", "") or ""),
                args.get("args") if isinstance(args.get("args"), dict) else {},
                progress=bool(args.get("progress", True)))
            return _format_check("已记一轮", chk, g)
        if action == "progress":
            g.record_progress()
            return "✅ 已标记实质进展（无进展看门狗重新计时）"
        if action == "pause":
            g.pause()
            return "✅ 无进展计时已暂停（等用户/等外部期间不计入）"
        if action == "resume":
            g.resume()
            return "✅ 无进展计时已恢复"
        if action == "check":
            return _format_check("处决结论", g.check(), g)
        if action == "status":
            return "✅ 状态快照：\n" + json.dumps(
                g.snapshot(), ensure_ascii=False, indent=2)
        if action == "extract":
            limit = int(args.get("limit") or 20)
            rec = g.extract_session_record(limit=limit)
            return ("✅ 会话摘录（JSONL 执行记录，可喂诊断子任务）：\n"
                    + (rec or "（暂无执行记录）"))
        if action == "diagnose":
            d = g.diagnose()
            return ("✅ 空转诊断：\n" + json.dumps(d, ensure_ascii=False, indent=2)
                    + "\n结论口径：continue=继续 / wrap_up=收尾交付现有产物 / "
                      "stop=停止并如实报告")
        return f"❌ 参数错误: 未知 action={action!r}（支持 {'/'.join(GUARD_ACTIONS)}）"

    # ── repair_loop ──────────────────────────────────────────────────

    def _repair_loop(self, args: dict, session_id: str) -> str:
        action = str(args.get("action", "") or "").strip().lower()
        st = self._state(session_id)
        if action == "start":
            st["repair"] = repair_loop.RepairLoop(
                goal=str(args.get("goal", "") or ""))
            return (f"✅ 三段式修复循环已启动（normal→change-strategy→minimum）"
                    f"：目标：{st['repair'].goal or '（未填）'}")
        r = st["repair"]
        if action == "step":
            validation = args.get("validation")
            if not isinstance(validation, dict):
                return "❌ 参数错误: step 需要 validation 对象 {ok, blocking?, issues?}"
            d = r.step(validation)
            lines = [f"✅ 修复决策：{d['action']}（模式 {d['mode']}）——{d['reason']}"]
            if d.get("blocking"):
                lines.append(f"阻塞项：{', '.join(d['blocking'])}")
            if d["action"] == "repair" and d.get("prompt"):
                lines.append("")
                lines.append("── 修复提示词（退回同会话执行）──")
                lines.append(d["prompt"])
            return "\n".join(lines)
        if action == "prompt":
            mode = str(args.get("mode", "") or "") or r.mode
            issues = args.get("issues") or []
            if not isinstance(issues, list):
                return "❌ 参数错误: prompt 的 issues 需为列表"
            norm = []
            for i, it in enumerate(issues):
                if isinstance(it, dict):
                    norm.append({"id": str(it.get("id", f"issue-{i+1}")),
                                 "severity": str(it.get("severity", "minor")),
                                 "detail": str(it.get("detail", "") or "")})
                else:
                    norm.append({"id": f"issue-{i+1}", "severity": "minor",
                                 "detail": str(it)})
            return "✅ 修复提示词模板：\n" + r.build_prompt(mode, norm)
        if action == "status":
            return "✅ 修复循环状态：\n" + json.dumps(
                r.status(), ensure_ascii=False, indent=2)
        if action == "reset":
            return "✅ 修复循环已重置：\n" + json.dumps(
                r.reset(str(args.get("goal", "") or "")), ensure_ascii=False, indent=2)
        return f"❌ 参数错误: 未知 action={action!r}（支持 {'/'.join(REPAIR_ACTIONS)}）"


def _format_check(head: str, chk: dict, g: run_guard.RunGuard) -> str:
    mark = {"ok": "✅", "warn": "⚠️", "stop": "🛑"}.get(chk["verdict"], "")
    line = f"{mark} {head}：verdict={chk['verdict']}"
    if chk.get("reason"):
        line += f"（{chk['reason']}）"
    detail = chk.get("detail") or {}
    if detail:
        line += "\n" + json.dumps(detail, ensure_ascii=False, indent=2)
    return line
