import json
from pathlib import Path

from lesson_builder.schema.export import ExportedLesson, to_export_dict
from lesson_builder.schema.lesson import Lesson

_GOLDEN = Path("tests/fixtures/schema/golden_lesson_v3.json")


def test_golden_parses_as_internal_lesson():
    data = json.loads(_GOLDEN.read_text())
    lesson = Lesson.model_validate(data)
    assert lesson.key == "adjective_agreement"


def test_golden_projects_and_revalidates():
    data = json.loads(_GOLDEN.read_text())
    internal = Lesson.model_validate(data)
    ExportedLesson.model_validate(to_export_dict(internal))


def test_golden_covers_each_objective_with_a_teaching_section():
    data = json.loads(_GOLDEN.read_text())
    lesson = Lesson.model_validate(data)
    taught = {
        oid
        for el in lesson.elements
        if el.element_kind == "section" and el.role in ("model", "contrast")
        for oid in el.objective_ids
    }
    assert {o.id for o in lesson.objectives} <= taught
