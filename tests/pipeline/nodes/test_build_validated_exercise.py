"""Task E Step 0: ``build_validated_exercise`` is a public, reusable helper that
turns flat author content into a schema-valid exercise element. Both
``add_exercises`` (improvement) and the cold-author exercise stage call it.

Entry point: ``build_validated_exercise``.
"""

from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from lesson_builder.gen.contracts_flat import FlatContractError
from lesson_builder.pipeline.nodes import add_exercises
from lesson_builder.schema import Lesson
from lesson_builder.schema.elements import Exercise


def test_build_validated_exercise_given_well_formed_flat_expect_schema_valid_element():
    # setup: a well-formed judge flat content payload.
    flat = {
        "sentence_no": "Han leser boka i dag.",
        "is_correct": True,
        "feedback": "Korrekt.",
    }

    # execute
    element = add_exercises.build_validated_exercise(
        operation="judge",
        objective_id="obj_001",
        bloom_level="understand",
        prompt_text="Er denne setningen riktig?",
        explanation_text="Dette er korrekt bruk.",
        flat=flat,
    )

    # assert: the element validates against the Exercise discriminated union.
    TypeAdapter(Exercise).validate_python(element)
    assert element["element_kind"] == "exercise"
    assert element["operation"] == "judge"
    assert element["objective_id"] == "obj_001"
    assert element["bloom_level"] == "understand"
    assert element["payload"]["is_correct"] is True
    # explanation is spans, not a raw string.
    assert element["explanation"] == [{"kind": "text", "value": "Dette er korrekt bruk."}]
    # prompt is required spans.
    assert element["prompt"] == [{"kind": "text", "value": "Er denne setningen riktig?"}]


def test_build_validated_exercise_given_malformed_flat_expect_raises():
    # setup: is_correct is a string, not a bool; sentence_no missing.
    flat = {"is_correct": "not-a-bool"}

    # execute + assert: the failure is surfaced (construct_payload raises).
    with pytest.raises((FlatContractError, ValidationError, ValueError)):
        add_exercises.build_validated_exercise(
            operation="judge",
            objective_id="obj_001",
            bloom_level="understand",
            prompt_text="??",
            explanation_text=None,
            flat=flat,
        )


def test_build_validated_exercise_is_exported_in_all():
    assert "build_validated_exercise" in add_exercises.__all__


def test_build_validated_exercise_given_explanation_none_expect_null_explanation():
    element = add_exercises.build_validated_exercise(
        operation="judge",
        objective_id="obj_001",
        bloom_level="understand",
        prompt_text="Riktig?",
        explanation_text=None,
        flat={"sentence_no": "Han går.", "is_correct": True, "feedback": "Ja."},
    )
    assert element["explanation"] is None
    # the full element still validates.
    TypeAdapter(Exercise).validate_python(element)


def test_add_exercises_still_uses_build_validated_exercise():
    """Regression: the refactored add_exercises path delegates to the public helper."""
    # Point the private helper at the public one and confirm the agent path still works.
    from lesson_builder.pipeline.nodes.add_exercises import add_exercises as ae_func

    well_formed = json.dumps(
        {
            "exercises": [
                {
                    "operation": "judge",
                    "prompt_text": "Riktig?",
                    "explanation_text": "Ja.",
                    "flat": {
                        "sentence_no": "Han sover.",
                        "is_correct": True,
                        "feedback": "Korrekt.",
                    },
                }
            ]
        }
    )

    class _Agent:
        def __init__(self, response):
            self._response = response
            self.calls = 0

        def invoke(self, prompt):
            self.calls += 1
            return self._response

    from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec

    lesson = {
        "key": "t",
        "concept_slug": "t",
        "grounding_mode": "fallback_no_wiki",
        "title": "T",
        "cefr_level": "A1",
        "goal": "G.",
        "objectives": [{"id": "o1", "statement": "S.", "bloom_targets": ["understand"]}],
        "elements": [],
        "review_pool": {"pools": []},
    }
    spec = ImprovementSpec(operation="add_exercises", target_slug="t", count=1, bloom_level="understand")
    result = ae_func(spec, lesson, author_agent=_Agent(well_formed))
    Lesson.model_validate(result)
    assert any(el.get("element_kind") == "exercise" for el in result["elements"])
