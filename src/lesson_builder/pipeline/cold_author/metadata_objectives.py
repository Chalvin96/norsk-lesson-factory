"""Entry point: ``author_metadata_objectives``.

Stage 1 of cold authoring: synthesize lesson title and goal from a
concept-requirements brief while consuming the card-authored CEFR level and
objectives verbatim. Each objective's bloom targets must yield at least
``MIN_EXERCISES_PER_OBJECTIVE`` eligible operations.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lesson_builder.gen.retry import call_with_validation
from lesson_builder.pipeline.cold_author.models import (
    ColdMetadata,
    ColdObjective,
    StageFailure,
    StageOK,
)
from lesson_builder.pipeline.concept_requirements import ConceptRequirements
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException
from lesson_builder.schema.selection import (
    MIN_EXERCISES_PER_OBJECTIVE,
    eligible_operations,
)

_LLM_FAILURES = (BackendDownException, LlmQuotaException)


class _LessonMetadata(BaseModel):
    """Structured-output schema the author must return for Stage 1."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    goal: str = Field(min_length=1)


class _BuiltMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metadata: ColdMetadata


def author_metadata_objectives(
    slug: str,
    requirements: ConceptRequirements,
    *,
    author_agent: Any,
) -> StageOK | StageFailure:
    """Synthesize lesson metadata while preserving card-authored objectives."""
    prompt = _build_prompt(slug, requirements)
    _validate_eligible_operations(requirements)

    def _emit() -> dict[str, Any]:
        raw = author_agent.invoke(prompt)
        parsed = json.loads(raw)
        spec = _LessonMetadata.model_validate(parsed)
        metadata = _build_metadata(spec, requirements)
        return {"metadata": metadata.model_dump(mode="json")}

    try:
        result = call_with_validation(_emit, _BuiltMetadata, max_attempts=2, label="cold_metadata")
    except _LLM_FAILURES as exc:
        return StageFailure("metadata_objectives", f"LLM backend unavailable: {exc}")
    except (ValidationError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return StageFailure("metadata_objectives", f"author did not return valid metadata: {exc}")
    return StageOK("metadata_objectives", result.metadata)


def _build_metadata(spec: _LessonMetadata, requirements: ConceptRequirements) -> ColdMetadata:
    return ColdMetadata(
        title=spec.title,
        goal=spec.goal,
        cefr_level=requirements.cefr_level,
        objectives=[
            ColdObjective(
                id=obj.id,
                statement=obj.statement,
                bloom_targets=list(obj.bloom_targets),
            )
            for obj in requirements.objectives
        ],
    )


def _validate_eligible_operations(requirements: ConceptRequirements) -> None:
    for i, obj in enumerate(requirements.objectives):
        ops = eligible_operations(list(obj.bloom_targets))
        if len(ops) < MIN_EXERCISES_PER_OBJECTIVE:
            raise ValueError(
                f"objective {i} bloom_targets {list(obj.bloom_targets)} yield only "
                f"{len(ops)} eligible operations ({ops}); "
                f"need >= {MIN_EXERCISES_PER_OBJECTIVE}"
            )


def _build_prompt(slug: str, requirements: ConceptRequirements) -> str:
    anchors = requirements.required_anchor_forms
    notes = requirements.notes
    return (
        f"You are authoring a Norwegian (Bokmål) language lesson for the concept '{slug}'.\n"
        f"Teaching notes: {notes}\n"
        f"Required anchor forms (Norwegian phrases that must appear in the lesson): "
        f"{json.dumps(anchors, ensure_ascii=False)}\n\n"
        "The lesson already has fixed CEFR level and objectives on its scope card.\n"
        "Synthesize only the remaining lesson metadata:\n"
        "- title: concise English lesson title\n"
        "- goal: one-sentence English learning goal\n"
        "- Do NOT invent, rewrite, or return CEFR level or objectives; those are supplied separately.\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no prose):\n"
        ' {"title": "...", "goal": "..."}'
    )


__all__ = ["author_metadata_objectives"]
