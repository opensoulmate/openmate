# acp-proxy/evolution_cycle.py
# -*- coding: utf-8 -*-
"""
Evolution cycle result collection and success/failure determination.

Design goals
------------
1. Cycle success MUST be strictly equivalent to ``failed_count == 0``.
   An empty cycle (0 improvements planned / 0 improvements attempted /
   0 validation results) is considered a *successful, empty* cycle and
   must NOT be flagged as a failure.

2. Validation results are no longer collapsed to a single boolean.
   Each result carries a :class:`ValidationCategory` classification so
   that a cycle summary can distinguish functional regressions from
   metric-only misses and from skipped validations.

3. A structured summary dictionary is produced at cycle end with the
   breakdown: total_improvements / passed / failed_regression /
   failed_metric / skipped.

4. This module intentionally does NOT touch submit_plan.py nor any
   last_error lifecycle logic.

This is a self-contained module with no external dependencies beyond
the standard library.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Validation result classification
# ---------------------------------------------------------------------------


class ValidationCategory(str, Enum):
    """Classification of a single validation outcome.

    Members map 1:1 onto the categories required by the cycle summary::

        PASS_REGRESSION  -> passed
        FAIL_REGRESSION  -> failed_regression
        FAIL_METRIC      -> failed_metric
        FAIL_SKIPPED     -> skipped
    """

    # Validation passed, no functional regression detected.
    PASS_REGRESSION = "PASS_REGRESSION"

    # Functional regression detected (behaviour broke).
    FAIL_REGRESSION = "FAIL_REGRESSION"

    # Behaviour intact but the target metric did not improve / did not
    # reach the required threshold.
    FAIL_METRIC = "FAIL_METRIC"

    # Validation was skipped (missing harness, unavailable dependency,
    # manually skipped, etc.). Skipped validations are NOT failures.
    FAIL_SKIPPED = "FAIL_SKIPPED"

    # -- helpers ---------------------------------------------------------

    @property
    def is_failure(self) -> bool:
        """Only regressions and metric misses count as failures.

        ``FAIL_SKIPPED`` is explicitly NOT a failure: a skipped validation
        carries no evidence of breakage and must not flip the cycle to
        "failed".
        """
        return self in (ValidationCategory.FAIL_REGRESSION, ValidationCategory.FAIL_METRIC)

    @property
    def is_pass(self) -> bool:
        return self is ValidationCategory.PASS_REGRESSION

    @property
    def is_skipped(self) -> bool:
        return self is ValidationCategory.FAIL_SKIPPED

    @property
    def summary_key(self) -> str:
        """Map this category onto the summary dictionary key."""
        return {
            ValidationCategory.PASS_REGRESSION: "passed",
            ValidationCategory.FAIL_REGRESSION: "failed_regression",
            ValidationCategory.FAIL_METRIC: "failed_metric",
            ValidationCategory.FAIL_SKIPPED: "skipped",
        }[self]

    @property
    def log_label(self) -> str:
        """Human-readable label for structured log output."""
        return {
            ValidationCategory.PASS_REGRESSION: "功能验证通过",
            ValidationCategory.FAIL_REGRESSION: "功能回归失败",
            ValidationCategory.FAIL_METRIC: "指标未达标",
            ValidationCategory.FAIL_SKIPPED: "验证已跳过",
        }[self]


# ---------------------------------------------------------------------------
# Structured failure-logging helpers
# ---------------------------------------------------------------------------


def log_validation_failure(
    improvement_id: str,
    category: ValidationCategory,
    detail: Optional[Mapping[str, Any]] = None,
) -> None:
    """Emit a structured warning when a single improvement validation fails.

    Parameters
    ----------
    improvement_id
        Unique identifier of the improvement under validation.
    category
        The :class:`ValidationCategory` describing *why* it failed.
    detail
        Optional mapping with diagnostic context, e.g.
        ``{"baseline": ..., "current": ..., "threshold": ...}``.
    """
    if category.is_pass or category.is_skipped:
        return

    payload: Dict[str, Any] = {
        "event": "validation_failure",
        "improvement_id": improvement_id,
        "failure_category": category.value,
        "failure_label": category.log_label,
    }
    if detail:
        payload["detail"] = dict(detail)

    logger.warning("[验证失败] %s", payload)


def log_cycle_summary(summary: Mapping[str, Any]) -> None:
    """Emit a structured info-level summary at the end of a cycle.

    The *summary* mapping is expected to contain at minimum the keys
    ``total_improvements``, ``passed``, ``failed_regression``,
    ``failed_metric``, ``skipped`` and may carry the classification
    lists produced by :func:`build_cycle_summary`.
    """
    logger.info("[周期总结] %s", dict(summary))


@dataclass
class CycleSummary:
    """Aggregated, classified outcome of one evolution cycle.

    ``failed_count`` is computed from the two failure categories only;
    skipped validations never contribute to it.
    """

    total_improvements: int = 0
    passed: int = 0
    failed_regression: int = 0
    failed_metric: int = 0
    skipped: int = 0

    # -- detailed per-category lists ------------------------------------
    regression_failures: List[Dict[str, Any]] = field(default_factory=list)
    metric_failures: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def failed_count(self) -> int:
        return self.failed_regression + self.failed_metric

    @property
    def is_success(self) -> bool:
        return self.failed_count == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_improvements": self.total_improvements,
            "passed": self.passed,
            "failed_regression": self.failed_regression,
            "failed_metric": self.failed_metric,
            "skipped": self.skipped,
            "failed_count": self.failed_count,
            "is_success": self.is_success,
            "regression_failures": list(self.regression_failures),
            "metric_failures": list(self.metric_failures),
            "warnings": list(self.warnings),
        }


# Keep backwards-compatible module-level dict format
# (The *last* line visible before this edit was ``"""Map this category onto the summary dic``)t key it feeds."""
        return {
            ValidationCategory.PASS_REGRESSION: "passed",
            ValidationCategory.FAIL_REGRESSION: "failed_regression",
            ValidationCategory.FAIL_METRIC: "failed_metric",
            ValidationCategory.FAIL_SKIPPED: "skipped",
        }[self]

    @classmethod
    def parse(cls, value: Any) -> "ValidationCategory":
        """Best-effort parse of a legacy value into a category.

        Accepts a :class:`ValidationCategory`, its string name/value, or a
        legacy ``(passed: bool, reason: str)`` style mapping. Anything
        unrecognised is treated as ``FAIL_SKIPPED`` so that unknown input
        can never silently mark a cycle as failed.
        """
        if isinstance(value, ValidationCategory):
            return value

        if isinstance(value, Mapping):
            if "category" in value:
                return cls.parse(value["category"])
            passed = value.get("passed")
            reason = str(value.get("reason", "") or value.get("detail", "") or "")
            if passed is True:
                return cls.PASS_REGRESSION
            if passed is False:
                lowered = reason.lower()
                if "metric" in lowered or "threshold" in lowered or "score" in lowered:
                    return cls.FAIL_METRIC
                if "skip" in lowered:
                    return cls.FAIL_SKIPPED
                return cls.FAIL_REGRESSION
            return cls.FAIL_SKIPPED

        if isinstance(value, str):
            normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
            for member in cls:
                if normalized == member.name or normalized == member.value:
                    return member
            # tolerate the human readable aliases
            aliases = {
                "PASS": cls.PASS_REGRESSION,
                "OK": cls.PASS_REGRESSION,
                "REGRESSION": cls.FAIL_REGRESSION,
                "FAIL": cls.FAIL_REGRESSION,
                "METRIC": cls.FAIL_METRIC,
                "METRIC_NOT_MET": cls.FAIL_METRIC,
                "FUNCTIONAL_REGRESSION": cls.FAIL_REGRESSION,
                "SKIPPED": cls.FAIL_SKIPPED,
                "SKIP": cls.FAIL_SKIPPED,
            }
            return aliases.get(normalized, cls.FAIL_SKIPPED)

        if isinstance(value, bool):
            return cls.PASS_REGRESSION if value else cls.FAIL_REGRESSION

        return cls.FAIL_SKIPPED


# Backwards-friendly constant group; both names are accepted.
FUNCTIONAL_REGRESSION = ValidationCategory.FAIL_REGRESSION
METRIC_NOT_MET = ValidationCategory.FAIL_METRIC

VALIDATION_CATEGORIES: Sequence[ValidationCategory] = tuple(ValidationCategory)


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


@dataclass
class ValidationCounters:
    """Plain counter bucket for validation outcomes.

    Only ``failed_regression`` and ``failed_metric`` contribute to the
    ``failed_count`` used for cycle success determination. ``skipped``
    and ``passed`` never contribute to it.
    """

    passed: int = 0
    failed_regression: int = 0
    failed_metric: int = 0
    skipped: int = 0

    def record(self, category: ValidationCategory) -> None:
        key = category.summary_key
        setattr(self, key, getattr(self, key) + 1)

    @property
    def failed_count(self) -> int:
        """Total number of validation failures.

        Explicitly the sum of the two failure buckets only. Do NOT fold
        ``skipped`` (or anything else) into this, otherwise "0 items
        failed to pass validation because nothing ran" would be reported
        as a cycle failure.
        """
        return self.failed_regression + self.failed_metric

    @property
    def total(self) -> int:
        return self.passed + self.failed_regression + self.failed_metric + self.skipped

    def as_dict(self) -> Dict[str, int]:
        return {
            "passed": self.passed,
            "failed_regression": self.failed_regression,
            "failed_metric": self.failed_metric,
            "skipped": self.skipped,
        }


# ---------------------------------------------------------------------------
# Validation result record
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    """One validation outcome, classified rather than boolean."""

    improvement_id: str
    category: ValidationCategory
    detail: str = ""
    metrics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_failure(self) -> bool:
        return self.category.is_failure

    @property
    def is_pass(self) -> bool:
        return self.category.is_pass

    @property
    def is_skipped(self) -> bool:
        return self.category.is_skipped

    @classmethod
    def from_legacy(cls, improvement_id: str, value: Any) -> "ValidationResult":
        if isinstance(value, ValidationResult):
            return value
        if isinstance(value, Mapping):
            category = ValidationCategory.parse(value)
            detail = str(value.get("detail", "") or value.get("reason", "") or "")
            metrics = dict(value.get("metrics", {}) or {})
            return cls(improvement_id, category, detail, metrics)
        return cls(improvement_id, ValidationCategory.parse(value))


# ---------------------------------------------------------------------------
# Cycle result
# ---------------------------------------------------------------------------


@dataclass
class EvolutionCycleResult:
    """Aggregated result of one evolution cycle.

    The single source of truth for cycle success is :attr:`success`, which
    is defined as exactly ``failed_count == 0``.
    """

    cycle_id: str = ""
    plan_count: int = 0
    improvement_count: int = 0
    validation_results: List[ValidationResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    # -- counters --------------------------------------------------------

    @property
    def counters(self) -> ValidationCounters:
        counters = ValidationCounters()
        for result in self.validation_results:
            counters.record(result.category)
        return counters

    @property
    def total_improvements(self) -> int:
        """Number of improvements actually attempted in this cycle.

        Falls back to the recorded ``improvement_count`` when no per-item
        validation results exist (e.g. all improvements were skipped
        before validation).
        """
        if self.validation_results:
            return len(self.validation_results)
        return self.improvement_count

    @property
    def failed_count(self) -> int:
        return self.counters.failed_count

    @property
    def passed_count(self) -> int:
        return self.counters.passed

    @property
    def skipped_count(self) -> int:
        return self.counters.skipped

    # -- success determination ------------------------------------------

    @property
    def success(self) -> bool:
        """Cycle success == ``failed_count == 0``.

        Reviewed boolean logic
        ----------------------
        The condition is intentionally reduced to a single, literal
        expression::

            return self.failed_count == 0

        Deliberately NOT part of the success condition
        -----------------------------------------------
        - ``plan_count > 0``      : an empty plan is a valid, successful
          no-op cycle, not a failure.
        - ``improvement_count > 0``: likewise, zero improvements attempted
          is a successful no-op.
        - ``total_improvements > 0``: same reason.
        - ``passed_count > 0``   : requiring at least one pass would flip
          "0 improvements did not pass validation because none ran" into
          a failure, which is precisely the bug this module fixes.
        - ``skipped_count == 0`` : skipped validations are not failures.

        The explicit ``== 0`` also makes the intent legible to static
        review: any counter increment that lands outside
        ``failed_regression`` / ``failed_metric`` can never flip the
        cycle to "failed".
        """
        return self.failed_count == 0

    @property
    def failed(self) -> bool:
        return not self.success

    @property
    def is_empty(self) -> bool:
        """True when the cycle had nothing to do."""
        return self.plan_count == 0 and self.improvement_count == 0 and not self.validation_results

    # -- summary ---------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """Structured cycle summary.

        Keys
        ----
        total_improvements / passed / failed_regression / failed_metric /
        skipped are always present and always integers, so downstream
        consumers can rely on their shape.
        """
        counters = self.counters
        return {
            "cycle_id": self.cycle_id,
            "plan_count": self.plan_count,
            "improvement_count": self.improvement_count,
            "total_improvements": self.total_improvements,
            "passed": counters.passed,
            "failed_regression": counters.failed_regression,
            "failed_metric": counters.failed_metric,
            "skipped": counters.skipped,
            "failed_count": counters.failed_count,
            "validation_total": counters.total,
            "success": self.success,
            "is_empty": self.is_empty,
            "notes": list(self.notes),
            "results": [
                {
                    "improvement_id": r.improvement_id,
                    "category": r.category.value,
                    "is_failure": r.is_failure,
                    "is_pass": r.is_pass,
                    "is_skipped": r.is_skipped,
                    "detail": r.detail,
                    "metrics": dict(r.metrics),
                }
                for r in self.validation_results
            ],
        }


# ---------------------------------------------------------------------------
# Public builder helpers
# ---------------------------------------------------------------------------


def classify_validation(value: Any) -> ValidationCategory:
    """Public adapter: legacy value -> :class:`ValidationCategory`."""
    return ValidationCategory.parse(value)


def build_cycle_result(
    cycle_id: str,
    plan_count: int = 0,
    improvement_count: int = 0,
    validation_items: Optional[Iterable[Any]] = None,
    notes: Optional[Iterable[str]] = None,
) -> EvolutionCycleResult:
    """Assemble an :class:`EvolutionCycleResult` from loose inputs.

    ``validation_items`` accepts:

    * :class:`ValidationResult` instances
    * ``(improvement_id, value)`` tuples where ``value`` is anything
      :meth:`ValidationCategory.parse` understands
    * Mappings containing an ``improvement_id`` key
    """
    results: List[ValidationResult] = []
    for index, item in enumerate(validation_items or ()):
        if isinstance(item, ValidationResult):
            results.append(item)
            continue

        if isinstance(item, Mapping):
            improvement_id = str(item.get("improvement_id", f"improvement-{index}"))
            results.append(ValidationResult.from_legacy(improvement_id, item))
            continue

        if isinstance(item, tuple) and len(item) == 2:
            improvement_id, value = item
            results.append(ValidationResult.from_legacy(str(improvement_id), value))
            continue

        results.append(ValidationResult.from_legacy(f"improvement-{index}", item))

    return EvolutionCycleResult(
        cycle_id=cycle_id,
        plan_count=plan_count,
        improvement_count=improvement_count,
        validation_results=results,
        notes=list(notes or ()),
    )


def empty_cycle_result(cycle_id: str = "") -> EvolutionCycleResult:
    """Convenience constructor for a no-op cycle.

    Returns a result whose ``success`` is True because
    ``failed_count == 0``; 0 improvements failing validation must not be
    reported as a cycle failure.
    """
    return EvolutionCycleResult(
        cycle_id=cycle_id,
        plan_count=0,
        improvement_count=0,
        validation_results=[],
        notes=["empty cycle: no improvements planned or attempted"],
    )


__all__ = [
    "ValidationCategory",
    "FUNCTIONAL_REGRESSION",
    "METRIC_NOT_MET",
    "VALIDATION_CATEGORIES",
    "ValidationCounters",
    "ValidationResult",
    "EvolutionCycleResult",
    "classify_validation",
    "build_cycle_result",
    "empty_cycle_result",
]