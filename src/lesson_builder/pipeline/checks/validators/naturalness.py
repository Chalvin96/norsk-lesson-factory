"""Entry point: ``naturalness_check_from_review``.

Advisory check: maps a structured LLM naturalness/register review (already
validated against ``NaturalnessReview``) into CheckResults. All findings are
advisory (severity="warning", ``advisory=True``) — naturalness is not
load-bearing yet; calibration/promotion is a later step. Also exports
``naturalness_percent`` for scoring a review independent of the check.

Three score axes (1-5 each):
- ``idiomatic_phrasing`` — natural word order, collocations, message register.
- ``register_appropriateness`` — CEFR-level appropriateness of grammar labels.
- ``terminology_consistency`` — house-style term consistency per the active
  rules table injected from ``docs/terminology-style-guide.md``.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from pydantic import BaseModel, Field

from lesson_builder.pipeline.checks.result import CheckResult

Severity = Literal["P1", "P2", "P3"]

K_NATURALNESS_AXES = ("idiomatic_phrasing", "register_appropriateness", "terminology_consistency")
K_NATURALNESS_AXIS_MAX = 5


class NaturalnessScores(BaseModel):
    idiomatic_phrasing: int = Field(ge=1, le=K_NATURALNESS_AXIS_MAX)
    register_appropriateness: int = Field(ge=1, le=K_NATURALNESS_AXIS_MAX)
    terminology_consistency: int = Field(ge=1, le=K_NATURALNESS_AXIS_MAX)


class NaturalnessIssue(BaseModel):
    unit_id: str
    severity: Severity
    message: str


class NaturalnessReview(BaseModel):
    scores: NaturalnessScores
    issues: list[NaturalnessIssue]


def naturalness_check_from_review(
    review_payload: dict[str, Any],
) -> tuple[list[CheckResult], NaturalnessReview]:
    """Map a validated naturalness review payload into advisory CheckResults.

    All findings are ADVISORY (severity="warning", ``advisory=True``) —
    naturalness never blocks export or routes the fix loop yet.
    """
    review = NaturalnessReview.model_validate(review_payload)
    results: list[CheckResult] = []
    for issue in review.issues:
        results.append(
            CheckResult(
                check_id="naturalness_check",
                severity="warning",
                unit_id=issue.unit_id,
                advisory=True,
                message=f"{issue.severity} naturalness: {issue.message}",
            )
        )
    return results, review


def naturalness_percent(review: NaturalnessReview) -> float:
    """Mean of the three axis scores as a percent (for future calibration)."""
    total = sum(getattr(review.scores, axis) for axis in K_NATURALNESS_AXES)
    return cast(
        "float",
        round(100 * total / (len(K_NATURALNESS_AXES) * K_NATURALNESS_AXIS_MAX), 2),
    )


__all__ = [
    "NaturalnessReview",
    "NaturalnessScores",
    "NaturalnessIssue",
    "naturalness_check_from_review",
    "naturalness_percent",
    "K_NATURALNESS_AXES",
    "K_NATURALNESS_AXIS_MAX",
]
