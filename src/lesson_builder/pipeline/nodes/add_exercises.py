"""Entry point: ``add_exercises``.

The improvement-flow front-end: applies an ``ImprovementSpec`` to an existing
lesson to produce a draft (new exercises appended) that then enters the shared
back-half (checks -> fix -> regression -> signoff -> human -> export).

When an LLM agent is available it generates the new exercises via the flat-
contract + construct + validate path (``build_validated_exercise``). Otherwise
the ``deterministic_add_exercises`` function appends a template exercise
matching the spec so the pipeline stays testable without a live LLM.
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from lesson_builder.gen.construct import construct_payload
from lesson_builder.gen.contracts_flat import FlatContractError
from lesson_builder.gen.retry import call_with_validation
from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec
from lesson_builder.schema.elements import Exercise, Operation


class AddExercisesError(Exception):
    """Raised when the author cannot produce schema-valid exercises after retries."""


class _AuthorExerciseEntry(BaseModel):
    """One exercise spec from the author: metadata + flat operation content.

    The ``flat`` dict carries the per-operation fields that
    ``construct_payload`` validates and turns into a typed payload.
    """

    model_config = ConfigDict(extra="forbid")

    operation: Operation
    prompt_text: str
    explanation_text: str | None = None
    bloom_level: str | None = None
    flat: dict[str, Any]


class _BuiltExercises(BaseModel):
    """Validated output of ``_emit_and_build``: constructed exercise element dicts.

    Each element is validated inside ``_emit_and_build`` via
    ``build_validated_exercise`` (driving the bounded re-ask on failure); this
    model confirms the emit/validate shape for ``call_with_validation``'s final
    ``_validate`` step.
    """

    model_config = ConfigDict(extra="forbid")

    exercises: list[dict[str, Any]]


def add_exercises(
    spec: ImprovementSpec,
    lesson: dict[str, Any],
    *,
    author_agent: Any | None = None,
) -> dict[str, Any]:
    """Apply ``spec`` to ``lesson``, appending new exercises.

    When ``author_agent`` is provided, it generates exercises via the LLM.
    Without it, ``deterministic_add_exercises`` appends a scaffold exercise.
    """
    if author_agent is not None:
        return _agent_add_exercises(spec, lesson, author_agent)
    return deterministic_add_exercises(spec, lesson)


def build_validated_exercise(
    *,
    operation: str,
    objective_id: str,
    bloom_level: str,
    prompt_text: str,
    explanation_text: str | None,
    flat: dict[str, Any],
) -> dict[str, Any]:
    """Build ONE schema-valid exercise element from flat author content.

    Constructs the per-operation payload via ``construct_payload`` (Python owns
    every id/order/flag so model_validators hold by construction), wraps it into
    a full exercise element with Python-owned ids, and validates the element
    against the ``Exercise`` discriminated union. Raises on any failure
    (``FlatContractError`` / ``ValidationError`` / ``ValueError``) so the caller
    can surface it or drive a bounded re-ask around the LLM call.

    Shared by the improvement-flow author (``_agent_add_exercises``) and the
    cold-author exercise stage.
    """
    payload = construct_payload(operation, flat)
    explanation: list[dict[str, Any]] | None = None
    if explanation_text:
        explanation = [{"kind": "text", "value": explanation_text}]
    element = {
        "element_kind": "exercise",
        "id": f"gen_ex_{uuid.uuid4().hex[:8]}",
        "operation": operation,
        "objective_id": objective_id,
        "bloom_level": bloom_level,
        "derived_from": [],
        "prompt": [{"kind": "text", "value": prompt_text}],
        "explanation": explanation,
        "payload": payload,
    }
    TypeAdapter(Exercise).validate_python(element)
    return element


def deterministic_add_exercises(spec: ImprovementSpec, lesson: dict[str, Any]) -> dict[str, Any]:
    """Append template exercises matching the spec (no LLM needed).

    Produces valid-schema exercises that the back-half checks will validate.
    The goal is a testable pipeline; real content needs the author agent.
    """
    updated = deepcopy(lesson)
    objective_id = spec.objective_id or _first_objective_id(updated)
    if not objective_id:
        return updated

    for i in range(spec.count):
        ex_id = f"improve_{spec.operation}_{uuid.uuid4().hex[:8]}"
        exercise = _template_exercise(ex_id, objective_id, spec, i)
        updated["elements"].append(exercise)
        _add_to_review_pool(updated, objective_id, ex_id)

    return updated


def _agent_add_exercises(
    spec: ImprovementSpec,
    lesson: dict[str, Any],
    agent: Any,
) -> dict[str, Any]:
    """Use the author agent to generate exercises matching the spec.

    The author emits FLAT content per exercise. ``build_validated_exercise``
    constructs the validated per-operation payload and validates the element
    against the ``Lesson`` element schema; the whole emit+build cycle is wrapped
    in ``call_with_validation`` (bounded re-ask). On unrepairable failure, raise
    ``AddExercisesError`` — never silently drop.
    """
    objective_id = spec.objective_id or _first_objective_id(lesson)
    if not objective_id:
        raise AddExercisesError("lesson has no objectives to attach exercises to")

    prompt = _build_author_prompt(spec, lesson)

    def _emit_and_build() -> dict[str, Any]:
        raw = agent.invoke(prompt)
        parsed = json.loads(raw)  # JSONDecodeError -> retryable
        built: list[dict[str, Any]] = []
        for entry_dict in parsed.get("exercises", []):
            entry = _AuthorExerciseEntry.model_validate(entry_dict)  # ValidationError -> retryable
            element = build_validated_exercise(
                operation=entry.operation,
                objective_id=objective_id,
                bloom_level=entry.bloom_level or spec.bloom_level or "understand",
                prompt_text=entry.prompt_text,
                explanation_text=entry.explanation_text,
                flat=entry.flat,
            )  # FlatContractError / ValidationError -> retryable
            built.append(element)
        return {"exercises": built}

    try:
        result = call_with_validation(
            _emit_and_build, _BuiltExercises, max_attempts=2, label="add_exercises"
        )
    except (ValidationError, ValueError, FlatContractError, KeyError) as exc:
        raise AddExercisesError(
            f"author failed to produce schema-valid exercises after retries: {exc}"
        ) from exc

    updated = deepcopy(lesson)
    for element in result.exercises:
        updated["elements"].append(element)
        _add_to_review_pool(updated, objective_id, element["id"])
    return updated


def _template_exercise(
    ex_id: str,
    objective_id: str,
    spec: ImprovementSpec,
    index: int,
) -> dict[str, Any]:
    """Create a valid-schema template exercise matching the spec."""
    content = spec.target_content or "the target concept"
    bloom = spec.bloom_level or "understand"
    return {
        "element_kind": "exercise",
        "id": ex_id,
        "operation": "judge",
        "objective_id": objective_id,
        "bloom_level": bloom,
        "derived_from": [],
        "prompt": [{"kind": "text", "value": f"Is this sentence about {content} correct?"}],
        "explanation": [{"kind": "text", "value": f"Practice exercise on {content}."}],
        "payload": {
            "sentence": [{"kind": "text", "value": f"Example {index + 1} about {content}."}],
            "is_correct": True,
            "feedback": f"This is correct usage of {content}.",
        },
    }


def _add_to_review_pool(lesson: dict[str, Any], objective_id: str, exercise_id: str) -> None:
    """Add a new exercise to the matching review_pool so it passes schema validation."""
    pool = lesson.get("review_pool", {})
    pools = pool.get("pools", [])
    for p in pools:
        if p.get("objective_id") == objective_id:
            p.setdefault("cards", []).append(
                {"uuid": str(uuid.uuid4()), "exercise_id": exercise_id}
            )
            return
    pools.append(
        {
            "key": objective_id,
            "objective_id": objective_id,
            "cards": [{"uuid": str(uuid.uuid4()), "exercise_id": exercise_id}],
        }
    )
    pool["pools"] = pools
    lesson["review_pool"] = pool


def _first_objective_id(lesson: dict[str, Any]) -> str | None:
    objectives = lesson.get("objectives", [])
    return objectives[0]["id"] if objectives else None


def _build_author_prompt(spec: ImprovementSpec, lesson: dict[str, Any]) -> str:
    operation_hint = (
        "Each exercise has an `operation` (judge, choose, recall_fill, match_pairs, "
        "categorize, build, find_fix), a `prompt_text` (the learner-facing prompt), an "
        "optional `explanation_text`, and a `flat` object carrying the operation-specific "
        "fields. For a `judge` exercise the flat fields are: `sentence_no` (the Norwegian "
        "sentence), `is_correct` (bool), `feedback` (string or null; required when "
        "is_correct is false). Python owns every id, index, and structural flag — you only "
        "provide the content."
    )
    return (
        f"Add {spec.count} exercise(s) about '{spec.target_content or 'the topic'}' "
        f"to the lesson '{lesson.get('concept_slug', '')}'. "
        f"Bloom level: {spec.bloom_level or 'any'}. "
        f"Return ONLY a JSON object: {{\"exercises\": [{{\"operation\": \"judge\", "
        f"\"prompt_text\": \"...\", \"explanation_text\": \"...\", \"flat\": {{...}}}}]}}.\n"
        f"{operation_hint}\n\n"
        f"Lesson context (JSON):\n{json.dumps(lesson, ensure_ascii=False)[:2000]}"
    )


__all__ = [
    "AddExercisesError",
    "add_exercises",
    "build_validated_exercise",
    "deterministic_add_exercises",
]
