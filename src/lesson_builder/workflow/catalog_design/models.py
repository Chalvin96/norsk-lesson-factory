"""Entry points: ``CatalogRequest`` / ``CatalogCandidate`` / ``CatalogProposal``.

Not checks themselves — Pydantic contracts carried by the catalog-design
LangGraph. CEFR is metadata on a candidate; ``category`` selects the catalog
owner category.
"""

from __future__ import annotations

from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from lesson_builder.domain.lesson.models.lesson import CefrLevel

AgentKind = Literal["explorer", "reviewer"]
Relationship = Literal[
    "distinct",
    "new",
    "merge",
    "merge_candidate",
    "duplicate",
    "exact_duplicate",
    "semantic_duplicate",
    "broader",
    "narrower",
    "related",
    "lesson_extension",
    "uncertain",
]
DecisionStatus = Literal[
    "accepted",
    "merged",
    "rejected_existing",
    "rejected_duplicate",
    "rejected_low_quality",
    "rejected_invalid",
    "needs_human_review",
]


class CatalogRequest(BaseModel):
    """One category fill request supplied to the graph."""

    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1)
    category_guidance: str = ""
    cefr_tags: list[CefrLevel] = Field(default_factory=list)
    max_iterations: int = Field(default=3, ge=1, le=5)


class ExistingComparison(BaseModel):
    """Catalog-review comparison between a candidate and a known owner."""

    model_config = ConfigDict(extra="forbid")

    catalog_id: str = Field(min_length=1)
    shared_core: str = ""
    independent_difference: str = ""


class CatalogCandidate(BaseModel):
    """A proposed catalog owner, deliberately smaller than a lesson."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    alternate_labels: list[str] = Field(default_factory=list)
    category: str = Field(min_length=1)
    learner_question: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    out_of_scope: list[str] = Field(default_factory=list)
    cefr_tags: list[CefrLevel] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    confusable_with: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    source_agent: AgentKind = "reviewer"
    teachable_core: str = ""
    independent_difference: str = ""
    nearest_existing: list[str] = Field(default_factory=list)


class CandidateBatch(BaseModel):
    """Structured output from one discovery branch."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[CatalogCandidate] = Field(default_factory=list)
    coverage_notes: list[str] = Field(default_factory=list)


class ExistingLesson(BaseModel):
    """A compact snapshot of one already-known lesson owner."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1)
    title: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    objective_summary: str = ""
    notes: str = ""
    teaching_point_ids: list[str] = Field(default_factory=list)
    chapter: str = ""


class CandidateResolution(BaseModel):
    """Catalog-review relationship and canonicalization decision."""

    model_config = ConfigDict(extra="forbid")

    canonical_slug: str = Field(min_length=1)
    canonical_title: str = Field(min_length=1)
    candidate_ids: list[str] = Field(min_length=1)
    relationship: Relationship
    aliases: list[str] = Field(default_factory=list)
    existing_slug: str | None = None
    teaching_point: str = ""
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    architecture_question: bool = False
    nearest_existing: list[ExistingComparison] = Field(default_factory=list)
    independent_difference: str = ""
    uncertainty_kind: Literal["semantic", "pragmatic"] | None = None
    review_question: str = ""


class ResolutionBatch(BaseModel):
    """Structured output from the semantic resolver."""

    model_config = ConfigDict(extra="forbid")

    resolutions: list[CandidateResolution] = Field(default_factory=list)
    architecture_question: bool = False
    architecture_question_reason: str = ""


class CandidateEvaluation(BaseModel):
    """Quality dimensions for one proposed canonical owner."""

    model_config = ConfigDict(extra="forbid")

    canonical_slug: str = Field(min_length=1)
    distinctness: float = Field(ge=0, le=1)
    usefulness: float = Field(ge=0, le=1)
    scope_clarity: float = Field(ge=0, le=1)
    category_fit: float = Field(ge=0, le=1)
    accuracy: float = Field(ge=0, le=1)
    quality_score: float = Field(ge=0, le=1)
    status: Literal["accept", "reject", "needs_human_review"]
    reasons: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    hard_failures: list[str] = Field(default_factory=list)


class EvaluationBatch(BaseModel):
    """Structured output from the catalog quality evaluator."""

    model_config = ConfigDict(extra="forbid")

    evaluations: list[CandidateEvaluation] = Field(default_factory=list)
    architecture_question: bool = False
    coverage_complete: bool = False
    coverage_gaps: list[str] = Field(default_factory=list)


class AdviceResult(BaseModel):
    """Bounded diagnosis when the graph detects an architecture problem."""

    model_config = ConfigDict(extra="forbid")

    advice: str = Field(min_length=1)
    architecture_question: bool = False


class CatalogDecision(BaseModel):
    """Final disposition for one canonical or rejected candidate."""

    model_config = ConfigDict(extra="forbid")

    canonical_slug: str
    title: str
    category: str
    cefr_tags: list[CefrLevel] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    relationship: Relationship | None = None
    target_lesson_slug: str | None = None
    teaching_point: str = ""
    status: DecisionStatus
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class LlmCallProvenance(BaseModel):
    """Effective backend and timing metadata for one catalog model call."""

    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1)
    status: Literal["ok", "error"]
    client: str = Field(min_length=1)
    model: str = ""
    agent: str | None = None
    variant: str | None = None
    latency_ms: int | None = None
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class CatalogProposal(BaseModel):
    """Proposal artifact emitted by a completed graph run."""

    model_config = ConfigDict(extra="forbid")

    category: str
    cefr_tags: list[CefrLevel] = Field(default_factory=list)
    candidates: list[CatalogCandidate] = Field(default_factory=list)
    resolutions: list[CandidateResolution] = Field(default_factory=list)
    evaluations: list[CandidateEvaluation] = Field(default_factory=list)
    decisions: list[CatalogDecision] = Field(default_factory=list)
    iteration: int = Field(ge=0)
    stagnation_count: int = Field(ge=0)
    advice: str | None = None
    coverage_complete: bool = False
    coverage_gaps: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    llm_provenance: list[LlmCallProvenance] = Field(default_factory=list)
    status: Literal["ready", "partial", "needs_human_review", "blocked", "failed"]


__all__ = [
    "AgentKind",
    "AdviceResult",
    "CatalogCandidate",
    "CatalogDecision",
    "CatalogProposal",
    "CatalogRequest",
    "CandidateBatch",
    "CandidateEvaluation",
    "CandidateResolution",
    "ExistingLesson",
    "LlmCallProvenance",
    "DecisionStatus",
    "EvaluationBatch",
    "ExistingComparison",
    "Relationship",
    "ResolutionBatch",
]
