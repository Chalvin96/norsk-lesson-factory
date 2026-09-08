"""Entry point: `write_export_json_schema` writes the public distribution schema.

The schema is generated from ``ExportedLesson``; the checked-in copy at
``dist/schema/lesson.schema.json`` is the canonical rendering refreshed by
``lesson-data regenerate-dist``.
"""

from __future__ import annotations

import json
from pathlib import Path

from lesson_builder.domain.lesson.models.export import ExportedLesson
from lesson_builder.workspace.paths import WorkspacePaths
from lesson_builder.workspace.paths import get_workspace_root

K_EXPORT_SCHEMA_PATH = WorkspacePaths(get_workspace_root()).dist_root / "schema" / "lesson.schema.json"


def write_export_json_schema(out: Path = K_EXPORT_SCHEMA_PATH) -> Path:
    """Write the canonical schema text to ``out`` and return the path."""
    out.parent.mkdir(parents=True, exist_ok=True)
    schema_text = json.dumps(ExportedLesson.model_json_schema(), indent=2, ensure_ascii=False) + "\n"
    out.write_text(schema_text, encoding="utf-8")
    return out
