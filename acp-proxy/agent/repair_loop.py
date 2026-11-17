# -*- coding: utf-8 -*-
"""repair_loop.py — 三段式提交修复循环（Pi piSubmissionPolicy 同语义）。

来源：《取长补短清单》#3——OpenBidKit 提交校验-修复双轨：程序验收
（validateOutput）→ **三段式修复策略 normal → change-strategy → minimum**：
逐条修复（normal）连续两轮无进展则换方法重写（change-strategy）；再无进展进入
"最低完成目标"（minimum，只修阻塞项）；仍不行才停。**stalled≥2 且无阻塞项
即接受**（非阻塞遗留项带标注接受，不死磕烧 token）。

SoulMate 落地（能力层，纯 Python 确定性状态机独立可测）：Agent 可以胡写，
程序兜底验收——`RepairLoop.step(validation)` 吃验收结果（ok/blocking/issues），
出 {"action": accept|repair|stop, "mode", "prompt"}：repair 附**结构化修复
提示词**（逐条列问题+模式指令+遗留标注纪律）退回同会话继续，accept=通过
（含"stalled 无阻塞带遗留接受"），stop=最低目标仍失败（失败必须可见）。

进度判定（确定性）：上轮问题有任何一个被修掉（问题 id 消失）=有进展；
一轮下来一个没修掉=stalled+1。
独立可测（tests/test_guard_tools.py）；与 fact_lock.check / doc_blank.scan 等
程序校验器天然对接（验收输入=校验器输出）。
"""
from __future__ import annotations

__all__ = [
    "MODES",
    "ACTION_ACCEPT",
    "ACTION_REPAIR",
    "ACTION_STOP",
    "RepairLoop",
]

MODES = ("normal", "change_strategy", "minimum")
ACTION_ACCEPT = "accept"
ACTION_REPAIR = "repair"
ACTION_STOP = "stop"


