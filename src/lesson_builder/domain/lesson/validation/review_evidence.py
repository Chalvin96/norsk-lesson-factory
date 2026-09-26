"""Entry point: `unsupported_semantic_findings` checks reviewer citations against one exercise."""

from __future__ import annotations

from typing import Any

K_REVIEW_EVIDENCE_MIN_CHARACTERS = 8
K_REVIEW_EVIDENCE_METADATA_FIELDS = frozenset(
    {
        "id",
        "operation",
        "objective_id",
        "option_id",
        "token_id",
        "blank_id",
        "item_id",
        "bucket_id",
        "left_id",
        "right_id",
        "section_id",
        "kind",
        "role",
        "response_language",
        "bloom",
        "speaker",
    }
)


def unsupported_semantic_findings(
    issues_by_exercise: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Identify findings without a verbatim quote in their own reviewed exercise.

    This proves only that the citation exists. A human or calibrated reviewer
    must still decide whether the quoted text is a pedagogical defect.
    """
    by_id = {question.get("id"): question for question in questions if isinstance(question.get("id"), str)}
    unsupported: list[dict[str, str]] = []
    for entry in issues_by_exercise:
        exercise_id = entry.get("id")
        question = by_id.get(exercise_id)
        for issue in entry.get("issues", []):
            evidence = issue.get("evidence") if isinstance(issue, dict) else None
            if question is not None and _quoted_in_question(evidence, question):
                continue
            unsupported.append(
                {
                    "id": str(exercise_id),
                    "evidence": evidence if isinstance(evidence, str) else "",
                    "reason": "finding does not cite a verbatim passage from its reviewed exercise",
                }
            )
    return unsupported


def _quoted_in_question(evidence: object, question: dict[str, Any]) -> bool:
    if not isinstance(evidence, str) or len(evidence.strip()) < K_REVIEW_EVIDENCE_MIN_CHARACTERS:
        return False
    return any(evidence in value for value in _reviewed_text(question))


def _reviewed_text(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _reviewed_text(item)]
    if not isinstance(value, dict):
        return []
    return [
        text
        for key, field_value in value.items()
        if key not in K_REVIEW_EVIDENCE_METADATA_FIELDS
        for text in _reviewed_text(field_value)
    ]


__all__ = ["unsupported_semantic_findings"]
