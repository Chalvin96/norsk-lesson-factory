import json
from pathlib import Path

from lesson_builder.pipeline.checks.validators.schema_validate import schema_validate

ROOT = Path(__file__).resolve().parents[3]


def test_schema_validate_given_real_internal_lesson_expect_no_results():
    lesson = json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())
    assert schema_validate(lesson) == []


def test_schema_validate_given_invalid_lesson_expect_blocker():
    bad = {"key": "bad"}
    results = schema_validate(bad)
    assert len(results) == 1
    assert results[0].check_id == "schema_validate"
    assert results[0].severity == "blocker"
    assert results[0].is_blocking
    assert "validation failed" in results[0].message
