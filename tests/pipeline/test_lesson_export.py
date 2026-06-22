import json
from pathlib import Path

from lesson_builder.pipeline.lesson_export import lesson_from_export, lesson_to_export
from lesson_builder.schema import ExportedLesson, Lesson

ROOT = Path(__file__).resolve().parents[2]
LESSON_EXPORT_FIXTURE = ROOT / "tests/fixtures/lesson_exports/ordinal_numbers.json"


def _canon(d: dict) -> dict:
    return ExportedLesson.model_validate(d).model_dump(mode="json")


def test_lesson_from_export_given_lesson_export_fixture_expect_valid_internal_lesson():
    export = json.loads(LESSON_EXPORT_FIXTURE.read_text())
    lesson = lesson_from_export(export)
    assert isinstance(lesson, Lesson)
    assert lesson.key == export["key"]
    pool_obj_ids = {p["objective_id"] for p in export["review_pool"]["pools"]}
    assert {o.id for o in lesson.objectives} == pool_obj_ids
    assert all(o.statement.startswith("[imported_unverified]") for o in lesson.objectives)


def test_lesson_to_export_given_imported_lesson_export_fixture_expect_dict_equal():
    raw = LESSON_EXPORT_FIXTURE.read_text()
    original = _canon(json.loads(raw))
    exported = _canon(lesson_to_export(lesson_from_export(json.loads(raw))))
    assert exported == original
