import pytest
from pydantic import ValidationError

from lesson_builder.pipeline.checks.validators.pedagogy import (
    PedagogyReview,
    pedagogy_check_from_review,
    pedagogy_percent,
)


def _review_payload():
    return {
        "scores": {
            "on_concept": 5,
            "complete": 4,
            "bokmal": 5,
            "sequencing": 4,
            "presentable": 5,
            "answerable": 4,
            "depth": 3,
        },
        "summary": "Mostly solid, one answerability defect.",
        "passes": ["Good scope discipline"],
        "issues": [
            {
                "severity": "P1",
                "category": "answerable",
                "title": "Valid distractor",
                "evidence": "Option B is also acceptable under the stated prompt.",
                "fix": "Rewrite the stem so only one option is valid.",
            },
            {
                "severity": "P3",
                "category": "depth",
                "title": "Recognition-heavy",
                "evidence": "Most items are choose/judge rather than production.",
                "fix": "Add one production-oriented build or find_fix exercise.",
            },
        ],
    }


def test_pedagogy_percent_given_valid_review_expect_computed_percent():
    review = PedagogyReview.model_validate(_review_payload())
    assert pedagogy_percent(review) == 85.71


def test_pedagogy_check_given_p1_and_p3_issues_expect_advisory_blocker_and_warning():
    results, review = pedagogy_check_from_review(_review_payload())
    assert review.summary.startswith("Mostly solid")
    assert len(results) == 2
    assert results[0].check_id == "pedagogy_check"
    assert results[0].advisory is True
    assert results[0].severity == "blocker"
    assert results[0].is_blocking is False
    assert "P1 answerable" in results[0].message
    assert results[1].severity == "warning"
    assert "P3 depth" in results[1].message


def test_pedagogy_review_given_invalid_score_range_expect_validation_error():
    payload = _review_payload()
    payload["scores"]["depth"] = 6
    with pytest.raises(ValidationError):
        PedagogyReview.model_validate(payload)
