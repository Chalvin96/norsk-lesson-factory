"""Entry points: ``answer_valid_check`` and ``compare_answers``.

``answer_valid_check`` is called by exercise-review operations; ``compare_answers``
is also available to callers that need the structured comparison only.

The historical graph path keeps this check advisory. Fresh catalog generation
can opt into strict mode, where a missing, mismatched, ambiguous, or
unanswerable closed exercise blocks before the package is parked.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.validation.review_payloads import build_expected_answers


def answer_valid_check(
    lesson: dict[str, Any],
    reviewer_payload: dict[str, Any],
    *,
    strict: bool = False,
) -> tuple[list[CheckResult], dict[str, Any]]:
    """Compare reviewer answers against the authored key.

    ``strict=False`` preserves the legacy advisory graph behavior. The fresh
    catalog gate passes ``strict=True`` so the same comparison becomes a real
    generation blocker without changing acceptance behavior for old lessons.
    """
    comparison = compare_answers(build_expected_answers(lesson), reviewer_payload)
    if comparison["passed"]:
        return [], comparison

    issues: list[str] = []
    if comparison["missing"]:
        issues.append(f"missing answers for: {comparison['missing']}")
    if comparison["mismatches"]:
        bad_ids = [m["id"] for m in comparison["mismatches"]]
        issues.append(f"mismatched answers for: {bad_ids}")
    if comparison["extra"]:
        issues.append(f"unexpected answer ids: {comparison['extra']}")
    if comparison["duplicates"]:
        issues.append(f"duplicate answer ids: {comparison['duplicates']}")
    if comparison["ambiguous"]:
        issues.append(f"ambiguous exercises: {comparison['ambiguous']}")
    if comparison["unanswerable"]:
        issues.append(f"unanswerable exercises: {comparison['unanswerable']}")

    return [
        CheckResult(
            check_id="answer_valid",
            severity="blocker",
            advisory=not strict,
            message="; ".join(issues),
            fix_hint=(
                "Repair only the affected exercise payload; the lesson prose is immutable."
                if strict
                else "Check whether the visible exercise data supports the stored answer keys."
            ),
        )
    ], comparison


def compare_answers(expected: dict[str, dict[str, Any]], reviewer: dict[str, Any]) -> dict[str, Any]:
    reviewed_items = _collect_reviewed_items(reviewer)
    reviewed_ids = [item.get("id") for item in reviewed_items if item.get("id") is not None]
    counts = Counter(reviewed_ids)
    duplicates = sorted((item_id for item_id, count in counts.items() if count > 1), key=str)
    extra = sorted((item_id for item_id in counts if item_id not in expected), key=str)
    reviewed_by_id = {item["id"]: item for item in reviewed_items if item.get("id") is not None}
    matches, missing, mismatches, ambiguous, unanswerable = _compare_expected_answers(expected, reviewed_by_id)

    return {
        "matches": matches,
        "total": len(expected),
        "missing": missing,
        "mismatches": mismatches,
        "ambiguous": ambiguous,
        "unanswerable": unanswerable,
        "extra": extra,
        "duplicates": duplicates,
        "passed": (
            not missing and not mismatches and not ambiguous and not unanswerable and not extra and not duplicates
        ),
    }


def _collect_reviewed_items(reviewer: dict[str, Any]) -> list[dict[str, Any]]:
    """Return structured answer entries and ignore malformed reviewer items."""
    reviewed_items = reviewer.get("answers", [])
    if not isinstance(reviewed_items, list):
        return []
    return [item for item in reviewed_items if isinstance(item, dict)]


def _compare_expected_answers(
    expected: dict[str, dict[str, Any]], reviewed_by_id: dict[str, dict[str, Any]]
) -> tuple[int, list[str], list[dict[str, Any]], list[str], list[str]]:
    """Compare each expected answer and group the observable outcomes."""
    matches = 0
    missing: list[str] = []
    mismatches: list[dict[str, Any]] = []
    ambiguous: list[str] = []
    unanswerable: list[str] = []
    for ex_id, expected_item in expected.items():
        reviewed = reviewed_by_id.get(ex_id)
        if reviewed is None:
            missing.append(ex_id)
            continue
        status = reviewed.get("status", "solved")
        if status in {"ambiguous", "unanswerable"}:
            (ambiguous if status == "ambiguous" else unanswerable).append(ex_id)
            continue
        operation = expected_item["operation"]
        actual = _normalize_answer(operation, reviewed.get("answer"))
        if actual == expected_item["answer"]:
            matches += 1
            continue
        mismatches.append(
            {
                "id": ex_id,
                "operation": operation,
                "expected": expected_item["answer"],
                "reviewer_answer": actual,
                "reviewer_reason": reviewed.get("reason", ""),
            }
        )
    return matches, missing, mismatches, ambiguous, unanswerable


def _normalize_answer(op: str, value: object) -> object:
    if op == "judge" and isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "correct", "yes"}:
            return True
        if lowered in {"false", "incorrect", "no"}:
            return False
    return value
