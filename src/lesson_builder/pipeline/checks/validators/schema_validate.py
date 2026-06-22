"""Entry point: ``schema_validate``.

Deterministic check: validates a lesson dict against the Pydantic ``Lesson``
schema and the export round-trip, surfacing validation errors as CheckResults.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.schema import ExportedLesson, Lesson, to_export_dict


def schema_validate(data: dict[str, Any]) -> list[CheckResult]:
    """Validate internal Lesson -> export projection -> ExportedLesson firewall."""
    try:
        lesson = Lesson.model_validate(data)
        export_dict = to_export_dict(lesson)
        ExportedLesson.model_validate(export_dict)
    except (ValidationError, ValueError, TypeError, KeyError) as e:
        return [
            CheckResult(
                check_id="schema_validate",
                severity="blocker",
                message=f"lesson schema/export validation failed: {e}",
                fix_hint="Repair the internal Lesson shape so it validates and projects through ExportedLesson 3.0.",
            )
        ]
    return []
