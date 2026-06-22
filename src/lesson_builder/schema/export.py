"""Project the internal Lesson to the lean exported/app shape the wire carries."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from lesson_builder.schema.blocks import Block
from lesson_builder.schema.elements import (
    BuildPayload,
    CategorizePayload,
    ChoosePayload,
    FindFixPayload,
    JudgePayload,
    MatchPairsPayload,
    RecallFillPayload,
    Spans,
)
from lesson_builder.schema.lesson import Lesson, ReviewPool
from lesson_builder.schema.version import SCHEMA_VERSION

# ── to_export_dict ───────────────────────────────────────────────────────

_SECTION_STRIP = ("id", "objective_ids", "element_kind")
_EXERCISE_STRIP = ("derived_from", "bloom_level", "objective_id", "element_kind")


def _project_element(el: dict[str, Any]) -> dict[str, Any]:
    kind = el["element_kind"]
    if kind == "section":
        out = {k: v for k, v in el.items() if k not in _SECTION_STRIP}
    else:
        out = {k: v for k, v in el.items() if k not in _EXERCISE_STRIP}
    return {"kind": kind, **out}


def to_export_dict(lesson: Lesson) -> dict[str, Any]:
    raw = lesson.model_dump(mode="json")
    return {
        "schema_version": SCHEMA_VERSION,
        "key": raw["key"],
        "concept_slug": raw["concept_slug"],
        "grounding_mode": raw["grounding_mode"],
        "title": raw["title"],
        "cefr_level": raw["cefr_level"],
        "goal": raw["goal"],
        "elements": [_project_element(e) for e in raw["elements"]],
        "review_pool": raw["review_pool"],
    }


# ── ExportedLesson (lean mirror for JSON Schema emission) ────────────────


class _Lean(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExportedSection(_Lean):
    kind: Literal["section"]
    role: Literal["orient", "model", "contrast", "recap"]
    title: str
    blocks: list[Block]


_LEAN_PAYLOAD: dict[str, type[BaseModel]] = {
    "recall_fill": RecallFillPayload,
    "match_pairs": MatchPairsPayload,
    "judge": JudgePayload,
    "choose": ChoosePayload,
    "categorize": CategorizePayload,
    "build": BuildPayload,
    "find_fix": FindFixPayload,
}


def _make_exported_exercise(op: str, payload_model: type[BaseModel]) -> type[BaseModel]:
    # Use create_model (not an inline class) so __name__/__qualname__ are set AT
    # construction. Pydantic derives the emitted $defs key from the ref it builds
    # while the class is created; a post-hoc __qualname__ assignment is too late
    # and leaves opaque "<locals>._Ex_N" keys that Plan 4's TS codegen keys off.
    return create_model(
        f"ExportedExercise_{op}",
        __base__=_Lean,
        kind=(Literal["exercise"], ...),
        id=(str, ...),
        operation=(Literal[op], ...),
        prompt=(Spans, ...),
        explanation=(Spans | None, ...),
        payload=(payload_model, ...),
    )


_EXPORTED_EXERCISES = tuple(_make_exported_exercise(op, m) for op, m in _LEAN_PAYLOAD.items())

ExportedExercise = Annotated[
    _EXPORTED_EXERCISES[0]  # type: ignore[valid-type]
    | _EXPORTED_EXERCISES[1]  # type: ignore[valid-type]
    | _EXPORTED_EXERCISES[2]  # type: ignore[valid-type]
    | _EXPORTED_EXERCISES[3]  # type: ignore[valid-type]
    | _EXPORTED_EXERCISES[4]  # type: ignore[valid-type]
    | _EXPORTED_EXERCISES[5]  # type: ignore[valid-type]
    | _EXPORTED_EXERCISES[6],  # type: ignore[valid-type]
    Field(discriminator="operation"),
]

ExportedElement = Annotated[ExportedSection | ExportedExercise, Field(discriminator="kind")]


class ExportedLesson(_Lean):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["3.0"] = "3.0"
    key: str
    concept_slug: str
    grounding_mode: Literal["grounded", "fallback_no_wiki"]
    title: str
    cefr_level: Literal["A1", "A2", "B1", "B2", "C1", "C2"]
    goal: str
    elements: list[ExportedElement]
    review_pool: ReviewPool
