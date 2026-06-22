import pytest
from pydantic import ValidationError

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


def test_minimal_lesson_parses():
    lesson = Lesson.model_validate(_min_lesson())
    assert lesson.key == "noun_gender"
    assert lesson.objectives[0].bloom_targets == ["remember"]


def test_grounding_mode_constrained():
    with pytest.raises(ValidationError):
        Lesson.model_validate(_min_lesson(grounding_mode="vibes"))


def test_pool_key_matches_objective_id():
    lesson = Lesson.model_validate(_min_lesson())
    pool = lesson.review_pool.pools[0]
    assert pool.key == "o1"
    assert pool.objective_id == "o1"


def test_pool_key_objective_mismatch_rejected():
    with pytest.raises(ValidationError, match="objective_id"):
        Lesson.model_validate(
            _min_lesson(
                review_pool={
                    "pools": [
                        {
                            "key": "o1",
                            "objective_id": "o2",
                            "cards": [{"uuid": "11111111-1111-5111-8111-111111111111", "exercise_id": "e1"}],
                        }
                    ]
                }
            )
        )
