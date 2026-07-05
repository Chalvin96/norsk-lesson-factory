import pytest
from pydantic import ValidationError

from lesson_builder.pipeline.checks.validators.objective_alignment import (
    ObjectiveAlignmentReview,
    objective_alignment_check_from_review,
)


def _review_payload():
    return {
        "passed": False,
        "summary": "One exercise drifts to a neighboring concept.",
        "issues": [
            {
                "objective_id": "obj_use_order_rank",
                "severity": "P1",
                "message": "Exercise tests dates rather than ranking/position.",
                "evidence": "The item asks for date formatting instead of ordinal position.",
                "fix": "Rewrite the exercise so it practices ordering/rank, not date notation.",
            },
            {
                "objective_id": "obj_choose_form",
                "severity": "P3",
                "message": "A distractor weakens the target contrast.",
                "evidence": "Two options test lexical knowledge more than ordinal form choice.",
                "fix": "Tighten distractors so the choice turns on the intended ordinal contrast.",
            },
        ],
    }


def test_objective_alignment_review_given_valid_payload_expect_validated_model():
    review = ObjectiveAlignmentReview.model_validate(_review_payload())
    assert review.passed is False
    assert review.issues[0].objective_id == "obj_use_order_rank"


def test_objective_alignment_check_given_p1_and_p3_issues_expect_advisory_blocker_and_warning():
    results, review = objective_alignment_check_from_review(_review_payload())
    assert review.summary.startswith("One exercise")
    assert len(results) == 2
    assert results[0].check_id == "objective_alignment"
    assert results[0].advisory is True
    assert results[0].severity == "blocker"
    assert results[0].unit_id == "obj_use_order_rank"
    assert results[0].is_blocking is False
    assert results[1].severity == "warning"
    assert results[1].unit_id == "obj_choose_form"


def test_objective_alignment_review_given_invalid_severity_expect_validation_error():
    payload = _review_payload()
    payload["issues"][0]["severity"] = "P0"
    with pytest.raises(ValidationError):
        ObjectiveAlignmentReview.model_validate(payload)
