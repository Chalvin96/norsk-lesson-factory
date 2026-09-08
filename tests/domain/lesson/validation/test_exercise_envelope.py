"""Entry point: tests for `validate_exercise_envelope` behavior."""

from __future__ import annotations

import pytest

from lesson_builder.domain.lesson.validation.exercise_envelope import validate_exercise_envelope


def test_validate_exercise_envelope_given_internal_provenance_expect_value_error():
    raw = [
        {
            "handle": "identify-question",
            "op": "choose",
            "prompt_md": "Choose the question.",
            "derived_from": [{"section_id": "sec-model"}],
        }
    ]

    with pytest.raises(ValueError, match="must not carry internal provenance"):
        validate_exercise_envelope(raw)
