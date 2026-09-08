"""Not a check itself — typed lesson sections and exercise operations."""

from __future__ import annotations

from collections.abc import Hashable
from collections.abc import Iterable
from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from lesson_builder.domain.lesson.models.blocks import Block
from lesson_builder.domain.lesson.models.inline import InlineSpan

Spans = list[InlineSpan]
BloomLevel = Literal["remember", "understand", "apply", "analyze"]


class SpeakPayload(BaseModel):
    """Authored target utterance for optional downstream recording and STT."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=1)

    @model_validator(mode="after")
    def _target_is_not_blank(self) -> SpeakPayload:
        if not self.target.strip():
            raise ValueError("speak target must contain non-whitespace text")
        return self


class WriteCriterion(BaseModel):
    """One observable criterion for an app-side LLM writing judgment."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    instruction: str = Field(min_length=1)


class WritePayload(BaseModel):
    """Authoring contract for bounded learner prose judged downstream."""

    model_config = ConfigDict(extra="forbid")

    response_language: Literal["no"] = "no"
    min_words: int | None = Field(default=None, ge=1)
    max_words: int | None = Field(default=None, ge=1)
    judge_prompt: str = Field(min_length=1)
    criteria: list[WriteCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def _word_bounds_are_ordered(self) -> WritePayload:
        if self.min_words is not None and self.max_words is not None and self.min_words > self.max_words:
            raise ValueError("write min_words must not exceed max_words")
        return self

    @model_validator(mode="after")
    def _criterion_ids_are_unique(self) -> WritePayload:
        duplicates = _duplicates(criterion.id for criterion in self.criteria)
        if duplicates:
            raise ValueError(f"duplicate criterion id {sorted(duplicates)[0]!r} in write")
        return self


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
    audio_target: str = Field(min_length=1)
    segments: list[_RecallSegment]

    @model_validator(mode="after")
    def _audio_target_is_not_blank(self) -> RecallFillPayload:
        if not self.audio_target.strip():
            raise ValueError("recall_fill audio_target must contain non-whitespace text")
        return self

    @model_validator(mode="after")
    def _blank_ids_are_unique(self) -> RecallFillPayload:
        duplicates = _duplicates(segment.blank_id for segment in self.segments if isinstance(segment, _RecallBlank))
        if duplicates:
            raise ValueError(f"duplicate blank id {sorted(duplicates)[0]!r} in recall_fill")
        return self


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

    @model_validator(mode="after")
    def _side_identities_are_unique(self) -> MatchPairsPayload:
        duplicate_left = _duplicates(item.left_id for item in self.left)
        duplicate_right = _duplicates(item.right_id for item in self.right)
        duplicate_pairs = _duplicates((pair.left_id, pair.right_id) for pair in self.pairs)
        if duplicate_left or duplicate_right:
            raise ValueError(f"duplicate match side id {sorted(duplicate_left | duplicate_right)[0]!r}")
        if duplicate_pairs:
            left_id, right_id = sorted(duplicate_pairs)[0]
            raise ValueError(f"duplicate match pairing {left_id!r}->{right_id!r}")
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

    @model_validator(mode="after")
    def _option_ids_are_unique(self) -> ChoosePayload:
        duplicates = _duplicates(option.option_id for option in self.options)
        if duplicates:
            raise ValueError(f"duplicate option id {sorted(duplicates)[0]!r} in choose")
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

    @model_validator(mode="after")
    def _identities_are_unique(self) -> CategorizePayload:
        duplicate_buckets = _duplicates(bucket.bucket_id for bucket in self.buckets)
        duplicate_items = _duplicates(item.item_id for item in self.items)
        if duplicate_buckets or duplicate_items:
            raise ValueError(f"duplicate categorize id {sorted(duplicate_buckets | duplicate_items)[0]!r}")
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
        duplicates = _duplicates(token.token_id for token in self.tokens)
        if duplicates:
            raise ValueError(f"duplicate token id {sorted(duplicates)[0]!r} in build")
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
        duplicates = _duplicates(token.token_id for token in self.tokens)
        if duplicates:
            raise ValueError(f"duplicate token id {sorted(duplicates)[0]!r} in find_fix")
        return self


# ── Exercise (discriminated by `operation`) ──────────────────────────────
class _SourceRef(_Base):
    section_id: str
    block_index: int | None = None
    note: str | None = None


Operation = Literal[
    "recall_fill", "match_pairs", "judge", "choose", "categorize", "build", "find_fix", "speak", "write"
]


class _ExerciseBase[OperationT: str, PayloadT: BaseModel](_Base):
    """Common fields for one statically typed exercise operation variant."""

    element_kind: Literal["exercise"]
    id: str
    operation: OperationT
    objective_id: str
    bloom_level: BloomLevel
    prompt: Spans
    explanation: Spans | None
    derived_from: list[_SourceRef]
    payload: PayloadT


type RecallFillExercise = _ExerciseBase[Literal["recall_fill"], RecallFillPayload]
type MatchPairsExercise = _ExerciseBase[Literal["match_pairs"], MatchPairsPayload]
type JudgeExercise = _ExerciseBase[Literal["judge"], JudgePayload]
type ChooseExercise = _ExerciseBase[Literal["choose"], ChoosePayload]
type CategorizeExercise = _ExerciseBase[Literal["categorize"], CategorizePayload]
type BuildExercise = _ExerciseBase[Literal["build"], BuildPayload]
type FindFixExercise = _ExerciseBase[Literal["find_fix"], FindFixPayload]
type SpeakExercise = _ExerciseBase[Literal["speak"], SpeakPayload]
type WriteExercise = _ExerciseBase[Literal["write"], WritePayload]

Exercise = Annotated[
    RecallFillExercise
    | MatchPairsExercise
    | JudgeExercise
    | ChooseExercise
    | CategorizeExercise
    | BuildExercise
    | FindFixExercise
    | SpeakExercise
    | WriteExercise,
    Field(discriminator="operation"),
]

Element = Annotated[Section | Exercise, Field(discriminator="element_kind")]


def _duplicates[TIdentity: Hashable](values: Iterable[TIdentity]) -> set[TIdentity]:
    """Return repeated hashable identities from one typed payload collection."""
    seen: set[TIdentity] = set()
    duplicated: set[TIdentity] = set()
    for value in values:
        if value in seen:
            duplicated.add(value)
        seen.add(value)
    return duplicated
