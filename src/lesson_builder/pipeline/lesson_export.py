from __future__ import annotations

from typing import Any

from lesson_builder.schema import ExportedLesson, Lesson, to_export_dict


def lesson_to_export(lesson: Lesson) -> dict[str, Any]:
    """Project internal Lesson -> export dict, validated against the locked ExportedLesson 3.0 contract."""
    export = to_export_dict(lesson)
    ExportedLesson.model_validate(export)  # firewall: fail loudly if projection breaks the contract
    return export
