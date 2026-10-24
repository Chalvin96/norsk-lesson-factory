"""Entry point: ``QualityFinding`` validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from tests.domain.lesson.models.fakes import valid_quality_review


def test_quality_review_given_advisory_finding_expect_source_evidence_and_rationale() -> None:
    payload = valid_quality_review()
    payload["findings"] = [
        {
            "code": "accepted_form_marked_wrong",
            "severity": "major",
            "artifact": "exercises.yaml",
            "location": "choose-form",
            "evidence": "Choose the wrong form.",
            "rationale": "Check the accepted form against the answer policy.",
        }
    ]

    review = LessonQualityReview.model_validate(payload)

    assert review.findings[0].rationale == "Check the accepted form against the answer policy."
    assert review.findings[0].evidence == "Choose the wrong form."


def test_quality_review_given_blocking_language_severity_expect_schema_error() -> None:
    payload = valid_quality_review()
    payload["findings"] = [
        {
            "code": "accepted_form_marked_wrong",
            "severity": "blocking",
            "artifact": "exercises.yaml",
            "location": "choose-form",
            "evidence": "Choose the wrong form.",
            "rationale": "Check the accepted form against the answer policy.",
        }
    ]

    with pytest.raises(ValidationError):
        LessonQualityReview.model_validate(payload)
