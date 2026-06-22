"""Tests for feasibility_check: deterministic pre-flight gate."""

from __future__ import annotations

from lesson_builder.pipeline.nodes.feasibility import feasibility_check
from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec


def _lesson(objectives=None, exercises=None):
    return {
        "objectives": objectives or [{"id": "o1", "bloom_targets": ["understand", "apply"]}],
        "elements": exercises or [],
    }


def test_feasibility_check_given_feasible_bloom_expect_feasible():
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="past_tense",
        bloom_level="apply",
        count=1,
    )
    result = feasibility_check(spec, lesson=_lesson(), known_slugs=["past_tense"])
    assert result.verdict == "feasible"


def test_feasibility_check_given_infeasible_bloom_expect_infeasible():
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="past_tense",
        bloom_level="analyze",
        count=1,
    )
    result = feasibility_check(spec, lesson=_lesson(), known_slugs=["past_tense"])
    assert result.verdict == "infeasible"
    assert "bloom_targets" in result.reason


def test_feasibility_check_given_unknown_slug_expect_off_scope():
    spec = ImprovementSpec(operation="add_exercises", target_slug="new_topic", count=1)
    result = feasibility_check(spec, known_slugs=["past_tense"])
    assert result.verdict == "off_scope"


def test_feasibility_check_given_no_target_slug_expect_off_scope():
    spec = ImprovementSpec(operation="add_exercises", target_slug=None, count=1)
    result = feasibility_check(spec, known_slugs=["past_tense"])
    assert result.verdict == "off_scope"
    assert "triage" in result.reason


def test_feasibility_check_given_saturated_lesson_expect_off_scope():
    exercises = [
        {"element_kind": "exercise", "id": f"ex{i}", "objective_id": "o1"}
        for i in range(8)
    ]
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="past_tense",
        count=1,
        objective_id="o1",
    )
    result = feasibility_check(spec, lesson=_lesson(exercises=exercises), known_slugs=["past_tense"])
    assert result.verdict == "off_scope"
    assert "redundant" in result.reason
