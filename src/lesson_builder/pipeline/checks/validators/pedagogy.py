"""Entry point: ``pedagogy_check_from_review``.

Advisory check: maps a structured LLM pedagogy review (already validated
against ``PedagogyReview``) into CheckResults. Also exports
``pedagogy_percent`` for scoring a review independent of the check.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from lesson_builder.pipeline.checks.result import CheckResult

Severity = Literal["P0", "P1", "P2", "P3"]
Category = Literal[
    "on_concept",
    "complete",
    "bokmal",
    "sequencing",
    "presentable",
    "answerable",
    "depth",
    "other",
]
K_PEDAGOGY_RUBRIC_AXES = ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth")
K_PEDAGOGY_AXIS_MAX = 5
K_PEDAGOGY_BLOCKING_SEVERITIES = frozenset(("P0", "P1"))


class PedagogyScores(BaseModel):
    on_concept: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    complete: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    bokmal: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    sequencing: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    presentable: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    answerable: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)
    depth: int = Field(ge=0, le=K_PEDAGOGY_AXIS_MAX)


class PedagogyIssue(BaseModel):
    severity: Severity
    category: Category
    title: str
    evidence: str
    fix: str


class PedagogyReview(BaseModel):
    scores: PedagogyScores
    summary: str
    passes: list[str]
    issues: list[PedagogyIssue]


def pedagogy_check_from_review(review_payload: dict[str, Any]) -> tuple[list[CheckResult], PedagogyReview]:
    """Map a validated pedagogy review payload into advisory CheckResults."""
    review = PedagogyReview.model_validate(review_payload)
    results: list[CheckResult] = []
    for issue in review.issues:
        severity: Literal["blocker", "warning", "info"] = (
            "blocker" if issue.severity in K_PEDAGOGY_BLOCKING_SEVERITIES else "warning"
        )
        results.append(
            CheckResult(
                check_id="pedagogy_check",
                severity=severity,
                advisory=True,
                message=f"{issue.severity} {issue.category}: {issue.title} — {issue.evidence}",
                fix_hint=issue.fix,
            )
        )
    return results, review


def pedagogy_percent(review: PedagogyReview) -> float:
    total = sum(getattr(review.scores, axis) for axis in K_PEDAGOGY_RUBRIC_AXES)
    return cast("float", round(100 * total / (len(K_PEDAGOGY_RUBRIC_AXES) * K_PEDAGOGY_AXIS_MAX), 2))
