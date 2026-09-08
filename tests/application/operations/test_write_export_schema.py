"""Entry point: behavior tests for public export schema emission."""

import json
from pathlib import Path

from lesson_builder.application.operations.write_export_schema import write_export_json_schema
from lesson_builder.domain.lesson.models.export import ExportedLesson


def test_export_schema_given_public_model_expect_version_and_separate_pages():
    schema = ExportedLesson.model_json_schema()
    props = schema.get("properties", {})
    sv = props.get("schema_version", {})
    assert sv.get("const") == "4.0"
    assert "schema_version" in schema["required"]
    assert "sections" in props
    assert "exercises" in props


def test_write_export_json_schema_given_output_path_expect_json_file(tmp_path: Path):
    out = tmp_path / "lesson.schema.json"
    write_export_json_schema(out)
    loaded = json.loads(out.read_text())
    assert loaded["title"] == "ExportedLesson"
