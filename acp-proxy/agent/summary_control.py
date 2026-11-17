# -*- coding: utf-8 -*-
"""summary_control.py — task_complete 免总结（Pi piSummaryControl 同语义）。

来源：《取长补短清单》#12——OpenBidKit 给每个工具 Schema 追加 task_complete
参数 + afterToolCall 置 terminate，复用 Pi 原生"**整批工具全 terminate 才提前
结束**"规则（agent-loop.ts shouldTerminateToolBatch），省掉末尾一次总结性模型
请求（长流水线逐节生成时省钱效果显著）。

SoulMate 落地（能力层，纯函数核心独立可测）：
- `param_schema()`：task_complete 参数 JSON Schema 片段（工具定义统一追加）；
- `SummaryControl`：按**批**（同一 assistant 消息的 tool_calls 批）记账——
  `record(func_args, result)` 每次工具调用后记一笔；`batch_terminate()` =
  批内**每一个**调用都显式 task_complete=true 才为真（整批语义，缺一不提前收）；
  `consume()` 取走结论（工具输出拼接即最终交付文本）并复位；
- `strip_meta(args)`：工具执行面剔除元参数（task_complete 不进业务参数）。

纪律：默认关闭——不带参数的调用零行为变化；部分带参数=不整批=不提前收
（防止"半批胡写直接交差"）。soulmate_agent 主循环消费（tests/test_guard_tools.py
独立可测语义）。
"""
from __future__ import annotations

__all__ = [
    "TASK_COMPLETE_ARG",
    "SummaryControl",
    "param_schema",
    "strip_meta",
]

TASK_COMPLETE_ARG = "task_complete"


def param_schema() -> dict:
    """task_complete 参数片段（追加进工具 parameters.properties）。"""
    return {
        TASK_COMPLETE_ARG: {
            "type": "boolean",
            "description": (
                "任务已完成、工具输出即最终交付时置 true（免总结收尾：本批工具"
                "调用全部带 true 时直接收尾，不再发起总结性追问）。⚠️ 只在确信"
                "任务完成时使用；批内只要有一个调用没带 true 就不会提前收尾"),
        },
    }


def strip_meta(args: dict) -> dict:
    """剔除元参数后的业务参数副本（工具执行面用）。"""
    return {k: v for k, v in dict(args or {}).items() if k != TASK_COMPLETE_ARG}


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


class SummaryControl:
    """批级免总结记账器（整批 terminate 语义）。"""

    def __init__(self):
        self._records: list[dict] = []

    def start_batch(self) -> None:
        """开启新批（清空上批残留；一批 = 一个 assistant 消息的全部工具调用）。"""
        self._records = []

    def record(self, func_args: dict, result: str = "") -> None:
        args = dict(func_args or {})
        self._records.append({
            "task_complete": _truthy(args.get(TASK_COMPLETE_ARG)),
            "result": str(result or ""),
        })

    def batch_terminate(self) -> bool:
        """整批 terminate：批非空 且 每个调用都显式 task_complete=true。"""
        return bool(self._records) and all(r["task_complete"] for r in self._records)

    def consume(self) -> dict:
        """取走结论并复位。{"terminate", "final_text", "count"}。"""
        records = self._records
        self._records = []
        terminate = bool(records) and all(r["task_complete"] for r in records)
        texts = [r["result"].strip() for r in records if r["result"].strip()]
        return {
            "terminate": terminate,
            "final_text": "\n".join(texts),
            "count": len(records),
        }
