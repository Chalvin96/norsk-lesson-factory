"""Behavior tests for exact-package lesson quality review.

Entry point: ``validate_quality_review``.
"""

from __future__ import annotations

from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.services.quality_review import quality_axes_for_kind
from lesson_builder.domain.lesson.services.quality_review import validate_quality_review
from tests.domain.lesson.models.fakes import valid_quality_review


def test_validate_quality_review_given_complete_passing_rubric_expect_valid_pass() -> None:
    review = LessonQualityReview.model_validate(valid_quality_review())

    validation = validate_quality_review(review, quality_axes_for_kind("grammar"))

    assert validation.status == "valid"
    assert validation.total_score == 20
    assert validation.expected_verdict == "pass"


def test_validate_quality_review_given_omitted_axis_expect_invalid_response_contract() -> None:
    payload = valid_quality_review()
    payload["scores"] = payload["scores"][:-1]
    payload["verdict"] = "needs_repair"
    review = LessonQualityReview.model_validate(payload)

    validation = validate_quality_review(review, quality_axes_for_kind("grammar"))

    assert validation.status == "invalid"
    assert any("omitted rubric axes" in error for error in validation.errors)


def test_validate_quality_review_given_major_finding_with_pass_expect_invalid_verdict() -> None:
    payload = valid_quality_review()
    payload["findings"] = [
        {
            "code": "pedagogy_gap",
            "severity": "major",
            "artifact": "lesson.md",
            "location": "sec-model",
            "evidence": "The rule is stated without a meaning contrast.",
            "repair_instruction": "Add one aligned contrast before practice.",
        }
    ]
    review = LessonQualityReview.model_validate(payload)

    validation = validate_quality_review(review, quality_axes_for_kind("grammar"))

    assert validation.status == "invalid"
    assert validation.expected_verdict == "needs_repair"


def test_quality_axes_for_kind_given_each_category_expect_shared_and_specific_axes() -> None:
    grammar_axes = quality_axes_for_kind("grammar")
    pronunciation_axes = quality_axes_for_kind("pronunciation")

    assert len(grammar_axes) == 10
    assert len(pronunciation_axes) == 10
    assert grammar_axes[:7] == pronunciation_axes[:7]
    assert "compact_generalization" in grammar_axes
    assert "perception_accuracy" in pronunciation_axes
    assert "compact_generalization" not in pronunciation_axes


def test_validate_quality_review_given_wrong_category_axes_expect_invalid_response_contract() -> None:
    review = LessonQualityReview.model_validate(valid_quality_review("grammar"))

    validation = validate_quality_review(review, quality_axes_for_kind("pronunciation"))

    assert validation.status == "invalid"
    assert any("omitted rubric axes" in error for error in validation.errors)
