# -*- coding: utf-8 -*-
"""执行旁白（完成度/决定类）路由 —— 回复正文只留回答，自评旁白只进日志/轨迹。

背景（evo 目标 2026-10-23 03:32「回复正文不得混入执行旁白（完成度/决定类），需有回归测试」）：
工具循环的"周期进展自评（推理门）"每隔 N 轮注入一条【进展自评】用户消息，请求模型
评估"①距目标的完成度 ②是否在重复同一失败 ③决定：继续/换策略/收尾"。该回答与用户
回答同走 agent_message_chunk 通道、同进 full_response——执行旁白（完成度/决定类）
混进回复正文 = 用户在聊天里看到执行旁白。治理铁律3：同一信息禁双通道（日志/轨迹=
有用通道，聊天正文=回答本身）。

生产者契约（与 frame_origin 同哲学：标记常量单一真源，生产者改措辞不改常量，测试即红）：
- 自评请求（build_self_assess_prompt）要求自评写成以 ASSESS_PREFIX 开头的行（或①②③
  分行），且明确"自评行只进执行日志、不进回复正文"；收尾总结写在自评行之后的正文里。
- 采集侧（NarrationSplitter）只在**自评轮**（自评请求的下一轮）按行做前缀匹配剥旁白，
  正文中间出现同类字样不误伤；剥出的旁白走 logger + trajectory，回复正文只留回答。

fail-safe 方向（安全方向=宁可多显示，不吞用户答案）：识别不出的行一律归正文。
"""

from __future__ import annotations

import re

# ── 生产者契约标记（单一真源）──
ASSESS_PREFIX = "【自评】"           # 模型自评行的帧首标记（生产者契约）
REQUEST_PREFIX = "【进展自评】"       # 注入给模型的自评请求帧首标记

# 旁白行判别（只判行首，正文中间出现同类字样不误伤）：
# ① 帧首标记行（【自评】/【进展自评】/【执行旁白】）
# ② 自评请求的①②③分项行（"①距目标的完成度…"③决定：…"）
# ③ 完成度/决定/重复失败 类旁白行首（"完成度约80%"、"决定：继续"）
_MARKER_LINE_RE = re.compile(r"^\s*(【自评】|【进展自评】|【执行旁白】)")
_ITEM_LINE_RE = re.compile(r"^\s*[①②③④⑤⑥]")
_NARRATION_HEAD_RE = re.compile(
    r"^\s*((距)?目标(的)?)?完成度"
    r"|^\s*决定\s*[：:]"
    r"|^\s*是否(在)?重复"
    r"|^\s*自评\s*[：:]"
)

# ①②③分项行内的旁白关键词（分项行只有命中这些词才判旁白——纯编号正文不误伤）
_ITEM_KEYWORDS = ("完成度", "重复", "决定", "收尾", "换策略")


def build_self_assess_prompt(rounds: int) -> str:
    """周期进展自评请求（推理门）——生产者契约：自评写 ASSESS_PREFIX 行，只进日志。"""
    return (
        f"{REQUEST_PREFIX}你已执行 {rounds} 轮工具调用。请先写一行以「{ASSESS_PREFIX}」"
        "开头的执行自评：①距目标的完成度 ②是否在重复同一失败 ③决定：继续/换策略/收尾。"
        f"自评行属于执行旁白，只进执行日志、不会展示给用户——回复正文里不要出现"
        "完成度/决定类旁白。自评行之后：若继续/换策略，直接调用工具（不要再写旁白）；"
        "若收尾，直接输出最终总结（总结是给用户的正文，不要复述自评）。"
    )


def is_narration_line(line: str) -> bool:
    """单行是否执行旁白（完成度/决定类）。只判行首前缀，正文中间不误伤。"""
    s = (line or "").rstrip()
    if not s:
        return False
    if _MARKER_LINE_RE.match(s):
        return True
    if _ITEM_LINE_RE.match(s):
        return any(k in s for k in _ITEM_KEYWORDS)
    return bool(_NARRATION_HEAD_RE.match(s))


class NarrationSplitter:
    """自评轮流式拆分器：feed 增量 chunk → (正文增量, 旁白增量)；flush 收尾余量。

    行级前缀匹配（chunk 流无帧分隔符，按完整行判别）；旁白只取**正文之前的领先块**
    （自评行在前、总结在后的生产者契约），一旦正文行出现，后续同类字样=正文不误伤。
    前导空行随第一个实质行的归属走（旁白块后的空行=分隔行随旁白，正文前导空行随正文）。
    """

    def __init__(self) -> None:
        self._buf = ""
        self._leading = True
        self._blanks: list[str] = []
        self.narration_text = ""   # 已剥出的旁白全文（日志/轨迹通道）

    def _drain(self, final: bool) -> tuple[str, str]:
        body_out: list[str] = []
        narr_out: list[str] = []
        while True:
            if "\n" in self._buf:
                line, self._buf = self._buf.split("\n", 1)
                piece = line + "\n"
            elif final and self._buf:
                piece, self._buf = self._buf, ""
            else:
                break
            stripped = piece.rstrip("\n")
            if self._leading:
                if not stripped.strip():
                    self._blanks.append(piece)      # 前导空行先扣留，随首个实质行走
                    continue
                if is_narration_line(stripped):
                    if self._blanks:
                        b = "".join(self._blanks)
                        self._blanks = []
                        narr_out.append(b)
                        self.narration_text += b
                    narr_out.append(piece)
                    self.narration_text += piece
                else:
                    self._leading = False
                    if self._blanks:
                        b = "".join(self._blanks)
                        self._blanks = []
                        body_out.append(b)
                    body_out.append(piece)
            else:
                body_out.append(piece)
        if final and self._blanks:
            b = "".join(self._blanks)
            self._blanks = []
            if self._leading and not self.narration_text:
                body_out.append(b)                  # 全程零旁白=纯正文前导空行归正文
            else:
                narr_out.append(b)
                self.narration_text += b
        return "".join(body_out), "".join(narr_out)

    def feed(self, chunk: str) -> tuple[str, str]:
        self._buf += chunk or ""
        return self._drain(final=False)

    def flush(self) -> tuple[str, str]:
        return self._drain(final=True)


def split_leading_narration(text: str) -> tuple[str, str]:
    """整段拆分（非流式入口/测试用）：返回 (正文, 旁白)。"""
    sp = NarrationSplitter()
    body, narr = sp.feed(text or "")
    b2, n2 = sp.flush()
    return body + b2, narr + n2
