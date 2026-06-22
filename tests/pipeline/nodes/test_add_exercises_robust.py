"""Task B: ``_agent_add_exercises`` must validate author output through
``construct_payload`` + the Lesson element schema, with bounded retry, and must
NEVER silently drop a malformed response.

Entry point: ``add_exercises`` (delegates to ``_agent_add_exercises`` when an
author agent is supplied).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from lesson_builder.pipeline.nodes.add_exercises import AddExercisesError, add_exercises
from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec
from lesson_builder.schema import Lesson


def _lesson() -> dict[str, Any]:
    """A minimal schema-valid lesson with one objective and no exercises."""
    return {
        "key": "test",
        "concept_slug": "test",
        "grounding_mode": "fallback_no_wiki",
        "title": "Test",
        "cefr_level": "A1",
        "goal": "Test goal.",
        "objectives": [{"id": "o1", "statement": "Understand the concept.", "bloom_targets": ["understand"]}],
        "elements": [
            {
                "element_kind": "section",
                "id": "s1",
                "role": "orient",
                "objective_ids": ["o1"],
                "title": "Intro",
                "blocks": [],
            }
        ],
        "review_pool": {
            "pools": [
                {
                    "key": "o1",
                    "objective_id": "o1",
                    "cards": [
                        {"uuid": "00000000-0000-0000-0000-000000000001", "exercise_id": "ex0"}
                    ],
                }
            ]
        },
    }


class _FakeAgent:
    """Returns a canned response on each ``invoke`` call."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def invoke(self, prompt: str, **kw: Any) -> str:
        self.calls += 1
        if self.calls > len(self._responses):
            return self._responses[-1]
        return self._responses[self.calls - 1]


_WELL_FORMED_RESPONSE = json.dumps(
    {
        "exercises": [
            {
                "operation": "judge",
                "prompt_text": "Er denne setningen riktig?",
                "explanation_text": "Dette er korrekt bruk.",
                "flat": {
                    "sentence_no": "Han leser den tredje setningen.",
                    "is_correct": True,
                    "feedback": "Korrekt.",
                },
            }
        ]
    },
    ensure_ascii=False,
)


def test_add_exercises_given_well_formed_flat_response_expect_schema_valid_exercise_appended():
    # setup: the author returns a well-formed flat exercise spec.
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="test",
        count=1,
        bloom_level="understand",
    )
    lesson = _lesson()
    agent = _FakeAgent([_WELL_FORMED_RESPONSE])

    # execute
    result = add_exercises(spec, lesson, author_agent=agent)

    # assert: exactly one exercise was appended, it validates against the Lesson
    # schema, and its payload was constructed from the flat contract.
    new_exercises = [el for el in result["elements"] if el.get("element_kind") == "exercise"]
    assert len(new_exercises) == 1
    ex = new_exercises[0]
    assert ex["operation"] == "judge"
    assert ex["objective_id"] == "o1"
    assert ex["bloom_level"] == "understand"
    assert ex["payload"]["sentence"] == [{"kind": "text", "value": "Han leser den tredje setningen."}]
    assert ex["payload"]["is_correct"] is True
    # the full lesson (with the new exercise + review pool card) must validate.
    Lesson.model_validate(result)
    # the original lesson is untouched.
    assert not any(el.get("element_kind") == "exercise" for el in lesson["elements"])


def test_add_exercises_given_malformed_response_expect_raises_and_lesson_unchanged():
    # setup: the author returns a structurally unusable response (is_correct is a
    # string, not a bool; sentence_no is missing).
    malformed = json.dumps(
        {
            "exercises": [
                {
                    "operation": "judge",
                    "prompt_text": "??",
                    "flat": {"is_correct": "not-a-bool"},
                }
            ]
        }
    )
    spec = ImprovementSpec(operation="add_exercises", target_slug="test", count=1)
    lesson = _lesson()
    original_elements = len(lesson["elements"])
    agent = _FakeAgent([malformed, malformed])

    # execute + assert: the failure is surfaced (not silently dropped).
    with pytest.raises(AddExercisesError):
        add_exercises(spec, lesson, author_agent=agent)

    # the original lesson is unchanged — no silent partial mutation.
    assert len(lesson["elements"]) == original_elements


def test_add_exercises_given_invalid_json_expect_raises_and_lesson_unchanged():
    # setup: the author returns garbage that is not even JSON.
    spec = ImprovementSpec(operation="add_exercises", target_slug="test", count=1)
    lesson = _lesson()
    original_elements = len(lesson["elements"])
    agent = _FakeAgent(["this is not json at all"])

    # execute + assert
    with pytest.raises((AddExercisesError, ValidationError)):
        add_exercises(spec, lesson, author_agent=agent)

    assert len(lesson["elements"]) == original_elements
