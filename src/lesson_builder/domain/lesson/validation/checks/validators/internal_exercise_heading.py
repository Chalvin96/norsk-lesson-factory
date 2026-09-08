"""Entry point: `internal_exercise_heading_check` detects leaked exercise labels."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult

K_INTERNAL_EXERCISE_HEADING_SLUG_RE = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)+")


def internal_exercise_heading_check(
    data: dict[str, Any],
    *,
    exercise_handles: Mapping[str, str] | None = None,
) -> list[CheckResult]:
    """Block a strong slug paragraph immediately before its exercise."""
    elements = [element for element in data.get("elements", []) if isinstance(element, dict)]
    results: list[CheckResult] = []
    for index, exercise in enumerate(elements):
        if exercise.get("element_kind") != "exercise" or index == 0:
            continue
        section = elements[index - 1]
        heading = _find_strong_paragraph_text(section)
        if heading is None or not K_INTERNAL_EXERCISE_HEADING_SLUG_RE.fullmatch(heading):
            continue
        exercise_id = str(exercise.get("id", ""))
        handle = exercise_handles.get(exercise_id, exercise_id) if exercise_handles is not None else exercise_id
        if _normalize_slug(heading) not in {_normalize_slug(exercise_id), _normalize_slug(handle)}:
            continue
        results.append(
            CheckResult(
                check_id="internal_exercise_heading",
                severity="blocker",
                unit_id=exercise_id,
                message=f"strong slug '{heading}' exposes the internal exercise label before this exercise",
                fix_hint="Remove the bold exercise handle paragraph or replace it with learner-facing explanation.",
            )
        )
    return results


def _find_strong_paragraph_text(element: dict[str, Any]) -> str | None:
    """Return the text of a section's final strong-only paragraph."""
    if element.get("element_kind") != "section":
        return None
    blocks = element.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return None
    block = blocks[-1]
    if not isinstance(block, dict) or block.get("kind") != "paragraph":
        return None
    spans = block.get("spans")
    if not isinstance(spans, list) or len(spans) != 1:
        return None
    span = spans[0]
    if not isinstance(span, dict) or span.get("kind") != "strong" or not isinstance(span.get("value"), str):
        return None
    text = span["value"].strip().lower()
    return text or None


def _normalize_slug(value: str) -> str:
    """Normalize handle spelling for comparison with a visible slug."""
    return value.strip().lower().replace("_", "-")


__all__ = ["internal_exercise_heading_check"]
