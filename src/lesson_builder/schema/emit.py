"""Emit the exported/app JSON Schema — the contract Plans 3 & 4 consume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lesson_builder.schema.export import ExportedLesson

DEFAULT_OUT = Path("dist/schema/lesson.schema.json")


def build_export_json_schema() -> dict[str, Any]:
    return ExportedLesson.model_json_schema()


def write_export_json_schema(out: Path = DEFAULT_OUT) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(build_export_json_schema(), indent=2, ensure_ascii=False) + "\n")
    return out
