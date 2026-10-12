"""反射产生源（路线图 20.2）——确定性时刻接 ReflectionEngine.record_reflection。

背景（周期#129 考古实证）：ReflectionEngine.record_reflection 写入面存在但产品链路
零产生源，reflections.db 恒 0 行 → bootstrap_kpi 反射 KPI 恒 available=False。

产生源（确定性时刻，纯事实合成，无 LLM、不硬造内容）：
  ① 自验收尾出结论 —— SelfVerifier.verify 产出结论（多需求项或有缺口）时
  ② 补做自愈完成   —— 拒做补做 / 自验补做收口（成功与失败都记，语义不同）
  ③ 工具失败恢复成功 —— _tool_recovery_execute 重试/换路径后 recovered

纪律：
  - fail-safe 旁路：record_safely 吞掉一切异常（含建库失败），绝不冒泡影响主流程
  - 与 bootstrap_kpi._edit_reflection 消费面对齐（COUNT/SUM(applied)/AVG(quality_score)
    即可观测，无需额外接线）
  - 读侧已有消费：ReflectionEngine.get_improvement_context 注入 prompt（quality<0.7
    的行带弱点/建议入上下文）——写入→存储→消费闭环成立
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("acp-proxy.reflection_sources")

# 各产生源的确定性质量分（非 LLM 评分：按时刻语义固定档位）
_SCORE_SELF_VERIFY = None  # 用 report.coverage（真实覆盖率）
_SCORE_REMEDIATION_OK = 0.8
_SCORE_REMEDIATION_FAIL = 0.3
_SCORE_TOOL_RECOVERY = 0.8

# 任务描述截断（对齐 token 预算纪律，内容不硬造只截取事实）
_DESC_MAX = 200
_SUMMARY_MAX = 300


def record_safely(
    engine: Any,
    *,
    session_id: str,
    task_description: str,
    result_summary: str,
    quality_score: float,
    strengths: Optional[list[str]] = None,
    weaknesses: Optional[list[str]] = None,
    improvement_suggestions: Optional[list[str]] = None,
    error_patterns: Optional[list[str]] = None,
) -> bool:
    """写一条反思（fail-safe：任何异常只记日志，返回 False，绝不冒泡）。"""
    try:
        if engine is None:
            # lazy-init 对齐 __new__ 构造纪律：调用方传 None 时自举默认引擎
            from agent.reflection_engine import ReflectionEngine
            engine = ReflectionEngine()
        engine.record_reflection(
            session_id=session_id,
            task_description=task_description[:_DESC_MAX],
            result_summary=result_summary[:_SUMMARY_MAX],
            quality_score=quality_score,
            strengths=list(strengths or []),
            weaknesses=list(weaknesses or []),
            improvement_suggestions=list(improvement_suggestions or []),
            error_patterns=list(error_patterns or []),
        )
        return True
    except Exception as e:  # fail-safe 旁路（与 compact 账本同款纪律）
        logger.debug(f"[reflection-sources] 记录失败(非致命): {e}")
        return False


def from_self_verify(
    engine: Any,
    session_id: str,
    user_text: str,
    report: Any,
    remediated: bool = False,
) -> bool:
    """产生源①：自验收尾出结论（多需求项或有缺口时的收口结论）。

    report = SelfVerifier.verify() 的 VerifyReport：items / coverage /
    missing_requirements() / uncertain_requirements()。
    """
    try:
        items = getattr(report, "items", None) or []
        if not items:
            return False
        missing = list(report.missing_requirements())
        uncertain = list(report.uncertain_requirements())
        met = [i.requirement for i in items if getattr(i, "status", "") == "met"]
        score = float(getattr(report, "coverage", 0.5) or 0.5)
        suggestions = []
        if missing:
            suggestions.append("未覆盖项走补做自愈（上限1轮）后复检")
        if uncertain:
            suggestions.append("uncertain 项下次提供可验证证据（命令输出/文件回读）")
        summary = (
            f"自验结论: items={len(items)} coverage={score:.2f} "
            f"met={len(met)} missing={len(missing)} uncertain={len(uncertain)} "
            f"remediated={remediated}"
        )
        return record_safely(
            engine,
            session_id=session_id,
            task_description=user_text or "(空输入)",
            result_summary=summary,
            quality_score=score,
            strengths=(
                [f"已覆盖: {r[:80]}" for r in met[:3]] or ["自验完成并出结论"]
            ),
            weaknesses=[f"未覆盖: {r[:80]}" for r in missing[:3]]
            + [f"待证: {r[:80]}" for r in uncertain[:3]],
            improvement_suggestions=suggestions,
            error_patterns=[f"self_verify_missing:{r[:60]}" for r in missing[:3]],
        )
    except Exception as e:
        logger.debug(f"[reflection-sources] 自验产生源失败(非致命): {e}")
        return False


def from_remediation(
    engine: Any,
    session_id: str,
    source: str,
    tool_name: str,
    succeeded: bool,
    user_text: str = "",
) -> bool:
    """产生源②：补做自愈完成（拒做补做 / 自验补做收口；成功与失败都记）。"""
    label = {"refusal-remedy": "拒做补做", "self-verify": "自验补做"}.get(
        source, source)
    if succeeded:
        return record_safely(
            engine,
            session_id=session_id,
            task_description=user_text or f"{label}: {tool_name}",
            result_summary=f"{label}自愈成功: {tool_name}",
            quality_score=_SCORE_REMEDIATION_OK,
            strengths=[f"{label}自愈成功: {tool_name} 真实执行并收口"],
            error_patterns=[f"remediation_triggered:{source}:{tool_name}"],
        )
    return record_safely(
        engine,
        session_id=session_id,
        task_description=user_text or f"{label}: {tool_name}",
        result_summary=f"{label}未自愈: {tool_name} 仍未执行",
        quality_score=_SCORE_REMEDIATION_FAIL,
        weaknesses=[f"{label}后 {tool_name} 仍未执行"],
        improvement_suggestions=[
            f"下次直接调用 {tool_name} 完成请求，不以工具缺失为由拒做"
        ],
        error_patterns=[f"remediation_failed:{source}:{tool_name}"],
    )


def from_tool_recovery(
    engine: Any,
    session_id: str,
    func_name: str,
    attempt: int,
    action: str,
    kind: str,
    first_result: str = "",
) -> bool:
    """产生源③：工具失败恢复成功（_tool_recovery_execute recovered 时刻）。"""
    return record_safely(
        engine,
        session_id=session_id,
        task_description=f"工具失败恢复: {func_name}",
        result_summary=(
            f"{func_name} 失败({kind})后第{attempt}次{action}恢复成功"
        ),
        quality_score=_SCORE_TOOL_RECOVERY,
        strengths=[f"失败恢复成功: {func_name} 第{attempt}次{action}"],
        error_patterns=[f"tool_failure:{func_name}:{kind}"],
    )
