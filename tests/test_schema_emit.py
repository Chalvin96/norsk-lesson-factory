import json
from pathlib import Path

from lesson_builder.schema.emit import build_export_json_schema, write_export_json_schema


def test_schema_has_version_and_elements():
    schema = build_export_json_schema()
    props = schema.get("properties", {})
    sv = props.get("schema_version", {})
    assert sv.get("const") == "3.0"
    assert "elements" in props


def test_write_emits_file(tmp_path: Path):
    out = tmp_path / "lesson.schema.json"
    write_export_json_schema(out)
    loaded = json.loads(out.read_text())
    assert loaded["title"] == "ExportedLesson"
