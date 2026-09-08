"""Behavior tests for parsed internal exercise heading detection."""

from __future__ import annotations

from lesson_builder.domain.lesson.validation.checks.validators.internal_exercise_heading import (
    internal_exercise_heading_check,
)


def test_internal_exercise_heading_check_given_matching_hyphen_slug_expect_blocker() -> None:
    results = internal_exercise_heading_check(
        {
            "elements": [
                _section([{"kind": "strong", "value": "identify-question"}]),
                {"element_kind": "exercise", "id": "identify-question"},
            ]
        }
    )

    assert [(result.check_id, result.unit_id, result.severity) for result in results] == [
        ("internal_exercise_heading", "identify-question", "blocker")
    ]


def test_internal_exercise_heading_check_given_underscore_handle_expect_normalized_match() -> None:
    results = internal_exercise_heading_check(
        {
            "elements": [
                _section([{"kind": "strong", "value": "identify_question"}]),
                {"element_kind": "exercise", "id": "identify_question"},
            ]
        }
    )

    assert len(results) == 1


def test_internal_exercise_heading_check_given_custom_handle_slug_expect_blocker() -> None:
    results = internal_exercise_heading_check(
        {
            "elements": [
                _section([{"kind": "strong", "value": "private_handle"}]),
                {"element_kind": "exercise", "id": "learner_activity"},
            ]
        },
        exercise_handles={"learner_activity": "private_handle"},
    )

    assert len(results) == 1


def test_internal_exercise_heading_check_given_bold_micro_heading_expect_no_finding() -> None:
    results = internal_exercise_heading_check(
        {
            "elements": [
                _section([{"kind": "strong", "value": "Key point"}]),
                {"element_kind": "exercise", "id": "identify-question"},
            ]
        }
    )

    assert results == []


def test_internal_exercise_heading_check_given_nonmatching_slug_expect_no_finding() -> None:
    results = internal_exercise_heading_check(
        {
            "elements": [
                _section([{"kind": "strong", "value": "question-pattern"}]),
                {"element_kind": "exercise", "id": "identify-question"},
            ]
        }
    )

    assert results == []


def _section(spans: list[dict[str, str]]) -> dict[str, object]:
    """Build the parsed section shape needed by the validator."""
    return {
        "element_kind": "section",
        "blocks": [{"kind": "paragraph", "spans": spans}],
    }
