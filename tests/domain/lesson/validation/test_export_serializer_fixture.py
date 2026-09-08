import json
from pathlib import Path

from lesson_builder.domain.lesson.models.export import ExportedLesson
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.domain.lesson.validation.export_serializer import to_export_dict
from tests.paths import K_ORDINAL_NUMBERS_EXPORT_PATH
from tests.paths import K_VALID_LESSON_SCHEMA_PATH


def test_valid_lesson_fixture_given_schema_json_expect_internal_lesson():
    data = json.loads(K_VALID_LESSON_SCHEMA_PATH.read_text())
    lesson = Lesson.model_validate(data)
    assert lesson.key == "adjective_agreement"


def test_valid_lesson_fixture_given_internal_projection_expect_export_schema():
    data = json.loads(K_VALID_LESSON_SCHEMA_PATH.read_text())
    internal = Lesson.model_validate(data)
    ExportedLesson.model_validate(to_export_dict(internal))


def test_export_fixture_given_direct_packet_validation_expect_self_describing_shape():
    packet = json.loads(K_ORDINAL_NUMBERS_EXPORT_PATH.read_text())

    validated = ExportedLesson.model_validate(packet)

    assert validated.schema_version == "4.0"
    assert validated.id == "ordinal_numbers"
    assert set(packet) == {
        "schema_version",
        "id",
        "kind",
        "language",
        "title",
        "cefr_level",
        "goal",
        "objectives",
        "content",
        "sections",
        "exercises",
        "practice_groups",
        "media",
    }


def test_speaker_icon_attribution_given_redistributed_data_expect_open_peeps_credit():
    license_text = Path("LICENSE-DATA.md").read_text(encoding="utf-8")

    assert "Open Peeps" in license_text
    assert "Pablo Stanley" in license_text
    assert "https://www.dicebear.com/styles/open-peeps/" in license_text
    assert "https://creativecommons.org/publicdomain/zero/1.0/" in license_text


def test_valid_lesson_fixture_given_objectives_expect_teaching_sections():
    data = json.loads(K_VALID_LESSON_SCHEMA_PATH.read_text())
    lesson = Lesson.model_validate(data)
    taught = {
        oid
        for el in lesson.elements
        if el.element_kind == "section" and el.role in ("model", "contrast")
        for oid in el.objective_ids
    }
    assert {o.id for o in lesson.objectives} <= taught
