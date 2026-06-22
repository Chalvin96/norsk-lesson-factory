"""Tests for ``naturalness_check_from_review`` and ``naturalness_percent``.

Entry point: ``naturalness_check_from_review``.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lesson_builder.pipeline.checks.validators.naturalness import (
    NaturalnessReview,
    naturalness_check_from_review,
    naturalness_percent,
)


def _review_payload():
    return {
        "scores": {
            "idiomatic_phrasing": 3,
            "register_appropriateness": 4,
            "terminology_consistency": 5,
        },
        "issues": [
            {
                "unit_id": "ex_001",
                "severity": "P2",
                "message": "Prefer 'heller ikke' over 'ikke ... heller' as the neutral default.",
            },
            {
                "unit_id": "",
                "severity": "P3",
                "message": "'front rounded vowel' needs an English approximation at A1.",
            },
        ],
    }


def test_naturalness_percent_given_valid_review_expect_mean_of_three_axes_as_percent():
    review = NaturalnessReview.model_validate(_review_payload())
    # (3 + 4 + 5) / (3 * 5) * 100 = 80.0
    assert naturalness_percent(review) == 80.0


def test_naturalness_check_given_p2_and_p3_issues_expect_all_advisory_warnings():
    results, review = naturalness_check_from_review(_review_payload())
    assert len(results) == 2
    assert review.scores.idiomatic_phrasing == 3
    assert review.scores.register_appropriateness == 4
    assert review.scores.terminology_consistency == 5
    for result in results:
        assert result.check_id == "naturalness_check"
        assert result.advisory is True
        assert result.severity == "warning"
        assert result.is_blocking is False


def test_naturalness_check_given_issues_expect_unit_ids_and_messages_mapped():
    results, _ = naturalness_check_from_review(_review_payload())
    assert results[0].unit_id == "ex_001"
    assert "heller ikke" in results[0].message
    assert results[1].unit_id == ""
    assert results[1].message.startswith("P3 naturalness: ")


def test_naturalness_check_given_no_issues_expect_empty_results():
    payload = {
        "scores": {
            "idiomatic_phrasing": 5,
            "register_appropriateness": 5,
            "terminology_consistency": 5,
        },
        "issues": [],
    }
    results, review = naturalness_check_from_review(payload)
    assert results == []
    assert review.issues == []


def test_naturalness_review_given_invalid_score_range_expect_validation_error():
    payload = _review_payload()
    payload["scores"]["idiomatic_phrasing"] = 6
    with pytest.raises(ValidationError):
        NaturalnessReview.model_validate(payload)


def test_naturalness_review_given_score_below_one_expect_validation_error():
    payload = _review_payload()
    payload["scores"]["register_appropriateness"] = 0
    with pytest.raises(ValidationError):
        NaturalnessReview.model_validate(payload)


def test_naturalness_review_given_missing_terminology_consistency_expect_validation_error():
    # setup: the 3rd axis is required — a two-axis payload must fail
    payload = {
        "scores": {"idiomatic_phrasing": 4, "register_appropriateness": 5},
        "issues": [],
    }
    with pytest.raises(ValidationError):
        NaturalnessReview.model_validate(payload)


# ---------------------------------------------------------------------------
# Terminology-consistency axis (WI 25 simplification: moved here from glossary)
# ---------------------------------------------------------------------------


class TestTerminologyConsistencyAxis:
    """The terminology_consistency score is the calibratable signal for
    house-style term consistency (the deterministic drift machinery was
    replaced by this LLM axis)."""

    def test_naturalness_percent_given_low_terminology_consistency_expect_lower_score(self):
        # setup: idiomatic + register are high but terminology_consistency is low
        payload = {
            "scores": {
                "idiomatic_phrasing": 5,
                "register_appropriateness": 5,
                "terminology_consistency": 1,
            },
            "issues": [],
        }
        review = NaturalnessReview.model_validate(payload)
        # exercise + assert: (5+5+1)/15*100 = 73.33
        assert naturalness_percent(review) == 73.33

    def test_naturalness_check_given_perfect_scores_expect_100_percent(self):
        payload = {
            "scores": {
                "idiomatic_phrasing": 5,
                "register_appropriateness": 5,
                "terminology_consistency": 5,
            },
            "issues": [],
        }
        review = NaturalnessReview.model_validate(payload)
        assert naturalness_percent(review) == 100.0
