"""Entry point: ``answer_valid_check``.

Advisory check: compares a reviewer's stated answers against the lesson's own
answer key (derived from exercise payloads), not against an external truth.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.lesson_exercises import expected_answers


def answer_valid_check(lesson: dict[str, Any], reviewer_payload: dict[str, Any]) -> tuple[list[CheckResult], dict[str, Any]]:
    """Advisory answerability check: compare a reviewer payload against the lesson's answer key."""
    comparison = compare_answers(expected_answers(lesson), reviewer_payload)
    if comparison["passed"]:
        return [], comparison

    issues: list[str] = []
    if comparison["missing"]:
        issues.append(f"missing answers for: {comparison['missing']}")
    if comparison["mismatches"]:
        bad_ids = [m["id"] for m in comparison["mismatches"]]
        issues.append(f"mismatched answers for: {bad_ids}")

    return [
        CheckResult(
            check_id="answer_valid",
            severity="blocker",
            advisory=True,
            message="; ".join(issues),
            fix_hint="Check whether the visible exercise data supports the stored answer keys.",
        )
    ], comparison


def compare_answers(expected: dict[str, dict[str, Any]], reviewer: dict[str, Any]) -> dict[str, Any]:
    reviewed_items = reviewer.get("answers", [])
    reviewed_by_id = {
        item.get("id"): item for item in reviewed_items if isinstance(item, dict) and item.get("id") is not None
    }
    mismatches: list[dict[str, Any]] = []
    missing: list[str] = []
    matches = 0

    for ex_id, expected_item in expected.items():
        reviewed = reviewed_by_id.get(ex_id)
        if reviewed is None:
            missing.append(ex_id)
            continue
        op = expected_item["operation"]
        actual = _normalize_answer(op, reviewed.get("answer"))
        if actual == expected_item["answer"]:
            matches += 1
        else:
            mismatches.append(
                {
                    "id": ex_id,
                    "operation": op,
                    "expected": expected_item["answer"],
                    "reviewer_answer": actual,
                    "reviewer_reason": reviewed.get("reason", ""),
                }
            )

    return {
        "matches": matches,
        "total": len(expected),
        "missing": missing,
        "mismatches": mismatches,
        "passed": not missing and not mismatches,
    }


def _normalize_answer(op: str, value: Any) -> Any:
    if op == "judge" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "correct", "yes"}:
            return True
        if lowered in {"false", "incorrect", "no"}:
            return False
    return value
