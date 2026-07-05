from lesson_builder.schema.export import ExportedLesson, to_export_dict
from lesson_builder.schema.lesson import Lesson


def _txt(v: str) -> dict:
    return {"kind": "text", "value": v}


def _min_lesson(**overrides) -> dict:
    base = {
        "key": "noun_gender",
        "concept_slug": "noun_gender",
        "grounding_mode": "grounded",
        "title": "Noun gender",
        "cefr_level": "A1",
        "goal": "Tell en/ei/et apart.",
        "objectives": [{"id": "o1", "statement": "pick the article", "bloom_targets": ["remember"]}],
        "elements": [
            {
                "element_kind": "section",
                "id": "s1",
                "role": "model",
                "objective_ids": ["o1"],
                "title": "Forms",
                "blocks": [{"kind": "paragraph", "spans": [_txt("hi")]}],
            }
        ],
        "review_pool": {
            "pools": [
                {
                    "key": "o1",
                    "objective_id": "o1",
                    "cards": [{"uuid": "11111111-1111-5111-8111-111111111111", "exercise_id": "e1"}],
                }
            ]
        },
    }
    base.update(overrides)
    return base


def test_export_renames_discriminator_and_strips_provenance():
    internal = Lesson.model_validate(_min_lesson())
    exported = to_export_dict(internal)

    el = exported["elements"][0]
    assert el["kind"] == "section"
    assert "element_kind" not in el
    assert "id" not in el
    assert "objective_ids" not in el


def test_export_keeps_pool_objective_key_and_exercise_id():
    internal = Lesson.model_validate(_min_lesson())
    exported = to_export_dict(internal)
    pool = exported["review_pool"]["pools"][0]
    assert pool["key"] == "o1"
    assert pool["objective_id"] == "o1"
    assert exported["schema_version"] == "3.0"


def test_export_dict_validates_against_exported_model():
    internal = Lesson.model_validate(_min_lesson())
    ExportedLesson.model_validate(to_export_dict(internal))


def test_export_omits_objectives_provenance():
    # objectives are deliberately NOT carried on the wire (Spec 1 §4: the app
    # anchors on review_pool[].key, not the pedagogy provenance). Guard the
    # omission so it can't silently regress back into the lean projection.
    internal = Lesson.model_validate(_min_lesson())
    exported = to_export_dict(internal)
    assert "objectives" not in exported
    assert not hasattr(ExportedLesson, "objectives") or "objectives" not in ExportedLesson.model_fields
