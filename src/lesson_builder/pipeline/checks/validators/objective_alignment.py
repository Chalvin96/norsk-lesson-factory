"""Entry point: ``objective_alignment_check_from_review``.

Advisory check: maps a structured LLM objective-alignment review (already
validated against ``ObjectiveAlignmentReview``) into CheckResults.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from lesson_builder.pipeline.checks.result import CheckResult

Severity = Literal["P1", "P2", "P3"]
K_OBJECTIVE_ALIGNMENT_BLOCKING_SEVERITY = "P1"


class ObjectiveAlignmentIssue(BaseModel):
    objective_id: str
    severity: Severity
    message: str
    evidence: str
    fix: str


class ObjectiveAlignmentReview(BaseModel):
    passed: bool
    summary: str
    issues: list[ObjectiveAlignmentIssue]


def objective_alignment_check_from_review(
    review_payload: dict[str, Any],
) -> tuple[list[CheckResult], ObjectiveAlignmentReview]:
    """Map a structured objective-alignment review into advisory CheckResults."""
    review = ObjectiveAlignmentReview.model_validate(review_payload)
    results: list[CheckResult] = []
    for issue in review.issues:
        results.append(
            CheckResult(
                check_id="objective_alignment",
                severity="blocker" if issue.severity == K_OBJECTIVE_ALIGNMENT_BLOCKING_SEVERITY else "warning",
                unit_id=issue.objective_id,
                advisory=True,
                message=f"{issue.severity} objective mismatch: {issue.message} — {issue.evidence}",
                fix_hint=issue.fix,
            )
        )
    return results, review