class RepairLoop:
    """三段式提交修复状态机（normal → change-strategy → minimum）。"""

    def __init__(self, goal: str = "", max_stall: int = 2):
        self.goal = str(goal or "")
        self.max_stall = int(max_stall)
        if self.max_stall < 1:
            raise ValueError(f"max_stall 需 ≥1，收到 {max_stall}")
        self._mode = MODES[0]
        self._stall = 0
        self._prev_issue_ids: set[str] | None = None
        self._rounds: list[dict] = []

    # ── 状态 ─────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def status(self) -> dict:
        return {
            "goal": self.goal,
            "mode": self._mode,
            "stall_count": self._stall,
            "rounds": len(self._rounds),
            "history": [dict(r) for r in self._rounds],
        }

    def reset(self, goal: str = "") -> dict:
        self.__init__(goal=goal or self.goal, max_stall=self.max_stall)
        return self.status()

    # ── 主入口：验收一步 ─────────────────────────────────────────────

    def step(self, validation: dict) -> dict:
        """吃一轮验收结果，出决策 {"action", "mode", "prompt", "reason", ...}。

        validation：{"ok": bool, "blocking": [str...],
                     "issues": [{"id", "severity", "detail"}...] | [str...]}
        """
        ok, blocking, issues = _parse_validation(validation)

        # 通过 → 接受
        if ok and not blocking:
            return self._decide(ACTION_ACCEPT, None,
                                "验收通过，接受产物", issues)

        # 进度判定：上轮问题有任一被修掉（resolved 非空）=有进展（否则 stalled+1）
        cur_ids = {i["id"] for i in issues}
        if self._prev_issue_ids is not None:
            resolved = self._prev_issue_ids - cur_ids
            if resolved:
                self._stall = 0
            else:
                self._stall += 1
        self._prev_issue_ids = cur_ids

        # stalled≥2 且无阻塞项 → 带遗留接受（Pi 口径：不烧 token 死磕非阻塞项）
        if not blocking and self._stall >= self.max_stall:
            return self._decide(
                ACTION_ACCEPT, None,
                f"连续 {self._stall} 轮无进展且无阻塞项——按三段式口径接受产物，"
                f"非阻塞遗留项带标注（不烧 token 死磕）", issues)

        # 模式推进（有阻塞必修）：normal --stall--> change_strategy --stall-->
        # minimum；minimum 再 stall 且阻塞仍在 → 停（失败必须可见）
        if self._stall >= self.max_stall:
            if self._mode == MODES[0]:
                self._mode = MODES[1]
            elif self._mode == MODES[1]:
                self._mode = MODES[2]
            else:  # minimum 仍无进展
                return self._decide(
                    ACTION_STOP, None,
                    "最低完成目标（minimum）修复后仍无进展且阻塞项仍在——停止并"
                    "如实报告失败（禁假称完成）", issues, blocking=blocking)
            self._stall = 0

        prompt = self.build_prompt(self._mode, issues, blocking)
        return self._decide(ACTION_REPAIR, prompt,
                            f"退回同会话修复（模式 {self._mode}）", issues,
                            blocking=blocking)

    # ── 修复提示词（三段式模板） ─────────────────────────────────────

    def build_prompt(self, mode: str, issues: list, blocking=None) -> str:
        """结构化修复提示词（normal=逐条修 / change-strategy=换方法重写 /
        minimum=只修阻塞+遗留标注）。"""
        if mode not in MODES:
            raise ValueError(f"mode 非法: {mode!r}（支持 {MODES}）")
        blocking = blocking or [i for i in issues if i.get("severity") == "blocking"]
        lines = []
        if self.goal:
            lines.append(f"目标：{self.goal}")
        if mode == MODES[0]:
            lines.append("【修复指令·normal】逐条修复下列问题，保持其余内容不动：")
        elif mode == MODES[1]:
            lines.append("【修复指令·change-strategy】上一方法已连续无进展——"
                         "**换一种方法**重写问题所在段落/结构（不要重复原修法）：")
        else:
            lines.append("【修复指令·minimum·最低完成目标】只修复阻塞项（blocking），"
                         "其余非阻塞遗留项**带标注接受**（在文末遗留清单列出），不要再扩写：")
        for i in issues:
            mark = "🔴阻塞" if i.get("severity") == "blocking" else "🟡建议"
            lines.append(f"  {mark} [{i['id']}] {i['detail']}")
        if mode == MODES[2]:
            lines.append(f"仅需修复阻塞项：{', '.join(i['id'] for i in blocking) or '（无）'}")
        lines.append("修完后重新提交验收；禁编造缺失资料（缺料走留白登记）。")
        return "\n".join(lines)

    def _decide(self, action: str, prompt, reason: str, issues: list,
                blocking=None) -> dict:
        rec = {"action": action, "mode": self._mode, "prompt": prompt,
               "reason": reason,
               "issue_ids": sorted(i["id"] for i in issues),
               "stall_count": self._stall}
        if blocking is not None:
            rec["blocking"] = [i["id"] for i in blocking]
        self._rounds.append(dict(rec))
        return rec


def _parse_validation(validation) -> tuple[bool, list, list]:
    """验收结果规范化 → (ok, blocking, issues[{id,severity,detail}])。"""
    if not isinstance(validation, dict):
        raise ValueError("validation 需为对象 {ok, blocking?, issues?}")
    issues_raw = validation.get("issues") or []
    if not isinstance(issues_raw, list):
        raise ValueError("issues 需为列表")
    issues = []
    for i, it in enumerate(issues_raw):
        if isinstance(it, str):
            issues.append({"id": f"issue-{i + 1}", "severity": "minor",
                           "detail": it})
        elif isinstance(it, dict):
            iid = str(it.get("id", "") or f"issue-{i + 1}")
            sev = str(it.get("severity", "minor") or "minor")
            detail = str(it.get("detail", "") or it.get("message", "") or "")
            issues.append({"id": iid,
                           "severity": "blocking" if sev == "blocking" else "minor",
                           "detail": detail})
        else:
            raise ValueError(f"issues[{i}] 需为字符串或对象")
    blocking_ids = {str(b) for b in (validation.get("blocking") or [])}
    blocking = [i for i in issues if i["id"] in blocking_ids
                or i["severity"] == "blocking"]
    ok = bool(validation.get("ok")) and not blocking
    return ok, blocking, issues
