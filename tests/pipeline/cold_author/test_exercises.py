"""Task E Step 3: ``author_exercises`` synthesizes >= 2 valid exercises per
objective, with operations drawn from ``eligible_operations`` and bloom levels
from each objective's bloom_targets. Reuses Step 0's ``build_validated_exercise``.

Entry point: ``author_exercises``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import TypeAdapter

from lesson_builder.pipeline.cold_author.exercises import _build_prompt, author_exercises
from lesson_builder.pipeline.cold_author.models import StageFailure, StageOK
from lesson_builder.schema import Lesson
from lesson_builder.schema.elements import Exercise
from lesson_builder.schema.selection import MIN_EXERCISES_PER_OBJECTIVE


def _objectives() -> list[dict[str, Any]]:
    return [
        {"id": "obj_001", "statement": "Recognize forms.", "bloom_targets": ["understand"]},
        {"id": "obj_002", "statement": "Produce forms.", "bloom_targets": ["apply"]},
    ]


class _FakeAgent:
    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def invoke(self, prompt: str, **kw: Any) -> str:
        self.calls += 1
        return self._response


_WELL_FORMED = json.dumps(
    {
        "exercises": [
            # understand -> judge
            {
                "objective_id": "obj_001",
                "operation": "judge",
                "bloom_level": "understand",
                "prompt_text": "Er dette riktig?",
                "explanation_text": "Ja.",
                "flat": {
                    "sentence_no": "en fin bil",
                    "is_correct": True,
                    "feedback": "Korrekt.",
                },
            },
            # understand -> choose
            {
                "objective_id": "obj_001",
                "operation": "choose",
                "bloom_level": "understand",
                "prompt_text": "Velg riktig.",
                "explanation_text": "Forklaring.",
                "flat": {
                    "options": ["en fin bil", "en fint bil", "en fine bil"],
                    "correct_index": 0,
                },
            },
            # apply -> build
            {
                "objective_id": "obj_002",
                "operation": "build",
                "bloom_level": "apply",
                "prompt_text": "Bygg setningen.",
                "explanation_text": "Rekkefolgen.",
                "flat": {
                    "sentence": "et fint hus",
                },
            },
            # apply -> recall_fill
            {
                "objective_id": "obj_002",
                "operation": "recall_fill",
                "bloom_level": "apply",
                "prompt_text": "Fyll ut.",
                "explanation_text": "Svar.",
                "flat": {
                    "sentence": "bilen er ___",
                    "blanks": [
                        {"options": ["fin", "fint", "fine"], "answer": "fin"}
                    ],
                },
            },
        ]
    },
    ensure_ascii=False,
)


def test_author_exercises_given_well_formed_response_expect_two_per_objective_and_schema_valid():
    result = author_exercises(_objectives(), author_agent=_FakeAgent(_WELL_FORMED))

    assert isinstance(result, StageOK)
    exercises = result.payload
    by_obj: dict[str, int] = {}
    for ex in exercises:
        TypeAdapter(Exercise).validate_python(ex)
        by_obj[ex["objective_id"]] = by_obj.get(ex["objective_id"], 0) + 1
    assert by_obj["obj_001"] >= MIN_EXERCISES_PER_OBJECTIVE
    assert by_obj["obj_002"] >= MIN_EXERCISES_PER_OBJECTIVE


def test_author_exercises_given_well_formed_response_expect_bloom_level_in_objective_targets():
    result = author_exercises(_objectives(), author_agent=_FakeAgent(_WELL_FORMED))
    assert isinstance(result, StageOK)
    targets = {"obj_001": ["understand"], "obj_002": ["apply"]}
    for ex in result.payload:
        assert ex["bloom_level"] in targets[ex["objective_id"]]


def test_author_exercises_given_malformed_response_expect_failure_not_silent():
    # is_correct is a string, not a bool: construct_payload must surface this.
    malformed = json.dumps(
        {
            "exercises": [
                {
                    "objective_id": "obj_001",
                    "operation": "judge",
                    "bloom_level": "understand",
                    "prompt_text": "??",
                    "flat": {"is_correct": "not-a-bool"},
                }
            ]
        },
        ensure_ascii=False,
    )
    agent = _FakeAgent(malformed)

    result = author_exercises(_objectives(), author_agent=agent)

    assert isinstance(result, StageFailure)
    assert result.stage == "exercises"


def test_author_exercises_given_uncovered_objective_expect_failure():
    # Only obj_001 exercises; obj_002 has none.
    uncovered = json.dumps(
        {
            "exercises": [
                {
                    "objective_id": "obj_001",
                    "operation": "judge",
                    "bloom_level": "understand",
                    "prompt_text": "Riktig?",
                    "flat": {"sentence_no": "Han går.", "is_correct": True, "feedback": "Ja."},
                },
                {
                    "objective_id": "obj_001",
                    "operation": "choose",
                    "bloom_level": "understand",
                    "prompt_text": "Velg.",
                    "flat": {"options": ["a", "b"], "correct_index": 0},
                },
            ]
        },
        ensure_ascii=False,
    )

    result = author_exercises(_objectives(), author_agent=_FakeAgent(uncovered))

    assert isinstance(result, StageFailure)
    assert "obj_002" in result.reason


def test_author_exercises_given_explanation_as_spans_not_string():
    # explanation_text becomes a list of spans, never a raw string.
    result = author_exercises(_objectives(), author_agent=_FakeAgent(_WELL_FORMED))
    assert isinstance(result, StageOK)
    for ex in result.payload:
        assert ex["explanation"] is None or isinstance(ex["explanation"], list)


def test_author_exercises_assemble_into_lesson():
    result = author_exercises(_objectives(), author_agent=_FakeAgent(_WELL_FORMED))
    assert isinstance(result, StageOK)
    exercises = result.payload

    lesson = {
        "key": "t",
        "concept_slug": "t",
        "grounding_mode": "fallback_no_wiki",
        "title": "T",
        "cefr_level": "A1",
        "goal": "G.",
        "objectives": _objectives(),
        "elements": exercises,
        "review_pool": {
            "pools": [
                {
                    "key": obj["id"],
                    "objective_id": obj["id"],
                    "cards": [
                        {"uuid": "00000000-0000-0000-0000-000000000001", "exercise_id": ex["id"]}
                        for ex in exercises
                        if ex["objective_id"] == obj["id"]
                    ],
                }
                for obj in _objectives()
            ]
        },
    }
    Lesson.model_validate(lesson)


def _sections_fixture():
    return [
        {
            "element_kind": "section",
            "id": "sec1",
            "role": "model",
            "objective_ids": ["obj_001"],
            "title": "Modal verbs",
            "blocks": [
                {"kind": "paragraph", "spans": [
                    {"kind": "text", "value": "Bruk kan for evne. Jeg kan snakke norsk."}
                ]}
            ],
        }
    ]


def test_build_prompt_given_sections_expect_teaching_block_and_manifest():
    prompt = _build_prompt(_objectives(), _sections_fixture())
    assert "LESSON MATERIAL already taught" in prompt
    assert "Jeg kan snakke norsk." in prompt
    assert "snakke" in prompt  # manifest token


def test_build_prompt_given_no_sections_expect_no_teaching_block():
    prompt = _build_prompt(_objectives(), None)
    assert "LESSON MATERIAL already taught" not in prompt


def test_author_exercises_still_works_without_sections():
    result = author_exercises(_objectives(), author_agent=_FakeAgent(_WELL_FORMED))
    assert result.__class__.__name__ == "StageOK"
