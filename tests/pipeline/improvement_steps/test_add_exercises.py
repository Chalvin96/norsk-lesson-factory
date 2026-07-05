"""Tests for add_exercises: deterministic scaffold + author front-end."""

from __future__ import annotations

from lesson_builder.pipeline.improvement_steps.add_exercises import add_exercises, deterministic_add_exercises
from lesson_builder.pipeline.improvement_steps.parse_request import ImprovementSpec


def _lesson() -> dict:
    return {
        "key": "test",
        "concept_slug": "test",
        "objectives": [{"id": "o1", "bloom_targets": ["understand"]}],
        "elements": [
            {"element_kind": "section", "id": "s1", "role": "orient", "objective_ids": ["o1"],
             "title": "Intro", "blocks": []},
        ],
        "review_pool": {
            "pools": [
                {"key": "o1", "objective_id": "o1",
                 "cards": [{"uuid": "00000000-0000-0000-0000-000000000001", "exercise_id": "ex0"}]}
            ]
        },
    }


def test_deterministic_add_exercises_given_count_2_expect_two_new_exercises():
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="test",
        count=2,
        target_content="fordi",
    )
    result = deterministic_add_exercises(spec, _lesson())
    new_exercises = [el for el in result["elements"] if el.get("element_kind") == "exercise"]
    assert len(new_exercises) == 2


def test_deterministic_add_exercises_given_new_exercises_expect_added_to_review_pool():
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="test",
        count=1,
        target_content="fordi",
    )
    result = deterministic_add_exercises(spec, _lesson())
    pool = result["review_pool"]["pools"][0]
    assert len(pool["cards"]) == 2  # original + new


def test_deterministic_add_exercises_given_bloom_level_expect_in_new_exercise():
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="test",
        count=1,
        bloom_level="analyze",
    )
    result = deterministic_add_exercises(spec, _lesson())
    new_ex = [el for el in result["elements"] if el.get("element_kind") == "exercise"][0]
    assert new_ex["bloom_level"] == "analyze"


def test_add_exercises_given_no_agent_expect_deterministic_path():
    spec = ImprovementSpec(operation="add_exercises", target_slug="test", count=1)
    result = add_exercises(spec, _lesson())
    assert any(el.get("element_kind") == "exercise" for el in result["elements"])


def test_add_exercises_given_does_not_mutate_original():
    lesson = _lesson()
    original_elements = len(lesson["elements"])
    spec = ImprovementSpec(operation="add_exercises", target_slug="test", count=1)
    add_exercises(spec, lesson)
    assert len(lesson["elements"]) == original_elements
