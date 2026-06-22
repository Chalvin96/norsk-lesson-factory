"""Entry point: ``author_exercises``.

Stage 3 of cold authoring: synthesize >= ``MIN_EXERCISES_PER_OBJECTIVE`` valid
exercises per objective. Python owns the operation choice (via
``eligible_operations``) and the bloom level (drawn from each objective's
bloom_targets); the LLM only fills flat per-operation content. Reuses Step 0's
public ``build_validated_exercise`` so the improvement-flow and cold-author
paths share one validated-exercise helper.

Objective-coverage invariant (R5): every declared objective must end up with at
least ``MIN_EXERCISES_PER_OBJECTIVE`` exercises. A malformed response surfaces
as a ``StageFailure`` (never a silent drop).
"""

from __future__ import annotations

import json
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lesson_builder.gen.contracts_flat import FlatContractError
from lesson_builder.gen.retry import call_with_validation
from lesson_builder.pipeline.cold_author.models import StageFailure, StageOK
from lesson_builder.pipeline.cold_author.naturalness_guide import K_NATURALNESS_GUIDE
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException
from lesson_builder.pipeline.nodes.add_exercises import build_validated_exercise
from lesson_builder.schema.selection import (
    MIN_EXERCISES_PER_OBJECTIVE,
    eligible_operations,
)

_LLM_FAILURES = (BackendDownException, LlmQuotaException)


class _ExerciseSpec(BaseModel):
    """One exercise spec from the author: objective linkage + flat content."""

    model_config = ConfigDict(extra="forbid")

    objective_id: str = Field(min_length=1)
    operation: str
    bloom_level: str | None = None
    prompt_text: str = Field(min_length=1)
    explanation_text: str | None = None
    flat: dict[str, Any]


class _ExercisesPayload(BaseModel):
    """Structured-output schema the author must return for Stage 3."""

    model_config = ConfigDict(extra="forbid")

    exercises: list[_ExerciseSpec] = Field(min_length=1)


class _BuiltExercises(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exercises: list[dict[str, Any]]


def author_exercises(
    objectives: list[dict[str, Any]],
    *,
    author_agent: Any,
) -> StageOK | StageFailure:
    """Synthesize >= MIN_EXERCISES_PER_OBJECTIVE valid exercises per objective."""
    prompt = _build_prompt(objectives)

    def _emit() -> dict[str, Any]:
        raw = author_agent.invoke(prompt)
        parsed = json.loads(raw)
        spec = _ExercisesPayload.model_validate(parsed)
        built: list[dict[str, Any]] = []
        for entry in spec.exercises:
            bloom_level = _resolve_bloom_level(entry, objectives)
            element = build_validated_exercise(
                operation=entry.operation,
                objective_id=entry.objective_id,
                bloom_level=bloom_level,
                prompt_text=entry.prompt_text,
                explanation_text=entry.explanation_text,
                flat=entry.flat,
            )
            built.append(element)
        _assert_full_exercise_coverage(built, objectives)
        return {"exercises": built}

    try:
        result = call_with_validation(_emit, _BuiltExercises, max_attempts=2, label="cold_exercises")
    except _LLM_FAILURES as exc:
        return StageFailure("exercises", f"LLM backend unavailable: {exc}")
    except (ValidationError, ValueError, FlatContractError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return StageFailure("exercises", f"author did not return valid exercises: {exc}")
    return StageOK("exercises", result.exercises)


def _resolve_bloom_level(
    entry: _ExerciseSpec,
    objectives: list[dict[str, Any]],
) -> str:
    """Bloom level comes from the objective's targets (R7), not a free LLM pick."""
    targets_by_id = {obj["id"]: obj["bloom_targets"] for obj in objectives}
    if entry.objective_id not in targets_by_id:
        raise ValueError(
            f"exercise references unknown objective_id {entry.objective_id!r}; "
            f"declared objectives: {sorted(targets_by_id)}"
        )
    targets = targets_by_id[entry.objective_id]
    if entry.bloom_level and entry.bloom_level in targets:
        return entry.bloom_level
    return cast("str", targets[0])


def _assert_full_exercise_coverage(
    exercises: list[dict[str, Any]],
    objectives: list[dict[str, Any]],
) -> None:
    declared = {obj["id"] for obj in objectives}
    counts: dict[str, int] = dict.fromkeys(declared, 0)
    for ex in exercises:
        oid = ex["objective_id"]
        if oid in counts:
            counts[oid] += 1
    short = sorted(oid for oid, n in counts.items() if n < MIN_EXERCISES_PER_OBJECTIVE)
    if short:
        raise ValueError(
            f"each objective needs >= {MIN_EXERCISES_PER_OBJECTIVE} exercises; "
            f"under-covered: {short} (counts={counts})"
        )


def _build_prompt(objectives: list[dict[str, Any]]) -> str:
    obj_summary = json.dumps(
        [
            {
                "id": o["id"],
                "statement": o["statement"],
                "bloom_targets": o["bloom_targets"],
                "eligible_operations": eligible_operations(list(o["bloom_targets"])),
            }
            for o in objectives
        ],
        ensure_ascii=False,
    )
    return (
        "You are authoring practice exercises for a Norwegian (Bokmål) language lesson.\n"
        f"Objectives (each needs >= {MIN_EXERCISES_PER_OBJECTIVE} exercises): {obj_summary}\n\n"
        "For each objective, author exercises using ONLY operations from its "
        "eligible_operations list. Each exercise's bloom_level MUST be one of the "
        "objective's bloom_targets.\n\n"
        "Each exercise returns FLAT per-operation content (Python constructs the typed "
        "payload and every id/structural flag). The flat fields per operation are:\n"
        '- judge: sentence_no (Norwegian sentence), is_correct (bool), feedback '
        '(string or null; required when is_correct is false).\n'
        '- choose: options (list of >= 2 unique strings), correct_index (int).\n'
        '- recall_fill: sentence (with "___" blank markers, one per blank), blanks '
        '(list of {options: [>=2 strings], answer: one of options}).\n'
        '- match_pairs: pairs (list of >= 2 {left, right}; all texts unique per side).\n'
        '- categorize: buckets (>= 2 unique labels), items (list of {text, bucket}).\n'
        '- build: sentence (>= 2 words), optional fixed_words.\n'
        '- find_fix: sentence_with_error, error_word (occurs exactly once), feedback.\n\n'
        "Inline span values are plain text only (no markdown characters: no * ` _).\n\n"
        f"{K_NATURALNESS_GUIDE}\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no prose):\n"
        ' {"exercises": [{"objective_id": "obj_001", "operation": "judge", '
        '"bloom_level": "understand", "prompt_text": "...", "explanation_text": "...", '
        '"flat": {"sentence_no": "...", "is_correct": true, "feedback": "..."}}]}'
    )


__all__ = ["author_exercises"]
