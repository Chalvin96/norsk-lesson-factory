"""Lesson elements: a teaching Section or a practice Exercise (one of 7 operations)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lesson_builder.schema.blocks import Block
from lesson_builder.schema.inline import InlineSpan

Spans = list[InlineSpan]
BloomLevel = Literal["remember", "understand", "apply", "analyze"]


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── Section ──────────────────────────────────────────────────────────────
class Section(_Base):
    element_kind: Literal["section"]
    id: str
    role: Literal["orient", "model", "contrast", "recap"]
    objective_ids: list[str]
    title: str
    blocks: list[Block]

    @model_validator(mode="after")
    def _objective_cardinality(self) -> Section:
        if self.role in ("model", "contrast") and len(self.objective_ids) != 1:
            raise ValueError(f"role {self.role} requires exactly 1 objective_id")
        return self


# ── Operation payloads ───────────────────────────────────────────────────
class _RecallSpanSeg(_Base):
    kind: Literal["span"]
    spans: Spans


class _RecallBlank(_Base):
    kind: Literal["blank"]
    blank_id: str
    options: list[str] = Field(min_length=2)
    answer_index: int

    @model_validator(mode="after")
    def _answer_in_range(self) -> _RecallBlank:
        if not 0 <= self.answer_index < len(self.options):
            raise ValueError("answer_index out of range for options")
        return self


_RecallSegment = Annotated[_RecallSpanSeg | _RecallBlank, Field(discriminator="kind")]


class RecallFillPayload(_Base):
    segments: list[_RecallSegment]


class _Left(_Base):
    left_id: str
    text: str


class _Right(_Base):
    right_id: str
    text: str


class _Pair(_Base):
    left_id: str
    right_id: str


class MatchPairsPayload(_Base):
    left: list[_Left]
    right: list[_Right]
    pairs: list[_Pair]

    @model_validator(mode="after")
    def _pairs_reference_sides(self) -> MatchPairsPayload:
        left_ids = {item.left_id for item in self.left}
        right_ids = {item.right_id for item in self.right}
        for pair in self.pairs:
            if pair.left_id not in left_ids:
                raise ValueError(f"pair references unknown left_id {pair.left_id!r}")
            if pair.right_id not in right_ids:
                raise ValueError(f"pair references unknown right_id {pair.right_id!r}")
        return self


class JudgePayload(_Base):
    sentence: Spans
    is_correct: bool
    # Plain post-reveal feedback for deterministic grading. This is separate
    # from Exercise.explanation (rich teaching spans) and often contains the
    # corrected form/rewrite, but it is never a learner answer field.
    feedback: str | None


class _ChooseOption(_Base):
    option_id: str
    text: str
    why: str | None = None


class ChoosePayload(_Base):
    options: list[_ChooseOption] = Field(min_length=2)
    answer_id: str
    stem: Spans | None = None

    @model_validator(mode="after")
    def _answer_exists(self) -> ChoosePayload:
        if self.answer_id not in {o.option_id for o in self.options}:
            raise ValueError("answer_id must match an option_id")
        return self


class _Bucket(_Base):
    bucket_id: str
    label: str


class _CategorizeItem(_Base):
    item_id: str
    text: str
    bucket_id: str


class CategorizePayload(_Base):
    buckets: list[_Bucket] = Field(min_length=2)
    items: list[_CategorizeItem]

    @model_validator(mode="after")
    def _items_reference_buckets(self) -> CategorizePayload:
        ids = {b.bucket_id for b in self.buckets}
        for item in self.items:
            if item.bucket_id not in ids:
                raise ValueError(f"item {item.item_id} references unknown bucket")
        return self


class _Token(_Base):
    token_id: str
    text: str
    fixed: bool


class BuildPayload(_Base):
    tokens: list[_Token]
    answer_order: list[str]

    @model_validator(mode="after")
    def _answer_covers_tokens(self) -> BuildPayload:
        if sorted(self.answer_order) != sorted(t.token_id for t in self.tokens):
            raise ValueError("answer_order must be a permutation of token_ids")
        return self


class _FindToken(_Base):
    token_id: str
    text: str


class FindFixPayload(_Base):
    tokens: list[_FindToken]
    error_token_id: str
    # Plain post-reveal feedback for deterministic grading. This is separate
    # from Exercise.explanation (rich teaching spans) and often contains the
    # corrected form/rewrite, but it is never a learner answer field.
    feedback: str

    @model_validator(mode="after")
    def _error_token_exists(self) -> FindFixPayload:
        if self.error_token_id not in {t.token_id for t in self.tokens}:
            raise ValueError("error_token_id must match a token_id")
        return self


# ── Exercise (discriminated by `operation`) ──────────────────────────────
class _SourceRef(_Base):
    section_id: str
    block_index: int | None = None
    note: str | None = None


_OPERATION_PAYLOAD: dict[str, type[BaseModel]] = {
    "recall_fill": RecallFillPayload,
    "match_pairs": MatchPairsPayload,
    "judge": JudgePayload,
    "choose": ChoosePayload,
    "categorize": CategorizePayload,
    "build": BuildPayload,
    "find_fix": FindFixPayload,
}

Operation = Literal[
    "recall_fill", "match_pairs", "judge", "choose", "categorize", "build", "find_fix"
]


def _exercise_model(operation: str, payload_model: type[BaseModel]) -> type[BaseModel]:
    """Build one Exercise variant whose `payload` is the operation's payload model."""

    class _ExerciseVariant(_Base):
        element_kind: Literal["exercise"]
        id: str
        operation: Literal[operation]  # type: ignore[valid-type]
        objective_id: str
        bloom_level: BloomLevel
        prompt: Spans
        explanation: Spans | None
        derived_from: list[_SourceRef]
        payload: payload_model  # type: ignore[valid-type]

    _ExerciseVariant.__name__ = f"Exercise_{operation}"
    _ExerciseVariant.__qualname__ = f"Exercise_{operation}"
    return _ExerciseVariant


_EXERCISE_VARIANTS = tuple(_exercise_model(op, m) for op, m in _OPERATION_PAYLOAD.items())

Exercise = Annotated[
    _EXERCISE_VARIANTS[0]  # type: ignore[valid-type]
    | _EXERCISE_VARIANTS[1]  # type: ignore[valid-type]
    | _EXERCISE_VARIANTS[2]  # type: ignore[valid-type]
    | _EXERCISE_VARIANTS[3]  # type: ignore[valid-type]
    | _EXERCISE_VARIANTS[4]  # type: ignore[valid-type]
    | _EXERCISE_VARIANTS[5]  # type: ignore[valid-type]
    | _EXERCISE_VARIANTS[6],  # type: ignore[valid-type]
    Field(discriminator="operation"),
]

Element = Annotated[Section | Exercise, Field(discriminator="element_kind")]
