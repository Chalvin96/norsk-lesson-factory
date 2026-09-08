"""Entry point: ``regression_check``.

Deterministic check: compares the current lesson's export against a trusted
baseline export for the same slug. No-ops if no baseline is supplied.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.domain.lesson.models import ExportedLesson
from lesson_builder.domain.lesson.models import Lesson
from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.validation.export_serializer import to_export_dict


def regression_check(
    slug: str,
    lesson: dict[str, Any],
    baseline_export: dict[str, Any] | None = None,
) -> list[CheckResult]:
    """Compare the current export against a trusted baseline export for the same slug.

    Callers pass the already-resolved baseline export (if any). Imported lessons
    are excluded as baselines upstream by the acceptance-log reader.
    """
    if not baseline_export:
        return []

    current = _canon_export(to_export_dict(Lesson.model_validate(lesson)))
    baseline = _canon_export(baseline_export)
    if current == baseline:
        return []

    return [
        CheckResult(
            check_id="regression_diff",
            severity="warning",
            unit_id=slug,
            message="export differs from regression baseline",
            fix_hint="Review the export diff against the trusted baseline before accepting the lesson.",
        )
    ]


def _canon_export(export_payload: dict[str, Any]) -> dict[str, Any]:
    return ExportedLesson.model_validate(export_payload).model_dump(mode="json")
