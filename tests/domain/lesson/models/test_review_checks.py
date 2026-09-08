"""Tests for typed lesson review response contracts."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.review_checks import NaturalnessReview
from lesson_builder.domain.lesson.models.review_checks import ObjectiveAlignmentReview
from lesson_builder.domain.lesson.models.review_checks import PedagogyReview


def test_objective_alignment_review_given_valid_payload_expect_validated_model():
    review = ObjectiveAlignmentReview.model_validate(
        {
            "passed": False,
            "summary": "One exercise drifts to a neighboring concept.",
            "issues": [
                {
                    "objective_id": "obj_use_order_rank",
                    "severity": "P1",
                    "message": "Exercise tests dates rather than ranking/position.",
                    "evidence": "The item asks for date formatting instead of ordinal position.",
                    "fix": "Rewrite the exercise so it practices ordering/rank, not date notation.",
                }
            ],
        }
    )

    assert review.passed is False
    assert review.issues[0].objective_id == "obj_use_order_rank"


def test_objective_alignment_review_given_invalid_severity_expect_validation_error():
    with pytest.raises(ValidationError):
        ObjectiveAlignmentReview.model_validate(
            {
                "passed": False,
                "summary": "Invalid review.",
                "issues": [
                    {
                        "objective_id": "obj_use_order_rank",
                        "severity": "P0",
                        "message": "Mismatch.",
                        "evidence": "Evidence.",
                        "fix": "Fix.",
                    }
                ],
            }
        )


def test_naturalness_review_given_valid_scores_expect_validated_model():
    review = NaturalnessReview.model_validate(
        {
            "scores": {
                "idiomatic_phrasing": 3,
                "register_appropriateness": 4,
                "terminology_consistency": 5,
            },
            "issues": [],
        }
    )

    assert review.scores.idiomatic_phrasing == 3
    assert review.scores.register_appropriateness == 4
    assert review.scores.terminology_consistency == 5


def test_naturalness_review_given_score_outside_range_expect_validation_error():
    with pytest.raises(ValidationError):
        NaturalnessReview.model_validate(
            {
                "scores": {
                    "idiomatic_phrasing": 6,
                    "register_appropriateness": 4,
                    "terminology_consistency": 5,
                },
                "issues": [],
            }
        )


def test_pedagogy_review_given_valid_scores_expect_validated_model():
    review = PedagogyReview.model_validate(
        {
            "scores": {
                "on_concept": 5,
                "complete": 4,
                "bokmal": 5,
                "sequencing": 4,
                "presentable": 5,
                "answerable": 4,
                "depth": 3,
            },
            "summary": "Mostly solid.",
            "passes": ["Good scope discipline"],
            "issues": [],
        }
    )

    assert review.summary == "Mostly solid."
    assert review.scores.depth == 3


def test_pedagogy_review_given_score_outside_range_expect_validation_error():
    with pytest.raises(ValidationError):
        PedagogyReview.model_validate(
            {
                "scores": {
                    "on_concept": 5,
                    "complete": 4,
                    "bokmal": 5,
                    "sequencing": 4,
                    "presentable": 5,
                    "answerable": 4,
                    "depth": 6,
                },
                "summary": "Invalid review.",
                "passes": [],
                "issues": [],
            }
        )
