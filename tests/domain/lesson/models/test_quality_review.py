"""Entry point: ``QualityFinding`` validation tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from tests.domain.lesson.models.fakes import valid_quality_review


def test_quality_finding_given_blocking_code_with_minor_severity_expect_schema_error() -> None:
    payload = valid_quality_review()
    payload["verdict"] = "needs_repair"
    payload["findings"] = [
        {
            "code": "accepted_form_marked_wrong",
            "severity": "minor",
            "artifact": "exercises.yaml",
            "location": "choose-form",
            "evidence": "A valid Bokmål form is keyed false.",
            "repair_instruction": "Replace the distractor with an invalid form.",
        }
    ]

    with pytest.raises(ValidationError, match="blocking severity"):
        LessonQualityReview.model_validate(payload)
