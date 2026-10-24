"""Not a check itself — structured provider responses for complete workbench drafts."""

from __future__ import annotations

from typing import Annotated
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.formats.markdown.frontmatter import FrontmatterObjective


class SourceRecord(BaseModel):
    """Strict source transport; the compiler checks semantic payload invariants."""

    model_config = ConfigDict(extra="forbid", strict=True)


class SourceObjective(SourceRecord):
    """A plan objective referenced by lesson sections and exercises."""

    id: str
    statement: str


class SourceCoverage(SourceRecord):
    """Required teaching evidence or a scope boundary."""

    id: str
    objective: str | None = None
    claim: str
    scope: Literal["required", "supporting", "guardrail", "excluded"]
    evidence: list[Literal["explanation", "example", "contrast_example", "controlled_practice", "whole_lesson"]]


class SourceTeachingPoint(SourceRecord):
    """One teaching claim linked to the new catalog owner."""

    id: str
    source_owner: str
    statement: str = Field(min_length=1, pattern=r"\S")


class SourcePlan(SourceRecord):
    """Authored plan fields; the application writes slot identity around them in plan.md."""

    teaching_points: list[SourceTeachingPoint] = Field(
        min_length=2,
        max_length=5,
        description="Generate 2–5 concrete teaching points derived from the outcome, naming teachable forms, contrasts, or decisions. Do not merely copy the outcome.",
    )
    terminology_ids: list[str] = Field(default_factory=list)
    objectives: list[SourceObjective] = Field(min_length=1)
    coverage: list[SourceCoverage] = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    exclusions: list[str] = Field(default_factory=list)


K_SERVER_PLAN_IDENTITY_FIELDS = (
    "package_version",
    "lesson_id",
    "kind",
    "title",
    "cefr_level",
    "cefr_target_basis",
    "approved",
    "catalog_id",
    "learner_outcome",
    "family_id",
    "cefr_tags",
    "prerequisites",
    "helpful_prerequisites",
    "approved_source_hash",
)
K_SERVER_OWNED_PLAN_FIELDS = (
    frozenset(CurriculumPlanSlot.model_fields).difference(SourcePlan.model_fields)
    | (FrontmatterObjective.model_fields.keys() - SourceObjective.model_fields.keys())
    | {"type", *K_SERVER_PLAN_IDENTITY_FIELDS}
)


class SourceReference(SourceRecord):
    """Authored exercise provenance."""

    section_id: str
    block_index: int | None = None
    note: str | None = None


class SourceTurn(SourceRecord):
    """One learner-visible dialogue utterance."""

    speaker: str
    text_md: str


class SourceDialogue(SourceRecord):
    """Structured context shared by exercise operations."""

    kind: Literal["dialogue"]
    turns: list[SourceTurn]


class SourceExercise(SourceRecord):
    """Common authored exercise fields, before Markdown span projection."""

    handle: str = Field(min_length=1, pattern=r"^[^\s{}]+$")
    id: str | None = None
    objective: str
    bloom: Literal["remember", "understand", "apply", "analyze"]
    prompt_md: str
    explanation_md: str | None = None
    stimulus: list[SourceDialogue] = Field(default_factory=list)
    derived_from: list[SourceReference] = Field(default_factory=list)


class SourceOption(SourceRecord):
    """An authored choice including its answer flag."""

    id: str
    text: str
    correct: bool = False
    why: str | None = None


class SourceChoose(SourceExercise):
    """One closed choice."""

    op: Literal["choose"]
    stem_md: str | None = None
    options: list[SourceOption] = Field(min_length=2)


class SourceSpan(SourceRecord):
    """Literal recall text, preserving boundary whitespace."""

    text_md: str


class SourceBlank(SourceRecord):
    """A recall gap with a local answer index."""

    blank_id: str
    options: list[str] = Field(min_length=2)
    answer_index: int


class SourceRecallFill(SourceExercise):
    """A recall utterance with ordered spans and blanks."""

    op: Literal["recall_fill"]
    audio_target: str
    segments: list[SourceSpan | SourceBlank]


class SourceLeft(SourceRecord):
    """One match item on the left."""

    left_id: str
    text: str


class SourceRight(SourceRecord):
    """One match item on the right."""

    right_id: str
    text: str


class SourcePair(SourceRecord):
    """An independent matching link."""

    left_id: str
    right_id: str


class SourceMatchPairs(SourceExercise):
    """A correspondence set."""

    op: Literal["match_pairs"]
    left: list[SourceLeft]
    right: list[SourceRight]
    pairs: list[SourcePair]


class SourceJudge(SourceExercise):
    """A sentence judgment."""

    op: Literal["judge"]
    sentence_md: str
    is_correct: bool
    feedback: str | None = None


class SourceBucket(SourceRecord):
    """An authored category."""

    bucket_id: str
    label: str


class SourceCategoryItem(SourceRecord):
    """One categorized item."""

    item_id: str
    text: str
    bucket_id: str


class SourceCategorize(SourceExercise):
    """A category assignment set."""

    op: Literal["categorize"]
    buckets: list[SourceBucket] = Field(min_length=2)
    items: list[SourceCategoryItem]


class SourceToken(SourceRecord):
    """A local token with an optional fixed position."""

    token_id: str
    text: str
    fixed: bool = False


class SourceBuild(SourceExercise):
    """An ordered utterance construction."""

    op: Literal["build"]
    tokens: list[SourceToken]
    answer_order: list[str]


class SourceFindFix(SourceExercise):
    """A token-level error identification."""

    op: Literal["find_fix"]
    tokens: list[SourceToken]
    error_token_id: str
    feedback: str


class SourceCriterion(SourceRecord):
    """A semantic writing criterion."""

    id: str
    instruction: str


class SourceWrite(SourceExercise):
    """A Norwegian writing task with a stateless rubric."""

    op: Literal["write"]
    response_language: Literal["no"]
    min_words: int | None = Field(default=None, gt=0)
    max_words: int | None = Field(default=None, gt=0)
    judge_prompt: str
    criteria: list[SourceCriterion]


class AuthoredLessonResponse(SourceRecord):
    """Complete draft response; only learner Markdown remains free text."""

    @model_validator(mode="before")
    @classmethod
    def discard_server_owned_plan_fields(cls, value: object) -> object:
        """Ignore curriculum metadata echoed from the authoritative slot, not authored fields."""
        if not isinstance(value, dict) or not isinstance(value.get("plan"), dict):
            return value
        plan = dict(value["plan"])
        for key in K_SERVER_OWNED_PLAN_FIELDS:
            plan.pop(key, None)
        if isinstance(plan.get("objectives"), list):
            plan["objectives"] = [
                {key: field for key, field in objective.items() if key != "bloom_targets"}
                if isinstance(objective, dict)
                else objective
                for objective in plan["objectives"]
            ]
        return value | {"plan": plan}

    plan: SourcePlan
    plan_body: str
    lesson_md: str
    exercises: list[
        Annotated[
            SourceChoose
            | SourceRecallFill
            | SourceMatchPairs
            | SourceJudge
            | SourceCategorize
            | SourceBuild
            | SourceFindFix
            | SourceWrite,
            Field(discriminator="op"),
        ]
    ] = Field(min_length=1)
